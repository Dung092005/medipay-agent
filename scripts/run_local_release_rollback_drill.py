#!/usr/bin/env python3
"""Exercise a local physical active/previous release rollback.

Run inside the local-full Compose network. The command clones the active
PostgreSQL rows, Qdrant points and Neo4j graph into a second physical release,
cuts the stable alias and pointer, then rolls back to the original release.
It uses explicit local defaults and never reads managed `.env`.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

import asyncpg
from neo4j import GraphDatabase
from qdrant_client import QdrantClient, models


def _url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _replace(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_replace(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _replace(item, old, new) for key, item in value.items()}
    return value


async def _columns(db: asyncpg.Connection, table: str) -> list[str]:
    rows = await db.fetch(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema='public' AND table_name=$1
             AND is_generated='NEVER' AND identity_generation IS NULL
           ORDER BY ordinal_position""",
        table,
    )
    return [str(row["column_name"]) for row in rows]


async def _insert_rows(db: asyncpg.Connection, table: str, columns: list[str], rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    names = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join(f"${index}" for index in range(1, len(columns) + 1))
    values = [tuple(row[column] for column in columns) for row in rows]
    await db.executemany(f'INSERT INTO public."{table}" ({names}) VALUES ({placeholders})', values)
    return len(values)


async def _clone_table(db: asyncpg.Connection, table: str, source: str, candidate: str) -> int:
    columns = await _columns(db, table)
    names = ", ".join(f'"{column}"' for column in columns)
    rows = [dict(row) for row in await db.fetch(f'SELECT {names} FROM public."{table}" WHERE dataset_id=$1', source)]
    for row in rows:
        row["dataset_id"] = candidate
    return await _insert_rows(db, table, columns, rows)


async def _clone_legal_units(db: asyncpg.Connection, source: str, candidate: str) -> int:
    """Insert the self-referencing legal-unit tree parent before children."""
    columns = await _columns(db, "legal_units")
    names = ", ".join(f'"{column}"' for column in columns)
    pending = [dict(row) for row in await db.fetch(f'SELECT {names} FROM public.legal_units WHERE dataset_id=$1', source)]
    for row in pending:
        row["dataset_id"] = candidate
    known: set[str] = set()
    inserted = 0
    while pending:
        ready = [row for row in pending if not row.get("parent_unit_id") or str(row["parent_unit_id"]) in known]
        if not ready:
            raise RuntimeError("legal-unit clone contains an unresolved parent reference")
        inserted += await _insert_rows(db, "legal_units", columns, ready)
        known.update(str(row["unit_id"]) for row in ready)
        pending = [row for row in pending if row not in ready]
    return inserted


async def _clone_postgres(database_url: str, source: str, candidate: str, collection: str) -> dict[str, int]:
    db = await asyncpg.connect(_url(database_url), server_settings={"application_name": "local-release-rollback-drill"})
    await db.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    transaction = db.transaction()
    await transaction.start()
    fingerprint = hashlib.sha256(f"local-rollback:{source}:{candidate}".encode()).hexdigest()
    counts: dict[str, int] = {}
    try:
        old = await db.fetchrow("SELECT manifest FROM public.datasets WHERE dataset_id=$1", source)
        if old is None:
            raise RuntimeError(f"source dataset is missing: {source}")
        if await db.fetchrow("SELECT 1 FROM public.datasets WHERE dataset_id=$1", candidate):
            raise RuntimeError(f"candidate dataset already exists: {candidate}")
        await db.execute(
            """INSERT INTO public.datasets
               (dataset_id,fingerprint,status,manifest,collection_name,created_at,published_at,failure_reason)
               VALUES ($1,$2,'staging',$3,$4,now(),now(),NULL)""",
            candidate,
            fingerprint,
            _replace(old["manifest"], source, candidate),
            collection,
        )
        for table in ("documents", "document_tables", "table_cells", "table_cell_facts", "document_aliases"):
            counts[table] = await _clone_table(db, table, source, candidate)
        counts["legal_units"] = await _clone_legal_units(db, source, candidate)

        columns = await _columns(db, "chunks")
        names = ", ".join(f'"{column}"' for column in columns)
        chunks = [dict(row) for row in await db.fetch(f'SELECT {names} FROM public.chunks WHERE dataset_id=$1', source)]
        prefix = f"{source}:"
        for row in chunks:
            row["dataset_id"] = candidate
            suffix = str(row["id"])[len(prefix):] if str(row["id"]).startswith(prefix) else str(row["id"])
            row["id"] = f"{candidate}:{suffix}"
            row["source_key"] = row["id"]
        counts["chunks"] = await _insert_rows(db, "chunks", columns, chunks)

        columns = await _columns(db, "release_projections")
        names = ", ".join(f'"{column}"' for column in columns)
        projections = [dict(row) for row in await db.fetch(f'SELECT {names} FROM public.release_projections WHERE dataset_id=$1', source)]
        for row in projections:
            row["dataset_id"] = candidate
            row["release_fingerprint"] = fingerprint
            row["locator"] = {
                "postgres": f"local-postgres-release:{candidate}",
                "qdrant": collection,
                "neo4j": f"local-neo4j-release:{candidate}",
            }[str(row["projection_kind"])]
            row["metadata"] = _replace(row["metadata"], source, candidate)
        counts["release_projections"] = await _insert_rows(db, "release_projections", columns, projections)
        await transaction.commit()
        return counts
    except Exception:
        await transaction.rollback()
        raise
    finally:
        await db.close()


def _clone_qdrant(url: str, api_key: str, source_collection: str, candidate_collection: str) -> int:
    client = QdrantClient(url=url, api_key=api_key, timeout=120)
    try:
        if client.collection_exists(candidate_collection):
            raise RuntimeError(f"candidate Qdrant collection already exists: {candidate_collection}")
        info = client.get_collection(source_collection)
        client.create_collection(candidate_collection, vectors_config=info.config.params.vectors, timeout=120)
        total = 0
        offset = None
        candidate_dataset = candidate_collection.removeprefix("medical_legal_")
        while True:
            points, offset = client.scroll(source_collection, offset=offset, limit=256, with_payload=True, with_vectors=True)
            if not points:
                break
            cloned = []
            for point in points:
                payload = dict(point.payload or {})
                payload["dataset_id"] = candidate_dataset
                cloned.append(models.PointStruct(id=point.id, vector=point.vector, payload=payload))
            client.upsert(candidate_collection, points=cloned, wait=True, timeout=120)
            total += len(cloned)
            if offset is None:
                break
        for key, schema in (
            ("dataset_id", models.PayloadSchemaType.KEYWORD),
            ("document_id", models.PayloadSchemaType.KEYWORD),
            ("answer_ready", models.PayloadSchemaType.BOOL),
            ("retrieval_scope", models.PayloadSchemaType.KEYWORD),
        ):
            client.create_payload_index(candidate_collection, field_name=key, field_schema=schema, wait=True)
        return total
    finally:
        client.close()


def _clone_neo4j(uri: str, username: str, password: str, database: str, source: str, candidate: str) -> dict[str, int]:
    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        with driver.session(database=database) as session:
            nodes = session.run("MATCH (n) WHERE n.dataset_id=$dataset RETURN properties(n) AS properties", dataset=source).data()
            relationships = session.run(
                """MATCH (a)-[r]->(b) WHERE a.dataset_id=$dataset AND b.dataset_id=$dataset
                   RETURN a.graph_id AS source,b.graph_id AS target,type(r) AS type,properties(r) AS properties""",
                dataset=source,
            ).data()
            if not nodes:
                raise RuntimeError("source Neo4j release has no nodes")
            mapped: dict[str, str] = {}
            cloned_nodes = []
            for row in nodes:
                properties = dict(row["properties"] or {})
                old_id = str(properties.get("graph_id", ""))
                new_id = f"{candidate}::{old_id}"
                mapped[old_id] = new_id
                properties["dataset_id"] = candidate
                properties["graph_id"] = new_id
                cloned_nodes.append(properties)
            session.run("UNWIND $rows AS properties CREATE (n:Document) SET n=properties", rows=cloned_nodes).consume()
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in relationships:
                properties = dict(row["properties"] or {})
                properties["dataset_id"] = candidate
                grouped.setdefault(str(row["type"]), []).append(
                    {"source": mapped[str(row["source"])], "target": mapped[str(row["target"])], "properties": properties}
                )
            for rel_type, rows in grouped.items():
                if not rel_type.replace("_", "").isalnum():
                    raise RuntimeError(f"unsafe relationship type: {rel_type}")
                session.run(
                    f"""UNWIND $rows AS row MATCH (a:Document {{graph_id:row.source}}),(b:Document {{graph_id:row.target}})
                    CREATE (a)-[r:`{rel_type}`]->(b) SET r=row.properties""",
                    rows=rows,
                ).consume()
            return {
                "nodes": len(cloned_nodes),
                "relationships": len(relationships),
                "approved_evidence": sum(1 for row in relationships if (row["properties"] or {}).get("serving_status") == "approved_evidence"),
            }
    finally:
        driver.close()


def _switch_alias(url: str, api_key: str, alias: str, collection: str) -> None:
    client = QdrantClient(url=url, api_key=api_key, timeout=120)
    try:
        existing = {item.alias_name: item.collection_name for item in client.get_aliases().aliases}
        changes: list[models.CreateAliasOperation | models.DeleteAliasOperation] = []
        if alias in existing:
            changes.append(models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=alias)))
        changes.append(models.CreateAliasOperation(create_alias=models.CreateAlias(collection_name=collection, alias_name=alias)))
        client.update_collection_aliases(changes, timeout=120)
    finally:
        client.close()


async def _activate(database_url: str, dataset_id: str, actor: str) -> None:
    db = await asyncpg.connect(_url(database_url))
    try:
        await db.execute("SELECT ops.activate_release($1,$2)", dataset_id, actor)
    finally:
        await db.close()


async def _verify_contract(args: argparse.Namespace, dataset_id: str) -> dict[str, Any]:
    db = await asyncpg.connect(_url(args.database_url))
    try:
        pointer = await db.fetchrow("SELECT active_dataset_id FROM ops.active_release WHERE singleton")
        rows = await db.fetch("""SELECT projection_kind,status,expected_count,actual_count,metadata
                                FROM release_projections WHERE dataset_id=$1""", dataset_id)
    finally:
        await db.close()
    projections = {str(row["projection_kind"]): dict(row) for row in rows}
    postgres_pass = str(pointer["active_dataset_id"]) == dataset_id and set(projections) == {"postgres", "qdrant", "neo4j"} and all(
        row["status"] == "ready" and row["expected_count"] == row["actual_count"] for row in projections.values()
    )
    qdrant = QdrantClient(url=args.qdrant_url, api_key=args.qdrant_api_key, timeout=120)
    try:
        aliases = {item.alias_name: item.collection_name for item in qdrant.get_aliases().aliases}
        collection = aliases.get(args.alias, "")
        qdrant_count = int(qdrant.count(collection, count_filter=models.Filter(must=[models.FieldCondition(key="dataset_id", match=models.MatchValue(value=dataset_id))]), exact=True).count) if collection else 0
    finally:
        qdrant.close()
    qdrant_pass = collection == f"medical_legal_{dataset_id}" and qdrant_count == int(projections.get("qdrant", {}).get("expected_count", -1))
    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_username, args.neo4j_password))
    try:
        with driver.session(database=args.neo4j_database) as session:
            nodes = int(session.run("MATCH (n) WHERE n.dataset_id=$d RETURN count(n) AS c", d=dataset_id).single()["c"])
            edges = int(session.run("MATCH ()-[r]->() WHERE r.dataset_id=$d AND r.serving_status='approved_evidence' RETURN count(r) AS c", d=dataset_id).single()["c"])
    finally:
        driver.close()
    metadata = projections.get("neo4j", {}).get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    expected_edges = int(metadata.get("approved_evidence", edges))
    neo4j_pass = nodes == int(projections.get("neo4j", {}).get("expected_count", -1)) and edges == expected_edges
    return {"active_dataset_id": str(pointer["active_dataset_id"]), "qdrant_collection": collection, "qdrant_points": qdrant_count, "neo4j_nodes": nodes, "neo4j_approved_edges": edges, "postgres_projection_pass": postgres_pass, "qdrant_pass": qdrant_pass, "neo4j_pass": neo4j_pass, "pass": postgres_pass and qdrant_pass and neo4j_pass}


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    db = await asyncpg.connect(_url(args.database_url))
    try:
        row = await db.fetchrow("SELECT active_dataset_id FROM dataset_state WHERE singleton")
        source = args.source or str(row["active_dataset_id"])
    finally:
        await db.close()
    collection = f"medical_legal_{args.candidate}"
    postgres = (
        {"reused_existing_release": 1}
        if args.reuse_existing
        else await _clone_postgres(args.database_url, source, args.candidate, collection)
    )
    qdrant_points = (
        0
        if args.reuse_existing
        else _clone_qdrant(args.qdrant_url, args.qdrant_api_key, f"medical_legal_{source}", collection)
    )
    neo4j = (
        {"reused_existing_release": 1}
        if args.reuse_existing
        else _clone_neo4j(args.neo4j_uri, args.neo4j_username, args.neo4j_password, args.neo4j_database, source, args.candidate)
    )
    _switch_alias(args.qdrant_url, args.qdrant_api_key, args.alias, collection)
    db = await asyncpg.connect(_url(args.database_url))
    try:
        await db.execute("UPDATE public.datasets SET status='superseded' WHERE dataset_id=$1", source)
        await db.execute("UPDATE public.datasets SET status='active' WHERE dataset_id=$1", args.candidate)
    finally:
        await db.close()
    await _activate(args.database_url, args.candidate, "local-rollback-drill-cutover")
    cutover_contract = await _verify_contract(args, args.candidate)
    if not cutover_contract["pass"]:
        raise RuntimeError(f"candidate release contract failed: {cutover_contract}")
    _switch_alias(args.qdrant_url, args.qdrant_api_key, args.alias, f"medical_legal_{source}")
    db = await asyncpg.connect(_url(args.database_url))
    try:
        await db.execute("UPDATE public.datasets SET status='superseded' WHERE dataset_id=$1", args.candidate)
        await db.execute("UPDATE public.datasets SET status='active' WHERE dataset_id=$1", source)
    finally:
        await db.close()
    await _activate(args.database_url, source, "local-rollback-drill-rollback")
    rollback_contract = await _verify_contract(args, source)
    if not rollback_contract["pass"]:
        raise RuntimeError(f"rollback release contract failed: {rollback_contract}")
    return {
        "source_dataset_id": source,
        "candidate_dataset_id": args.candidate,
        "candidate_qdrant_collection": collection,
        "postgres_counts": postgres,
        "qdrant_points_cloned_in_invocation": qdrant_points,
        "neo4j_counts": neo4j,
        "cutover_contract": cutover_contract,
        "rollback_contract": rollback_contract,
        "candidate_counts_verified": {
            "qdrant_points": cutover_contract["qdrant_points"],
            "neo4j_nodes": cutover_contract["neo4j_nodes"],
            "neo4j_approved_edges": cutover_contract["neo4j_approved_edges"],
            "projection_parity": cutover_contract["postgres_projection_pass"],
        },
        "cutover_verified": cutover_contract["pass"],
        "rollback_verified": rollback_contract["pass"],
        "physical_previous_retained": True,
        "created_at_utc": datetime.now(UTC).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("LOCAL_DATABASE_URL", "postgresql://medipay:medipay-local-only@postgres:5432/medipay"))
    parser.add_argument("--qdrant-url", default=os.getenv("LOCAL_QDRANT_URL", "http://qdrant:6333"))
    parser.add_argument("--qdrant-api-key", default=os.getenv("LOCAL_QDRANT_API_KEY", "local-qdrant-only"))
    parser.add_argument("--neo4j-uri", default=os.getenv("LOCAL_NEO4J_URI", "bolt://neo4j:7687"))
    parser.add_argument("--neo4j-username", default="neo4j")
    parser.add_argument("--neo4j-password", default=os.getenv("LOCAL_NEO4J_PASSWORD", "local-neo4j-only"))
    parser.add_argument("--neo4j-database", default="neo4j")
    parser.add_argument("--source")
    parser.add_argument("--candidate", default=f"local-rollback-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}")
    parser.add_argument("--alias", default="medical_legal_active")
    parser.add_argument("--reuse-existing", action="store_true", help="Reuse an already cloned PostgreSQL release and rebuild only missing projections.")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
