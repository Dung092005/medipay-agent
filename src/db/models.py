from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class Dataset(Base):
    __tablename__ = "datasets"

    dataset_id: Mapped[str] = mapped_column(String, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String)
    manifest: Mapped[dict] = mapped_column(JSONB)
    collection_name: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatasetState(Base):
    __tablename__ = "dataset_state"

    singleton: Mapped[bool] = mapped_column(Boolean, primary_key=True, default=True)
    active_dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.dataset_id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReleaseProjection(Base):
    """Control-plane row describing one verified projection of a release."""

    __tablename__ = "release_projections"

    dataset_id: Mapped[str] = mapped_column(String, primary_key=True)
    projection_kind: Mapped[str] = mapped_column(String, primary_key=True)
    locator: Mapped[str] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String)
    release_fingerprint: Mapped[str] = mapped_column(String)
    expected_count: Mapped[int] = mapped_column(BigInteger)
    actual_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_sha256: Mapped[str] = mapped_column(String, default="")
    embedding_model: Mapped[str] = mapped_column(String, default="")
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Document(Base):
    __tablename__ = "documents"

    dataset_id: Mapped[str] = mapped_column(String, primary_key=True)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    is_external: Mapped[bool] = mapped_column(Boolean)
    content_text: Mapped[str] = mapped_column(Text)
    content_available: Mapped[bool] = mapped_column(Boolean)
    categories: Mapped[list[str]] = mapped_column(ARRAY(String))
    payload: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (ForeignKeyConstraint(["dataset_id"], ["datasets.dataset_id"]),)


class Chunk(Base):
    __tablename__ = "chunks"

    dataset_id: Mapped[str] = mapped_column(String, primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String, primary_key=True)
    id: Mapped[str] = mapped_column(String, unique=True)
    source_key: Mapped[str] = mapped_column(String)
    document_id: Mapped[str] = mapped_column(String)
    chunk_order: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    section_title: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(["dataset_id", "document_id"], ["documents.dataset_id", "documents.id"]),
    )


DocumentChunk = Chunk


class User(Base):
    __tablename__ = "users"

    uid: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, default="")
    display_name: Mapped[str] = mapped_column(String, default="")
    photo_url: Mapped[str] = mapped_column(String, default="")
    role: Mapped[str] = mapped_column(String, default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(Base):
    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_uid: Mapped[str] = mapped_column(ForeignKey("users.uid"))
    title: Mapped[str] = mapped_column(String(240), default="")
    active_dataset_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"

    turn_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.conversation_id"))
    owner_uid: Mapped[str] = mapped_column(ForeignKey("users.uid"))
    turn_index: Mapped[int] = mapped_column(Integer)
    user_message: Mapped[str] = mapped_column(Text)
    assistant_response: Mapped[str] = mapped_column(Text)
    dataset_id: Mapped[str | None] = mapped_column(String, nullable=True)
    citations: Mapped[list] = mapped_column(JSONB, default=list)
    claims: Mapped[list] = mapped_column(JSONB, default=list)
    request_id: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__ = [
    "Chunk", "Conversation", "ConversationTurn", "Dataset", "DatasetState", "ReleaseProjection",
    "Document", "DocumentChunk", "User",
]
