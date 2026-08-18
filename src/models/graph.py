from __future__ import annotations

from pydantic import BaseModel, Field


class Entity(BaseModel):
    name: str
    entity_type: str = "unknown"
    description: str = ""


class Relation(BaseModel):
    source: str
    target: str
    relation_type: str
    description: str = ""
    source_id: str = ""
    target_id: str = ""


class RetrievalResult(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    source: str = ""
    title: str = ""
    section_title: str = ""
    score: float = 0.0
    entities: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    document_id: str
    chunk_id: str
    title: str = ""
    section_title: str = ""
    quote: str = ""
    channels: list[str] = Field(default_factory=list)
