from __future__ import annotations

import asyncio
from collections.abc import Sequence
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text

from src.agents.prompts import NO_EVIDENCE_RESPONSE, SYSTEM_PROMPT
from src.config import get_settings
from src.db.repositories import GraphRepository
from src.db.session import session_scope
from src.integrations.embeddings import EmbeddingModel, get_embedding_model
from src.integrations.langfuse import llm_invoke_config, trace_span
from src.integrations.neo4j import Neo4jGraphStore
from src.services.llm import get_llm


class GraphRagUnavailableError(RuntimeError):
    """A required GraphRAG dependency is unavailable."""


class ChatProviderError(RuntimeError):
    """The configured chat provider failed to generate a response."""


class GraphRagRuntime:
    """Own request-time GraphRAG dependencies and their lifecycle."""

    def __init__(self) -> None:
        self._embeddings: EmbeddingModel | None = None
        self._graph_store: Neo4jGraphStore | None = None

    def _get_embeddings(self) -> EmbeddingModel:
        if self._embeddings is None:
            self._embeddings = get_embedding_model()
        return self._embeddings

    def _get_graph_store(self) -> Neo4jGraphStore:
        if self._graph_store is None:
            self._graph_store = Neo4jGraphStore()
        return self._graph_store

    async def retrieve(self, query: str) -> tuple[list, list]:
        async with trace_span(
            "retrieve-context",
            as_type="retriever",
            input={"query": query},
        ) as span:
            evidence, relations = await self._retrieve(query)
            if span is not None:
                span.update(
                    output={
                        "evidence_count": len(evidence),
                        "relation_count": len(relations),
                        "chunk_ids": [item.chunk_id for item in evidence[:20]],
                    }
                )
            return evidence, relations

    async def _retrieve(self, query: str) -> tuple[list, list]:
        settings = get_settings()
        try:
            embeddings = self._get_embeddings()
            graph_store = self._get_graph_store()
            async with trace_span("neo4j-connectivity") as span:
                await asyncio.wait_for(graph_store.verify_connectivity(), timeout=5)
                if span is not None:
                    span.update(output={"ok": True})
            async with trace_span(
                "embedding-query",
                as_type="embedding",
                input={"query_length": len(query)},
                metadata={"model": settings.embedding_model},
            ) as span:
                vector = await embeddings.embed_query(query)
                if span is not None:
                    span.update(
                        output={
                            "query_length": len(query),
                            "embedding_dimensions": len(vector),
                        }
                    )
            async with session_scope() as session:
                repository = GraphRepository(session, graph_store)
                async with trace_span("get-current-dataset") as span:
                    dataset_id = await repository.current_dataset()
                    if span is not None:
                        span.update(output={"dataset_id": dataset_id})
                if dataset_id is None:
                    raise GraphRagUnavailableError("No active dataset is available")
                async with trace_span(
                    "pgvector-search",
                    as_type="retriever",
                    metadata={
                        "dataset_id": dataset_id,
                        "similarity_threshold": settings.semantic_similarity_threshold,
                    },
                ) as span:
                    vector_results = await repository.search_vectors(
                        vector,
                        limit=settings.retrieval_top_k,
                        dataset_id=dataset_id,
                        similarity_threshold=settings.semantic_similarity_threshold,
                    )
                    if span is not None:
                        span.update(
                            output={
                                "result_count": len(vector_results),
                                "top_k": settings.retrieval_top_k,
                            }
                        )
                document_ids = list(dict.fromkeys(item.document_id for item in vector_results))
                async with trace_span(
                    "neo4j-expand",
                    as_type="retriever",
                    metadata={"dataset_id": dataset_id},
                ) as span:
                    graph_results = await repository.expand_entities(
                        document_ids,
                        dataset_id=dataset_id,
                        hops=settings.graph_hops,
                        limit=settings.graph_neighbor_limit,
                    )
                    if span is not None:
                        span.update(
                            output={
                                "relation_count": len(graph_results),
                                "document_count": len(document_ids),
                                "hops": settings.graph_hops,
                            }
                        )
                related_ids = list(
                    dict.fromkeys(
                        item_id
                        for relation in graph_results
                        for item_id in (relation.source_id, relation.target_id)
                        if item_id
                    )
                )
                hydrate_ids = related_ids[: settings.graph_evidence_limit]
                async with trace_span(
                    "hydrate-documents",
                    as_type="retriever",
                    metadata={"dataset_id": dataset_id},
                ) as span:
                    graph_evidence = await repository.hydrate_documents(
                        hydrate_ids,
                        dataset_id=dataset_id,
                        chunks_per_document=settings.max_chunks_per_document,
                    )
                    if span is not None:
                        span.update(output={"evidence_count": len(graph_evidence)})
            return _limit_evidence(
                _merge_evidence(vector_results, graph_evidence),
                settings.max_llm_evidence,
            ), graph_results
        except GraphRagUnavailableError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise GraphRagUnavailableError("GraphRAG dependencies are unavailable") from exc
        except Exception as exc:
            raise GraphRagUnavailableError("GraphRAG retrieval failed") from exc

    async def generate(self, query: str, context: str) -> str:
        settings = get_settings()
        try:
            llm = get_llm()
            result = await asyncio.wait_for(
                llm.ainvoke(
                    [
                        SystemMessage(content=SYSTEM_PROMPT),
                        HumanMessage(
                            content=(
                                f"Câu hỏi người dùng:\n{query}\n\n"
                                f"Evidence và graph relations được phép sử dụng:\n{context}"
                            )
                        ),
                    ],
                    config=llm_invoke_config() or None,
                ),
                timeout=settings.llm_timeout_seconds,
            )
        except TimeoutError:
            return NO_EVIDENCE_RESPONSE
        except Exception as exc:
            raise ChatProviderError("Chat provider failed") from exc
        content = result.content
        if isinstance(content, str):
            return content
        if isinstance(content, Sequence):
            return "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        return ""

    async def readiness(self) -> dict[str, bool]:
        settings = get_settings()
        checks = {
            "llm": settings.llm_configured,
            "embedding": bool(settings.openai_api_key and settings.embedding_model),
            "database": False,
            "neo4j": False,
        }
        if settings.app_env == "test":
            return {**checks, "database": True, "neo4j": True}
        try:
            async with session_scope() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = True
        except Exception:
            pass
        try:
            await asyncio.wait_for(self._get_graph_store().verify_connectivity(), timeout=5)
            checks["neo4j"] = True
        except Exception:
            pass
        return checks

    async def close(self) -> None:
        if self._graph_store is not None:
            await self._graph_store.close()
            self._graph_store = None
        self._embeddings = None


def _merge_evidence(vector_results: list, graph_results: list) -> list:
    merged: dict[str, object] = {}
    for item in [*vector_results, *graph_results]:
        current = merged.get(item.chunk_id)
        if current is None:
            merged[item.chunk_id] = item
            continue
        current.channels = sorted(set([*current.channels, *item.channels]))
        current.score = max(current.score, item.score)
    return list(merged.values())


def _limit_evidence(evidence: list, limit: int) -> list:
    """Keep highest-ranked unique evidence within configured citation budget."""
    if limit <= 0:
        return []
    ranked = sorted(
        evidence,
        key=lambda item: (-float(getattr(item, "score", 0.0)), str(item.chunk_id)),
    )
    return ranked[:limit]


@lru_cache
def get_runtime() -> GraphRagRuntime:
    return GraphRagRuntime()


__all__ = [
    "ChatProviderError",
    "GraphRagRuntime",
    "GraphRagUnavailableError",
    "NO_EVIDENCE_RESPONSE",
    "SYSTEM_PROMPT",
    "get_runtime",
]
