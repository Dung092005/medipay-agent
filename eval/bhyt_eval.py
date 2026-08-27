from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.golden_eval import (  # noqa: E402
    _canonical_json,
    _load_jsonl,
    generate_actual_answers,
    is_generic_fallback,
    score_ragas_answers,
)

BHYT_CATEGORY = "Bảo hiểm y tế"
BHYT_CATEGORY_NORMALIZED = BHYT_CATEGORY.casefold()
RAGAS_METRICS = (
    "factual_correctness",
    "response_relevancy",
    "faithfulness",
    "context_precision",
    "context_recall",
)


def load_eval_environment(env_file: Path = PROJECT_ROOT / ".env") -> None:
    """Load local configuration without logging any secret values."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_file, override=False)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_candidate_envelope(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Cannot read candidate JSON: {exc}") from exc
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError("Candidate JSON must contain a records list")
    return [record for record in records if isinstance(record, dict)]


def _candidate_case(record: dict[str, Any], source_path: Path) -> dict[str, Any]:
    source_id = str(record.get("source_item_id") or record.get("id") or "").strip()
    question = str(record.get("question") or "").strip()
    official_answer = str(record.get("official_answer") or "").strip()
    temporal_risk = str(record.get("temporal_risk") or "medium").strip().casefold()
    risk = {"low": "P2", "medium": "P1", "high": "P1"}.get(temporal_risk, "P1")
    return {
        "case_id": str(record.get("id") or source_id).strip(),
        "case_origin": "bhyt_candidate",
        "category": BHYT_CATEGORY,
        "risk": risk,
        "draft_gold": True,
        "gold_status": "candidate_unreviewed",
        "reference_status": "official_answer_unreviewed",
        "legal_gold_validated": False,
        "agent_input": {
            "messages": [{"role": "user", "content": question}],
            "runtime_context": {},
        },
        "question": question,
        "official_answer": official_answer,
        "reference": official_answer,
        "required_facts": [],
        "reference_contexts": [],
        "reference_context_ids": [],
        "evidence_refs": [
            {
                "source_item_id": source_id,
                "source_url": str(record.get("source_url") or "").strip(),
            }
        ],
        "source_file": source_path.name,
        "source_hashes": {source_path.name: _sha256(source_path)},
        "candidate_metadata": {
            "submitted_at": record.get("submitted_at"),
            "answered_at": record.get("answered_at"),
            "legal_basis": record.get("legal_basis", []),
            "temporal_risk": record.get("temporal_risk"),
            "classification_reason": record.get("classification_reason", ""),
        },
        "forbidden_claims": [],
    }


def build_bhyt_dataset(
    candidates_path: Path,
    output_path: Path,
    expected_count: int = 200,
    *,
    candidate_limit: int | None = None,
) -> dict[str, Any]:
    records = _read_candidate_envelope(candidates_path)
    if candidate_limit is not None:
        if candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive")
        records = records[:candidate_limit]
    if len(records) != expected_count:
        raise ValueError(f"Expected exactly {expected_count} candidates, got {len(records)}")

    seen_ids: set[str] = set()
    cases: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        category = str(record.get("category") or "").strip().casefold()
        if category != BHYT_CATEGORY_NORMALIZED:
            raise ValueError(f"line {index}: record does not have the exact BHYT category")
        case_id = str(record.get("id") or record.get("source_item_id") or "").strip()
        if not case_id:
            raise ValueError(f"line {index}: missing candidate id")
        if case_id in seen_ids:
            raise ValueError(f"line {index}: duplicate candidate id {case_id}")
        seen_ids.add(case_id)
        if not str(record.get("question") or "").strip():
            raise ValueError(f"line {index}: missing question")
        if not str(record.get("official_answer") or "").strip():
            raise ValueError(f"line {index}: missing official answer")
        cases.append(_candidate_case(record, candidates_path))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(_canonical_json(case) + "\n")
    return {
        "count": len(cases),
        "dataset_sha256": _sha256(output_path),
        "candidate_sha256": _sha256(candidates_path),
        "source": str(candidates_path),
        "gold_status": "candidate_unreviewed",
    }


def validate_bhyt_dataset(dataset_path: Path, expected_count: int = 200) -> dict[str, Any]:
    errors: list[str] = []
    try:
        cases = _load_jsonl(dataset_path)
    except Exception as exc:
        return {"valid": False, "count": 0, "errors": [f"cannot_read_dataset: {exc}"]}

    seen: set[str] = set()
    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("case_id") or "").strip()
        if not case_id or case_id in seen:
            errors.append(f"line {index}: missing or duplicate case_id")
        seen.add(case_id)
        if str(case.get("case_origin")) != "bhyt_candidate":
            errors.append(f"line {index}: wrong case_origin")
        if str(case.get("category") or "").strip().casefold() != BHYT_CATEGORY_NORMALIZED:
            errors.append(f"line {index}: wrong category")
        messages = case.get("agent_input", {}).get("messages", [])
        question = messages[0].get("content", "") if messages and isinstance(messages[0], dict) else ""
        if not str(question).strip():
            errors.append(f"line {index}: empty question")
        if not str(case.get("reference") or "").strip():
            errors.append(f"line {index}: empty reference")
        if case.get("gold_status") != "candidate_unreviewed":
            errors.append(f"line {index}: wrong gold_status")
        if case.get("reference_status") != "official_answer_unreviewed":
            errors.append(f"line {index}: wrong reference_status")
        if case.get("legal_gold_validated") is not False:
            errors.append(f"line {index}: candidate must not be marked legally validated")
        if case.get("required_facts") != []:
            errors.append(f"line {index}: required_facts must remain empty before legal annotation")
        if case.get("reference_context_ids") != []:
            errors.append(f"line {index}: reference_context_ids must remain empty before legal annotation")

    if len(cases) != expected_count:
        errors.append(f"expected {expected_count} cases, got {len(cases)}")
    return {
        "valid": not errors and len(cases) == expected_count,
        "count": len(cases),
        "expected_count": expected_count,
        "gold_status": "candidate_unreviewed",
        "errors": errors,
        "dataset_sha256": _sha256(dataset_path) if dataset_path.exists() else None,
    }


def _metric_value(result: Any) -> float | None:
    if not isinstance(result, dict):
        return None
    value = result.get("value")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def score_bhyt_case(
    case: dict[str, Any],
    actual: dict[str, Any],
    ragas: dict[str, Any],
    *,
    threshold: float = 0.60,
) -> dict[str, Any]:
    categories: list[str] = []
    runtime_status = str(actual.get("status") or "invalid_output")
    answer = str(actual.get("answer") or "").strip()
    if runtime_status != "completed":
        categories.append(
            {
                "agent_error": "AGENT_RUNTIME_ERROR",
                "not_observable": "OBSERVABILITY_GAP",
                "invalid_output": "INVALID_OUTPUT",
            }.get(runtime_status, "AGENT_RUNTIME_ERROR")
        )
    if not answer:
        categories.append("EMPTY_ANSWER")
    if is_generic_fallback(answer):
        categories.append("FALLBACK_ANSWER")

    retrieved = actual.get("retrieved_contexts") or []
    if not retrieved:
        categories.append("EMPTY_RETRIEVAL")

    ragas_metrics = ragas.get("metrics", {}) if isinstance(ragas, dict) else {}
    values: dict[str, float | None] = {
        name: _metric_value(ragas_metrics.get(name)) for name in RAGAS_METRICS
    }
    unavailable = [name for name, value in values.items() if value is None]
    if unavailable:
        categories.append("RAGAS_METRIC_NOT_OBSERVABLE")
    for name, value in values.items():
        if value is not None and value < threshold:
            categories.append(f"LOW_{name.upper()}")

    ragas_mean = round(mean(values.values()), 6) if not unavailable else None
    if runtime_status != "completed" or unavailable:
        status = "NOT_OBSERVABLE"
    else:
        status = "PROVISIONAL_PASS" if not categories else "PROVISIONAL_FAIL"

    return {
        "case_id": case.get("case_id"),
        "case_origin": "bhyt_candidate",
        "status": status,
        "reference_status": case.get("reference_status", "official_answer_unreviewed"),
        "legal_gold_validated": False,
        "question": case.get("question")
        or case.get("agent_input", {}).get("messages", [{}])[0].get("content", ""),
        "answer": answer,
        "runtime_status": runtime_status,
        "failure_categories": list(dict.fromkeys(categories)),
        "retrieved_document_ids": [
            str(item.get("document_id"))
            for item in retrieved
            if isinstance(item, dict) and item.get("document_id")
        ],
        "citation_count": len(actual.get("structured_output", {}).get("citations", []) or []),
        "metrics": {
            **values,
            "ragas_mean": ragas_mean,
            "completeness": None,
            "id_context_recall": None,
        },
        "why_failed": "; ".join(list(dict.fromkeys(categories))),
        "official_answer_is_not_final_gold": True,
    }


def score_bhyt_runtime_case(case: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    """Score only observable runtime behavior when Ragas is unavailable.

    This deliberately reports observations and never converts them into answer-quality
    or legal-correctness points.
    """
    categories: list[str] = []
    runtime_status = str(actual.get("status") or "invalid_output")
    answer = str(actual.get("answer") or "").strip()
    if runtime_status != "completed":
        categories.append("AGENT_RUNTIME_ERROR")
    if not answer:
        categories.append("EMPTY_ANSWER")
    if answer and is_generic_fallback(answer):
        categories.append("FALLBACK_ANSWER")
    retrieved = actual.get("retrieved_contexts") or []
    if not retrieved:
        categories.append("EMPTY_RETRIEVAL")
    citations = actual.get("structured_output", {}).get("citations", []) or []
    if runtime_status != "completed":
        status = "RUNTIME_FAIL"
    elif categories:
        status = "OBSERVED_WITH_WARNINGS"
    else:
        status = "OBSERVED"
    return {
        "case_id": case.get("case_id"),
        "status": status,
        "quality_status": "RAGAS_NOT_AVAILABLE",
        "quality_score": None,
        "question": case.get("question", ""),
        "answer": answer,
        "runtime_status": runtime_status,
        "failure_categories": list(dict.fromkeys(categories)),
        "retrieved_context_count": len(retrieved),
        "retrieved_document_ids": [
            str(item.get("document_id"))
            for item in retrieved
            if isinstance(item, dict) and item.get("document_id")
        ],
        "citation_count": len(citations),
    }


def finalize_bhyt_deterministic_evaluation(
    dataset_path: Path,
    actual_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    cases = {case["case_id"]: case for case in _load_jsonl(dataset_path)}
    actuals = {record["case_id"]: record for record in _load_jsonl(actual_path)}
    scores = [
        score_bhyt_runtime_case(
            case,
            actuals.get(case_id, {"case_id": case_id, "status": "not_observable"}),
        )
        for case_id, case in cases.items()
    ]
    _write_jsonl(output_dir / "deterministic_case_scores.jsonl", scores)
    category_counts: dict[str, int] = {}
    for score in scores:
        for category in score["failure_categories"]:
            category_counts[category] = category_counts.get(category, 0) + 1
    summary = {
        "status": "OBSERVED_WITHOUT_RAGAS",
        "quality_status": "RAGAS_NOT_AVAILABLE",
        "legal_gold_validated": False,
        "reference_status": "official_answer_unreviewed",
        "total": len(scores),
        "observed": sum(score["status"] == "OBSERVED" for score in scores),
        "observed_with_warnings": sum(
            score["status"] == "OBSERVED_WITH_WARNINGS" for score in scores
        ),
        "runtime_failures": sum(score["status"] == "RUNTIME_FAIL" for score in scores),
        "empty_answers": sum("EMPTY_ANSWER" in score["failure_categories"] for score in scores),
        "empty_retrieval": sum("EMPTY_RETRIEVAL" in score["failure_categories"] for score in scores),
        "fallback_answers": sum("FALLBACK_ANSWER" in score["failure_categories"] for score in scores),
        "average_retrieved_contexts": round(
            mean(score["retrieved_context_count"] for score in scores), 6
        )
        if scores
        else 0.0,
        "average_citations": round(mean(score["citation_count"] for score in scores), 6)
        if scores
        else 0.0,
        "failure_categories": dict(
            sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
    }
    _write_json(output_dir / "deterministic_summary.json", summary)
    lines = [
        "# BHYT deterministic runtime evaluation",
        "",
        "> Ragas chưa chạy được trong môi trường hiện tại. File này chỉ phản ánh runtime, answer presence và retrieval/citation observability; không phải điểm đúng-sai pháp lý.",
        "",
        f"- Tổng: **{summary['total']}**",
        f"- Observed: **{summary['observed']}**",
        f"- Observed with warnings: **{summary['observed_with_warnings']}**",
        f"- Runtime failures: **{summary['runtime_failures']}**",
        f"- Context trung bình: **{summary['average_retrieved_contexts']}**",
        f"- Citation trung bình: **{summary['average_citations']}**",
        "",
        "## Cách xem",
        "",
        "- `actual_answers.jsonl`: answer và context thật của model.",
        "- `deterministic_case_scores.jsonl`: trạng thái từng case.",
        "- `deterministic_summary.json`: thống kê tổng.",
        "- Cài Ragas rồi chạy `ragas-score` và `finalize` trên cùng dataset/actual để có điểm chất lượng.",
    ]
    (output_dir / "deterministic_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def _run_id() -> str:
    return datetime.now(UTC).strftime("bhyt-200-%Y%m%d-%H%M%S")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(_canonical_json(payload) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(_canonical_json(row) + "\n")


def finalize_bhyt_evaluation(
    dataset_path: Path,
    actual_path: Path,
    ragas_path: Path,
    output_dir: Path,
    *,
    threshold: float = 0.60,
) -> dict[str, Any]:
    cases = {case["case_id"]: case for case in _load_jsonl(dataset_path)}
    actuals = {record["case_id"]: record for record in _load_jsonl(actual_path)}
    ragas_records = (
        {record["case_id"]: record for record in _load_jsonl(ragas_path)}
        if ragas_path.exists()
        else {}
    )
    scores = [
        score_bhyt_case(
            case,
            actuals.get(case_id, {"case_id": case_id, "status": "not_observable"}),
            ragas_records.get(case_id, {}),
            threshold=threshold,
        )
        for case_id, case in cases.items()
    ]
    _write_jsonl(output_dir / "case_scores.jsonl", scores)
    metric_means = {
        name: round(
            mean(
                float(score["metrics"][name])
                for score in scores
                if score["metrics"].get(name) is not None
            ),
            6,
        )
        if any(score["metrics"].get(name) is not None for score in scores)
        else None
        for name in (*RAGAS_METRICS, "ragas_mean")
    }
    failure_counts: dict[str, int] = {}
    for score in scores:
        for category in score["failure_categories"]:
            failure_counts[category] = failure_counts.get(category, 0) + 1
    summary = {
        "status": "PROVISIONAL_PASS" if all(score["status"] == "PROVISIONAL_PASS" for score in scores) else "PROVISIONAL_FAIL",
        "reference_status": "official_answer_unreviewed",
        "legal_gold_validated": False,
        "threshold": threshold,
        "total": len(scores),
        "provisional_passed": sum(score["status"] == "PROVISIONAL_PASS" for score in scores),
        "provisional_failed": sum(score["status"] == "PROVISIONAL_FAIL" for score in scores),
        "not_observable": sum(score["status"] == "NOT_OBSERVABLE" for score in scores),
        "runtime_completed": sum(score["runtime_status"] == "completed" for score in scores),
        "empty_retrieval": sum("EMPTY_RETRIEVAL" in score["failure_categories"] for score in scores),
        "fallback_answers": sum("FALLBACK_ANSWER" in score["failure_categories"] for score in scores),
        "metric_means": metric_means,
        "failure_categories": dict(sorted(failure_counts.items(), key=lambda item: (-item[1], item[0]))),
    }
    _write_json(output_dir / "summary.json", summary)
    failures = [score for score in scores if score["status"] != "PROVISIONAL_PASS"]
    failure_lines = [
        "# BHYT 200 provisional evaluation failures",
        "",
        "> Đây là evaluation trên candidates. `official_answer` chưa phải gold truth pháp lý cuối cùng.",
        "",
        f"- Trạng thái: **{summary['status']}**",
        f"- Tổng câu: **{summary['total']}**",
        f"- Provisional pass: **{summary['provisional_passed']}**",
        f"- Provisional fail: **{summary['provisional_failed']}**",
        f"- Not observable: **{summary['not_observable']}**",
        "",
    ]
    for score in failures:
        failure_lines.extend(
            [
                f"## {score['case_id']} — {score['status']}",
                "",
                f"- Nhóm lỗi: {', '.join(score['failure_categories']) or '(không có)' }",
                f"- Điểm Ragas trung bình: {score['metrics'].get('ragas_mean')}",
                f"- Câu hỏi: {score['question']}",
                f"- Câu trả lời: {score['answer']}",
                f"- Document truy xuất: {', '.join(score['retrieved_document_ids']) or '(không có)' }",
                "",
            ]
        )
    (output_dir / "failures.md").write_text("\n".join(failure_lines) + "\n", encoding="utf-8")
    report_lines = [
        "# BHYT 200 evaluation report",
        "",
        "> Đây là provisional evaluation candidates, không phải final Gold Evaluation Dataset.",
        "",
        f"- Status: **{summary['status']}**",
        f"- Cases: **{summary['total']}**",
        f"- Runtime completed: **{summary['runtime_completed']}**",
        f"- Provisional pass/fail/N/A: **{summary['provisional_passed']}/{summary['provisional_failed']}/{summary['not_observable']}**",
        "",
        "## Metric means",
        "",
    ]
    for name, value in metric_means.items():
        report_lines.append(f"- `{name}`: `{value}`")
    report_lines.extend(
        [
            "",
            "## How to inspect",
            "",
            "1. Mở `summary.json` để xem tổng quan.",
            "2. Mở `case_scores.jsonl` để xem từng câu và metric.",
            "3. Mở `actual_answers.jsonl` để xem output thật và context model đã truy xuất.",
            "4. Mở `ragas_scores.jsonl` để xem điểm Ragas từng câu.",
            "5. Mở `failures.md` để xem các case cần debug.",
        ]
    )
    (output_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return summary


def run_evaluation(
    candidates_path: Path,
    output_dir: Path,
    *,
    ragas_python: Path,
    evaluator_model: str = "gpt-4o-mini",
    embedding_model: str = "text-embedding-3-small",
    concurrency: int = 3,
    threshold: float = 0.60,
    expected_count: int = 200,
    deterministic_only: bool = False,
) -> dict[str, Any]:
    load_eval_environment()
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = output_dir / "bhyt_dataset.jsonl"
    actual_path = output_dir / "actual_answers.jsonl"
    ragas_path = output_dir / "ragas_scores.jsonl"
    run_id = _run_id()
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "status": "RUNNING",
        "mode": "bhyt_candidates_live_read_only",
        "candidate_source": str(candidates_path.resolve()),
        "models": {
            "agent": os.getenv("MODEL_NAME") or os.getenv("EVAL_MODEL_NAME"),
            "evaluator": evaluator_model,
            "embedding": embedding_model,
        },
        "threshold": threshold,
        "reference_status": "official_answer_unreviewed",
        "legal_gold_validated": False,
    }
    _write_json(output_dir / "run_manifest.json", manifest)
    build_result = build_bhyt_dataset(
        candidates_path,
        dataset_path,
        expected_count=expected_count,
        candidate_limit=expected_count,
    )
    validation = validate_bhyt_dataset(dataset_path, expected_count=expected_count)
    _write_json(output_dir / "dataset_validation.json", validation)
    if not validation["valid"]:
        manifest.update({"status": "DATASET_INVALID", "build": build_result})
        _write_json(output_dir / "run_manifest.json", manifest)
        raise RuntimeError(f"BHYT eval dataset invalid: {validation['errors']}")

    os.environ["EVAL_AGENT_MODE"] = "read_only"
    actual_summary = generate_actual_answers(dataset_path, actual_path, run_id)
    manifest["build"] = build_result
    manifest["actual_answer_generation"] = actual_summary
    _write_json(output_dir / "run_manifest.json", manifest)

    if deterministic_only:
        summary = finalize_bhyt_deterministic_evaluation(dataset_path, actual_path, output_dir)
        manifest.update(
            {
                "status": summary["status"],
                "summary": summary,
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
        _write_json(output_dir / "run_manifest.json", manifest)
        return summary

    if not ragas_python.is_file():
        raise FileNotFoundError(f"RAGAS interpreter not found: {ragas_python}")
    command = [
        str(ragas_python),
        str(Path(__file__).resolve()),
        "ragas-score",
        "--dataset",
        str(dataset_path.resolve()),
        "--actual",
        str(actual_path.resolve()),
        "--out",
        str(output_dir.resolve()),
        "--ragas",
        str(ragas_path.resolve()),
        "--evaluator-model",
        evaluator_model,
        "--embedding-model",
        embedding_model,
        "--concurrency",
        str(concurrency),
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, env=os.environ.copy(), check=False)
    if completed.returncode not in {0, 1} or not ragas_path.is_file():
        manifest.update({"status": "RAGAS_RUNTIME_ERROR", "ragas_exit_code": completed.returncode})
        _write_json(output_dir / "run_manifest.json", manifest)
        raise RuntimeError(f"RAGAS scoring failed with exit code {completed.returncode}")

    summary = finalize_bhyt_evaluation(
        dataset_path,
        actual_path,
        ragas_path,
        output_dir,
        threshold=threshold,
    )
    manifest.update(
        {
            "status": summary["status"],
            "summary": summary,
            "ragas_exit_code": completed.returncode,
            "finished_at": datetime.now(UTC).isoformat(),
        }
    )
    _write_json(output_dir / "run_manifest.json", manifest)
    return summary


def _cli() -> int:
    parser = argparse.ArgumentParser(description="BHYT 200 candidate live evaluation")
    parser.add_argument("command", choices=("build", "validate", "run", "ragas-score", "finalize"))
    parser.add_argument("--candidates", type=Path, default=Path("data/eval/bhyt_good_candidates.json"))
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--actual", type=Path)
    parser.add_argument("--ragas", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ragas-python", type=Path, default=Path(".eval-ragas-venv/Scripts/python.exe"))
    parser.add_argument("--evaluator-model", default=os.getenv("EVAL_JUDGE_MODEL", "gpt-4o-mini"))
    parser.add_argument("--embedding-model", default="text-embedding-3-small")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=0.60)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--deterministic-only", action="store_true")
    args = parser.parse_args()
    if args.command == "build":
        result = build_bhyt_dataset(args.candidates, args.out, expected_count=args.count)
        print(_canonical_json(result))
        return 0
    if args.command == "validate":
        result = validate_bhyt_dataset(args.dataset or args.out, expected_count=args.count)
        print(_canonical_json(result))
        return 0 if result["valid"] else 2
    if args.command == "ragas-score":
        result = score_ragas_answers(
            args.dataset,
            args.actual,
            args.ragas or args.out / "ragas_scores.jsonl",
            evaluator_model=args.evaluator_model,
            embedding_model=args.embedding_model,
            concurrency=args.concurrency,
            case_origins=("bhyt_candidate",),
        )
        print(_canonical_json(result))
        return 0 if result["metric_errors"] == 0 else 1
    if args.command == "finalize":
        summary = finalize_bhyt_evaluation(
            args.dataset,
            args.actual,
            args.ragas or args.out / "ragas_scores.jsonl",
            args.out,
            threshold=args.threshold,
        )
        print(_canonical_json(summary))
        return 0 if summary["status"] == "PROVISIONAL_PASS" else 1
    summary = run_evaluation(
        args.candidates,
        args.out,
        ragas_python=args.ragas_python,
        evaluator_model=args.evaluator_model,
        embedding_model=args.embedding_model,
        concurrency=args.concurrency,
        threshold=args.threshold,
        expected_count=args.count,
        deterministic_only=args.deterministic_only,
    )
    print(_canonical_json(summary))
    return 0 if summary["status"] == "PROVISIONAL_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
