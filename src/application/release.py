from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.domain.ports import ReleasePublisherPort


@dataclass(frozen=True)
class PublishCorpusRelease:
    """Enforce the three-projection invariant before publication."""

    publisher: ReleasePublisherPort

    @staticmethod
    def validate_contract(dataset_id: str, contract: dict[str, Any]) -> None:
        if not dataset_id.strip():
            raise ValueError("dataset_id must not be blank")
        fingerprint = str(contract.get("release_fingerprint") or "").strip()
        if not fingerprint:
            raise ValueError("release_fingerprint is required")
        projections = contract.get("projections")
        if not isinstance(projections, dict):
            raise ValueError("projections contract is required")
        required = {"postgres", "qdrant", "neo4j"}
        if set(projections) != required:
            raise ValueError("postgres, qdrant and neo4j projections are required")
        for name, projection in projections.items():
            if not isinstance(projection, dict) or projection.get("status") != "ready":
                raise ValueError(f"projection {name} is not ready")
            if projection.get("release_fingerprint") not in {None, "", fingerprint}:
                raise ValueError(f"projection {name} has a different fingerprint")
            expected = int(projection.get("expected_count", -1))
            actual = int(projection.get("actual_count", -2))
            if expected < 0 or actual != expected:
                raise ValueError(f"projection {name} count parity failed")

    async def execute(self, dataset_id: str, contract: dict[str, Any]) -> dict[str, Any]:
        self.validate_contract(dataset_id, contract)
        result = await self.publisher.publish(dataset_id)
        return dict(result)
