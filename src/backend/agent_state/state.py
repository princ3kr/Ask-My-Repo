from typing import Literal, TypedDict

from langchain_core.messages import AIMessage, HumanMessage


class GraphResult(TypedDict):
    is_fallback: bool
    data: list[dict]
    method: Literal["llm", "template", "architect"]
    timestamp: float

class AgentState(TypedDict, total=False):
    repo_id: str
    session_id: str
    current_agent: Literal["router", "query_rewriter", "graph", "vector", "synthesizer", "architect"]
    router_decision: Literal["graph", "vector", "hybrid", "architecture"]
    reason: str
    context: str
    plan: list[str]
    user_query: str
    rewritten_query: str
    user_history: list[HumanMessage | AIMessage]
    cypher_query: str
    graph_result: GraphResult | None
    vector_result: list[dict]
    architect_subtype: str
    final_answer: str

    # Set by graph_node when it starts an unfiltered vector search ahead of
    # time; collected by vector_node. Untyped because it is a
    # concurrent.futures.Future, which LangGraph's reducer would otherwise try
    # to merge. Never read outside the graph run that created it.
    _vector_future: object
