"""Shared fixtures: a scripted fake model client and a jcode module reset
to a known state for every test. No network, no API key needed."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("TEST_KEY_A", "fake-key-a")
os.environ.setdefault("TEST_KEY_B", "fake-key-b")

import jcode
import jcode_extras


# ------------------------------------------------------------ Fake responses
class FakeToolCall:
    """Mimics openai's tool call object: attribute access plus model_dump()."""

    def __init__(self, call_id: str, name: str, args: dict) -> None:
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=json.dumps(args))

    def model_dump(self) -> dict:
        return {"id": self.id, "type": "function",
                "function": {"name": self.function.name, "arguments": self.function.arguments}}


def reply(text: str = "", tool_calls: list | None = None, tokens: int = 100):
    """A chat completion response with optional tool calls."""
    message = SimpleNamespace(content=text, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                           usage=SimpleNamespace(total_tokens=tokens))


def rate_limit_error(message: str) -> Exception:
    """An openai.RateLimitError (HTTP 429), built for httpx or httpx2."""
    import openai
    try:
        import httpx2 as http
    except ImportError:
        import httpx as http
    response = http.Response(429, request=http.Request("POST", "https://example.invalid"))
    return openai.RateLimitError(message, response=response, body=None)


DAILY = "Error code: 429 - quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier"
MINUTE = ("Error code: 429 - quotaId GenerateRequestsPerMinutePerProjectPerModel-FreeTier, "
          "'retryDelay': '7s'")


class FakeClient:
    """Plays back a script of responses/exceptions and records every request."""

    def __init__(self) -> None:
        self.script: list = []
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("fake model called more often than scripted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ------------------------------------------------------------- Fake clock
class FakeTime:
    """Stands in for the time module inside jcode only: sleep() records the
    delay instead of waiting, everything else is the real time module.

    Patching time.sleep globally would also catch the standard library's own
    sleeps (subprocess.Popen.wait with a timeout polls with time.sleep while
    the git check waits for git to exit), so the recorded delays would depend
    on how fast git exits on the machine running the tests.
    """

    def __init__(self, sleeps: list[float]) -> None:
        self.sleep = sleeps.append

    def __getattr__(self, name: str):
        return getattr(time, name)


# ------------------------------------------------------------------ Fixture
@pytest.fixture
def jc(tmp_path, monkeypatch):
    """jcode in executor mode, sandbox and logs in tmp_path, two fake
    providers 'pa'/'pb' and a fallback chain A -> B (both on 'pa')."""
    fake = FakeClient()
    sandbox = tmp_path / "code"
    sandbox.mkdir()
    providers = {"pa": {"base_url": "https://a.invalid/v1", "api_key_env": "TEST_KEY_A", "model": "model-a"},
                 "pb": {"base_url": "https://b.invalid/v1", "api_key_env": "TEST_KEY_B", "model": "model-c"}}
    chain = (jcode_extras.ChainEntry("pa", "model-a", "low"),
             jcode_extras.ChainEntry("pa", "model-b", None),
             jcode_extras.ChainEntry("pb", "model-c", None, compact_threshold=500))
    day = {"value": "day-1"}
    sleeps: list[float] = []

    monkeypatch.setattr(jcode, "OpenAI", lambda **kw: fake)
    monkeypatch.setattr(jcode, "client", fake)
    monkeypatch.setattr(jcode, "SETTINGS", {"providers": providers})
    monkeypatch.setattr(jcode, "SANDBOX", sandbox.resolve())
    monkeypatch.setattr(jcode, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(jcode, "LOG_FILE", tmp_path / "logs" / "test.md")
    monkeypatch.setattr(jcode, "MODE", jcode_extras.EXECUTOR)
    monkeypatch.setattr(jcode, "MAX_STEPS", 15)
    monkeypatch.setattr(jcode, "COMPACT_THRESHOLD", 60_000)
    monkeypatch.setattr(jcode, "REASONING_EFFORT", None)
    monkeypatch.setattr(jcode, "FALLBACK", jcode_extras.FallbackChain(chain, day_fn=lambda: day["value"]))
    monkeypatch.setattr(jcode, "ACTIVE_ENTRY", None)
    monkeypatch.setattr(jcode, "_AUTO_SWITCHED", False)
    monkeypatch.setattr(jcode, "time", FakeTime(sleeps))  # only jcode's sleeps
    jcode._activate_entry(chain[0])

    return SimpleNamespace(mod=jcode, fake=fake, sandbox=sandbox.resolve(), chain=chain,
                           day=day, sleeps=sleeps, tmp=tmp_path)
