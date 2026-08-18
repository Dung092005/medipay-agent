# MediPay GraphRAG Architecture

## System Overview

```mermaid
graph TB
    User([User]) --> Web[Next.js app / web]
    Web -->|REST| API[FastAPI / src/api]
    API --> Agent[LangGraph GraphRAG]
    Agent --> Retrieve[Multi-store retrieval]
    Retrieve --> LV[(Lexical + Vector DB\nSupabase PostgreSQL + pgvector)]
    Retrieve --> PI[(PageIndex index\nSupabase legal_units + spans)]
    Retrieve --> G[(Graph DB\nNeo4j Aura)]
    LV --> Union[Candidate union + dedupe]
    PI --> Union
    G --> Union
    Union --> RRF[Weighted RRF]
    RRF --> Rerank[Deterministic rerank + diversity]
    Rerank --> Evidence[Hydrate Supabase text + citations]
    Evidence --> Answer[Grounded answer]
    Agent --> Model[Configured LLM adapter]
```

## Agent Flow

```mermaid
graph LR
    START((START)) --> Intake[Intake]
    Intake --> Plan[Build query plan]
    Plan --> Channels[Exact + lexical + semantic]
    Channels --> Graph[Bounded Neo4j expansion]
    Channels --> Page[PageIndex hierarchy/spans]
    Channels --> Context[Candidate union]
    Graph --> Context
    Page --> Context
    Context --> RRF[RRF + deterministic rerank]
    RRF --> Generate[Generate]
    Generate --> Guardrail[Guardrail]
    Guardrail --> END((END))
```

## Components

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Frontend | Next.js in `web/` | Future user interface |
| Backend | FastAPI in `src/` | API and application services |
| Agent | LangGraph | GraphRAG orchestration |
| Database | Supabase PostgreSQL | Documents, chunks, PageIndex, lexical and semantic indexes |
| Vector | PostgreSQL `pgvector` | Semantic chunk retrieval |
| Graph | Neo4j | Graph neighborhood retrieval |
| Models | OpenAI embeddings + configured LLM adapter | `text-embedding-3-small`, 1536 dimensions |

Schema SQL lives in `database/schema.sql`; `legal_units` is the PageIndex
structure store inside Supabase, while Neo4j stores the knowledge graph. Docker
runs backend only and connects to both services.
