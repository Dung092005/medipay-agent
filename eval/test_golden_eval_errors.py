from __future__ import annotations

import json
from pathlib import Path

from eval.golden_eval import build_dataset, evaluate_answers


def test_agent_error_is_a_quality_failure_not_a_pass(tmp_path: Path) -> None:
    header = "id,title,so_ky_hieu,ngay_co_hieu_luc,ngay_het_hieu_luc,tinh_trang_hieu_luc,status_filter,agent_category\n"
    (tmp_path / "metadata_bhyt.csv").write_text(header + "DOC-1,Policy,01/2026,2026-01-01,,,active,bhyt\n", encoding="utf-8")
    (tmp_path / "metadata_vien_phi.csv").write_text(header + "DOC-2,Billing,02/2026,2026-02-01,,,active,vien_phi\n", encoding="utf-8")
    dataset = tmp_path / "draft_gold.jsonl"
    build_dataset(tmp_path, dataset, count=1)
    actual = tmp_path / "actual_answers.jsonl"
    actual.write_text(
        json.dumps(
            {
                "run_id": "run-test",
                "case_id": json.loads(dataset.read_text(encoding="utf-8"))["case_id"],
                "status": "agent_error",
                "answer": "",
                "error": "GraphRagUnavailableError: GraphRAG retrieval failed",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = evaluate_answers(dataset, actual, tmp_path)
    score = json.loads((tmp_path / "case_scores.jsonl").read_text(encoding="utf-8").splitlines()[0])

    assert summary == {"total": 1, "passed": 0, "failed": 1, "not_observable": 0, "status": "BLOCKED"}
    assert score["status"] == "FAIL"
    assert score["failure_categories"] == ["AGENT_RUNTIME_ERROR"]
