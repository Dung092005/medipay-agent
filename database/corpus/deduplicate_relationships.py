#!/usr/bin/env python3
"""Collapse duplicate legal edges while preserving the strongest evidence."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

csv.field_size_limit(sys.maxsize)

EVIDENCE_FIELDS = (
    "relation_confidence",
    "relation_status",
    "evidence_text",
    "evidence_start",
    "evidence_end",
    "evidence_sha256",
    "target_signature",
    "target_resolution",
    "scope",
    "effective_date_text",
    "model_name",
    "model_prompt_sha256",
    "validity_impact_candidate",
    "target_official_url",
    "target_official_evidence_sha256",
)


def score(row: dict[str, str]) -> tuple[int, float, int]:
    provenance = row.get("provenance_status", "")
    trust = 3 if provenance.startswith("curated_csv") else 2 if "official" in provenance else 1
    confidence = float(row.get("relation_confidence") or 0)
    return trust, confidence, len(row.get("evidence_text", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    with (args.source_dir / "relationships.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [dict(row) for row in reader]
        fields = list(reader.fieldnames or [])
    with (args.source_dir / "metadata.csv").open(encoding="utf-8-sig", newline="") as handle:
        source_categories = {row["id"]: row.get("agent_category", "") for row in csv.DictReader(handle)}
    repaired_categories = 0
    for row in rows:
        categories = {part.strip() for part in row.get("agent_category", "").split(",") if part.strip()}
        if not categories or not categories <= {"bhyt", "vien_phi"}:
            row["agent_category"] = source_categories.get(row.get("doc_id", ""), "bhyt")
            repaired_categories += 1
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["doc_id"], row["other_doc_id"], row["relationship"])].append(row)
    output: list[dict[str, str]] = []
    duplicate_groups = merged_evidence = 0
    for key, candidates in grouped.items():
        primary = dict(max(candidates, key=score))
        if len(candidates) > 1:
            duplicate_groups += 1
            evidence_candidates = [row for row in candidates if row.get("evidence_text", "").strip()]
            if evidence_candidates:
                evidence = max(
                    evidence_candidates,
                    key=lambda row: (float(row.get("relation_confidence") or 0), len(row.get("evidence_text", ""))),
                )
                for field in EVIDENCE_FIELDS:
                    if evidence.get(field, "").strip():
                        primary[field] = evidence[field]
                provenances = sorted(
                    {row.get("provenance_status", "") for row in candidates if row.get("provenance_status")}
                )
                primary["provenance_status"] = "+".join(provenances)
                merged_evidence += 1
        output.append(primary)
    output.sort(key=lambda row: (row["doc_id"], row["other_doc_id"], row["relationship"]))
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.source_dir, args.output_dir)
    temporary = args.output_dir / "relationships.csv.tmp"
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output)
    temporary.replace(args.output_dir / "relationships.csv")
    report = {
        "input_relationship_rows": len(rows),
        "output_relationship_rows": len(output),
        "duplicate_edge_groups": duplicate_groups,
        "duplicate_rows_removed": len(rows) - len(output),
        "groups_with_evidence_merged": merged_evidence,
        "agent_categories_repaired": repaired_categories,
    }
    (args.output_dir / "RELATIONSHIP_DEDUPLICATION_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
