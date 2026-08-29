from __future__ import annotations

import argparse
import asyncio
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


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(_canonical_json(row) + "\n")


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
    os.environ["EVAL_AGENT_MODE"] = "read_only"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metric_value(result: Any) -> float | None:
    if not isinstance(result, dict):
        return None
    value = result.get("value")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_bhxh_dataset(
    candidates_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    if not candidates_path.is_file():
        raise FileNotFoundError(f"Candidate file not found: {candidates_path}")

    # Support JSON envelope or JSONL
    if candidates_path.suffix == ".jsonl":
        records = _load_jsonl(candidates_path)
    else:
        payload = json.loads(candidates_path.read_text(encoding="utf-8"))
        records = payload.get("records") if isinstance(payload, dict) else payload

    if not isinstance(records, list):
        raise ValueError("Candidates file must contain a list of records")

    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for idx, r in enumerate(records, start=1):
        case_id = str(r.get("id") or r.get("source_item_id") or f"CASE-{idx}").strip()
        if case_id in seen_ids:
            case_id = f"{case_id}-{idx}"
        seen_ids.add(case_id)

        question = str(r.get("question") or "").strip()
        official_answer = str(r.get("official_answer") or r.get("reference") or "").strip()
        category = str(r.get("category") or "bhxh_policy").strip()
        temporal_risk = str(r.get("temporal_risk") or "high").strip().casefold()
        risk = {"low": "P2", "medium": "P1", "high": "P1"}.get(temporal_risk, "P1")

        case = {
            "case_id": case_id,
            "case_origin": "source_derived",
            "category": category,
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
                    "source_item_id": str(r.get("source_item_id") or case_id),
                    "source_url": str(r.get("source_url") or "").strip(),
                }
            ],
            "source_file": candidates_path.name,
            "source_hashes": {candidates_path.name: _sha256(candidates_path)},
            "candidate_metadata": {
                "submitted_at": r.get("submitted_at"),
                "answered_at": r.get("answered_at"),
                "legal_basis": r.get("legal_basis", []),
                "temporal_risk": r.get("temporal_risk"),
                "classification_reason": r.get("classification_reason", ""),
            },
        }
        cases.append(case)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(_canonical_json(case) + "\n")

    return {
        "count": len(cases),
        "dataset_sha256": _sha256(output_path),
        "source": str(candidates_path),
        "cases": cases,
    }


def score_bhxh_case(
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

    numeric_vals = [v for v in values.values() if v is not None]
    ragas_mean = round(mean(numeric_vals), 6) if numeric_vals else None

    if runtime_status != "completed" or unavailable:
        status = "NOT_OBSERVABLE"
    else:
        status = "PROVISIONAL_PASS" if not categories else "PROVISIONAL_FAIL"

    return {
        "case_id": case.get("case_id"),
        "category": case.get("category"),
        "status": status,
        "reference_status": case.get("reference_status", "official_answer_unreviewed"),
        "legal_gold_validated": False,
        "question": case.get("question")
        or case.get("agent_input", {}).get("messages", [{}])[0].get("content", ""),
        "answer": answer,
        "official_answer": case.get("official_answer", ""),
        "runtime_status": runtime_status,
        "failure_categories": list(dict.fromkeys(categories)),
        "retrieved_document_ids": [
            str(item.get("document_id"))
            for item in retrieved
            if isinstance(item, dict) and item.get("document_id")
        ],
        "retrieved_context_count": len(retrieved),
        "citation_count": len(actual.get("structured_output", {}).get("citations", []) or []),
        "latency_ms": actual.get("latency_ms"),
        "metrics": {
            **values,
            "ragas_mean": ragas_mean,
        },
        "why_failed": "; ".join(list(dict.fromkeys(categories))),
    }


def finalize_bhxh_evaluation(
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
        if ragas_path.is_file()
        else {}
    )

    scores = [
        score_bhxh_case(
            case,
            actuals.get(case_id, {"case_id": case_id, "status": "not_observable"}),
            ragas_records.get(case_id, {}),
            threshold=threshold,
        )
        for case_id, case in cases.items()
    ]
    _write_jsonl(output_dir / "case_scores.jsonl", scores)

    metric_means = {}
    for name in (*RAGAS_METRICS, "ragas_mean"):
        valid_nums = [float(score["metrics"][name]) for score in scores if score["metrics"].get(name) is not None]
        metric_means[name] = round(mean(valid_nums), 6) if valid_nums else None

    failure_counts: dict[str, int] = {}
    for score in scores:
        for cat in score["failure_categories"]:
            failure_counts[cat] = failure_counts.get(cat, 0) + 1

    category_stats: dict[str, dict[str, int]] = {}
    for score in scores:
        cat = str(score.get("category") or "unknown")
        bucket = category_stats.setdefault(cat, {"total": 0, "passed": 0, "failed": 0, "not_observable": 0})
        bucket["total"] += 1
        if score["status"] == "PROVISIONAL_PASS":
            bucket["passed"] += 1
        elif score["status"] == "NOT_OBSERVABLE":
            bucket["not_observable"] += 1
        else:
            bucket["failed"] += 1

    summary = {
        "status": "PROVISIONAL_PASS" if all(s["status"] == "PROVISIONAL_PASS" for s in scores) else "PROVISIONAL_FAIL",
        "dataset_name": dataset_path.stem,
        "reference_status": "official_answer_unreviewed",
        "threshold": threshold,
        "total": len(scores),
        "provisional_passed": sum(s["status"] == "PROVISIONAL_PASS" for s in scores),
        "provisional_failed": sum(s["status"] == "PROVISIONAL_FAIL" for s in scores),
        "not_observable": sum(s["status"] == "NOT_OBSERVABLE" for s in scores),
        "runtime_completed": sum(s["runtime_status"] == "completed" for s in scores),
        "empty_retrieval": sum("EMPTY_RETRIEVAL" in s["failure_categories"] for s in scores),
        "fallback_answers": sum("FALLBACK_ANSWER" in s["failure_categories"] for s in scores),
        "metric_means": metric_means,
        "failure_categories": dict(sorted(failure_counts.items(), key=lambda item: (-item[1], item[0]))),
        "by_category": category_stats,
    }

    (output_dir / "summary.json").write_text(_canonical_json(summary) + "\n", encoding="utf-8")

    # Failures report
    failures = [score for score in scores if score["status"] != "PROVISIONAL_PASS"]
    failure_lines = [
        f"# {dataset_path.stem} — Evaluation Failures",
        "",
        f"> Đánh giá RAGAS trên golden dataset `{dataset_path.name}`. Ngưỡng đạt chuẩn threshold = {threshold}",
        "",
        f"- **Tổng số câu**: {summary['total']}",
        f"- **Provisional PASS**: {summary['provisional_passed']}",
        f"- **Provisional FAIL**: {summary['provisional_failed']}",
        f"- **Not Observable / Lỗi**: {summary['not_observable']}",
        "",
        "## Chi tiết các case không đạt chuẩn",
        "",
    ]
    for score in failures:
        failure_lines.extend(
            [
                f"### Case `{score['case_id']}` ({score['status']})",
                f"- **Chủ đề**: `{score['category']}`",
                f"- **Lý do / Lỗi**: `{score['why_failed'] or 'None'}`",
                f"- **Ragas Mean**: `{score['metrics'].get('ragas_mean')}` | Factual: `{score['metrics'].get('factual_correctness')}` | Faithfulness: `{score['metrics'].get('faithfulness')}`",
                f"- **Câu hỏi**: {score['question']}",
                f"- **Câu trả lời của Bot**:\n> {score['answer']}",
                f"- **Câu trả lời mẫu (Reference)**:\n> {score['official_answer']}",
                f"- **Số văn bản truy xuất**: {score['retrieved_context_count']} (IDs: {', '.join(score['retrieved_document_ids']) or 'None'})",
                "",
                "---",
                "",
            ]
        )
    (output_dir / "failures.md").write_text("\n".join(failure_lines) + "\n", encoding="utf-8")

    # Full report
    pass_rate = round(summary["provisional_passed"] / summary["total"] * 100, 1) if summary["total"] else 0.0
    report_lines = [
        f"# MediPay GraphRAG — Báo Cáo RAGAS ({dataset_path.stem})",
        "",
        f"- **Thời gian chạy**: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"- **Dataset**: `{dataset_path.name}`",
        f"- **Trạng thái tổng thể**: **{summary['status']}**",
        f"- **Tổng số câu hỏi**: **{summary['total']}**",
        f"- **Runtime hoàn thành**: **{summary['runtime_completed']}/{summary['total']}**",
        f"- **Pass rate**: **{summary['provisional_passed']}/{summary['total']} ({pass_rate}%)**",
        f"- **Fail**: **{summary['provisional_failed']}** | **Not observable**: **{summary['not_observable']}**",
        "",
        "## 1. Điểm RAGAS trung bình",
        "",
        "| Metric | Điểm trung bình | Ngưỡng yêu cầu | Đánh giá |",
        "|---|---:|---:|:---:|",
    ]

    for name in RAGAS_METRICS:
        val = metric_means.get(name)
        val_str = f"{val:.4f}" if val is not None else "N/A"
        pass_icon = "✅ PASS" if (val is not None and val >= threshold) else "❌ FAIL"
        report_lines.append(f"| `{name}` | **{val_str}** | `{threshold}` | {pass_icon} |")

    mean_val = metric_means.get("ragas_mean")
    mean_str = f"{mean_val:.4f}" if mean_val is not None else "N/A"
    report_lines.append(f"| **Tổng thể (ragas_mean)** | **{mean_str}** | `{threshold}` | {'✅ PASS' if (mean_val and mean_val >= threshold) else '❌ FAIL'} |")

    report_lines.extend(
        [
            "",
            "## 2. Pass rate theo chủ đề",
            "",
            "| Chủ đề | Tổng | Pass | Fail | Not observable |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for cat, bucket in sorted(summary["by_category"].items()):
        report_lines.append(
            f"| `{cat}` | {bucket['total']} | {bucket['passed']} | {bucket['failed']} | {bucket['not_observable']} |"
        )

    report_lines.extend(
        [
            "",
            "## 3. Top lỗi",
            "",
            "| Nhóm lỗi | Số lượng |",
            "|---|---:|",
        ]
    )
    for cat, count in summary["failure_categories"].items():
        report_lines.append(f"| `{cat}` | {count} |")

    report_lines.extend(
        [
            "",
            "## 4. Bảng điểm từng câu",
            "",
            "| Case | Chủ đề | Status | Ragas mean | Factual | Faithfulness | Relevancy |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for score in sorted(scores, key=lambda item: (item["status"] != "PROVISIONAL_PASS", -(item["metrics"].get("ragas_mean") or 0))):
        metrics = score["metrics"]
        ragas_mean = metrics.get("ragas_mean")
        report_lines.append(
            "| `{case_id}` | `{category}` | {status} | {ragas_mean} | {factual} | {faithfulness} | {relevancy} |".format(
                case_id=score["case_id"],
                category=score["category"],
                status=score["status"],
                ragas_mean=f"{ragas_mean:.3f}" if ragas_mean is not None else "N/A",
                factual=f"{metrics.get('factual_correctness'):.3f}" if metrics.get("factual_correctness") is not None else "N/A",
                faithfulness=f"{metrics.get('faithfulness'):.3f}" if metrics.get("faithfulness") is not None else "N/A",
                relevancy=f"{metrics.get('response_relevancy'):.3f}" if metrics.get("response_relevancy") is not None else "N/A",
            )
        )

    report_lines.extend(
        [
            "",
            "## 5. File kết quả",
            "",
            "- `dataset.jsonl` — golden questions + reference answers",
            "- `actual_answers.jsonl` — câu trả lời thật + retrieved context",
            "- `ragas_scores.jsonl` — 5 metric RAGAS từng case",
            "- `case_scores.jsonl` — tổng hợp pass/fail từng case",
            "- `failures.md` — chi tiết các case không đạt",
            "- `summary.json` — số liệu tổng hợp",
        ]
    )
    (output_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RAGAS evaluation on golden hoidap QA cases")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run command (full end-to-end)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--candidates", type=Path, default=PROJECT_ROOT / "data" / "eval" / "golden_hoidap_v50.json")
    run_parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "eval" / "results" / "hoidap-v50-ragas")
    run_parser.add_argument("--ragas-python", type=Path, default=PROJECT_ROOT / ".eval-ragas-venv" / "Scripts" / "python.exe")
    run_parser.add_argument("--evaluator-model", default="gpt-4o-mini")
    run_parser.add_argument("--embedding-model", default="text-embedding-3-small")
    run_parser.add_argument("--concurrency", type=int, default=3)
    run_parser.add_argument("--threshold", type=float, default=0.60)
    run_parser.add_argument("--skip-inference", action="store_true", help="Skip agent inference if actual_answers.jsonl already exists")

    # ragas-score command (run in isolated ragas venv)
    score_parser = subparsers.add_parser("ragas-score")
    score_parser.add_argument("--dataset", type=Path, required=True)
    score_parser.add_argument("--actual", type=Path, required=True)
    score_parser.add_argument("--output", type=Path, required=True)
    score_parser.add_argument("--evaluator-model", default="gpt-4o-mini")
    score_parser.add_argument("--embedding-model", default="text-embedding-3-small")
    score_parser.add_argument("--concurrency", type=int, default=3)

    args = parser.parse_args()

    if args.command == "ragas-score":
        score_ragas_answers(
            args.dataset,
            args.actual,
            args.output,
            evaluator_model=args.evaluator_model,
            embedding_model=args.embedding_model,
            concurrency=args.concurrency,
            case_origins=("source_derived",),
        )
        return

    if args.command == "run":
        load_eval_environment()
        args.out.mkdir(parents=True, exist_ok=True)
        dataset_path = args.out / "dataset.jsonl"
        actual_path = args.out / "actual_answers.jsonl"
        ragas_path = args.out / "ragas_scores.jsonl"

        print("[1/4] Chuẩn hóa Dataset...", flush=True)
        build_info = build_bhxh_dataset(args.candidates, dataset_path)
        print(f"      Đã nạp {build_info['count']} cases vào {dataset_path}", flush=True)

        if not args.skip_inference or not actual_path.is_file():
            print(f"[2/4] Chạy Live Agent Inference trên {build_info['count']} câu hỏi (read-only)...", flush=True)
            run_id = f"hoidap-v50-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
            generate_actual_answers(dataset_path, actual_path, run_id)
            print(f"      Hoàn thành inference. Kết quả lưu tại {actual_path}", flush=True)
        else:
            print(f"[2/4] Bỏ qua inference, sử dụng kết quả có sẵn tại {actual_path}", flush=True)

        print("[3/4] Chấm điểm Ragas qua LLM Judge (.eval-ragas-venv)...", flush=True)
        ragas_py = str(args.ragas_python)
        cmd = [
            ragas_py,
            str(Path(__file__).resolve()),
            "ragas-score",
            "--dataset", str(dataset_path),
            "--actual", str(actual_path),
            "--output", str(ragas_path),
            "--evaluator-model", args.evaluator_model,
            "--embedding-model", args.embedding_model,
            "--concurrency", str(args.concurrency),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            print(f"Lỗi khi chạy Ragas scoring: {res.stderr}", file=sys.stderr)
            sys.exit(res.returncode)
        print(f"      Hoàn thành chấm điểm Ragas. Kết quả lưu tại {ragas_path}", flush=True)

        print("[4/4] Tổng hợp báo cáo và sinh metrics...", flush=True)
        summary = finalize_bhxh_evaluation(
            dataset_path,
            actual_path,
            ragas_path,
            args.out,
            threshold=args.threshold,
        )

        manifest = {
            "run_id": f"hoidap-v50-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}",
            "status": "COMPLETED",
            "candidate_source": str(args.candidates),
            "threshold": args.threshold,
            "models": {
                "evaluator": args.evaluator_model,
                "embedding": args.embedding_model,
            },
            "summary": summary,
        }
        (args.out / "run_manifest.json").write_text(_canonical_json(manifest) + "\n", encoding="utf-8")
        print("====== HOÀN TẤT ĐÁNH GIÁ ======", flush=True)
        print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
