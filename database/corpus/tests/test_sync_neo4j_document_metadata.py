from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from database.corpus.sync_neo4j_document_metadata import expected_nodes


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class Neo4jMetadataSyncTest(unittest.TestCase):
    def test_expected_nodes_include_canonical_and_alias_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            write_csv(
                source / "metadata.csv",
                ["id", "title", "so_ky_hieu"],
                [{"id": "1", "title": "Văn bản A", "so_ky_hieu": "01/QĐ"}],
            )
            write_csv(
                source / "aliases.csv",
                ["alias_document_id", "alias_title", "alias_signature"],
                [{"alias_document_id": "old-1", "alias_title": "Văn bản A cũ", "alias_signature": "01-QĐ"}],
            )
            rows = expected_nodes(source)

        self.assertEqual(
            rows,
            [
                {"id": "1", "title": "Văn bản A", "so_ky_hieu": "01/QĐ", "node_kind": "canonical_document"},
                {"id": "old-1", "title": "Văn bản A cũ", "so_ky_hieu": "01-QĐ", "node_kind": "document_alias"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
