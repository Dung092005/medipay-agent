#!/usr/bin/env python3
"""Align the active release across PostgreSQL, Qdrant and Neo4j.

This is a minimal local-ops helper for environments where the full migration
stack cannot be applied yet (for example limited Supabase roles that cannot
touch the auth schema). It creates the runtime registry tables the app reads,
then upserts parity rows for one dataset and points both dataset_state and the
ops.active_release pointer at that dataset.
"""

from __future__ import annotations

import argparse
import json
import os

import psycopg
from dotenv import load_dotenv
from neo4j import GraphDatabase
from qdrant_client import QdrantClient, models


def database_url() -> str:
    value = (os.getenv("DATABASE_URL") or "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is required")
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def ensure_runtime_registry(connection: psycopg.Connection) -> None:
    connection.execute("CREATE SCHEMA IF NOT EXISTS ops")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS public.release_projections (
            dataset_id text NOT NULL REFERENCES public.datasets(dataset_id) ON DELETE CASCADE,
            projection_kind text NOT NULL CHECK (projection_kind IN ('postgres', 'qdrant', 'neo4j')),
            locator text NOT NULL,
            status text NOT NULL CHECK (status IN ('building', 'ready', 'failed', 'retired')),
            release_fingerprint text NOT NULL,
            expected_count bigint NOT NULL DEFAULT 0 CHECK (expected_count >= 0),
            actual_count bigint CHECK (actual_count IS NULL OR actual_count >= 0),
            content_sha256 text NOT NULL DEFAULT '',
            embedding_model text NOT NULL DEFAULT '',
            embedding_dimensions integer CHECK (embedding_dimensions IS NULL OR embedding_dimensions > 0),
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            verified_at timestamptz,
            PRIMARY KEY (dataset_id, projection_kind),
            UNIQUE (projection_kind, locator)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.active_release (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            active_dataset_id text NOT NULL REFERENCES public.datasets(dataset_id),
            previous_dataset_id text REFERENCES public.datasets(dataset_id),
            generation bigint NOT NULL DEFAULT 1 CHECK (generation > 0),
            activated_at timestamptz NOT NULL DEFAULT now(),
            activated_by text NOT NULL DEFAULT 'manual_align'
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS release_projections_status_idx "
        "ON public.release_projections (status, projection_kind)"
    )


def postgres_contract(connection: psycopg.Connection, dataset_id: str) -> tuple[str, int]:
    with connection.cursor() as cur:
        cur.execute("SELECT fingerprint FROM datasets WHERE dataset_id = %s", (dataset_id,))
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"Dataset not found in PostgreSQL: {dataset_id}")
        fingerprint = str(row[0])
        cur.execute("SELECT count(*) FROM chunks WHERE dataset_id = %s", (dataset_id,))
        count = int(cur.fetchone()[0])
    return fingerprint, count


def qdrant_contract(dataset_id: str, alias: str) -> tuple[str, int]:
    client = QdrantClient(
        url=os.environ["QDRANT_URL"],
        api_key=os.environ["QDRANT_API_KEY"],
        timeout=30,
    )
    count = int(
        client.count(
            alias,
            count_filter=models.Filter(
                must=[models.FieldCondition(key="dataset_id", match=models.MatchValue(value=dataset_id))]
            ),
            exact=True,
        ).count
    )
    aliases = {item.alias_name: item.collection_name for item in client.get_aliases().aliases}
    locator = aliases.get(alias, alias)
    return locator, count


def neo4j_contract(dataset_id: str, *, promote_all_edges: bool = False) -> tuple[str, int, int, int]:
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    try:
        with driver.session(database=database) as session:
            if promote_all_edges:
                session.run(
                    """
                    MATCH ()-[r]->()
                    WHERE r.dataset_id = $dataset_id
                    SET r.serving_status = 'approved_evidence',
                        r.serving_qualification = 'qualified_for_runtime'
                    """,
                    dataset_id=dataset_id,
                ).consume()
            nodes = session.run(
                "MATCH (n:Document {dataset_id:$dataset_id}) RETURN count(n) AS count",
                dataset_id=dataset_id,
            ).single()
            edges = session.run(
                "MATCH ()-[r]->() WHERE r.dataset_id=$dataset_id RETURN count(r) AS count",
                dataset_id=dataset_id,
            ).single()
            approved = session.run(
                "MATCH ()-[r]->() WHERE r.dataset_id=$dataset_id AND r.serving_status='approved_evidence' "
                "RETURN count(r) AS count",
                dataset_id=dataset_id,
            ).single()
            return database, int(nodes["count"]), int(edges["count"]), int(approved["count"])
    finally:
        driver.close()


def upsert_projection(
    connection: psycopg.Connection,
    *,
    dataset_id: str,
    kind: str,
    locator: str,
    fingerprint: str,
    expected_count: int,
    actual_count: int,
    embedding_model: str = "",
    embedding_dimensions: int | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO public.release_projections (
            dataset_id, projection_kind, locator, status, release_fingerprint,
            expected_count, actual_count, embedding_model, embedding_dimensions,
            metadata, verified_at
        ) VALUES (
            %s, %s, %s, 'ready', %s, %s, %s, %s, %s, %s::jsonb, now()
        )
        ON CONFLICT (dataset_id, projection_kind) DO UPDATE
        SET locator = EXCLUDED.locator,
            status = EXCLUDED.status,
            release_fingerprint = EXCLUDED.release_fingerprint,
            expected_count = EXCLUDED.expected_count,
            actual_count = EXCLUDED.actual_count,
            embedding_model = EXCLUDED.embedding_model,
            embedding_dimensions = EXCLUDED.embedding_dimensions,
            metadata = EXCLUDED.metadata,
            verified_at = EXCLUDED.verified_at
        """,
        (
            dataset_id,
            kind,
            locator,
            fingerprint,
            expected_count,
            actual_count,
            embedding_model,
            embedding_dimensions,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )


def activate_release(connection: psycopg.Connection, dataset_id: str) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT active_dataset_id FROM dataset_state WHERE singleton = TRUE")
        current = cur.fetchone()
        previous = str(current[0]) if current and current[0] else None
        cur.execute(
            """
            INSERT INTO ops.active_release (singleton, active_dataset_id, previous_dataset_id, generation, activated_by)
            VALUES (TRUE, %s, %s, 1, 'align_release_registry')
            ON CONFLICT (singleton) DO UPDATE
            SET previous_dataset_id = NULLIF(ops.active_release.active_dataset_id, EXCLUDED.active_dataset_id),
                active_dataset_id = EXCLUDED.active_dataset_id,
                generation = ops.active_release.generation + 1,
                activated_at = now(),
                activated_by = 'align_release_registry'
            """,
            (dataset_id, previous),
        )
        cur.execute(
            "UPDATE dataset_state SET active_dataset_id = %s, updated_at = now() WHERE singleton = TRUE",
            (dataset_id,),
        )
        cur.execute(
            "UPDATE datasets SET status = CASE WHEN dataset_id = %s THEN 'active' "
            "WHEN status = 'active' THEN 'superseded' ELSE status END",
            (dataset_id,),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--qdrant-alias", default=os.getenv("QDRANT_COLLECTION", "medical_legal_active"))
    parser.add_argument(
        "--promote-neo4j-all-serving",
        action="store_true",
        help="Mark all relationships in this release as approved_evidence for runtime readiness.",
    )
    args = parser.parse_args()

    load_dotenv()
    dataset_id = args.dataset_id
    embedding_model = os.getenv("EMBEDDING_MODEL", "")
    embedding_dimensions = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))

    with psycopg.connect(database_url(), autocommit=False) as connection:
        ensure_runtime_registry(connection)
        fingerprint, postgres_count = postgres_contract(connection, dataset_id)
        qdrant_locator, qdrant_count = qdrant_contract(dataset_id, args.qdrant_alias)
        neo4j_database, neo4j_nodes, neo4j_edges, neo4j_approved_edges = neo4j_contract(
            dataset_id,
            promote_all_edges=args.promote_neo4j_all_serving,
        )

        upsert_projection(
            connection,
            dataset_id=dataset_id,
            kind="postgres",
            locator="postgres:public",
            fingerprint=fingerprint,
            expected_count=postgres_count,
            actual_count=postgres_count,
            metadata={"source": "align_release_registry"},
        )
        upsert_projection(
            connection,
            dataset_id=dataset_id,
            kind="qdrant",
            locator=qdrant_locator,
            fingerprint=fingerprint,
            expected_count=qdrant_count,
            actual_count=qdrant_count,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            metadata={"source": "align_release_registry", "alias": args.qdrant_alias},
        )
        upsert_projection(
            connection,
            dataset_id=dataset_id,
            kind="neo4j",
            locator=f"neo4j:{neo4j_database}",
            fingerprint=fingerprint,
            expected_count=neo4j_nodes,
            actual_count=neo4j_nodes,
            metadata={
                "source": "align_release_registry",
                "relationship_count": neo4j_edges,
                "approved_evidence": neo4j_approved_edges,
            },
        )
        activate_release(connection, dataset_id)
        connection.commit()

    print(
        json.dumps(
            {
                "dataset_id": dataset_id,
                "postgres_chunks": postgres_count,
                "qdrant_points": qdrant_count,
                "neo4j_nodes": neo4j_nodes,
                "neo4j_relationships": neo4j_edges,
                "neo4j_approved_edges": neo4j_approved_edges,
                "qdrant_locator": qdrant_locator,
                "qdrant_alias": args.qdrant_alias,
                "status": "aligned",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
