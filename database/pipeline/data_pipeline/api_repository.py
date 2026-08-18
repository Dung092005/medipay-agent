"""Read-only PostgreSQL repository used by the HTTP API.

The repository reads immutable release tables selected through
``dataset_state``.  It deliberately has no schema, ingest, update, or delete
operations, so the API process can use a database role with SELECT-only access.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

from data_pipeline.api_models import (
    Category,
    DatasetInfo,
    DocumentResponse,
    LegalUnitResponse,
    TableCellResponse,
    TableResponse,
    RelationshipDirection,
    RelationshipItem,
    SearchHit,
    RetrieveHit,
    StatsResponse,
)


load_dotenv()


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class DatabaseSettings:
    database_url: str | None
    host: str
    port: int
    dbname: str
    user: str
    password: str
    connect_timeout_seconds: int
    statement_timeout_ms: int

    @classmethod
    def from_env(cls) -> DatabaseSettings:
        return cls(
            database_url=os.getenv("DATABASE_URL") or None,
            host=os.getenv("PGHOST", "localhost"),
            port=_positive_int("PGPORT", 5432),
            dbname=os.getenv("PGDATABASE", "postgres"),
            user=os.getenv("PGUSER", "postgres"),
            password=os.getenv("PGPASSWORD", "postgres"),
            connect_timeout_seconds=_positive_int("API_DB_CONNECT_TIMEOUT_SECONDS", 5),
            statement_timeout_ms=_positive_int("API_DB_STATEMENT_TIMEOUT_MS", 15_000),
        )

    def connect(self) -> psycopg.Connection[Any]:
        common: dict[str, Any] = {
            "connect_timeout": self.connect_timeout_seconds,
            "application_name": "bhyt-data-api",
            "options": (
                f"-c statement_timeout={self.statement_timeout_ms} "
                "-c default_transaction_read_only=on"
            ),
        }
        if self.database_url:
            return psycopg.connect(self.database_url, **common)
        return psycopg.connect(
            host=self.host,
            port=self.port,
            dbname=self.dbname,
            user=self.user,
            password=self.password,
            **common,
        )


@dataclass(frozen=True)
class ActiveDataset:
    dataset_id: str
    dataset_version: str
    collection_name: str
    manifest: dict[str, Any]
    published_at: datetime | None

    def public_info(self) -> DatasetInfo:
        manifest = self.manifest
        scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
        manifest_counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
        counts = {
            public: int(manifest[source])
            for public, source in (
                ("canonical_documents", "canonical_document_rows"),
                ("external_nodes", "external_document_stub_rows"),
                ("content_rows", "content_rows"),
                ("relationships", "relationship_rows"),
                ("categories", "category_rows"),
                ("chunks", "chunk_rows"),
            )
            if source in manifest and isinstance(manifest[source], int)
        }
        for public, source in (
            ("canonical_documents", "documents"),
            ("content_rows", "content"),
            ("relationships", "relationships"),
            ("categories", "categories"),
            ("chunks", "passages"),
        ):
            if public not in counts and isinstance(manifest_counts.get(source), int):
                counts[public] = int(manifest_counts[source])
        source_as_of = (
            manifest.get("source_as_of_date")
            or manifest.get("as_of_date")
            or scope.get("as_of_date")
        )
        dimensions = manifest.get("embedding_dimensions")
        return DatasetInfo(
            dataset_id=self.dataset_id,
            dataset_version=self.dataset_version,
            published_at=self.published_at,
            pipeline_version=_optional_text(manifest.get("pipeline_version")),
            source_as_of_date=_optional_text(source_as_of),
            embedding_model=_optional_text(manifest.get("embedding_model")),
            embedding_dimensions=int(dimensions) if isinstance(dimensions, int) else None,
            counts=counts,
        )


@dataclass(frozen=True)
class SearchPage:
    dataset_version: str
    hits: list[SearchHit]


@dataclass(frozen=True)
class RelationshipPage:
    dataset_version: str
    items: list[RelationshipItem]


class ReadRepository(Protocol):
    def ping(self) -> bool: ...

    def current_dataset(self) -> ActiveDataset | None: ...

    def search(
        self,
        vector: Sequence[float],
        *,
        category: Category | None,
        status: str | None,
        limit: int,
    ) -> SearchPage | None: ...

    def exact_search(
        self,
        query: str,
        *,
        category: Category | None,
        status: str | None,
        limit: int,
    ) -> SearchPage | None: ...

    def graph_expand(
        self,
        document_ids: Sequence[str],
        *,
        limit: int,
        reference_date: str | None = None,
        jurisdiction: str | None = None,
    ) -> SearchPage | None: ...

    def lexical_search(
        self,
        query: str,
        *,
        category: Category | None,
        status: str | None,
        limit: int,
    ) -> SearchPage | None: ...

    def get_document(self, document_id: str, *, include_content: bool) -> DocumentResponse | None: ...

    def get_document_html(self, document_id: str) -> tuple[str, str, str] | None: ...

    def get_legal_unit(self, unit_id: str) -> LegalUnitResponse | None: ...

    def get_table(self, table_id: str, *, cell_limit: int) -> TableResponse | None: ...

    def relationships(
        self,
        document_id: str,
        *,
        direction: RelationshipDirection,
        limit: int,
    ) -> RelationshipPage | None: ...

    def stats(self) -> StatsResponse | None: ...


ConnectionFactory = Callable[[], psycopg.Connection[Any]]


class PsycopgReadRepository:
    """Small SQL adapter over the release-scoped storage schema."""

    def __init__(self, connection_factory: ConnectionFactory | None = None) -> None:
        settings = DatabaseSettings.from_env()
        self._connection_factory = connection_factory or settings.connect

    def ping(self) -> bool:
        with self._connection_factory() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone() == (1,)

    def current_dataset(self) -> ActiveDataset | None:
        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            return self._active_dataset(cur)

    @staticmethod
    def _active_dataset(cur: Any) -> ActiveDataset | None:
        cur.execute(
            """
            SELECT r.dataset_id, r.fingerprint AS dataset_version,
                   r.collection_name, r.manifest, r.published_at
            FROM dataset_state runtime
            JOIN datasets r ON r.dataset_id = runtime.active_dataset_id
            WHERE runtime.singleton = TRUE
            """
        )
        row = cur.fetchone()
        if row is None:
            return None
        return ActiveDataset(
            dataset_id=str(row["dataset_id"]),
            dataset_version=str(row["dataset_version"]),
            collection_name=str(row["collection_name"]),
            manifest=dict(row["manifest"] or {}),
            published_at=row["published_at"],
        )

    def search(
        self,
        vector: Sequence[float],
        *,
        category: Category | None,
        status: str | None,
        limit: int,
    ) -> SearchPage | None:
        vector_value = _vector_literal(vector)
        candidate_limit = min(100, limit * 5) if status or category else limit
        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            dataset = self._active_dataset(cur)
            if dataset is None:
                return None

            params: list[Any] = [vector_value]

            category_clause = ""
            status_clause = ""
            params.extend([dataset.dataset_id, dataset.dataset_id])
            if category:
                category_clause = "AND %s = ANY(n.categories)"
                params.append(category.value)
            if status:
                status_clause = "AND COALESCE(n.payload -> 'metadata' ->> 'status_filter', '') = %s"
                params.append(status)
            params.append(limit)
            cur.execute(
                f"""
                SELECT c.chunk_id, c.document_id,
                       1.0 - (c.embedding <=> %s::extensions.vector) AS score, c.text,
                       c.payload AS chunk_payload, n.title, n.is_external,
                       n.payload AS node_payload
                FROM chunks c
                JOIN documents n
                  ON (n.dataset_id, n.id) = (c.dataset_id, c.document_id)
                WHERE c.embedding IS NOT NULL
                  AND c.dataset_id = %s AND n.dataset_id = %s
                  {category_clause}
                  {status_clause}
                ORDER BY c.embedding <=> %s::extensions.vector, c.chunk_id
                LIMIT %s
                """,
                [params[0], *params[1:-1], params[0], params[-1]],
            )
            hits = [_search_hit(row) for row in cur.fetchall()]
        return SearchPage(dataset_version=dataset.dataset_version, hits=hits)

    def exact_search(
        self,
        query: str,
        *,
        category: Category | None,
        status: str | None,
        limit: int,
    ) -> SearchPage | None:
        """Find identifier/title matches without loading the embedding model."""

        needle = query.strip()
        if not needle:
            return None
        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            dataset = self._active_dataset(cur)
            if dataset is None:
                return None
            clauses = [
                "(n.title ILIKE %s OR COALESCE(n.payload -> 'metadata' ->> 'so_ky_hieu', '') ILIKE %s)",
                "n.dataset_id = %s",
            ]
            params: list[Any] = [f"%{needle}%", f"%{needle}%", dataset.dataset_id]
            if category:
                clauses.append(
                    "%s = ANY(n.categories)"
                )
                params.append(category.value)
            if status:
                clauses.append("COALESCE(n.payload -> 'metadata' ->> 'status_filter', '') = %s")
                params.append(status)
            params.append(limit)
            cur.execute(
                f"""
                SELECT c.chunk_id, c.document_id, 1.0::double precision AS score, c.text,
                       c.payload AS chunk_payload, n.title, n.is_external,
                       n.payload AS node_payload
                FROM documents n
                LEFT JOIN LATERAL (
                    SELECT chunk_id, document_id, text, payload
                    FROM chunks
                    WHERE dataset_id = n.dataset_id AND document_id = n.id
                    ORDER BY chunk_order, chunk_id
                    LIMIT 1
                ) c ON TRUE
                WHERE {' AND '.join(clauses)}
                ORDER BY n.is_external, n.title, n.id
                LIMIT %s
                """,
                params,
            )
            hits = [_search_hit(row) for row in cur.fetchall() if row.get("chunk_id")]
        return SearchPage(dataset_version=dataset.dataset_version, hits=hits)

    def graph_expand(
        self,
        document_ids: Sequence[str],
        *,
        limit: int,
        reference_date: str | None = None,
        jurisdiction: str | None = None,
    ) -> SearchPage | None:
        # Graph expansion is intentionally served by Neo4j. The PostgreSQL
        # release reader must never fall back to a legacy relationship table.
        return None

    def lexical_search(
        self,
        query: str,
        *,
        category: Category | None,
        status: str | None,
        limit: int,
    ) -> SearchPage | None:
        needle = query.strip()
        if not needle:
            return None
        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            dataset = self._active_dataset(cur)
            if dataset is None:
                return None
            clauses = ["c.search_vector @@ plainto_tsquery('simple', %s)", "c.dataset_id = %s"]
            params: list[Any] = [needle, dataset.dataset_id]
            if category:
                clauses.append("%s = ANY(n.categories)")
                params.append(category.value)
            if status:
                clauses.append("COALESCE(n.payload -> 'metadata' ->> 'status_filter', '') = %s")
                params.append(status)
            params.append(limit)
            cur.execute(
                f"""
                SELECT c.chunk_id, c.document_id,
                       ts_rank_cd(c.search_vector, plainto_tsquery('simple', %s))::double precision AS score,
                       c.text,
                       c.payload AS chunk_payload, n.title, n.is_external,
                       n.payload AS node_payload
                FROM chunks c
                JOIN documents n ON (n.dataset_id, n.id) = (c.dataset_id, c.document_id)
                WHERE {' AND '.join(clauses)}
                ORDER BY score DESC, c.document_id, c.chunk_order, c.chunk_id
                LIMIT %s
                """,
                [params[0], params[0], *params[1:-1], params[-1]],
            )
            hits = [_search_hit(row) for row in cur.fetchall()]
        return SearchPage(dataset_version=dataset.dataset_version, hits=hits)

    def get_document(self, document_id: str, *, include_content: bool) -> DocumentResponse | None:
        content_column = "c.content_text" if include_content else "NULL::TEXT"
        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT n.dataset_version, n.id, n.title, n.is_external, n.payload,
                       {content_column} AS content_text, c.payload AS content_payload,
                       COALESCE(
                           (SELECT array_agg(category ORDER BY category)
                            FROM active_document_categories categories
                            WHERE categories.document_id = n.id),
                           ARRAY[]::TEXT[]
                       ) AS categories
                FROM active_document_nodes n
                LEFT JOIN active_document_content c ON c.document_id = n.id
                WHERE n.id = %s
                """,
                (document_id,),
            )
            row = cur.fetchone()
        return _document_response(row) if row is not None else None

    def get_document_html(self, document_id: str) -> tuple[str, str, str] | None:
        """Return the immutable raw HTML and its release/hash provenance."""

        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT dataset_version, raw_html, raw_html_sha256
                FROM active_document_html
                WHERE document_id = %s
                """,
                (document_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return str(row["dataset_version"]), str(row["raw_html"]), str(row["raw_html_sha256"])

    def get_legal_unit(self, unit_id: str) -> LegalUnitResponse | None:
        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT dataset_version, unit_id, document_id, parent_unit_id, unit_type,
                       ordinal_raw, label, heading, text, source_start, source_end,
                       text_sha256, parser_version
                FROM active_legal_units
                WHERE unit_id = %s
                """,
                (unit_id,),
            )
            row = cur.fetchone()
        return LegalUnitResponse(**dict(row)) if row is not None else None

    def get_table(self, table_id: str, *, cell_limit: int) -> TableResponse | None:
        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT dataset_version, table_id, document_id, table_ordinal,
                       source_selector, source_fragment_sha256, table_text_sha256,
                       row_count, column_count, extraction_version
                FROM active_document_tables
                WHERE table_id = %s
                """,
                (table_id,),
            )
            table = cur.fetchone()
            if table is None:
                return None
            cur.execute(
                """
                SELECT row_index, column_index, header, row_header, value,
                       cell_tag, colspan, rowspan
                FROM active_table_cells
                WHERE table_id = %s
                ORDER BY row_index, column_index
                LIMIT %s
                """,
                (table_id, cell_limit),
            )
            cells = [TableCellResponse(**dict(row)) for row in cur.fetchall()]
        return TableResponse(**dict(table), cells=cells)

    def relationships(
        self,
        document_id: str,
        *,
        direction: RelationshipDirection,
        limit: int,
    ) -> RelationshipPage | None:
        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            dataset = self._active_dataset(cur)
            if dataset is None:
                return None
        return RelationshipPage(dataset_version=dataset.dataset_version, items=[])

    def stats(self) -> StatsResponse | None:
        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT r.fingerprint AS dataset_version,
                    (SELECT count(*) FROM documents n
                     WHERE n.dataset_id = r.dataset_id AND NOT n.is_external) AS canonical_nodes,
                    (SELECT count(*) FROM documents n
                     WHERE n.dataset_id = r.dataset_id AND n.is_external) AS external_nodes,
                    (SELECT count(*) FROM documents c
                     WHERE c.dataset_id = r.dataset_id) AS content_rows,
                    (SELECT count(*) FROM documents c
                     WHERE c.dataset_id = r.dataset_id AND c.content_available) AS available_content,
                    (SELECT COALESCE(sum(cardinality(c.categories)), 0) FROM documents c
                     WHERE c.dataset_id = r.dataset_id) AS category_rows,
                    0 AS relationship_rows,
                    0 AS adverse_edges,
                    (SELECT count(*) FROM chunks c
                     WHERE c.dataset_id = r.dataset_id) AS chunk_rows
                FROM dataset_state runtime
                JOIN datasets r ON r.dataset_id = runtime.active_dataset_id
                WHERE runtime.singleton = TRUE
                """
            )
            row = cur.fetchone()
        if row is None:
            return None
        return StatsResponse(**row)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _payload_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _nested_dict(value: Any, key: str) -> dict[str, Any]:
    nested = value.get(key) if isinstance(value, dict) else None
    return dict(nested) if isinstance(nested, dict) else {}


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y", "có", "co"}
    return bool(value)


def _vector_literal(values: Sequence[float]) -> str:
    if not values:
        raise ValueError("embedding provider returned an empty vector")
    normalized = [float(value) for value in values]
    if any(not math.isfinite(value) for value in normalized):
        raise ValueError("embedding provider returned a non-finite vector")
    return "[" + ",".join(format(value, ".10g") for value in normalized) + "]"


def _search_hit(row: dict[str, Any]) -> SearchHit:
    chunk = _payload_dict(row.get("chunk_payload"))
    chunk_metadata = _nested_dict(chunk, "metadata")
    node = _payload_dict(row.get("node_payload"))
    node_metadata = _nested_dict(node, "metadata")
    return SearchHit(
        chunk_id=str(row["chunk_id"]),
        document_id=str(row["document_id"]),
        score=float(row["score"]),
        section_title=str(chunk.get("section_title") or ""),
        text=str(row.get("text") or ""),
        title=str(row.get("title") or ""),
        so_ky_hieu=str(node_metadata.get("so_ky_hieu") or node.get("so_ky_hieu") or ""),
        status=str(
            chunk_metadata.get("status_filter")
            or node_metadata.get("status_filter")
            or node.get("status_filter")
            or ""
        ),
        node_kind="external" if row.get("is_external") else "canonical",
        citation={
            key: str(row[key]) for key in ("relationship_type", "relationship_source_id", "relationship_target_id")
            if row.get(key) is not None
        },
        unit_id=str(chunk.get("unit_id")) if chunk.get("unit_id") else None,
        source_start=int(chunk["source_start"]) if chunk.get("source_start") is not None else None,
        source_end=int(chunk["source_end"]) if chunk.get("source_end") is not None else None,
    )


def _document_response(row: dict[str, Any]) -> DocumentResponse:
    payload = _payload_dict(row.get("payload"))
    metadata = _nested_dict(payload, "metadata")
    content_payload = _payload_dict(row.get("content_payload"))
    is_external = bool(row.get("is_external"))
    resolution_status = (
        str(
            metadata.get("resolution_status")
            or metadata.get("external_lookup_status")
            or "unresolved"
        )
        if is_external
        else "canonical"
    )
    return DocumentResponse(
        dataset_version=str(row["dataset_version"]),
        id=str(row["id"]),
        title=str(row.get("title") or ""),
        so_ky_hieu=str(metadata.get("so_ky_hieu") or payload.get("so_ky_hieu") or ""),
        node_kind="external" if is_external else "canonical",
        resolution_status=resolution_status,
        categories=[str(category) for category in (row.get("categories") or [])],
        ngay_ban_hanh=_optional_text(metadata.get("ngay_ban_hanh") or payload.get("ngay_ban_hanh")),
        ngay_co_hieu_luc=_optional_text(
            metadata.get("ngay_co_hieu_luc") or payload.get("ngay_co_hieu_luc")
        ),
        ngay_het_hieu_luc=_optional_text(
            metadata.get("ngay_het_hieu_luc") or payload.get("ngay_het_hieu_luc")
        ),
        tinh_trang_hieu_luc=str(
            metadata.get("tinh_trang_hieu_luc") or payload.get("tinh_trang_hieu_luc") or ""
        ),
        status_filter=str(metadata.get("status_filter") or payload.get("status_filter") or ""),
        pham_vi=str(metadata.get("pham_vi") or payload.get("pham_vi") or ""),
        linh_vuc=str(metadata.get("linh_vuc") or payload.get("linh_vuc") or ""),
        co_quan_ban_hanh=str(
            metadata.get("co_quan_ban_hanh") or payload.get("co_quan_ban_hanh") or ""
        ),
        content_available=_bool(content_payload.get("content_available")),
        content_text=row.get("content_text"),
        metadata=metadata,
    )


def _relationship_item(row: dict[str, Any]) -> RelationshipItem:
    payload = _payload_dict(row.get("payload"))
    metadata = _nested_dict(payload, "metadata")
    return RelationshipItem(
        edge_key=str(row["edge_key"]),
        source_id=str(row["source_id"]),
        target_id=str(row["target_id"]),
        relationship_type=str(row.get("relationship_type") or ""),
        relationship_is_adverse=_bool(
            metadata.get("relationship_is_adverse", payload.get("relationship_is_adverse"))
        ),
        source_title=str(
            row.get("source_title")
            or metadata.get("source_title_raw")
            or payload.get("source_title")
            or ""
        ),
        target_title=str(
            row.get("target_title")
            or metadata.get("target_title_raw")
            or payload.get("target_title")
            or ""
        ),
    )
