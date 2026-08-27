# Secret rotation runbook

Rotate in the provider secret manager, not in Git or `.env.example`.

1. Create a replacement credential with least privilege and record its owner,
   scope and expiry in the provider system.
2. Update Render/API and local ignored `.env` values; restart/recreate the
   service without rebuilding an image containing secrets.
3. Run authenticated `/ready`, `/chat`, `/analyze`, admin and projection smoke.
4. Revoke the old credential and verify an old-token/old-key negative test.
5. Scan tracked files, Docker context, image layers, CI logs and Vercel bundle;
   attach only redacted evidence to the release record.

The Firebase Admin private key supplied during development is a rotation
blocker until this sequence is completed. Do not copy it into this repository.
