"""Deterministic overlapping facets derived from canonical metadata."""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


FACET_VERSION = "metadata-facets-v1"


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _member_id(row: Mapping[str, Any]) -> str:
    return str(row["document_id"])


def build_facets(snapshot: Any) -> tuple[dict[str, str], ...]:
    """Return overlapping release members; no clustering or LLM inference."""

    result: list[dict[str, str]] = []
    for document in snapshot.documents:
        document_id = _member_id(document)
        metadata = document.get("metadata", {})
        values: list[tuple[str, str]] = []
        categories = str(metadata.get("agent_category", ""))
        values.extend(("category", value.casefold()) for value in categories.split(",") if _clean(value))
        for key, facet_name in (
            ("ngay_ban_hanh", "issued_date"),
            ("ngay_co_hieu_luc", "effective_from"),
            ("tinh_trang_hieu_luc", "status"),
            ("loai_van_ban", "document_type"),
            ("co_quan_ban_hanh", "issuing_authority"),
            ("pham_vi", "jurisdiction"),
        ):
            value = _clean(metadata.get(key))
            if value:
                values.append((facet_name, value))
        issued = _clean(metadata.get("ngay_ban_hanh"))
        year = issued[-4:] if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", issued) else ""
        if year:
            values.append(("issued_year", year))
        seen: set[tuple[str, str]] = set()
        for facet_name, facet_value in values:
            key = (facet_name, facet_value)
            if key in seen:
                continue
            seen.add(key)
            membership_key = hashlib.sha256(f"{snapshot.dataset_id}|{facet_name}|{facet_value}|{document_id}".encode("utf-8")).hexdigest()
            result.append({
                "dataset_id": snapshot.dataset_id,
                "facet_version": FACET_VERSION,
                "facet_name": facet_name,
                "facet_value": facet_value,
                "member_id": document_id,
                "membership_key": membership_key,
                "source": "metadata.csv",
            })
    return tuple(sorted(result, key=lambda row: (row["facet_name"], row["facet_value"], row["member_id"])))


def write_facets_csv(snapshot: Any, output_root: str | Path) -> Path:
    destination = Path(output_root) / snapshot.dataset_id
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "facet_memberships.csv"
    fields = ("dataset_id", "facet_version", "facet_name", "facet_value", "member_id", "membership_key", "source")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(build_facets(snapshot))
    return path
