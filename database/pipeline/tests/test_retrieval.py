from __future__ import annotations

import unittest

from data_pipeline.retrieval import EvidenceHit, RetrievalChannel, build_query_plan, reciprocal_rank_fusion


class RetrievalContractTest(unittest.TestCase):
    def test_query_plan_extracts_legal_hints(self) -> None:
        plan = build_query_plan("Điều 5 của 12/2024/QĐ-UBND có chi trả không?", category="bhyt")
        self.assertEqual(plan.intent, "eligibility")
        self.assertEqual(plan.document_numbers, ["12/2024/QĐ-UBND"])
        self.assertIn("Điều 5", plan.legal_labels)
        self.assertEqual(plan.channels[0], RetrievalChannel.EXACT)

    def test_rrf_keeps_channel_provenance(self) -> None:
        direct = EvidenceHit(evidence_id="a", document_id="d", channel=RetrievalChannel.SEMANTIC, score=0.8, rank=1)
        graph = EvidenceHit(evidence_id="a", document_id="d", channel=RetrievalChannel.LEGAL_GRAPH, score=0.3, rank=1)
        result = reciprocal_rank_fusion({RetrievalChannel.SEMANTIC: [direct], RetrievalChannel.LEGAL_GRAPH: [graph]})
        self.assertEqual(result[0].evidence_id, "a")
        self.assertEqual(result[0].citation["channels"], "legal_graph,semantic")
        self.assertGreater(result[0].score, 0)


if __name__ == "__main__":
    unittest.main()
