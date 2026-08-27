#!/usr/bin/env python3
"""Build a rerunnable full P-151 + Hà Nội serving corpus.

The builder reads the P-151 six-CSV contract without modifying P-151 or the
Group raw corpus. It keeps every base document with a non-empty id, overlays
the existing Hà Nội module on duplicate ids, and writes a complete six-CSV
serving directory with explicit locality metadata.

Usage:
    python scripts/build_serving_p151_hanoi.py
    python scripts/build_serving_p151_hanoi.py --output-dir data/clean/serving_p151_hanoi
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
P151_ROOT = ROOT.parent / "P-151"
DEFAULT_MODULE = ROOT / "data" / "clean" / "hanoi_bhyt_module"
DEFAULT_OUTPUT = ROOT / "data" / "clean" / "serving_p151_hanoi"
REQUIRED_AUTHORITY_FILES = ("metadata.csv", "content.csv", "relationships.csv")
REQUIRED_MODULE_FILES = ("metadata.csv", "content.csv")
EXTRA_FIELDS = ("province", "answer_ready")
_PROVINCE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Hà Nội", ("thanh pho ha noi", "tp ha noi", "ha noi", "hanoi")),
    ("Đà Nẵng", ("thanh pho da nang", "tp da nang", "da nang", "danang")),
    (
        "Hồ Chí Minh",
        ("thanh pho ho chi minh", "tp ho chi minh", "tp hcm", "tphcm", "ho chi minh", "hcm", "sai gon", "saigon"),
    ),
)

csv.field_size_limit(min(2**31 - 1, 2**31 - 1))


def _clean(value: object) -> str:
    return unicodedata.normalize("NFC", str(value or "").replace("\ufeff", "")).strip()


def _fold(value: object) -> str:
    text = _clean(value).casefold()
    text = text.replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing CSV: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        fields = [_clean(field) for field in reader.fieldnames if field is not None]
        rows: list[dict[str, str]] = []
        for raw in reader:
            row = {field: _clean(raw.get(field, "")) for field in fields}
            if any(row.values()):
                rows.append(row)
        return fields, rows


def _write_csv(path: Path, fields: list[str], rows: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _has_required_files(path: Path) -> bool:
    return path.is_dir() and all((path / name).is_file() for name in REQUIRED_AUTHORITY_FILES)


def _has_module_files(path: Path) -> bool:
    return path.is_dir() and all((path / name).is_file() for name in REQUIRED_MODULE_FILES)


def resolve_base_dir(explicit: str | Path | None = None) -> Path:
    """Resolve P-151 clean input, falling back to its six-CSV raw input."""

    if explicit:
        candidate = Path(explicit).expanduser()
        if not _has_required_files(candidate):
            raise FileNotFoundError(f"Base source does not contain the authority CSVs: {candidate}")
        return candidate.resolve()

    clean_root = P151_ROOT / "data" / "clean"
    candidates: list[Path] = []
    if _has_required_files(clean_root):
        candidates.append(clean_root)
    if clean_root.is_dir():
        candidates.extend(
            sorted(
                (path for path in clean_root.rglob("*") if _has_required_files(path)),
                key=lambda path: str(path).casefold(),
            )
        )
    candidates.append(P151_ROOT / "data" / "raw")
    for candidate in candidates:
        if _has_required_files(candidate):
            return candidate.resolve()
    searched = ", ".join(str(path) for path in candidates) or str(clean_root)
    raise FileNotFoundError(f"No P-151 six-CSV source found; searched: {searched}")


def _is_local_scope(row: dict[str, str]) -> bool:
    scope = _fold(row.get("pham_vi"))
    if "dia phuong" in scope or "local" in scope:
        return True
    locality = " ".join(
        _clean(row.get(field))
        for field in ("province", "region", "thong_tin_ap_dung")
        if _clean(row.get(field))
    )
    return bool(locality and any(marker in _fold(locality) for marker in ("ha noi", "da nang", "ho chi minh")))


def _infer_province(row: dict[str, str]) -> str:
    """Infer a known locality while keeping national scope province-free."""
    scope = _fold(row.get("pham_vi"))
    if scope in {"trung uong", "central", "national"}:
        return ""
    existing = _clean(row.get("province") or row.get("region"))
    searchable = " ".join(
        _clean(row.get(field))
        for field in (
            "province", "region", "thong_tin_ap_dung", "title",
            "co_quan_ban_hanh", "nguon_thu_thap",
        )
        if _clean(row.get(field))
    )
    folded = f" {_fold(searchable)} "
    for canonical, aliases in _PROVINCE_ALIASES:
        if any(f" {alias} " in folded for alias in aliases):
            return canonical
    return existing


def _is_false(value: object) -> bool:
    return _fold(value) in {"false", "0", "no", "n", "sai", "khong"}


def _answer_ready(row: dict[str, str], content_html: str) -> str:
    explicit = _clean(row.get("answer_ready"))
    if explicit:
        return "false" if _is_false(explicit) else "true"
    visible = html.unescape(re.sub(r"<[^>]+>", " ", content_html or ""))
    return "true" if re.sub(r"\s+", " ", visible).strip() else "false"


def _field_union(*field_lists: Iterable[str], ensure: Iterable[str] = ()) -> list[str]:
    fields: list[str] = []
    for values in (*field_lists, ensure):
        for field in values:
            field = _clean(field)
            if field and field not in fields:
                fields.append(field)
    return fields


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _merge_relationships(
    rows: Iterable[dict[str, str]], selected_ids: set[str], fields: list[str]
) -> list[dict[str, str]]:
    merged: dict[tuple[str, ...], dict[str, str]] = {}
    for row in rows:
        source = _clean(row.get("doc_id") or row.get("source_doc_id") or row.get("source_id"))
        target = _clean(row.get("other_doc_id") or row.get("target_doc_id") or row.get("target_id"))
        if not source or not target or source not in selected_ids or target not in selected_ids:
            continue
        normalized = {field: _clean(row.get(field, "")) for field in fields}
        key = tuple(normalized.get(field, "") for field in fields)
        merged[key] = normalized
    return list(merged.values())


def build_corpus(
    *,
    base_dir: str | Path | None = None,
    module_dir: str | Path = DEFAULT_MODULE,
    output_dir: str | Path = DEFAULT_OUTPUT,
) -> dict[str, object]:
    base = resolve_base_dir(base_dir)
    module = Path(module_dir).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if not _has_module_files(module):
        raise FileNotFoundError(f"Hà Nội module does not contain metadata.csv and content.csv: {module}")

    base_meta_fields, base_meta_rows = _read_csv(base / "metadata.csv")
    base_content_fields, base_content_rows = _read_csv(base / "content.csv")
    base_rel_fields, base_rel_rows = _read_csv(base / "relationships.csv")
    local_meta_fields, local_meta_rows = _read_csv(module / "metadata.csv")
    local_content_fields, local_content_rows = _read_csv(module / "content.csv")
    if (module / "relationships.csv").is_file():
        local_rel_fields, local_rel_rows = _read_csv(module / "relationships.csv")
    else:
        local_rel_fields, local_rel_rows = [], []

    # The source order is intentional: every P-151 row forms the base and the
    # local module is the explicit override if an id is present in both.
    metadata_by_id: dict[str, dict[str, str]] = {}
    source_by_id: dict[str, str] = {}
    for row in base_meta_rows:
        identifier = _clean(row.get("id"))
        if identifier:
            metadata_by_id[identifier] = dict(row)
            source_by_id[identifier] = "p151"
    for row in local_meta_rows:
        identifier = _clean(row.get("id"))
        if identifier:
            metadata_by_id[identifier] = dict(row)
            source_by_id[identifier] = "hanoi"

    content_by_id: dict[str, dict[str, str]] = {}
    for row in [*base_content_rows, *local_content_rows]:
        identifier = _clean(row.get("id"))
        if identifier and identifier in metadata_by_id:
            content_by_id[identifier] = dict(row)

    metadata_fields = _field_union(base_meta_fields, local_meta_fields, ensure=EXTRA_FIELDS)
    content_fields = _field_union(base_content_fields, local_content_fields, ensure=("id", "agent_category", "content_html"))
    document_fields = _field_union(metadata_fields, content_fields, ensure=("content_html",))
    relationship_fields = _field_union(
        base_rel_fields,
        local_rel_fields,
        ensure=(
            "agent_category", "doc_id", "other_doc_id", "relationship",
            "source_is_selected", "target_is_selected", "relationship_is_adverse",
            "source_title", "target_title",
        ),
    )

    metadata_rows: list[dict[str, str]] = []
    document_rows: list[dict[str, str]] = []
    content_rows: list[dict[str, str]] = []
    for identifier in sorted(metadata_by_id):
        metadata = dict(metadata_by_id[identifier])
        is_hanoi = source_by_id[identifier] == "hanoi"
        if is_hanoi:
            metadata["pham_vi"] = "Địa phương"
            metadata["province"] = "Hà Nội"
            if not _clean(metadata.get("thong_tin_ap_dung")):
                metadata["thong_tin_ap_dung"] = "Thành phố Hà Nội"
        else:
            metadata["province"] = _infer_province(metadata)
        content = dict(content_by_id.get(identifier, {}))
        content_html = content.get("content_html", "")
        metadata["answer_ready"] = _answer_ready(metadata, content_html)
        metadata_rows.append(metadata)

        document = {**metadata, **content}
        document["id"] = identifier
        document["agent_category"] = metadata.get("agent_category", content.get("agent_category", ""))
        document["content_html"] = content_html
        document_rows.append(document)
        if content:
            content["id"] = identifier
            content["agent_category"] = metadata.get("agent_category", content.get("agent_category", ""))
            content_rows.append(content)

    selected_ids = set(metadata_by_id)
    relationships = _merge_relationships(
        [*base_rel_rows, *local_rel_rows], selected_ids, relationship_fields
    )
    metadata_bhyt = [row for row in metadata_rows if "bhyt" in {_fold(part) for part in row.get("agent_category", "").split(",")}]
    metadata_vien_phi = [row for row in metadata_rows if "vien_phi" in {_fold(part) for part in row.get("agent_category", "").split(",")}]

    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "metadata.csv", metadata_fields, metadata_rows)
    _write_csv(output / "content.csv", content_fields, content_rows)
    _write_csv(output / "documents.csv", document_fields, document_rows)
    _write_csv(output / "metadata_bhyt.csv", metadata_fields, metadata_bhyt)
    _write_csv(output / "metadata_vien_phi.csv", metadata_fields, metadata_vien_phi)
    _write_csv(output / "relationships.csv", relationship_fields, relationships)

    digest = hashlib.sha256()
    for name, fields, rows in (
        ("metadata.csv", metadata_fields, metadata_rows),
        ("content.csv", content_fields, content_rows),
        ("relationships.csv", relationship_fields, relationships),
    ):
        digest.update(name.encode("utf-8"))
        digest.update(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    dataset_id = f"serving-p151-hanoi-{digest.hexdigest()[:12]}"
    central_count = sum(_fold(row.get("pham_vi")) == "trung uong" for row in metadata_rows)
    non_central_count = len(metadata_rows) - central_count
    hanoi_count = sum(_fold(row.get("province")) == "ha noi" for row in metadata_rows)
    answer_ready_count = sum(row.get("answer_ready") == "true" for row in metadata_rows)
    summary: dict[str, object] = {
        "dataset_id": dataset_id,
        "snapshot_id": dataset_id,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "base_source": _relative(base),
        "hanoi_source": _relative(module),
        "document_count": len(metadata_rows),
        "content_count": len(content_rows),
        "relationship_count": len(relationships),
        "central_count": central_count,
        "non_central_count": non_central_count,
        "hanoi_count": hanoi_count,
        "answer_ready_count": answer_ready_count,
        "schema": {
            "authority_files": list(REQUIRED_AUTHORITY_FILES),
            "projection_files": ["documents.csv", "metadata_bhyt.csv", "metadata_vien_phi.csv"],
            "metadata_fields": metadata_fields,
            "content_fields": content_fields,
            "relationship_fields": relationship_fields,
        },
        "scope_policy": "all P-151 rows with non-empty id retained; Hà Nội module overrides duplicate ids",
        "routing_policy": {
            "explicit_locality": "target locality + Trung ương; other localities demoted",
            "unspecified_locality": "Hà Nội boosted for local support/price/registration intent",
            "national_question": "Trung ương first; local documents are not boosted",
        },
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    readme = f"""# Serving corpus: full P-151 + Hà Nội

Dataset/snapshot: `{dataset_id}`

Built by `python scripts/build_serving_p151_hanoi.py`.

- Base: `{_relative(base)}`; every row with a non-empty `id` is retained,
  including all P-151 localities.
- Local module: `{_relative(module)}`; its rows override duplicate ids and are
  marked `pham_vi=Địa phương` and `province=Hà Nội`.
- Output: six P-151-compatible CSV files plus `summary.json`.
- Counts: {len(metadata_rows)} documents ({central_count} Trung ương, {non_central_count} non-central,
  including {hanoi_count} Hà Nội), {len(relationships)} relationships, {answer_ready_count}
  answer-ready documents.

Routing is implemented at retrieval time: an explicit locality selects that
locality plus Trung ương; unspecified local support/price/registration queries
prefer Hà Nội; national legal questions keep Trung ương first.

This build does not ingest databases or activate a Qdrant alias.
"""
    (output / "README.md").write_text(readme, encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", help="Optional P-151 clean/raw source directory")
    parser.add_argument("--module-dir", default=str(DEFAULT_MODULE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    summary = build_corpus(
        base_dir=args.base_dir,
        module_dir=args.module_dir,
        output_dir=args.output_dir,
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
