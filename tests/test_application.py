from __future__ import annotations

import pytest

from src.application.answer import AnswerLegalQuestion, StreamLegalQuestion
from src.application.release import PublishCorpusRelease


class StubAgent:
    def __init__(self):
        self.stream_query = None
        self.stream_result = object()

    async def answer(self, query: str):
        return {"response": query, "citations": []}

    def stream(self, query: str):
        self.stream_query = query
        return self.stream_result


class StubPublisher:
    async def publish(self, dataset_id: str):
        return {"dataset_id": dataset_id, "status": "published"}


@pytest.mark.asyncio
async def test_answer_use_case_normalizes_query_and_keeps_port_boundary():
    result = await AnswerLegalQuestion(StubAgent()).execute("  Quyền lợi BHYT?  ")
    assert result["response"] == "Quyền lợi BHYT?"


@pytest.mark.asyncio
async def test_answer_use_case_rejects_blank_query():
    with pytest.raises(ValueError):
        await AnswerLegalQuestion(StubAgent()).execute("  ")


def test_stream_use_case_normalizes_and_delegates():
    agent = StubAgent()
    stream = StreamLegalQuestion(agent).execute("  câu hỏi  ")
    assert agent.stream_query == "câu hỏi"
    assert stream is agent.stream_result


@pytest.mark.asyncio
async def test_publish_release_requires_three_projection_parity():
    contract = {
        "release_fingerprint": "sha256:release",
        "projections": {
            name: {"status": "ready", "expected_count": 1, "actual_count": 1}
            for name in ("postgres", "qdrant", "neo4j")
        },
    }
    result = await PublishCorpusRelease(StubPublisher()).execute("release-1", contract)
    assert result["status"] == "published"


@pytest.mark.asyncio
async def test_publish_release_rejects_mixed_projection_counts():
    contract = {
        "release_fingerprint": "sha256:release",
        "projections": {
            "postgres": {"status": "ready", "expected_count": 1, "actual_count": 1},
            "qdrant": {"status": "ready", "expected_count": 1, "actual_count": 0},
            "neo4j": {"status": "ready", "expected_count": 1, "actual_count": 1},
        },
    }
    with pytest.raises(ValueError, match="count parity"):
        await PublishCorpusRelease(StubPublisher()).execute("release-1", contract)
