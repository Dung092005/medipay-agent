from __future__ import annotations

import unittest

from data_pipeline.page_index import PAGE_INDEX_VERSION, build_page_index, unit_for_offset


class PageIndexTest(unittest.TestCase):
    def test_hierarchy_and_offsets_are_stable(self) -> None:
        text = """Chương I\n\nQUY ĐỊNH CHUNG\n\nĐiều 1. Phạm vi\n\n1. Nội dung thứ nhất\n\na) Điểm a\n\nb) Điểm b\n\n2. Nội dung thứ hai\n\nĐiều 2. Hiệu lực"""
        units = build_page_index("doc-1", text, raw_html_sha256="raw-hash")
        types = [unit["unit_type"] for unit in units]
        self.assertEqual(types, ["document", "chapter", "article", "clause", "point", "point", "clause", "article"])
        article = next(unit for unit in units if unit["label"].startswith("Điều 1"))
        clause = next(unit for unit in units if unit["label"].startswith("1."))
        point = next(unit for unit in units if unit["label"].startswith("a)"))
        self.assertEqual(article["parent_unit_id"], units[1]["unit_id"])
        self.assertEqual(clause["parent_unit_id"], article["unit_id"])
        self.assertEqual(point["parent_unit_id"], clause["unit_id"])
        self.assertEqual(point["text"], "a) Điểm a")
        self.assertEqual(point["parser_version"], PAGE_INDEX_VERSION)
        self.assertEqual(unit_for_offset(units, text.index("Điểm a")), point["unit_id"])

    def test_empty_content_has_no_synthetic_units(self) -> None:
        self.assertEqual(build_page_index("doc-1", ""), [])


if __name__ == "__main__":
    unittest.main()
