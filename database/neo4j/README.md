# Neo4j knowledge graph

Neo4j là nơi duy nhất lưu knowledge graph: document nodes và các relationship
từ release authority đã qualify. Release hiện tại được dựng từ
`data/clean/medical_active_v31_fully_reviewed/relationships.csv`.
Supabase/PostgreSQL chỉ lưu canonical document, alias resolution, chunk và
table; không lưu relationship hoặc reference-only stub. Vector của release
Free-tier hiện tại được giữ ngoài PostgreSQL để chờ chuyển Qdrant.

## Local development

```bash
docker compose --profile local-full up -d neo4j
```

Thiết lập `NEO4J_URI=bolt://localhost:7687`, `NEO4J_USERNAME=neo4j` và
`NEO4J_PASSWORD` trong `.env`. Với Aura dùng `neo4j+s://...` và password Aura.
Import release bằng script pipeline sau khi snapshot canonical đã pass:

```bash
python3 database/neo4j/scripts/import_relationships.py \
  --source-dir data/clean/medical_active_v22_production_hotfix_source --dataset-id <release>
```

Importer tạo `graph_id=<dataset_id>:<document_id>`, giữ nguyên
`relationship_id`, predicate gốc, adverse/provenance flags và alias. Canonical
và alias nodes đồng bộ cả `title` và `so_ky_hieu`; `content_text` vẫn chỉ nằm
ở Supabase và được hydrate theo cùng `dataset_id`. Toàn bộ
release nằm trong một transaction. Trước commit, script đối chiếu node kinds,
edge count theo predicate, ID uniqueness, alias count và cross-release edges;
bất kỳ sai lệch nào cũng rollback.

Không dùng `--replace-existing` trừ khi retry có kiểm soát đúng cùng
`dataset_id`. Mọi Cypher query online phải filter `dataset_id` lấy từ
Supabase `dataset_state`; graph release mới không tự trở thành active chỉ vì đã
import vào Neo4j. Online expansion và endpoint relationships phải bổ sung
`r.serving_status = 'approved_evidence'`; các cạnh audit-only không được dùng
để tạo câu trả lời hay cảnh báo hiệu lực.

Active `snapshot-c439751724ab7f10` đã parity-pass ngày 2026-08-18: 1.901
nodes (682 canonical, 1.211 reference-only, 8 alias), 5.808 legal relationships
và 8 `ALIAS_OF` edges. Supabase giữ text/chunk lexical; 14.393 vector 1536 chiều
được offload vào artifact local đã đối chiếu passage ID và input SHA-256 để chờ
import Qdrant. Mọi query graph online vẫn phải lấy `dataset_id` active từ
Supabase.
