# Restore and rollback runbook

1. Freeze publication and record `ops.active_release.active_dataset_id`,
   `previous_dataset_id` and `generation` (the legacy `dataset_state` value
   must match it).
2. Restore PostgreSQL into a disposable volume first; apply the ordered
   migrations and run `shadow_rehearsal.py`.
3. Restore the matching Qdrant collection/artifact and Neo4j export. Refuse a
   non-empty target unless an explicit disposable target is selected.
4. Run `verify_live_corpus_parity.py` and compare document/chunk/hash,
   Qdrant-point and Neo4j approved-edge counts.
5. Call guarded `ops.activate_release()` only after all rows are `ready`; restart API and
   verify `/ready`, exact lookup, high-risk abstention and SSE.
6. If parity fails, keep the active pointer unchanged and retain the failed
   restore for diagnosis. Never delete the only active or rollback projection.
