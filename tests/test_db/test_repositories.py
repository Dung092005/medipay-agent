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
async def test_search_title_documents_uses_active_release():
    session = CaptureSession()
    repository = GraphRepository(session)

    result = await repository.search_title_documents(
        "Luật Bảo hiểm y tế",
        limit=4,
        dataset_id="dataset-1",
    )

    assert result == []
    sql = str(session.statement)
    assert "d.dataset_id = :dataset_id" in sql
    assert "documents d" in sql


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
    assert "c.search_vector" in sql
    assert "c.dataset_id = :dataset_id" in sql
    assert session.parameters["query"] == "Thông tư 01"
    assert session.parameters["dataset_id"] == "dataset-1"
