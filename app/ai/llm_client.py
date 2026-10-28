import logging
import time
from typing import Protocol

import httpx

from app.core.config import Settings, get_settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMClient(Protocol):
    def complete(self, system: str, user: str, max_tokens: int = 1200) -> str: ...


class AnthropicClient:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self.client = httpx.Client(timeout=60)

    def complete(self, system: str, user: str, max_tokens: int = 1200) -> str:
        body = {"model": self.s.llm_model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": self.s.llm_api_key.get_secret_value(), "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        for attempt in range(self.s.http_max_retries + 1):
            try:
                r = self.client.post(self.s.llm_api_url, json=body, headers=headers)
                if r.status_code in (429, 500, 502, 503, 529) and attempt < self.s.http_max_retries:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
            except httpx.HTTPError as e:
                log.warning("LLM call failed: %s", type(e).__name__)  # never log headers/body
                if attempt >= self.s.http_max_retries:
                    raise LLMError("The AI service is unavailable") from None
        raise LLMError("The AI service is unavailable")


def get_llm_client(settings: Settings | None = None) -> LLMClient | None:
    """None means 'use the rule-based fallbacks' (mock mode or no key configured)."""
    s = settings or get_settings()
    if s.llm_provider == "anthropic" and s.llm_api_key:
        return AnthropicClient(s)
    return None
