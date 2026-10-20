"""A small client for any OpenAI-compatible chat API (Gemini, Groq, Ollama), with a fallback chain.

Switching provider is a config change, not a code change:

    llm:
      provider: gemini
      model: gemini-3.8-flash
      fallbacks:
        - {provider: gemini, model: gemini-3.5-flash-lite}
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Protocol, TypeVar

import requests
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

PROVIDERS: dict[str, dict[str, str | None]] = {
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "key_env": "GEMINI_API_KEY"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY"},
    "ollama": {"base_url": "http://localhost:11434/v1", "key_env": None},  # runs locally, no key
}

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_WAIT_SECONDS = 60  # if the server asks us to wait longer than this, give up and let a fallback take over
_RETRY_DELAY_RE = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')
_DAILY_QUOTA_RE = re.compile(r"per.?day|PerDay|daily", re.IGNORECASE)


class LLMError(RuntimeError):
    """Raised when the model cannot be reached or keeps returning unusable output."""


class LLM(Protocol):
    def complete(self, system: str, user: str) -> str: ...


def _server_wait(resp) -> float | None:
    """How long the server asked us to wait, from Retry-After or Google's retryDelay."""
    header = (getattr(resp, "headers", None) or {}).get("Retry-After")
    if header and str(header).replace(".", "", 1).isdigit():
        return float(header)
    m = _RETRY_DELAY_RE.search(getattr(resp, "text", "") or "")
    return float(m.group(1)) if m else None


class OpenAICompatClient:
    def __init__(
        self,
        provider: str,
        model: str,
        temperature: float = 0.3,
        timeout: float = 120,
        max_retries: int = 2,
        reasoning_effort: str | None = None,
        min_interval: float = 0.0,
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
        self.reasoning_effort, self.min_interval = reasoning_effort, min_interval
        self.http = session or requests.Session()
        self.sleep = time.sleep          # swapped out in tests
        self.clock = time.monotonic      # swapped out in tests
        self._last_call: float | None = None

    @property
    def name(self) -> str:
        return f"{self.provider}/{self.model}"

    def _pace(self) -> None:
        """Leave at least `min_interval` seconds between calls, to stay under per-minute limits."""
        if self._last_call is not None and self.min_interval:
            wait = self.min_interval - (self.clock() - self._last_call)
            if wait > 0:
                self.sleep(wait)
        self._last_call = self.clock()

    def complete(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body: dict = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort

        timeouts = 0
        for attempt in range(self.max_retries + 1):
            self._pace()
            wait: float | None = None
            try:
                resp = self.http.post(self.url, headers=headers, json=body, timeout=self.timeout)
            except requests.Timeout:
                timeouts += 1
                error = f"no answer within {self.timeout:.0f}s"
                if timeouts > 1:  # a slow model rarely gets faster on the third try; let a fallback take over
                    raise LLMError(f"{self.name} timed out twice")
            except requests.RequestException as exc:
                error = f"network error: {exc.__class__.__name__}"
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()["choices"][0]["message"]["content"] or ""
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        raise LLMError(f"Unexpected response from {self.name}: {exc}") from exc
                if resp.status_code not in RETRY_STATUS:
                    raise LLMError(f"{self.name} returned {resp.status_code}: {resp.text[:300]}")
                if resp.status_code == 429 and _DAILY_QUOTA_RE.search(resp.text or ""):
                    raise LLMError(f"{self.name}: daily free quota used up (resets at midnight Pacific time)")
                error = f"HTTP {resp.status_code}"
                wait = _server_wait(resp)
                if wait is not None and wait > MAX_WAIT_SECONDS:
                    raise LLMError(f"{self.name}: server asked to wait {wait:.0f}s ({error})")
            if attempt == self.max_retries:
                raise LLMError(f"{self.name} failed after {attempt + 1} attempts ({error})")
            wait = wait if wait is not None else min(2 ** attempt * 3, 30)
            log.warning("%s: %s, retrying in %.0fs", self.name, error, wait)
            self.sleep(wait)
        raise AssertionError("unreachable")


class FallbackLLM:
    """Tries each model in order. Once a model fails, the rest of the run sticks with the next one."""

    def __init__(self, clients: list[OpenAICompatClient]):
        if not clients:
            raise LLMError("No models configured.")
        self.clients, self.active = clients, 0

    @property
    def name(self) -> str:
        return self.clients[self.active].name

    def complete(self, system: str, user: str) -> str:
        while True:
            client = self.clients[self.active]
            try:
                return client.complete(system, user)
            except LLMError as exc:
                if self.active + 1 >= len(self.clients):
                    raise
                self.active += 1
                log.warning("%s failed (%s). Switching to %s for the rest of this run.",
                            client.name, exc, self.clients[self.active].name)


def ask_json(llm: LLM, system: str, user: str, schema: type[T], attempts: int = 2) -> T:
    """Ask for JSON matching `schema`. If the reply is unusable, ask once more and say why."""
    last_error = ""
    for _ in range(attempts):
        prompt = user if not last_error else (
            f"{user}\n\nYour previous reply could not be used ({last_error}). Reply with valid JSON only."
        )
        raw = llm.complete(system, prompt)
        try:
            return schema.model_validate(extract_json(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc).splitlines()[0][:200]
            log.warning("Unusable reply for %s: %s", schema.__name__, last_error)
    raise LLMError(f"No usable {schema.__name__} after {attempts} attempts: {last_error}")


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
