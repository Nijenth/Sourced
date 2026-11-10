"""Provider adapters: one module per provider, chosen through the registry.

Contracts live in base.py. App code should use the registry functions rather than the classes.
"""
from .anthropic_provider import ClaudeChat
from .gemini_provider import GeminiChat, GeminiEmbedder
from .base import ChatModel, Embedder, Provider, ProviderError, friendly_error
from .openai_provider import OpenAIChat, OpenAIEmbedder
from .registry import (
    PROVIDERS,
    chat_provider_names,
    embedding_provider_name,
    make_chat,
    make_embedder,
    offline_mode,
    setup_problems,
)

__all__ = [
    "ChatModel",
    "ClaudeChat",
    "Embedder",
    "GeminiChat",
    "GeminiEmbedder",
    "OpenAIChat",
    "OpenAIEmbedder",
    "PROVIDERS",
    "Provider",
    "ProviderError",
    "chat_provider_names",
    "embedding_provider_name",
    "friendly_error",
    "make_chat",
    "make_embedder",
    "offline_mode",
    "setup_problems",
]
