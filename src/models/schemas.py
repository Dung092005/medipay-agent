from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(ApiModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Câu hỏi của người dùng về BHYT hoặc viện phí.",
        examples=["Quyền lợi BHYT khi khám trái tuyến là gì?"],
    )
    conversation_id: str = Field(default="", max_length=128)
    turn_id: str = Field(default="", max_length=128)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message must not be blank")
        return normalized

class AnalyzeRequest(ApiModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Nội dung cần phân tích.",
        examples=["Hóa đơn có khoản thu nào cần kiểm tra?"],
    )

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message must not be blank")
        return normalized


class ChatCitation(ApiModel):
    title: str = Field(default="", description="Tên văn bản nguồn.")
    document_number: str = Field(default="", description="Số hiệu văn bản công khai nếu có.")
    section_title: str = Field(default="", description="Tiêu đề điều/mục nếu có.")
    quote: str = Field(default="", description="Đoạn trích công khai dùng để trả lời.")
    source_url: str = Field(default="", description="URL nguồn chính thức nếu có.")
    source_checked_at: str = Field(default="", description="Thời điểm kiểm tra provenance.")


class ChatResponse(ApiModel):
    response: str = Field(..., description="Câu trả lời grounded từ evidence và graph relations.")
    citations: list[ChatCitation] = Field(
        default_factory=list,
        description="Nguồn evidence đã được kiểm tra provenance.",
    )
    claims: list[AnswerClaim] = Field(
        default_factory=list,
        description="Audit claim → citation mapping; unsupported high-risk claims are downgraded.",
    )


class AnswerClaim(ApiModel):
    claim_id: str
    text: str
    claim_type: Literal[
        "document", "status", "entitlement", "condition", "procedure", "exception", "general"
    ] = "general"
    subject: str = ""
    condition: str = ""
    entitlement: str = ""
    exception: str = ""
    procedure: str = ""
    effective_from: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    source_spans: list[list[int | None]] = Field(default_factory=list)
    source_hashes: list[str] = Field(default_factory=list)
    verification: Literal["entailed", "partial", "unsupported"]
    reason: str = ""


class AnalyzeResponse(ApiModel):
    analysis: str = Field(..., description="Kết quả phân tích input.")


class ConversationSummary(ApiModel):
    conversation_id: str
    title: str = ""
    active_dataset_id: str = ""
    updated_at: datetime


class ConversationTurn(ApiModel):
    turn_id: str
    user_message: str
    assistant_response: str
    dataset_id: str = ""
    citations: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class ReviewQueueItem(ApiModel):
    review_id: str
    domain: Literal["legal_document", "hospital_fee_ocr"]
    source_id: str = ""
    title: str = ""
    status: Literal["pending", "accepted", "rejected"]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    submitted_by: str = ""
    assigned_to: str = ""
    decision_note: str = ""
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None = None
    audit: list[dict[str, Any]] = Field(default_factory=list)


class ReviewDecisionRequest(ApiModel):
    status: Literal["accepted", "rejected"]
    note: str = Field(default="", max_length=2000)


class AgentStatusResponse(ApiModel):
    status: str = Field(..., description="Trạng thái agent.")
    agent: str = Field(..., description="Tên và phiên bản agent.")


class ErrorResponse(ApiModel):
    code: str = Field(..., description="Mã lỗi ổn định cho client.")
    message: str = Field(..., description="Thông báo lỗi an toàn cho người dùng.")
    request_id: str = Field(..., description="ID dùng để tra log server-side.")


class ReadinessResponse(ApiModel):
    status: str
    database: bool
    qdrant: bool
    neo4j: bool
    llm: bool
    embedding: bool
    details: dict[str, Any] = Field(default_factory=dict)
