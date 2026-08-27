from __future__ import annotations

from pydantic import BaseModel, Field


class Entity(BaseModel):
    name: str
    entity_type: str = "unknown"
    description: str = ""


class DocumentCandidate(BaseModel):
    """Metadata-only hit used by the deterministic document lookup path."""

    document_id: str
    title: str
    so_ky_hieu: str = ""
    ngay_ban_hanh: str = ""
    ngay_co_hieu_luc: str = ""
    ngay_het_hieu_luc: str = ""
    legal_status: str = ""
    legal_status_verified: bool = False
    legal_status_source: str = ""
    legal_status_checked_at: str = ""
    categories: list[str] = Field(default_factory=list)
    answer_ready: bool = False


class Relation(BaseModel):
    source: str
    target: str
    relation_type: str
    description: str = ""
    source_id: str = ""
    target_id: str = ""
    relationship_id: str = ""
    adverse: bool = False
    direction: str = ""


class RetrievalResult(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    dataset_id: str = ""
    source: str = ""
    title: str = ""
    document_number: str = ""
    document_type: str = ""
    issued_date: str = ""
    effective_from: str = ""
    effective_to: str = ""
    legal_status: str = ""
    legal_status_verified: bool = False
    issuer: str = ""
    jurisdiction: str = ""
    province: str = ""
    source_url: str = ""
    source_checked_at: str = ""
    categories: list[str] = Field(default_factory=list)
    section_title: str = ""
    score: float = 0.0
    unit_id: str = ""
    source_start: int | None = None
    source_end: int | None = None
    text_sha256: str = ""
    input_sha256: str = ""
    rank_details: dict[str, float] = Field(default_factory=dict)
    entities: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    document_id: str
    chunk_id: str
    dataset_id: str = ""
    title: str = ""
    document_number: str = ""
    section_title: str = ""
    quote: str = ""
    channels: list[str] = Field(default_factory=list)
    evidence_kind: str = "passage"
    source_start: int | None = None
    source_end: int | None = None
    text_sha256: str = ""
    provenance_verified: bool = False
    source_url: str = ""
    source_checked_at: str = ""
