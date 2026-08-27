from __future__ import annotations

import json
import tempfile
import unittest

from data_pipeline.canonical import CanonicalSnapshot
from data_pipeline.page_index_export import export_page_index


class PageIndexExportTest(unittest.TestCase):
    def test_export_keeps_dataset_and_parent_edges(self) -> None:
        snapshot = CanonicalSnapshot(
            dataset_id="snapshot-test",
            manifest={"source_manifest_sha256": "source-hash", "legal_unit_version": "v1"},
            documents=(), content=(), categories=(), relationships=(), passages=(), aliases=(), validation_issues=(),
            legal_units=(
                {"unit_id": "root", "parent_unit_id": "", "document_id": "doc", "unit_type": "document"},
                {"unit_id": "article", "parent_unit_id": "root", "document_id": "doc", "unit_type": "article"},
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            destination = export_page_index(snapshot, temp)
            manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["node_count"], 2)
            self.assertEqual(manifest["edge_count"], 1)
            self.assertIn(",root,article,HAS_UNIT", (destination / "page_index_edges.csv").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
