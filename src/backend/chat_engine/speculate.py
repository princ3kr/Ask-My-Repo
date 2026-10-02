"""Speculative graph+vector retrieval.

Today the graph node runs to completion before vector retrieval starts, so the
two never overlap even though the vector search does not need the graph result:
it filters by filenames, but an unfiltered search is a valid first pass and a
refinement pass can follow once the graph lands.

`speculate` starts the unfiltered search on a worker thread and hands back a
callable that yields either the finished result or a skip. The graph node then
runs on the main thread. By the time it finishes, the search is usually done,
and the cost of waiting is bounded rather than additive.

A thread pool is used rather than asyncio because both backends are synchronous
blocking clients (the Neo4j driver and the Qdrant client here), so a thread is
the only thing that actually overlaps them.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor

logger = logging.getLogger("askmyrepo.speculate")

_POOL: ThreadPoolExecutor | None = None


def speculation_enabled() -> bool:
    """`ASK_NO_SPECULATE=1` forces every call to run inline.

    Both clients are synchronous and neither is documented as thread-safe, so
    this is the switch to reach for if a deployment ever sees cross-request
    interference. It also makes the latency win measurable, since the
    benchmark toggles it rather than reasoning about it.
    """
    return os.getenv("ASK_NO_SPECULATE", "0") != "1"


def pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        # Small and bounded: this is for overlapping two I/O calls per request,
        # not for saturating the database.
        _POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="speculate")
    return _POOL


def speculate(fn):
    """Start `fn` on a worker and return its Future.

    Returns None if speculation is disabled or the pool could not take the work,
    so the caller falls back to running it inline. Speculation is an
    optimisation; it must never be the reason a query fails.
    """
    if not speculation_enabled():
        return None
    try:
        return pool().submit(fn)
    except Exception as e:
        logger.warning("could not start speculative work (%s: %s)", type(e).__name__, e)
        return None


def resolve(future: Future | None, inline_fn):
    """Get a future's value, running the work inline if it never started.

    A failed speculative future is retried inline rather than propagated: the
    first attempt may have failed because it raced something, and a plain
    sequential call is the known-good path.
    """
    if future is None:
        return inline_fn()
    try:
        return future.result()
    except Exception as e:
        logger.warning(
            "speculative call failed (%s: %s); retrying inline", type(e).__name__, e
        )
        try:
            return inline_fn()
        except Exception as e2:
            logger.error("inline retry also failed: %s: %s", type(e2).__name__, e2)
            return []


def shutdown() -> None:
    """Drain the pool. Called from the app lifespan."""
    global _POOL
    if _POOL is not None:
        _POOL.shutdown(wait=True)
        _POOL = None
