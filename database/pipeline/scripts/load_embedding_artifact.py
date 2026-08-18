#!/usr/bin/env python3
"""Load a previously generated .npy embedding artifact without a GPU."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()

from data_pipeline.storage import publish_dataset, ensure_dataset_vector_collection


def connection() -> psycopg.Connection:
    return psycopg.connect(
        host=os.getenv("PGHOST", "localhost"), port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "postgres"), user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"),
    )


def vector_literal(values: object) -> str:
    return "[" + ",".join(format(float(value), ".10g") for value in values) + "]"


def load_artifact(dataset_id: str, artifact_dir: str | Path) -> int:
    root = Path(artifact_dir)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    artifact_dataset_id = manifest.get("dataset_id") or manifest.get("release_id")
    if artifact_dataset_id != dataset_id:
        raise ValueError("artifact dataset_id does not match the requested release")
    vectors = np.load(root / "embeddings.float32.npy", mmap_mode="r")
    metadata = [json.loads(line) for line in (root / "passages.jsonl").read_text(encoding="utf-8").splitlines()]
    if vectors.ndim != 2 or vectors.shape[0] != len(metadata):
        raise ValueError("vector rows and passages metadata do not match")
    if vectors.shape[1] != int(manifest["dimensions"]):
        raise ValueError("vector dimensions do not match manifest")
    if len(metadata) != int(manifest["rows"]):
        raise ValueError("artifact row count does not match manifest")
    if not np.isfinite(vectors).all():
        raise ValueError("artifact contains non-finite vector values")

    model_name = str(manifest.get("model", "artifact"))
    dimensions = int(manifest["dimensions"])
    with connection() as conn:
        ensure_dataset_vector_collection(conn, dataset_id, dimensions=dimensions)
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM datasets WHERE dataset_id = %s", (dataset_id,))
            row = cur.fetchone()
            if row is None or row[0] != "staging":
                raise ValueError(f"release must exist and be staging, found {row!r}")
            updates = []
            for item, vector in zip(metadata, vectors, strict=True):
                input_hash = str(item.get("input_sha256", ""))
                if len(input_hash) != 64 or not all(c in "0123456789abcdef" for c in input_hash):
                    raise ValueError(f"invalid input_sha256 for {item.get('passage_id')}")
                updates.append((vector_literal(vector), model_name, dimensions, "artifact",
                                bool(manifest.get("normalized")), input_hash, dataset_id,
                                str(item["passage_id"])))
            update_sql = """UPDATE chunks
                       SET embedding = %s::extensions.vector, embedding_model = %s,
                                embedding_dimensions = %s, embedding_preprocessor = %s,
                                embedding_normalized = %s, embedded_input_sha256 = %s,
                                embedding_created_at = now()
                            WHERE dataset_id = %s AND chunk_id = %s"""
            for start in range(0, len(updates), 256):
                batch = updates[start : start + 256]
                cur.executemany(update_sql, batch)
                if cur.rowcount != len(batch):
                    raise ValueError(f"artifact batch updated {cur.rowcount} of {len(batch)} rows")
        conn.commit()
        publish_dataset(conn, dataset_id, require_embeddings=True)
    return len(metadata)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_id")
    parser.add_argument("artifact_dir", help="directory containing manifest.json and embeddings.float32.npy")
    args = parser.parse_args()
    print(f"Loaded and published {args.dataset_id}: {load_artifact(args.dataset_id, args.artifact_dir)} vectors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
