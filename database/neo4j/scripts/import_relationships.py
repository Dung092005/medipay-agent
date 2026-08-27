#!/usr/bin/env python3
"""Atomically import one reconciled relationship release into Neo4j."""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase

# Windows C long cannot hold sys.maxsize; use the largest safe limit.
try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(int(1e9))
load_dotenv()


def relationship_label(value: str) -> str:
    """Turn the source predicate into a bounded readable Neo4j type."""

    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    safe = re.sub(r"[^A-Za-z0-9]+", "_", ascii_value).strip("_") or "RELATED"
    return f"REL_{safe[:50]}"


def source_bool(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"true", "1", "yes", "y"}


def read_rows(path: Path, *, required: bool = True) -> list[dict[str, str]]:
    if not path.is_file():
        if required:
            raise FileNotFoundError(path)
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def prepare(source_dir: Path, dataset_id: str) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    relationship_rows = read_rows(source_dir / "relationships.csv")
    metadata_rows = read_rows(source_dir / "metadata.csv", required=False)
    alias_rows = read_rows(source_dir / "aliases.csv", required=False)

    nodes: dict[str, dict[str, Any]] = {}
    for row in metadata_rows:
        identifier = row.get("id", "").strip()
        if not identifier or identifier in nodes:
            raise ValueError(f"metadata.csv has missing/duplicate id: {identifier!r}")
        nodes[identifier] = {
            "graph_id": f"{dataset_id}:{identifier}",
            "id": identifier,
            "name": row.get("title", ""),
            "title": row.get("title", ""),
            "so_ky_hieu": row.get("so_ky_hieu", ""),
            "node_kind": "canonical_document",
            "categories": [part for part in row.get("agent_category", "").split(",") if part],
            "retrieval_scope": row.get("retrieval_scope", ""),
            "legal_status": row.get("tinh_trang_hieu_luc", ""),
            "status_checked_at": row.get("status_checked_at", ""),
            "derived_status_candidate": row.get("derived_status_candidate", ""),
            "derived_status_candidate_count": row.get("derived_status_candidate_count", ""),
        }

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    relationship_ids: set[str] = set()
    edge_keys: set[tuple[str, str, str]] = set()
    for row_number, row in enumerate(relationship_rows, start=2):
        source_id = row.get("doc_id", "").strip()
        target_id = row.get("other_doc_id", "").strip()
        relationship_type = row.get("relationship", "").strip()
        if not source_id or not target_id or not relationship_type or source_id == target_id:
            raise ValueError(f"relationships.csv row {row_number} has an invalid edge")
        edge_key = (source_id, target_id, relationship_type)
        if edge_key in edge_keys:
            raise ValueError(f"relationships.csv has duplicate edge: {edge_key}")
        edge_keys.add(edge_key)
        relationship_id = row.get("relationship_id", "").strip() or "|".join(edge_key)
        if relationship_id in relationship_ids:
            raise ValueError(f"relationships.csv has duplicate relationship_id: {relationship_id}")
        relationship_ids.add(relationship_id)
        for identifier, title in ((source_id, row.get("source_title", "")), (target_id, row.get("target_title", ""))):
            nodes.setdefault(identifier, {
                "graph_id": f"{dataset_id}:{identifier}",
                "id": identifier,
                "name": title,
                "title": title,
                "so_ky_hieu": "",
                "node_kind": "reference_only",
                "categories": [],
                "retrieval_scope": "reference_only",
            })
        grouped[relationship_label(relationship_type)].append({
            "source_graph_id": f"{dataset_id}:{source_id}",
            "target_graph_id": f"{dataset_id}:{target_id}",
            "relationship_id": relationship_id,
            "relationship_type": relationship_type,
            "source_title": row.get("source_title", ""),
            "target_title": row.get("target_title", ""),
            "categories": [part for part in row.get("agent_category", "").split(",") if part],
            "adverse": source_bool(row.get("relationship_is_adverse")),
            "source_is_selected": source_bool(row.get("source_is_selected")),
            "target_is_selected": source_bool(row.get("target_is_selected")),
            "provenance_status": row.get("provenance_status", ""),
            "adverse_provenance": row.get("adverse_provenance", ""),
            # These fields are optional for legacy imported edges, but required
            # to keep model-extracted candidate edges auditable in Neo4j.
            "relation_confidence": row.get("relation_confidence", ""),
            "relation_status": row.get("relation_status", ""),
            "evidence_text": row.get("evidence_text", ""),
            "evidence_start": row.get("evidence_start", ""),
            "evidence_end": row.get("evidence_end", ""),
            "evidence_sha256": row.get("evidence_sha256", ""),
            "target_signature": row.get("target_signature", ""),
            "target_resolution": row.get("target_resolution", ""),
            "scope": row.get("scope", ""),
            "effective_date_text": row.get("effective_date_text", ""),
            "model_name": row.get("model_name", ""),
            "model_prompt_sha256": row.get("model_prompt_sha256", ""),
            "validity_impact_candidate": row.get("validity_impact_candidate", ""),
            "target_official_url": row.get("target_official_url", ""),
            "target_official_evidence_sha256": row.get("target_official_evidence_sha256", ""),
            "serving_status": row.get("serving_status", "audit_only_unverified"),
            "serving_qualification": row.get("serving_qualification", "not_qualified"),
        })

    aliases: list[dict[str, Any]] = []
    seen_aliases: set[str] = set()
    for row_number, row in enumerate(alias_rows, start=2):
        alias_id = row.get("alias_document_id", "").strip()
        canonical_id = row.get("canonical_document_id", "").strip()
        if (
            not alias_id
            or alias_id == canonical_id
            or alias_id in seen_aliases
            or alias_id in nodes
            or canonical_id not in nodes
        ):
            raise ValueError(f"aliases.csv row {row_number} is invalid")
        seen_aliases.add(alias_id)
        nodes[alias_id] = {
            "graph_id": f"{dataset_id}:{alias_id}",
            "id": alias_id,
            "name": row.get("alias_title", ""),
            "title": row.get("alias_title", ""),
            "so_ky_hieu": row.get("alias_signature", ""),
            "node_kind": "document_alias",
            "categories": [],
            "retrieval_scope": "alias_only",
        }
        aliases.append({
            "alias_graph_id": f"{dataset_id}:{alias_id}",
            "canonical_graph_id": f"{dataset_id}:{canonical_id}",
            "relationship_id": f"alias:{alias_id}:{canonical_id}",
            "alias_type": row.get("alias_type", ""),
            "confidence": row.get("confidence", ""),
            "reason": row.get("reason", ""),
            "evidence_url": row.get("evidence_url", ""),
        })
    return list(nodes.values()), grouped, aliases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", default="data/clean/medical_active_v2")
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Atomically replace an already imported dataset ID; normally each release ID is new.",
    )
    args = parser.parse_args()
    source_dir = Path(args.source_dir)
    nodes, grouped, aliases = prepare(source_dir, args.dataset_id)
    relationship_count = sum(len(rows) for rows in grouped.values())
    expected_relationship_types = Counter(
        row["relationship_type"]
        for rows in grouped.values()
        for row in rows
    )
    expected_node_kinds = Counter(row["node_kind"] for row in nodes)

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    with driver.session(database=database) as session:
        session.run(
            "CREATE CONSTRAINT document_graph_id IF NOT EXISTS FOR (n:Document) REQUIRE n.graph_id IS UNIQUE"
        ).consume()
        existing_record = session.run(
            """MATCH (n:Document {dataset_id:$dataset_id})
               WITH count(n) AS nodes
               OPTIONAL MATCH ()-[r]->() WHERE r.dataset_id=$dataset_id
               RETURN nodes, count(r) AS relationships""",
            dataset_id=args.dataset_id,
        ).single()
        existing_nodes = int(existing_record["nodes"])
        existing_relationships = int(existing_record["relationships"])
        if (existing_nodes or existing_relationships) and not args.replace_existing:
            raise ValueError(
                f"Dataset {args.dataset_id} already has {existing_nodes} nodes and "
                f"{existing_relationships} relationships; use --replace-existing for an atomic retry"
            )

        # One transaction means a failed retry restores the previous graph for
        # this dataset instead of leaving a partially imported release.
        with session.begin_transaction() as tx:
            if existing_nodes or existing_relationships:
                tx.run("MATCH ()-[r]->() WHERE r.dataset_id=$dataset_id DELETE r", dataset_id=args.dataset_id).consume()
                tx.run("MATCH (n:Document {dataset_id:$dataset_id}) DETACH DELETE n", dataset_id=args.dataset_id).consume()
            tx.run(
                """UNWIND $rows AS row
                MERGE (n:Document {graph_id:row.graph_id})
                SET n.dataset_id=$dataset_id, n.id=row.id, n.name=row.name,
                    n.title=row.title, n.so_ky_hieu=row.so_ky_hieu,
                    n.node_kind=row.node_kind, n.categories=row.categories,
                    n.retrieval_scope=row.retrieval_scope,
                    n.legal_status=row.legal_status,
                    n.status_checked_at=row.status_checked_at,
                    n.derived_status_candidate=row.derived_status_candidate,
                    n.derived_status_candidate_count=row.derived_status_candidate_count""",
                rows=nodes,
                dataset_id=args.dataset_id,
            ).consume()
            for label, payload in grouped.items():
                tx.run(
                    f"""UNWIND $rows AS row
                    MATCH (source:Document {{graph_id:row.source_graph_id}})
                    MATCH (target:Document {{graph_id:row.target_graph_id}})
                    CREATE (source)-[r:`{label}` {{dataset_id:$dataset_id, relationship_id:row.relationship_id}}]->(target)
                    SET r.relationship_type=row.relationship_type, r.categories=row.categories,
                        r.source_title=row.source_title, r.target_title=row.target_title,
                        r.adverse=row.adverse, r.source_is_selected=row.source_is_selected,
                        r.target_is_selected=row.target_is_selected,
                        r.provenance_status=row.provenance_status,
                        r.adverse_provenance=row.adverse_provenance,
                        r.relation_confidence=row.relation_confidence,
                        r.relation_status=row.relation_status,
                        r.evidence_text=row.evidence_text,
                        r.evidence_start=row.evidence_start,
                        r.evidence_end=row.evidence_end,
                        r.evidence_sha256=row.evidence_sha256,
                        r.target_signature=row.target_signature,
                        r.target_resolution=row.target_resolution,
                        r.scope=row.scope,
                        r.effective_date_text=row.effective_date_text,
                        r.model_name=row.model_name,
                        r.model_prompt_sha256=row.model_prompt_sha256,
                        r.validity_impact_candidate=row.validity_impact_candidate,
                        r.target_official_url=row.target_official_url,
                        r.target_official_evidence_sha256=row.target_official_evidence_sha256,
                        r.serving_status=row.serving_status,
                        r.serving_qualification=row.serving_qualification""",
                    rows=payload,
                    dataset_id=args.dataset_id,
                ).consume()
            if aliases:
                tx.run(
                    """UNWIND $rows AS row
                    MATCH (alias:Document {graph_id:row.alias_graph_id})
                    MATCH (canonical:Document {graph_id:row.canonical_graph_id})
                    CREATE (alias)-[r:ALIAS_OF {dataset_id:$dataset_id, relationship_id:row.relationship_id}]->(canonical)
                    SET r.alias_type=row.alias_type, r.confidence=row.confidence,
                        r.reason=row.reason, r.evidence_url=row.evidence_url""",
                    rows=aliases,
                    dataset_id=args.dataset_id,
                ).consume()

            # Validate inside the same transaction. Any mismatch raises before
            # commit, leaving an earlier import of this release untouched.
            actual_nodes = {
                str(record["node_kind"]): int(record["count"])
                for record in tx.run(
                    """MATCH (n:Document {dataset_id:$dataset_id})
                       RETURN n.node_kind AS node_kind, count(n) AS count""",
                    dataset_id=args.dataset_id,
                )
            }
            actual_relationship_types = {
                str(record["relationship_type"]): int(record["count"])
                for record in tx.run(
                    """MATCH ()-[r]->()
                       WHERE r.dataset_id=$dataset_id AND type(r) <> 'ALIAS_OF'
                       RETURN r.relationship_type AS relationship_type, count(r) AS count""",
                    dataset_id=args.dataset_id,
                )
            }
            relationship_integrity = tx.run(
                """MATCH (source:Document)-[r]->(target:Document)
                   WHERE r.dataset_id=$dataset_id AND type(r) <> 'ALIAS_OF'
                   RETURN count(r) AS count,
                          count(DISTINCT r.relationship_id) AS distinct_ids,
                          count(CASE WHEN source.dataset_id <> $dataset_id
                                          OR target.dataset_id <> $dataset_id
                                     THEN 1 END) AS cross_release""",
                dataset_id=args.dataset_id,
            ).single()
            alias_integrity = tx.run(
                """MATCH (source:Document)-[r:ALIAS_OF]->(target:Document)
                   WHERE r.dataset_id=$dataset_id
                   RETURN count(r) AS count,
                          count(DISTINCT r.relationship_id) AS distinct_ids,
                          count(CASE WHEN source.dataset_id <> $dataset_id
                                          OR target.dataset_id <> $dataset_id
                                     THEN 1 END) AS cross_release""",
                dataset_id=args.dataset_id,
            ).single()

            failures: list[str] = []
            if actual_nodes != dict(expected_node_kinds):
                failures.append(f"node kinds {actual_nodes!r} != {dict(expected_node_kinds)!r}")
            if actual_relationship_types != dict(expected_relationship_types):
                failures.append(
                    f"relationship types {actual_relationship_types!r} != "
                    f"{dict(expected_relationship_types)!r}"
                )
            if (
                int(relationship_integrity["count"]) != relationship_count
                or int(relationship_integrity["distinct_ids"]) != relationship_count
                or int(relationship_integrity["cross_release"]) != 0
            ):
                failures.append(f"legal relationship integrity {dict(relationship_integrity)!r}")
            if (
                int(alias_integrity["count"]) != len(aliases)
                or int(alias_integrity["distinct_ids"]) != len(aliases)
                or int(alias_integrity["cross_release"]) != 0
            ):
                failures.append(f"alias relationship integrity {dict(alias_integrity)!r}")
            if failures:
                raise ValueError("Neo4j parity gate failed: " + "; ".join(failures))
            tx.commit()
    driver.close()
    print(
        f"Imported dataset {args.dataset_id}: {len(nodes)} nodes, "
        f"{relationship_count} legal relationships, {len(aliases)} aliases"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
