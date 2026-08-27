#!/usr/bin/env python3
"""Hydrate high-impact missing legal targets with official Tavily evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv

csv.field_size_limit(sys.maxsize)
load_dotenv()

HIGH_IMPACT = {"Bãi bỏ", "Thay thế", "Sửa đổi, bổ sung"}
CUES = {
    "Sửa đổi, bổ sung": re.compile(r"\b(?:sửa đổi|bổ sung)\b", re.I),
    "Thay thế": re.compile(r"\bthay thế\b", re.I),
    "Bãi bỏ": re.compile(r"\bbãi bỏ\b", re.I),
}
SEARCH_DOMAINS = ["vbpl.vn", "vanban.chinhphu.vn", "congbao.chinhphu.vn", "gov.vn"]


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def identity(value: Any) -> str:
    folded = unicodedata.normalize("NFKD", clean(value).casefold())
    return "".join(char for char in folded if char.isalnum())


def official(url: str) -> bool:
    host = (urlparse(url).hostname or "").casefold()
    return (
        host == "vbpl.vn"
        or host.endswith(".vbpl.vn")
        or host == "chinhphu.vn"
        or host.endswith(".chinhphu.vn")
        or host == "gov.vn"
        or host.endswith(".gov.vn")
    )


def search(api_key: str, signature: str, context: str, timeout: float) -> dict[str, Any]:
    body = json.dumps(
        {
            "query": f'"{signature}" văn bản {context[:180]}',
            "topic": "general",
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": False,
            "include_raw_content": False,
            "include_usage": True,
            "include_domains": SEARCH_DOMAINS,
        }
    ).encode()
    request = Request(
        "https://api.tavily.com/search",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read().decode())
    except HTTPError as error:
        raise RuntimeError(f"Tavily HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"Tavily network error: {error.reason}") from error


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


def impact(relation: str, scope: str) -> str:
    if relation == "Bãi bỏ":
        return "repeal_whole_candidate" if scope == "toàn bộ" else "repeal_partial_or_unknown_candidate"
    if relation == "Thay thế":
        return "replacement_candidate"
    return "amendment_candidate"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-requests", type=int, default=488)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=float, default=20)
    parser.add_argument("--retry-errors-only", action="store_true")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is required")

    unresolved, _ = read_csv(args.source_dir / "model_relation_unresolved.csv")
    relationships, relationship_fields = read_csv(args.source_dir / "relationships.csv")
    references, reference_fields = read_csv(args.source_dir / "reference_nodes.csv")
    metadata, _ = read_csv(args.source_dir / "metadata.csv")
    canonical_ids = {row["id"] for row in metadata}
    title_by_id = {row["id"]: row.get("title", "") for row in metadata}
    for row in relationships:
        title_by_id.setdefault(row.get("doc_id", ""), row.get("source_title", ""))
        title_by_id.setdefault(row.get("other_doc_id", ""), row.get("target_title", ""))

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in unresolved:
        signature = clean(row.get("target_signature"))
        relation = clean(row.get("relation_type"))
        quote = clean(row.get("evidence_quote"))
        if signature and relation in HIGH_IMPACT and CUES[relation].search(quote):
            grouped[signature].append(row)
    ranked = sorted(grouped, key=lambda sig: (-max(float(r.get("confidence") or 0) for r in grouped[sig]), sig))
    existing_audit_path = args.source_dir / "tavily_relation_target_evidence.jsonl"
    existing_audits: list[dict[str, Any]] = []
    if existing_audit_path.is_file():
        existing_audits = [
            json.loads(line)
            for line in existing_audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    if args.retry_errors_only:
        retry_signatures = {clean(row.get("signature")) for row in existing_audits if row.get("error")}
        ranked = [signature for signature in ranked if signature in retry_signatures]
    ranked = ranked[: args.max_requests]

    def execute(signature: str) -> tuple[str, dict[str, Any] | None, str]:
        try:
            return (
                signature,
                search(api_key, signature, grouped[signature][0].get("evidence_quote", ""), args.timeout_seconds),
                "",
            )
        except Exception as error:
            return signature, None, str(error)

    audits: list[dict[str, Any]] = []
    accepted: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(execute, signature) for signature in ranked]
        for future in as_completed(futures):
            signature, response, error = future.result()
            match = None
            needle = identity(signature).removeprefix("so")
            for result in (response or {}).get("results", []):
                evidence = clean(result.get("title")) + " " + clean(result.get("content"))
                if official(clean(result.get("url"))) and needle and needle in identity(evidence):
                    match = result
                    break
            if match:
                accepted[signature] = match
            audits.append(
                {
                    "signature": signature,
                    "accepted": bool(match),
                    "selected": match,
                    "error": error,
                    "usage": (response or {}).get("usage", {}),
                    "results": (response or {}).get("results", []),
                }
            )

    existing_edges = {(r["doc_id"], r["other_doc_id"], r["relationship"]) for r in relationships}
    existing_reference_ids = {r["id"] for r in references}
    added_edges: list[dict[str, str]] = []
    added_refs: dict[str, dict[str, str]] = {}
    for signature, result in accepted.items():
        reference_id = "official-ref-" + hashlib.sha256(identity(signature).encode()).hexdigest()[:20]
        if reference_id not in canonical_ids and reference_id not in existing_reference_ids:
            added_refs[reference_id] = {
                "id": reference_id,
                "title": clean(result.get("title")) or signature,
                "resolution_status": "official_search_verified_reference",
                "official_url": clean(result.get("url")),
                "signature": signature,
                "evidence_sha256": hashlib.sha256(
                    json.dumps(result, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest(),
                "verified_at": datetime.now(UTC).isoformat(),
            }
        for row in grouped[signature]:
            relation = clean(row["relation_type"])
            source = clean(row["source_document_id"])
            key = (source, reference_id, relation)
            if key in existing_edges:
                continue
            quote = clean(row["evidence_quote"])
            scope = clean(row.get("scope"))
            edge_hash = hashlib.sha256("|".join((*key, quote)).encode()).hexdigest()
            added_edges.append(
                {
                    "agent_category": "model_grounded",
                    "doc_id": source,
                    "other_doc_id": reference_id,
                    "relationship": relation,
                    "source_is_selected": "true",
                    "target_is_selected": "false",
                    "relationship_is_adverse": str(relation in {"Bãi bỏ", "Thay thế"}).lower(),
                    "source_title": title_by_id.get(source, ""),
                    "target_title": clean(result.get("title")) or signature,
                    "relationship_id": edge_hash,
                    "provenance_status": "model_grounded_official_reference_v1",
                    "adverse_provenance": "exact_quote_plus_official_search",
                    "source_row_hashes": "",
                    "original_edge_count": "1",
                    "relation_confidence": clean(row.get("confidence")),
                    "relation_status": "candidate_grounded_official_target",
                    "evidence_text": quote,
                    "evidence_start": "",
                    "evidence_end": "",
                    "evidence_sha256": hashlib.sha256(quote.encode()).hexdigest(),
                    "target_signature": signature,
                    "target_resolution": "official_search_signature_exact",
                    "scope": scope,
                    "effective_date_text": clean(row.get("effective_date_text")),
                    "model_name": clean(row.get("model")),
                    "model_prompt_sha256": clean(row.get("model_prompt_sha256")),
                    "validity_impact_candidate": impact(relation, scope),
                    "target_official_url": clean(result.get("url")),
                    "target_official_evidence_sha256": hashlib.sha256(
                        json.dumps(result, ensure_ascii=False, sort_keys=True).encode()
                    ).hexdigest(),
                }
            )
            existing_edges.add(key)

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.source_dir, args.output_dir)
    for field in ("official_url", "signature", "evidence_sha256", "verified_at"):
        if field not in reference_fields:
            reference_fields.append(field)
    for field in ("validity_impact_candidate", "target_official_url", "target_official_evidence_sha256"):
        if field not in relationship_fields:
            relationship_fields.append(field)
    write_csv(args.output_dir / "reference_nodes.csv", references + list(added_refs.values()), reference_fields)
    write_csv(args.output_dir / "relationships.csv", relationships + added_edges, relationship_fields)
    merged_audits = {clean(row.get("signature")): row for row in existing_audits}
    merged_audits.update({clean(row.get("signature")): row for row in audits})
    with (args.output_dir / "tavily_relation_target_evidence.jsonl").open("w", encoding="utf-8") as handle:
        for row in sorted(merged_audits.values(), key=lambda x: x["signature"]):
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    report = {
        "requests": len(audits),
        "credits_reported": sum(int((r.get("usage") or {}).get("credits", 0) or 0) for r in audits),
        "errors": sum(bool(r["error"]) for r in audits),
        "official_targets_accepted": len(accepted),
        "reference_nodes_added": len(added_refs),
        "grounded_edges_added": len(added_edges),
        "audit_records_cumulative": len(merged_audits),
    }
    (args.output_dir / "TAVILY_RELATION_TARGET_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
