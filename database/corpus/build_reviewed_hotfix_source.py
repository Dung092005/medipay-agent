#!/usr/bin/env python3
"""Merge reviewed identity/status corrections into the qualified release source.

The corrected base owns document identity and source-validation fields.  The
qualified source owns the richer, evidence-qualified relationship graph and
derived graph metadata.  This merge deliberately leaves HTML, chunks and
embeddings unchanged.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

IDENTITY_FIELDS = ("title", "so_ky_hieu", "content_validation_status")
STATUS_FIELDS = (
    "tinh_trang_hieu_luc",
    "status_checked_at",
    "status_filter",
    "legal_status_verified",
    "official_status_url",
    "official_status_result_title",
    "official_status_evidence_sha256",
    "official_status_verified_at",
)
AUTHORITY_FILES = ("metadata.csv", "content.csv", "relationships.csv", "aliases.csv")
REVIEWED_STATUS = {
    "58187": {
        "tinh_trang_hieu_luc": "Còn hiệu lực",
        "status_checked_at": "2026-08-18",
        "status_filter": "Còn hiệu lực",
        "legal_status_verified": "true",
        "official_status_url": "https://vbpl.vn/TW/Pages/vbpq-lichsu.aspx?ItemID=58187&Keyword=",
        "official_status_result_title": "Thông tư 07/2015/TT-BYT - Lịch sử hiệu lực",
        "official_status_verified_at": "2026-08-18T00:00:00+00:00",
    }
}


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualified-source", type=Path, required=True)
    parser.add_argument("--corrected-base", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    shutil.copytree(args.qualified_source, args.output_dir)

    qualified_rows, metadata_fields = read_csv(args.qualified_source / "metadata.csv")
    base_rows, _ = read_csv(args.corrected_base / "metadata.csv")
    base_by_id = {row["id"]: row for row in base_rows}
    if {row["id"] for row in qualified_rows} != set(base_by_id):
        raise ValueError("qualified and corrected sources have different canonical document IDs")

    reviewed_status_changes: list[dict[str, str]] = []
    identity_changes: list[dict[str, str]] = []
    for row in qualified_rows:
        identifier = row["id"]
        base = base_by_id[identifier]
        before = {field: row.get(field, "") for field in IDENTITY_FIELDS}
        for field in IDENTITY_FIELDS:
            row[field] = base.get(field, "")
        if any(before[field] != row[field] for field in IDENTITY_FIELDS):
            identity_changes.append({
                "id": identifier,
                "old_title": before["title"],
                "title": row["title"],
                "old_so_ky_hieu": before["so_ky_hieu"],
                "so_ky_hieu": row["so_ky_hieu"],
            })
        if identifier in REVIEWED_STATUS:
            correction = dict(REVIEWED_STATUS[identifier])
            evidence = json.dumps(correction, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            correction["official_status_evidence_sha256"] = hashlib.sha256(evidence.encode()).hexdigest()
            old_status = row.get("tinh_trang_hieu_luc", "")
            for field in STATUS_FIELDS:
                row[field] = correction.get(field, "")
            reviewed_status_changes.append({
                "id": identifier,
                "old_status": old_status,
                "status": row["tinh_trang_hieu_luc"],
                "official_status_url": row["official_status_url"],
            })
        row["answer_ready"] = "true" if (
            row.get("retrieval_scope") == "seed_core"
            and row.get("content_validation_status") != "source_audit_review_required"
            and row.get("legal_status_verified") == "true"
        ) else "false"

    final_by_id = {row["id"]: row for row in qualified_rows}
    write_csv(args.output_dir / "metadata.csv", qualified_rows, metadata_fields)

    relationships, relationship_fields = read_csv(args.qualified_source / "relationships.csv")
    for row in relationships:
        if row["doc_id"] in final_by_id:
            row["source_title"] = final_by_id[row["doc_id"]]["title"]
        if row["other_doc_id"] in final_by_id:
            row["target_title"] = final_by_id[row["other_doc_id"]]["title"]
    write_csv(args.output_dir / "relationships.csv", relationships, relationship_fields)

    aliases, alias_fields = read_csv(args.corrected_base / "aliases.csv")
    for row in aliases:
        canonical = final_by_id[row["canonical_document_id"]]
        row["canonical_title"] = canonical["title"]
        row["canonical_signature"] = canonical["so_ky_hieu"]
    write_csv(args.output_dir / "aliases.csv", aliases, alias_fields)

    shutil.copy2(args.corrected_base / "source_provenance.csv", args.output_dir / "source_provenance.csv")
    correction_file = args.corrected_base / "metadata_corrections.csv"
    if correction_file.is_file():
        shutil.copy2(correction_file, args.output_dir / "metadata_corrections.csv")

    verified = {row["id"] for row in qualified_rows if row.get("legal_status_verified") == "true"}
    issues, issue_fields = read_csv(args.corrected_base / "quality_issues.csv")
    issues = [
        row for row in issues
        if not (row["code"] == "legal_status_unverified" and row["entity_id"] in verified)
    ]
    write_csv(args.output_dir / "quality_issues.csv", issues, issue_fields)
    backlog, backlog_fields = read_csv(args.corrected_base / "crawl_backlog.csv")
    backlog = [
        row for row in backlog
        if not (row["task"] == "verify_legal_status" and row["entity_id"] in verified)
    ]
    write_csv(args.output_dir / "crawl_backlog.csv", backlog, backlog_fields)

    manifest = json.loads((args.corrected_base / "build_manifest.json").read_text(encoding="utf-8"))
    manifest["generated_at_utc"] = dt.datetime.now(dt.UTC).isoformat()
    manifest["build_version"] = "reviewed-qualified-hotfix-v1"
    manifest["inputs"]["qualified_source"] = str(args.qualified_source)
    manifest["inputs"]["corrected_base"] = str(args.corrected_base)
    manifest["counts"]["canonical_relationships"] = len(relationships)
    manifest["counts"]["open_quality_issues"] = len(issues)
    manifest["counts"]["crawl_backlog_tasks"] = len(backlog)
    manifest["counts"]["answer_ready_documents"] = sum(row["answer_ready"] == "true" for row in qualified_rows)
    manifest["counts"]["reviewed_identity_changes"] = len(identity_changes)
    manifest["counts"]["reviewed_status_changes"] = len(reviewed_status_changes)
    manifest["artifacts"] = {name: file_sha256(args.output_dir / name) for name in AUTHORITY_FILES}
    manifest["artifact_set_sha256"] = hashlib.sha256(
        json.dumps(manifest["artifacts"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    (args.output_dir / "build_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report = {
        "generated_at_utc": manifest["generated_at_utc"],
        "identity_changes": identity_changes,
        "reviewed_status_changes": reviewed_status_changes,
        "counts": {
            "documents": len(qualified_rows),
            "relationships": len(relationships),
            "quality_issues": len(issues),
            "crawl_backlog": len(backlog),
            "answer_ready": manifest["counts"]["answer_ready_documents"],
        },
        "content_and_embeddings_changed": False,
    }
    (args.output_dir / "REVIEWED_CORRECTIONS_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
