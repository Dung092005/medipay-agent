from __future__ import annotations

import unittest

from data_pipeline.facets import build_facets


class FacetTest(unittest.TestCase):
    def test_facets_are_overlapping_and_deterministic(self) -> None:
        snapshot = type("Snapshot", (), {
            "dataset_id": "r-1",
            "documents": ({"document_id": "d-1", "metadata": {
                "agent_category": "bhyt,vien_phi", "ngay_ban_hanh": "01/02/2024",
                "tinh_trang_hieu_luc": "Còn hiệu lực", "pham_vi": "Địa phương",
            }},),
        })()
        first = build_facets(snapshot)
        second = build_facets(snapshot)
        self.assertEqual(first, second)
        self.assertGreaterEqual(sum(row["facet_name"] == "category" for row in first), 2)
        self.assertIn({"facet_name": "issued_year", "facet_value": "2024"}, [
            {"facet_name": row["facet_name"], "facet_value": row["facet_value"]} for row in first
        ])


if __name__ == "__main__":
    unittest.main()
