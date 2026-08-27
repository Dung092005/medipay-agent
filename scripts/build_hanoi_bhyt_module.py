"""Build a clean Hanoi BHYT module and optionally merge into data/raw.

Produces:
  data/clean/hanoi_bhyt_module/   — standalone Hanoi package (6 docs)
  data/clean/serving_bhyt_slice/  — national CORE + Hanoi (for retrieval)
  Updates data/raw with 3 new Hanoi docs (backup first)

Does NOT delete other-local docs from raw; serving slice excludes them.
"""
from __future__ import annotations

import csv
import html
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
INCOMING = ROOT / "data" / "incoming" / "hanoi"
MODULE = ROOT / "data" / "clean" / "hanoi_bhyt_module"
SLICE = ROOT / "data" / "clean" / "serving_bhyt_slice"
AUDIT = ROOT / "docs" / "corpus_audit"
BACKUP = ROOT / "data" / "incoming" / "hanoi" / "_raw_backup_before_hanoi_merge"

csv.field_size_limit(min(2**31 - 1, 2**31 - 1))

META_FIELDS = [
    "id", "title", "so_ky_hieu", "ngay_ban_hanh", "loai_van_ban",
    "ngay_co_hieu_luc", "ngay_het_hieu_luc", "nguon_thu_thap",
    "ngay_dang_cong_bao", "nganh", "linh_vuc", "co_quan_ban_hanh",
    "chuc_danh", "nguoi_ky", "pham_vi", "thong_tin_ap_dung",
    "tinh_trang_hieu_luc", "agent_category", "status_checked_at", "status_filter",
]
CONTENT_FIELDS = ["id", "agent_category", "content_html"]
DOC_FIELDS = META_FIELDS + ["content_html"]

STATUS_FILTER = "Còn hiệu lực và không là target của quan hệ Bãi bỏ/Thay thế"
EXISTING_HANOI_IDS = [
    "179920",  # 19/2025
    "a58d0900-6ae3-11f1-9463-4ba9a7c0cbbb",  # 17/2026
    "267ffff0-6647-11f1-b01e-8bc328b4d0e9",  # 14/2026
]

# Stable IDs for new docs (deterministic-ish namespace UUID5)
NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
ID_321 = str(uuid.uuid5(NS, "hanoi:321/KH-UBND"))
ID_SYT = str(uuid.uuid5(NS, "hanoi:SYT-KCB-BAN-DAU-2026"))
ID_91 = str(uuid.uuid5(NS, "hanoi:91/2026/NQ-HDND"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in fields})


def text_to_html(title: str, body: str, *, provenance: str) -> str:
    parts = [
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">",
        f"<title>{html.escape(title)}</title></head><body>",
        f"<p><em>Provenance: {html.escape(provenance)}</em></p>",
    ]
    for block in re.split(r"\n\s*\n", body.strip()):
        block = block.strip()
        if not block:
            continue
        escaped = html.escape(block).replace("\n", "<br>\n")
        if re.match(r"^(Điều|I+|II+|III+|IV+|V+)\b", block) or block.startswith("NGHỊ QUYẾT") or block.startswith("KẾ HOẠCH") or block.startswith("HƯỚNG DẪN"):
            parts.append(f"<h2>{escaped}</h2>")
        else:
            parts.append(f"<p>{escaped}</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


def pdf_text(path: Path) -> str:
    doc = fitz.open(path)
    return "\n\n".join(doc[i].get_text().strip() for i in range(doc.page_count) if doc[i].get_text().strip())


def base_meta(**kwargs: str) -> dict[str, str]:
    row = {f: "" for f in META_FIELDS}
    row.update(
        {
            "pham_vi": "Địa phương",
            "tinh_trang_hieu_luc": "Còn hiệu lực",
            "agent_category": "bhyt",
            "status_filter": STATUS_FILTER,
            "status_checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "nganh": "Y tế",
            "linh_vuc": "Bảo hiểm y tế",
            "thong_tin_ap_dung": "Thành phố Hà Nội",
        }
    )
    row.update(kwargs)
    return row


def build_new_docs() -> list[tuple[dict[str, str], str]]:
    """Return list of (metadata_row, content_html)."""
    docs: list[tuple[dict[str, str], str]] = []

    # 321/KH-UBND
    p321 = INCOMING / "321_KH-UBND_trien_khai_NQ19_2025.pdf"
    body321 = pdf_text(p321)
    title321 = (
        "Kế hoạch triển khai thực hiện việc quy định nội dung, mức hỗ trợ đóng "
        "bảo hiểm xã hội tự nguyện, bảo hiểm y tế cho các đối tượng chính sách xã hội "
        "của thành phố Hà Nội theo Nghị quyết số 19/2025/NQ-HĐND ngày 09/7/2025 "
        "của Hội đồng nhân dân thành phố Hà Nội"
    )
    meta321 = base_meta(
        id=ID_321,
        title=title321,
        so_ky_hieu="321/KH-UBND",
        ngay_ban_hanh="28/11/2025",
        loai_van_ban="Kế hoạch",
        ngay_co_hieu_luc="01/01/2026",
        nguon_thu_thap="PDF UBND TP Hà Nội (incoming/hanoi/321_KH-UBND_trien_khai_NQ19_2025.pdf)",
        co_quan_ban_hanh="UBND Thành phố Hà Nội",
        chuc_danh="Phó Chủ tịch",
        nguoi_ky="Vũ Thu Hà",
    )
    html321 = text_to_html(title321, body321, provenance=str(p321.name))
    docs.append((meta321, html321))

    # SYT hướng dẫn KCB ban đầu 2026
    psyt = INCOMING / "SYT_Huong_dan_KCB_ban_dau_2026.pdf"
    body_syt = pdf_text(psyt)
    title_syt = (
        "Hướng dẫn về việc đăng ký khám bệnh, chữa bệnh bảo hiểm y tế ban đầu "
        "trên địa bàn thành phố Hà Nội năm 2026"
    )
    meta_syt = base_meta(
        id=ID_SYT,
        title=title_syt,
        so_ky_hieu="SYT-QLBHYTCNTT/2026-KCB-BAN-DAU",
        ngay_ban_hanh="25/03/2026",
        loai_van_ban="Hướng dẫn",
        ngay_co_hieu_luc="25/03/2026",
        nguon_thu_thap="PDF Sở Y tế Hà Nội (incoming/hanoi/SYT_Huong_dan_KCB_ban_dau_2026.pdf); số hiệu trên header bản file để trống",
        co_quan_ban_hanh="Sở Y tế Hà Nội",
        nganh="Y tế",
        linh_vuc="Bảo hiểm y tế, Khám chữa bệnh",
    )
    html_syt = text_to_html(title_syt, body_syt, provenance=str(psyt.name))
    docs.append((meta_syt, html_syt))

    # 91/2026 body (from recovered text)
    body91 = (INCOMING / "staging" / "91_2026_NQ-HDND.txt").read_text(encoding="utf-8")
    title91 = (
        "Nghị quyết số 91/2026/NQ-HĐND Sửa đổi, bổ sung một số Điều của Nghị quyết "
        "45/2024/NQ-HĐND ngày 10/12/2024 của Hội đồng nhân dân thành phố Hà Nội quy định "
        "giá cụ thể dịch vụ khám bệnh, chữa bệnh tại các cơ sở khám bệnh, chữa bệnh của "
        "Nhà nước thuộc Thành phố Hà Nội quản lý"
    )
    meta91 = base_meta(
        id=ID_91,
        title=title91,
        so_ky_hieu="91/2026/NQ-HĐND",
        ngay_ban_hanh="27/01/2026",
        loai_van_ban="Nghị quyết",
        ngay_co_hieu_luc="27/01/2026",
        nguon_thu_thap=(
            "LuatVietnam full text body; scan PDF kept as provenance "
            "(incoming/hanoi/91_2026_NQ-HDND_gia_dich_vu_KCB_SCAN.pdf). "
            "Phụ lục bảng giá chưa có trong bản này."
        ),
        co_quan_ban_hanh="HĐND Thành phố Hà Nội",
        chuc_danh="Chủ tịch",
        nguoi_ky="Phùng Thị Hồng Hà",
        agent_category="bhyt,vien_phi",
        linh_vuc="Khám chữa bệnh, Bảo hiểm y tế",
    )
    html91 = (INCOMING / "staging" / "91_2026_NQ-HDND.html").read_text(encoding="utf-8")
    docs.append((meta91, html91))
    return docs


def merge_into_raw(new_docs: list[tuple[dict[str, str], str]]) -> None:
    BACKUP.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for name in ("metadata.csv", "content.csv", "documents.csv", "metadata_bhyt.csv", "metadata_vien_phi.csv"):
        src = RAW / name
        if src.exists():
            shutil.copy2(src, BACKUP / f"{stamp}_{name}")

    metadata = read_csv(RAW / "metadata.csv")
    content = read_csv(RAW / "content.csv")
    documents = read_csv(RAW / "documents.csv")
    bhyt = read_csv(RAW / "metadata_bhyt.csv")
    vp = read_csv(RAW / "metadata_vien_phi.csv")

    existing_ids = {r["id"] for r in metadata}
    for meta, html_body in new_docs:
        if meta["id"] in existing_ids:
            for rows in (metadata, documents, bhyt, vp):
                for i, row in enumerate(rows):
                    if row.get("id") == meta["id"]:
                        updated = {**row, **{k: meta.get(k, row.get(k, "")) for k in META_FIELDS}}
                        if rows is documents:
                            updated["content_html"] = html_body
                        rows[i] = updated
            for i, row in enumerate(content):
                if row["id"] == meta["id"]:
                    content[i] = {
                        "id": meta["id"],
                        "agent_category": meta["agent_category"],
                        "content_html": html_body,
                    }
            continue

        metadata.append(meta)
        content.append({"id": meta["id"], "agent_category": meta["agent_category"], "content_html": html_body})
        documents.append({**meta, "content_html": html_body})
        # metadata_bhyt / metadata_vien_phi are full metadata projections
        if "bhyt" in meta["agent_category"]:
            bhyt.append({k: meta.get(k, "") for k in (bhyt[0].keys() if bhyt else META_FIELDS)})
        if "vien_phi" in meta["agent_category"]:
            vp.append({k: meta.get(k, "") for k in (vp[0].keys() if vp else META_FIELDS)})

    write_csv(RAW / "metadata.csv", META_FIELDS, metadata)
    write_csv(RAW / "content.csv", CONTENT_FIELDS, content)
    write_csv(RAW / "documents.csv", DOC_FIELDS, documents)
    write_csv(RAW / "metadata_bhyt.csv", list(bhyt[0].keys()) if bhyt else META_FIELDS, bhyt)
    write_csv(RAW / "metadata_vien_phi.csv", list(vp[0].keys()) if vp else META_FIELDS, vp)


def write_hanoi_module(new_docs: list[tuple[dict[str, str], str]]) -> None:
    if MODULE.exists():
        shutil.rmtree(MODULE)
    MODULE.mkdir(parents=True)

    metadata = read_csv(RAW / "metadata.csv")
    content = {r["id"]: r for r in read_csv(RAW / "content.csv")}
    by_id = {r["id"]: r for r in metadata}

    module_meta: list[dict[str, str]] = []
    module_content: list[dict[str, str]] = []
    module_docs: list[dict[str, str]] = []

    for doc_id in EXISTING_HANOI_IDS:
        meta = by_id[doc_id]
        html_body = content[doc_id]["content_html"]
        module_meta.append(meta)
        module_content.append({"id": doc_id, "agent_category": meta["agent_category"], "content_html": html_body})
        module_docs.append({**meta, "content_html": html_body})

    for meta, html_body in new_docs:
        # prefer freshly built rows
        module_meta = [r for r in module_meta if r["id"] != meta["id"]] + [meta]
        module_content = [r for r in module_content if r["id"] != meta["id"]] + [
            {"id": meta["id"], "agent_category": meta["agent_category"], "content_html": html_body}
        ]
        module_docs = [r for r in module_docs if r["id"] != meta["id"]] + [{**meta, "content_html": html_body}]

    write_csv(MODULE / "metadata.csv", META_FIELDS, module_meta)
    write_csv(MODULE / "content.csv", CONTENT_FIELDS, module_content)
    write_csv(MODULE / "documents.csv", DOC_FIELDS, module_docs)
    write_csv(MODULE / "metadata_bhyt.csv", META_FIELDS, [r for r in module_meta if "bhyt" in r.get("agent_category", "")])
    write_csv(
        MODULE / "metadata_vien_phi.csv",
        META_FIELDS,
        [r for r in module_meta if "vien_phi" in r.get("agent_category", "")],
    )

    manifest = {
        "module": "hanoi_bhyt",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "document_count": len(module_meta),
        "documents": [
            {
                "id": r["id"],
                "so_ky_hieu": r["so_ky_hieu"],
                "title": r["title"],
                "issuer": r["co_quan_ban_hanh"],
                "agent_category": r["agent_category"],
            }
            for r in module_meta
        ],
        "missing_recommended": [
            {
                "so_ky_hieu": "45/2024/NQ-HĐND",
                "why": "Base fee schedule; 91/2026 only amends it and replaces appendices. Need full PDF with phụ lục giá.",
                "link_hint": "Search LuatVietnam / Công báo Hà Nội: 45/2024/NQ-HĐND giá dịch vụ khám chữa bệnh",
            },
            {
                "so_ky_hieu": "91/2026/NQ-HĐND appendices",
                "why": "Current 91 body is present; fee tables in appendices still missing for price Q&A.",
                "link": "https://luatvietnam.vn/y-te/nghi-quyet-91-2026-nq-hdnd-ha-noi-sua-doi-bo-sung-gia-dich-vu-kham-chua-benh-426554-d2.html",
            },
        ],
        "serving_rule": "Serve Hanoi docs only when query jurisdiction is Hà Nội or local support/fee intent.",
    }
    (MODULE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (MODULE / "README.md").write_text(
        "\n".join(
            [
                "# Hanoi BHYT module",
                "",
                f"Generated: {manifest['generated_at']}",
                "",
                f"Documents: **{manifest['document_count']}**",
                "",
                "## Included",
                "",
                *[f"- `{d['so_ky_hieu']}` — {d['title'][:100]}" for d in manifest["documents"]],
                "",
                "## Serving",
                "",
                "- Default national answers: use `data/clean/serving_bhyt_slice/`",
                "- Local Hà Nội answers: retrieve from this module (or slice which already includes it)",
                "",
                "## Still missing for fee price lookup",
                "",
                "- `45/2024/NQ-HĐND` full text + appendices",
                "- `91/2026/NQ-HĐND` fee appendices",
                "",
            ]
        ),
        encoding="utf-8",
    )


def is_hanoi_row(row: dict[str, str]) -> bool:
    blob = f"{row.get('co_quan_ban_hanh', '')} {row.get('title', '')} {row.get('thong_tin_ap_dung', '')}".casefold()
    return "hà nội" in blob or "ha noi" in blob or "hanoi" in blob


def write_serving_slice() -> None:
    """All Trung ương + Hà Nội local only; exclude other provinces."""
    if SLICE.exists():
        shutil.rmtree(SLICE)
    SLICE.mkdir(parents=True)

    metadata = read_csv(RAW / "metadata.csv")
    content = {r["id"]: r for r in read_csv(RAW / "content.csv")}

    selected: list[dict[str, str]] = []
    for row in metadata:
        scope = (row.get("pham_vi") or "").strip()
        keep = False
        if scope == "Trung ương":
            keep = True
        elif row["id"] in EXISTING_HANOI_IDS or row["id"] in {ID_321, ID_SYT, ID_91}:
            keep = True
        elif is_hanoi_row(row):
            keep = True
        if not keep:
            continue
        html_body = (content.get(row["id"]) or {}).get("content_html", "")
        if not html_body.strip():
            continue
        selected.append(row)

    seen: set[str] = set()
    uniq: list[dict[str, str]] = []
    for row in selected:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        uniq.append(row)

    write_csv(SLICE / "metadata.csv", META_FIELDS, uniq)
    write_csv(
        SLICE / "content.csv",
        CONTENT_FIELDS,
        [
            {
                "id": r["id"],
                "agent_category": r["agent_category"],
                "content_html": content[r["id"]]["content_html"],
            }
            for r in uniq
            if r["id"] in content
        ],
    )
    write_csv(
        SLICE / "documents.csv",
        DOC_FIELDS,
        [{**r, "content_html": content[r["id"]]["content_html"]} for r in uniq if r["id"] in content],
    )
    write_csv(
        SLICE / "metadata_bhyt.csv",
        META_FIELDS,
        [r for r in uniq if "bhyt" in r.get("agent_category", "")],
    )
    write_csv(
        SLICE / "metadata_vien_phi.csv",
        META_FIELDS,
        [r for r in uniq if "vien_phi" in r.get("agent_category", "")],
    )
    rels = read_csv(RAW / "relationships.csv")
    ids = {r["id"] for r in uniq}
    kept_rels = [r for r in rels if r.get("doc_id") in ids or r.get("other_doc_id") in ids]
    if rels:
        write_csv(SLICE / "relationships.csv", list(rels[0].keys()), kept_rels)

    national = sum(1 for r in uniq if r.get("pham_vi") == "Trung ương")
    local = len(uniq) - national
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": "all_trung_uong_plus_hanoi_only",
        "document_count": len(uniq),
        "national_count": national,
        "hanoi_local_count": local,
        "relationship_count": len(kept_rels),
        "note": (
            "Serving corpus: ALL Trung ương documents with content + Hà Nội local only. "
            "Other provinces excluded to avoid regional retrieval noise."
        ),
        "ids": sorted(ids),
    }
    (SLICE / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (SLICE / "README.md").write_text(
        "\n".join(
            [
                "# Serving BHYT slice — all national + Hà Nội only",
                "",
                f"Documents: **{len(uniq)}** (Trung ương: {national}, Hà Nội: {local})",
                "",
                "Policy: keep every Trung ương document that has content; keep only Hà Nội for local scope.",
                "Exclude all other provinces.",
                "",
                "```bash",
                "python database/pipeline/scripts/ingest_snapshot.py --source-dir data/clean/serving_bhyt_slice",
                "```",
                "",
                "Presentation notes: `docs/PRESENTATION_BHYT_CORPUS.md`",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> None:
    new_docs = build_new_docs()
    merge_into_raw(new_docs)
    write_hanoi_module(new_docs)
    audit_script = ROOT / "scripts" / "bhyt_corpus_audit.py"
    if audit_script.exists():
        import subprocess
        import sys

        subprocess.run([sys.executable, str(audit_script)], check=False)
    write_serving_slice()
    print("Merged new Hanoi docs:", [m["so_ky_hieu"] for m, _ in new_docs])
    print("IDs:", [m["id"] for m, _ in new_docs])
    print("Module:", MODULE)
    print("Slice:", SLICE)
    print("Raw backup:", BACKUP)


if __name__ == "__main__":
    main()
