#!/usr/bin/env python3
"""Build auditable temporal legal-status candidates from grounded graph edges."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

csv.field_size_limit(sys.maxsize)


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def status_candidate(relation: str, scope: str) -> str:
    if relation == "Bãi bỏ":
        return "possibly_repealed_whole" if scope == "toàn bộ" else "possibly_repealed_partially"
    if relation == "Thay thế":
        return "possibly_replaced"
    if relation == "Sửa đổi, bổ sung":
        return "possibly_amended"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)

    metadata, metadata_fields = read_csv(args.source_dir / "metadata.csv")
    relationships, _ = read_csv(args.source_dir / "relationships.csv")
    canonical_ids = {row["id"] for row in metadata}
    candidates: list[dict[str, str]] = []
    by_document: dict[str, list[dict[str, str]]] = defaultdict(list)
    for edge in relationships:
        relation = edge.get("relationship", "").strip()
        target = edge.get("other_doc_id", "").strip()
        evidence = edge.get("evidence_text", "").strip()
        candidate = status_candidate(relation, edge.get("scope", "").strip())
        # A temporal signal is only safe to surface when the underlying edge
        # has passed the graph-serving evidence/target-resolution gate.  Old
        # audit-only relationships remain preserved in the graph, but cannot
        # alter even a derived (non-authoritative) legal-status warning.
        if (
            not candidate
            or target not in canonical_ids
            or not evidence
            or edge.get("serving_status", "") != "approved_evidence"
        ):
            continue
        row = {
            "document_id": target,
            "status_candidate": candidate,
            "scope": edge.get("scope", ""),
            "effective_date_text": edge.get("effective_date_text", ""),
            "source_document_id": edge.get("doc_id", ""),
            "relationship_id": edge.get("relationship_id", ""),
            "relationship": relation,
            "relation_confidence": edge.get("relation_confidence", ""),
            "relation_status": edge.get("relation_status", ""),
            "provenance_status": edge.get("provenance_status", ""),
            "evidence_text": evidence,
            "evidence_sha256": edge.get("evidence_sha256", ""),
            "verification_status": "candidate_requires_official_status_confirmation",
        }
        candidates.append(row)
        by_document[target].append(row)

    precedence = {
        "possibly_repealed_whole": 0,
        "possibly_replaced": 1,
        "possibly_repealed_partially": 2,
        "possibly_amended": 3,
    }
    for field in ("derived_status_candidate", "derived_status_candidate_count"):
        if field not in metadata_fields:
            metadata_fields.append(field)
    for row in metadata:
        document_candidates = by_document.get(row["id"], [])
        row["derived_status_candidate_count"] = str(len(document_candidates))
        row["derived_status_candidate"] = (
            min((item["status_candidate"] for item in document_candidates), key=lambda value: precedence[value])
            if document_candidates
            else ""
        )

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.source_dir, args.output_dir)
    write_csv(args.output_dir / "metadata.csv", metadata, metadata_fields)
    candidate_fields = [
        "document_id", "status_candidate", "scope", "effective_date_text", "source_document_id",
        "relationship_id", "relationship", "relation_confidence", "relation_status",
        "provenance_status", "evidence_text", "evidence_sha256", "verification_status",
    ]
    write_csv(args.output_dir / "legal_status_candidates.csv", candidates, candidate_fields)
    report = {
        "canonical_documents": len(metadata),
        "documents_with_status_candidates": len(by_document),
        "status_candidate_edges": len(candidates),
        "candidate_counts": dict(Counter(row["status_candidate"] for row in candidates)),
        "final_status_fields_overwritten": False,
    }
    (args.output_dir / "TEMPORAL_STATUS_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
