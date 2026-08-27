from __future__ import annotations

from scripts.scrape_bhxh_ground_truth import (
    classify_record,
    extract_answered_item_ids,
    extract_legal_basis,
    normalize_question,
    redact_pii,
    temporal_risk,
)


def make_record(
    question: str,
    answer: str = "Theo quy định pháp luật về BHYT, người tham gia được hưởng quyền lợi theo phạm vi chi trả.",
    category: str = "Bảo hiểm y tế",
    answered_at: str = "01/08/2026",
) -> dict[str, str]:
    return {
        "question": question,
        "ground_truth": answer,
        "category": category,
        "answered_at": answered_at,
    }


def test_classify_rejects_wrong_category_before_quality_rules() -> None:
    decision, reason = classify_record(
        make_record("Khám trái tuyến được hưởng BHYT thế nào?", category="Ốm đau, thai sản")
    )

    assert (decision, reason) == ("rejected", "wrong_category")


def test_classify_accepts_general_bhyt_policy_question() -> None:
    decision, reason = classify_record(make_record("Khám trái tuyến được hưởng BHYT thế nào?"))

    assert decision == "accepted"
    assert reason == "general BHYT policy question answerable from public legal corpus"


def test_classify_rejects_personal_internal_lookup() -> None:
    decision, reason = classify_record(
        make_record("Kiểm tra giúp tôi mã BHXH 0123456789, hồ sơ của tôi đã được duyệt chưa?")
    )

    assert (decision, reason) == ("rejected", "personal_record_lookup")


def test_classify_routes_mixed_personal_policy_question_to_review() -> None:
    decision, reason = classify_record(
        make_record("Thẻ BHYT của tôi còn hạn không và quy định chung về thời hạn thẻ là gì?")
    )

    assert decision == "needs_review"
    assert reason == "mixed_personal_and_policy_question"


def test_classify_rejects_missing_question_or_answer() -> None:
    decision, reason = classify_record(make_record("", answer="Câu trả lời có nội dung."))
    assert (decision, reason) == ("rejected", "missing_question")

    decision, reason = classify_record(make_record("Điều kiện hưởng BHYT là gì?", answer=""))
    assert (decision, reason) == ("rejected", "missing_answer")


def test_extract_legal_basis_keeps_only_explicit_references() -> None:
    answer = (
        "Căn cứ khoản 3 Điều 22 Luật bảo hiểm y tế và Nghị định số 146/2018/NĐ-CP; "
        "không suy đoán thêm điều khoản khác."
    )

    basis = extract_legal_basis(answer)

    assert {item["type"] for item in basis} >= {"law", "decree"}
    assert any(item["document_number"] == "146/2018/NĐ-CP" for item in basis)
    assert any(item["article"] == "22" and item["clause"] == "3" for item in basis)


def test_extract_legal_basis_returns_empty_when_answer_has_no_reference() -> None:
    assert extract_legal_basis("BHXH Việt Nam trả lời: bạn liên hệ cơ quan địa phương.") == []


def test_redact_pii_preserves_question_meaning() -> None:
    text = (
        "Tôi là Nguyễn Văn A, email test@example.com, SĐT 0912345678, "
        "mã BHXH 0123456789 và CCCD 012345678901."
    )

    redacted = redact_pii(text)

    assert "Nguyễn Văn A" not in redacted
    assert "test@example.com" not in redacted
    assert "0912345678" not in redacted
    assert "0123456789" not in redacted
    assert "[REDACTED]" in redacted
    assert "mã BHXH" in redacted


def test_temporal_risk_increases_for_old_answers() -> None:
    assert temporal_risk("01/08/2026", "Quy định hiện hành") == "low"
    assert temporal_risk("01/08/2022", "Quy định tại Nghị định số 146/2018/NĐ-CP") == "medium"
    assert temporal_risk("01/08/2018", "Quy định tại Luật năm 2008") == "high"


def test_normalize_question_deduplicates_punctuation_and_whitespace() -> None:
    assert normalize_question("  Khám trái tuyến được hưởng BHYT thế nào? ") == normalize_question(
        "Khám trái tuyến được hưởng BHYT thế nào"
    )


def test_extract_answered_item_ids_skips_unanswered_list_items() -> None:
    html = """
    <div class="item-vanban">
      <a href="?ItemID=101">Câu hỏi đã trả lời</a>
      <span>Trạng thái: Đã trả lời</span>
    </div>
    <div class="item-vanban">
      <a href="?ItemID=102">Câu hỏi chưa trả lời</a>
      <span>Trạng thái: Chưa trả lời</span>
    </div>
    """

    assert extract_answered_item_ids(html) == [101]
