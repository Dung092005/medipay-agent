#!/usr/bin/env python3
"""Import authoritative relationships.csv into Neo4j."""

from __future__ import annotations

import argparse
import csv
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from neo4j import GraphDatabase


def relationship_label(value: str) -> str:
    """Turn the source predicate into a readable Neo4j relationship type."""
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    safe = re.sub(r"[^A-Za-z0-9]+", "_", ascii_value).strip("_") or "RELATED"
    return f"REL_{safe[:50]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", default="data/raw")
    parser.add_argument("--dataset-id", required=True)
    args = parser.parse_args()
    rows = list(csv.DictReader((Path(args.source_dir) / "relationships.csv").open(encoding="utf-8-sig", newline="")))
    driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]))
    with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
        # Community edition supports single-property uniqueness constraints;
        # use a deterministic composite value instead of Enterprise NODE KEY.
        session.run("CREATE CONSTRAINT document_graph_id IF NOT EXISTS FOR (n:Document) REQUIRE n.graph_id IS UNIQUE")
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            relationship_type = row.get("relationship", "")
            grouped[relationship_label(relationship_type)].append({
            "source_graph_id": f"{args.dataset_id}:{row.get('doc_id', '')}",
            "target_graph_id": f"{args.dataset_id}:{row.get('other_doc_id', '')}",
            "source_id": row.get("doc_id", ""), "target_id": row.get("other_doc_id", ""),
            "source_title": row.get("source_title", ""), "target_title": row.get("target_title", ""),
            "relationship_id": f"{row.get('doc_id','')}|{row.get('other_doc_id','')}|{row.get('relationship','')}",
            "relationship_type": relationship_type,
            "adverse": row.get("relationship_is_adverse", "False").lower() == "true",
            })
        session.run("MATCH ()-[r]->() WHERE r.dataset_id=$dataset_id DELETE r", dataset_id=args.dataset_id).consume()
        for label, payload in grouped.items():
            session.run(f"""UNWIND $rows AS row
                MERGE (source:Document {{graph_id:row.source_graph_id}})
                ON CREATE SET source.dataset_id=$dataset_id, source.id=row.source_id, source.name=row.source_title
                MERGE (target:Document {{graph_id:row.target_graph_id}})
                ON CREATE SET target.dataset_id=$dataset_id, target.id=row.target_id, target.name=row.target_title
                MERGE (source)-[r:`{label}` {{dataset_id:$dataset_id, relationship_id:row.relationship_id}}]->(target)
                SET r.relationship_type=row.relationship_type, r.adverse=row.adverse""",
                rows=payload, dataset_id=args.dataset_id).consume()
    driver.close()
    print(f"Imported {len(rows)} relationships into Neo4j")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
