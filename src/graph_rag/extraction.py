from __future__ import annotations

from typing import Protocol

from src.models.graph import Entity, Relation


class GraphExtractor(Protocol):
    async def extract(self, text: str) -> tuple[list[Entity], list[Relation]]:
        """Extract graph records from source text."""


class UnconfiguredGraphExtractor:
    async def extract(self, text: str) -> tuple[list[Entity], list[Relation]]:
        raise RuntimeError(
            "Graph extractor is not configured. Select a local LLM runtime before ingestion."
        )
