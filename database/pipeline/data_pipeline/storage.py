"""Release-scoped PostgreSQL storage for the legal BHYT/viện phí corpus.

The old graph tables use document IDs as global primary keys.  That makes an
upsert unsafe: a document/edge/chunk removed by a later import remains in the
database.  This module makes a dataset release immutable instead.  Data is
written to a ``staging`` release, validated, then one small row is switched to
``active`` in the same transaction.  Readers use the ``active_*`` views and
therefore never see a partially loaded release.

This layer intentionally does not decide legal validity.  It preserves the
prepared dataset and its manifest so a result can always identify its source
release and parser version.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from psycopg.types.json import Jsonb

from data_pipeline.facets import build_facets
from data_pipeline.tables import TABLE_EXTRACTION_VERSION, extract_html_tables

DATASET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
DATASET_STATUSES = ("staging", "active", "failed", "superseded")


class DatasetLike(Protocol):
    """The narrow GraphDataset contract required by :func:`stage_graph_dataset`."""

    document_nodes: Sequence[Mapping[str, Any]]
    contents: Sequence[Mapping[str, Any]]
    categories: Sequence[Mapping[str, Any]]
    relationships: Sequence[Mapping[str, Any]]
    chunks: Sequence[Mapping[str, Any]]
    legal_units: Sequence[Mapping[str, Any]]
    tables: Sequence[Mapping[str, Any]]
    table_cells: Sequence[Mapping[str, Any]]
    facets: Sequence[Mapping[str, Any]]
    aliases: Sequence[Mapping[str, Any]]
    manifest: Mapping[str, Any]


@dataclass(frozen=True)
class IngestionDataset:
    """Storage-shaped data, kept separate from either legacy or canonical builders."""

    manifest: Mapping[str, Any]
    document_nodes: Sequence[Mapping[str, Any]]
    contents: Sequence[Mapping[str, Any]]
    categories: Sequence[Mapping[str, Any]]
    relationships: Sequence[Mapping[str, Any]]
    chunks: Sequence[Mapping[str, Any]]
    legal_units: Sequence[Mapping[str, Any]]
    tables: Sequence[Mapping[str, Any]]
    table_cells: Sequence[Mapping[str, Any]]
    facets: Sequence[Mapping[str, Any]]
    aliases: Sequence[Mapping[str, Any]]


def dataset_fingerprint(manifest: Mapping[str, Any]) -> str:
    """Return a stable hash of a prepared manifest, excluding its wall-clock time."""

    stable = dict(manifest)
    stable.pop("generated_at_utc", None)
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def new_dataset_id(manifest: Mapping[str, Any]) -> str:
    """Generate a readable, collision-resistant release identifier."""

    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"r{stamp}-{dataset_fingerprint(manifest)[:12]}-{uuid.uuid4().hex[:6]}"


def collection_name_for_dataset(dataset_id: str) -> str:
    """Return a legacy metadata locator for a staged release.

    Semantic vectors are published by ``database/corpus/qdrant_release.py``;
    this value is retained only for immutable manifest compatibility.
    """

    validate_dataset_id(dataset_id)
    return f"legal_graph_chunks__{dataset_id.replace('-', '_')}"


def validate_dataset_id(dataset_id: str) -> None:
    if not DATASET_ID_RE.fullmatch(dataset_id):
        raise ValueError("dataset_id must contain only lowercase letters, digits, '_' or '-' (max 80)")


def _extra_payload(row: Mapping[str, Any], physical_fields: set[str] | frozenset[str]) -> Jsonb:
    """Store only non-column extras instead of duplicating every large value."""

    return Jsonb({key: value for key, value in row.items() if key not in physical_fields})


def _edge_key(row: Mapping[str, Any]) -> str:
    if row.get("relationship_id"):
        return str(row["relationship_id"])
    value = "|".join(str(row.get(key, "")) for key in (
        "source_id", "target_id", "relationship_type", "agent_category",
    ))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_snapshot_to_dataset(snapshot: Any) -> IngestionDataset:
    """Materialize a ``CanonicalSnapshot`` for release storage.

    PostgreSQL contains authority documents and evidence only. Graph-only
    relationship endpoints stay in Neo4j and are not materialized as empty
    PostgreSQL document stubs. Each normalized passage becomes one initial
    chunk; embeddings are left empty for the separate embedding job.
    """

    nodes: dict[str, dict[str, Any]] = {}
    for document in snapshot.documents:
        identifier = str(document["document_id"])
        metadata = dict(document.get("metadata", {}))
        nodes[identifier] = {
            "id": identifier,
            "title": str(metadata.get("title", "")),
            "is_external": False,
            "node_kind": str(document.get("node_kind", "canonical_document")),
            "metadata": metadata,
        }

    relationships: list[dict[str, Any]] = []
    for relationship in snapshot.relationships:
        source_id = str(relationship["source_document_id"])
        target_id = str(relationship["target_document_id"])
        relationships.append({
            "source_id": source_id,
            "target_id": target_id,
            "relationship_type": str(relationship["relationship_type"]),
            "agent_category": ",".join(relationship.get("categories", [])),
            "relationship_id": relationship.get("relationship_id", ""),
            "source_is_selected": bool(relationship.get("source_is_selected", False)),
            "target_is_selected": bool(relationship.get("target_is_selected", False)),
            "relationship_is_adverse": bool(relationship.get("relationship_is_adverse", False)),
            "metadata": dict(relationship),
        })

    contents = [{
        "document_id": str(row["document_id"]),
        "content_text": str(row.get("normalized_text", "")),
        "text_sha256": str(row.get("normalized_text_sha256", "")),
        "raw_html": str(row.get("raw_html", "")),
        "raw_html_sha256": str(row.get("raw_html_sha256", "")),
        "parser_version": str(row.get("parser_version", "")),
        "content_available": bool(row.get("content_available", False)),
    } for row in snapshot.content]
    chunks = [{
        "chunk_id": str(row["passage_id"]),
        "document_id": str(row["document_id"]),
        "chunk_order": int(row["passage_order"]),
        "text": str(row["text"]),
        "embedding_input_text": "\n\n".join(
            part for part in (str(row.get("section_label", "")), str(row.get("text", ""))) if part
        ),
        "embedding_input_sha256": str(row.get("embedding_input_sha256") or hashlib.sha256(
            "\n\n".join(
                part for part in (str(row.get("section_label", "")), str(row.get("text", ""))) if part
            ).encode()
        ).hexdigest()),
        "section_title": str(row.get("section_label", "")),
        "text_sha256": str(row.get("text_sha256", "")),
        "parser_version": str(row.get("parser_version", "")),
        "chunker_version": str(row.get("chunker_version", "")),
        "unit_id": str(row.get("unit_id", "")),
        "source_start": row.get("source_start"),
        "source_end": row.get("source_end"),
        "semantic_eligible": bool(row.get("semantic_eligible", True)),
        "lexical_eligible": bool(row.get("lexical_eligible", True)),
        "passage_kind": str(row.get("passage_kind", "prose")),
        "table_id": str(row.get("table_id", "")),
        "table_row_index": row.get("table_row_index"),
    } for row in snapshot.passages]
    legal_units = [{
        "unit_id": str(row["unit_id"]),
        "document_id": str(row["document_id"]),
        "parent_unit_id": str(row.get("parent_unit_id", "")),
        "unit_type": str(row.get("unit_type", "other")),
        "ordinal_raw": str(row.get("ordinal_raw", "")),
        "label": str(row.get("label", "")),
        "heading": str(row.get("heading", "")),
        # Prose unit text is reconstructed from documents.content_text and the
        # exact offsets at read time. Only derived table text needs storage.
        "text": str(row.get("text", "")) if str(row.get("unit_type", "")) == "table" else "",
        "source_start": row.get("source_start"),
        "source_end": row.get("source_end"),
        "source_selector": str(row.get("source_selector", "")),
        "source_fragment_sha256": str(row.get("source_fragment_sha256", "")),
        "text_sha256": str(row.get("text_sha256", "")),
        "raw_fragment_sha256": str(row.get("raw_fragment_sha256", "")),
        "parse_method": str(row.get("parse_method", "deterministic")),
        "parse_confidence": float(row.get("parse_confidence", 0.0)),
        "parser_version": str(row.get("parser_version", "")),
    } for row in getattr(snapshot, "legal_units", ())]
    tables: list[dict[str, Any]] = []
    table_cells: list[dict[str, Any]] = []
    indexed_document_ids = {
        str(document["document_id"])
        for document in snapshot.documents
        if str(document.get("metadata", {}).get("index_eligible", "true")).strip().casefold()
        not in {"false", "0", "no", "n"}
    }
    for content_row in snapshot.content:
        if str(content_row["document_id"]) not in indexed_document_ids:
            continue
        for table in extract_html_tables(str(content_row["document_id"]), str(content_row.get("raw_html", ""))):
            tables.append({
                "table_id": table.table_id, "document_id": table.document_id,
                "table_ordinal": table.table_ordinal, "source_selector": table.source_selector,
                "source_fragment_sha256": table.source_fragment_sha256,
                "table_text_sha256": table.table_text_sha256, "row_count": table.row_count,
                "column_count": table.column_count, "extraction_version": TABLE_EXTRACTION_VERSION,
            })
            table_cells.extend(table.records)
    return IngestionDataset(
        manifest=dict(snapshot.manifest), document_nodes=tuple(nodes.values()),
        contents=tuple(contents), categories=tuple(snapshot.categories),
        relationships=tuple(relationships), chunks=tuple(chunks), legal_units=tuple(legal_units),
        tables=tuple(tables), table_cells=tuple(table_cells), facets=build_facets(snapshot),
        aliases=tuple(getattr(snapshot, "aliases", ())),
    )


def create_dataset_schema(conn: Any) -> None:
    """Bootstrap a disposable database schema for an explicit local fixture.

    This helper is retained for compatibility tests and fresh fixture setup.
    Production ingestion must use ``database/postgres/migrations/runner.py`` and never
    call this function from a worker/request path.
    ``conn`` follows the psycopg connection/cursor interface; accepting ``Any``
    keeps this function easy to exercise using a recording DB adapter in unit
    tests, without requiring a live PostgreSQL/Supabase database.
    """

    statements = (
        """
        CREATE TABLE IF NOT EXISTS datasets (
            dataset_id TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK (status IN ('staging', 'active', 'failed', 'superseded')),
            manifest JSONB NOT NULL,
            collection_name TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            published_at TIMESTAMPTZ,
            failure_reason TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS dataset_state (
            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
            active_dataset_id TEXT REFERENCES datasets(dataset_id),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        "INSERT INTO dataset_state (singleton) VALUES (TRUE) ON CONFLICT (singleton) DO NOTHING",
        """
        CREATE TABLE IF NOT EXISTS documents (
            dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
            id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            is_external BOOLEAN NOT NULL DEFAULT FALSE,
            content_text TEXT NOT NULL DEFAULT '',
            text_sha256 TEXT NOT NULL DEFAULT '',
            content_available BOOLEAN NOT NULL DEFAULT FALSE,
            raw_html TEXT NOT NULL DEFAULT '',
            raw_html_sha256 TEXT NOT NULL DEFAULT '',
            raw_html_encoding TEXT NOT NULL DEFAULT 'utf-8',
            categories TEXT[] NOT NULL DEFAULT '{}',
            facets JSONB NOT NULL DEFAULT '[]'::jsonb,
            payload JSONB NOT NULL,
            PRIMARY KEY (dataset_id, id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS document_aliases (
            dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
            alias_document_id TEXT NOT NULL,
            canonical_document_id TEXT NOT NULL,
            alias_type TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            evidence_url TEXT NOT NULL DEFAULT '',
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (dataset_id, alias_document_id),
            FOREIGN KEY (dataset_id, canonical_document_id)
                REFERENCES documents(dataset_id, id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS document_tables (
            dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
            table_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            table_ordinal INTEGER NOT NULL,
            source_selector TEXT NOT NULL,
            source_fragment_sha256 TEXT NOT NULL,
            table_text_sha256 TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            column_count INTEGER NOT NULL,
            extraction_version TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (dataset_id, table_id),
            FOREIGN KEY (dataset_id, document_id)
                REFERENCES documents(dataset_id, id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS table_cells (
            dataset_id TEXT NOT NULL,
            table_id TEXT NOT NULL,
            row_index INTEGER NOT NULL,
            column_index INTEGER NOT NULL,
            header TEXT NOT NULL DEFAULT '',
            row_header TEXT NOT NULL DEFAULT '',
            value TEXT NOT NULL DEFAULT '',
            cell_tag TEXT NOT NULL DEFAULT 'td',
            colspan INTEGER NOT NULL DEFAULT 1,
            rowspan INTEGER NOT NULL DEFAULT 1,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (dataset_id, table_id, row_index, column_index),
            FOREIGN KEY (dataset_id, table_id)
                REFERENCES document_tables(dataset_id, table_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS legal_units (
            dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
            unit_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            parent_unit_id TEXT,
            unit_type TEXT NOT NULL,
            ordinal_raw TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            heading TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL DEFAULT '',
            source_start INTEGER,
            source_end INTEGER,
            source_selector TEXT NOT NULL DEFAULT '',
            source_fragment_sha256 TEXT NOT NULL DEFAULT '',
            text_sha256 TEXT NOT NULL DEFAULT '',
            raw_fragment_sha256 TEXT NOT NULL DEFAULT '',
            parse_method TEXT NOT NULL,
            parse_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
            parser_version TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (dataset_id, unit_id),
            FOREIGN KEY (dataset_id, document_id)
                REFERENCES documents(dataset_id, id) ON DELETE CASCADE,
            FOREIGN KEY (dataset_id, parent_unit_id)
                REFERENCES legal_units(dataset_id, unit_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS chunks (
            dataset_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            id TEXT NOT NULL,
            source_key TEXT NOT NULL,
            document_id TEXT NOT NULL,
            chunk_order INTEGER NOT NULL,
            unit_id TEXT NOT NULL,
            source_start INTEGER NOT NULL,
            source_end INTEGER NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            section_title TEXT NOT NULL DEFAULT '',
            text_sha256 TEXT NOT NULL DEFAULT '',
            parser_version TEXT NOT NULL DEFAULT '',
            chunker_version TEXT NOT NULL DEFAULT '',
            lexical_eligible BOOLEAN NOT NULL DEFAULT TRUE,
            semantic_eligible BOOLEAN NOT NULL DEFAULT TRUE,
            embedding_input_text TEXT NOT NULL DEFAULT '',
            embedding_input_sha256 TEXT NOT NULL DEFAULT '',
            embedding_model TEXT,
            embedding_dimensions INTEGER,
            embedding_preprocessor TEXT,
            embedding_normalized BOOLEAN,
            embedded_input_sha256 TEXT,
            embedding_created_at TIMESTAMPTZ,
            search_vector TSVECTOR,
            payload JSONB NOT NULL,
            PRIMARY KEY (dataset_id, chunk_id),
            UNIQUE (id),
            UNIQUE (dataset_id, source_key),
            UNIQUE (dataset_id, document_id, chunk_order),
            FOREIGN KEY (dataset_id, document_id)
                REFERENCES documents(dataset_id, id) ON DELETE CASCADE,
            FOREIGN KEY (dataset_id, unit_id)
                REFERENCES legal_units(dataset_id, unit_id) ON DELETE CASCADE
        )
        """,
        # Forward-compatible migration for databases initialized by an early
        # P0 build that did not yet namespace vector source keys.
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS source_key TEXT",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS id TEXT",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_model TEXT",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_dimensions INTEGER",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_preprocessor TEXT",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_normalized BOOLEAN",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedded_input_sha256 TEXT",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_created_at TIMESTAMPTZ",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS search_vector TSVECTOR",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS section_title TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS unit_id TEXT",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS source_start INTEGER",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS source_end INTEGER",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS text_sha256 TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS parser_version TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS chunker_version TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS lexical_eligible BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS semantic_eligible BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE legal_units ADD COLUMN IF NOT EXISTS source_selector TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE legal_units ADD COLUMN IF NOT EXISTS source_fragment_sha256 TEXT NOT NULL DEFAULT ''",
        "UPDATE chunks SET source_key = dataset_id || ':' || chunk_id WHERE source_key IS NULL",
        "UPDATE chunks SET id = dataset_id || ':' || chunk_id WHERE id IS NULL",
        "ALTER TABLE chunks ALTER COLUMN source_key SET NOT NULL",
        "ALTER TABLE chunks ALTER COLUMN id SET NOT NULL",
        "CREATE INDEX IF NOT EXISTS dataset_nodes_title_idx ON documents (dataset_id, title)",
        "CREATE INDEX IF NOT EXISTS dataset_chunks_search_idx ON chunks USING GIN (search_vector)",
        """
        CREATE OR REPLACE VIEW active_document_nodes WITH (security_invoker = true) AS
        SELECT n.*, r.fingerprint AS dataset_version
        FROM documents n
        JOIN dataset_state runtime ON runtime.singleton
        JOIN datasets r ON r.dataset_id = runtime.active_dataset_id
        WHERE n.dataset_id = runtime.active_dataset_id
        """,
        """
        CREATE OR REPLACE VIEW active_document_content WITH (security_invoker = true) AS
        SELECT d.dataset_id, d.id AS document_id, d.content_text, d.text_sha256,
               d.payload || jsonb_build_object(
                   'content_available', d.content_available
               ) AS payload,
               r.fingerprint AS dataset_version
        FROM documents d
        JOIN dataset_state runtime ON runtime.singleton
        JOIN datasets r ON r.dataset_id = runtime.active_dataset_id
        WHERE d.dataset_id = runtime.active_dataset_id
        """,
        """
        CREATE OR REPLACE VIEW active_document_aliases WITH (security_invoker = true) AS
        SELECT a.*, r.fingerprint AS dataset_version
        FROM document_aliases a
        JOIN dataset_state runtime ON runtime.singleton
        JOIN datasets r ON r.dataset_id = runtime.active_dataset_id
        WHERE a.dataset_id = runtime.active_dataset_id
        """,
        """
        CREATE OR REPLACE VIEW active_document_html WITH (security_invoker = true) AS
        SELECT d.dataset_id, d.id AS document_id, d.raw_html, d.raw_html_sha256,
               d.raw_html_encoding AS encoding, d.payload,
               r.fingerprint AS dataset_version
        FROM documents d
        JOIN dataset_state runtime ON runtime.singleton
        JOIN datasets r ON r.dataset_id = runtime.active_dataset_id
        WHERE d.dataset_id = runtime.active_dataset_id
        """,
        """
        CREATE OR REPLACE VIEW active_document_tables WITH (security_invoker = true) AS
        SELECT t.*, r.fingerprint AS dataset_version
        FROM document_tables t
        JOIN dataset_state runtime ON runtime.singleton
        JOIN datasets r ON r.dataset_id = runtime.active_dataset_id
        WHERE t.dataset_id = runtime.active_dataset_id
        """,
        """
        CREATE OR REPLACE VIEW active_table_cells WITH (security_invoker = true) AS
        SELECT c.*, r.fingerprint AS dataset_version
        FROM table_cells c
        JOIN dataset_state runtime ON runtime.singleton
        JOIN datasets r ON r.dataset_id = runtime.active_dataset_id
        WHERE c.dataset_id = runtime.active_dataset_id
        """,
        """
        CREATE OR REPLACE VIEW active_document_categories WITH (security_invoker = true) AS
        SELECT d.dataset_id, d.id AS document_id, category,
               r.fingerprint AS dataset_version
        FROM documents d
        CROSS JOIN LATERAL unnest(d.categories) AS category
        JOIN dataset_state runtime ON runtime.singleton
        JOIN datasets r ON r.dataset_id = runtime.active_dataset_id
        WHERE d.dataset_id = runtime.active_dataset_id
        """,
        """
        CREATE OR REPLACE VIEW active_legal_units WITH (security_invoker = true) AS
        SELECT u.*, r.fingerprint AS dataset_version
        FROM legal_units u
        JOIN dataset_state runtime ON runtime.singleton
        JOIN datasets r ON r.dataset_id = runtime.active_dataset_id
        WHERE u.dataset_id = runtime.active_dataset_id
        """,
        """
        CREATE OR REPLACE VIEW active_graph_chunks WITH (security_invoker = true) AS
        SELECT c.*, r.fingerprint AS dataset_version
        FROM chunks c
        JOIN dataset_state runtime ON runtime.singleton
        JOIN datasets r ON r.dataset_id = runtime.active_dataset_id
        WHERE c.dataset_id = runtime.active_dataset_id
        """,
    )
    with conn.cursor() as cur:
        # Legacy deployments may have active views with an older column order.
        # Views contain no data and are recreated below, so drop them before
        # CREATE OR REPLACE (which cannot reorder existing view columns).
        for view_name in (
            "active_document_nodes", "active_document_content", "active_document_html", "active_document_tables",
            "active_table_cells", "active_document_aliases", "active_document_categories", "active_legal_units",
            "active_graph_chunks",
        ):
            cur.execute(f"DROP VIEW IF EXISTS {view_name} CASCADE")
        for statement in statements:
            cur.execute(statement)
    conn.commit()


def begin_dataset(conn: Any, manifest: Mapping[str, Any], *, dataset_id: str | None = None) -> str:
    """Create a fresh staging release.

    A matching fingerprint cannot be re-ingested accidentally.  Callers may
    explicitly choose a new release ID only for controlled recovery workflows.
    """

    dataset_id = dataset_id or new_dataset_id(manifest)
    validate_dataset_id(dataset_id)
    fingerprint = dataset_fingerprint(manifest)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO datasets (dataset_id, fingerprint, status, manifest, collection_name)
            VALUES (%s, %s, 'staging', %s, %s)
            """,
            (dataset_id, fingerprint, Jsonb(dict(manifest)), collection_name_for_dataset(dataset_id)),
        )
    return dataset_id


def stage_graph_dataset(conn: Any, dataset_id: str, dataset: DatasetLike) -> None:
    """Stage every prepared record under one release; no existing release is mutated."""

    validate_dataset_id(dataset_id)
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO documents (dataset_id, id, title, is_external, payload)
               VALUES (%s, %s, %s, %s, %s)""",
            [(dataset_id, str(n["id"]), str(n.get("title", "")), bool(n.get("is_external", False)),
              _extra_payload(n, {"id", "title", "is_external"}))
             for n in dataset.document_nodes],
        )
        cur.executemany(
            """INSERT INTO document_aliases
               (dataset_id, alias_document_id, canonical_document_id, alias_type,
                confidence, reason, evidence_url, payload)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            [(
                dataset_id,
                str(row["alias_document_id"]),
                str(row["canonical_document_id"]),
                str(row.get("alias_type", "")),
                str(row.get("confidence", "")),
                str(row.get("reason", "")),
                str(row.get("evidence_url", "")),
                _extra_payload(row, {
                    "alias_document_id", "canonical_document_id", "alias_type",
                    "confidence", "reason", "evidence_url",
                }),
            ) for row in getattr(dataset, "aliases", ())],
        )
        cur.executemany(
            """UPDATE documents
               SET content_text = %s, text_sha256 = %s, content_available = %s,
                   raw_html = %s, raw_html_sha256 = %s, raw_html_encoding = %s
               WHERE dataset_id = %s AND id = %s""",
            [(str(r.get("content_text", "")), str(r.get("text_sha256", "")),
              bool(r.get("content_text", "")), str(r.get("raw_html", "")),
              str(r.get("raw_html_sha256", "")), str(r.get("encoding", "utf-8")),
              dataset_id, str(r["document_id"])) for r in dataset.contents],
        )
        cur.executemany(
            """INSERT INTO document_tables
               (dataset_id, table_id, document_id, table_ordinal, source_selector,
                source_fragment_sha256, table_text_sha256, row_count, column_count,
                extraction_version, payload)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(dataset_id, str(row["table_id"]), str(row["document_id"]), int(row["table_ordinal"]),
              str(row.get("source_selector", "")), str(row.get("source_fragment_sha256", "")),
              str(row.get("table_text_sha256", "")), int(row.get("row_count", 0)), int(row.get("column_count", 0)),
              str(row.get("extraction_version", "")), _extra_payload(row, {
                  "table_id", "document_id", "table_ordinal", "source_selector",
                  "source_fragment_sha256", "table_text_sha256", "row_count",
                  "column_count", "extraction_version",
              })) for row in getattr(dataset, "tables", ())],
        )
        cur.executemany(
            """INSERT INTO table_cells
               (dataset_id, table_id, row_index, column_index, header, row_header, value,
                cell_tag, colspan, rowspan, payload)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(dataset_id, str(row["table_id"]), int(row["row_index"]), int(row["column_index"]),
              str(row.get("header", "")), str(row.get("row_header", "")), str(row.get("value", "")),
              str(row.get("cell_tag", "td")), int(row.get("colspan", 1)), int(row.get("rowspan", 1)),
              _extra_payload(row, {
                  "document_id", "table_id", "table_ordinal", "row_index", "column_index",
                  "header", "row_header", "value", "cell_tag", "colspan", "rowspan",
                  "source_selector", "source_fragment_sha256", "table_text_sha256",
                  "extraction_version",
              }))
             for row in getattr(dataset, "table_cells", ())],
        )
        cur.executemany(
            """UPDATE documents SET categories = %s
               WHERE dataset_id = %s AND id = %s""",
            [(list(categories), dataset_id, document_id)
             for document_id, categories in _group_categories(dataset.categories).items()],
        )
        cur.executemany(
            """UPDATE documents SET facets = %s
               WHERE dataset_id = %s AND id = %s""",
            [(Jsonb(list(facets)), dataset_id, document_id)
             for document_id, facets in _group_facets(getattr(dataset, "facets", ())).items()],
        )
        cur.executemany(
            """INSERT INTO legal_units
               (dataset_id, unit_id, document_id, parent_unit_id, unit_type, ordinal_raw,
                label, heading, text, source_start, source_end, source_selector,
                source_fragment_sha256, text_sha256, raw_fragment_sha256, parse_method,
                parse_confidence, parser_version, payload)
               VALUES (%s, %s, %s, NULLIF(%s, ''), %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(dataset_id, str(r["unit_id"]), str(r["document_id"]), str(r.get("parent_unit_id", "")),
              str(r.get("unit_type", "other")), str(r.get("ordinal_raw", "")), str(r.get("label", "")),
              str(r.get("heading", "")), str(r.get("text", "")), r.get("source_start"), r.get("source_end"),
              str(r.get("source_selector", "")), str(r.get("source_fragment_sha256", "")),
              str(r.get("text_sha256", "")), str(r.get("raw_fragment_sha256", "")), str(r.get("parse_method", "deterministic")),
              float(r.get("parse_confidence", 0.0)), str(r.get("parser_version", "")),
              _extra_payload(r, {
                  "unit_id", "document_id", "parent_unit_id", "unit_type", "ordinal_raw",
                  "label", "heading", "text", "source_start", "source_end", "source_selector",
                  "source_fragment_sha256", "text_sha256", "raw_fragment_sha256", "parse_method",
                  "parse_confidence", "parser_version",
              })) for r in getattr(dataset, "legal_units", ())],
        )
        cur.executemany(
            """INSERT INTO chunks
               (dataset_id, chunk_id, id, source_key, document_id, chunk_order,
                unit_id, source_start, source_end, text, section_title, text_sha256,
                parser_version, chunker_version, lexical_eligible, semantic_eligible,
                embedding_input_text, embedding_input_sha256, payload)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s, %s)""",
            [(dataset_id, str(r["chunk_id"]), f"{dataset_id}:{r['chunk_id']}", f"{dataset_id}:{r['chunk_id']}", str(r["document_id"]), int(r["chunk_order"]),
              str(r.get("unit_id", "")), r.get("source_start"), r.get("source_end"),
              str(r.get("text", "")), str(r.get("section_title", "")), str(r.get("text_sha256", "")),
              str(r.get("parser_version", "")), str(r.get("chunker_version", "")),
              bool(r.get("lexical_eligible", True)), bool(r.get("semantic_eligible", True)),
              str(r.get("embedding_input_text", "")), str(r.get("embedding_input_sha256", "")),
              _extra_payload(r, {
                  "chunk_id", "document_id", "chunk_order", "unit_id", "source_start", "source_end",
                  "text", "section_title", "text_sha256", "parser_version", "chunker_version",
                  "lexical_eligible", "semantic_eligible", "embedding_input_text", "embedding_input_sha256",
              })) for r in dataset.chunks],
        )
        cur.execute(
            """UPDATE chunks
               SET search_vector = CASE WHEN lexical_eligible THEN to_tsvector('simple', text) ELSE NULL END
               WHERE dataset_id = %s""",
            (dataset_id,),
        )


def validate_staged_dataset(
    conn: Any, dataset_id: str, *, require_embeddings: bool = False,
) -> dict[str, int]:
    """Validate the minimum invariants before a release can become visible."""

    validate_dataset_id(dataset_id)
    checks = {
        "documents": "SELECT count(*) FROM documents WHERE dataset_id = %s AND is_external = FALSE",
        "content_available": "SELECT count(*) FROM documents WHERE dataset_id = %s AND content_available",
        "chunks": "SELECT count(*) FROM chunks WHERE dataset_id = %s",
        "legal_units": "SELECT count(*) FROM legal_units WHERE dataset_id = %s",
        "aliases": "SELECT count(*) FROM document_aliases WHERE dataset_id = %s",
    }
    result: dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT status, manifest FROM datasets WHERE dataset_id = %s FOR UPDATE", (dataset_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"Unknown dataset_id: {dataset_id}")
        if row[0] != "staging":
            raise ValueError(f"Release {dataset_id} is {row[0]!r}, not staging")
        manifest = row[1] or {}
        expected = manifest.get("counts", {})
        for name, statement in checks.items():
            cur.execute(statement, (dataset_id,))
            result[name] = int(cur.fetchone()[0])
        if require_embeddings:
            cur.execute(
                """SELECT count(*) FROM chunks
                   WHERE dataset_id = %s AND semantic_eligible AND embedding IS NULL""",
                (dataset_id,),
            )
            result["missing_embeddings"] = int(cur.fetchone()[0])
            cur.execute(
                """SELECT count(*) FROM chunks
                   WHERE dataset_id = %s AND NOT semantic_eligible AND embedding IS NOT NULL""",
                (dataset_id,),
            )
            result["wasted_embeddings"] = int(cur.fetchone()[0])
        cur.execute(
            """SELECT count(*) FROM chunks
               WHERE dataset_id = %s AND text <> '' AND (
                   NULLIF(unit_id, '') IS NULL OR
                   source_start IS NULL OR
                   source_end IS NULL OR
                   source_end <= source_start
               )""",
            (dataset_id,),
        )
        result["missing_chunk_provenance"] = int(cur.fetchone()[0])
        cur.execute(
            """SELECT count(*) FROM documents
               WHERE dataset_id = %s AND content_available AND (
                   content_text = '' OR text_sha256 = '' OR
                   raw_html = '' OR raw_html_sha256 = ''
               )""",
            (dataset_id,),
        )
        result["missing_content_provenance"] = int(cur.fetchone()[0])
        cur.execute(
            """SELECT count(*) FROM chunks
               WHERE dataset_id = %s AND (
                   (semantic_eligible AND NOT lexical_eligible) OR
                   (lexical_eligible AND search_vector IS NULL) OR
                   (NOT lexical_eligible AND search_vector IS NOT NULL)
               )""",
            (dataset_id,),
        )
        result["invalid_retrieval_eligibility"] = int(cur.fetchone()[0])
    expected_keys = {
        "documents": "documents",
        "content_available": "content_available",
        "chunks": "passages",
        "legal_units": "legal_units",
        "aliases": "aliases",
    }
    for name, expected_key in expected_keys.items():
        actual = result[name]
        expected_count = expected.get(expected_key)
        if expected_count is not None and int(expected_count) != actual:
            raise ValueError(
                f"Release {name} count {actual} does not match manifest "
                f"{expected_key}={expected_count}"
            )
    # A table's CSS selector and raw-fragment hash remain exact even when its
    # visible text cannot be mapped to one contiguous normalized-text span.
    # Such tables are reviewable but do not invalidate the whole release.
    chunk_validation = manifest.get("chunk_validation", {})
    if int(chunk_validation.get("oversized_chunks", 0)) or int(chunk_validation.get("missing_source_offsets", 0)):
        raise ValueError("Manifest reports invalid retrieval chunks")
    if result["documents"] == 0 or result["chunks"] == 0:
        raise ValueError("A release needs at least one document and one chunk")
    if result["missing_chunk_provenance"]:
        raise ValueError(f"Release has {result['missing_chunk_provenance']} chunks without source provenance")
    if result["missing_content_provenance"]:
        raise ValueError(
            f"Release has {result['missing_content_provenance']} content documents without source provenance"
        )
    if result["invalid_retrieval_eligibility"]:
        raise ValueError(
            f"Release has {result['invalid_retrieval_eligibility']} invalid retrieval eligibility rows"
        )
    if require_embeddings and result["missing_embeddings"]:
        raise ValueError(f"Release has {result['missing_embeddings']} chunks without embeddings")
    if require_embeddings and result["wasted_embeddings"]:
        raise ValueError(f"Release has {result['wasted_embeddings']} ineligible chunks with embeddings")
    return result


def publish_dataset(conn: Any, dataset_id: str, *, require_embeddings: bool = True) -> None:
    """Atomically make a validated release current.

    The transaction-level advisory lock serializes concurrent ingest jobs.
    Existing readers remain on the previous committed runtime pointer until this
    function commits; no delete-and-reload window exists.
    """

    validate_dataset_id(dataset_id)
    validate_staged_dataset(conn, dataset_id, require_embeddings=require_embeddings)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('data_pipelineset_publication'))")
        cur.execute("UPDATE datasets SET status = 'superseded' WHERE status = 'active'")
        cur.execute(
            "UPDATE datasets SET status = 'active', published_at = now() WHERE dataset_id = %s",
            (dataset_id,),
        )
        cur.execute(
            "UPDATE dataset_state SET active_dataset_id = %s, updated_at = now() WHERE singleton = TRUE",
            (dataset_id,),
        )
    conn.commit()


def _group_categories(rows: Sequence[Mapping[str, Any]]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, set[str]] = {}
    for row in rows:
        document_id = str(row["document_id"])
        category = str(row.get("category", "")).strip()
        if category:
            grouped.setdefault(document_id, set()).add(category)
    return {document_id: tuple(sorted(values)) for document_id, values in grouped.items()}


def _group_facets(rows: Sequence[Mapping[str, Any]]) -> dict[str, tuple[Mapping[str, Any], ...]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        member_id = str(row.get("member_id", "")).strip()
        if member_id:
            grouped.setdefault(member_id, []).append(dict(row))
    return {member_id: tuple(values) for member_id, values in grouped.items()}


def fail_dataset(conn: Any, dataset_id: str, reason: str) -> None:
    """Record a staging failure without touching the active release."""

    validate_dataset_id(dataset_id)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE datasets SET status = 'failed', failure_reason = %s WHERE dataset_id = %s AND status = 'staging'",
            (reason[:4000], dataset_id),
        )
    conn.commit()


def assert_schema_migrated(conn: Any) -> None:
    """Fail closed when a worker is pointed at an un-migrated database.

    DDL is intentionally absent from the ingest transaction. This keeps
    migration authority in the one-shot runner and prevents two workers from
    racing to alter active tables while a release is being staged.
    """
    required = {"datasets", "dataset_state", "documents", "legal_units", "chunks"}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = ANY(%s)
            """,
            (list(required),),
        )
        present = {str(row[0]) for row in cur.fetchall()}
    missing = sorted(required - present)
    if missing:
        raise RuntimeError(
            "Database migrations are incomplete; run database/postgres/migrations/runner.py "
            f"before ingest (missing: {', '.join(missing)})"
        )


def stage_dataset(conn: Any, dataset: DatasetLike, *, dataset_id: str | None = None) -> tuple[str, dict[str, int]]:
    """Validate migration-owned schema and stage an immutable release."""

    assert_schema_migrated(conn)
    dataset_id = begin_dataset(conn, dataset.manifest, dataset_id=dataset_id)
    try:
        stage_graph_dataset(conn, dataset_id, dataset)
        report = validate_staged_dataset(conn, dataset_id)
        conn.commit()
        return dataset_id, report
    except Exception:
        conn.rollback()
        raise


def ingest_dataset(
    conn: Any, dataset: DatasetLike, *, dataset_id: str | None = None,
    require_embeddings: bool = True,
) -> tuple[str, dict[str, int]]:
    """Stage, validate and atomically publish using migration-owned tables.

    On errors the caller gets the exception and the active release is unchanged.
    A failed staging record is retained only when the database transaction can
    be recovered safely by the caller.
    """

    dataset_id, report = stage_dataset(conn, dataset, dataset_id=dataset_id)
    try:
        publish_dataset(conn, dataset_id, require_embeddings=require_embeddings)
        return dataset_id, report
    except Exception:
        conn.rollback()
        # The release insert is part of the rolled-back transaction.  The
        # active pointer remains untouched, which is the safety guarantee.
        raise


def ingest_canonical_snapshot(
    conn: Any, snapshot: Any, *, dataset_id: str | None = None, require_embeddings: bool = True,
) -> tuple[str, dict[str, int]]:
    """Ingest the canonical pipeline output without importing legacy GraphDataset."""

    dataset = canonical_snapshot_to_dataset(snapshot)
    return ingest_dataset(
        conn, dataset, dataset_id=dataset_id or str(snapshot.dataset_id), require_embeddings=require_embeddings,
    )


def stage_canonical_snapshot(conn: Any, snapshot: Any, *, dataset_id: str | None = None) -> tuple[str, dict[str, int]]:
    """Stage a canonical snapshot before embedding and publication."""

    return stage_dataset(conn, canonical_snapshot_to_dataset(snapshot), dataset_id=dataset_id or str(snapshot.dataset_id))


def get_active_dataset(conn: Any) -> dict[str, Any] | None:
    """Return the current release metadata for APIs and response provenance."""

    with conn.cursor() as cur:
        cur.execute(
            """SELECT r.dataset_id, r.fingerprint, r.manifest, r.collection_name, r.published_at
               FROM dataset_state runtime
               JOIN datasets r ON r.dataset_id = runtime.active_dataset_id
               WHERE runtime.singleton = TRUE"""
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "dataset_id": row[0], "dataset_version": row[1], "manifest": row[2],
        "collection_name": row[3], "published_at": row[4],
    }
