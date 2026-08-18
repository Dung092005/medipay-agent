"""Write the deterministic legal PageIndex graph as immutable release artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from data_pipeline.canonical import CanonicalSnapshot


def export_page_index(snapshot: CanonicalSnapshot, output_root: str | Path) -> Path:
    """Export node JSONL, parent edges CSV and a manifest under ``dataset_id``.

    The caller can safely publish the resulting directory as a read-only
    artifact.  It contains derived text/provenance only; raw HTML stays in the
    canonical release storage.
    """

    target = Path(output_root) / snapshot.dataset_id
    target.mkdir(parents=True, exist_ok=True)
    nodes_path = target / "page_index_nodes.jsonl"
    edges_path = target / "page_index_edges.csv"
    manifest_path = target / "manifest.json"
    with nodes_path.open("w", encoding="utf-8", newline="") as handle:
        for node in snapshot.legal_units:
            handle.write(json.dumps(node, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    with edges_path.open("w", encoding="utf-8", newline="") as handle:
        fields = ["dataset_id", "edge_id", "source_unit_id", "target_unit_id", "predicate"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for node in snapshot.legal_units:
            parent = str(node.get("parent_unit_id", ""))
            if not parent:
                continue
            writer.writerow({
                "dataset_id": snapshot.dataset_id,
                "edge_id": f"{parent}:HAS_UNIT:{node['unit_id']}",
                "source_unit_id": parent,
                "target_unit_id": node["unit_id"],
                "predicate": "HAS_UNIT",
            })
    manifest: dict[str, Any] = {
        "artifact_type": "legal_page_index",
        "dataset_id": snapshot.dataset_id,
        "source_manifest_sha256": snapshot.manifest["source_manifest_sha256"],
        "parser_version": snapshot.manifest["legal_unit_version"],
        "node_count": len(snapshot.legal_units),
        "edge_count": sum(bool(node.get("parent_unit_id")) for node in snapshot.legal_units),
        "files": {"nodes": nodes_path.name, "edges": edges_path.name},
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
