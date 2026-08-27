#!/usr/bin/env python3
"""Mark the curated serving corpus as answer-ready in Postgres and Qdrant.

The Group serving slice is already filtered (Trung ương + Hà Nội). P-151
runtime filters on answer_ready=true; this corpus never had that column, so
semantic and lexical retrieval both returned empty for most questions.
"""

from __future__ import annotations

import argparse
import os

import psycopg
from dotenv import load_dotenv
from qdrant_client import QdrantClient, models


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--qdrant-alias", default=os.getenv("QDRANT_COLLECTION", "medical_legal_active"))
    args = parser.parse_args()
    load_dotenv()
    dataset_id = args.dataset_id

    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE documents
            SET payload = jsonb_set(payload, '{metadata,answer_ready}', 'true'::jsonb, true)
            WHERE dataset_id = %s
            """,
            (dataset_id,),
        )
        count = connection.execute(
            """
            SELECT count(*) FROM documents
            WHERE dataset_id = %s
              AND (payload->'metadata'->>'answer_ready') = 'true'
            """,
            (dataset_id,),
        ).fetchone()[0]
        print(f"postgres_answer_ready={count}")

    client = QdrantClient(
        url=os.environ["QDRANT_URL"],
        api_key=os.environ["QDRANT_API_KEY"],
        timeout=60,
    )
    client.set_payload(
        args.qdrant_alias,
        payload={"answer_ready": True},
        points=models.Filter(
            must=[models.FieldCondition(key="dataset_id", match=models.MatchValue(value=dataset_id))]
        ),
        wait=True,
    )
    ready = client.count(
        args.qdrant_alias,
        count_filter=models.Filter(
            must=[
                models.FieldCondition(key="dataset_id", match=models.MatchValue(value=dataset_id)),
                models.FieldCondition(key="answer_ready", match=models.MatchValue(value=True)),
            ]
        ),
        exact=True,
    ).count
    print(f"qdrant_answer_ready={ready}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
