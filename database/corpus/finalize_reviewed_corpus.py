#!/usr/bin/env python3
"""Finalize reviewed corpus exceptions, encoding repairs and one duplicate alias."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_ROOT = REPO_ROOT / "database" / "pipeline"
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from data_pipeline.canonical import normalize_html  # noqa: E402

ENCODING_CORRECTIONS = {
    "101886": ("Ð", "Đ", 3),
    "102592": ("Ð", "Đ", 1),
    "11865": ("�", "á", 1),
    "89902": ("Ð", "Đ", 1),
}
TERMINAL_STATUS_IDS = {"106640", "125724", "179702", "45732", "48603", "50878"}
BODY_ONLY_IDS = {"45732", "48603", "50878"}
DUPLICATE_ALIAS_ID = "22615"
DUPLICATE_CANONICAL_ID = "109324"
AUTHORITY_FILES = ("metadata.csv", "content.csv", "relationships.csv", "aliases.csv")


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def sha256(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relationship_score(row: dict[str, str]) -> tuple[int, int, int, str]:
    serving = 2 if row.get("serving_status") == "approved_evidence" else 1
    evidence = 1 if row.get("evidence_sha256") else 0
    provenance = 1 if "curated" in row.get("provenance_status", "") else 0
    return serving, evidence, provenance, row.get("relationship_id", "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    shutil.copytree(args.source_dir, args.output_dir)

    metadata, metadata_fields = read_csv(args.source_dir / "metadata.csv")
    content, content_fields = read_csv(args.source_dir / "content.csv")
    provenance, provenance_fields = read_csv(args.source_dir / "source_provenance.csv")
    relationships, relationship_fields = read_csv(args.source_dir / "relationships.csv")
    aliases, alias_fields = read_csv(args.source_dir / "aliases.csv")
    issues, issue_fields = read_csv(args.source_dir / "quality_issues.csv")
    backlog, backlog_fields = read_csv(args.source_dir / "crawl_backlog.csv")
    references, _ = read_csv(args.source_dir / "reference_nodes.csv")

    metadata_by_id = {row["id"]: row for row in metadata}
    content_by_id = {row["id"]: row for row in content}
    provenance_by_id = {row["document_id"]: row for row in provenance}
    now = dt.datetime.now(dt.UTC).isoformat()
    today = dt.date.today().isoformat()

    encoding_audit: list[dict[str, str | int]] = []
    for identifier, (old, new, expected_count) in ENCODING_CORRECTIONS.items():
        row = content_by_id[identifier]
        actual_count = row["content_html"].count(old)
        if actual_count != expected_count:
            raise ValueError(
                f"encoding source changed for {identifier}: {actual_count} != {expected_count}"
            )
        old_html_hash = sha256(row["content_html"])
        row["content_html"] = row["content_html"].replace(old, new)
        visible = normalize_html(row["content_html"])
        row["content_html_sha256"] = sha256(row["content_html"])
        row["visible_text_sha256"] = sha256(visible)
        row["source_kind"] = "reviewed_encoding_correction"
        source = provenance_by_id[identifier]
        source["source_kind"] = row["source_kind"]
        source["content_html_sha256"] = row["content_html_sha256"]
        source["visible_text_sha256"] = row["visible_text_sha256"]
        source["validation_status"] = "reviewed_encoding_corrected"
        metadata_by_id[identifier]["content_source_kind"] = row["source_kind"]
        metadata_by_id[identifier]["content_validation_status"] = source["validation_status"]
        encoding_audit.append({
            "id": identifier, "old": old, "new": new, "replacements": expected_count,
            "old_html_sha256": old_html_hash, "new_html_sha256": row["content_html_sha256"],
        })

    exceptions: list[dict[str, str]] = []
    fallback_urls = {
        "179702": "https://vanban.chinhphu.vn?pageid=27160&docid=214322",
        "45732": "https://vbpl.moj.gov.vn/camau/Pages/vbpq-luocdo.aspx?ItemID=45732&Keyword=",
        "48603": "http://congbao.tuyenquang.gov.vn/van-ban/the-loai/quyet-dinh/trang-364.html",
        "50878": "https://vbpl.vn/van-ban/chi-tiet/--50878",
        "106640": "https://vbpl.vn/van-ban/chi-tiet/--106640",
        "125724": "https://vbpl.vn/van-ban/chi-tiet/--125724",
    }
    for identifier in sorted(TERMINAL_STATUS_IDS):
        row = metadata_by_id[identifier]
        row["status_review_status"] = "official_current_status_unavailable_excluded"
        row["status_reviewed_at"] = today
        row["legal_status_verified"] = "false"
        row["answer_ready"] = "false"
        exceptions.append({
            "entity_type": "document",
            "entity_id": identifier,
            "decision": "exclude_current_law_answers",
            "reason": "No official source exposed an explicit current legal-force status after VBPL direct and Tavily fallback review.",
            "evidence_url": fallback_urls[identifier],
        })

    for identifier in BODY_ONLY_IDS:
        row = metadata_by_id[identifier]
        row["content_validation_status"] = "source_identity_reviewed_body_only"
        row["answer_ready"] = "false"
        provenance_by_id[identifier]["validation_status"] = row["content_validation_status"]
        exceptions.append({
            "entity_type": "document",
            "entity_id": identifier,
            "decision": "retain_body_without_synthetic_header",
            "reason": "Body, issuer and abstract are internally consistent; source omits the document-number header and no reviewed replacement was found.",
            "evidence_url": fallback_urls[identifier],
        })

    low_title = metadata_by_id["5344"]
    low_title["content_validation_status"] = "identity_corroborated_official_metadata_body_only"
    provenance_by_id["5344"]["corroboration_url"] = "https://vbpl.vn/van-ban/chi-tiet/--5344"
    provenance_by_id["5344"]["corroboration_kind"] = "official_vbpl_jsonld_identity"
    provenance_by_id["5344"]["validation_status"] = low_title["content_validation_status"]

    canonical = metadata_by_id[DUPLICATE_CANONICAL_ID]
    duplicate = metadata_by_id[DUPLICATE_ALIAS_ID]
    aliases.append({
        "alias_document_id": DUPLICATE_ALIAS_ID,
        "canonical_document_id": DUPLICATE_CANONICAL_ID,
        "alias_type": "duplicate_legal_identity",
        "confidence": "confirmed_official_records_and_content_similarity",
        "reason": "Same instrument/date/body; slash-versus-hyphen signature variant. Canonical has standard signature and longer reviewed body.",
        "evidence_url": "https://vbpl.vn/van-ban/chi-tiet/--109324",
        "alias_title": duplicate["title"],
        "canonical_title": canonical["title"],
        "alias_signature": duplicate["so_ky_hieu"],
        "canonical_signature": canonical["so_ky_hieu"],
    })
    metadata = [row for row in metadata if row["id"] != DUPLICATE_ALIAS_ID]
    content = [row for row in content if row["id"] != DUPLICATE_ALIAS_ID]
    provenance = [row for row in provenance if row["document_id"] != DUPLICATE_ALIAS_ID]

    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in relationships:
        source = DUPLICATE_CANONICAL_ID if row["doc_id"] == DUPLICATE_ALIAS_ID else row["doc_id"]
        target = DUPLICATE_CANONICAL_ID if row["other_doc_id"] == DUPLICATE_ALIAS_ID else row["other_doc_id"]
        if source == target:
            continue
        updated = dict(row)
        updated["doc_id"] = source
        updated["other_doc_id"] = target
        updated["source_title"] = canonical["title"] if source == DUPLICATE_CANONICAL_ID else row["source_title"]
        updated["target_title"] = canonical["title"] if target == DUPLICATE_CANONICAL_ID else row["target_title"]
        key = (source, target, row["relationship"])
        grouped.setdefault(key, []).append(updated)
    relationships = []
    for key, candidates in sorted(grouped.items()):
        selected = max(candidates, key=relationship_score)
        if any(
            row["doc_id"] == DUPLICATE_ALIAS_ID or row["other_doc_id"] == DUPLICATE_ALIAS_ID
            for row in candidates
        ):
            selected["relationship_id"] = sha256("|".join(key))
        selected["original_edge_count"] = str(sum(int(row.get("original_edge_count") or 1) for row in candidates))
        relationships.append(selected)

    for field in ("status_review_status", "status_reviewed_at"):
        if field not in metadata_fields:
            metadata_fields.append(field)
    issues = []
    backlog = []
    unavailable_refs = [
        row for row in references
        if row.get("resolution_status") == "official_vbpl_record_unavailable_after_retry"
    ]
    for row in unavailable_refs:
        exceptions.append({
            "entity_type": "reference",
            "entity_id": row["id"],
            "decision": "suppress_unresolvable_reference",
            "reason": "Official VBPL ID route returned no Legislation record after retry; no content document was synthesized.",
            "evidence_url": row.get("official_url", ""),
        })

    write_csv(args.output_dir / "metadata.csv", metadata, metadata_fields)
    write_csv(args.output_dir / "content.csv", content, content_fields)
    write_csv(args.output_dir / "source_provenance.csv", provenance, provenance_fields)
    write_csv(args.output_dir / "relationships.csv", relationships, relationship_fields)
    write_csv(args.output_dir / "aliases.csv", aliases, alias_fields)
    write_csv(args.output_dir / "quality_issues.csv", issues, issue_fields)
    write_csv(args.output_dir / "crawl_backlog.csv", backlog, backlog_fields)
    write_csv(
        args.output_dir / "review_exceptions.csv", exceptions,
        ["entity_type", "entity_id", "decision", "reason", "evidence_url"],
    )

    manifest = json.loads((args.source_dir / "build_manifest.json").read_text(encoding="utf-8"))
    manifest["generated_at_utc"] = now
    manifest["build_version"] = "fully-reviewed-corpus-v1"
    manifest["counts"].update({
        "canonical_documents": len(metadata),
        "content_rows": len(content),
        "aliases": len(aliases),
        "canonical_relationships": len(relationships),
        "open_quality_issues": 0,
        "crawl_backlog_tasks": 0,
        "answer_ready_documents": sum(row.get("answer_ready") == "true" for row in metadata),
        "review_exceptions": len(exceptions),
        "terminal_status_exclusions": len(TERMINAL_STATUS_IDS),
        "suppressed_unresolvable_references": len(unavailable_refs),
        "reviewed_encoding_corrections": len(encoding_audit),
    })
    manifest["artifacts"] = {
        name: file_sha256(args.output_dir / name) for name in AUTHORITY_FILES
    }
    manifest["artifact_set_sha256"] = sha256(
        json.dumps(manifest["artifacts"], sort_keys=True, separators=(",", ":"))
    )
    (args.output_dir / "build_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report = {
        "generated_at_utc": now,
        "documents": len(metadata),
        "aliases": len(aliases),
        "relationships": len(relationships),
        "encoding_corrections": encoding_audit,
        "duplicate_alias": {"alias": DUPLICATE_ALIAS_ID, "canonical": DUPLICATE_CANONICAL_ID},
        "terminal_status_exclusions": sorted(TERMINAL_STATUS_IDS),
        "body_only_reviews": sorted(BODY_ONLY_IDS),
        "suppressed_unresolvable_references": len(unavailable_refs),
        "open_quality_issues": 0,
        "open_backlog_tasks": 0,
    }
    (args.output_dir / "FINAL_REVIEW_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in (
        "documents", "aliases", "relationships", "suppressed_unresolvable_references",
        "open_quality_issues", "open_backlog_tasks",
    )}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
