from __future__ import annotations

import unittest

import numpy as np
from scripts.reuse_embedding_backup import input_sha256, input_text, parse_vector, reusable_vectors


class ReuseEmbeddingBackupTests(unittest.TestCase):
    def test_input_digest_matches_canonical_embedding_text(self) -> None:
        text = input_text({"section_label": "Điều 1", "text": "Nội dung"})
        self.assertEqual(text, "Điều 1\n\nNội dung")
        self.assertEqual(len(input_sha256(text)), 64)

    def test_only_embedded_semantic_rows_are_reusable(self) -> None:
        vector = "[" + ",".join(["0.25"] * 1536) + "]"
        backup = {"tables": {"chunks": [
            {"chunk_id": "kept", "semantic_eligible": True,
             "embedded_input_sha256": "a" * 64, "embedding": vector},
            {"chunk_id": "lexical", "semantic_eligible": False,
             "embedded_input_sha256": "b" * 64, "embedding": vector},
        ]}}
        result = reusable_vectors(backup)
        self.assertEqual(set(result), {("kept", "a" * 64)})
        self.assertEqual(result[("kept", "a" * 64)].dtype, np.float32)

    def test_invalid_vector_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_vector("[1,2]")


if __name__ == "__main__":
    unittest.main()
