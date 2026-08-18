"""Stable request and response contracts for the read-only data API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiModel(BaseModel):
    """Reject unknown fields so clients notice contract mistakes early."""

    model_config = ConfigDict(extra="forbid")


class Category(StrEnum):
    BHYT = "bhyt"
    VIEN_PHI = "vien_phi"


class RelationshipDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BOTH = "both"


class SearchRequest(ApiModel):
    query: str = Field(min_length=1, max_length=2_000)
    category: Category | None = None
    status: str | None = Field(default=None, max_length=120)
    limit: int = Field(default=8, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class DatasetInfo(ApiModel):
    dataset_id: str
    dataset_version: str
    published_at: datetime | None = None
    pipeline_version: str | None = None
    source_as_of_date: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    counts: dict[str, int] = Field(default_factory=dict)


class SearchHit(ApiModel):
    chunk_id: str
    document_id: str
    score: float
    section_title: str = ""
    text: str
    title: str = ""
    so_ky_hieu: str = ""
    status: str = ""
    node_kind: Literal["canonical", "external"] = "canonical"
    citation: dict[str, str] = Field(default_factory=dict)
    unit_id: str | None = None
    source_start: int | None = None
    source_end: int | None = None


class SearchResponse(ApiModel):
    dataset_version: str
    hits: list[SearchHit]


class RetrieveRequest(ApiModel):
    query: str = Field(min_length=1, max_length=2_000)
    category: Category | None = None
    status: str | None = Field(default=None, max_length=120)
    reference_date: str | None = Field(default=None, max_length=32)
    jurisdiction: str | None = Field(default=None, max_length=240)
    limit: int = Field(default=8, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


class RetrieveHit(ApiModel):
    evidence_id: str
    document_id: str
    passage_id: str | None = None
    unit_id: str | None = None
    text: str = ""
    score: float
    channel: str
    citation: dict[str, str] = Field(default_factory=dict)


class RetrieveResponse(ApiModel):
    dataset_version: str
    query_plan: dict[str, Any]
    hits: list[RetrieveHit]
    warnings: list[str] = Field(default_factory=list)


class DocumentResponse(ApiModel):
    dataset_version: str
    id: str
    title: str = ""
    so_ky_hieu: str = ""
    node_kind: Literal["canonical", "external"]
    resolution_status: str = "canonical"
    categories: list[str] = Field(default_factory=list)
    ngay_ban_hanh: str | None = None
    ngay_co_hieu_luc: str | None = None
    ngay_het_hieu_luc: str | None = None
    tinh_trang_hieu_luc: str = ""
    status_filter: str = ""
    pham_vi: str = ""
    linh_vuc: str = ""
    co_quan_ban_hanh: str = ""
    content_available: bool = False
    content_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LegalUnitResponse(ApiModel):
    dataset_version: str
    unit_id: str
    document_id: str
    parent_unit_id: str | None = None
    unit_type: str
    ordinal_raw: str = ""
    label: str = ""
    heading: str = ""
    text: str
    source_start: int | None = None
    source_end: int | None = None
    text_sha256: str = ""
    parser_version: str = ""


class TableCellResponse(ApiModel):
    row_index: int
    column_index: int
    header: str = ""
    row_header: str = ""
    value: str = ""
    cell_tag: str = "td"
    colspan: int = 1
    rowspan: int = 1


class TableResponse(ApiModel):
    dataset_version: str
    table_id: str
    document_id: str
    table_ordinal: int
    source_selector: str
    source_fragment_sha256: str
    table_text_sha256: str
    row_count: int
    column_count: int
    extraction_version: str
    cells: list[TableCellResponse] = Field(default_factory=list)


class RelationshipItem(ApiModel):
    edge_key: str
    source_id: str
    target_id: str
    relationship_type: str
    relationship_is_adverse: bool = False
    source_title: str = ""
    target_title: str = ""


class RelationshipResponse(ApiModel):
    dataset_version: str
    document_id: str
    direction: RelationshipDirection
    relationships: list[RelationshipItem]


class StatsResponse(ApiModel):
    dataset_version: str
    canonical_nodes: int
    external_nodes: int
    content_rows: int
    available_content: int
    category_rows: int
    relationship_rows: int
    adverse_edges: int
    chunk_rows: int


class HealthResponse(ApiModel):
    status: Literal["ok", "not_ready"]
    dataset_version: str | None = None


class ErrorResponse(ApiModel):
    code: str
    message: str
    request_id: str
