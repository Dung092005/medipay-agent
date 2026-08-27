# Medical corpus audit

`audit_medical_corpus.py` reconciles the curated CSV corpus, the two active
JSON exports and (optionally) the active PostgreSQL/Neo4j inventories. It is
strictly read-only with respect to source files and databases.

Run the complete audit from the repository root:

```bash
.venv/bin/python database/audit/audit_medical_corpus.py \
  --with-database --with-neo4j
```

The default CSV source is:

```text
/home/minh/projects/csv_admin_bhyt_vien_phi/source_originals
```

Override it with `--source-dir` when moving the corpus. Generated files are
written to `database/audit/results/`:

- `REPORT.md`: human-readable assessment and recovery plan;
- `summary.json`: machine-readable quality-gate metrics;
- `document_inventory.csv`: one row per document/reference ID;
- `relationship_inventory.csv`: active, retained and removed edges;
- `issues.csv`: explicit issue, severity and resolution rows.
- `STORAGE_CLEANUP.md`: live Supabase cleanup, exact size and Free-plan gate;
- `NEO4J_CANDIDATE_IMPORT.md`: candidate graph parity/import result.

The audit never trusts `medical_docs_active.json.content_text`; it compares
that projection with visible text regenerated from the record's own HTML.
