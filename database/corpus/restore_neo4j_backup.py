#!/usr/bin/env python3
"""Restore a graph JSON backup into a disposable Neo4j target.

The backup is produced by ``backup_live_release.py``.  The command refuses a
non-empty target by default, imports the exported ``Document`` nodes and
relationship types, then verifies node/relationship counts before committing.
It is deliberately a restore tool, not a runtime graph import path.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

SAFE_RELATIONSHIP = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def read_backup(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("nodes"), list) or not isinstance(payload.get("relationships"), list):
        raise ValueError("Neo4j backup must contain nodes and relationships arrays")
    return payload


def _validate(payload: dict[str, Any], expected_dataset_id: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = payload["nodes"]
    relationships = payload["relationships"]
    graph_ids = [str(node.get("properties", {}).get("graph_id", "")) for node in nodes]
    if any(not value for value in graph_ids) or len(set(graph_ids)) != len(graph_ids):
        raise ValueError("backup contains missing or duplicate graph_id values")
    dataset_ids = {
        str(node.get("properties", {}).get("dataset_id", ""))
        for node in nodes
        if node.get("properties", {}).get("dataset_id")
    }
    if expected_dataset_id and expected_dataset_id not in dataset_ids:
        raise ValueError(f"backup does not contain expected dataset {expected_dataset_id}")
    known = set(graph_ids)
    for relationship in relationships:
        source = str(relationship.get("source_graph_id", ""))
        target = str(relationship.get("target_graph_id", ""))
        rel_type = str(relationship.get("type", ""))
        if source not in known or target not in known:
            raise ValueError("relationship references a missing graph node")
        if not SAFE_RELATIONSHIP.fullmatch(rel_type):
            raise ValueError(f"unsafe relationship type: {rel_type!r}")
    return nodes, relationships


def restore(
    uri: str,
    username: str,
    password: str,
    database: str,
    backup: Path,
    *,
    expected_dataset_id: str | None = None,
    allow_nonempty: bool = False,
) -> dict[str, Any]:
    payload = read_backup(backup)
    nodes, relationships = _validate(payload, expected_dataset_id)
    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        with driver.session(database=database) as session:
            existing = session.run("MATCH (n) RETURN count(n) AS nodes").single()
            if int(existing["nodes"] or 0) and not allow_nonempty:
                raise RuntimeError("refusing non-empty target; use --allow-nonempty for a reviewed fixture")
            with session.begin_transaction() as tx:
                if allow_nonempty:
                    tx.run("MATCH (n) DETACH DELETE n").consume()
                tx.run(
                    """UNWIND $rows AS row
                    CREATE (n:Document)
                    SET n = row.properties""",
                    rows=nodes,
                ).consume()
                grouped: dict[str, list[dict[str, Any]]] = {}
                for relationship in relationships:
                    grouped.setdefault(str(relationship["type"]), []).append(relationship)
                for rel_type, rows in grouped.items():
                    tx.run(
                        f"""UNWIND $rows AS row
                        MATCH (source:Document {{graph_id: row.source_graph_id}})
                        MATCH (target:Document {{graph_id: row.target_graph_id}})
                        CREATE (source)-[r:`{rel_type}`]->(target)
                        SET r = row.properties""",
                        rows=rows,
                    ).consume()
                actual = tx.run(
                    """MATCH (n) WITH count(n) AS nodes
                    OPTIONAL MATCH ()-[r]->()
                    RETURN nodes, count(r) AS relationships"""
                ).single()
                if int(actual["nodes"]) != len(nodes) or int(actual["relationships"]) != len(relationships):
                    raise RuntimeError(
                        f"restore parity mismatch nodes={actual['nodes']}/{len(nodes)} "
                        f"relationships={actual['relationships']}/{len(relationships)}"
                    )
    finally:
        driver.close()
    return {
        "nodes": len(nodes),
        "relationships": len(relationships),
        "relationship_types": dict(Counter(str(row["type"]) for row in relationships)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI", ""))
    parser.add_argument("--neo4j-username", default=os.getenv("NEO4J_USERNAME", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD", ""))
    parser.add_argument("--neo4j-database", default=os.getenv("NEO4J_DATABASE", "neo4j"))
    parser.add_argument("--dataset-id")
    parser.add_argument("--allow-nonempty", action="store_true")
    args = parser.parse_args()
    if not args.neo4j_uri or not args.neo4j_password:
        parser.error("Neo4j URI and password are required")
    print(json.dumps(restore(
        args.neo4j_uri,
        args.neo4j_username,
        args.neo4j_password,
        args.neo4j_database,
        args.backup,
        expected_dataset_id=args.dataset_id,
        allow_nonempty=args.allow_nonempty,
    ), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
