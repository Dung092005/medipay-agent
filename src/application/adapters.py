from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any


class LangGraphAgentAdapter:
    """Translate the LangGraph SDK object into the application port."""

    def __init__(self, provider: Callable[[], Any]) -> None:
        self._provider = provider

    async def answer(self, query: str) -> Mapping[str, Any]:
        result = await self._provider().ainvoke({"query": query})
        return result if isinstance(result, Mapping) else {}

    def stream(self, query: str) -> AsyncIterator[Mapping[str, Any]]:
        return self._provider().astream_events({"query": query}, version="v2")
