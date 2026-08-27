#!/usr/bin/env python3
"""Build an embedding artifact by safely reusing vectors from a release backup.

Only vectors whose canonical embedding-input SHA-256 still matches are reused.
Any genuinely new inputs are embedded with the configured OpenAI model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()

from data_pipeline.canonical import build_snapshot  # noqa: E402
from data_pipeline.embedding import dimensions, embed_batch, model_name  # noqa: E402


def input_text(row: dict[str, Any]) -> str:
    return "\n\n".join(
        part for part in (str(row.get("section_label", "")), str(row.get("text", ""))) if part
    )


def input_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_vector(value: Any) -> np.ndarray:
    if isinstance(value, str):
        vector = np.fromstring(value.strip().strip("[]"), sep=",", dtype=np.float32)
    else:
        vector = np.asarray(value, dtype=np.float32)
    if vector.ndim != 1 or vector.size != dimensions() or not np.isfinite(vector).all():
        raise ValueError("backup contains an invalid embedding vector")
    return vector


def reusable_vectors(backup: dict[str, Any]) -> dict[tuple[str, str], np.ndarray]:
    result: dict[tuple[str, str], np.ndarray] = {}
    for row in backup.get("tables", {}).get("chunks", []):
        if not row.get("semantic_eligible") or row.get("embedding") is None:
            continue
        digest = str(row.get("embedded_input_sha256") or "")
        passage_id = str(row.get("chunk_id") or "")
        if len(digest) == 64 and passage_id:
            result[(passage_id, digest)] = parse_vector(row["embedding"])
    return result


def build_artifact(
    source_dir: Path, backup_path: Path, output_root: Path, *, batch_size: int
) -> tuple[Path, dict[str, int]]:
    if batch_size <= 0:
        raise ValueError("batch size must be positive")
    snapshot = build_snapshot(source_dir)
    passages = [row for row in snapshot.passages if bool(row.get("semantic_eligible", True))]
    texts = [input_text(row) for row in passages]
    digests = [input_sha256(text) for text in texts]
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    old = reusable_vectors(backup)

    vectors: list[np.ndarray | None] = [None] * len(passages)
    missing: list[int] = []
    for index, (row, digest) in enumerate(zip(passages, digests, strict=True)):
        vector = old.get((str(row["passage_id"]), digest))
        if vector is None:
            missing.append(index)
        else:
            vectors[index] = vector

    for start in range(0, len(missing), batch_size):
        indexes = missing[start : start + batch_size]
        embedded = embed_batch([texts[index] for index in indexes])
        for index, vector in zip(indexes, embedded, strict=True):
            vectors[index] = np.asarray(vector, dtype=np.float32)

    if any(vector is None for vector in vectors):
        raise RuntimeError("embedding artifact is incomplete")
    matrix = np.stack(vectors).astype(np.float32, copy=False)  # type: ignore[arg-type]
    target = output_root / snapshot.dataset_id
    target.mkdir(parents=True, exist_ok=False)
    np.save(target / "embeddings.float32.npy", matrix)
    with (target / "passages.jsonl").open("w", encoding="utf-8") as handle:
        for row, digest in zip(passages, digests, strict=True):
            handle.write(json.dumps({
                "passage_id": row["passage_id"],
                "document_id": row["document_id"],
                "unit_id": row["unit_id"],
                "source_start": row["source_start"],
                "source_end": row["source_end"],
                "input_sha256": digest,
            }, ensure_ascii=False) + "\n")
    manifest = {
        "artifact_type": "openai_embedding_snapshot_reused",
        "dataset_id": snapshot.dataset_id,
        "model": model_name(),
        "dimensions": dimensions(),
        "rows": len(passages),
        "normalized": True,
        "reused_rows": len(passages) - len(missing),
        "newly_embedded_rows": len(missing),
        "source_backup_dataset_id": backup.get("active_dataset_id", ""),
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target, {"rows": len(passages), "reused": len(passages) - len(missing), "embedded": len(missing)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    target, counts = build_artifact(
        args.source_dir, args.backup, args.output_dir, batch_size=args.batch_size
    )
    print(json.dumps({"artifact_dir": str(target), **counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
