from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text

from src.api.auth import get_current_user, require_admin
from src.db.session import session_scope
from src.models.schemas import (
    ConversationSummary,
    ConversationTurn,
    ReviewDecisionRequest,
    ReviewQueueItem,
)
from src.services.conversations import ConversationStoreError, get_conversation_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


class UserProfile(BaseModel):
    uid: str
    email: str = ""
    display_name: str = ""
    photo_url: str = ""
    role: str = "user"


class RegisterRequest(BaseModel):
    uid: str


@router.get(
    "/me",
    response_model=UserProfile,
    summary="Get current user profile",
    description="Returns the authenticated user's profile from the users table.",
)
async def get_me(user: dict[str, Any] = Depends(get_current_user)) -> UserProfile:
    uid = user.get("uid", "")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    async with session_scope() as session:
        result = await session.execute(
            text("SELECT uid, email, display_name, photo_url, role FROM users WHERE uid = :uid"),
            {"uid": uid},
        )
        row = result.mappings().first()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return UserProfile(
        uid=str(row["uid"]),
        email=str(row["email"] or ""),
        display_name=str(row["display_name"] or ""),
        photo_url=str(row["photo_url"] or ""),
        role=str(row["role"] or "user"),
    )


@router.post(
    "/register",
    response_model=UserProfile,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description="Creates a new user record from the Firebase token. Default role is 'user'.",
)
async def register(
    body: RegisterRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> UserProfile:
    uid = user.get("uid", "")
    email = user.get("email", "")
    display_name = user.get("name", user.get("firebase", {}).get("sign_in_provider", ""))
    photo_url = user.get("picture", "")

    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    async with session_scope() as session:
        existing = await session.execute(
            text("SELECT uid FROM users WHERE uid = :uid"), {"uid": uid}
        )
        if existing.scalar_one_or_none() is not None:
            result = await session.execute(
                text("SELECT uid, email, display_name, photo_url, role FROM users WHERE uid = :uid"),
                {"uid": uid},
            )
            row = result.mappings().first()
            return UserProfile(
                uid=str(row["uid"]),
                email=str(row["email"] or ""),
                display_name=str(row["display_name"] or ""),
                photo_url=str(row["photo_url"] or ""),
                role=str(row["role"] or "user"),
            )

        await session.execute(
            text(
                "INSERT INTO users (uid, email, display_name, photo_url, role) "
                "VALUES (:uid, :email, :display_name, :photo_url, 'user')"
            ),
            {"uid": uid, "email": email, "display_name": display_name, "photo_url": photo_url},
        )
        await session.commit()

    return UserProfile(uid=uid, email=email, display_name=display_name, photo_url=photo_url, role="user")


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    user: dict[str, Any] = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=50),
) -> list[ConversationSummary]:
    rows = await get_conversation_store().list_conversations(owner_uid=str(user.get("uid") or ""), limit=limit)
    return [ConversationSummary(**row) for row in rows]


@router.get("/conversations/{conversation_id}/turns", response_model=list[ConversationTurn])
async def conversation_turns(
    conversation_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=20),
) -> list[ConversationTurn]:
    try:
        rows = await get_conversation_store().recent_turns(
            owner_uid=str(user.get("uid") or ""), conversation_id=conversation_id, limit=limit
        )
    except ConversationStoreError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return [ConversationTurn(**row) for row in rows]


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> None:
    try:
        await get_conversation_store().delete(owner_uid=str(user.get("uid") or ""), conversation_id=conversation_id)
    except ConversationStoreError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


async def _review_item(session, review_id: str) -> ReviewQueueItem:
    result = await session.execute(
        text("SELECT * FROM review_queue_items WHERE review_id = :review_id"),
        {"review_id": review_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")
    audit_result = await session.execute(
        text(
            "SELECT event_id, action, actor_uid, created_at, note "
            "FROM review_audit_events WHERE review_id = :review_id ORDER BY created_at"
        ),
        {"review_id": review_id},
    )
    payload = dict(row)
    payload["audit"] = [dict(item) for item in audit_result.mappings().all()]
    return ReviewQueueItem(**payload)


@router.get("/admin/reviews", response_model=list[ReviewQueueItem], tags=["Admin"])
async def list_review_queue(
    user: dict[str, Any] = Depends(require_admin),
    review_status: str = Query(default="pending", alias="status", pattern="^(pending|accepted|rejected|all)$"),
    domain: str = Query(default="all", pattern="^(legal_document|hospital_fee_ocr|all)$"),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[ReviewQueueItem]:
    del user
    conditions: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if review_status != "all":
        conditions.append("status = :status")
        params["status"] = review_status
    if domain != "all":
        conditions.append("domain = :domain")
        params["domain"] = domain
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    async with session_scope() as session:
        result = await session.execute(
            text(f"SELECT * FROM review_queue_items{where} ORDER BY created_at DESC LIMIT :limit"),
            params,
        )
        rows = result.mappings().all()
    return [ReviewQueueItem(**dict(row)) for row in rows]


@router.get("/admin/reviews/{review_id}", response_model=ReviewQueueItem, tags=["Admin"])
async def get_review_queue_item(
    review_id: str,
    _user: dict[str, Any] = Depends(require_admin),
) -> ReviewQueueItem:
    async with session_scope() as session:
        return await _review_item(session, review_id)


@router.patch("/admin/reviews/{review_id}", response_model=ReviewQueueItem, tags=["Admin"])
async def decide_review_queue_item(
    review_id: str,
    body: ReviewDecisionRequest,
    user: dict[str, Any] = Depends(require_admin),
) -> ReviewQueueItem:
    async with session_scope() as session:
        result = await session.execute(
            text(
                "UPDATE review_queue_items SET status = :status, decision_note = :note, "
                "assigned_to = :actor, decided_at = now() "
                "WHERE review_id = :review_id "
                "RETURNING review_id"
            ),
            {"status": body.status, "note": body.note, "actor": str(user.get("uid") or ""), "review_id": review_id},
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")
        await session.execute(
            text(
                "INSERT INTO review_audit_events(event_id, review_id, action, actor_uid, note) "
                "VALUES (:event_id, :review_id, :action, :actor, :note)"
            ),
            {
                "event_id": str(uuid.uuid4()), "review_id": review_id,
                "action": body.status, "actor": str(user.get("uid") or ""), "note": body.note,
            },
        )
        await session.commit()
        return await _review_item(session, review_id)
