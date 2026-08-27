#!/usr/bin/env python3
"""Apply a reviewed official-status metadata correction to active stores."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from neo4j import GraphDatabase
from psycopg.types.json import Jsonb

load_dotenv()

FIELDS = (
    "legal_status_verified", "tinh_trang_hieu_luc", "status_filter", "status_checked_at", "official_status_url",
    "official_status_result_title", "official_status_evidence_sha256", "official_status_verified_at",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--report-path", type=Path, required=True)
    args = parser.parse_args()
    with (args.source_dir / "metadata.csv").open(encoding="utf-8-sig", newline="") as handle:
        metadata = {row["id"]: dict(row) for row in csv.DictReader(handle)}
    merge_report = json.loads((args.source_dir / "TAVILY_STATUS_MERGE_REPORT.json").read_text(encoding="utf-8"))
    document_ids = list(merge_report["applied_status_document_ids"])
    rows = [{"id": identifier, **{field: metadata[identifier].get(field, "") for field in FIELDS}}
            for identifier in document_ids]

    database_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(database_url, connect_timeout=20) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT active_dataset_id FROM dataset_state WHERE singleton")
        if str(cursor.fetchone()[0]) != args.dataset_id:
            raise ValueError("active PostgreSQL dataset differs from requested hotfix dataset")
        cursor.executemany(
            """UPDATE documents SET payload=jsonb_set(payload, '{metadata}',
                   COALESCE(payload -> 'metadata', '{}'::jsonb) || %s::jsonb, true)
               WHERE dataset_id=%s AND id=%s""",
            [(Jsonb({field: row[field] for field in FIELDS}), args.dataset_id, row["id"]) for row in rows],
        )
        connection.commit()

    driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]))
    try:
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            session.run(
                """UNWIND $rows AS row MATCH (n:Document {dataset_id:$dataset_id, id:row.id})
                   SET n.legal_status=row.tinh_trang_hieu_luc, n.status_checked_at=row.status_checked_at,
                       n.legal_status_verified=row.legal_status_verified,
                       n.official_status_url=row.official_status_url,
                       n.official_status_evidence_sha256=row.official_status_evidence_sha256""",
                rows=rows, dataset_id=args.dataset_id,
            ).consume()
    finally:
        driver.close()
    report = {"dataset_id": args.dataset_id, "official_status_document_ids": document_ids, "updates": len(rows)}
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
