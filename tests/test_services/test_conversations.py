from __future__ import annotations

import pytest

from src.services.conversations import ConversationStoreError, _json, _uuid


def test_conversation_ids_are_canonicalized() -> None:
    assert _uuid("550e8400-e29b-41d4-a716-446655440000", "conversation_id") == "550e8400-e29b-41d4-a716-446655440000"


def test_conversation_ids_reject_untrusted_arbitrary_strings() -> None:
    with pytest.raises(ConversationStoreError):
        _uuid("../../users", "conversation_id")


def test_conversation_payload_is_compact_and_json_safe() -> None:
    assert _json([{"text": "Mức hưởng", "value": 80}]) == '[{"text":"Mức hưởng","value":80}]'
