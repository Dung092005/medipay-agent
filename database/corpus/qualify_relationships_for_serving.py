#!/usr/bin/env python3
"""Classify graph edges for online serving without discarding audit history.

Legacy CSV/export edges often have no quote or target-resolution evidence. They
remain in the graph for audit, but are never used by online graph expansion.
Only model-grounded edges whose target is unambiguous (or whose full title is
disambiguated by the quote) are eligible for serving.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

csv.field_size_limit(sys.maxsize)

STOPWORDS = {
    "ban", "banhanh", "bao", "benh", "bo", "bosung", "cua", "dieu", "doi", "duoc",
    "hanh", "huong", "kham", "mot", "nghi", "nhung", "phap", "quyet", "quydinh",
    "sua", "thay", "the", "thong", "thu", "tu", "van", "ve", "vaban", "vban",
    "vav", "yte", "vietnam", "y", "te", "so", "theo", "trong", "va",
}
LOCATION_STOPWORDS = {
    "ban", "bo", "cua", "den", "do", "duoc", "hdnd", "ngay", "quy", "quyet",
    "sau", "tai", "theo", "tren", "trong", "tu", "ubnd", "va", "ve", "voi", "vung",
}


def clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def identity(value: str | None) -> str:
    folded = unicodedata.normalize("NFKD", clean(value).casefold())
    return "".join(char for char in folded if char.isalnum())


def signature_variants(value: str | None) -> set[str]:
    normalized = identity(value)
    return {normalized, normalized.removeprefix("so")} - {""}


def title_terms(value: str | None) -> set[str]:
    return {
        identity(word)
        for word in re.findall(r"[\wÀ-ỹĐđ]+", clean(value).casefold(), flags=re.UNICODE)
        if len(identity(word)) >= 4 and identity(word) not in STOPWORDS
    }


def local_contexts(value: str | None) -> set[str]:
    """Extract bounded local-government place names from legal prose.

    This is intentionally a veto, not a resolver: if an exact quote explicitly
    names Cà Mau while the selected target explicitly belongs to Ninh Thuận,
    the relationship cannot be served even if its signature happens to be
    unique in the current corpus.
    """

    tokens = re.findall(r"[\wÀ-ỹĐđ]+", clean(value), flags=re.UNICODE)
    contexts: set[str] = set()
    for index, token in enumerate(tokens):
        marker = identity(token)
        start = index + 1
        if marker == "thanh" and start < len(tokens) and identity(tokens[start]) == "pho":
            start += 1
        elif marker != "tinh":
            continue
        parts: list[str] = []
        for candidate in tokens[start : start + 4]:
            normalized = identity(candidate)
            if not normalized or normalized in LOCATION_STOPWORDS:
                break
            parts.append(candidate)
        context = identity(" ".join(parts))
        if len(context) >= 3:
            contexts.add(context)
    return contexts


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)

    with (args.source_dir / "metadata.csv").open(encoding="utf-8-sig", newline="") as handle:
        metadata = [dict(row) for row in csv.DictReader(handle)]
    by_id = {row["id"]: row for row in metadata}
    by_signature: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in metadata:
        for variant in signature_variants(row.get("so_ky_hieu")):
            by_signature[variant].append(row)
    with (args.source_dir / "relationships.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        relationships = [dict(row) for row in reader]
        fields = list(reader.fieldnames or [])

    diagnostics: list[dict[str, str]] = []
    for row in relationships:
        provenance = clean(row.get("provenance_status"))
        quote = clean(row.get("evidence_text"))
        target_id = clean(row.get("other_doc_id"))
        status = "audit_only_unverified"
        qualification = "no_grounded_evidence"
        candidates: list[dict[str, str]] = []
        # Deduplication can preserve both a legacy and a model provenance in
        # one field (for example ``curated_csv+model_grounded_v1``).  The
        # presence of grounded model evidence, not the field prefix, is what
        # matters for serving qualification.
        if "model_grounded" in provenance and quote:
            resolution = clean(row.get("target_resolution"))
            if resolution in {"official_search_signature_exact", "unique_legacy_reference_title_signature"}:
                status = "approved_evidence"
                qualification = resolution
            elif resolution == "canonical_signature_exact":
                for signature in signature_variants(row.get("target_signature")):
                    candidates.extend(by_signature.get(signature, []))
                candidates = list({candidate["id"]: candidate for candidate in candidates}.values())
                target_contexts = local_contexts(
                    f"{by_id.get(target_id, {}).get('title', '')} "
                    f"{by_id.get(target_id, {}).get('co_quan_ban_hanh', '')}"
                )
                quote_contexts = local_contexts(quote)
                if target_contexts and quote_contexts and not (target_contexts & quote_contexts):
                    status = "suppressed_target_location_mismatch"
                    qualification = (
                        "canonical_location_mismatch:"
                        f"{','.join(sorted(quote_contexts))}!={','.join(sorted(target_contexts))}"
                    )
                elif len(candidates) == 1:
                    status = "approved_evidence"
                    qualification = "canonical_signature_unique"
                elif target_id in by_id:
                    quote_terms = title_terms(quote)
                    scores = {
                        candidate["id"]: len(title_terms(candidate.get("title")) & quote_terms)
                        for candidate in candidates
                    }
                    target_score = scores.get(target_id, 0)
                    runner_up = max((value for key, value in scores.items() if key != target_id), default=0)
                    if target_score >= 3 and target_score > runner_up:
                        status = "approved_evidence"
                        qualification = f"canonical_title_quote_disambiguated:{target_score}>{runner_up}"
                    else:
                        status = "suppressed_ambiguous_target"
                        qualification = f"canonical_signature_ambiguous:{target_score}<={runner_up}"
                else:
                    status = "suppressed_ambiguous_target"
                    qualification = "canonical_target_not_found"
            else:
                status = "suppressed_unrecognized_resolution"
                qualification = resolution or "missing_target_resolution"
        row["serving_status"] = status
        row["serving_qualification"] = qualification
        diagnostics.append({
            "relationship_id": row.get("relationship_id", ""),
            "doc_id": row.get("doc_id", ""),
            "other_doc_id": target_id,
            "relationship": row.get("relationship", ""),
            "provenance_status": provenance,
            "serving_status": status,
            "serving_qualification": qualification,
            "target_signature": row.get("target_signature", ""),
        })

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.source_dir, args.output_dir)
    for field in ("serving_status", "serving_qualification"):
        if field not in fields:
            fields.append(field)
    write_csv(args.output_dir / "relationships.csv", relationships, fields)
    diagnostic_fields = [
        "relationship_id", "doc_id", "other_doc_id", "relationship", "provenance_status",
        "serving_status", "serving_qualification", "target_signature",
    ]
    write_csv(args.output_dir / "RELATIONSHIP_SERVING_AUDIT.csv", diagnostics, diagnostic_fields)
    report = {
        "relationships": len(relationships),
        "serving_status_counts": dict(sorted(Counter(row["serving_status"] for row in relationships).items())),
        "qualification_counts": dict(sorted(Counter(row["serving_qualification"] for row in relationships).items())),
    }
    (args.output_dir / "RELATIONSHIP_SERVING_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
