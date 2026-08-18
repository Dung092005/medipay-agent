# 🏥 MediPay Agent - AI Agent Trợ Lý Hành Chính Y Tế (Bảo Hiểm & Thanh Toán Viện Phí)

> **MediPay Agent** là hệ thống AI Agent thông minh hỗ trợ giải đáp tự động các thắc mắc về Bảo hiểm Y tế (BHYT), bóc tách & giải thích bảng kê chi phí viện phí từ hóa đơn/ảnh chụp, và hướng dẫn quy trình thanh toán cho bệnh nhân tại bệnh viện.

---

## 👤 Tác giả

**Nguyễn Tiến Dũng**

Phát triển toàn bộ hệ thống: backend FastAPI, frontend Next.js, RAG pipeline, LangGraph Agent, hạ tầng Docker và CI/CD.

---

## 🛠️ Tech Stack & Kiến Trúc Hệ Thống

* **Frontend:** Next.js 14, Tailwind CSS, TypeScript (Triển khai trên **Vercel**)
* **Backend:** Python 3.11, FastAPI, Pydantic, Uvicorn (Đóng gói **Docker Container**)
* **Database & Vector Search:** PostgreSQL với extension `pgvector`
* **AI & Agentic Framework:**
  * **LangChain & LangGraph:** Điều phối luồng Agent, định tuyến ý định (Intent Routing) & State Management.
  * **Langfuse:** Giám sát chất lượng phản hồi AI, theo dõi độ trễ (Latency) & quản lý chi phí token.
* **DevOps & Infrastructure:** Docker Multi-stage, GitHub Actions CI/CD.

### Cấu trúc dự án hiện tại và hướng phát triển

Backend và GraphRAG nằm trong `src`. Frontend Next.js sẽ đặt tại `web/`. Supabase quản lý PostgreSQL + `pgvector` cho document/chunk; Neo4j quản lý knowledge graph; Firebase được chuẩn bị cho đăng nhập. Embedding dùng `text-embedding-3-small` (1536 chiều).

```text
.
├── src/                              # FastAPI backend và application logic
│   ├── main.py                       # FastAPI app, lifespan, CORS
│   ├── config.py                     # Supabase DB + GraphRAG settings
│   ├── api/                          # REST routes và dependencies
│   ├── agents/                       # LangGraph state, graph, nodes, tools
│   ├── db/                           # SQLAlchemy session, models, repositories
│   ├── graph_rag/                    # chunking, extraction, retrieval, ingestion
│   ├── integrations/                 # LLM/embedding interfaces, telemetry
│   ├── models/                       # API và graph schemas
│   └── services/                     # chat và GraphRAG use cases
├── web/                              # Next.js frontend (giai đoạn tiếp theo)
│   ├── app/                          # App Router pages/layout
│   ├── components/                   # Chat/document/shared UI
│   └── lib/                          # Typed API client, env helpers
├── database/                         # PostgreSQL, pipeline, Neo4j và Firebase
│   ├── neo4j/                         # Knowledge graph và importer
│   └── firebase/                      # Firebase Authentication scaffold
├── docker-compose.yml                # Chạy backend, kết nối Supabase
├── Dockerfile                        # FastAPI image
├── requirements.txt                  # Python dependencies
└── ARCHITECTURE.md                   # Chi tiết kiến trúc
```

Xem [ARCHITECTURE.md](ARCHITECTURE.md) để biết GraphRAG flow, data model và ranh giới module.

---

## API và Swagger

Chạy backend ở thư mục gốc:

```bash
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

FastAPI tự cung cấp giao diện Swagger tại [http://localhost:8000/docs](http://localhost:8000/docs)

| Method | Path | Mô tả |
| :--- | :--- | :--- |
| `GET` | `/health` | Kiểm tra process API đang chạy. |
| `GET` | `/ready` | Kiểm tra API và kết nối database. |
| `GET` | `/api/v1/status` | Kiểm tra trạng thái LangGraph agent. |
| `POST` | `/api/v1/chat` | Gửi câu hỏi và nhận phản hồi agent. |
| `POST` | `/api/v1/analyze` | Phân tích nội dung mà không trả conversational response. |

`/api/v1/chat` và `/api/v1/analyze` nhận JSON với `message` dài từ 1 đến 5000 ký tự:

```json
{
  "message": "Quyền lợi BHYT khi khám trái tuyến là gì?"
}
```

Ví dụ response chat:

```json
{
  "response": "...",
  "analysis": "..."
}
```

Ví dụ response analyze:

```json
{
  "analysis": "..."
}
```

Chạy kiểm tra:

```bash
ruff check src/ tests/
pytest tests/ -v --tb=short
```

---
