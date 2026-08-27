from __future__ import annotations

from typing import TypedDict

from src.models.graph import Citation, Entity, Relation, RetrievalResult


class AgentState(TypedDict, total=False):
    """State passed between GraphRAG LangGraph nodes."""

    query: str
    entities: list[Entity]
    vector_results: list[RetrievalResult]
    graph_results: list[Relation]
    retrieved_evidence: list[RetrievalResult]
    direct_citations: list[Citation]
    citations: list[Citation]
    claims: list[dict]
    context: str
    response: str
    error: str
    metadata: dict
    verification_failed: bool
