#!/usr/bin/env python3
"""Generate grounded natural-language semantic retrieval questions."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

csv.field_size_limit(sys.maxsize)
load_dotenv()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=80)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required")
    with (args.source_dir / "metadata.csv").open(encoding="utf-8-sig", newline="") as handle:
        metadata = [dict(row) for row in csv.DictReader(handle)]
    # Choose answer-ready documents and use canonical semantic passages from
    # PostgreSQL after release publication, so the benchmark tests real chunks.
    selected = [row for row in metadata if row.get("answer_ready", "").casefold() == "true"][: args.cases]
    import psycopg

    with (
        psycopg.connect(
            host=os.getenv("PGHOST"),
            port=int(os.getenv("PGPORT", "5432")),
            dbname=os.getenv("PGDATABASE"),
            user=os.getenv("PGUSER"),
            password=os.getenv("PGPASSWORD"),
        ) as conn,
        conn.cursor() as cur,
    ):
        payloads = []
        for row in selected:
            cur.execute(
                """SELECT text FROM active_graph_chunks
                   WHERE document_id=%s AND semantic_eligible AND length(text)>250
                   ORDER BY length(text) DESC LIMIT 1""",
                (row["id"],),
            )
            result = cur.fetchone()
            if result:
                payloads.append((row, result[0][:3500]))

    client = OpenAI(timeout=30, max_retries=0)

    def generate(item: tuple[dict[str, str], str]) -> dict[str, str]:
        row, passage = item
        prompt = f"""Tạo đúng 1 câu hỏi tiếng Việt mà câu trả lời nằm trực tiếp trong đoạn pháp lý dưới đây.
Câu hỏi phải nhắc chi tiết phân biệt như đối tượng, mức tiền, tỷ lệ, cơ quan, địa phương hoặc điều kiện.
Không ghi số/ký hiệu văn bản, không nói 'đoạn trên', không thêm kiến thức ngoài đoạn.
Chỉ trả về câu hỏi, không giải thích.

TIÊU ĐỀ: {row["title"]}
ĐOẠN: {passage}"""
        response = client.chat.completions.create(
            model=os.getenv("SEMANTIC_EVAL_MODEL", "gpt-4.1-mini"),
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        question = re.sub(r"\s+", " ", response.choices[0].message.content or "").strip()
        return {"document_id": row["id"], "question": question, "passage": passage}

    cases: list[dict[str, str]] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(generate, item) for item in payloads]
        for future in as_completed(futures):
            try:
                case = future.result()
                if case["question"]:
                    cases.append(case)
            except Exception:
                errors += 1
    cases.sort(key=lambda row: row["document_id"])
    with args.output.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")
    report = {"requested": args.cases, "generated": len(cases), "errors": errors, "human_adjudicated": False}
    args.output.with_suffix(".report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
