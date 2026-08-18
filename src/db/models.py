from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.config import get_settings
from src.db.base import Base

settings = get_settings()
_embedding_type = Vector(settings.embedding_dimensions)


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
    embedding = mapped_column(_embedding_type, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(["dataset_id", "document_id"], ["documents.dataset_id", "documents.id"]),
    )


DocumentChunk = Chunk

__all__ = ["Chunk", "Dataset", "DatasetState", "Document", "DocumentChunk"]
