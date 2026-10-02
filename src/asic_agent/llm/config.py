"""Provider configuration: config/llm.toml plus environment overrides.

config/llm.toml names the providers (endpoint, wire format, which environment
variable holds the key) and which provider/model each role uses. It holds no
secrets. The environment can override the role selection:

    LLM_PROVIDER, LLM_MODEL                     chat (all agents)
    LLM_FALLBACK_PROVIDER, LLM_FALLBACK_MODEL   optional, after a spent quota
    EMBEDDING_PROVIDER, EMBEDDING_MODEL         knowledge-base vectors
                                                (EMBEDDING_PROVIDER=none: keyword only)

Adding a provider is a [providers.<name>] table; a new wire format is one
adapter class in providers.py registered in ADAPTERS.
"""

from __future__ import annotations

import os
try:
    import tomllib                     # Python 3.11+
except ModuleNotFoundError:            # 3.10 (e.g. the OpenROAD container image)
    import tomli as tomllib            # type: ignore[no-redef]
from dataclasses import dataclass, field
from pathlib import Path

from .. import settings
from .errors import LLMConfigError


@dataclass(frozen=True)
class Provider:
    name: str
    kind: str                 # wire format: "openai" | "anthropic"
    base_url: str
    api_key_env: str
    models: tuple[str, ...] = ()   # offered for choice in the app (optional)
    # Extra request fields per model, e.g. {"openai/gpt-oss-20b": {"reasoning_effort": "low"}}.
    # Provider-specific knobs live here as configuration, not in code.
    model_params: dict = field(default_factory=dict, hash=False, compare=False)
    # How the key is sent (OpenAI wire format). Defaults suit most providers;
    # Azure OpenAI uses auth_header = "api-key", auth_prefix = "" and
    # query = {"api-version": "..."}.
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    query: dict = field(default_factory=dict, hash=False, compare=False)

    def auth(self) -> dict:
        return {self.auth_header: f"{self.auth_prefix}{self.api_key()}"}

    def params_for(self, model: str) -> dict:
        return dict(self.model_params.get(model, {}))

    def api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise LLMConfigError(
                f"provider '{self.name}' needs an API key in ${self.api_key_env} "
                f"(environment, ~/.config/asic-agent/secrets.env, or .env)")
        return key

    def has_key(self) -> bool:
        return bool(os.environ.get(self.api_key_env, "").strip())


@dataclass(frozen=True)
class Selection:
    provider: Provider
    model: str


@dataclass
class LLMConfig:
    providers: dict[str, Provider]
    chat: Selection | None
    fallback: Selection | None
    embeddings: Selection | None
    min_tokens: int = 4096
    timeout_s: int = 300
    extra: dict = field(default_factory=dict)

    def chat_choices(self) -> list[dict]:
        """Provider/model pairs a person may pick per run (keys present only)."""
        out = []
        for p in self.providers.values():
            if p.has_key():
                out += [{"provider": p.name, "model": m} for m in p.models]
        return out


def _select(providers: dict[str, Provider], section: dict, env_p: str, env_m: str,
            role: str, allow_none: bool = False) -> Selection | None:
    pname = os.environ.get(env_p, section.get("provider", "")).strip()
    model = os.environ.get(env_m, section.get("model", "")).strip()
    if allow_none and pname.lower() in ("", "none"):
        return None
    if not pname:
        raise LLMConfigError(f"no provider configured for {role} (config/llm.toml or ${env_p})")
    if pname not in providers:
        raise LLMConfigError(f"{role}: unknown provider '{pname}'. Known: {', '.join(sorted(providers))}")
    if not model:
        raise LLMConfigError(f"{role}: no model set for provider '{pname}' (or ${env_m})")
    return Selection(providers[pname], model)


def load(path: Path | None = None) -> LLMConfig:
    settings.load_env()
    path = path or settings.LLM_CONFIG
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError as e:
        raise LLMConfigError(f"{path} not found") from e
    except tomllib.TOMLDecodeError as e:
        raise LLMConfigError(f"{path}: {e}") from e

    providers = {}
    for name, p in (raw.get("providers") or {}).items():
        kind = p.get("kind", "openai")
        if kind not in ("openai", "anthropic"):
            raise LLMConfigError(f"provider '{name}': unknown kind '{kind}'")
        if not p.get("base_url") or not p.get("api_key_env"):
            raise LLMConfigError(f"provider '{name}': base_url and api_key_env are required")
        providers[name] = Provider(name, kind, p["base_url"].rstrip("/"), p["api_key_env"],
                                   tuple(p.get("models", ())), dict(p.get("model_params", {})),
                                   p.get("auth_header", "Authorization"),
                                   p.get("auth_prefix", "Bearer "), dict(p.get("query", {})))

    chat_s = raw.get("chat") or {}
    fb = chat_s.get("fallback") or {}
    chat = _select(providers, chat_s, "LLM_PROVIDER", "LLM_MODEL", "chat", allow_none=True)
    if (chat is None or not chat.provider.has_key()) and not os.environ.get("LLM_PROVIDER"):
        # The configured provider has no key: use the first provider (in file
        # order) that has a key and a recommended model, so adding any one key
        # is enough to run. Explicit choices ($LLM_PROVIDER, `models use`) win.
        auto = next((Selection(p, p.models[0]) for p in providers.values()
                     if p.has_key() and p.models), None)
        if auto:
            chat = auto
    return LLMConfig(
        providers=providers,
        chat=chat,
        fallback=_select(providers, fb, "LLM_FALLBACK_PROVIDER", "LLM_FALLBACK_MODEL",
                         "chat fallback", allow_none=True),
        embeddings=_select(providers, raw.get("embeddings") or {}, "EMBEDDING_PROVIDER",
                           "EMBEDDING_MODEL", "embeddings", allow_none=True),
        min_tokens=int(os.environ.get("LLM_MIN_TOKENS", chat_s.get("min_tokens", 4096))),
        timeout_s=int(os.environ.get("LLM_TIMEOUT", chat_s.get("timeout_s", 300))),
    )
