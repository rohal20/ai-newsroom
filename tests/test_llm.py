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


# ---- v0.3.1: faster, gentler, with fallbacks -------------------------------------------------

from newsroom.llm import FallbackLLM


class Resp429(FakeResp):
    def __init__(self, text="", headers=None):
        super().__init__(429, text=text)
        self.headers = headers or {}


def test_sends_reasoning_effort_only_when_set():
    s = FakeSession([ok("a"), ok("b")])
    client(s, reasoning_effort="low").complete("s", "u")
    client(s).complete("s", "u")
    assert s.calls[0]["json"]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in s.calls[1]["json"]


def test_waits_as_long_as_the_server_asks():
    s = FakeSession([Resp429('{"error": {"details": [{"retryDelay": "7s"}]}}'), ok("done")])
    c, waits = client(s), []
    c.sleep = waits.append
    assert c.complete("s", "u") == "done"
    assert waits == [7.0]


def test_daily_quota_fails_fast_without_retrying():
    s = FakeSession([Resp429('Quota exceeded for metric: generate_requests_per_model_per_day')])
    with pytest.raises(LLMError, match="daily free quota"):
        client(s).complete("s", "u")
    assert len(s.calls) == 1


def test_very_long_server_wait_hands_over_to_a_fallback():
    s = FakeSession([Resp429(headers={"Retry-After": "300"})])
    with pytest.raises(LLMError, match="asked to wait 300s"):
        client(s).complete("s", "u")
    assert len(s.calls) == 1


def test_only_one_retry_after_a_timeout():
    s = FakeSession([requests.Timeout(), requests.Timeout(), ok("never reached")])
    with pytest.raises(LLMError, match="timed out twice"):
        client(s, max_retries=4).complete("s", "u")
    assert len(s.calls) == 2


def test_pacing_between_calls():
    s = FakeSession([ok("a"), ok("b")])
    c, waits, now = client(s, min_interval=5), [], [100.0]
    c.sleep, c.clock = waits.append, lambda: now[0]
    c.complete("s", "u")
    now[0] = 102.0            # second call comes 2s later
    c.complete("s", "u")
    assert waits == [3.0]     # so it waits the remaining 3s


def test_fallback_switches_and_sticks():
    main = client(FakeSession([FakeResp(503), FakeResp(503), FakeResp(503)]), max_retries=2)
    backup_session = FakeSession([ok("from backup"), ok("still backup")])
    backup = client(backup_session)
    llm = FallbackLLM([main, backup])
    assert llm.complete("s", "u") == "from backup"
    assert llm.complete("s", "u") == "still backup"       # does not go back to the failing model
    assert llm.name == "gemini/test-model" and llm.active == 1


def test_fallback_raises_when_everything_fails():
    a = client(FakeSession([FakeResp(400, text="bad")]))
    b = client(FakeSession([FakeResp(400, text="bad too")]))
    with pytest.raises(LLMError, match="bad too"):
        FallbackLLM([a, b]).complete("s", "u")
