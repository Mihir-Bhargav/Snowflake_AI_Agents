"""ModelProvider: the thin seam between agents and the LLM.

The provider is selected by config (``model.provider``) so the Anthropic / Bedrock /
Vertex decision can be deferred to security review without touching agent code
(docs/DEPLOYMENT.md §4). Only the StubProvider is implemented in Phase 1; the others
raise until wired, but the interface they must satisfy is fixed here.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..config import ModelConfig


@dataclass
class ModelResponse:
    text: str
    # Tool-use, token usage, stop reason, etc. get added when a real provider lands.
    raw: dict | None = None


class ModelProvider(ABC):
    """Every provider implementation must satisfy this interface."""

    def __init__(self, config: ModelConfig):
        self.config = config

    @abstractmethod
    def complete(self, system: str, messages: list[dict]) -> ModelResponse:
        """Run one completion. ``messages`` is a list of {role, content} dicts."""
        raise NotImplementedError


class StubProvider(ModelProvider):
    """Deterministic, offline provider for Phase 1 dev + tests (no network, no key)."""

    def complete(self, system: str, messages: list[dict]) -> ModelResponse:
        last = messages[-1]["content"] if messages else ""
        return ModelResponse(
            text=f"[stub:{self.config.name}] received {len(messages)} message(s); "
            f"last≈{str(last)[:60]!r}",
            raw={"provider": "stub"},
        )


class GeminiProvider(ModelProvider):
    """Google Gemini (2.5+) via the Generative Language REST API. Stdlib HTTP, no SDK dep.

    The API key is resolved lazily (only when ``complete`` is first called) so the ETL
    stages — which never touch the model — run offline without a key.
    """

    _ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self._key: str | None = None

    def _api_key(self) -> str:
        if self._key is None:
            self._key = self.config.resolve_key()
        return self._key

    def complete(self, system: str, messages: list[dict]) -> ModelResponse:
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user" if m["role"] != "assistant" else "model",
                          "parts": [{"text": m["content"]}]} for m in messages],
            "generationConfig": {
                "maxOutputTokens": self.config.max_tokens,
                "temperature": self.config.temperature,
                # Gemini 2.5 'thinking' tokens count against maxOutputTokens and can truncate
                # the visible answer. A grounded briefing needs no extended reasoning, so we
                # disable thinking — all output tokens go to the response text.
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        url = self._ENDPOINT.format(model=self.config.name)
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key()},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"Gemini API error {e.code}: {detail}") from None
        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise RuntimeError(f"Unexpected Gemini response: {json.dumps(payload)[:500]}") from None
        return ModelResponse(text=text, raw=payload)


class _NotWired(ModelProvider):
    provider_name = "?"

    def complete(self, system: str, messages: list[dict]) -> ModelResponse:  # pragma: no cover
        raise NotImplementedError(
            f"Provider '{self.provider_name}' is not wired yet. Set model.provider to "
            f"'stub' or 'gemini' for local dev, or implement this provider before security review."
        )


class AnthropicProvider(_NotWired):
    provider_name = "anthropic"


class BedrockProvider(_NotWired):
    provider_name = "bedrock"


class VertexProvider(_NotWired):
    provider_name = "vertex"


_PROVIDERS: dict[str, type[ModelProvider]] = {
    "stub": StubProvider,
    "gemini": GeminiProvider,
    "anthropic": AnthropicProvider,
    "bedrock": BedrockProvider,
    "vertex": VertexProvider,
}


def get_provider(config: ModelConfig | None = None) -> ModelProvider:
    config = config or ModelConfig.load()
    try:
        cls = _PROVIDERS[config.provider]
    except KeyError:
        raise ValueError(
            f"Unknown model provider '{config.provider}'. Options: {sorted(_PROVIDERS)}"
        ) from None
    return cls(config)
