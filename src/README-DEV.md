# Backend source — developer guide

`src/api` exposes HTTP boundaries, `src/application` orchestrates use cases,
`src/domain` owns ports/claims, `src/services` contains business services,
`src/integrations` wraps providers, and `src/db` owns persistence adapters.
Routes should remain thin and use dependency injection.

Start locally with `make dev`; run `make check` before a commit.
