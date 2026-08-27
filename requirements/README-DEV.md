# Python dependency sets — developer guide

There are four intentional environments, all locked with hashes:

| File | Use | Installed into |
|---|---|---|
| `runtime.in` / `runtime.lock` | API/worker online runtime | `Dockerfile` |
| `migrate.in` / `migrate.lock` | one-shot PostgreSQL migrations | `Dockerfile.migrate` |
| `pipeline.in` / `pipeline.lock` | offline corpus worker | `Dockerfile.pipeline` |
| `dev.in` / `dev.lock` | local tests, lint and all above | `make setup` |

Do not add a package to the runtime set merely because an offline script needs
it. Change the relevant `.in` file and regenerate its lock with uv for Python
3.11; never hand-edit generated lock hashes.
