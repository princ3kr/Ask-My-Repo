"""Generate the DSPy eval set from live Neo4j data.

Labels are *derived from the graph*, not written by hand, so they are
objectively correct and stay correct when the graph is rebuilt. A question like
"which files import X" gets its gold answer from actually running the query,
which means the Cypher metric needs no LLM judge at all — the target is the
set of paths Neo4j returns.

Two label kinds:
  expected_paths  structural questions. Scored by set F1. No judge needed.
  expected_answer behavioural questions. Scored by LLM judge.

Run:  uv run python -m src.evaluation.build_eval_set
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from src.backend.services.connections import get_neo4j_driver  # noqa: E402

DEFAULT_REPOS = [
    "princ3kr-Ask-My-Repo",
    "princ3kr-Notebook-LM-Mini",
    "princ3kr-VQAModel",
]


def _run(cypher: str, repo_id: str, **params) -> list[dict]:
    with get_neo4j_driver().session(default_access_mode="READ") as s:
        return [r.data() for r in s.run(cypher, repo_id=repo_id, **params)]


def files_for_repo(repo_id: str) -> list[str]:
    return [r["path"] for r in _run(
        "MATCH (f:File {repo_id: $repo_id}) RETURN f.path AS path ORDER BY path", repo_id
    )]


def entry_points(repo_id: str) -> list[dict]:
    return _run(
        """
        MATCH (fn:Function {repo_id: $repo_id}) WHERE fn.is_entry = true
        RETURN fn.qualified_name AS qname, fn.entry_kind AS kind,
               fn.path AS path ORDER BY qname
        """, repo_id,
    )


def importers_of(repo_id: str, target: str) -> list[str]:
    return [r["path"] for r in _run(
        """
        MATCH (a:File {repo_id: $repo_id})-[:IMPORTS]->(b:File {repo_id: $repo_id})
        WHERE b.path ENDS WITH $target
        RETURN a.path AS path ORDER BY path
        """, repo_id, target=target) if r["path"]]


def most_connected(repo_id: str, limit: int = 5) -> list[str]:
    return [r["path"] for r in _run(
        """
        MATCH (f:File {repo_id: $repo_id})-[:IMPORTS]->()
        WITH f, count(*) AS c WHERE c > 0
        RETURN f.path AS path ORDER BY c DESC, path LIMIT $limit
        """, repo_id, limit=limit)]



def leaf_files(repo_id: str) -> list[str]:
    return [r["path"] for r in _run(
        """
        MATCH (f:File {repo_id: $repo_id})
        WHERE NOT (f)-[:IMPORTS]->() RETURN f.path AS path ORDER BY path
        """, repo_id)]


def classes(repo_id: str) -> list[str]:
    return [r["qname"] for r in _run(
        """
        MATCH (c:Class {repo_id: $repo_id}) RETURN c.qualified_name AS qname
        ORDER BY qname
        """, repo_id)]


def _pick(seq, i, default=None):
    return seq[i] if i < len(seq) else default


def build_for_repo(repo_id: str) -> list[dict]:
    """Derive labelled examples from whatever is actually in the graph."""
    out: list[dict] = []
    files = files_for_repo(repo_id)
    if not files:
        return out

    def add(qid, question, kind, gold, paths=None, answer=None):
        out.append({
            "qid": qid,
            "repo_id": repo_id,
            "question": question,
            "kind": kind,
            "expected_paths": paths or [],
            "expected_answer": answer or "",
            "gold": gold,
        })

    # ── structural, label = exact path set ────────────────────────────────
    # One question per import target with >=2 importers. Several targets give
    # genuinely different graphs rather than the same question restated, and
    # each is independently checkable.
    seen_targets = 0
    for target in files:
        importers = importers_of(repo_id, target.rsplit("/", 1)[-1])
        if len(importers) < 2:
            continue
        add(
            f"{repo_id}:importers:{target}",
            f"Which files import {target.rsplit('/', 1)[-1]}?",
            "graph", len(importers), paths=importers[:12],
        )
        seen_targets += 1
        if seen_targets >= 4:
            break

    # Reverse direction: who does this file depend on?
    for target in files:
        deps = importers_of(repo_id, target.rsplit("/", 1)[-1])
        if len(deps) >= 3:
            add(
                f"{repo_id}:leaves-neighbour:{target}",
                f"Which files does {target.rsplit('/', 1)[-1]} import?",
                "graph", len(deps), paths=deps[:12],
            )
            break

    leaves = leaf_files(repo_id)
    if leaves:
        add(
            f"{repo_id}:leaves",
            "Which files import no other local module?",
            "graph", len(leaves), paths=leaves[:12],
        )

    hub = most_connected(repo_id)
    if hub:
        add(
            f"{repo_id}:hub",
            "Which file imports the most other files?",
            "graph", len(hub), paths=hub,
        )

    eps = entry_points(repo_id)
    if eps:
        add(
            f"{repo_id}:entrypoints",
            "Which functions are application entry points?",
            "graph", len(eps),
            paths=sorted({e["path"] for e in eps if e.get("path")})[:12],
        )

    cl = classes(repo_id)
    if cl:
        add(
            f"{repo_id}:classes",
            "Which classes are defined in this repository?",
            "graph", len(cl), paths=[],
        )

    # ── behavioural, label = free text for the judge ──────────────────────
    add(
        f"{repo_id}:behaviour:what",
        "What is this project for, in one or two sentences?",
        "behaviour", "",
        answer="A summary of the repository's purpose derived from its files.",
    )
    add(
        f"{repo_id}:behaviour:flow",
        "How does a request flow through this codebase, from entry point to work being done?",
        "behaviour", "",
        answer="A description of the call path from an entry point to a sink.",
    )
    if _pick(files, 0):
        add(
            f"{repo_id}:behaviour:file",
            f"What does {_pick(files, 0)} do?",
            "behaviour", "",
            answer=f"A description of the responsibility of {_pick(files, 0)}.",
        )

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", nargs="*", default=DEFAULT_REPOS)
    ap.add_argument("--out", default=str(ROOT / "src" / "evaluation" / "eval_set.json"))
    ap.add_argument("--min-per-repo", type=int, default=8)
    args = ap.parse_args()

    all_rows: list[dict] = []
    for repo_id in args.repos:
        try:
            rows = build_for_repo(repo_id)
        except Exception as e:  # a repo may not be indexed yet
            print(f"  ! {repo_id}: {type(e).__name__}: {e}")
            continue
        if len(rows) < args.min_per_repo:
            print(f"  ! {repo_id}: only {len(rows)} examples, skipping")
            continue
        all_rows.extend(rows)
        print(f"  {repo_id}: {len(rows)} examples")

    if not all_rows:
        print("No examples generated. Is any repo indexed?")
        return 1

    # Deterministic 80/20 split, and never let one repo fall entirely on one
    # side: stride by position within each repo's own block.
    train, test = [], []
    blocks: dict[str, list[dict]] = {}
    for r in all_rows:
        blocks.setdefault(r["repo_id"], []).append(r)
    for rows in blocks.values():
        for i, r in enumerate(rows):
            (test if i % 5 == 0 else train).append(r)

    payload = {
        "version": 1,
        "note": "Labels derived from live Neo4j state by build_eval_set.py.",
        "repos": args.repos,
        "train": train,
        "test": test,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n")

    n_struct = sum(1 for r in all_rows if r["kind"] == "graph")
    print(f"\n  {len(all_rows)} examples across {len(blocks)} repos "
          f"({n_struct} objective / {len(all_rows)-n_struct} judge)")
    print(f"  train={len(train)}  test={len(test)}  -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
