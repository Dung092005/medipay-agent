# Database operating boundary

`database/postgres/migrations/` is the only forward-DDL authority. API and corpus
workers must not execute DDL. `database/postgres/schema.sql` is a disposable bootstrap
snapshot; managed installations use the ordered runner and a reviewed
baseline.

Canonical data remains in PostgreSQL (`public.*`). Qdrant and Neo4j are
release-scoped projections registered in `public.release_projections`. The
`ops.active_release` pointer records the active/previous release generation;
the guarded `ops.activate_release()` function is the intended cutover entrypoint.
`corpus.*_shadow` tables are an additive rehearsal contract with internal
bigint keys; they are not production reads until the parity and rollback gates
are signed off.

## Folder contract

| Path | Authority | Runtime rule |
|---|---|---|
| `postgres/` | PostgreSQL schema, ordered forward/rollback SQL and runner | The only place allowed to own PostgreSQL DDL |
| `qdrant/` | Qdrant projection contract and release notes | Vectors only; canonical text remains in PostgreSQL |
| `pipeline/` | Reusable canonicalization, indexing and ingest code | Offline/worker only; never runs schema creation |
| `corpus/` | Release build, parity, backup/restore and evaluation tools | Explicit command, release ID and artifact output required |
| `neo4j/` | Graph projection importer and tests | Serving edges must be `approved_evidence` |
| `firebase/` | Frontend Firebase client helpers | Admin credentials never belong here |
| `audit/` | Read-only audit code and generated evidence | Generated CSV/JSON stays ignored |
| `backups/` | Recoverable local backup/trash only | Never a Docker or Git source; retain checksums |

Large JSON exports, derived snapshots and backup trash are intentionally kept
outside the source authority and are recoverable locally. A cleanup may remove
only a named superseded release after its checksum, restore and rollback
evidence are attached; never delete the sole active or previous projection.

Generated artifacts, backups and audit reports belong outside the source
authority. They may be written to ignored `database/backups/` or
`eval/results/`, but never copied into Docker images or committed as corpus
source.

Useful checks:

```text
python database/postgres/migrations/runner.py --dry-run
python database/corpus/verify_live_corpus_parity.py ...
python database/corpus/shadow_rehearsal.py --dataset-id <release> ...
python database/corpus/build_table_cell_sat.py --dataset-id <release> ...
```
