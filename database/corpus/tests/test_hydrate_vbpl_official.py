from __future__ import annotations

import unittest

from database.corpus.hydrate_vbpl_official import parse_legislation_jsonld, signature_variants


class OfficialVbplJsonLdTest(unittest.TestCase):
    def test_extracts_legislation_block(self) -> None:
        page = """
        <script type="application/ld+json">{"@type":"Organization","name":"VBPL"}</script>
        <script type="application/ld+json">
          {"@type":"Legislation","legislationIdentifier":"20/TTLB",
           "legislationLegalForce":"NotInForce","url":"https://vbpl.vn/x--10018"}
        </script>
        """
        result = parse_legislation_jsonld(page)
        self.assertEqual(result["legislationIdentifier"], "20/TTLB")
        self.assertEqual(result["legislationLegalForce"], "NotInForce")

    def test_ignores_invalid_and_unrelated_jsonld(self) -> None:
        page = """
        <script type="application/ld+json">not-json</script>
        <script type="application/ld+json">{"@type":"WebSite"}</script>
        """
        self.assertIsNone(parse_legislation_jsonld(page))

    def test_signature_variants_normalize_reviewed_formatting_aliases(self) -> None:
        self.assertTrue(
            signature_variants("Thông tư 28/2023/TT-BYT của Bộ Y tế")
            & signature_variants("28/2023/TT-BYT")
        )
        self.assertTrue(
            signature_variants("22/2003/TTLT-BQP-BLÐTBXH-BYT-BTC")
            & signature_variants("22/2003/TTLT-BQP-BLĐTBXH-BYT-BTC")
        )


if __name__ == "__main__":
    unittest.main()
