from __future__ import annotations

import unittest

from database.corpus.enrich_with_tavily import accepted_result, is_official_url


class TavilyAcceptanceTest(unittest.TestCase):
    def test_accepts_vietnamese_government_subdomain(self) -> None:
        self.assertTrue(is_official_url("https://moh.gov.vn/van-ban/example"))
        self.assertFalse(is_official_url("https://gov.vn.example.com/not-official"))

    def test_related_document_signature_cannot_inherit_page_status(self) -> None:
        metadata = {
            "so_ky_hieu": "30/2014/TT-BYT",
            "ngay_ban_hanh": "28/08/2014",
            "co_quan_ban_hanh": "Bộ Y tế",
        }
        response = {
            "results": [{
                "title": "Thông tư số 41/2011/TT-BYT - Hết hiệu lực",
                "url": "https://congbao.chinhphu.vn/van-ban/thong-tu-so-41-2011-tt-byt.htm",
                "content": "Văn bản liên quan: 30/2014/TT-BYT của Bộ Y tế",
            }]
        }

        result, status = accepted_result(metadata, response)

        self.assertIsNotNone(result)
        self.assertEqual(status, "")

    def test_exact_official_result_title_can_supply_status(self) -> None:
        metadata = {
            "so_ky_hieu": "06/2024/NQ-HĐND",
            "ngay_ban_hanh": "15/05/2024",
            "co_quan_ban_hanh": "HĐND tỉnh Bình Thuận",
        }
        response = {
            "results": [{
                "title": "Nghị quyết 06/2024/NQ-HĐND của HĐND tỉnh Bình Thuận",
                "url": "https://vbpl.vn/van-ban/chi-tiet/nghi-quyet-06-2024-nq-hdnd",
                "content": "Còn hiệu lực. Ngày ban hành 15/05/2024.",
            }]
        }

        result, status = accepted_result(metadata, response)

        self.assertIsNotNone(result)
        self.assertEqual(status, "Còn hiệu lực")


if __name__ == "__main__":
    unittest.main()
