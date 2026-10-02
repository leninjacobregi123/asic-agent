"""Model management: what the CLI (`python -m asic_agent models ...`) and the
web app's Models page use to set up and check providers.

    status()                   every provider: key present, roles, offered models
    available_models(p)        the models a provider's API lists for this key
    test(p, model)             one tiny request: ok / auth / quota / not found / network
    save_key(p, key)           store the key in ~/.config/asic-agent/secrets.env (0600)
    set_role(role, p, model)   choose the model for chat / fallback / embeddings

Keys are written only to the per-user secrets file, never to the repository,
and are never returned or logged. config/llm.toml is edited in place (comments
kept) and re-validated; a change that does not load is rolled back.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import settings
from .config import LLMConfig, Provider, load
from .errors import LLMConfigError, LLMError, LLMQuotaError
from .providers import CHAT_ADAPTERS, EMBEDDING_ADAPTERS, USER_AGENT

ROLES = {"chat": "chat", "fallback": "chat.fallback", "embeddings": "embeddings"}


def status(cfg: LLMConfig | None = None) -> list[dict]:
    cfg = cfg or load()
    roles = {"chat": cfg.chat, "fallback": cfg.fallback, "embeddings": cfg.embeddings}
    out = []
    for p in cfg.providers.values():
        out.append({
            "provider": p.name, "kind": p.kind, "base_url": p.base_url,
            "key_env": p.api_key_env, "has_key": p.has_key(), "models": list(p.models),
            "roles": {r: sel.model for r, sel in roles.items() if sel and sel.provider.name == p.name},
        })
    return out


def _provider(name: str, cfg: LLMConfig | None = None) -> Provider:
    cfg = cfg or load()
    if name not in cfg.providers:
        raise LLMConfigError(f"unknown provider '{name}'. Known: {', '.join(sorted(cfg.providers))}")
    return cfg.providers[name]


def available_models(name: str) -> list[str]:
    """Model ids the provider lists for this key (GET /models)."""
    p = _provider(name)
    headers = {"User-Agent": USER_AGENT}
    if p.kind == "anthropic":
        headers |= {"x-api-key": p.api_key(), "anthropic-version": "2023-06-01"}
    else:
        headers |= p.auth()
    url = f"{p.base_url}/models" + ("?" + urllib.parse.urlencode(p.query) if p.query else "")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise LLMError(f"{name}: HTTP {e.code} listing models: {e.read().decode('utf-8', 'replace')[:200]}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"cannot reach {name} ({p.base_url}): {e.reason}") from e
    return sorted(m.get("id", "") for m in body.get("data", []) if m.get("id"))


def test(name: str, model: str, embeddings: bool = False) -> dict:
    """One small request. Never raises: the result says what went wrong."""
    t0 = time.time()
    try:
        cfg = load()
        p = _provider(name, cfg)
        if embeddings:
            adapter = EMBEDDING_ADAPTERS.get(p.kind)
            if adapter is None:
                return {"ok": False, "error": "config", "detail": f"{name} has no embeddings API"}
            vec = adapter(p, model).embed(["connection test"])[0]
            detail = f"{len(vec)}-dimensional vectors"
        else:
            r = CHAT_ADAPTERS[p.kind](p, model, min_tokens=256, timeout=60).complete(
                "Reply with the single word OK.", "Connection test.", max_tokens=256)
            detail = r.text[:40]
        return {"ok": True, "latency_ms": round((time.time() - t0) * 1000), "detail": detail}
    except LLMQuotaError as e:
        return {"ok": False, "error": "quota", "detail": str(e)[:200]}
    except LLMConfigError as e:
        return {"ok": False, "error": "config", "detail": str(e)[:200]}
    except LLMError as e:
        msg = str(e)
        kind = ("auth" if re.search(r"HTTP 40[13]", msg) else
                "not_found" if "HTTP 404" in msg or "model_not_found" in msg or "does not exist" in msg else
                "network" if "cannot reach" in msg else "error")
        return {"ok": False, "error": kind, "detail": msg[:200]}


def save_key(name: str, key: str) -> str:
    """Store a provider's key in the per-user secrets file. Returns its path."""
    p = _provider(name)
    key = key.strip()
    if not key or any(c in key for c in "\n\r ="):
        raise LLMConfigError("that does not look like an API key")
    f = settings.USER_ENV_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(f.parent, 0o700)
    lines = f.read_text().splitlines() if f.exists() else []
    lines = [l for l in lines if not re.match(rf"^\s*(export\s+)?{re.escape(p.api_key_env)}\s*=", l)]
    lines.append(f"{p.api_key_env}={key}")
    tmp = f.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    tmp.replace(f)
    os.environ[p.api_key_env] = key          # usable in this process right away
    return str(f)


def set_role(role: str, provider: str, model: str) -> None:
    """Point a role at provider/model in config/llm.toml ("none" disables
    fallback or embeddings). Comments and other settings are kept."""
    if role not in ROLES:
        raise LLMConfigError(f"role must be one of {', '.join(ROLES)}")
    if provider != "none":
        _provider(provider)
    if not re.fullmatch(r"[A-Za-z0-9._:/@+-]*", model) or not re.fullmatch(r"[A-Za-z0-9._-]+", provider):
        raise LLMConfigError("provider/model names may contain letters, digits and . _ : / @ + - only")
    path = settings.LLM_CONFIG
    text = path.read_text()
    header = f"[{ROLES[role]}]"
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == header)
    except StopIteration:
        lines += ["", header, f'provider = "{provider}"', f'model = "{model}"']
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        found = set()
        for i in range(start + 1, end):
            for key, val in (("provider", provider), ("model", model)):
                if re.match(rf"^\s*{key}\s*=", lines[i]):
                    comment = re.search(r"\s+#.*$", lines[i])
                    lines[i] = f'{key} = "{val}"' + (comment.group(0) if comment and key == "provider" else "")
                    found.add(key)
        for key, val in (("model", model), ("provider", provider)):
            if key not in found:
                lines.insert(start + 1, f'{key} = "{val}"')
    new = "\n".join(lines) + "\n"
    path.write_text(new)
    try:
        load()
    except LLMConfigError:
        path.write_text(text)                # roll back a change that does not load
        raise
