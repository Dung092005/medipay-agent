# MediPay BHYT GraphRAG Agent - Tài liệu Kiến Trúc & Trạng Thái Hệ Thống

Tài liệu này tổng hợp toàn bộ thông tin chi tiết về dự án **MediPay Agent (BHYT & Viện phí)**: cấu trúc hệ thống, cấu hình môi trường, luồng xử lý truy vấn GraphRAG, các lỗi đã giải quyết và kết quả kiểm thử.

---

## 1. Thông Tin Môi Trường & Dịch Vụ

| Dịch vụ | URL / Địa chỉ | Chức năng |
| :--- | :--- | :--- |
| **Frontend Web** | `https://medipay-ai.vercel.app` | Next.js App Router (UI Chat, Streaming, Quản trị kiểm duyệt) |
| **Backend API** | `https://medipay-api-1u5t.onrender.com` | FastAPI (GraphRAG Agent, SSE Stream, REST Endpoints) |
| **GitHub Repo** | `https://github.com/Dung092005/medipay-agent.git` | Branch chính: `main` |
| **Langfuse Tracing**| `https://us.cloud.langfuse.com` | Giám sát trace từng node của LangGraph & LLM calls |
| **Vector DB** | Qdrant Cloud Cluster | 10,112 vector (1536 dims) - Collection `medical_legal_active` |
| **Relational DB**| Supabase PostgreSQL | Chứa các bảng `documents`, `chunks`, `legal_units`, `review_queue` |

---

## 2. Danh Sách Biến Môi Trường (Cấu hình trên Render)

### 2.1. LLM Generation (Google Cloud Vertex AI)
* **`LLM_PROVIDER`**: `google`
* **`MODEL_NAME`**: `gemini-3.1-flash-lite`
* **`GOOGLE_PROJECT_ID`**: `project-3b0c96e7-a43e-4f65-8bd`
* **`GOOGLE_LOCATION`**: `global`
* **`GOOGLE_CREDENTIALS_JSON`**: *(Chuỗi JSON chứa `type: authorized_user`, `client_id`, `client_secret`, `refresh_token`)*

### 2.2. Embeddings (OpenRouter)
* **`OPENAI_BASE_URL`**: `https://openrouter.ai/api/v1`
* **`OPENAI_API_KEY`**: *(Khóa OpenRouter API key `sk-or-v1-...`)*
* **`EMBEDDING_API_KEY`**: *(Khóa OpenRouter API key `sk-or-v1-...`)*
* **`EMBEDDING_MODEL`**: `openai/text-embedding-3-small`
* **`EMBEDDING_DIMENSIONS`**: `1536`

### 2.3. Cơ Sở Dữ Liệu & Monitoring
* **`DATABASE_URL`**: *(Supabase PostgreSQL connection string)*
* **`QDRANT_URL`**: *(Qdrant Cloud endpoint)*
* **`QDRANT_API_KEY`**: *(Qdrant access key)*
* **`LANGFUSE_PUBLIC_KEY`**: `pk-lf-ada49797-8837-4c2a-a00d-eba9dca499ca`
* **`LANGFUSE_SECRET_KEY`**: `sk-lf-673e4453-65e3-47cb-9257-89df51564756`
* **`LANGFUSE_BASE_URL`**: `https://us.cloud.langfuse.com`

---

## 3. Kiến Trúc Luồng Xử Lý GraphRAG (Pipeline Flow)

1. **Intake & Classification**: Phân loại intent câu hỏi (thực thể, mức hưởng, thủ tục, thời gian).
2. **Multi-Channel Retrieval**:
   - Dense Vector Search trên Qdrant (1536 dimensions qua OpenRouter).
   - Lexical Full-Text Search trên PostgreSQL.
   - Title Documents Search (`search_title_documents`) để lấy các luật gốc (`Luật BHYT 51/2024`, `Nghị định 146`).
   - Operative Expansion (`search_document_operatives`) tìm kiếm các điều khoản trọng tâm (Điều 22, Điều 12,...).
3. **Legal Hierarchy Reranking**: Xếp hạng ưu tiên theo thứ bậc hiệu lực pháp luật (`Luật` > `Nghị định` > `Thông tư` > `Địa phương`).
4. **Context Assembly**: Đóng gói các điều khoản nguồn và quan hệ pháp lý.
5. **Generation**: LLM Google Vertex AI (`gemini-3.1-flash-lite`) sinh câu trả lời trực tiếp, mạch lạc kèm trích dẫn văn bản quy phạm.
6. **Guardrail & Provenance Audit**: Kiểm chứng tính xác thực của câu trả lời dựa trên tài liệu gốc trước khi stream về người dùng qua SSE.

---

## 4. Các Vấn Đề Đã Được Khắc Phục

1. **Khắc phục xác thực Vertex AI Cloud:**
   - Tạo phương thức `ChatVertexGemini` nhận thông tin từ `GOOGLE_CREDENTIALS_JSON` dạng `authorized_user` để tự động refresh token OAuth2 với Google Cloud.
2. **Khắc phục lỗi Embedding Proxy:**
   - Tự động định tuyến `OPENAI_BASE_URL` sang `https://openrouter.ai/api/v1` với model `openai/text-embedding-3-small`.
   - Thêm cơ chế retry tự động (3 lần) với `httpx.AsyncClient` để chống rớt kết nối (`APIConnectionError`).
3. **Ưu tiên văn bản pháp luật cấp Quốc gia (`Luật`, `Nghị định`):**
   - Trước đây các quyết định cấp tỉnh (Ninh Thuận, Quảng Ngãi) do lặp nhiều từ khóa nên bị đẩy điểm cao hơn Luật BHYT.
   - Đã cài đặt thuật toán xếp hạng ưu tiên thứ bậc thẩm quyền: `Luật` (0.40) > `Nghị định` (0.30) > `Văn bản hợp nhất` (0.25) > `Thông tư` (0.15) > `Địa phương` (-0.60 nếu câu hỏi không hỏi địa phương đó).
4. **Cài đặt `search_title_documents`:**
   - Bổ sung hàm tìm kiếm tiêu đề văn bản vào `GraphRepository` để truy xuất ngay các Luật BHYT, Nghị định 146 vào danh sách ứng viên trọng tâm.
5. **Khắc phục bộ lọc Guardrail:**
   - Nới lỏng kiểm tra hash cứng nhắc, bảo toàn toàn bộ câu trả lời phân tích chuyên sâu của Gemini cùng trích dẫn điều khoản luật.
6. **Sửa bộ đọc SSE Stream trên Next.js (`api.ts`):**
   - Đảm bảo nhận diện chính xác các frame SSE `status`, `generate`, `final`, `done` mà không bị ném lỗi format.

---

## 5. Hướng Dẫn Kiểm Thử Nhanh

Chạy lệnh kiểm thử độc lập trên máy:
```bash
python -c "
import asyncio
import sys
from dotenv import load_dotenv
load_dotenv('.env', override=True)

from src.application.answer import StreamLegalQuestion
from src.application.adapters import LangGraphAgentAdapter
from src.agents.graph import get_agent

async def test():
    query = 'Người có thẻ BHYT tự đi khám bệnh tại bệnh viện tuyến tỉnh và bệnh viện tuyến trung ương không có giấy chuyển tuyến thì được quỹ BHYT thanh toán chi phí điều trị ngoại trú và nội trú theo tỷ lệ bao nhiêu?'
    use_case = StreamLegalQuestion(LangGraphAgentAdapter(get_agent))
    stream = use_case.execute(query)
    async for event in stream:
        if event.get('event') == 'on_chain_end' and event.get('name') == 'guardrail':
            out = event.get('data', {}).get('output', {})
            print('ANSWER:\n', out.get('response', ''))
            print('CITATIONS:', len(out.get('citations', [])))

asyncio.run(test())
"
```
