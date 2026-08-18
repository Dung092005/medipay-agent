from langgraph.graph import END, StateGraph

from src.agents.nodes.graphrag_nodes import (
    assemble_context_node,
    extract_entities_node,
    generate_node,
    guardrail_node,
    intake_node,
    retrieve_vectors_node,
)
from src.agents.state import AgentState


def should_continue(state: AgentState) -> str:
    return END if state.get("error") else "extract_entities"


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("intake", intake_node)
    graph.add_node("extract_entities", extract_entities_node)
    graph.add_node("retrieve_vectors", retrieve_vectors_node)
    graph.add_node("assemble_context", assemble_context_node)
    graph.add_node("generate", generate_node)
    graph.add_node("guardrail", guardrail_node)

    graph.set_entry_point("intake")
    graph.add_conditional_edges("intake", should_continue)
    graph.add_edge("extract_entities", "retrieve_vectors")
    graph.add_edge("retrieve_vectors", "assemble_context")
    graph.add_edge("assemble_context", "generate")
    graph.add_edge("generate", "guardrail")
    graph.add_edge("guardrail", END)
    return graph.compile()


_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_graph()
    return _agent


agent = get_agent()
