# Corpus pipeline package — AI contract

Keep this package as a thin import boundary. It must not own DDL, production
release activation, credentials or a parallel algorithm. Any compatibility
change must preserve dataset IDs, provenance and deterministic output.
