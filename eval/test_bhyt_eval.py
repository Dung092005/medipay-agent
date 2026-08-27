from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from eval.bhyt_eval import (
    build_bhyt_dataset,
    finalize_bhyt_deterministic_evaluation,
    load_eval_environment,
    score_bhyt_case,
    score_bhyt_runtime_case,
    validate_bhyt_dataset,
)
from eval.golden_eval import select_ragas_cases
from eval.golden_eval import write_actual_answers_checkpoint


def _candidate_record(
    item_id: str,
    *,
    question: str = "Khám trái tuyến được hưởng BHYT thế nào?",
    answer: str = "Theo quy định BHYT, mức hưởng phụ thuộc vào trường hợp khám chữa bệnh.",
    category: str = "Bảo hiểm y tế",
) -> dict[str, object]:
    return {
        "id": item_id,
        "source_item_id": item_id,
        "question": question,
        "official_answer": answer,
        "category": category,
        "submitted_at": "01/01/2020",
        "answered_at": "02/01/2020",
        "source_url": f"https://example.test/{item_id}",
        "legal_basis": [],
        "temporal_risk": "high",
        "review_status": "pending",
        "classification_reason": "general BHYT policy question answerable from public legal corpus",
    }


def _write_candidates(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps(
            {
                "metadata": {"requested_count": len(records)},
                "records": records,
                "statistics": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_build_bhyt_dataset_preserves_reference_without_inventing_gold(tmp_path: Path) -> None:
    candidates_path = tmp_path / "candidates.json"
    output_path = tmp_path / "dataset.jsonl"
    _write_candidates(candidates_path, [_candidate_record("1"), _candidate_record("2")])

    manifest = build_bhyt_dataset(candidates_path, output_path, expected_count=2)

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert manifest["count"] == 2
    assert len(rows) == 2
    assert rows[0]["case_origin"] == "bhyt_candidate"
    assert rows[0]["agent_input"]["messages"][0]["content"] == rows[0]["question"]
    assert rows[0]["reference"] == rows[0]["official_answer"]
    assert rows[0]["reference_status"] == "official_answer_unreviewed"
    assert rows[0]["required_facts"] == []
    assert rows[0]["reference_context_ids"] == []
    assert rows[0]["evidence_refs"][0]["source_item_id"] == "1"


def test_build_bhyt_dataset_limits_an_explicit_smoke_subset(tmp_path: Path) -> None:
    candidates_path = tmp_path / "candidates.json"
    output_path = tmp_path / "dataset.jsonl"
    _write_candidates(candidates_path, [_candidate_record("1"), _candidate_record("2")])

    manifest = build_bhyt_dataset(
        candidates_path,
        output_path,
        expected_count=1,
        candidate_limit=1,
    )

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert manifest["count"] == 1
    assert [row["case_id"] for row in rows] == ["1"]


def test_build_bhyt_dataset_rejects_wrong_category(tmp_path: Path) -> None:
    candidates_path = tmp_path / "candidates.json"
    output_path = tmp_path / "dataset.jsonl"
    _write_candidates(candidates_path, [_candidate_record("1", category="Hưu trí")])

    with pytest.raises(ValueError, match="exact BHYT category"):
        build_bhyt_dataset(candidates_path, output_path, expected_count=1)


def test_validate_bhyt_dataset_requires_exact_count_and_candidate_status(tmp_path: Path) -> None:
    candidates_path = tmp_path / "candidates.json"
    dataset_path = tmp_path / "dataset.jsonl"
    _write_candidates(candidates_path, [_candidate_record("1")])
    build_bhyt_dataset(candidates_path, dataset_path, expected_count=1)

    validation = validate_bhyt_dataset(dataset_path, expected_count=1)

    assert validation["valid"] is True
    assert validation["count"] == 1
    assert validation["gold_status"] == "candidate_unreviewed"


def test_score_bhyt_case_does_not_fake_completeness_or_id_recall() -> None:
    case = {
        "case_id": "BHXH-QA-1",
        "case_origin": "bhyt_candidate",
        "reference": "Câu trả lời chính thức chưa được review.",
        "reference_status": "official_answer_unreviewed",
        "risk": "P1",
        "agent_input": {"messages": [{"content": "Câu hỏi BHYT"}]},
    }
    actual = {
        "status": "completed",
        "answer": "Câu trả lời của model.",
        "retrieved_contexts": [{"document_id": "doc-1", "text": "Evidence"}],
    }
    ragas = {
        "metrics": {
            "factual_correctness": {"value": 0.8, "status": "OK"},
            "response_relevancy": {"value": 0.9, "status": "OK"},
            "faithfulness": {"value": 0.8, "status": "OK"},
            "context_precision": {"value": 0.7, "status": "OK"},
            "context_recall": {"value": 0.6, "status": "OK"},
        }
    }

    score = score_bhyt_case(case, actual, ragas, threshold=0.60)

    assert score["status"] == "PROVISIONAL_PASS"
    assert score["reference_status"] == "official_answer_unreviewed"
    assert score["metrics"]["completeness"] is None
    assert score["metrics"]["id_context_recall"] is None
    assert score["metrics"]["ragas_mean"] == 0.76
    assert score["legal_gold_validated"] is False


def test_score_bhyt_case_marks_low_metric_and_empty_retrieval_as_provisional_fail() -> None:
    case = {
        "case_id": "BHXH-QA-2",
        "case_origin": "bhyt_candidate",
        "reference": "Câu trả lời chính thức.",
        "reference_status": "official_answer_unreviewed",
        "risk": "P1",
        "agent_input": {"messages": [{"content": "Câu hỏi BHYT"}]},
    }
    actual = {"status": "completed", "answer": "Câu trả lời.", "retrieved_contexts": []}
    ragas = {
        "metrics": {
            name: {"value": 0.5, "status": "OK"}
            for name in (
                "factual_correctness",
                "response_relevancy",
                "faithfulness",
                "context_precision",
                "context_recall",
            )
        }
    }

    score = score_bhyt_case(case, actual, ragas, threshold=0.60)

    assert score["status"] == "PROVISIONAL_FAIL"
    assert "EMPTY_RETRIEVAL" in score["failure_categories"]
    assert "LOW_FACTUAL_CORRECTNESS" in score["failure_categories"]


def test_select_ragas_cases_is_explicit_about_case_origins() -> None:
    cases = [
        {"case_id": "source", "case_origin": "source_derived"},
        {"case_id": "candidate", "case_origin": "bhyt_candidate"},
        {"case_id": "policy", "case_origin": "synthetic_policy"},
    ]

    assert [case["case_id"] for case in select_ragas_cases(cases)] == ["source"]
    assert [case["case_id"] for case in select_ragas_cases(cases, ("bhyt_candidate",))] == [
        "candidate"
    ]


def test_score_bhyt_runtime_case_reports_observed_without_faking_quality() -> None:
    case = {"case_id": "BHXH-QA-3", "question": "Câu hỏi BHYT"}
    actual = {
        "status": "completed",
        "answer": "Câu trả lời quan sát được.",
        "retrieved_contexts": [{"document_id": "doc-1"}],
        "structured_output": {"citations": [{"chunk_id": "chunk-1"}]},
    }

    result = score_bhyt_runtime_case(case, actual)

    assert result["status"] == "OBSERVED"
    assert result["quality_status"] == "RAGAS_NOT_AVAILABLE"
    assert result["retrieved_context_count"] == 1
    assert result["citation_count"] == 1
    assert result["quality_score"] is None


def test_finalize_bhyt_deterministic_evaluation_counts_runtime_failures(tmp_path: Path) -> None:
    cases_path = tmp_path / "dataset.jsonl"
    actual_path = tmp_path / "actual.jsonl"
    cases_path.write_text(
        json.dumps({"case_id": "1", "question": "Q1"}) + "\n" + json.dumps({"case_id": "2", "question": "Q2"}) + "\n",
        encoding="utf-8",
    )
    actual_path.write_text(
        json.dumps(
            {
                "case_id": "1",
                "status": "completed",
                "answer": "A1",
                "retrieved_contexts": [{"document_id": "doc-1"}],
                "structured_output": {"citations": []},
            }
        )
        + "\n"
        + json.dumps(
            {
                "case_id": "2",
                "status": "agent_error",
                "answer": "",
                "retrieved_contexts": [],
                "structured_output": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = finalize_bhyt_deterministic_evaluation(cases_path, actual_path, tmp_path)

    assert summary["total"] == 2
    assert summary["observed"] == 1
    assert summary["runtime_failures"] == 1
    assert summary["quality_status"] == "RAGAS_NOT_AVAILABLE"
    assert (tmp_path / "deterministic_case_scores.jsonl").is_file()
    assert (tmp_path / "deterministic_summary.json").is_file()


def test_write_actual_answers_checkpoint_writes_jsonl_records(tmp_path: Path) -> None:
    output = tmp_path / "actual_answers.jsonl"

    write_actual_answers_checkpoint(
        output,
        [
            {"case_id": "1", "status": "completed"},
            {"case_id": "2", "status": "agent_error"},
        ],
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["case_id"] for row in rows] == ["1", "2"]


def test_load_eval_environment_reads_model_name_without_printing_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MODEL_NAME=test-model\nOPENAI_API_KEY=secret-value\n", encoding="utf-8")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    load_eval_environment(env_file)

    assert os.environ["MODEL_NAME"] == "test-model"
    assert os.environ["OPENAI_API_KEY"] == "secret-value"
