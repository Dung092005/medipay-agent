# Database contract for AI coding agents

This file is the implementation contract for an AI agent working on the data
layer. Follow it before changing code.

## Source of truth

`data/raw/` is authoritative. The canonical builder creates a deterministic
release with a stable `dataset_id`, document provenance, legal-unit boundaries,
table source spans, chunks and relationship predicates.

## Storage ownership

| Concern | System | Contract |
|---|---|---|
| Documents and source HTML | Supabase PostgreSQL | Release-scoped, immutable after staging |
| Lexical retrieval | PostgreSQL | `search_vector` and active views |
| Semantic retrieval | Qdrant | `medical_legal_active` alias → versioned cosine collection |
| Legal document graph | Neo4j | Directed edges from `relationships.csv` |
| Authentication client | Firebase | Frontend public config only |

## Retrieval architecture

Treat retrieval as four evidence channels, not as one generic vector search:

1. **Exact**: match document title and `so_ky_hieu` for high precision.
2. **Lexical**: query `chunks.search_vector` with PostgreSQL full-text search
   and deterministic `ts_rank_cd` ordering.
3. **Semantic**: embed the question with `text-embedding-3-small`, then query
   Qdrant with the active `dataset_id` and `answer_ready=true` payload filter.
4. **Legal graph**: use document IDs from the first three channels as seeds,
   traverse bounded directed edges in Neo4j, then fetch target chunk evidence
   from Supabase.

PageIndex is the structural evidence layer. It is built from legal HTML and
materialized as `page_index_nodes.jsonl`/`page_index_edges.csv`, with the same
legal-unit structure persisted in `legal_units`. It supplies hierarchy,
ordinal, parent, source offsets and citation boundaries. It is not a ranking
channel and must not be replaced by arbitrary text splitting.

Use three physical stores: Supabase contains canonical text, lexical and
PageIndex/legal-unit data; Qdrant contains derived vectors; Neo4j Aura contains
the directed graph. PageIndex artifacts are build outputs, while `legal_units`
in Supabase is the runtime source for hierarchy and spans.

```text
query
 ├─ query plan: document number, legal labels, category, date/jurisdiction
 ├─ exact + lexical + semantic candidates from active Supabase release
 ├─ graph expansion from candidate document IDs in Neo4j, depth <= graph_hops
 ├─ legal_unit/PageIndex provenance for every selected chunk
 └─ reciprocal-rank fusion → grounded context → cited answer
```

Fuse the candidate union with weighted RRF, then apply deterministic reranking
and a document/unit diversity cap. Suggested starting weights are exact 2.0,
PageIndex 1.25, lexical 1.0, semantic 1.0 and graph 0.75; tune them on a
held-out evaluation set. Preserve channel ranks and raw scores in every hit.
Graph traversal returns IDs and typed paths; final text must be hydrated from
Supabase chunks/legal units. Never turn a graph edge or similarity score into
legal authority.

Never recreate graph persistence in PostgreSQL. `src/db/repositories.py` may
query vectors through SQLAlchemy, but graph traversal must be delegated to the
Neo4j adapter (`src/integrations/neo4j.py`).

## Evidence and citation invariants

Every answer-ready evidence item must include the active `dataset_version`,
canonical `document_id`, `chunk_id`/`passage_id`, `unit_id`, source offsets,
retrieval channel and score. Graph nodes and edges are navigation/context
signals; quoted legal text must come from Supabase chunks/legal units, never
from an LLM-generated graph fact.

## Embedding contract

- Model: `text-embedding-3-small`.
- Dimensions: exactly `1536`.
- Provider: OpenAI API, using `OPENAI_API_KEY`.
- Inputs: section title followed by chunk text.
- Stored metadata: model, dimensions, input SHA-256, normalized flag and
  embedding timestamp.
- Existing vectors from another dimension must be discarded and rebuilt.
- Do not silently truncate, fall back to a local model, or change dimensions.

## Release contract

1. Build deterministic artifacts from `data/raw`.
2. Stage documents/chunks/tables in Supabase.
3. Embed only semantic-eligible passages and preserve the local artifact.
4. Upload a versioned Qdrant collection; verify every passage ID/input hash.
5. Move `medical_legal_active` alias atomically only after Qdrant parity.
6. Import the same `dataset_id` into Neo4j and publish/cut over the release.

If any step fails, leave the previous active release untouched. Never mark a
release active by bypassing vector validation except for an explicitly named
emergency operation.

## Neo4j contract

The importer reads `data/raw/relationships.csv`. Each edge must retain:

- `dataset_id`
- source and target document IDs
- original `relationship_type`
- `relationship_id`
- adverse flag

Neo4j Community cannot use Enterprise composite NODE KEY constraints. The
importer therefore uses a deterministic single property `graph_id` and creates
readable relationship labels such as `REL_Can_cu`; the original Vietnamese
predicate remains `r.relationship_type`.

Aura deployment uses `neo4j+s://...`, not the local Bolt URI. Credentials come
only from environment variables. Do not invent, print, or commit credentials.

## Firebase contract

`database/firebase/` is only a frontend auth scaffold. It may contain public
`NEXT_PUBLIC_FIREBASE_*` configuration. It must not contain Admin SDK keys,
service-account JSON, private keys or server auth implementation unless a
separate backend auth task explicitly requires it.

## Safe change procedure

- Inspect `git status` first and preserve unrelated user changes.
- Prefer `database/postgres/schema.sql` and the pipeline modules over new ad-hoc SQL.
- Keep all SQL release-scoped by `dataset_id`.
- Keep API routes free of direct SQL, embedding calls and graph orchestration.
- Update `README-DEV.md`, this contract and `.env.example` when a boundary or
  environment variable changes.
- Run compile, tests and `git diff --check` before handoff.
- Report blockers instead of fabricating successful external writes.
