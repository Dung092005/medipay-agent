#!/usr/bin/env python3
"""Run a bounded concurrent readiness smoke and write latency evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx


async def _request(client: httpx.AsyncClient, endpoint: str, index: int) -> dict[str, float | int]:
    started = time.perf_counter()
    response = await client.get(endpoint)
    return {
        "index": index,
        "status": response.status_code,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


async def run(endpoint: str, concurrency: int, timeout: float) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await asyncio.gather(*(_request(client, endpoint, index) for index in range(concurrency)))
    latencies = sorted(float(result["latency_ms"]) for result in results)
    status_counts = {
        str(status): sum(result["status"] == status for result in results)
        for status in sorted({int(result["status"]) for result in results})
    }
    return {
        "endpoint": endpoint,
        "requests": concurrency,
        "status_counts": status_counts,
        "latency_ms": {
            "p50": statistics.median(latencies),
            "p95": latencies[max(0, int(concurrency * 0.95) - 1)],
            "max": max(latencies),
        },
        "pass": all(result["status"] == 200 for result in results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/ready")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("concurrency must be positive")
    report = asyncio.run(run(args.endpoint, args.concurrency, args.timeout))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("endpoint", "requests", "status_counts", "latency_ms", "pass")}))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
