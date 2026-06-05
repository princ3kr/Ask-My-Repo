import sys
from src.backend.chunking.repo_parser import get_files, get_filename
from src.backend.chunking.chunk_builder import ChunkBuilder
from src.backend.services.vector_db import VectorStore

def map_repository(repo_url: str):
    print(f"[*] Fetching files from {repo_url}...")
    files = get_files(repo_url)
    repo_id = get_filename(repo_url)
    
    # 1. Neo4j Graph DB Ingestion
    print("[*] Processing AST structure and pushing to Neo4j...")
    builder = ChunkBuilder(files=files, repo_id=repo_id)
    builder.build()          # populate dependency graph (must run before push_to_neo4j)
    builder.push_to_neo4j()
    
    # 2. Qdrant Vector DB Ingestion
    print("[*] Splitting files into code chunks and indexing in Qdrant...")
    vstore = VectorStore(files=files, collection_name=f"repo_{repo_id}")
    vstore.build()
    vstore.push()
    
    print("[+] Repository indexing successfully completed!")
    return repo_id, files

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.backend.map.mapper <repo_url>")
        sys.exit(1)
    map_repository(sys.argv[1])