from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from src.domain.ports import AnswerAgentPort


@dataclass(frozen=True)
class AnswerLegalQuestion:
    """Orchestrate one bounded legal-answer request through an injected port."""

    agent: AnswerAgentPort

    async def execute(self, query: str) -> dict[str, Any]:
        normalized = query.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        result = await self.agent.answer(normalized)
        return dict(result)


@dataclass(frozen=True)
class StreamLegalQuestion:
    """Expose the same normalized answer boundary for verified SSE runs."""

    agent: AnswerAgentPort

    def execute(self, query: str) -> AsyncIterator[Mapping[str, Any]]:
        normalized = query.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return self.agent.stream(normalized)
