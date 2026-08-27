#!/usr/bin/env python3
"""Read every canonical HTML document and add evidence-grounded legal edges.

The deterministic pass reads all documents. A language model is called only for
documents containing legal-relation signals, and it receives bounded snippets
with offsets. Its output is accepted only when the returned evidence quote is
an exact substring and its cited signature resolves to a canonical document.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

csv.field_size_limit(sys.maxsize)
PIPELINE_ROOT = Path(__file__).resolve().parents[1] / "pipeline"
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))
from data_pipeline.canonical import normalize_html  # noqa: E402

load_dotenv()

RELATION_TYPES = {
    "Căn cứ",
    "Dẫn chiếu",
    "Sửa đổi, bổ sung",
    "Thay thế",
    "Bãi bỏ",
    "Hướng dẫn",
    "Quy định chi tiết",
    "Hợp nhất",
}
SIGNAL_RE = re.compile(
    r"\b(?:căn cứ|dẫn chiếu|sửa đổi|bổ sung|thay thế|bãi bỏ|hướng dẫn(?: thi hành)?|"
    r"quy định chi tiết|hợp nhất)\b",
    re.IGNORECASE,
)
SIGNATURE_RE = re.compile(
    r"(?:số\s*)?\d{1,4}(?:[./-]\d{2,4})?(?:[./-](?:QĐ|NQ|TT|NĐ|CT|LT|L|CV)[\w./-]*)+",
    re.IGNORECASE,
)
EXTRA_RELATION_FIELDS = (
    "relation_confidence",
    "relation_status",
    "evidence_text",
    "evidence_start",
    "evidence_end",
    "evidence_sha256",
    "target_signature",
    "target_resolution",
    "scope",
    "effective_date_text",
    "model_name",
    "model_prompt_sha256",
)
RELATION_CUE_RE = {
    "Căn cứ": re.compile(r"\bcăn cứ\b", re.IGNORECASE),
    "Dẫn chiếu": re.compile(r"\bdẫn chiếu\b", re.IGNORECASE),
    "Sửa đổi, bổ sung": re.compile(r"\b(?:sửa đổi|bổ sung)\b", re.IGNORECASE),
    "Thay thế": re.compile(r"\bthay thế\b", re.IGNORECASE),
    "Bãi bỏ": re.compile(r"\bbãi bỏ\b", re.IGNORECASE),
    "Hướng dẫn": re.compile(r"\bhướng dẫn\b", re.IGNORECASE),
    "Quy định chi tiết": re.compile(r"\bquy định chi tiết\b", re.IGNORECASE),
    "Hợp nhất": re.compile(r"\bhợp nhất\b", re.IGNORECASE),
}


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def identity(value: Any) -> str:
    folded = unicodedata.normalize("NFKD", clean(value).casefold())
    return "".join(char for char in folded if char.isalnum())


def signature_variants(value: str) -> set[str]:
    normalized = identity(value)
    return {normalized, normalized.removeprefix("so")} - {""}


def source_snippets(text: str, *, width: int = 2600, maximum: int = 8) -> list[dict[str, Any]]:
    spans: list[tuple[int, int]] = []
    for match in SIGNAL_RE.finditer(text):
        start = max(0, match.start() - width // 3)
        end = min(len(text), match.end() + width * 2 // 3)
        if spans and start <= spans[-1][1] + 300:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    return [
        {"start": start, "end": end, "text": text[start:end]}
        for start, end in spans[:maximum]
    ]


def prompt_for(document: dict[str, str], snippets: list[dict[str, Any]]) -> str:
    payload = {
        "source_document": {
            "id": document["id"],
            "title": document.get("title", ""),
            "signature": document.get("so_ky_hieu", ""),
        },
        "snippets": snippets,
    }
    return """Bạn là chuyên gia pháp lý Việt Nam trích xuất quan hệ giữa văn bản.
Chỉ dùng đúng các snippets được cung cấp. Trả về JSON object có khóa `relations`
là mảng. Mỗi phần tử phải có: relation_type (một trong Căn cứ, Dẫn chiếu,
Sửa đổi, bổ sung, Thay thế, Bãi bỏ, Hướng dẫn, Quy định chi tiết, Hợp nhất),
target_signature (số/ký hiệu được ghi trong snippet), evidence_quote (trích
nguyên văn ngắn từ snippet), scope (toàn bộ|một phần|không rõ),
effective_date_text (hoặc rỗng), confidence (0..1). Không suy diễn, không tạo
target nếu không nhìn thấy số/ký hiệu. Với 'Căn cứ', hướng là source_document
tham chiếu target. Với 'Bãi bỏ/Thay thế/Sửa đổi', hướng là source_document tác
động target. Nếu không có quan hệ, trả `{"relations":[]}`.

INPUT:\n""" + json.dumps(payload, ensure_ascii=False)


def model_relations(client: OpenAI, model: str, document: dict[str, str], snippets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    prompt = prompt_for(document, snippets)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Return only valid JSON. Do not use markdown."},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    content = response.choices[0].message.content or "{}"
    parsed = json.loads(content)
    return list(parsed.get("relations", [])) if isinstance(parsed, dict) else [], hashlib.sha256(prompt.encode()).hexdigest()


def atomic_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("data/clean/medical_active_v3"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/clean/medical_active_v4"))
    parser.add_argument("--model", default=os.getenv("RELATION_EXTRACTION_MODEL", "gpt-4.1-mini"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-model-calls", type=int, default=700)
    parser.add_argument("--start-offset", type=int, default=0, help="Zero-based offset in the deterministic work list.")
    parser.add_argument("--request-timeout-seconds", type=float, default=30.0)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required")

    with (args.source_dir / "metadata.csv").open(encoding="utf-8-sig", newline="") as handle:
        metadata_reader = csv.DictReader(handle)
        metadata = [dict(row) for row in metadata_reader]
    with (args.source_dir / "content.csv").open(encoding="utf-8-sig", newline="") as handle:
        content = {row["id"]: dict(row) for row in csv.DictReader(handle)}
    by_signature: dict[str, str] = {}
    for row in metadata:
        for variant in signature_variants(row.get("so_ky_hieu", "")):
            by_signature.setdefault(variant, row["id"])

    work: list[tuple[dict[str, str], str, list[dict[str, Any]]]] = []
    documents_read = 0
    for row in metadata:
        text = normalize_html(content[row["id"]]["content_html"])
        documents_read += 1
        snippets = source_snippets(text)
        if snippets:
            work.append((row, text, snippets))
    work.sort(key=lambda item: (item[0].get("retrieval_scope") != "seed_core", item[0]["id"]))
    total_signal_documents = len(work)
    work = work[args.start_offset : args.start_offset + args.max_model_calls]

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.source_dir, args.output_dir)
    # The API client otherwise retries/blocks for too long on a single slow
    # request, preventing an auditable batch from ever being committed.
    client = OpenAI(timeout=args.request_timeout_seconds, max_retries=0)
    evidence: list[dict[str, Any]] = []
    accepted: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []

    def execute(item: tuple[dict[str, str], str, list[dict[str, Any]]]) -> tuple[dict[str, str], str, list[dict[str, Any]], list[dict[str, Any]], str, str]:
        row, text, snippets = item
        try:
            relations, prompt_hash = model_relations(client, args.model, row, snippets)
            return row, text, snippets, relations, prompt_hash, ""
        except Exception as error:  # record model failure without fabricating edges
            return row, text, snippets, [], "", str(error)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(execute, item) for item in work]
        for future in as_completed(futures):
            source, text, snippets, relations, prompt_hash, error = future.result()
            for relation in relations:
                relation_type = clean(relation.get("relation_type"))
                target_signature = clean(relation.get("target_signature"))
                quote = clean(relation.get("evidence_quote"))
                confidence = float(relation.get("confidence", 0) or 0)
                target_id = next((by_signature[variant] for variant in signature_variants(target_signature) if variant in by_signature), "")
                quote_start = text.find(quote) if quote else -1
                cue_matches_quote = bool(RELATION_CUE_RE.get(relation_type, re.compile(r"$^", re.IGNORECASE)).search(quote))
                accepted_edge = (
                    relation_type in RELATION_TYPES
                    and bool(target_id)
                    and target_id != source["id"]
                    and quote_start >= 0
                    and cue_matches_quote
                    and confidence >= 0.75
                )
                record = {
                    "source_document_id": source["id"],
                    "target_document_id": target_id,
                    "relation_type": relation_type,
                    "target_signature": target_signature,
                    "evidence_quote": quote,
                    "evidence_start": quote_start,
                    "evidence_end": quote_start + len(quote) if quote_start >= 0 else -1,
                    "confidence": confidence,
                    "scope": clean(relation.get("scope")),
                    "effective_date_text": clean(relation.get("effective_date_text")),
                    "accepted": accepted_edge,
                    "model_prompt_sha256": prompt_hash,
                    "model": args.model,
                }
                evidence.append(record)
                if accepted_edge:
                    identity_value = "|".join((source["id"], target_id, relation_type, quote))
                    accepted.append({
                        "agent_category": source.get("agent_category", ""),
                        "doc_id": source["id"],
                        "other_doc_id": target_id,
                        "relationship": relation_type,
                        "source_is_selected": "true",
                        "target_is_selected": "true",
                        "relationship_is_adverse": str(relation_type in {"Bãi bỏ", "Thay thế"}).lower(),
                        "source_title": source.get("title", ""),
                        "target_title": next(row["title"] for row in metadata if row["id"] == target_id),
                        "relationship_id": hashlib.sha256(identity_value.encode()).hexdigest(),
                        "provenance_status": "model_grounded_v1",
                        "adverse_provenance": "model_grounded_exact_quote",
                        "source_row_hashes": "",
                        "original_edge_count": "1",
                        "relation_confidence": f"{confidence:.3f}",
                        "relation_status": "candidate_grounded",
                        "evidence_text": quote,
                        "evidence_start": str(quote_start),
                        "evidence_end": str(quote_start + len(quote)),
                        "evidence_sha256": hashlib.sha256(quote.encode()).hexdigest(),
                        "target_signature": target_signature,
                        "target_resolution": "canonical_signature_exact",
                        "scope": record["scope"],
                        "effective_date_text": record["effective_date_text"],
                        "model_name": args.model,
                        "model_prompt_sha256": prompt_hash,
                    })
                elif relation_type in RELATION_TYPES and target_signature:
                    unresolved.append({key: str(value) for key, value in record.items()})
            if error:
                evidence.append({"source_document_id": source["id"], "error": error, "accepted": False})

    with (args.source_dir / "relationships.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        relationship_rows = [dict(row) for row in reader]
        relationship_fields = list(reader.fieldnames or [])
    existing = {(row["doc_id"], row["other_doc_id"], row["relationship"]) for row in relationship_rows}
    new_edges = [row for row in accepted if (row["doc_id"], row["other_doc_id"], row["relationship"]) not in existing]
    for field in EXTRA_RELATION_FIELDS:
        if field not in relationship_fields:
            relationship_fields.append(field)
    atomic_csv(args.output_dir / "relationships.csv", relationship_rows + new_edges, relationship_fields)
    existing_evidence_path = args.source_dir / "model_relation_evidence.jsonl"
    existing_evidence = existing_evidence_path.read_text(encoding="utf-8") if existing_evidence_path.is_file() else ""
    with (args.output_dir / "model_relation_evidence.jsonl").open("w", encoding="utf-8") as handle:
        handle.write(existing_evidence)
        for row in sorted(evidence, key=lambda item: (str(item.get("source_document_id", "")), str(item.get("target_signature", "")))):
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    existing_unresolved_path = args.source_dir / "model_relation_unresolved.csv"
    existing_unresolved: list[dict[str, str]] = []
    if existing_unresolved_path.is_file():
        with existing_unresolved_path.open(encoding="utf-8-sig", newline="") as handle:
            existing_unresolved = [dict(row) for row in csv.DictReader(handle)]
    atomic_csv(
        args.output_dir / "model_relation_unresolved.csv",
        existing_unresolved + unresolved,
        ["source_document_id", "relation_type", "target_signature", "evidence_quote", "confidence", "scope", "effective_date_text", "accepted", "model_prompt_sha256", "model"],
    )
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "model": args.model,
        "documents_read": documents_read,
        "documents_with_relation_signals": total_signal_documents,
        "batch_start_offset": args.start_offset,
        "batch_documents_processed": len(work),
        "model_calls": len(work),
        "grounded_relation_candidates": len(accepted),
        "new_edges_added": len(new_edges),
        "unresolved_relation_candidates": len(unresolved),
    }
    (args.output_dir / "MODEL_RELATION_EXTRACTION_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
