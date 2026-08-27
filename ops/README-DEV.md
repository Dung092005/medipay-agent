# Operations — developer guide

`ops/runbooks/` contains deploy, restore, observability and secret-rotation
procedures. `ops/monitoring/` contains Prometheus alert rules. Use `make
env-check`, `make deploy-contract` and the documented one-shot migration flow
before changing a deployment contract.

Render/Vercel operator credentials belong in an ignored `.env` or a platform
secret manager; never put them in `.env.example` or logs.
