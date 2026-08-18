from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from eval.golden_eval import build_dataset, generate_actual_answers


def _source_files(path: Path) -> None:
    header = "id,title,so_ky_hieu,ngay_co_hieu_luc,ngay_het_hieu_luc,tinh_trang_hieu_luc,status_filter,agent_category\n"
    path.joinpath("metadata_bhyt.csv").write_text(header + "DOC-1,Policy,01/2026,2026-01-01,,,active,bhyt\n", encoding="utf-8")
    path.joinpath("metadata_vien_phi.csv").write_text(header + "DOC-2,Billing,02/2026,2026-02-01,,,active,vien_phi\n", encoding="utf-8")


def test_read_only_adapter_passes_only_query_and_records_observable_evidence(tmp_path: Path, monkeypatch) -> None:
    _source_files(tmp_path)
    dataset = tmp_path / "draft_gold.jsonl"
    build_dataset(tmp_path, dataset, count=1)
    actual = tmp_path / "actual_answers.jsonl"
    monkeypatch.setenv("EVAL_AGENT_MODE", "read_only")
    monkeypatch.setenv("MODEL_NAME", "test-model")
    fake_agent = type("FakeAgent", (), {})()
    fake_agent.ainvoke = AsyncMock(
        return_value={
            "response": "Grounded answer",
            "citations": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
            "retrieved_evidence": [
                type(
                    "Evidence",
                    (),
                    {
                        "chunk_id": "chunk-1",
                        "document_id": "doc-1",
                        "title": "Policy",
                        "section_title": "Điều 1",
                        "content": "Nội dung evidence thật mà agent đã nhận.",
                        "score": 0.9,
                        "channels": ["semantic"],
                    },
                )()
            ],
        }
    )

    with patch("src.agents.graph.get_agent", return_value=fake_agent):
        result = generate_actual_answers(dataset, actual, "run-read-only")

    record = json.loads(actual.read_text(encoding="utf-8").splitlines()[0])
    assert result == {"total": 1, "completed": 1, "not_observable": 0, "agent_errors": 0}
    assert record["status"] == "completed"
    assert record["answer"] == "Grounded answer"
    assert record["retrieved_contexts"][0]["chunk_id"] == "chunk-1"
    assert record["retrieved_contexts"][0]["text"] == "Nội dung evidence thật mà agent đã nhận."
    assert record["retrieved_contexts"][0]["section_title"] == "Điều 1"
    fake_agent.ainvoke.assert_awaited_once_with({"query": record["case_id"] and json.loads(dataset.read_text(encoding="utf-8").splitlines()[0])["agent_input"]["messages"][0]["content"]})
