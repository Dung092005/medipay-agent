#!/usr/bin/env python3
"""Evaluate active exact/Qdrant semantic retrieval and Neo4j evidence parity."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from neo4j import GraphDatabase
from qdrant_client import QdrantClient, models

PIPELINE_ROOT = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_ROOT))
load_dotenv()

from data_pipeline.api_repository import PsycopgReadRepository  # noqa: E402
from data_pipeline.embedding import embed_batch  # noqa: E402


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
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument(
        "--semantic-benchmark", type=Path,
        help="Optional thematic benchmark. Never reuse exact-signature cases for semantic quality.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--semantic-limit", type=int, default=20)
    args = parser.parse_args()
    cases = [json.loads(line) for line in args.benchmark.read_text(encoding="utf-8").splitlines() if line]
    exact_cases = [case for case in cases if case["case_type"] == "exact_document_retrieval"]
    graph_cases = [case for case in cases if case["case_type"] != "exact_document_retrieval"]

    repository = PsycopgReadRepository(connection_factory=connection)
    exact_pass = 0
    exact_failures: list[str] = []
    for case in exact_cases:
        result = repository.exact_search(case["expected_signature"], category=None, status=None, limit=10)
        ids = {hit.document_id for hit in result.hits} if result else set()
        expected = set(case["expected_document_ids"])
        if expected & ids:
            exact_pass += 1
        else:
            exact_failures.append(case["case_id"])

    dataset_id = repository.current_dataset().dataset_id
    semantic = {"status": "not_run", "reason": "pass --semantic-benchmark separately"}
    semantic_failures: list[str] = []
    semantic_at_5 = semantic_at_10 = semantic_at_limit = 0
    semantic_total = 0
    if args.semantic_benchmark:
        semantic_cases = [
            json.loads(line) for line in args.semantic_benchmark.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        if not os.getenv("QDRANT_URL") or not os.getenv("QDRANT_API_KEY"):
            raise RuntimeError("QDRANT_URL and QDRANT_API_KEY are required for active semantic evaluation")
        vectors = embed_batch([str(case.get("question") or case.get("query") or "") for case in semantic_cases])
        qdrant = QdrantClient(
            url=os.environ["QDRANT_URL"], api_key=os.environ["QDRANT_API_KEY"], timeout=int(float(os.getenv("QDRANT_TIMEOUT_SECONDS", "30")))
        )
        collection = os.getenv("QDRANT_COLLECTION", "medical_legal_active")
        for case, vector in zip(semantic_cases, vectors, strict=True):
            response = qdrant.query_points(
                collection, query=vector, limit=args.semantic_limit,
                query_filter=models.Filter(must=[
                    models.FieldCondition(key="dataset_id", match=models.MatchValue(value=dataset_id)),
                    models.FieldCondition(key="answer_ready", match=models.MatchValue(value=True)),
                ]),
                with_payload=["document_id"], with_vectors=False,
            )
            hits = [str(point.payload.get("document_id", "")) for point in response.points if point.payload]
            expected = {str(case["document_id"])}
            semantic_at_5 += bool(expected & set(hits[:5]))
            semantic_at_10 += bool(expected & set(hits[:10]))
            semantic_at_limit += bool(expected & set(hits[:args.semantic_limit]))
            if not expected & set(hits[:args.semantic_limit]):
                semantic_failures.append(str(case.get("case_id") or case.get("document_id")))
        qdrant.close()
        semantic_total = len(semantic_cases)
        semantic = {
            "status": "complete",
            "recall_at_5": semantic_at_5 / max(1, semantic_total),
            "recall_at_10": semantic_at_10 / max(1, semantic_total),
            "recall_at_limit": semantic_at_limit / max(1, semantic_total),
            "limit": args.semantic_limit,
            "passed_at_10": semantic_at_10,
            "total": semantic_total,
        }

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )
    graph_pass = 0
    graph_failures: list[str] = []
    with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
        for case in graph_cases:
            record = session.run(
                """MATCH ()-[r]->() WHERE r.dataset_id=$dataset_id
                   AND r.relationship_id=$relationship_id
                   RETURN r.evidence_sha256 AS evidence_sha256,
                          r.relationship_type AS relationship_type,
                          r.scope AS scope""",
                dataset_id=dataset_id,
                relationship_id=case["expected_relationship_id"],
            ).single()
            passed = bool(record) and (
                not case.get("expected_evidence_sha256")
                or record["evidence_sha256"] == case["expected_evidence_sha256"]
            )
            if passed:
                graph_pass += 1
            else:
                graph_failures.append(case["case_id"])
    driver.close()

    exact_total = max(1, len(exact_cases))
    graph_total = max(1, len(graph_cases))
    report = {
        "dataset_id": dataset_id,
        "benchmark_cases": len(cases),
        "exact_identifier": {
            "passed": exact_pass,
            "total": len(exact_cases),
            "recall_at_10": exact_pass / exact_total,
        },
        "semantic": semantic,
        "graph_evidence": {"passed": graph_pass, "total": len(graph_cases), "parity": graph_pass / graph_total},
        "failures": {"exact": exact_failures, "semantic_at_10": semantic_failures, "graph": graph_failures},
        "release_gate_pass": exact_pass == len(exact_cases) and graph_pass == len(graph_cases)
        and (semantic.get("status") != "complete" or float(semantic["recall_at_limit"]) >= 0.85),
        "human_adjudicated": False,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["release_gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
