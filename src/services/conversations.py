"""Owner-scoped conversation persistence with bounded retention.

Conversation rows are an audit/memory aid only.  They never replace release-
scoped retrieval, and every query uses the verified Firebase UID rather than a
client-provided owner field.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from src.db.session import session_scope

logger = logging.getLogger(__name__)
MAX_TURNS_PER_CONVERSATION = 100
MAX_HISTORY_TURNS = 20


class ConversationStoreError(ValueError):
    """A caller supplied an invalid or unauthorized conversation identity."""


def _uuid(value: str, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ConversationStoreError(f"{field} must be a UUID") from exc


def _json(value: object) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False, separators=(",", ":"))


class ConversationStore:
    """Small SQL boundary; no ORM relationship graph is loaded on request."""

    async def append_turn(
        self,
        *,
        owner_uid: str,
        conversation_id: str,
        turn_id: str,
        user_message: str,
        assistant_response: str,
        dataset_id: str = "",
        citations: Sequence[dict] = (),
        claims: Sequence[dict] = (),
        anchors: Sequence[dict] = (),
        request_id: str = "",
    ) -> bool:
        if not owner_uid or not conversation_id or not turn_id:
            return False
        conversation_key = _uuid(conversation_id, "conversation_id")
        turn_key = _uuid(turn_id, "turn_id")
        if not user_message.strip() or len(user_message) > 5000:
            raise ConversationStoreError("user_message exceeds the conversation budget")

        try:
            async with session_scope() as session:
                # Chat may arrive immediately after Firebase sign-in, before the
                # profile bootstrap request finishes.  Upsert profile metadata,
                # but never update the server-assigned role from token data.
                await session.execute(
                    text(
                        """
                        INSERT INTO users(uid, email, display_name, photo_url, role)
                        VALUES (:uid, '', '', '', 'user')
                        ON CONFLICT (uid) DO NOTHING
                        """
                    ),
                    {"uid": owner_uid},
                )
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                    {"lock_key": f"conversation:{conversation_key}"},
                )
                existing = await session.execute(
                    text("SELECT owner_uid FROM conversations WHERE conversation_id = :conversation_id"),
                    {"conversation_id": conversation_key},
                )
                owner = existing.scalar_one_or_none()
                if owner is None:
                    await session.execute(
                        text(
                            """
                            INSERT INTO conversations(conversation_id, owner_uid, title, active_dataset_id)
                            VALUES (:conversation_id, :owner_uid, :title, NULLIF(:dataset_id, ''))
                            """
                        ),
                        {
                            "conversation_id": conversation_key,
                            "owner_uid": owner_uid,
                            "title": user_message.strip()[:240],
                            "dataset_id": dataset_id,
                        },
                    )
                elif str(owner) != owner_uid:
                    raise ConversationStoreError("conversation does not belong to the authenticated user")

                duplicate = await session.execute(
                    text("SELECT 1 FROM conversation_turns WHERE turn_id = :turn_id"),
                    {"turn_id": turn_key},
                )
                if duplicate.scalar_one_or_none() is not None:
                    await session.rollback()
                    return True

                next_index = await session.execute(
                    text(
                        "SELECT COALESCE(MAX(turn_index), 0) + 1 FROM conversation_turns "
                        "WHERE conversation_id = :conversation_id"
                    ),
                    {"conversation_id": conversation_key},
                )
                turn_index = int(next_index.scalar_one())
                await session.execute(
                    text(
                        """
                        INSERT INTO conversation_turns(
                            turn_id, conversation_id, owner_uid, turn_index,
                            user_message, assistant_response, dataset_id,
                            citations, claims, anchors, request_id
                        ) VALUES (
                            :turn_id, :conversation_id, :owner_uid, :turn_index,
                            :user_message, :assistant_response, NULLIF(:dataset_id, ''),
                            CAST(:citations AS jsonb), CAST(:claims AS jsonb),
                            CAST(:anchors AS jsonb), :request_id
                        )
                        """
                    ),
                    {
                        "turn_id": turn_key,
                        "conversation_id": conversation_key,
                        "owner_uid": owner_uid,
                        "turn_index": turn_index,
                        "user_message": user_message.strip(),
                        "assistant_response": assistant_response.strip()[:20000],
                        "dataset_id": dataset_id,
                        "citations": _json(citations),
                        "claims": _json(claims),
                        "anchors": _json(anchors),
                        "request_id": request_id[:128],
                    },
                )
                await session.execute(
                    text(
                        "DELETE FROM conversation_turns WHERE conversation_id = :conversation_id "
                        "AND turn_index <= :cutoff"
                    ),
                    {
                        "conversation_id": conversation_key,
                        "cutoff": max(0, turn_index - MAX_TURNS_PER_CONVERSATION),
                    },
                )
                await session.execute(
                    text(
                        "UPDATE conversations SET active_dataset_id = NULLIF(:dataset_id, '') "
                        "WHERE conversation_id = :conversation_id"
                    ),
                    {"dataset_id": dataset_id, "conversation_id": conversation_key},
                )
                await session.commit()
                return True
        except ProgrammingError as exc:
            # A deploy that has not run the protected conversation migration must
            # still answer safely; never make memory persistence a correctness
            # dependency for retrieval.
            if "conversation" in str(exc).casefold() or "users" in str(exc).casefold():
                logger.warning("Conversation store is not migrated; answer was not persisted")
                return False
            raise

    async def recent_turns(self, *, owner_uid: str, conversation_id: str, limit: int = MAX_HISTORY_TURNS) -> list[dict]:
        conversation_key = _uuid(conversation_id, "conversation_id")
        bounded_limit = max(1, min(int(limit), MAX_HISTORY_TURNS))
        async with session_scope() as session:
            result = await session.execute(
                text(
                    """
                    SELECT turn_id, user_message, assistant_response, dataset_id, citations, claims,
                           anchors, created_at
                    FROM conversation_turns
                    WHERE owner_uid = :owner_uid AND conversation_id = :conversation_id
                    ORDER BY turn_index DESC
                    LIMIT :limit
                    """
                ),
                {"owner_uid": owner_uid, "conversation_id": conversation_key, "limit": bounded_limit},
            )
            rows = list(result.mappings())
        rows.reverse()
        return [dict(row) for row in rows]

    async def list_conversations(self, *, owner_uid: str, limit: int = 50) -> list[dict]:
        bounded_limit = max(1, min(int(limit), 50))
        async with session_scope() as session:
            result = await session.execute(
                text(
                    """
                    SELECT conversation_id, title, COALESCE(active_dataset_id, '') AS active_dataset_id, updated_at
                    FROM conversations
                    WHERE owner_uid = :owner_uid AND deleted_at IS NULL
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"owner_uid": owner_uid, "limit": bounded_limit},
            )
            return [dict(row) for row in result.mappings()]

    async def delete(self, *, owner_uid: str, conversation_id: str) -> bool:
        conversation_key = _uuid(conversation_id, "conversation_id")
        async with session_scope() as session:
            result = await session.execute(
                text(
                    "UPDATE conversations SET deleted_at = now() "
                    "WHERE conversation_id = :conversation_id AND owner_uid = :owner_uid AND deleted_at IS NULL"
                ),
                {"conversation_id": conversation_key, "owner_uid": owner_uid},
            )
            await session.commit()
        return bool(result.rowcount)


_store = ConversationStore()


def get_conversation_store() -> ConversationStore:
    return _store
