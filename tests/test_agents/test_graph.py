from unittest.mock import AsyncMock, patch

import pytest

from src.config import get_settings
from src.models.graph import RetrievalResult


@pytest.fixture(autouse=True)
def reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_agent_basic_flow():
    evidence = RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        title="Luật BHYT",
        content="Mức hưởng BHYT được quy định tại Điều 22.",
        channels=["semantic"],
    )
    with patch("src.agents.nodes.graphrag_nodes.get_runtime") as runtime_factory:
        runtime = runtime_factory.return_value
        runtime.retrieve = AsyncMock(return_value=([evidence], []))
        runtime.generate = AsyncMock(return_value="Câu trả lời grounded.")
        from src.agents.graph import get_agent

        result = await get_agent().ainvoke({"query": "Quyền lợi BHYT?"})

    assert result["response"] == "Câu trả lời grounded."
    assert result["citations"][0]["chunk_id"] == "chunk-1"


@pytest.mark.asyncio
async def test_agent_state_structure():
    evidence = RetrievalResult(chunk_id="chunk-1", document_id="doc-1", content="Evidence")
    with patch("src.agents.nodes.graphrag_nodes.get_runtime") as runtime_factory:
        runtime = runtime_factory.return_value
        runtime.retrieve = AsyncMock(return_value=([evidence], []))
        runtime.generate = AsyncMock(return_value="Answer")
        from src.agents.graph import get_agent

        result = await get_agent().ainvoke({"query": "Test query"})

    assert isinstance(result, dict)
    assert "query" in result
    assert "retrieved_evidence" in result


@pytest.mark.asyncio
async def test_context_can_exceed_public_citation_budget(monkeypatch):
    monkeypatch.setenv("MAX_LLM_EVIDENCE", "12")
    monkeypatch.setenv("MAX_CITATIONS", "8")
    get_settings.cache_clear()
    evidence = [
        RetrievalResult(
            chunk_id=f"chunk-{index}",
            document_id="doc-1",
            content=f"Evidence {index}",
            score=index / 10,
        )
        for index in range(12)
    ]
    with patch("src.agents.nodes.graphrag_nodes.get_runtime") as runtime_factory:
        runtime = runtime_factory.return_value
        runtime.retrieve = AsyncMock(return_value=(evidence, []))
        runtime.generate = AsyncMock(return_value="Answer")
        from src.agents.graph import get_agent

        result = await get_agent().ainvoke({"query": "Test query"})

    assert "EVIDENCE_ID=chunk-0" in runtime.generate.await_args.args[1]
    assert len(result["citations"]) == 8
    get_settings.cache_clear()
