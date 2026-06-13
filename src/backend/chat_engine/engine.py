import time
import logging
import traceback
from typing import Literal
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, END

from src.backend.agent_state.state import AgentState, GraphResult
from src.backend.services.query_engine import QueryEngine
from src.backend.services.vector_db import VectorStore

logger = logging.getLogger("askmyrepo.engine")


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
    def __init__(self, llm):
        self.llm = llm.with_structured_output(ResponseModel)

    def generate_response(self, query: str, context: str, history_text: str = ""):
        history_block = f"\n\nConversation history:\n{history_text}" if history_text else ""
        prompt = ChatPromptTemplate.from_messages([
            ("system", """
                You are a code repository assistant.
                Answer only from the provided context.
                Use conversation history to resolve follow-up references (e.g. "it", "that function").
                When asked about initial or default values, prioritize code that
                constructs or initializes objects (e.g. GraphState(...), __init__,
                initial_state) over code that updates or transitions values.
                Sources should be file names used to answer.
                Confidence should be between 0 and 1.
            """),
            ("user", "Context:\n{context}{history_block}\n\nQuestion: {query}"),
        ])
        chain = prompt | self.llm
        return chain.invoke({
            "context": context,
            "history_block": history_block,
            "query": query,
        })


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

        self.query_engine = QueryEngine(repo_id=repo_id, db_client=None, llm=llm)
        self.vector_store = VectorStore(files=files, collection_name=f"repo_{repo_id}")
        self.query_engine.db_client = self.vector_store

        self.answer_engine = AnswerEngine(llm)
        self.app = self._build_graph()

        model_name = getattr(llm, 'model_name', str(llm.__class__.__name__))
        logger.info(f"ChatWorkflow initialized: repo={repo_id}, model={model_name}")

    def router_node(self, state: AgentState) -> dict:
        query = state["user_query"]
        logger.debug(f"[router] Routing query: \"{query[:60]}...\"")

        try:
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

            if res.decision == "architecture":
                router_decision_val = "architecture"
            elif res.decision == "graph_only":
                router_decision_val = "graph"
            else:
                router_decision_val = "hybrid"

            logger.info(f"[router] -> {router_decision_val} (reason: {res.reason[:80]})")

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

        logger.debug(f"[rewriter] Rewriting query with {len(history)} history turns")

        try:
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

                critical_files = self.query_engine.extract_critical_path_files(res, limit=4)
                if critical_files:
                    logger.debug(f"[architect] Enriching with vector data for {len(critical_files)} files")
                    vector_data = self.vector_store.vector_search(query, filenames=critical_files)
                    reranked = self.vector_store.rerank(vector_data, query, top_k=4)
                    vector_res = [
                        {"metadata": item[0], "score": float(item[1]), "content": item[2]}
                        for item in reranked
                    ]

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
            }
        except Exception as e:
            logger.error(f"[graph] Failed: {type(e).__name__}: {e}")
            for line in traceback.format_exc().splitlines():
                logger.error(f"  {line}")
            return {
                "graph_result": None,
                "current_agent": "graph",
            }

    def vector_node(self, state: AgentState) -> dict:
        query = state.get("rewritten_query") or state["user_query"]
        graph_res = state.get("graph_result")
        filenames = []

        if graph_res:
            filenames = self.query_engine._extract_filenames_safe(graph_res, query)

        logger.debug(f"[vector] Searching vectors{', filtered by ' + str(len(filenames)) + ' files' if filenames else ''}")

        try:
            if filenames:
                vector_data = self.vector_store.vector_search(query, filenames=filenames)
            else:
                vector_data = self.vector_store.search(query)

            reranked = self.vector_store.rerank(vector_data, query, top_k=5)

            vector_res = [
                {"metadata": item[0], "score": float(item[1]), "content": item[2]}
                for item in reranked
            ]
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
            response = self.answer_engine.generate_response(query, formatted_context, history_text)
            logger.info(f"[synthesizer] Answer generated ({len(response.answer)} chars, confidence={response.score:.2f})")
            return {
                "context": formatted_context,
                "final_answer": response.answer,
                "current_agent": "synthesizer",
            }
        except Exception as e:
            logger.error(f"[synthesizer] Failed: {type(e).__name__}: {e}")
            for line in traceback.format_exc().splitlines():
                logger.error(f"  {line}")
            return {
                "context": formatted_context,
                "final_answer": "I encountered an error while generating the answer. Please try rephrasing your question.",
                "current_agent": "synthesizer",
            }

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
