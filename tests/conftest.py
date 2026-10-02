"""Shared fixtures.

No test may reach a live Neo4j, Qdrant, OpenAI or the network. The unit under
test is always constructed through `__new__` (or given a stub) so that
__init__'s live-client wiring is never invoked.
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Importing src.backend.* pulls in langchain/langgraph/qdrant. Guard the env
# so a developer's real .env is never consulted and a missing one can't fail
# collection.
os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASS", "test")
os.environ.setdefault("QDRANT_END_POINT", "http://localhost:6333")
os.environ.setdefault("QDRANT_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")


@pytest.fixture
def repo_id() -> str:
    return "acme-widget"


@pytest.fixture
def query_engine():
    """A QueryEngine with no live driver and no database.

    __init__ resolves real credentials and a real driver, so it is bypassed.
    Only the pure methods are exercised.
    """
    from src.backend.services.query_engine import QueryEngine

    qe = QueryEngine.__new__(QueryEngine)
    qe.repo_id = "acme-widget"
    qe.llm = None
    qe.db_client = None
    qe._file_index = None
    qe._entity_map = None
    qe._entity_names = []
    qe._file_names = []
    qe._graph_cache = {}
    return qe


@pytest.fixture
def vector_store():
    """A VectorStore with no Qdrant client — enough for the chunking maths."""
    from src.backend.services.vector_db import VectorStore

    vs = VectorStore.__new__(VectorStore)
    vs.files = None
    vs.collection_name = "repo_test"
    vs.chunks = []
    vs._reranker = None
    vs.client = None
    return vs
