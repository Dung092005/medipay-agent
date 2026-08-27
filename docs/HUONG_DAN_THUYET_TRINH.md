# MediPay — Hướng dẫn đọc hiểu dự án (thuyết trình nhóm)

Tài liệu này giúp bạn **hiểu toàn bộ hệ thống** để thuyết trình: vì sao có 3
database, mỗi loại search dùng khi nào, hạ tầng chạy ra sao, và câu trả lời
được tối ưu thế nào. Đọc từ trên xuống một lần; phần 8 là “cheat sheet” slide.

---

## 0. Sản phẩm đang làm gì?

**MediPay Agent** là chatbot trả lời câu hỏi **BHYT / viện phí** dựa trên văn
bản pháp lý đã được đưa vào hệ thống (không phải “ChatGPT bịa luật”).

Nguyên tắc cứng:

> Chỉ trả lời từ **bằng chứng đã kiểm tra** trong corpus. Không có bằng chứng
> thì nói không đủ căn cứ — không bịa Điều/Khoản.

Corpus serving hiện tại (sau cutover):

| Chỉ số | Giá trị |
|---|---|
| Documents | ~318 (Trung ương + địa phương, ưu tiên Hà Nội) |
| Chunks (Postgres) | ~11 546 |
| Vectors (Qdrant) | ~10 112 |
| Quan hệ (Neo4j) | ~173 |
| Release id | `snapshot-5dfc6bb64d046a1c` |

---

## 1. Bức tranh lớn: một câu hỏi đi đâu?

```text
Người dùng hỏi trên web (Next.js)
        │
        ▼
API FastAPI  (/api/v1/chat hoặc /chat/stream)
        │
        ├─ 1. Policy sớm: chào / cảm ơn / bye → trả lời ngắn, KHÔNG search
        ├─ 2. Safety: OTP, kê đơn, hỏi hộ sơ… → từ chối
        │
        ▼
GraphRAG Runtime (src/services/chat.py)
        │
        ├─ Seed: tìm đoạn ứng viên (nhiều kênh search)
        ├─ Expand: mở rộng Điều–Khoản / văn bản liên quan (PageIndex + Neo4j)
        ├─ Re-retrieve: lấy lại đoạn text thật từ DB (không lấy chữ từ cạnh graph)
        ├─ Verify + Rerank: lọc nhiễu, ưu tiên điều khoản “đúng việc”, routing tỉnh
        └─ Generate: LLM viết câu trả lời + citation công khai
        │
        ▼
UI hiện: KẾT LUẬN / ĐIỀU KIỆN / CĂN CỨ + nút “Mở nguồn chính thức”
```

**Ý bạn cần nói trên slide:** chatbot không “nhớ luật trong model”, mà
**truy xuất → chọn đoạn → rồi mới viết**.

---

## 2. Vì sao có 3 database? Mỗi cái làm gì?

Đây là phần hay bị hỏi: “Sao không để hết trong một Postgres?”

| Store | Giữ gì | Vai trò khi trả lời |
|---|---|---|
| **PostgreSQL (Supabase)** | Văn bản chuẩn, chunks, legal units (Điều/Khoản), metadata, release đang active | Nguồn **canonical**: full-text search, hydrate text + citation, PageIndex |
| **Qdrant** | Vector embedding (`text-embedding-3-small`, 1536 chiều) | **Semantic search**: câu hỏi gần nghĩa dù khác chữ |
| **Neo4j** | Node Document + cạnh quan hệ (căn cứ, sửa đổi, bãi bỏ…) | **Graph expand**: từ 1 văn bản nhảy sang văn bản liên quan |

### Ai đọc cái nào lúc runtime?

1. Câu hỏi → (tuỳ kênh) Postgres **và/hoặc** Qdrant tìm **passage/chunk ứng viên**.
2. Có `document_id` seed → Neo4j mở rộng sang doc liên quan (nếu có).
3. Mọi nội dung đưa vào LLM đều **hydrate lại từ Postgres** (text + số hiệu + URL).
4. Neo4j **không** được dùng làm nguồn chữ để trích dẫn — chỉ để mở rộng ứng viên.

Alias ổn định Qdrant app đọc: `medical_legal_active` (trỏ collection vật lý theo release).

---

## 3. Các loại search — dùng khi nào?

Hệ thống **không chỉ semantic**. Có nhiều kênh; sau đó gộp bằng **weighted RRF**
rồi **rerank pháp lý** (policy `hybrid-v11-document-recall-rerank`).

| Kênh | Store | Khi nào hữu ích | Ví dụ câu |
|---|---|---|---|
| **Exact / số hiệu** | Postgres | User nêu đúng số văn bản | “105/2014/NĐ-CP quy định gì?” |
| **Lexical (full-text)** | Postgres `tsvector` | Khớp từ khóa pháp lý rõ | “mức hưởng trái tuyến”, “giấy chuyển tuyến” |
| **Semantic** | Qdrant | Cùng ý nhưng khác wording | “khám không đúng nơi đăng ký được chi trả không?” |
| **Title / document recall** | Postgres | Tìm đúng **văn bản** rồi mới lấy đoạn | Câu dài, dễ lệch chunk |
| **Document operatives** | Postgres | Cứu **điều khoản then chốt** (điểm a/b, mức %, điều kiện) | Câu hỏi thao tác cụ thể |
| **PageIndex (legal_units)** | Postgres | Neo cấu trúc Điều–Khoản, citation đúng span | Cần chỉ đúng Điều X Khoản Y |
| **Graph** | Neo4j | Văn bản A sửa/bãi/căn cứ B | “văn bản này còn hiệu lực / bị thay chưa?” |

### Fusion & tối ưu thứ hạng (phần “thông minh” thật)

1. **Weighted RRF** — gộp điểm nhiều kênh, tránh một kênh độc chiếm.
2. **Rerank pháp lý** — ưu tiên đoạn mang *điều kiện / mức hưởng / thời điểm hiệu lực*, hạ đoạn định nghĩa chung hoặc nhiễu hành chính.
3. **Document recall + operatives** — nếu semantic chỉ lấy được đoạn loãng, hệ thống quay lại đúng document và scan điều khoản then chốt.
4. **Jurisdiction routing (đặc thù nhóm bạn)**  
   - User nêu tỉnh → ưu tiên văn bản tỉnh đó + Trung ương; hạ tỉnh khác.  
   - Không nêu tỉnh → câu quốc gia (trái tuyến, Luật/NĐ) lấy Trung ương trước; chỉ boost Hà Nội khi hỏi hỗ trợ/giá/đăng ký KCB địa phương.
5. **Verify** — bỏ evidence không hydrate được / không đủ tin; thiếu bằng chứng thì abstain.

Trên slide có thể nói: *“Search đa kênh → hợp nhất → rerank theo nghiệp vụ pháp lý → mới gọi LLM.”*

---

## 4. Hạ tầng (infra) cần biết để thuyết trình

### 4.1 Backend

- **FastAPI** (`src/main.py`): REST + SSE stream.
- **LangGraph / agent nodes** (`src/agents/`): orchestration trả lời.
- **Runtime GraphRAG** (`src/services/chat.py`): owner của retrieval + LLM.
- **Config** (`.env` + `src/config.py`): model chat, embedding, URL 3 DB, Qdrant alias.

Chạy local (nhóm demo):

```powershell
cd "Group project"
.\.venv\Scripts\python.exe -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

### 4.2 Frontend

- **Next.js** trong `web/`: chat UI, hiển thị citation, nút nguồn chính thức.
- Gọi API qua `NEXT_PUBLIC_API_URL`.

```powershell
cd web
npm run dev
```

### 4.3 Embedding & release (offline / ops)

Không embed lúc user chat từng chữ của cả corpus. Quy trình:

```text
CSV corpus (data/clean/serving_…)
   → build snapshot (canonical)
   → embed_snapshot.py  (gọi API embedding, ra .npy + passages.jsonl)
   → ingest Postgres (text chuẩn)
   → qdrant_release.py --activate  (upload vector + bật alias)
   → import Neo4j (cùng dataset_id)
   → align_release_registry (3 projection = ready)
```

**Release** = một `dataset_id` thống nhất trên cả 3 store. App chỉ đọc release
**active**. Cutover mới = clean cũ (free-tier) → nạp mới → trỏ alias/registry.

### 4.4 Auth / quan sát (biết là có, demo có thể bypass)

- Firebase: scaffold đăng nhập (dev có thể bypass).
- Langfuse / metrics: trace cuộc gọi LLM (nếu bật).
- Eval/RAGAS: thư mục `eval/` — đo chất lượng; có thể nói “định hướng tiếp theo”.

---

## 5. Tối ưu câu trả lời (ngoài search)

| Lớp | Việc làm | File gợi ý |
|---|---|---|
| Policy xã giao | `xin chào` / cảm ơn / bye → không RAG | `src/services/retrieval.py` |
| Prompt hệ thống | Mở đầu bằng **kết luận**, có Điều kiện / Căn cứ | `src/agents/prompts.py` |
| Citation công khai | UI chỉ thấy title, số hiệu, quote, `source_url` | `src/api/public_contract.py` |
| Nguồn chính thức | Link VBPL / official URL khi có | ranking metadata + UI |
| Lọc nhiễu | Bỏ đoạn hành chính không liên quan BHYT | `filter_relevant_evidence` |
| Query rewrite | Chuẩn hóa / paraphrase nhẹ trước retrieve | `rewrite_user_query`, `query_rewrite.py` |

**Điểm thuyết trình:** “Chất lượng câu trả lời = data sạch + retrieval đúng +
prompt kỷ luật + citation kiểm chứng được.”

---

## 6. Corpus & metadata (data layer)

Corpus nằm dưới `data/clean/` (serving hiện tại: thư mục serving full + Hà Nội).

Các file authority điển hình:

- `metadata.csv` — số hiệu, ngày, `pham_vi`, `province`, `answer_ready`…
- `content.csv` — HTML/nội dung
- `documents.csv` — gộp phục vụ pipeline
- `relationships.csv` — quan hệ đưa vào Neo4j
- `summary.json` — thống kê build

`answer_ready=true` nghĩa là document đủ điều kiện đưa vào trả lời. Filter này
tránh “có vector nhưng không nên dùng”.

Builder/scripts liên quan: `scripts/build_serving_p151_hanoi.py`,
`scripts/build_hanoi_bhyt_module.py`, pipeline trong `database/pipeline/`.

---

## 7. Ai thường làm phần nào? (gợi ý chia slide Team)

Không cần nhớ từng commit; chia theo **lớp** để thuyết trình:

| Lớp | Việc | Nơi nhìn trong repo |
|---|---|---|
| **Data / corpus** | Thu thập VBPL, CSV, Hà Nội module, audit | `data/`, `scripts/`, `docs/PRESENTATION_BHYT_CORPUS.md` |
| **Database & release** | Schema Postgres, Qdrant projection, Neo4j import, cutover | `database/` |
| **Retrieval & LLM** | Đa kênh search, RRF, rerank, GraphRAG, prompt | `src/services/`, `src/agents/` |
| **API & product** | FastAPI routes, stream, citation contract | `src/api/`, `src/main.py` |
| **Frontend** | Chat UI, nguồn chính thức | `web/` |
| **Ops** | Runbook deploy, monitoring | `ops/` |

Khi bị hỏi “bạn làm gì / bạn mình làm gì”: trả lời theo **lớp**, không theo
tên thư mục lạ.

---

## 8. Cheat sheet 60 giây (nhớ thuộc)

1. **3 DB:** Postgres = chữ chuẩn + FTS; Qdrant = gần nghĩa; Neo4j = quan hệ văn bản.  
2. **Luồng:** Policy → Seed đa kênh → Expand → Verify/Rerank → Generate.  
3. **Không bịa:** không evidence thì không kết luận pháp lý.  
4. **Routing tỉnh:** có nêu tỉnh thì tỉnh đó + Trung ương; mặc định quốc gia thì Trung ương trước.  
5. **Release:** một `dataset_id` khớp cả 3 store; alias `medical_legal_active`.  
6. **Demo câu:**  
   - `xin chào` → xã giao  
   - trái tuyến / mức hưởng → Trung ương  
   - hỗ trợ đăng ký KCB Hà Nội → ưu tiên Hà Nội  

---

## 9. Map file “đọc khi cần”

| Muốn hiểu… | Đọc |
|---|---|
| Toàn hệ thống (bản dài) | `ARCHITECTURE.md` |
| Corpus BHYT / Hà Nội (slide data) | `docs/PRESENTATION_BHYT_CORPUS.md`, `docs/SERVING_CORPUS.md` |
| Gắn Qdrant | `docs/QDRANT_SETUP.md` |
| Retrieval runtime | `src/services/chat.py`, `src/services/retrieval.py` |
| Truy vấn DB | `src/db/repositories.py` |
| Prompt | `src/agents/prompts.py` |
| Pitch deck | `presentation/` |

---

## 10. Cách trả lời giám khảo (mẫu)

**“Sao không dùng mỗi ChatGPT?”**  
Vì pháp lý cần căn cứ kiểm chứng; model chỉ viết câu, bằng chứng lấy từ DB.

**“Semantic có đủ không?”**  
Không. Số hiệu và điều khoản then chốt cần lexical/exact/operatives; graph xử lý quan hệ văn bản.

**“Làm sao biết đúng tỉnh?”**  
Metadata `pham_vi` / `province` + bước boost/demote trước khi xếp hạng cuối.

**“Deploy thế nào?”**  
Build corpus → embed → ingest 3 store cùng release → bật alias → API/web trỏ env.

---

*Tài liệu phục vụ thuyết trình nhóm MediPay. Cập nhật theo release
`snapshot-5dfc6bb64d046a1c`.*
