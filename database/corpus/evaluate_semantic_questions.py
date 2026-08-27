#!/usr/bin/env python3
"""Measure semantic Recall@k on grounded natural-language questions."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
load_dotenv()
from data_pipeline.embedding import embed_batch  # noqa: E402


def literal(vector: list[float]) -> str:
    return "[" + ",".join(format(value, ".10g") for value in vector) + "]"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = [json.loads(line) for line in args.benchmark.read_text(encoding="utf-8").splitlines() if line]
    vectors = embed_batch([case["question"] for case in cases])
    at_1 = at_5 = at_10 = 0
    failures: list[dict[str, str]] = []
    with (
        psycopg.connect(
            host=os.getenv("PGHOST"),
            port=int(os.getenv("PGPORT", "5432")),
            dbname=os.getenv("PGDATABASE"),
            user=os.getenv("PGUSER"),
            password=os.getenv("PGPASSWORD"),
        ) as conn,
        conn.cursor() as cur,
    ):
        cur.execute("SELECT active_dataset_id FROM dataset_state WHERE singleton")
        dataset_id = cur.fetchone()[0]
        for case, vector in zip(cases, vectors, strict=True):
            cur.execute(
                """SELECT document_id FROM active_graph_chunks
                   WHERE embedding IS NOT NULL AND semantic_eligible
                   ORDER BY embedding <=> %s::extensions.vector, chunk_id LIMIT 10""",
                (literal(vector),),
            )
            hits = [row[0] for row in cur.fetchall()]
            expected = case["document_id"]
            at_1 += expected in hits[:1]
            at_5 += expected in hits[:5]
            at_10 += expected in hits[:10]
            if expected not in hits[:10]:
                failures.append({"document_id": expected, "question": case["question"]})
    total = len(cases)
    report = {
        "dataset_id": dataset_id,
        "cases": total,
        "recall_at_1": at_1 / total,
        "recall_at_5": at_5 / total,
        "recall_at_10": at_10 / total,
        "passed_at_10": at_10,
        "failures": failures,
        "synthetic_grounded_not_human_adjudicated": True,
        "gate_pass": at_10 / total >= 0.8,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "failures"}, ensure_ascii=False))
    return 0 if report["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
