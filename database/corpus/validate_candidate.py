#!/usr/bin/env python3
"""Run canonical, citation and retrieval gates for a reconciled corpus."""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_ROOT = REPO_ROOT / "database" / "pipeline"
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from data_pipeline.canonical import build_snapshot  # noqa: E402


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def validate(source_dir: Path) -> dict[str, Any]:
    snapshot = build_snapshot(source_dir)
    content = {row["document_id"]: row for row in snapshot.content}
    units = {row["unit_id"]: row for row in snapshot.legal_units}
    metadata = {row["document_id"]: row["metadata"] for row in snapshot.documents}
    passage_ids: set[str] = set()
    document_text: set[tuple[str, str]] = set()
    duplicate_text_samples: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    per_document = collections.Counter(row["document_id"] for row in snapshot.passages)
    semantic_per_document = collections.Counter(
        row["document_id"] for row in snapshot.passages if row.get("semantic_eligible")
    )

    for row in snapshot.passages:
        passage_id = str(row["passage_id"])
        if passage_id in passage_ids:
            errors.append(f"duplicate passage_id: {passage_id}")
        passage_ids.add(passage_id)
        identity = (str(row["document_id"]), str(row["text_sha256"]))
        if identity in document_text:
            if len(duplicate_text_samples) < 20:
                duplicate_text_samples.append(f"{identity[0]}:{identity[1]}")
        document_text.add(identity)
        if row["unit_id"] not in units:
            errors.append(f"missing unit for passage: {passage_id}")
        start, end = row.get("source_start"), row.get("source_end")
        if not isinstance(start, int) or not isinstance(end, int) or end <= start:
            errors.append(f"invalid source offsets: {passage_id}")
            continue
        if row.get("passage_kind") == "prose":
            projected = content[str(row["document_id"])]["normalized_text"][start:end]
            if projected != row["text"]:
                errors.append(f"prose text/source offset mismatch: {passage_id}")
        elif row.get("passage_kind") == "table_row":
            if not row.get("table_id") or row.get("table_row_index") is None:
                errors.append(f"table row lacks cell provenance: {passage_id}")

    if duplicate_text_samples:
        errors.append(
            "duplicate text remains within documents; samples: " + ", ".join(duplicate_text_samples)
        )

    index_documents = {
        identifier for identifier, row in metadata.items()
        if str(row.get("index_eligible", "true")).casefold() == "true"
    }
    semantic_documents = {
        identifier for identifier, row in metadata.items()
        if str(row.get("semantic_eligible", "true")).casefold() == "true"
    }
    missing_index_passages = sorted(identifier for identifier in index_documents if not per_document[identifier])
    missing_semantic_passages = sorted(identifier for identifier in semantic_documents if not semantic_per_document[identifier])
    if missing_index_passages:
        errors.append(f"index-eligible documents without passages: {len(missing_index_passages)}")
    if missing_semantic_passages:
        warnings.append(f"semantic documents without prose embeddings: {len(missing_semantic_passages)}")

    edge_keys = [
        (row["source_document_id"], row["target_document_id"], row["relationship_type"])
        for row in snapshot.relationships
    ]
    if len(edge_keys) != len(set(edge_keys)):
        errors.append("duplicate canonical relationship edge")
    if any(source == target for source, target, _ in edge_keys):
        errors.append("canonical relationship self-loop")

    lengths = [len(row["text"]) for row in snapshot.passages]
    kinds = collections.Counter(str(row.get("passage_kind", "unknown")) for row in snapshot.passages)
    semantic_passages = sum(bool(row.get("semantic_eligible")) for row in snapshot.passages)
    lexical_passages = sum(bool(row.get("lexical_eligible")) for row in snapshot.passages)
    table_fallbacks = int(snapshot.manifest["counts"].get("table_source_span_fallbacks", 0))
    if table_fallbacks:
        warnings.append(
            f"{table_fallbacks} tables use exact selector/hash but only parent normalized-text offsets"
        )
    if sum(length < 20 for length in lengths):
        warnings.append(f"{sum(length < 20 for length in lengths)} passages are shorter than 20 characters")

    canonical_ids = set(metadata)
    endpoints = {identifier for edge in edge_keys for identifier in edge[:2]}
    result = {
        "dataset_id": snapshot.dataset_id,
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
        "counts": {
            **snapshot.manifest["counts"],
            "relationship_reference_only_endpoints": len(endpoints - canonical_ids),
            "semantic_passages": semantic_passages,
            "lexical_passages": lexical_passages,
            "passage_kind": dict(sorted(kinds.items())),
            "answer_ready_documents": sum(
                str(row.get("answer_ready", "false")).casefold() == "true"
                for row in metadata.values()
            ),
            "missing_index_passages": len(missing_index_passages),
            "missing_semantic_passages": len(missing_semantic_passages),
        },
        "chunk_quality": {
            **snapshot.manifest["chunk_validation"],
            "min_characters": min(lengths, default=0),
            "under_20_characters": sum(length < 20 for length in lengths),
            "under_50_characters": sum(length < 50 for length in lengths),
            "under_100_characters": sum(length < 100 for length in lengths),
            "total_characters": sum(lengths),
            "raw_float32_embedding_bytes": semantic_passages * 1536 * 4,
        },
        "missing_index_passage_ids": missing_index_passages,
        "missing_semantic_passage_ids": missing_semantic_passages,
    }
    write_json(source_dir / "canonical_validation.json", result)
    report = f"""# Canonical candidate validation

- Dataset: `{snapshot.dataset_id}`
- Status: **{result['status'].upper()}**
- Documents/content: {result['counts']['documents']} / {result['counts']['content_available']}
- Aliases: {result['counts']['aliases']}
- Relationships: {result['counts']['relationships']}; reference-only endpoints: {result['counts']['relationship_reference_only_endpoints']}
- Passages: {result['counts']['passages']} ({dict(kinds)})
- Semantic passages: {semantic_passages}; raw float32 vectors alone: {result['chunk_quality']['raw_float32_embedding_bytes']:,} bytes
- Legal units: {result['counts']['legal_units']}
- Answer-ready documents: {result['counts']['answer_ready_documents']}
- Errors: {len(errors)}; warnings: {len(warnings)}

Errors: {json.dumps(errors, ensure_ascii=False)}

Warnings: {json.dumps(warnings, ensure_ascii=False)}

`table_source_span_fallbacks` is a warning, not loss of provenance: those table
units retain an exact CSS selector and raw-fragment SHA-256, while only their
offset into normalized visible text falls back to the parent unit.
"""
    (source_dir / "CANONICAL_VALIDATION.md").write_text(report, encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=REPO_ROOT / "data" / "clean" / "medical_active_v2")
    args = parser.parse_args()
    result = validate(args.source_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
