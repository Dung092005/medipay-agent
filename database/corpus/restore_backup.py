#!/usr/bin/env python3
"""Restore a release-scoped JSON backup into a disposable PostgreSQL target.

The command is intentionally conservative: it refuses a non-empty target by
default, writes only tables present in the backup, and prints counts rather
than any user/content payload.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

TABLE_ORDER = (
    "datasets", "dataset_state", "release_projections", "users", "documents", "legal_units",
    "document_tables", "table_cells", "document_aliases", "chunks",
    "conversations", "conversation_turns",
)
SAFE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


def read_backup(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def _connection(url: str) -> psycopg.Connection[Any]:
    return psycopg.connect(
        re.sub(r"^postgresql\+asyncpg://", "postgresql://", url),
        connect_timeout=20,
        application_name="medipay-backup-restore",
    )


def _json_columns(connection: psycopg.Connection[Any], table: str) -> set[str]:
    rows = connection.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND data_type = 'jsonb'
        """,
        (table,),
    ).fetchall()
    return {str(row[0]) for row in rows}


def _insert_rows(connection: psycopg.Connection[Any], table: str, rows: list[dict[str, Any]]) -> int:
    if not SAFE_NAME.fullmatch(table) or not rows:
        return 0
    columns = [str(key) for key in rows[0] if SAFE_NAME.fullmatch(str(key))]
    available = {
        str(row[0])
        for row in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
            (table,),
        ).fetchall()
    }
    columns = [column for column in columns if column in available]
    if not columns:
        return 0
    json_columns = _json_columns(connection, table)
    statement = sql.SQL("INSERT INTO public.{table} ({columns}) VALUES ({values}) ON CONFLICT DO NOTHING").format(
        table=sql.Identifier(table),
        columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        values=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    values = []
    for row in rows:
        values.append(
            tuple(Jsonb(row[column]) if column in json_columns and row[column] is not None else row.get(column) for column in columns)
        )
    with connection.cursor() as cursor:
        cursor.executemany(statement, values)
    return len(values)


def _insert_legal_units(connection: psycopg.Connection[Any], rows: list[dict[str, Any]]) -> int:
    """Insert the self-referencing legal-unit tree parent before child."""
    pending = list(rows)
    inserted = 0
    known: set[str] = set()
    while pending:
        ready = [
            row for row in pending
            if not row.get("parent_unit_id") or str(row.get("parent_unit_id")) in known
        ]
        if not ready:
            raise RuntimeError("legal_units backup contains an unresolved parent reference")
        inserted += _insert_rows(connection, "legal_units", ready)
        known.update(str(row.get("unit_id")) for row in ready if row.get("unit_id"))
        pending = [row for row in pending if row not in ready]
    return inserted


def restore(database_url: str, backup_path: Path, *, allow_nonempty: bool = False) -> dict[str, Any]:
    backup = read_backup(backup_path)
    tables = backup.get("tables") or {}
    with _connection(database_url) as connection:
        if not allow_nonempty:
            existing = connection.execute("SELECT count(*) FROM public.datasets").fetchone()[0]
            if int(existing):
                raise RuntimeError("refusing non-empty target; use --allow-nonempty only for a reviewed fixture")
        restore_tables: dict[str, int] = {}
        for table in TABLE_ORDER:
            rows = tables.get(table) or []
            if table == "datasets" and backup.get("dataset"):
                rows = [backup["dataset"]]
            if table == "dataset_state" and backup.get("dataset_state"):
                rows = [backup["dataset_state"]]
            if not rows:
                continue
            connection.execute(sql.SQL("ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY").format(table=sql.Identifier(table)))
            restore_tables[table] = (
                _insert_legal_units(connection, rows)
                if table == "legal_units"
                else _insert_rows(connection, table, rows)
            )
            connection.execute(sql.SQL("ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY").format(table=sql.Identifier(table)))
        if backup.get("active_dataset_id"):
            connection.execute(
                "UPDATE public.dataset_state SET active_dataset_id = %s WHERE singleton",
                (str(backup["active_dataset_id"]),),
            )
        connection.commit()
        active = connection.execute("SELECT active_dataset_id FROM public.dataset_state WHERE singleton").fetchone()
    return {"active_dataset_id": str(active[0]) if active else "", "insert_attempts": restore_tables}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("RESTORE_DATABASE_URL") or os.getenv("DATABASE_URL", ""))
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--allow-nonempty", action="store_true")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("RESTORE_DATABASE_URL or DATABASE_URL is required")
    print(json.dumps(restore(args.database_url, args.backup, allow_nonempty=args.allow_nonempty), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
