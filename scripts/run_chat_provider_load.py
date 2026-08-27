#!/usr/bin/env python3
"""Run a bounded provider-backed GraphRAG concurrency smoke.

This is deliberately smaller than a production load test: it measures the
real embedding/Qdrant/Neo4j/LLM path without emitting answers or credentials.
Use the result to tune concurrency before a managed staging load run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

QUERIES = (
    "Đối tượng nào được quy định mức hỗ trợ đóng bảo hiểm y tế trên địa bàn tỉnh Quảng Ngãi theo nghị quyết này?",
    "Quyết định có hiệu lực sau bao nhiêu ngày kể từ ngày ký ban hành và thay thế quyết định nào của Ủy ban nhân dân tỉnh Ninh Thuận?",
    "Ủy ban nhân dân tỉnh Ninh Thuận căn cứ vào những văn bản pháp luật nào để ban hành quyết định bổ sung Điều 6?",
    "Cơ quan nào đề nghị ban hành Quy chế phối hợp thực hiện pháp luật về bảo hiểm xã hội, bảo hiểm y tế, bảo hiểm thất nghiệp?",
    "Cơ quan Bảo hiểm y tế có trách nhiệm gì khi thu BHYT từ các doanh nghiệp và phí thu hộ là bao nhiêu phần trăm?",
    "Người thuộc diện quá nghèo được cấp thẻ bảo hiểm y tế và thanh toán chi phí khám chữa bệnh như thế nào?",
    "Mức giá dịch vụ khám bệnh, chữa bệnh không thuộc phạm vi thanh toán của quỹ bảo hiểm y tế được quy định ra sao?",
    "Những điều kiện nào cần kiểm tra khi xác định quyền lợi bảo hiểm y tế của người bệnh?",
    "Văn bản nào hướng dẫn chế độ quản lý tài chính quỹ bảo hiểm y tế và có hiệu lực từ thời điểm nào?",
    "Các bên cần đối chiếu những dữ liệu nào trước khi kết luận số tiền viện phí cuối cùng?",
)


async def _one(agent, query: str, timeout: float, index: int) -> dict[str, object]:
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(agent.ainvoke({"query": query}), timeout=timeout)
        answer = str(result.get("response") or "").strip()
        return {
            "index": index,
            "status": "completed" if answer else "empty",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "response_chars": len(answer),
            "citation_count": len(result.get("citations") or []),
        }
    except Exception as exc:
        return {
            "index": index,
            "status": "error",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error_type": type(exc).__name__,
        }


async def run(concurrency: int, timeout: float, count: int) -> dict[str, object]:
    if concurrency < 1 or count < 1:
        raise ValueError("concurrency and count must be positive")
    from dotenv import load_dotenv

    load_dotenv(override=False)
    from src.agents.graph import get_agent
    from src.services.chat import get_runtime
    from src.services.llm import close_llm

    semaphore = asyncio.Semaphore(concurrency)
    agent = get_agent()

    async def bounded(index: int, query: str):
        async with semaphore:
            return await _one(agent, query, timeout, index)

    selected = list(QUERIES[: min(count, len(QUERIES))])
    results = await asyncio.gather(*(bounded(index, query) for index, query in enumerate(selected)))
    await get_runtime().close()
    close_llm()
    latencies = sorted(float(item["latency_ms"]) for item in results)
    completed = sum(item["status"] == "completed" for item in results)
    return {
        "requests": len(results),
        "concurrency": concurrency,
        "timeout_seconds": timeout,
        "status_counts": {
            status: sum(item["status"] == status for item in results)
            for status in sorted({str(item["status"]) for item in results})
        },
        "latency_ms": {
            "p50": round(statistics.median(latencies), 2),
            "p95": latencies[max(0, int(len(latencies) * 0.95) - 1)],
            "max": max(latencies),
        },
        "completed": completed,
        "pass": completed == len(results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run(args.concurrency, args.timeout, args.count))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("requests", "concurrency", "status_counts", "latency_ms", "pass")}, ensure_ascii=False))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
