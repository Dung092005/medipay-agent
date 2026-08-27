# MediPay Agent

Trợ lý AI tra cứu **BHYT / viện phí** trên corpus văn bản pháp lý đã kiểm chứng.
Trả lời theo luồng GraphRAG: tìm bằng chứng → xếp hạng → mới sinh câu trả lời.

## Chạy nhanh (local)

```powershell
# API
.\.venv\Scripts\python.exe -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

# Web
cd web
npm run dev
```

## Cấu trúc chính

```text
src/            FastAPI + GraphRAG + agents
web/            Next.js chat UI
database/       Postgres / Qdrant / Neo4j / pipeline / release tools
data/clean/     Corpus serving (CSV)
docs/           Hướng dẫn thuyết trình + corpus + Qdrant
presentation/   Pitch deck / demo
```

## Đọc trước khi thuyết trình

1. **`docs/HUONG_DAN_THUYET_TRINH.md`** — hiểu 3 DB, loại search, infra, tối ưu câu trả lời  
2. `docs/SERVING_CORPUS.md` — số liệu release đang chạy  
3. `ARCHITECTURE.md` — bản kiến trúc đầy đủ (tham chiếu kỹ thuật)

## Release hiện tại

- Dataset: `snapshot-5dfc6bb64d046a1c`
- ~318 documents · Qdrant alias `medical_legal_active`
