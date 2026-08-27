from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from database.corpus.build_active_corpus import (
    apply_metadata_correction,
    atomic_write_csv,
    compact_identity,
    extract_full_content_fragments,
    filter_reasons,
    resolve_alias,
)


class CorpusBuilderTest(unittest.TestCase):
    def test_filter_matches_the_requested_sql_semantics(self) -> None:
        self.assertIn(
            "title_thanh_toan_kcb",
            filter_reasons({"title": "Quy trình thanh toán chi phí KCB"}),
        )
        self.assertEqual(
            filter_reasons({"title": "Quản lý bệnh viện", "linh_vuc": "Khác"}),
            [],
        )

    def test_extracts_balanced_full_content_div(self) -> None:
        page = '<main><div id="full-content"><div>A</div><p>B</p></div><div>ads</div></main>'
        self.assertEqual(
            extract_full_content_fragments(page),
            ['<div id="full-content"><div>A</div><p>B</p></div>'],
        )

    def test_alias_resolution_rejects_cycles(self) -> None:
        self.assertEqual(resolve_alias("old", {"old": "canonical"}), "canonical")
        with self.assertRaises(ValueError):
            resolve_alias("a", {"a": "b", "b": "a"})

    def test_identity_compaction_ignores_signature_punctuation(self) -> None:
        self.assertEqual(compact_identity("27/2020/QĐ-UBND"), compact_identity("27 2020 QĐ UBND"))

    def test_html_field_is_serialized_without_whitespace_rewrite(self) -> None:
        raw_html = '<div class="law">\n  Điều 1&nbsp;  Nội dung\n</div>'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "content.csv"
            atomic_write_csv(
                path,
                [{"id": "1", "content_html": raw_html}],
                ("id", "content_html"),
                preserve_fields=frozenset({"content_html"}),
            )
            with path.open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
        self.assertEqual(row["content_html"], raw_html)

    def test_reviewed_metadata_correction_updates_signature_and_title(self) -> None:
        metadata = {
            "so_ky_hieu": "113/2022/NQ-UBND",
            "title": "Nghị quyết 113/2022/NQ-UBND về giá dịch vụ",
        }

        audit = apply_metadata_correction("153839", metadata)

        self.assertEqual(metadata["so_ky_hieu"], "113/2022/NQ-HĐND")
        self.assertEqual(metadata["title"], "Nghị quyết 113/2022/NQ-HĐND về giá dịch vụ")
        self.assertEqual(audit["old_signature"], "113/2022/NQ-UBND")

    def test_reviewed_metadata_correction_rejects_changed_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "source changed"):
            apply_metadata_correction(
                "153839",
                {"so_ky_hieu": "already-fixed", "title": "Nghị quyết"},
            )

    def test_reviewed_metadata_correction_rejects_changed_title(self) -> None:
        with self.assertRaisesRegex(ValueError, "title changed"):
            apply_metadata_correction(
                "153839",
                {"so_ky_hieu": "113/2022/NQ-UBND", "title": "Nghị quyết không còn số"},
            )


if __name__ == "__main__":
    unittest.main()
