# PostgreSQL — developer guide

PostgreSQL/Supabase is the canonical store for documents, legal units, chunks,
tables, lexical search, users and release metadata. The only schema authority
is `schema.sql` plus the ordered files in `migrations/`.

```bash
make setup
uv run python database/postgres/migrations/runner.py --dry-run
```

The API and corpus worker never create tables at startup. Apply migrations as a
separate one-shot job with `Dockerfile.migrate`; keep `MIGRATION_DATABASE_URL`
separate from the runtime connection. Do not commit dumps or passwords.
