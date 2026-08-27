#!/usr/bin/env python3
"""Apply a verified graph-serving qualification to the active release.

The corpus relationship audit trail remains intact in Neo4j.  This script adds
serving properties to every legal edge and refreshes only the derived,
non-authoritative temporal candidates in Neo4j and PostgreSQL document payloads.
It never changes source HTML, document text, chunks, embeddings, or official
legal-status fields.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from neo4j import GraphDatabase

csv.field_size_limit(sys.maxsize)
load_dotenv()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def postgres_connection() -> psycopg.Connection[Any]:
    database_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    return psycopg.connect(database_url, connect_timeout=20, application_name="graph-serving-hotfix")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--report-path", type=Path, required=True)
    args = parser.parse_args()

    relationships = read_csv(args.source_dir / "relationships.csv")
    metadata = read_csv(args.source_dir / "metadata.csv")
    edge_rows = [
        {
            "relationship_id": row["relationship_id"],
            "serving_status": row.get("serving_status", "audit_only_unverified"),
            "serving_qualification": row.get("serving_qualification", "not_qualified"),
        }
        for row in relationships
    ]
    if len({row["relationship_id"] for row in edge_rows}) != len(edge_rows):
        raise ValueError("relationships.csv contains duplicate relationship_id values")
    node_rows = [
        {
            "id": row["id"],
            "derived_status_candidate": row.get("derived_status_candidate", ""),
            "derived_status_candidate_count": row.get("derived_status_candidate_count", "0"),
        }
        for row in metadata
    ]
    if len({row["id"] for row in node_rows}) != len(node_rows):
        raise ValueError("metadata.csv contains duplicate id values")

    with postgres_connection() as connection, connection.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute("SELECT active_dataset_id FROM dataset_state WHERE singleton")
        active_dataset_id = str(cursor.fetchone()["active_dataset_id"])
        if active_dataset_id != args.dataset_id:
            raise ValueError(f"active PostgreSQL dataset is {active_dataset_id}, expected {args.dataset_id}")
        cursor.execute("SELECT count(*) AS count FROM documents WHERE dataset_id=%s", (args.dataset_id,))
        if int(cursor.fetchone()["count"]) != len(node_rows):
            raise ValueError("canonical metadata count does not match active PostgreSQL documents")

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    try:
        with driver.session(database=database) as session:
            existing_ids = {
                str(record["relationship_id"])
                for record in session.run(
                    """MATCH ()-[r]->()
                       WHERE r.dataset_id=$dataset_id AND type(r) <> 'ALIAS_OF'
                       RETURN r.relationship_id AS relationship_id""",
                    dataset_id=args.dataset_id,
                )
            }
            expected_ids = {row["relationship_id"] for row in edge_rows}
            if existing_ids != expected_ids:
                raise ValueError(
                    "Neo4j legal-edge IDs do not exactly match qualified source "
                    f"(neo4j={len(existing_ids)}, source={len(expected_ids)})"
                )
            existing_nodes = int(session.run(
                "MATCH (n:Document {dataset_id:$dataset_id, node_kind:'canonical_document'}) RETURN count(n) AS count",
                dataset_id=args.dataset_id,
            ).single()["count"])
            if existing_nodes != len(node_rows):
                raise ValueError("Neo4j canonical-node count does not match qualified source")
            with session.begin_transaction() as tx:
                tx.run(
                    """UNWIND $rows AS row
                       MATCH ()-[r]->() WHERE r.dataset_id=$dataset_id
                         AND r.relationship_id=row.relationship_id
                       SET r.serving_status=row.serving_status,
                           r.serving_qualification=row.serving_qualification""",
                    dataset_id=args.dataset_id,
                    rows=edge_rows,
                ).consume()
                tx.run(
                    """UNWIND $rows AS row
                       MATCH (n:Document {dataset_id:$dataset_id, id:row.id})
                       SET n.derived_status_candidate=row.derived_status_candidate,
                           n.derived_status_candidate_count=row.derived_status_candidate_count""",
                    dataset_id=args.dataset_id,
                    rows=node_rows,
                ).consume()
                actual_statuses = {
                    str(record["serving_status"]): int(record["count"])
                    for record in tx.run(
                        """MATCH ()-[r]->()
                           WHERE r.dataset_id=$dataset_id AND type(r) <> 'ALIAS_OF'
                           RETURN r.serving_status AS serving_status, count(r) AS count""",
                        dataset_id=args.dataset_id,
                    )
                }
                expected_statuses = dict(Counter(row["serving_status"] for row in edge_rows))
                if actual_statuses != expected_statuses:
                    raise ValueError(f"Neo4j qualification mismatch: {actual_statuses!r} != {expected_statuses!r}")
                tx.commit()
    finally:
        driver.close()

    with postgres_connection() as connection, connection.cursor() as cursor:
        cursor.executemany(
            """UPDATE documents AS d
               SET payload = jsonb_set(
                   d.payload,
                   '{metadata}',
                   COALESCE(d.payload -> 'metadata', '{}'::jsonb)
                   || jsonb_build_object(
                       'derived_status_candidate', %s::text,
                       'derived_status_candidate_count', %s::text
                   ),
                   true
               )
               WHERE d.dataset_id=%s AND d.id=%s""",
            [
                (
                    row["derived_status_candidate"],
                    row["derived_status_candidate_count"],
                    args.dataset_id,
                    row["id"],
                )
                for row in node_rows
            ],
        )
        connection.commit()
    with postgres_connection() as connection, connection.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """SELECT id,
                       COALESCE(payload -> 'metadata' ->> 'derived_status_candidate', '') AS candidate,
                       COALESCE(payload -> 'metadata' ->> 'derived_status_candidate_count', '') AS candidate_count
               FROM documents WHERE dataset_id=%s""",
            (args.dataset_id,),
        )
        actual_metadata = {str(row["id"]): (str(row["candidate"]), str(row["candidate_count"])) for row in cursor}
    expected_metadata = {
        row["id"]: (row["derived_status_candidate"], row["derived_status_candidate_count"])
        for row in node_rows
    }
    if actual_metadata != expected_metadata:
        raise ValueError("PostgreSQL derived temporal metadata did not match qualified source")

    report = {
        "dataset_id": args.dataset_id,
        "source_dir": str(args.source_dir),
        "legal_relationships": len(edge_rows),
        "serving_status_counts": dict(sorted(Counter(row["serving_status"] for row in edge_rows).items())),
        "canonical_documents": len(node_rows),
        "documents_with_status_candidates": sum(bool(row["derived_status_candidate"]) for row in node_rows),
        "status_candidate_edges": sum(int(row["derived_status_candidate_count"] or 0) for row in node_rows),
    }
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
