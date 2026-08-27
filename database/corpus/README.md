# Reconciled BHYT / viện phí corpus

`build_active_corpus.py` combines the curated CSV authority files with the two
active JSON exports. It never trusts JSON `content_text`; visible text is built
later from selected HTML by the canonical pipeline. Selected `content_html` is
serialized without whitespace normalization, then both its raw SHA-256 and the
regenerated visible-text SHA-256 are rechecked from the written CSV.

Run from the repository root:

```bash
python3 database/corpus/build_active_corpus.py
```

The default output is `data/clean/medical_active_v2/`. Four damaged/missing
documents are recovered from reviewed HTML sources and checked against
official metadata. Cached fragments make subsequent builds reproducible:

```bash
python3 database/corpus/build_active_corpus.py --offline
```

Important outputs:

- `metadata.csv`, `content.csv`, `relationships.csv`: candidate authority input
  for the canonical pipeline;
- `aliases.csv`: old/duplicate IDs mapped to one legal identity;
- `source_provenance.csv` and `recovery_cache/`: evidence lineage;
- `quality_issues.csv`, `crawl_backlog.csv`, `rejected_records.csv`,
  `reference_nodes.csv`: explicit
  unresolved data rather than silently fabricated values;
- `build_manifest.json`: input and artifact hashes plus release counts.

Do not overwrite `data/raw/` or publish to Supabase until the candidate passes
the canonical build and storage-capacity gates.

## Free-tier external-vector release

Release production `snapshot-c439751724ab7f10` (2026-08-18) giữ canonical
documents, lexical chunks và provenance trong Supabase, graph trong Neo4j, còn
14.393 embedding được giữ ở artifact local để chuyển sang Qdrant. Cách này giữ
Supabase dưới quota 500 MB mà không bỏ mất vector đã tạo.

Quy trình kiểm soát:

1. `reuse_embedding_backup.py` chỉ tái sử dụng vector khi cả passage ID và
   embedding-input SHA-256 khớp; input mới phải được embed lại.
2. `offload_staging_embeddings.py` chỉ dọn staging vector metadata sau khi
   artifact local khớp dataset ID, row count, dimensions và toàn bộ vector hữu hạn.
3. `verify_live_corpus_parity.py --external-embedding-artifact ...` đối chiếu
   source, Supabase, Neo4j và artifact theo từng passage/edge trước khi báo pass.

Artifact và candidate nằm dưới `data/clean/` nên không được commit. Không xóa
`data/clean/embeddings-reused/snapshot-c439751724ab7f10/` trước khi import và
verify Qdrant.
