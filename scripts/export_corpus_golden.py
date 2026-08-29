"""Export corpus-grounded golden dataset for eval."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.golden_eval import _load_jsonl, build_golden_dataset, validate_golden_dataset


def export(source_dir: Path, output_json: Path, *, count: int = 30) -> dict:
    jsonl_path = output_json.with_suffix(".jsonl")
    build_info = build_golden_dataset(source_dir, jsonl_path, source_case_count=count - 6)
    cases = _load_jsonl(jsonl_path)
    validation = validate_golden_dataset(jsonl_path, source_dir)
    payload = {
        "metadata": {
            "schema_version": "1.0",
            "dataset_name": "MediPay answerable corpus golden",
            "source": "data/raw metadata_bhyt + metadata_vien_phi + content.csv",
            "record_count": len(cases),
            "gold_status": "corpus_grounded",
            "active_release": "snapshot-5dfc6bb64d046a1c",
            "created_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "notes": "Câu hỏi sạch, sinh từ văn bản trong corpus — bot có khả năng trả lời cao.",
        },
        "records": cases,
        "validation": validation,
        "build": build_info,
    }
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    out = PROJECT_ROOT / "data" / "eval" / "golden_answerable_v30.json"
    info = export(PROJECT_ROOT / "data" / "raw", out, count=30)
    print(json.dumps({"count": info["metadata"]["record_count"], "valid": info["validation"]["valid"]}, ensure_ascii=False))
