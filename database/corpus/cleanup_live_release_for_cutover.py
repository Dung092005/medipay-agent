#!/usr/bin/env python3
"""Delete one explicitly named live release and reclaim Supabase disk space."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

load_dotenv()

VACUUM_TABLES = (
    "chunks", "legal_units", "table_cells", "document_tables",
    "document_aliases", "documents", "datasets",
)


def clear_runtime_release_pointers(cursor, dataset_id: str) -> None:
    """Remove registry pointers that would block deleting the release row."""
    cursor.execute(
        "DELETE FROM ops.active_release "
        "WHERE active_dataset_id=%s OR previous_dataset_id=%s",
        (dataset_id, dataset_id),
    )


def connect(*, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(
        host=os.getenv("PGHOST", "localhost"), port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "postgres"), user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"), autocommit=autocommit,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-active", required=True)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument(
        "--resume-vacuum-only", action="store_true",
        help="Resume physical space reclamation after deletion committed but VACUUM timed out.",
    )
    args = parser.parse_args()
    if not args.backup_manifest.is_file():
        raise FileNotFoundError(args.backup_manifest)
    backup = json.loads(args.backup_manifest.read_text(encoding="utf-8"))
    backup_active = backup.get("postgres", {}).get("active_dataset_id") or backup.get("active_dataset_id")
    if backup_active and backup_active != args.expected_active:
        raise ValueError(f"Backup is for {backup_active}, expected {args.expected_active}")

    indexes: list[str] = []
    if args.resume_vacuum_only:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT active_dataset_id FROM dataset_state WHERE singleton")
            active = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM datasets WHERE dataset_id=%s", (args.expected_active,))
            remaining = int(cur.fetchone()[0])
        if active is not None or remaining:
            raise ValueError(
                f"Cannot resume cleanup: active={active!r}, deleted release rows={remaining}"
            )
    else:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('data_pipelineset_publication'))")
            cur.execute("SELECT active_dataset_id FROM dataset_state WHERE singleton FOR UPDATE")
            active = cur.fetchone()[0]
            if active != args.expected_active:
                raise ValueError(f"Active release changed: {active!r}")
            cur.execute(
                """SELECT indexname FROM pg_indexes
                   WHERE schemaname='public' AND strpos(lower(indexdef), 'using hnsw') > 0
                     AND indexdef LIKE %s""",
                (f"%{args.expected_active}%",),
            )
            indexes = [row[0] for row in cur.fetchall()]
            cur.execute("UPDATE dataset_state SET active_dataset_id=NULL, updated_at=now() WHERE singleton")
            clear_runtime_release_pointers(cur, args.expected_active)
            for index_name in indexes:
                cur.execute(sql.SQL("DROP INDEX IF EXISTS {} CASCADE").format(sql.Identifier(index_name)))
            cur.execute("DELETE FROM datasets WHERE dataset_id=%s", (args.expected_active,))
            if cur.rowcount != 1:
                raise ValueError("Expected exactly one dataset row to be deleted")
            conn.commit()

    # VACUUM FULL cannot run inside a transaction. It is necessary here because
    # plain DELETE/VACUUM leaves the physical files counted against Free quota.
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SET statement_timeout = 0")
        for table in VACUUM_TABLES:
            cur.execute(sql.SQL("VACUUM (FULL, ANALYZE) {} ").format(sql.Identifier(table)))
        cur.execute("SELECT pg_database_size(current_database())")
        database_bytes = int(cur.fetchone()[0])
        cur.execute("SELECT active_dataset_id FROM dataset_state WHERE singleton")
        active_after = cur.fetchone()[0]
    result = {
        "deleted_dataset_id": args.expected_active,
        "dropped_hnsw_indexes": indexes,
        "resumed_vacuum_only": args.resume_vacuum_only,
        "active_dataset_after": active_after,
        "database_bytes_after": database_bytes,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
