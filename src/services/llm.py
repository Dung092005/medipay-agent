from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from src.config import get_settings


class LlmConfigurationError(RuntimeError):
    """The configured provider cannot serve chat requests."""


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    settings = get_settings()
    provider = settings.llm_provider.casefold()
    if provider not in {"openai", "openrouter"}:
        raise LlmConfigurationError("Only OpenAI or OpenRouter is supported for chat")
    if not settings.openai_api_key or not settings.model_name:
        raise LlmConfigurationError("Chat provider is not configured")
    kwargs: dict = {
        "model": settings.model_name,
        "api_key": settings.openai_api_key,
        "temperature": settings.llm_temperature,
        "timeout": settings.llm_timeout_seconds,
        "max_tokens": settings.llm_max_output_tokens,
        "max_retries": 2,
    }
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return ChatOpenAI(**kwargs)


@lru_cache(maxsize=1)
def get_rewrite_llm() -> ChatOpenAI:
    """Return the low-latency model profile used only for retrieval rewriting."""
    settings = get_settings()
    provider = settings.llm_provider.casefold()
    if provider not in {"openai", "openrouter"}:
        raise LlmConfigurationError("Only OpenAI or OpenRouter is supported for query rewriting")
    if not settings.openai_api_key or not settings.model_name:
        raise LlmConfigurationError("Query rewrite provider is not configured")
    kwargs: dict = {
        "model": settings.model_name,
        "api_key": settings.openai_api_key,
        "temperature": 0.0,
        "timeout": min(settings.llm_timeout_seconds, settings.query_rewrite_timeout_seconds),
        "max_tokens": settings.query_rewrite_max_tokens,
        "max_retries": 1,
    }
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return ChatOpenAI(**kwargs)


def close_llm() -> None:
    """Drop the process-wide model wrapper during application shutdown/tests."""
    get_llm.cache_clear()
    get_rewrite_llm.cache_clear()
