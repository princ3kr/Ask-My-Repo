"""Tests for the DSPy bridge: metrics, gates, runtime fallback, speculation.

The rule these protect: DSPy is an *optional* accelerator. Nothing here should
fail, raise a different exception, or change an answer when DSPy is disabled,
misconfigured, or broken. Tests assert that behaviour directly rather than
trusting the wiring to be correct by inspection.
"""
import os
import sys
import time
from pathlib import Path

import pytest
from langchain_core.runnables import Runnable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── path metrics ────────────────────────────────────────────────────────────
class TestPathMetrics:
    def test_exact_match_scores_one(self):
        from src.backend.dspy_bridge.metrics import path_f1

        f1, p, r = path_f1(["a.py", "b.py"], ["a.py", "b.py"])
        assert (f1, p, r) == (1.0, 1.0, 1.0)

    def test_bare_filename_matches_full_path(self):
        """A question says "repo_parser.py"; the graph stores a full path."""
        from src.backend.dspy_bridge.metrics import path_f1

        f1, p, r = path_f1(["repo_parser.py"], ["src/backend/chunking/repo_parser.py"])
        assert p == 1.0
        assert r == 1.0
        assert f1 == 1.0

    def test_missing_half_penalises_both_directions(self):
        from src.backend.dspy_bridge.metrics import path_f1

        f1, p, r = path_f1(["a.py"], ["a.py", "b.py"])
        assert r == 0.5 and p == 1.0

    def test_empty_prediction_scores_zero_not_one(self):
        """The failure this guards: a query that returns nothing is not correct."""
        from src.backend.dspy_bridge.metrics import path_f1

        f1, p, r = path_f1([], ["a.py"])
        assert f1 == 0.0 and r == 0.0

    def test_both_empty_is_vacuously_correct(self):
        from src.backend.dspy_bridge.metrics import path_f1

        assert path_f1([], [])[0] == 1.0

    def test_repeated_predictions_collapse(self):
        """Saying the same file twice is not a second claim, so it must not
        dilute precision — the gold is a set and predictions are compared as one."""
        from src.backend.dspy_bridge.metrics import path_f1

        f1, p, r = path_f1(["x.py", "x.py"], ["dir/x.py"])
        assert (p, r) == (1.0, 1.0)

    def test_two_spellings_of_one_gold_count_as_one_match(self):
        """The guard against over-crediting: a bare name and the full path are
        the same file, so precision must not exceed 1.0 by matching one gold twice."""
        from src.backend.dspy_bridge.metrics import path_f1

        f1, p, r = path_f1(["x.py", "dir/x.py"], ["dir/x.py"])
        assert p == 0.5
        assert r == 1.0
        assert f1 < 1.0


class TestCitationGrounding:
    def test_invented_filename_is_not_grounded(self):
        from src.backend.dspy_bridge.metrics import citation_precision

        answer = "The cache lives in src/backend/services/cache_magic.py."
        context = "File src/backend/services/cache.py defines get()."
        assert citation_precision(answer, context) == 0.0

    def test_real_citation_is_grounded(self):
        from src.backend.dspy_bridge.metrics import citation_precision

        answer = "Defined in src/backend/services/cache.py."
        context = "src/backend/services/cache.py contains the cache logic."
        assert citation_precision(answer, context) == 1.0

    def test_no_citations_scores_zero(self):
        from src.backend.dspy_bridge.metrics import citation_precision

        assert citation_precision("It is handled by the router.", "anything") == 0.0

    def test_partial_credit(self):
        from src.backend.dspy_bridge.metrics import citation_precision

        answer = "See src/backend/services/cache.py and src/backend/fake.py."
        context = "src/backend/services/cache.py exists."
        assert citation_precision(answer, context) == 0.5


class TestStructuralMetric:
    def test_returning_nothing_scores_zero(self):
        from src.backend.dspy_bridge.metrics import structural_metric

        metric = structural_metric(lambda c, r: [])
        ex = type("E", (), {"repo_id": "r", "expected_paths": ["a.py"]})()
        pred = type("P", (), {"cypher": "MATCH (n) RETURN n"})()
        assert metric(ex, pred) == 0.0

    def test_executor_exception_scores_zero_not_raises(self):
        """A query that cannot run must not abort an optimisation run."""
        from src.backend.dspy_bridge.metrics import structural_metric

        def boom(cypher, repo_id):
            raise RuntimeError("syntax error")

        metric = structural_metric(boom)
        ex = type("E", (), {"repo_id": "r", "expected_paths": ["a.py"]})()
        pred = type("P", (), {"cypher": "bad"})()
        assert metric(ex, pred) == 0.0

    def test_correct_query_scores_one(self):
        from src.backend.dspy_bridge.metrics import structural_metric

        metric = structural_metric(lambda c, r: ["src/a.py"])
        ex = type("E", (), {"repo_id": "r", "expected_paths": ["src/a.py"]})()
        pred = type("P", (), {"cypher": "MATCH ... RETURN f.path"})()
        assert metric(ex, pred) == 1.0


# ── rewriter gate ───────────────────────────────────────────────────────────
class TestRewriterGate:
    HISTORY = [{"role": "user", "content": "What does QueryEngine do?"},
               {"role": "assistant", "content": "It runs Cypher."}]

    def test_no_history_never_rewrites(self):
        from src.backend.chat_engine.gates import needs_rewrite

        assert needs_rewrite("And what does it return?", []) is False

    def test_self_contained_question_skips_the_call(self):
        from src.backend.chat_engine.gates import needs_rewrite

        q = "How does the vector store rank results?"
        assert needs_rewrite(q, self.HISTORY) is False

    def test_dangling_pronoun_still_rewrites(self):
        from src.backend.chat_engine.gates import needs_rewrite

        assert needs_rewrite("What does it return?", self.HISTORY) is True

    def test_named_target_skips_the_call(self):
        from src.backend.chat_engine.gates import needs_rewrite

        assert needs_rewrite("What does QueryEngine.graph_search return?", self.HISTORY) is False

    def test_leading_pronoun_with_target_rewrites(self):
        """A leading "it" has no referent even when a target appears later."""
        from src.backend.chat_engine.gates import needs_rewrite

        assert needs_rewrite("It calls QueryEngine.graph_search, right?", self.HISTORY) is True

    def test_gate_can_be_disabled(self, monkeypatch):
        """Disabling is what measures what the gate is worth."""
        from src.backend.chat_engine.gates import needs_rewrite

        monkeypatch.setenv("ASK_NO_GATES", "1")
        assert needs_rewrite("How does it work?", []) is True


# ── runtime fallback ────────────────────────────────────────────────────────
class TestRuntimeFallback:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("ASK_DSPY", raising=False)
        from src.backend.dspy_bridge.runtime import DspyRuntime

        assert DspyRuntime().enabled is False

    def test_enabled_by_opt_in(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        from src.backend.dspy_bridge.runtime import DspyRuntime

        assert DspyRuntime().enabled is True

    def test_every_call_returns_none_when_disabled(self, monkeypatch):
        """The whole safety story: with DSPy off, no model is ever contacted."""
        monkeypatch.delenv("ASK_DSPY", raising=False)
        from src.backend.dspy_bridge.runtime import DspyRuntime

        rt = DspyRuntime()
        assert rt.route("what imports x?") is None
        assert rt.rewrite("what about it?", "hist") is None
        assert rt.cypher("which files import x?", "repo") is None
        assert rt.synthesize("q", "ctx", "") is None
        assert rt.verify("q", "ctx", "draft") is None
        assert rt.stats.summary() == {}

    def test_program_failure_falls_back_instead_of_raising(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        from src.backend.dspy_bridge.runtime import DspyRuntime

        rt = DspyRuntime()

        def boom():
            raise RuntimeError("model unavailable")

        rt.program = lambda name: boom if name == "route" else None
        assert rt.route("q") is None
        assert rt.stats.summary()["route"]["errors"] == 1

    def test_unknown_route_is_rejected(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        from src.backend.dspy_bridge.runtime import DspyRuntime

        rt = DspyRuntime()
        rt.program = lambda name: (lambda **kw: type(
            "O", (), {"route": "teleport", "reason": "why"}
        )())
        assert rt.route("q") is None

    def test_route_aliases_are_normalised(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        from src.backend.dspy_bridge.runtime import DspyRuntime

        rt = DspyRuntime()
        for alias, expected in [
            ("graph_only", "graph"), ("graph", "graph"),
            ("architecture", "architecture"), ("hybrid", "hybrid"),
        ]:
            rt.program = lambda name, a=alias: (lambda **kw: type(
                "O", (), {"route": a, "reason": "r"}
            )())
            assert rt.route("q")[0] == expected

    def test_empty_synthesis_is_treated_as_failure(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        from src.backend.dspy_bridge.runtime import DspyRuntime

        rt = DspyRuntime()
        rt.program = lambda name: (lambda **kw: type(
            "O", (), {"answer": "  ", "confidence": 0.9, "used_files": ""}
        )())
        assert rt.synthesize("q", "ctx") is None

    def test_confidence_is_clamped(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        from src.backend.dspy_bridge.runtime import DspyRuntime

        rt = DspyRuntime()
        rt.program = lambda name: (lambda **kw: type(
            "O", (), {"answer": "text", "confidence": 4.2, "used_files": "a.py"}
        )())
        assert rt.synthesize("q", "ctx")["score"] == 1.0

    def test_non_numeric_confidence_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        from src.backend.dspy_bridge.runtime import DspyRuntime

        rt = DspyRuntime()
        rt.program = lambda name: (lambda **kw: type(
            "O", (), {"answer": "t", "confidence": "very high", "used_files": ""}
        )())
        assert rt.synthesize("q", "ctx")["score"] == 0.0

    def test_bad_verdict_is_rejected(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        from src.backend.dspy_bridge.runtime import DspyRuntime

        rt = DspyRuntime()
        rt.program = lambda name: (lambda **kw: type(
            "O", (), {"verdict": "probably fine", "worst_gap": "?"}
        )())
        assert rt.verify("q", "c", "d") is None

    def test_singleton_is_reused(self, monkeypatch):
        from src.backend.dspy_bridge.runtime import DspyRuntime

        assert DspyRuntime.get() is DspyRuntime.get()


class TestStats:
    def test_reports_p50_and_max(self):
        from src.backend.dspy_bridge.runtime import Stats

        s = Stats()
        for ms in (10, 20, 30, 40, 500):
            s.record("route", ms, True)
        summary = s.summary()["route"]
        assert summary["calls"] == 5
        assert summary["p50_ms"] == 30.0
        assert summary["max_ms"] == 500.0

    def test_records_failures_separately(self):
        from src.backend.dspy_bridge.runtime import Stats

        s = Stats()
        s.record("verify", 5, True)
        s.record("verify", 5, False)
        assert s.summary()["verify"]["errors"] == 1


# ── speculation ─────────────────────────────────────────────────────────────
class TestSpeculation:
    def test_future_result_is_returned(self):
        from src.backend.chat_engine import speculate

        fut = speculate.speculate(lambda: [1, 2, 3])
        assert speculate.resolve(fut, lambda: []) == [1, 2, 3]

    def test_none_future_runs_inline(self):
        from src.backend.chat_engine import speculate

        assert speculate.resolve(None, lambda: ["inline"]) == ["inline"]

    def test_failed_future_is_retried_inline(self):
        """A speculative failure must not cost the query its results."""
        from src.backend.chat_engine import speculate

        def boom():
            raise RuntimeError("raced")

        fut = speculate.speculate(boom)
        with pytest.raises(RuntimeError):
            fut.result()
        assert speculate.resolve(fut, lambda: ["ok"]) == ["ok"]

    def test_overlapping_is_faster_than_sequential(self):
        """The actual latency claim, measured rather than asserted."""
        from src.backend.chat_engine import speculate

        delay = 0.25

        def slow():
            time.sleep(delay)
            return "graph"

        def slow_vector():
            time.sleep(delay)
            return "vector"

        start = time.perf_counter()
        fut = speculate.speculate(slow_vector)
        slow()
        speculate.resolve(fut, slow_vector)
        overlapped = time.perf_counter() - start

        start = time.perf_counter()
        slow()
        slow_vector()
        sequential = time.perf_counter() - start

        assert overlapped < sequential * 0.75

    def test_pool_is_bounded_and_shared(self):
        from src.backend.chat_engine import speculate

        assert speculate.pool() is speculate.pool()
        assert speculate.pool()._max_workers == 2

    def test_disable_flag_forces_inline(self, monkeypatch):
        """The switch to reach if a deployment ever sees cross-request
        interference, since neither client is documented as thread-safe."""
        from src.backend.chat_engine import speculate

        monkeypatch.setenv("ASK_NO_SPECULATE", "1")
        assert speculate.speculation_enabled() is False
        assert speculate.speculate(lambda: [1]) is None

    def test_enabled_by_default(self, monkeypatch):
        from src.backend.chat_engine import speculate

        monkeypatch.delenv("ASK_NO_SPECULATE", raising=False)
        assert speculate.speculation_enabled() is True


# ── program shape ───────────────────────────────────────────────────────────
class TestProgramShape:
    def test_cypher_program_needs_no_schema_argument(self):
        """DSPy calls forward() with only the inputs the example declares, so a
        required third parameter scores zero on every example rather than
        raising where it would be noticed."""
        import inspect

        from src.backend.dspy_bridge.programs import GenerateCypher

        params = inspect.signature(GenerateCypher.forward).parameters
        required = [
            n for n, p in params.items()
            if n != "self" and p.default is inspect.Parameter.empty
            and p.kind is not inspect.Parameter.VAR_KEYWORD
        ]
        assert required == ["question", "repo_id"], required

    def test_graph_schema_describes_the_imports_edge(self):
        """The model reached for Import nodes because the schema did not say
        that file dependencies live on (File)-[:IMPORTS]->(File)."""
        from src.backend.dspy_bridge.programs import FALLBACK_SCHEMA

        assert "(File)-[:IMPORTS]->(File)" in FALLBACK_SCHEMA
        assert "repo_id" in FALLBACK_SCHEMA

    def test_fallback_schema_has_no_write_verbs(self):
        from src.backend.dspy_bridge.programs import FALLBACK_SCHEMA

        for verb in ("CREATE ", "MERGE ", "DELETE ", "SET ", "REMOVE ", "DROP "):
            assert verb not in FALLBACK_SCHEMA

    def test_all_five_programs_are_buildable(self):
        from src.backend.dspy_bridge.programs import build_programs

        assert sorted(build_programs()) == [
            "cypher", "rewrite", "route", "synthesize", "verify"
        ]


# ── verifier gate in the engine ─────────────────────────────────────────────
class TestVerifyGate:
    def _engine(self, enabled):
        from src.backend.chat_engine.engine import ChatWorkflow
        from src.backend.dspy_bridge.runtime import DspyRuntime

        eng = ChatWorkflow.__new__(ChatWorkflow)
        eng.dspy = DspyRuntime()
        if enabled:
            os.environ["ASK_DSPY"] = "1"
        return eng

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("ASK_DSPY", raising=False)

    def test_confident_well_grounded_answer_skips_the_call(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        eng = self._engine(True)
        called = []
        eng.dspy.verify = lambda *a: called.append(a) or ("supported", "none")

        out = eng._verify_answer("q", "x" * 1000, "answer", 0.95, 1000)
        assert out is None
        assert called == []

    def test_thin_context_triggers_verification(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        eng = self._engine(True)
        eng.dspy.verify = lambda *a: ("supported", "none")

        eng._verify_answer("q", "tiny", "answer", 0.95, 10)
        # Returning None means "keep the draft", which is correct for a
        # supported verdict, so check the call happened via the gap path.
        eng.dspy.verify = lambda *a: ("unsupported", "nothing in context")
        out = eng._verify_answer("q", "tiny", "answer", 0.95, 10)
        assert out is not None
        assert out[1] == 0.0
        assert "nothing in context" in out[0]

    def test_disabled_dspy_never_verifies(self):
        eng = self._engine(False)
        eng.dspy.verify = lambda *a: pytest.fail("verifier called while disabled")
        assert eng._verify_answer("q", "", "answer", 0.0, 0) is None

    def test_verifier_cannot_rewrite_prose(self, monkeypatch):
        """The verifier reports; it does not generate. An answer it flags keeps
        its original text and gains a caveat."""
        monkeypatch.setenv("ASK_DSPY", "1")
        eng = self._engine(True)
        eng.dspy.verify = lambda *a: ("unsupported", "the value is not in context")

        out = eng._verify_answer("q", "c", "original answer text", 0.2, 100)
        assert out[0].startswith("original answer text")
        assert out[1] == 0.0

    def test_flag_without_a_gap_still_lowers_confidence(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        eng = self._engine(True)
        eng.dspy.verify = lambda *a: ("unsupported", "none")

        # No citations at all, so grounding is 0.0 and the verdict stands.
        answer, confidence = eng._verify_answer("q", "c", "answer", 0.2, 100)
        assert answer == "answer"
        assert confidence == 0.0

    def test_grounded_answer_overrides_a_negative_verdict(self, monkeypatch):
        """The false-accusation case, reproduced from a live run.

        A short graph-only context, a correct list, and a verifier that called
        it unsupported. Acting on that would degrade a right answer in front of
        the user, so grounding arbitrates.
        """
        monkeypatch.setenv("ASK_DSPY", "1")
        eng = self._engine(True)
        eng.dspy.verify = lambda *a: (
            "unsupported", "the claim that these files import X is unsupported"
        )

        context = (
            "Record 1:\n  path: app.py\n"
            "Record 2:\n  path: src/backend/api.py\n"
            "Record 3:\n  path: src/backend/map/mapper.py\n"
        )
        question = "Which files import repo_parser.py?"
        answer = "The files importing repo_parser.py are app.py, "
        answer += "src/backend/api.py, and src/backend/map/mapper.py."

        assert eng._verify_answer(question, context, answer, 0.4, len(context)) is None

    def test_invented_answer_still_gets_the_caveat(self, monkeypatch):
        """The override must not become a free pass for hallucination."""
        monkeypatch.setenv("ASK_DSPY", "1")
        eng = self._engine(True)
        eng.dspy.verify = lambda *a: ("unsupported", "no such file in context")

        context = "Record 1:\n  path: src/backend/services/cache.py\n"
        answer = "This is implemented in src/backend/magic/teleporter.py."

        out = eng._verify_answer("q", context, answer, 0.4, len(context))
        assert out is not None
        assert "teleporter" in out[0]
        assert out[1] == 0.0

    def test_hedged_answer_with_one_real_citation_still_flagged(self, monkeypatch):
        """50% grounding is the threshold, so this sits exactly on the boundary
        and must not be overridden -- inventing one path among several is still
        inventing."""
        monkeypatch.setenv("ASK_DSPY", "1")
        eng = self._engine(True)
        eng.dspy.verify = lambda *a: ("unsupported", "second file is not in context")

        context = "path: src/backend/services/cache.py"
        answer = "See src/backend/services/cache.py and src/backend/ghost.py."

        # Question names cache.py, so grounding is 1/2 = 50%: below the
        # override, so the invented path still gets flagged.
        out = eng._verify_answer("What does cache.py do?", context, answer, 0.4, len(context))
        assert out is not None
        assert out[1] == 0.0


# ── tier assignment ─────────────────────────────────────────────────────────
class _FakeLLM(Runnable):
    """A minimal LangChain Runnable.

    Subclasses `Runnable` rather than faking `__or__`, because the pipe
    operator type-checks its operand and rejects anything that is not
    Runnable-like. Also serves as `llm.with_structured_output(...)`, which is
    what every legacy path calls first.
    """

    def __init__(self, result=None, boom: str | None = None):
        super().__init__()
        self._result = result
        self._boom = boom

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _payload, config=None, **kwargs):
        if self._boom:
            raise AssertionError(self._boom)
        return self._result


class TestAllFiveWired:
    """All five programs must be reachable from the engine. Three of them
    (rewrite, cypher, synthesize) were defined but never called, so DSPy was
    barely integrated and the cost argument did not apply."""

    def test_rewrite_reaches_dspy(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        monkeypatch.setenv("ASK_DSPY_SHADOW", "0")
        from src.backend.chat_engine.engine import ChatWorkflow
        from src.backend.chat_engine import gates

        monkeypatch.setattr(gates, "needs_rewrite", lambda q, h: True)
        wf = ChatWorkflow.__new__(ChatWorkflow)
        wf.dspy = type("D", (), {
            "enabled": True,
            "rewrite": staticmethod(lambda q, h: "standalone version"),
        })()
        state = {"user_query": "what about it?", "user_history": [{"role": "user", "content": "x"}]}
        assert wf.query_rewriter_node(state)["rewritten_query"] == "standalone version"

    def test_rewrite_falls_back_to_langchain(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        from src.backend.chat_engine.engine import ChatWorkflow, RewrittenQuery
        from src.backend.chat_engine import gates
        from tests.test_dspy_bridge import _FakeLLM

        monkeypatch.setattr(gates, "needs_rewrite", lambda q, h: True)
        wf = ChatWorkflow.__new__(ChatWorkflow)
        wf.dspy = type("D", (), {"enabled": True, "rewrite": staticmethod(lambda q, h: None)})()
        wf.llm = _FakeLLM(RewrittenQuery(rewritten_query="legacy rewrite"))
        state = {"user_query": "what about it?", "user_history": [{"role": "user", "content": "x"}]}
        assert wf.query_rewriter_node(state)["rewritten_query"] == "legacy rewrite"

    def test_synthesize_reaches_dspy(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        from src.backend.chat_engine.engine import AnswerEngine
        from tests.test_dspy_bridge import _FakeLLM

        runtime = type("D", (), {
            "enabled": True,
            "synthesize": staticmethod(
                lambda q, c, h="": {"answer": "dspy answer", "score": 0.9, "sources": "a.py"}
            ),
        })()
        out = AnswerEngine(
            _FakeLLM(None, boom="legacy path ran while DSPy was available")
        ).generate_response("q", "ctx", "", dspy_runtime=runtime)
        assert out.answer == "dspy answer"
        assert out.score == 0.9

    def test_synthesize_falls_back_to_langchain(self, monkeypatch):
        from src.backend.chat_engine.engine import AnswerEngine, ResponseModel
        from tests.test_dspy_bridge import _FakeLLM

        runtime = type("D", (), {
            "enabled": True,
            "synthesize": staticmethod(lambda q, c, h="": None),
            "compare": staticmethod(lambda *a, **k: None),
        })()
        out = AnswerEngine(
            _FakeLLM(ResponseModel(answer="legacy answer", score=0.5, sources="b.py"))
        ).generate_response("q", "ctx", "", dspy_runtime=runtime)
        assert out.answer == "legacy answer"

    def test_cypher_reaches_dspy_and_is_sanitized(self, monkeypatch):
        """The DSPy path must go through the same sanitizer as the legacy one,
        or it becomes a write hole."""
        from src.backend.services.query_engine import QueryEngine

        eng = QueryEngine.__new__(QueryEngine)
        eng.repo_id = "r"
        eng._graph_cache = {}

        class _Runtime:
            enabled = True

            def cypher(self, query, repo_id):
                return "MATCH (f:File) DELETE f"

            def compare(self, *a, **k):
                pass

        eng.dspy_runtime = _Runtime()
        # Write clause -> sanitizer rejects -> no execution.
        assert eng._execute_generated_cypher("MATCH (f:File) DELETE f", "q") is None

    def test_cypher_falls_through_when_generated_query_fails(self, monkeypatch):
        from src.backend.services.query_engine import QueryEngine

        eng = QueryEngine.__new__(QueryEngine)
        eng.repo_id = "r"
        eng._graph_cache = {}

        class _Runtime:
            enabled = True

            def cypher(self, query, repo_id):
                return "THIS IS NOT CYPHER"

            def compare(self, *a, **k):
                pass

        eng.dspy_runtime = _Runtime()
        # Returns None so graph_search falls through to the legacy prompt
        # rather than reporting an empty answer.
        assert eng._execute_generated_cypher("THIS IS NOT CYPHER", "q") is None

    def test_execute_generated_cypher_rejects_writes(self):
        from src.backend.services.query_engine import QueryEngine

        eng = QueryEngine.__new__(QueryEngine)
        eng.repo_id = "r"
        for bad in ("MATCH (n) DELETE n", "CREATE (n:File)", "MATCH (n) SET n.x=1"):
            assert eng._execute_generated_cypher(bad, "q") is None, bad


class TestShadowMode:
    """Shadow mode is the mechanism for deciding whether to cut over, so it
    has to actually run. It previously could not: `_call` gated on `enabled`,
    which defaults false, and `shadow` was the raw env string."""

    def test_shadow_defaults_off(self, monkeypatch):
        monkeypatch.delenv("ASK_DSPY_SHADOW", raising=False)
        monkeypatch.delenv("ASK_DSPY", raising=False)
        from src.backend.dspy_bridge.runtime import DspyRuntime

        assert DspyRuntime().shadow is False

    def test_zero_string_disables_shadow(self, monkeypatch):
        """"0" is truthy in Python, so the raw string could not be turned off."""
        monkeypatch.setenv("ASK_DSPY_SHADOW", "0")
        from src.backend.dspy_bridge.runtime import DspyRuntime

        assert DspyRuntime().shadow is False

    def test_truthy_values(self, monkeypatch):
        from src.backend.dspy_bridge.runtime import _truthy

        for v in ("1", "true", "TRUE", "yes", "on"):
            assert _truthy(v) is True, v
        for v in ("0", "false", "no", "off", ""):
            assert _truthy(v) is False, v
        assert _truthy(None) is False

    def test_shadow_runs_the_program_but_returns_none(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY_SHADOW", "1")
        from src.backend.dspy_bridge.runtime import DspyRuntime

        rt = DspyRuntime()
        ran = []

        rt.program = lambda name: (lambda **kw: ran.append(kw) or type(
            "O", (), {"standalone_question": "x"}
        )())
        # DSPy is NOT serving, yet the program still runs and still returns None.
        assert rt.enabled is False
        assert rt.rewrite("q", "h") is None
        assert ran, "shadow mode did not invoke the program"
        assert rt.stats.summary()["rewrite"]["calls"] == 1

    def test_shadow_records_agreement(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY_SHADOW", "1")
        from src.backend.dspy_bridge.runtime import DspyRuntime

        rt = DspyRuntime()
        rt.compare("synthesize", "the file does X", "the file does X")
        rt.compare("synthesize", "imports parser", "quantum field theory")
        report = rt.shadow_report()["synthesize"]
        assert report["compared"] == 2
        assert report["agree_rate"] == 0.5

    def test_compare_is_inert_without_shadow(self, monkeypatch):
        monkeypatch.delenv("ASK_DSPY_SHADOW", raising=False)
        from src.backend.dspy_bridge.runtime import DspyRuntime

        rt = DspyRuntime()
        rt.compare("route", "a", "b")
        assert rt.shadow_report() == {}

    def test_similar_detects_divergence(self):
        from src.backend.dspy_bridge.runtime import _similar

        assert _similar("imports repo_parser", "imports repo_parser")
        assert not _similar("imports repo_parser", "explains the BPE tokenizer")
        assert not _similar("", "anything")
        assert not _similar("anything", "")


class TestRouterCache:
    """`_router_cache` was written on every route and read by nothing, so the
    router re-paid for an LLM call on any repeated question. Reading it turns a
    write-only cache into the saving it looks like."""

    def _workflow(self):
        from src.backend.chat_engine.engine import ChatWorkflow

        wf = ChatWorkflow.__new__(ChatWorkflow)
        wf.query_engine = type("QE", (), {"_router_cache": {}})()
        wf.dspy = type("D", (), {"enabled": False, "route": staticmethod(lambda q: None)})()
        return wf

    def _decision(self, decision, reason="because"):
        from src.backend.chat_engine.engine import RouterDecision

        return RouterDecision(decision=decision, reason=reason)

    def test_cached_route_short_circuits_the_call(self, monkeypatch):
        monkeypatch.delenv("ASK_DSPY", raising=False)
        wf = self._workflow()
        wf.query_engine._router_cache["q"] = self._decision("graph_only", "structural")

        monkeypatch.setattr(
            wf.dspy.__class__, "route", staticmethod(lambda q: pytest.fail("routed despite cache hit"))
        )
        out = wf.router_node({"user_query": "q"})
        assert out["router_decision"] == "graph"
        assert out["reason"] == "structural"

    def test_cache_miss_consults_dspy(self, monkeypatch):
        monkeypatch.setenv("ASK_DSPY", "1")
        wf = self._workflow()
        wf.dspy.route = lambda q: ("hybrid", "dspy says so")
        out = wf.router_node({"user_query": "q"})
        assert out["router_decision"] == "hybrid"

    def test_dspy_result_is_cached_under_the_shared_key(self, monkeypatch):
        """Otherwise a repeat question is free only when the legacy path
        happened to answer first."""
        monkeypatch.setenv("ASK_DSPY", "1")
        wf = self._workflow()
        wf.dspy.route = lambda q: ("graph", "structural")
        wf.router_node({"user_query": "q"})
        assert wf.query_engine._router_cache["q"].decision == "graph_only"

    def test_state_mapping_round_trips(self):
        from src.backend.chat_engine.engine import _route_to_state, _state_to_route

        for decision in ("graph_only", "hybrid", "architecture"):
            assert _state_to_route(_route_to_state(decision)) == decision

    def test_unknown_decision_falls_back_to_hybrid(self):
        """The router's own prompt says to prefer hybrid when unsure."""
        from src.backend.chat_engine.engine import _route_to_state

        assert _route_to_state("something_else") == "hybrid"

    def test_graph_node_is_reachable_from_state(self):
        from src.backend.chat_engine.engine import _route_to_state

        assert _route_to_state("graph_only") == "graph"


class TestLazyRepoFiles:
    """Chat never reads the file inventory, so opening a session must not clone
    and parse the repository to produce one."""

    def _lazy(self, calls):
        from src.backend import api

        api.get_files = lambda url: (calls.append(url), {"a.py": {}})[1]
        api.repo_files_cache.clear()
        return api._LazyRepoFiles("r", "https://example.com/r.git")

    def test_constructing_does_not_parse(self, monkeypatch):
        calls = []
        lazy = self._lazy(calls)
        assert calls == []
        assert "deferred" in repr(lazy)

    def test_first_iteration_parses_once(self, monkeypatch):
        calls = []
        lazy = self._lazy(calls)
        list(lazy)
        list(lazy)
        len(lazy)
        lazy["a.py"]
        assert calls == ["https://example.com/r.git"]

    def test_behaves_like_the_mapping_it_wraps(self, monkeypatch):
        from src.backend import api

        api.get_files = lambda url: {"a.py": {"content": "x"}, "b.py": {}}
        api.repo_files_cache.clear()
        lazy = api._LazyRepoFiles("r", "u")
        assert set(lazy) == {"a.py", "b.py"}
        assert len(lazy) == 2
        assert lazy["a.py"] == {"content": "x"}
        assert "a.py" in lazy
        assert list(lazy.items()) == [("a.py", {"content": "x"}), ("b.py", {})]

    def test_parse_result_lands_in_the_shared_cache(self, monkeypatch):
        """Indexing reads the cache directly, so a deferred parse must publish
        there or the work is repeated."""
        from src.backend import api

        api.get_files = lambda url: {"a.py": {}}
        api.repo_files_cache.clear()
        lazy = api._LazyRepoFiles("r", "u")
        list(lazy)
        assert api.repo_files_cache["r"] == {"a.py": {}}

    def test_chat_engine_construction_does_not_trigger_a_clone(self, monkeypatch):
        from src.backend import api

        clones = []
        api.get_files = lambda url: clones.append(url) or {}
        api.repo_files_cache.clear()
        api.active_engines.clear()

        class _FakeLLM:
            model_name = "fake"

            def with_structured_output(self, _):
                return self

        api.FallbackChatModel = _FakeLLM
        api.ChatWorkflow = lambda repo_id, files, llm: {"files": files}
        api._get_or_create_engine("r", "s", "https://example.com/r.git")
        assert clones == [], "engine construction cloned the repo"

        # And it is still available to whoever needs it.
        engine = api.active_engines[api._engine_key("r", "s")]
        assert list(engine["files"]) == []
        assert len(clones) == 1


class TestModelBinding:
    """A `dspy.Predict` with no `lm` of its own resolves `dspy.settings.lm` at
    call time. Setting a global default therefore does *not* implement per-step
    tiers: every program runs on whichever model is the default. These tests
    exist because that bug made the whole tier configuration decorative while
    still returning plausible answers."""

    def _bound(self, tier):

        from src.backend.dspy_bridge.config import bind_models, configure_lms
        from src.backend.dspy_bridge.programs import build_programs

        configure_lms(tier)
        programs = build_programs()
        bind_models(programs, tier)
        return programs

    def _predictor(self, program):
        """The predictor a call would actually reach.

        For a ChainOfThought program that is the inner `predict`, not the
        wrapper -- binding the wrapper would set an attribute nothing reads.
        """
        from src.backend.dspy_bridge.config import _leaf_predictors

        predictors = _leaf_predictors(program)
        assert predictors, "no reachable predictor"
        return predictors[0]

    def test_every_program_binds_a_model(self):

        programs = self._bound("mixed")
        for name in ("route", "rewrite", "cypher", "synthesize", "verify"):
            lm = getattr(self._predictor(programs[name]), "lm", None)
            assert lm is not None, f"{name} has no bound LM"

    def test_bound_model_matches_the_tier(self):

        from src.backend.dspy_bridge.config import model_for
        from src.backend.dspy_bridge.config import PROGRAMS as PROGRAMS_IN_TIER

        programs = self._bound("mixed")
        for name in PROGRAMS_IN_TIER:
            lm = self._predictor(programs[name]).lm
            assert lm.model == model_for(name, "mixed"), name

    def test_mixed_tier_actually_differs_from_a_uniform_one(self):
        """The whole point: if every program binds the same model, the tier
        changes nothing and the cost configuration is a no-op."""
        programs = self._bound("mixed")
        models = {
            n: self._predictor(programs[n]).lm.model
            for n in ("route", "cypher", "synthesize")
        }
        assert len(set(models.values())) > 1, f"tier did nothing: {models}"

    def test_rebinding_replaces_a_stale_model(self):
        """Caching the runtime must not leave the previous tier's model bound."""
        self._bound("strong")
        programs = self._bound("cheap")
        assert self._predictor(programs["synthesize"]).lm.model == "openai/gpt-4o-mini"

    def test_chain_of_thought_is_bound_too(self):
        """Route and synthesize wrap Predict in ChainOfThought, which delegates
        to an inner Predict — so binding only bare Predicts would miss them."""
        programs = self._bound("mixed")
        for name in ("route", "synthesize"):
            assert self._predictor(programs[name]).lm is not None, name


class TestModelTiers:
    def test_every_tier_assigns_every_program(self):
        """A partial mapping makes model_for fall through to a default nobody
        chose — that is how the cheap model ended up serving synthesis."""
        from src.backend.dspy_bridge.config import PROGRAMS, TIERS

        for tier, mapping in TIERS.items():
            assert set(mapping) == set(PROGRAMS), f"tier {tier} is incomplete"

    def test_mixed_tier_keeps_synthesis_strong(self):
        """The one step whose output the user reads is not the one to economise on."""
        from src.backend.dspy_bridge.config import CHEAP_MODEL, STRONG_MODEL, model_for

        assert model_for("synthesize", "mixed") == STRONG_MODEL
        assert model_for("route", "mixed") == CHEAP_MODEL

    def test_strong_tier_is_the_baseline(self):
        from src.backend.dspy_bridge.config import PROGRAMS, STRONG_MODEL, model_for

        assert all(model_for(p, "strong") == STRONG_MODEL for p in PROGRAMS)

    def test_cheap_tier_is_all_cheap(self):
        from src.backend.dspy_bridge.config import CHEAP_MODEL, PROGRAMS, model_for

        assert all(model_for(p, "cheap") == CHEAP_MODEL for p in PROGRAMS)

    def test_unknown_tier_falls_back_to_mixed_not_strong(self):
        """A typo must not silently triple the bill."""
        from src.backend.dspy_bridge.config import DEFAULT_TIER, model_for

        assert model_for("synthesize", "gpt5-turbo") == model_for(
            "synthesize", DEFAULT_TIER
        )

    def test_default_tier_is_mixed(self):
        from src.backend.dspy_bridge.config import DEFAULT_TIER

        assert DEFAULT_TIER == "mixed"

    def test_model_for_never_raises_for_an_unknown_program(self):
        from src.backend.dspy_bridge.config import model_for

        assert model_for("not_a_program")
