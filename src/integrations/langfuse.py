from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

from src.config import get_settings

logger = logging.getLogger(__name__)

_configured = False


def tracing_enabled() -> bool:
    settings = get_settings()
    if settings.app_env == "test":
        return False
    return settings.langfuse_configured


def configure_langfuse() -> None:
    """Copy Settings into process env before the Langfuse client is created."""
    global _configured
    settings = get_settings()
    public_key = settings.langfuse_public_key.strip()
    secret_key = settings.langfuse_secret_key.strip()
    host = (settings.langfuse_base_url or settings.langfuse_host).strip()
    if not public_key or not secret_key or not host:
        return
    os.environ["LANGFUSE_PUBLIC_KEY"] = public_key
    os.environ["LANGFUSE_SECRET_KEY"] = secret_key
    os.environ["LANGFUSE_BASE_URL"] = host
    os.environ["LANGFUSE_HOST"] = host
    os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", settings.app_env)
    _configured = True


def get_callback_handler():
    if not tracing_enabled():
        return None
    configure_langfuse()
    from langfuse.langchain import CallbackHandler

    return CallbackHandler()


def llm_invoke_config() -> dict[str, Any]:
    handler = get_callback_handler()
    if handler is None:
        return {}
    return {"callbacks": [handler]}


@asynccontextmanager
async def trace_span(
    name: str,
    *,
    as_type: str = "span",
    input: Any = None,
    metadata: Mapping[str, Any] | None = None,
):
    if not tracing_enabled():
        yield None
        return
    configure_langfuse()
    from langfuse import get_client

    with get_client().start_as_current_observation(as_type=as_type, name=name) as observation:
        updates: dict[str, Any] = {}
        if input is not None:
            updates["input"] = input
        if metadata:
            updates["metadata"] = dict(metadata)
        if updates:
            observation.update(**updates)
        yield observation


def flush_langfuse() -> None:
    if not _configured and not tracing_enabled():
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception:
        logger.debug("Langfuse flush skipped", exc_info=True)
