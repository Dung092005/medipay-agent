from __future__ import annotations

import json
from pathlib import Path

from eval.golden_eval import (
    build_dataset,
    evaluate_answers,
    generate_actual_answers,
    validate_dataset,
)


def _metadata_source(source_dir: Path) -> None:
    source_dir.joinpath("metadata_bhyt.csv").write_text(
        "id,title,so_ky_hieu,ngay_co_hieu_luc,ngay_het_hieu_luc,tinh_trang_hieu_luc,status_filter,agent_category\n"
        "BHYT-1,Quy định BHYT,01/2026,2026-01-01,,Còn hiệu lực,active,bhyt\n",
        encoding="utf-8",
    )
    source_dir.joinpath("metadata_vien_phi.csv").write_text(
        "id,title,so_ky_hieu,ngay_co_hieu_luc,ngay_het_hieu_luc,tinh_trang_hieu_luc,status_filter,agent_category\n"
        "VP-1,Quy định viện phí,02/2026,2026-02-01,,Còn hiệu lực,active,vien_phi\n",
        encoding="utf-8",
    )


def test_validate_dataset_rejects_gold_leak(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(
        json.dumps(
            {
                "case_id": "BAD-001",
                "draft_gold": True,
                "agent_input": {"gold_facts": {"answer": "leaked"}, "messages": []},
                "gold_facts": {"answer": "leaked"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = validate_dataset(path)

    assert result["valid"] is False
    assert any("gold leakage" in error for error in result["errors"])


def test_missing_isolated_runtime_produces_complete_not_observable_denominator(tmp_path: Path) -> None:
    _metadata_source(tmp_path)
    dataset = tmp_path / "draft_gold.jsonl"
    build_dataset(tmp_path, dataset, count=6)
    actual = tmp_path / "actual_answers.jsonl"

    result = generate_actual_answers(dataset, actual, "run-test")
    records = [json.loads(line) for line in actual.read_text(encoding="utf-8").splitlines()]

    assert result == {"total": 6, "completed": 0, "not_observable": 6, "agent_errors": 0}
    assert len(records) == 6
    assert {record["status"] for record in records} == {"not_observable"}


def test_evaluate_answers_writes_case_scores_for_unobservable_cases(tmp_path: Path) -> None:
    _metadata_source(tmp_path)
    dataset = tmp_path / "draft_gold.jsonl"
    build_dataset(tmp_path, dataset, count=6)
    actual = tmp_path / "actual_answers.jsonl"
    generate_actual_answers(dataset, actual, "run-test")

    summary = evaluate_answers(dataset, actual, tmp_path)

    assert summary == {"total": 6, "passed": 0, "failed": 0, "not_observable": 6, "status": "BLOCKED"}
    scores = [json.loads(line) for line in (tmp_path / "case_scores.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(scores) == 6
    assert all(score["status"] == "NOT_OBSERVABLE" for score in scores)
