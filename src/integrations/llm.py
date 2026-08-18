from __future__ import annotations

from typing import Protocol

from src.config import get_settings


class ChatModel(Protocol):
    async def generate(self, prompt: str) -> str:
        """Generate text from prompt."""


class UnconfiguredChatModel:
    async def generate(self, prompt: str) -> str:
        raise RuntimeError(
            "LLM provider is not configured. Set LLM_PROVIDER and MODEL_NAME "
            "after selecting a local model runtime."
        )


def get_chat_model() -> ChatModel:
    settings = get_settings()
    if not settings.llm_configured:
        return UnconfiguredChatModel()
    raise NotImplementedError(
        "Selected LLM adapter is not implemented yet; configure local runtime first."
    )
