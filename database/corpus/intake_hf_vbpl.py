#!/usr/bin/env python3
"""Create a review-only intake from selected ``tmquan/vbpl-vn`` shards.

The Hugging Face dataset is a useful *content* source, but it does not carry
an authoritative legal-force field.  This tool therefore never writes to a
serving corpus, never calls an activation command, and marks every exported
record as requiring an independent VBPL legal-status review.

It deliberately accepts explicit document numbers.  This prevents a broad
keyword search from silently importing provincial, obsolete, or unrelated
documents into the BHYT corpus.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DATASET_ID = "tmquan/vbpl-vn"
DATASET_REVISION = "11c902856b7a389788853fdd39b4998a5effa490"
DATASET_URL = f"https://huggingface.co/datasets/{DATASET_ID}/tree/{DATASET_REVISION}"
NUMBER_SPACE_RE = re.compile(r"\s+")


def normalize_document_number(value: str) -> str:
    """Normalize harmless formatting variation without changing legal identity."""
    return NUMBER_SPACE_RE.sub("", value).upper()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def select_records(rows: Iterable[dict[str, Any]], document_numbers: set[str]) -> list[dict[str, Any]]:
    """Return only central VBPL rows with a non-empty markdown body."""
    selected: list[dict[str, Any]] = []
    for row in rows:
        numbers = [normalize_document_number(str(value)) for value in row.get("doc_number") or []]
        matches = sorted(set(numbers) & document_numbers)
        if not matches:
            continue
        if row.get("source") != "vbpl.vn":
            raise ValueError(f"{matches}: unexpected source {row.get('source')!r}")
        if row.get("scope") != "trung_uong":
            raise ValueError(f"{matches}: refusing non-central scope {row.get('scope')!r}")
        markdown = str(row.get("markdown") or "").strip()
        if not markdown:
            raise ValueError(f"{matches}: document has no usable markdown body")
        item_id = str(row.get("item_id") or "").strip()
        source_url = str(row.get("source_url") or "").strip()
        if not item_id.isdigit() or not source_url.startswith("https://vbpl.vn/"):
            raise ValueError(f"{matches}: missing verifiable VBPL identity")
        selected.append({
            "intake_schema_version": 1,
            "review_status": "needs_official_status_verification",
            "promotion_status": "review_only_not_indexable",
            "hf_dataset": DATASET_ID,
            "hf_revision": DATASET_REVISION,
            "hf_dataset_url": DATASET_URL,
            "source": "vbpl.vn",
            "source_item_id": item_id,
            "source_url": source_url,
            "api_url": str(row.get("api_url") or "").strip(),
            "document_numbers": matches,
            "title": str(row.get("title") or "").strip(),
            "legal_type": str(row.get("legal_type") or "").strip(),
            "issuing_authority": str(row.get("issuing_authority") or "").strip(),
            "issue_date": str(row.get("issue_date") or "").strip(),
            "legal_area": str(row.get("legal_area") or "").strip(),
            "markdown": markdown,
            "markdown_sha256": sha256_text(markdown),
        })
    return sorted(selected, key=lambda record: (record["document_numbers"], record["source_item_id"]))


def load_rows(paths: list[Path]) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:  # pragma: no cover - depends on local tool environment
        raise RuntimeError("Install the one-off reader with: uv run --with pyarrow python ...") from error
    columns = [
        "item_id", "scope", "source", "source_url", "api_url", "title", "legal_type",
        "legal_area", "doc_number", "issue_date", "issuing_authority", "markdown",
    ]
    rows: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        rows.extend(pq.read_table(path, columns=columns).to_pylist())
    return rows


def write_intake(output_dir: Path, records: list[dict[str, Any]], requested_numbers: set[str]) -> None:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing intake: {output_dir}")
    found = {number for record in records for number in record["document_numbers"]}
    missing = sorted(requested_numbers - found)
    if missing:
        raise ValueError(f"Requested document number(s) were not found in supplied shard(s): {', '.join(missing)}")
    output_dir.mkdir(parents=True)
    intake_path = output_dir / "selected_documents.jsonl"
    with intake_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    manifest = {
        "schema_version": 1,
        "created_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "dataset": {"id": DATASET_ID, "revision": DATASET_REVISION, "url": DATASET_URL},
        "records": len(records),
        "requested_document_numbers": sorted(requested_numbers),
        "review_gate": "Every record must have legal force re-verified on vbpl.vn and pass corpus/eval gates before indexing.",
        "intake_sha256": sha256_text(intake_path.read_text(encoding="utf-8")),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True, help="Local documents-*.parquet shard; repeatable.")
    parser.add_argument("--document-number", action="append", required=True, help="Exact legal number, e.g. 51/2024/QH15; repeatable.")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    requested = {normalize_document_number(value) for value in args.document_number}
    records = select_records(load_rows(args.input), requested)
    write_intake(args.output_dir, records, requested)
    print(json.dumps({"status": "review_only_created", "records": len(records), "output_dir": str(args.output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
