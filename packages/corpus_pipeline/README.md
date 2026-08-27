# `corpus_pipeline`

This package is the future import boundary for offline corpus work.  During the
cutover it deliberately re-exports the audited implementation under
`database/pipeline/data_pipeline`; callers can migrate imports one module at a
time without creating a second algorithm or storage path.

The compatibility layer is offline-only.  It must never be installed in the
online API image and it does not own release publication or database DDL.
