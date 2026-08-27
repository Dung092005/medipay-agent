import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from qdrant_client import models

from src.integrations.qdrant import QdrantVectorStore


def make_store(client, *, dimensions=3):
    store = object.__new__(QdrantVectorStore)
    store.collection = "medical_legal_active"
    store.dimensions = dimensions
    store.timeout = 5
    store.client = client
    store._hybrid_bm25 = None
    store._hybrid_bm25_checked_at = 0.0
    return store


@pytest.mark.asyncio
async def test_hybrid_search_uses_dense_and_bm25_prefetch_with_rrf():
    client = SimpleNamespace(query_points=AsyncMock(return_value=SimpleNamespace(points=[])))
    store = make_store(client)
    store._supports_hybrid_bm25 = AsyncMock(return_value=True)

    await store.search(
        [0.1, 0.2, 0.3], query_text="mức hưởng bảo hiểm y tế",
        dataset_id="release-1", limit=8, score_threshold=0.2,
    )

    call = client.query_points.await_args.kwargs
    assert len(call["prefetch"]) == 2
    assert call["prefetch"][0].using == "dense"
    assert call["prefetch"][1].using == "bm25"
    assert isinstance(call["query"], models.FusionQuery)
    assert "score_threshold" not in call


@pytest.mark.asyncio
async def test_hybrid_failure_falls_back_to_dense_and_disables_capability():
    client = SimpleNamespace(
        query_points=AsyncMock(
            side_effect=[RuntimeError("hybrid unavailable"), SimpleNamespace(points=[])]
        )
    )
    store = make_store(client)
    store._supports_hybrid_bm25 = AsyncMock(return_value=True)

    assert await store.search(
        [0.1, 0.2, 0.3], query_text="BHYT", dataset_id="release-1", limit=8
    ) == []
    assert client.query_points.await_count == 2
    assert store._hybrid_bm25 is False
    assert client.query_points.await_args.kwargs["query"] == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_readiness_accepts_named_dense_vector_in_hybrid_release():
    client = SimpleNamespace(
        collection_exists=AsyncMock(return_value=True),
        get_collection=AsyncMock(return_value=SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(vectors={"dense": SimpleNamespace(size=3)}),
                metadata={"artifact_rows": 2},
            )
        )),
        count=AsyncMock(return_value=SimpleNamespace(count=2)),
    )
    store = make_store(client)
    assert await store.readiness(dataset_id="release-1", expected_points=2)


@pytest.mark.asyncio
async def test_hybrid_capability_is_rechecked_after_cache_ttl(monkeypatch):
    client = SimpleNamespace(get_collection=AsyncMock(return_value=SimpleNamespace(
        config=SimpleNamespace(params=SimpleNamespace(
            vectors={"dense": SimpleNamespace(size=3)},
            sparse_vectors={"bm25": SimpleNamespace()},
        ))
    )))
    store = make_store(client)
    store._hybrid_bm25 = False
    store._hybrid_bm25_checked_at = 100.0
    monkeypatch.setattr("src.integrations.qdrant.time.monotonic", lambda: 401.0)

    assert await store._supports_hybrid_bm25() is True
    client.get_collection.assert_awaited_once()


@pytest.mark.asyncio
async def test_hybrid_search_many_bounds_concurrent_queries():
    active_queries = 0
    peak_queries = 0

    async def query_points(**kwargs):
        nonlocal active_queries, peak_queries
        assert "prefetch" in kwargs
        active_queries += 1
        peak_queries = max(peak_queries, active_queries)
        await asyncio.sleep(0.01)
        active_queries -= 1
        return SimpleNamespace(points=[])

    client = SimpleNamespace(query_points=AsyncMock(side_effect=query_points))
    store = make_store(client)
    store._supports_hybrid_bm25 = AsyncMock(return_value=True)

    result = await store.search_many(
        [[0.1, 0.2, 0.3]] * 9,
        query_texts=[f"question {index}" for index in range(9)],
        dataset_id="release-1",
        limit=8,
    )

    assert result == [[]] * 9
    assert peak_queries <= 3
