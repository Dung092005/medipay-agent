from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI

from src.config import get_settings

logger = logging.getLogger(__name__)


class LlmConfigurationError(RuntimeError):
    """The configured provider cannot serve chat requests."""


class ChatVertexGemini(BaseChatModel):
    """LangChain-compatible ChatModel wrapper for Google GenAI / Vertex AI SDK with robust auth."""

    project: str = "project-3b0c96e7-a43e-4f65-8bd"
    location: str = "global"
    model: str = "gemini-3.1-flash-lite"
    temperature: float = 0.2
    max_output_tokens: Optional[int] = None
    timeout: float = 45.0

    @property
    def _llm_type(self) -> str:
        return "google-vertex-gemini"

    def _get_client(self):
        from google import genai
        from google.oauth2 import credentials, service_account
        import json

        # 1. Try credentials JSON from GOOGLE_CREDENTIALS_JSON or FIREBASE_SERVICE_ACCOUNT_JSON
        creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON") or os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON") or ""
        if creds_json.strip():
            try:
                info = json.loads(creds_json)
                if info.get("type") == "authorized_user":
                    creds = credentials.Credentials.from_authorized_user_info(
                        info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
                    )
                    return genai.Client(vertexai=True, project=self.project, location=self.location, credentials=creds)
                elif info.get("type") == "service_account":
                    creds = service_account.Credentials.from_service_account_info(
                        info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
                    )
                    return genai.Client(vertexai=True, project=self.project, location=self.location, credentials=creds)
            except Exception as e:
                logger.warning("Failed to load GOOGLE_CREDENTIALS_JSON: %s", e)

        # 2. Try Gemini API key
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None
        if api_key:
            try:
                return genai.Client(api_key=api_key)
            except Exception as e:
                logger.warning("Failed to create Client with API key: %s", e)

        # 3. Default: Vertex AI with ADC
        return genai.Client(vertexai=True, project=self.project, location=self.location)

    def _get_fallback_llm(self) -> Optional[ChatOpenAI]:
        settings = get_settings()
        if not settings.openai_api_key:
            return None
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        fallback_model = "gpt-5" if settings.openai_base_url and "yescale" in settings.openai_base_url else "openai/gpt-4o-mini"
        kwargs: dict = {
            "model": fallback_model,
            "api_key": settings.openai_api_key,
            "temperature": self.temperature,
            "timeout": self.timeout,
            "max_retries": 2,
            "default_headers": headers,
        }
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        return ChatOpenAI(**kwargs)

    def _format_prompt(self, messages: List[Any]) -> str:
        prompt_parts = []
        for m in messages:
            if isinstance(m, tuple):
                role, content = m
                prompt_parts.append(f"{role.capitalize()}:\n{content}")
            elif isinstance(m, SystemMessage):
                prompt_parts.append(f"System:\n{m.content}")
            elif isinstance(m, HumanMessage):
                prompt_parts.append(f"Human:\n{m.content}")
            elif hasattr(m, "content"):
                prompt_parts.append(f"{m.content}")
            else:
                prompt_parts.append(str(m))
        return "\n\n".join(prompt_parts)

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            client = self._get_client()
            full_prompt = self._format_prompt(messages)
            config: dict[str, Any] = {"temperature": self.temperature}
            if self.max_output_tokens:
                config["max_output_tokens"] = self.max_output_tokens
            if stop:
                config["stop_sequences"] = stop

            res = client.models.generate_content(
                model=self.model,
                contents=full_prompt,
                config=config,
            )
            msg = AIMessage(content=res.text or "")
            return ChatResult(generations=[ChatGeneration(message=msg)])
        except Exception as exc:
            fallback = self._get_fallback_llm()
            if fallback is not None:
                logger.warning("Gemini generate failed (%s), falling back to OpenAI/OpenRouter", exc)
                return fallback._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            raise

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            client = self._get_client()
            full_prompt = self._format_prompt(messages)
            config: dict[str, Any] = {"temperature": self.temperature}
            if self.max_output_tokens:
                config["max_output_tokens"] = self.max_output_tokens
            if stop:
                config["stop_sequences"] = stop

            res = await client.aio.models.generate_content(
                model=self.model,
                contents=full_prompt,
                config=config,
            )
            msg = AIMessage(content=res.text or "")
            return ChatResult(generations=[ChatGeneration(message=msg)])
        except Exception as exc:
            fallback = self._get_fallback_llm()
            if fallback is not None:
                logger.warning("Gemini agenerate failed (%s), falling back to OpenAI/OpenRouter", exc)
                return await fallback._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            raise

    def with_structured_output(self, schema: Any, **kwargs: Any):
        """Return a runnable returning parsed Pydantic schema using Gemini native JSON schema with fallback."""

        async def _run_structured(messages: Any, **_kw: Any):
            try:
                client = self._get_client()
                full_prompt = self._format_prompt(messages)
                res = await client.aio.models.generate_content(
                    model=self.model,
                    contents=full_prompt,
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": schema,
                        "temperature": 0.0,
                    },
                )
                return schema.model_validate_json(res.text)
            except Exception as exc:
                fallback = self._get_fallback_llm()
                if fallback is not None:
                    logger.warning("Gemini structured output failed (%s), falling back to OpenAI", exc)
                    structured_fb = fallback.with_structured_output(schema, method="json_schema")
                    return await structured_fb.ainvoke(messages)
                raise

        return RunnableLambda(_run_structured)


@lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    settings = get_settings()
    provider = settings.llm_provider.casefold()

    if provider in {"google", "vertexai", "gemini"}:
        if not settings.model_name:
            raise LlmConfigurationError("Google/Vertex model name is not configured")
        return ChatVertexGemini(
            project=settings.google_project_id,
            location=settings.google_location,
            model=settings.model_name,
            temperature=settings.llm_temperature,
            max_output_tokens=settings.llm_max_output_tokens,
            timeout=settings.llm_timeout_seconds,
        )

    if provider not in {"openai", "openrouter"}:
        raise LlmConfigurationError(f"Unsupported LLM provider: {settings.llm_provider}")
    if not settings.openai_api_key or not settings.model_name:
        raise LlmConfigurationError("Chat provider is not configured")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    kwargs: dict = {
        "model": settings.model_name,
        "api_key": settings.openai_api_key,
        "temperature": settings.llm_temperature,
        "timeout": settings.llm_timeout_seconds,
        "max_tokens": settings.llm_max_output_tokens,
        "max_retries": 2,
        "default_headers": headers,
    }
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return ChatOpenAI(**kwargs)


@lru_cache(maxsize=1)
def get_rewrite_llm() -> BaseChatModel:
    """Return the low-latency model profile used only for retrieval rewriting."""
    settings = get_settings()
    provider = settings.llm_provider.casefold()

    if provider in {"google", "vertexai", "gemini"}:
        if not settings.model_name:
            raise LlmConfigurationError("Google/Vertex model name is not configured")
        return ChatVertexGemini(
            project=settings.google_project_id,
            location=settings.google_location,
            model=settings.model_name,
            temperature=0.0,
            max_output_tokens=settings.query_rewrite_max_tokens,
            timeout=min(settings.llm_timeout_seconds, settings.query_rewrite_timeout_seconds),
        )

    if provider not in {"openai", "openrouter"}:
        raise LlmConfigurationError(f"Unsupported query rewrite provider: {settings.llm_provider}")
    if not settings.openai_api_key or not settings.model_name:
        raise LlmConfigurationError("Query rewrite provider is not configured")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    kwargs: dict = {
        "model": settings.model_name,
        "api_key": settings.openai_api_key,
        "temperature": 0.0,
        "timeout": min(settings.llm_timeout_seconds, settings.query_rewrite_timeout_seconds),
        "max_tokens": settings.query_rewrite_max_tokens,
        "max_retries": 1,
        "default_headers": headers,
    }
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return ChatOpenAI(**kwargs)


def close_llm() -> None:
    """Drop the process-wide model wrapper during application shutdown/tests."""
    get_llm.cache_clear()
    get_rewrite_llm.cache_clear()
