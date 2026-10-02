"""Provider adapters: one class per wire format, stdlib HTTP only.

    OpenAIChat         /chat/completions — OpenAI, Groq, Gemini (OpenAI-compatible
                       endpoint), Mistral, OpenRouter, Together, DeepSeek, ...
    AnthropicChat      /messages — Anthropic Claude
    OpenAIEmbeddings   /embeddings — OpenAI, Gemini, Mistral, Jina, Together, ...

Behaviour shared by all of them lives in _post(): retry on 429/5xx with the
server's retry-after, raise LLMQuotaError on a daily quota, and fit requests
under a per-minute token cap (a 413 "Limit N, Requested M" shrinks the answer
budget by the overshoot and retries).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import log
from .config import Provider
from .errors import LLMError, LLMQuotaError, LLMResponse

_log = log.get("llm")
_THINK = re.compile(r"<think>.*?(?:</think>|$)", re.S)
_OVER = re.compile(r"Limit (\d+), Requested (\d+)")
USER_AGENT = "asic-agent/1.0"   # some gateways (Cloudflare) reject urllib's default


class ChatClient:
    """Interface every chat adapter implements."""

    provider = "none"
    model = "none"

    def complete(self, system: str, user: str, max_tokens: int = 1500,
                 temperature: float = 0.0) -> LLMResponse:
        raise NotImplementedError

    @property
    def name(self) -> str:   # what run records show
        return self.provider


class EmbeddingClient:
    provider = "none"
    model = "none"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class _Http:
    def __init__(self, prov: Provider, model: str, timeout: int):
        self.prov, self.model, self.timeout = prov, model, timeout
        self.tpm: int | None = None    # per-minute token cap, learned from headers

    def _post(self, path: str, payload: dict, headers: dict, budget_key: str | None = None) -> dict:
        url = f"{self.prov.base_url}{path}"
        if self.prov.query:
            url += "?" + urllib.parse.urlencode(self.prov.query)
        for attempt in range(5):
            req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": USER_AGENT, **headers})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode())
                    try:
                        self.tpm = int(resp.headers.get("x-ratelimit-limit-tokens"))
                    except (TypeError, ValueError):
                        pass
                    return body
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:400]
                over = _OVER.search(detail)
                if e.code == 413 and over and budget_key and attempt < 4:
                    limit, asked = int(over.group(1)), int(over.group(2))
                    self.tpm = limit
                    smaller = payload[budget_key] - (asked - limit) - 200
                    if smaller < 1024:
                        raise LLMError(f"prompt too large for {self.model}'s per-minute limit "
                                       f"({limit} tokens)") from e
                    _log.info("%s: request over the per-minute cap, retrying with %d tokens",
                              self.model, smaller)
                    payload[budget_key] = smaller
                    continue
                if e.code == 429:
                    try:
                        ra = float(e.headers.get("retry-after"))
                    except (TypeError, ValueError):
                        ra = 0.0
                    if ra > 90 or "per day" in detail or "(TPD)" in detail or "quota" in detail.lower():
                        raise LLMQuotaError(f"quota exhausted at {self.prov.name} for {self.model}: "
                                            f"{detail[:200]}") from e
                if e.code in (429, 500, 502, 503, 529) and attempt < 4:
                    try:
                        wait = min(float(e.headers.get("retry-after")), 60.0)
                    except (TypeError, ValueError):
                        wait = 2.0 * 2 ** attempt
                    _log.info("%s: HTTP %s, retrying in %.0fs", self.model, e.code, wait)
                    time.sleep(wait)
                    continue
                raise LLMError(f"HTTP {e.code} from {self.prov.name}: {detail}") from e
            except urllib.error.URLError as e:
                raise LLMError(f"cannot reach {self.prov.name} ({self.prov.base_url}): {e.reason}") from e
        raise LLMError(f"{self.prov.name}: gave up after retries")

    def _fit(self, system: str, user: str, budget: int) -> int:
        if not self.tpm:
            return budget
        room = self.tpm - (len(system) + len(user)) // 3 - 200
        return max(1024, min(budget, room))


class OpenAIChat(_Http, ChatClient):
    def __init__(self, prov: Provider, model: str, min_tokens: int = 4096, timeout: int = 300):
        super().__init__(prov, model, timeout)
        self.provider, self.min_tokens = prov.name, min_tokens
        self._key = prov.api_key()

    def complete(self, system, user, max_tokens=1500, temperature=0.0) -> LLMResponse:
        # Reasoning models spend a small budget thinking and return nothing, so
        # callers' budgets are raised to a floor, then fitted under the cap.
        budget = self._fit(system, user, max(max_tokens, self.min_tokens))
        payload = {"model": self.model, "temperature": temperature, "max_tokens": budget,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                   **self.prov.params_for(self.model)}
        body = self._post("/chat/completions", payload,
                          self.prov.auth(), budget_key="max_tokens")
        try:
            choice = body["choices"][0]
            text = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected response shape from {self.provider}: {str(body)[:300]}") from e
        text = _THINK.sub("", text).strip()
        if not text and choice.get("finish_reason") == "length":
            raise LLMError(f"answer cut off: the model's reasoning used all {payload['max_tokens']} "
                           f"tokens (raise chat.min_tokens / LLM_MIN_TOKENS)")
        return LLMResponse(text, self.provider, self.model)


class AnthropicChat(_Http, ChatClient):
    VERSION = "2023-06-01"

    def __init__(self, prov: Provider, model: str, min_tokens: int = 4096, timeout: int = 300):
        super().__init__(prov, model, timeout)
        self.provider, self.min_tokens = prov.name, min_tokens
        self._key = prov.api_key()

    def complete(self, system, user, max_tokens=1500, temperature=0.0) -> LLMResponse:
        payload = {"model": self.model, "system": system, "temperature": temperature,
                   "max_tokens": self._fit(system, user, max(max_tokens, self.min_tokens)),
                   "messages": [{"role": "user", "content": user}],
                   **self.prov.params_for(self.model)}
        body = self._post("/messages", payload,
                          {"x-api-key": self._key, "anthropic-version": self.VERSION},
                          budget_key="max_tokens")
        try:
            text = "".join(b.get("text", "") for b in body["content"] if b.get("type") == "text")
        except (KeyError, TypeError) as e:
            raise LLMError(f"unexpected response shape from {self.provider}: {str(body)[:300]}") from e
        if not text.strip() and body.get("stop_reason") == "max_tokens":
            raise LLMError(f"answer cut off at {payload['max_tokens']} tokens")
        return LLMResponse(text.strip(), self.provider, self.model)


class OpenAIEmbeddings(_Http, EmbeddingClient):
    BATCH = 64

    def __init__(self, prov: Provider, model: str, timeout: int = 120):
        super().__init__(prov, model, timeout)
        self.provider = prov.name
        self._key = prov.api_key()

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            body = self._post("/embeddings", {"model": self.model, "input": texts[i:i + self.BATCH]},
                              self.prov.auth())
            try:
                rows = sorted(body["data"], key=lambda d: d.get("index", 0))
                out += [r["embedding"] for r in rows]
            except (KeyError, TypeError) as e:
                raise LLMError(f"unexpected embeddings response from {self.provider}: "
                               f"{str(body)[:300]}") from e
        return out


# wire format -> adapter. A new format is one class registered here.
CHAT_ADAPTERS = {"openai": OpenAIChat, "anthropic": AnthropicChat}
EMBEDDING_ADAPTERS = {"openai": OpenAIEmbeddings}
