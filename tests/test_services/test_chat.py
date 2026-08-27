import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.nodes.graphrag_nodes import _pack_context
from src.integrations.qdrant import VectorHit
from src.models.graph import DocumentCandidate, RetrievalResult
from src.services.chat import (
    GraphRagRuntime,
    RetrievalBundle,
    _answer_cache_allowed,
    _format_metadata_answer,
    _limit_evidence,
)


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


def test_metadata_status_fails_closed_without_verified_source():
    document = DocumentCandidate(
        document_id="doc-1",
        title="Văn bản",
        so_ky_hieu="123/2020/TT-BYT",
        legal_status="Còn hiệu lực",
        answer_ready=True,
    )
    answer = _format_metadata_answer("Văn bản 123/2020/TT-BYT còn hiệu lực không?", document)
    assert "chưa xác minh từ nguồn chính thức" in answer


def test_metadata_category_uses_user_facing_labels():
    document = DocumentCandidate(
        document_id="doc-1",
        title="Văn bản",
        so_ky_hieu="123/2020/TT-BYT",
        categories=["bhyt"],
        answer_ready=True,
    )
    answer = _format_metadata_answer("Văn bản 123/2020/TT-BYT thuộc nhóm nào?", document)
    assert "Nhóm nội dung của văn bản số hiệu 123/2020/TT-BYT là bảo hiểm y tế (BHYT) trong bộ dữ liệu" in answer
    assert "Văn bản 123/2020/TT-BYT:" not in answer


def test_metadata_title_and_status_answers_are_intent_focused():
    document = DocumentCandidate(
        document_id="doc-1",
        title="Nghị quyết về hỗ trợ bảo hiểm y tế",
        so_ky_hieu="60/2026/NQ-HĐND",
        ngay_co_hieu_luc="01/07/2026",
        legal_status="Còn hiệu lực",
        legal_status_verified=True,
        categories=["bhyt"],
        answer_ready=True,
    )
    title = _format_metadata_answer("Văn bản 60/2026/NQ-HĐND có tên đầy đủ là gì?", document)
    status = _format_metadata_answer("Văn bản 60/2026/NQ-HĐND có hiệu lực từ ngày nào?", document)
    citation = _format_metadata_answer("Văn bản 60/2026/NQ-HĐND có hiệu lực từ ngày nào?", document, include_context=True)
    assert title == "Tên đầy đủ của văn bản số hiệu 60/2026/NQ-HĐND là: Nghị quyết về hỗ trợ bảo hiểm y tế."
    assert status == "Văn bản số hiệu 60/2026/NQ-HĐND có hiệu lực từ ngày 01/07/2026 và có tình trạng Còn hiệu lực."
    assert "Văn bản 60/2026/NQ-HĐND:" in citation


def test_context_packer_keeps_complete_evidence_blocks():
    evidence = [
        RetrievalResult(
            chunk_id="chunk-1",
            document_id="doc-1",
            dataset_id="release-1",
            content="A" * 40,
        ),
        RetrievalResult(
            chunk_id="chunk-2",
            document_id="doc-1",
            dataset_id="release-1",
            content="B" * 40,
        ),
    ]
    context = _pack_context(evidence, [], 180)
    assert "EVIDENCE_ID=E1" in context
    assert "EVIDENCE_ID=E2" not in context
    assert context.endswith("A" * 40)


def test_context_packer_honors_token_budget():
    evidence = [
        RetrievalResult(
            chunk_id=f"chunk-{index}",
            document_id="doc-1",
            dataset_id="release-1",
            content="Thông tin pháp lý có kiểm chứng " * 80,
        )
        for index in range(3)
    ]
    context = _pack_context(evidence, [], 100_000, token_budget=600, model="gpt-4o-mini")
    assert "EVIDENCE_ID=E1" in context
    assert "EVIDENCE_ID=E3" not in context


def test_answer_cache_excludes_temporal_and_high_risk_intents():
    assert _answer_cache_allowed("Quyền lợi BHYT là gì?")
    assert not _answer_cache_allowed("Văn bản này còn hiệu lực không?")
    assert not _answer_cache_allowed("Mức chi trả là bao nhiêu?")


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
async def test_generate_cache_is_scoped_to_active_release():
    runtime = GraphRagRuntime()
    runtime._active_release = ("release-1", 10, 0.0)
    result = type("Message", (), {"content": "Grounded answer"})()
    llm = type("Llm", (), {"ainvoke": AsyncMock(return_value=result)})()

    with patch("src.services.chat.get_llm", return_value=llm):
        first = await runtime.generate("same question", "EVIDENCE_ID=chunk-1")
        second = await runtime.generate(" same   question ", "EVIDENCE_ID=chunk-1")

    assert first == second == "Grounded answer"
    llm.ainvoke.assert_awaited_once()

    runtime._active_release = ("release-2", 11, 0.0)
    with patch("src.services.chat.get_llm", return_value=llm):
        await runtime.generate("same question", "EVIDENCE_ID=chunk-1")
    assert llm.ainvoke.await_count == 2

    runtime._active_release = ("release-3", 12, 0.0)
    with patch("src.services.chat.get_llm", return_value=llm):
        await runtime.generate("Văn bản này còn hiệu lực không?", "EVIDENCE_ID=chunk-1")
        await runtime.generate("Văn bản này còn hiệu lực không?", "EVIDENCE_ID=chunk-1")
    assert llm.ainvoke.await_count == 4


@pytest.mark.asyncio
async def test_embedding_singleflight_deduplicates_concurrent_queries():
    runtime = GraphRagRuntime()
    embed = AsyncMock(return_value=[0.1] * 1536)
    runtime._embeddings = SimpleNamespace(embed_query=embed)

    vectors = await asyncio.gather(*(runtime._embed_query("cùng câu hỏi") for _ in range(5)))

    assert len(vectors) == 5
    assert embed.await_count == 1


@pytest.mark.asyncio
async def test_retrieve_bundle_many_batches_embeddings_and_qdrant(monkeypatch):
    runtime = GraphRagRuntime()
    runtime._embeddings = SimpleNamespace(
        embed_queries=AsyncMock(return_value=[[0.1] * 3, [0.2] * 3]),
    )
    runtime._vector_store = SimpleNamespace(
        search_many=AsyncMock(return_value=[[], []]),
    )
    monkeypatch.setattr("src.services.chat.get_settings", lambda: SimpleNamespace(
        embedding_dimensions=3,
        retrieval_top_k=2,
        semantic_similarity_threshold=0.1,
        max_llm_evidence=8,
    ))
    runtime._retrieve_staged = AsyncMock(
        side_effect=lambda query, **_kwargs: RetrievalBundle(
            evidence=[RetrievalResult(chunk_id=query, document_id=query, content=query, score=1.0)],
            relations=[],
        )
    )
    with patch("src.services.chat.session_scope") as scope:
        @asynccontextmanager
        async def fake_scope():
            yield object()
        scope.side_effect = fake_scope
        with patch("src.services.chat.GraphRepository") as repository_factory:
            repository_factory.return_value.current_dataset_release = AsyncMock(return_value=("release", 2))
            result = await runtime.retrieve_bundle_many(["câu hỏi thứ nhất", "câu hỏi thứ hai"])

    runtime._embeddings.embed_queries.assert_awaited_once()
    runtime._vector_store.search_many.assert_awaited_once()
    assert runtime._vector_store.search_many.await_args.kwargs["query_texts"] == [
        "câu hỏi thứ nhất", "câu hỏi thứ hai",
    ]
    assert {item.chunk_id for item in result.evidence} == {"câu hỏi thứ nhất", "câu hỏi thứ hai"}


@pytest.mark.asyncio
async def test_single_vector_search_keeps_original_query_text():
    runtime = GraphRagRuntime()
    runtime._vector_store = SimpleNamespace(search=AsyncMock(return_value=[]))

    async def call_provider(_stage, _breaker, operation):
        return await operation()

    runtime._provider_call = call_provider

    await runtime._search_vectors(
        [0.1, 0.2, 0.3],
        query_text="khám chữa bệnh không đúng tuyến",
        dataset_id="release-1",
        limit=5,
    )

    assert runtime._vector_store.search.await_args.kwargs["query_text"] == (
        "khám chữa bệnh không đúng tuyến"
    )


@pytest.mark.asyncio
async def test_readiness_coalesces_concurrent_probes():
    runtime = GraphRagRuntime()
    probe = AsyncMock(return_value={"llm": True, "embedding": True, "database": True, "qdrant": True, "neo4j": True})
    runtime._readiness_probe = probe
    results = await asyncio.gather(*(runtime.readiness() for _ in range(20)))
    assert all(result["qdrant"] for result in results)
    probe.assert_awaited_once()


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
        dataset_id="dataset-1",
        content="Evidence",
        text_sha256=sha256(b"Evidence").hexdigest(),
        input_sha256=sha256(b"embedding-input").hexdigest(),
        channels=["semantic"],
    )
    repository = SimpleNamespace(
        current_dataset_release=AsyncMock(return_value=("dataset-1", 1)),
        find_documents=AsyncMock(return_value=[]),
            resolve_legal_units=AsyncMock(return_value=[]),
            expand_sibling_legal_units=AsyncMock(return_value=[]),
            search_lexical=AsyncMock(return_value=[]),
        hydrate_chunks=AsyncMock(return_value=[evidence]),
    )
    runtime = GraphRagRuntime()
    runtime._embeddings = SimpleNamespace(embed_query=AsyncMock(return_value=[0.1] * 1536))
    runtime._vector_store = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                VectorHit(
                    "chunk-1", "doc-1", "", 0.9, sha256(b"embedding-input").hexdigest()
                )
            ]
        )
    )

    with (
        patch("src.services.chat.trace_span", fake_span),
        patch("src.services.chat.session_scope", fake_session_scope),
        patch("src.services.chat.GraphRepository", return_value=repository),
    ):
        result_evidence, relations = await runtime.retrieve("Quyền lợi BHYT?")

    assert names == [
        "retrieve-context",
        "get-current-dataset",
        "embedding-query",
        "qdrant-search",
    ]
    assert result_evidence[0].chunk_id == "chunk-1"
    assert relations == []
    repository.search_lexical.assert_awaited_once()
    repository.hydrate_chunks.assert_awaited_once()
