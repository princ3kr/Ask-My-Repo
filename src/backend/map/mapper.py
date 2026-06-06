import sys
from typing import Callable, Optional

from src.backend.chunking.repo_parser import get_files, get_filename
from src.backend.chunking.chunk_builder import ChunkBuilder
from src.backend.services.vector_db import VectorStore

ProgressCallback = Callable[[str, int, str], None]


def map_repository(repo_url: str, on_progress: Optional[ProgressCallback] = None):
    def report(stage: str, progress: int, message: str):
        if on_progress:
            on_progress(stage, progress, message)

    report("fetching", 8, "Downloading your repository…")
    files = get_files(repo_url)
    repo_id = get_filename(repo_url)
    file_count = len(files)

    report("graph_building", 22, f"Reading {file_count} files to understand the layout…")
    builder = ChunkBuilder(files=files, repo_id=repo_id)
    builder.build()

    report("graph_saving", 42, "Connecting files, classes, and dependencies…")
    builder.push_to_neo4j()
    nodes_count = builder.G.number_of_nodes()
    edges_count = builder.G.number_of_edges()

    report("vector_building", 55, "Breaking code into easy-to-search pieces…")
    vstore = VectorStore(files=files, collection_name=f"repo_{repo_id}")
    vstore.reset_collection()
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