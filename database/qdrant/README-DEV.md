# Qdrant — developer guide

Qdrant stores derived semantic vectors only. Release builders and parity tools
remain in `database/corpus/`; the API adapter is `src/integrations/qdrant.py`.

Required environment: `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION` and
`EMBEDDING_MODEL=text-embedding-3-small` with `EMBEDDING_DIMENSIONS=1536`.
Publish a versioned collection, verify point IDs and input hashes, then move
the active alias atomically. Never commit vector artifacts.
