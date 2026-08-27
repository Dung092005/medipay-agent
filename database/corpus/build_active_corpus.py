#!/usr/bin/env python3
"""Build the reviewed BHYT/medical-fee corpus from CSV and active exports.

The active document export is not an authority for ``content_text``.  This
builder deliberately ignores that field, selects one identity-checked HTML
source per legal instrument, and lets the canonical pipeline derive visible
text again.  Active relationship JSON controls graph membership; the legacy
CSV is used only to enrich matching edges with provenance and flags.

Recovered web content is cached as the exact extracted legal-document HTML
fragment.  Every recovery records the response URL/hash and an official
corroboration URL.  A cached build is deterministic and can run offline.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

csv.field_size_limit(sys.maxsize)

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_ROOT = REPO_ROOT / "database" / "pipeline"
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from data_pipeline.canonical import normalize_html  # noqa: E402

DEFAULT_SOURCE_DIR = Path("/home/minh/projects/csv_admin_bhyt_vien_phi/source_originals")
DEFAULT_ACTIVE_DOCS = REPO_ROOT / "database" / "medical_docs_active.json"
DEFAULT_ACTIVE_RELATIONSHIPS = REPO_ROOT / "database" / "medical_relationships_active.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "clean" / "medical_active_v2"

BASE_METADATA_FIELDS = (
    "id",
    "title",
    "so_ky_hieu",
    "ngay_ban_hanh",
    "loai_van_ban",
    "ngay_co_hieu_luc",
    "ngay_het_hieu_luc",
    "nguon_thu_thap",
    "ngay_dang_cong_bao",
    "nganh",
    "linh_vuc",
    "co_quan_ban_hanh",
    "chuc_danh",
    "nguoi_ky",
    "pham_vi",
    "thong_tin_ap_dung",
    "tinh_trang_hieu_luc",
    "agent_category",
    "status_checked_at",
    "status_filter",
)
DERIVED_METADATA_FIELDS = (
    "canonical_document_id",
    "retrieval_scope",
    "selection_reasons",
    "index_eligible",
    "lexical_eligible",
    "semantic_eligible",
    "metadata_provenance",
    "category_provenance",
    "content_source_kind",
    "content_source_document_id",
    "content_validation_status",
    "legal_status_verified",
    "answer_ready",
)
METADATA_FIELDS = BASE_METADATA_FIELDS + DERIVED_METADATA_FIELDS

RELATIONSHIP_BASE_FIELDS = (
    "agent_category",
    "doc_id",
    "other_doc_id",
    "relationship",
    "source_is_selected",
    "target_is_selected",
    "relationship_is_adverse",
    "source_title",
    "target_title",
)
RELATIONSHIP_DERIVED_FIELDS = (
    "relationship_id",
    "provenance_status",
    "adverse_provenance",
    "source_row_hashes",
    "original_edge_count",
)

SPACE_RE = re.compile(r"\s+")
IDENTITY_RE = re.compile(r"[^0-9a-zà-ỹđ]+", flags=re.IGNORECASE)
WORD_RE = re.compile(r"[\wÀ-ỹ]+", flags=re.UNICODE)
DIV_RE = re.compile(r"</?div\b[^>]*>", flags=re.IGNORECASE | re.DOTALL)
FULL_CONTENT_RE = re.compile(
    r"id\s*=\s*['\"]full-content['\"]", flags=re.IGNORECASE
)

STRONG_FILTER_REASONS = {
    "field_bao_hiem_y_te",
    "title_bao_hiem_y_te",
    "title_bhyt",
    "title_gia_dich_vu_kham",
    "title_gia_dich_vu_y_te",
    "title_thanh_toan_bao_hiem_y_te",
    "title_thanh_toan_kcb",
    "title_thanh_toan_kham",
    "title_thanh_toan_vien_phi",
    "title_thanh_toan_y_te",
    "title_vien_phi",
}

TITLE_STOP_WORDS = {
    "ban",
    "bổ",
    "các",
    "cho",
    "chữa",
    "có",
    "của",
    "do",
    "định",
    "đối",
    "được",
    "hành",
    "khám",
    "một",
    "này",
    "phạm",
    "quy",
    "số",
    "tại",
    "theo",
    "trên",
    "trong",
    "và",
    "về",
    "việc",
    "với",
}

ADVERSE_PREDICATES = {"Bãi bỏ", "Thay thế"}
WRONG_OR_MISSING_SOURCE_HTML = {"102353", "143848", "157394", "187533", "187782"}


@dataclass(frozen=True)
class RecoverySource:
    document_id: str
    source_url: str
    corroboration_url: str
    corroboration_kind: str


RECOVERY_SOURCES = {
    source.document_id: source
    for source in (
        RecoverySource(
            "102353",
            "https://thuviennhadat.vn/vbpl/quyet-dinh-258-2009-qd-ubnd-dieu-chinh-quyet-dinh-212-2006-qd-ubnd-gia-thu-dich-vu-y-te-98284.html",
            "https://vbpl.vn/TW/Pages/vanban.aspx?Page=9&dvid=222&fromyear=01%2F01%2F2001&toyear=31%2F12%2F2010",
            "official_vbpl_listing",
        ),
        RecoverySource(
            "157394",
            "https://thuviennhadat.vn/vbpl/quyet-dinh-27-2020-qd-ubnd-gia-kham-chua-benh-khong-thuoc-thanh-toan-cua-bao-hiem-ninh-thuan-459884.html",
            "https://vbpl.vn/ninhthuan/Pages/vanban.aspx?Page=4&cqbh=140&dvid=222&fromyear=01%2F01%2F2011&toyear=31%2F12%2F2020",
            "official_vbpl_listing",
        ),
        RecoverySource(
            "187533",
            "https://thuviennhadat.vn/vbpl/nghi-quyet-09-2026-nq-hdnd-muc-ho-tro-dong-bao-hiem-y-te-cho-mot-so-doi-tuong-dien-bien-698911.html",
            "https://vbpl.vn/TW/Pages/vbpq-thuoctinh.aspx?ItemID=187533",
            "official_vbpl_metadata",
        ),
        RecoverySource(
            "187782",
            "https://thuviennhadat.vn/vbpl/thong-tu-107-2025-tt-btc-huong-dan-ke-toan-quy-bao-hiem-xa-hoi-quy-bao-hiem-y-te-682144.html",
            "https://vanban.chinhphu.vn/?classid=1&docid=216041&orggroupid=4&pageid=27160",
            "official_government_metadata_and_pdf",
        ),
    )
}


@dataclass(frozen=True)
class AliasRule:
    alias_document_id: str
    canonical_document_id: str
    alias_type: str
    confidence: str
    reason: str
    evidence_url: str


ALIAS_RULES = (
    AliasRule(
        "143848",
        "157394",
        "invalid_source_record",
        "confirmed",
        "Bản ghi ghi Quyết định 28/2020/QĐ-UBND về giá khám chữa bệnh nhưng văn bản chính thức là Quyết định 27/2020/QĐ-UBND; Quyết định 28/2020 có trích yếu khác.",
        "https://vbpl.vn/ninhthuan/Pages/vanban.aspx?Page=4&cqbh=140&dvid=222&fromyear=01%2F01%2F2011&toyear=31%2F12%2F2020",
    ),
    AliasRule("32696", "101450", "duplicate_legal_identity", "high", "Trùng số/ký hiệu, tiêu đề và nội dung (audit corpus).", "database/audit/results/duplicate_candidates.csv"),
    AliasRule("77480", "108369", "duplicate_legal_identity", "high", "Trùng số/ký hiệu, tiêu đề và nội dung (audit corpus).", "database/audit/results/duplicate_candidates.csv"),
    AliasRule("77981", "108745", "duplicate_legal_identity", "high", "Trùng số/ký hiệu, tiêu đề và nội dung (audit corpus).", "database/audit/results/duplicate_candidates.csv"),
    AliasRule("21183", "13349", "duplicate_legal_identity", "high", "Trùng số/ký hiệu, tiêu đề và nội dung (audit corpus).", "database/audit/results/duplicate_candidates.csv"),
    AliasRule("184708", "185037", "duplicate_legal_identity", "high", "Trùng số/ký hiệu, tiêu đề và nội dung (audit corpus).", "database/audit/results/duplicate_candidates.csv"),
    AliasRule("100567", "79102", "duplicate_legal_identity", "high", "Trùng số/ký hiệu, tiêu đề và nội dung (audit corpus).", "database/audit/results/duplicate_candidates.csv"),
)


@dataclass(frozen=True)
class MetadataCorrection:
    document_id: str
    old_signature: str
    corrected_signature: str
    evidence: str


# Reviewed identity corrections only. Each rule is guarded by the exact old
# value so an upstream source change fails loudly instead of being overwritten.
METADATA_CORRECTIONS = {
    item.document_id: item
    for item in (
        MetadataCorrection("101450", "39/2013/QĐ-UBND.", "39/2013/QĐ-UBND", "selected_html_header"),
        MetadataCorrection("108357", "05/1999/TTLT/BLÐTBXH-BYT-BTC", "05/1999/TTLT-BLĐTBXH-BYT-BTC", "selected_html_header"),
        MetadataCorrection("108710", "15/1998/TTLT-BYT-BTC-BLÐTBXH", "15/1998/TTLT-BYT-BTC-BLĐTBXH", "selected_html_header"),
        MetadataCorrection("109238", "09/2002/TTLT/BTCCBCP-BLÐTBXH-BTC-BYT", "09/2002/TTLT-BTCCBCP-BLĐTBXH-BTC-BYT", "selected_html_header"),
        MetadataCorrection("109673", "22/2003/TTLT-BQP-BLÐTBXH-BYT-BTC", "22/2003/TTLT-BQP-BLĐTBXH-BYT-BTC", "selected_html_header"),
        MetadataCorrection("110115", "03/2004/TTLT-BCA-BTC-BNV-BLĐTB & XH", "03/2004/TTLT-BCA-BTC-BNV-BLĐTBXH", "https://vbpl.vn/bocongan/Pages/vbpq-toanvan.aspx?ItemID=19944"),
        MetadataCorrection("116216", "02/2014/NQ-HĐND.", "02/2014/NQ-HĐND", "selected_html_header"),
        MetadataCorrection("129213", "Nghị quyết số: 528/2018/UBTVQH14", "528/2018/UBTVQH14", "selected_html_header"),
        MetadataCorrection("153839", "113/2022/NQ-UBND", "113/2022/NQ-HĐND", "https://vbpl.vn/hoabinh/Pages/vbpq-van-ban-goc.aspx?ItemID=153839"),
        MetadataCorrection("156429", "13/2022/NQ-HĐND.loi", "13/2022/NQ-HĐND", "https://vbpl.vn/thuathienhue/Pages/vbpq-van-ban-goc.aspx?ItemID=156562"),
        MetadataCorrection("167742", "số 11/2024/NQ-HĐND", "11/2024/NQ-HĐND", "selected_html_header"),
        MetadataCorrection("172923", "Luật số 51/2024/QH15", "51/2024/QH15", "selected_html_header"),
        MetadataCorrection("173951", "Thông tư 01/2025/TT-BYT", "01/2025/TT-BYT", "selected_html_header"),
        MetadataCorrection("174189", "Thông tư 51/2024/TT-BYT", "51/2024/TT-BYT", "selected_html_header"),
        MetadataCorrection("174192", "Thông tư 57/2024/TT-BYT", "57/2024/TT-BYT", "selected_html_header"),
        MetadataCorrection("178219", "Thông tư 13/2025/TT-BYT", "13/2025/TT-BYT", "selected_html_header"),
        MetadataCorrection("178302", "Thông tư 28/2023/TT-BYT của Bộ Y tế", "28/2023/TT-BYT", "selected_html_header"),
        MetadataCorrection("185356", "'26/2025/NQ-HĐND", "26/2025/NQ-HĐND", "selected_html_header"),
        MetadataCorrection("32676", "17/2012/NQ-HĐND.", "17/2012/NQ-HĐND", "selected_html_header"),
    )
}


def apply_metadata_correction(document_id: str, metadata: dict[str, Any]) -> dict[str, str] | None:
    correction = METADATA_CORRECTIONS.get(document_id)
    if correction is None:
        return None
    actual = clean(metadata.get("so_ky_hieu"))
    if actual != correction.old_signature:
        raise ValueError(
            f"Metadata correction source changed for {document_id}: "
            f"{actual!r} != {correction.old_signature!r}"
        )
    old_title = clean(metadata.get("title"))
    if correction.old_signature not in old_title:
        raise ValueError(
            f"Metadata correction title changed for {document_id}: "
            f"missing {correction.old_signature!r} in {old_title!r}"
        )
    metadata["so_ky_hieu"] = correction.corrected_signature
    metadata["title"] = old_title.replace(
        correction.old_signature, correction.corrected_signature, 1,
    )
    return {
        "document_id": document_id,
        "old_signature": correction.old_signature,
        "corrected_signature": correction.corrected_signature,
        "old_title": old_title,
        "corrected_title": metadata["title"],
        "evidence": correction.evidence,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--active-docs", type=Path, default=DEFAULT_ACTIVE_DOCS)
    parser.add_argument("--active-relationships", type=Path, default=DEFAULT_ACTIVE_RELATIONSHIPS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--offline", action="store_true", help="Require cached recovery fragments; make no HTTP requests.")
    parser.add_argument("--refresh-recovery", action="store_true", help="Refresh the four web-recovery fragments even when cached.")
    return parser.parse_args()


def is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def clean(value: Any) -> str:
    if value is None or is_nan(value):
        return ""
    normalized = unicodedata.normalize("NFC", str(value).replace("\ufeff", ""))
    return SPACE_RE.sub(" ", normalized).strip()


def normalized_identity(value: Any) -> str:
    return SPACE_RE.sub(" ", IDENTITY_RE.sub(" ", clean(value).casefold())).strip()


def compact_identity(value: Any) -> str:
    return normalized_identity(value).replace(" ", "")


def sha256(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def read_json_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"Expected an array of objects: {path}")
    return value


def index_unique(rows: Iterable[dict[str, Any]], field: str, source: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row_number, row in enumerate(rows, start=2):
        identifier = clean(row.get(field))
        if not identifier or identifier in result:
            raise ValueError(f"{source} row {row_number} has missing/duplicate {field}: {identifier!r}")
        result[identifier] = row
    return result


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def atomic_write_csv(
    path: Path,
    rows: Iterable[dict[str, Any]],
    fieldnames: Iterable[str],
    *,
    preserve_fields: frozenset[str] = frozenset(),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: (
                    "" if value is None or is_nan(value) else str(value)
                    if key in preserve_fields else clean(value)
                )
                for key, value in row.items()
            })
    os.replace(temporary, path)


def ordered_contains(value: str, first: str, second: str) -> bool:
    position = value.find(first)
    return position >= 0 and second in value[position + len(first) :]


def filter_reasons(document: dict[str, Any]) -> list[str]:
    """Reproduce the user-provided SQL LIKE conditions exactly."""

    title = clean(document.get("title")).lower()
    field = clean(document.get("linh_vuc")).lower()
    checks = (
        ("title_bao_hiem_y_te", "bảo hiểm y tế" in title),
        ("title_bhyt", "bhyt" in title),
        ("title_vien_phi", "viện phí" in title),
        ("title_kham_benh_chua_benh", "khám bệnh, chữa bệnh" in title),
        ("title_kham_chua_benh_comma", "khám, chữa bệnh" in title),
        ("title_kham_chua_benh", "khám chữa bệnh" in title),
        ("title_thanh_toan_y_te", ordered_contains(title, "thanh toán", "y tế")),
        ("title_thanh_toan_kcb", ordered_contains(title, "thanh toán", "kcb")),
        ("title_thanh_toan_kham", ordered_contains(title, "thanh toán", "khám")),
        ("title_thanh_toan_vien_phi", ordered_contains(title, "thanh toán", "viện phí")),
        ("title_thanh_toan_bao_hiem_y_te", ordered_contains(title, "thanh toán", "bảo hiểm y tế")),
        ("title_gia_dich_vu_kham", "giá dịch vụ khám" in title),
        ("title_gia_dich_vu_y_te", "giá dịch vụ y tế" in title),
        ("field_bao_hiem_y_te", "bảo hiểm y tế" in field),
    )
    return [reason for reason, matches in checks if matches]


def split_categories(value: Any) -> set[str]:
    return {part.strip().casefold() for part in clean(value).split(",") if part.strip() in {"bhyt", "vien_phi"}}


def derived_categories(document: dict[str, Any]) -> set[str]:
    haystack = " ".join((clean(document.get("title")), clean(document.get("linh_vuc")))).casefold()
    result: set[str] = set()
    if "bảo hiểm y tế" in haystack or "bhyt" in haystack:
        result.add("bhyt")
    if any(term in haystack for term in ("viện phí", "giá dịch vụ", "giá thu dịch vụ", "thanh toán viện phí")):
        result.add("vien_phi")
    return result


def role_for(document_id: str, document: dict[str, Any], active_ids: set[str], endpoints: set[str]) -> tuple[str, list[str]]:
    reasons = filter_reasons(document)
    if set(reasons) & STRONG_FILTER_REASONS:
        return "seed_core", reasons
    if reasons:
        return "seed_broad_kcb", reasons
    if document_id in active_ids and document_id in endpoints:
        return "graph_context", reasons
    return "csv_only", reasons


def token_set(value: Any, *, title: bool = False) -> set[str]:
    result = set(WORD_RE.findall(clean(value).casefold()))
    if title:
        result = {word for word in result if len(word) > 2 and word not in TITLE_STOP_WORDS}
    return result


def coverage(expected: Any, actual: str, *, title: bool = False) -> float:
    expected_tokens = token_set(expected, title=title)
    if not expected_tokens:
        return 1.0
    return len(expected_tokens & token_set(actual)) / len(expected_tokens)


def extract_full_content_fragments(page_html: str) -> list[str]:
    """Extract balanced ``div#full-content`` subtrees without extra dependencies."""

    fragments: list[str] = []
    seen_starts: set[int] = set()
    for marker in FULL_CONTENT_RE.finditer(page_html):
        start = page_html.rfind("<div", 0, marker.start())
        if start < 0 or start in seen_starts:
            continue
        seen_starts.add(start)
        depth = 0
        for tag_match in DIV_RE.finditer(page_html, start):
            tag = tag_match.group(0)
            if tag.startswith("</"):
                depth -= 1
                if depth == 0:
                    fragments.append(page_html[start : tag_match.end()])
                    break
            elif not tag.rstrip().endswith("/>"):
                depth += 1
    return fragments


def validate_recovered_html(metadata: dict[str, Any], raw_html: str) -> dict[str, Any]:
    visible = normalize_html(raw_html)
    signature = compact_identity(metadata.get("so_ky_hieu"))
    visible_identity = compact_identity(visible)
    issue_year = clean(metadata.get("ngay_ban_hanh"))[-4:]
    metrics = {
        "visible_characters": len(visible),
        "signature_found": bool(signature) and signature in visible_identity,
        "issuer_token_coverage": round(coverage(metadata.get("co_quan_ban_hanh"), visible), 6),
        "title_token_coverage": round(coverage(metadata.get("title"), visible, title=True), 6),
        "issue_year_found": bool(issue_year) and issue_year in visible,
    }
    failures = [
        name
        for name, failed in (
            ("visible_too_short", metrics["visible_characters"] < 500),
            ("signature_not_found", not metrics["signature_found"]),
            ("issuer_mismatch", metrics["issuer_token_coverage"] < 0.45),
            ("title_mismatch", metrics["title_token_coverage"] < 0.45),
            ("issue_year_not_found", not metrics["issue_year_found"]),
        )
        if failed
    ]
    if failures:
        raise ValueError(f"Recovered HTML failed identity gates for {clean(metadata.get('id'))}: {', '.join(failures)}")
    return metrics


def download_recovery(
    source: RecoverySource,
    metadata: dict[str, Any],
    cache_dir: Path,
    *,
    offline: bool,
    refresh: bool,
) -> tuple[str, dict[str, Any]]:
    fragment_path = cache_dir / f"{source.document_id}.html"
    provenance_path = cache_dir / f"{source.document_id}.json"
    if fragment_path.is_file() and provenance_path.is_file() and not refresh:
        # Path.read_text applies universal-newline conversion; decoding exact
        # bytes preserves the recovered fragment hash across offline builds.
        fragment = fragment_path.read_bytes().decode("utf-8")
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance["validation"] = validate_recovered_html(metadata, fragment)
        if provenance.get("fragment_sha256") != file_sha256(fragment_path):
            raise ValueError(f"Recovery cache hash mismatch: {fragment_path}")
        return fragment, provenance
    if offline:
        raise FileNotFoundError(f"No valid cached recovery for {source.document_id}: {fragment_path}")

    request = Request(
        source.source_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; VinGeniusLegalCorpus/1.0; evidence recovery)",
            "Accept-Encoding": "identity",
        },
    )
    with urlopen(request, timeout=45) as response:  # noqa: S310 - fixed reviewed URLs only
        response_bytes = response.read(32 * 1024 * 1024 + 1)
        if len(response_bytes) > 32 * 1024 * 1024:
            raise ValueError(f"Recovery response is unexpectedly large: {source.document_id}")
        charset = response.headers.get_content_charset() or "utf-8"
        final_url = response.url
        status = response.status
    if status != 200:
        raise ValueError(f"Recovery HTTP status {status}: {source.document_id}")
    page_html = response_bytes.decode(charset, errors="replace")
    fragments = extract_full_content_fragments(page_html)
    if not fragments:
        raise ValueError(f"No div#full-content found for {source.document_id}")

    signature = compact_identity(metadata.get("so_ky_hieu"))
    candidates: list[tuple[tuple[int, int], str]] = []
    for fragment in fragments:
        visible = normalize_html(fragment)
        candidates.append(((int(bool(signature) and signature in compact_identity(visible)), len(visible)), fragment))
    fragment = max(candidates, key=lambda item: item[0])[1]
    validation = validate_recovered_html(metadata, fragment)
    provenance = {
        **asdict(source),
        "retrieved_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "http_status": status,
        "final_url": final_url,
        "response_charset": charset,
        "response_bytes": len(response_bytes),
        "response_sha256": sha256(response_bytes),
        "extraction_selector": "div#full-content",
        "fragment_bytes": len(fragment.encode("utf-8")),
        "fragment_sha256": sha256(fragment),
        "validation": validation,
    }
    atomic_write_text(fragment_path, fragment)
    atomic_write_json(provenance_path, provenance)
    return fragment, provenance


def bool_source(value: Any) -> bool:
    return clean(value).casefold() in {"true", "1", "yes", "y"}


def resolve_alias(document_id: str, aliases: dict[str, str]) -> str:
    seen: set[str] = set()
    current = document_id
    while current in aliases:
        if current in seen:
            raise ValueError(f"Alias cycle involving {document_id}")
        seen.add(current)
        current = aliases[current]
    return current


def source_row_hash(row: dict[str, Any]) -> str:
    return sha256(canonical_json({str(key): clean(value) for key, value in row.items()}))


def build_corpus(
    source_dir: Path,
    active_docs_path: Path,
    active_relationships_path: Path,
    output_dir: Path,
    *,
    offline: bool = False,
    refresh_recovery: bool = False,
) -> dict[str, Any]:
    csv_metadata_rows = read_csv(source_dir / "metadata.csv")
    csv_content_rows = read_csv(source_dir / "content.csv")
    csv_relationship_rows = read_csv(source_dir / "relationships.csv")
    active_document_rows = read_json_rows(active_docs_path)
    active_relationship_rows = read_json_rows(active_relationships_path)

    csv_metadata = index_unique(csv_metadata_rows, "id", "metadata.csv")
    csv_content = index_unique(csv_content_rows, "id", "content.csv")
    active_documents = index_unique(active_document_rows, "id", active_docs_path.name)
    all_documents = set(csv_metadata) | set(active_documents)
    alias_map = {rule.alias_document_id: rule.canonical_document_id for rule in ALIAS_RULES}
    for alias_id, canonical_id in alias_map.items():
        if alias_id not in all_documents or canonical_id not in all_documents:
            raise ValueError(f"Alias endpoint is absent from corpus: {alias_id} -> {canonical_id}")
        resolve_alias(alias_id, alias_map)

    raw_active_edges: list[tuple[str, str, str, dict[str, Any]]] = []
    for row_number, row in enumerate(active_relationship_rows, start=2):
        source = clean(row.get("doc_id"))
        target = clean(row.get("other_doc_id"))
        predicate = clean(row.get("relationship"))
        if not source or not target or not predicate:
            raise ValueError(f"Active relationship row {row_number} lacks endpoint/predicate")
        raw_active_edges.append((source, target, predicate, row))
    if len({edge[:3] for edge in raw_active_edges}) != len(raw_active_edges):
        raise ValueError("Active relationship export contains duplicate edge triples")
    active_endpoints = {identifier for edge in raw_active_edges for identifier in edge[:2]}

    groups: dict[str, list[str]] = defaultdict(list)
    for document_id in sorted(all_documents):
        groups[resolve_alias(document_id, alias_map)].append(document_id)
    canonical_ids = set(groups)

    recovery_cache = output_dir / "recovery_cache"
    recovered: dict[str, tuple[str, dict[str, Any]]] = {}
    for document_id, source in RECOVERY_SOURCES.items():
        if document_id not in canonical_ids:
            raise ValueError(f"Recovery target unexpectedly became an alias: {document_id}")
        metadata = dict(csv_metadata.get(document_id) or active_documents.get(document_id) or {})
        metadata["id"] = document_id
        recovered[document_id] = download_recovery(
            source,
            metadata,
            recovery_cache,
            offline=offline,
            refresh=refresh_recovery,
        )

    metadata_output: list[dict[str, Any]] = []
    content_output: list[dict[str, Any]] = []
    provenance_output: list[dict[str, Any]] = []
    quality_issues: list[dict[str, Any]] = []
    crawl_backlog: list[dict[str, Any]] = []
    canonical_metadata: dict[str, dict[str, Any]] = {}
    canonical_categories: dict[str, set[str]] = {}
    metadata_correction_output: list[dict[str, str]] = []

    role_priority = {"seed_core": 4, "seed_broad_kcb": 3, "graph_context": 2, "csv_only": 1}
    fillable_fields = set(BASE_METADATA_FIELDS) - {"id", "title", "so_ky_hieu", "ngay_ban_hanh", "loai_van_ban", "co_quan_ban_hanh"}

    for canonical_id in sorted(canonical_ids):
        members = groups[canonical_id]
        primary_source = csv_metadata.get(canonical_id) or active_documents.get(canonical_id)
        if not primary_source:
            raise ValueError(f"Missing canonical metadata: {canonical_id}")
        metadata = {field: clean(primary_source.get(field)) for field in BASE_METADATA_FIELDS}
        metadata["id"] = canonical_id

        ordered_sources = [
            *(csv_metadata[item] for item in members if item in csv_metadata),
            *(active_documents[item] for item in members if item in active_documents),
        ]
        for field in fillable_fields:
            if not metadata.get(field):
                metadata[field] = next((clean(row.get(field)) for row in ordered_sources if clean(row.get(field))), "")

        correction_audit = apply_metadata_correction(canonical_id, metadata)
        if correction_audit:
            metadata_correction_output.append(correction_audit)

        categories: set[str] = set()
        explicit_categories = False
        for member in members:
            if member in csv_metadata:
                found = split_categories(csv_metadata[member].get("agent_category"))
                explicit_categories = explicit_categories or bool(found)
                categories.update(found)
        if not categories:
            for row in ordered_sources:
                categories.update(derived_categories(row))
        metadata["agent_category"] = ",".join(sorted(categories))

        roles: list[tuple[str, list[str]]] = []
        for member in members:
            source_metadata = csv_metadata.get(member) or active_documents.get(member) or {}
            roles.append(role_for(member, source_metadata, set(active_documents), active_endpoints))
        role = max((item[0] for item in roles), key=lambda item: role_priority[item])
        reasons = sorted({reason for _, member_reasons in roles for reason in member_reasons})

        raw_html = ""
        source_kind = ""
        source_document_id = canonical_id
        web_provenance: dict[str, Any] = {}
        if canonical_id in recovered:
            raw_html, web_provenance = recovered[canonical_id]
            source_kind = "recovered_third_party_html_officially_corroborated"
        else:
            content_candidates = [canonical_id, *(item for item in members if item != canonical_id)]
            for candidate in content_candidates:
                if candidate in WRONG_OR_MISSING_SOURCE_HTML:
                    continue
                csv_raw = (csv_content.get(candidate) or {}).get("content_html", "")
                active_raw_value = (active_documents.get(candidate) or {}).get("content_html", "")
                active_raw = active_raw_value if isinstance(active_raw_value, str) else ""
                if normalize_html(csv_raw):
                    raw_html = csv_raw
                    source_kind = "curated_csv_content_html"
                    source_document_id = candidate
                    break
                if normalize_html(active_raw):
                    raw_html = active_raw
                    source_kind = "active_json_content_html"
                    source_document_id = candidate
                    break
        visible = normalize_html(raw_html)
        if not visible:
            raise ValueError(f"Canonical document has no usable HTML after recovery: {canonical_id}")

        signature_found = compact_identity(metadata.get("so_ky_hieu")) in compact_identity(visible)
        title_token_coverage = coverage(metadata.get("title"), visible, title=True)
        validation_status = (
            "identity_validated_recovery"
            if canonical_id in recovered
            else "source_audit_usable"
            if signature_found and title_token_coverage >= 0.80
            else "source_audit_review_required"
        )
        if not signature_found:
            quality_issues.append({
                "severity": "medium", "entity_type": "document", "entity_id": canonical_id,
                "code": "signature_not_found_in_selected_html",
                "detail": f"Không tìm thấy nguyên dạng số/ký hiệu {metadata.get('so_ky_hieu')!r}; cần review khác biệt định dạng/OCR.",
                "resolution": "Đối chiếu bản chính thức; chỉ thêm signature alias sau review, không tự sửa nội dung.",
            })
        if title_token_coverage < 0.80:
            quality_issues.append({
                "severity": "medium", "entity_type": "document", "entity_id": canonical_id,
                "code": "low_title_html_coverage", "detail": f"Title token coverage={title_token_coverage:.3f}.",
                "resolution": "Review identity và trích yếu trước khi dùng làm bằng chứng answer-ready.",
            })
        if "�" in raw_html or "Ð" in visible:
            quality_issues.append({
                "severity": "medium", "entity_type": "document", "entity_id": canonical_id,
                "code": "encoding_warning", "detail": "HTML có ký tự thay thế hoặc ký tự Ð đáng ngờ.",
                "resolution": "Đối chiếu bản gốc và sửa encoding trong một release mới.",
            })
        if role == "seed_broad_kcb":
            quality_issues.append({
                "severity": "medium", "entity_type": "document", "entity_id": canonical_id,
                "code": "broad_scope_requires_review", "detail": "Chỉ được chọn bởi cụm khám/chữa bệnh rộng.",
                "resolution": "Giữ lexical/index để review nhưng không semantic-search mặc định.",
            })

        legal_status_verified = bool(metadata.get("status_checked_at"))
        if not legal_status_verified:
            quality_issues.append({
                "severity": "medium", "entity_type": "document", "entity_id": canonical_id,
                "code": "legal_status_unverified",
                "detail": "Có nhãn tình trạng hiệu lực nhưng không có status_checked_at từ nguồn kiểm chứng.",
                "resolution": "Không trình bày nhãn này như tình trạng pháp lý hiện tại; crawl nguồn chính thức và ghi ngày kiểm tra.",
            })
            crawl_backlog.append({
                "entity_id": canonical_id,
                "task": "verify_legal_status",
                "priority": "high" if role == "seed_core" else "medium",
                "search_query": f'"{metadata.get("so_ky_hieu", "")}" "{metadata.get("co_quan_ban_hanh", "")}"',
                "preferred_domains": "vbpl.vn;vanban.chinhphu.vn;congbao.chinhphu.vn",
                "acceptance_gate": "Match signature, issuer and issue date; store official URL, status, checked_at and response hash.",
            })
        if validation_status == "source_audit_review_required":
            crawl_backlog.append({
                "entity_id": canonical_id,
                "task": "verify_or_replace_content_html",
                "priority": "high",
                "search_query": f'"{metadata.get("so_ky_hieu", "")}" "{metadata.get("title", "")}"',
                "preferred_domains": "vbpl.vn;vanban.chinhphu.vn;congbao.chinhphu.vn",
                "acceptance_gate": "Match signature, issuer, issue date and title; preserve URL/time/SHA-256 and rerun normalization.",
            })

        metadata.update({
            "canonical_document_id": canonical_id,
            "retrieval_scope": role,
            "selection_reasons": ";".join(reasons),
            "index_eligible": "true" if role in {"seed_core", "seed_broad_kcb"} else "false",
            "lexical_eligible": "true" if role in {"seed_core", "seed_broad_kcb"} else "false",
            "semantic_eligible": "true" if role == "seed_core" else "false",
            "metadata_provenance": "curated_csv" if canonical_id in csv_metadata else "active_json_export",
            "category_provenance": "curated_csv" if explicit_categories else "derived_keyword_v1" if categories else "unknown",
            "content_source_kind": source_kind,
            "content_source_document_id": source_document_id,
            "content_validation_status": validation_status,
            "legal_status_verified": "true" if legal_status_verified else "false",
            "answer_ready": "true" if (
                role == "seed_core"
                and validation_status != "source_audit_review_required"
                and legal_status_verified
            ) else "false",
        })
        canonical_metadata[canonical_id] = metadata
        canonical_categories[canonical_id] = categories
        metadata_output.append(metadata)
        content_output.append({
            "id": canonical_id,
            "agent_category": metadata["agent_category"],
            "content_html": raw_html,
            "source_kind": source_kind,
            "source_document_id": source_document_id,
            "content_html_sha256": sha256(raw_html),
            "visible_text_sha256": sha256(visible),
        })
        provenance_output.append({
            "document_id": canonical_id,
            "source_document_id": source_document_id,
            "source_kind": source_kind,
            "source_url": web_provenance.get("final_url", ""),
            "corroboration_url": web_provenance.get("corroboration_url", ""),
            "corroboration_kind": web_provenance.get("corroboration_kind", ""),
            "retrieved_at_utc": web_provenance.get("retrieved_at_utc", ""),
            "response_sha256": web_provenance.get("response_sha256", ""),
            "content_html_sha256": sha256(raw_html),
            "visible_text_sha256": sha256(visible),
            "signature_found": str(signature_found),
            "title_token_coverage": f"{title_token_coverage:.6f}",
            "validation_status": validation_status,
        })

    alias_output: list[dict[str, Any]] = []
    for rule in ALIAS_RULES:
        canonical_id = resolve_alias(rule.canonical_document_id, alias_map)
        alias_metadata = csv_metadata.get(rule.alias_document_id) or active_documents.get(rule.alias_document_id) or {}
        target_metadata = canonical_metadata[canonical_id]
        alias_output.append({
            **asdict(rule),
            "canonical_document_id": canonical_id,
            "alias_title": clean(alias_metadata.get("title")),
            "canonical_title": target_metadata.get("title", ""),
            "alias_signature": clean(alias_metadata.get("so_ky_hieu")),
            "canonical_signature": target_metadata.get("so_ky_hieu", ""),
        })

    old_edges: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    reference_titles: dict[str, str] = {}
    for row in csv_relationship_rows:
        key = (clean(row.get("doc_id")), clean(row.get("other_doc_id")), clean(row.get("relationship")))
        old_edges[key].append(row)
        for identifier_field, title_field in (("doc_id", "source_title"), ("other_doc_id", "target_title")):
            identifier = clean(row.get(identifier_field))
            title = clean(row.get(title_field))
            if identifier and title and identifier not in reference_titles:
                reference_titles[identifier] = title

    remapped_edges: dict[tuple[str, str, str], list[tuple[str, str, str, dict[str, Any]]]] = defaultdict(list)
    dropped_self_loops = 0
    for source, target, predicate, row in raw_active_edges:
        mapped_source = resolve_alias(source, alias_map)
        mapped_target = resolve_alias(target, alias_map)
        if mapped_source == mapped_target:
            dropped_self_loops += 1
            continue
        remapped_edges[(mapped_source, mapped_target, predicate)].append((source, target, predicate, row))

    relationship_output: list[dict[str, Any]] = []
    relationship_provenance_counts: dict[str, int] = defaultdict(int)
    for (source, target, predicate), originals in sorted(remapped_edges.items()):
        curated_rows = [
            curated
            for original_source, original_target, original_predicate, _ in originals
            for curated in old_edges.get((original_source, original_target, original_predicate), [])
        ]
        if curated_rows and len(curated_rows) == len(originals):
            provenance_status = "curated_csv" if len(originals) == 1 else "curated_csv_after_alias_merge"
        elif curated_rows:
            provenance_status = "mixed_curated_and_active_export"
        else:
            provenance_status = "active_export_only"
        relationship_provenance_counts[provenance_status] += 1

        categories = {category for row in curated_rows for category in split_categories(row.get("agent_category"))}
        if not categories:
            categories.update(canonical_categories.get(source, set()))
            categories.update(canonical_categories.get(target, set()))
        if curated_rows:
            adverse = any(bool_source(row.get("relationship_is_adverse")) for row in curated_rows)
            adverse_provenance = "curated_csv"
        else:
            adverse = predicate in ADVERSE_PREDICATES
            adverse_provenance = "derived_predicate_allowlist_v1"

        source_title = canonical_metadata.get(source, {}).get("title") or reference_titles.get(source, "")
        target_title = canonical_metadata.get(target, {}).get("title") or reference_titles.get(target, "")
        identity = "|".join((source, target, predicate))
        relationship_output.append({
            "agent_category": ",".join(sorted(categories)),
            "doc_id": source,
            "other_doc_id": target,
            "relationship": predicate,
            "source_is_selected": str(source in canonical_ids),
            "target_is_selected": str(target in canonical_ids),
            "relationship_is_adverse": str(adverse),
            "source_title": source_title,
            "target_title": target_title,
            "relationship_id": sha256(identity),
            "provenance_status": provenance_status,
            "adverse_provenance": adverse_provenance,
            "source_row_hashes": ";".join(sorted({source_row_hash(row) for row in curated_rows})),
            "original_edge_count": len(originals),
        })

    endpoint_ids = {identifier for row in relationship_output for identifier in (row["doc_id"], row["other_doc_id"])}
    reference_output = [
        {
            "id": identifier,
            "title": reference_titles.get(identifier, ""),
            "resolution_status": "title_from_legacy_relationship" if reference_titles.get(identifier) else "unresolved_reference",
        }
        for identifier in sorted(endpoint_ids - canonical_ids)
    ]
    unresolved_references = sum(row["resolution_status"] == "unresolved_reference" for row in reference_output)
    degree: dict[str, int] = defaultdict(int)
    for row in relationship_output:
        degree[str(row["doc_id"])] += 1
        degree[str(row["other_doc_id"])] += 1
    for row in reference_output:
        identifier = str(row["id"])
        crawl_backlog.append({
            "entity_id": identifier,
            "task": "hydrate_reference_metadata",
            "priority": "high" if degree[identifier] >= 10 else "medium" if degree[identifier] >= 3 else "low",
            "search_query": clean(row.get("title")) or f'legal document graph endpoint id "{identifier}"',
            "preferred_domains": "vbpl.vn;vanban.chinhphu.vn;congbao.chinhphu.vn",
            "acceptance_gate": "Resolve signature/title/issuer from an official source before promoting to a content document.",
        })

    quality_issues.append({
        "severity": "medium", "entity_type": "duplicate_candidate", "entity_id": "109324|22615",
        "code": "duplicate_identity_requires_human_review",
        "detail": "Hai record gần giống nhưng số/ký hiệu khác dấu gạch; chưa tự động merge.",
        "resolution": "Đối chiếu bản chính thức và lịch sử văn bản trước khi tạo alias.",
    })
    if unresolved_references:
        quality_issues.append({
            "severity": "medium", "entity_type": "reference_summary", "entity_id": "reference_only",
            "code": "unresolved_reference_nodes", "detail": f"{unresolved_references} endpoint chỉ có ID, chưa có title/metadata.",
            "resolution": "Crawl metadata theo hàng đợi ưu tiên graph degree; không tạo chunk/embedding rỗng.",
        })

    rejected_output = [{
        "document_id": "143848",
        "canonical_document_id": "157394",
        "reason": "invalid_source_record_conflicts_with_official_listing",
        "action": "exclude_content_and_metadata_from_index; preserve_alias",
        "evidence_url": next(rule.evidence_url for rule in ALIAS_RULES if rule.alias_document_id == "143848"),
    }]

    atomic_write_csv(output_dir / "metadata.csv", metadata_output, METADATA_FIELDS)
    atomic_write_csv(
        output_dir / "content.csv",
        content_output,
        ("id", "agent_category", "content_html", "source_kind", "source_document_id", "content_html_sha256", "visible_text_sha256"),
        preserve_fields=frozenset({"content_html"}),
    )
    atomic_write_csv(
        output_dir / "relationships.csv",
        relationship_output,
        RELATIONSHIP_BASE_FIELDS + RELATIONSHIP_DERIVED_FIELDS,
    )
    atomic_write_csv(
        output_dir / "aliases.csv",
        alias_output,
        (
            "alias_document_id", "canonical_document_id", "alias_type", "confidence", "reason", "evidence_url",
            "alias_title", "canonical_title", "alias_signature", "canonical_signature",
        ),
    )
    atomic_write_csv(
        output_dir / "source_provenance.csv",
        provenance_output,
        (
            "document_id", "source_document_id", "source_kind", "source_url", "corroboration_url",
            "corroboration_kind", "retrieved_at_utc", "response_sha256", "content_html_sha256",
            "visible_text_sha256", "signature_found", "title_token_coverage", "validation_status",
        ),
    )
    atomic_write_csv(output_dir / "reference_nodes.csv", reference_output, ("id", "title", "resolution_status"))
    atomic_write_csv(
        output_dir / "quality_issues.csv",
        quality_issues,
        ("severity", "entity_type", "entity_id", "code", "detail", "resolution"),
    )
    atomic_write_csv(
        output_dir / "rejected_records.csv",
        rejected_output,
        ("document_id", "canonical_document_id", "reason", "action", "evidence_url"),
    )
    atomic_write_csv(
        output_dir / "crawl_backlog.csv",
        sorted(crawl_backlog, key=lambda row: ({"high": 0, "medium": 1, "low": 2}[str(row["priority"])], str(row["task"]), str(row["entity_id"]))),
        ("entity_id", "task", "priority", "search_query", "preferred_domains", "acceptance_gate"),
    )
    atomic_write_csv(
        output_dir / "metadata_corrections.csv",
        metadata_correction_output,
        ("document_id", "old_signature", "corrected_signature", "old_title", "corrected_title", "evidence"),
    )

    # Re-read the serialized authority artifact. This catches accidental CSV
    # transformations where provenance hashes describe an in-memory value but
    # the canonical pipeline would ingest different HTML bytes.
    serialized_content = index_unique(
        read_csv(output_dir / "content.csv"), "id", "content.csv output"
    )
    for expected_row in content_output:
        identifier = str(expected_row["id"])
        serialized_html = serialized_content[identifier]["content_html"]
        if sha256(serialized_html) != str(expected_row["content_html_sha256"]):
            raise ValueError(f"Serialized HTML hash mismatch: {identifier}")
        if sha256(normalize_html(serialized_html)) != str(expected_row["visible_text_sha256"]):
            raise ValueError(f"Serialized visible-text hash mismatch: {identifier}")

    authority_files = ("metadata.csv", "content.csv", "relationships.csv", "aliases.csv")
    role_counts: dict[str, int] = defaultdict(int)
    for row in metadata_output:
        role_counts[str(row["retrieval_scope"])] += 1
    manifest = {
        "schema_version": 1,
        "build_version": "active-corpus-reconciliation-v1",
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "inputs": {
            "source_dir": str(source_dir),
            "active_docs": str(active_docs_path),
            "active_relationships": str(active_relationships_path),
            "sha256": {
                "metadata.csv": file_sha256(source_dir / "metadata.csv"),
                "content.csv": file_sha256(source_dir / "content.csv"),
                "relationships.csv": file_sha256(source_dir / "relationships.csv"),
                active_docs_path.name: file_sha256(active_docs_path),
                active_relationships_path.name: file_sha256(active_relationships_path),
            },
        },
        "rules": {
            "content_text": "discarded_and_rebuilt_from_selected_html",
            "metadata_precedence": "curated_csv_then_active_json",
            "relationship_membership": "active_relationship_json",
            "semantic_eligibility": "seed_core_only",
            "index_eligibility": "seed_core_and_seed_broad_kcb",
            "adverse_predicates": sorted(ADVERSE_PREDICATES),
        },
        "counts": {
            "input_union_documents": len(all_documents),
            "canonical_documents": len(metadata_output),
            "content_rows": len(content_output),
            "aliases": len(alias_output),
            "recovered_documents": len(recovered),
            "rejected_source_records": len(rejected_output),
            "input_active_relationships": len(raw_active_edges),
            "canonical_relationships": len(relationship_output),
            "alias_collapsed_duplicate_edges": len(raw_active_edges) - dropped_self_loops - len(relationship_output),
            "alias_collapsed_self_loops": dropped_self_loops,
            "reference_nodes": len(reference_output),
            "unresolved_reference_nodes": unresolved_references,
            "open_quality_issues": len(quality_issues),
            "crawl_backlog_tasks": len(crawl_backlog),
            "semantic_eligible_documents": sum(row["semantic_eligible"] == "true" for row in metadata_output),
            "index_eligible_documents": sum(row["index_eligible"] == "true" for row in metadata_output),
            "answer_ready_documents": sum(row["answer_ready"] == "true" for row in metadata_output),
            "metadata_corrections": len(metadata_correction_output),
            "retrieval_scope": dict(sorted(role_counts.items())),
            "relationship_provenance": dict(sorted(relationship_provenance_counts.items())),
        },
        "recovery": {identifier: provenance for identifier, (_, provenance) in sorted(recovered.items())},
        "artifacts": {name: file_sha256(output_dir / name) for name in authority_files},
    }
    manifest["artifact_set_sha256"] = sha256(canonical_json(manifest["artifacts"]))
    atomic_write_json(output_dir / "build_manifest.json", manifest)

    report = f"""# Active corpus v2 — build report

Generated: {manifest['generated_at_utc']}

## Result

- Input document union: {len(all_documents)}.
- Canonical legal documents after alias collapse: {len(metadata_output)}; all have non-empty selected HTML.
- Recovered and identity-validated from the web: {len(recovered)} (`{', '.join(sorted(recovered))}`).
- Rejected bad source record: `143848`; preserved as alias to `157394`.
- Active graph edges: {len(raw_active_edges)} before and {len(relationship_output)} after alias remap/deduplication.
- Reference-only graph nodes: {len(reference_output)}; unresolved title/metadata: {unresolved_references}.
- Semantic-default documents: {manifest['counts']['semantic_eligible_documents']}; lexical/index documents: {manifest['counts']['index_eligible_documents']}.
- Answer-ready for current-law claims: {manifest['counts']['answer_ready_documents']}.

## Deliberate safety rules

- No `content_text` value from the JSON export is used.
- `metadata.csv` wins on overlapping IDs; active JSON only fills documents/fields absent from CSV.
- Relationship JSON decides active membership; legacy CSV only enriches matching edges.
- Broad KCB and graph-context documents are not embedded by default.
- Third-party recovery HTML is accepted only after signature, issuer, title, date-year and minimum-length gates; official URLs are stored as corroboration.

## Still not perfect

- Open document/scope/encoding issues: {len(quality_issues)}; see `quality_issues.csv`.
- Crawl/verification backlog: {len(crawl_backlog)} tasks; see `crawl_backlog.csv`.
- {unresolved_references} graph references still have only an ID. They remain Neo4j-only and must never become empty semantic documents.
- Relationship provenance: {json.dumps(dict(sorted(relationship_provenance_counts.items())), ensure_ascii=False)}.
- The near-duplicate pair `109324` / `22615` remains separate pending legal-source review.

This directory is a candidate authority release, not an automatic live publish. Run the canonical quality gates, create embeddings only for `semantic_eligible=true`, import the same release ID into Neo4j, then switch traffic.
"""
    atomic_write_text(output_dir / "BUILD_REPORT.md", report)
    return manifest


def main() -> int:
    args = parse_args()
    manifest = build_corpus(
        args.source_dir,
        args.active_docs,
        args.active_relationships,
        args.output_dir,
        offline=args.offline,
        refresh_recovery=args.refresh_recovery,
    )
    print(json.dumps({"output_dir": str(args.output_dir), "counts": manifest["counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
