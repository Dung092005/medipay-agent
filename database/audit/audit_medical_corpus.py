#!/usr/bin/env python3
"""Read-only audit for the BHYT / medical-fee legal corpus.

The audit reconciles four sources without mutating any of them:

* curated CSV files in ``csv_admin_bhyt_vien_phi/source_originals``;
* the full-document active JSON export;
* the active-relationship JSON export;
* optional PostgreSQL and Neo4j runtime inventories.

It deliberately treats raw HTML as source material and ``content_text`` as a
derived projection.  Reports contain hashes, lengths, classifications and
short metadata only; raw legal content and credentials are never copied to the
output directory.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import csv
import datetime as dt
import hashlib
import itertools
import json
import math
import os
import re
import sys
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

csv.field_size_limit(sys.maxsize)

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_ROOT = REPO_ROOT / "database" / "pipeline"
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from data_pipeline.canonical import normalize_html  # noqa: E402, I001


DEFAULT_SOURCE_DIR = Path("/home/minh/projects/csv_admin_bhyt_vien_phi/source_originals")
DEFAULT_ACTIVE_DOCS = REPO_ROOT / "database" / "medical_docs_active.json"
DEFAULT_ACTIVE_RELATIONSHIPS = REPO_ROOT / "database" / "medical_relationships_active.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "database" / "audit" / "results"

WORD_RE = re.compile(r"[\wÀ-ỹ]+", flags=re.UNICODE)
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    flags=re.IGNORECASE,
)
SPACE_RE = re.compile(r"\s+")
IDENTIFIER_RE = re.compile(r"[^0-9a-zà-ỹđ]+", flags=re.IGNORECASE)

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

CSV_METADATA_FIELDS = (
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

ACTIVE_REQUIRED_METADATA_FIELDS = (
    "title",
    "so_ky_hieu",
    "loai_van_ban",
    "co_quan_ban_hanh",
    "ngay_ban_hanh",
    "tinh_trang_hieu_luc",
)


@dataclass(frozen=True)
class DuplicateCandidate:
    left_id: str
    right_id: str
    confidence: str
    title_similarity: float
    content_similarity: float
    recommended_canonical_id: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--active-docs", type=Path, default=DEFAULT_ACTIVE_DOCS)
    parser.add_argument(
        "--active-relationships", type=Path, default=DEFAULT_ACTIVE_RELATIONSHIPS
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--with-database",
        action="store_true",
        help="Read the active PostgreSQL inventory using DATABASE_URL from .env.",
    )
    parser.add_argument(
        "--with-neo4j",
        action="store_true",
        help="Read the current Neo4j node/edge inventory using NEO4J_* from .env.",
    )
    parser.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env")
    return parser.parse_args()


def is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def clean(value: Any) -> str:
    if value is None or is_nan(value):
        return ""
    normalized = unicodedata.normalize("NFC", str(value).replace("\ufeff", ""))
    return SPACE_RE.sub(" ", normalized).strip()


def normalized_identity(value: Any) -> str:
    return SPACE_RE.sub(" ", IDENTIFIER_RE.sub(" ", clean(value).casefold())).strip()


def sha256(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def read_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"Expected a JSON array of objects: {path}")
    return value


def index_unique(rows: Iterable[dict[str, Any]], field: str, source: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for number, row in enumerate(rows, start=2):
        identifier = clean(row.get(field))
        if not identifier:
            raise ValueError(f"{source} row {number} has an empty {field}")
        if identifier in result:
            raise ValueError(f"{source} has duplicate {field}: {identifier}")
        result[identifier] = row
    return result


def ordered_contains(value: str, first: str, second: str) -> bool:
    first_at = value.find(first)
    return first_at >= 0 and second in value[first_at + len(first) :]


def filter_reasons(document: dict[str, Any]) -> list[str]:
    """Reproduce the user-provided SQL LIKE predicates exactly."""

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
        (
            "title_thanh_toan_bao_hiem_y_te",
            ordered_contains(title, "thanh toán", "bảo hiểm y tế"),
        ),
        ("title_gia_dich_vu_kham", "giá dịch vụ khám" in title),
        ("title_gia_dich_vu_y_te", "giá dịch vụ y tế" in title),
        ("field_bao_hiem_y_te", "bảo hiểm y tế" in field),
    )
    return [name for name, matched in checks if matched]


def token_counter(value: str) -> collections.Counter[str]:
    return collections.Counter(WORD_RE.findall(unicodedata.normalize("NFC", value).casefold()))


def cosine(left: collections.Counter[str], right: collections.Counter[str]) -> float:
    left_norm = sum(count * count for count in left.values())
    right_norm = sum(count * count for count in right.values())
    if not left_norm or not right_norm:
        return 1.0 if not left_norm and not right_norm else 0.0
    dot = sum(count * right.get(term, 0) for term, count in left.items())
    return dot / math.sqrt(left_norm * right_norm)


def token_set(value: Any, *, remove_title_stop_words: bool = False) -> set[str]:
    tokens = set(WORD_RE.findall(clean(value).casefold()))
    if remove_title_stop_words:
        tokens = {token for token in tokens if len(token) > 2 and token not in TITLE_STOP_WORDS}
    return tokens


def jaccard(left: Any, right: Any) -> float:
    left_tokens = token_set(left)
    right_tokens = token_set(right)
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 1.0


def title_coverage(title: Any, visible_text: str) -> float:
    title_tokens = token_set(title, remove_title_stop_words=True)
    if not title_tokens:
        return 1.0
    visible_tokens = token_set(visible_text)
    return len(title_tokens & visible_tokens) / len(title_tokens)


def normalized_signature_found(signature: Any, visible_text: str) -> bool:
    expected = normalized_identity(signature).replace(" ", "")
    actual = normalized_identity(visible_text).replace(" ", "")
    return bool(expected) and expected in actual


def parse_date(value: Any) -> dt.date | None | str:
    raw = clean(value)
    if not raw:
        return None
    for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw, pattern).date()
        except ValueError:
            pass
    return "invalid"


def json_safe(value: Any) -> Any:
    if value is None or is_nan(value):
        return None
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


async def read_postgres_inventory(env_file: Path) -> dict[str, Any]:
    from dotenv import load_dotenv
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    load_dotenv(env_file, override=False)
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={
            "timeout": 15,
            "command_timeout": 40,
            "server_settings": {
                "default_transaction_read_only": "on",
                "statement_timeout": "40000",
                "application_name": "medical-corpus-audit",
            },
        },
    )
    try:
        async with engine.connect() as connection:
            dataset = (
                await connection.execute(
                    text(
                        """
                        SELECT d.dataset_id, d.fingerprint, d.status, d.created_at,
                               d.published_at, d.manifest
                        FROM datasets d
                        JOIN dataset_state s ON s.active_dataset_id = d.dataset_id
                        WHERE s.singleton = TRUE
                        """
                    )
                )
            ).mappings().first()
            if dataset is None:
                return {"dataset": None, "documents": {}, "metrics": {}}

            document_rows = (
                await connection.execute(
                    text(
                        """
                        SELECT id, title, is_external, content_available,
                               text_sha256, raw_html_sha256,
                               length(content_text) AS text_length,
                               length(raw_html) AS html_length
                        FROM documents
                        WHERE dataset_id = :dataset_id
                        """
                    ),
                    {"dataset_id": dataset["dataset_id"]},
                )
            ).mappings().all()

            chunk_metrics = (
                await connection.execute(
                    text(
                        """
                        SELECT count(*) AS total,
                               count(DISTINCT document_id) AS documents,
                               count(*) FILTER (WHERE length(btrim(text)) < 20) AS under_20_chars,
                               count(*) FILTER (WHERE length(btrim(text)) < 50) AS under_50_chars,
                               count(*) FILTER (WHERE length(btrim(text)) < 100) AS under_100_chars,
                               count(*) FILTER (WHERE search_vector::text = '') AS empty_fts
                        FROM chunks WHERE dataset_id = :dataset_id
                        """
                    ),
                    {"dataset_id": dataset["dataset_id"]},
                )
            ).mappings().one()

            duplicate_chunks = (
                await connection.execute(
                    text(
                        """
                        SELECT count(*) AS groups,
                               coalesce(sum(n - 1), 0) AS duplicate_excess,
                               coalesce(max(n), 0) AS largest_group
                        FROM (
                            SELECT md5(text), count(*) AS n
                            FROM chunks WHERE dataset_id = :dataset_id
                            GROUP BY 1 HAVING count(*) > 1
                        ) duplicates
                        """
                    ),
                    {"dataset_id": dataset["dataset_id"]},
                )
            ).mappings().one()

            unit_metrics = (
                await connection.execute(
                    text(
                        """
                        SELECT count(*) AS total,
                               count(*) FILTER (WHERE btrim(text) = '') AS empty_text,
                               count(*) FILTER (
                                   WHERE source_start IS NULL OR source_end IS NULL
                               ) AS missing_source_span
                        FROM legal_units WHERE dataset_id = :dataset_id
                        """
                    ),
                    {"dataset_id": dataset["dataset_id"]},
                )
            ).mappings().one()

        return {
            "dataset": dict(dataset),
            "documents": {clean(row["id"]): dict(row) for row in document_rows},
            "metrics": {
                "chunks": dict(chunk_metrics),
                "duplicate_chunks": dict(duplicate_chunks),
                "legal_units": dict(unit_metrics),
            },
        }
    finally:
        await engine.dispose()


def read_neo4j_inventory(env_file: Path, dataset_id: str | None) -> dict[str, Any]:
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv(env_file, override=False)
    uri = os.getenv("NEO4J_URI", "")
    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    if not uri or not password:
        raise RuntimeError("NEO4J_URI and NEO4J_PASSWORD are required")

    driver = GraphDatabase.driver(uri, auth=(username, password), connection_timeout=15)
    try:
        with driver.session(database=database) as session:
            node_query = "MATCH (n:Document)"
            edge_query = "MATCH (source:Document)-[r]->(target:Document)"
            parameters: dict[str, Any] = {}
            if dataset_id:
                node_query += " WHERE n.dataset_id = $dataset_id"
                edge_query += " WHERE r.dataset_id = $dataset_id"
                parameters["dataset_id"] = dataset_id
            node_query += " RETURN n.id AS id, n.graph_id AS graph_id"
            edge_query += (
                " RETURN source.id AS source_id, target.id AS target_id,"
                " coalesce(r.relationship_type, type(r)) AS relationship_type"
            )
            nodes = [dict(row) for row in session.run(node_query, **parameters)]
            edges = [dict(row) for row in session.run(edge_query, **parameters)]
        return {
            "nodes": {clean(row["id"]) for row in nodes if clean(row.get("id"))},
            "edges": {
                (
                    clean(row["source_id"]),
                    clean(row["target_id"]),
                    clean(row["relationship_type"]),
                )
                for row in edges
            },
        }
    finally:
        driver.close()


def choose_duplicate_canonical(
    left: dict[str, Any],
    right: dict[str, Any],
    csv_ids: set[str],
    db_documents: dict[str, dict[str, Any]],
    visible_by_id: dict[str, str],
) -> str:
    def score(row: dict[str, Any]) -> tuple[int, int, int, str]:
        identifier = clean(row["id"])
        db_row = db_documents.get(identifier, {})
        return (
            int(identifier in csv_ids),
            int(bool(db_row) and not bool(db_row.get("is_external"))),
            len(visible_by_id.get(identifier, "")),
            identifier,
        )

    return max((left, right), key=score)["id"]


def find_duplicate_candidates(
    active_documents: dict[str, dict[str, Any]],
    visible_by_id: dict[str, str],
    csv_ids: set[str],
    db_documents: dict[str, dict[str, Any]],
) -> list[DuplicateCandidate]:
    by_signature: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in active_documents.values():
        by_signature[normalized_identity(row.get("so_ky_hieu"))].append(row)

    candidates: list[DuplicateCandidate] = []
    for rows in by_signature.values():
        if len(rows) < 2:
            continue
        for left, right in itertools.combinations(rows, 2):
            title_similarity = jaccard(left.get("title"), right.get("title"))
            if title_similarity < 0.90:
                continue
            left_id = clean(left["id"])
            right_id = clean(right["id"])
            content_similarity = cosine(
                token_counter(visible_by_id.get(left_id, "")),
                token_counter(visible_by_id.get(right_id, "")),
            )
            if title_similarity >= 0.95 and content_similarity >= 0.99:
                confidence = "high"
            elif content_similarity >= 0.90:
                confidence = "review"
            else:
                continue
            candidates.append(
                DuplicateCandidate(
                    left_id=left_id,
                    right_id=right_id,
                    confidence=confidence,
                    title_similarity=round(title_similarity, 6),
                    content_similarity=round(content_similarity, 6),
                    recommended_canonical_id=clean(
                        choose_duplicate_canonical(
                            left, right, csv_ids, db_documents, visible_by_id
                        )
                    ),
                )
            )
    return sorted(candidates, key=lambda item: (item.confidence, item.left_id, item.right_id))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: json_safe(row.get(field, "")) for field in fieldnames})


def add_issue(
    issues: list[dict[str, str]],
    *,
    severity: str,
    entity_type: str,
    entity_id: str,
    code: str,
    detail: str,
    resolution: str,
) -> None:
    issues.append(
        {
            "severity": severity,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "code": code,
            "detail": detail,
            "resolution": resolution,
        }
    )


def build_report(summary: dict[str, Any]) -> str:
    csv_counts = summary["csv"]
    active = summary["active_documents"]
    content = summary["content_quality"]
    relationships = summary["relationships"]
    database = summary.get("database") or {}
    neo4j = summary.get("neo4j") or {}
    duplicate_rows = summary["duplicate_candidates"]
    quarantine = ", ".join(f"`{item}`" for item in content["quarantine_ids"])

    db_section = "Database runtime was not requested."
    if database:
        db_section = f"""- Active dataset: `{database['dataset_id']}`.
- PostgreSQL có {database['document_records']} document rows: {database['canonical_documents']} canonical và {database['external_references']} external references.
- Trong 689 active JSON documents: {database['active_as_existing_canonical']} đã là canonical, {database['active_as_external_reference']} có thể promote từ stub và {database['active_absent']} chưa có trong DB.
- Đã đối chiếu hash {database['csv_content_hash_compared']} document với CSV: {database['csv_text_hash_mismatch']} text-hash mismatch và {database['csv_html_hash_mismatch']} raw-HTML-hash mismatch.
- Retrieval hiện có {database['chunks']['total']} chunks: {database['chunks']['under_20_chars']} ngắn dưới 20 ký tự, {database['chunks']['under_50_chars']} dưới 50, {database['chunks']['under_100_chars']} dưới 100 và {database['chunks']['empty_fts']} có lexical vector rỗng.
- Exact duplicate chunk text tạo {database['duplicate_chunks']['duplicate_excess']} hàng dư trong {database['duplicate_chunks']['groups']} nhóm; {database['legal_units']['empty_text']} legal-unit rỗng đồng thời thiếu source span.
- Có {database['legacy_external_references']} external stubs cũ không còn thuộc graph active mới; không copy chúng sang release mới."""

    neo_section = "Neo4j runtime was not requested."
    if neo4j:
        neo_section = f"""- Neo4j hiện có {neo4j['nodes']} nodes và {neo4j['edges']} relationships.
- Active JSON edges đã có: {neo4j['active_edges_present']}.
- Active JSON edges còn thiếu: {neo4j['active_edges_missing']}.
- Neo4j edges không còn trong active JSON mới: {neo4j['stale_edges']}.
"""

    high_duplicates = [row for row in duplicate_rows if row["confidence"] == "high"]
    review_duplicates = [row for row in duplicate_rows if row["confidence"] == "review"]
    duplicate_lines = "\n".join(
        f"- `{row['left_id']}` ↔ `{row['right_id']}` ({row['confidence']}; "
        f"recommended canonical ID: `{row['recommended_canonical_id']}`)."
        for row in duplicate_rows
    )

    return f"""# Kiểm toán dữ liệu BHYT / viện phí

Ngày tạo: {summary['generated_at']}
Chế độ: read-only; không sửa CSV, JSON, PostgreSQL hoặc Neo4j.

## Kết luận điều hành

Corpus hiện tại phải được rebuild thành release mới, nhưng không nên ingest trực tiếp hai JSON:

- CSV có {csv_counts['metadata_rows']} metadata, {csv_counts['content_rows']} content và {csv_counts['relationship_rows']} relationships.
- JSON có {active['records']} full-document records. Trong đó {active['sql_seed_core']} là seed mạnh, {active['sql_seed_broad']} chỉ lọt qua cụm rộng về khám/chữa bệnh, và {active['graph_context']} là full document được kéo vào qua graph nhưng không khớp điều kiện SQL.
- Union metadata có {content['candidate_document_records']} record; {content['usable_source_html']} có source HTML dùng được và {content['quarantine_count']} phải quarantine do thiếu hoặc gán sai HTML.
- `content_text` trong JSON sai document ở {content['wrong_derived_text']} / {active['records']} records. Phải bỏ toàn bộ và tái sinh từ HTML.
- Có {len(high_duplicates)} cặp duplicate identity độ tin cậy cao và {len(review_duplicates)} cặp cần review; không nên index hai lần cùng một văn bản pháp lý.
- Graph active có {relationships['active_rows']} cạnh trên {relationships['active_endpoint_ids']} endpoint; {relationships['reference_only_ids']} endpoint chưa có full document.

## Phạm vi dữ liệu nên dùng

Không dùng một cờ `active=true` duy nhất. Tách thành bốn tầng:

| Tầng | Số record | Cách dùng |
|---|---:|---|
| `seed_core` | {active['sql_seed_core']} | Exact, lexical và semantic retrieval mặc định |
| `seed_broad_kcb` | {active['sql_seed_broad']} | Review phạm vi; chỉ index sau khi chấp nhận nhu cầu sản phẩm |
| `graph_context` | {active['graph_context']} | Hydrate khi graph expansion; giảm trọng số hoặc không semantic-search mặc định |
| `reference_only` | {relationships['reference_only_ids']} | Chỉ làm endpoint graph; không tạo text, chunk hay embedding |

Điều kiện SQL thực tế tạo {active['sql_seed_total']} seed. {active['graph_context']} record còn lại không khớp bất kỳ nhánh `LIKE` nào nhưng đều xuất hiện trong graph, vì vậy chúng là context chứ không phải seed.

## Lỗi nội dung

### P0 — không được ingest

- {content['wrong_derived_text']} `content_text` bị gán sai document. Dù {content['consistent_derived_text']} record còn lại tương đối khớp HTML, nên regenerate cả {active['records']} record để chỉ có một quy trình.
- Quarantine {content['quarantine_count']} IDs: {quarantine}.
- Hai record `{content['wrong_html_assignments'][0]}` và `{content['wrong_html_assignments'][1]}` dùng cùng HTML với document đúng `{content['duplicate_html_owner']}`; đây là gán nhầm raw HTML, không thể sửa bằng normalize.
- JSON chứa {content['nonstandard_nan_values']} giá trị `NaN`, không hợp lệ theo chuẩn JSON. Chuyển thành `null` trước khi dùng ngoài Python.

### P1 — cần review

- {content['signature_not_found']} document không tìm thấy số/ký hiệu metadata trong visible HTML. Nhiều trường hợp là khác chuẩn dấu câu hoặc lỗi chính tả, nhưng phải có review queue.
- {content['low_title_coverage']} document có độ phủ token tiêu đề dưới 80% trong HTML.
- Có {content['encoding_warning_documents']} document mang ký tự encoding đáng ngờ (`�` hoặc `Ð`).
- {content['effective_before_issue']} document có ngày hiệu lực trước ngày ban hành. Có thể là hiệu lực hồi tố hợp pháp, nên đánh dấu review thay vì tự sửa.

### Duplicate identity

{duplicate_lines}

Giữ alias ID để không làm gãy graph/citation; chỉ chọn một `canonical_document_id` để index nội dung.

## Metadata thiếu

- Active JSON thiếu `ngay_co_hieu_luc` ở {content['missing_metadata']['ngay_co_hieu_luc']} record, `linh_vuc` ở {content['missing_metadata']['linh_vuc']} và `nganh` ở {content['missing_metadata']['nganh']}.
- Trong CSV, `nguon_thu_thap` thiếu {csv_counts['metadata_missing']['nguon_thu_thap']}/315, `ngay_dang_cong_bao` thiếu {csv_counts['metadata_missing']['ngay_dang_cong_bao']}/315, `nganh` thiếu {csv_counts['metadata_missing']['nganh']}/315 và `ngay_co_hieu_luc` thiếu {csv_counts['metadata_missing']['ngay_co_hieu_luc']}/315.
- Active-only documents không có các trường provenance/category đầy đủ như CSV (`agent_category`, `status_checked_at`, `nguon_thu_thap`, người ký, phạm vi). Không được tự bịa; lưu `selection_reason` dạng derived và đưa provenance còn thiếu vào backlog.
- CSV-only ID `187782` khớp điều kiện BHYT nhưng không có content và biến mất khỏi active JSON. Giữ metadata, quarantine content và thu thập lại văn bản nguồn.

## Quan hệ

- {relationships['retained_from_csv']} cạnh CSV còn tồn tại trong active JSON.
- {relationships['removed_since_csv']} cạnh cũ đã bị loại; không union trở lại release active.
- {relationships['new_active']} cạnh mới chỉ có ba trường ID/type, thiếu flags, title và source-row provenance.
- {relationships['both_full']} cạnh có đủ hai full documents; {relationships['one_full']} có đúng một full document; {relationships['neither_full']} không có full document ở cả hai đầu.
- {relationships['multi_predicate_pairs']} cặp có nhiều predicate. Giữ nguyên từng predicate, không collapse theo cặp source/target.
- {relationships['reference_only_with_known_title']} / {relationships['reference_only_ids']} reference-only IDs có thể lấy được title từ CSV/DB cũ; phần còn lại chỉ có ID và phải để `resolution_status=unresolved_reference`.

Chiến lược: active JSON quyết định membership; enrich {relationships['retained_from_csv']} cạnh trùng bằng provenance CSV; {relationships['new_active']} cạnh còn lại phải mang `provenance_status=active_export_only` cho đến khi thu được source row đầy đủ.

## Trạng thái database hiện tại

{db_section}

DB hiện tại có content đúng hash với corpus CSV cũ, nhưng phạm vi đã cũ. Không update tại chỗ; build immutable release mới và chỉ đổi active pointer sau quality gates.

Chunk hiện tại bị over-fragment: heading như “QUYẾT NGHỊ”, “Điều 1” và placeholder bảng đang thành chunk riêng. Release mới nên giữ legal-unit gốc nhưng chỉ tạo retrieval chunk khi có nội dung đủ nghĩa; heading phải ghép với phần thân kế tiếp và boilerplate duplicate phải bị hạ trọng số/deduplicate ở tầng index.

## Trạng thái Neo4j

{neo_section}

## Quy tắc hợp nhất đề xuất

1. Metadata CSV thắng cho {active['overlap_csv']} ID giao nhau vì có provenance/category đầy đủ; các trường chung hiện khớp JSON.
2. HTML CSV thắng khi có visible text hợp lệ. Với active-only record, dùng HTML JSON sau identity validation.
3. Không bao giờ lấy `content_text` JSON. Luôn tạo `content_text = normalize_html(chosen_raw_html)` và lưu hash.
4. Document thiếu/sai HTML vẫn được giữ metadata và graph ID nhưng không có chunk/embedding.
5. Duplicate document dùng alias table; graph edges có thể giữ source ID gốc nhưng retrieval hydrate về canonical ID.
6. Relationship membership lấy từ active JSON, không union với CSV cũ; CSV chỉ enrich provenance cho cạnh còn tồn tại.
7. Reference-only node không được biến thành document rỗng trong semantic index.

## Quality gates cho release mới

- Zero derived-text mismatch.
- Zero wrong/missing HTML trong tập được đánh dấu searchable.
- Zero `NaN`; missing value phải là JSON `null`.
- Zero duplicate canonical identity trong retrieval index.
- 100% relationship endpoint tồn tại dưới dạng canonical document, alias hoặc reference node.
- 100% chunk có document/unit/source offsets/hash.
- Không embed chunk chỉ có heading, dấu chấm hoặc placeholder bảng.
- Manifest tách rõ seed core, broad context, graph context, reference-only và quarantine.
- Legal status phải có `status_checked_at`; status chưa kiểm chứng không được trình bày như sự thật hiện tại.

## Artifacts

- `document_inventory.csv`: một dòng cho mọi document/reference/legacy DB ID.
- `relationship_inventory.csv`: retained/new/removed relationship và provenance.
- `issues.csv`: lỗi cụ thể, severity và cách xử lý.
- `content_recovery_queue.csv`: 5 document phải tìm lại source HTML.
- `scope_review.csv`: 168 seed từ điều kiện khám/chữa bệnh rộng.
- `duplicate_candidates.csv`: các cặp ID có khả năng cùng một văn bản.
- `summary.json`: số liệu máy đọc được để dùng làm CI quality gate.
"""


async def run(args: argparse.Namespace) -> dict[str, Any]:
    required_csv = (
        "metadata.csv",
        "content.csv",
        "documents.csv",
        "metadata_bhyt.csv",
        "metadata_vien_phi.csv",
        "relationships.csv",
    )
    for filename in required_csv:
        if not (args.source_dir / filename).is_file():
            raise FileNotFoundError(args.source_dir / filename)
    if not args.active_docs.is_file() or not args.active_relationships.is_file():
        raise FileNotFoundError("Active JSON exports are required")

    csv_metadata_rows = read_csv(args.source_dir / "metadata.csv")
    csv_content_rows = read_csv(args.source_dir / "content.csv")
    csv_document_rows = read_csv(args.source_dir / "documents.csv")
    csv_bhyt_rows = read_csv(args.source_dir / "metadata_bhyt.csv")
    csv_vien_phi_rows = read_csv(args.source_dir / "metadata_vien_phi.csv")
    csv_relationship_rows = read_csv(args.source_dir / "relationships.csv")
    active_document_rows = read_json(args.active_docs)
    active_relationship_rows = read_json(args.active_relationships)

    csv_metadata = index_unique(csv_metadata_rows, "id", "metadata.csv")
    csv_content = index_unique(csv_content_rows, "id", "content.csv")
    active_documents = index_unique(active_document_rows, "id", args.active_docs.name)

    db_inventory: dict[str, Any] = {}
    if args.with_database:
        db_inventory = await read_postgres_inventory(args.env_file)
    db_documents: dict[str, dict[str, Any]] = db_inventory.get("documents", {})

    visible_active = {
        identifier: normalize_html(clean(row.get("content_html")))
        for identifier, row in active_documents.items()
    }
    visible_csv = {
        identifier: normalize_html(row.get("content_html", ""))
        for identifier, row in csv_content.items()
    }
    active_title_coverage = {
        identifier: title_coverage(row.get("title"), visible_active[identifier])
        for identifier, row in active_documents.items()
    }

    active_edges = {
        (
            clean(row.get("doc_id")),
            clean(row.get("other_doc_id")),
            clean(row.get("relationship")),
        )
        for row in active_relationship_rows
    }
    csv_edges = {
        (
            clean(row.get("doc_id")),
            clean(row.get("other_doc_id")),
            clean(row.get("relationship")),
        )
        for row in csv_relationship_rows
    }
    active_endpoints = {identifier for edge in active_edges for identifier in edge[:2]}

    # Exact visible-content collisions with different identities expose raw-HTML assignment bugs.
    by_visible_hash: dict[str, list[str]] = collections.defaultdict(list)
    for identifier, visible in visible_active.items():
        if visible:
            by_visible_hash[sha256(visible)].append(identifier)
    wrong_html_ids: set[str] = set()
    duplicate_html_owner = ""
    for identifiers in by_visible_hash.values():
        if len(identifiers) < 2:
            continue
        identity_keys = {
            (
                normalized_identity(active_documents[item].get("title")),
                normalized_identity(active_documents[item].get("so_ky_hieu")),
            )
            for item in identifiers
        }
        if len(identity_keys) == 1:
            continue
        owner = max(identifiers, key=lambda item: active_title_coverage[item])
        duplicate_html_owner = owner
        wrong_html_ids.update(set(identifiers) - {owner})

    missing_active_html = {
        identifier for identifier, visible in visible_active.items() if not visible
    }
    suspect_identity_ids = {
        identifier
        for identifier, coverage in active_title_coverage.items()
        if coverage < 0.35 and identifier not in missing_active_html
    }
    wrong_html_ids.update(suspect_identity_ids)

    duplicate_candidates = find_duplicate_candidates(
        active_documents,
        visible_active,
        set(csv_metadata),
        db_documents,
    )
    high_duplicate_aliases: dict[str, str] = {}
    for candidate in duplicate_candidates:
        if candidate.confidence != "high":
            continue
        alias = (
            candidate.right_id
            if candidate.recommended_canonical_id == candidate.left_id
            else candidate.left_id
        )
        high_duplicate_aliases[alias] = candidate.recommended_canonical_id

    issues: list[dict[str, str]] = []
    document_inventory: list[dict[str, Any]] = []

    # Titles for reference-only nodes can be recovered from legacy CSV relationship provenance.
    reference_titles: dict[str, str] = {}
    for row in csv_relationship_rows:
        for id_field, title_field in (
            ("doc_id", "source_title"),
            ("other_doc_id", "target_title"),
        ):
            identifier = clean(row.get(id_field))
            title = clean(row.get(title_field))
            if identifier and title and identifier not in reference_titles:
                reference_titles[identifier] = title

    universe_ids = (
        set(csv_metadata)
        | set(active_documents)
        | active_endpoints
        | set(db_documents)
    )

    active_text_similarity: dict[str, float] = {}
    for identifier, row in active_documents.items():
        active_text_similarity[identifier] = cosine(
            token_counter(clean(row.get("content_text"))),
            token_counter(visible_active[identifier]),
        )

    for identifier in sorted(universe_ids):
        csv_row = csv_metadata.get(identifier)
        active_row = active_documents.get(identifier)
        db_row = db_documents.get(identifier, {})
        metadata = csv_row or active_row or {}
        reasons = filter_reasons(active_row or csv_row or {})
        if reasons:
            selection_role = (
                "seed_core"
                if set(reasons) & STRONG_FILTER_REASONS
                else "seed_broad_kcb"
            )
        elif identifier in active_documents and identifier in active_endpoints:
            selection_role = "graph_context"
        elif identifier in active_endpoints:
            selection_role = "reference_only"
        elif identifier in csv_metadata:
            selection_role = "csv_only"
        else:
            selection_role = "legacy_db_only"

        issue_codes: list[str] = []
        active_visible = visible_active.get(identifier, "")
        csv_visible = visible_csv.get(identifier, "")
        coverage = active_title_coverage.get(identifier)
        signature_found = (
            normalized_signature_found(metadata.get("so_ky_hieu"), active_visible)
            if active_row and active_visible
            else None
        )

        if csv_visible:
            recommended_html_source = "csv_content_html"
            recommended_visible = csv_visible
        elif active_visible and identifier not in wrong_html_ids:
            recommended_html_source = "active_json_content_html"
            recommended_visible = active_visible
        else:
            recommended_html_source = ""
            recommended_visible = ""

        if csv_row or active_row:
            if not recommended_visible:
                content_status = "quarantine"
                issue_codes.append("missing_or_wrong_source_html")
                add_issue(
                    issues,
                    severity="critical",
                    entity_type="document",
                    entity_id=identifier,
                    code="missing_or_wrong_source_html",
                    detail="No identity-validated source HTML is available.",
                    resolution="Keep metadata/graph ID, but do not create text, chunks or embeddings; reacquire an authoritative source.",
                )
            elif coverage is not None and coverage < 0.80:
                content_status = "usable_after_identity_review"
                issue_codes.append("low_title_html_coverage")
                add_issue(
                    issues,
                    severity="high" if coverage < 0.35 else "medium",
                    entity_type="document",
                    entity_id=identifier,
                    code="low_title_html_coverage",
                    detail=f"Title-token coverage in active HTML is {coverage:.3f}.",
                    resolution="Verify document number, issuer and title against the source before marking searchable.",
                )
            else:
                content_status = "usable"
        else:
            content_status = "reference_only"

        if active_row:
            similarity = active_text_similarity[identifier]
            if similarity < 0.90:
                issue_codes.append("wrong_derived_content_text")
                add_issue(
                    issues,
                    severity="critical",
                    entity_type="document",
                    entity_id=identifier,
                    code="wrong_derived_content_text",
                    detail=f"content_text vs own content_html token cosine={similarity:.4f}.",
                    resolution="Discard JSON content_text and regenerate it deterministically from the selected raw HTML.",
                )
            nan_fields = [key for key, value in active_row.items() if is_nan(value)]
            if nan_fields:
                issue_codes.append("nonstandard_json_nan")
                add_issue(
                    issues,
                    severity="high",
                    entity_type="document",
                    entity_id=identifier,
                    code="nonstandard_json_nan",
                    detail="NaN fields: " + ", ".join(sorted(nan_fields)),
                    resolution="Serialize missing values as JSON null and validate with a strict JSON parser.",
                )
            missing_required = [
                field
                for field in ACTIVE_REQUIRED_METADATA_FIELDS
                if not clean(active_row.get(field))
            ]
            if missing_required:
                issue_codes.append("missing_required_metadata")
                add_issue(
                    issues,
                    severity="high",
                    entity_type="document",
                    entity_id=identifier,
                    code="missing_required_metadata",
                    detail="Missing fields: " + ", ".join(missing_required),
                    resolution="Reacquire metadata or quarantine the record from answer-ready retrieval.",
                )
            if active_visible and signature_found is False:
                issue_codes.append("signature_not_found_in_html")
                add_issue(
                    issues,
                    severity="medium",
                    entity_type="document",
                    entity_id=identifier,
                    code="signature_not_found_in_html",
                    detail=f"Metadata signature {clean(metadata.get('so_ky_hieu'))!r} was not found in normalized HTML.",
                    resolution="Review source formatting/typos and store approved signature aliases; do not silently rewrite authority data.",
                )
            source_html = clean(active_row.get("content_html"))
            if "�" in source_html or "Ð" in active_visible:
                issue_codes.append("encoding_warning")
                add_issue(
                    issues,
                    severity="medium",
                    entity_type="document",
                    entity_id=identifier,
                    code="encoding_warning",
                    detail="Source contains a Unicode replacement or suspicious Ð character.",
                    resolution="Compare with the authority source and repair encoding before rebuilding chunks.",
                )

        if selection_role == "seed_broad_kcb":
            issue_codes.append("broad_scope_requires_review")
            add_issue(
                issues,
                severity="medium",
                entity_type="document",
                entity_id=identifier,
                code="broad_scope_requires_review",
                detail="Selected only by a broad khám/chữa bệnh title predicate.",
                resolution="Classify as project-relevant or medical context before enabling default semantic retrieval.",
            )

        issue_date = parse_date(metadata.get("ngay_ban_hanh"))
        effective_date = parse_date(metadata.get("ngay_co_hieu_luc"))
        if isinstance(issue_date, dt.date) and isinstance(effective_date, dt.date):
            if effective_date < issue_date:
                issue_codes.append("effective_before_issue")
                add_issue(
                    issues,
                    severity="medium",
                    entity_type="document",
                    entity_id=identifier,
                    code="effective_before_issue",
                    detail=f"Effective {effective_date.isoformat()} before issue {issue_date.isoformat()}.",
                    resolution="Verify whether the document intentionally has retroactive effect; do not auto-correct.",
                )

        canonical_id = high_duplicate_aliases.get(identifier, identifier)
        if identifier in high_duplicate_aliases:
            issue_codes.append("duplicate_document_alias")

        if not (csv_row or active_row):
            if identifier in active_endpoints:
                issue_codes.append("unresolved_reference")
                add_issue(
                    issues,
                    severity="medium",
                    entity_type="document_reference",
                    entity_id=identifier,
                    code="unresolved_reference",
                    detail="Relationship endpoint has no full document in the available corpus.",
                    resolution="Keep as graph-only reference; acquire metadata/content separately and never embed an empty stub.",
                )

        if csv_row or active_row:
            if content_status == "quarantine":
                action = "quarantine_do_not_embed"
            elif identifier in high_duplicate_aliases:
                action = "retain_alias_map_to_canonical"
            elif db_row and not bool(db_row.get("is_external")):
                action = "rebuild_in_new_release"
            elif db_row and bool(db_row.get("is_external")):
                action = "promote_external_stub_in_new_release"
            else:
                action = "insert_in_new_release"
        elif identifier in active_endpoints:
            action = "retain_graph_reference_only"
        else:
            action = "exclude_legacy_id_from_new_release"

        if db_row:
            db_status = "external_reference" if db_row.get("is_external") else "canonical"
        else:
            db_status = "absent"

        document_inventory.append(
            {
                "document_id": identifier,
                "canonical_document_id": canonical_id,
                "entity_kind": "document" if csv_row or active_row else "reference",
                "selection_role": selection_role,
                "filter_reasons": ";".join(reasons),
                "in_csv_metadata": bool(csv_row),
                "in_csv_content": identifier in csv_content,
                "in_active_json": bool(active_row),
                "in_active_relationships": identifier in active_endpoints,
                "db_status": db_status,
                "title": clean(metadata.get("title")) or reference_titles.get(identifier, ""),
                "so_ky_hieu": clean(metadata.get("so_ky_hieu")),
                "loai_van_ban": clean(metadata.get("loai_van_ban")),
                "co_quan_ban_hanh": clean(metadata.get("co_quan_ban_hanh")),
                "ngay_ban_hanh": clean(metadata.get("ngay_ban_hanh")),
                "tinh_trang_hieu_luc": clean(metadata.get("tinh_trang_hieu_luc")),
                "recommended_html_source": recommended_html_source,
                "content_status": content_status,
                "visible_text_length": len(recommended_visible),
                "visible_text_sha256": sha256(recommended_visible) if recommended_visible else "",
                "active_text_html_similarity": (
                    round(active_text_similarity[identifier], 6) if active_row else ""
                ),
                "title_html_coverage": round(coverage, 6) if coverage is not None else "",
                "signature_found_in_active_html": signature_found if signature_found is not None else "",
                "issue_codes": ";".join(sorted(set(issue_codes))),
                "recommended_action": action,
            }
        )

    # Explicit duplicate issues are pair-level so no candidate is silently collapsed.
    for candidate in duplicate_candidates:
        add_issue(
            issues,
            severity="high" if candidate.confidence == "high" else "medium",
            entity_type="document_pair",
            entity_id=f"{candidate.left_id}|{candidate.right_id}",
            code="duplicate_legal_instrument_candidate",
            detail=(
                f"confidence={candidate.confidence}; title_similarity={candidate.title_similarity:.4f}; "
                f"content_similarity={candidate.content_similarity:.4f}"
            ),
            resolution=(
                f"Review and retain aliases; recommended canonical ID is "
                f"{candidate.recommended_canonical_id}."
            ),
        )

    csv_relationship_by_edge = {
        (
            clean(row.get("doc_id")),
            clean(row.get("other_doc_id")),
            clean(row.get("relationship")),
        ): row
        for row in csv_relationship_rows
    }
    neo4j_inventory: dict[str, Any] = {}
    if args.with_neo4j:
        dataset_id = clean((db_inventory.get("dataset") or {}).get("dataset_id")) or None
        neo4j_inventory = read_neo4j_inventory(args.env_file, dataset_id)
    neo_edges: set[tuple[str, str, str]] = neo4j_inventory.get("edges", set())

    relationship_inventory: list[dict[str, Any]] = []
    multi_predicate: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for source, target, relation_type in active_edges:
        multi_predicate[(source, target)].add(relation_type)

    for edge in sorted(active_edges | csv_edges):
        source, target, relation_type = edge
        in_active = edge in active_edges
        in_csv = edge in csv_edges
        csv_row = csv_relationship_by_edge.get(edge, {})
        if in_active and in_csv:
            membership = "retained_from_csv"
            provenance = "enriched_from_csv"
        elif in_active:
            membership = "new_active"
            provenance = "active_export_only"
            add_issue(
                issues,
                severity="medium",
                entity_type="relationship",
                entity_id="|".join(edge),
                code="relationship_provenance_incomplete",
                detail="Active edge has only source ID, target ID and predicate.",
                resolution="Keep membership but mark provenance incomplete until flags, source titles and source-row hash are recovered.",
            )
        else:
            membership = "removed_since_csv"
            provenance = "historical_csv"

        source_full = source in active_documents or source in csv_metadata
        target_full = target in active_documents or target in csv_metadata
        endpoint_issue = ""
        if in_active and not source_full and not target_full:
            endpoint_issue = "neither_endpoint_has_full_document"
        elif in_active and not source_full:
            endpoint_issue = "source_is_reference_only"
        elif in_active and not target_full:
            endpoint_issue = "target_is_reference_only"

        relationship_inventory.append(
            {
                "source_id": source,
                "target_id": target,
                "relationship_type": relation_type,
                "membership": membership,
                "provenance_status": provenance,
                "in_current_neo4j": edge in neo_edges if neo4j_inventory else "",
                "source_has_full_document": source_full,
                "target_has_full_document": target_full,
                "source_is_selected": clean(csv_row.get("source_is_selected")),
                "target_is_selected": clean(csv_row.get("target_is_selected")),
                "relationship_is_adverse": clean(csv_row.get("relationship_is_adverse")),
                "agent_category": clean(csv_row.get("agent_category")),
                "source_title": clean(csv_row.get("source_title")),
                "target_title": clean(csv_row.get("target_title")),
                "endpoint_issue": endpoint_issue,
                "recommended_action": (
                    "load_and_enrich"
                    if in_active and in_csv
                    else "load_with_incomplete_provenance"
                    if in_active
                    else "retain_in_history_do_not_union"
                ),
            }
        )

    active_ids = set(active_documents)
    csv_ids = set(csv_metadata)
    candidate_ids = active_ids | csv_ids
    quarantine_ids = sorted(
        row["document_id"]
        for row in document_inventory
        if row["entity_kind"] == "document" and row["content_status"] == "quarantine"
    )
    usable_source_html = sum(
        row["entity_kind"] == "document" and row["content_status"] != "quarantine"
        for row in document_inventory
    )
    missing_metadata_counts = {
        field: sum(not clean(row.get(field)) for row in active_document_rows)
        for field in (
            "ngay_co_hieu_luc",
            "ngay_het_hieu_luc",
            "linh_vuc",
            "nganh",
        )
    }
    nonstandard_nan_values = sum(
        is_nan(value) for row in active_document_rows for value in row.values()
    )
    csv_metadata_missing = {
        field: sum(not clean(row.get(field)) for row in csv_metadata_rows)
        for field in CSV_METADATA_FIELDS
    }

    reasons_by_id = {
        identifier: filter_reasons(row) for identifier, row in active_documents.items()
    }
    core_ids = {
        identifier
        for identifier, reasons in reasons_by_id.items()
        if set(reasons) & STRONG_FILTER_REASONS
    }
    broad_ids = {
        identifier
        for identifier, reasons in reasons_by_id.items()
        if reasons and not set(reasons) & STRONG_FILTER_REASONS
    }
    graph_context_ids = {
        identifier
        for identifier, reasons in reasons_by_id.items()
        if not reasons and identifier in active_endpoints
    }

    both_full = sum(
        source in candidate_ids and target in candidate_ids
        for source, target, _ in active_edges
    )
    one_full = sum(
        (source in candidate_ids) ^ (target in candidate_ids)
        for source, target, _ in active_edges
    )
    neither_full = len(active_edges) - both_full - one_full

    db_summary: dict[str, Any] = {}
    if db_inventory:
        canonical_db = {
            identifier
            for identifier, row in db_documents.items()
            if not bool(row.get("is_external"))
        }
        external_db = set(db_documents) - canonical_db
        csv_db_common = set(csv_content) & set(db_documents)
        csv_text_hash_mismatch = sum(
            clean(db_documents[identifier].get("text_sha256"))
            != sha256(visible_csv[identifier])
            for identifier in csv_db_common
        )
        csv_html_hash_mismatch = sum(
            clean(db_documents[identifier].get("raw_html_sha256"))
            != sha256(csv_content[identifier].get("content_html", ""))
            for identifier in csv_db_common
        )
        db_summary = {
            "dataset_id": clean((db_inventory.get("dataset") or {}).get("dataset_id")),
            "document_records": len(db_documents),
            "canonical_documents": len(canonical_db),
            "external_references": len(external_db),
            "active_as_existing_canonical": len(active_ids & canonical_db),
            "active_as_external_reference": len(active_ids & external_db),
            "active_absent": len(active_ids - set(db_documents)),
            "db_canonical_not_in_active": sorted(canonical_db - active_ids),
            "legacy_external_references": len(external_db - active_endpoints),
            "active_graph_endpoints_as_canonical": len(active_endpoints & canonical_db),
            "active_graph_endpoints_as_external": len(active_endpoints & external_db),
            "active_graph_endpoints_absent": len(active_endpoints - set(db_documents)),
            "csv_content_hash_compared": len(csv_db_common),
            "csv_text_hash_mismatch": csv_text_hash_mismatch,
            "csv_html_hash_mismatch": csv_html_hash_mismatch,
            **json_safe(db_inventory.get("metrics", {})),
        }

    neo_summary: dict[str, Any] = {}
    if neo4j_inventory:
        neo_nodes = neo4j_inventory["nodes"]
        neo_edges = neo4j_inventory["edges"]
        neo_summary = {
            "nodes": len(neo_nodes),
            "edges": len(neo_edges),
            "active_edges_present": len(active_edges & neo_edges),
            "active_edges_missing": len(active_edges - neo_edges),
            "stale_edges": len(neo_edges - active_edges),
        }

    summary = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "inputs": {
            "source_dir": str(args.source_dir),
            "active_docs": str(args.active_docs),
            "active_relationships": str(args.active_relationships),
            "sha256": {
                **{
                    filename: file_sha256(args.source_dir / filename)
                    for filename in required_csv
                },
                args.active_docs.name: file_sha256(args.active_docs),
                args.active_relationships.name: file_sha256(args.active_relationships),
            },
        },
        "csv": {
            "metadata_rows": len(csv_metadata_rows),
            "content_rows": len(csv_content_rows),
            "documents_projection_rows": len(csv_document_rows),
            "bhyt_projection_rows": len(csv_bhyt_rows),
            "vien_phi_projection_rows": len(csv_vien_phi_rows),
            "relationship_rows": len(csv_relationship_rows),
            "metadata_missing": csv_metadata_missing,
            "metadata_without_content_row": sorted(set(csv_metadata) - set(csv_content)),
            "content_with_empty_visible_text": sorted(
                identifier for identifier, value in visible_csv.items() if not value
            ),
        },
        "active_documents": {
            "records": len(active_documents),
            "overlap_csv": len(active_ids & csv_ids),
            "active_only": len(active_ids - csv_ids),
            "csv_only": len(csv_ids - active_ids),
            "sql_seed_total": len(core_ids | broad_ids),
            "sql_seed_core": len(core_ids),
            "sql_seed_broad": len(broad_ids),
            "graph_context": len(graph_context_ids),
        },
        "content_quality": {
            "candidate_document_records": len(candidate_ids),
            "usable_source_html": usable_source_html,
            "quarantine_count": len(quarantine_ids),
            "quarantine_ids": quarantine_ids,
            "wrong_html_assignments": sorted(wrong_html_ids),
            "duplicate_html_owner": duplicate_html_owner,
            "wrong_derived_text": sum(value < 0.90 for value in active_text_similarity.values()),
            "consistent_derived_text": sum(value >= 0.90 for value in active_text_similarity.values()),
            "nonstandard_nan_values": nonstandard_nan_values,
            "missing_metadata": missing_metadata_counts,
            "signature_not_found": sum(
                bool(visible_active[identifier])
                and not normalized_signature_found(
                    active_documents[identifier].get("so_ky_hieu"),
                    visible_active[identifier],
                )
                for identifier in active_documents
            ),
            "low_title_coverage": sum(
                coverage < 0.80 for coverage in active_title_coverage.values()
            ),
            "encoding_warning_documents": sum(
                "�" in clean(row.get("content_html")) or "Ð" in visible_active[identifier]
                for identifier, row in active_documents.items()
            ),
            "effective_before_issue": sum(
                issue["code"] == "effective_before_issue" for issue in issues
            ),
        },
        "duplicate_candidates": [
            {
                "left_id": row.left_id,
                "right_id": row.right_id,
                "confidence": row.confidence,
                "title_similarity": row.title_similarity,
                "content_similarity": row.content_similarity,
                "recommended_canonical_id": row.recommended_canonical_id,
            }
            for row in duplicate_candidates
        ],
        "relationships": {
            "csv_rows": len(csv_edges),
            "active_rows": len(active_edges),
            "retained_from_csv": len(active_edges & csv_edges),
            "removed_since_csv": len(csv_edges - active_edges),
            "new_active": len(active_edges - csv_edges),
            "active_endpoint_ids": len(active_endpoints),
            "full_document_endpoint_ids": len(active_endpoints & candidate_ids),
            "reference_only_ids": len(active_endpoints - candidate_ids),
            "reference_only_with_known_title": sum(
                bool(
                    reference_titles.get(identifier)
                    or clean(db_documents.get(identifier, {}).get("title"))
                )
                for identifier in active_endpoints - candidate_ids
            ),
            "both_full": both_full,
            "one_full": one_full,
            "neither_full": neither_full,
            "multi_predicate_pairs": sum(
                len(predicates) > 1 for predicates in multi_predicate.values()
            ),
        },
        "database": db_summary,
        "neo4j": neo_summary,
        "issues": {
            "total": len(issues),
            "by_severity": dict(collections.Counter(row["severity"] for row in issues)),
            "by_code": dict(collections.Counter(row["code"] for row in issues)),
        },
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(
        args.output_dir / "document_inventory.csv",
        document_inventory,
        [
            "document_id",
            "canonical_document_id",
            "entity_kind",
            "selection_role",
            "filter_reasons",
            "in_csv_metadata",
            "in_csv_content",
            "in_active_json",
            "in_active_relationships",
            "db_status",
            "title",
            "so_ky_hieu",
            "loai_van_ban",
            "co_quan_ban_hanh",
            "ngay_ban_hanh",
            "tinh_trang_hieu_luc",
            "recommended_html_source",
            "content_status",
            "visible_text_length",
            "visible_text_sha256",
            "active_text_html_similarity",
            "title_html_coverage",
            "signature_found_in_active_html",
            "issue_codes",
            "recommended_action",
        ],
    )
    write_csv(
        args.output_dir / "relationship_inventory.csv",
        relationship_inventory,
        [
            "source_id",
            "target_id",
            "relationship_type",
            "membership",
            "provenance_status",
            "in_current_neo4j",
            "source_has_full_document",
            "target_has_full_document",
            "source_is_selected",
            "target_is_selected",
            "relationship_is_adverse",
            "agent_category",
            "source_title",
            "target_title",
            "endpoint_issue",
            "recommended_action",
        ],
    )
    write_csv(
        args.output_dir / "issues.csv",
        sorted(
            issues,
            key=lambda row: (
                {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(
                    row["severity"], 4
                ),
                row["code"],
                row["entity_id"],
            ),
        ),
        ["severity", "entity_type", "entity_id", "code", "detail", "resolution"],
    )
    recovery_rows = []
    for row in document_inventory:
        if row["content_status"] != "quarantine":
            continue
        recovery_rows.append(
            {
                "document_id": row["document_id"],
                "title": row["title"],
                "so_ky_hieu": row["so_ky_hieu"],
                "co_quan_ban_hanh": row["co_quan_ban_hanh"],
                "ngay_ban_hanh": row["ngay_ban_hanh"],
                "problem": row["issue_codes"],
                "official_source_search": (
                    f'"{row["so_ky_hieu"]}" "{row["co_quan_ban_hanh"]}"'
                ).strip(),
                "acceptance_gate": (
                    "Document number, issuer, issue date and title must match; "
                    "preserve raw bytes/source URL/retrieved_at/SHA-256; then rebuild visible text."
                ),
            }
        )
    write_csv(
        args.output_dir / "content_recovery_queue.csv",
        recovery_rows,
        [
            "document_id",
            "title",
            "so_ky_hieu",
            "co_quan_ban_hanh",
            "ngay_ban_hanh",
            "problem",
            "official_source_search",
            "acceptance_gate",
        ],
    )
    write_csv(
        args.output_dir / "scope_review.csv",
        [row for row in document_inventory if row["selection_role"] == "seed_broad_kcb"],
        [
            "document_id",
            "title",
            "so_ky_hieu",
            "loai_van_ban",
            "co_quan_ban_hanh",
            "ngay_ban_hanh",
            "filter_reasons",
            "content_status",
            "recommended_action",
        ],
    )
    write_csv(
        args.output_dir / "duplicate_candidates.csv",
        [
            {
                "left_id": row.left_id,
                "right_id": row.right_id,
                "confidence": row.confidence,
                "title_similarity": row.title_similarity,
                "content_similarity": row.content_similarity,
                "recommended_canonical_id": row.recommended_canonical_id,
            }
            for row in duplicate_candidates
        ],
        [
            "left_id",
            "right_id",
            "confidence",
            "title_similarity",
            "content_similarity",
            "recommended_canonical_id",
        ],
    )
    with (args.output_dir / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(json_safe(summary), handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    (args.output_dir / "REPORT.md").write_text(build_report(summary), encoding="utf-8")
    return summary


def main() -> None:
    args = parse_args()
    summary = asyncio.run(run(args))
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
