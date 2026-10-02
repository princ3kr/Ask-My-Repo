"""Repository-URL parsing, repo-id derivation and clone behaviour."""
import os
import subprocess

import pytest

from src.backend.chunking import repo_parser
from src.backend.chunking.repo_parser import (
    REPO_ROOT,
    get_filename,
    normalize_repo_url,
)


class TestNormalizeRepoUrl:
    @pytest.mark.parametrize("raw,expected", [
        ("https://github.com/o/r", "https://github.com/o/r"),
        ("https://github.com/o/r/", "https://github.com/o/r"),
        ("github.com/o/r", "https://github.com/o/r"),
        ("git@github.com:o/r.git", "git@github.com:o/r.git"),
        ("", ""),
    ])
    def test_normalisation(self, raw, expected):
        assert normalize_repo_url(raw) == expected


class TestGetFilename:
    @pytest.mark.parametrize("url,expected", [
        ("https://github.com/princ3kr/Ask-My-Repo", "princ3kr-Ask-My-Repo"),
        ("github.com/princ3kr/Ask-My-Repo/", "princ3kr-Ask-My-Repo"),
        ("https://github.com/princ3kr/Ask-My-Repo.git", "princ3kr-Ask-My-Repo"),
        ("https://gitlab.com/group/sub/project", "group-sub"),
    ])
    def test_valid_urls(self, url, expected):
        assert get_filename(url) == expected

    @pytest.mark.parametrize("url", [
        # The Cypher injection payload. It used to be returned verbatim and
        # then interpolated into the str.format()-built templates.
        "https://github.com/a')-DETACH DELETE n//b",
        "https://github.com/o/r' OR '1'='1",
        "https://github.com/o/re%60po",
        "not-a-url",
        "",
        "https://github.com/onlyowner",
    ])
    def test_rejects_unsafe_or_unusable_input(self, url):
        assert get_filename(url) is None

    def test_payload_after_the_path_is_discarded(self):
        """urlparse truncates the path at `;`, so the injected tail never even
        reaches the charset check."""
        assert get_filename("https://github.com/o/re; DROP INDEX x") == "o-re"

    def test_repo_id_is_restricted_to_a_safe_charset(self):
        """The id becomes a directory name, a Qdrant collection name and a
        Cypher property value, so it must not carry punctuation or quotes."""
        for url in [
            "https://github.com/o/r",
            "https://github.com/some.user/some_repo.js",
        ]:
            repo_id = get_filename(url)
            assert repo_id
            assert all(c.isalnum() or c in "._-" for c in repo_id), repo_id


class TestPathsAreCwdIndependent:
    def test_clone_dir_is_anchored_to_the_repo_root(self):
        assert repo_parser.REPO_ROOT == REPO_ROOT
        assert repo_parser.CLONES_DIR == REPO_ROOT / "src" / "data"
        assert repo_parser.NOTEBOOK_DIR == REPO_ROOT / "notebook"

    def test_notebook_dir_is_absolute(self):
        assert repo_parser.NOTEBOOK_DIR.is_absolute()


class TestCloneRepo:
    @pytest.fixture(autouse=True)
    def _isolated_clone_dir(self, tmp_path, monkeypatch):
        """Never touch the real src/data/ from a test."""
        monkeypatch.setattr(repo_parser, "CLONES_DIR", tmp_path / "data")

    def test_rejects_a_url_with_no_derivable_id(self):
        with pytest.raises(ValueError, match="Could not derive"):
            repo_parser.clone_repo("not-a-url")

    def test_clone_failure_propagates(self, monkeypatch):
        """Previously swallowed with a print, so a bad or private URL produced
        an empty inventory that the pipeline reported as a successful index."""
        def boom(*a, **k):
            raise subprocess.CalledProcessError(
                128, "git", stderr="fatal: repository not found"
            )
        monkeypatch.setattr(repo_parser.subprocess, "run", boom)

        with pytest.raises(RuntimeError, match="repository not found"):
            repo_parser.clone_repo("https://github.com/o/definitely-not-here")

    def test_missing_git_binary_is_reported_clearly(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("git")
        monkeypatch.setattr(repo_parser.subprocess, "run", boom)

        with pytest.raises(RuntimeError, match="not installed"):
            repo_parser.clone_repo("https://github.com/o/r")

    def test_existing_clone_is_reused_without_cloning_again(self, monkeypatch, tmp_path):
        """An existing checkout is fast-forwarded, not re-cloned."""
        (tmp_path / "data" / "o-r").mkdir(parents=True)
        calls = []

        def record(cmd, **k):
            calls.append(cmd)
            if "rev-parse" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="main\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(repo_parser.subprocess, "run", record)

        result = repo_parser.clone_repo("https://github.com/o/r")

        assert result.endswith(os.path.join("data", "o-r"))
        assert any("clone" in c for c in calls) is False, calls
        assert any("fetch" in c for c in calls), calls

    def test_unusable_clone_is_discarded_and_re_cloned(self, monkeypatch, tmp_path):
        real_run = subprocess.run
        stale = tmp_path / "data" / "o-r"
        stale.mkdir(parents=True)
        (stale / "leftover.txt").write_text("stale")

        calls = []

        def record(cmd, **k):
            calls.append(cmd)
            if "rev-parse" in cmd:
                raise subprocess.CalledProcessError(128, "git", stderr="not a git repo")
            if "clone" in cmd:
                real_run(["git", "init", "-q", str(stale)], check=True)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(repo_parser.subprocess, "run", record)

        repo_parser.clone_repo("https://github.com/o/r")

        assert any("clone" in c for c in calls), calls
        assert not (stale / "leftover.txt").exists(), "stale contents must be gone"

    def test_successful_clone_returns_an_absolute_path(self, monkeypatch, tmp_path):
        real_run = subprocess.run

        def fake_run(cmd, **k):
            # `real_run` is captured before the patch, so this cannot recurse.
            return real_run(["git", "init", "-q", cmd[-1]], check=True)

        monkeypatch.setattr(repo_parser.subprocess, "run", fake_run)

        result = repo_parser.clone_repo("https://github.com/o/r")

        assert os.path.isabs(result), "clone path must not depend on the CWD"
        assert result.endswith(os.path.join("data", "o-r"))


class TestIgnoredDirs:
    def test_prunes_the_directories_that_actually_hold_python(self):
        for d in ["__pycache__", "node_modules", ".venv", ".git", "build", "dist"]:
            assert d in repo_parser.IGNORED_DIRS

    def test_oversized_files_are_skipped(self):
        assert repo_parser.MAX_FILE_BYTES > 0


class TestParseFile:
    def test_extracts_classes_functions_and_imports(self):
        source = '''
import os
from pathlib import Path

class Base:
    pass

class Child(Base):
    def method(self):
        return helper()

def helper():
    return Path(os.getcwd())

if __name__ == "__main__":
    helper()
'''
        result = repo_parser.parse_file(source, filepath="pkg/mod.py")

        assert {c["name"] for c in result["classes"]} == {"Base", "Child"}
        assert "helper" in {f["name"] for f in result["functions"]}
        assert "method" in {f["name"] for f in result["functions"]}
        assert any(i["module"] == "os" for i in result["imports"])
        assert result["entry_points"], "expected the __main__ guard to be detected"
        assert any(ep["kind"] == "main_block" for ep in result["entry_points"])

    def test_qualified_names_include_the_path(self):
        result = repo_parser.parse_file("def f():\n    pass\n", filepath="a/b.py")
        fq = [f["qualified_name"] for f in result["functions"]]
        assert "a/b.py::f" in fq

    def test_http_decorators_are_entry_points(self):
        source = '''
from fastapi import FastAPI
app = FastAPI()

@app.get("/things")
def list_things():
    return []
'''
        result = repo_parser.parse_file(source, filepath="api/routes.py")
        assert any(
            ep["kind"] == "http_endpoint" and ep["name"] == "list_things"
            for ep in result["entry_points"]
        )

    def test_conventional_entry_filename_is_flagged_not_asserted(self):
        """An uncertain file is flagged for LLM review rather than being
        claimed as an entry point outright."""
        result = repo_parser.parse_file("x = 1\n", filepath="main.py")
        assert result["entry_flagged"] is True
        assert result["entry_points"] == []
        assert "conventional entry filename" in result["flag_reason"]

    def test_syntax_error_propagates_for_the_caller_to_handle(self):
        with pytest.raises(SyntaxError):
            repo_parser.parse_file("def broken(:\n", filepath="x.py")

    def test_call_edges_are_collected(self):
        source = '''
def a():
    b()

def b():
    pass
'''
        result = repo_parser.parse_file(source, filepath="m.py")
        callees = {c["callee"] for c in result["calls"]}
        assert "b" in callees
