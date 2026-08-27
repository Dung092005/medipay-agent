#!/usr/bin/env python3
"""Create an embedding artifact with OpenAI text-embedding-3-small."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()
from data_pipeline.canonical import build_snapshot
from data_pipeline.embedding import dimensions, embed_batch, model_name


def encode_snapshot(source_dir: str | Path, output_root: str | Path, *, batch_size: int) -> Path:
    if batch_size <= 0:
        raise ValueError("batch size must be positive")
    snapshot = build_snapshot(source_dir)
    passages = [row for row in snapshot.passages if bool(row.get("semantic_eligible", True))]
    inputs = ["\n\n".join(part for part in (str(row.get("section_label", "")), str(row.get("text", ""))) if part) for row in passages]
    vectors: list[list[float]] = []
    for start in range(0, len(inputs), batch_size):
        vectors.extend(embed_batch(inputs[start:start + batch_size]))
    target = Path(output_root) / snapshot.dataset_id
    target.mkdir(parents=True, exist_ok=True)
    np.save(target / "embeddings.float32.npy", np.asarray(vectors, dtype=np.float32))
    with (target / "passages.jsonl").open("w", encoding="utf-8") as handle:
        for row, text in zip(passages, inputs, strict=True):
            handle.write(json.dumps({"passage_id": row["passage_id"], "document_id": row["document_id"], "unit_id": row["unit_id"], "source_start": row["source_start"], "source_end": row["source_end"], "input_sha256": hashlib.sha256(text.encode()).hexdigest()}, ensure_ascii=False) + "\n")
    manifest = {"artifact_type": "openai_embedding_snapshot", "dataset_id": snapshot.dataset_id, "model": model_name(), "dimensions": dimensions(), "rows": len(vectors), "normalized": True}
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", default="data/raw")
    parser.add_argument("--output-dir", default="data/clean/embeddings")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    print(f"Embedding artifact written to {encode_snapshot(args.source_dir, args.output_dir, batch_size=args.batch_size)}")
