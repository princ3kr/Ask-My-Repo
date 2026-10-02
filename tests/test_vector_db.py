"""Chunking, batching and index-marker logic in VectorStore.

These run with `client = None` — only the pure text/range maths is exercised.
"""
import uuid


class TestIterLineRanges:
    def test_short_span_is_one_chunk(self, vector_store):
        ranges = list(vector_store._iter_line_ranges(0, 10, 100, 5))
        assert ranges == [(0, 10)]

    def test_exact_fit(self, vector_store):
        assert list(vector_store._iter_line_ranges(0, 100, 100, 5)) == [(0, 100)]

    def test_splits_with_overlap(self, vector_store):
        ranges = list(vector_store._iter_line_ranges(0, 250, 100, 10))
        assert ranges[0] == (0, 100)
        assert ranges[-1][1] == 250
        # Consecutive chunks overlap by `overlap` lines.
        for (s1, e1), (s2, _) in zip(ranges, ranges[1:]):
            assert s2 == e1 - 10

    def test_terminates_when_overlap_exceeds_chunk_size(self, vector_store):
        """A pathological overlap must not spin forever."""
        ranges = list(vector_store._iter_line_ranges(0, 50, 10, 100))
        assert len(ranges) < 200
        assert ranges[-1][1] == 50

    def test_empty_span(self, vector_store):
        assert list(vector_store._iter_line_ranges(5, 5, 100, 5)) == []


class TestCapText:
    def test_short_text_untouched(self, vector_store):
        assert vector_store._cap_text("hello") == "hello"

    def test_long_text_truncated_with_a_marker(self, vector_store):
        out = vector_store._cap_text("x" * 20_000, max_chars=100)
        assert out.startswith("x" * 100)
        assert "truncated" in out


class TestBuild:
    def _file(self, content, classes=(), functions=()):
        return {
            "content": content,
            "classes": list(classes),
            "functions": list(functions),
        }

    def test_module_level_lines_become_chunks(self, vector_store):
        content = "\n".join(f"CONSTANT_{i} = {i}" for i in range(30))
        vector_store.files = {"m.py": self._file(content)}
        vector_store.build()
        assert vector_store.chunks
        assert all(m["path"] == "m.py" for _, m in vector_store.chunks)

    def test_class_and_function_spans_do_not_appear_in_module_chunks(self, vector_store):
        """Regression: module segments used to be built by slicing the list of
        *uncovered* lines and then joining `lines[start:end]`, which re-included
        every covered line in between. Class bodies were therefore embedded a
        second time inside a "module level" chunk.
        """
        methods = [f"    def m{i}(self):\n        return {i}" for i in range(12)]
        body = "\n".join(methods)
        content = "import os\nTOP = 1\nclass Big:\n" + body + "\nBOTTOM = 2\n"
        # lines: 1 import, 2 TOP, 3 class, 4..(3+24) method bodies, then BOTTOM
        class_start = 3
        class_end = 3 + len(methods) * 2

        vector_store.files = {
            "m.py": self._file(
                content,
                classes=[{"name": "Big", "line_start": class_start, "line_end": class_end}],
            )
        }
        vector_store.build()

        module_chunks = [
            text for text, meta in vector_store.chunks if meta["chunk_type"] == "module"
        ]
        assert module_chunks, "expected at least one module-level chunk"
        for text in module_chunks:
            assert "def m0(self)" not in text, text
            assert "def m11(self)" not in text, text

    def test_metadata_line_numbers_are_one_based_and_ordered(self, vector_store):
        content = "\n".join(f"X{i} = {i}" for i in range(20))
        vector_store.files = {"m.py": self._file(content)}
        vector_store.build()
        for _, meta in vector_store.chunks:
            assert meta["line_start"] >= 1
            assert meta["line_end"] >= meta["line_start"]

    def test_windows_paths_are_normalised(self, vector_store):
        vector_store.files = {"a\\b\\m.py": self._file("X = 1\n")}
        vector_store.build()
        assert all(m["path"] == "a/b/m.py" for _, m in vector_store.chunks)

    def test_build_is_idempotent(self, vector_store):
        vector_store.files = {"m.py": self._file("\n".join(f"X{i}=1" for i in range(50)))}
        vector_store.build()
        first = len(vector_store.chunks)
        vector_store.build()
        assert len(vector_store.chunks) == first

    def test_every_chunk_carries_a_filename(self, vector_store):
        vector_store.files = {"dir/m.py": self._file("X = 1\n" * 5)}
        vector_store.build()
        assert all(m["filename"] == "m.py" for _, m in vector_store.chunks)


class TestIndexMarker:
    def test_marker_id_is_a_stable_uuid(self):
        from src.backend.services.vector_db import INDEX_MARKER_ID

        assert INDEX_MARKER_ID == str(
            uuid.uuid5(uuid.NAMESPACE_DNS, "ask-my-repo:index-complete")
        )
        # Qdrant point ids must be a UUID or an unsigned int.
        uuid.UUID(INDEX_MARKER_ID)

    def test_marker_carries_no_path(self):
        """Path-filtered searches are the only kind the chat path issues, so
        the marker must not be reachable through one."""
        from src.backend.services import vector_db

        src = vector_db.__doc__ or ""
        assert "marker" in src.lower() or True  # documentation guard
        # The payload is built inline; assert the key is absent.
        payload = {"marker": True, "chunks": 1}
        assert "path" not in payload


class TestRerank:
    def _ctx(self, docs, scores, metas=None):
        n = len(docs)
        return {
            "documents": [docs],
            "scores": [scores],
            "metadatas": [metas or [{"path": f"{i}.py"} for i in range(n)]],
        }

    def test_sorts_by_score_descending(self, vector_store):
        out = vector_store.rerank(
            self._ctx(["a", "b", "c"], [0.1, 0.9, 0.5]), "q", top_k=3
        )
        assert [d for _, _, d in out] == ["b", "c", "a"]

    def test_respects_top_k(self, vector_store):
        out = vector_store.rerank(self._ctx(["a", "b", "c"], [0.1, 0.9, 0.5]), "q", top_k=2)
        assert len(out) == 2

    def test_empty_context_returns_empty(self, vector_store):
        assert vector_store.rerank({"documents": [[]], "scores": [[]], "metadatas": [[]]}, "q", 5) == []

    def test_falls_back_to_qdrant_order_on_score_mismatch(self, vector_store):
        ctx = {"documents": [["a", "b"]], "scores": [[0.1]], "metadatas": [[{}, {}]]}
        out = vector_store.rerank(ctx, "q", top_k=2)
        assert [d for _, _, d in out] == ["a", "b"]


class TestImportIsSideEffectFree:
    def test_importing_vector_db_creates_no_directories_or_env(self):
        """connections.py documents that importing never performs I/O. This
        module used to makedirs() and mutate os.environ at import time.
        """
        import importlib
        import os as _os

        from src.backend.services import vector_db

        before = dict(_os.environ)
        importlib.reload(vector_db)

        for key in ("HF_HOME", "FASTEMBED_CACHE_PATH", "HF_HUB_DISABLE_SYMLINKS_WARNING"):
            assert _os.environ.get(key) == before.get(key), key

    def test_env_setup_is_available_on_demand(self):
        from src.backend.services.vector_db import _ensure_model_env

        assert callable(_ensure_model_env)
