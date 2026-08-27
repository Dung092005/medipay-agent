"""Async Qdrant adapter for the immutable active corpus release."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from src.config import get_settings


@dataclass(frozen=True)
class VectorHit:
    chunk_id: str
    document_id: str
    unit_id: str
    score: float
    input_sha256: str


class QdrantVectorStore:
    """Small read-only boundary around the active Qdrant collection alias."""

    _SEARCH_MANY_CONCURRENCY = 3

    def __init__(self) -> None:
        from qdrant_client import AsyncQdrantClient

        settings = get_settings()
        if not settings.qdrant_url or not settings.qdrant_api_key:
            raise RuntimeError("QDRANT_URL and QDRANT_API_KEY are required")
        self.collection = settings.qdrant_collection
        # Qdrant's REST ``timeout`` query parameter is integral on current
        # Cloud clusters; passing Pydantic's float directly yields ``30.0``
        # and a 400 response.
        self.timeout = max(1, int(settings.qdrant_timeout_seconds))
        self.dimensions = settings.embedding_dimensions
        self.client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=self.timeout,
            cloud_inference=True,
        )
        self._hybrid_bm25: bool | None = None
        self._hybrid_bm25_checked_at = 0.0

    async def _supports_hybrid_bm25(self) -> bool:
        now = time.monotonic()
        if self._hybrid_bm25 is not None and now - self._hybrid_bm25_checked_at < 300:
            return self._hybrid_bm25
        try:
            info = await self.client.get_collection(self.collection)
            vectors = info.config.params.vectors
            sparse = info.config.params.sparse_vectors or {}
            self._hybrid_bm25 = (
                isinstance(vectors, dict)
                and "dense" in vectors
                and "bm25" in sparse
            )
        except Exception:
            self._hybrid_bm25 = False
        self._hybrid_bm25_checked_at = now
        return self._hybrid_bm25

    def _query_filter(self, dataset_id: str, document_ids: Sequence[str] | None):
        from qdrant_client import models

        conditions: list[models.FieldCondition] = [
            models.FieldCondition(key="dataset_id", match=models.MatchValue(value=dataset_id)),
            models.FieldCondition(key="answer_ready", match=models.MatchValue(value=True)),
        ]
        if document_ids:
            conditions.append(
                models.FieldCondition(
                    key="document_id", match=models.MatchAny(any=list(dict.fromkeys(document_ids))),
                )
            )
        return models.Filter(must=conditions)

    @staticmethod
    def _hits(response) -> list[VectorHit]:
        return [
            VectorHit(
                chunk_id=str(point.payload.get("passage_id") or point.id).replace("-", ""),
                document_id=str(point.payload.get("document_id") or ""),
                unit_id=str(point.payload.get("unit_id") or ""),
                score=float(point.score),
                input_sha256=str(point.payload.get("input_sha256") or ""),
            )
            for point in response.points
            if point.payload and point.payload.get("document_id")
        ]

    async def _gather_bounded(self, coroutines):
        semaphore = asyncio.Semaphore(self._SEARCH_MANY_CONCURRENCY)

        async def bounded(coroutine):
            async with semaphore:
                return await coroutine

        return await asyncio.gather(*(bounded(coroutine) for coroutine in coroutines))

    async def search(
        self,
        vector: Sequence[float],
        *,
        query_text: str,
        dataset_id: str,
        limit: int,
        document_ids: Sequence[str] | None = None,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[VectorHit]:
        """Search only the selected release and answer-ready source passages."""
        if len(vector) != self.dimensions or limit <= 0:
            return []
        from qdrant_client import models

        dense_vector = [float(value) for value in vector]
        query_filter = self._query_filter(dataset_id, document_ids)
        if await self._supports_hybrid_bm25():
            try:
                response = await self.client.query_points(
                    collection_name=self.collection,
                    prefetch=[
                        models.Prefetch(
                            query=dense_vector,
                            using="dense",
                            limit=limit,
                            score_threshold=score_threshold,
                        ),
                        models.Prefetch(
                            query=models.Document(text=query_text, model="qdrant/bm25"),
                            using="bm25",
                            limit=limit,
                        ),
                    ],
                    query=models.FusionQuery(fusion=models.Fusion.RRF),
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=["passage_id", "document_id", "unit_id", "input_sha256"],
                    with_vectors=False,
                    timeout=self.timeout,
                )
                return self._hits(response)
            except Exception:
                self._hybrid_bm25 = False
                self._hybrid_bm25_checked_at = time.monotonic()

        response = await self.client.query_points(
            collection_name=self.collection,
            query=dense_vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=["passage_id", "document_id", "unit_id", "input_sha256"],
            with_vectors=False,
            score_threshold=score_threshold,
            timeout=self.timeout,
        )
        return self._hits(response)

    async def search_many(
        self,
        vectors: Sequence[Sequence[float]],
        *,
        query_texts: Sequence[str] | None = None,
        dataset_id: str,
        limit: int,
        document_ids: Sequence[str] | None = None,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[list[VectorHit]]:
        """Search bounded sub-query vectors in one Qdrant batch when supported.

        Older Qdrant clients/servers fall back to the same bounded concurrent
        adapter, preserving ordering and release filters without changing
        correctness.
        """
        values = [list(vector) for vector in vectors]
        if not values:
            return []
        if any(len(vector) != self.dimensions for vector in values) or limit <= 0:
            return [[] for _ in values]
        if query_texts is not None and len(query_texts) != len(values):
            raise ValueError("query_texts must have the same length as vectors")
        if query_texts is not None and await self._supports_hybrid_bm25():
            return await self._gather_bounded(
                self.search(
                    vector,
                    query_text=query_text,
                    dataset_id=dataset_id,
                    limit=limit,
                    document_ids=document_ids,
                    score_threshold=score_threshold,
                )
                for vector, query_text in zip(values, query_texts)
            )

        from qdrant_client import models

        query_filter = self._query_filter(dataset_id, document_ids)

        async def one(vector: Sequence[float]) -> list[VectorHit]:
            response = await self.client.query_points(
                self.collection,
                query=[float(value) for value in vector],
                query_filter=query_filter,
                limit=limit,
                with_payload=["passage_id", "document_id", "unit_id", "input_sha256"],
                with_vectors=False,
                score_threshold=score_threshold,
                timeout=self.timeout,
            )
            return self._hits(response)

        # The client API exposes query_batch only in newer releases. Keep the
        # fallback explicit so dependency upgrades cannot silently widen scope.
        query_batch = getattr(self.client, "query_batch", None)
        if query_batch is None:
            return await self._gather_bounded(one(vector) for vector in values)
        requests = [
            models.QueryRequest(
                query=vector,
                filter=query_filter,
                limit=limit,
                with_payload=["passage_id", "document_id", "unit_id", "input_sha256"],
                with_vector=False,
                score_threshold=score_threshold,
            )
            for vector in values
        ]
        try:
            responses = await query_batch(collection_name=self.collection, requests=requests)
        except (AttributeError, TypeError, NotImplementedError):
            return await self._gather_bounded(one(vector) for vector in values)
        return [
            self._hits(response)
            for response in responses
        ]

    async def readiness(self, *, dataset_id: str, expected_points: int) -> bool:
        """Validate alias shape and release point count without touching PostgreSQL."""
        from qdrant_client import models

        if not await self.client.collection_exists(self.collection):
            return False
        info = await self.client.get_collection(self.collection)
        vectors = info.config.params.vectors
        dense_vectors = vectors.get("dense") if isinstance(vectors, dict) else vectors
        if getattr(dense_vectors, "size", None) != self.dimensions:
            return False
        metadata = getattr(info.config, "metadata", None) or {}
        collection_points = int(metadata.get("artifact_rows", 0) or 0)
        required_points = expected_points or collection_points
        if required_points <= 0:
            return False
        count = await self.client.count(
            self.collection,
            count_filter=models.Filter(
                must=[models.FieldCondition(key="dataset_id", match=models.MatchValue(value=dataset_id))]
            ),
            exact=True,
            timeout=self.timeout,
        )
        return count.count == required_points

    async def close(self) -> None:
        await self.client.close()
