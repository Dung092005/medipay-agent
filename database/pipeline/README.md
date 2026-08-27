# BHYT / viện phí: Supabase + Neo4j + Qdrant

Thư mục này chứa dữ liệu nguồn và pipeline đưa dữ liệu BHYT/viện phí vào
database. Không có dữ liệu bệnh nhân hay dữ liệu khách hàng. Knowledge graph
được import riêng vào [`database/neo4j`](../neo4j). Semantic vectors được lưu
ở Qdrant; Supabase giữ dữ liệu chuẩn, provenance và lexical search.

## Làm nhanh

Chạy theo đúng thứ tự sau.

### 1. Tạo database Supabase

1. Tạo một project mới tại Supabase.
2. Mở **SQL Editor → New query**.
3. Mở file [database/postgres/schema.sql](../postgres/schema.sql), copy toàn bộ nội dung vào
   SQL Editor và bấm **Run**.
4. Vào **Table Editor**. Nếu thấy các bảng `datasets`, `documents`, `chunks`
   và `legal_units`, phần PostgreSQL đã sẵn sàng. Relationships nằm trong Neo4j.

Schema giữ raw HTML, legal unit, PageIndex và lexical search. Relationship
graph được lưu trong Neo4j; vector production được lưu ở Qdrant, không cần
giữ HNSW/vector trong Supabase Free.

### 2. Cấu hình máy chạy pipeline

Chạy các lệnh sau từ root repository:

```bash
cd /home/minh/projects/team-Vin-genius
python3 -m venv .venv-bhyt
source .venv-bhyt/bin/activate
make setup
```

Nếu đã có môi trường Python dùng chung của team thì không cần tạo môi trường
mới; chỉ cần bảo đảm `make setup` đã hoàn tất.

Copy `.env.example` thành `.env` ở root repo nếu chưa có, sau đó điền:

```env
DATABASE_URL=postgresql+asyncpg://postgres.<project-ref>:<mat-khau>@aws-0-<region>.pooler.supabase.com:5432/postgres
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<anon-key-neu-backend-can>

PGHOST=aws-0-<region>.pooler.supabase.com
PGPORT=5432
PGDATABASE=postgres
PGUSER=postgres.<project-ref>
PGPASSWORD=<mat-khau>

OPENAI_API_KEY=<openai-key>
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
EMBEDDING_BATCH_SIZE=256
QDRANT_URL=https://<cluster-id>.<region>.aws.cloud.qdrant.io
QDRANT_API_KEY=<qdrant-api-key>
QDRANT_COLLECTION=medical_legal_v1
QDRANT_TIMEOUT_SECONDS=30
```

Lấy các giá trị này tại **Supabase Dashboard → Connect → Session pooler**.
Session pooler port `5432` phù hợp với máy IPv4 hiện tại. Nếu máy có IPv6 và
cần chạy migration dài, Direct connection cũng dùng được. Không commit `.env`
hoặc mật khẩu lên Git.

### 3. Tạo và nạp dataset

```bash
cd /home/minh/projects/team-Vin-genius
export PYTHONPATH="$PWD/database/pipeline"

python database/corpus/build_active_corpus.py --offline
python database/corpus/validate_candidate.py

python database/pipeline/scripts/build_page_index.py --source-dir data/clean/medical_active_v2 \
  --output-dir data/clean/page_index
python database/pipeline/scripts/extract_tables.py --source-dir data/clean/medical_active_v2 \
  --output-dir data/clean/tables
python database/pipeline/scripts/build_facets.py --source-dir data/clean/medical_active_v2 \
  --output-dir data/clean/facets
python database/pipeline/scripts/ingest_snapshot.py --source-dir data/clean/medical_active_v2
```

Lệnh cuối in ra một `dataset_id`, ví dụ `snapshot-...`. Worker chỉ embed các
prose passage có `semantic_eligible=true` và mặc định giữ
release ở `staging`:

```bash
python database/pipeline/scripts/embed_dataset.py snapshot-... --batch-size 256
python database/neo4j/scripts/import_relationships.py \
  --source-dir data/clean/medical_active_v2 --dataset-id snapshot-...
# Sau khi edge/type/direction/provenance parity đã pass:
python database/pipeline/scripts/embed_dataset.py snapshot-... --publish
```

Table rows dùng lexical/structured retrieval, không tạo vector mặc định.

Artifact embedding phải được upload vào Qdrant sau khi parity pass. Mỗi point
dùng `passage_id` làm ID; payload tối thiểu gồm `dataset_id`, `document_id`,
`unit_id`, `source_start`, `source_end`, `input_sha256`, `answer_ready` và
`legal_status`. Chỉ publish collection sau khi đối chiếu đủ passage ID/input
hash với `database/corpus/verify_live_corpus_parity.py`.

### 4. Kiểm tra

```bash
python3 -m unittest discover -s database/pipeline/tests -p 'test_*.py' -q
python3 -m compileall -q database/pipeline/data_pipeline database/pipeline/scripts
```

## Chạy embedding offline

Nếu chưa muốn ghi vào Supabase, vẫn có thể tạo artifact embedding local qua OpenAI:

```bash
python3 database/pipeline/scripts/embed_snapshot.py \
  --source-dir data/clean/medical_active_v2 \
  --output-dir data/clean/embeddings \
  --batch-size 8
```

Artifact này không tự upload lên Supabase.

## Lưu embedding artifact

Không nên commit file `.npy` vào Git. Sau khi tạo artifact, hãy lưu ba file
trong một bucket **private** của Supabase Storage, ví dụ bucket
`bhyt-artifacts`:

```text
embeddings/snapshot-.../embeddings.float32.npy
embeddings/snapshot-.../passages.jsonl
embeddings/snapshot-.../manifest.json
```

Tạo bucket tại **Supabase Dashboard → Storage → New bucket**. Không dùng
`anon key` để upload; thao tác upload cần service role key hoặc dashboard và
service role key không được commit.

Máy khác chỉ cần tải thư mục artifact về rồi chạy:

```bash
python3 database/corpus/qdrant_release.py \
  --artifact-dir data/clean/embeddings/snapshot-... \
  --metadata-csv data/clean/metadata.csv
```

Lệnh này kiểm tra manifest, số dòng, số chiều, nạp vector vào collection Qdrant
theo release và chỉ được phép đổi alias sau parity; PostgreSQL vẫn giữ canonical
text/provenance để hydrate/citation.
Qdrant là bản dùng trực tiếp cho semantic query; Supabase vẫn là nguồn chuẩn
để hydrate nội dung và citation.

## Có nên push CSV và `.npy` lên Git không?

| Loại file | Có push Git? | Lý do |
|---|---:|---|
| `data/raw/*.csv` | Có thể | Đây là dữ liệu authority, không chứa khách hàng và cần để tái tạo release. Tổng khoảng 25 MB, vẫn chấp nhận được nếu team muốn version hóa trực tiếp. |
| `data/clean/*.csv`, PageIndex, facets, tables | Không cần | Đây là artifact sinh lại được từ CSV nguồn. |
| `embeddings.float32.npy` | Không nên | Candidate v2 khoảng 88,5 MB raw float32, phụ thuộc model; lưu trong private object storage thay vì làm repository phình. |
| `.env` | Tuyệt đối không | Chứa mật khẩu Supabase. |

Khuyến nghị: commit `data/raw/*.csv` nếu đây là bộ dữ liệu chính thức
của team; không commit `data/clean/` và `.npy`. Nếu muốn lưu embedding để không
phải gọi lại API, dùng Git LFS, Supabase Storage hoặc object storage riêng.

Chunker v5 dùng legal unit → paragraph/sentence → gom tối đa 320 approximate
tokens, bỏ structural-only chunks và tạo table-row passages có header context.
Release active `snapshot-c439751724ab7f10` có 682 documents, 37.170 passages,
28.285 legal units và 5.808 legal relationships; 14.393 passages cần embedding
và đang chờ upload vào Qdrant.

## Supabase Free

Live project hiện dùng khoảng 162 MiB dưới quota 500 MiB vì vector/HNSW đã được
offload sang Qdrant. Không chạy `load_embedding_artifact.py` lên production
Supabase Free; hãy upload artifact vào Qdrant và giữ Supabase ở chế độ lexical.
