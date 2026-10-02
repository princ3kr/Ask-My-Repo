import logging
import time
import traceback
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from src.backend.agent_state.state import AgentState, GraphResult
from src.backend.chat_engine import gates, speculate
from src.backend.dspy_bridge.runtime import DspyRuntime
from src.backend.services.query_engine import QueryEngine
from src.backend.services.vector_db import VectorStore

logger = logging.getLogger("askmyrepo.engine")

# How well an answer's citations must be grounded for a negative verification
# verdict to be discarded as a false accusation.
#
# Strict, and strict on purpose. Grounding is a *precision* measure over cited
# paths, so 0.5 means half the paths the answer names do not exist in the
# retrieved context -- which is inventing, regardless of what the verifier
# thinks. An answer that cites several real files and one invented one is
# exactly the case this gate exists to catch, so a partial score must not
# suppress the warning.
_VERIFY_GROUNDING_OVERRIDE = 0.8


class RouterDecision(BaseModel):
    decision: Literal['graph_only', 'hybrid', 'architecture'] = Field(
        ...,
        description=(
            "graph_only for pure structural questions; "
            "architecture for system-wide flow/overview/trace questions; "
            "hybrid for everything else."
        ),
    )
    reason: str = Field(..., description="Explanation of why this routing path was selected")


def _shadow_compare(dspy_runtime, program: str, legacy, shadowed, reference=None):
    """Record a shadow comparison, tolerating a runtime that lacks it.

    Shadow bookkeeping is instrumentation: it must never be the reason a
    question goes unanswered, so a runtime without the method (or a failure
    inside it) is logged and dropped.
    """
    compare = getattr(dspy_runtime, "compare", None)
    if compare is None:
        return
    try:
        compare(program, legacy, shadowed, reference)
    except Exception as e:
        logger.debug(f"[shadow] compare({program}) failed: {type(e).__name__}: {e}")


def _route_to_state(decision: str) -> str:
    """Map a router decision onto the value AgentState carries.

    The model's label set and the graph's node names differ: `graph_only`
    routes to the `graph` node. Both the cached and the fresh path go through
    here, so the two cannot disagree.
    """
    return {
        "architecture": "architecture",
        "graph_only": "graph",
        "graph": "graph",
        "hybrid": "hybrid",
    }.get(decision, "hybrid")


def _state_to_route(decision: str) -> str:
    """Inverse of _route_to_state, for writing a cached RouterDecision."""
    return {"graph": "graph_only"}.get(decision, decision)


class RewrittenQuery(BaseModel):
    rewritten_query: str = Field(
        ...,
        description="Self-contained query that resolves pronouns/references using conversation history",
    )


class ResponseModel(BaseModel):
    answer: str = Field(..., description="Response of the query from the llm model")
    score: float = Field(..., description="Confidence score of the response")
    sources: str = Field(..., description="File name used for response")


def _format_history(history: list) -> str:
    if not history:
        return ""
    lines = []
    for msg in history[-8:]:
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            content = msg.get("content", "")
        elif isinstance(msg, HumanMessage):
            role, content = "user", msg.content
        elif isinstance(msg, AIMessage):
            role, content = "assistant", msg.content
        else:
            continue
        lines.append(f"{role.capitalize()}: {content}")
    return "\n".join(lines)


class AnswerEngine:
    # Kept as one named constant rather than an inline literal inside
    # generate_response, so any second call path (e.g. streaming) reuses the
    # same instructions instead of drifting into a second, subtly different
    # prompt.
    SYSTEM = """
        You are a code repository assistant.
        Answer only from the provided context.
        Use conversation history to resolve follow-up references (e.g. "it", "that function").
        When asked about initial or default values, prioritize code that
        constructs or initializes objects (e.g. GraphState(...), __init__,
        initial_state) over code that updates or transitions values.
        Name the source files you used inline, like `path/to/file.py`.
        Report a confidence score between 0 and 1.
    """

    def __init__(self, llm):
        self.llm = llm.with_structured_output(ResponseModel)

    def _messages(self, query: str, context: str, history_text: str = ""):
        history_block = f"\n\nConversation history:\n{history_text}" if history_text else ""
        return [
            ("system", self.SYSTEM),
            ("user", "Context:\n{context}" + history_block + "\n\nQuestion: {query}"),
        ]

    def generate_response(self, query: str, context: str, history_text: str = "",
                          dspy_runtime=None):
        """Answer from context.

        DSPy is tried first when enabled. It returns None on any failure, so
        the prompt below stays the fallback rather than becoming dead code --
        which matters because it is also the shadow-mode comparison target.
        """
        if dspy_runtime is not None:
            dspy_answer = dspy_runtime.synthesize(query, context, history_text)
            if dspy_answer:
                return ResponseModel(
                    answer=dspy_answer["answer"],
                    score=dspy_answer["score"],
                    sources=dspy_answer["sources"],
                )

        history_block = f"\n\nConversation history:\n{history_text}" if history_text else ""
        chain = ChatPromptTemplate.from_messages(
            self._messages(query, context, history_text)
        ) | self.llm
        result = chain.invoke({
            "context": context,
            "history_block": history_block,
            "query": query,
        })
        if dspy_runtime is not None:
            _shadow_compare(dspy_runtime, "synthesize", result.answer, query, context)
        return result


def format_documents(graph_data: list[dict], vector_data: list[dict], decision: str) -> str:
    sections = []

    if graph_data:
        graph_text = ["[Graph Relationships]"]
        for i, r in enumerate(graph_data, 1):
            graph_text.append(f"\nRecord {i}:")
            for k, v in r.items():
                if v is None:
                    continue
                if isinstance(v, list):
                    values = [str(x) for x in v if x]
                    if values:
                        graph_text.append(f"  {k}: {', '.join(values)}")
                else:
                    graph_text.append(f"  {k}: {v}")
        sections.append("\n".join(graph_text))

    if vector_data:
        chunk_text = ["[Code Chunks]"]
        for item in vector_data:
            meta = item["metadata"]
            score = item["score"]
            doc = item["content"]
            chunk_text.append(
                f"\n[{meta.get('path', 'unknown')} | "
                f"{meta.get('class_name', 'module_level')}.{meta.get('function_name', 'module_level_segment')} | "
                f"lines {meta.get('line_start', '?')}-{meta.get('line_end', '?')} | "
                f"score {score:.4f}]\n{doc}"
            )
        sections.append("\n".join(chunk_text))

    return "\n\n".join(sections).strip() if sections else ""


class ChatWorkflow:
    def __init__(self, repo_id: str, files: dict, llm):
        self.repo_id = repo_id
        self.files = files
        self.llm = llm

        self.dspy = DspyRuntime.get()
        self.query_engine = QueryEngine(repo_id=repo_id, db_client=None, llm=llm,
                                        dspy_runtime=self.dspy)
        self.vector_store = VectorStore(files=files, collection_name=f"repo_{repo_id}")
        self.query_engine.db_client = self.vector_store

        self.answer_engine = AnswerEngine(llm)
        self.app = self._build_graph()

        model_name = getattr(llm, 'model_name', str(llm.__class__.__name__))
        logger.info(f"ChatWorkflow initialized: repo={repo_id}, model={model_name}")

    def router_node(self, state: AgentState) -> dict:
        query = state["user_query"]
        logger.debug(f"[router] Routing query: \"{query[:60]}...\"")

        # The same query text routes the same way, and the cache below was
        # already being written to by the non-DSPy path. Reading it turns a
        # write-only cache into the saving it was presumably meant to be.
        cached = self.query_engine._router_cache.get(query)
        if cached is not None:
            logger.debug("[router] Cache hit — skipping the call")
            return {
                "router_decision": _route_to_state(cached.decision),
                "reason": cached.reason,
                "current_agent": "router",
            }

        try:
            dspy_result = self.dspy.route(query)
            if dspy_result is not None:
                router_decision_val, reason = dspy_result
                logger.info(
                    f"[router] dspy -> {router_decision_val} (reason: {reason[:80]})"
                )
                # Cache under the shared key so a repeat question is free
                # whichever backend answered it the first time.
                self.query_engine._router_cache[query] = RouterDecision(
                    decision=_state_to_route(router_decision_val), reason=reason
                )
                return {
                    "router_decision": router_decision_val,
                    "reason": reason,
                    "current_agent": "router",
                }

            router_llm = self.llm.with_structured_output(RouterDecision)
            router_prompt = ChatPromptTemplate.from_messages([
                ('system', """You are a query router for code repository Q&A.
                    The knowledge graph stores: files, imports, classes, functions, methods, inheritance, call edges, entry points.
                    It has NO knowledge of variable values, runtime state, or full code behavior.

                    Classify into:

                    graph_only — answerable purely from code structure:
                    - which files import X
                    - what does <file> depend on
                    - where is a class/function/method defined
                    - which methods belong to a class
                    - which functions/methods call or instantiate another symbol
                    - which files have no imports
                    - which file has the most dependencies
                    - transitive dependencies of <file>
                    - what files depend on <file> (reverse lookup)

                    architecture — system-wide structural questions:
                    - how does a request flow through the system
                    - what is the end-to-end architecture
                    - trace the call chain from API to DB
                    - give me a system overview
                    - how are components connected
                    - what are the main entry points and how do they connect

                    hybrid — everything else:
                    - what a function/method does internally or returns
                    - what happens when a condition is met
                    - initial / default value of anything
                    - how a feature is implemented
                    - what database / framework / library is used
                    - any question about runtime behavior, state, or logic

                    RULE: Architecture questions ask about flows, overviews, or multi-hop system structure.
                    RULE: If mentioning specific variables/fields or asking about behavior → hybrid.
                    When in doubt, choose hybrid."""),
                ('user', "query: {query}"),
            ])

            decision_chain = router_prompt | router_llm
            res: RouterDecision = decision_chain.invoke({"query": query})

            self.query_engine._router_cache[query] = res

            router_decision_val = _route_to_state(res.decision)
            logger.info(f"[router] -> {router_decision_val} (reason: {res.reason[:80]})")
            _shadow_compare(self.dspy, "route", router_decision_val, "graph_only")

            return {
                "router_decision": router_decision_val,
                "reason": res.reason,
                "current_agent": "router",
            }
        except Exception as e:
            logger.error(f"[router] Failed: {type(e).__name__}: {e}")
            for line in traceback.format_exc().splitlines():
                logger.error(f"  {line}")
            return {
                "router_decision": "hybrid",
                "reason": f"Router fallback: {e}",
                "current_agent": "router",
            }

    def query_rewriter_node(self, state: AgentState) -> dict:
        history = state.get("user_history") or []
        if not history:
            logger.debug("[rewriter] No history — skipping rewrite")
            return {
                "rewritten_query": state["user_query"],
                "current_agent": "query_rewriter",
            }

        # A question with no back-reference, or one that names its target, is
        # already self-contained. Rewriting it would spend a model call to
        # produce the input unchanged.
        if not gates.needs_rewrite(state["user_query"], history):
            logger.debug("[rewriter] Gate: question is self-contained — skipping call")
            return {
                "rewritten_query": state["user_query"],
                "current_agent": "query_rewriter",
            }

        logger.debug(f"[rewriter] Rewriting query with {len(history)} history turns")

        history_text = _format_history(history)
        try:
            dspy_result = self.dspy.rewrite(state["user_query"], history_text)
            if dspy_result:
                logger.debug(
                    f"[rewriter] dspy -> \"{dspy_result[:60]}...\""
                )
                return {
                    "rewritten_query": dspy_result,
                    "current_agent": "query_rewriter",
                }

            rewriter_llm = self.llm.with_structured_output(RewrittenQuery)
            history_text = _format_history(history)
            prompt = ChatPromptTemplate.from_messages([
                ("system", """Rewrite the user's latest question to be fully self-contained.
                    Resolve pronouns and references (it, that, they, the function, etc.) using conversation history.
                    Keep the same intent. If already self-contained, return it unchanged."""),
                ("user", "History:\n{history}\n\nLatest question: {query}"),
            ])
            chain = prompt | rewriter_llm
            result: RewrittenQuery = chain.invoke({
                "history": history_text,
                "query": state["user_query"],
            })
            _shadow_compare(self.dspy, "rewrite", result.rewritten_query, state["user_query"])
            logger.debug(f"[rewriter] \"{state['user_query'][:50]}...\" -> \"{result.rewritten_query[:60]}...\"")
            return {
                "rewritten_query": result.rewritten_query,
                "current_agent": "query_rewriter",
            }
        except Exception as e:
            logger.error(f"[rewriter] Failed: {type(e).__name__}: {e}")
            for line in traceback.format_exc().splitlines():
                logger.error(f"  {line}")
            return {
                "rewritten_query": state["user_query"],
                "current_agent": "query_rewriter",
            }

    def architect_node(self, state: AgentState) -> dict:
        query = state.get("rewritten_query") or state["user_query"]
        logger.info(f"[architect] Running architecture search for: \"{query[:60]}...\"")

        try:
            res = self.query_engine.architect_search(query)

            graph_res = None
            vector_res = []

            if res:
                graph_res = GraphResult(
                    is_fallback=False,
                    data=res.get("data", []),
                    method="architect",
                    timestamp=res.get("timestamp", time.time()),
                )

                # Vector enrichment is a separate try. Sharing the block with
                # the graph search meant one vector failure discarded graph
                # records that had already been fetched successfully.
                critical_files = self.query_engine.extract_critical_path_files(res, limit=4)
                if critical_files:
                    logger.debug(
                        f"[architect] Enriching with vector data for {len(critical_files)} files"
                    )
                    try:
                        vector_data = self.vector_store.vector_search(
                            query, filenames=critical_files
                        )
                        vector_res = self._rerank_vectors(vector_data, query, top_k=4)
                    except Exception as ve:
                        logger.error(
                            "[architect] vector enrichment failed "
                            f"({type(ve).__name__}: {ve}); keeping graph result"
                        )

            logger.info(f"[architect] -> {len(graph_res['data']) if graph_res else 0} graph records, {len(vector_res)} vectors")
            return {
                "graph_result": graph_res,
                "vector_result": vector_res,
                "architect_subtype": res.get("subtype", "") if res else "",
                "current_agent": "architect",
            }
        except Exception as e:
            logger.error(f"[architect] Failed: {type(e).__name__}: {e}")
            for line in traceback.format_exc().splitlines():
                logger.error(f"  {line}")
            return {
                "graph_result": None,
                "vector_result": [],
                "architect_subtype": "",
                "current_agent": "architect",
            }

    def graph_node(self, state: AgentState) -> dict:
        query = state.get("rewritten_query") or state["user_query"]
        logger.debug(f"[graph] Searching Neo4j for: \"{query[:60]}...\"")

        # Cypher generation is the slow part of this node, and an unfiltered vector
        # search does not need the graph result. On the hybrid path vector
        # retrieval always runs, so start it now and overlap the two.
        #
        # Only hybrid speculates. On the graph path, a meaningful result goes
        # straight to the synthesizer and the speculative search would be
        # started, paid for and thrown away.
        pending = {}
        if state.get("router_decision") == "hybrid":
            pending["_vector_future"] = speculate.speculate(
                lambda: self.vector_store.search(query)
            )

        try:
            res = self.query_engine.graph_search(query)
            graph_res = None
            if res:
                graph_res = GraphResult(
                    is_fallback=res.get("is_fallback", False),
                    data=res.get("data", []),
                    method=res.get("method", "llm"),
                    timestamp=res.get("timestamp", time.time()),
                )

            data_count = len(graph_res['data']) if graph_res else 0
            logger.debug(f"[graph] -> {data_count} records (method: {res.get('method', '?') if res else 'none'})")
            return {
                "graph_result": graph_res,
                "current_agent": "graph",
                **pending,
            }
        except Exception as e:
            logger.error(f"[graph] Failed: {type(e).__name__}: {e}")
            for line in traceback.format_exc().splitlines():
                logger.error(f"  {line}")
            # Drain the speculative future so a later vector_node does not wait
            # on a search whose result is now pointless.
            self._discard_future(pending.get("_vector_future"))
            return {
                "graph_result": None,
                "current_agent": "graph",
            }

    def _discard_future(self, future) -> None:
        """Cancel speculative work that nothing will consume."""
        if future is None:
            return
        try:
            future.cancel()
        except Exception:
            pass

    def _rerank_vectors(self, vector_data: list, query: str, top_k: int = 5) -> list:
        """Rerank raw hits into the shape the state and formatter expect."""
        reranked = self.vector_store.rerank(vector_data, query, top_k=top_k)
        return [
            {"metadata": item[0], "score": float(item[1]), "content": item[2]}
            for item in reranked
        ]

    def vector_node(self, state: AgentState) -> dict:
        """Retrieve and rerank code chunks.

        The unfiltered search does not depend on the graph result, so when the
        graph search is also running it is started speculatively and joined here.
        `state["_vector_future"]` is set by graph_node; without it this behaves
        exactly as it did before, running inline.
        """
        query = state.get("rewritten_query") or state["user_query"]
        graph_res = state.get("graph_result")
        filenames = []

        if graph_res:
            filenames = self.query_engine._extract_filenames_safe(graph_res, query)

        logger.debug(f"[vector] Searching vectors{', filtered by ' + str(len(filenames)) + ' files' if filenames else ''}")

        try:
            # A filtered search supersedes the speculative one, so only wait on
            # it when there is nothing to gain from waiting.
            if filenames:
                vector_data = self.vector_store.vector_search(query, filenames=filenames)
            else:
                vector_data = speculate.resolve(
                    state.get("_vector_future"),
                    lambda: self.vector_store.search(query),
                )

            vector_res = self._rerank_vectors(vector_data, query, top_k=5)
            logger.debug(f"[vector] -> {len(vector_res)} chunks after rerank")
            return {
                "vector_result": vector_res,
                "current_agent": "vector",
            }
        except Exception as e:
            logger.error(f"[vector] Failed: {type(e).__name__}: {e}")
            for line in traceback.format_exc().splitlines():
                logger.error(f"  {line}")
            return {
                "vector_result": [],
                "current_agent": "vector",
            }

    def synthesizer_node(self, state: AgentState) -> dict:
        graph_res = state.get("graph_result")
        graph_data = graph_res["data"] if graph_res else []
        vector_res = state.get("vector_result", [])

        formatted_context = format_documents(graph_data, vector_res, state["router_decision"])
        history_text = _format_history(state.get("user_history") or [])
        query = state.get("rewritten_query") or state["user_query"]

        context_len = len(formatted_context)
        logger.info(f"[synthesizer] Generating answer from {context_len} chars of context")

        try:
            response = self.answer_engine.generate_response(
                query, formatted_context, history_text, dspy_runtime=self.dspy
            )
            answer, confidence = response.answer, response.score
        except Exception as e:
            logger.error(f"[synthesizer] Failed: {type(e).__name__}: {e}")
            for line in traceback.format_exc().splitlines():
                logger.error(f"  {line}")
            return {
                "context": formatted_context,
                "final_answer": "I encountered an error while generating the answer. Please try rephrasing your question.",
                "current_agent": "synthesizer",
            }

        # Verification is a second model call, so it runs only where it can
        # change the outcome: a low self-reported confidence, or a thin context.
        # Verifying every confident answer would double cost to re-confirm
        # answers that were already fine.
        checked = self._verify_answer(query, formatted_context, answer, confidence, context_len)
        if checked:
            answer, confidence = checked

        logger.info(
            f"[synthesizer] Answer generated ({len(answer)} chars, confidence={confidence:.2f})"
        )
        return {
            "context": formatted_context,
            "final_answer": answer,
            "current_agent": "synthesizer",
        }

    def _verify_answer(
        self, query: str, context: str, answer: str, confidence: float, context_len: int
    ) -> tuple[str, float] | None:
        """Return a corrected (answer, confidence), or None to keep the draft.

        The verifier never rewrites prose. It reports whether the answer is
        supported, and a negative verdict appends the gap and zeroes the
        confidence.

        But the verifier is a cheap model, and it misjudges: on a correct
        answer over a short graph-only context it reported "the claim that the
        listed files import X is unsupported" — for a list that was exactly
        right. A false accusation is worse than silence, because it degrades a
        correct answer in front of the user.

        So a negative verdict is only acted on when an *objective* check agrees.
        Citation grounding measures how many of the answer's file paths actually
        appear in the retrieved context; that requires no model and cannot be
        talked into agreeing. A verdict contradicted by grounding is discarded
        and logged, not surfaced.
        """
        if not self.dspy.enabled:
            return None
        if confidence >= 0.7 and context_len >= 800:
            return None

        verdict = self.dspy.verify(query, context, answer)
        if verdict is None:
            return None
        label, gap = verdict
        if label == "supported":
            logger.info("[verify] draft supported — keeping it")
            return None

        from src.backend.dspy_bridge.metrics import citation_precision

        # The question counts as grounded context: an answer that names the file
        # the question was about is not inventing it, even when the retrieved
        # records never mention that path. Without this, every well-formed
        # answer to "which files import X" looks partly fabricated.
        grounding = citation_precision(answer, f"{context}\n{query}")
        if grounding >= _VERIFY_GROUNDING_OVERRIDE:
            logger.warning(
                "[verify] %s, but %.0f%% of cited paths are in the context — "
                "discarding the verdict",
                label, grounding * 100,
            )
            return None

        if not gap or gap.lower() == "none":
            logger.info("[verify] flagged %s with no specific gap", label)
            return answer, 0.0

        logger.warning(f"[verify] {label} (grounding {grounding:.0%}): {gap[:120]}")
        disclaimer = (
            f"\n\n_Note: part of this could not be confirmed from the retrieved "
            f"context — {gap}._"
        )
        return f"{answer}{disclaimer}", 0.0

    def _build_graph(self):
        workflow = StateGraph(AgentState)

        workflow.add_node("router", self.router_node)
        workflow.add_node("query_rewriter", self.query_rewriter_node)
        workflow.add_node("architect", self.architect_node)
        workflow.add_node("graph", self.graph_node)
        workflow.add_node("vector", self.vector_node)
        workflow.add_node("synthesizer", self.synthesizer_node)

        workflow.set_entry_point("router")

        def route_after_router(state: AgentState):
            decision = state["router_decision"]
            if decision == "architecture":
                return "architect"
            if decision == "hybrid":
                return "query_rewriter"
            return "graph"

        workflow.add_conditional_edges(
            "router",
            route_after_router,
            {
                "architect": "architect",
                "query_rewriter": "query_rewriter",
                "graph": "graph",
            },
        )

        workflow.add_edge("query_rewriter", "graph")
        workflow.add_edge("architect", "synthesizer")

        def route_after_graph(state: AgentState):
            if state["router_decision"] == "graph":
                graph_res = state.get("graph_result")
                if graph_res and self.query_engine._is_meaningful(graph_res):
                    return "synthesizer"
            return "vector"

        workflow.add_conditional_edges(
            "graph",
            route_after_graph,
            {"vector": "vector", "synthesizer": "synthesizer"},
        )
        workflow.add_edge("vector", "synthesizer")
        workflow.add_edge("synthesizer", END)

        return workflow.compile()
