"""Phase 11C Batch 1C targeted follow-up fixes - focused tests.

Fix #1: Alibaba LLM usage normalization (nested DashScope usage ->
existing ``LLMResponse.usage: Dict[str, int]`` contract; adapter-local,
no contract loosening).

Fix #2: model-aware Alibaba video size selection (per-model
provider-validated capability entries; live-validated wan2.2-t2v-plus
evidence is authoritative; NO universal resolution hard-code).

Hermetic: realistic fixtures, no network, no billable calls.
"""

import json

import pytest

from app.content.providers.alibaba import AlibabaProvider, _normalize_usage
from app.content.providers.base import LLMResponse
from app.orchestrator.alibaba_video import AlibabaVideoVisualProvider
from app.orchestrator.alibaba_video_config import (
    DEFAULT_VIDEO_MODEL,
    model_capabilities,
    select_video_size,
)
from tests.test_phase11c_batch1c_visual import FakeDashScope, make_request


PROJECT_ID = "20260912_alibaba_probe_001"


# ---------------------------------------------------------------------------
# Fix #1: Alibaba usage normalization
# ---------------------------------------------------------------------------


def test_normalize_usage_flattens_nested_dashscope_details():
    raw = {
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
        "prompt_tokens_details": {"cached_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 0},
    }
    flat = _normalize_usage(raw)
    assert flat == {
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
        "prompt_tokens_details_cached_tokens": 0,
        "completion_tokens_details_reasoning_tokens": 0,
    }
    # The existing contract holds: every value is an int.
    response = LLMResponse(
        content="x", provider="alibaba", model="qwen-plus", usage=flat
    )
    assert response.usage == flat


def test_normalize_usage_is_all_integers_and_contract_constructible():
    flat = _normalize_usage(
        {
            "prompt_tokens": 12,
            "completion_tokens": 5,
            "total_tokens": 17,
            "prompt_tokens_details": {"cached_tokens": 3},
        }
    )
    assert all(isinstance(v, int) for v in flat.values())
    assert flat["prompt_tokens_details_cached_tokens"] == 3


def test_normalize_usage_missing_and_null_map_to_none():
    assert _normalize_usage(None) is None
    assert _normalize_usage({}) is None


def test_normalize_usage_drops_non_numeric_leaves():
    flat = _normalize_usage(
        {
            "prompt_tokens": 12,
            "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
            "models": ["qwen-plus"],  # non-numeric noise is dropped
            "server_ip": "10.0.0.1",
        }
    )
    assert flat == {
        "prompt_tokens": 12,
        "prompt_tokens_details_cached_tokens": 0,
        "prompt_tokens_details_audio_tokens": 0,
    }


class _FakeHTTPResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = ""
        self.headers = {}

    def json(self):
        return self._payload


class _FakeRequests:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.response


def _install(monkeypatch, fake):
    monkeypatch.setattr(
        "app.content.providers.alibaba.requests", fake, raising=False
    )


def _alibaba_payload(content, usage):
    return {
        "choices": [{"message": {"content": content}}],
        "usage": usage,
        "request_id": "req-live-shaped",
    }


# Realistic DashScope OpenAI-compatible usage fixture (live-proven shape,
# including the nested details object that previously broke construction).
DASHSCOPE_USAGE = {
    "prompt_tokens": 12,
    "completion_tokens": 5,
    "total_tokens": 17,
    "prompt_tokens_details": {"cached_tokens": 0},
    "completion_tokens_details": {"reasoning_tokens": 0},
}


def test_text_generation_succeeds_through_the_adapter_with_nested_usage(
    monkeypatch,
):
    fake = _FakeRequests(
        _FakeHTTPResponse(
            _alibaba_payload("AVM_BATCH_1C_ALIVE", DASHSCOPE_USAGE)
        )
    )
    _install(monkeypatch, fake)
    provider = AlibabaProvider(
        api_key="sk-ws-abc123",
        base_url="https://dashscope.test/compatible-mode/v1",
        model="qwen-plus",
    )
    response = provider.generate("Reply exactly: AVM_BATCH_1C_ALIVE")

    assert isinstance(response, LLMResponse)
    assert response.provider == "alibaba"
    assert response.model == "qwen-plus"
    assert response.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
        "prompt_tokens_details_cached_tokens": 0,
        "completion_tokens_details_reasoning_tokens": 0,
    }


def test_json_generation_succeeds_through_the_same_normalization(monkeypatch):
    fake = _FakeRequests(
        _FakeHTTPResponse(
            _alibaba_payload(
                json.dumps({"ok": True, "note": "batch 1c"}),
                DASHSCOPE_USAGE,
            )
        )
    )
    _install(monkeypatch, fake)
    provider = AlibabaProvider(
        api_key="sk-ws-abc123",
        base_url="https://dashscope.test/compatible-mode/v1",
        model="qwen-plus",
    )
    response = provider.generate_json("Return JSON only")
    assert json.loads(response.content) == {"ok": True, "note": "batch 1c"}
    assert response.usage is not None
    assert "prompt_tokens_details_cached_tokens" in response.usage


def test_missing_usage_still_constructs_the_contract(monkeypatch):
    fake = _FakeRequests(
        _FakeHTTPResponse(_alibaba_payload("hello", None))
    )
    _install(monkeypatch, fake)
    provider = AlibabaProvider(
        api_key="sk-ws-abc123",
        base_url="https://dashscope.test/compatible-mode/v1",
    )
    response = provider.generate("hi")
    assert response.usage is None


def test_openrouter_usage_behavior_is_unaffected():
    # The normalization lives ONLY in the Alibaba adapter; OpenRouter's
    # provider module contains no reference to it.
    import inspect

    from app.content.providers import openrouter as openrouter_mod

    assert "normalize_usage" not in inspect.getsource(openrouter_mod)



# ---------------------------------------------------------------------------
# Fix #2: model-aware Alibaba video size selection
# ---------------------------------------------------------------------------


def test_wan2_2_t2v_plus_declares_the_live_validated_capability_set():
    caps = model_capabilities("wan2.2-t2v-plus")
    assert caps["supported_sizes"] == (
        "1080*1920", "1920*1080", "1440*1440", "1632*1248",
        "1248*1632", "480*832", "832*480", "624*624",
    )
    # Live-validated provider evidence (InvalidParameter truth, 2026-09-13).
    assert "720*1280" not in caps["supported_sizes"]
    assert caps["durations"] == (5, 10)
    assert "t2v" in caps["modes"]


def test_wan2_2_t2v_plus_selects_provider_supported_9_16_size():
    size, error = select_video_size("wan2.2-t2v-plus", "9:16")
    assert error is None
    assert size == "1080*1920"


def test_wan2_2_t2v_plus_selects_provider_supported_16_9_size():
    size, error = select_video_size("wan2.2-t2v-plus", "16:9")
    assert error is None
    assert size == "1920*1080"


def test_adapter_translation_uses_model_aware_sizes(tmp_path):
    provider = AlibabaVideoVisualProvider(
        tmp_path, model="wan2.2-t2v-plus",
        api_key="sk-ws-abc123", http_client=FakeDashScope(),
    )
    body = provider._translate_request(make_request(duration=5))
    assert body["parameters"]["size"] == "1080*1920"
    body_wide = provider._translate_request(make_request(aspect_ratio="16:9"))
    assert body_wide["parameters"]["size"] == "1920*1080"


def test_operator_configuration_wins_but_is_capability_checked(tmp_path):
    # Operator mapping wins over the model default...
    provider = AlibabaVideoVisualProvider(
        tmp_path, model="wan2.2-t2v-plus", api_key="sk-ws-abc123",
        http_client=FakeDashScope(),
        size_map={"9:16": "832*480"},
    )
    body = provider._translate_request(make_request(duration=5))
    assert body["parameters"]["size"] == "832*480"

    # ...but an operator size OUTSIDE the model's supported set is rejected
    # truthfully BEFORE any billable submission (never sent to Alibaba).
    provider_bad = AlibabaVideoVisualProvider(
        tmp_path, model="wan2.2-t2v-plus", api_key="sk-ws-abc123",
        http_client=FakeDashScope(),
        size_map={"9:16": "720*1280"},  # the OLD invalid assumption
    )
    with pytest.raises(Exception) as excinfo:
        provider_bad._translate_request(make_request(duration=5))
    assert "720*1280" in str(excinfo.value)
    assert "provider-supported sizes" in str(excinfo.value)


def test_no_universal_720_1280_assumption_remains_for_the_validated_model():
    # The old global default map is gone from the config module entirely.
    import app.orchestrator.alibaba_video_config as config

    assert not hasattr(config, "DEFAULT_VIDEO_SIZE_MAP")
    # And the validated model's own default is the live-validated size.
    caps = model_capabilities(DEFAULT_VIDEO_MODEL)
    assert caps["size_map"]["9:16"] == "1080*1920"


def test_unknown_model_has_no_invented_sizes_and_requires_configuration():
    caps = model_capabilities("totally-unknown-video-model")
    assert "supported_sizes" not in caps
    assert "size_map" not in caps
    size, error = select_video_size("totally-unknown-video-model", "9:16")
    assert size is None
    assert "no declared size" in error
    assert "AVM_ALIBABA_VIDEO_SIZES" in error


def test_unknown_model_with_operator_configuration_uses_it_uncapped():
    # A model WITHOUT declared sizes (no live evidence) accepts the explicit
    # operator mapping — configuration-driven, never invented.
    size, error = select_video_size(
        "totally-unknown-video-model", "9:16", {"9:16": "720*1280"}
    )
    assert error is None
    assert size == "720*1280"


def test_family_durations_apply_and_model_entry_can_override():
    assert model_capabilities("wan2.2-t2v-plus")["durations"] == (5, 10)
    assert model_capabilities("wan2.5-t2v-future")["durations"] == (5, 10)
    assert model_capabilities("happyhorse-x")["durations"] == (5, 10)


def test_future_model_entry_extends_without_rewriting_the_selector(tmp_path):
    # Simulate adding a future model capability entry: only the TABLE gains
    # an entry; select_video_size is untouched and serves it identically.
    from app.orchestrator import alibaba_video_config as config

    original = dict(config._MODEL_CAPABILITIES)
    try:
        config._MODEL_CAPABILITIES["future-model-a"] = {
            "supported_sizes": ("640*640",),
            "size_map": {"9:16": "640*640"},
        }
        caps = model_capabilities("future-model-a")
        assert caps["supported_sizes"] == ("640*640",)
        size, error = select_video_size("future-model-a", "9:16")
        assert error is None
        assert size == "640*640"
        # Unsupported aspect on the future model still fails truthfully.
        size2, error2 = select_video_size("future-model-a", "1:1")
        assert size2 is None and "no declared size" in error2
    finally:
        config._MODEL_CAPABILITIES.clear()
        config._MODEL_CAPABILITIES.update(original)


def test_invalid_size_never_reaches_the_billable_submission(tmp_path):
    client = FakeDashScope()
    provider = AlibabaVideoVisualProvider(
        tmp_path, model="wan2.2-t2v-plus", api_key="sk-ws-abc123",
        http_client=client, size_map={"9:16": "999*999"},
    )
    result = provider.generate_scene(make_request(duration=5))
    assert not result.success
    assert client.submit_calls == [], "no billable call may be made"
    assert result.verification["failure_category"] in {
        "capability", "invalid_request"
    }
    assert "999*999" in (result.error or "")

