from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol


class AnswerAgentPort(Protocol):
    """Application-facing boundary for a verified answer graph."""

    async def answer(self, query: str) -> Mapping[str, Any]:
        ...

    def stream(self, query: str) -> AsyncIterator[Mapping[str, Any]]:
        ...


class ReleasePublisherPort(Protocol):
    """Boundary for atomic release publication, independent of database SDKs."""

    async def publish(self, dataset_id: str) -> Mapping[str, Any]:
        ...
