#!/usr/bin/env python3
"""Build a deterministic, evidence-backed retrieval/graph benchmark."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

csv.field_size_limit(sys.maxsize)


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-cases", type=int, default=200)
    args = parser.parse_args()
    output = args.output or args.source_dir / "release_benchmark.jsonl"
    metadata = read(args.source_dir / "metadata.csv")
    relationships = read(args.source_dir / "relationships.csv")
    cases: list[dict[str, object]] = []

    # Exact title/signature cases prove that identifiers and canonical titles
    # survive chunking and retrieval. Seed-core documents are prioritized.
    ordered = sorted(metadata, key=lambda row: (row.get("retrieval_scope") != "seed_core", row["id"]))
    for row in ordered[:100]:
        signature = row.get("so_ky_hieu", "").strip()
        query = f"Nội dung và phạm vi áp dụng của {signature or row['title']} là gì?"
        cases.append({
            "case_id": hashlib.sha256(("document|" + row["id"]).encode()).hexdigest()[:20],
            "case_type": "exact_document_retrieval",
            "query": query,
            "expected_document_ids": [row["id"]],
            "expected_signature": signature,
            "expected_relationship_id": "",
            "expected_evidence_sha256": "",
            "provenance": "canonical_metadata",
        })

    grounded = [
        row for row in relationships
        if row.get("evidence_text", "").strip()
        and row.get("relation_status", "").startswith("candidate_grounded")
    ]
    grounded.sort(key=lambda row: (row.get("relationship", ""), row.get("relationship_id", "")))
    for row in grounded[:80]:
        query = (
            f"{row.get('source_title') or row.get('doc_id')} {row.get('relationship')} "
            f"văn bản nào, toàn bộ hay một phần?"
        )
        cases.append({
            "case_id": hashlib.sha256(("relation|" + row["relationship_id"]).encode()).hexdigest()[:20],
            "case_type": "grounded_legal_relationship",
            "query": query,
            "expected_document_ids": [row["doc_id"], row["other_doc_id"]],
            "expected_signature": row.get("target_signature", ""),
            "expected_relationship_id": row["relationship_id"],
            "expected_relation": row.get("relationship", ""),
            "expected_scope": row.get("scope", ""),
            "expected_evidence_sha256": row.get("evidence_sha256", ""),
            "provenance": row.get("provenance_status", ""),
        })

    status_path = args.source_dir / "legal_status_candidates.csv"
    if status_path.is_file():
        for row in read(status_path)[:20]:
            cases.append({
                "case_id": hashlib.sha256(("status|" + row["relationship_id"]).encode()).hexdigest()[:20],
                "case_type": "temporal_status_candidate",
                "query": f"Văn bản {row['document_id']} có dấu hiệu thay đổi hiệu lực như thế nào?",
                "expected_document_ids": [row["document_id"], row["source_document_id"]],
                "expected_signature": "",
                "expected_relationship_id": row["relationship_id"],
                "expected_status_candidate": row["status_candidate"],
                "expected_evidence_sha256": row["evidence_sha256"],
                "provenance": row["provenance_status"],
            })

    cases = cases[: args.max_cases]
    with output.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")
    report = {
        "cases": len(cases),
        "exact_document_cases": sum(case["case_type"] == "exact_document_retrieval" for case in cases),
        "relationship_cases": sum(case["case_type"] == "grounded_legal_relationship" for case in cases),
        "temporal_status_cases": sum(case["case_type"] == "temporal_status_candidate" for case in cases),
        "human_adjudicated": False,
        "purpose": "release regression benchmark; human legal gold set remains a separate governance task",
    }
    (args.source_dir / "RELEASE_BENCHMARK_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
