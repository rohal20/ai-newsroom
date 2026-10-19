import json

import pytest
import requests

from newsroom.llm import LLMError, OpenAICompatClient, extract_json


class FakeResp:
    def __init__(self, status: int, payload: dict | None = None, text: str = ""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def ok(text: str) -> FakeResp:
    return FakeResp(200, {"choices": [{"message": {"content": text}}]})


def client(session, provider="gemini", **kw) -> OpenAICompatClient:
    c = OpenAICompatClient(provider, "test-model", session=session, api_key="k" if provider != "ollama" else None, **kw)
    c.sleep = lambda s: None
    return c


def test_sends_openai_compatible_request():
    s = FakeSession([ok("hello")])
    assert client(s).complete("sys", "user") == "hello"
    call = s.calls[0]
    assert call["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer k"
    assert call["json"]["model"] == "test-model"
    assert [m["role"] for m in call["json"]["messages"]] == ["system", "user"]


def test_ollama_needs_no_key():
    s = FakeSession([ok("hi")])
    c = client(s, provider="ollama")
    assert c.complete("s", "u") == "hi"
    assert "Authorization" not in s.calls[0]["headers"]


def test_retries_rate_limits_then_succeeds():
    s = FakeSession([FakeResp(429), requests.ConnectionError("blip"), ok("done")])
    assert client(s).complete("s", "u") == "done"
    assert len(s.calls) == 3


def test_gives_up_after_max_retries():
    s = FakeSession([FakeResp(503)] * 3)
    with pytest.raises(LLMError, match="failed after 3 attempts"):
        client(s, max_retries=2).complete("s", "u")


def test_does_not_retry_bad_requests():
    s = FakeSession([FakeResp(400, text="bad model name")])
    with pytest.raises(LLMError, match="400"):
        client(s).complete("s", "u")
    assert len(s.calls) == 1


def test_missing_key_explains_what_to_do(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        OpenAICompatClient("gemini", "m")


def test_unknown_provider():
    with pytest.raises(LLMError, match="Unknown provider"):
        OpenAICompatClient("nope", "m", api_key="k")


@pytest.mark.parametrize("raw", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    'Sure! Here is the JSON:\n{"a": 1}\nHope that helps.',
])
def test_extract_json_tolerates_wrapping(raw):
    assert extract_json(raw) == {"a": 1}


def test_extract_json_rejects_garbage():
    with pytest.raises(json.JSONDecodeError):
        extract_json("no json here")
