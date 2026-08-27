"""Deterministic PageIndex-like hierarchy for Vietnamese legal documents.

The source corpus is HTML rather than paginated PDF, so an addressable legal
unit tree is a better analogue than synthetic page numbers.  Every node stores
character offsets into the versioned normalized text; raw HTML remains the
authority and the tree is an immutable per-release projection.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

PAGE_INDEX_VERSION = "legal-page-index-v2"


@dataclass(frozen=True)
class _Match:
    start: int
    unit_type: str
    level: int
    label: str
    ordinal_raw: str


_PATTERNS: tuple[tuple[str, int, re.Pattern[str]], ...] = (
    ("chapter", 1, re.compile(r"(?im)^\s*(Chương\s+([IVXLCDM]+|\d+)[^\n]*)")),
    ("section", 2, re.compile(r"(?im)^\s*(Mục\s+([IVXLCDM]+|\d+)[^\n]*)")),
    ("article", 3, re.compile(r"(?im)^\s*(Điều\s+(\d+[A-Za-z]?)[^\n]*)")),
    ("clause", 4, re.compile(r"(?im)^\s*(\d+)\.\s+[^\n]+")),
    ("point", 5, re.compile(r"(?im)^\s*([a-zđ])\)\s+[^\n]+")),
    ("appendix", 2, re.compile(r"(?im)^\s*(Phụ lục(?:\s+[IVXLCDM\d]+)?[^\n]*)")),
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _matches(text: str) -> list[_Match]:
    candidates: list[_Match] = []
    for unit_type, level, pattern in _PATTERNS:
        for found in pattern.finditer(text):
            label = found.group(0).strip()
            # The first capture group is the whole heading, the optional second
            # is its ordinal.  Clause/point patterns use the first group itself.
            ordinal = found.group(2).strip() if found.lastindex and found.lastindex >= 2 else found.group(1).strip()
            candidates.append(_Match(found.start(), unit_type, level, label, ordinal))
    # A clause/point can match a table row or example line.  Keep only one
    # strongest structural interpretation per offset.
    result: list[_Match] = []
    for candidate in sorted(candidates, key=lambda item: (item.start, item.level)):
        if result and result[-1].start == candidate.start:
            continue
        result.append(candidate)
    return result


def build_page_index(document_id: str, normalized_text: str, *, raw_html_sha256: str = "") -> list[dict[str, Any]]:
    """Return a hierarchy of document/chapter/article/clause/point units.

    Nodes with no confidently recognised heading remain children of the
    document node rather than being hallucinated as legal provisions.
    """

    if not normalized_text:
        return []
    document_id = str(document_id)
    document_unit_id = _sha256(f"{document_id}:document:0:{len(normalized_text)}")[:32]
    nodes: list[dict[str, Any]] = [{
        "unit_id": document_unit_id,
        "document_id": document_id,
        "parent_unit_id": "",
        "unit_type": "document",
        "ordinal_raw": "",
        "label": "",
        "heading": "",
        "text": normalized_text,
        "source_start": 0,
        "source_end": len(normalized_text),
        "raw_fragment_sha256": raw_html_sha256 or _sha256(normalized_text),
        "text_sha256": _sha256(normalized_text),
        "parse_method": "deterministic",
        "parse_confidence": 1.0,
        "parser_version": PAGE_INDEX_VERSION,
    }]
    matches = _matches(normalized_text)
    stack: list[tuple[_Match, str]] = []
    for index, match in enumerate(matches):
        end = len(normalized_text)
        for later in matches[index + 1 :]:
            if later.level <= match.level:
                end = later.start
                break
        while stack and stack[-1][0].level >= match.level:
            stack.pop()
        parent_unit_id = stack[-1][1] if stack else document_unit_id
        fragment = normalized_text[match.start:end].strip()
        unit_id = _sha256(f"{document_id}:{match.unit_type}:{match.start}:{end}:{match.label}")[:32]
        nodes.append({
            "unit_id": unit_id,
            "document_id": document_id,
            "parent_unit_id": parent_unit_id,
            "unit_type": match.unit_type,
            "ordinal_raw": match.ordinal_raw,
            "label": match.label,
            "heading": match.label,
            "text": fragment,
            "source_start": match.start,
            "source_end": end,
            "raw_fragment_sha256": _sha256(fragment),
            "text_sha256": _sha256(fragment),
            "parse_method": "deterministic",
            "parse_confidence": 0.98 if match.unit_type in {"clause", "point"} else 1.0,
            "parser_version": PAGE_INDEX_VERSION,
        })
        stack.append((match, unit_id))
    return nodes


def unit_for_offset(units: list[dict[str, Any]], offset: int) -> str:
    """Return the deepest known legal unit containing a normalized-text offset."""

    containing = [
        unit for unit in units
        if unit["unit_type"] != "document"
        and unit.get("source_start") is not None
        and unit.get("source_end") is not None
        and int(unit["source_start"]) <= offset < int(unit["source_end"])
    ]
    if not containing:
        return next(unit["unit_id"] for unit in units if unit["unit_type"] == "document")
    # Lowest span is the most specific child in a well-formed tree.
    return min(containing, key=lambda unit: int(unit["source_end"]) - int(unit["source_start"]))["unit_id"]
