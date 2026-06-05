import time
from typing import Literal
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END

from src.backend.agent_state.state import AgentState, GraphResult
from src.backend.services.query_engine import QueryEngine
from src.backend.services.vector_db import VectorStore

class RouterDecision(BaseModel):
    decision: Literal['graph_only', 'hybrid'] = Field(
        ...,
        description="graph_only ONLY for pure structural/dependency/topology questions. hybrid for everything else."
    )
    reason: str = Field(..., description="Explanation of why this routing path was selected")

class ResponseModel(BaseModel):
    answer: str = Field(..., description="Response of the query from the llm model")
    score: float = Field(..., description="Confidence score of the response")
    sources: str = Field(..., description="File name used for response")

class AnswerEngine:
    def __init__(self, llm):
        self.llm = llm.with_structured_output(ResponseModel)

    def generate_response(self, query: str, context: str):
        prompt = ChatPromptTemplate.from_messages([
            ("system", """
                You are a code repository assistant.
                Answer only from the provided context.
                When asked about initial or default values, prioritize code that
                constructs or initializes objects (e.g. GraphState(...), __init__,
                initial_state) over code that updates or transitions values.
                Sources should be file names used to answer.
                Confidence should be between 0 and 1.
            """),
            ("user", "Context:\n{context}\n\nQuestion: {query}")
        ])
        chain = prompt | self.llm
        return chain.invoke({"context": context, "query": query})

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
        
        # Initialize services
        self.query_engine = QueryEngine(repo_id=repo_id, db_client=None, llm=llm)
        self.vector_store = VectorStore(files=files, collection_name=f"repo_{repo_id}")
        self.query_engine.db_client = self.vector_store
        
        self.answer_engine = AnswerEngine(llm)
        self.app = self._build_graph()

    def router_node(self, state: AgentState) -> dict:
        router_llm = self.llm.with_structured_output(RouterDecision)
        router_prompt = ChatPromptTemplate.from_messages([
            ('system', """You are a query router for code repository Q&A.
                The knowledge graph stores: files, imports, classes, functions, methods, inheritance, call edges.
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

                hybrid — everything else:
                - what a function/method does internally or returns
                - what happens when a condition is met
                - initial / default value of anything
                - how a feature is implemented
                - what database / framework / library is used
                - any question about runtime behavior, state, or logic

                RULE: If mentioning specific variables/fields or asking about behavior → hybrid.
                When in doubt, choose hybrid."""),
            ('user', "query: {query}")
        ])
        
        decision_chain = router_prompt | router_llm
        res: RouterDecision = decision_chain.invoke({"query": state["user_query"]})
        
        # Save decision metadata in QueryEngine router cache to match notebook structure
        self.query_engine._router_cache[state["user_query"]] = res
        
        # Map graph_only to graph for state compatibility
        router_decision_val = "graph" if res.decision == "graph_only" else "hybrid"
        
        return {
            "router_decision": router_decision_val,
            "reason": res.reason,
            "current_agent": "router"
        }

    def graph_node(self, state: AgentState) -> dict:
        res = self.query_engine.graph_search(state["user_query"])
        graph_res = None
        if res:
            graph_res = GraphResult(
                is_fallback=res.get("is_fallback", False),
                data=res.get("data", []),
                method=res.get("method", "llm"),
                timestamp=res.get("timestamp", time.time())
            )
        return {
            "graph_result": graph_res,
            "current_agent": "graph"
        }

    def vector_node(self, state: AgentState) -> dict:
        graph_res = state.get("graph_result")
        filenames = []
        
        if graph_res:
            filenames = self.query_engine._extract_filenames_safe(graph_res, state["user_query"])
            
        if filenames:
            vector_data = self.vector_store.vector_search(state["user_query"], filenames=filenames)
        else:
            vector_data = self.vector_store.search(state["user_query"])
            
        reranked = self.vector_store.rerank(vector_data, state["user_query"], top_k=5)
        
        vector_res = [
            {"metadata": item[0], "score": float(item[1]), "content": item[2]}
            for item in reranked
        ]
        return {
            "vector_result": vector_res,
            "current_agent": "vector"
        }

    def synthesizer_node(self, state: AgentState) -> dict:
        graph_res = state.get("graph_result")
        graph_data = graph_res["data"] if graph_res else []
        vector_res = state.get("vector_result", [])
        
        formatted_context = format_documents(graph_data, vector_res, state["router_decision"])
        
        response = self.answer_engine.generate_response(state["user_query"], formatted_context)
        
        return {
            "context": formatted_context,
            "final_answer": response.answer,
            "current_agent": "synthesizer"
        }

    def _build_graph(self):
        workflow = StateGraph(AgentState)
        
        # Add Nodes
        workflow.add_node("router", self.router_node)
        workflow.add_node("graph", self.graph_node)
        workflow.add_node("vector", self.vector_node)
        workflow.add_node("synthesizer", self.synthesizer_node)
        
        # Set Entry Point
        workflow.set_entry_point("router")
        
        # Conditional routing edge to check for meaningful graph results
        def route_after_graph(state: AgentState):
            if state["router_decision"] == "graph":
                graph_res = state.get("graph_result")
                if graph_res and self.query_engine._is_meaningful(graph_res):
                    return "synthesizer"
            return "vector"
            
        workflow.add_edge("router", "graph")
        workflow.add_conditional_edges(
            "graph",
            route_after_graph,
            {"vector": "vector", "synthesizer": "synthesizer"}
        )
        workflow.add_edge("vector", "synthesizer")
        workflow.add_edge("synthesizer", END)
        
        return workflow.compile()