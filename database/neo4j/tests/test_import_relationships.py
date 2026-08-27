from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from database.neo4j.scripts.import_relationships import prepare


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class Neo4jImportPreparationTest(unittest.TestCase):
    def test_canonical_and_alias_nodes_keep_title_and_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            write_csv(
                source / "metadata.csv",
                ["id", "title", "so_ky_hieu", "agent_category"],
                [{"id": "doc-1", "title": "Quyết định A", "so_ky_hieu": "01/QĐ", "agent_category": "bhyt"}],
            )
            write_csv(
                source / "relationships.csv",
                ["doc_id", "other_doc_id", "relationship"],
                [{"doc_id": "doc-1", "other_doc_id": "ref-1", "relationship": "tham chiếu"}],
            )
            write_csv(
                source / "aliases.csv",
                ["alias_document_id", "canonical_document_id", "alias_title", "alias_signature"],
                [{
                    "alias_document_id": "old-1",
                    "canonical_document_id": "doc-1",
                    "alias_title": "Quyết định A (cũ)",
                    "alias_signature": "01-QĐ",
                }],
            )

            nodes, _, _ = prepare(source, "release-1")
            by_id = {node["id"]: node for node in nodes}

        self.assertEqual(by_id["doc-1"]["title"], "Quyết định A")
        self.assertEqual(by_id["doc-1"]["so_ky_hieu"], "01/QĐ")
        self.assertEqual(by_id["old-1"]["so_ky_hieu"], "01-QĐ")
        self.assertEqual(by_id["ref-1"]["so_ky_hieu"], "")

    def test_relationships_keep_denormalized_titles_for_parity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            write_csv(
                source / "relationships.csv",
                ["doc_id", "other_doc_id", "relationship", "source_title", "target_title"],
                [{
                    "doc_id": "doc-1", "other_doc_id": "ref-1", "relationship": "tham chiếu",
                    "source_title": "Văn bản nguồn", "target_title": "Văn bản đích",
                }],
            )
            _, grouped, _ = prepare(source, "release-1")
            edge = next(iter(grouped.values()))[0]

        self.assertEqual(edge["source_title"], "Văn bản nguồn")
        self.assertEqual(edge["target_title"], "Văn bản đích")


if __name__ == "__main__":
    unittest.main()
