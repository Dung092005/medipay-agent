"""Build a deterministic canonical snapshot from the authoritative CSV files.

This module is intentionally database-free.  It gives analytics, embedding and
future database ingest exactly the same normalized text and passages.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from data_pipeline.page_index import PAGE_INDEX_VERSION, build_page_index, unit_for_offset
from data_pipeline.tables import extract_html_tables

csv.field_size_limit(sys.maxsize)

NORMALIZER_VERSION = "html-visible-text-v1"
PASSAGE_VERSION = "legal-unit-sentence-token-chunks-v4"
CHUNK_TARGET_TOKENS = 144
CHUNK_WORD_TARGET = 80
CHUNK_OVERLAP_WORDS = 16
LEGAL_UNIT_VERSION = PAGE_INDEX_VERSION
AUTHORITY_FILES = ("metadata.csv", "content.csv", "relationships.csv")
PROJECTION_FILES = ("documents.csv", "metadata_bhyt.csv", "metadata_vien_phi.csv")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE = re.compile(r"\s+")
_CATEGORIES = {"bhyt", "vien_phi"}
_BOOL_TRUE = {"true", "1", "yes", "y", "đúng", "co", "có"}
_BOOL_FALSE = {"false", "0", "no", "n", "sai", "không", "khong"}


class SnapshotValidationError(ValueError):
    """Raised when authoritative source files cannot form a safe snapshot."""


def _clean(value: str | None) -> str:
    value = unicodedata.normalize("NFC", (value or "").replace("\ufeff", ""))
    value = _CONTROL.sub("", value.replace("\r\n", "\n").replace("\r", "\n"))
    return _SPACE.sub(" ", value).strip()


def _sha256(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _find_flexible_whitespace(text: str, value: str, start: int = 0) -> tuple[int, int] | None:
    """Find extracted table text despite HTML ``br``/cell whitespace splits."""

    if not value:
        return None
    # HTML ``br`` tags can split even a punctuation boundary (``ký)Vương``),
    # so permit whitespace between lexical/punctuation pieces, not only where
    # the extracted value already contains a space.
    pieces = re.findall(r"[\wÀ-ỹ]+|[^\wÀ-ỹ\s]+", value, flags=re.UNICODE)
    pattern = r"\s*".join(re.escape(part) for part in pieces)
    found = re.search(pattern, text[start:])
    if not found:
        return None
    offset = start + found.start()
    return offset, start + found.end()


def _categories(value: str) -> tuple[str, ...]:
    categories = tuple(sorted({_clean(part).casefold() for part in value.split(",") if _clean(part)}))
    invalid = set(categories) - _CATEGORIES
    if invalid:
        raise SnapshotValidationError(f"Unsupported category: {', '.join(sorted(invalid))}")
    return categories


def _bool_field(value: str | None, *, field: str, row_number: int) -> bool:
    normalized = _clean(value).casefold()
    if normalized in _BOOL_TRUE:
        return True
    if normalized in _BOOL_FALSE or not normalized:
        return False
    raise SnapshotValidationError(f"relationships.csv row {row_number} has invalid {field}: {value!r}")


def _read_csv(path: Path, *, preserve_fields: frozenset[str] = frozenset()) -> list[dict[str, str]]:
    if not path.is_file():
        raise SnapshotValidationError(f"Missing required source file: {path.name}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SnapshotValidationError(f"Missing CSV header: {path.name}")
        result: list[dict[str, str]] = []
        for row in reader:
            if not any(_clean(value) for value in row.values() if value is not None):
                continue
            cleaned_row: dict[str, str] = {}
            for key, value in row.items():
                if key is None:
                    continue
                field = _clean(key)
                cleaned_row[field] = (value or "") if field in preserve_fields else _clean(value)
            result.append(cleaned_row)
        return result


class _VisibleBlocks(HTMLParser):
    _BLOCKS = {"address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "li", "main", "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul"}
    _IGNORED = {"script", "style", "noscript", "template"}
    _HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str | None, str]] = []
        self._current: list[str] = []
        self._ignored = 0
        self._heading: str | None = None

    def _flush(self) -> None:
        # Adjacent HTML text nodes often split around inline tags (for example
        # ``Nội dung <b>gốc</b>.``); joining them with a space changes text.
        text = _clean("".join(self._current))
        if text:
            self.blocks.append((self._heading, text))
        self._current = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in self._IGNORED:
            self._ignored += 1
        elif not self._ignored and tag in self._BLOCKS:
            self._flush()
            if tag in self._HEADINGS:
                self._heading = None

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self._IGNORED and self._ignored:
            self._ignored -= 1
        elif not self._ignored and tag in self._BLOCKS:
            text = _clean("".join(self._current))
            if tag in self._HEADINGS and text:
                self._heading = text
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self._current.append(data)

    def finish(self) -> list[tuple[str | None, str]]:
        self._flush()
        return self.blocks


def _parse_html(raw_html: str) -> list[tuple[str | None, str]]:
    if not raw_html:
        return []
    parser = _VisibleBlocks()
    try:
        parser.feed(raw_html)
        parser.close()
        return parser.finish()
    except Exception:
        return [(None, _clean(raw_html))] if _clean(raw_html) else []


def normalize_html(raw_html: str) -> str:
    """Return the one versioned visible-text projection for raw HTML."""

    return "\n\n".join(text for _, text in _parse_html(raw_html))


def _load_token_counter() -> Any:
    """Approximate token count used before the OpenAI embedding request.

    OpenAI's tokenizer is remote and is intentionally not downloaded during
    deterministic snapshot creation; the final embedding worker validates the
    request against the provider response.
    """
    cache: dict[str, int] = {}

    def count(text: str) -> int:
        if text not in cache:
            cache[text] = max(1, len(text.split()))
        return cache[text]

    return count


def _split_by_token_budget(
    text: str,
    token_count: Any,
    *,
    target: int = CHUNK_TARGET_TOKENS,
) -> list[tuple[int, int, str]]:
    """Split text by the real model tokenizer, including unbroken garbage runs."""

    if not text:
        return []
    if token_count(text) <= target:
        return [(0, len(text), text)]
    result: list[tuple[int, int, str]] = []
    start = 0
    while start < len(text):
        low, high, best = start + 1, len(text), start + 1
        while low <= high:
            middle = (low + high) // 2
            if token_count(text[start:middle]) <= target:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        boundaries = [match.end() for match in re.finditer(r"\S+", text[start:best])]
        end = start + (max(boundaries) if boundaries else best - start)
        if end <= start:
            end = best
        result.append((start, end, text[start:end]))
        if end >= len(text):
            break
        previous_words = list(re.finditer(r"\S+", text[start:end]))
        if len(previous_words) >= CHUNK_OVERLAP_WORDS:
            next_start = start + previous_words[-CHUNK_OVERLAP_WORDS].start()
        else:
            next_start = end
        start = max(start + 1, min(next_start, end - 1)) if next_start < end else end
    return result


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Return conservative Vietnamese sentence spans without breaking source offsets."""

    spans: list[tuple[int, int]] = []
    start = 0
    for match in re.finditer(r"[.!?]+[\"'»”’)\]]*(?=\s+|$)", text):
        # A numbered legal point such as ``1. Đối tượng`` is structure, not a
        # sentence boundary.  It is already represented by the PageIndex unit.
        line_prefix = text[text.rfind("\n", 0, match.start()) + 1:match.start()].strip()
        if re.fullmatch(r"\d{1,3}", line_prefix):
            continue
        end = match.end()
        if end > start:
            spans.append((start, end))
            start = end
    if start < len(text):
        spans.append((start, len(text)))
    return spans or ([(0, len(text))] if text else [])


def _split_sentence_aware(
    text: str,
    token_count: Any,
    *,
    target: int = CHUNK_TARGET_TOKENS,
) -> list[tuple[int, int, str]]:
    """Group complete sentences to target, then hard-split pathological sentences."""

    if not text:
        return []
    spans = _sentence_spans(text)
    if len(spans) <= 1:
        return _split_by_token_budget(text, token_count, target=target)

    result: list[tuple[int, int, str]] = []
    group_start: int | None = None
    group_end: int | None = None
    for sentence_start, sentence_end in spans:
        sentence = text[sentence_start:sentence_end]
        if token_count(sentence) > target:
            if group_start is not None and group_end is not None and group_end > group_start:
                result.append((group_start, group_end, text[group_start:group_end]))
            result.extend(
                (sentence_start + start, sentence_start + end, piece)
                for start, end, piece in _split_by_token_budget(sentence, token_count, target=target)
            )
            group_start = group_end = sentence_end
            continue
        if group_start is None or group_end is None:
            group_start, group_end = sentence_start, sentence_end
        elif token_count(text[group_start:sentence_end]) <= target:
            group_end = sentence_end
        else:
            result.append((group_start, group_end, text[group_start:group_end]))
            group_start, group_end = sentence_start, sentence_end
    if group_start is not None and group_end is not None and group_end > group_start:
        result.append((group_start, group_end, text[group_start:group_end]))
    return result


def _retrieval_blocks(
    blocks: list[tuple[str | None, str]],
    normalized_text: str,
    units: list[dict[str, Any]],
    token_count: Any,
) -> list[tuple[str, str, int, int]]:
    """Create retrieval chunks without crossing a deterministic legal unit.

    HTML blocks remain recoverable through their offsets, but short adjacent
    blocks are combined so table cells/formatting tags do not become thousands
    of useless semantic vectors.
    """

    located: list[tuple[str, str, int, int]] = []
    cursor = 0
    for _, text in blocks:
        start = normalized_text.find(text, cursor)
        if start < 0:
            continue
        end = start + len(text)
        cursor = end
        unit_id = unit_for_offset(units, start)
        located.append((unit_id, text, start, end))

    result: list[tuple[str, str, int, int]] = []
    pending_unit = ""
    pending_text: list[str] = []
    pending_start = 0
    pending_end = 0

    def flush() -> None:
        nonlocal pending_unit, pending_text, pending_start, pending_end
        if pending_text:
            result.append((pending_unit, "\n\n".join(pending_text), pending_start, pending_end))
        pending_unit = ""
        pending_text = []

    for unit_id, text, start, end in located:
        candidate = "\n\n".join([*pending_text, text])
        if pending_text and (unit_id != pending_unit or len(candidate.split()) > CHUNK_WORD_TARGET):
            flush()
        if not pending_text:
            pending_unit, pending_start = unit_id, start
        pending_text.append(text)
        pending_end = end
    flush()
    split_result: list[tuple[str, str, int, int]] = []
    for unit_id, text, start, end in result:
        for local_start, local_end, piece in _split_sentence_aware(text, token_count):
            split_result.append((unit_id, piece, start + local_start, start + local_end))
    return split_result


@dataclass(frozen=True)
class CanonicalSnapshot:
    dataset_id: str
    manifest: dict[str, Any]
    documents: tuple[dict[str, Any], ...]
    content: tuple[dict[str, Any], ...]
    categories: tuple[dict[str, str], ...]
    relationships: tuple[dict[str, Any], ...]
    passages: tuple[dict[str, Any], ...]
    legal_units: tuple[dict[str, Any], ...]
    validation_issues: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "manifest": self.manifest,
            "documents": list(self.documents),
            "content": list(self.content),
            "categories": list(self.categories),
            "relationships": list(self.relationships),
            "passages": list(self.passages),
            "legal_units": list(self.legal_units),
            "validation_issues": list(self.validation_issues),
        }


def _projection_issues(base: Path, documents: dict[str, dict[str, str]], contents: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for name in PROJECTION_FILES:
        path = base / name
        if not path.exists():
            continue
        rows = _read_csv(path, preserve_fields=frozenset({"content_html"}) if name == "documents.csv" else frozenset())
        projection_ids = {_clean(row.get("id")) for row in rows}
        if name == "documents.csv":
            for row in rows:
                identifier = _clean(row.get("id"))
                if identifier in documents and _clean(row.get("title")) != documents[identifier].get("title", ""):
                    issues.append({"file": name, "id": identifier, "kind": "title_mismatch"})
                if identifier in contents and (row.get("content_html") or "") != contents[identifier].get("content_html", ""):
                    issues.append({"file": name, "id": identifier, "kind": "content_mismatch"})
        else:
            expected = {identifier for identifier, row in documents.items() if name == "metadata_bhyt.csv" and "bhyt" in _categories(row.get("agent_category", "")) or name == "metadata_vien_phi.csv" and "vien_phi" in _categories(row.get("agent_category", ""))}
            if projection_ids != expected:
                issues.append({"file": name, "id": "", "kind": "category_projection_ids_mismatch"})
    return issues


def build_snapshot(source_dir: str | Path) -> CanonicalSnapshot:
    """Load authority CSVs and return an immutable deterministic snapshot.

    Projection discrepancies are reported in ``validation_issues``; authority
    integrity problems (duplicate/missing IDs) fail the build.
    """

    base = Path(source_dir)
    metadata_rows = _read_csv(base / "metadata.csv")
    content_rows = _read_csv(base / "content.csv", preserve_fields=frozenset({"content_html"}))
    relationship_rows = _read_csv(base / "relationships.csv")
    documents: dict[str, dict[str, str]] = {}
    for row in metadata_rows:
        identifier = _clean(row.get("id"))
        if not identifier or identifier in documents:
            raise SnapshotValidationError(f"metadata.csv has missing or duplicate id: {identifier!r}")
        _categories(row.get("agent_category", ""))
        documents[identifier] = row
    contents: dict[str, dict[str, str]] = {}
    for row in content_rows:
        identifier = _clean(row.get("id"))
        if not identifier or identifier in contents:
            raise SnapshotValidationError(f"content.csv has missing or duplicate id: {identifier!r}")
        if identifier not in documents:
            raise SnapshotValidationError(f"content.csv references unknown metadata id: {identifier}")
        contents[identifier] = row

    canonical_documents = tuple(
        {"document_id": identifier, "node_kind": "canonical_document", "metadata": dict(row)}
        for identifier, row in sorted(documents.items())
    )
    canonical_content: list[dict[str, Any]] = []
    passages: list[dict[str, Any]] = []
    legal_units: list[dict[str, Any]] = []
    categories: list[dict[str, str]] = []
    token_count = _load_token_counter()
    table_fallbacks = 0
    for identifier, row in sorted(documents.items()):
        for category in _categories(row.get("agent_category", "")):
            categories.append({"document_id": identifier, "category": category})
        raw_html = contents.get(identifier, {}).get("content_html", "")
        blocks = _parse_html(raw_html)
        normalized_text = "\n\n".join(text for _, text in blocks)
        document_units = build_page_index(
            identifier, normalized_text, raw_html_sha256=_sha256(raw_html),
        )
        table_search_cursor = 0
        for table in extract_html_tables(identifier, raw_html):
            table_values = [str(record.get("value", "")) for record in table.records if str(record.get("value", ""))]
            table_text = "\n".join(table_values)
            serialized_table = "\n\n".join(table_values)
            flexible = _find_flexible_whitespace(normalized_text, serialized_table, table_search_cursor) if serialized_table else None
            table_start, table_end = flexible if flexible else (-1, None)
            if table_start < 0 and table_values:
                first = _find_flexible_whitespace(normalized_text, table_values[0], table_search_cursor)
                last = _find_flexible_whitespace(normalized_text, table_values[-1], first[1] if first else table_search_cursor)
                table_start = first[0] if first else -1
                table_end = last[1] if last else None
            source_span_quality = (
                "empty" if not table_values
                else "exact" if table_start >= 0 and table_end is not None
                else "fallback_parent"
            )
            if table_end is not None:
                table_search_cursor = table_end
            if document_units:
                parent = unit_for_offset(document_units, table_start) if table_start >= 0 else next(
                    unit["unit_id"] for unit in document_units if unit["unit_type"] == "document"
                )
            else:
                parent = ""
            if source_span_quality == "fallback_parent":
                table_fallbacks += 1
                parent_unit = next((unit for unit in document_units if unit["unit_id"] == parent), None)
                table_start = parent_unit.get("source_start") if parent_unit else 0
                table_end = parent_unit.get("source_end") if parent_unit else table_start
            document_units.append({
                "unit_id": table.table_id,
                "document_id": identifier,
                "parent_unit_id": parent,
                "unit_type": "table",
                "ordinal_raw": str(table.table_ordinal),
                "label": f"Table {table.table_ordinal}",
                "heading": f"Table {table.table_ordinal}",
                "text": table_text,
                "source_start": table_start if isinstance(table_start, int) and table_start >= 0 else None,
                "source_end": table_end,
                "source_selector": table.source_selector,
                "source_fragment_sha256": table.source_fragment_sha256,
                "raw_fragment_sha256": table.source_fragment_sha256,
                "text_sha256": table.table_text_sha256,
                "parse_method": "deterministic",
                "parse_confidence": 1.0,
                "parser_version": table.extraction_version if hasattr(table, "extraction_version") else "html-tables-deterministic-v1",
                "source_span_quality": source_span_quality,
            })
        legal_units.extend(document_units)
        canonical_content.append({"document_id": identifier, "raw_html": raw_html, "raw_html_sha256": _sha256(raw_html), "normalized_text": normalized_text, "normalized_text_sha256": _sha256(normalized_text), "content_available": bool(normalized_text), "parser_version": NORMALIZER_VERSION})
        for order, (unit_id, text, source_start, source_end) in enumerate(
            _retrieval_blocks(blocks, normalized_text, document_units, token_count), start=1
        ):
            passage_id = _sha256(f"{identifier}:{PASSAGE_VERSION}:{order}:{source_start}:{source_end}:{text}")[:32]
            unit = next((item for item in document_units if item["unit_id"] == unit_id), None)
            passages.append({
                "passage_id": passage_id,
                "document_id": identifier,
                "unit_id": unit_id,
                "passage_order": order,
                "section_label": str(unit.get("heading", "")) if unit else "",
                "text": text,
                "source_start": source_start,
                "source_end": source_end,
                "text_sha256": _sha256(text),
                "parser_version": NORMALIZER_VERSION,
                "chunker_version": PASSAGE_VERSION,
            })

    relationships: list[dict[str, Any]] = []
    for row_number, row in enumerate(relationship_rows, start=2):
        source = _clean(row.get("doc_id"))
        target = _clean(row.get("other_doc_id"))
        relation_type = _clean(row.get("relationship"))
        if not source or not target or not relation_type:
            raise SnapshotValidationError(f"relationships.csv row {row_number} lacks endpoint or relationship")
        relation_categories = _categories(row.get("agent_category", ""))
        identity = "|".join((source, target, relation_type, ",".join(relation_categories)))
        relationships.append({"relationship_id": _sha256(identity), "source_document_id": source, "target_document_id": target, "relationship_type": relation_type, "categories": list(relation_categories), "source_is_selected": _bool_field(row.get("source_is_selected"), field="source_is_selected", row_number=row_number), "target_is_selected": _bool_field(row.get("target_is_selected"), field="target_is_selected", row_number=row_number), "relationship_is_adverse": _bool_field(row.get("relationship_is_adverse"), field="relationship_is_adverse", row_number=row_number), "source_title_raw": row.get("source_title", ""), "target_title_raw": row.get("target_title", ""), "source_row_hash": _sha256(_canonical_json(row))})

    file_hashes = {name: _sha256((base / name).read_bytes()) for name in AUTHORITY_FILES}
    as_of_dates = sorted(_clean(row.get("status_checked_at")) for row in metadata_rows if _clean(row.get("status_checked_at")))
    manifest = {
        "schema_version": 3,
        "pipeline_version": "canonical-evidence-v4",
        "normalizer_version": NORMALIZER_VERSION,
        "passage_version": PASSAGE_VERSION,
        "legal_unit_version": LEGAL_UNIT_VERSION,
        "relationship_flags_version": "source-relationship-flags-v1",
        "source_as_of_date": as_of_dates[-1] if as_of_dates else None,
        "source_files": file_hashes,
        "counts": {
            "documents": len(canonical_documents),
            "source_content_rows": len(content_rows),
            "content_records": len(canonical_content),
            "content_available": sum(bool(row["content_available"]) for row in canonical_content),
            "categories": len(categories),
            "relationships": len(relationships),
            "passages": len(passages),
            "legal_units": len(legal_units),
            "table_source_span_fallbacks": table_fallbacks,
        },
        "chunk_validation": {
            "target_tokens": CHUNK_TARGET_TOKENS,
            "max_tokens": max((token_count(row["text"]) for row in passages), default=0),
            "oversized_chunks": sum(token_count(row["text"]) > CHUNK_TARGET_TOKENS for row in passages),
            "missing_source_offsets": sum(row.get("source_start") is None or row.get("source_end") is None for row in passages),
        },
    }
    manifest_hash = _sha256(_canonical_json(manifest))
    manifest["source_manifest_sha256"] = manifest_hash
    return CanonicalSnapshot(dataset_id=f"snapshot-{manifest_hash[:16]}", manifest=manifest, documents=canonical_documents, content=tuple(canonical_content), categories=tuple(categories), relationships=tuple(relationships), passages=tuple(passages), legal_units=tuple(legal_units), validation_issues=tuple(_projection_issues(base, documents, contents)))
