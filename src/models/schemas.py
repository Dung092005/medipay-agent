from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatHistoryMessage(ApiModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=5000)


class ChatRequest(ApiModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Câu hỏi của người dùng về BHYT hoặc viện phí.",
        examples=["Quyền lợi BHYT khi khám trái tuyến là gì?"],
    )
    chat_history: list[ChatHistoryMessage] = Field(
        default_factory=list,
        description="Lịch sử hội thoại từ frontend. Hiện chưa đưa vào GraphRAG.",
    )

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
    document_id: str = Field(..., description="ID tài liệu trong dataset đang active.")
    chunk_id: str = Field(..., description="ID đoạn evidence được trích dẫn.")
    title: str = Field(default="", description="Tên văn bản nguồn.")
    section_title: str = Field(default="", description="Tiêu đề điều/mục nếu có.")
    quote: str = Field(default="", description="Đoạn evidence dùng để trả lời.")
    channels: list[str] = Field(default_factory=list, description="Kênh retrieval tạo ra evidence.")


class ChatResponse(ApiModel):
    response: str = Field(..., description="Câu trả lời grounded từ evidence và graph relations.")
    citations: list[ChatCitation] = Field(
        default_factory=list,
        description="Nguồn evidence đã được kiểm tra provenance.",
    )


class AnalyzeResponse(ApiModel):
    analysis: str = Field(..., description="Kết quả phân tích input.")


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
    neo4j: bool
    llm: bool
    embedding: bool
    details: dict[str, Any] = Field(default_factory=dict)
