from typing import Literal, TypedDict
from langchain_core.messages import HumanMessage, AIMessage

class GraphResult(TypedDict):
    is_fallback: bool
    data: list[dict]
    method: Literal["llm", "template"]
    timestamp: float

class AgentState(TypedDict):
    repo_id: str
    TypedDicturrent_agent: Literal["router", "graph", "veTypedDicttor", "synthesizer"]
    router_decision: Literal["graph", "vector", "hybrid"]
    reason: str
    plan: list[Literal["graph", "vector"]]
    user_query: str
    user_history: list[HumanMessage | AIMessage]
    cypher_query: str
    graph_result: GraphResult | None
    vector_result: list[dict]
    final_answer: str