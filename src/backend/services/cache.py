"""Bounded, namespaced caches for the query path.

Design notes (these are the corrections to the original cache proposal):

  * Cypher is keyed by repo_id as well as the query. Generated Cypher embeds
    `repo_id` in its text, so a query-only key would return the previous
    user's repo.
  * Route and rewrite are cached separately. The route is a function of the
    raw query; the rewrite is a function of the raw query *plus* history.
  * Failures are never memoised. A transient error would otherwise become a
    permanent one.
  * Precomputed views (system overview, dependency map) are computed lazily on
    first use, not at index time — indexing is already the slow path.
  * Everything is bounded (LRU) and TTL'd, and guarded by a lock because parse
    jobs and chat requests run on different threads.
"""
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

MAX_ENTRIES = int(os.getenv("CACHE_MAX_ENTRIES", "500"))
TTL_SECS = int(os.getenv("CACHE_TTL_SECS", "1800"))


class TTLCache:
    """Thread-safe LRU cache with a TTL. Never stores None."""

    def __init__(self, max_entries: int = MAX_ENTRIES, ttl_secs: int = TTL_SECS, name: str = "cache"):
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max(1, max_entries)
        self._ttl = ttl_secs
        self._name = name
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            stored_at, value = entry
            if time.time() - stored_at > self._ttl:
                self._data.pop(key, None)
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: str, value: Any) -> None:
        if value is None:
            return
        with self._lock:
            self._data[key] = (time.time(), value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def get_or_set(self, key: str, factory: Callable[[], Any]) -> Any:
        hit = self.get(key)
        if hit is not None:
            return hit
        value = factory()
        self.put(key, value)
        return value

    def invalidate_prefix(self, prefix: str) -> int:
        """Drop every entry whose key starts with `prefix`. Returns the count."""
        with self._lock:
            doomed = [k for k in self._data if k.startswith(prefix)]
            for k in doomed:
                self._data.pop(k, None)
            return len(doomed)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "name": self._name,
                "size": len(self._data),
                "max_entries": self._max,
                "ttl_secs": self._ttl,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else None,
            }


# ── normalisation ────────────────────────────────────────────
# Exact-match keys only. A semantic cache is tempting here but "which files
# import X" and "which files import Y" are near neighbours with opposite
# answers, so similarity-based reuse is a correctness hazard.
_PUNCT = re.compile(r"[^\w\s]")


def normalize(text: str) -> str:
    """Case-, whitespace- and punctuation-insensitive key form."""
    return " ".join(_PUNCT.sub(" ", text.lower()).split())


def route_key(query: str) -> str:
    return f"route|{normalize(query)}"


def rewrite_key(query: str, history: list) -> str:
    # History-dependent: a different conversation is a different question.
    tail = normalize(" ".join(
        str(m.get("content", "")) if isinstance(m, dict) else str(m) for m in (history or [])[-4:]
    ))
    return f"rewrite|{normalize(query)}|{hash(tail) & 0xFFFFFFFF:08x}"


def graph_key(repo_id: str, query: str) -> str:
    # repo_id is required: results are scoped to one repository's graph.
    return f"graph|{repo_id}|{normalize(query)}"


def answer_key(repo_id: str, standalone_query: str, prompt_version: str, model: str) -> str:
    return f"answer|{repo_id}|{normalize(standalone_query)}|{prompt_version}|{model}"


def view_key(repo_id: str, view: str) -> str:
    return f"view|{repo_id}|{view}"


def repo_prefix(cache_name: str, repo_id: str) -> str:
    return f"{cache_name}|{repo_id}|"


# ── process-wide instances ───────────────────────────────────
caches = {
    "route": TTLCache(name="route"),
    "rewrite": TTLCache(name="rewrite"),
    "graph": TTLCache(name="graph"),
    "answer": TTLCache(name="answer"),
    "view": TTLCache(name="view"),
}


def get(name: str) -> TTLCache:
    return caches[name]


def invalidate_repo(repo_id: str) -> int:
    """Drop every cached artefact for one repo. Called from cleanup."""
    total = 0
    for name, cache in caches.items():
        total += cache.invalidate_prefix(repo_prefix(name, repo_id))
    return total


def all_stats() -> dict:
    return {name: c.stats() for name, c in caches.items()}
