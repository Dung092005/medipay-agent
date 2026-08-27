#!/usr/bin/env python3
"""Copy embedding input hashes from a local Qdrant artifact into PostgreSQL chunks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--artifact-dir", required=True, type=Path)
    args = parser.parse_args()
    load_dotenv()
    rows = [
        json.loads(line)
        for line in (args.artifact_dir / "passages.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    payload = [(str(row["input_sha256"]), args.dataset_id, str(row["passage_id"])) for row in rows]
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(url, autocommit=True) as connection:
        with connection.cursor() as cur:
            cur.executemany(
                """
                UPDATE chunks
                SET embedding_input_sha256 = %s
                WHERE dataset_id = %s AND chunk_id = %s
                """,
                payload,
            )
            filled = cur.execute(
                """
                SELECT count(*) FROM chunks
                WHERE dataset_id = %s AND embedding_input_sha256 <> ''
                """,
                (args.dataset_id,),
            ).fetchone()[0]
    print(f"updated={len(payload)} filled={filled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
