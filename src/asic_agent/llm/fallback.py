"""Primary model with a second hosted model behind it.

When the primary's quota is spent mid-run, later calls go to the fallback and
on_switch is told, so the switch is written into the run record instead of
happening silently. Both are API models; there is no local inference.
"""

from __future__ import annotations

from .errors import LLMQuotaError, LLMResponse
from .providers import ChatClient


class FallbackClient(ChatClient):
    def __init__(self, primary: ChatClient, fallback: ChatClient):
        self.primary, self.fallback, self.active = primary, fallback, primary
        self.on_switch = None

    @property
    def provider(self) -> str:      # type: ignore[override]
        return self.active.provider

    @property
    def model(self) -> str:         # type: ignore[override]
        return self.active.model

    def complete(self, system: str, user: str, max_tokens: int = 1500,
                 temperature: float = 0.0) -> LLMResponse:
        if self.active is self.primary:
            try:
                return self.primary.complete(system, user, max_tokens, temperature)
            except LLMQuotaError as e:
                self.active = self.fallback
                if self.on_switch:
                    self.on_switch(f"{self.primary.provider}/{self.primary.model}",
                                   f"{self.fallback.provider}/{self.fallback.model}", str(e))
        return self.fallback.complete(system, user, max_tokens, temperature)
