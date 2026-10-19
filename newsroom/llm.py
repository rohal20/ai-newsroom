"""A small client for any OpenAI-compatible chat API (Gemini, Groq, Ollama).

Switching provider is a config change, not a code change:

    llm:
      provider: gemini          # or groq, ollama
      model: gemini-3.8-flash
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Protocol

import requests

log = logging.getLogger(__name__)

PROVIDERS: dict[str, dict[str, str | None]] = {
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "key_env": "GEMINI_API_KEY"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY"},
    "ollama": {"base_url": "http://localhost:11434/v1", "key_env": None},  # runs locally, no key
}

RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """Raised when the model cannot be reached or keeps returning unusable output."""


class LLM(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatClient:
    def __init__(
        self,
        provider: str,
        model: str,
        temperature: float = 0.3,
        timeout: float = 60,
        max_retries: int = 4,
        session: requests.Session | None = None,
        api_key: str | None = None,
    ):
        if provider not in PROVIDERS:
            raise LLMError(f"Unknown provider '{provider}'. Choose one of: {', '.join(PROVIDERS)}")
        spec = PROVIDERS[provider]
        key_env = spec["key_env"]
        self.api_key = api_key or (os.environ.get(key_env) if key_env else None)
        if key_env and not self.api_key:
            raise LLMError(
                f"Missing {key_env}. Create a free key, then add it to a .env file:\n"
                f"    {key_env}=your-key-here"
            )
        self.url = f"{spec['base_url']}/chat/completions"
        self.provider, self.model, self.temperature = provider, model, temperature
        self.timeout, self.max_retries = timeout, max_retries
        self.http = session or requests.Session()
        self.sleep = time.sleep  # swapped out in tests

    def complete(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.http.post(self.url, headers=headers, json=body, timeout=self.timeout)
            except requests.RequestException as exc:
                error = f"network error: {exc}"
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()["choices"][0]["message"]["content"] or ""
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        raise LLMError(f"Unexpected response from {self.provider}: {exc}") from exc
                if resp.status_code not in RETRY_STATUS:
                    raise LLMError(f"{self.provider} returned {resp.status_code}: {resp.text[:300]}")
                error = f"HTTP {resp.status_code}"
            if attempt == self.max_retries:
                raise LLMError(f"{self.provider} failed after {attempt + 1} attempts ({error})")
            wait = min(2 ** attempt * 2, 30)
            log.warning("%s: %s, retrying in %ss", self.provider, error, wait)
            self.sleep(wait)
        raise AssertionError("unreachable")


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extract_json(text: str) -> dict:
    """Parse a JSON object from model output, tolerating ```json fences and chatter around it."""
    cleaned = _FENCE_RE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])
