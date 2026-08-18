"""OpenAI embedding runtime shared by the database pipeline and API."""

from __future__ import annotations

import os
from typing import Any

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_DIMENSIONS = 1536
PREPROCESSOR = "none"


def model_name() -> str:
    return os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def dimensions() -> int:
    return int(os.getenv("EMBEDDING_DIMENSIONS", str(DEFAULT_DIMENSIONS)))


def embed_query(text: str) -> list[float]:
    from openai import OpenAI

    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is required for text-embedding-3-small")
    response = OpenAI(api_key=key).embeddings.create(
        model=model_name(), input=text, dimensions=dimensions()
    )
    vector = response.data[0].embedding
    if len(vector) != dimensions():
        raise RuntimeError(f"embedding dimension mismatch: expected {dimensions()}, got {len(vector)}")
    return vector


def embed_batch(texts: list[str]) -> list[list[float]]:
    from openai import OpenAI

    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is required for text-embedding-3-small")
    response = OpenAI(api_key=key).embeddings.create(
        model=model_name(), input=texts, dimensions=dimensions()
    )
    ordered = sorted(response.data, key=lambda item: item.index)
    vectors = [item.embedding for item in ordered]
    if any(len(vector) != dimensions() for vector in vectors):
        raise RuntimeError("OpenAI returned an unexpected embedding dimension")
    return vectors
