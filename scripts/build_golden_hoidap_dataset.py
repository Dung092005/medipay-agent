"""Build a curated golden Q&A dataset from hoidap_detail_latest.json."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.scrape_bhxh_ground_truth import (  # noqa: E402
    INTERNAL_ANSWER_PATTERNS,
    PERSONAL_LOOKUP_PATTERNS,
    POLICY_ANSWER_SIGNALS,
    POLICY_QUESTION_SIGNALS,
    _contains_any,
    build_eval_record,
    clean_text,
    contains_obvious_pii,
    extract_legal_basis,
    normalize_question,
    redact_pii,
    temporal_risk,
)

SOURCE_FILE = PROJECT_ROOT / "hoidap_detail_latest.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "eval" / "golden_hoidap_v50.json"

TOPIC_QUOTAS: dict[str, int] = {
    "sickness_leave": 10,
    "maternity_eligibility": 10,
    "maternity_procedure": 8,
    "maternity_benefit_level": 8,
    "bhyt_related": 6,
    "other_policy": 8,
}

TOPIC_PATTERNS: dict[str, tuple[str, ...]] = {
    "bhyt_related": (
        "bhyt",
        "bảo hiểm y tế",
        "thẻ bh",
        "khám chữa bệnh",
        "trái tuyến",
        "đúng tuyến",
        "cấp cứu",
        "5 năm liên tục",
        "giá trị sử dụng thẻ",
        "đăng ký khám",
    ),
    "sickness_leave": (
        "ốm",
        "nghỉ ốm",
        "dưỡng sức",
        "phục hồi sức khỏe",
        "nghỉ việc hưởng",
        "bệnh dài ngày",
    ),
    "maternity_eligibility": (
        "điều kiện",
        "đủ điều kiện",
        "6 tháng",
        "12 tháng",
        "sinh non",
        "dưỡng thai",
        "con nuôi",
        "sinh đôi",
        "sinh ba",
    ),
    "maternity_benefit_level": (
        "mức hưởng",
        "trợ cấp",
        "lương cơ sở",
        "100%",
        "mức lương",
        "chi trả",
        "thanh toán",
    ),
    "maternity_procedure": (
        "hồ sơ",
        "thủ tục",
        "nộp",
        "giấy tờ",
        "thời hạn",
        "45 ngày",
        "10 ngày",
    ),
}


def classify_hoidap_record(question: str, answer: str) -> tuple[str, str]:
  """Filter personal/internal lookups; keep general policy Q&A only."""
  question = clean_text(question)
  answer = clean_text(answer)
  if len(question) < 25 or len(answer) < 120:
    return "rejected", "too_short"
  if len(question) > 1200 or len(answer) > 6000:
    return "rejected", "too_long"

  question_lower = question.casefold()
  answer_lower = answer.casefold()
  personal_hit = _contains_any(PERSONAL_LOOKUP_PATTERNS, question)
  internal_answer = _contains_any(INTERNAL_ANSWER_PATTERNS, answer)
  policy_question = any(signal in question_lower for signal in POLICY_QUESTION_SIGNALS)
  policy_answer = any(signal in answer_lower for signal in POLICY_ANSWER_SIGNALS)
  direct_lookup = any(
    signal in question_lower
    for signal in (
      "mã bhxh",
      "mã số bhxh",
      "số bhxh",
      "cccd",
      "cmnd",
      "mã hồ sơ",
      "tra cứu giúp",
      "kiểm tra giúp",
      "đã được duyệt chưa",
      "chưa nhận được tiền",
      "vssid chưa cập nhật",
      "trạng thái hồ sơ",
      "quá trình đóng của tôi",
      "quá trình tham gia của tôi",
    )
  )

  if internal_answer and not policy_answer:
    return "rejected", "requires_internal_bhxh_data"
  if direct_lookup:
    return "rejected", "personal_record_lookup"
  if personal_hit and not (policy_question or policy_answer):
    return "rejected", "personal_record_lookup"
  if not policy_question and not policy_answer:
    return "rejected", "not_policy_question"
  if not re.search(r"\b(?:luật|nghị định|thông tư|điều\s+\d+)\b", answer_lower):
    return "rejected", "no_legal_citation"
  if answer_lower.count("liên hệ") >= 2 and len(answer) < 250:
    return "rejected", "contact_only_answer"
  return "accepted", "general policy question with legal citation"


def infer_topic(question: str, answer: str) -> str:
  text = f"{question} {answer}".casefold()
  scores: dict[str, int] = {}
  for topic, patterns in TOPIC_PATTERNS.items():
    scores[topic] = sum(1 for pattern in patterns if pattern in text)
  best = max(scores.items(), key=lambda item: item[1])
  return best[0] if best[1] > 0 else "other_policy"


def quality_score(question: str, answer: str) -> float:
  legal_basis = extract_legal_basis(answer)
  score = 0.0
  score += min(len(legal_basis), 8) * 4.0
  score += min(len(answer), 2500) / 120.0
  score += min(len(question), 600) / 60.0
  if re.search(r"\b(?:Luật|Nghị định|Thông tư)\b", answer, re.IGNORECASE):
    score += 8.0
  if re.search(r"\bĐiều\s+\d+", answer, re.IGNORECASE):
    score += 4.0
  if _contains_any(INTERNAL_ANSWER_PATTERNS, answer):
    score -= 6.0
  if "1900" in answer or "19009068" in answer:
    score -= 1.0
  return score


def map_source_record(raw: dict[str, object]) -> dict[str, str]:
  item_id = str(raw.get("item_id") or "").strip()
  question = clean_text(str(raw.get("question_full") or raw.get("title_detail") or ""))
  answer = clean_text(str(raw.get("answer_full") or ""))
  answer = re.sub(r"^Bảo hiểm xã hội Việt Nam trả lời\s*:?\s*", "", answer, flags=re.IGNORECASE)
  answer = re.sub(r"\s*BHXH Việt Nam trả lời\s*$", "", answer, flags=re.IGNORECASE)
  return {
    "id": f"BHXH-QA-{item_id}" if item_id else "",
    "source_item_id": item_id,
    "title": clean_text(str(raw.get("title_detail") or "")),
    "question": question,
    "ground_truth": answer,
    "official_answer": answer,
    "category": clean_text(str(raw.get("category") or "Ốm đau, thai sản")),
    "submitted_at": clean_text(str(raw.get("sent_date_detail") or raw.get("sent_date") or "")),
    "answered_at": clean_text(str(raw.get("reply_date") or "")),
    "status": clean_text(str(raw.get("status_detail") or raw.get("status") or "")),
    "source_url": clean_text(str(raw.get("detail_url") or "")),
  }


def build_classification_reason(topic: str, legal_count: int) -> str:
  labels = {
    "sickness_leave": "Câu hỏi chế độ ốm đau/dưỡng sức có viện dẫn pháp lý.",
    "maternity_eligibility": "Câu hỏi điều kiện hưởng thai sản có trích Điều/Khoản.",
    "maternity_benefit_level": "Câu hỏi mức hưởng/trợ cấp có căn cứ luật.",
    "maternity_procedure": "Câu hỏi hồ sơ/thủ tục/thời hạn giải quyết.",
    "bhyt_related": "Câu hỏi liên quan BHYT/KCB có quy định chung.",
    "other_policy": "Câu hỏi chính sách chung có trích dẫn pháp lý.",
  }
  return f"{labels.get(topic, labels['other_policy'])} ({legal_count} legal refs)"


def select_records(candidates: list[dict[str, object]], target: int) -> list[dict[str, object]]:
  by_topic: dict[str, list[dict[str, object]]] = {topic: [] for topic in TOPIC_QUOTAS}
  for item in candidates:
    by_topic.setdefault(str(item["topic"]), []).append(item)

  selected: list[dict[str, object]] = []
  used_questions: set[str] = set()
  used_ids: set[str] = set()

  def pick_from(topic: str, quota: int) -> None:
    pool = sorted(by_topic.get(topic, []), key=lambda row: float(row["score"]), reverse=True)
    for row in pool:
      if len([item for item in selected if item["topic"] == topic]) >= quota:
        break
      question_key = normalize_question(str(row["record"]["question"]))
      source_id = str(row["record"]["source_item_id"])
      if question_key in used_questions or source_id in used_ids:
        continue
      used_questions.add(question_key)
      used_ids.add(source_id)
      selected.append(row)

  for topic, quota in TOPIC_QUOTAS.items():
    pick_from(topic, quota)

  if len(selected) < target:
    remainder = sorted(candidates, key=lambda row: float(row["score"]), reverse=True)
    for row in remainder:
      if len(selected) >= target:
        break
      question_key = normalize_question(str(row["record"]["question"]))
      source_id = str(row["record"]["source_item_id"])
      if question_key in used_questions or source_id in used_ids:
        continue
      used_questions.add(question_key)
      used_ids.add(source_id)
      selected.append(row)

  return selected[:target]


def build_dataset(source_path: Path, output_path: Path, *, count: int = 50) -> dict[str, object]:
  raw_records = json.loads(source_path.read_text(encoding="utf-8"))
  if not isinstance(raw_records, list):
    raise ValueError("Source file must be a JSON array")

  stats = Counter()
  candidates: list[dict[str, object]] = []

  for raw in raw_records:
    if not isinstance(raw, dict):
      continue
    mapped = map_source_record(raw)
    status = mapped.get("status", "").casefold()
    if status and "chưa trả lời" in status:
      stats["unanswered"] += 1
      continue

    decision, reason = classify_hoidap_record(mapped["question"], mapped["official_answer"])
    stats[decision] += 1
    if decision != "accepted":
      stats[f"reject:{reason}"] += 1
      continue

    topic = infer_topic(mapped["question"], mapped["official_answer"])
    score = quality_score(mapped["question"], mapped["official_answer"])
    candidates.append({"record": mapped, "topic": topic, "score": score, "reason": reason})

  selected = select_records(candidates, count + 10)
  if len(selected) < count:
    raise RuntimeError(f"Only selected {len(selected)} records; need {count}")

  records: list[dict[str, object]] = []
  stats_counter = Counter(stats)
  for item in selected:
    mapped = item["record"]
    assert isinstance(mapped, dict)
    topic = str(item["topic"])
    answer = clean_text(str(mapped["official_answer"]))
    legal_basis = extract_legal_basis(answer)
    eval_record = build_eval_record(
      {
        **mapped,
        "category": topic,
        "official_answer": answer,
      },
      build_classification_reason(topic, len(legal_basis)),
    )
    eval_record["reference"] = eval_record["official_answer"]
    eval_record["review_status"] = "candidate_gold"
    eval_record["legal_basis"] = legal_basis
    eval_record["temporal_risk"] = temporal_risk(str(mapped.get("answered_at", "")), answer)
    eval_record["question"] = redact_pii(str(eval_record["question"]))
    if contains_obvious_pii(str(eval_record["question"])):
      stats_counter["pii_skipped"] += 1
      continue
    records.append(eval_record)
    if len(records) >= count:
      break

  if len(records) < count:
    raise RuntimeError(f"Only built {len(records)} records after PII filtering; need {count}")

  payload = {
    "metadata": {
      "schema_version": "1.0",
      "dataset_name": "MediPay golden hoidap v50",
      "source_file": source_path.name,
      "record_count": len(records),
      "gold_status": "candidate",
      "active_release": "snapshot-5dfc6bb64d046a1c",
      "created_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
      "notes": "50 câu chính sách chung từ BHXH hoidap; đã mask PII; reference chưa human-reviewed.",
    },
    "records": records,
    "statistics": {
      "source_total": len(raw_records),
      "accepted_pool": len(candidates),
      "selection_pool_stats": dict(stats_counter),
      "by_category": dict(Counter(str(record["category"]) for record in records)),
      "by_temporal_risk": dict(Counter(str(record["temporal_risk"]) for record in records)),
    },
  }

  output_path.parent.mkdir(parents=True, exist_ok=True)
  output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  jsonl_path = output_path.with_suffix(".jsonl")
  with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
    for record in records:
      handle.write(json.dumps(record, ensure_ascii=False) + "\n")

  return payload


def main() -> None:
  parser = argparse.ArgumentParser(description="Build golden hoidap dataset")
  parser.add_argument("--source", type=Path, default=SOURCE_FILE)
  parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
  parser.add_argument("--count", type=int, default=50)
  args = parser.parse_args()

  payload = build_dataset(args.source, args.output, count=args.count)
  print(json.dumps(payload["statistics"], ensure_ascii=False, indent=2))
  print(f"Wrote {args.output} and {args.output.with_suffix('.jsonl')}")


if __name__ == "__main__":
  main()
