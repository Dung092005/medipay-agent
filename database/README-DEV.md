# Database workspace — Developer guide

## Mục đích

`database/` chứa toàn bộ lớp dữ liệu của dự án:

```text
database/
├── schema.sql                 # Supabase PostgreSQL + pgvector
├── pipeline/                  # build snapshot, ingest, embedding, retrieval API
├── neo4j/                     # local Docker, Aura importer và graph docs
└── firebase/                  # Firebase Auth scaffold cho frontend
```

Ranh giới dữ liệu:

- Supabase/PostgreSQL: documents, raw HTML, legal units, tables, chunks,
  facets, full-text search và vector `text-embedding-3-small` 1536 chiều.
- Neo4j: document nodes và directed relationships từ
  `data/raw/relationships.csv`.
- Firebase: client configuration cho đăng nhập; không lưu service-account key
  trong repository.

## Bốn lớp của GraphRAG

| Lớp | Dữ liệu | Vai trò khi truy vấn |
|---|---|---|
| PageIndex index | `legal_units` trong Supabase; artifacts ở `data/clean/page_index/` | Cấu trúc Điều/Khoản, parent, thứ tự và source span cho citation |
| Lexical | `chunks.search_vector`, GIN index | Tìm chính xác số hiệu, tên văn bản, Điều/Khoản và từ khóa |
| Semantic | `chunks.embedding`, pgvector HNSW | Tìm các đoạn gần nghĩa bằng `text-embedding-3-small` |
| Graph | Neo4j Aura/local | Mở rộng document liên quan theo predicate có hướng |

PageIndex là index cấu trúc/provenance trong Supabase, không phải database tách
riêng hay ranking channel độc lập. Về vật lý, `legal_units` đang nằm trong
Supabase cùng lexical/vector;
nếu tách thành service riêng thì vẫn phải giữ `dataset_id`, `unit_id` và source
spans. Lexical và semantic tạo candidates; graph mở rộng từ document seed.
Hợp nhất bằng weighted RRF, sau đó rerank có giới hạn diversity theo
document/unit; graph không bao giờ là nguồn text để trích dẫn.

### Luồng truy vấn chuẩn

```text
question
  ├─ exact: số hiệu/tên văn bản
  ├─ lexical: PostgreSQL plainto_tsquery + ts_rank_cd
  ├─ semantic: OpenAI query embedding → pgvector cosine
  └─ graph: seed document IDs → Neo4j bounded expansion
          ↓
candidate union → weighted RRF → deterministic rerank/diversity
→ hydrate chunks + PageIndex provenance → answer + citations
```

Mỗi evidence phải giữ `dataset_version`, `document_id`, `chunk_id`/`passage_id`,
`unit_id`, `source_start`, `source_end` và channel. Không trả lời chỉ từ graph
label; nội dung chứng cứ phải lấy từ document/chunk trong Supabase.

API pipeline đã có exact, lexical, semantic và RRF trong
`database/pipeline/data_pipeline/api.py`. Khi xây backend production, inject
Neo4j-backed `graph_expand` vào repository/service thay vì truy vấn quan hệ
trực tiếp trong route. Graph adapter trả document/path candidates; repository
phải hydrate chunk text từ Supabase trước khi đưa vào prompt.

## Thiết lập

```bash
source .venv/bin/activate
cp .env.example .env       # chỉ khi chưa có .env
```

Điền `DATABASE_URL`/`PG*`, `OPENAI_API_KEY` và Neo4j. Local Neo4j:

```bash
docker compose -f database/neo4j/docker-compose.yml up -d
```

Local mặc định dùng `bolt://localhost:7687`, user `neo4j`, password
`change-me`. Trước khi deploy phải đổi password.

## Tái tạo và cập nhật dataset

```bash
export PYTHONPATH="$PWD/database/pipeline:."

.venv/bin/python database/pipeline/scripts/build_page_index.py \
  --source-dir data/raw --output-dir data/clean/page_index
.venv/bin/python database/pipeline/scripts/extract_tables.py \
  --source-dir data/raw --output-dir data/clean/tables
.venv/bin/python database/pipeline/scripts/build_facets.py \
  --source-dir data/raw --output-dir data/clean/facets
.venv/bin/python database/pipeline/scripts/ingest_snapshot.py \
  --source-dir data/raw
```

Lệnh ingest tạo release `staging`. Sau đó lấy `dataset_id` được in ra và chạy:

```bash
.venv/bin/python database/pipeline/scripts/embed_dataset.py <dataset-id> \
  --batch-size 256

NEO4J_URI=bolt://localhost:7687 \
NEO4J_USERNAME=neo4j NEO4J_PASSWORD=change-me \
.venv/bin/python database/neo4j/scripts/import_relationships.py \
  --source-dir data/raw --dataset-id <dataset-id>
```

## Kiểm tra dữ liệu sau ingest

Supabase:

```sql
SELECT dataset_id, status, manifest->'counts'
FROM datasets ORDER BY created_at DESC;

SELECT count(*) AS chunks, count(embedding) AS embedded
FROM chunks WHERE dataset_id = '<dataset-id>';
```

Neo4j Aura:

```cypher
MATCH (n)-[r]->(m)
WHERE r.dataset_id = '<dataset-id>'
RETURN n, r, m LIMIT 200;
```

Embedding chỉ publish release sau khi mọi chunk có vector. Graph importer chạy
idempotent theo `dataset_id`, xóa cạnh cũ của release rồi nạp lại theo đúng
predicate; kiểu quan hệ hiển thị là `REL_Can_cu`, `REL_Bai_bo`, v.v., còn
predicate gốc vẫn được giữ trong `r.relationship_type`.

## Neo4j Aura

Aura dùng cùng importer, chỉ thay biến môi trường:

```bash
export NEO4J_URI='neo4j+s://<instance-id>.databases.neo4j.io'
export NEO4J_USERNAME='neo4j'
export NEO4J_PASSWORD='<aura-password>'
export NEO4J_DATABASE='neo4j'
.venv/bin/python database/neo4j/scripts/import_relationships.py \
  --source-dir data/raw --dataset-id <active-dataset-id>
```

Không dùng `database/neo4j/docker-compose.yml` trong production. Không commit
Aura password.

## Kiểm tra

```bash
PYTHONPATH=database/pipeline:. .venv/bin/pytest -q
.venv/bin/python -m compileall -q src database neo4j firebase
git diff --check
```

Nếu test pipeline được chạy độc lập, luôn đặt `PYTHONPATH=database/pipeline`.

## Quy tắc dọn dẹp

- Không thêm bảng `entities`, `relations` hoặc `relationships` vào Supabase;
  graph thuộc Neo4j.
- Không dùng Chroma, Pinecone, SentenceTransformer, PyVI hoặc GPU embedding
  local cho pipeline hiện tại.
- Không commit `data/clean/`, vector artifacts, `.env`, Firebase service
  account hoặc Aura credential.
- `database/pipeline/data_pipeline/canonical.py` vẫn đọc
  `relationships.csv` để tạo manifest và Neo4j import; điều đó không có nghĩa
  relationships được lưu trong PostgreSQL.
