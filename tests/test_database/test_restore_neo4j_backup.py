from __future__ import annotations

import pytest

from database.corpus.restore_neo4j_backup import _validate


def _payload(rel_type: str = "ALIAS_OF") -> dict:
    return {
        "nodes": [
            {"labels": ["Document"], "properties": {"graph_id": "d:a", "dataset_id": "d"}},
            {"labels": ["Document"], "properties": {"graph_id": "d:b", "dataset_id": "d"}},
        ],
        "relationships": [{
            "source_graph_id": "d:a", "target_graph_id": "d:b", "type": rel_type, "properties": {},
        }],
    }


def test_restore_backup_validates_expected_dataset_and_relationship() -> None:
    nodes, relationships = _validate(_payload(), "d")
    assert len(nodes) == 2
    assert len(relationships) == 1


def test_restore_backup_rejects_unsafe_relationship_type() -> None:
    with pytest.raises(ValueError, match="unsafe relationship"):
        _validate(_payload("BAD`TYPE"), "d")
