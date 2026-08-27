# Hướng dẫn gắn Qdrant (Group project)

Mục tiêu: semantic search chạy trên **Qdrant của bạn**, Postgres/Supabase chỉ giữ text + citation.

```text
serving_bhyt_slice (70 docs)
        │
        ▼
embed_snapshot.py  →  data/clean/embeddings/<dataset_id>/
        │                 (manifest.json + passages.jsonl + embeddings.float32.npy)
        ▼
qdrant_release.py --activate
        │
        ▼
Qdrant Cloud của bạn
  physical: medical_legal_snapshot_bhyt_...
  alias:    medical_legal_active   ← app đọc alias này
```

---

## Bước 1 — Lấy Qdrant của bạn (Cloud)

1. Vào [https://cloud.qdrant.io](https://cloud.qdrant.io) → tạo cluster (Free tier đủ ~8k vectors).
2. Cluster → **Overview**:
   - **Cluster URL** dạng `https://xxxxx-xxxxx.aws.cloud.qdrant.io`
3. **API Keys** → tạo key (copy 1 lần).

Không dùng Qdrant của P-151 / team khác.

---

## Bước 2 — Điền `.env`

Trong `Group project/.env`:

```env
QDRANT_URL=https://<cluster-id>.<region>.aws.cloud.qdrant.io
QDRANT_API_KEY=<api-key-cua-ban>
QDRANT_COLLECTION=medical_legal_active
QDRANT_TIMEOUT_SECONDS=30
```

`QDRANT_COLLECTION` là **alias ổn định** mà API đọc. Collection vật lý theo từng release sẽ có tên dạng `medical_legal_snapshot_bhyt_...`.

Gửi cho agent (chat) 2 dòng `QDRANT_URL` + `QDRANT_API_KEY` nếu muốn agent ghi giúp `.env` và chạy tiếp.

---

## Bước 3 — Cài client

PowerShell, trong thư mục `Group project`:

```powershell
.\.venv\Scripts\Activate.ps1
pip install "qdrant-client>=1.9.0"
python scripts/verify_qdrant_connection.py
```

Thấy `ok: true` + list collections là xong bước kết nối.

---

## Bước 4 — Tạo embedding artifact (local)

Corpus serving: `data/clean/serving_bhyt_slice` (~70 docs).

```powershell
$env:PYTHONPATH="$PWD\database\pipeline"
python database\pipeline\scripts\embed_snapshot.py `
  --source-dir data\clean\serving_bhyt_slice `
  --output-dir data\clean\embeddings `
  --batch-size 64
```

Lệnh này gọi OpenRouter/OpenAI embedding → tốn token. Kết quả:

```text
data/clean/embeddings/<dataset_id>/
  manifest.json
  passages.jsonl
  embeddings.float32.npy
```

Ghi lại `<dataset_id>` in ra (hoặc mở `manifest.json`).

---

## Bước 5 — Upload lên Qdrant + bật alias

```powershell
$env:PYTHONPATH="$PWD\database\pipeline"
python database\corpus\qdrant_release.py `
  --artifact-dir data\clean\embeddings\<dataset_id> `
  --metadata-csv data\clean\serving_bhyt_slice\metadata.csv `
  --activate
```

Thành công sẽ in JSON: `uploaded_or_replaced_points`, `parity.id_hash_parity=true`, `alias_activated=true`.

Dry-run trước (không ghi Qdrant):

```powershell
python database\corpus\qdrant_release.py `
  --artifact-dir data\clean\embeddings\<dataset_id> `
  --metadata-csv data\clean\serving_bhyt_slice\metadata.csv `
  --dry-run
```

---

## Bước 6 — Kiểm tra nhanh

```powershell
python scripts/verify_qdrant_connection.py
```

Expect: alias `medical_legal_active` trỏ đúng collection, point count ≈ số dòng artifact.

---

## Lưu ý quan trọng

| Việc | Trạng thái Group hiện tại |
|---|---|
| Postgres text + Neo4j | Đã có release BHYT Hà Nội |
| Qdrant vectors | **Chưa** — làm theo guide này |
| Bảng `release_projections` | Schema cũ **chưa có** → `/ready` kiểu P-151 có thể vẫn `qdrant:false` cho đến khi chạy migrations |
| Firebase auth | Dev bypass vẫn bật |

Sau khi Qdrant xong, bước kế: chạy migrations `database/postgres` + đăng ký projection cho release active.

---

## Checklist

- [ ] Có cluster Qdrant **của bạn**
- [ ] `.env` có `QDRANT_URL` + `QDRANT_API_KEY`
- [ ] `verify_qdrant_connection.py` → ok
- [ ] Đã chạy `embed_snapshot.py`
- [ ] Đã chạy `qdrant_release.py --activate`
- [ ] Alias `medical_legal_active` tồn tại
