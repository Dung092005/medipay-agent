import csv
from pathlib import Path

from scripts.build_serving_p151_hanoi import build_corpus


META_FIELDS = [
    "id", "title", "so_ky_hieu", "pham_vi", "thong_tin_ap_dung",
    "co_quan_ban_hanh", "agent_category",
]
CONTENT_FIELDS = ["id", "agent_category", "content_html"]
RELATIONSHIP_FIELDS = ["doc_id", "other_doc_id", "relationship"]


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _row(identifier: str, *, title: str, scope: str, applies: str = "") -> dict[str, str]:
    return {
        "id": identifier,
        "title": title,
        "so_ky_hieu": f"{identifier}/2026/QD-X",
        "pham_vi": scope,
        "thong_tin_ap_dung": applies,
        "co_quan_ban_hanh": title,
        "agent_category": "bhyt",
    }


def test_builder_keeps_all_p151_localities_and_module_overrides(tmp_path: Path):
    base = tmp_path / "p151"
    module = tmp_path / "hanoi"
    output = tmp_path / "serving"
    base.mkdir()
    module.mkdir()

    base_rows = [
        _row("central", title="Central rule", scope="Trung ương"),
        _row("danang", title="Da Nang rule", scope="Địa phương", applies="Thành phố Đà Nẵng"),
        _row("hcm", title="HCM rule", scope="Địa phương", applies="TP. Hồ Chí Minh"),
        _row("override", title="Old Hanoi rule", scope="Địa phương", applies="Hà Nội"),
        _row("", title="Missing id", scope="Địa phương"),
    ]
    _write_csv(base / "metadata.csv", META_FIELDS, base_rows)
    _write_csv(
        base / "content.csv",
        CONTENT_FIELDS,
        [
            {"id": row["id"], "agent_category": "bhyt", "content_html": f"<p>{row['title']}</p>"}
            for row in base_rows
            if row["id"]
        ],
    )
    _write_csv(
        base / "relationships.csv",
        RELATIONSHIP_FIELDS,
        [
            {"doc_id": "central", "other_doc_id": "danang", "relationship": "Căn cứ"},
            {"doc_id": "central", "other_doc_id": "unknown", "relationship": "Bãi bỏ"},
        ],
    )

    module_rows = [
        _row("override", title="Module Hanoi override", scope="Địa phương"),
        _row("hanoi-new", title="New Hanoi rule", scope="Địa phương"),
    ]
    _write_csv(module / "metadata.csv", META_FIELDS, module_rows)
    _write_csv(
        module / "content.csv",
        CONTENT_FIELDS,
        [
            {"id": row["id"], "agent_category": "bhyt", "content_html": f"<p>{row['title']}</p>"}
            for row in module_rows
        ],
    )

    summary = build_corpus(base_dir=base, module_dir=module, output_dir=output)

    with (output / "metadata.csv").open(encoding="utf-8-sig", newline="") as handle:
        metadata = {row["id"]: row for row in csv.DictReader(handle)}
    with (output / "content.csv").open(encoding="utf-8-sig", newline="") as handle:
        content = {row["id"]: row["content_html"] for row in csv.DictReader(handle)}
    with (output / "relationships.csv").open(encoding="utf-8-sig", newline="") as handle:
        relationships = list(csv.DictReader(handle))

    assert set(metadata) == {"central", "danang", "hcm", "override", "hanoi-new"}
    assert summary["document_count"] == 5
    assert metadata["central"]["pham_vi"] == "Trung ương"
    assert metadata["central"]["province"] == ""
    assert metadata["danang"]["province"] == "Đà Nẵng"
    assert metadata["hcm"]["province"] == "Hồ Chí Minh"
    assert metadata["override"]["province"] == "Hà Nội"
    assert metadata["override"]["thong_tin_ap_dung"] == "Thành phố Hà Nội"
    assert content["override"] == "<p>Module Hanoi override</p>"
    assert len(relationships) == 1
    assert relationships[0]["doc_id"] == "central"
    assert relationships[0]["other_doc_id"] == "danang"
