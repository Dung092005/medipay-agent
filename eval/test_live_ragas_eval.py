from __future__ import annotations

import csv
import json
from pathlib import Path

from eval.golden_eval import (
    NO_EVIDENCE_RESPONSE,
    build_golden_dataset,
    merge_case_scores,
    score_deterministic,
    validate_golden_dataset,
)


METADATA_FIELDS = [
    "id",
    "title",
    "so_ky_hieu",
    "ngay_ban_hanh",
    "loai_van_ban",
    "ngay_co_hieu_luc",
    "ngay_het_hieu_luc",
    "nguon_thu_thap",
    "ngay_dang_cong_bao",
    "nganh",
    "linh_vuc",
    "co_quan_ban_hanh",
    "chuc_danh",
    "nguoi_ky",
    "pham_vi",
    "thong_tin_ap_dung",
    "tinh_trang_hieu_luc",
    "agent_category",
    "status_checked_at",
    "status_filter",
]


def _write_sources(source_dir: Path) -> None:
    rows = {
        "metadata_bhyt.csv": {
            "id": "DOC-BHYT-REAL-1",
            "title": "Thông tư số 01/2026/TT-SYN quy định quyền lợi bảo hiểm y tế",
            "so_ky_hieu": "01/2026/TT-SYN",
            "ngay_ban_hanh": "01/01/2026",
            "loai_van_ban": "Thông tư",
            "ngay_co_hieu_luc": "15/01/2026",
            "ngay_het_hieu_luc": "",
            "tinh_trang_hieu_luc": "Còn hiệu lực",
            "agent_category": "bhyt",
            "status_checked_at": "2026-08-04",
            "status_filter": "Còn hiệu lực",
        },
        "metadata_vien_phi.csv": {
            "id": "DOC-VP-REAL-1",
            "title": "Nghị quyết số 02/2026/NQ-SYN quy định giá dịch vụ viện phí",
            "so_ky_hieu": "02/2026/NQ-SYN",
            "ngay_ban_hanh": "02/02/2026",
            "loai_van_ban": "Nghị quyết",
            "ngay_co_hieu_luc": "20/02/2026",
            "ngay_het_hieu_luc": "",
            "tinh_trang_hieu_luc": "Còn hiệu lực",
            "agent_category": "vien_phi",
            "status_checked_at": "2026-08-04",
            "status_filter": "Còn hiệu lực",
        },
    }
    for filename, row in rows.items():
        with (source_dir / filename).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=METADATA_FIELDS)
            writer.writeheader()
            writer.writerow(row)
    with (source_dir / "content.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "agent_category", "content_html"])
        writer.writeheader()
        writer.writerow(
            {
                "id": "DOC-BHYT-REAL-1",
                "agent_category": "bhyt",
                "content_html": "<p>Thông tư 01 có hiệu lực từ ngày 15/01/2026.</p>",
            }
        )
        writer.writerow(
            {
                "id": "DOC-VP-REAL-1",
                "agent_category": "vien_phi",
                "content_html": "<p>Nghị quyết 02 quy định giá dịch vụ viện phí và có hiệu lực ngày 20/02/2026.</p>",
            }
        )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_build_golden_dataset_joins_real_content_and_balances_sources(tmp_path: Path) -> None:
    _write_sources(tmp_path)
    output = tmp_path / "golden_dataset.jsonl"

    manifest = build_golden_dataset(tmp_path, output, source_case_count=6)
    cases = _read_jsonl(output)
    source_cases = [case for case in cases if case["case_origin"] == "source_derived"]

    assert manifest["source_case_count"] == 6
    assert manifest["policy_case_count"] == 6
    assert len(cases) == 12
    assert {case["source_file"] for case in source_cases} == {
        "metadata_bhyt.csv",
        "metadata_vien_phi.csv",
    }
    assert all(case["reference"] for case in source_cases)
    assert all(case["reference_contexts"] for case in source_cases)
    assert all("<p>" not in case["reference_contexts"][0] for case in source_cases)
    assert all(case["reference_context_ids"] == [case["evidence_refs"][0]["document_id"]] for case in source_cases)
    assert all(case["evidence_refs"][0]["document_id"] not in case["agent_input"]["messages"][0]["content"] for case in source_cases)


def test_validate_golden_dataset_checks_source_hashes_and_content_refs(tmp_path: Path) -> None:
    _write_sources(tmp_path)
    output = tmp_path / "golden_dataset.jsonl"
    build_golden_dataset(tmp_path, output, source_case_count=6)

    result = validate_golden_dataset(output, tmp_path)

    assert result["valid"] is True
    assert result["count"] == 12
    assert result["source_case_count"] == 6
    assert result["gold_completeness"] == 1.0
    assert result["errors"] == []


def test_completed_fallback_is_a_real_failure_not_a_pass() -> None:
    case = {
        "case_id": "DOC-1-TITLE",
        "case_origin": "source_derived",
        "category": "document_lookup",
        "risk": "P2",
        "required_facts": [{"name": "title", "value": "Thông tư số 01/2026/TT-SYN"}],
        "reference_context_ids": ["DOC-1"],
        "forbidden_claims": [],
    }
    actual = {
        "status": "completed",
        "answer": NO_EVIDENCE_RESPONSE,
        "trace_id": "trace-1",
        "retrieved_contexts": [{"document_id": "WRONG-DOC"}],
    }

    score = score_deterministic(case, actual)

    assert score["status"] == "FAIL"
    assert score["metrics"]["completeness"] == 0.0
    assert score["metrics"]["id_context_recall"] == 0.0
    assert "FALLBACK_ANSWER" in score["failure_categories"]
    assert "RETRIEVAL_MISS" in score["failure_categories"]
    assert score["missing_facts"] == ["title"]


def test_deterministic_score_accepts_human_readable_date_variant() -> None:
    case = {
        "case_id": "DATE-1",
        "case_origin": "source_derived",
        "category": "policy_date",
        "risk": "P1",
        "required_facts": [
            {"name": "effective_date", "value": "15/01/2026"},
            {"name": "status", "value": "Còn hiệu lực"},
        ],
        "reference_context_ids": ["DOC-1"],
        "forbidden_claims": [],
    }
    actual = {
        "status": "completed",
        "answer": "Văn bản có hiệu lực từ ngày 15 tháng 1 năm 2026 và hiện còn hiệu lực.",
        "trace_id": "trace-1",
        "retrieved_contexts": [{"document_id": "DOC-1"}],
    }

    score = score_deterministic(case, actual)

    assert score["status"] == "PASS"
    assert score["metrics"]["completeness"] == 1.0
    assert score["metrics"]["id_context_recall"] == 1.0
    assert score["missing_facts"] == []


def test_deterministic_score_accepts_title_with_nonessential_prefix_omitted() -> None:
    case = {
        "case_id": "DOC-1",
        "case_origin": "source_derived",
        "category": "document_lookup",
        "risk": "P1",
        "required_facts": [
            {
                "name": "title",
                "value": "Quyết định số 31/2015/QĐ-UBND Về việc bổ sung Điều 6 Quyết định số 25/2015/QĐ-UBND ngày 12 tháng 5 năm 2015",
            }
        ],
        "reference_context_ids": ["100276"],
        "forbidden_claims": [],
    }
    actual = {
        "status": "completed",
        "answer": "Tên đầy đủ là Quyết định về việc bổ sung Điều 6 Quyết định số 25/2015/QĐ-UBND ngày 12 tháng 5 năm 2015.",
        "trace_id": "trace-1",
        "retrieved_contexts": [{"document_id": "100276"}],
    }

    score = score_deterministic(case, actual)

    assert score["metrics"]["completeness"] == 1.0
    assert score["missing_facts"] == []


def test_policy_fallback_fails_required_behavior() -> None:
    case = {
        "case_id": "SAFETY-001",
        "case_origin": "synthetic_policy",
        "category": "medical_safety",
        "risk": "P0",
        "required_facts": [
            {"name": "required_behavior", "value": "refuse_medical_diagnosis"}
        ],
        "reference_context_ids": [],
        "forbidden_claims": ["tôi chẩn đoán"],
    }
    actual = {
        "status": "completed",
        "answer": NO_EVIDENCE_RESPONSE,
        "trace_id": "trace-safety",
        "retrieved_contexts": [],
    }

    score = score_deterministic(case, actual)

    assert score["status"] == "FAIL"
    assert score["metrics"]["completeness"] == 0.0
    assert "POLICY_BEHAVIOR_MISSING" in score["failure_categories"]
    assert "FALLBACK_ANSWER" in score["failure_categories"]


def test_merge_case_scores_applies_real_metric_floor() -> None:
    case = {
        "case_id": "DOC-1",
        "case_origin": "source_derived",
        "category": "document_lookup",
        "risk": "P1",
        "agent_input": {"messages": [{"role": "user", "content": "Tên văn bản?"}]},
        "reference": "Tên văn bản là Thông tư 01.",
    }
    actual = {"answer": "Tên văn bản là Thông tư 02."}
    deterministic = {
        "case_id": "DOC-1",
        "status": "PASS",
        "severity": "P1",
        "failure_categories": [],
        "missing_facts": [],
        "forbidden_claims_triggered": [],
        "fallback": False,
        "target_document_rank": 1,
        "metrics": {
            "completeness": 1.0,
            "id_context_precision": 1.0,
            "id_context_recall": 1.0,
        },
    }
    ragas = {
        "case_id": "DOC-1",
        "metrics": {
            "factual_correctness": {"value": 0.40, "status": "OK"},
            "response_relevancy": {"value": 0.95, "status": "OK"},
            "faithfulness": {"value": 0.90, "status": "OK"},
            "context_precision": {"value": 1.0, "status": "OK"},
            "context_recall": {"value": 1.0, "status": "OK"},
        },
    }

    merged = merge_case_scores(case, actual, deterministic, ragas, threshold=0.60)

    assert merged["status"] == "FAIL"
    assert merged["metrics"]["quality_score"] > 0.60
    assert "LOW_FACTUAL_CORRECTNESS" in merged["failure_categories"]
    assert "0.400" in merged["why_failed"]


def test_merge_case_scores_never_passes_missing_ragas_metric() -> None:
    case = {
        "case_id": "DOC-2",
        "case_origin": "source_derived",
        "category": "document_lookup",
        "risk": "P1",
        "agent_input": {"messages": [{"role": "user", "content": "Tên văn bản?"}]},
        "reference": "Tên văn bản là Thông tư 01.",
    }
    actual = {"answer": "Tên văn bản là Thông tư 01."}
    deterministic = {
        "case_id": "DOC-2",
        "status": "PASS",
        "severity": "P1",
        "failure_categories": [],
        "missing_facts": [],
        "forbidden_claims_triggered": [],
        "fallback": False,
        "target_document_rank": 1,
        "metrics": {
            "completeness": 1.0,
            "id_context_precision": 1.0,
            "id_context_recall": 1.0,
        },
    }
    ragas = {
        "case_id": "DOC-2",
        "metrics": {
            "factual_correctness": {"value": 1.0, "status": "OK"},
            "response_relevancy": {"value": None, "status": "NOT_OBSERVABLE"},
            "faithfulness": {"value": 1.0, "status": "OK"},
            "context_precision": {"value": 1.0, "status": "OK"},
            "context_recall": {"value": 1.0, "status": "OK"},
        },
    }

    merged = merge_case_scores(case, actual, deterministic, ragas, threshold=0.60)

    assert merged["status"] == "NOT_OBSERVABLE"
    assert merged["metrics"]["quality_score"] is None
    assert "RAGAS_METRIC_NOT_OBSERVABLE" in merged["failure_categories"]


def test_merge_case_scores_fails_when_context_precision_is_below_floor() -> None:
    case = {
        "case_id": "DOC-NOISY",
        "case_origin": "source_derived",
        "category": "document_lookup",
        "risk": "P1",
        "agent_input": {"messages": [{"role": "user", "content": "Tên văn bản?"}]},
        "reference": "Tên văn bản là Thông tư 01.",
    }
    actual = {"answer": "Tên văn bản là Thông tư 01."}
    deterministic = {
        "case_id": "DOC-NOISY",
        "status": "PASS",
        "severity": "P1",
        "failure_categories": [],
        "missing_facts": [],
        "forbidden_claims_triggered": [],
        "fallback": False,
        "target_document_rank": 8,
        "metrics": {
            "completeness": 1.0,
            "id_context_precision": 0.18,
            "id_context_recall": 1.0,
        },
    }
    ragas = {
        "case_id": "DOC-NOISY",
        "metrics": {
            "factual_correctness": {"value": 0.86, "status": "OK"},
            "response_relevancy": {"value": 0.84, "status": "OK"},
            "faithfulness": {"value": 1.0, "status": "OK"},
            "context_precision": {"value": 0.16, "status": "OK"},
            "context_recall": {"value": 1.0, "status": "OK"},
        },
    }

    merged = merge_case_scores(case, actual, deterministic, ragas, threshold=0.60)

    assert merged["metrics"]["quality_score"] > 0.60
    assert merged["status"] == "FAIL"
    assert "LOW_CONTEXT_PRECISION" in merged["failure_categories"]
