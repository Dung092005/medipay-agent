# PostgreSQL — AI implementation contract

Only `database/postgres/schema.sql` and `database/postgres/migrations/` may own
PostgreSQL DDL. Add every schema change as a numbered forward migration and a
matching rollback file when rollback is meaningful. Preserve `dataset_id`,
release state, RLS and migration checksums.

Never add Neo4j relationships or Qdrant vectors to PostgreSQL merely to make a
query convenient. Hydrate answer evidence from canonical PostgreSQL rows and
retain document/unit/source-span provenance.
