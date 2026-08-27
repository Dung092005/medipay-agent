# Packages — AI contract

Do not create a second implementation of canonicalization, storage or release
publication inside `packages/`. Compatibility modules may re-export audited
code, must remain offline-only, and must document their migration path.
