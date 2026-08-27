#!/usr/bin/env python3
"""Apply reviewed identity/status corrections without rebuilding embeddings."""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from neo4j import GraphDatabase
from psycopg.types.json import Jsonb

load_dotenv()

SYNC_METADATA_FIELDS = (
    "title", "so_ky_hieu", "content_validation_status", "legal_status_verified",
    "answer_ready", "tinh_trang_hieu_luc", "status_checked_at", "status_filter",
    "official_status_url", "official_status_result_title",
    "official_status_evidence_sha256", "official_status_verified_at",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def postgres_connection() -> psycopg.Connection[Any]:
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    return psycopg.connect(url, connect_timeout=20, application_name="reviewed-metadata-hotfix")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    metadata = {row["id"]: row for row in read_csv(args.source_dir / "metadata.csv")}
    relationships = read_csv(args.source_dir / "relationships.csv")
    aliases = read_csv(args.source_dir / "aliases.csv")
    correction_report = json.loads(
        (args.source_dir / "REVIEWED_CORRECTIONS_REPORT.json").read_text(encoding="utf-8")
    )
    identity_changes = correction_report["identity_changes"]
    status_changes = correction_report["reviewed_status_changes"]
    changed_ids = sorted({row["id"] for row in identity_changes + status_changes})
    update_rows = [
        {"id": identifier, **{field: metadata[identifier].get(field, "") for field in SYNC_METADATA_FIELDS}}
        for identifier in changed_ids
    ]
    expected_old = {row["id"]: row for row in identity_changes}
    expected_old_status = {row["id"]: row["old_status"] for row in status_changes}

    with postgres_connection() as connection, connection.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute("SELECT active_dataset_id FROM dataset_state WHERE singleton")
        active_dataset_id = str(cursor.fetchone()["active_dataset_id"])
        if active_dataset_id != args.dataset_id:
            raise ValueError(f"active PostgreSQL dataset is {active_dataset_id}, expected {args.dataset_id}")
        cursor.execute(
            """SELECT id, title, payload FROM documents
               WHERE dataset_id=%s AND id=ANY(%s) ORDER BY id""",
            (args.dataset_id, changed_ids),
        )
        postgres_before = [dict(row) for row in cursor]
        if len(postgres_before) != len(changed_ids):
            raise ValueError("one or more corrected PostgreSQL documents are missing")
        for row in postgres_before:
            expected = expected_old.get(str(row["id"]))
            if not expected:
                continue
            old_signature = str((row["payload"].get("metadata") or {}).get("so_ky_hieu", ""))
            if row["title"] != expected["old_title"] or old_signature != expected["old_so_ky_hieu"]:
                raise ValueError(f"live PostgreSQL identity changed unexpectedly for {row['id']}")
        for row in postgres_before:
            expected_status = expected_old_status.get(str(row["id"]))
            actual_status = str((row["payload"].get("metadata") or {}).get("tinh_trang_hieu_luc", ""))
            if expected_status is not None and actual_status != expected_status:
                raise ValueError(f"live PostgreSQL status changed unexpectedly for {row['id']}")

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    try:
        with driver.session(database=database) as session:
            neo4j_before = session.run(
                """MATCH (n:Document {dataset_id:$dataset_id}) WHERE n.id IN $ids
                   RETURN n.id AS id, properties(n) AS properties ORDER BY n.id""",
                dataset_id=args.dataset_id, ids=changed_ids,
            ).data()
            if len(neo4j_before) != len(changed_ids):
                raise ValueError("one or more corrected Neo4j documents are missing")
            for row in neo4j_before:
                expected = expected_old.get(str(row["id"]))
                props = row["properties"]
                if expected and (
                    props.get("title") != expected["old_title"]
                    or props.get("so_ky_hieu") != expected["old_so_ky_hieu"]
                ):
                    raise ValueError(f"live Neo4j identity changed unexpectedly for {row['id']}")
                expected_status = expected_old_status.get(str(row["id"]))
                if expected_status is not None and props.get("legal_status") != expected_status:
                    raise ValueError(f"live Neo4j status changed unexpectedly for {row['id']}")

            if args.apply:
                with session.begin_transaction() as tx:
                    tx.run(
                        """UNWIND $rows AS row
                           MATCH (n:Document {dataset_id:$dataset_id, id:row.id})
                           SET n.name=row.title, n.title=row.title, n.so_ky_hieu=row.so_ky_hieu,
                               n.content_validation_status=row.content_validation_status,
                               n.legal_status_verified=row.legal_status_verified,
                               n.answer_ready=row.answer_ready,
                               n.legal_status=row.tinh_trang_hieu_luc,
                               n.status_checked_at=row.status_checked_at,
                               n.official_status_url=row.official_status_url,
                               n.official_status_evidence_sha256=row.official_status_evidence_sha256""",
                        dataset_id=args.dataset_id, rows=update_rows,
                    ).consume()
                    tx.run(
                        """UNWIND $rows AS row
                           MATCH ()-[r]->() WHERE r.dataset_id=$dataset_id
                             AND r.relationship_id=row.relationship_id
                           SET r.source_title=row.source_title, r.target_title=row.target_title""",
                        dataset_id=args.dataset_id,
                        rows=[{
                            "relationship_id": row["relationship_id"],
                            "source_title": row.get("source_title", ""),
                            "target_title": row.get("target_title", ""),
                        } for row in relationships],
                    ).consume()
                    tx.commit()
    finally:
        driver.close()

    if args.apply:
        with postgres_connection() as connection, connection.cursor() as cursor:
            cursor.executemany(
                """UPDATE documents
                   SET title=%s,
                       payload=jsonb_set(payload, '{metadata}',
                           COALESCE(payload -> 'metadata', '{}'::jsonb) || %s::jsonb, true)
                   WHERE dataset_id=%s AND id=%s""",
                [(row["title"], Jsonb({field: row[field] for field in SYNC_METADATA_FIELDS}),
                  args.dataset_id, row["id"]) for row in update_rows],
            )
            for alias in aliases:
                cursor.execute(
                    """UPDATE document_aliases
                       SET payload=payload || %s::jsonb
                       WHERE dataset_id=%s AND alias_document_id=%s""",
                    (Jsonb({
                        "canonical_title": alias.get("canonical_title", ""),
                        "canonical_signature": alias.get("canonical_signature", ""),
                    }), args.dataset_id, alias["alias_document_id"]),
                )
            connection.commit()

    audit = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "applied" if args.apply else "dry_run",
        "dataset_id": args.dataset_id,
        "source_dir": str(args.source_dir),
        "changed_document_ids": changed_ids,
        "identity_changes": len(identity_changes),
        "status_changes": len(status_changes),
        "relationship_titles_checked": len(relationships),
        "postgres_before": postgres_before,
        "neo4j_before": neo4j_before,
    }
    args.audit_output.parent.mkdir(parents=True, exist_ok=True)
    args.audit_output.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: audit[key] for key in (
        "mode", "dataset_id", "identity_changes", "status_changes", "relationship_titles_checked"
    )}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
