from __future__ import annotations

import json
from pathlib import Path

from eval.golden_eval import write_report


def test_write_report_creates_a_human_readable_failure_map(tmp_path: Path) -> None:
    summary = {
        "run_id": "run-test",
        "status": "BLOCKED",
        "dataset": {"path": "draft_gold.jsonl", "count": 1},
        "actual_answer_generation": {"total": 1, "completed": 0, "not_observable": 1},
        "metrics": {"total": 1, "passed": 0, "failed": 0, "not_observable": 1},
    }
    scores = [
        {
            "case_id": "PRIV-001",
            "status": "NOT_OBSERVABLE",
            "severity": "P0",
            "failure_categories": ["OBSERVABILITY_GAP"],
            "why_failed": "No isolated adapter",
            "recommended_next_action": "Configure sandbox",
            "inspection_points": ["src/services/chat.py"],
        }
    ]

    write_report(tmp_path, summary, scores)

    assert (tmp_path / "summary.json").is_file()
    assert (tmp_path / "failures.md").is_file()
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    failures = (tmp_path / "failures.md").read_text(encoding="utf-8")
    assert "BLOCKED" in report
    assert "PRIV-001" in failures
    assert "src/services/chat.py" in failures
    assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))["status"] == "BLOCKED"
