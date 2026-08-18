from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from data_pipeline.tables import TABLE_EXTRACTION_VERSION, extract_html_tables, write_dataset_table_csv


class TableExtractionTest(unittest.TestCase):
    HTML = """
    <table class='prices'>
      <thead><tr><th>Mã</th><th>Giá</th></tr></thead>
      <tbody><tr><td>A01</td><td>1.000</td></tr><tr><td>B02</td><td>2.000</td></tr></tbody>
    </table>
    """

    def test_extracts_deterministic_cells_with_provenance(self) -> None:
        first = extract_html_tables("doc-a", self.HTML)
        second = extract_html_tables("doc-a", self.HTML)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        table = first[0]
        self.assertEqual(table.row_count, 3)
        self.assertEqual(table.column_count, 2)
        self.assertTrue(table.source_fragment_sha256)
        self.assertEqual(table.source_selector, "table:nth-of-type(1)")
        cells = [record for record in table.records if record["row_index"] == 2]
        self.assertEqual([(cell["header"], cell["value"]) for cell in cells], [("Mã", "A01"), ("Giá", "1.000")])
        self.assertTrue(all(cell["extraction_version"] == TABLE_EXTRACTION_VERSION for cell in table.records))

    def test_expands_colspan_and_rowspan_to_logical_columns(self) -> None:
        html = """
        <table><tr><th colspan='2'>Dịch vụ</th><th>Giá</th></tr>
        <tr><th>Mã</th><th>Tên</th><th rowspan='2'>BHYT</th></tr>
        <tr><td>X</td><td>Khám</td></tr></table>
        """
        table = extract_html_tables("doc-b", html)[0]
        values = {(record["row_index"], record["column_index"]): record for record in table.records}
        self.assertEqual(table.column_count, 3)
        self.assertEqual(values[(3, 3)]["value"], "BHYT")
        self.assertEqual(values[(3, 1)]["header"], "Dịch vụ / Mã")
        self.assertEqual(values[(3, 3)]["header"], "Giá / BHYT")

    def test_writer_is_dataset_scoped_and_does_not_require_a_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifact = write_dataset_table_csv(
                [{"document_id": "doc-a", "raw_html": self.HTML}, {"document_id": "doc-none", "raw_html": "<p>Không có bảng</p>"}],
                temp,
                "snapshot-123",
            )
            self.assertEqual(artifact.table_count, 1)
            self.assertEqual(artifact.cell_count, 6)
            self.assertEqual(artifact.tables_path.parent.name, "snapshot-123")
            with artifact.cells_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 6)
            self.assertEqual(rows[-1]["document_id"], "doc-a")


if __name__ == "__main__":
    unittest.main()
