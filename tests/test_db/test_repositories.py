import pytest

from src.db.repositories import GraphRepository


class CaptureSession:
    def __init__(self) -> None:
        self.statement = None
        self.parameters = None

    async def execute(self, statement, parameters):
        self.statement = statement
        self.parameters = parameters
        return []


@pytest.mark.asyncio
async def test_search_vectors_restricts_query_to_semantic_eligible_chunks():
    session = CaptureSession()
    repository = GraphRepository(session)

    result = await repository.search_vectors(
        [0.1, 0.2],
        limit=10,
        dataset_id="dataset-1",
        similarity_threshold=0.25,
    )

    assert result == []
    sql = str(session.statement)
    assert "c.dataset_id = :dataset_id" in sql
    assert "AND c.embedding IS NOT NULL" in sql
    assert "AND c.semantic_eligible IS TRUE" in sql
    assert session.parameters == {
        "embedding": "[0.1,0.2]",
        "dataset_id": "dataset-1",
        "limit": 10,
        "similarity_threshold": 0.25,
    }


@pytest.mark.asyncio
async def test_search_lexical_uses_active_release_full_text_index():
    session = CaptureSession()
    repository = GraphRepository(session)

    result = await repository.search_lexical(
        "Thông tư 01",
        limit=10,
        dataset_id="dataset-1",
    )

    assert result == []
    sql = str(session.statement)
    assert "c.search_vector @@ plainto_tsquery('simple', :query)" in sql
    assert "c.dataset_id = :dataset_id" in sql
    assert session.parameters == {
        "query": "Thông tư 01",
        "dataset_id": "dataset-1",
        "limit": 10,
    }
