#!/usr/bin/env python3
"""Evaluate policy, table and no-answer cases omitted from retrieval ablations."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.retrieval import policy_response


async def _run(cases: list[dict]) -> list[dict]:
    from src.agents.graph import get_agent

    agent = get_agent()
    records: list[dict] = []
    for case in cases:
        question = str(case["question"])
        if case["kind"] == "policy":
            answer = policy_response(question) or ""
            records.append({"case_id": case["case_id"], "kind": case["kind"], "answer": answer, "pass": bool(answer)})
            continue
        try:
            result = await asyncio.wait_for(agent.ainvoke({"query": question}), timeout=120)
            answer = str(result.get("response") or "").strip()
            citations = result.get("citations") or []
            abstention = any(marker in answer.casefold() for marker in ("không tìm thấy", "chưa thể xác minh", "không khẳng định"))
            records.append({
                "case_id": case["case_id"], "kind": case["kind"], "answer": answer,
                "citation_count": len(citations), "pass": bool(answer) and (bool(citations) or abstention),
            })
        except Exception as exc:
            records.append({"case_id": case["case_id"], "kind": case["kind"], "answer": "", "error": type(exc).__name__, "pass": False})
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(Path(".env"), override=False)
    rows = [json.loads(line) for line in args.suite.read_text(encoding="utf-8").splitlines() if line.strip()]
    cases = [row for row in rows[1:] if row.get("kind") in {"policy", "table", "no_answer"}]
    records = asyncio.run(_run(cases))
    report = {
        "dataset_id": rows[0]["manifest"]["dataset_id"],
        "cases": len(records),
        "passed": sum(bool(row["pass"]) for row in records),
        "failed": sum(not row["pass"] for row in records),
        "deterministic_gate_pass": all(row["pass"] for row in records),
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("dataset_id", "cases", "passed", "failed", "deterministic_gate_pass")}))
    return 0 if report["deterministic_gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
