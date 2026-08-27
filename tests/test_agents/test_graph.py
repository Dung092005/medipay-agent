import os
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.nodes.graphrag_nodes import (
    _audit_claims,
    _deterministic_legal_unit_response,
    generate_node,
    guardrail_node,
    verify_evidence_node,
)
from src.agents.prompts import NO_EVIDENCE_RESPONSE
from src.config import get_settings
from src.models.graph import Citation, RetrievalResult
from src.services.chat import RetrievalBundle
from src.services.claims import claim_facts_supported


@pytest.fixture(autouse=True)
def reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_claim_fact_verifier_rejects_changed_number_and_status_polarity():
    evidence = ["Văn bản có hiệu lực từ ngày 01/07/2026 và còn hiệu lực."]
    assert claim_facts_supported("Có hiệu lực từ ngày 01/07/2026.", evidence)
    assert not claim_facts_supported("Có hiệu lực từ ngày 02/07/2026.", evidence)
    assert not claim_facts_supported("Văn bản hết hiệu lực.", evidence)


def test_claim_fact_verifier_rejects_claims_without_concrete_facts():
    assert not claim_facts_supported(
        "Hồ sơ được nộp tại bộ phận một cửa.",
        ["Hồ sơ được nộp tại bộ phận một cửa."],
    )


def test_claim_audit_uses_lexical_evidence_for_claims_without_concrete_facts():
    citation = Citation(
        document_id="doc-a",
        chunk_id="chunk-a",
        quote="Hồ sơ được nộp tại bộ phận một cửa.",
    )

    claims = _audit_claims(
        "Hồ sơ được nộp tại bộ phận một cửa.", [citation], ""
    )

    assert claims[0]["verification"] == "entailed"
    assert claims[0]["evidence_ids"] == ["chunk-a"]


def test_claim_fact_verifier_rejects_neutral_status_wording():
    neutral = ["Cổng thông tin đăng tải dữ liệu về hiệu lực của văn bản."]

    assert not claim_facts_supported("Văn bản còn hiệu lực.", neutral)
    assert not claim_facts_supported("Văn bản hết hiệu lực.", neutral)

    claims = _audit_claims(
        "Văn bản còn hiệu lực.",
        [
            Citation(
                document_id="doc-a",
                chunk_id="chunk-a",
                quote=neutral[0],
            )
        ],
        "",
    )
    assert claims[0]["verification"] == "unsupported"
    assert claims[0]["evidence_ids"] == []


def test_claim_audit_does_not_stitch_numeric_facts_across_sources():
    citations = [
        Citation(
            document_id="a",
            chunk_id="a",
            title="A",
            quote="Tham gia BHYT 05 năm liên tục.",
        ),
        Citation(
            document_id="b",
            chunk_id="b",
            title="B",
            quote="Nhóm khác được hưởng 100% chi phí.",
        ),
    ]
    claims = _audit_claims(
        "Tham gia BHYT 05 năm liên tục được hưởng 100% chi phí.", citations, ""
    )
    assert claims[0]["verification"] == "unsupported"
    assert claims[0]["evidence_ids"] == []


@pytest.mark.asyncio
async def test_guardrail_removes_citations_when_core_claim_is_unsupported():
    evidence = RetrievalResult(
        chunk_id="chunk-a",
        document_id="doc-a",
        dataset_id="release-1",
        title="Văn bản A",
        content="Quy định về thủ tục tiếp nhận hồ sơ.",
        source_start=0,
        source_end=39,
        text_sha256="a" * 64,
    )
    result = await guardrail_node(
        {
            "query": "mức hưởng là bao nhiêu",
            "retrieved_evidence": [evidence],
            "response": "Người bệnh chắc chắn được hưởng 100%.",
        }
    )
    assert result["response"] == NO_EVIDENCE_RESPONSE
    assert result["claims"] == []
    assert result["citations"] == []


@pytest.mark.asyncio
async def test_direct_citation_cannot_bypass_final_evidence_claim_enforcement():
    evidence = RetrievalResult(
        chunk_id="final-chunk",
        document_id="final-doc",
        dataset_id="release-1",
        content="Hồ sơ được tiếp nhận tại bộ phận một cửa.",
        source_start=0,
        source_end=45,
        text_sha256="a" * 64,
    )
    direct = Citation(
        document_id="stale-doc",
        chunk_id="stale-chunk",
        quote="Người bệnh được hưởng 100% chi phí.",
        evidence_kind="passage",
        provenance_verified=True,
        source_url="https://example.test/stale",
    )

    result = await guardrail_node(
        {
            "query": "mức hưởng là bao nhiêu",
            "retrieved_evidence": [evidence],
            "direct_citations": [direct],
            "response": "Người bệnh được hưởng 100% chi phí.",
        }
    )

    assert result == {
        "response": NO_EVIDENCE_RESPONSE,
        "citations": [],
        "claims": [],
    }


@pytest.mark.asyncio
async def test_guardrail_prunes_all_entailed_citations_to_claim_evidence_ids():
    evidence = [
        RetrievalResult(
            chunk_id="chunk-a",
            document_id="doc-a",
            content="Hồ sơ được nộp tại bộ phận một cửa.",
        ),
        RetrievalResult(
            chunk_id="chunk-b",
            document_id="doc-b",
            content="Cơ quan công khai lịch tiếp công dân hằng tuần.",
        ),
    ]

    result = await guardrail_node(
        {
            "query": "Nộp hồ sơ ở đâu?",
            "retrieved_evidence": evidence,
            "response": "Hồ sơ được nộp tại bộ phận một cửa.",
        }
    )

    assert [citation["chunk_id"] for citation in result["citations"]] == ["chunk-a"]
    assert result["claims"][0]["verification"] == "entailed"


@pytest.mark.asyncio
async def test_guardrail_prunes_deterministic_source_answer_citations():
    supported_text = "Mức hưởng bảo hiểm y tế là 80% chi phí khám chữa bệnh."
    evidence = [
        RetrievalResult(
            chunk_id="chunk-a",
            document_id="doc-a",
            content=supported_text,
            text_sha256="a" * 64,
        ),
        RetrievalResult(
            chunk_id="chunk-b",
            document_id="doc-b",
            content="Người tham gia bảo hiểm được cấp mã số cá nhân duy nhất.",
            text_sha256="b" * 64,
        ),
    ]

    result = await guardrail_node(
        {
            "query": "Mức hưởng bảo hiểm y tế là bao nhiêu?",
            "retrieved_evidence": evidence,
            "response": f"- {supported_text}",
        }
    )

    assert [citation["chunk_id"] for citation in result["citations"]] == ["chunk-a"]
    assert result["claims"][0]["verification"] == "entailed"


@pytest.mark.asyncio
async def test_verified_direct_metadata_prunes_unattached_citations():
    supporting = Citation(
        document_id="doc-supported",
        chunk_id="metadata:doc-supported",
        title="Văn bản hỗ trợ",
        quote="Tên văn bản là Nghị định hỗ trợ.",
        evidence_kind="document_metadata",
        provenance_verified=True,
        source_url="https://example.test/supported",
    )
    unrelated = Citation(
        document_id="doc-unrelated",
        chunk_id="metadata:doc-unrelated",
        title="Văn bản khác",
        quote="Cơ quan ban hành là Bộ Y tế.",
        evidence_kind="document_metadata",
        provenance_verified=True,
        source_url="https://example.test/unrelated",
    )

    result = await guardrail_node(
        {
            "query": "Tên văn bản là gì?",
            "retrieved_evidence": [],
            "direct_citations": [supporting, unrelated],
            "response": "Tên văn bản là Nghị định hỗ trợ.",
        }
    )

    assert result["response"] == "Tên văn bản là Nghị định hỗ trợ."
    assert result["citations"] == [supporting.model_dump()]
    assert result["claims"][0]["evidence_ids"] == [supporting.chunk_id]


@pytest.mark.asyncio
async def test_verified_direct_metadata_without_entailed_attachment_abstains():
    citation = Citation(
        document_id="doc-unrelated",
        chunk_id="metadata:doc-unrelated",
        title="Văn bản khác",
        quote="Cơ quan ban hành là Bộ Y tế.",
        evidence_kind="document_metadata",
        provenance_verified=True,
        source_url="https://example.test/unrelated",
    )

    result = await guardrail_node(
        {
            "query": "Tên văn bản là gì?",
            "retrieved_evidence": [],
            "direct_citations": [citation],
            "response": "Nghị định chưa được kiểm chứng.",
        }
    )

    assert result == {
        "response": NO_EVIDENCE_RESPONSE,
        "citations": [],
        "claims": [],
    }


def test_langsmith_tracing_is_disabled_before_graph_use():
    import src.agents.graph  # noqa: F401

    assert os.environ.get("LANGCHAIN_TRACING_V2") == "false"
    assert os.environ.get("LANGSMITH_TRACING") == "false"
@pytest.mark.asyncio
async def test_agent_basic_flow():
    evidence = RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        title="Luật BHYT",
        content="Mức hưởng BHYT được quy định tại Điều 22.",
        channels=["semantic"],
    )
    with patch("src.agents.nodes.graphrag_nodes.get_runtime") as runtime_factory:
        runtime = runtime_factory.return_value
        runtime.retrieve_bundle = AsyncMock(return_value=RetrievalBundle([evidence], []))
        runtime.generate = AsyncMock(
            return_value="BHYT được quy định tại Điều 22."
        )
        from src.agents.graph import get_agent

        result = await get_agent().ainvoke({"query": "Quyền lợi BHYT?"})

    assert result["response"] == "BHYT được quy định tại Điều 22."
    assert result["citations"][0]["chunk_id"] == "chunk-1"
    assert result["claims"][0]["claim_type"] == "general"


@pytest.mark.asyncio
async def test_agent_state_structure():
    evidence = RetrievalResult(chunk_id="chunk-1", document_id="doc-1", content="Evidence")
    with patch("src.agents.nodes.graphrag_nodes.get_runtime") as runtime_factory:
        runtime = runtime_factory.return_value
        runtime.retrieve_bundle = AsyncMock(return_value=RetrievalBundle([evidence], []))
        runtime.generate = AsyncMock(return_value="Answer")
        from src.agents.graph import get_agent

        result = await get_agent().ainvoke({"query": "Test query"})

    assert isinstance(result, dict)
    assert "query" in result
    assert "retrieved_evidence" in result


@pytest.mark.asyncio
async def test_metadata_direct_answer_keeps_document_provenance():
    citation = Citation(
        document_id="doc-1", chunk_id="metadata:doc-1", title="Luật BHYT",
        quote="Tên văn bản.", channels=["exact"],
        evidence_kind="document_metadata", provenance_verified=True,
        source_url="https://example.test/luat-bhyt",
    )
    with patch("src.agents.nodes.graphrag_nodes.get_runtime") as runtime_factory:
        runtime = runtime_factory.return_value
        runtime.retrieve_bundle = AsyncMock(return_value=RetrievalBundle([], [], "Tên văn bản.", [citation]))
        runtime.generate = AsyncMock()
        from src.agents.graph import get_agent

        result = await get_agent().ainvoke({"query": "Tiêu đề văn bản là gì?"})

    assert result["response"] == "Tên văn bản."
    assert result["citations"] == [citation.model_dump()]
    runtime.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_high_risk_query_without_provenance_is_rejected():
    result = await verify_evidence_node({"query": "Văn bản này còn hiệu lực không?", "retrieved_evidence": []})

    assert result["verification_failed"] is True
    assert "xác minh" in result["response"]


@pytest.mark.asyncio
async def test_official_status_metadata_can_pass_status_gate():
    citation = Citation(
        document_id="doc-1",
        chunk_id="metadata:doc-1",
        dataset_id="release-1",
        title="Luật BHYT",
        quote="Còn hiệu lực",
        evidence_kind="document_metadata",
        provenance_verified=True,
        source_url="https://vbpl.vn/example",
    )
    result = await verify_evidence_node(
        {
            "query": "Văn bản này còn hiệu lực không?",
            "retrieved_evidence": [],
            "direct_citations": [citation],
        }
    )
    assert result["verification_failed"] is False


@pytest.mark.asyncio
async def test_high_risk_guardrail_abstains_when_claim_is_not_source_backed():
    evidence = RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        dataset_id="release-1",
        content="Nguồn chính thức ghi nhận ngày ban hành văn bản.",
        source_start=0,
        source_end=52,
    )
    result = await guardrail_node(
        {
            "query": "Văn bản này còn hiệu lực không?",
            "response": "Văn bản chắc chắn còn hiệu lực.",
            "retrieved_evidence": [evidence],
        }
    )

    assert result["response"] == NO_EVIDENCE_RESPONSE


@pytest.mark.asyncio
async def test_generation_preserves_model_abstention_without_safe_source_rule():
    evidence = RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        title="Nghị quyết BHYT",
        section_title="Điều 2",
        content="Người cao tuổi chưa có thẻ BHYT được hỗ trợ 30%.",
        dataset_id="release-1",
        source_start=0,
        source_end=52,
    )
    with patch("src.agents.nodes.graphrag_nodes.get_runtime") as runtime_factory:
        runtime_factory.return_value.generate = AsyncMock(
            return_value=(
                "Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp "
                "để trả lời đầy đủ câu hỏi."
            )
        )
        result = await generate_node(
            {"query": "Đối tượng nào được hỗ trợ?", "context": "evidence", "retrieved_evidence": [evidence]}
        )

    assert result["response"] == NO_EVIDENCE_RESPONSE
    assert "Người cao tuổi" not in result["response"]


@pytest.mark.asyncio
async def test_nonverification_query_does_not_use_deterministic_composer():
    evidence = RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        content="Nội dung có thể tạo thành câu trả lời xác định.",
        source_start=0,
        source_end=48,
        text_sha256="a" * 64,
    )
    with (
        patch("src.agents.nodes.graphrag_nodes.compose_source_answer", return_value="- deterministic") as composer,
        patch("src.agents.nodes.graphrag_nodes.get_runtime") as runtime_factory,
    ):
        runtime_factory.return_value.generate = AsyncMock(return_value="LLM response")
        result = await generate_node(
            {
                "query": "Hãy tóm tắt văn bản này",
                "context": "evidence",
                "retrieved_evidence": [evidence],
            }
        )

    assert result["response"] == "LLM response"
    composer.assert_not_called()
    runtime_factory.return_value.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_can_exceed_public_citation_budget(monkeypatch):
    monkeypatch.setenv("MAX_LLM_EVIDENCE", "12")
    monkeypatch.setenv("MAX_CITATIONS", "8")
    get_settings.cache_clear()
    evidence = [
        RetrievalResult(
            chunk_id=f"chunk-{index}",
            document_id="doc-1",
            content=f"Evidence item {index}",
            score=index / 10,
        )
        for index in range(12)
    ]
    with patch("src.agents.nodes.graphrag_nodes.get_runtime") as runtime_factory:
        runtime = runtime_factory.return_value
        runtime.retrieve_bundle = AsyncMock(return_value=RetrievalBundle(evidence, []))
        runtime.generate = AsyncMock(
            return_value=" ".join(f"Evidence item {index}." for index in range(8))
        )
        from src.agents.graph import get_agent

        result = await get_agent().ainvoke({"query": "Test query"})

    assert "Evidence item 0" in runtime.generate.await_args.args[1]
    assert "EVIDENCE_ID=E1" in runtime.generate.await_args.args[1]
    assert "EVIDENCE_ID=E12" in runtime.generate.await_args.args[1]
    assert len(result["citations"]) == 8
    get_settings.cache_clear()


def test_legal_unit_formatter_is_stable_and_deduplicated():
    evidence = [
        RetrievalResult(
            chunk_id="chunk-1", document_id="doc-1", unit_id="unit-1", section_title="a)",
            content="Điều kiện thứ nhất.",
        ),
        RetrievalResult(
            chunk_id="chunk-2", document_id="doc-1", unit_id="unit-1", section_title="a)",
            content="Bản trùng không được lặp.",
        ),
        RetrievalResult(
            chunk_id="chunk-3", document_id="doc-1", unit_id="unit-2", section_title="b)",
            content="Điều kiện thứ hai.",
        ),
    ]
    formatted = _deterministic_legal_unit_response(evidence)
    assert formatted.count("a):") == 1
    assert "Điều kiện thứ nhất" in formatted
    assert "Điều kiện thứ hai" in formatted
    assert "Bản trùng" not in formatted
