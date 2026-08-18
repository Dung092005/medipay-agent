from __future__ import annotations

import csv
import json
from pathlib import Path

from eval.golden_eval import build_dataset, validate_dataset


def test_source_questions_use_public_document_labels_not_internal_ids(tmp_path: Path) -> None:
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
    row = {
        "id": "0682b030-84d3-11f1-8e08-0594f352574d",
        "title": "Nghị quyết synthetic về bảo hiểm y tế",
        "so_ky_hieu": "60/2026/NQ-HĐND",
        "ngay_co_hieu_luc": "01/07/2026",
        "ngay_het_hieu_luc": "",
        "tinh_trang_hieu_luc": "Còn hiệu lực",
        "status_filter": "active",
        "agent_category": "bhyt",
    }
    for filename in ("metadata_bhyt.csv", "metadata_vien_phi.csv"):
        with (tmp_path / filename).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(row)

    output = tmp_path / "draft_gold.jsonl"
    build_dataset(tmp_path, output, count=3)
    cases = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    questions = [case["agent_input"]["messages"][0]["content"] for case in cases]

    assert all(row["id"] not in question for question in questions)
    assert all(row["id"] not in case["case_id"] for case in cases)
    assert all(row["so_ky_hieu"] in question for question in questions)


def test_validation_rejects_internal_id_in_user_question(tmp_path: Path) -> None:
    dataset = tmp_path / "bad.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "case_id": "DATE-001",
                "category": "policy_date",
                "risk": "P1",
                "draft_gold": True,
                "agent_input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": "Văn bản 0682b030-84d3-11f1-8e08-0594f352574d có hiệu lực từ ngày nào?",
                        }
                    ],
                    "runtime_context": {},
                },
                "gold_facts": {"document_id": "0682b030-84d3-11f1-8e08-0594f352574d"},
                "evidence_refs": [{"document_id": "0682b030-84d3-11f1-8e08-0594f352574d"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = validate_dataset(dataset)

    assert result["valid"] is False
    assert any("internal document_id" in error for error in result["errors"])
