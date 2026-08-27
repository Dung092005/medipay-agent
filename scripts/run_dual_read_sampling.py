#!/usr/bin/env python3
"""Read-only source-vs-shadow sampling for the corpus-v2 cutover gate."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import psycopg


def _url(value: str) -> str:
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", value.strip())


def _sample_documents(connection: psycopg.Connection[Any], dataset_id: str, limit: int) -> list[str]:
    rows = connection.execute(
        """
        SELECT id FROM public.documents
        WHERE dataset_id = %s
        ORDER BY md5(id), id
        LIMIT %s
        """,
        (dataset_id, limit),
    ).fetchall()
    return [str(row[0]) for row in rows]


def _sample_chunks(connection: psycopg.Connection[Any], dataset_id: str, limit: int) -> list[str]:
    rows = connection.execute(
        """
        SELECT chunk_id FROM public.chunks
        WHERE dataset_id = %s
        ORDER BY md5(chunk_id), chunk_id
        LIMIT %s
        """,
        (dataset_id, limit),
    ).fetchall()
    return [str(row[0]) for row in rows]


def _compare_documents(connection: psycopg.Connection[Any], dataset_id: str, ids: list[str]) -> list[str]:
    if not ids:
        return []
    rows = connection.execute(
        """
        SELECT p.id, p.title, p.text_sha256, s.source_document_id, s.title, s.text_sha256
        FROM public.documents p
        LEFT JOIN corpus.documents_shadow s
          ON s.dataset_id = p.dataset_id AND s.source_document_id = p.id
        WHERE p.dataset_id = %s AND p.id = ANY(%s)
        """,
        (dataset_id, ids),
    ).fetchall()
    mismatches = []
    for row in rows:
        if row[3] is None or row[1] != row[4] or row[2] != row[5]:
            mismatches.append(f"document:{row[0]}")
    return mismatches


def _compare_chunks(connection: psycopg.Connection[Any], dataset_id: str, ids: list[str]) -> list[str]:
    if not ids:
        return []
    rows = connection.execute(
        """
        SELECT p.chunk_id, p.text_sha256, p.embedding_input_sha256,
               s.source_chunk_id, s.text_sha256, s.embedding_input_sha256
        FROM public.chunks p
        LEFT JOIN corpus.chunks_shadow s
          ON s.dataset_id = p.dataset_id AND s.source_chunk_id = p.chunk_id
        WHERE p.dataset_id = %s AND p.chunk_id = ANY(%s)
        """,
        (dataset_id, ids),
    ).fetchall()
    mismatches = []
    for row in rows:
        if row[3] is None or row[1] != row[4] or row[2] != row[5]:
            mismatches.append(f"chunk:{row[0]}")
    return mismatches


def run(database_url: str, dataset_id: str = "", sample_size: int = 100) -> dict[str, Any]:
    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    with psycopg.connect(_url(database_url), options="-c default_transaction_read_only=on") as connection:
        if not dataset_id:
            dataset_id = str(
                connection.execute(
                    "SELECT active_dataset_id FROM ops.active_release WHERE singleton"
                ).fetchone()[0]
            )
        document_ids = _sample_documents(connection, dataset_id, sample_size)
        chunk_ids = _sample_chunks(connection, dataset_id, sample_size)
        document_mismatches = _compare_documents(connection, dataset_id, document_ids)
        chunk_mismatches = _compare_chunks(connection, dataset_id, chunk_ids)
    mismatches = [*document_mismatches, *chunk_mismatches]
    return {
        "dataset_id": dataset_id,
        "sample_size_per_entity": sample_size,
        "sampled_documents": len(document_ids),
        "sampled_chunks": len(chunk_ids),
        "document_mismatches": document_mismatches,
        "chunk_mismatches": chunk_mismatches,
        "mismatch_count": len(mismatches),
        "pass": not mismatches and bool(document_ids) and bool(chunk_ids),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--dataset-id", default="")
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL or --database-url is required")
    report = run(args.database_url, args.dataset_id, args.sample_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
