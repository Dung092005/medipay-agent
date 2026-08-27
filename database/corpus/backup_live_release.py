#!/usr/bin/env python3
"""Create a recoverable logical backup before a Free-tier corpus cutover.

The backup is intentionally release-scoped in PostgreSQL and graph-wide in
Neo4j. It contains no credentials and can be inspected as JSON/JSONL.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def write_json(path: Path, value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=json_default).encode("utf-8")
    path.write_bytes(encoded + b"\n")
    return hashlib.sha256(encoded).hexdigest()


def postgres_connection() -> psycopg.Connection[Any]:
    database_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    return psycopg.connect(database_url, connect_timeout=20, application_name="corpus-cutover-backup")


def backup_postgres(
    destination: Path, *, omit_generated_retrieval_columns: bool = False,
    metadata_only: bool = False,
) -> tuple[str, dict[str, int], str]:
    release_tables = (
        "documents",
        "release_projections",
        "document_aliases",
        "legal_units",
        "document_tables",
        "table_cells",
        "chunks",
    )
    if metadata_only:
        release_tables = ("documents", "document_aliases")
    with postgres_connection() as connection, connection.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute("SELECT active_dataset_id FROM public.dataset_state WHERE singleton")
        active_dataset_id = str(cursor.fetchone()["active_dataset_id"])
        cursor.execute("SELECT * FROM public.datasets WHERE dataset_id = %s", (active_dataset_id,))
        dataset = cursor.fetchone()
        cursor.execute("SELECT * FROM public.dataset_state WHERE singleton")
        state = cursor.fetchone()
        payload: dict[str, Any] = {
            "active_dataset_id": active_dataset_id,
            "dataset": dataset,
            "dataset_state": state,
            "tables": {},
        }
        counts: dict[str, int] = {}
        for table in release_tables:
            projection = "*"
            if metadata_only and table == "documents":
                projection = (
                    '"dataset_id", "id", "title", "is_external", "text_sha256", '
                    '"content_available", "raw_html_sha256", "raw_html_encoding", '
                    '"categories", "facets", "payload"'
                )
            elif omit_generated_retrieval_columns and table == "chunks":
                cursor.execute(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_schema='public' AND table_name='chunks'
                         AND column_name NOT IN ('embedding', 'search_vector')
                       ORDER BY ordinal_position"""
                )
                columns = [row["column_name"] for row in cursor.fetchall()]
                projection = ", ".join(f'"{column}"' for column in columns)
            cursor.execute(
                f"SELECT {projection} FROM public.{table} WHERE dataset_id = %s",
                (active_dataset_id,),
            )
            rows = cursor.fetchall()
            payload["tables"][table] = rows
            counts[table] = len(rows)
        # Conversation data is owner-scoped application state. Include it only
        # when the migration exists, and never pretend it is release content.
        for table in ("conversations", "conversation_turns"):
            cursor.execute("SELECT to_regclass(%s) AS relation", (f"public.{table}",))
            if cursor.fetchone()["relation"] is None:
                continue
            cursor.execute(f"SELECT * FROM public.{table} WHERE deleted_at IS NULL" if table == "conversations" else f"SELECT * FROM public.{table}")
            payload["tables"][table] = cursor.fetchall()
            counts[table] = len(payload["tables"][table])
    return write_json(destination / "postgres_active_release.json", payload), counts, active_dataset_id


def backup_neo4j(destination: Path) -> tuple[str, dict[str, int]]:
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    try:
        with driver.session(database=database) as session:
            nodes = [
                {"labels": sorted(record["labels"]), "properties": dict(record["properties"])}
                for record in session.run("MATCH (n) RETURN labels(n) AS labels, properties(n) AS properties")
            ]
            relationships = [
                {
                    "source_graph_id": record["source_graph_id"],
                    "source_id": record["source_id"],
                    "target_graph_id": record["target_graph_id"],
                    "target_id": record["target_id"],
                    "type": record["type"],
                    "properties": dict(record["properties"]),
                }
                for record in session.run(
                    """MATCH (source)-[r]->(target)
                       RETURN source.graph_id AS source_graph_id, source.id AS source_id,
                              target.graph_id AS target_graph_id, target.id AS target_id,
                              type(r) AS type, properties(r) AS properties"""
                )
            ]
    finally:
        driver.close()
    payload = {"nodes": nodes, "relationships": relationships}
    return write_json(destination / "neo4j_graph.json", payload), {
        "nodes": len(nodes),
        "relationships": len(relationships),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--omit-generated-retrieval-columns", action="store_true",
        help="Omit chunk embedding/search_vector; both are reproducible from the embedding artifact and text.",
    )
    parser.add_argument(
        "--metadata-only", action="store_true",
        help="Back up mutable document/alias metadata only; use with an earlier full release backup.",
    )
    parser.add_argument(
        "--compress", action="store_true",
        help="Write a gzip-compressed copy beside the JSON backup and record its checksum.",
    )
    args = parser.parse_args()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)
    postgres_hash, postgres_counts, active_dataset_id = backup_postgres(
        output,
        omit_generated_retrieval_columns=args.omit_generated_retrieval_columns,
        metadata_only=args.metadata_only,
    )
    neo4j_hash, neo4j_counts = backup_neo4j(output)
    manifest = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "active_dataset_id": active_dataset_id,
        "postgres_active_release_sha256": postgres_hash,
        "neo4j_graph_sha256": neo4j_hash,
        "omitted_generated_retrieval_columns": args.omit_generated_retrieval_columns,
        "metadata_only": args.metadata_only,
        "postgres_counts": postgres_counts,
        "neo4j_counts": neo4j_counts,
    }
    if args.compress:
        source = output / "postgres_active_release.json"
        compressed = output / "postgres_active_release.json.gz"
        with source.open("rb") as input_file, gzip.open(compressed, "wb", compresslevel=9) as output_file:
            while block := input_file.read(1024 * 1024):
                output_file.write(block)
        manifest["postgres_active_release_gzip_sha256"] = hashlib.sha256(compressed.read_bytes()).hexdigest()
        manifest["compression"] = "gzip"
    write_json(output / "manifest.json", manifest)
    print(json.dumps({"output_dir": str(output), "postgres": postgres_counts, "neo4j": neo4j_counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
