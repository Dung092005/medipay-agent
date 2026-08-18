from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.models.graph import RetrievalResult
from src.services.chat import GraphRagRuntime, _limit_evidence


def test_limit_evidence_uses_internal_budget():
    evidence = [
        RetrievalResult(
            chunk_id=f"chunk-{index}",
            document_id="doc-1",
            content=f"Evidence {index}",
            score=index / 10,
        )
        for index in range(12)
    ]

    selected = _limit_evidence(evidence, 10)

    assert len(selected) == 10
    assert selected[0].chunk_id == "chunk-11"
    assert selected[-1].chunk_id == "chunk-2"


@pytest.mark.asyncio
async def test_generate_uses_configured_llm():
    runtime = GraphRagRuntime()
    result = type("Message", (), {"content": "Grounded answer"})()
    llm = type("Llm", (), {"ainvoke": AsyncMock(return_value=result)})()

    with patch("src.services.chat.get_llm", return_value=llm):
        answer = await runtime.generate("question", "EVIDENCE_ID=chunk-1")

    assert answer == "Grounded answer"
    llm.ainvoke.assert_awaited_once()
    assert "EVIDENCE_ID=chunk-1" in llm.ainvoke.await_args.args[0][1].content


@pytest.mark.asyncio
async def test_retrieve_nests_child_span_names():
    names: list[str] = []

    @asynccontextmanager
    async def fake_span(name, **_kwargs):
        names.append(name)
        yield SimpleNamespace(update=lambda **_kw: None)

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    evidence = RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        content="Evidence",
        channels=["semantic"],
    )
    repository = SimpleNamespace(
        current_dataset=AsyncMock(return_value="dataset-1"),
        search_vectors=AsyncMock(return_value=[evidence]),
        expand_entities=AsyncMock(return_value=[]),
        hydrate_documents=AsyncMock(return_value=[]),
    )
    runtime = GraphRagRuntime()
    runtime._embeddings = SimpleNamespace(embed_query=AsyncMock(return_value=[0.1, 0.2, 0.3]))
    runtime._graph_store = SimpleNamespace(verify_connectivity=AsyncMock())

    with (
        patch("src.services.chat.trace_span", fake_span),
        patch("src.services.chat.session_scope", fake_session_scope),
        patch("src.services.chat.GraphRepository", return_value=repository),
    ):
        result_evidence, relations = await runtime.retrieve("Quyền lợi BHYT?")

    assert names == [
        "retrieve-context",
        "neo4j-connectivity",
        "embedding-query",
        "get-current-dataset",
        "pgvector-search",
        "neo4j-expand",
        "hydrate-documents",
    ]
    assert result_evidence[0].chunk_id == "chunk-1"
    assert relations == []
    repository.search_vectors.assert_awaited_once()
    repository.expand_entities.assert_awaited_once()
    repository.hydrate_documents.assert_awaited_once()
