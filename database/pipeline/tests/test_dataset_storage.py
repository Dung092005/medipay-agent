from __future__ import annotations

import unittest

from data_pipeline.storage import (
    collection_name_for_dataset,
    canonical_snapshot_to_dataset,
    create_dataset_schema,
    dataset_fingerprint,
    stage_graph_dataset,
    validate_dataset_id,
)


class RecordingCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def __enter__(self) -> "RecordingCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, params: object = None) -> None:
        self.calls.append((statement, params))

    def executemany(self, statement: str, params: object) -> None:
        self.calls.append((statement, list(params)))


class RecordingConnection:
    def __init__(self) -> None:
        self.cursor_instance = RecordingCursor()
        self.commits = 0

    def cursor(self) -> RecordingCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1


class Dataset:
    manifest = {"pipeline_version": "test", "generated_at_utc": "ignore-me"}
    document_nodes = [
        {"id": "doc-1", "title": "Văn bản", "is_external": False},
        {"id": "doc-2", "title": "Căn cứ", "is_external": True},
    ]
    contents = [{"document_id": "doc-1", "content_text": "Nội dung", "text_sha256": "abc", "raw_html": "<p>Nội dung</p>", "raw_html_sha256": "raw"}]
    categories = [{"document_id": "doc-1", "category": "bhyt"}]
    relationships = [{"source_id": "doc-1", "target_id": "doc-2", "relationship_type": "CITES"}]
    chunks = [{"chunk_id": "doc-1:0001", "document_id": "doc-1", "chunk_order": 1, "text": "Nội dung"}]


class ReleaseStorageTest(unittest.TestCase):
    def test_manifest_fingerprint_ignores_build_timestamp(self) -> None:
        first = {"pipeline_version": "2", "generated_at_utc": "2026-01-01T00:00:00Z"}
        second = {"pipeline_version": "2", "generated_at_utc": "2026-02-01T00:00:00Z"}
        self.assertEqual(dataset_fingerprint(first), dataset_fingerprint(second))

    def test_dataset_identifier_and_collection_are_namespaced(self) -> None:
        dataset_id = "r20260807120000-abc123"
        validate_dataset_id(dataset_id)
        self.assertEqual(collection_name_for_dataset(dataset_id), "legal_graph_chunks__r20260807120000_abc123")
        with self.assertRaises(ValueError):
            validate_dataset_id("Bad release id")

    def test_schema_creates_active_views_and_atomic_pointer_table(self) -> None:
        conn = RecordingConnection()
        create_dataset_schema(conn)
        sql = "\n".join(statement for statement, _ in conn.cursor_instance.calls)
        self.assertIn("CREATE TABLE IF NOT EXISTS datasets", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS dataset_state", sql)
        self.assertIn("CREATE OR REPLACE VIEW active_graph_chunks", sql)
        self.assertIn("source_key", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS document_tables", sql)
        self.assertIn("raw_html", sql)
        self.assertIn("CREATE OR REPLACE VIEW active_document_html", sql)
        self.assertEqual(conn.commits, 1)

    def test_staging_prefixes_vector_source_keys_with_release(self) -> None:
        conn = RecordingConnection()
        stage_graph_dataset(conn, "r20260807120000-abc123", Dataset())
        content_call = next(call for call in conn.cursor_instance.calls if "UPDATE documents" in call[0])
        self.assertEqual(content_call[1][0][3], "<p>Nội dung</p>")
        chunk_call = next(call for call in conn.cursor_instance.calls if "INSERT INTO chunks" in call[0])
        values = chunk_call[1]
        self.assertEqual(values[0][3], "r20260807120000-abc123:doc-1:0001")

    def test_canonical_adapter_creates_external_nodes_and_passage_chunks(self) -> None:
        class Snapshot:
            manifest = {"schema_version": 1}
            documents = ({"document_id": "1", "metadata": {"title": "Luật"}},)
            content = ({"document_id": "1", "normalized_text": "Nội dung"},)
            categories = ({"document_id": "1", "category": "bhyt"},)
            relationships = ({
                "source_document_id": "1", "target_document_id": "outside", "relationship_type": "Căn cứ",
                "source_title_raw": "Luật", "target_title_raw": "Văn bản ngoài", "categories": ["bhyt"],
            },)
            passages = ({"passage_id": "p1", "document_id": "1", "passage_order": 1, "text": "Nội dung"},)

        dataset = canonical_snapshot_to_dataset(Snapshot())
        external = next(row for row in dataset.document_nodes if row["id"] == "outside")
        self.assertTrue(external["is_external"])
        self.assertEqual(dataset.chunks[0]["chunk_id"], "p1")
        self.assertEqual(dataset.chunks[0]["embedding_input_text"], "")


if __name__ == "__main__":
    unittest.main()
