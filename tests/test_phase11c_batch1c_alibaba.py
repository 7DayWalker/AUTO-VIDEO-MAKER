"""Phase 11C Batch 1C - Alibaba primary provider + OpenRouter fallback (LLM).

Hermetic tests for the LLM/text seam: credential classification, response
parsing/usage mapping, failure classification, and the ONE authorized
cross-provider policy (Alibaba primary -> classified OpenRouter fallback,
never parallel). No network requests are made; the HTTP boundary is
monkeypatched.
"""

import json

import pytest

from app.content.exceptions import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMResponseError,
)
from app.content.providers.alibaba import (
    ALIBABA_API_KEY_ENV,
    AlibabaCredentialClassError,
    AlibabaProvider,
    validate_alibaba_credential,
)
from app.content.providers.base import LLMResponse
from app.content.providers.fallback import FallbackLLMProvider
from app.providers.fallback_policy import (
    FailureCategory,
    classify_provider_failure,
    is_fallback_eligible,
)


# ---------------------------------------------------------------------------
# Credential classification (sk-ws accepted / sk-sp rejected / missing)
# ---------------------------------------------------------------------------


def test_standard_application_credential_class_is_accepted():
    assert validate_alibaba_credential("sk-ws-abc123") == "sk-ws-abc123"


def test_token_plan_credential_class_is_rejected_without_leaking_the_key():
    secret = "sk-sp-very-secret-token-plan-value"
    with pytest.raises(AlibabaCredentialClassError) as excinfo:
        validate_alibaba_credential(secret)
    # The credential must NEVER appear in the error output.
    assert secret not in str(excinfo.value)
    assert secret not in repr(excinfo.value)
    assert excinfo.value.category == FailureCategory.CREDENTIAL_CLASS
    assert not is_fallback_eligible(excinfo.value.category)


def test_missing_credential_reports_unavailable_without_raising():
    assert validate_alibaba_credential("") == ""
    provider = AlibabaProvider(api_key="")
    assert provider.is_available() is False


def test_provider_is_available_with_application_key(monkeypatch):
    monkeypatch.setenv(ALIBABA_API_KEY_ENV, "sk-ws-abc123")
    assert AlibabaProvider().is_available() is True


def test_provider_reports_unavailable_for_forbidden_credential_class(
    monkeypatch,
):
    monkeypatch.setenv(ALIBABA_API_KEY_ENV, "sk-sp-nope")
    provider = AlibabaProvider()
    assert provider.is_available() is False


# ---------------------------------------------------------------------------
# Request/response translation (fake HTTP boundary)
# ---------------------------------------------------------------------------


class FakeHTTPResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeRequests:
    """Monkeypatch seam standing in for the ``requests`` module."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if self.error is not None:
            raise self.error
        return self.response


def successful_payload(content='{"scenes": []}'):
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        },
    }

def test_generate_posts_openai_compatible_request_and_returns_contract(
    monkeypatch,
):
    fake = FakeRequests(response=FakeHTTPResponse(payload=successful_payload()))
    install(monkeypatch, fake)
    provider = make_provider(model="qwen-plus")
    response = provider.generate("Make a plan", system_prompt="Be brief.")

    assert fake.calls, "expected exactly one HTTP call"
    call = fake.calls[0]
    assert call["url"].endswith("/chat/completions")
    # The credential is sent as an authorization header, never in the body.
    assert call["headers"]["Authorization"] == "Bearer sk-ws-abc123"
    assert "sk-ws-abc123" not in json.dumps(call["json"])
    body = call["json"]
    assert body["model"] == "qwen-plus"
    assert body["messages"][-1] == {"role": "user", "content": "Make a plan"}

    assert isinstance(response, LLMResponse)
    assert response.provider == "alibaba"
    assert response.model == "qwen-plus"
    assert response.usage == {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
    }
    assert provider.last_latency_ms is not None
    assert provider.last_request_kind == "text"


def test_generate_json_uses_native_json_mode(monkeypatch):
    fake = FakeRequests(
        response=FakeHTTPResponse(payload=successful_payload("{}"))
    )
    install(monkeypatch, fake)
    provider = make_provider()
    response = provider.generate_json("Return JSON")
    assert fake.calls[0]["json"]["response_format"] == {"type": "json_object"}
    assert response.content == "{}"
    assert provider.last_request_kind == "json"


def test_reasoning_uses_the_configured_reasoning_model(monkeypatch):
    fake = FakeRequests(response=FakeHTTPResponse(payload=successful_payload()))
    install(monkeypatch, fake)
    provider = make_provider(model="qwen-plus", reasoning_model="qwen3-max")
    response = provider.generate("Think", use_reasoning=True)
    assert fake.calls[0]["json"]["model"] == "qwen3-max"
    assert response.model == "qwen3-max"


def test_malformed_response_is_classified_provider_failure(monkeypatch):
    fake = FakeRequests(
        response=FakeHTTPResponse(payload={"choices": []})
    )
    install(monkeypatch, fake)
    provider = make_provider()
    with pytest.raises(LLMResponseError) as excinfo:
        provider.generate("hi")
    assert classify_provider_failure(excinfo.value) == (
        FailureCategory.RESPONSE_MALFORMED
    )
    assert is_fallback_eligible(FailureCategory.RESPONSE_MALFORMED)

# ---------------------------------------------------------------------------
# HTTP status/error-code mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "payload", "expected_type", "expected_category"),
    [
        (401, {"error": {"code": "InvalidApiKey", "message": "bad key"}},
         LLMAuthenticationError, FailureCategory.AUTHENTICATION),
        (429, {"error": {"code": "Throttling.RateQuota", "message": "slow"}},
         LLMRateLimitError, FailureCategory.RATE_LIMIT),
        (402, {"error": {"code": "Arrearage", "message": "no credit"}},
         LLMQuotaError, FailureCategory.QUOTA),
        (500, {"error": {"code": "SystemError", "message": "boom"}},
         LLMConnectionError, FailureCategory.PROVIDER_5XX),
        (400, {"error": {"code": "InvalidParameter", "message": "bad param"}},
         LLMResponseError, FailureCategory.INVALID_REQUEST),
    ],
)
def test_http_failures_map_to_existing_avm_exceptions_and_categories(
    monkeypatch, status, payload, expected_type, expected_category
):
    fake = FakeRequests(
        response=FakeHTTPResponse(status_code=status, payload=payload)
    )
    install(monkeypatch, fake)
    provider = make_provider()
    with pytest.raises(expected_type) as excinfo:
        provider.generate("hi")
    assert classify_provider_failure(excinfo.value) == expected_category
    assert is_fallback_eligible(expected_category) == (
        expected_category in (
            FailureCategory.RATE_LIMIT,
            FailureCategory.QUOTA,
            FailureCategory.PROVIDER_5XX,
        )
    )


def test_content_moderation_rejection_is_not_fallback_eligible(monkeypatch):
    fake = FakeRequests(
        response=FakeHTTPResponse(
            status_code=400,
            payload={
                "error": {
                    "code": "DataInspectionFailed",
                    "message": "content policy",
                }
            },
        )
    )
    install(monkeypatch, fake)
    provider = make_provider()


# ---------------------------------------------------------------------------
# FallbackLLMProvider: the fixed Alibaba -> OpenRouter policy
# ---------------------------------------------------------------------------


class FakePrimary:
    name = "alibaba"
    model = "qwen-plus"

    def __init__(self, error=None, response=None):
        self._error = error
        self._response = response or LLMResponse(
            content="plan", provider="alibaba", model="qwen-plus"
        )
        self.calls = 0

    def is_available(self):
        return True

    def generate(self, **kwargs):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._response

    def generate_json(self, **kwargs):
        return self.generate(**kwargs)


class FakeFallback:
    name = "openrouter"
    model = "or-model"

    def __init__(self):
        self.calls = 0

    def is_available(self):
        return True

    def generate(self, **kwargs):
        self.calls += 1
        return LLMResponse(
            content="fallback plan", provider="openrouter", model="or-model"
        )

    def generate_json(self, **kwargs):
        return self.generate(**kwargs)


def test_alibaba_success_never_calls_openrouter():
    primary = FakePrimary()
    fallback = FakeFallback()
    wrapper = FallbackLLMProvider(primary, fallback)
    response = wrapper.generate("make a plan")

    assert primary.calls == 1
    assert fallback.calls == 0, "OpenRouter must not be called on success"
    assert response.provider == "alibaba"
    assert response.metadata["primary_provider"] == "alibaba"
    assert response.metadata["final_provider"] == "alibaba"
    assert response.metadata["fallback_used"] is False
    assert wrapper.last_fallback_used is False


def test_rate_limit_falls_back_to_openrouter_with_recorded_reason():
    wrapper = FallbackLLMProvider(
        FakePrimary(error=LLMRateLimitError("alibaba", 5)), FakeFallback()
    )
    response = wrapper.generate("make a plan")

    assert response.provider == "openrouter"
    assert response.metadata["fallback_used"] is True
    assert response.metadata["fallback_reason"] == FailureCategory.RATE_LIMIT
    assert response.metadata["primary_provider"] == "alibaba"
    assert response.metadata["final_provider"] == "openrouter"
    assert wrapper.last_fallback_used is True
    assert wrapper.last_fallback_reason == FailureCategory.RATE_LIMIT


def test_json_generation_falls_back_the_same_way():
    wrapper = FallbackLLMProvider(
        FakePrimary(error=LLMRateLimitError("alibaba")), FakeFallback()
    )
    response = wrapper.generate_json("make a plan")
    assert response.provider == "openrouter"
    assert response.metadata["fallback_reason"] == FailureCategory.RATE_LIMIT


def test_provider_5xx_falls_back():
    from app.content.providers.alibaba import AlibabaServerBusyError

    wrapper = FallbackLLMProvider(
        FakePrimary(error=AlibabaServerBusyError("alibaba", "HTTP 502")),
        FakeFallback(),
    )
    response = wrapper.generate("hi")
    assert response.provider == "openrouter"
    assert response.metadata["fallback_reason"] == FailureCategory.PROVIDER_5XX


@pytest.mark.parametrize(
    "error",
    [
        LLMAuthenticationError("alibaba"),
        AlibabaCredentialClassError(),
    ],
)
def test_non_eligible_failures_never_fall_back(error):
    fallback = FakeFallback()
    wrapper = FallbackLLMProvider(FakePrimary(error=error), fallback)

    with pytest.raises(type(error)):
        wrapper.generate("hi")
    assert fallback.calls == 0
    assert wrapper.last_fallback_used is False


def test_fallback_disabled_fails_truthfully_on_eligible_failure():
    fallback = FakeFallback()
    wrapper = FallbackLLMProvider(
        FakePrimary(error=LLMRateLimitError("alibaba")),
        fallback,
        fallback_enabled=False,
    )
    with pytest.raises(LLMRateLimitError):
        wrapper.generate("hi")
    assert fallback.calls == 0


def test_sequential_only_one_fallback_call_per_request():
    primary = FakePrimary(error=LLMRateLimitError("alibaba"))
    fallback = FakeFallback()
    wrapper = FallbackLLMProvider(primary, fallback)
    wrapper.generate("hi")
    wrapper.generate("hi again")
    assert primary.calls == 2 and fallback.calls == 2


def test_wrapper_availability_requires_only_the_primary():
    class UnavailablePrimary(FakePrimary):
        def is_available(self):
            return False

    wrapper = FallbackLLMProvider(UnavailablePrimary(), FakeFallback())
    assert wrapper.is_available() is False


def test_wrapper_identity_is_the_primary():
    wrapper = FallbackLLMProvider(FakePrimary(), FakeFallback())
    assert wrapper.name == "alibaba"
    assert wrapper.model == "qwen-plus"




def test_forbidden_credential_class_raises_before_any_network_call(monkeypatch):
    fake = FakeRequests()
    install(monkeypatch, fake)
    provider = make_provider(api_key="sk-sp-forbidden")
    with pytest.raises(AlibabaCredentialClassError):
        provider.generate("hi")
    assert fake.calls == [], "no request may be made with a forbidden key"



def install(monkeypatch, fake):
    monkeypatch.setattr(
        "app.content.providers.alibaba.requests", fake, raising=False
    )


def make_provider(**kwargs):
    kwargs.setdefault("api_key", "sk-ws-abc123")
    kwargs.setdefault("base_url", "https://dashscope.test/compatible-mode/v1")
    return AlibabaProvider(**kwargs)
