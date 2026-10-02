import ast
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger("askmyrepo.repo_parser")

# Anchored to the repo root rather than the process CWD. The previous
# relative "src/data/<id>" made the clone land somewhere else — or fail —
# whenever the server was started from a different directory.
REPO_ROOT = Path(__file__).resolve().parents[3]
CLONES_DIR = REPO_ROOT / "src" / "data"
# Same reason: api.py built these from os.getcwd().
NOTEBOOK_DIR = REPO_ROOT / "notebook"

ignores = { ".git", ".gitignore", ".lock", ".venv", "__pycache__", "node_modules", ".vscode", "pyproject.toml", ".python-version", "requirements.txt" }

# Pruned from os.walk's `dirs` in place, so os.walk never descends into them.
# The `ignores` set above is matched against FILE names only, so its directory
# entries (.git, node_modules, .venv) could never fire; this is the set that
# actually contains .py files worth skipping.
IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".nox", ".eggs",
    "__pycache__", "node_modules", "bower_components", "vendor",
    ".venv", "venv", "env", ".env", "virtualenv",
    "site-packages", "dist-packages",
    "build", "dist", "out", "target", ".next", ".nuxt", ".parcel-cache",
    "htmlcov", "coverage", ".terraform", ".gradle",
    "migrations", "alembic",
    "tests", "test", "spec", "specs", "__tests__", "testing", "e2e",
    "docs", "doc", "examples", "example", "samples", "sample",
    "benchmarks", "benchmark", "fixtures", "third_party", "vendored",
}

# Generated/minified blobs are both huge and useless as embedding input, and
# parsing them is pure waste.
MAX_FILE_BYTES = 512 * 1024

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


# The repo id becomes a Cypher property value, a Qdrant collection name and a
# directory name, so it is restricted to a conservative character set. It
# previously passed arbitrary URL text straight through.
_SAFE_REPO_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


def get_filename(url: str) -> str | None:
    """Derive the repo id (`owner-name`) from a repository URL.

    Returns None when the URL does not name a repository or when the derived id
    contains anything outside the safe set.
    """
    url = normalize_repo_url(url)
    if not url:
        return None
    if url.endswith(".git"):
        url = url[:-4]

    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) < 2:
        return None

    result = f"{parts[0]}-{parts[1]}"
    if not _SAFE_REPO_ID.match(result):
        logger.warning(f"Rejecting unsafe repo id derived from URL: {result!r}")
        return None
    return result


def _is_self_contained_clone(target: Path) -> bool:
    """True only if `target` is its own git checkout.

    This guard is not paranoia. `git -C <dir>` does not require `<dir>` to be a
    repository: if there is no `.git` inside it, git walks *up* and operates on
    whichever ancestor repository it finds first.

    That made this indexer destructive. `src/data/princ3kr-Ask-My-Repo` was a
    plain directory (one stray file, no `.git`), so `_refresh_clone` ran
    `fetch` and then `reset --hard origin/mainV2` against **this project's own
    repository** — silently discarding uncommitted work — and then reported
    "Refreshed existing clone". Indexing 0 files was the only visible symptom.
    """
    git_marker = target / ".git"
    if not git_marker.exists():
        return False

    try:
        top = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout.strip()
    except Exception:
        return False

    try:
        return Path(top).resolve() == target.resolve()
    except OSError:
        return False


def _refresh_clone(target: Path, repo_link: str) -> bool:
    """Fast-forward an existing shallow clone to the remote's current HEAD.

    Reusing a clone as-is meant a re-parse silently indexed whatever the code
    looked like at first clone. Because the pipeline also short-circuits when
    the graph and vectors already exist, that stale index then persisted
    indefinitely and answered questions about files that no longer existed.

    Refuses to touch anything that is not a self-contained checkout, and
    verifies afterwards that files actually landed — `reset --hard` can report
    success while leaving an empty working tree.
    """
    if not _is_self_contained_clone(target):
        logger.warning(
            f"{target} exists but is not its own git checkout; discarding it "
            "rather than risk operating on a parent repository."
        )
        return False

    try:
        subprocess.run(
            ["git", "-C", str(target), "fetch", "--depth", "1", "origin"],
            check=True, capture_output=True, text=True, timeout=300,
        )
        head = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True, capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(target), "reset", "--hard", f"origin/{head}"],
            check=True, capture_output=True, text=True, timeout=120,
        )
    except Exception as e:
        logger.info(f"Could not refresh existing clone ({e}); will re-clone.")
        return False

    # A successful `reset --hard` still leaves an empty working tree if the
    # checkout was broken. Never report a refresh we cannot see the result of.
    tracked = subprocess.run(
        ["git", "-C", str(target), "ls-files"],
        check=False, capture_output=True, text=True, timeout=60,
    ).stdout.split()
    present = [p for p in tracked if (target / p).exists()]
    if tracked and not present:
        logger.warning(
            f"{target}: reset reported success but no tracked files are present; "
            "treating as unusable so it gets re-cloned."
        )
        return False

    return True


def clone_repo(repo_link: str) -> str:
    """Clone (or refresh and reuse) the repo and return its absolute path.

    Raises on failure. A failed clone used to be swallowed with a print, so a
    bad or private URL produced an empty inventory that the pipeline then
    reported as a successful index.
    """
    filename = get_filename(repo_link)
    if not filename:
        raise ValueError(f"Could not derive a repository name from {repo_link!r}")
    target = CLONES_DIR / filename

    if target.is_dir():
        if _refresh_clone(target, repo_link):
            logger.info(f"Refreshed existing clone at {target}")
        else:
            # Not a usable checkout (or the ref moved in a way we cannot
            # fast-forward to) — discard it and clone cleanly.
            shutil.rmtree(target, ignore_errors=True)
            logger.info(f"Removed unusable clone at {target}")
        if target.is_dir():
            return str(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # --depth 1 / --single-branch: the working tree is all we ever read, so
        # fetching full history and every ref is wasted time and disk.
        logger.info(f"Cloning {repo_link} -> {target}")
        subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", repo_link, str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip().splitlines()
        hint = detail[-1] if detail else str(e)
        shutil.rmtree(target, ignore_errors=True)
        raise RuntimeError(f"git clone failed: {hint}") from e
    except FileNotFoundError as e:
        raise RuntimeError("git is not installed or not on PATH") from e

    return str(target)

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
    return bool(len(parts) >= 2 and parts[-2] in ("app", "router", "api", "blueprint") and parts[-1] in HTTP_METHODS)


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
    module_level_bootstrap = False
    decorator_names: list[str] = []

    for node in tree.body:
        if _is_main_guard(node):
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

    # One pass to map each function node to its enclosing class name. The
    # previous code re-walked the entire tree for every HTTP-decorated function
    # to answer the same question, which is O(functions * nodes): a file with
    # 300 endpoints spent seconds in this loop alone.
    parent_class: dict[int, str] = {}
    for parent in ast.walk(tree):
        if isinstance(parent, ast.ClassDef):
            for child in parent.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    parent_class[id(child)] = parent.name

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                dec_name = _decorator_name(dec)
                if dec_name:
                    decorator_names.append(dec_name)

                if _is_http_endpoint_decorator(dec):
                    cls_name = parent_class.get(id(node))
                    qname = (
                        f"{filepath}::{cls_name}.{node.name}"
                        if filepath and cls_name
                        else (f"{filepath}::{node.name}" if filepath else node.name)
                    )
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
    skipped_large = 0
    for root, dirs, files in os.walk(directory):
        # Prune in place: os.walk honours the mutation and never descends.
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]

        rel_dir = os.path.relpath(root, directory)
        if rel_dir == ".":
            rel_dir = ""

        for name in files:
            if (name not in ignores) and (name == "README.md" or name.endswith(".py")):
                full_path = os.path.join(root, name)

                try:
                    if os.path.getsize(full_path) > MAX_FILE_BYTES:
                        skipped_large += 1
                        continue
                    with open(full_path, encoding="utf-8") as f:
                        content = f.read()
                except (OSError, UnicodeDecodeError):
                    # A binary or unreadable file should not abort the index.
                    continue

                posix_path = os.path.join(rel_dir, name).replace("\\", "/")
                if posix_path.startswith("./"):
                    posix_path = posix_path[2:]

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

    logger.info(
        f"[parse] {len(inventory)} files indexed "
        f"({skipped_large} skipped as generated/oversized)"
    )
    return inventory
