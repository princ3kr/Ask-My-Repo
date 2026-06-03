import time
from typing import Literal
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END

from src.backend.agent_state.state import AgentState, GraphResult
from src.backend.services.graph_db import GraphAgent
from src.backend.services.vector_db import VectorStore

class RouterDecision(BaseModel):
    decision: Literal['graph', 'vector', 'hybrid'] = Field(
        ...,
        description="graph for pure structural/dependency/topology questions. vector for specific code implementation questions. hybrid for everything else."
    )
    reason: str = Field(..., description="Explanation of why this routing path was selected")

class ResponseModel(BaseModel):
    answer: str = Field(..., description="Response to the user query based ONLY on the provided context")
    score: float = Field(..., description="Confidence score between 0 and 1")
    sources: str = Field(..., description="Comma-separated filenames/paths used for the response")

class ChatWorkflow:
    def __init__(self, repo_id: str, files: dict, llm: ChatOpenAI):
        self.repo_id = repo_id
        self.files = files
        self.llm = llm
        
        # Initialize agents/stores
        self.graph_agent = GraphAgent(llm=llm, repo_id=repo_id)
        self.vector_store = VectorStore(files=files, collection_name=f"repo_{repo_id}")
        
        self.app = self._build_graph()

    def router_node(self, state: AgentState) -> dict:
        router_llm = self.llm.with_structured_output(RouterDecision)
        router_prompt = ChatPromptTemplate.from_messages([
            ('system', """You are a query router for a code repository Q&A system.
                Determine the query path:
                - graph: answering structural, dependency, import, class hierarchy, or call graph questions.
                - vector: answering how a specific function/method is implemented, variable usage, or details in a single file.
                - hybrid: queries that involve both codebase architecture and specific implementation/logic details.
                When in doubt, choose hybrid."""),
            ('user', "Query: {query}")
        ])
        
        decision_chain = router_prompt | router_llm
        res: RouterDecision = decision_chain.invoke({"query": state["user_query"]})
        
        plan = []
        if res.decision == "graph":
            plan = ["graph"]
        elif res.decision == "vector":
            plan = ["vector"]
        else:
            plan = ["graph", "vector"]
            
        return {
            "router_decision": res.decision,
            "reason": res.reason,
            "plan": plan,
            "current_agent": "router"
        }

    def graph_node(self, state: AgentState) -> dict:
        res = self.graph_agent.graph_search(state["user_query"])
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
        filenames = []
        graph_res = state.get("graph_result")
        
        # Determine if we can restrict search scope using files from Neo4j
        if graph_res and self.graph_agent._is_meaningful(graph_res):
            filenames = self.graph_agent._extract_filenames_safe(graph_res, state["user_query"])
            
        if filenames:
            vector_data = self.vector_store.vector_search(state["user_query"], filenames=filenames)
        else:
            vector_data = self.vector_store.search(state["user_query"])
            
        reranked = self.vector_store.rerank(vector_data, state["user_query"], top_k=5)
        
        # Serialize list of tuples (metadata, score, document) to dicts for safety
        vector_res = [
            {"metadata": item[0], "score": float(item[1]), "content": item[2]}
            for item in reranked
        ]
        return {
            "vector_result": vector_res,
            "current_agent": "vector"
        }

    def synthesizer_node(self, state: AgentState) -> dict:
        # Format the combined context
        sections = []
        
        graph_res = state.get("graph_result")
        if graph_res and graph_res["data"]:
            graph_text = ["[Graph Relationships]"]
            for i, r in enumerate(graph_res["data"], 1):
                graph_text.append(f"\nRecord {i}:")
                for k, v in r.items():
                    if v is None: continue
                    if isinstance(v, list):
                        values = [str(x) for x in v if x]
                        if values: graph_text.append(f"  {k}: {', '.join(values)}")
                    else:
                        graph_text.append(f"  {k}: {v}")
            sections.append("\n".join(graph_text))
            
        vector_res = state.get("vector_result", [])
        if vector_res:
            chunk_text = ["[Code Chunks]"]
            for item in vector_res:
                meta = item["metadata"]
                chunk_text.append(
                    f"\n[{meta.get('path', 'unknown')} | "
                    f"{meta.get('class_name', 'module_level')}.{meta.get('function_name', 'module_segment')} | "
                    f"lines {meta.get('line_start', '?')}-{meta.get('line_end', '?')} | "
                    f"score {item['score']:.4f}]\n{item['content']}"
                )
            sections.append("\n".join(chunk_text))
            
        context_str = "\n\n".join(sections).strip()
        if len(context_str) > 15000:
            context_str = context_str[:15000]
            context_str = "[TRUNCATED CONTEXT]\n" + context_str
        
        # Call LLM with ResponseModel output structure
        synthesizer_llm = self.llm.with_structured_output(ResponseModel)
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a code repository assistant.
                Answer only from the provided context. Do not invent facts.
                Sources should be file names used to answer.
                Confidence score should be between 0.0 and 1.0.
                Keep your response concise and avoid overly long explanations.
                If the context cannot answer the question, say you do not know."""),
            ("user", "Context:\n{context}\n\nQuestion: {query}")
        ])
        
        chain = prompt | synthesizer_llm
        output: ResponseModel = chain.invoke({"context": context_str, "query": state["user_query"]})
        
        return {
            "context": context_str,
            "final_answer": output.answer,
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
        
        # Define conditional routing edges
        def route_after_router(state: AgentState):
            plan = state.get("plan", [])
            if "graph" in plan: return "graph"
            if "vector" in plan: return "vector"
            return "synthesizer"
            
        def route_after_graph(state: AgentState):
            plan = state.get("plan", [])
            if "vector" in plan: return "vector"
            return "synthesizer"
            
        workflow.add_conditional_edges(
            "router",
            route_after_router,
            {"graph": "graph", "vector": "vector", "synthesizer": "synthesizer"}
        )
        
        workflow.add_conditional_edges(
            "graph",
            route_after_graph,
            {"vector": "vector", "synthesizer": "synthesizer"}
        )
        
        workflow.add_edge("vector", "synthesizer")
        workflow.add_edge("synthesizer", END)
        
        return workflow.compile()