#!/usr/bin/env python3
"""Publish a verified local embedding artifact to a versioned Qdrant release.

PostgreSQL remains the canonical store for document text and provenance.  This
tool only creates a derived Qdrant index, verifies it against the immutable
artifact, then (optionally) moves a stable alias in one atomic operation.
It deliberately never deletes a collection or overwrites an alias before the
new collection has passed parity.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

PAYLOAD_INDEXES: tuple[tuple[str, models.PayloadSchemaType], ...] = (
    ("dataset_id", models.PayloadSchemaType.KEYWORD),
    ("document_id", models.PayloadSchemaType.KEYWORD),
    ("answer_ready", models.PayloadSchemaType.BOOL),
    ("retrieval_scope", models.PayloadSchemaType.KEYWORD),
    ("legal_status", models.PayloadSchemaType.KEYWORD),
    ("categories", models.PayloadSchemaType.KEYWORD),
)


@dataclass(frozen=True)
class Artifact:
    root: Path
    dataset_id: str
    model: str
    dimensions: int
    rows: int
    normalized: bool
    vectors: np.ndarray
    passages: list[dict[str, Any]]


def collection_name_for(dataset_id: str) -> str:
    """Create a Qdrant-safe physical collection name from a release ID."""
    return "medical_legal_" + "".join(
        character if character.isalnum() or character in "_-" else "_"
        for character in dataset_id.casefold()
    )


def load_artifact(root: Path, expected_dataset_id: str | None = None) -> Artifact:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    dataset_id = str(manifest.get("dataset_id") or manifest.get("release_id") or "")
    if not dataset_id:
        raise ValueError("embedding artifact has no dataset_id")
    if expected_dataset_id and dataset_id != expected_dataset_id:
        raise ValueError("embedding artifact belongs to a different dataset")
    vectors = np.load(root / "embeddings.float32.npy", mmap_mode="r")
    passages = [
        json.loads(line)
        for line in (root / "passages.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = int(manifest["rows"])
    dimensions = int(manifest["dimensions"])
    if vectors.shape != (rows, dimensions) or len(passages) != rows:
        raise ValueError("artifact vector/metadata dimensions do not match manifest")
    if not np.isfinite(vectors).all():
        raise ValueError("artifact contains non-finite vector values")
    passage_ids = [str(row.get("passage_id", "")) for row in passages]
    if len(set(passage_ids)) != rows or any(len(identifier) != 32 for identifier in passage_ids):
        raise ValueError("artifact passage IDs must be unique 32-character Qdrant UUIDs")
    hashes = [str(row.get("input_sha256", "")) for row in passages]
    if any(len(value) != 64 for value in hashes):
        raise ValueError("artifact contains an invalid input_sha256")
    return Artifact(
        root=root,
        dataset_id=dataset_id,
        model=str(manifest.get("model", "unknown")),
        dimensions=dimensions,
        rows=rows,
        normalized=bool(manifest.get("normalized", False)),
        vectors=vectors,
        passages=passages,
    )


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes"}


def load_document_payloads(metadata_path: Path, dataset_id: str) -> dict[str, dict[str, Any]]:
    """Return only non-content metadata that is safe and useful for filtering."""
    result: dict[str, dict[str, Any]] = {}
    with metadata_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            document_id = str(row.get("id", ""))
            if not document_id:
                continue
            categories = [
                value.strip() for value in (
                    row.get("agent_category", ""), row.get("nganh", ""), row.get("linh_vuc", "")
                ) if value and value.strip()
            ]
            # Curated serving CSVs often omit answer_ready. Treat missing as
            # ready so Qdrant runtime filters do not drop the whole corpus.
            raw_ready = row.get("answer_ready")
            answer_ready = True if raw_ready is None or str(raw_ready).strip() == "" else _as_bool(raw_ready)
            result[document_id] = {
                "dataset_id": dataset_id,
                "dataset_version": dataset_id,
                "answer_ready": answer_ready,
                "retrieval_scope": str(row.get("retrieval_scope", "")),
                "legal_status": str(row.get("tinh_trang_hieu_luc", "")),
                "categories": categories,
            }
    if not result:
        raise ValueError("metadata.csv did not contain any documents")
    return result


def build_points(artifact: Artifact, document_payloads: dict[str, dict[str, Any]]) -> Iterable[models.PointStruct]:
    for passage, vector in zip(artifact.passages, artifact.vectors, strict=True):
        document_id = str(passage["document_id"])
        document_payload = document_payloads.get(document_id)
        if document_payload is None:
            raise ValueError(f"passage {passage['passage_id']} references unknown document {document_id}")
        payload = {
            **document_payload,
            "passage_id": str(passage["passage_id"]),
            "document_id": document_id,
            "unit_id": str(passage.get("unit_id") or ""),
            "source_start": int(passage.get("source_start") or 0),
            "source_end": int(passage.get("source_end") or 0),
            "input_sha256": str(passage["input_sha256"]),
            "embedding_model": artifact.model,
            "embedding_dimensions": artifact.dimensions,
        }
        yield models.PointStruct(
            id=str(passage["passage_id"]),
            vector=np.asarray(vector, dtype=np.float32).tolist(),
            payload=payload,
        )


def _assert_collection_shape(client: QdrantClient, collection: str, dimensions: int) -> None:
    info = client.get_collection(collection)
    vectors = info.config.params.vectors
    if not isinstance(vectors, models.VectorParams) or vectors.size != dimensions:
        raise ValueError(f"Qdrant collection {collection} has incompatible vector dimensions")
    if vectors.distance != models.Distance.COSINE:
        raise ValueError(f"Qdrant collection {collection} must use cosine distance")


def ensure_collection(client: QdrantClient, collection: str, artifact: Artifact) -> None:
    if not client.collection_exists(collection):
        client.create_collection(
            collection_name=collection,
            vectors_config=models.VectorParams(size=artifact.dimensions, distance=models.Distance.COSINE),
            metadata={
                "dataset_id": artifact.dataset_id,
                "embedding_model": artifact.model,
                "embedding_dimensions": artifact.dimensions,
                "artifact_rows": artifact.rows,
            },
            timeout=60,
        )
    _assert_collection_shape(client, collection, artifact.dimensions)
    for field_name, field_type in PAYLOAD_INDEXES:
        client.create_payload_index(collection, field_name, field_type, wait=True, timeout=60)


def upload_points(client: QdrantClient, collection: str, points: Iterable[models.PointStruct], *, batch_size: int) -> int:
    batches: list[list[models.PointStruct]] = []
    batch: list[models.PointStruct] = []
    for point in points:
        batch.append(point)
        if len(batch) >= batch_size:
            batches.append(batch)
            batch = []
    if batch:
        batches.append(batch)
    # Cloud requests dominate CPU time.  A small bounded fan-out keeps each
    # Qdrant write fully acknowledged without making a one-off release upload
    # needlessly serial.  Point IDs make retries idempotent.
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(client.upsert, collection, points=part, wait=True, timeout=120)
            for part in batches
        ]
        for future in futures:
            future.result()
    return sum(len(part) for part in batches)


def verify_parity(client: QdrantClient, collection: str, artifact: Artifact, *, sample_size: int = 64) -> dict[str, Any]:
    """Verify every ID/hash plus a deterministic sample of returned vectors."""
    expected = {str(row["passage_id"]): str(row["input_sha256"]) for row in artifact.passages}
    actual: dict[str, str] = {}
    batches = [list(expected)[start : start + 256] for start in range(0, len(expected), 256)]

    def retrieve_batch(identifiers: list[str]):
        return client.retrieve(
            collection, ids=identifiers,
            with_payload=["passage_id", "dataset_id", "input_sha256", "embedding_dimensions"],
            with_vectors=False, timeout=120,
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        for points in executor.map(retrieve_batch, batches):
            for point in points:
                payload = point.payload or {}
                point_id = str(point.id).replace("-", "")
                if payload.get("dataset_id") != artifact.dataset_id:
                    raise ValueError(f"Qdrant point {point_id} belongs to another dataset")
                if int(payload.get("embedding_dimensions", 0)) != artifact.dimensions:
                    raise ValueError(f"Qdrant point {point_id} has an unexpected dimension")
                actual[point_id] = str(payload.get("input_sha256", ""))
    if actual != expected:
        missing = sorted(set(expected) - set(actual))[:10]
        unexpected = sorted(set(actual) - set(expected))[:10]
        changed = sorted(identifier for identifier in set(expected) & set(actual) if expected[identifier] != actual[identifier])[:10]
        raise ValueError(f"Qdrant parity mismatch missing={missing} unexpected={unexpected} changed_hash={changed}")
    rng = random.Random(artifact.dataset_id)
    sample_indexes = sorted(rng.sample(range(artifact.rows), k=min(sample_size, artifact.rows)))
    records = client.retrieve(
        collection,
        ids=[str(artifact.passages[index]["passage_id"]) for index in sample_indexes],
        with_payload=False,
        with_vectors=True,
        timeout=120,
    )
    vectors_by_id = {str(record.id).replace("-", ""): np.asarray(record.vector, dtype=np.float32) for record in records}
    for index in sample_indexes:
        identifier = str(artifact.passages[index]["passage_id"])
        actual_vector = vectors_by_id.get(identifier)
        expected_vector = np.asarray(artifact.vectors[index], dtype=np.float32)
        if actual_vector is None or actual_vector.shape != expected_vector.shape:
            raise ValueError(f"Qdrant vector sample mismatch for {identifier}")
        denominator = float(np.linalg.norm(actual_vector) * np.linalg.norm(expected_vector))
        cosine = float(np.dot(actual_vector, expected_vector) / denominator) if denominator else 0.0
        # Cosine collections normalize vectors during ingestion, so bytewise
        # equality is neither expected nor useful.  Direction parity is the
        # release contract.
        if cosine < 0.999999:
            raise ValueError(f"Qdrant vector cosine mismatch for {identifier}: {cosine:.8f}")
    return {"point_count": len(actual), "id_hash_parity": True, "vector_samples_checked": len(sample_indexes)}


def activate_alias(client: QdrantClient, alias: str, collection: str) -> None:
    existing = {item.alias_name: item.collection_name for item in client.get_aliases().aliases}
    if existing.get(alias) == collection:
        return
    changes: list[models.CreateAliasOperation | models.DeleteAliasOperation] = []
    if alias in existing:
        changes.append(models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=alias)))
    changes.append(models.CreateAliasOperation(create_alias=models.CreateAlias(collection_name=collection, alias_name=alias)))
    client.update_collection_aliases(changes, timeout=60)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--metadata-csv", type=Path, required=True)
    parser.add_argument("--dataset-id")
    parser.add_argument("--collection")
    parser.add_argument("--alias", default=os.getenv("QDRANT_COLLECTION", "medical_legal_active"))
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--activate", action="store_true", help="Move the stable alias after all parity checks pass.")
    parser.add_argument("--verify-only", action="store_true", help="Verify an existing physical collection without sending upserts.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 64 <= args.batch_size <= 256:
        raise ValueError("batch-size must be between 64 and 256")
    load_dotenv()
    if not os.getenv("QDRANT_URL") or not os.getenv("QDRANT_API_KEY"):
        raise RuntimeError("QDRANT_URL and QDRANT_API_KEY are required")
    artifact = load_artifact(args.artifact_dir, args.dataset_id)
    collection = args.collection or collection_name_for(artifact.dataset_id)
    document_payloads = load_document_payloads(args.metadata_csv, artifact.dataset_id)
    report: dict[str, Any] = {
        "dataset_id": artifact.dataset_id,
        "collection": collection,
        "alias": args.alias,
        "artifact_rows": artifact.rows,
        "dimensions": artifact.dimensions,
        "model": artifact.model,
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    client = QdrantClient(url=os.environ["QDRANT_URL"], api_key=os.environ["QDRANT_API_KEY"], timeout=120)
    if args.verify_only:
        if not client.collection_exists(collection):
            raise ValueError(f"Qdrant collection {collection} does not exist")
        _assert_collection_shape(client, collection, artifact.dimensions)
    else:
        ensure_collection(client, collection, artifact)
        report["uploaded_or_replaced_points"] = upload_points(
            client, collection, build_points(artifact, document_payloads), batch_size=args.batch_size
        )
    report["parity"] = verify_parity(client, collection, artifact)
    if args.activate:
        activate_alias(client, args.alias, collection)
        report["alias_activated"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
