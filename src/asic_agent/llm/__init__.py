"""Provider-agnostic LLM access for the agents.

    from asic_agent.llm import get_chat_client, extract_json, LLMError

    client = get_chat_client()                 # role selection from config/env
    client = get_chat_client("groq", "openai/gpt-oss-20b")   # explicit
    text = client.complete(system, user).text

Agents depend only on ChatClient.complete(); which provider answers is
configuration (config/llm.toml, environment), never code.
"""

from __future__ import annotations

import os

from .config import LLMConfig, Provider, load as load_config
from .errors import LLMConfigError, LLMError, LLMQuotaError, LLMResponse
from .fallback import FallbackClient
from .jsonutil import extract_json
from .providers import CHAT_ADAPTERS, EMBEDDING_ADAPTERS, ChatClient, EmbeddingClient

__all__ = [
    "ChatClient", "EmbeddingClient", "FallbackClient", "LLMConfig", "LLMConfigError",
    "LLMError", "LLMQuotaError", "LLMResponse", "Provider", "extract_json",
    "get_chat_client", "get_embedding_client", "load_config",
]


def _chat(cfg: LLMConfig, provider: Provider, model: str) -> ChatClient:
    return CHAT_ADAPTERS[provider.kind](provider, model, cfg.min_tokens, cfg.timeout_s)


def get_chat_client(provider: str | None = None, model: str | None = None,
                    cfg: LLMConfig | None = None) -> ChatClient:
    """The chat client for the agents, wrapped with the configured fallback.

    Raises LLMConfigError when nothing usable is configured; the orchestrator
    then runs without agents (rule-based diagnosis, people decide)."""
    cfg = cfg or load_config()
    if provider:
        if provider not in cfg.providers:
            raise LLMConfigError(f"unknown provider '{provider}'")
        if not model:
            raise LLMConfigError(f"no model given for provider '{provider}'")
        primary = _chat(cfg, cfg.providers[provider], model)
    elif cfg.chat:
        primary = _chat(cfg, cfg.chat.provider, model or cfg.chat.model)
    else:
        raise LLMConfigError("no chat provider configured (config/llm.toml [chat] or $LLM_PROVIDER)")
    fb = None if os.environ.get("LLM_NO_FALLBACK") else cfg.fallback
    if fb and (fb.provider.name, fb.model) != (primary.provider, primary.model) and fb.provider.has_key():
        return FallbackClient(primary, _chat(cfg, fb.provider, fb.model))
    return primary


def get_embedding_client(cfg: LLMConfig | None = None) -> EmbeddingClient | None:
    """The embeddings client, or None when retrieval should be keyword-only."""
    cfg = cfg or load_config()
    sel = cfg.embeddings
    if sel is None:
        return None
    adapter = EMBEDDING_ADAPTERS.get(sel.provider.kind)
    if adapter is None:
        raise LLMConfigError(f"provider '{sel.provider.name}' ({sel.provider.kind}) has no embeddings API")
    return adapter(sel.provider, sel.model)
