"""Deterministic, release-scoped extraction of HTML tables.

Legal schedules and price appendices often express the decisive condition in a
table rather than in prose.  This module deliberately keeps table extraction
outside the canonical authority model: its CSV files are *derived artifacts*
which retain a selector and hashes back to the raw ``content_html`` source.
They can therefore be rebuilt for any immutable dataset release.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

TABLE_EXTRACTION_VERSION = "html-tables-deterministic-v2"
_SPACE = re.compile(r"\s+")
_TD_HEADER_LABEL = re.compile(
    r"^(?:stt|tt|mã(?:\s+số)?|tên(?:\s+(?:dịch vụ|kỹ thuật|hoạt chất))?|nội dung|"
    r"đơn vị(?:\s+tính)?|mức thu|mức giá|giá|ghi chú|tuyến|hạng(?:\s+bệnh viện)?|"
    r"số lượng|đối tượng|chỉ tiêu|dịch vụ)$",
    flags=re.IGNORECASE,
)


def _clean(value: str | None) -> str:
    return _SPACE.sub(" ", (value or "").replace("\xa0", " ")).strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ExtractedTable:
    """One source table and its cell-level, queryable representation."""

    document_id: str
    table_id: str
    table_ordinal: int
    source_selector: str
    source_fragment_sha256: str
    table_text_sha256: str
    row_count: int
    column_count: int
    records: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class TableCsvArtifact:
    """Locations and counts for the derived files of one release."""

    dataset_id: str
    tables_path: Path
    cells_path: Path
    table_count: int
    cell_count: int


@dataclass
class _Cell:
    tag: str
    text_parts: list[str]
    colspan: int
    rowspan: int

    @property
    def text(self) -> str:
        return _clean("".join(self.text_parts))


@dataclass
class _Table:
    ordinal: int
    raw_parts: list[str]
    rows: list[list[_Cell]]


class _TableParser(HTMLParser):
    """A small HTML table parser with logical-grid colspan/rowspan handling.

    It purposefully skips nested tables as cells of their outer table.  Nested
    tables are independently extracted, which avoids mixing two distinct legal
    schedules into one record set.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[_Table] = []
        self._table_stack: list[_Table] = []
        self._row: list[_Cell] | None = None
        self._cell: _Cell | None = None
        self._ignored = 0

    @staticmethod
    def _span(attrs: list[tuple[str, str | None]], name: str) -> int:
        raw = next((value for key, value in attrs if key.casefold() == name), None)
        try:
            return max(1, int(raw or "1"))
        except ValueError:
            return 1

    @property
    def _active_table(self) -> _Table | None:
        return self._table_stack[-1] if self._table_stack else None

    def _append_raw(self, value: str) -> None:
        for table in self._table_stack:
            table.raw_parts.append(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        folded = tag.casefold()
        raw = self.get_starttag_text() or f"<{tag}>"
        self._append_raw(raw)
        if folded in {"script", "style", "noscript", "template"}:
            self._ignored += 1
            return
        if folded == "table":
            table = _Table(ordinal=len(self.tables) + 1, raw_parts=[raw], rows=[])
            self.tables.append(table)
            self._table_stack.append(table)
            return
        # Only the innermost table owns row/cell state.
        if not self._active_table:
            return
        if folded == "tr":
            self._row = []
        elif folded in {"td", "th"} and self._row is not None:
            self._cell = _Cell(
                tag=folded,
                text_parts=[],
                colspan=self._span(attrs, "colspan"),
                rowspan=self._span(attrs, "rowspan"),
            )
        elif folded == "br" and self._cell is not None:
            self._cell.text_parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        folded = tag.casefold()
        self._append_raw(f"</{tag}>")
        if folded in {"script", "style", "noscript", "template"} and self._ignored:
            self._ignored -= 1
            return
        if folded == "table":
            if self._table_stack:
                self._table_stack.pop()
            self._row = None
            self._cell = None
            return
        if not self._active_table:
            return
        if folded in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(self._cell)
            self._cell = None
        elif folded == "tr" and self._row is not None:
            if self._row:
                self._active_table.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        self._append_raw(data)
        if not self._ignored and self._cell is not None:
            self._cell.text_parts.append(data)

    def handle_entityref(self, name: str) -> None:  # pragma: no cover - convert_charrefs handles it
        self._append_raw(f"&{name};")


def _logical_rows(rows: Sequence[Sequence[_Cell]]) -> list[list[_Cell | None]]:
    """Expand each source row into a stable logical grid.

    A repeated rowspan cell remains visible in following rows.  This is useful
    for retrieval because the reader sees the inherited header/value context.
    """

    result: list[list[_Cell | None]] = []
    # ``active`` contains only spans inherited from *previous* source rows.
    # New rowspans must not be consumed in the same row they are declared.
    active: dict[int, tuple[int, _Cell]] = {}
    for source_row in rows:
        grid: list[_Cell | None] = []
        col = 0
        next_active: dict[int, tuple[int, _Cell]] = {}
        pending = iter(source_row)
        current = next(pending, None)
        while current is not None or active:
            if col in active:
                remaining, cell = active[col]
                grid.append(cell)
                if remaining == 1:
                    del active[col]
                else:
                    del active[col]
                    next_active[col] = (remaining - 1, cell)
                col += 1
                continue
            if current is None:
                grid.append(None)
                col += 1
                continue
            for offset in range(current.colspan):
                grid.append(current)
                if current.rowspan > 1:
                    next_active[col + offset] = (current.rowspan - 1, current)
            col += current.colspan
            current = next(pending, None)
        result.append(grid)
        active = next_active
    width = max((len(row) for row in result), default=0)
    return [row + [None] * (width - len(row)) for row in result]


def _column_headers(grid: Sequence[Sequence[_Cell | None]]) -> tuple[list[str], int]:
    """Return column labels and number of leading header rows."""

    if not grid:
        return [], 0
    header_rows = 0
    for row in grid[:3]:
        populated = [cell for cell in row if cell is not None]
        labels = {_clean(cell.text).casefold() for cell in populated if _clean(cell.text)}
        # A large part of the source corpus uses TD for visually bold header
        # rows. Recognise only a narrow legal-table vocabulary, and stop as
        # soon as the first column looks like a numbered data row.
        first_value = _clean(populated[0].text) if populated else ""
        td_header = (
            bool(labels)
            and not re.fullmatch(r"\d+[.)]?", first_value)
            and sum(bool(_TD_HEADER_LABEL.fullmatch(label)) for label in labels)
            >= max(1, min(2, len(labels) // 2))
        )
        if populated and (all(cell.tag == "th" for cell in populated) or td_header):
            header_rows += 1
        else:
            break
    if not header_rows:
        return [f"column_{index + 1}" for index in range(len(grid[0]))], 0
    headers: list[str] = []
    for column in range(len(grid[0])):
        parts: list[str] = []
        for row in grid[:header_rows]:
            cell = row[column]
            if cell and cell.text and cell.text not in parts:
                parts.append(cell.text)
        headers.append(" / ".join(parts) or f"column_{column + 1}")
    return headers, header_rows


def extract_html_tables(document_id: str, raw_html: str) -> tuple[ExtractedTable, ...]:
    """Extract top-level and nested source tables without LLM interpretation.

    Every output record points back to its source table via a deterministic CSS
    selector and a fragment hash.  Empty tables are retained in the table CSV
    but naturally yield no cell records.
    """

    if not _clean(document_id) or not raw_html:
        return ()
    parser = _TableParser()
    try:
        parser.feed(raw_html)
        parser.close()
    except Exception:
        return ()
    extracted: list[ExtractedTable] = []
    for table in parser.tables:
        grid = _logical_rows(table.rows)
        headers, header_rows = _column_headers(grid)
        fragment = "".join(table.raw_parts)
        fragment_hash = _sha256(fragment)
        table_text = "\n".join(" | ".join(cell.text if cell else "" for cell in row) for row in grid)
        table_id = _sha256(f"{document_id}:{TABLE_EXTRACTION_VERSION}:{table.ordinal}:{fragment_hash}")[:32]
        selector = f"table:nth-of-type({table.ordinal})"
        records: list[dict[str, Any]] = []
        for row_index, row in enumerate(grid, start=1):
            row_header = ""
            # First non-empty TH in a data row is often a row label (e.g. drug
            # name); retain it in every value record but do not infer semantics.
            if row_index > header_rows:
                row_header = next((cell.text for cell in row if cell and cell.tag == "th" and cell.text), "")
            for column_index, cell in enumerate(row, start=1):
                if cell is None:
                    continue
                records.append({
                    "document_id": document_id,
                    "table_id": table_id,
                    "table_ordinal": table.ordinal,
                    "row_index": row_index,
                    "column_index": column_index,
                    "header": headers[column_index - 1] if column_index <= len(headers) else f"column_{column_index}",
                    "row_header": row_header,
                    "value": cell.text,
                    "cell_tag": cell.tag,
                    "colspan": cell.colspan,
                    "rowspan": cell.rowspan,
                    "source_selector": selector,
                    "source_fragment_sha256": fragment_hash,
                    "table_text_sha256": _sha256(table_text),
                    "extraction_version": TABLE_EXTRACTION_VERSION,
                })
        extracted.append(ExtractedTable(
            document_id=document_id,
            table_id=table_id,
            table_ordinal=table.ordinal,
            source_selector=selector,
            source_fragment_sha256=fragment_hash,
            table_text_sha256=_sha256(table_text),
            row_count=len(grid),
            column_count=len(grid[0]) if grid else 0,
            records=tuple(records),
        ))
    return tuple(extracted)


_TABLE_FIELDS = (
    "document_id", "table_id", "table_ordinal", "source_selector",
    "source_fragment_sha256", "table_text_sha256", "row_count", "column_count",
    "extraction_version",
)
_CELL_FIELDS = (
    "document_id", "table_id", "table_ordinal", "row_index", "column_index",
    "header", "row_header", "value", "cell_tag", "colspan", "rowspan",
    "source_selector", "source_fragment_sha256", "table_text_sha256", "extraction_version",
)


def write_dataset_table_csv(
    content_rows: Iterable[Mapping[str, Any]],
    output_dir: str | Path,
    dataset_id: str,
) -> TableCsvArtifact:
    """Build ``tables.csv`` and ``table_cells.csv`` under one release directory.

    The function only writes derived data below ``output_dir/dataset_id``.  It
    neither changes authority CSVs nor assumes a database is present.
    """

    if not _clean(dataset_id):
        raise ValueError("dataset_id must not be empty")
    dataset_dir = Path(output_dir) / dataset_id
    dataset_dir.mkdir(parents=True, exist_ok=True)
    tables_path = dataset_dir / "tables.csv"
    cells_path = dataset_dir / "table_cells.csv"
    tables: list[ExtractedTable] = []
    for row in sorted(content_rows, key=lambda item: str(item.get("document_id", ""))):
        tables.extend(extract_html_tables(str(row.get("document_id", "")), str(row.get("raw_html", ""))))
    with tables_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_TABLE_FIELDS)
        writer.writeheader()
        for table in tables:
            writer.writerow({
                "document_id": table.document_id, "table_id": table.table_id,
                "table_ordinal": table.table_ordinal, "source_selector": table.source_selector,
                "source_fragment_sha256": table.source_fragment_sha256,
                "table_text_sha256": table.table_text_sha256, "row_count": table.row_count,
                "column_count": table.column_count, "extraction_version": TABLE_EXTRACTION_VERSION,
            })
    with cells_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CELL_FIELDS)
        writer.writeheader()
        for table in tables:
            writer.writerows(table.records)
    return TableCsvArtifact(
        dataset_id=dataset_id, tables_path=tables_path, cells_path=cells_path,
        table_count=len(tables), cell_count=sum(len(table.records) for table in tables),
    )


def write_snapshot_table_csv(snapshot: Any, output_dir: str | Path) -> TableCsvArtifact:
    """Convenience adapter for ``CanonicalSnapshot`` without importing it."""

    return write_dataset_table_csv(snapshot.content, output_dir, snapshot.dataset_id)
