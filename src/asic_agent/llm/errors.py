"""Errors and the response type shared by every provider."""

from __future__ import annotations

from dataclasses import dataclass


class LLMError(RuntimeError):
    """A call produced nothing usable (bad request, unreachable, unparsable).
    Agents treat it as "this candidate failed" and may spend an attempt."""


class LLMConfigError(LLMError):
    """The configuration is incomplete or wrong (unknown provider, missing key).
    Raised at start-up, before any work is done."""


class LLMQuotaError(RuntimeError):
    """The provider's quota is used up for longer than is worth waiting for
    (e.g. a tokens-per-day limit). Retrying the same call is pointless.

    Deliberately NOT an LLMError: agents treat LLMError as a failed candidate
    and spend an attempt on it. A spent quota is not a bad answer, so it passes
    through every agent; the orchestrator stops the run (`quota_stopped`, no
    attempt spent) or FallbackClient switches to the configured fallback."""


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
