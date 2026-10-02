"""Process-scoped database clients and models.

Every one of these is expensive to construct and safe to share:

  * QdrantClient + set_model  — loads the dense ONNX weights and the BM25
    sparse model into memory. Constructing one per chat session meant N open
    tabs held N copies of the embedding model.
  * GraphDatabase.driver      — is a connection *pool*, designed to be
    long-lived and reused. Building one per session leaked pools.

The singletons are created lazily and guarded by a lock, so importing this
module never performs I/O (important for tests and CLI tools that only need
part of the stack).
"""
import os
import threading

from dotenv import load_dotenv
from neo4j import GraphDatabase
from qdrant_client import QdrantClient

load_dotenv()

_lock = threading.Lock()
_qdrant_client = None
_neo4j_driver = None

EMBED_MODEL = os.getenv("EMBED_MODEL", "jinaai/jina-embeddings-v2-base-code")
SPARSE_MODEL = os.getenv("SPARSE_MODEL", "Qdrant/bm25")


def _model_cache_dir() -> str:
    """Mirror of the path logic in vector_db.py (repo-local model cache)."""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    cache_dir = os.path.join(base_dir, "src", "models")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "fastembed")


def get_qdrant_client() -> QdrantClient:
    """Shared Qdrant client with the dense and sparse models already loaded."""
    global _qdrant_client
    if _qdrant_client is not None:
        return _qdrant_client

    with _lock:
        if _qdrant_client is None:
            url = os.getenv("QDRANT_END_POINT")
            api_key = os.getenv("QDRANT_API_KEY")
            if not url or not api_key:
                raise ValueError("Please set QDRANT_END_POINT and QDRANT_API_KEY in your .env file!")

            cache_dir = _model_cache_dir()
            client = QdrantClient(url=url, api_key=api_key, timeout=60)
            client.set_model(EMBED_MODEL, cache_dir=cache_dir)
            client.set_sparse_model(SPARSE_MODEL, cache_dir=cache_dir)
            _qdrant_client = client
    return _qdrant_client


def get_neo4j_driver():
    """Shared Neo4j driver (connection pool)."""
    global _neo4j_driver
    if _neo4j_driver is not None:
        return _neo4j_driver

    with _lock:
        if _neo4j_driver is None:
            uri = os.getenv("NEO4J_URI")
            user = os.getenv("NEO4J_USER")
            password = os.getenv("NEO4J_PASS")
            if not uri or not user or not password:
                raise ValueError(
                    "Neo4j credentials are not set. Set NEO4J_URI, NEO4J_USER and NEO4J_PASS."
                )
            _neo4j_driver = GraphDatabase.driver(uri, auth=(user, password))
    return _neo4j_driver


def warm_up() -> None:
    """Load the shared clients at startup so no request pays the cost.

    Failures are logged rather than raised: a missing Qdrant collection or
    an unreachable database should surface on the first real request with a
    useful error, not prevent the server from booting.
    """
    try:
        get_qdrant_client()
        print("[Warmup] Qdrant client + embedding models ready.")
    except Exception as e:
        print(f"[Warmup] Qdrant unavailable: {e}")

    try:
        driver = get_neo4j_driver()
        # verify_connectivity() already leaves a pooled connection behind
        # (measured: the first query after it runs in ~85ms). An extra
        # priming query was added, measured as no help, and removed. Note
        # that *additional concurrent* connections to Aura still cost ~390ms
        # each regardless of warming -- see the note in api.get_graph_data.
        driver.verify_connectivity()
        print("[Warmup] Neo4j driver connected.")
    except Exception as e:
        print(f"[Warmup] Neo4j unavailable: {e}")


def close_all() -> None:
    global _qdrant_client, _neo4j_driver
    with _lock:
        if _neo4j_driver is not None:
            try:
                _neo4j_driver.close()
            except Exception:
                pass
            _neo4j_driver = None
        _qdrant_client = None
