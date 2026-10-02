"""The TTL/LRU caches and their key construction."""
import time

import pytest

from src.backend.services.cache import (
    TTLCache,
    all_stats,
    answer_key,
    graph_key,
    invalidate_repo,
    normalize,
    repo_prefix,
    rewrite_key,
    route_key,
    view_key,
)


class TestTTLCache:
    def test_put_get(self):
        c = TTLCache(max_entries=10, ttl_secs=60, name="t")
        c.put("a", 1)
        assert c.get("a") == 1

    def test_never_stores_none(self):
        c = TTLCache()
        c.put("a", None)
        assert c.get("a") is None
        assert c.stats()["size"] == 0

    def test_expired_entry_is_a_miss(self):
        c = TTLCache(max_entries=10, ttl_secs=0, name="t")
        c.put("a", 1)
        time.sleep(0.01)
        assert c.get("a") is None
        assert c.stats()["size"] == 0

    def test_lru_eviction(self):
        c = TTLCache(max_entries=3, ttl_secs=60, name="t")
        for k in "abcd":
            c.put(k, k)
        assert c.stats()["size"] == 3
        assert c.get("a") is None  # oldest evicted
        assert c.get("d") == "d"

    def test_get_moves_to_end(self):
        c = TTLCache(max_entries=2, ttl_secs=60, name="t")
        c.put("a", 1)
        c.put("b", 2)
        c.get("a")          # 'a' is now most-recent
        c.put("c", 3)       # evicts 'b'
        assert c.get("b") is None
        assert c.get("a") == 1

    def test_get_or_set_only_computes_on_miss(self):
        c = TTLCache()
        calls = []

        def factory():
            calls.append(1)
            return "v"

        assert c.get_or_set("k", factory) == "v"
        assert c.get_or_set("k", factory) == "v"
        assert len(calls) == 1

    def test_invalidate_prefix(self):
        c = TTLCache()
        c.put("repo_a:1", 1)
        c.put("repo_a:2", 2)
        c.put("repo_b:1", 3)
        assert c.invalidate_prefix("repo_a:") == 2
        assert c.get("repo_b:1") == 3

    def test_hit_rate(self):
        c = TTLCache()
        c.put("a", 1)
        c.get("a")
        c.get("missing")
        assert c.stats()["hit_rate"] == 0.5

    def test_concurrent_access_is_safe(self):
        import threading

        c = TTLCache(max_entries=500, ttl_secs=60, name="t")
        errors = []

        def worker(n):
            try:
                for i in range(200):
                    c.put(f"k{n}-{i}", i)
                    c.get(f"k{n}-{i}")
                    c.stats()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


class TestNormalize:
    @pytest.mark.parametrize("a,b", [
        ("Which Files import X?", "which files import x"),
        ("  spaced   out  ", "spaced out"),
        ("hyphen-ated", "hyphen ated"),
    ])
    def test_case_punct_and_space_insensitive(self, a, b):
        assert normalize(a) == normalize(b)


class TestKeys:
    def test_route_key_ignores_punctuation_and_case(self):
        assert route_key("Which Files?") == route_key("which files")

    def test_graph_key_is_scoped_to_the_repo(self):
        """Results belong to one repository's graph, so a query-only key would
        serve the previous user's repo."""
        assert graph_key("repo1", "q") != graph_key("repo2", "q")

    def test_answer_key_includes_repo_prompt_and_model(self):
        base = answer_key("r", "q", "v1", "gpt-4o")
        assert base != answer_key("r2", "q", "v1", "gpt-4o")
        assert base != answer_key("r", "q", "v2", "gpt-4o")
        assert base != answer_key("r", "q", "v1", "gpt-5")
        assert base == answer_key("r", "q", "v1", "gpt-4o")

    def test_rewrite_key_depends_on_history(self):
        """The rewrite is a function of the query *plus* the conversation, so
        two different histories must not collide."""
        a = rewrite_key("what about it?", [{"role": "user", "content": "alpha"}])
        b = rewrite_key("what about it?", [{"role": "user", "content": "beta"}])
        assert a != b

    def test_rewrite_key_stable_for_identical_history(self):
        h = [{"role": "user", "content": "alpha"}]
        assert rewrite_key("q", h) == rewrite_key("q", list(h))

    def test_rewrite_key_handles_missing_history(self):
        assert rewrite_key("q", []) == rewrite_key("q", None)

    def test_view_key(self):
        assert view_key("r", "system_overview") != view_key("r2", "system_overview")


class TestRepoScoping:
    def test_prefix_matches_its_own_caches_keys(self):
        for name in ("route", "rewrite", "graph", "answer", "view"):
            prefix = repo_prefix(name, "acme")
            assert prefix == f"{name}|acme|"

    def test_invalidate_repo_clears_every_cache_for_that_repo(self):
        from src.backend.services.cache import caches

        for cache in caches.values():
            cache.clear()

        caches["graph"].put("graph|acme|x", [1])
        caches["graph"].put("graph|other|x", [2])
        caches["view"].put("view|acme|y", [3])

        assert invalidate_repo("acme") == 2
        assert caches["graph"].get("graph|acme|x") is None
        assert caches["view"].get("view|acme|y") is None
        # Another repo's data must survive.
        assert caches["graph"].get("graph|other|x") == [2]

    def test_all_stats_reports_every_cache(self):
        stats = all_stats()
        assert set(stats) == {"route", "rewrite", "graph", "answer", "view"}
        for s in stats.values():
            assert {"name", "size", "max_entries", "ttl_secs", "hits", "misses"} <= set(s)
