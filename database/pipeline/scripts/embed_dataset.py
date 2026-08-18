#!/usr/bin/env python3
"""Embed a staged release with OpenAI text-embedding-3-small."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()

from data_pipeline.embedding import PREPROCESSOR, dimensions, embed_batch, model_name
from data_pipeline.storage import ensure_dataset_vector_collection, publish_dataset


def connection() -> psycopg.Connection:
    return psycopg.connect(
        host=os.getenv("PGHOST", "localhost"), port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "postgres"), user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"),
    )


def vector_literal(values: object) -> str:
    return "[" + ",".join(format(float(value), ".10g") for value in values) + "]"


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def embed_dataset(dataset_id: str, *, batch_size: int) -> int:
    if batch_size <= 0:
        raise ValueError("batch size must be positive")
    with connection() as conn:
        ensure_dataset_vector_collection(conn, dataset_id, dimensions=dimensions())
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM datasets WHERE dataset_id = %s", (dataset_id,))
            row = cur.fetchone()
            if row is None or row[0] != "staging":
                raise ValueError(f"release must exist and be staging, found {row!r}")
            cur.execute("SELECT id, section_title, text FROM chunks WHERE dataset_id = %s AND text <> '' AND embedding IS NULL ORDER BY document_id, chunk_order", (dataset_id,))
            rows = cur.fetchall()
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            inputs = ["\n\n".join(part for part in (str(title), str(text)) if part) for _, title, text in batch]
            vectors = embed_batch(inputs)
            with conn.cursor() as cur:
                updates = []
                for (chunk_id, _, _), input_text, vector in zip(batch, inputs, vectors, strict=True):
                    digest = sha256(input_text)
                    updates.append((input_text, digest, vector_literal(vector), model_name(), dimensions(), PREPROCESSOR, digest, dataset_id, chunk_id))
                cur.executemany("""UPDATE chunks SET embedding_input_text=%s, embedding_input_sha256=%s,
                    embedding=%s::extensions.vector, embedding_model=%s, embedding_dimensions=%s,
                    embedding_preprocessor=%s, embedding_normalized=TRUE, embedded_input_sha256=%s,
                    embedding_created_at=now() WHERE dataset_id=%s AND id=%s""", updates)
            conn.commit()
        publish_dataset(conn, dataset_id, require_embeddings=True)
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_id")
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("EMBEDDING_BATCH_SIZE", "32")))
    args = parser.parse_args()
    print(f"Embedded and published {args.dataset_id}: {embed_dataset(args.dataset_id, batch_size=args.batch_size)} passages")
