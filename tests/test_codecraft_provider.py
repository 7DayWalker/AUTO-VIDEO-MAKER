"""
AVM Step 4 -- CodeCraft provider focused tests (offline, mocked).
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from app.content.providers.codecraft import CodeCraftProvider
from app.content.exceptions import LLMAuthenticationError, LLMQuotaError, LLMRateLimitError, LLMConnectionError, LLMResponseError
from app.providers.fallback_policy import classify_provider_failure, is_fallback_eligible

@pytest.fixture(autouse=True)
def _neutralize_operator_env(monkeypatch):
    """Keep this module independent of the developer/operator ``.env``.

    ``app.config`` loads the real ``.env`` at import time, so ambient
    ``CODECRAFT_*`` values (a live base url, a live key) would otherwise leak
    into assertions about code defaults and make results machine-dependent.
    Tests that WANT an environment value set it themselves via monkeypatch.
    """
    for name in (
        "CODECRAFT_API_KEY",
        "CODECRAFT_BASE_URL",
        "CODECRAFT_TEXT_MODEL",
        "CODECRAFT_REQUEST_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)



def test_provider_identity():
    p = CodeCraftProvider(api_key="sk-test-123", model="qwen3.8-27b")
    assert p.name == "codecraft"
    assert p.model == "qwen3.8-27b"


def test_missing_key_unavailable():
    p = CodeCraftProvider(api_key="", model="qwen3.8-27b")
    assert p.is_available() is False


def test_base_url_handling():
    p = CodeCraftProvider(api_key="sk-x", base_url="https://example.com/v1/")
    assert p._base_url == "https://example.com/v1"
    p2 = CodeCraftProvider(api_key="sk-x")
    assert p2._base_url == "https://api.codecraft.ai/v1"


def test_generate_returns_llmresponse():
    p = CodeCraftProvider(api_key="sk-test", model="qwen3.8-27b")
    fake_resp = Mock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "id": "chatcmpl-123",
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }
    with patch("app.content.providers.codecraft.requests.post", return_value=fake_resp) as mock_post:
        resp = p.generate("hello", system_prompt="you are test", temperature=0, max_tokens=10)
        assert resp.provider == "codecraft"
        assert resp.model == "qwen3.8-27b"
        assert resp.content == '{"ok": true}'
        assert resp.usage["total_tokens"] == 8
        assert resp.metadata["request_id"] == "chatcmpl-123"
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["model"] == "qwen3.8-27b"
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test"


def test_structured_json_sends_response_format():
    p = CodeCraftProvider(api_key="sk-test", model="qwen3.8-27b")
    fake_resp = Mock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"choices": [{"message": {"content": '{"a": 1}'}}]}
    with patch("app.content.providers.codecraft.requests.post", return_value=fake_resp) as mp:
        p.generate("hi", temperature=0, max_tokens=5, json_mode=True)
        assert mp.call_args[1]["json"]["response_format"] == {"type": "json_object"}
    from pydantic import BaseModel
    class Dummy(BaseModel):
        ok: bool
    with patch("app.content.providers.codecraft.requests.post", return_value=fake_resp) as mp2:
        p.generate("hi", response_schema=Dummy)
        assert mp2.call_args[1]["json"]["response_format"] == {"type": "json_object"}


def test_timeout_follows_convention():
    assert CodeCraftProvider(api_key="sk-test", timeout=77)._timeout == 77


def test_error_auth():
    p = CodeCraftProvider(api_key="sk-bad", model="qwen3.8-27b")
    fake_resp = Mock()
    fake_resp.status_code = 401
    fake_resp.json.return_value = {"error": {"code": "invalid_api_key", "message": "bad key"}}
    fake_resp.headers = {}
    with patch("app.content.providers.codecraft.requests.post", return_value=fake_resp):
        with pytest.raises(LLMAuthenticationError):
            p.generate("hi")


def test_error_rate_limit():
    p = CodeCraftProvider(api_key="sk-test", model="qwen3.8-27b")
    fake_resp = Mock()
    fake_resp.status_code = 429
    fake_resp.json.return_value = {"error": {"code": "rate_limit", "message": "too many"}}
    fake_resp.headers = {"Retry-After": "5"}
    with patch("app.content.providers.codecraft.requests.post", return_value=fake_resp):
        with pytest.raises(LLMRateLimitError) as ei:
            p.generate("hi")
        assert ei.value.retry_after == 5
        assert is_fallback_eligible(classify_provider_failure(ei.value))


def test_error_quota():
    p = CodeCraftProvider(api_key="sk-test", model="qwen3.8-27b")
    fake_resp = Mock()
    fake_resp.status_code = 402
    fake_resp.json.return_value = {"error": {"code": "quota_exceeded", "message": "quota"}}
    fake_resp.headers = {}
    with patch("app.content.providers.codecraft.requests.post", return_value=fake_resp):
        with pytest.raises(LLMQuotaError):
            p.generate("hi")


def test_error_timeout():
    p = CodeCraftProvider(api_key="sk-test", model="qwen3.8-27b")
    import requests as req
    with patch("app.content.providers.codecraft.requests.post", side_effect=req.exceptions.Timeout()):
        with pytest.raises(LLMConnectionError):
            p.generate("hi")


def test_error_malformed():
    p = CodeCraftProvider(api_key="sk-test", model="qwen3.8-27b")
    fake_resp = Mock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"choices": []}
    with patch("app.content.providers.codecraft.requests.post", return_value=fake_resp):
        with pytest.raises(LLMResponseError):
            p.generate("hi")


def test_security_no_key_in_exception():
    p = CodeCraftProvider(api_key="sk-super-secret-1234567890", model="qwen3.8-27b")
    fake_resp = Mock()
    fake_resp.status_code = 500
    fake_resp.json.return_value = {"error": {"code": "internalerror", "message": "oops sk-super-secret-1234567890 leaked"}}
    fake_resp.headers = {}
    with patch("app.content.providers.codecraft.requests.post", return_value=fake_resp):
        try:
            p.generate("hi")
            assert False
        except Exception as e:
            assert "sk-super-secret" not in str(e)


def test_registry_codecraft():
    import app.content.provider_registry as reg
    assert reg.has_provider("codecraft") is True
    p = reg.create_provider("codecraft", "qwen3.8-27b")
    assert p.name == "codecraft" and p.model == "qwen3.8-27b"


def test_resolver_integration_offline():
    from app.content.model_resolver import resolve_model
    r = resolve_model("qwen-3.8-flash")
    assert r.canonical_model_id == "qwen-3.8-flash"
    assert r.provider == "codecraft"
    assert r.provider_model_id == "qwen3.8-27b"


def test_model_id_integrity():
    from app.content.model_registry import get_model
    m = get_model("qwen-3.8-flash")
    assert m.id != m.bindings[0].provider_model_id
    import app.content.provider_registry as reg
    p = reg.create_provider(m.bindings[0].provider, m.bindings[0].provider_model_id)
    assert p.model == "qwen3.8-27b"
    assert p.model != m.id


def test_usage_and_request_id():
    p = CodeCraftProvider(api_key="sk-test", model="qwen3.8-27b")
    fake_resp = Mock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "id": "req-abc",
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
    }
    with patch("app.content.providers.codecraft.requests.post", return_value=fake_resp):
        r = p.generate("hi")
        assert r.usage["total_tokens"] == 4
        assert r.metadata["request_id"] == "req-abc"
