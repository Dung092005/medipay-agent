#!/usr/bin/env python3
"""Enrich a reconciled corpus only when Tavily finds official corroboration.

This is deliberately conservative: search results are evidence records, not
truth by themselves. A legal-status field changes only when an official-domain
result matches the document signature plus issuer/year and states a recognizable
status. All non-accepted responses remain in a JSONL review trail.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv

csv.field_size_limit(sys.maxsize)
load_dotenv()

OFFICIAL_DOMAINS = ("vbpl.vn", "vanban.chinhphu.vn", "congbao.chinhphu.vn", "gov.vn")
STATUS_PATTERNS = (
    ("Hết hiệu lực một phần", re.compile(r"hết hiệu lực một phần", re.IGNORECASE)),
    ("Ngưng hiệu lực", re.compile(r"(?:ngưng|tạm ngưng|đình chỉ) hiệu lực", re.IGNORECASE)),
    ("Hết hiệu lực", re.compile(r"hết hiệu lực", re.IGNORECASE)),
    ("Chưa có hiệu lực", re.compile(r"chưa có hiệu lực", re.IGNORECASE)),
    ("Còn hiệu lực", re.compile(r"còn hiệu lực", re.IGNORECASE)),
)
EXTRA_FIELDS = (
    "official_status_url",
    "official_status_result_title",
    "official_status_evidence_sha256",
    "official_status_verified_at",
    "official_content_candidate_url",
    "official_content_candidate_sha256",
)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\ufeff", "")).strip()


def identity(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", clean(value).casefold())
    return "".join(char for char in normalized if char.isalnum())


def words(value: Any) -> set[str]:
    return {
        identity(token)
        for token in re.findall(r"[\wÀ-ỹĐđ]+", clean(value).casefold(), flags=re.UNICODE)
        if len(identity(token)) >= 3
    }


def is_official_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").casefold()
    return any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_DOMAINS)


def detect_status(value: str) -> str:
    for label, pattern in STATUS_PATTERNS:
        if pattern.search(value):
            return label
    return ""


def search_tavily(
    api_key: str,
    query: str,
    *,
    search_depth: str,
    timeout_seconds: float,
    attempts: int,
) -> dict[str, Any]:
    body = json.dumps({
        "query": query,
        "topic": "general",
        "search_depth": search_depth,
        "max_results": 5,
        "include_answer": False,
        "include_raw_content": False,
        "include_usage": True,
        "include_domains": list(OFFICIAL_DOMAINS),
    }).encode("utf-8")
    request = Request(
        "https://api.tavily.com/search",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "VinGeniusCorpusEvidence/1.0",
        },
    )
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed API endpoint
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise RuntimeError(f"Tavily HTTP {error.code}") from error
        except URLError as error:
            if attempt == attempts - 1:
                raise RuntimeError(f"Tavily network error: {error.reason}") from error
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def accepted_result(metadata: dict[str, str], response: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    signature = identity(metadata.get("so_ky_hieu"))
    issue_year = clean(metadata.get("ngay_ban_hanh"))[-4:]
    issuer_words = words(metadata.get("co_quan_ban_hanh"))
    for result in response.get("results", []):
        if not isinstance(result, dict) or not is_official_url(str(result.get("url", ""))):
            continue
        evidence = " ".join((str(result.get("title", "")), str(result.get("content", ""))))
        evidence_identity = identity(evidence)
        if not signature or signature not in evidence_identity:
            continue
        evidence_words = words(evidence)
        issuer_coverage = len(issuer_words & evidence_words) / len(issuer_words) if issuer_words else 1.0
        if issue_year and issue_year not in evidence and issuer_coverage < 0.5:
            continue
        # Search snippets from official portals often contain long "related
        # documents" lists. A target signature appearing only in that list
        # does not make a status phrase on the page apply to the target. Status
        # is authoritative only when the result title itself identifies the
        # requested document; otherwise retain the result as identity evidence
        # without changing legal status.
        title_identity = identity(result.get("title", ""))
        status = detect_status(evidence) if signature in title_identity else ""
        return result, status
    return None, ""


def task_query(task: dict[str, str], metadata: dict[str, str]) -> str:
    signature = clean(metadata.get("so_ky_hieu"))
    issuer = clean(metadata.get("co_quan_ban_hanh"))
    if task["task"] == "verify_legal_status":
        return f'"{signature}" "{metadata.get("title", "")}" "{issuer}"'
    return f'"{signature}" "{issuer}" "{metadata.get("title", "")}"'


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("data/clean/medical_active_v2"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/clean/medical_active_v3"))
    parser.add_argument("--max-requests", type=int, default=450)
    parser.add_argument("--start-offset", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--search-depth", choices=("basic", "advanced"), default="advanced")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--retry-errors-only", action="store_true")
    args = parser.parse_args()
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is required in the process environment")
    if args.max_requests <= 0 or args.workers <= 0 or args.attempts <= 0:
        raise ValueError("max-requests, workers, and attempts must be positive")
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)

    with (args.source_dir / "metadata.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        metadata_rows = [dict(row) for row in reader]
        metadata_fields = list(reader.fieldnames or [])
    metadata_by_id = {row["id"]: row for row in metadata_rows}
    with (args.source_dir / "crawl_backlog.csv").open(encoding="utf-8-sig", newline="") as handle:
        backlog = [dict(row) for row in csv.DictReader(handle)]
    priority = {"high": 0, "medium": 1, "low": 2}
    tasks = [
        row for row in backlog
        if row["task"] in {"verify_legal_status", "verify_or_replace_content_html"}
        and row["entity_id"] in metadata_by_id
    ]
    tasks.sort(key=lambda row: (priority.get(row["priority"], 9), row["task"], row["entity_id"]))
    total_tasks = len(tasks)
    existing_audit_path = args.source_dir / "tavily_evidence.jsonl"
    existing_audit_rows: list[dict[str, Any]] = []
    if existing_audit_path.is_file():
        existing_audit_rows = [
            json.loads(line)
            for line in existing_audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    if args.retry_errors_only:
        retry_keys = {
            (str(row.get("document_id")), str(row.get("task")))
            for row in existing_audit_rows
            if row.get("error")
        }
        tasks = [row for row in tasks if (row["entity_id"], row["task"]) in retry_keys]
        tasks = tasks[: args.max_requests]
    else:
        tasks = tasks[args.start_offset : args.start_offset + args.max_requests]

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.source_dir, args.output_dir)
    audit_rows: list[dict[str, Any]] = []

    def execute(task: dict[str, str]) -> tuple[dict[str, str], dict[str, Any] | None, Exception | None]:
        try:
            return task, search_tavily(
                api_key,
                task_query(task, metadata_by_id[task["entity_id"]]),
                search_depth=args.search_depth,
                timeout_seconds=args.timeout_seconds,
                attempts=args.attempts,
            ), None
        except Exception as error:  # retain failures as audit records
            return task, None, error

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(execute, task) for task in tasks]
        for future in as_completed(futures):
            task, response, error = future.result()
            document_id = task["entity_id"]
            metadata = metadata_by_id[document_id]
            result, status = accepted_result(metadata, response or {}) if response else (None, "")
            audit: dict[str, Any] = {
                "document_id": document_id,
                "task": task["task"],
                "query": task_query(task, metadata),
                "accepted": bool(result),
                "status_detected": status,
                "error": str(error) if error else "",
                "usage": (response or {}).get("usage", {}),
                "results": (response or {}).get("results", []),
            }
            if result:
                evidence = json.dumps(result, ensure_ascii=False, sort_keys=True)
                result_url = str(result.get("url", ""))
                result_title = clean(result.get("title"))
                if task["task"] == "verify_legal_status" and status:
                    metadata["status_checked_at"] = dt.date.today().isoformat()
                    metadata["status_filter"] = status
                    metadata["tinh_trang_hieu_luc"] = status
                    metadata["official_status_url"] = result_url
                    metadata["official_status_result_title"] = result_title
                    metadata["official_status_evidence_sha256"] = hashlib.sha256(evidence.encode()).hexdigest()
                    metadata["official_status_verified_at"] = dt.datetime.now(dt.UTC).isoformat()
                    audit["applied"] = "legal_status"
                elif task["task"] == "verify_or_replace_content_html":
                    metadata["official_content_candidate_url"] = result_url
                    metadata["official_content_candidate_sha256"] = hashlib.sha256(evidence.encode()).hexdigest()
                    audit["applied"] = "content_identity_candidate"
                else:
                    audit["applied"] = "evidence_only_no_explicit_status"
            audit_rows.append(audit)

    for field in EXTRA_FIELDS:
        if field not in metadata_fields:
            metadata_fields.append(field)
    write_csv(args.output_dir / "metadata.csv", metadata_rows, metadata_fields)
    merged_audits = {
        (str(row["document_id"]), str(row["task"])): row for row in existing_audit_rows
    }
    merged_audits.update({(str(row["document_id"]), str(row["task"])): row for row in audit_rows})
    cumulative_audits = sorted(
        merged_audits.values(), key=lambda row: (str(row["document_id"]), str(row["task"]))
    )
    with (args.output_dir / "tavily_evidence.jsonl").open("w", encoding="utf-8") as handle:
        for row in cumulative_audits:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    applied_status = sum(row.get("applied") == "legal_status" for row in audit_rows)
    applied_content = sum(row.get("applied") == "content_identity_candidate" for row in audit_rows)
    errors = sum(bool(row["error"]) for row in audit_rows)
    credits = sum(int((row.get("usage") or {}).get("credits", 0) or 0) for row in audit_rows)
    report = {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "source_dir": str(args.source_dir),
        "output_dir": str(args.output_dir),
        "requests": len(audit_rows),
        "credits_reported": credits,
        "search_depth": args.search_depth,
        "status_updates_applied": applied_status,
        "content_identity_candidates_recorded": applied_content,
        "errors": errors,
        "batch_start_offset": args.start_offset,
        "audit_records_cumulative": len(cumulative_audits),
        "unprocessed_eligible_tasks": max(0, total_tasks - args.start_offset - len(tasks)),
    }
    (args.output_dir / "TAVILY_ENRICHMENT_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
