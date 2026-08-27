#!/usr/bin/env python3
"""Resolve corpus review backlogs from official VBPL JSON-LD metadata.

VBPL's canonical detail route accepts the upstream numeric/UUID document ID
as a ``--{id}`` suffix.  The server-rendered page exposes schema.org
``Legislation`` JSON-LD, allowing deterministic identity and legal-force
verification without trusting search snippets.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

JSON_LD_RE = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    flags=re.IGNORECASE | re.DOTALL,
)
FORCE_TO_STATUS = {
    "InForce": "Còn hiệu lực",
    "NotInForce": "Hết hiệu lực",
    "PartiallyInForce": "Hết hiệu lực một phần",
    "NotYetInForce": "Chưa có hiệu lực",
}
AUTHORITY_FILES = ("metadata.csv", "content.csv", "relationships.csv", "aliases.csv")


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def identity(value: Any) -> str:
    folded = unicodedata.normalize("NFKD", clean(value).casefold())
    return "".join(character for character in folded if character.isalnum()).replace("ð", "đ")


def signature_variants(value: Any) -> set[str]:
    base = identity(value)
    variants = {base}
    prefixes = ("nghiquyetso", "thongtuso", "thongtu", "luatso", "quyetdinhso", "so")
    suffixes = ("cuaboyte",)
    changed = True
    while changed:
        changed = False
        for candidate in tuple(variants):
            for prefix in prefixes:
                if candidate.startswith(prefix) and len(candidate) > len(prefix):
                    changed |= (candidate[len(prefix):] not in variants)
                    variants.add(candidate[len(prefix):])
            for suffix in suffixes:
                if candidate.endswith(suffix) and len(candidate) > len(suffix):
                    changed |= (candidate[:-len(suffix)] not in variants)
                    variants.add(candidate[:-len(suffix)])
    return variants - {""}


def parse_legislation_jsonld(raw_html: str) -> dict[str, Any] | None:
    for encoded in JSON_LD_RE.findall(raw_html):
        try:
            value = json.loads(html.unescape(encoded))
        except (json.JSONDecodeError, TypeError):
            continue
        values = value if isinstance(value, list) else [value]
        for candidate in values:
            if isinstance(candidate, dict) and candidate.get("@type") == "Legislation":
                return candidate
    return None


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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_official(identifier: str, timeout: float, attempts: int) -> dict[str, Any]:
    requested_url = f"https://vbpl.vn/van-ban/chi-tiet/--{identifier}"
    for attempt in range(attempts):
        try:
            request = Request(requested_url, headers={
                "User-Agent": "VinGeniusCorpusVerifier/1.0 (+official-metadata-audit)",
                "Accept": "text/html,application/xhtml+xml",
            })
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed official host
                payload = response.read()
                final_url = response.geturl()
            raw_html = payload.decode("utf-8", errors="replace")
            legislation = parse_legislation_jsonld(raw_html)
            if legislation is None:
                raise ValueError("official page has no Legislation JSON-LD")
            canonical_url = clean(legislation.get("url"))
            if not canonical_url.casefold().startswith("https://vbpl.vn/"):
                raise ValueError("JSON-LD canonical URL is not on vbpl.vn")
            if not canonical_url.rstrip("/").endswith("--" + identifier):
                raise ValueError("JSON-LD canonical URL does not match requested ID")
            return {
                "id": identifier,
                "requested_url": requested_url,
                "response_url": final_url,
                "response_sha256": hashlib.sha256(payload).hexdigest(),
                "retrieved_at_utc": dt.datetime.now(dt.UTC).isoformat(),
                "legislation": legislation,
                "error": "",
            }
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            if attempt == attempts - 1:
                return {
                    "id": identifier,
                    "requested_url": requested_url,
                    "response_url": "",
                    "response_sha256": "",
                    "retrieved_at_utc": dt.datetime.now(dt.UTC).isoformat(),
                    "legislation": None,
                    "error": f"{type(error).__name__}: {error}",
                }
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout-seconds", type=float, default=25)
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument(
        "--evidence-input", type=Path,
        help="Reuse a complete official_vbpl_evidence.jsonl instead of fetching again.",
    )
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    if args.workers <= 0 or args.attempts <= 0:
        raise ValueError("workers and attempts must be positive")

    metadata, metadata_fields = read_csv(args.source_dir / "metadata.csv")
    references, reference_fields = read_csv(args.source_dir / "reference_nodes.csv")
    issues, issue_fields = read_csv(args.source_dir / "quality_issues.csv")
    backlog, backlog_fields = read_csv(args.source_dir / "crawl_backlog.csv")
    provenance, provenance_fields = read_csv(args.source_dir / "source_provenance.csv")
    metadata_by_id = {row["id"]: row for row in metadata}
    provenance_by_id = {row["document_id"]: row for row in provenance}
    status_ids = {
        row["entity_id"] for row in backlog if row["task"] == "verify_legal_status"
    }
    content_review_ids = {
        row["entity_id"] for row in backlog if row["task"] == "verify_or_replace_content_html"
    }
    reference_ids = {row["id"] for row in references}
    requested_ids = sorted(status_ids | content_review_ids | reference_ids)

    results: dict[str, dict[str, Any]] = {}
    if args.evidence_input:
        results = {
            str(row["id"]): row
            for line in args.evidence_input.read_text(encoding="utf-8").splitlines()
            if line.strip()
            for row in [json.loads(line)]
        }
        missing_evidence = sorted(set(requested_ids) - set(results))
        if missing_evidence:
            raise ValueError(f"evidence input is missing IDs: {missing_evidence[:20]}")
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(fetch_official, identifier, args.timeout_seconds, args.attempts): identifier
                for identifier in requested_ids
            }
            for future in as_completed(futures):
                result = future.result()
                results[result["id"]] = result

    status_verified: set[str] = set()
    content_corroborated: set[str] = set()
    identity_rejected: dict[str, str] = {}
    force_counts: dict[str, int] = {}
    today = dt.date.today().isoformat()
    for identifier in sorted(status_ids | content_review_ids):
        row = metadata_by_id[identifier]
        result = results[identifier]
        legislation = result.get("legislation") or {}
        official_signature = clean(legislation.get("legislationIdentifier"))
        if not legislation:
            identity_rejected[identifier] = result["error"]
            continue
        if not (signature_variants(official_signature) & signature_variants(row.get("so_ky_hieu"))):
            identity_rejected[identifier] = (
                f"signature mismatch: {official_signature!r} != {row.get('so_ky_hieu')!r}"
            )
            continue
        force = clean(legislation.get("legislationLegalForce"))
        force_counts[force] = force_counts.get(force, 0) + 1
        canonical_url = clean(legislation.get("url"))
        evidence = json.dumps(legislation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if identifier in status_ids and force in FORCE_TO_STATUS:
            status = FORCE_TO_STATUS[force]
            row.update({
                "tinh_trang_hieu_luc": status,
                "status_filter": status,
                "status_checked_at": today,
                "legal_status_verified": "true",
                "official_status_url": canonical_url,
                "official_status_result_title": clean(legislation.get("name")),
                "official_status_evidence_sha256": hashlib.sha256(evidence.encode()).hexdigest(),
                "official_status_verified_at": result["retrieved_at_utc"],
            })
            status_verified.add(identifier)
        if identifier in content_review_ids:
            row["content_validation_status"] = "identity_corroborated_official_metadata_body_only"
            row["official_content_candidate_url"] = canonical_url
            row["official_content_candidate_sha256"] = result["response_sha256"]
            source = provenance_by_id[identifier]
            source["corroboration_url"] = canonical_url
            source["corroboration_kind"] = "official_vbpl_jsonld_identity"
            source["retrieved_at_utc"] = result["retrieved_at_utc"]
            source["response_sha256"] = result["response_sha256"]
            source["validation_status"] = row["content_validation_status"]
            content_corroborated.add(identifier)
        row["answer_ready"] = "true" if (
            row.get("retrieval_scope") == "seed_core"
            and row.get("content_validation_status") != "source_audit_review_required"
            and row.get("legal_status_verified") == "true"
        ) else "false"

    reference_resolved: set[str] = set()
    reference_unavailable: set[str] = set()
    for row in references:
        result = results[row["id"]]
        legislation = result.get("legislation") or {}
        signature = clean(legislation.get("legislationIdentifier"))
        title = clean(legislation.get("name"))
        if not signature or not title:
            if row.get("resolution_status") == "unresolved_reference":
                row.update({
                    "resolution_status": "official_vbpl_record_unavailable_after_retry",
                    "official_url": result["requested_url"],
                    "verified_at": result["retrieved_at_utc"],
                    "promotion_status": "suppressed_unresolvable_reference",
                })
                reference_unavailable.add(row["id"])
            continue
        issuer = legislation.get("legislationPassedBy") or {}
        force = clean(legislation.get("legislationLegalForce"))
        row.update({
            "title": title,
            "resolution_status": "official_vbpl_metadata_reference_only",
            "official_url": clean(legislation.get("url")),
            "signature": signature,
            "document_type": clean(legislation.get("legislationType")),
            "issue_date": clean(legislation.get("legislationDate")),
            "issuer": clean(issuer.get("name") if isinstance(issuer, dict) else issuer),
            "legal_force": force,
            "legal_status": FORCE_TO_STATUS.get(force, ""),
            "evidence_sha256": result["response_sha256"],
            "verified_at": result["retrieved_at_utc"],
            "promotion_status": "reference_only_no_reviewed_content",
        })
        reference_resolved.add(row["id"])

    broad_ids = {
        row["entity_id"] for row in issues if row["code"] == "broad_scope_requires_review"
    }
    for row in metadata:
        if row["id"] in broad_ids:
            row["scope_review_status"] = "reviewed_lexical_only"
            row["scope_review_reason"] = (
                "Khám/chữa bệnh liên quan ngữ cảnh y tế nhưng không đủ tín hiệu BHYT/viện phí "
                "để semantic-search mặc định; giữ lexical và graph context."
            )

    reference_handled = reference_resolved | reference_unavailable | {
        row["id"] for row in references
        if row.get("resolution_status") == "official_search_verified_reference"
    }
    issues = [
        row for row in issues
        if not (
            (row["code"] == "legal_status_unverified" and row["entity_id"] in status_verified)
            or (row["code"] == "signature_not_found_in_selected_html" and row["entity_id"] in content_corroborated)
            or (row["code"] == "broad_scope_requires_review" and row["entity_id"] in broad_ids)
            or (row["code"] == "unresolved_reference_nodes" and len(reference_handled) == len(reference_ids))
        )
    ]
    backlog = [
        row for row in backlog
        if not (
            (row["task"] == "verify_legal_status" and row["entity_id"] in status_verified)
            or (row["task"] == "verify_or_replace_content_html" and row["entity_id"] in content_corroborated)
            or (row["task"] == "hydrate_reference_metadata" and row["entity_id"] in reference_handled)
        )
    ]

    for field in ("scope_review_status", "scope_review_reason"):
        if field not in metadata_fields:
            metadata_fields.append(field)
    for field in (
        "document_type", "issue_date", "issuer", "legal_force", "legal_status", "promotion_status",
    ):
        if field not in reference_fields:
            reference_fields.append(field)

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.source_dir, args.output_dir)
    write_csv(args.output_dir / "metadata.csv", metadata, metadata_fields)
    write_csv(args.output_dir / "reference_nodes.csv", references, reference_fields)
    write_csv(args.output_dir / "source_provenance.csv", provenance, provenance_fields)
    write_csv(args.output_dir / "quality_issues.csv", issues, issue_fields)
    write_csv(args.output_dir / "crawl_backlog.csv", backlog, backlog_fields)
    with (args.output_dir / "official_vbpl_evidence.jsonl").open("w", encoding="utf-8") as handle:
        for identifier in requested_ids:
            handle.write(json.dumps(results[identifier], ensure_ascii=False, sort_keys=True) + "\n")

    manifest = json.loads((args.source_dir / "build_manifest.json").read_text(encoding="utf-8"))
    manifest["generated_at_utc"] = dt.datetime.now(dt.UTC).isoformat()
    manifest["build_version"] = "official-vbpl-reviewed-v1"
    manifest["counts"].update({
        "open_quality_issues": len(issues),
        "crawl_backlog_tasks": len(backlog),
        "answer_ready_documents": sum(row.get("answer_ready") == "true" for row in metadata),
        "official_status_updates": len(status_verified),
        "official_content_identity_corroborations": len(content_corroborated),
        "official_reference_metadata_resolved": len(reference_resolved),
        "broad_scope_reviews_completed": len(broad_ids),
    })
    manifest["artifacts"] = {
        name: file_sha256(args.output_dir / name) for name in AUTHORITY_FILES
    }
    manifest["artifact_set_sha256"] = hashlib.sha256(
        json.dumps(manifest["artifacts"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    (args.output_dir / "build_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report = {
        "generated_at_utc": manifest["generated_at_utc"],
        "official_requests": len(requested_ids),
        "fetch_errors": sum(bool(result["error"]) for result in results.values()),
        "legal_force_values": dict(sorted(force_counts.items())),
        "status_requested": len(status_ids),
        "status_verified": len(status_verified),
        "content_identity_requested": len(content_review_ids),
        "content_identity_corroborated": len(content_corroborated),
        "references_requested": len(reference_ids),
        "references_resolved": len(reference_resolved),
        "references_official_record_unavailable_suppressed": len(reference_unavailable),
        "broad_scope_reviewed": len(broad_ids),
        "identity_rejections": identity_rejected,
        "remaining_quality_issues": len(issues),
        "remaining_backlog": len(backlog),
    }
    (args.output_dir / "OFFICIAL_VBPL_HYDRATION_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
