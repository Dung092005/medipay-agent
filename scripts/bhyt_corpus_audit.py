"""Read-only BHYT corpus audit over the six data/raw CSV datasets.

Produces:
  docs/corpus_audit/corpus_audit.csv
  docs/corpus_audit/core_keep.csv
  docs/corpus_audit/other_local_exclude.csv
  docs/corpus_audit/missing_core_documents.csv
  docs/corpus_audit/audit_report.md

Does not modify data/raw or any production dataset.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "docs" / "corpus_audit"
csv.field_size_limit(min(2**31 - 1, 2**31 - 1))

AUDIT_FIELDS = [
    "document_id",
    "document_number",
    "title",
    "classification",
    "national_local_scope",
    "issuing_authority",
    "legal_status",
    "effective_from",
    "effective_to",
    "issued_date",
    "document_type",
    "agent_category",
    "source_provenance",
    "provenance_available",
    "full_content_exists",
    "content_length_chars",
    "in_metadata_bhyt",
    "in_metadata_vien_phi",
    "duplicate_group",
    "local_noise_risk",
    "recommended_serving_status",
    "rationale",
]

KEEP_FIELDS = [
    "document_id",
    "document_number",
    "title",
    "classification",
    "issuing_authority",
    "recommended_serving_status",
    "rationale",
]

EXCLUDE_FIELDS = [
    "document_id",
    "document_number",
    "title",
    "classification",
    "issuing_authority",
    "national_local_scope",
    "local_noise_risk",
    "recommended_serving_status",
    "rationale",
]

MISSING_FIELDS = [
    "priority",
    "document_number",
    "title_or_role",
    "issuing_authority",
    "why_needed",
    "cited_in_eval_candidates",
    "currently_in_corpus",
    "notes",
]


def fold(value: str) -> str:
    value = unicodedata.normalize("NFD", value or "")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return value.casefold()


def clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\ufeff", "")).strip()


def read_csv(name: str) -> list[dict[str, str]]:
    with (RAW / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def normalize_doc_number(value: str) -> str:
    value = fold(clean(value))
    value = value.replace("luật số ", "").replace("so:", "").replace("số:", "")
    value = re.sub(r"\s+", "", value)
    return value


def is_hanoi(row: dict[str, str]) -> bool:
    blob = fold(f"{row.get('co_quan_ban_hanh','')} {row.get('title','')} {row.get('thong_tin_ap_dung','')}")
    return "ha noi" in blob or "hanoi" in blob or "thu do" in blob and "ha noi" in fold(row.get("title", ""))


def is_hospital_fee(row: dict[str, str], in_vp: bool) -> bool:
    title = fold(row.get("title", ""))
    patterns = [
        r"vien phi",
        r"gia dich vu",
        r"muc gia",
        r"gia thu",
        r"dich vu kham benh, chua benh",
        r"dich vu kham benh chua benh",
        r"xet nghiem sars",
        r"khong thuoc.*quy bao hiem",
        r"khong thuoc danh muc do quy",
        r"mot phan vien phi",
        r"dung thu vien phi",
    ]
    if any(re.search(pat, title) for pat in patterns):
        return True
    # Pure viện-phí metadata rows that are clearly fee schedules.
    if in_vp and row.get("pham_vi") == "Địa phương" and re.search(r"gia|vien phi|dich vu", title):
        return True
    return False


def is_national_issuer(row: dict[str, str]) -> bool:
    auth = fold(row.get("co_quan_ban_hanh", ""))
    national_markers = [
        "chinh phu",
        "thu tuong",
        "quoc hoi",
        "bo y te",
        "bo tai chinh",
        "bo lao dong",
        "bo tu phap",
        "bo cong an",
        "bo quoc phong",
        "bo giao duc",
        "toa an nhan dan toi cao",
        "bao hiem xa hoi viet nam",
        "chu tich nuoc",
        "ban chap hanh trung uong",
        "ban to chuc",
    ]
    return any(marker in auth for marker in national_markers)


# Explicit national keep-set (minimal authoritative serving set among present docs).
CORE_KEEP_NUMBERS = {
    "luật số 51/2024/qh15",
    "188/2025/nđ-cp",
    "02/2025/nđ-cp",
    "105/2014/nđ-cp",
    "04/vbhn-byt",
    "37/2024/tt-byt",
    "39/2024/tt-byt",
    "13/2020/tt-byt",
    "24/2025/tt-byt",
    "27/2025/tt-byt",
    "27/2018/tt-byt",
    "04/2021/tt-byt",
    "22/2021/tt-byt",
    "48/2017/tt-byt",
    "12/2026/tt-btc",
    "09/2026/tt-btc",
    "30/2019/tt-blđtbxh",
    "63/2024/nđ-cp",
    "05/2015/ttlt-btp-bca-byt",
}

# Present national docs that are historically superseded / organizational / tangential.
OUT_OF_SCOPE_NUMBERS = {
    "01-tt/lb",
    "15/ttlb",
    "15/1998/ttlt-byt-btc-blđtbxh",
    "15/1998/ttlt-byt-btc-blÐtbxh",
    "40/1998/ttlt-bgdĐT-byt",
    "40/1998/ttlt-bgddt-byt",
    "151/1998/ttlt-btc-byt",
    "07/2002/ttlt-btc-byt",
    "07/2002/ttlt/btc-byt",
    "09/2002/ttlt/btccbcp-blđtbxh-btc-byt",
    "09/2002/ttlt/btccbcp-blÐtbxh-btc-byt",
    "20/2002/qđ-ttg",
    "77/2003/ttlt-btc-byt",
    "07/2003/l-ctn",
    "21/2005/ttlt-byt-btc",
    "196/2005/qđ-ttg",
    "16/2006/ttlt-byt-btc",
    "1008/byt-qđ",
    "170/2008/qđ-ttg",
    "23/2008/l-ctn",
    "87/2010/qđ-ttg",
    "02/2011/qđ-ttg",
    "178/2012/tt-btc",
    "06/2013/tt-byt",
    "68/2013/qh13",
    "538/qđ-ttg",
    "08/2015/qđ-ttg",
    "21/2016/nđ-cp",
    "51/2018/qđ-ttg",
    "05/2019/nq-hđtp",
    "19/2022/qđ-ttg",
    "56/2017/tt-byt",
    "15/2025/tt-byt",
    "06/2026/tt-byt",
    "16/2026/tt-byt",
    "08/2024/tt-byt",
    "107/2025/tt-btc",
    "126/2025/tt-btc",
    "116/2025/tt-btc",
    "233/2025/nđ-cp",
    "21-nq/tw",
}

# Sector-specific / amendment-without-base / mis-scoped national ops → review.
NEEDS_REVIEW_NUMBERS = {
    "70/2015",  # not present as base
    "74/2025/nđ-cp",
    "63/2025/tt-bqp",
    "98/2025/tt-bqp",
    "81/2025/tt-bca",
    "86 /2025/tt-bca",
    "86/2025/tt-bca",
    "99/2024/tt-bca",
    "04/2021/tt-byt",  # suspended by 22/2021
    "1005/qđ-bhxh",
    "1111/qđ-bhxh",
    "2559/qđ-bhxh",
    "82/qđ-bhxh",
}


def classify_row(
    row: dict[str, str],
    *,
    in_bhyt: bool,
    in_vp: bool,
    content_len: int,
    duplicate_group: str,
) -> tuple[str, str, str, str]:
    """Return classification, serving status, local_noise_risk, rationale."""
    number = clean(row.get("so_ky_hieu", ""))
    number_key = normalize_doc_number(number)
    title = clean(row.get("title", ""))
    title_f = fold(title)
    scope = clean(row.get("pham_vi", ""))
    auth = clean(row.get("co_quan_ban_hanh", ""))

    # Exact duplicate twin of another present doc (drop side only).
    if duplicate_group.endswith(":drop"):
        canonical_id = duplicate_group.split(":", 1)[1].removesuffix(":drop")
        rationale = f"Near-duplicate of document_id={canonical_id}; keep one canonical copy only."
        if is_hanoi(row):
            return ("HANOI_LOCAL", "EXCLUDE_FROM_RETRIEVAL", "low", rationale)
        if is_hospital_fee(row, in_vp):
            return ("HOSPITAL_FEE_RELATED", "EXCLUDE_FROM_RETRIEVAL", "high", rationale)
        if scope in {"Địa phương", "Tuyên"} and not is_national_issuer(row):
            return ("OTHER_LOCAL", "EXCLUDE_FROM_RETRIEVAL", "high", rationale)
        return ("NEEDS_REVIEW", "EXCLUDE_FROM_RETRIEVAL", "medium", rationale)

    if is_hanoi(row) and scope != "Trung ương":
        return (
            "HANOI_LOCAL",
            "SERVE_HANOI_ONLY",
            "low",
            "Hà Nội local policy; serve only when query jurisdiction is Hà Nội.",
        )

    if is_hospital_fee(row, in_vp):
        if scope == "Trung ương" or is_national_issuer(row):
            return (
                "HOSPITAL_FEE_RELATED",
                "SERVE_FEE_DOMAIN_ONLY",
                "medium",
                "National/fee-domain text about viện phí or non-BHYT service prices; do not mix into general BHYT retrieval.",
            )
        return (
            "HOSPITAL_FEE_RELATED",
            "EXCLUDE_FROM_RETRIEVAL",
            "high",
            "Local viện phí / service-price schedule; high region-noise risk for national BHYT answers.",
        )

    # Mis-scoped national issuers sitting in local metadata.
    if is_national_issuer(row) and scope != "Trung ương":
        if "bao hiem xa hoi viet nam" in fold(auth):
            return (
                "NEEDS_REVIEW",
                "HOLD_NEEDS_REVIEW",
                "medium",
                "BHXH Việt Nam operational decision mis-tagged as local; useful but needs provenance/scope cleanup before serving.",
            )
        if "ban chap hanh trung uong" in fold(auth):
            return (
                "OUT_OF_SCOPE",
                "EXCLUDE_FROM_RETRIEVAL",
                "high",
                "Party resolution, not a legal instrument for benefit determination.",
            )

    if scope in {"Địa phương", "Tuyên"}:
        return (
            "OTHER_LOCAL",
            "EXCLUDE_FROM_RETRIEVAL",
            "high",
            "Non-Hà Nội local BHYT support/admin rule; excludes from minimal national corpus to avoid region retrieval noise.",
        )

    # National path.
    if number_key in {normalize_doc_number(n) for n in OUT_OF_SCOPE_NUMBERS} or any(
        normalize_doc_number(n) == number_key for n in OUT_OF_SCOPE_NUMBERS
    ):
        return (
            "OUT_OF_SCOPE",
            "EXCLUDE_FROM_RETRIEVAL",
            "medium",
            "Historical, organizational, accounting, criminal, or non-benefit national text; not needed in minimal answering corpus.",
        )

    if any(normalize_doc_number(n) == number_key for n in NEEDS_REVIEW_NUMBERS) or number_key in {
        normalize_doc_number(n) for n in NEEDS_REVIEW_NUMBERS
    }:
        return (
            "NEEDS_REVIEW",
            "HOLD_NEEDS_REVIEW",
            "medium",
            "Sector-specific, suspended, duplicate spelling, or operational national text; review before serving.",
        )

    if any(normalize_doc_number(n) == number_key for n in CORE_KEEP_NUMBERS):
        serve = "SERVE_CORE"
        note = "Core national BHYT instrument retained for minimal authoritative corpus."
        if content_len <= 0:
            serve = "HOLD_NEEDS_REVIEW"
            note = "Core candidate but full content missing; do not serve until content is restored."
        return ("CORE_NATIONAL_BHYT", serve, "low", note)

    # Heuristic fallback for remaining national rows.
    if re.search(r"bao hiem y te|bhyt|kham benh, chua benh bao hiem", title_f):
        if re.search(r"ke toan|thanh tra|to chuc thuc hien|thanh lap|chuyen bao hiem|thi diem gia thuoc|ma hoa benh tat|duoc co truyen|phan cap", title_f):
            return (
                "OUT_OF_SCOPE",
                "EXCLUDE_FROM_RETRIEVAL",
                "medium",
                "National text mentions BHYT but primary purpose is admin/accounting/org/specialty outside minimal benefit Q&A.",
            )
        return (
            "NEEDS_REVIEW",
            "HOLD_NEEDS_REVIEW",
            "medium",
            "National BHYT-related document not in the strict minimal keep-set; review relevance vs. noise.",
        )

    return (
        "OUT_OF_SCOPE",
        "EXCLUDE_FROM_RETRIEVAL",
        "medium",
        "National document outside BHYT benefit-determination core.",
    )


def build_duplicate_groups(rows: list[dict[str, str]]) -> dict[str, str]:
    """Map document id -> duplicate group label. First id in sorted order is keep candidate."""
    by_number: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = normalize_doc_number(row.get("so_ky_hieu", ""))
        if key:
            by_number[key].append(row)

    # Known near-duplicate national pairs. First id is the preferred canonical copy.
    alias_groups = [
        ["22615", "109324"],  # 07/2002 TTLT
        ["21993", "109238"],  # 09/2002 TTLT
        ["169565", "144205"],  # 13/2020 TT-BYT preferred over TT-BYTT spelling
        ["185037", "184708"],  # Hải Phòng 57/2025
        ["13349", "21183"],  # Bắc Ninh 119/2001
    ]
    result: dict[str, str] = {}
    for group in alias_groups:
        keep = group[0]
        for doc_id in group:
            result[doc_id] = f"exact_dup:{keep}" if doc_id == keep else f"exact_dup:{keep}:drop"

    for key, group_rows in by_number.items():
        if len(group_rows) < 2:
            continue
        # Same number across different provinces is not a true duplicate content-wise,
        # but it is a retrieval collision risk.
        authorities = {clean(r.get("co_quan_ban_hanh", "")) for r in group_rows}
        if len(authorities) > 1:
            for row in group_rows:
                result.setdefault(row["id"], f"number_collision:{key}")
            continue
        # Same issuer + same number → true duplicate.
        keep = sorted(group_rows, key=lambda r: r["id"])[0]["id"]
        for row in group_rows:
            result[row["id"]] = f"exact_dup:{keep}" if row["id"] == keep else f"exact_dup:{keep}:drop"
    return result


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def missing_core_rows() -> list[dict[str, str]]:
    return [
        {
            "priority": "P0",
            "document_number": "25/2008/QH12",
            "title_or_role": "Luật Bảo hiểm y tế (văn bản gốc)",
            "issuing_authority": "Quốc hội",
            "why_needed": "Base statute for rights, eligibility, benefit levels; amendment 51/2024 alone is incomplete.",
            "cited_in_eval_candidates": "yes (as Luật BHYT / articles)",
            "currently_in_corpus": "no",
            "notes": "Corpus only has Luật số 51/2024/QH15 amending law.",
        },
        {
            "priority": "P0",
            "document_number": "46/2014/QH13",
            "title_or_role": "Luật sửa đổi, bổ sung một số điều của Luật BHYT",
            "issuing_authority": "Quốc hội",
            "why_needed": "Most frequently cited law in BHXH Q&A eval set for benefit/route rules.",
            "cited_in_eval_candidates": "yes (very frequent)",
            "currently_in_corpus": "no",
            "notes": "Critical gap for answering historical and still-referenced benefit questions.",
        },
        {
            "priority": "P0",
            "document_number": "146/2018/NĐ-CP",
            "title_or_role": "Nghị định hướng dẫn Luật BHYT",
            "issuing_authority": "Chính phủ",
            "why_needed": "Primary implementing decree for contribution, entitlement, procedures.",
            "cited_in_eval_candidates": "yes (top cited decree)",
            "currently_in_corpus": "no",
            "notes": "Only later amending decrees 75/2023 (missing) and 02/2025 (present) exist as references.",
        },
        {
            "priority": "P0",
            "document_number": "75/2023/NĐ-CP",
            "title_or_role": "Nghị định sửa đổi Nghị định 146/2018/NĐ-CP",
            "issuing_authority": "Chính phủ",
            "why_needed": "Updates key entitlement/contribution rules still cited via 146 chain.",
            "cited_in_eval_candidates": "indirect",
            "currently_in_corpus": "no",
            "notes": "Referenced by present 02/2025/NĐ-CP but base texts absent.",
        },
        {
            "priority": "P0",
            "document_number": "40/2015/TT-BYT",
            "title_or_role": "Đăng ký KCB ban đầu và chuyển tuyến",
            "issuing_authority": "Bộ Y tế",
            "why_needed": "Core for đúng tuyến / thông tuyến / chuyển tuyến questions.",
            "cited_in_eval_candidates": "yes",
            "currently_in_corpus": "no",
            "notes": "Highest-value missing circular for patient-facing BHYT Q&A.",
        },
        {
            "priority": "P0",
            "document_number": "41/2014/TTLT-BYT-BTC",
            "title_or_role": "Hướng dẫn thực hiện BHYT (liên tịch)",
            "issuing_authority": "Bộ Y tế, Bộ Tài chính",
            "why_needed": "Frequently cited operational guidance in official BHXH answers.",
            "cited_in_eval_candidates": "yes",
            "currently_in_corpus": "no",
            "notes": "Often paired with Luật 46/2014 and NĐ 146/2018 in answers.",
        },
        {
            "priority": "P0",
            "document_number": "35/2016/TT-BYT",
            "title_or_role": "Danh mục và điều kiện thanh toán dịch vụ kỹ thuật BHYT",
            "issuing_authority": "Bộ Y tế",
            "why_needed": "Base technical-service payment list; corpus only has amending circulars.",
            "cited_in_eval_candidates": "indirect",
            "currently_in_corpus": "no",
            "notes": "Present amendments: 13/2020, 39/2024.",
        },
        {
            "priority": "P0",
            "document_number": "04/2017/TT-BYT",
            "title_or_role": "Danh mục và điều kiện thanh toán vật tư y tế BHYT",
            "issuing_authority": "Bộ Y tế",
            "why_needed": "Base medical-device payment list; only 24/2025 amendment present.",
            "cited_in_eval_candidates": "indirect",
            "currently_in_corpus": "no",
            "notes": "Amendment without base creates incomplete evidence chains.",
        },
        {
            "priority": "P1",
            "document_number": "14/2014/TT-BYT",
            "title_or_role": "Chuyển tuyến giữa cơ sở KCB",
            "issuing_authority": "Bộ Y tế",
            "why_needed": "Cited for transfer/referral pathway questions.",
            "cited_in_eval_candidates": "yes",
            "currently_in_corpus": "no",
            "notes": "Complement to 40/2015/TT-BYT.",
        },
        {
            "priority": "P1",
            "document_number": "595/QĐ-BHXH",
            "title_or_role": "Quy trình thu BHXH/BHYT và cấp sổ/thẻ",
            "issuing_authority": "BHXH Việt Nam",
            "why_needed": "Operational source heavily cited in official answers for card/participation procedures.",
            "cited_in_eval_candidates": "yes",
            "currently_in_corpus": "no",
            "notes": "Older BHXH QĐs exist but not this key decision.",
        },
        {
            "priority": "P1",
            "document_number": "70/2015/NĐ-CP",
            "title_or_role": "BHYT đối với Quân đội / Công an / cơ yếu",
            "issuing_authority": "Chính phủ",
            "why_needed": "Base decree for armed-forces BHYT; only amending 74/2025 and sector circulars present.",
            "cited_in_eval_candidates": "low",
            "currently_in_corpus": "no",
            "notes": "Needed only if serving military/police questions; else keep those circulars out of core.",
        },
        {
            "priority": "P1",
            "document_number": "consol./current consolidated BHYT law text",
            "title_or_role": "Văn bản hợp nhất Luật BHYT sau 51/2024 (nếu có bản chính thức)",
            "issuing_authority": "Quốc hội / cơ quan công bố",
            "why_needed": "Single readable current law text reduces retrieval fragmentation across 25/2008+46/2014+51/2024.",
            "cited_in_eval_candidates": "n/a",
            "currently_in_corpus": "no",
            "notes": "Prefer official consolidated text over multiple amendment layers when available.",
        },
        {
            "priority": "P2",
            "document_number": "25/2016/TT-BLĐTBXH",
            "title_or_role": "Hướng dẫn đối tượng do ngành LĐTBXH quản lý",
            "issuing_authority": "Bộ LĐTBXH",
            "why_needed": "Appears in eval answers for social-policy beneficiary lists.",
            "cited_in_eval_candidates": "yes",
            "currently_in_corpus": "no",
            "notes": "Optional once P0 set is complete; 30/2019/TT-BLĐTBXH already present.",
        },
    ]


def write_report(
    path: Path,
    *,
    audit_rows: list[dict[str, str]],
    keep_rows: list[dict[str, str]],
    exclude_rows: list[dict[str, str]],
    missing_rows: list[dict[str, str]],
    file_stats: dict[str, int],
) -> None:
    class_counts = Counter(r["classification"] for r in audit_rows)
    serve_counts = Counter(r["recommended_serving_status"] for r in audit_rows)
    provenance_missing = sum(1 for r in audit_rows if r["provenance_available"] == "no")
    content_missing = sum(1 for r in audit_rows if r["full_content_exists"] == "no")
    content_short = sum(1 for r in audit_rows if r["full_content_exists"] == "short")
    noise_high = sum(1 for r in audit_rows if r["local_noise_risk"] == "high")
    dup_rows = [r for r in audit_rows if r["duplicate_group"]]

    lines = [
        "# BHYT Legal Corpus Audit",
        "",
        "Date: 2026-08-24",
        "Scope: read-only inspection of the six authoritative CSVs under `data/raw/`.",
        "Constraint: no crawl, embed, delete, or modification of production data.",
        "",
        "## Goal",
        "",
        "Design the **smallest authoritative corpus** that can answer Vietnamese BHYT questions accurately.",
        "Do not maximize corpus size. Prefer excluding local-region noise over retaining marginal documents.",
        "",
        "## Source files inspected",
        "",
        "| File | Rows | Role |",
        "|---|---:|---|",
        f"| `metadata.csv` | {file_stats['metadata']} | Authority metadata for all documents |",
        f"| `metadata_bhyt.csv` | {file_stats['metadata_bhyt']} | BHYT projection |",
        f"| `metadata_vien_phi.csv` | {file_stats['metadata_vien_phi']} | Viện phí projection |",
        f"| `content.csv` | {file_stats['content']} | HTML content store |",
        f"| `documents.csv` | {file_stats['documents']} | Joined metadata+content projection |",
        f"| `relationships.csv` | {file_stats['relationships']} | Legal graph edges |",
        "",
        "## Classification summary",
        "",
        "| Classification | Count |",
        "|---|---:|",
    ]
    for key in [
        "CORE_NATIONAL_BHYT",
        "HANOI_LOCAL",
        "OTHER_LOCAL",
        "HOSPITAL_FEE_RELATED",
        "OUT_OF_SCOPE",
        "NEEDS_REVIEW",
    ]:
        lines.append(f"| {key} | {class_counts.get(key, 0)} |")
    lines.extend(
        [
            "",
            f"Total documents audited: **{len(audit_rows)}**",
            "",
            "## Recommended serving status",
            "",
            "| Status | Count |",
            "|---|---:|",
        ]
    )
    for key, value in sorted(serve_counts.items()):
        lines.append(f"| {key} | {value} |")

    lines.extend(
        [
            "",
            "## Minimal corpus recommendation",
            "",
            "Serve only:",
            "",
            "1. **SERVE_CORE** national BHYT instruments in `core_keep.csv`",
            "2. **Missing P0 documents** listed in `missing_core_documents.csv` (must be acquired before claiming coverage)",
            "3. Optionally **SERVE_HANOI_ONLY** if the product explicitly answers Hà Nội local support questions",
            "4. Optionally **SERVE_FEE_DOMAIN_ONLY** national fee texts if a separate viện-phí agent is retained",
            "",
            "Exclude by default:",
            "",
            "- All `OTHER_LOCAL` province/city documents (`other_local_exclude.csv`)",
            "- Local hospital-fee schedules",
            "- Historical pre-2014/2015 superseded national guidance",
            "- Criminal, accounting, organizational, and party documents",
            "",
            f"Proposed keep-now count (present docs with SERVE_CORE): **{sum(1 for r in keep_rows if r['recommended_serving_status']=='SERVE_CORE')}**",
            f"Proposed exclude-now count (OTHER_LOCAL + excluded fee/local noise): **{len(exclude_rows)}**",
            f"Missing P0/P1/P2 instruments to add later: **{len(missing_rows)}**",
            "",
            "## Critical gaps (missing core)",
            "",
            "These documents are absent but required for accurate BHYT answering. Eval-candidate citations confirm demand:",
            "",
        ]
    )
    for row in missing_rows:
        if row["priority"] == "P0":
            lines.append(
                f"- **{row['document_number']}** — {row['title_or_role']} ({row['why_needed']})"
            )
    lines.extend(
        [
            "",
            "Without Luật 25/2008 + 46/2014, Nghị định 146/2018 (+75/2023), and Thông tư 40/2015, the system cannot reliably answer the most common entitlement / tuyến / đóng-hưởng questions even if local noise is removed.",
            "",
            "## Duplicate / overlapping documents",
            "",
            f"Documents carrying a duplicate/collision label: **{len(dup_rows)}**",
            "",
            "High-priority overlaps to resolve:",
            "",
            "- `07/2002/TTLT-BTC-BYT` appears twice (`109324`, `22615`)",
            "- `09/2002/TTLT/...` appears twice with character-variant spelling (`109238`, `21993`)",
            "- `13/2020/TT-BYT` vs `13/2020/TT-BYTT` (`169565`, `144205`) — likely same circular",
            "- Hải Phòng `57/2025/NQ-HĐND` duplicated (`185037`, `184708`)",
            "- Bắc Ninh `119/2001/QĐ-UB` duplicated (`13349`, `21183`)",
            "- Many identical `so_ky_hieu` values across provinces (e.g. `30/2024/NQ-HĐND` x4) create exact-match collisions if document-number search is not authority-scoped",
            "",
            "Also present are amendment documents without their base text (e.g. amendments to 35/2016, 04/2017, 146/2018, 70/2015).",
            "",
            "## Local-region retrieval noise",
            "",
            f"High local-noise-risk documents: **{noise_high}**",
            "",
            "Almost all non-Hà Nội `Địa phương` rows are province-specific hỗ trợ đóng BHYT, quy chế phối hợp, or local fee schedules.",
            "They share vocabulary with national BHYT questions (`mức hưởng`, `hỗ trợ đóng`, `đúng tuyến`) and will dilute ranking if left in the default index.",
            "",
            "Hà Nội currently has 3 local resolutions; keep them behind a jurisdiction gate, not in the national default index.",
            "",
            "## Provenance and content completeness",
            "",
            f"- Empty `nguon_thu_thap` (missing provenance string): **{provenance_missing}/{len(audit_rows)}**",
            f"- Missing full content in `content.csv`: **{content_missing}**",
            f"- Extremely short content (<300 chars HTML): **{content_short}**",
            "",
            "Notable content gaps:",
            "",
            "- `187782` / `107/2025/TT-BTC` — metadata present, content absent",
            "- `157394` and `143848` (Ninh Thuận fee QĐs) — stub-length content",
            "",
            "All rows currently claim `tinh_trang_hieu_luc = Còn hiệu lực`, which is not credible for 1990s–2000s superseded instruments. Legal-status metadata needs independent verification before serving.",
            "",
            "## Scope anomalies",
            "",
            "- `95267` has `pham_vi = Tuyên` (truncated Tuyên Quang)",
            "- 4 BHXH Việt Nam decisions and Party resolution `21-NQ/TW` are tagged `Địa phương` despite national issuers",
            "",
            "## Deliverables",
            "",
            "| File | Purpose |",
            "|---|---|",
            "| `corpus_audit.csv` | One row per current document with full audit fields |",
            "| `core_keep.csv` | Minimal present docs recommended to keep/serve |",
            "| `other_local_exclude.csv` | Local/noise docs recommended to exclude from default retrieval |",
            "| `missing_core_documents.csv` | Authoritative documents to acquire later (not present now) |",
            "| `audit_report.md` | This summary |",
            "",
            "## Next actions (no data changes yet)",
            "",
            "1. Acquire P0 missing national instruments and verify official provenance.",
            "2. Rebuild a staging dataset containing only SERVE_CORE (+ optional Hà Nội/fee domains).",
            "3. Re-run BHYT eval candidates against the reduced corpus before any production publish.",
            "4. Do not embed or publish local province support schedules into the default BHYT index.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    metadata = read_csv("metadata.csv")
    bhyt_ids = {r["id"] for r in read_csv("metadata_bhyt.csv")}
    vp_ids = {r["id"] for r in read_csv("metadata_vien_phi.csv")}
    content_rows = read_csv("content.csv")
    documents = read_csv("documents.csv")
    relationships = read_csv("relationships.csv")
    content_by_id = {r["id"]: r for r in content_rows}
    dup_groups = build_duplicate_groups(metadata)

    audit_rows: list[dict[str, str]] = []
    for row in metadata:
        doc_id = row["id"]
        html = clean(content_by_id.get(doc_id, {}).get("content_html", ""))
        if not html:
            # documents.csv may still carry content for projection checks
            html = clean(next((d.get("content_html", "") for d in documents if d["id"] == doc_id), ""))
        content_len = len(html)
        if content_len == 0:
            full_content = "no"
        elif content_len < 300:
            full_content = "short"
        else:
            full_content = "yes"
        provenance = clean(row.get("nguon_thu_thap", ""))
        in_bhyt = doc_id in bhyt_ids
        in_vp = doc_id in vp_ids
        duplicate_group = dup_groups.get(doc_id, "")
        classification, serving, noise, rationale = classify_row(
            row,
            in_bhyt=in_bhyt,
            in_vp=in_vp,
            content_len=content_len,
            duplicate_group=duplicate_group,
        )
        scope = clean(row.get("pham_vi", ""))
        if is_national_issuer(row) and scope != "Trung ương":
            scope_label = f"{scope} (issuer appears national)"
        else:
            scope_label = scope or "unknown"

        audit_rows.append(
            {
                "document_id": doc_id,
                "document_number": clean(row.get("so_ky_hieu", "")),
                "title": clean(row.get("title", "")),
                "classification": classification,
                "national_local_scope": scope_label,
                "issuing_authority": clean(row.get("co_quan_ban_hanh", "")),
                "legal_status": clean(row.get("tinh_trang_hieu_luc", "")),
                "effective_from": clean(row.get("ngay_co_hieu_luc", "")),
                "effective_to": clean(row.get("ngay_het_hieu_luc", "")),
                "issued_date": clean(row.get("ngay_ban_hanh", "")),
                "document_type": clean(row.get("loai_van_ban", "")),
                "agent_category": clean(row.get("agent_category", "")),
                "source_provenance": provenance,
                "provenance_available": "yes" if provenance else "no",
                "full_content_exists": full_content,
                "content_length_chars": str(content_len),
                "in_metadata_bhyt": "yes" if in_bhyt else "no",
                "in_metadata_vien_phi": "yes" if in_vp else "no",
                "duplicate_group": duplicate_group,
                "local_noise_risk": noise,
                "recommended_serving_status": serving,
                "rationale": rationale,
            }
        )

    keep_rows = [
        r
        for r in audit_rows
        if r["recommended_serving_status"] in {"SERVE_CORE", "SERVE_HANOI_ONLY", "SERVE_FEE_DOMAIN_ONLY"}
        or (r["classification"] == "CORE_NATIONAL_BHYT" and r["recommended_serving_status"] == "HOLD_NEEDS_REVIEW")
    ]
    # Prefer a strict core_keep focused on national core + hanoi optional.
    keep_rows = [
        r
        for r in audit_rows
        if r["classification"] in {"CORE_NATIONAL_BHYT", "HANOI_LOCAL"}
        or (
            r["classification"] == "HOSPITAL_FEE_RELATED"
            and r["recommended_serving_status"] == "SERVE_FEE_DOMAIN_ONLY"
        )
    ]

    exclude_rows = [
        r
        for r in audit_rows
        if r["classification"] in {"OTHER_LOCAL", "HOSPITAL_FEE_RELATED"}
        and r["recommended_serving_status"] == "EXCLUDE_FROM_RETRIEVAL"
    ]

    missing_rows = missing_core_rows()
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "corpus_audit.csv", AUDIT_FIELDS, audit_rows)
    write_csv(OUT / "core_keep.csv", KEEP_FIELDS, keep_rows)
    write_csv(OUT / "other_local_exclude.csv", EXCLUDE_FIELDS, exclude_rows)
    write_csv(OUT / "missing_core_documents.csv", MISSING_FIELDS, missing_rows)
    write_report(
        OUT / "audit_report.md",
        audit_rows=audit_rows,
        keep_rows=keep_rows,
        exclude_rows=exclude_rows,
        missing_rows=missing_rows,
        file_stats={
            "metadata": len(metadata),
            "metadata_bhyt": len(bhyt_ids),
            "metadata_vien_phi": len(vp_ids),
            "content": len(content_rows),
            "documents": len(documents),
            "relationships": len(relationships),
        },
    )

    counts = Counter(r["classification"] for r in audit_rows)
    print("Wrote audit to", OUT)
    print("class_counts", dict(counts))
    print("core_keep", len(keep_rows), "exclude", len(exclude_rows), "missing", len(missing_rows))


if __name__ == "__main__":
    main()
