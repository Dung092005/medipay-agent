from __future__ import annotations

import csv
import json
from pathlib import Path

from eval.golden_eval import build_dataset, make_case


def _write_metadata(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "id",
        "title",
        "so_ky_hieu",
        "ngay_co_hieu_luc",
        "ngay_het_hieu_luc",
        "tinh_trang_hieu_luc",
        "status_filter",
        "agent_category",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_make_case_keeps_gold_out_of_agent_input() -> None:
    case = make_case(
        case_id="DOC-001",
        question="Văn bản này có tên gì?",
        category="coverage",
        risk="P2",
        gold={"document_id": "DOC-1", "title": "Văn bản synthetic"},
    )

    assert case["agent_input"] == {
        "messages": [{"role": "user", "content": "Văn bản này có tên gì?"}],
        "runtime_context": {},
    }
    assert "gold_facts" not in case["agent_input"]
    assert case["draft_gold"] is True
    assert case["gold_facts"]["document_id"] == "DOC-1"


def test_build_dataset_is_deterministic_and_source_backed(tmp_path: Path) -> None:
    rows = [
        {
            "id": "BHYT-1",
            "title": "Quy định BHYT synthetic",
            "so_ky_hieu": "01/2026/TT-SYN",
            "ngay_co_hieu_luc": "2026-01-01",
            "ngay_het_hieu_luc": "",
            "tinh_trang_hieu_luc": "Còn hiệu lực",
            "status_filter": "active",
            "agent_category": "bhyt",
        },
        {
            "id": "VP-1",
            "title": "Quy định viện phí synthetic",
            "so_ky_hieu": "02/2026/TT-SYN",
            "ngay_co_hieu_luc": "2026-02-01",
            "ngay_het_hieu_luc": "",
            "tinh_trang_hieu_luc": "Còn hiệu lực",
            "status_filter": "active",
            "agent_category": "vien_phi",
        },
    ]
    _write_metadata(tmp_path / "metadata_bhyt.csv", rows[:1])
    _write_metadata(tmp_path / "metadata_vien_phi.csv", rows[1:])
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"

    first_manifest = build_dataset(tmp_path, first, count=6)
    second_manifest = build_dataset(tmp_path, second, count=6)

    assert first_manifest == second_manifest
    first_cases = [json.loads(line) for line in first.read_text(encoding="utf-8").splitlines()]
    assert len(first_cases) == 6
    assert len({case["case_id"] for case in first_cases}) == 6
    document_cases = [case for case in first_cases if case["category"] == "document_lookup"]
    assert document_cases
    assert all(case["evidence_refs"] for case in document_cases)
    assert all("gold_facts" not in case["agent_input"] for case in first_cases)
