#!/usr/bin/env python3
"""Build the additive Subject–Attribute–Temporal table-cell projection."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import psycopg

DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")


def _url(value: str) -> str:
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", value.strip())


def _date(value: str) -> str | None:
    match = DATE.search(value)
    if not match:
        return None
    day, month, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL or --database-url is required")
    with psycopg.connect(_url(args.database_url), autocommit=False) as connection:
        exists = connection.execute("SELECT 1 FROM public.datasets WHERE dataset_id = %s", (args.dataset_id,)).fetchone()
        if exists is None:
            raise SystemExit(f"dataset not found: {args.dataset_id}")
        connection.execute("DELETE FROM public.table_cell_facts WHERE dataset_id = %s", (args.dataset_id,))
        connection.execute(
            """
            INSERT INTO public.table_cell_facts(
                dataset_id, table_id, document_id, legal_unit_id, row_index, column_index, subject, attribute, value,
                effective_from, source_selector, source_fragment_sha256, value_sha256, payload
            )
            SELECT c.dataset_id, c.table_id, t.document_id,
                   COALESCE(NULLIF(c.payload ->> 'unit_id', ''), ''),
                   c.row_index, c.column_index,
                   COALESCE(NULLIF(trim(c.row_header), ''), NULLIF(trim(first_cell.value), ''), ''),
                   COALESCE(NULLIF(trim(c.header), ''), ''), c.value,
                   NULL, t.source_selector, t.source_fragment_sha256,
                   encode(sha256(convert_to(c.value, 'UTF8')), 'hex'), c.payload
            FROM public.table_cells c
            JOIN public.document_tables t
              ON t.dataset_id = c.dataset_id AND t.table_id = c.table_id
            LEFT JOIN LATERAL (
                SELECT value FROM public.table_cells first_cell
                WHERE first_cell.dataset_id = c.dataset_id
                  AND first_cell.table_id = c.table_id
                  AND first_cell.row_index = c.row_index
                ORDER BY first_cell.column_index
                LIMIT 1
            ) first_cell ON true
            WHERE c.dataset_id = %s
            """,
            (args.dataset_id,),
        )
        total = int(connection.execute("SELECT count(*) FROM public.table_cell_facts WHERE dataset_id = %s", (args.dataset_id,)).fetchone()[0])
        source = int(connection.execute("SELECT count(*) FROM public.table_cells WHERE dataset_id = %s", (args.dataset_id,)).fetchone()[0])
        empty_subject = int(connection.execute("SELECT count(*) FROM public.table_cell_facts WHERE dataset_id = %s AND subject = ''", (args.dataset_id,)).fetchone()[0])
        documents_covered = int(connection.execute("SELECT count(DISTINCT document_id) FROM public.table_cell_facts WHERE dataset_id = %s", (args.dataset_id,)).fetchone()[0])
        legal_units_anchored = int(connection.execute("SELECT count(*) FROM public.table_cell_facts WHERE dataset_id = %s AND legal_unit_id <> ''", (args.dataset_id,)).fetchone()[0])
        connection.commit()
    report: dict[str, Any] = {
        "dataset_id": args.dataset_id,
        "source_cells": source,
        "indexed_facts": total,
        "empty_subject": empty_subject,
        "documents_covered": documents_covered,
        "legal_units_anchored": legal_units_anchored,
        "parity": source == total,
        "algorithm": "subject=row_header; attribute=header; temporal=date-extension-v1",
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["parity"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
