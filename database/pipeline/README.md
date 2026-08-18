# BHYT / viện phí: hướng dẫn từ đầu đến cuối

Thư mục này chứa dữ liệu nguồn và pipeline đưa dữ liệu BHYT/viện phí vào
database. Không có dữ liệu bệnh nhân hay dữ liệu khách hàng. Knowledge graph
được import riêng vào [`database/neo4j`](../neo4j).

## Làm nhanh

Chạy theo đúng thứ tự sau.

### 1. Tạo database Supabase

1. Tạo một project mới tại Supabase.
2. Mở **SQL Editor → New query**.
3. Mở file [database/schema.sql](../schema.sql), copy toàn bộ nội dung vào
   SQL Editor và bấm **Run**.
4. Vào **Table Editor**. Nếu thấy các bảng `datasets`, `documents`, `chunks`
   và `legal_units`, phần PostgreSQL đã sẵn sàng. Relationships nằm trong Neo4j.

Schema đã bật `pgvector`, giữ raw HTML, legal unit, PageIndex, lexical search
và vector search. Relationship graph được lưu trong Neo4j, không nằm trong
schema PostgreSQL.

### 2. Cấu hình máy chạy pipeline

Chạy các lệnh sau từ root repository:

```bash
python3 -m venv .venv-bhyt
source .venv-bhyt/bin/activate
pip install -r requirements.txt
```

Nếu đã có môi trường Python dùng chung của team thì không cần tạo môi trường
mới; chỉ cần bảo đảm `requirements.txt` đã được cài.

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
```

Lấy các giá trị này tại **Supabase Dashboard → Connect → Session pooler**.
Session pooler port `5432` phù hợp với máy IPv4 hiện tại. Nếu máy có IPv6 và
cần chạy migration dài, Direct connection cũng dùng được. Không commit `.env`
hoặc mật khẩu lên Git.

### 3. Tạo và nạp dataset

```bash
export PYTHONPATH="$PWD/database/pipeline"

python database/pipeline/scripts/build_page_index.py --source-dir data/raw \
  --output-dir data/clean/page_index
python database/pipeline/scripts/extract_tables.py --source-dir data/raw \
  --output-dir data/clean/tables
python database/pipeline/scripts/build_facets.py --source-dir data/raw \
  --output-dir data/clean/facets
python database/pipeline/scripts/ingest_snapshot.py --source-dir data/raw
```

Lệnh cuối in ra một `dataset_id`, ví dụ `snapshot-...`. Dùng dataset đó để
chạy embedding:

```bash
python database/pipeline/scripts/embed_dataset.py snapshot-... --batch-size 256
```

Worker gọi OpenAI theo batch, kiểm tra provenance, ghi vector vào Supabase và
chỉ publish dataset nếu toàn bộ kiểm tra đạt.

### 4. Kiểm tra

```bash
python3 -m unittest discover -s database/pipeline/tests -p 'test_*.py' -q
python3 -m compileall -q database/pipeline/data_pipeline database/pipeline/scripts
```

## Chạy embedding offline

Nếu chưa muốn ghi vào Supabase, vẫn có thể tạo artifact embedding local qua OpenAI:

```bash
python3 database/pipeline/scripts/embed_snapshot.py \
  --source-dir data/raw \
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
python3 database/pipeline/scripts/load_embedding_artifact.py snapshot-... \
  data/clean/embeddings/snapshot-...
```

Lệnh này kiểm tra manifest, số dòng, số chiều và nạp vector vào `pgvector`.
Storage là bản lưu/phân phối, còn
`chunks.embedding` là bản dùng trực tiếp để truy vấn.

## Có nên push CSV và `.npy` lên Git không?

| Loại file | Có push Git? | Lý do |
|---|---:|---|
| `data/raw/*.csv` | Có thể | Đây là dữ liệu authority, không chứa khách hàng và cần để tái tạo release. Tổng khoảng 25 MB, vẫn chấp nhận được nếu team muốn version hóa trực tiếp. |
| `data/clean/*.csv`, PageIndex, facets, tables | Không cần | Đây là artifact sinh lại được từ CSV nguồn. |
| `embeddings.float32.npy` | Không nên | File khoảng 45 MB, phụ thuộc model; lưu trong Supabase Storage thay vì làm repository phình. |
| `.env` | Tuyệt đối không | Chứa mật khẩu Supabase. |

Khuyến nghị: commit `data/raw/*.csv` nếu đây là bộ dữ liệu chính thức
của team; không commit `data/clean/` và `.npy`. Nếu muốn lưu embedding để không
phải gọi lại API, dùng Git LFS, Supabase Storage hoặc object storage riêng.

Chunk hiện tại dùng legal unit → paragraph/sentence → gom mục tiêu 144 tokens.
Dataset hiện có 15.471
passages, 646 tables và 26.079 cells.
