"""Routing-graph pure functions, prompt formatting, and the API contract.

The engine's graph nodes are tested through their underlying helpers rather
than by invoking LangGraph, so no LLM or database is required.
"""
import pytest

from src.backend.agent_state.state import AgentState, GraphResult
from src.backend.chat_engine.engine import AnswerEngine, format_documents
from src.backend.services.llm_fallback import _is_openai_failure


class TestFormatDocuments:
    def test_empty_inputs(self):
        assert format_documents([], [], "graph") == ""

    def test_graph_section_header(self):
        out = format_documents([{"path": "a.py"}], [], "graph")
        assert "[Graph Relationships]" in out
        assert "a.py" in out

    def test_vector_section_includes_metadata(self):
        out = format_documents([], [{
            "metadata": {
                "path": "a.py", "class_name": "Foo", "function_name": "bar",
                "line_start": 1, "line_end": 9,
            },
            "score": 0.5,
            "content": "code here",
        }], "hybrid")
        assert "[Code Chunks]" in out
        assert "a.py" in out and "Foo.bar" in out
        assert "lines 1-9" in out
        assert "0.5000" in out
        assert "code here" in out

    def test_both_sections_present(self):
        out = format_documents(
            [{"path": "a.py"}],
            [{"metadata": {"path": "b.py"}, "score": 1.0, "content": "x"}],
            "hybrid",
        )
        assert "[Graph Relationships]" in out
        assert "[Code Chunks]" in out

    def test_none_values_are_skipped(self):
        out = format_documents([{"path": "a.py", "missing": None}], [], "graph")
        assert "missing" not in out

    def test_list_values_are_joined(self):
        out = format_documents([{"tags": ["x", "y"]}], [], "graph")
        assert "x, y" in out


class TestAnswerEnginePrompts:
    def test_prompt_is_a_single_named_constant(self):
        ae = AnswerEngine.__new__(AnswerEngine)
        msgs = ae._messages("q", "ctx", "hist")
        assert msgs[0][1] is AnswerEngine.SYSTEM
        assert len(msgs) == 2

    def test_history_is_included_only_when_present(self):
        ae = AnswerEngine.__new__(AnswerEngine)
        assert "Conversation history" not in ae._messages("q", "ctx", "")[1][1]
        assert "Conversation history" in ae._messages("q", "ctx", "h")[1][1]


class TestFallbackDetection:
    @pytest.mark.parametrize("message", [
        "Error code: 429 - insufficient_quota",
        "You exceeded your current quota, please check your plan and billing details",
        "Rate limit reached for gpt-4o",
        "The server had an error while processing your request",
        "The server is overloaded or not ready yet",
        "Incorrect API key provided",
        "Your account has been deactivated",
    ])
    def test_recognises_openai_failures(self, message):
        assert _is_openai_failure(Exception(message))

    @pytest.mark.parametrize("message", [
        "token limit reached",
        "max_tokens must be an integer",
        "This token expired",
        "maximum context length is 8192 tokens",
        "Connection reset by peer",
        "Could not resolve host",
    ])
    def test_ignores_failures_a_second_provider_cannot_help_with(self, message):
        """"token" was in the keyword list and matched all of these, so
        ordinary request-shape errors silently started spending the Groq key.
        A context-window overflow is also pointless to fail over on, because
        Groq's window is smaller."""
        assert not _is_openai_failure(Exception(message))


class TestFallbackModelState:
    def test_fallback_flag_is_monotonic_per_session(self):
        """It was reset to False at the start of every invoke, so a request
        where only the router fell back reported "no fallback" because the last
        call succeeded."""
        from src.backend.services.llm_fallback import FallbackChatModel

        m = FallbackChatModel.__new__(FallbackChatModel)
        m._fallback_count = 0
        m._last_error = None
        m._groq = None

        assert m._fallback_used is False
        m._fallback_count += 1
        assert m._fallback_used is True
        m._fallback_count += 1
        assert m._fallback_used is True, "must not be reset by a later success"


class TestAgentState:
    def test_declares_the_fields_the_api_initialises(self):
        """api.py, app.py and eval.py each build an initial state dict; keep
        them honest about the TypedDict."""
        initial = {
            "repo_id": "r", "session_id": "s", "current_agent": "router",
            "router_decision": "hybrid", "reason": "", "context": "",
            "plan": [], "user_query": "q", "rewritten_query": "",
            "user_history": [], "cypher_query": "", "graph_result": None,
            "vector_result": [], "architect_subtype": "", "final_answer": "",
        }
        assert set(initial) <= set(AgentState.__annotations__)

    def test_graph_result_shape(self):
        gr: GraphResult = {
            "is_fallback": False, "data": [], "method": "template", "timestamp": 0.0,
        }
        assert set(gr) == set(GraphResult.__annotations__)


class TestApiContract:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from src.backend import api

        return TestClient(api.app, raise_server_exceptions=False)

    def test_health_never_leaks_a_traceback(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.text
        assert "Traceback" not in body
        assert ".py" not in body or "down:" in body

    def test_empty_repo_url_rejected(self, client):
        r = client.post("/api/parse", json={"repo_url": ""})
        assert r.status_code == 400

    def test_blank_session_id_rejected(self, client):
        r = client.post("/api/chat", json={
            "repo_url": "https://github.com/o/r", "query": "q", "session_id": "   ",
        })
        assert r.status_code == 400

    def test_missing_session_id_is_a_422(self, client):
        r = client.post("/api/chat", json={
            "repo_url": "https://github.com/o/r", "query": "q",
        })
        assert r.status_code == 422

    def test_unknown_job_is_404(self, client):
        assert client.get("/api/parse/status/does-not-exist").status_code == 404

    def test_cache_module_is_self_contained(self):
        """cache.py ships with the repo but nothing imports it yet: the query
        path still uses QueryEngine._graph_cache. Kept honest here rather than
        asserting an endpoint that does not exist.

        TODO: wire the shared TTL caches into the query path, or delete the
        module. Having both is the worst option.
        """
        from src.backend.services import cache

        assert cache.all_stats()

    def test_cors_does_not_allow_any_origin(self, client):
        """allow_origins=['*'] + allow_credentials=True lets any site make
        credentialed cross-origin calls to this API. With an explicit
        allow-list a foreign Origin gets no ACAO header, so the browser
        refuses the response."""
        r = client.get("/health", headers={"Origin": "https://evil.example"})
        assert r.headers.get("access-control-allow-origin") != "*"
        assert r.headers.get("access-control-allow-origin") is None

    def test_dev_origin_is_allowed(self, client):
        r = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_all_documented_routes_are_registered(self, client):
        paths = {r.path for r in client.app.routes}
        for p in [
            "/health", "/api/parse", "/api/parse/status/{job_id}", "/api/chat",
            "/api/activity", "/api/cleanup/manual/{repo_id}", "/api/graph/{repo_id}",
            "/api/tree/{repo_id}", "/api/graph_data/{repo_id}",
        ]:
            assert p in paths, p
