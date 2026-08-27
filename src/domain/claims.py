from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ClaimType(StrEnum):
    DOCUMENT = "document"
    STATUS = "status"
    ENTITLEMENT = "entitlement"
    CONDITION = "condition"
    PROCEDURE = "procedure"
    EXCEPTION = "exception"
    GENERAL = "general"


@dataclass(frozen=True)
class LegalClaim:
    """Typed, citation-linked claim shape proposed by the answer auditor."""

    claim_id: str
    text: str
    claim_type: ClaimType = ClaimType.GENERAL
    subject: str = ""
    condition: str = ""
    entitlement: str = ""
    exception: str = ""
    procedure: str = ""
    effective_from: str = ""
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    source_spans: tuple[tuple[int | None, int | None], ...] = field(default_factory=tuple)
    source_hashes: tuple[str, ...] = field(default_factory=tuple)
    verification: str = "unsupported"
    reason: str = ""
