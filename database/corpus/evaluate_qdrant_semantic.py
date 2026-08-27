#!/usr/bin/env python3
"""Evaluate thematic Qdrant retrieval and ANN recall against exact search."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import AsyncQdrantClient, models

PIPELINE_ROOT = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_ROOT))
load_dotenv()

from data_pipeline.embedding import embed_batch  # noqa: E402


async def _evaluate(
    client: AsyncQdrantClient, *, collection: str, dataset_id: str, vectors: list[list[float]], cases: list[dict], limit: int
) -> dict[str, object]:
    query_filter = models.Filter(must=[
        models.FieldCondition(key="dataset_id", match=models.MatchValue(value=dataset_id)),
        models.FieldCondition(key="answer_ready", match=models.MatchValue(value=True)),
    ])
    normal, exact = await asyncio.gather(
        asyncio.gather(*[
            client.query_points(collection, query=vector, query_filter=query_filter, limit=limit,
                                with_payload=["document_id"], with_vectors=False)
            for vector in vectors
        ]),
        asyncio.gather(*[
            client.query_points(collection, query=vector, query_filter=query_filter, limit=limit,
                                with_payload=["document_id"], with_vectors=False,
                                search_params=models.SearchParams(exact=True))
            for vector in vectors
        ]),
    )
    recall: dict[int, int] = {1: 0, 5: 0, 10: 0, limit: 0}
    ann_overlap: list[float] = []
    failures: list[str] = []
    for index, (case, approximate, exhaustive) in enumerate(zip(cases, normal, exact, strict=True)):
        expected = str(case["document_id"])
        approximate_ids = [str(point.payload.get("document_id", "")) for point in approximate.points if point.payload]
        exact_ids = [str(point.payload.get("document_id", "")) for point in exhaustive.points if point.payload]
        for cutoff in recall:
            recall[cutoff] += expected in approximate_ids[:cutoff]
        ann_overlap.append(len(set(approximate_ids) & set(exact_ids)) / max(1, len(set(exact_ids))))
        if expected not in approximate_ids:
            failures.append(str(case.get("case_id") or index))
    total = len(cases)
    return {
        "cases": total,
        "recall": {f"at_{key}": value / total for key, value in sorted(recall.items())},
        "ann_document_overlap": sum(ann_overlap) / total,
        "failures_at_limit": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--minimum-recall", type=float, default=0.85)
    args = parser.parse_args()
    if args.limit < 1 or not 0 <= args.minimum_recall <= 1:
        raise ValueError("limit must be positive and minimum-recall must be in [0, 1]")
    if not os.getenv("QDRANT_URL") or not os.getenv("QDRANT_API_KEY"):
        raise RuntimeError("QDRANT_URL and QDRANT_API_KEY are required")
    cases = [json.loads(line) for line in args.benchmark.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not cases or any(not case.get("question") or not case.get("document_id") for case in cases):
        raise ValueError("benchmark must contain question and document_id for every case")
    vectors = embed_batch([str(case["question"]) for case in cases])
    collection = os.getenv("QDRANT_COLLECTION", "medical_legal_active")

    async def run() -> dict[str, object]:
        client = AsyncQdrantClient(url=os.environ["QDRANT_URL"], api_key=os.environ["QDRANT_API_KEY"], timeout=30)
        try:
            return await _evaluate(client, collection=collection, dataset_id=args.dataset_id, vectors=vectors, cases=cases, limit=args.limit)
        finally:
            await client.close()

    metrics = asyncio.run(run())
    passed = float(metrics["recall"][f"at_{args.limit}"]) >= args.minimum_recall and float(metrics["ann_document_overlap"]) >= 0.95
    report = {"dataset_id": args.dataset_id, "collection": collection, "gate_pass": passed, **metrics}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
