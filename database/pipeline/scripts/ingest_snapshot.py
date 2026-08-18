#!/usr/bin/env python3
"""Build the canonical CSV snapshot and stage it for database publication.

Usage:
    python scripts/ingest_snapshot.py --source-dir data/raw

Embedding is deliberately separate. The default command only stages and
validates the passage/relationship snapshot. An embedding worker creates the
release-scoped pgvector index and populates it before ``publish_dataset``
makes it visible to read APIs.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# Direct script execution sets sys.path to ``scripts/``; make the repository
# package importable without requiring an editable installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

load_dotenv()

from data_pipeline.canonical import build_snapshot
from data_pipeline.storage import ingest_canonical_snapshot, stage_canonical_snapshot


def connection() -> psycopg.Connection:
    return psycopg.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "postgres"),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", default=os.getenv("DATA_INPUT_DIR", "data/raw"))
    parser.add_argument("--dataset-id", help="Optional unique dataset identifier for a controlled rerun")
    parser.add_argument(
        "--publish-without-embeddings", action="store_true",
        help="Emergency passage-only publish; normal production flow embeds then calls publish_dataset.",
    )
    args = parser.parse_args()
    snapshot = build_snapshot(args.source_dir)
    with connection() as conn:
        if args.publish_without_embeddings:
            dataset_id, report = ingest_canonical_snapshot(
                conn, snapshot, dataset_id=args.dataset_id, require_embeddings=False,
            )
            state = "Activated without embeddings"
        else:
            dataset_id, report = stage_canonical_snapshot(conn, snapshot, dataset_id=args.dataset_id)
            state = "Staged (not active until vector embedding validates)"
    print(f"{state} release {dataset_id}")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
