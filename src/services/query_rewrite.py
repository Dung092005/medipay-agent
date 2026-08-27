"""Constrained hypothetical-document rewriting for retrieval only."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from src.integrations.langfuse import llm_invoke_config
from src.services.llm import get_rewrite_llm
from src.services.retrieval import (
    extract_document_numbers,
    policy_response,
    retrieval_intent,
)

_NUMBER_TOKEN = re.compile(r"\d+(?:[.,/%-]\d+)*")

_REWRITE_INSTRUCTION = """Bạn chuyển câu hỏi pháp luật BHYT/viện phí thành đúng một đoạn
giả định ngắn giống câu chữ của điều khoản pháp luật có khả năng trả lời câu hỏi.
Đoạn này là HyDE dùng nội bộ để tìm kiếm, không phải câu trả lời cho người dùng
và không được hiển thị ra ngoài.

Yêu cầu:
- Giữ nguyên chủ thể, điều kiện, thời điểm, số tiền và số hiệu đã có trong câu hỏi.
- Có thể mở rộng từ viết tắt và dùng thuật ngữ pháp lý tương đương.
- Viết thành mệnh đề quy phạm có cả hoàn cảnh và hệ quả pháp lý cần tìm
  (quyền, nghĩa vụ, điều kiện hoặc ngoại lệ), thay vì chỉ đảo thứ tự từ của
  câu hỏi. Có thể thêm thuật ngữ pháp lý không định lượng có khả năng xuất
  hiện trong điều khoản trả lời (điều kiện, ngoại lệ, phạm vi, mức hưởng,
  thanh toán, cơ sở khám chữa bệnh) khi giúp làm rõ hệ quả cần tra cứu.
  Đây chỉ là giả thuyết tìm kiếm, không phải kết luận thực tế.
- Không được tự thêm con số, tỷ lệ phần trăm, số tiền, tên văn bản, tên cơ
  quan, địa phương hoặc khẳng định một mức/quyền lợi cụ thể.
- Không thêm số hiệu văn bản, tên cơ quan hoặc địa phương không có trong câu hỏi.
- Không viết giải thích, trích dẫn, tiêu đề hay lời dẫn.
- Trả đúng schema đã yêu cầu."""


class RetrievalRewrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=3, max_length=900)


def should_rewrite_query(query: str) -> bool:
    """Route only open-ended legal questions through the extra model call."""
    normalized = " ".join(query.split())
    # Temporal legal questions are exactly where a user's colloquial wording
    # (for example, “trái tuyến” or “hiện hành”) differs most from the
    # operative clause. The original query still runs concurrently, while the
    # rewrite supplies a precise second retrieval view. Exact document lookups
    # remain untouched because their identifier is already the best anchor.
    return bool(
        len(normalized.split()) >= 4
        and policy_response(normalized) is None
        and retrieval_intent(normalized) in {"thematic", "temporal", "relational"}
        and not extract_document_numbers(normalized)
    )


async def rewrite_retrieval_query(query: str) -> str:
    """Produce a bounded HyDE query, rejecting newly invented document IDs."""
    normalized = " ".join(query.split())
    if not normalized:
        return ""
    structured = get_rewrite_llm().with_structured_output(
        RetrievalRewrite,
        method="json_schema",
    )
    result = await structured.ainvoke(
        [
            ("system", _REWRITE_INSTRUCTION),
            ("human", normalized),
        ],
        config=llm_invoke_config(),
    )
    rewritten = " ".join(result.query.split())[:900]
    original_document_numbers = set(extract_document_numbers(normalized))
    rewritten_document_numbers = set(extract_document_numbers(rewritten))
    original_fact_numbers = set(_NUMBER_TOKEN.findall(normalized))
    rewritten_fact_numbers = set(_NUMBER_TOKEN.findall(rewritten))
    if (
        rewritten_document_numbers - original_document_numbers
        or rewritten_fact_numbers - original_fact_numbers
    ):
        return normalized
    return rewritten or normalized
