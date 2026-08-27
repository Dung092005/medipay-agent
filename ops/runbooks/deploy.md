# Deploy runbook — Render API / Vercel web

This runbook is intentionally provider-agnostic until the external projects,
region and secret owners are recorded. Never paste secret values into a shell
transcript or CI log.

## External prerequisites

The Firebase service-account JSON previously shared during development is
compromised. Revoke that key in Firebase/Google Cloud IAM, generate a new key,
and install it only in the backend secret store. Prefer a Render secret file
with `GOOGLE_APPLICATION_CREDENTIALS`; if an environment variable is used,
set the new single-line JSON as `FIREBASE_SERVICE_ACCOUNT_JSON`. Never put it
in Vercel, the browser bundle, Git, or this runbook.

Generate the backend metrics bearer token locally and paste it directly into
the Render secret field (do not commit or send it here):

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Render access is created in Dashboard → Account Settings → API Keys. Store the
new value outside the repository as `RENDER_API_KEY`; the Render CLI/API uses
it as a bearer credential. Vercel access is created in the Personal Account →
Settings → Account Tokens page; store it outside the repository as
`VERCEL_TOKEN`. Then create/import the project with root directory `web`,
framework `nextjs`, and separate Development/Preview/Production variables.

These are operator credentials, not application runtime variables. Do not add
them to `.env.example` or pass them as Docker build arguments.

1. Verify `ops.active_release` generation and the release fingerprint; all
   three projection rows must be `ready` with exact counts.
2. Create PostgreSQL and Neo4j backups and retain active plus previous release.
3. Build migration, API and web images from a clean checkout; inspect context,
   SBOM and non-root/read-only settings.
4. Run the migration job once with `MIGRATION_DATABASE_URL`; never give that
   credential to API replicas.
5. Deploy Render with the injected `$PORT`, `/health` liveness and `/ready`
   readiness. Wait for dependency health before traffic.
6. Set Vercel Development/Preview/Production variables separately. Only public
   Firebase client variables may enter the browser bundle; Admin JSON belongs
   only in the backend secret store.
7. Set the protected `METRICS_TOKEN` and configure the dashboard scraper with
   an Authorization bearer header; never expose `/metrics` publicly in
   production.
8. Run authenticated browser login → chat → SSE final/cancel → citation smoke,
   then the dependency-failure and 20-request readiness smoke.
9. Promote only if quality, latency, cost and security gates pass. Keep the
   previous service/image/projections until the observation window ends.

Rollback: stop promotion, restore the previous API image and release pointer,
restore matching Qdrant/Neo4j projections, verify parity, then re-enable traffic.
