from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from data_pipeline.canonical import SnapshotValidationError, build_snapshot, normalize_html


class CanonicalSnapshotTest(unittest.TestCase):
    def write(self, directory: Path, name: str, fields: list[str], rows: list[dict[str, str]]) -> None:
        with (directory / name).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def source(self, directory: Path, *, mismatched_document: bool = False) -> None:
        self.write(directory, "metadata.csv", ["id", "title", "agent_category"], [{"id": "1", "title": "Luật A", "agent_category": "bhyt,vien_phi"}])
        self.write(directory, "content.csv", ["id", "agent_category", "content_html"], [{"id": "1", "agent_category": "bhyt,vien_phi", "content_html": "<h1>Điều 1</h1><p>Nội dung <b>gốc</b>.</p><script>x()</script>"}])
        self.write(directory, "relationships.csv", ["agent_category", "doc_id", "other_doc_id", "relationship", "source_is_selected", "target_is_selected", "relationship_is_adverse", "source_title", "target_title"], [{"agent_category": "bhyt", "doc_id": "1", "other_doc_id": "external", "relationship": "Dẫn chiếu", "source_is_selected": "True", "target_is_selected": "False", "relationship_is_adverse": "True", "source_title": "Luật A", "target_title": "Ngoài corpus"}])
        self.write(directory, "documents.csv", ["id", "title", "content_html"], [{"id": "1", "title": "Khác" if mismatched_document else "Luật A", "content_html": "<h1>Điều 1</h1><p>Nội dung <b>gốc</b>.</p><script>x()</script>"}])
        self.write(directory, "metadata_bhyt.csv", ["id"], [{"id": "1"}])
        self.write(directory, "metadata_vien_phi.csv", ["id"], [{"id": "1"}])

    def test_snapshot_is_deterministic_and_preserves_audit_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            self.source(path)
            first, second = build_snapshot(path), build_snapshot(path)
            self.assertEqual(first.dataset_id, second.dataset_id)
            self.assertEqual(first.content[0]["normalized_text"], "Điều 1\n\nNội dung gốc.")
            self.assertEqual(first.content[0]["raw_html"], "<h1>Điều 1</h1><p>Nội dung <b>gốc</b>.</p><script>x()</script>")
            self.assertEqual(first.content[0]["raw_html_sha256"], hashlib.sha256(first.content[0]["raw_html"].encode()).hexdigest())
            self.assertEqual(first.passages[0]["section_label"], "Điều 1")
            self.assertEqual(first.manifest["counts"]["passages"], 1)
            self.assertEqual(first.relationships[0]["target_document_id"], "external")
            self.assertTrue(first.relationships[0]["source_is_selected"])
            self.assertFalse(first.relationships[0]["target_is_selected"])
            self.assertTrue(first.relationships[0]["relationship_is_adverse"])
            self.assertEqual(first.manifest["counts"]["legal_units"], 2)
            self.assertEqual(first.passages[0]["unit_id"], first.legal_units[1]["unit_id"])
            self.assertEqual(normalize_html("<p>A</p><style>x</style><p>B</p>"), "A\n\nB")

    def test_projection_mismatch_is_reported_not_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            self.source(path, mismatched_document=True)
            snapshot = build_snapshot(path)
            self.assertEqual(snapshot.documents[0]["metadata"]["title"], "Luật A")
            self.assertIn({"file": "documents.csv", "id": "1", "kind": "title_mismatch"}, snapshot.validation_issues)

    def test_bad_authority_data_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            self.source(path)
            self.write(path, "content.csv", ["id", "content_html"], [{"id": "unknown", "content_html": "<p>x</p>"}])
            with self.assertRaises(SnapshotValidationError):
                build_snapshot(path)

    def test_markup_without_visible_text_is_not_available_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            self.source(path)
            self.write(path, "content.csv", ["id", "content_html"], [{"id": "1", "content_html": "<script>secret()</script><style>.x{}</style>"}])
            snapshot = build_snapshot(path)
            self.assertEqual(snapshot.content[0]["normalized_text"], "")
            self.assertFalse(snapshot.content[0]["content_available"])


if __name__ == "__main__":
    unittest.main()
