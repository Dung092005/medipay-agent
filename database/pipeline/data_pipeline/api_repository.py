"""Read-only PostgreSQL repository used by the HTTP API.

The repository reads immutable release tables selected through
``dataset_state``.  It deliberately has no schema, ingest, update, or delete
operations, so the API process can use a database role with SELECT-only access.
"""

from __future__ import annotations

import logging
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
    RelationshipDirection,
    RelationshipItem,
    SearchHit,
    StatsResponse,
    TableCellResponse,
    TableResponse,
)

load_dotenv()

LOGGER = logging.getLogger("data_pipeline.api_repository")


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
            "options": (f"-c statement_timeout={self.statement_timeout_ms} -c default_transaction_read_only=on"),
        }
        if self.database_url:
            # Supabase projects often share a SQLAlchemy URL such as
            # ``postgresql+asyncpg://`` in .env. psycopg accepts the matching
            # libpq scheme, not SQLAlchemy's driver suffix.
            database_url = self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
            return psycopg.connect(database_url, **common)
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
        source_as_of = manifest.get("source_as_of_date") or manifest.get("as_of_date") or scope.get("as_of_date")
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
        query: str,
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

    @staticmethod
    def _neo4j_rows(statement: str, **parameters: Any) -> list[dict[str, Any]] | None:
        """Run one bounded, release-filtered read query, or degrade safely.

        The PostgreSQL repository remains the authority for active release and
        hydrated text. Neo4j is only a navigation adapter and is initialized
        lazily so a graph outage cannot prevent direct retrieval.
        """

        uri = os.getenv("NEO4J_URI", "").strip()
        password = os.getenv("NEO4J_PASSWORD", "").strip()
        if not uri or not password:
            return None
        try:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(
                uri,
                auth=(os.getenv("NEO4J_USERNAME", "neo4j"), password),
                connection_timeout=5,
            )
            try:
                with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
                    return [dict(record) for record in session.run(statement, **parameters)]
            finally:
                driver.close()
        except Exception as error:
            LOGGER.warning("neo4j read unavailable: %s", type(error).__name__)
            return None

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
            params.append(candidate_limit)
            cur.execute(
                f"""
                SELECT c.chunk_id, c.document_id,
                       1.0 - (c.embedding <=> %s::extensions.vector) AS score, c.text,
                       c.payload AS chunk_payload, c.section_title, c.unit_id,
                       c.source_start, c.source_end, n.title, n.is_external,
                       n.payload AS node_payload
                FROM chunks c
                JOIN documents n
                  ON (n.dataset_id, n.id) = (c.dataset_id, c.document_id)
                WHERE c.embedding IS NOT NULL AND c.semantic_eligible
                  AND c.dataset_id = %s AND n.dataset_id = %s
                  {category_clause}
                  {status_clause}
                ORDER BY c.embedding <=> %s::extensions.vector, c.chunk_id
                LIMIT %s
                """,
                [params[0], *params[1:-1], params[0], params[-1]],
            )
            hits = [_search_hit(row) for row in cur.fetchall()[:limit]]
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

        needle = query.replace("Ð", "Đ").replace("ð", "đ").strip()
        compact_needle = needle.replace("-", "").replace("/", "")
        if not needle:
            return None
        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            dataset = self._active_dataset(cur)
            if dataset is None:
                return None
            clauses = [
                """(n.title ILIKE %s
                    OR COALESCE(n.payload -> 'metadata' ->> 'so_ky_hieu', '') ILIKE %s
                    OR regexp_replace(COALESCE(n.payload -> 'metadata' ->> 'so_ky_hieu', ''), '[-/]', '', 'g') ILIKE %s
                    OR EXISTS (
                        SELECT 1 FROM active_document_aliases a
                        WHERE a.canonical_document_id = n.id
                          AND (a.alias_document_id ILIKE %s
                               OR COALESCE(a.payload -> 'metadata' ->> 'alias_signature', '') ILIKE %s
                               OR regexp_replace(COALESCE(a.payload -> 'metadata' ->> 'alias_signature', ''), '[-/]', '', 'g') ILIKE %s)
                    ))""",
                "n.dataset_id = %s",
            ]
            params: list[Any] = [
                f"%{needle}%",
                f"%{needle}%",
                compact_needle,
                f"%{needle}%",
                f"%{needle}%",
                compact_needle,
                dataset.dataset_id,
            ]
            if category:
                clauses.append("%s = ANY(n.categories)")
                params.append(category.value)
            if status:
                clauses.append("COALESCE(n.payload -> 'metadata' ->> 'status_filter', '') = %s")
                params.append(status)
            params.append(limit)
            cur.execute(
                f"""
                SELECT c.chunk_id, c.document_id, 1.0::double precision AS score, c.text,
                       c.payload AS chunk_payload, c.section_title, c.unit_id,
                       c.source_start, c.source_end, n.title, n.is_external,
                       n.payload AS node_payload
                FROM documents n
                LEFT JOIN LATERAL (
                    SELECT chunk_id, document_id, text, payload, section_title,
                           unit_id, source_start, source_end
                    FROM chunks
                    WHERE dataset_id = n.dataset_id AND document_id = n.id
                    ORDER BY chunk_order, chunk_id
                    LIMIT 1
                ) c ON TRUE
                WHERE {" AND ".join(clauses)}
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
        query: str,
        limit: int,
        reference_date: str | None = None,
        jurisdiction: str | None = None,
    ) -> SearchPage | None:
        seed_ids = list(dict.fromkeys(identifier.strip() for identifier in document_ids if identifier.strip()))
        if not seed_ids or limit <= 0:
            return None
        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            dataset = self._active_dataset(cur)
        if dataset is None:
            return None
        # One bounded hop is deliberately used here. A second hop belongs to a
        # planned/decomposed temporal/relational query, not ordinary recall.
        graph_rows = self._neo4j_rows(
            """MATCH (seed:Document {dataset_id:$dataset_id})-[rel]-(related:Document {dataset_id:$dataset_id})
               WHERE seed.id IN $seed_ids
                 AND NOT related.id IN $seed_ids
                 AND related.node_kind = 'canonical_document'
                 AND type(rel) <> 'ALIAS_OF'
                 AND rel.serving_status = 'approved_evidence'
               WITH related, rel,
                    1.0
                    + CASE WHEN coalesce(rel.evidence_text, '') <> '' THEN 0.20 ELSE 0.0 END
                    + CASE WHEN rel.relation_status = 'candidate_grounded_official_target' THEN 0.15 ELSE 0.0 END
                    + CASE WHEN rel.adverse THEN 0.05 ELSE 0.0 END AS graph_score
               ORDER BY graph_score DESC, rel.relationship_id ASC
               WITH related, collect({
                   relationship_id: rel.relationship_id,
                   relationship_type: rel.relationship_type,
                   source_id: startNode(rel).id,
                   target_id: endNode(rel).id,
                   adverse: rel.adverse,
                   evidence_sha256: rel.evidence_sha256,
                   relation_status: rel.relation_status,
                   scope: rel.scope,
                   target_official_url: rel.target_official_url
               })[0] AS edge, max(graph_score) AS graph_score
               RETURN related.id AS document_id, graph_score,
                      edge.relationship_id AS relationship_id,
                      edge.relationship_type AS relationship_type,
                      edge.source_id AS relationship_source_id,
                      edge.target_id AS relationship_target_id,
                      edge.adverse AS relationship_is_adverse,
                      edge.evidence_sha256 AS evidence_sha256,
                      edge.relation_status AS relation_status,
                      edge.scope AS relation_scope,
                      edge.target_official_url AS target_official_url
               ORDER BY graph_score DESC, document_id ASC
               LIMIT $limit""",
            dataset_id=dataset.dataset_id,
            seed_ids=seed_ids[:100],
            limit=min(limit, 40),
        )
        if graph_rows is None:
            return None
        if not graph_rows:
            return SearchPage(dataset_version=dataset.dataset_version, hits=[])
        graph_by_document = {str(row["document_id"]): row for row in graph_rows}
        candidate_ids = list(graph_by_document)
        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT DISTINCT ON (candidate.document_id)
                       candidate.document_id AS graph_document_id,
                       candidate.ordinality AS graph_rank,
                       c.chunk_id, c.document_id, c.text, c.payload AS chunk_payload,
                       c.section_title, c.unit_id, c.source_start, c.source_end,
                       n.title, n.is_external, n.payload AS node_payload,
                       CASE WHEN c.search_vector @@ plainto_tsquery('simple', %s)
                            THEN ts_rank_cd(c.search_vector, plainto_tsquery('simple', %s))
                            ELSE 0.0 END::double precision AS score
                   FROM unnest(%s::text[]) WITH ORDINALITY AS candidate(document_id, ordinality)
                   JOIN chunks c ON c.dataset_id=%s AND c.document_id=candidate.document_id
                   JOIN documents n ON (n.dataset_id, n.id)=(c.dataset_id, c.document_id)
                   WHERE c.lexical_eligible
                   ORDER BY candidate.document_id, score DESC, c.semantic_eligible DESC,
                            c.chunk_order, c.chunk_id""",
                (query, query, candidate_ids, dataset.dataset_id),
            )
            rows = [dict(row) for row in cur.fetchall()]
        hits: list[SearchHit] = []
        for row in sorted(rows, key=lambda value: int(value["graph_rank"])):
            graph = graph_by_document[str(row.pop("graph_document_id"))]
            row["score"] = float(graph["graph_score"])
            hit = _search_hit(row)
            citation = {
                **hit.citation,
                "relationship_id": str(graph.get("relationship_id") or ""),
                "relationship_type": str(graph.get("relationship_type") or ""),
                "relationship_source_id": str(graph.get("relationship_source_id") or ""),
                "relationship_target_id": str(graph.get("relationship_target_id") or ""),
                "relationship_is_adverse": str(bool(graph.get("relationship_is_adverse"))).lower(),
                "relation_status": str(graph.get("relation_status") or ""),
                "relation_scope": str(graph.get("relation_scope") or ""),
                "evidence_sha256": str(graph.get("evidence_sha256") or ""),
                "target_official_url": str(graph.get("target_official_url") or ""),
            }
            hits.append(hit.model_copy(update={"citation": citation}))
        return SearchPage(dataset_version=dataset.dataset_version, hits=hits[:limit])

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
            clauses = ["c.search_vector @@ plainto_tsquery('simple', %s)", "c.lexical_eligible", "c.dataset_id = %s"]
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
                       c.payload AS chunk_payload, c.section_title, c.unit_id,
                       c.source_start, c.source_end, n.title, n.is_external,
                       n.payload AS node_payload
                FROM chunks c
                JOIN documents n ON (n.dataset_id, n.id) = (c.dataset_id, c.document_id)
                WHERE {" AND ".join(clauses)}
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
                WITH resolved AS (
                    SELECT COALESCE(
                        (SELECT canonical_document_id FROM active_document_aliases
                         WHERE alias_document_id = %s),
                        %s
                    ) AS document_id
                )
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
                WHERE n.id = (SELECT document_id FROM resolved)
                """,
                (document_id, document_id),
            )
            row = cur.fetchone()
        return _document_response(row) if row is not None else None

    def get_document_html(self, document_id: str) -> tuple[str, str, str] | None:
        """Return the immutable raw HTML and its release/hash provenance."""

        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                WITH resolved AS (
                    SELECT COALESCE(
                        (SELECT canonical_document_id FROM active_document_aliases
                         WHERE alias_document_id = %s),
                        %s
                    ) AS document_id
                )
                SELECT dataset_version, raw_html, raw_html_sha256
                FROM active_document_html
                WHERE document_id = (SELECT document_id FROM resolved)
                """,
                (document_id, document_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return str(row["dataset_version"]), str(row["raw_html"]), str(row["raw_html_sha256"])

    def get_legal_unit(self, unit_id: str) -> LegalUnitResponse | None:
        with self._connection_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT u.dataset_version, u.unit_id, u.document_id, u.parent_unit_id, u.unit_type,
                       u.ordinal_raw, u.label, u.heading,
                       CASE WHEN u.unit_type = 'table' THEN u.text
                            ELSE btrim(
                                substring(d.content_text FROM u.source_start + 1
                                          FOR u.source_end - u.source_start),
                                E' \n\r\t'
                            )
                       END AS text,
                       u.source_start, u.source_end, u.text_sha256, u.parser_version
                FROM active_legal_units u
                JOIN active_document_content d ON d.document_id = u.document_id
                WHERE u.unit_id = %s
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
            cur.execute(
                "SELECT 1 FROM documents WHERE dataset_id=%s AND id=%s",
                (dataset.dataset_id, document_id),
            )
            if cur.fetchone() is None:
                return None
        direction_clause = {
            RelationshipDirection.OUTBOUND: "source.id = $document_id",
            RelationshipDirection.INBOUND: "target.id = $document_id",
            RelationshipDirection.BOTH: "source.id = $document_id OR target.id = $document_id",
        }[direction]
        rows = self._neo4j_rows(
            f"""MATCH (source:Document {{dataset_id:$dataset_id}})-[rel]->(target:Document {{dataset_id:$dataset_id}})
                WHERE type(rel) <> 'ALIAS_OF'
                  AND rel.serving_status = 'approved_evidence'
                  AND ({direction_clause})
                RETURN rel.relationship_id AS edge_key, source.id AS source_id, target.id AS target_id,
                       rel.relationship_type AS relationship_type, rel.adverse AS relationship_is_adverse,
                       source.name AS source_title, target.name AS target_title
                ORDER BY relationship_type, edge_key LIMIT $limit""",
            dataset_id=dataset.dataset_id,
            document_id=document_id,
            limit=min(limit, 300),
        )
        if rows is None:
            return RelationshipPage(dataset_version=dataset.dataset_version, items=[])
        return RelationshipPage(
            dataset_version=dataset.dataset_version,
            items=[RelationshipItem(**row) for row in rows],
        )

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


def _physical_or_payload(row: dict[str, Any], payload: dict[str, Any], key: str) -> Any:
    """Prefer compact physical storage columns while reading legacy payloads."""

    return row[key] if row.get(key) is not None else payload.get(key)


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
        section_title=str(row.get("section_title") or chunk.get("section_title") or ""),
        text=str(row.get("text") or ""),
        title=str(row.get("title") or ""),
        so_ky_hieu=str(node_metadata.get("so_ky_hieu") or node.get("so_ky_hieu") or ""),
        status=str(
            chunk_metadata.get("status_filter") or node_metadata.get("status_filter") or node.get("status_filter") or ""
        ),
        node_kind="external" if row.get("is_external") else "canonical",
        citation={
            key: str(row[key])
            for key in ("relationship_type", "relationship_source_id", "relationship_target_id")
            if row.get(key) is not None
        },
        unit_id=str(_physical_or_payload(row, chunk, "unit_id"))
        if _physical_or_payload(row, chunk, "unit_id")
        else None,
        source_start=int(_physical_or_payload(row, chunk, "source_start"))
        if _physical_or_payload(row, chunk, "source_start") is not None
        else None,
        source_end=int(_physical_or_payload(row, chunk, "source_end"))
        if _physical_or_payload(row, chunk, "source_end") is not None
        else None,
    )


def _document_response(row: dict[str, Any]) -> DocumentResponse:
    payload = _payload_dict(row.get("payload"))
    metadata = _nested_dict(payload, "metadata")
    content_payload = _payload_dict(row.get("content_payload"))
    is_external = bool(row.get("is_external"))
    resolution_status = (
        str(metadata.get("resolution_status") or metadata.get("external_lookup_status") or "unresolved")
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
        ngay_co_hieu_luc=_optional_text(metadata.get("ngay_co_hieu_luc") or payload.get("ngay_co_hieu_luc")),
        ngay_het_hieu_luc=_optional_text(metadata.get("ngay_het_hieu_luc") or payload.get("ngay_het_hieu_luc")),
        tinh_trang_hieu_luc=str(metadata.get("tinh_trang_hieu_luc") or payload.get("tinh_trang_hieu_luc") or ""),
        status_filter=str(metadata.get("status_filter") or payload.get("status_filter") or ""),
        pham_vi=str(metadata.get("pham_vi") or payload.get("pham_vi") or ""),
        linh_vuc=str(metadata.get("linh_vuc") or payload.get("linh_vuc") or ""),
        co_quan_ban_hanh=str(metadata.get("co_quan_ban_hanh") or payload.get("co_quan_ban_hanh") or ""),
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
        relationship_is_adverse=_bool(metadata.get("relationship_is_adverse", payload.get("relationship_is_adverse"))),
        source_title=str(
            row.get("source_title") or metadata.get("source_title_raw") or payload.get("source_title") or ""
        ),
        target_title=str(
            row.get("target_title") or metadata.get("target_title_raw") or payload.get("target_title") or ""
        ),
    )
