# Database migration authority

`database/postgres/migrations/` is the ordered, idempotent forward-migration history
for the managed PostgreSQL database. Apply the files lexicographically, once,
from a dedicated migration/service-role connection; never run DDL from an API
replica or from a request handler.

`database/postgres/schema.sql` is the fresh-bootstrap snapshot and must remain
compatible with the migration head. It is intentionally useful for a new
Supabase project, while existing projects must use the forward migrations so
that release data is not rewritten accidentally.

For a protected one-shot job, build `Dockerfile.migrate` and provide only
`MIGRATION_DATABASE_URL` to that container. The API image and Render web
service do not run this command at startup.

Before a production migration:

1. take a PostgreSQL and Neo4j backup and record the active release;
2. run the SQL in a transaction where possible, with an advisory lock;
3. verify table/view/RLS parity and active-release counts;
4. run `database/corpus/verify_live_corpus_parity.py` before changing the
   active pointer.

Rollback files are emergency, explicitly reviewed operations. They are not
automatically executed by deploy tooling because dropping a table or policy
can destroy recoverability.

The release manifest is the control-plane contract for external projections.
It records the dataset fingerprint, semantic passage count, Qdrant collection,
embedding model/dimensions, input-hash algorithm, and parity verification
state. A release is not considered publishable until those values agree with
PostgreSQL and Qdrant.

`public.release_projections` is the machine-readable projection registry. It
stores one row per release and projection (`postgres`, `qdrant`, `neo4j`), with
the physical locator, release fingerprint, expected/actual counts and parity
metadata. Publication tooling must write `building` → `ready` only after the
projection-specific verifier passes; runtime readiness rejects a missing,
failed or count-mismatched Qdrant/Neo4j row.

`ops.active_release` is the cutover control-plane pointer. Its guarded
`activate_release()` function requires all three projection rows to be ready
with matching fingerprint/counts, records `previous_dataset_id` and advances
a monotonic generation under an advisory lock. The physical previous
projections must still exist before an operator uses it for rollback.
