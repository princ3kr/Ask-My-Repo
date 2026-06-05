import subprocess
import os
import ast
from urllib.parse import urlparse

ignores = { ".git", ".gitignore", ".lock", ".venv", "__pycache__", "node_modules", ".vscode", "pyproject.toml", "__init__.py", ".python-version", "requirements.txt" }

def get_filename(url: str):
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

    return {"import_modules": import_modules, "import_names": import_names, "imports": imports, "classes": classes, "functions": functions, "methods": methods, "calls": calls, "inheritance": inheritance}

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

                    parsed_structure = {"import_modules": [], "import_names": [], "imports": [], "classes": [], "functions": [], "methods": [], "calls": [], "inheritance": []}
                    if name.endswith(".py"):
                        try:
                            parsed_structure = parse_file(content, filepath=posix_path)
                        except SyntaxError:
                            pass

                    parsed_structure['content'] = content
                    
                    inventory[posix_path] = parsed_structure

    return inventory
