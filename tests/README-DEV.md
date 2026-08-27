# Tests — developer guide

`tests/` covers API/application/security behavior; database-specific suites
live beside their implementation under `database/*/tests`. Run all gates with
`make check`. Tests deliberately override developer secrets so a local `.env`
cannot change expected results.
