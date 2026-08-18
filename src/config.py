from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "MediPay Agent"
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3000"

    llm_provider: str = "openai"
    model_name: str = ""
    openai_api_key: str = ""
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_timeout_seconds: float = Field(default=45.0, gt=0)
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=1)
    embedding_api_key: str = ""

    database_url: str = ""
    supabase_url: str = ""
    supabase_anon_key: str = ""
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout: int = Field(default=30, ge=1)
    db_pool_recycle: int = Field(default=1800, ge=60)

    neo4j_uri: str = ""
    neo4j_username: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    retrieval_top_k: int = Field(default=5, ge=1, le=50)
    semantic_similarity_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    graph_hops: int = Field(default=1, ge=0, le=5)
    graph_neighbor_limit: int = Field(default=20, ge=1, le=100)
    graph_evidence_limit: int = Field(default=10, ge=1, le=100)
    max_llm_evidence: int = Field(default=20, ge=1, le=100)
    max_citations: int = Field(default=8, ge=1, le=50)
    max_chunks_per_document: int = Field(default=2, ge=1, le=20)
    max_context_chars: int = Field(default=60_000, ge=1_000, le=200_000)
    chunk_size: int = Field(default=800, ge=100)
    chunk_overlap: int = Field(default=120, ge=0)

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""
    langfuse_base_url: str = ""

    @property
    def embeddings_configured(self) -> bool:
        return bool(self.embedding_provider and self.embedding_model and self.openai_api_key)

    @property
    def llm_configured(self) -> bool:
        return self.llm_provider.casefold() == "openai" and bool(
            self.model_name and self.openai_api_key
        )

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url)

    @property
    def langfuse_configured(self) -> bool:
        return bool(
            self.langfuse_public_key
            and self.langfuse_secret_key
            and (self.langfuse_base_url or self.langfuse_host)
        )

    def validate_chunk_settings(self) -> None:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")


@lru_cache
def get_settings() -> Settings:
    return Settings()
