import subprocess
import os
import ast
from urllib.parse import urlparse

ignores = { ".git", ".gitignore", ".lock", ".venv", "__pycache__", "node_modules", ".vscode", "pyproject.toml", ".python-version", "requirements.txt" }

ENTRY_FILENAME_HINTS = {"main.py", "server.py", "run.py", "app.py", "wsgi.py", "asgi.py"}
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "route", "head", "options", "websocket"}
APP_RUNNER_CALLS = {
    "uvicorn.run", "app.run", "application.run", "celery.start",
    "serve.run", "hypercorn.run", "gunicorn.run",
}
CLI_DECORATORS = {"click.command", "click.group", "typer.run"}
TASK_DECORATORS = {"celery.task", "shared_task", "app.task"}

def normalize_repo_url(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    if not url.startswith(("http://", "https://", "git@")):
        url = f"https://{url}"
    return url.rstrip("/")


def get_filename(url: str):
    url = normalize_repo_url(url)
    if url[-4:] == ".git":
        url = url[:-4]
        
    parts = urlparse(url).path.strip("/").split("/")

    if len(parts) >= 2:
        username = parts[0]
        project_name = parts[1]
        result = f"{username}-{project_name}"
        return result

def clone_repo(repo_link):
    filename = get_filename(repo_link)
    dir = f"src/data/{filename}"

    if os.path.isdir(dir):
        print("Directory already exists")
    else:
        try:
            subprocess.run(["git", "clone", repo_link, dir], check=True)
            print("Clone successful!")
        except subprocess.CalledProcessError as e:
            print(f"Git command failed with error: {e}")
    
    return dir

def _expr_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _expr_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _expr_name(node.func)
    if isinstance(node, ast.Subscript):
        return _expr_name(node.value)
    return None

def _decorator_name(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _decorator_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return None


def _is_http_endpoint_decorator(decorator) -> bool:
    name = _decorator_name(decorator)
    if not name:
        return False
    parts = name.split(".")
    if parts[-1] in HTTP_METHODS:
        return True
    if len(parts) >= 2 and parts[-2] in ("app", "router", "api", "blueprint") and parts[-1] in HTTP_METHODS:
        return True
    return False


def _is_cli_decorator(decorator) -> bool:
    name = _decorator_name(decorator)
    if not name:
        return False
    return name in CLI_DECORATORS or name.endswith(".command") or name.endswith(".group")


def _is_task_decorator(decorator) -> bool:
    name = _decorator_name(decorator)
    if not name:
        return False
    return name in TASK_DECORATORS or name.endswith(".task")


def _is_main_guard(node) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not isinstance(test, ast.Compare):
        return False
    if not isinstance(test.left, ast.Name) or test.left.id != "__name__":
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    if len(test.comparators) != 1:
        return False
    comp = test.comparators[0]
    return isinstance(comp, ast.Constant) and comp.value == "__main__"


def _detect_entry_points(tree, filepath: str | None) -> tuple[list[dict], bool, str]:
    """AST pass: high-confidence entry points + flag uncertain files for LLM review."""
    entry_points: list[dict] = []
    has_main_block = False
    module_level_bootstrap = False
    decorator_names: list[str] = []

    for node in tree.body:
        if _is_main_guard(node):
            has_main_block = True
            for child in node.body:
                if isinstance(child, ast.Expr) and isinstance(child.value, ast.Call):
                    callee = _expr_name(child.value.func)
                    if callee:
                        qname = f"{filepath}::__main__" if filepath else "__main__"
                        entry_points.append({
                            "qualified_name": qname,
                            "name": "__main__",
                            "kind": "main_block",
                            "confidence": 0.95,
                            "source": "ast",
                            "reason": "if __name__ == '__main__' block",
                        })
                        break
            if not any(ep["kind"] == "main_block" for ep in entry_points):
                qname = f"{filepath}::__main__" if filepath else "__main__"
                entry_points.append({
                    "qualified_name": qname,
                    "name": "__main__",
                    "kind": "main_block",
                    "confidence": 0.95,
                    "source": "ast",
                    "reason": "if __name__ == '__main__' block",
                })

        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            callee = _expr_name(node.value.func)
            if callee and callee in APP_RUNNER_CALLS:
                module_level_bootstrap = True
                qname = f"{filepath}::{callee}" if filepath else callee
                entry_points.append({
                    "qualified_name": qname,
                    "name": callee.split(".")[-1],
                    "kind": "app_runner",
                    "confidence": 0.95,
                    "source": "ast",
                    "reason": f"module-level call to {callee}",
                })

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                dec_name = _decorator_name(dec)
                if dec_name:
                    decorator_names.append(dec_name)

                func_qname = (
                    f"{filepath}::{node.name}" if filepath and not getattr(node, "_in_class", False)
                    else node.name
                )
                for cls in getattr(node, "_parent_classes", []):
                    func_qname = f"{filepath}::{cls}.{node.name}" if filepath else f"{cls}.{node.name}"

                if _is_http_endpoint_decorator(dec):
                    qname = f"{filepath}::{node.name}" if filepath else node.name
                    for parent in ast.walk(tree):
                        if isinstance(parent, ast.ClassDef):
                            for child in parent.body:
                                if child is node:
                                    qname = f"{filepath}::{parent.name}.{node.name}" if filepath else f"{parent.name}.{node.name}"
                    entry_points.append({
                        "qualified_name": qname,
                        "name": node.name,
                        "kind": "http_endpoint",
                        "confidence": 0.95,
                        "source": "ast",
                        "reason": f"HTTP decorator: {_decorator_name(dec)}",
                    })
                elif _is_cli_decorator(dec):
                    qname = f"{filepath}::{node.name}" if filepath else node.name
                    entry_points.append({
                        "qualified_name": qname,
                        "name": node.name,
                        "kind": "cli_entry",
                        "confidence": 0.95,
                        "source": "ast",
                        "reason": f"CLI decorator: {_decorator_name(dec)}",
                    })
                elif _is_task_decorator(dec):
                    qname = f"{filepath}::{node.name}" if filepath else node.name
                    entry_points.append({
                        "qualified_name": qname,
                        "name": node.name,
                        "kind": "task_entry",
                        "confidence": 0.95,
                        "source": "ast",
                        "reason": f"Task decorator: {_decorator_name(dec)}",
                    })

    # Tag class methods with parent for qualified names
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qname = f"{filepath}::{node.name}.{child.name}" if filepath else f"{node.name}.{child.name}"
                    for dec in child.decorator_list:
                        if _is_http_endpoint_decorator(dec):
                            entry_points.append({
                                "qualified_name": qname,
                                "name": child.name,
                                "kind": "http_endpoint",
                                "confidence": 0.95,
                                "source": "ast",
                                "reason": f"HTTP decorator on method: {_decorator_name(dec)}",
                            })

    # Deduplicate by qualified_name + kind
    seen = set()
    unique_entries = []
    for ep in entry_points:
        key = (ep["qualified_name"], ep["kind"])
        if key not in seen:
            seen.add(key)
            unique_entries.append(ep)

    flagged = False
    flag_reason = ""

    basename = filepath.split("/")[-1] if filepath else ""
    if not unique_entries:
        if basename in ENTRY_FILENAME_HINTS:
            flagged = True
            flag_reason = f"conventional entry filename ({basename}) with no AST-detected entry point"
        elif basename == "__init__.py" and len(tree.body) > 3:
            import_count = sum(
                1 for n in tree.body
                if isinstance(n, (ast.Import, ast.ImportFrom))
            )
            if import_count >= 2:
                flagged = True
                flag_reason = "package __init__.py with multiple imports (possible package entry)"
        elif module_level_bootstrap:
            flagged = True
            flag_reason = "module-level bootstrap calls without clear entry classification"

    return unique_entries, flagged, flag_reason


def _collect_calls(node, caller):
    calls = []
    nested_def_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    from collections import deque
    
    # Custom BFS traversal that does not queue/descend into nested functions/classes
    todo = deque(node.body if hasattr(node, "body") else [])
    
    while todo:
        child = todo.popleft()
        if isinstance(child, nested_def_types):
            continue
        if isinstance(child, ast.Call):
            callee = _expr_name(child.func)
            if callee:
                calls.append({
                    "caller": caller,
                    "callee": callee,
                    "line": getattr(child, "lineno", None)
                })
        todo.extend(ast.iter_child_nodes(child))
    return calls

def parse_file(source_code, filepath=None):
    tree = ast.parse(source_code)
    import_modules, import_names, classes, functions = [], [], [], []
    imports, methods, calls, inheritance = [], [], [], []

    # Speed Optimization: Pre-collect class methods to avoid walking the whole tree for every function definition
    method_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_nodes.add(child)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                import_modules.append(alias.name)
                import_names.append([alias.asname or alias.name.split(".")[0]])
                imports.append({"module": alias.name, "name": alias.name.split(".")[0], "alias": alias.asname, "line": node.lineno})

        if isinstance(node, ast.ImportFrom):
            import_modules.append(node.module)
            import_names.append([alias.asname or alias.name for alias in node.names])
            for alias in node.names:
                imports.append({"module": node.module or "", "name": alias.name, "alias": alias.asname, "line": node.lineno})

        if isinstance(node, ast.ClassDef):
            class_qname = node.name if filepath is None else f"{filepath}::{node.name}"
            bases = [base for base in (_expr_name(base) for base in node.bases) if base]
            classes.append({"name": node.name, "qualified_name": class_qname, "bases": bases, "line_start": node.lineno, "line_end": node.end_lineno})
            for base in bases:
                inheritance.append({"class": node.name, "qualified_name": class_qname, "base": base, "line": node.lineno})

            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_qname = f"{class_qname}.{child.name}"
                    method = {"name": child.name, "qualified_name": method_qname, "class_name": node.name, "line_start": child.lineno, "line_end": child.end_lineno}
                    methods.append(method)
                    functions.append(method)
                    calls.extend(_collect_calls(child, method_qname))

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Check if this function is a class method
            if node in method_nodes:
                continue
            func_qname = node.name if filepath is None else f"{filepath}::{node.name}"
            function = {"name": node.name, "qualified_name": func_qname, "class_name": None, "line_start": node.lineno, "line_end": node.end_lineno}
            functions.append(function)
            calls.extend(_collect_calls(node, func_qname))

    entry_points, entry_flagged, flag_reason = _detect_entry_points(tree, filepath)
    decorator_names = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                name = _decorator_name(dec)
                if name:
                    decorator_names.append(name)

    return {
        "import_modules": import_modules,
        "import_names": import_names,
        "imports": imports,
        "classes": classes,
        "functions": functions,
        "methods": methods,
        "calls": calls,
        "inheritance": inheritance,
        "entry_points": entry_points,
        "entry_flagged": entry_flagged,
        "flag_reason": flag_reason,
        "decorator_names": list(set(decorator_names)),
    }

def get_files(repo_link):
    directory = clone_repo(repo_link)

    inventory = {}
    for root, dirs, files in os.walk(directory):
        rel_dir = os.path.relpath(root, directory)
        if rel_dir == ".":
            rel_dir = ""
        
        for name in files:
            if (name not in ignores) and (name == "README.md" or name.endswith(".py")):
                full_path = os.path.join(root, name)

                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    posix_path = os.path.join(rel_dir, name).replace("\\", "/")

                    parsed_structure = {
                        "import_modules": [], "import_names": [], "imports": [],
                        "classes": [], "functions": [], "methods": [], "calls": [],
                        "inheritance": [], "entry_points": [], "entry_flagged": False,
                        "flag_reason": "", "decorator_names": [],
                    }
                    if name.endswith(".py"):
                        try:
                            parsed_structure = parse_file(content, filepath=posix_path)
                        except SyntaxError:
                            pass

                    parsed_structure['content'] = content
                    
                    inventory[posix_path] = parsed_structure

    return inventory
