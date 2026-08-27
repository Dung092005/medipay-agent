#!/usr/bin/env python3
"""Synchronize canonical/alias document identity metadata into one Neo4j release."""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def expected_nodes(source_dir: Path) -> list[dict[str, str]]:
    rows = [
        {
            "id": row["id"].strip(),
            "title": row.get("title", "").strip(),
            "so_ky_hieu": row.get("so_ky_hieu", "").strip(),
            "node_kind": "canonical_document",
        }
        for row in read_csv(source_dir / "metadata.csv")
    ]
    alias_path = source_dir / "aliases.csv"
    if alias_path.is_file():
        rows.extend(
            {
                "id": row["alias_document_id"].strip(),
                "title": row.get("alias_title", "").strip(),
                "so_ky_hieu": row.get("alias_signature", "").strip(),
                "node_kind": "document_alias",
            }
            for row in read_csv(alias_path)
        )
    if any(not row["id"] for row in rows) or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("metadata/aliases contain a missing or duplicate document ID")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    expected = expected_nodes(args.source_dir)
    expected_by_id = {row["id"]: row for row in expected}
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    try:
        with driver.session(database=database) as session:
            before = session.run(
                """MATCH (n:Document {dataset_id:$dataset_id})
                   WHERE n.id IN $ids
                   RETURN n.id AS id, n.node_kind AS node_kind, n.name AS name,
                          n.title AS title, n.so_ky_hieu AS so_ky_hieu""",
                dataset_id=args.dataset_id,
                ids=list(expected_by_id),
            ).data()
            actual_by_id = {str(row["id"]): row for row in before}
            missing = sorted(set(expected_by_id) - set(actual_by_id))
            wrong_kind = sorted(
                identifier
                for identifier, expected_row in expected_by_id.items()
                if identifier in actual_by_id
                and actual_by_id[identifier]["node_kind"] != expected_row["node_kind"]
            )
            if missing or wrong_kind:
                raise ValueError(
                    f"Neo4j release identity mismatch: missing={missing[:20]!r}, "
                    f"wrong_kind={wrong_kind[:20]!r}"
                )

            changed = [
                row for row in expected
                if actual_by_id[row["id"]].get("name") != row["title"]
                or actual_by_id[row["id"]].get("title") != row["title"]
                or actual_by_id[row["id"]].get("so_ky_hieu") != row["so_ky_hieu"]
            ]
            if args.apply and changed:
                with session.begin_transaction() as tx:
                    tx.run(
                        """UNWIND $rows AS row
                           MATCH (n:Document {dataset_id:$dataset_id, id:row.id})
                           SET n.name=row.title, n.title=row.title,
                               n.so_ky_hieu=row.so_ky_hieu""",
                        dataset_id=args.dataset_id,
                        rows=changed,
                    ).consume()
                    mismatch = tx.run(
                        """UNWIND $rows AS row
                           MATCH (n:Document {dataset_id:$dataset_id, id:row.id})
                           WITH row, n
                           WHERE n.name <> row.title OR n.title <> row.title
                              OR n.so_ky_hieu <> row.so_ky_hieu
                           RETURN count(*) AS count""",
                        dataset_id=args.dataset_id,
                        rows=expected,
                    ).single()
                    if int(mismatch["count"]):
                        raise ValueError(f"Neo4j metadata parity failed for {mismatch['count']} nodes")
                    tx.commit()
    finally:
        driver.close()

    audit = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "dataset_id": args.dataset_id,
        "source_dir": str(args.source_dir),
        "mode": "applied" if args.apply else "dry_run",
        "expected_nodes": len(expected),
        "changed_nodes": len(changed),
        "before": before if args.apply else [],
    }
    args.audit_output.parent.mkdir(parents=True, exist_ok=True)
    args.audit_output.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: audit[key] for key in ("dataset_id", "mode", "expected_nodes", "changed_nodes")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
