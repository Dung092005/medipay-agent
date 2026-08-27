"""Collect answered BHXH Việt Nam Q&A pages into a ground-truth JSON file.

The source is an ASP.NET/SharePoint-rendered site.  This script intentionally
uses only the standard library so the parser and validator can run offline
after the JSON artifact has been collected.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = (
    "https://baohiemxahoi.gov.vn/"
    "chuyen-trang-bhxh-bhyt/tu-van-hoi-dap/Pages/default.aspx"
)
LIST_BASE_URL = "https://baohiemxahoi.gov.vn/hoidap/Pages/default.aspx"
DEFAULT_START_ID = 481_068
DEFAULT_OUTPUT = Path("data/bhxh_ground_truth_200.json")
USER_AGENT = "Al-20k-BHXH-GroundTruth/1.0"
READER_BASE_URL = "https://r.jina.ai/http://"
READER_MIN_INTERVAL_SECONDS = 2.0
READER_429_BACKOFF_SECONDS = 15.0
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
DEFAULT_CATEGORIES = (3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
DEFAULT_PAGE_FRACTIONS = (1.0, 0.75, 0.50, 0.35, 0.20)
BHYT_CATEGORY = "Bảo hiểm y tế"
DEFAULT_BHYT_CATEGORY_ID = 6
DEFAULT_EVAL_OUTPUT_DIR = Path("data/eval")
DEFAULT_GOOD_OUTPUT = DEFAULT_EVAL_OUTPUT_DIR / "bhyt_good_candidates.json"
DEFAULT_REVIEW_OUTPUT = DEFAULT_EVAL_OUTPUT_DIR / "bhyt_needs_review.json"
DEFAULT_REJECTED_OUTPUT = DEFAULT_EVAL_OUTPUT_DIR / "bhyt_rejected.json"
REDACTED = "[REDACTED]"
PREFER_READER_HTML = False
_reader_rate_lock = threading.Lock()
_reader_next_request_at = 0.0
FALLBACK_CATEGORY_COUNTS = {
    3: 6839,
    4: 225,
    5: 969,
    6: 4365,
    7: 1461,
    8: 3280,
    9: 4622,
    10: 5955,
    11: 2821,
    12: 14916,
}

PERSONAL_LOOKUP_PATTERNS = (
    re.compile(r"\bmã\s*(?:số\s*)?bhxh\b", re.IGNORECASE),
    re.compile(r"\bsố\s*bhxh\b", re.IGNORECASE),
    re.compile(r"\b(?:cccd|cmnd|căn cước công dân)\b", re.IGNORECASE),
    re.compile(r"\b(?:số|mã)\s*(?:thẻ|hồ sơ)\b", re.IGNORECASE),
    re.compile(r"\btra cứu giúp\b", re.IGNORECASE),
    re.compile(r"\bkiểm tra giúp\b", re.IGNORECASE),
    re.compile(r"\bđã\s+(?:được\s+)?duyệt\s+chưa\b", re.IGNORECASE),
    re.compile(r"\bchưa\s+nhận\s+được\s+tiền\b", re.IGNORECASE),
    re.compile(r"\bvssid\s+(?:chưa\s+)?cập nhật\b", re.IGNORECASE),
    re.compile(r"\btrạng thái\s+hồ sơ\b", re.IGNORECASE),
    re.compile(r"\bquá trình\s+(?:tham gia|đóng)\s+(?:của\s+)?tôi\b", re.IGNORECASE),
    re.compile(r"\bbao giờ\s+tiền\s+(?:về|được\s+chuyển)\b", re.IGNORECASE),
    re.compile(r"\bthẻ\s+(?:bhyt\s+)?của\s+tôi\s+còn\s+hạn\b", re.IGNORECASE),
)
INTERNAL_ANSWER_PATTERNS = (
    re.compile(r"\btra cứu\b.{0,80}\bcơ sở dữ liệu\b", re.IGNORECASE),
    re.compile(r"\brà soát\b.{0,80}\bcơ sở dữ liệu\b", re.IGNORECASE),
    re.compile(r"\bcập nhật\b.{0,80}\bvssid\b", re.IGNORECASE),
    re.compile(r"\btheo thông tin (?:ông|bà) cung cấp\b", re.IGNORECASE),
)
POLICY_QUESTION_SIGNALS = (
    "điều kiện",
    "quyền lợi",
    "mức hưởng",
    "hưởng bhyt",
    "trái tuyến",
    "đúng tuyến",
    "cấp cứu",
    "5 năm liên tục",
    "chi trả",
    "thanh toán",
    "thuốc",
    "giá trị sử dụng",
    "thời hạn thẻ",
    "đối tượng tham gia",
    "phạm vi hưởng",
    "tuyến khám",
    "đăng ký khám",
    "khám chữa bệnh",
)
POLICY_ANSWER_SIGNALS = (
    "luật",
    "nghị định",
    "thông tư",
    "quyết định",
    "điều ",
    "khoản ",
    "quy định",
    "mức hưởng",
    "bảo hiểm y tế",
)
LEGAL_DOCUMENT_PATTERNS = (
    ("decree", re.compile(r"\bNghị định\s+(?:số\s+)?([0-9]+/[0-9]{4}/NĐ-CP)\b", re.IGNORECASE)),
    ("circular", re.compile(r"\bThông tư\s+(?:số\s+)?([0-9]+/[0-9]{4}/TT-[A-ZĐ]+)\b", re.IGNORECASE)),
    ("decision", re.compile(r"\bQuyết định\s+(?:số\s+)?([0-9]+/[0-9]{4}/QĐ-[A-ZĐ]+)\b", re.IGNORECASE)),
    ("law", re.compile(r"\bLuật(?:\s+(?:số\s+)?([0-9]+/[0-9]{4}/QH[0-9]+))?\b", re.IGNORECASE)),
)
ARTICLE_PATTERN = re.compile(r"\bĐiều\s+(\d+[A-Za-z]?)\b", re.IGNORECASE)
CLAUSE_PATTERN = re.compile(r"\bKhoản\s+(\d+[A-Za-z]?)\b", re.IGNORECASE)
POINT_PATTERN = re.compile(r"\bĐiểm\s+([a-zđ])\b", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?84|0)(?:[\s.-]?\d){8,10}(?!\d)")
LONG_NUMBER_PATTERN = re.compile(r"(?<!\d)\d{9,12}(?!\d)")
NAME_CONTEXT_PATTERN = re.compile(
    r"(?P<prefix>\b(?:tôi\s+là|tên\s+tôi\s+là|họ\s+tên\s+là)\s+)"
    r"(?P<name>[A-ZÀ-ỴĐ][A-Za-zÀ-ỹĐđ]*(?:\s+[A-ZÀ-ỴĐ][A-Za-zÀ-ỹĐđ]*){1,4})",
    re.IGNORECASE,
)
IDENTIFIER_PATTERN = re.compile(
    r"(?P<label>\b(?:mã\s*(?:số\s*)?(?:bhxh|bhyt)|số\s*(?:bhxh|thẻ|tài khoản)|"
    r"mã\s*(?:thẻ|hồ sơ)|(?:cccd|cmnd|căn cước công dân))\b\s*[:#-]?\s*)"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9./-]{5,})",
    re.IGNORECASE,
)


class Node:
    """Small HTML tree node sufficient for the stable source page structure."""

    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: Node | None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[Node | str] = []
        self.parent = parent

    def text_content(self) -> str:
        pieces: list[str] = []
        for child in self.children:
            if isinstance(child, Node):
                if child.tag in {"br", "p", "div", "li", "tr", "td", "th"}:
                    pieces.append(" ")
                pieces.append(child.text_content())
            else:
                pieces.append(child)
        return "".join(pieces)


class TreeParser(HTMLParser):
    """Parse enough malformed HTML to reliably inspect the target containers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {}, None)
        self.stack: list[Node] = [self.root]
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_attrs = {key.lower(): value or "" for key, value in attrs}
        node = Node(tag.lower(), normalized_attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() in {"script", "style", "noscript"}:
            self.skip_depth += 1
        if tag.lower() not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == normalized_tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and data:
            self.stack[-1].children.append(data)


def clean_text(value: str) -> str:
    value = value.replace("\xa0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", value).strip()


def has_class(node: Node, class_name: str) -> bool:
    return class_name in node.attrs.get("class", "").split()


def find_all(node: Node, predicate: Callable[[Node], bool]) -> Iterable[Node]:
    for child in node.children:
        if isinstance(child, Node):
            if predicate(child):
                yield child
            yield from find_all(child, predicate)


def first_node(node: Node, predicate: Callable[[Node], bool]) -> Node | None:
    return next(iter(find_all(node, predicate)), None)


def parse_field(text: str, label: str, following_labels: tuple[str, ...]) -> str:
    end = "|".join(re.escape(item) for item in following_labels)
    match = re.search(
        rf"{re.escape(label)}\s*(.*?)(?=\s*(?:{end})|$)",
        text,
        flags=re.IGNORECASE,
    )
    return clean_text(match.group(1)) if match else ""


def normalize_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\W+", " ", normalized, flags=re.UNICODE).strip()


def normalize_question(value: str) -> str:
    """Normalize question text for primary deduplication."""
    return normalize_key(value)


def redact_pii(text: str) -> str:
    """Redact common personal identifiers without changing the question's intent."""
    redacted = EMAIL_PATTERN.sub(REDACTED, text or "")
    redacted = NAME_CONTEXT_PATTERN.sub(lambda match: f"{match.group('prefix')}{REDACTED}", redacted)
    redacted = IDENTIFIER_PATTERN.sub(lambda match: f"{match.group('label')}{REDACTED}", redacted)
    redacted = PHONE_PATTERN.sub(REDACTED, redacted)
    redacted = LONG_NUMBER_PATTERN.sub(REDACTED, redacted)
    return clean_text(redacted)


def _legal_entry(
    legal_type: str,
    document_number: str = "",
    article: str = "",
    clause: str = "",
    point: str = "",
) -> dict[str, str]:
    return {
        "type": legal_type,
        "document_number": document_number,
        "article": article,
        "clause": clause,
        "point": point,
    }


def extract_legal_basis(answer: str) -> list[dict[str, str]]:
    """Extract only legal references explicitly present in an official answer."""
    text = clean_text(answer)
    if not text:
        return []

    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for legal_type, pattern in LEGAL_DOCUMENT_PATTERNS:
        for match in pattern.finditer(text):
            document_number = match.group(1) or ""
            entry = _legal_entry(legal_type, document_number=document_number)
            key = tuple(entry.values())
            if key not in seen:
                seen.add(key)
                entries.append(entry)

    provision_matches = list(ARTICLE_PATTERN.finditer(text))
    for article_match in provision_matches:
        window_start = max(0, article_match.start() - 60)
        window = text[window_start : article_match.end() + 20]
        clause_match = CLAUSE_PATTERN.search(window)
        point_match = POINT_PATTERN.search(window)
        entry = _legal_entry(
            "unknown",
            article=article_match.group(1),
            clause=clause_match.group(1) if clause_match else "",
            point=point_match.group(1) if point_match else "",
        )
        key = tuple(entry.values())
        if key not in seen:
            seen.add(key)
            entries.append(entry)

    if not entries:
        # A named law without a document number is still an explicit reference.
        if re.search(r"\bLuật\s+bảo hiểm y tế\b", text, re.IGNORECASE):
            entries.append(_legal_entry("law"))
    return entries


def temporal_risk(answered_at: str, answer: str) -> str:
    """Assign a review signal based on answer age and old regulation references."""
    match = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", answered_at or "")
    if not match:
        return "medium"
    answered_date = datetime(int(match.group(3)), int(match.group(2)), int(match.group(1))).date()
    age_years = (datetime.now(UTC).date() - answered_date).days / 365.25
    if age_years > 5:
        return "high"
    if age_years > 2:
        return "medium"
    if re.search(r"\b(?:Luật|Nghị định|Thông tư|Quyết định).{0,80}/20(?:0[0-9]|1[0-9])\b", answer or "", re.IGNORECASE):
        return "medium"
    return "low"


def _contains_any(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def classify_record(record: dict[str, str]) -> tuple[str, str]:
    """Classify one parsed Q&A record without using an LLM or private data."""
    category = clean_text(record.get("category", ""))
    if category.strip().lower() != BHYT_CATEGORY.lower():
        return "rejected", "wrong_category"

    question = clean_text(record.get("question", ""))
    answer = clean_text(record.get("official_answer") or record.get("ground_truth", ""))
    if not question:
        return "rejected", "missing_question"
    if not answer:
        return "rejected", "missing_answer"

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

    if internal_answer:
        if policy_question:
            return "needs_review", "mixed_personal_and_policy_question"
        return "rejected", "requires_internal_bhxh_data"
    if direct_lookup:
        return "rejected", "personal_record_lookup"
    if personal_hit:
        if policy_question or policy_answer:
            return "needs_review", "mixed_personal_and_policy_question"
        return "rejected", "personal_record_lookup"
    if not policy_question and not policy_answer:
        return "rejected", "not_policy_question"
    return "accepted", "general BHYT policy question answerable from public legal corpus"


def build_eval_record(record: dict[str, str], reason: str) -> dict[str, object]:
    """Map a parser record to the candidate schema used by all three outputs."""
    answer = clean_text(record.get("official_answer") or record.get("ground_truth", ""))
    return {
        "id": record.get("id", ""),
        "source_item_id": record.get("source_item_id", ""),
        "question": redact_pii(record.get("question", "")),
        "official_answer": answer,
        "category": clean_text(record.get("category", "")),
        "submitted_at": record.get("submitted_at", ""),
        "answered_at": record.get("answered_at", ""),
        "source_url": record.get("source_url", ""),
        "legal_basis": extract_legal_basis(answer),
        "temporal_risk": temporal_risk(record.get("answered_at", ""), answer),
        "review_status": "pending",
        "classification_reason": reason,
    }


def extract_record(html: str, item_id: int, require_answer: bool = True) -> dict[str, str] | None:
    parser = TreeParser()
    parser.feed(html)
    parser.close()
    root = parser.root

    content_nodes = list(
        find_all(
            root,
            lambda node: node.tag == "div" and has_class(node, "item-col-hoidap"),
        )
    )
    question = ""
    answer = ""
    for node in content_nodes:
        text = clean_text(node.text_content())
        question_match = re.match(r"Nội dung câu hỏi\s*:\s*(.*)$", text, re.IGNORECASE)
        answer_match = re.match(r"Câu trả lời\s*:\s*(.*)$", text, re.IGNORECASE)
        if question_match and not question:
            question = clean_text(question_match.group(1))
        elif answer_match and not answer:
            answer = clean_text(answer_match.group(1))

    if not question and not answer:
        return None

    item_blocks = list(
        find_all(root, lambda node: node.tag == "div" and has_class(node, "item-vanban"))
    )
    question_block = next(
        (block for block in item_blocks if "Nội dung câu hỏi" in clean_text(block.text_content())),
        None,
    )
    answer_block = next(
        (block for block in item_blocks if "Câu trả lời" in clean_text(block.text_content())),
        None,
    )
    question_block_text = clean_text(question_block.text_content()) if question_block else ""
    answer_block_text = clean_text(answer_block.text_content()) if answer_block else ""

    status = parse_field(
        question_block_text,
        "Trạng thái:",
        ("Nội dung câu hỏi:",),
    )
    if require_answer and (not question or not answer or "chưa trả lời" in status.casefold()):
        return None

    title_node = first_node(root, lambda node: node.tag == "div" and has_class(node, "item-vanban-head"))
    title = clean_text(title_node.text_content()) if title_node else ""
    submitted_at = parse_field(
        question_block_text,
        "Ngày gửi:",
        ("Lĩnh vực:", "Trạng thái:", "Nội dung câu hỏi:"),
    )
    category = parse_field(
        question_block_text,
        "Lĩnh vực:",
        ("Trạng thái:", "Nội dung câu hỏi:"),
    )
    answered_at = parse_field(
        answer_block_text,
        "Ngày trả lời:",
        ("File đính kèm:", "Câu trả lời:"),
    )

    return {
        "id": f"BHXH-QA-{item_id}",
        "source_item_id": str(item_id),
        "title": title,
        "question": question,
        "ground_truth": answer,
        "category": category,
        "submitted_at": submitted_at,
        "answered_at": answered_at,
        "status": status or "Đã trả lời",
        "source_url": f"{BASE_URL}?ItemID={item_id}",
    }


def reader_url(url: str) -> str:
    if url.startswith("https://"):
        return READER_BASE_URL + url[len("https://") :]
    if url.startswith("http://"):
        return READER_BASE_URL + url[len("http://") :]
    raise ValueError(f"Unsupported URL for Reader fallback: {url}")


def _wait_for_reader_slot() -> None:
    global _reader_next_request_at
    with _reader_rate_lock:
        now = time.monotonic()
        delay = max(0.0, _reader_next_request_at - now)
        _reader_next_request_at = max(now, _reader_next_request_at) + READER_MIN_INTERVAL_SECONDS
    if delay:
        time.sleep(delay)


def fetch_reader_html(url: str, timeout: int = 30, retries: int = 2) -> str:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            _wait_for_reader_slot()
            request = Request(
                reader_url(url),
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "text/html",
                    "X-Engine": "direct",
                    "X-Respond-With": "html",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt < retries:
                if isinstance(error, HTTPError) and error.code == 429:
                    time.sleep(READER_429_BACKOFF_SECONDS)
                else:
                    time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"Could not fetch raw HTML through Reader for {url}: {last_error}")


def fetch_url(url: str, retries: int = 1, timeout: int = 10) -> str:
    if PREFER_READER_HTML:
        return fetch_reader_html(url)

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
            with urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except HTTPError as error:
            last_error = error
            if error.code not in RETRYABLE_STATUS_CODES:
                raise
        except (URLError, TimeoutError, OSError) as error:
            last_error = error
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))

    try:
        return fetch_reader_html(url)
    except Exception as reader_error:
        raise RuntimeError(
            f"Could not fetch {url} directly ({last_error}) or through Reader ({reader_error})"
        ) from reader_error


def fetch_item(
    item_id: int,
    require_answer: bool = True,
) -> tuple[int, dict[str, str] | None, str | None]:
    url = f"{BASE_URL}?ItemID={item_id}"
    try:
        return item_id, extract_record(fetch_url(url), item_id, require_answer=require_answer), None
    except Exception as error:  # Keep one bad item from stopping the bounded crawl.
        return item_id, None, str(error)


def extract_item_ids(html: str) -> list[int]:
    """Extract the seven main result IDs from a category page, preserving order."""
    ids: list[int] = []
    for raw_id in re.findall(r'href=["\']\?ItemID=(\d+)["\']', html, flags=re.IGNORECASE):
        item_id = int(raw_id)
        if item_id not in ids:
            ids.append(item_id)
    return ids


def extract_answered_item_ids(html: str) -> list[int]:
    """Extract only detail links whose category-list block says ``Đã trả lời``."""
    parser = TreeParser()
    parser.feed(html)
    parser.close()
    ids: list[int] = []
    for block in find_all(
        parser.root,
        lambda node: node.tag == "div" and has_class(node, "item-vanban"),
    ):
        block_text = clean_text(block.text_content())
        if not re.search(r"\bĐã trả lời\b", block_text, flags=re.IGNORECASE):
            continue
        if re.search(r"\bChưa trả lời\b", block_text, flags=re.IGNORECASE):
            continue
        for anchor in find_all(block, lambda node: node.tag == "a"):
            match = re.search(r"(?:[?&]ItemID=)(\d+)", anchor.attrs.get("href", ""), flags=re.IGNORECASE)
            if match:
                item_id = int(match.group(1))
                if item_id not in ids:
                    ids.append(item_id)
    return ids


def category_count(category_id: int) -> int:
    try:
        html = fetch_url(f"{LIST_BASE_URL}?CateID={category_id}")
        match = re.search(r"Danh sách (?:tìm kiếm|câu hỏi)\s*\((\d+)\)", html, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return FALLBACK_CATEGORY_COUNTS[category_id]


def collect_candidate_ids(
    categories: tuple[int, ...] = DEFAULT_CATEGORIES,
    page_fractions: tuple[float, ...] = DEFAULT_PAGE_FRACTIONS,
    workers: int = 3,
) -> list[int]:
    """Sample older category pages so the crawl avoids the newest unanswered items."""
    counts: dict[int, int] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        count_futures = {executor.submit(category_count, category_id): category_id for category_id in categories}
        for future in as_completed(count_futures):
            category_id = count_futures[future]
            try:
                counts[category_id] = future.result()
            except Exception:
                counts[category_id] = FALLBACK_CATEGORY_COUNTS[category_id]
    page_requests: list[tuple[int, int]] = []
    for category_id in categories:
        page_count = max(1, math.ceil(counts[category_id] / 7))
        pages: list[int] = []
        for fraction in page_fractions:
            page = max(1, min(page_count, round(page_count * fraction)))
            if page not in pages:
                pages.append(page)
        page_requests.extend((category_id, page) for page in pages)

    results: dict[tuple[int, int], list[int]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_url, f"{LIST_BASE_URL}?CateID={category_id}&Page={page}"): (category_id, page)
            for category_id, page in page_requests
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = extract_item_ids(future.result())
            except Exception:
                results[key] = []

    candidate_ids: list[int] = []
    for category_id, page in page_requests:
        for item_id in results.get((category_id, page), []):
            if item_id not in candidate_ids:
                candidate_ids.append(item_id)
    return candidate_ids


def iter_category_page_batches(
    category_id: int,
    max_pages: int,
    workers: int,
    page_batch_size: int = 20,
) -> Iterable[tuple[list[int], dict[int, list[int]]]]:
    """Yield oldest category pages first so accepted-count progress is observable."""
    total_records = category_count(category_id)
    total_pages = max(1, math.ceil(total_records / 7))
    pages = list(range(total_pages, max(0, total_pages - max_pages), -1))
    for offset in range(0, len(pages), page_batch_size):
        page_batch = pages[offset : offset + page_batch_size]
        results: dict[int, list[int]] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(fetch_url, f"{LIST_BASE_URL}?CateID={category_id}&Page={page}"): page
                for page in page_batch
            }
            for future in as_completed(futures):
                page = futures[future]
                try:
                    results[page] = extract_answered_item_ids(future.result())
                except Exception:
                    results[page] = []
        yield page_batch, results


def _candidate_record_with_reason(record: dict[str, str], reason: str) -> dict[str, object]:
    candidate = build_eval_record(record, reason)
    if reason not in {"general BHYT policy question answerable from public legal corpus", "mixed_personal_and_policy_question"}:
        candidate["rejection_reason"] = reason
    return candidate


def scrape_bhyt_candidates(
    target_count: int = 200,
    category_id: int = DEFAULT_BHYT_CATEGORY_ID,
    max_pages: int = 1_000,
    max_scan: int = 5_000,
    workers: int = 3,
    batch_size: int = 60,
    page_batch_size: int = 5,
    progress: bool = False,
) -> dict[str, object]:
    """Collect accepted BHYT candidates until target_count is actually reached."""
    if target_count < 1:
        raise ValueError("target_count must be positive")
    if max_scan < target_count:
        raise ValueError("max_scan must be at least target_count")

    good: list[dict[str, object]] = []
    needs_review: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    seen_questions: set[str] = set()
    category_distribution: collections.Counter[str] = collections.Counter()
    rejection_counts: collections.Counter[str] = collections.Counter()
    pages_scanned = 0
    records_scanned = 0
    fetch_errors = 0
    candidate_ids_seen: set[int] = set()

    for page_batch, page_results in iter_category_page_batches(
        category_id,
        max_pages,
        workers,
        page_batch_size=page_batch_size,
    ):
        pages_scanned += len(page_batch)
        page_candidate_ids: list[int] = []
        for page in page_batch:
            for item_id in page_results.get(page, []):
                if item_id not in candidate_ids_seen:
                    candidate_ids_seen.add(item_id)
                    page_candidate_ids.append(item_id)

        if not page_candidate_ids:
            if progress:
                print(f"[bhyt] pages={pages_scanned} scanned=0 accepted={len(good)}", flush=True)
            continue

        page_candidate_ids = page_candidate_ids[: max(0, max_scan - records_scanned)]
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(fetch_item, item_id, False)
                for item_id in page_candidate_ids
            ]
            batch_results: list[tuple[int, dict[str, str] | None, str | None]] = []
            for future in as_completed(futures):
                batch_results.append(future.result())

        for item_id, record, error in sorted(batch_results, key=lambda result: result[0], reverse=True):
            records_scanned += 1
            if error:
                fetch_errors += 1
                continue
            if record is None:
                rejection_counts["missing_record"] += 1
                continue

            category = clean_text(record.get("category", "")) or "<missing>"
            category_distribution[category] += 1
            question_key = normalize_question(record.get("question", ""))
            if question_key and question_key in seen_questions:
                rejection_counts["duplicate"] += 1
                rejected.append(_candidate_record_with_reason(record, "duplicate"))
                continue
            if question_key:
                seen_questions.add(question_key)

            decision, reason = classify_record(record)
            if decision == "accepted":
                good.append(_candidate_record_with_reason(record, reason))
            elif decision == "needs_review":
                needs_review.append(_candidate_record_with_reason(record, reason))
            else:
                rejection_counts[reason] += 1
                rejected.append(_candidate_record_with_reason(record, reason))

            if len(good) >= target_count:
                break

        if progress:
            print(
                f"[bhyt] pages={pages_scanned} scanned={records_scanned} "
                f"accepted={len(good)} review={len(needs_review)} rejected={len(rejected)}",
                flush=True,
            )
        if len(good) >= target_count or records_scanned >= max_scan:
            break

    if len(good) < target_count:
        raise RuntimeError(
            f"Only found {len(good)} accepted BHYT candidates after scanning "
            f"{records_scanned} records across {pages_scanned} pages; increase --max-pages or --max-scan."
        )
    assert len(good[:target_count]) == target_count
    assert all(
        record["category"].strip().lower() == BHYT_CATEGORY.lower()
        for record in good[:target_count]
    )

    stats = {
        "total_pages_scanned": pages_scanned,
        "total_records_scanned": records_scanned,
        "wrong_category": rejection_counts.get("wrong_category", 0),
        "rejected_personal_internal": rejection_counts.get("personal_record_lookup", 0)
        + rejection_counts.get("requires_internal_bhxh_data", 0),
        "rejected_duplicates": rejection_counts.get("duplicate", 0),
        "needs_review": len(needs_review),
        "accepted_bhyt_candidates": len(good[:target_count]),
        "fetch_errors": fetch_errors,
        "rejection_counts": dict(rejection_counts),
        "category_distribution": dict(category_distribution),
    }
    return {
        "metadata": {
            "dataset_name": "BHYT evaluation candidates",
            "schema_version": "1.0",
            "source_name": "Cổng Thông tin điện tử Bảo hiểm xã hội Việt Nam",
            "source_url": BASE_URL,
            "category": BHYT_CATEGORY,
            "category_id": str(category_id),
            "collected_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "requested_count": target_count,
            "record_count": target_count,
            "review_status": "pending",
            "not_final_gold_truth": True,
        },
        "records": good[:target_count],
        "statistics": stats,
        "needs_review": needs_review,
        "rejected": rejected,
    }


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _dataset_payload(
    name: str,
    records: list[dict[str, object]],
    stats: dict[str, object],
    requested_count: int,
) -> dict[str, object]:
    return {
        "metadata": {
            "dataset_name": name,
            "schema_version": "1.0",
            "category": BHYT_CATEGORY,
            "requested_count": requested_count,
            "record_count": len(records),
            "review_status": "pending" if name != "BHYT rejected records" else "rejected",
            "not_final_gold_truth": True,
            "created_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        },
        "records": records,
        "statistics": stats,
    }


def write_bhyt_outputs(
    result: dict[str, object],
    good_path: Path = DEFAULT_GOOD_OUTPUT,
    review_path: Path = DEFAULT_REVIEW_OUTPUT,
    rejected_path: Path = DEFAULT_REJECTED_OUTPUT,
    target_count: int = 200,
) -> None:
    stats = result["statistics"]
    good = result["records"]
    needs_review = result["needs_review"]
    rejected = result["rejected"]
    assert isinstance(stats, dict)
    assert isinstance(good, list)
    assert isinstance(needs_review, list)
    assert isinstance(rejected, list)
    write_json(good_path, _dataset_payload("BHYT good candidates", good, stats, target_count))
    write_json(review_path, _dataset_payload("BHYT needs-review records", needs_review, stats, target_count))
    write_json(rejected_path, _dataset_payload("BHYT rejected records", rejected, stats, target_count))


def contains_obvious_pii(text: str) -> bool:
    return bool(
        EMAIL_PATTERN.search(text)
        or PHONE_PATTERN.search(text)
        or LONG_NUMBER_PATTERN.search(text)
        or re.search(
            r"\b(?:mã\s*(?:số\s*)?(?:bhxh|bhyt)|cccd|cmnd|số\s*(?:thẻ|bhxh))\b\s*[:#-]?\s*[A-Za-z0-9]",
            text,
            flags=re.IGNORECASE,
        )
    )


def validate_bhyt_outputs(
    good_path: Path = DEFAULT_GOOD_OUTPUT,
    review_path: Path = DEFAULT_REVIEW_OUTPUT,
    rejected_path: Path = DEFAULT_REJECTED_OUTPUT,
    target_count: int = 200,
) -> dict[str, object]:
    def load(path: Path) -> dict[str, object]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise ValueError(f"{path} must contain an object with records")
        return data

    good_data = load(good_path)
    review_data = load(review_path)
    rejected_data = load(rejected_path)
    good = good_data["records"]
    review = review_data["records"]
    rejected = rejected_data["records"]
    assert isinstance(good, list) and isinstance(review, list) and isinstance(rejected, list)
    if len(good) != target_count:
        raise ValueError(f"expected {target_count} good candidates, found {len(good)}")

    required = {
        "id",
        "source_item_id",
        "question",
        "official_answer",
        "category",
        "submitted_at",
        "answered_at",
        "source_url",
        "legal_basis",
        "temporal_risk",
        "review_status",
        "classification_reason",
    }
    source_ids: set[str] = set()
    question_keys: set[str] = set()
    for index, record in enumerate(good):
        if not isinstance(record, dict):
            raise ValueError(f"good record {index} is not an object")
        missing = sorted(required - record.keys())
        if missing:
            raise ValueError(f"good record {index} missing {missing}")
        if record["category"].strip().lower() != BHYT_CATEGORY.lower():
            raise ValueError(f"good record {index} has non-BHYT category")
        if record["review_status"] != "pending":
            raise ValueError(f"good record {index} is not pending review")
        if not record["question"].strip() or not record["official_answer"].strip():
            raise ValueError(f"good record {index} has empty question/official_answer")
        if contains_obvious_pii(record["question"]):
            raise ValueError(f"good record {index} still contains obvious PII")
        if record["source_item_id"] in source_ids:
            raise ValueError(f"duplicate source_item_id {record['source_item_id']}")
        source_ids.add(record["source_item_id"])
        question_key = normalize_question(record["question"])
        if question_key in question_keys:
            raise ValueError(f"duplicate normalized question at record {index}")
        question_keys.add(question_key)
        if record["temporal_risk"] not in {"low", "medium", "high"}:
            raise ValueError(f"invalid temporal risk at record {index}")

    allowed_reasons = {
        "wrong_category",
        "personal_record_lookup",
        "requires_internal_bhxh_data",
        "not_policy_question",
        "missing_answer",
        "missing_question",
        "duplicate",
        "mixed_personal_and_policy_question",
    }
    for record in [*review, *rejected]:
        if not isinstance(record, dict):
            raise ValueError("review/rejected record is not an object")
        if "classification_reason" not in record and "rejection_reason" not in record:
            raise ValueError("review/rejected record has no reason")
        reason = record.get("rejection_reason") or record.get("classification_reason")
        if reason not in allowed_reasons:
            raise ValueError(f"unknown review/rejected reason: {reason}")

    return {
        "status": "ok",
        "good_candidates": len(good),
        "needs_review": len(review),
        "rejected": len(rejected),
        "unique_good_source_ids": len(source_ids),
    }


def print_bhyt_statistics(stats: dict[str, object]) -> None:
    print(f"Total pages scanned: {stats.get('total_pages_scanned', 0)}")
    print(f"Total records scanned: {stats.get('total_records_scanned', 0)}")
    print(f"Wrong category: {stats.get('wrong_category', 0)}")
    print(f"Rejected personal/internal: {stats.get('rejected_personal_internal', 0)}")
    print(f"Rejected duplicates: {stats.get('rejected_duplicates', 0)}")
    print(f"Needs review: {stats.get('needs_review', 0)}")
    print(f"Accepted BHYT candidates: {stats.get('accepted_bhyt_candidates', 0)}")
    print(f"Category distribution: {json.dumps(stats.get('category_distribution', {}), ensure_ascii=False, sort_keys=True)}")


def scrape(
    start_id: int = DEFAULT_START_ID,
    target_count: int = 200,
    max_scan: int = 2_000,
    workers: int = 3,
    batch_size: int = 60,
    progress: bool = False,
) -> dict:
    if target_count < 1:
        raise ValueError("target_count must be positive")
    if max_scan < target_count:
        raise ValueError("max_scan must be at least target_count")
    if not 1 <= workers <= 6:
        raise ValueError("workers must be between 1 and 6")

    records: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    scanned = 0
    errors = 0
    candidate_ids = collect_candidate_ids(workers=workers)
    if not candidate_ids:
        candidate_ids = list(range(start_id, start_id - max_scan, -1))
    candidate_ids = candidate_ids[:max_scan]
    if progress:
        print(f"[scrape] candidate ItemIDs: {len(candidate_ids)}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for offset in range(0, len(candidate_ids), batch_size):
            batch_ids = candidate_ids[offset : offset + batch_size]
            futures = [executor.submit(fetch_item, item_id) for item_id in batch_ids]
            batch_results: dict[int, tuple[dict[str, str] | None, str | None]] = {}
            for future in as_completed(futures):
                item_id, record, error = future.result()
                batch_results[item_id] = (record, error)

            for item_id in sorted(batch_results, reverse=True):
                scanned += 1
                record, error = batch_results[item_id]
                if error:
                    errors += 1
                if not record:
                    continue
                pair_key = (normalize_key(record["question"]), normalize_key(record["ground_truth"]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                records.append(record)
                if len(records) >= target_count:
                    break

            if len(records) >= target_count:
                break
            if progress:
                print(
                    f"[scrape] scanned={scanned} answered_unique={len(records)} errors={errors}",
                    flush=True,
                )

    if len(records) < target_count:
        raise RuntimeError(
            f"Only found {len(records)} unique answered records after scanning {scanned} items "
            f"({errors} fetch errors); increase --max-scan."
        )

    collected_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "metadata": {
            "dataset_name": "BHXH Việt Nam Q&A Ground Truth",
            "schema_version": "1.0",
            "source_name": "Cổng Thông tin điện tử Bảo hiểm xã hội Việt Nam",
            "source_url": BASE_URL,
            "collected_at_utc": collected_at,
            "requested_count": target_count,
            "record_count": target_count,
            "start_item_id": start_id,
            "candidate_item_count": len(candidate_ids),
            "items_scanned": scanned,
            "fetch_errors": errors,
            "deduplicated_by": "normalized question + normalized ground_truth",
        },
        "records": records[:target_count],
    }


def write_dataset(dataset: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_dataset(path: Path, expected_count: int = 200) -> dict[str, int | str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise ValueError("dataset must be an object with a records array")
    records = data["records"]
    if len(records) != expected_count:
        raise ValueError(f"expected {expected_count} records, found {len(records)}")

    required_fields = {
        "id",
        "source_item_id",
        "question",
        "ground_truth",
        "category",
        "source_url",
    }
    source_ids: set[str] = set()
    pair_keys: set[tuple[str, str]] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"record {index} is not an object")
        missing = sorted(required_fields - record.keys())
        if missing:
            raise ValueError(f"record {index} missing fields: {', '.join(missing)}")
        for field in required_fields:
            if not isinstance(record[field], str) or not record[field].strip():
                raise ValueError(f"record {index} has empty/non-string {field}")
        source_id = record["source_item_id"]
        if source_id in source_ids:
            raise ValueError(f"duplicate source_item_id: {source_id}")
        source_ids.add(source_id)
        pair_key = (normalize_key(record["question"]), normalize_key(record["ground_truth"]))
        if pair_key in pair_keys:
            raise ValueError(f"duplicate normalized question/ground_truth at record {index}")
        pair_keys.add(pair_key)
        expected_prefix = f"{BASE_URL}?ItemID="
        if not record["source_url"].startswith(expected_prefix):
            raise ValueError(f"record {index} has an unexpected source_url")
        if "chưa trả lời" in record.get("status", "").casefold():
            raise ValueError(f"record {index} is marked as unanswered")

    metadata = data.get("metadata", {})
    if metadata.get("record_count") != expected_count:
        raise ValueError("metadata.record_count does not match the expected count")
    return {"status": "ok", "records": len(records), "unique_source_ids": len(source_ids)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("bhyt", "legacy"), default="bhyt")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--good-output", type=Path, default=DEFAULT_GOOD_OUTPUT)
    parser.add_argument("--review-output", type=Path, default=DEFAULT_REVIEW_OUTPUT)
    parser.add_argument("--rejected-output", type=Path, default=DEFAULT_REJECTED_OUTPUT)
    parser.add_argument("--category-id", type=int, default=DEFAULT_BHYT_CATEGORY_ID)
    parser.add_argument("--start-id", type=int, default=DEFAULT_START_ID)
    parser.add_argument("--target-count", type=int, default=200)
    parser.add_argument("--max-pages", type=int, default=1_000)
    parser.add_argument("--max-scan", type=int, default=5_000)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--page-batch-size", type=int, default=5)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument(
        "--reader-html",
        action="store_true",
        help="Fetch source pages through the raw-HTML Reader fallback when the origin resets connections",
    )
    parser.add_argument("--validate", type=Path, help="Validate an existing JSON dataset and exit")
    parser.add_argument("--validate-eval", action="store_true", help="Validate the three BHYT evaluation outputs")
    return parser


def main() -> int:
    global PREFER_READER_HTML
    args = build_parser().parse_args()
    PREFER_READER_HTML = args.reader_html
    if args.validate_eval:
        print(
            json.dumps(
                validate_bhyt_outputs(
                    args.good_output,
                    args.review_output,
                    args.rejected_output,
                    args.target_count,
                ),
                ensure_ascii=False,
            )
        )
        return 0
    if args.validate:
        print(json.dumps(validate_dataset(args.validate, args.target_count), ensure_ascii=False))
        return 0

    if args.mode == "legacy":
        dataset = scrape(
            start_id=args.start_id,
            target_count=args.target_count,
            max_scan=args.max_scan,
            workers=args.workers,
            progress=args.progress,
        )
        write_dataset(dataset, args.output)
        print(json.dumps(dataset["metadata"], ensure_ascii=False))
        print(json.dumps(validate_dataset(args.output, args.target_count), ensure_ascii=False))
        return 0

    result = scrape_bhyt_candidates(
        target_count=args.target_count,
        category_id=args.category_id,
        max_pages=args.max_pages,
        max_scan=args.max_scan,
        workers=args.workers,
        page_batch_size=args.page_batch_size,
        progress=args.progress,
    )
    write_bhyt_outputs(
        result,
        good_path=args.good_output,
        review_path=args.review_output,
        rejected_path=args.rejected_output,
        target_count=args.target_count,
    )
    print_bhyt_statistics(result["statistics"])
    print(
        json.dumps(
            validate_bhyt_outputs(
                args.good_output,
                args.review_output,
                args.rejected_output,
                args.target_count,
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
