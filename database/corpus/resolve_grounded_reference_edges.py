#!/usr/bin/env python3
"""Resolve model-grounded legal edges to existing reference-only graph nodes.

Only unambiguous signature matches are accepted. The exact evidence quote and
legal cue were already captured by the model pass; this pass rechecks both and
adds an explicit validity-impact candidate without asserting final legal status.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

csv.field_size_limit(sys.maxsize)

CUES = {
    "Căn cứ": re.compile(r"\bcăn cứ\b", re.I),
    "Dẫn chiếu": re.compile(r"\bdẫn chiếu\b", re.I),
    "Sửa đổi, bổ sung": re.compile(r"\b(?:sửa đổi|bổ sung)\b", re.I),
    "Thay thế": re.compile(r"\bthay thế\b", re.I),
    "Bãi bỏ": re.compile(r"\bbãi bỏ\b", re.I),
    "Hướng dẫn": re.compile(r"\bhướng dẫn\b", re.I),
    "Quy định chi tiết": re.compile(r"\bquy định chi tiết\b", re.I),
    "Hợp nhất": re.compile(r"\bhợp nhất\b", re.I),
}
EXTRA_FIELDS = [
    "relation_confidence", "relation_status", "evidence_text", "evidence_start",
    "evidence_end", "evidence_sha256", "target_signature", "target_resolution",
    "scope", "effective_date_text", "model_name", "model_prompt_sha256",
    "validity_impact_candidate",
]


def clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def identity(value: str | None) -> str:
    folded = unicodedata.normalize("NFKD", clean(value).casefold())
    return "".join(char for char in folded if char.isalnum())


def variants(value: str) -> set[str]:
    normalized = identity(value)
    return {normalized, normalized.removeprefix("so")} - {""}


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


def impact(relation_type: str, scope: str) -> str:
    if relation_type == "Bãi bỏ":
        return "repeal_whole_candidate" if scope == "toàn bộ" else "repeal_partial_or_unknown_candidate"
    if relation_type == "Thay thế":
        return "replacement_candidate"
    if relation_type == "Sửa đổi, bổ sung":
        return "amendment_candidate"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)

    relationships, fields = read_csv(args.source_dir / "relationships.csv")
    unresolved, _ = read_csv(args.source_dir / "model_relation_unresolved.csv")
    metadata, _ = read_csv(args.source_dir / "metadata.csv")
    canonical_ids = {row["id"] for row in metadata}

    # Titles on either endpoint are the strongest metadata available for the
    # legacy reference-only nodes. A signature is usable only if it maps to one ID.
    title_by_id: dict[str, str] = {}
    for row in relationships:
        for identifier, title in (
            (clean(row.get("doc_id")), clean(row.get("source_title"))),
            (clean(row.get("other_doc_id")), clean(row.get("target_title"))),
        ):
            if identifier and title:
                title_by_id.setdefault(identifier, title)

    signatures = {variant for row in unresolved for variant in variants(row.get("target_signature", ""))}
    index: dict[str, set[str]] = defaultdict(set)
    for identifier, title in title_by_id.items():
        title_identity = identity(title)
        for signature in signatures:
            if len(signature) >= 4 and signature in title_identity:
                index[signature].add(identifier)

    existing = {(row["doc_id"], row["other_doc_id"], row["relationship"]) for row in relationships}
    added: list[dict[str, str]] = []
    ambiguous = missing = rejected_cue = 0
    for row in unresolved:
        relation_type = clean(row.get("relation_type"))
        quote = clean(row.get("evidence_quote"))
        candidates = set().union(*(index.get(v, set()) for v in variants(row.get("target_signature", ""))))
        candidates.discard(clean(row.get("source_document_id")))
        if not CUES.get(relation_type, re.compile(r"$^", re.I)).search(quote):
            rejected_cue += 1
            continue
        if not candidates:
            missing += 1
            continue
        if len(candidates) != 1:
            ambiguous += 1
            continue
        target_id = next(iter(candidates))
        key = (clean(row["source_document_id"]), target_id, relation_type)
        if key in existing:
            continue
        confidence = float(row.get("confidence") or 0)
        if confidence < 0.75:
            continue
        edge_identity = "|".join((*key, quote))
        added.append({
            "agent_category": "model_grounded",
            "doc_id": key[0],
            "other_doc_id": target_id,
            "relationship": relation_type,
            "source_is_selected": "true",
            "target_is_selected": str(target_id in canonical_ids).lower(),
            "relationship_is_adverse": str(relation_type in {"Bãi bỏ", "Thay thế"}).lower(),
            "source_title": title_by_id.get(key[0], ""),
            "target_title": title_by_id.get(target_id, ""),
            "relationship_id": hashlib.sha256(edge_identity.encode()).hexdigest(),
            "provenance_status": "model_grounded_reference_v1",
            "adverse_provenance": "model_grounded_exact_quote",
            "source_row_hashes": "",
            "original_edge_count": "1",
            "relation_confidence": f"{confidence:.3f}",
            "relation_status": "candidate_grounded",
            "evidence_text": quote,
            "evidence_start": "",
            "evidence_end": "",
            "evidence_sha256": hashlib.sha256(quote.encode()).hexdigest(),
            "target_signature": clean(row.get("target_signature")),
            "target_resolution": "unique_legacy_reference_title_signature",
            "scope": clean(row.get("scope")),
            "effective_date_text": clean(row.get("effective_date_text")),
            "model_name": clean(row.get("model")),
            "model_prompt_sha256": clean(row.get("model_prompt_sha256")),
            "validity_impact_candidate": impact(relation_type, clean(row.get("scope"))),
        })
        existing.add(key)

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.source_dir, args.output_dir)
    for field in EXTRA_FIELDS:
        if field not in fields:
            fields.append(field)
    write_csv(args.output_dir / "relationships.csv", relationships + added, fields)
    report = {
        "unresolved_rows_examined": len(unresolved),
        "reference_edges_added": len(added),
        "ambiguous_signature_rows": ambiguous,
        "missing_signature_rows": missing,
        "rejected_missing_relation_cue": rejected_cue,
    }
    (args.output_dir / "REFERENCE_EDGE_RESOLUTION_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
