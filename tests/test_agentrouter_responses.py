"""Hermetic proofs for verified Responses/direct and Chat/local endpoint pairs."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest
import requests

from app.content.exceptions import LLMConnectionError, LLMInvalidRequestError, LLMModelUnavailableError, LLMOutputTruncatedError, LLMResponseError
from app.content.providers.agentrouter import AgentRouterProvider


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    for name in ("AGENTROUTER_API_KEY", "AGENTROUTER_BASE_URL", "AGENTROUTER_MODEL", "AGENTROUTER_REASONING_EFFORT"):
        monkeypatch.delenv(name, raising=False)


def completed(model="gpt-6.1-sol"):
    return {
        "model": model, "status": "completed", "error": None,
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": '{"ok":true}'}]},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14,
                  "input_tokens_details": {"cached_tokens": 2}, "output_tokens_details": {"reasoning_tokens": 1}},
    }


def test_chapter_json_forces_low_reasoning_and_16k_on_first_call(monkeypatch):
    from app.production.narration_real import _call_json
    monkeypatch.setenv("AGENTROUTER_REASONING_EFFORT", "high")
    response = Mock(status_code=200)
    response.json.return_value = completed()
    response.json.return_value["output"][1]["content"][0]["text"] = '{"beats":[]}'
    post = Mock(return_value=response)
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    provider = AgentRouterProvider(api_key="synthetic-key", timeout=7)
    assert _call_json(provider, "Grounded text only", "Return JSON") == {"beats": []}
    assert post.call_count == 1
    assert post.call_args.kwargs["json"]["max_output_tokens"] == 16384
    assert post.call_args.kwargs["json"]["reasoning"] == {"effort": "low"}
    assert post.call_args.kwargs["timeout"] == 300
    assert provider._timeout == 7


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-6-astra"])
@pytest.mark.parametrize("base_url", [None, "https://agentrouter.org", "https://agentrouter.org/v1/"])
def test_direct_responses_json_instructions_usage_and_exact_identity(monkeypatch, model, base_url):
    response = Mock(status_code=200)
    response.json.return_value = completed(model)
    post = Mock(return_value=response)
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    provider = AgentRouterProvider(api_key="synthetic-private-credential", model=model, base_url=base_url)
    result = provider.generate("Return JSON from the supplied dialogue.", system_prompt="Grounded text only.",
                               response_schema=dict, max_tokens=128)
    assert post.call_args.args == ("https://agentrouter.org/v1/responses",)
    sent = post.call_args.kwargs
    assert sent["allow_redirects"] is False
    assert sent["headers"]["User-Agent"] == "codex_cli_rs/0.1.0"
    assert sent["json"] == {
        "model": model, "input": [{"role": "user", "content": [{"type": "input_text", "text": "Return JSON from the supplied dialogue."}]}],
        "instructions": "Grounded text only.", "max_output_tokens": 128, "stream": False,
        "reasoning": {"effort": "low"}, "text": {"format": {"type": "json_object"}},
    }
    assert json.loads(result.content) == {"ok": True}
    assert result.model == model and result.provider == "agentrouter" and result.raw_response is None
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14,
                            "prompt_tokens_details_cached_tokens": 2, "completion_tokens_details_reasoning_tokens": 1}
    assert result.metadata["api_style"] == "responses" and result.metadata["transport"] == "direct"
    assert result.metadata["http_status"] == 200 and result.metadata["image_payloads_sent"] is False
    assert "synthetic-private-credential" not in repr(result)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8318/v1", "http://localhost:8318", "http://[::1]:8318/v1/"])
def test_loopback_preserves_existing_text_proxy_protocol(monkeypatch, url):
    response = Mock(status_code=200)
    response.json.return_value = {"model": "gpt-6.1-sol", "choices": [{"finish_reason": "stop", "message": {"content": "OK"}}]}
    post = Mock(return_value=response)
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    result = AgentRouterProvider(api_key="synthetic-key", base_url=url).generate("Pure text", max_tokens=32)
    assert post.call_args.args[0].endswith("/v1/chat/completions")
    assert post.call_args.kwargs["json"]["messages"] == [{"role": "user", "content": "Pure text"}]
    assert result.metadata["transport"] == "local_proxy" and result.content == "OK"


@pytest.mark.parametrize("payload", [
    {**completed(), "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
    {**completed(), "status": "incomplete", "incomplete_details": None},
])
def test_incomplete_responses_are_never_accepted_as_completed_json(monkeypatch, payload):
    response = Mock(status_code=200)
    response.json.return_value = payload
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", Mock(return_value=response))
    with pytest.raises(LLMOutputTruncatedError):
        AgentRouterProvider(api_key="synthetic-key").generate("Return JSON")


@pytest.mark.parametrize("change", [
    {"model": "different-model"}, {"model": None}, {"status": "failed"}, {"status": "in_progress"},
    {"status": None}, {"output": []}, {"output": None}, {"output": [None]},
    {"error": {"message": "synthetic-private-credential"}},
    {"output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": 42}]}]},
])
def test_malformed_or_substituted_responses_fail_closed_without_body_leakage(monkeypatch, change):
    response = Mock(status_code=200)
    response.json.return_value = {**completed(), **change}
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", Mock(return_value=response))
    with pytest.raises(LLMResponseError) as error:
        AgentRouterProvider(api_key="synthetic-private-credential").generate("Text only")
    assert "synthetic-private-credential" not in str(error.value)


@pytest.mark.parametrize("kind", ["refusal", "content_filter"])
def test_moderation_is_not_disguised_as_retryable_malformed_output(monkeypatch, kind):
    payload = deepcopy(completed())
    if kind == "refusal":
        payload["output"][-1]["content"] = [{"type": "refusal", "refusal": "synthetic refusal"}]
    else:
        payload.update(status="incomplete", incomplete_details={"reason": "content_filter"})
    response = Mock(status_code=200)
    response.json.return_value = payload
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", Mock(return_value=response))
    with pytest.raises(LLMResponseError) as error:
        AgentRouterProvider(api_key="synthetic-key").generate("Text only")
    assert error.value.category == "content_moderation"


@pytest.mark.parametrize("base", [
    "http://agentrouter.org/v1", "ftp://agentrouter.org/v1", "https://user:synthetic-private-credential@agentrouter.org/v1",
    "https://agentrouter.org/v1?key=synthetic-private-credential", "https://agentrouter.org/v1#fragment",
    "https://agentrouter.org:invalid/v1", "https://agentrouter.org:999999/v1", "https://agentrouter.org/v1/messages",
    "https://agentrouter.org/v1\n", 8318,
])
def test_unsafe_endpoint_configuration_is_rejected_without_network_or_secret_echo(monkeypatch, base):
    post = Mock(side_effect=AssertionError("Invalid configuration must never reach HTTP"))
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    with pytest.raises(ValueError) as error:
        AgentRouterProvider(api_key="synthetic-key", base_url=base)
    assert "synthetic-private-credential" not in str(error.value) and not post.called


def test_explicit_native_responses_does_not_change_default_loopback_transport(monkeypatch):
    default = AgentRouterProvider(api_key="synthetic-only", base_url="http://127.0.0.1:8318")
    native = AgentRouterProvider(api_key="synthetic-only", base_url="http://127.0.0.1:8318", use_responses_api=True)
    assert default._api_style == "chat_completions" and default._base_url == "http://127.0.0.1:8318/v1"
    assert native._api_style == "responses" and native._base_url == "https://agentrouter.org/v1"


@pytest.mark.parametrize("flag", ["true", 1, {}, []])
def test_explicit_transport_flag_is_strict_and_never_dispatches(monkeypatch, flag):
    post = Mock()
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    with pytest.raises(ValueError, match="boolean"):
        AgentRouterProvider(api_key="synthetic-only", use_responses_api=flag)
    assert not post.called


@pytest.mark.parametrize("model", ["claude-opus-5.5", "gpt-4o", "whisper-1", "tts-1", "gpt-image-1"])
def test_unverified_models_and_operations_are_not_enabled_by_key_presence(monkeypatch, model):
    post = Mock()
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    with pytest.raises(LLMModelUnavailableError):
        AgentRouterProvider(api_key="synthetic-key", model=model)
    assert not post.called


@pytest.mark.parametrize("budget", [True, 0, -1, 1.5, 131073])
def test_invalid_output_budgets_fail_before_http(monkeypatch, budget):
    post = Mock()
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    with pytest.raises(ValueError):
        AgentRouterProvider(api_key="synthetic-key").generate("Text only", max_tokens=budget)
    assert not post.called


def test_direct_timeout_does_not_leak_transport_details(monkeypatch):
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", Mock(side_effect=requests.Timeout("synthetic-private-credential")))
    with pytest.raises(LLMConnectionError) as error:
        AgentRouterProvider(api_key="synthetic-key").generate("Text only")
    assert error.value.category == "timeout" and "synthetic-private-credential" not in str(error.value)
    assert error.value.diagnostics["http_status"] is None
    assert error.value.diagnostics["error_code"] == "REQUEST_TIMEOUT"
    assert error.value.diagnostics["timeout_seconds"] == 300


@pytest.mark.parametrize("base", ["https://agentrouter.org/v1", "http://127.0.0.1:8318/v1"])
def test_http_400_parameter_reason_is_preserved_and_secret_safe(monkeypatch, base):
    credential = "synthetic-private-credential"
    response = Mock(status_code=400, headers={"x-request-id": "request-fixture"})
    response.json.return_value = {"error": {"code": "unsupported_parameter",
        "message": f"Unsupported field temperature. Authorization: Bearer {credential}; https://private.invalid/endpoint"}}
    post = Mock(return_value=response)
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    with pytest.raises(LLMInvalidRequestError) as caught:
        AgentRouterProvider(api_key=credential, base_url=base).generate_json("Grounded text", max_tokens=16384, reasoning={"effort": "low"})
    assert post.call_count == 1
    error = caught.value
    assert error.status_code == 400 and error.diagnostics["error_code"] == "unsupported_parameter"
    assert "Unsupported field temperature" in error.diagnostics["message"]
    assert credential not in str(error) and credential not in str(error.diagnostics)
    assert "private.invalid" not in str(error) and error.diagnostics["request_id"] == "request-fixture"


@pytest.mark.parametrize("status,code,message", [(404, "model_not_found", "Model not found"),
    (400, "model_not_found", "Model not found")])
def test_model_rejection_keeps_classification_and_diagnostics(monkeypatch, status, code, message):
    response = Mock(status_code=status, headers={})
    response.json.return_value = {"error": {"code": code, "message": message}}
    post = Mock(return_value=response)
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    with pytest.raises(LLMModelUnavailableError) as caught:
        AgentRouterProvider(api_key="synthetic-only").generate("Text")
    assert post.call_count == 1 and caught.value.status_code == status
    assert caught.value.diagnostics["message"] == message