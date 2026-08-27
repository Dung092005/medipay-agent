#!/usr/bin/env python3
"""Load and verify the bigint-key shadow contract for one active release.

The loader is deliberately explicit: it refuses a missing dataset, copies only
canonical metadata/text hashes, never copies embeddings, and reports source vs
shadow counts plus identity/hash mismatches.  It can be rerun safely for the
same dataset.
"""

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


def _counts(connection: psycopg.Connection[Any], dataset_id: str, schema: str) -> dict[str, int]:
    tables = {
        "documents": f"{schema}.documents_shadow" if schema == "corpus" else "public.documents",
        "legal_units": f"{schema}.legal_units_shadow" if schema == "corpus" else "public.legal_units",
        "chunks": f"{schema}.chunks_shadow" if schema == "corpus" else "public.chunks",
    }
    return {
        name: int(connection.execute(f"SELECT count(*) FROM {table} WHERE dataset_id = %s", (dataset_id,)).fetchone()[0])
        for name, table in tables.items()
    }


def _copy(
    connection: psycopg.Connection[Any], dataset_id: str, *, timeout_seconds: int
) -> None:
    # Cascading removal of an existing 28k-unit shadow can exceed a managed
    # role's short default timeout.  Keep the longer budget local to this
    # explicit rehearsal transaction; it cannot alter the session or runtime
    # API timeout after commit/rollback.
    timeout = max(30, min(timeout_seconds, 900))
    connection.execute(
        "SELECT set_config('statement_timeout', %s, true)",
        (f"{timeout}s",),
    )
    connection.execute("DELETE FROM corpus.chunks_shadow WHERE dataset_id = %s", (dataset_id,))
    connection.execute("DELETE FROM corpus.legal_units_shadow WHERE dataset_id = %s", (dataset_id,))
    connection.execute("DELETE FROM corpus.documents_shadow WHERE dataset_id = %s", (dataset_id,))
    connection.execute(
        """
        INSERT INTO corpus.documents_shadow(dataset_id, source_document_id, title, text_sha256, categories, payload)
        SELECT dataset_id, id, title, text_sha256, categories, payload
        FROM public.documents WHERE dataset_id = %s
        """,
        (dataset_id,),
    )
    # Insert parents before children and resolve the bigint IDs by external key.
    connection.execute(
        """
        INSERT INTO corpus.legal_units_shadow(
            dataset_id, source_unit_id, document_internal_id, unit_type, label, heading,
            text, text_sha256, source_start, source_end
        )
        SELECT u.dataset_id, u.unit_id, d.internal_id, u.unit_type, u.label, u.heading,
               u.text, u.text_sha256, u.source_start, u.source_end
        FROM public.legal_units u
        JOIN corpus.documents_shadow d
          ON d.dataset_id = u.dataset_id AND d.source_document_id = u.document_id
        WHERE u.dataset_id = %s
        ORDER BY u.parent_unit_id NULLS FIRST, u.unit_id
        """,
        (dataset_id,),
    )
    connection.execute(
        """
        UPDATE corpus.legal_units_shadow child
        SET parent_internal_id = parent.internal_id
        FROM public.legal_units source
        JOIN corpus.legal_units_shadow parent
          ON parent.dataset_id = source.dataset_id AND parent.source_unit_id = source.parent_unit_id
        WHERE child.dataset_id = source.dataset_id
          AND child.source_unit_id = source.unit_id
          AND source.parent_unit_id IS NOT NULL
          AND source.dataset_id = %s
        """,
        (dataset_id,),
    )
    connection.execute(
        """
        INSERT INTO corpus.chunks_shadow(
            dataset_id, source_chunk_id, document_internal_id, unit_internal_id, source_key,
            chunk_order, section_title, text, text_sha256, embedding_input_sha256,
            lexical_eligible, semantic_eligible
        )
        SELECT c.dataset_id, c.chunk_id, d.internal_id, u.internal_id, c.source_key,
               c.chunk_order, c.section_title, c.text, c.text_sha256,
               c.embedding_input_sha256, c.lexical_eligible, c.semantic_eligible
        FROM public.chunks c
        JOIN corpus.documents_shadow d
          ON d.dataset_id = c.dataset_id AND d.source_document_id = c.document_id
        LEFT JOIN corpus.legal_units_shadow u
          ON u.dataset_id = c.dataset_id AND u.source_unit_id = NULLIF(c.unit_id, '')
        WHERE c.dataset_id = %s
        """,
        (dataset_id,),
    )


def _mismatches(connection: psycopg.Connection[Any], dataset_id: str) -> list[str]:
    rows = connection.execute(
        """
        SELECT 'document:' || p.id
        FROM public.documents p
        LEFT JOIN corpus.documents_shadow s
          ON s.dataset_id = p.dataset_id AND s.source_document_id = p.id
        WHERE p.dataset_id = %s AND (s.internal_id IS NULL OR s.title <> p.title OR s.text_sha256 <> p.text_sha256)
        UNION ALL
        SELECT 'chunk:' || p.chunk_id
        FROM public.chunks p
        LEFT JOIN corpus.chunks_shadow s
          ON s.dataset_id = p.dataset_id AND s.source_chunk_id = p.chunk_id
        WHERE p.dataset_id = %s AND (s.internal_id IS NULL OR s.text_sha256 <> p.text_sha256 OR s.embedding_input_sha256 <> p.embedding_input_sha256)
        LIMIT 100
        """,
        (dataset_id, dataset_id),
    ).fetchall()
    return [str(row[0]) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--dataset-id", default="")
    parser.add_argument("--rehearsal-id", default="shadow-rehearsal")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--copy-timeout-seconds", type=int, default=300)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.database_url or not args.dataset_id:
        parser.error("--database-url/DATABASE_URL and --dataset-id are required")
    with psycopg.connect(_url(args.database_url), autocommit=False) as connection:
        exists = connection.execute("SELECT 1 FROM public.datasets WHERE dataset_id = %s", (args.dataset_id,)).fetchone()
        if exists is None:
            raise SystemExit(f"dataset not found: {args.dataset_id}")
        if not args.verify_only:
            _copy(connection, args.dataset_id, timeout_seconds=args.copy_timeout_seconds)
        source = _counts(connection, args.dataset_id, "public")
        shadow = _counts(connection, args.dataset_id, "corpus")
        mismatches = _mismatches(connection, args.dataset_id)
        status = "parity" if source == shadow and not mismatches else "failed"
        connection.execute(
            """
            INSERT INTO ops.release_rehearsals(rehearsal_id, dataset_id, status, source_counts, shadow_counts, mismatches, verified_at)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, CASE WHEN %s = 'parity' THEN now() ELSE NULL END)
            ON CONFLICT (rehearsal_id) DO UPDATE SET status = excluded.status,
                source_counts = excluded.source_counts, shadow_counts = excluded.shadow_counts,
                mismatches = excluded.mismatches, verified_at = excluded.verified_at
            """,
            (args.rehearsal_id, args.dataset_id, status, json.dumps(source), json.dumps(shadow), json.dumps(mismatches), status),
        )
        connection.commit()
    report = {"status": status, "dataset_id": args.dataset_id, "source_counts": source, "shadow_counts": shadow, "mismatches": mismatches}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if status == "parity" else 1


if __name__ == "__main__":
    raise SystemExit(main())
