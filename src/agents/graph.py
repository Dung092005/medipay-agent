from __future__ import annotations

import os

# LangSmith is a transitive LangChain dependency, but it is not an
# observability backend for this service. Disable its ambient environment
# tracer before importing LangGraph so a stale local `.env` cannot cause 403
# requests or leak prompts. Langfuse is configured explicitly by the adapter.
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"
for _name in (
    "LANGCHAIN_API_KEY",
    "LANGCHAIN_PROJECT",
    "LANGCHAIN_ENDPOINT",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
):
    os.environ.pop(_name, None)

from langgraph.graph import END, StateGraph  # noqa: E402

from src.agents.nodes.graphrag_nodes import (  # noqa: E402
    assemble_context_node,
    extract_entities_node,
    generate_node,
    guardrail_node,
    intake_node,
    retrieve_vectors_node,
    verify_evidence_node,
)
from src.agents.state import AgentState  # noqa: E402


async def should_continue(state: AgentState) -> str:
    """Route without dispatching a trivial condition through a thread pool."""
    return END if state.get("error") else "extract_entities"


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("intake", intake_node)
    graph.add_node("extract_entities", extract_entities_node)
    graph.add_node("retrieve_vectors", retrieve_vectors_node)
    graph.add_node("assemble_context", assemble_context_node)
    graph.add_node("verify_evidence", verify_evidence_node)
    graph.add_node("generate", generate_node)
    graph.add_node("guardrail", guardrail_node)

    graph.set_entry_point("intake")
    graph.add_conditional_edges("intake", should_continue)
    graph.add_edge("extract_entities", "retrieve_vectors")
    graph.add_edge("retrieve_vectors", "assemble_context")
    graph.add_edge("assemble_context", "verify_evidence")
    graph.add_edge("verify_evidence", "generate")
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
