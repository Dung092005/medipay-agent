import pytest

from src.models.graph import RetrievalResult
from src.services.evidence_answer import compose_source_answer, select_source_fragments


def evidence(
    content, *, section="Điều 22", chunk="c1", score=1.0,
    provenance=True,
):
    return RetrievalResult(
        chunk_id=chunk, document_id="doc", title="Luật BHYT",
        document_number="25/2008/QH12", section_title=section,
        content=content, score=score,
        source_start=0 if provenance else None,
        source_end=len(content) if provenance else None,
        text_sha256="a" * 64 if provenance else "",
        channels=["lexical", "semantic"],
    )


def test_composer_extracts_only_relevant_numeric_rule_fragment():
    rows = [
        evidence("Nội dung hành chính không liên quan.", chunk="noise", score=2.0),
        evidence(
            "Người tham gia thuộc trường hợp quy định được quỹ BHYT thanh toán 100% chi phí khám chữa bệnh.",
            chunk="answer",
        ),
    ]
    answer = compose_source_answer("trường hợp nào được BHYT thanh toán 100%", rows)
    assert "100%" in answer
    assert "hành chính" not in answer
    assert "chunk" not in answer.casefold()


def test_composer_does_not_join_numbers_from_different_sources():
    rows = [
        evidence("Người bệnh tham gia BHYT 05 năm liên tục.", chunk="a"),
        evidence("Một nhóm khác được thanh toán 100% chi phí.", chunk="b"),
    ]
    assert compose_source_answer(
        "tham gia 05 năm liên tục có được thanh toán 100% không", rows
    ) is None


def test_composer_accepts_complete_numeric_combination_from_one_source():
    row = evidence(
        "Người bệnh tham gia BHYT 05 năm liên tục được thanh toán 100% chi phí.",
    )
    answer = compose_source_answer(
        "tham gia 05 năm liên tục có được thanh toán 100% không", [row]
    )
    assert answer is not None
    assert "05 năm liên tục" in answer
    assert "100%" in answer


@pytest.mark.parametrize(
    ("text_sha256", "source_start", "source_end"),
    [
        ("not-a-sha256", None, None),
        ("", 20, 10),
        ("", -1, 10),
        ("", 0, 0),
        ("", 0, 500),
    ],
)
def test_composer_rejects_malformed_or_invalid_provenance(
    text_sha256, source_start, source_end
):
    content = "Người tham gia BHYT được thanh toán 100% chi phí khám chữa bệnh."
    row = evidence(content).model_copy(
        update={
            "text_sha256": text_sha256,
            "source_start": source_start,
            "source_end": source_end,
        }
    )
    assert compose_source_answer("BHYT thanh toán 100% chi phí", [row]) is None


def test_composer_does_not_render_section_title_without_content_overlap():
    row = evidence(
        "Nội dung thủ tục hành chính không liên quan.",
        section="Đối tượng BHYT được thanh toán 100% chi phí khám chữa bệnh",
    )
    assert compose_source_answer("đối tượng BHYT thanh toán 100%", [row]) is None


def test_select_source_fragments_returns_empty_for_nonpositive_limit():
    row = evidence("Người tham gia BHYT được thanh toán 100% chi phí.")
    assert select_source_fragments("BHYT thanh toán 100% chi phí", [row], limit=0) == []
    assert select_source_fragments("BHYT thanh toán 100% chi phí", [row], limit=-1) == []
