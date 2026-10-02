"""The clone-containment guard.

`git -C <dir>` does not require `<dir>` to be a repository. With no `.git`
inside it, git walks *up* and operates on whichever ancestor repository it
finds first.

That made this indexer destructive. `src/data/princ3kr-Ask-My-Repo` was a plain
directory holding one stray file, so `_refresh_clone` ran `fetch` and then
`reset --hard origin/mainV2` against the *host project's own repository* —
silently discarding uncommitted work — and logged "Refreshed existing clone".
The only visible symptom was 0 files indexed.
"""
import subprocess

import pytest

from src.backend.chunking import repo_parser
from src.backend.chunking.repo_parser import _is_self_contained_clone, clone_repo


def _run(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def outer_repo(tmp_path, monkeypatch):
    """A real git repository, standing in for the host project."""
    repo = tmp_path / "host-project"
    repo.mkdir()
    _run(str(repo), "init", "-q")
    _run(str(repo), "config", "user.email", "t@example.com")
    _run(str(repo), "config", "user.name", "t")
    (repo / "important.py").write_text("VALUE = 42\n")
    _run(str(repo), "add", ".")
    _run(str(repo), "commit", "-q", "-m", "init")
    return repo


class TestIsSelfContainedClone:
    def test_a_real_clone_is_self_contained(self, outer_repo, tmp_path, monkeypatch):
        # get_filename() only understands http(s)/git@ URLs, so the clone has
        # to be made for real and then inspected.
        monkeypatch.setattr(repo_parser, "CLONES_DIR", tmp_path / "data")
        target = repo_parser.CLONES_DIR / "o-r"
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", str(outer_repo), str(target)],
            check=True, capture_output=True,
        )
        assert _is_self_contained_clone(target)

    def test_a_plain_directory_is_not(self, tmp_path, monkeypatch):
        """The exact case that reset --hard'd the host repository."""
        monkeypatch.setattr(repo_parser, "CLONES_DIR", tmp_path / "data")
        target = tmp_path / "data" / "o-r"
        target.mkdir(parents=True)
        (target / "junk.txt").write_text("junk")
        assert not _is_self_contained_clone(target)

    def test_a_nested_directory_inside_a_repo_is_not(self, outer_repo, monkeypatch):
        """The dangerous shape: no `.git` of its own, but an ancestor has one."""
        nested = outer_repo / "src" / "data" / "o-r"
        nested.mkdir(parents=True)
        assert not _is_self_contained_clone(nested)

    def test_missing_directory_is_not(self, tmp_path):
        assert not _is_self_contained_clone(tmp_path / "nope")


class TestRefreshNeverEscapesTheClone:
    def test_a_plain_directory_is_discarded_not_refreshed(self, outer_repo, monkeypatch):
        """A plain dir inside a repo must be removed, and the *outer* repo must
        be left untouched."""
        monkeypatch.setattr(repo_parser, "CLONES_DIR", outer_repo / "src" / "data")
        target = outer_repo / "src" / "data" / "o-r"
        target.mkdir(parents=True)
        (target / "junk.txt").write_text("junk")

        head_before = _run(str(outer_repo), "rev-parse", "HEAD")
        (outer_repo / "important.py").write_text("VALUE = 999\n")  # uncommitted

        # Point the clone at the host repo itself, which is what the real
        # incident looked like.
        result = clone_repo(str(outer_repo))

        # The uncommitted edit must survive: no reset --hard happened.
        assert (outer_repo / "important.py").read_text() == "VALUE = 999\n", \
            "clone_repo reset --hard the host repository"
        assert _run(str(outer_repo), "rev-parse", "HEAD") == head_before
        # And we did not silently reuse the junk directory.
        assert result != str(target)

    def test_refresh_is_refused_for_a_non_clone(self, outer_repo, monkeypatch):
        called = []

        def spy(*a, **k):
            called.append(a)
            raise AssertionError("must not run git against a non-clone")

        monkeypatch.setattr(repo_parser.subprocess, "run", spy)

        target = outer_repo / "not-a-clone"
        target.mkdir()

        assert repo_parser._refresh_clone(target, "https://github.com/o/r") is False
        assert called == []


class TestRefreshVerifiesResult:
    def test_an_empty_worktree_after_reset_is_reported_as_failure(self, tmp_path, monkeypatch):
        """`reset --hard` can exit 0 and still leave nothing checked out.

        Reporting success there is what let a broken clone pass for a good one.
        """
        fake = tmp_path / "clone"
        fake.mkdir()
        (fake / ".git").mkdir()

        def fake_run(cmd, **kw):
            if "ls-files" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="a.py\nb.py\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="main\n", stderr="")

        monkeypatch.setattr(repo_parser.subprocess, "run", fake_run)
        monkeypatch.setattr(repo_parser, "_is_self_contained_clone", lambda t: True)

        assert repo_parser._refresh_clone(fake, "https://github.com/o/r") is False

    def test_a_populated_worktree_is_accepted(self, tmp_path, monkeypatch):
        fake = tmp_path / "clone"
        fake.mkdir()
        (fake / ".git").mkdir()
        (fake / "a.py").write_text("x = 1\n")

        def fake_run(cmd, **kw):
            if "ls-files" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="a.py\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="main\n", stderr="")

        monkeypatch.setattr(repo_parser.subprocess, "run", fake_run)
        monkeypatch.setattr(repo_parser, "_is_self_contained_clone", lambda t: True)

        assert repo_parser._refresh_clone(fake, "https://github.com/o/r") is True


class TestZeroFilesIsAnError:
    def test_map_repository_refuses_an_empty_checkout(self, monkeypatch):
        """Previously 0 files fell through and reported success, so whatever
        graph was already in Neo4j stayed on screen and looked correct."""
        from src.backend.map import mapper

        monkeypatch.setattr(mapper, "get_files", lambda url: {})
        monkeypatch.setattr(mapper, "get_filename", lambda url: "o-r")

        with pytest.raises(RuntimeError, match="No Python files found"):
            mapper.map_repository("https://github.com/o/r")

    def test_the_message_reaches_the_user(self):
        from src.backend.api import friendly_error

        msg = friendly_error("No Python files found in the repository checkout.")
        assert "came back empty" in msg
