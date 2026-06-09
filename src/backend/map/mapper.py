import sys
from typing import Callable, Optional

from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI

from src.backend.chunking.repo_parser import get_files, get_filename
from src.backend.chunking.chunk_builder import ChunkBuilder
from src.backend.services.vector_db import VectorStore

ProgressCallback = Callable[[str, int, str], None]


class EntryPointReview(BaseModel):
    path: str = Field(..., description="File path relative to repo root")
    is_entry: bool = Field(..., description="Whether this file is an application entry point")
    kind: str = Field(default="", description="Entry kind: http_endpoint, main_block, app_runner, cli_entry, task_entry, or empty")
    confidence: float = Field(default=0.0, description="Confidence score 0-1")
    reason: str = Field(default="", description="Brief reason for the classification")


class EntryPointReviewBatch(BaseModel):
    results: list[EntryPointReview]


def _llm_review_entry_points(files: dict, flagged_paths: list[str]) -> list[dict]:
    """Second-pass LLM review for uncertain entry point candidates."""
    if not flagged_paths:
        return []

    llm = ChatOpenAI(model="gpt-4o", temperature=0, max_tokens=2000, max_retries=3, timeout=60.0)
    structured_llm = llm.with_structured_output(EntryPointReviewBatch)

    file_summaries = []
    for path in flagged_paths:
        data = files.get(path, {})
        fn_names = [f["name"] for f in data.get("functions", [])]
        decorators = data.get("decorator_names", [])
        imports = [imp.get("module", "") for imp in data.get("imports", [])[:15]]
        file_summaries.append(
            f"PATH: {path}\n"
            f"FUNCTIONS: {', '.join(fn_names[:20]) or '(none)'}\n"
            f"DECORATORS: {', '.join(decorators[:15]) or '(none)'}\n"
            f"IMPORTS: {', '.join(imports) or '(none)'}"
        )

    prompt = f"""You are a Python codebase analyst. For each file below, determine if it is an application entry point.

Entry kinds:
- http_endpoint: FastAPI/Flask/Django route handlers or API bootstrap
- main_block: script entry via if __name__ == '__main__'
- app_runner: starts a web server or application loop (uvicorn, gunicorn, app.run)
- cli_entry: command-line interface entry (click, typer, argparse main)
- task_entry: background task worker entry (celery task definitions as entry)
- (empty string if not an entry point)

Only mark is_entry=true when reasonably confident. Conventional filenames like main.py, app.py, server.py
are often but not always entry points — use function names and imports as evidence.

FILES:
{chr(10).join(file_summaries)}
"""

    try:
        batch: EntryPointReviewBatch = structured_llm.invoke([
            ("system", "Classify Python file entry points. Return one result per file path."),
            ("user", prompt),
        ])
        merged = []
        for r in batch.results:
            if r.is_entry:
                fn_qname = None
                data = files.get(r.path, {})
                if data.get("functions"):
                    fn = data["functions"][0]
                    fn_qname = fn.get("qualified_name")
                merged.append({
                    "path": r.path,
                    "qualified_name": fn_qname or f"{r.path}::main",
                    "is_entry": True,
                    "kind": r.kind or "app_runner",
                    "confidence": r.confidence,
                    "source": "llm",
                    "reason": r.reason,
                })
                files.setdefault(r.path, {})["entry_points"] = files.get(r.path, {}).get("entry_points", []) + [{
                    "qualified_name": fn_qname or f"{r.path}::main",
                    "name": "main",
                    "kind": r.kind or "app_runner",
                    "confidence": r.confidence,
                    "source": "llm",
                    "reason": r.reason,
                }]
        return merged
    except Exception as e:
        print(f"[EntryPoint LLM] Review failed: {e}")
        return []


def map_repository(repo_url: str, on_progress: Optional[ProgressCallback] = None):
    def report(stage: str, progress: int, message: str):
        if on_progress:
            on_progress(stage, progress, message)

    report("fetching", 8, "Downloading your repository…")
    files = get_files(repo_url)
    repo_id = get_filename(repo_url)
    file_count = len(files)

    qdrant_collection_name = f"repo_{repo_id}"
    qdrant_exists = VectorStore.collection_exists(qdrant_collection_name)
    neo4j_exists = ChunkBuilder.repo_exists(repo_id)

    if qdrant_exists and neo4j_exists:
        report("assistant_ready", 95, "Repository already indexed — reusing existing search/graph data.")
        print("[+] Repository already indexed. Skipping re-index.")
        nodes_count, edges_count = ChunkBuilder.get_repo_graph_counts(repo_id)
        return {
            "repo_id": repo_id,
            "files": files,
            "nodes_count": nodes_count,
            "edges_count": edges_count,
        }

    if neo4j_exists:
        report("graph_building", 22, "Repository graph already exists — skipping graph rebuild.")
        nodes_count, edges_count = ChunkBuilder.get_repo_graph_counts(repo_id)
    else:
        report("graph_building", 22, f"Reading {file_count} files to understand the layout…")
        builder = ChunkBuilder(files=files, repo_id=repo_id)
        builder.build()

        report("graph_saving", 42, "Connecting files, classes, and dependencies…")
        builder.push_to_neo4j()

        flagged_paths = [p for p, d in files.items() if d.get("entry_flagged")]
        if flagged_paths:
            report("graph_saving", 45, f"Reviewing {len(flagged_paths)} potential entry points…")
            llm_entries = _llm_review_entry_points(files, flagged_paths)
            if llm_entries:
                ChunkBuilder.apply_entry_points(repo_id, llm_entries)
                print(f"[+] LLM entry point review: {len(llm_entries)} entries confirmed.")

        nodes_count = builder.G.number_of_nodes()
        edges_count = builder.G.number_of_edges()

    if qdrant_exists:
        report("vector_saving", 60, "Search index already exists — skipping vector push.")
    else:
        report("vector_building", 55, "Breaking code into easy-to-search pieces…")
        vstore = VectorStore(files=files, collection_name=qdrant_collection_name)
        vstore.build()

        def on_push(progress_pct: float, _detail: str):
            overall = 58 + int(progress_pct * 0.32)
            report("vector_saving", overall, "Making everything searchable…")

        report("vector_saving", 60, "Making everything searchable…")
        vstore.push(on_progress=on_push)

    report("assistant_ready", 95, "Almost ready — preparing your assistant…")
    print("[+] Repository indexing successfully completed!")
    return {
        "repo_id": repo_id,
        "files": files,
        "nodes_count": nodes_count,
        "edges_count": edges_count,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.backend.map.mapper <repo_url>")
        sys.exit(1)
    map_repository(sys.argv[1])
