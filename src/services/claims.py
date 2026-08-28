from __future__ import annotations

from src.domain.claims import ClaimType, LegalClaim
from src.models.graph import Citation


def classify_claim(text: str) -> ClaimType:
    lowered = text.casefold()
    if any(token in lowered for token in ("hiệu lực", "còn hiệu lực", "hết hiệu lực", "bãi bỏ", "thay thế")):
        return ClaimType.STATUS
    if any(token in lowered for token in ("mức hưởng", "được hưởng", "quyền lợi", "được chi trả", "thanh toán")):
        return ClaimType.ENTITLEMENT
    if any(token in lowered for token in ("điều kiện", "nếu ", "khi ", "trường hợp")):
        return ClaimType.CONDITION
    if any(token in lowered for token in ("thủ tục", "hồ sơ", "cần nộp", "liên hệ", "đối chiếu")):
        return ClaimType.PROCEDURE
    if any(token in lowered for token in ("ngoại lệ", "trừ trường hợp", "không áp dụng")):
        return ClaimType.EXCEPTION
    if any(token in lowered for token in ("văn bản", "nghị định", "thông tư", "luật ")):
        return ClaimType.DOCUMENT
    return ClaimType.GENERAL


def build_legal_claim(
    *,
    claim_id: str,
    text: str,
    citation: Citation | None,
    verification: str,
    reason: str,
) -> LegalClaim:
    evidence_ids = (citation.chunk_id,) if citation and citation.chunk_id else ()
    source_spans = (
        ((citation.source_start, citation.source_end),)
        if citation and (citation.source_start is not None or citation.source_end is not None)
        else ()
    )
    source_hashes = (citation.text_sha256,) if citation and citation.text_sha256 else ()
    subject = citation.title if citation else ""
    return LegalClaim(
        claim_id=claim_id,
        text=text,
        claim_type=classify_claim(text),
        subject=subject,
        entitlement=text if classify_claim(text) == ClaimType.ENTITLEMENT else "",
        effective_from=(text if classify_claim(text) == ClaimType.STATUS and "từ" in text.casefold() else ""),
        evidence_ids=evidence_ids,
        source_spans=source_spans,
        source_hashes=source_hashes,
        verification=verification,
        reason=reason,
    )


def claim_dict(claim: LegalClaim) -> dict[str, object]:
    """Serialize the domain claim without importing Pydantic into the domain."""
    return {
        "claim_id": claim.claim_id,
        "text": claim.text,
        "claim_type": claim.claim_type.value,
        "subject": claim.subject,
        "condition": claim.condition,
        "entitlement": claim.entitlement,
        "exception": claim.exception,
        "procedure": claim.procedure,
        "effective_from": claim.effective_from,
        "evidence_ids": list(claim.evidence_ids),
        "source_spans": [list(span) for span in claim.source_spans],
        "source_hashes": list(claim.source_hashes),
        "verification": claim.verification,
        "reason": claim.reason,
    }


import re
from collections.abc import Sequence

_FACT_NUMBER = re.compile(r"\d+(?:[./%-]\d+)*", re.IGNORECASE)
_STATUS_POLARITIES = (
    ("hết hiệu lực", "còn hiệu lực"),
    ("không còn hiệu lực", "còn hiệu lực"),
    ("bãi bỏ", "còn hiệu lực"),
    ("thay thế", "còn hiệu lực"),
)
_STATUS_MARKERS = ("còn hiệu lực", "hết hiệu lực", "không còn hiệu lực", "bãi bỏ", "thay thế")


def claim_facts_supported(claim: str, evidence: Sequence[str]) -> bool:
    """Verify that concrete numbers or status claims are explicitly supported in evidence."""
    claim_text = claim.casefold()
    evidence_text = " ".join(evidence).casefold()

    claim_numbers = set(_FACT_NUMBER.findall(claim_text))
    claim_statuses = [marker for marker in _STATUS_MARKERS if marker in claim_text]

    if not claim_numbers and not claim_statuses:
        # No concrete numeric/status assertions in claim
        return False

    if claim_numbers and not claim_numbers.issubset(set(_FACT_NUMBER.findall(evidence_text))):
        return False

    for status in claim_statuses:
        if status not in evidence_text:
            return False

    for positive, negative in _STATUS_POLARITIES:
        if positive in claim_text and negative in evidence_text and positive not in evidence_text:
            return False
        if negative in claim_text and positive in evidence_text and negative not in evidence_text:
            return False

    return True

