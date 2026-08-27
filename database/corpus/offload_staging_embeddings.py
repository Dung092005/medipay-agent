#!/usr/bin/env python3
"""Remove vectors from a staging Supabase release after verifying a local artifact.

This is the Free-tier bridge to an external vector store such as Qdrant. The
canonical text/chunks remain in PostgreSQL; only generated vectors and their
release-scoped HNSW indexes are removed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import psycopg
from dotenv import load_dotenv
from psycopg import sql

load_dotenv()


def connection(*, autocommit: bool = False) -> psycopg.Connection:
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    return psycopg.connect(url, connect_timeout=20, autocommit=autocommit)


def verify_artifact(root: Path, dataset_id: str) -> int:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("dataset_id") != dataset_id:
        raise ValueError("local embedding artifact belongs to another dataset")
    vectors = np.load(root / "embeddings.float32.npy", mmap_mode="r")
    rows = int(manifest["rows"])
    dimensions = int(manifest["dimensions"])
    if vectors.shape != (rows, dimensions) or not np.isfinite(vectors).all():
        raise ValueError("local embedding artifact is incomplete or invalid")
    passage_rows = sum(1 for _ in (root / "passages.jsonl").open(encoding="utf-8"))
    if passage_rows != rows:
        raise ValueError("artifact passage metadata count does not match vectors")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    artifact_rows = verify_artifact(args.artifact_dir, args.dataset_id)

    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM datasets WHERE dataset_id=%s FOR UPDATE", (args.dataset_id,))
        status = cur.fetchone()
        if status != ("staging",):
            raise ValueError(f"release must be staging, found {status!r}")
        cur.execute(
            """SELECT count(*) FILTER (WHERE semantic_eligible),
                      count(*) FILTER (WHERE semantic_eligible AND embedding IS NOT NULL)
               FROM chunks WHERE dataset_id=%s""",
            (args.dataset_id,),
        )
        semantic_rows, embedded_rows = map(int, cur.fetchone())
        if semantic_rows != artifact_rows or embedded_rows != artifact_rows:
            raise ValueError(
                f"artifact/live mismatch: artifact={artifact_rows}, semantic={semantic_rows}, embedded={embedded_rows}"
            )
        cur.execute(
            """SELECT indexname FROM pg_indexes
               WHERE schemaname='public' AND strpos(lower(indexdef), 'using hnsw') > 0
                 AND indexdef LIKE %s""",
            (f"%{args.dataset_id}%",),
        )
        indexes = [str(row[0]) for row in cur.fetchall()]
        for index_name in indexes:
            cur.execute(sql.SQL("DROP INDEX IF EXISTS {} CASCADE").format(sql.Identifier(index_name)))
        cur.execute(
            """UPDATE chunks SET embedding=NULL, embedding_model=NULL,
                      embedding_dimensions=NULL, embedding_preprocessor=NULL,
                      embedding_normalized=FALSE, embedded_input_sha256='',
                      embedding_created_at=NULL
               WHERE dataset_id=%s AND embedding IS NOT NULL""",
            (args.dataset_id,),
        )
        cleared = int(cur.rowcount)
        if cleared != artifact_rows:
            raise ValueError(f"cleared {cleared} vectors, expected {artifact_rows}")
        conn.commit()

    with connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SET statement_timeout = 0")
        cur.execute("VACUUM (FULL, ANALYZE) chunks")
        cur.execute("SELECT pg_database_size(current_database())")
        database_bytes = int(cur.fetchone()[0])
    print(json.dumps({
        "dataset_id": args.dataset_id,
        "artifact_rows_preserved_locally": artifact_rows,
        "vectors_cleared": cleared,
        "dropped_hnsw_indexes": indexes,
        "database_bytes_after": database_bytes,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
