#!/usr/bin/env python3
"""Run a release-locked semantic diversity ablation.

The experiment deliberately compares two selectors over the same Qdrant ANN
candidate set: raw rank and a bounded per-document selector.  It never changes
the serving collection; it only produces evidence for a promotion decision.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from qdrant_client import AsyncQdrantClient, models

PIPELINE_ROOT = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_ROOT))
load_dotenv()

from data_pipeline.embedding import embed_batch  # noqa: E402


def bounded_document_selector(points: list[Any], *, limit: int, max_per_document: int) -> list[Any]:
    """Preserve score order while preventing one document from monopolizing results."""
    if limit < 1 or max_per_document < 1:
        raise ValueError("limit and max_per_document must be positive")
    selected: list[Any] = []
    counts: dict[str, int] = {}
    deferred: list[Any] = []
    for point in points:
        payload = point.payload or {}
        document_id = str(payload.get("document_id", ""))
        if counts.get(document_id, 0) < max_per_document:
            selected.append(point)
            counts[document_id] = counts.get(document_id, 0) + 1
        else:
            deferred.append(point)
        if len(selected) >= limit:
            return selected
    selected.extend(deferred[: max(0, limit - len(selected))])
    return selected[:limit]


def _ids(points: list[Any]) -> list[str]:
    return [str(point.payload.get("document_id", "")) for point in points if point.payload]


def _score_cases(cases: list[dict[str, Any]], raw: list[list[Any]], diversified: list[list[Any]], limit: int) -> dict[str, Any]:
    metrics: dict[str, dict[str, float]] = {}
    for name, groups in (("raw", raw), ("diversified", diversified)):
        recalls = {1: 0, 5: 0, 10: 0, limit: 0}
        duplicate_ratios: list[float] = []
        for case, points in zip(cases, groups, strict=True):
            ids = _ids(points)
            expected = str(case["document_id"])
            for cutoff in recalls:
                recalls[cutoff] += expected in ids[:cutoff]
            duplicate_ratios.append(1 - len(set(ids)) / max(1, len(ids)))
        total = max(1, len(cases))
        metrics[name] = {
            **{f"recall_at_{cutoff}": value / total for cutoff, value in recalls.items()},
            "duplicate_ratio_at_limit": sum(duplicate_ratios) / total,
        }
    return metrics


async def _run(args: argparse.Namespace, cases: list[dict[str, Any]], vectors: list[list[float]]) -> dict[str, Any]:
    query_filter = models.Filter(must=[
        models.FieldCondition(key="dataset_id", match=models.MatchValue(value=args.dataset_id)),
        models.FieldCondition(key="answer_ready", match=models.MatchValue(value=True)),
    ])
    client = AsyncQdrantClient(url=os.environ["QDRANT_URL"], api_key=os.environ["QDRANT_API_KEY"], timeout=30)
    try:
        responses = await asyncio.gather(*[
            client.query_points(
                collection_name=args.collection,
                query=vector,
                query_filter=query_filter,
                limit=args.candidate_limit,
                with_payload=["document_id"],
                with_vectors=False,
            )
            for vector in vectors
        ])
    finally:
        await client.close()
    raw = [list(response.points[: args.limit]) for response in responses]
    diversified = [bounded_document_selector(list(response.points), limit=args.limit, max_per_document=args.max_per_document) for response in responses]
    return _score_cases(cases, raw, diversified, args.limit)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--collection", default=os.getenv("QDRANT_COLLECTION", "medical_legal_active"))
    parser.add_argument("--candidate-limit", type=int, default=50)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-per-document", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.candidate_limit < args.limit or args.limit < 1:
        parser.error("candidate-limit must be >= limit >= 1")
    if not os.getenv("QDRANT_URL") or not os.getenv("QDRANT_API_KEY"):
        raise RuntimeError("QDRANT_URL and QDRANT_API_KEY are required")
    cases = [json.loads(line) for line in args.benchmark.read_text(encoding="utf-8").splitlines() if line.strip()]
    vectors = embed_batch([str(case["question"]) for case in cases])
    metrics = asyncio.run(_run(args, cases, vectors))
    raw = metrics["raw"]
    diversified = metrics["diversified"]
    report = {
        "dataset_id": args.dataset_id,
        "collection": args.collection,
        "cases": len(cases),
        "candidate_limit": args.candidate_limit,
        "limit": args.limit,
        "max_per_document": args.max_per_document,
        "metrics": metrics,
        "promotion_gate": {
            "recall_at_limit_not_lower": diversified[f"recall_at_{args.limit}"] >= raw[f"recall_at_{args.limit}"],
            "duplicate_ratio_not_higher": diversified["duplicate_ratio_at_limit"] <= raw["duplicate_ratio_at_limit"],
        },
    }
    report["gate_pass"] = all(report["promotion_gate"].values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
