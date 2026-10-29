"""Real Standard narration with hermetic adapters; no provider/network calls."""

from __future__ import annotations

import json

import pytest

from app.content.capability_router import assess_model_for_job, clear_quota_blockers, record_quota_blocker
from app.content.model_registry import (
    JOB_SOURCE_TRANSCRIPT_RECAP, JOB_SOURCE_VIDEO_RECAP, MODEL_REGISTRY,
    CanonicalModel, InputModality, ModelCapabilities, ProviderBinding,
)
from app.production.narration import build_grounded_narration_plan, resolve_summary_model, validate_narration_artifact
from app.production.storage import load_narration_artifact, save_narration_artifact
from tests.track9_narration_fakes import (
    StubNarrationProvider, install_standard_provider, standard_evidence, standard_response,
)


PID = "prd_20261024_standard_real_001"
UID = "unit-001"


@pytest.fixture(autouse=True)
def forbid_mock_planner(monkeypatch):
    clear_quota_blockers()

    def forbidden(*args, **kwargs):
        raise AssertionError("real Standard execution must never use a mock/Pro planner")

    monkeypatch.setattr("app.production.narration_mock.mock_grounded_plan", forbidden)
    monkeypatch.setattr("app.production.narration_pro.build_pro_multimodal_plan", forbidden)
    yield
    clear_quota_blockers()


def build(provider, *, target_duration=30.0, asset=None, transcript=None):
    default_asset, default_transcript = standard_evidence()
    return build_grounded_narration_plan(
        PID, UID, asset or default_asset, transcript or default_transcript,
        target_duration_seconds=target_duration, summary_mode="standard", mock_mode=False,
        llm_provider_override=provider,
    )


@pytest.mark.parametrize("model_id", [None, "qwen-3.8-flash", "summary-standard-qwen-flash", "gemini-3-flash-preview"])
def test_real_standard_resolves_exact_binding_and_persists(monkeypatch, tmp_path, model_id):
    asset, transcript = standard_evidence()
    provider, factory_calls, binding = install_standard_provider(monkeypatch, model_id or "qwen-3.8-flash")
    artifact = build_grounded_narration_plan(
        PID, UID, asset, transcript, target_duration_seconds=30.0,
        canonical_model_id=model_id, summary_mode="standard", mock_mode=False,
    )
    assert factory_calls == [binding.provider_model_id]
    assert provider.json_calls == len(provider.calls) == 1
    assert artifact.narration_text == " ".join(beat["narration_text"] for beat in standard_response()["beats"])
    assert artifact.transcript_refs == ["1", "9"]
    assert [beat.order for beat in artifact.beats] == [1, 2]
    assert [ref.transcript_segment_ids for ref in artifact.source_refs] == [[1], [9]]
    assert "Summary beat" not in artifact.narration_text
    assert len(artifact.narration_text.split()) <= 90
    assert sum(ref.end_seconds - ref.start_seconds for ref in artifact.source_refs) < asset.duration
    validate_narration_artifact(artifact, source_asset=asset, source_transcript=transcript)
    path = save_narration_artifact(artifact, production_id=PID, unit_id=UID, root=tmp_path, source_asset=asset, source_transcript=transcript)
    assert path.is_file()
    assert load_narration_artifact(PID, UID, root=tmp_path).model_dump() == artifact.model_dump()
    prompt = provider.calls[0]["prompt"]
    evidence = json.loads(prompt.split("evidence:\n", 1)[1].split("\nRequired JSON schema:", 1)[0])
    assert evidence["asset_id"] == asset.asset_id
    assert [segment["id"] for segment in evidence["transcript_segments"]] == list(range(12))
    assert evidence["max_narration_words"] == 90


def test_provider_override_with_mock_false_uses_real_json_response():
    provider = StubNarrationProvider()
    artifact = build(provider)
    assert provider.json_calls == 1
    assert artifact.summary == standard_response()["summary"]
    assert artifact.beats[0].title == "Preparation"


def test_generate_only_adapter_and_fenced_json_are_supported():
    delegate = StubNarrationProvider(raw="```json\n" + json.dumps(standard_response()) + "\n```")

    class TextAdapter:
        generate = delegate.generate
        is_available = delegate.is_available

    artifact = build(TextAdapter())
    assert len(delegate.calls) == 1
    assert artifact.beats[1].title == "Discovery"


def test_standard_job_is_text_json_not_pro_visual(monkeypatch):
    install_standard_provider(monkeypatch)
    standard = assess_model_for_job("qwen-3.8-flash", JOB_SOURCE_TRANSCRIPT_RECAP)
    pro = assess_model_for_job("qwen-3.8-flash", JOB_SOURCE_VIDEO_RECAP)
    assert standard.capability_ok and standard.eligible
    assert standard.video_understanding_method is None
    assert standard.eligible_routes()[0].input_modality == InputModality.TEXT
    assert not pro.capability_ok
    assert resolve_summary_model("standard", "gemini-3-flash-preview") == "gemini-3-flash-preview"
    assert resolve_summary_model("standard", "summary-standard-qwen-flash") == "qwen-3.8-flash"


@pytest.mark.parametrize("caps", [ModelCapabilities(text=False), ModelCapabilities(json_=False)])
def test_standard_rejects_missing_text_json_capability_before_generation(monkeypatch, caps):
    model = CanonicalModel(id="standard-incapable", display_name="Incapable", family="fixture", capabilities=caps)
    monkeypatch.setitem(MODEL_REGISTRY, model.id, model)
    with pytest.raises(ValueError, match="cannot satisfy"):
        resolve_summary_model("standard", model.id)


def test_visual_capable_model_standard_route_only_needs_text(monkeypatch):
    model = CanonicalModel(
        id="standard-text-route", display_name="Text Route", family="fixture",
        capabilities=ModelCapabilities(vision=True),
        bindings=(ProviderBinding(provider="mock", provider_model_id="fixture-text", supported_input_modalities=(InputModality.TEXT,)),),
    )
    monkeypatch.setitem(MODEL_REGISTRY, model.id, model)
    standard = assess_model_for_job(model.id, JOB_SOURCE_TRANSCRIPT_RECAP)
    pro = assess_model_for_job(model.id, JOB_SOURCE_VIDEO_RECAP)
    assert standard.eligible and standard.eligible_routes()[0].input_modality == InputModality.TEXT
    assert not pro.eligible


def test_real_standard_skips_ineligible_binding_without_cross_model_fallback(monkeypatch):
    from app.content import provider_registry

    model = CanonicalModel(
        id="standard-two-routes", display_name="Two Routes", family="fixture",
        bindings=(
            ProviderBinding(provider="standard-blocked", provider_model_id="blocked-wire", config_keys=("STANDARD_UNSET_KEY",)),
            ProviderBinding(provider="standard-ready", provider_model_id="ready-wire"),
        ),
    )
    monkeypatch.delenv("STANDARD_UNSET_KEY", raising=False)
    monkeypatch.setitem(MODEL_REGISTRY, model.id, model)
    provider = StubNarrationProvider(name="standard-ready", model="ready-wire")

    def forbidden(*args):
        raise AssertionError("ineligible binding must not be instantiated")

    monkeypatch.setitem(provider_registry._REGISTRY, "standard-blocked", forbidden)
    monkeypatch.setitem(provider_registry._REGISTRY, "standard-ready", lambda wire: provider)
    asset, transcript = standard_evidence()
    artifact = build_grounded_narration_plan(
        PID, UID, asset, transcript, target_duration_seconds=30.0,
        canonical_model_id=model.id, summary_mode="standard", mock_mode=False,
    )
    assert artifact.beats and len(provider.calls) == 1


def test_quota_blocked_route_never_invokes_adapter(monkeypatch):
    provider, factory_calls, binding = install_standard_provider(monkeypatch)
    record_quota_blocker(binding.provider, binding.provider_model_id, "synthetic quota evidence")
    asset, transcript = standard_evidence()
    with pytest.raises(RuntimeError, match="no eligible route"):
        build_grounded_narration_plan(PID, UID, asset, transcript, target_duration_seconds=30.0,
                                     canonical_model_id="qwen-3.8-flash", mock_mode=False)
    assert factory_calls == provider.calls == []


@pytest.mark.parametrize("raw", ["", "not JSON", "[]", "{}", '{"summary":"s","beats":[]}'])
def test_invalid_json_never_returns_mock_or_retries(raw):
    provider = StubNarrationProvider(raw=raw)
    with pytest.raises(ValueError):
        build(provider)
    assert provider.json_calls == len(provider.calls) == 1


@pytest.mark.parametrize("change", [
    {"asset_id": "src-999"}, {"end_seconds": 121}, {"transcript_segment_ids": [999]},
    {"transcript_segment_ids": [-1]}, {"transcript_segment_ids": [True]},
    {"transcript_segment_ids": ["1"]}, {"transcript_segment_ids": []},
    {"start_seconds": 70, "end_seconds": 80},
    {"source_segment_id": "seg-999"},
])
def test_invalid_grounding_is_rejected_without_retry(change):
    data = standard_response()
    data["beats"][0]["source_refs"][0].update(change)
    provider = StubNarrationProvider(data=data)
    with pytest.raises(ValueError):
        build(provider)
    assert len(provider.calls) == 1


def test_nonconsecutive_beats_are_rejected():
    data = standard_response()
    data["beats"][1]["order"] = 1
    with pytest.raises(ValueError, match="consecutive"):
        build(StubNarrationProvider(data=data))


def test_narration_word_budget_is_enforced():
    data = standard_response()
    data["beats"][0]["narration_text"] = "word " * 100
    with pytest.raises(ValueError, match="word budget"):
        build(StubNarrationProvider(data=data))


def test_transcript_copy_is_not_a_summary():
    asset, transcript = standard_evidence()
    data = standard_response()
    data["beats"] = data["beats"][:1]
    data["beats"][0]["narration_text"] = " ".join(segment.text for segment in transcript.segments)
    with pytest.raises(ValueError, match="transcript copy"):
        build(StubNarrationProvider(data=data), target_duration=120.0, asset=asset, transcript=transcript)


def test_condensed_plan_cannot_select_the_whole_source_timeline():
    data = standard_response()
    data["beats"][0]["source_refs"][0].update(start_seconds=0, end_seconds=60)
    data["beats"][1]["source_refs"][0].update(start_seconds=60, end_seconds=120)
    with pytest.raises(ValueError, match="full source timeline"):
        build(StubNarrationProvider(data=data))


def test_provider_error_is_not_retried_or_replaced():
    provider = StubNarrationProvider(error=TypeError("synthetic adapter failure"))
    with pytest.raises(TypeError, match="synthetic adapter failure"):
        build(provider)
    assert provider.json_calls == len(provider.calls) == 1


def install_auto_pool(monkeypatch, primary_error=None, *, secondary_error=None, secondary_raw=None):
    from app.content import provider_registry

    candidates = []
    providers = []
    for index, error in enumerate((primary_error, secondary_error)):
        name, wire = f"auto-provider-{index}", f"auto-wire-{index}"
        key = f"AUTO_STANDARD_TEST_KEY_{index}"
        model = CanonicalModel(id=f"auto-model-{index}", display_name=f"Auto Model {index}", family="fixture",
            capabilities=ModelCapabilities(text=True, json_=True),
            bindings=(ProviderBinding(provider=name, provider_model_id=wire, config_keys=(key,),
                supported_input_modalities=(InputModality.TEXT,)),))
        monkeypatch.setitem(MODEL_REGISTRY, model.id, model)
        monkeypatch.setenv(key, "synthetic-test-only")
        provider = StubNarrationProvider(name=name, model=wire, error=error,
                                        **({"raw": secondary_raw} if index == 1 and secondary_raw is not None else {}))
        monkeypatch.setitem(provider_registry._REGISTRY, name, lambda wire_id, provider=provider: provider)
        candidates.append(model); providers.append(provider)
    monkeypatch.setattr("app.content.model_registry.list_models", lambda: tuple(candidates))
    return candidates, providers


def build_auto(**kwargs):
    asset, transcript = standard_evidence()
    return build_grounded_narration_plan(PID, UID, asset, transcript, target_duration_seconds=30,
        summary_mode="standard", summary_understanding_mode="auto", mock_mode=False, **kwargs)


@pytest.mark.parametrize("failure", ["503", "busy", "network", "timeout", "requests_connection", "http_status"])
def test_standard_auto_failover_tries_next_eligible_configured_model_after_transient_error(monkeypatch, failure):
    import requests
    from app.content.providers.codecraft import CodeCraftServerBusyError
    from app.content.exceptions import LLMConnectionError

    failures = {
        "503": RuntimeError("HTTP 503: service being upgraded"),
        "busy": CodeCraftServerBusyError("fixture", "service being upgraded"),
        "network": LLMConnectionError("fixture", "connection failed"),
        "timeout": TimeoutError("timed out"),
        "requests_connection": requests.exceptions.ConnectionError("connection refused"),
        "http_status": requests.exceptions.HTTPError(response=type("Response", (), {"status_code": 503})()),
    }
    candidates, providers = install_auto_pool(monkeypatch, failures[failure])
    artifact = build_auto()
    assert [len(provider.calls) for provider in providers] == [1, 1]
    assert artifact.summary == standard_response()["summary"]
    assert artifact.summary_understanding_model_id == candidates[1].id
    assert artifact.summary_selection_mode == "auto"
    assert artifact.summary_failover_used is True
    validate_narration_artifact(artifact, source_asset=standard_evidence()[0], source_transcript=standard_evidence()[1])
    assert providers[0].calls[0] == providers[1].calls[0], "each candidate must receive the same grounded request"


def test_standard_auto_first_success_never_calls_a_backup_model(monkeypatch):
    candidates, providers = install_auto_pool(monkeypatch)
    artifact = build_auto()
    assert [len(provider.calls) for provider in providers] == [1, 0]
    assert artifact.summary_understanding_model_id == candidates[0].id
    assert artifact.summary_failover_used is False


def test_standard_auto_skips_unconfigured_primary_instead_of_calling_dead_route(monkeypatch):
    candidates, providers = install_auto_pool(monkeypatch)
    monkeypatch.delenv("AUTO_STANDARD_TEST_KEY_0")
    artifact = build_auto()
    assert [len(provider.calls) for provider in providers] == [0, 1]
    assert artifact.summary_understanding_model_id == candidates[1].id


@pytest.mark.parametrize("selection", ["explicit", "manual", None, "auto"])
def test_locked_standard_model_never_cross_model_fails_over_even_on_503(monkeypatch, selection):
    from app.content.providers.codecraft import CodeCraftServerBusyError
    candidates, providers = install_auto_pool(monkeypatch, CodeCraftServerBusyError("fixture", "HTTP 503"))
    with pytest.raises(CodeCraftServerBusyError):
        asset, transcript = standard_evidence()
        build_grounded_narration_plan(PID, UID, asset, transcript, target_duration_seconds=30,
            summary_mode="standard", summary_understanding_mode=selection,
            summary_understanding_model_id=candidates[0].id, mock_mode=False)
    assert [len(provider.calls) for provider in providers] == [1, 0]


@pytest.mark.parametrize("failure", ["auth", "quota", "bad_json", "bad_grounding", "invalid_request", "bug"])
def test_standard_auto_never_hides_nontransient_or_grounding_failures(monkeypatch, failure):
    from app.content.exceptions import LLMAuthenticationError, LLMQuotaError
    from app.content.providers.codecraft import CodeCraftInvalidRequestError
    error = {"auth": LLMAuthenticationError("fixture"), "quota": LLMQuotaError("fixture"),
             "invalid_request": CodeCraftInvalidRequestError("invalid request"), "bug": TypeError("adapter bug")}.get(failure)
    _, providers = install_auto_pool(monkeypatch, error)
    if failure == "bad_json": providers[0].raw = "not json"
    if failure == "bad_grounding":
        data = standard_response(); data["beats"][0]["source_refs"][0]["transcript_segment_ids"] = [999]
        providers[0].raw = json.dumps(data)
    with pytest.raises(Exception):
        build_auto()
    assert [len(provider.calls) for provider in providers] == [1, 0]


def test_standard_auto_all_transient_failures_are_bounded_actionable_and_never_persist(monkeypatch, tmp_path):
    _, providers = install_auto_pool(monkeypatch, RuntimeError("HTTP 503: secret-fixture must not reach error"),
                                     secondary_error=ConnectionError("private transport detail"))
    with pytest.raises(RuntimeError, match="eligible.*temporarily unavailable") as error:
        build_auto(root=tmp_path)
    assert [len(provider.calls) for provider in providers] == [1, 1]
    assert "secret-fixture" not in str(error.value) and "private transport" not in str(error.value)
    assert not list(tmp_path.rglob("narration.json"))


def test_explicit_test_adapter_is_not_replaced_by_auto_failover(monkeypatch):
    _, providers = install_auto_pool(monkeypatch)
    override = StubNarrationProvider(error=RuntimeError("HTTP 503"))
    with pytest.raises(RuntimeError, match="HTTP 503"):
        build_auto(llm_provider_override=override)
    assert len(override.calls) == 1
    assert [len(provider.calls) for provider in providers] == [0, 0]


def test_standard_auto_can_fail_over_to_next_route_of_same_canonical_model(monkeypatch):
    from app.content import provider_registry
    candidates, providers = install_auto_pool(monkeypatch, RuntimeError("HTTP 503"))
    first = candidates[0]
    second_binding = candidates[1].bindings[0]
    model = first.model_copy(update={"bindings": (*first.bindings, second_binding)})
    monkeypatch.setitem(MODEL_REGISTRY, model.id, model)
    monkeypatch.setattr("app.content.model_registry.list_models", lambda: (model,))
    artifact = build_auto()
    assert [len(provider.calls) for provider in providers] == [1, 1]
    assert artifact.summary_understanding_model_id == first.id
    assert artifact.summary_failover_used is True


def test_codecraft_runtime_quarantine_wins_over_key_presence_without_changing_catalogue(monkeypatch):
    from app.content.providers.codecraft import CodeCraftProvider
    from app.web.server import _ai_summary_projection
    monkeypatch.setenv("CODECRAFT_API_KEY", "synthetic-key-not-a-real-secret")
    monkeypatch.setenv("CODECRAFT_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AVM_ENABLE_CODECRAFT", "0")
    assessment = assess_model_for_job("qwen-3.8-flash", JOB_SOURCE_TRANSCRIPT_RECAP)
    assert assessment.capability_ok and not assessment.eligible
    assert "runtime_quarantined" in assessment.routes[0].reasons
    assert CodeCraftProvider().is_available() is False
    entry = next(model for model in _ai_summary_projection()["understanding_models"] if model["id"] == "qwen-3.8-flash")
    assert entry["configured"] is False and entry["status"] == "not_configured"
    monkeypatch.setenv("AVM_ENABLE_CODECRAFT", "1")
    assert assess_model_for_job("qwen-3.8-flash", JOB_SOURCE_TRANSCRIPT_RECAP).eligible
    assert CodeCraftProvider().is_available() is True


def test_quarantined_codecraft_is_not_instantiated_or_called_by_standard_auto(monkeypatch):
    from app.content import provider_registry
    from app.content.model_registry import get_model
    qwen, gemini = get_model("qwen-3.8-flash"), get_model("gemini-3-flash-preview")
    monkeypatch.setattr("app.content.model_registry.list_models", lambda: (qwen, gemini))
    monkeypatch.setenv("AVM_ENABLE_CODECRAFT", "0")
    monkeypatch.setenv("CODECRAFT_API_KEY", "synthetic-key")
    monkeypatch.setenv("CODECRAFT_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setitem(provider_registry._REGISTRY, "codecraft", lambda wire: pytest.fail("quarantined CodeCraft must never be instantiated"))
    provider = StubNarrationProvider(name="gemini", model="gemini-3-flash-preview")
    monkeypatch.setitem(provider_registry._REGISTRY, "gemini", lambda wire: provider)
    artifact = build_auto()
    assert len(provider.calls) == 1 and artifact.summary_understanding_model_id == gemini.id