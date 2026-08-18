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
from datetime import datetime, timezone
from typing import Any, Protocol

from psycopg.types.json import Jsonb
from psycopg import sql

from data_pipeline.facets import build_facets
from data_pipeline.tables import extract_html_tables


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


def dataset_fingerprint(manifest: Mapping[str, Any]) -> str:
    """Return a stable hash of a prepared manifest, excluding its wall-clock time."""

    stable = dict(manifest)
    stable.pop("generated_at_utc", None)
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def new_dataset_id(manifest: Mapping[str, Any]) -> str:
    """Generate a readable, collision-resistant release identifier."""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"r{stamp}-{dataset_fingerprint(manifest)[:12]}-{uuid.uuid4().hex[:6]}"


def collection_name_for_dataset(dataset_id: str) -> str:
    """Return the release-scoped vector index name used by Supabase/pgvector."""

    validate_dataset_id(dataset_id)
    return f"legal_graph_chunks__{dataset_id.replace('-', '_')}"


def validate_dataset_id(dataset_id: str) -> None:
    if not DATASET_ID_RE.fullmatch(dataset_id):
        raise ValueError("dataset_id must contain only lowercase letters, digits, '_' or '-' (max 80)")


def _payload(row: Mapping[str, Any]) -> Jsonb:
    return Jsonb(dict(row))


def _edge_key(row: Mapping[str, Any]) -> str:
    if row.get("relationship_id"):
        return str(row["relationship_id"])
    value = "|".join(str(row.get(key, "")) for key in (
        "source_id", "target_id", "relationship_type", "agent_category",
    ))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_snapshot_to_dataset(snapshot: Any) -> IngestionDataset:
    """Materialize a ``CanonicalSnapshot`` for release storage.

    The canonical layer deliberately contains only authority documents.  This
    adapter creates bounded external-reference nodes for relationship endpoints
    outside that set, so relational foreign keys always resolve while retaining
    an explicit ``is_external`` boundary.  Each normalized passage becomes one
    initial graph chunk.  Embeddings are intentionally left empty for the
    separate embedding job.
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
        for identifier, raw_title in (
            (source_id, relationship.get("source_title_raw", "")),
            (target_id, relationship.get("target_title_raw", "")),
        ):
            if identifier not in nodes:
                nodes[identifier] = {
                    "id": identifier,
                    "title": str(raw_title),
                    "is_external": True,
                    "node_kind": "external_reference",
                    "metadata": {"resolution_status": "relationship_endpoint_only"},
                }
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
        "embedding_input_text": "",
        "embedding_input_sha256": "",
        "section_title": str(row.get("section_label", "")),
        "text_sha256": str(row.get("text_sha256", "")),
        "parser_version": str(row.get("parser_version", "")),
        "chunker_version": str(row.get("chunker_version", "")),
        "unit_id": str(row.get("unit_id", "")),
        "source_start": row.get("source_start"),
        "source_end": row.get("source_end"),
    } for row in snapshot.passages]
    legal_units = [{
        "unit_id": str(row["unit_id"]),
        "document_id": str(row["document_id"]),
        "parent_unit_id": str(row.get("parent_unit_id", "")),
        "unit_type": str(row.get("unit_type", "other")),
        "ordinal_raw": str(row.get("ordinal_raw", "")),
        "label": str(row.get("label", "")),
        "heading": str(row.get("heading", "")),
        "text": str(row.get("text", "")),
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
    for content_row in snapshot.content:
        for table in extract_html_tables(str(content_row["document_id"]), str(content_row.get("raw_html", ""))):
            tables.append({
                "table_id": table.table_id, "document_id": table.document_id,
                "table_ordinal": table.table_ordinal, "source_selector": table.source_selector,
                "source_fragment_sha256": table.source_fragment_sha256,
                "table_text_sha256": table.table_text_sha256, "row_count": table.row_count,
                "column_count": table.column_count, "extraction_version": "html-tables-deterministic-v1",
            })
            table_cells.extend(table.records)
    return IngestionDataset(
        manifest=dict(snapshot.manifest), document_nodes=tuple(nodes.values()),
        contents=tuple(contents), categories=tuple(snapshot.categories),
        relationships=tuple(relationships), chunks=tuple(chunks), legal_units=tuple(legal_units),
        tables=tuple(tables), table_cells=tuple(table_cells), facets=build_facets(snapshot),
    )


def create_dataset_schema(conn: Any) -> None:
    """Create idempotent release tables and active-release views.

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
            text TEXT NOT NULL DEFAULT '',
            section_title TEXT NOT NULL DEFAULT '',
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
                REFERENCES documents(dataset_id, id) ON DELETE CASCADE
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
        "UPDATE chunks SET source_key = dataset_id || ':' || chunk_id WHERE source_key IS NULL",
        "UPDATE chunks SET id = dataset_id || ':' || chunk_id WHERE id IS NULL",
        "ALTER TABLE chunks ALTER COLUMN source_key SET NOT NULL",
        "ALTER TABLE chunks ALTER COLUMN id SET NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS dataset_chunks_source_key_idx ON chunks (dataset_id, source_key)",
        "CREATE UNIQUE INDEX IF NOT EXISTS dataset_chunks_id_idx ON chunks (id)",
        "CREATE INDEX IF NOT EXISTS dataset_nodes_title_idx ON documents (dataset_id, title)",
        "CREATE INDEX IF NOT EXISTS dataset_chunks_document_idx ON chunks (dataset_id, document_id, chunk_order)",
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
               d.payload, r.fingerprint AS dataset_version
        FROM documents d
        JOIN dataset_state runtime ON runtime.singleton
        JOIN datasets r ON r.dataset_id = runtime.active_dataset_id
        WHERE d.dataset_id = runtime.active_dataset_id
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
            "active_table_cells", "active_document_categories", "active_legal_units",
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
            [(dataset_id, str(n["id"]), str(n.get("title", "")), bool(n.get("is_external", False)), _payload(n))
             for n in dataset.document_nodes],
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
              str(row.get("extraction_version", "")), _payload(row)) for row in getattr(dataset, "tables", ())],
        )
        cur.executemany(
            """INSERT INTO table_cells
               (dataset_id, table_id, row_index, column_index, header, row_header, value,
                cell_tag, colspan, rowspan, payload)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(dataset_id, str(row["table_id"]), int(row["row_index"]), int(row["column_index"]),
              str(row.get("header", "")), str(row.get("row_header", "")), str(row.get("value", "")),
              str(row.get("cell_tag", "td")), int(row.get("colspan", 1)), int(row.get("rowspan", 1)), _payload(row))
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
                label, heading, text, source_start, source_end, text_sha256,
                raw_fragment_sha256, parse_method, parse_confidence, parser_version, payload)
               VALUES (%s, %s, %s, NULLIF(%s, ''), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(dataset_id, str(r["unit_id"]), str(r["document_id"]), str(r.get("parent_unit_id", "")),
              str(r.get("unit_type", "other")), str(r.get("ordinal_raw", "")), str(r.get("label", "")),
              str(r.get("heading", "")), str(r.get("text", "")), r.get("source_start"), r.get("source_end"),
              str(r.get("text_sha256", "")), str(r.get("raw_fragment_sha256", "")), str(r.get("parse_method", "deterministic")),
              float(r.get("parse_confidence", 0.0)), str(r.get("parser_version", "")), _payload(r)) for r in getattr(dataset, "legal_units", ())],
        )
        cur.executemany(
            """INSERT INTO chunks
               (dataset_id, chunk_id, id, source_key, document_id, chunk_order, text, section_title, embedding_input_text,
                embedding_input_sha256, payload) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(dataset_id, str(r["chunk_id"]), f"{dataset_id}:{r['chunk_id']}", f"{dataset_id}:{r['chunk_id']}", str(r["document_id"]), int(r["chunk_order"]),
              str(r.get("text", "")), str(r.get("section_title", "")), str(r.get("embedding_input_text", "")),
             str(r.get("embedding_input_sha256", "")), _payload(r)) for r in dataset.chunks],
        )
        cur.execute(
            "UPDATE chunks SET search_vector = to_tsvector('simple', text) WHERE dataset_id = %s",
            (dataset_id,),
        )


def validate_staged_dataset(
    conn: Any, dataset_id: str, *, require_embeddings: bool = False,
) -> dict[str, int]:
    """Validate the minimum invariants before a release can become visible."""

    validate_dataset_id(dataset_id)
    checks = {
        "documents": "SELECT count(*) FROM documents WHERE dataset_id = %s AND is_external = FALSE",
        "chunks": "SELECT count(*) FROM chunks WHERE dataset_id = %s",
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
                   WHERE dataset_id = %s AND embedding IS NULL""",
                (dataset_id,),
            )
            result["missing_embeddings"] = int(cur.fetchone()[0])
        cur.execute(
            """SELECT count(*) FROM chunks
               WHERE dataset_id = %s AND text <> '' AND (
                   NULLIF(payload->>'unit_id', '') IS NULL OR
                   payload->>'source_start' IS NULL OR
                   payload->>'source_end' IS NULL OR
                   (payload->>'source_end')::bigint <= (payload->>'source_start')::bigint
               )""",
            (dataset_id,),
        )
        result["missing_chunk_provenance"] = int(cur.fetchone()[0])
    for name, actual in (("documents", result["documents"]), ("chunks", result["chunks"])):
        expected_count = expected.get(name)
        if expected_count is not None and int(expected_count) != actual:
            raise ValueError(f"Release {name} count {actual} does not match manifest {expected_count}")
    if int(expected.get("table_source_span_fallbacks", 0)):
        raise ValueError("Release contains table units without exact source spans")
    chunk_validation = manifest.get("chunk_validation", {})
    if int(chunk_validation.get("oversized_chunks", 0)) or int(chunk_validation.get("missing_source_offsets", 0)):
        raise ValueError("Manifest reports invalid retrieval chunks")
    if result["documents"] == 0 or result["chunks"] == 0:
        raise ValueError("A release needs at least one document and one chunk")
    if result["missing_chunk_provenance"]:
        raise ValueError(f"Release has {result['missing_chunk_provenance']} chunks without source provenance")
    if require_embeddings and result["missing_embeddings"]:
        raise ValueError(f"Release has {result['missing_embeddings']} chunks without embeddings")
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


def ensure_dataset_vector_collection(conn: Any, dataset_id: str, *, dimensions: int) -> str:
    """Create the release-scoped pgvector column and HNSW index.

    Supabase exposes PostgreSQL plus pgvector rather than a separate
    collection API.  The release id remains part of every row and every active
    view, so vector retrieval cannot cross release boundaries.
    """

    validate_dataset_id(dataset_id)
    if dimensions <= 0:
        raise ValueError("dimensions must be positive")
    collection = collection_name_for_dataset(dataset_id)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM datasets WHERE dataset_id = %s", (dataset_id,))
        if cur.fetchone() is None:
            raise ValueError(f"Unknown dataset_id: {dataset_id}")
        # A staging release created by an earlier implementation may carry a
        # legacy collection name with hyphens.  It has not been activated, so
        # correcting this metadata is safe and makes a retry idempotent.
        cur.execute(
            "UPDATE datasets SET collection_name = %s WHERE dataset_id = %s AND status = 'staging'",
            (collection, dataset_id),
        )
        cur.execute("CREATE SCHEMA IF NOT EXISTS extensions")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions")
        cur.execute("ALTER EXTENSION vector SET SCHEMA extensions")
        cur.execute(f"ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding extensions.vector({dimensions})")
        cur.execute("""SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = 'chunks' AND a.attname = 'embedding' AND a.attnum > 0""")
        current_type = cur.fetchone()
        if not current_type or current_type[0] != f"vector({dimensions})":
            cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'chunks' AND indexdef ILIKE '%embedding%'")
            for (old_index_name,) in cur.fetchall():
                cur.execute(sql.SQL("DROP INDEX IF EXISTS {} CASCADE").format(sql.Identifier(old_index_name)))
            cur.execute("DROP VIEW IF EXISTS active_graph_chunks CASCADE")
            # Existing deployments may have a vector column from the previous
            # local model. Changing dimensions requires a full rebuild.
            cur.execute(f"ALTER TABLE chunks ALTER COLUMN embedding TYPE extensions.vector({dimensions}) USING NULL")
        index_name = f"dataset_chunks_embedding_hnsw_{hashlib.sha256(dataset_id.encode()).hexdigest()[:12]}"
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} "
            "ON chunks USING hnsw (embedding extensions.vector_cosine_ops) "
            "WHERE dataset_id = '" + dataset_id + "'"
        )
        cur.execute("""CREATE OR REPLACE VIEW active_graph_chunks WITH (security_invoker = true) AS
            SELECT c.*, r.fingerprint AS dataset_version
            FROM chunks c
            JOIN dataset_state runtime ON runtime.singleton
            JOIN datasets r ON r.dataset_id = runtime.active_dataset_id
            WHERE c.dataset_id = runtime.active_dataset_id""")
    conn.commit()
    return collection


def fail_dataset(conn: Any, dataset_id: str, reason: str) -> None:
    """Record a staging failure without touching the active release."""

    validate_dataset_id(dataset_id)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE datasets SET status = 'failed', failure_reason = %s WHERE dataset_id = %s AND status = 'staging'",
            (reason[:4000], dataset_id),
        )
    conn.commit()


def stage_dataset(conn: Any, dataset: DatasetLike, *, dataset_id: str | None = None) -> tuple[str, dict[str, int]]:
    """Create and validate an immutable staging release, without exposing it."""

    create_dataset_schema(conn)
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
    """Perform schema setup, stage, validate and atomic publication.

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
