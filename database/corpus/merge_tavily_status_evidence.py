#!/usr/bin/env python3
"""Merge only explicitly applied Tavily official-status evidence into a release."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

STATUS_FIELDS = (
    "legal_status_verified",
    "tinh_trang_hieu_luc",
    "status_filter",
    "status_checked_at",
    "official_status_url",
    "official_status_result_title",
    "official_status_evidence_sha256",
    "official_status_verified_at",
)


def read_metadata(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def write_metadata(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--tavily-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    source_rows, source_fields = read_metadata(args.source_dir / "metadata.csv")
    tavily_rows, _ = read_metadata(args.tavily_dir / "metadata.csv")
    source_by_id = {row["id"]: row for row in source_rows}
    tavily_by_id = {row["id"]: row for row in tavily_rows}
    applied_ids = sorted({
        str(row["document_id"])
        for line in (args.tavily_dir / "tavily_evidence.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
        for row in [json.loads(line)]
        if row.get("applied") == "legal_status"
    })
    for document_id in applied_ids:
        if document_id not in source_by_id or document_id not in tavily_by_id:
            raise ValueError(f"Tavily status record is absent from source: {document_id}")
        for field in STATUS_FIELDS:
            source_by_id[document_id][field] = tavily_by_id[document_id].get(field, "")
        # An accepted result must have passed the official-domain, signature,
        # issuer/year, and explicit-status gates in enrich_with_tavily.py.
        source_by_id[document_id]["legal_status_verified"] = "true"
    for field in STATUS_FIELDS:
        if field not in source_fields:
            source_fields.append(field)
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.source_dir, args.output_dir)
    write_metadata(args.output_dir / "metadata.csv", source_rows, source_fields)
    report = {"applied_status_document_ids": applied_ids, "applied_status_updates": len(applied_ids)}
    (args.output_dir / "TAVILY_STATUS_MERGE_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
