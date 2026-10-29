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
    {"transcript_segment_ids": [1.5]}, {"transcript_segment_ids": []},
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


@pytest.mark.parametrize("failure", ["auth", "quota", "bad_json", "bad_grounding", "unsupported_model", "truncation"])
def test_standard_auto_fails_over_on_provider_output_and_access_failures(monkeypatch, failure):
    from app.content.exceptions import LLMAuthenticationError, LLMQuotaError, LLMModelUnavailableError, LLMOutputTruncatedError
    error = {"auth": LLMAuthenticationError("fixture"), "quota": LLMQuotaError("fixture"),
             "unsupported_model": LLMModelUnavailableError(), "truncation": LLMOutputTruncatedError()}.get(failure)
    candidates, providers = install_auto_pool(monkeypatch, error)
    if failure == "bad_json": providers[0].raw = "not json"
    if failure == "bad_grounding":
        data = standard_response(); data["beats"][0]["source_refs"][0]["transcript_segment_ids"] = [999]
        providers[0].raw = json.dumps(data)
    artifact = build_auto()
    assert [len(provider.calls) for provider in providers] == [2 if failure in {"bad_json", "bad_grounding", "truncation"} else 1, 1]
    assert artifact.summary_understanding_model_id == candidates[1].id
    assert artifact.summary_failover_used is True


@pytest.mark.parametrize("failure", ["invalid_request", "bug", "invalid_source", "moderation", "credential_class"])
def test_standard_auto_never_hides_local_or_policy_failures(monkeypatch, failure):
    from app.content.providers.codecraft import CodeCraftInvalidRequestError
    from app.content.providers.alibaba import AlibabaContentModerationError, AlibabaCredentialClassError
    error = {"invalid_request": CodeCraftInvalidRequestError("invalid request"), "bug": TypeError("adapter bug"),
             "invalid_source": ValueError("local evidence is invalid"),
             "moderation": AlibabaContentModerationError("policy rejection"),
             "credential_class": AlibabaCredentialClassError()}[failure]
    _, providers = install_auto_pool(monkeypatch, error)
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


@pytest.mark.parametrize("wrapper", [
    lambda data: "Here is the recap:\n```JSON\n" + json.dumps(data) + "\n```\nEnd of recap.",
    lambda data: "[recap follows] {not json}\n" + json.dumps(data) + "\nThanks.",
    lambda data: json.dumps(data["beats"]),
    lambda data: "\ufeff```json\n" + json.dumps(data["beats"]) + "\n```",
])
def test_real_standard_accepts_wrapped_objects_and_top_level_beat_arrays(wrapper):
    data = standard_response()
    data["beats"][0]["narration_text"] = 'A discovery includes a "quoted [clue]" and a {symbol}.'
    artifact = build(StubNarrationProvider(raw=wrapper(data)))
    assert artifact.beats[0].narration_text == data["beats"][0]["narration_text"]
    assert artifact.transcript_refs == ["1", "9"]


@pytest.mark.parametrize("raw", [
    '{"summary":"s","beats":' + json.dumps(standard_response()["beats"]),
    json.dumps(standard_response())[:-8],
    '```json\n{"beats":[{"text":"unfinished',
])
def test_partial_outer_json_is_never_salvaged_as_a_complete_nested_array(raw):
    with pytest.raises(ValueError, match="complete JSON"):
        build(StubNarrationProvider(raw=raw))


@pytest.mark.parametrize("field", ["segment_ids", "source_segment_ids", "transcript_segment_ids", "transcript_refs"])
def test_equivalent_beat_fields_are_normalized_to_canonical_references(field):
    provider = StubNarrationProvider(data={"beats": [
        {"text": "Careful preparations establish the goal.", field: ["1"], "timestamps": ["00:10", "00:20"]},
        {"narration": "A discovery changes the outcome.", field: [9]},
    ]})
    artifact = build(provider)
    assert [beat.order for beat in artifact.beats] == [1, 2]
    assert artifact.transcript_refs == ["1", "9"]
    assert [(ref.start_seconds, ref.end_seconds) for ref in artifact.source_refs] == [(10, 20), (90, 100)]
    assert all(ref.asset_id == "src-001" for ref in artifact.source_refs)


def test_source_segment_aliases_resolve_only_supplied_grounded_ranges():
    from app.production.models import SourceSegment
    from app.production.narration_real import ai_grounded_plan
    asset, transcript = standard_evidence()
    segment = SourceSegment(segment_id="seg-001", asset_id=asset.asset_id, start_seconds=12, end_seconds=18,
                            transcript_segment_ids=[1])
    provider = StubNarrationProvider(data={"beats": [{"text": "Preparation establishes the goal.", "source_segment_ids": ["seg-001"]}]})
    artifact = ai_grounded_plan(PID, UID, asset, transcript, target_duration_seconds=30, prov=provider, source_segments=[segment])
    ref = artifact.source_refs[0]
    assert ref.source_segment_id == "seg-001" and ref.transcript_segment_ids == [1]
    assert (ref.start_seconds, ref.end_seconds) == (12, 18)


def test_timestamps_can_resolve_real_overlap_but_cannot_invent_evidence():
    data = {"beats": [{"text": "Preparation establishes the goal.", "start": "00:00:10", "end": "00:00:20"}]}
    assert build(StubNarrationProvider(data=data)).transcript_refs == ["1"]
    data["beats"][0]["start"] = "00:61:10"
    with pytest.raises(ValueError, match="timecode"):
        build(StubNarrationProvider(data=data))


def test_real_adapters_can_return_decoded_objects_or_raw_json():
    from app.production.narration_real import _call_json
    for response in (standard_response(), standard_response()["beats"], json.dumps(standard_response())):
        class Adapter:
            def generate_json(self, **kwargs):
                return response
        assert len(_call_json(Adapter(), "system", "prompt")["beats"]) == 2


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("failure", ["bad_json", "bad_grounding", "unsupported_wire"])
def test_execution_failover_tries_another_route_of_same_identity(monkeypatch, explicit, failure):
    from app.content.exceptions import LLMModelUnavailableError
    candidates, providers = install_auto_pool(monkeypatch, LLMModelUnavailableError() if failure == "unsupported_wire" else None)
    first = candidates[0]
    model = first.model_copy(update={"bindings": (*first.bindings, candidates[1].bindings[0])})
    monkeypatch.setitem(MODEL_REGISTRY, model.id, model)
    if failure == "bad_json": providers[0].raw = "```json\n{unfinished"
    if failure == "bad_grounding":
        data = standard_response(); data["beats"][0]["source_refs"][0]["asset_id"] = "src-999"
        providers[0].raw = json.dumps(data)
    artifact = build_auto(**({"canonical_model_id": model.id} if explicit else {}))
    assert [len(provider.calls) for provider in providers] == [1 if failure == "unsupported_wire" else 2, 1]
    assert artifact.summary_understanding_model_id == model.id
    assert artifact.summary_selection_mode == ("explicit" if explicit else "auto")
    assert artifact.summary_failover_used is True


def test_all_output_failures_are_bounded_and_never_persist(monkeypatch, tmp_path):
    _, providers = install_auto_pool(monkeypatch, secondary_raw="private-source invalid JSON")
    providers[0].raw = '{"beats":[{"text":"private-source"'
    with pytest.raises(RuntimeError, match="unusable narration") as error:
        build_auto(root=tmp_path)
    assert [len(provider.calls) for provider in providers] == [2, 2]
    assert "private-source" not in str(error.value)
    assert not list(tmp_path.rglob("narration.json"))


def test_live_fallback_models_keep_true_identities_and_configured_wire_ids():
    from app.content.model_registry import get_model
    gemini = get_model("gemini-2.5-flash")
    assert {(binding.provider, binding.provider_model_id) for binding in gemini.bindings} == {
        ("gemini", "gemini-2.5-flash"), ("openrouter", "google/gemini-2.5-flash"),
    }
    assert get_model("gemma-4-26b-a4b-it").bindings[0].provider_model_id == "google/gemma-4-26b-a4b-it:free"
    assert get_model("qwen-plus").bindings[0].provider_model_id == "qwen-plus"
    assert all(binding.provider_model_id == "gemini-3-flash-preview" for binding in get_model("gemini-3-flash-preview").bindings)


class WindowNarrationProvider(StubNarrationProvider):
    """Deterministic model double driven by the exact evidence on each call."""

    def generate(self, prompt, system_prompt=None, **kwargs):
        evidence = json.loads(prompt.split("evidence:\n", 1)[1].split("\nRequired JSON schema:", 1)[0])
        if "transcript_segments" in evidence:
            row = evidence["transcript_segments"][0]
            data = {"beats": [{"text": "A source-grounded discovery changes the outcome.", "segment_ids": [row["id"]]}]}
        else:
            data = evidence["candidate_plans"][0]
        self.raw = json.dumps(data)
        return super().generate(prompt, system_prompt, **kwargs)


@pytest.mark.parametrize("count,text", [(900, "A detailed observed source event. " * 8), (8, "The source shows a discovery. " * 400),
                                         (90, "发现龙的故事，事件和证据。" * 35)])
def test_long_transcript_windows_preserve_all_text_ids_and_bounded_prompts(count, text):
    from app.production.models import TranscriptSegment
    from app.production.narration_real import ai_grounded_plan
    asset, transcript = standard_evidence(duration=count * 5.0)
    transcript.segments = [TranscriptSegment(text=f"{index}: {text}", start=index * 5, end=(index + 1) * 5) for index in range(count)]
    provider = WindowNarrationProvider()
    artifact = ai_grounded_plan(PID, UID, asset, transcript, target_duration_seconds=30, prov=provider)
    recovered = {index: [] for index in range(count)}
    merges = 0
    for call in provider.calls:
        assert len((call["system_prompt"] + call["prompt"]).encode("utf-8")) + call["max_tokens"] + 256 <= 32768
        assert call["max_tokens"] == 8192
        evidence = json.loads(call["prompt"].split("evidence:\n", 1)[1].split("\nRequired JSON schema:", 1)[0])
        if "candidate_plans" in evidence:
            merges += 1
            assert all(plan["beats"][0]["source_refs"] for plan in evidence["candidate_plans"])
        for row in evidence.get("transcript_segments", []):
            recovered[row["id"]].append(row["text"])
    assert merges > 0
    assert all(" ".join(recovered[index]).split() == segment.text.split() for index, segment in enumerate(transcript.segments))
    validate_narration_artifact(artifact, source_asset=asset, source_transcript=transcript)


def test_half_hour_movie_with_short_dialogue_is_compacted_without_dropping_any_segment():
    from app.production.models import TranscriptSegment
    from app.production.narration_real import ai_grounded_plan
    asset, transcript = standard_evidence(duration=1805)
    transcript.segments = [TranscriptSegment(text=f"Observed event {index}.", start=index * 4.9, end=(index + 1) * 4.9) for index in range(365)]
    provider = WindowNarrationProvider()
    ai_grounded_plan(PID, UID, asset, transcript, target_duration_seconds=30, prov=provider)
    assert len(provider.calls) == 1
    evidence = json.loads(provider.calls[0]["prompt"].split("evidence:\n", 1)[1].split("\nRequired JSON schema:", 1)[0])
    assert [row["id"] for row in evidence["transcript_segments"]] == list(range(365))
    assert all(row["text"] == transcript.segments[row["id"]].text for row in evidence["transcript_segments"])


def test_window_model_cannot_cite_transcript_ids_it_was_not_given():
    from app.production.models import TranscriptSegment
    from app.production.narration_real import ai_grounded_plan
    asset, transcript = standard_evidence(duration=1800)
    transcript.segments = [TranscriptSegment(text="Evidence " * 40, start=index * 5, end=(index + 1) * 5) for index in range(360)]
    data = standard_response()
    data["beats"][0]["source_refs"][0].update(transcript_segment_ids=[359], start_seconds=1795, end_seconds=1800)
    with pytest.raises(ValueError, match="supplied evidence"):
        ai_grounded_plan(PID, UID, asset, transcript, target_duration_seconds=30, prov=StubNarrationProvider(data=data))


@pytest.mark.parametrize("provider_name", ["gemini", "openrouter", "alibaba"])
def test_real_provider_adapters_invoke_native_json_http_for_arbitrary_narration(monkeypatch, provider_name):
    from app.content.providers.gemini import GeminiProvider
    from app.content.providers.openrouter import OpenRouterProvider
    from app.content.providers.alibaba import AlibabaProvider
    from types import SimpleNamespace
    classes = {"gemini": GeminiProvider, "openrouter": OpenRouterProvider, "alibaba": AlibabaProvider}
    model = {"gemini": "gemini-3-flash-preview", "openrouter": "openai/gpt-4o", "alibaba": "qwen-plus"}[provider_name]
    raw = json.dumps(standard_response())
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        if provider_name == "gemini":
            data = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"text": "private reasoning must never become JSON", "thought": True},
                {"text": raw[:80]}, {"text": raw[80:]},
            ]}}]}
        else:
            data = {"choices": [{"finish_reason": "stop", "message": {"content": [
                {"type": "text", "text": raw[:80]}, {"type": "text", "text": raw[80:]},
            ]}}]}
        return SimpleNamespace(status_code=200, json=lambda: data)

    monkeypatch.setattr("requests.post", post)
    provider = classes[provider_name](api_key="synthetic-http-boundary-key", model=model)
    artifact = build(provider)
    assert len(calls) == 1
    url, kwargs = calls[0]
    payload = kwargs["json"]
    if provider_name == "gemini":
        assert url.endswith("/models/gemini-3-flash-preview:generateContent")
        assert "key=" not in url and "synthetic-http-boundary-key" not in url
        assert payload["generationConfig"]["responseMimeType"] == "application/json"
        assert payload["generationConfig"]["maxOutputTokens"] == 8192
        assert payload["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    else:
        assert url.endswith("/chat/completions")
        assert payload["model"] == model
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["max_tokens"] == 8192
    assert artifact.narration_text == " ".join(beat["narration_text"] for beat in standard_response()["beats"])


@pytest.mark.parametrize("provider_name", ["gemini", "openrouter", "alibaba"])
def test_provider_truncation_is_classified_before_parsing_even_valid_looking_json(monkeypatch, provider_name):
    from app.content.providers.gemini import GeminiProvider
    from app.content.providers.openrouter import OpenRouterProvider
    from app.content.providers.alibaba import AlibabaProvider
    from app.content.exceptions import LLMOutputTruncatedError
    from types import SimpleNamespace
    raw = json.dumps(standard_response())
    data = ({"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": raw}]}}]}
            if provider_name == "gemini" else {"choices": [{"finish_reason": "length", "message": {"content": raw}}]})
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: SimpleNamespace(status_code=200, json=lambda: data))
    provider = {"gemini": GeminiProvider, "openrouter": OpenRouterProvider, "alibaba": AlibabaProvider}[provider_name](api_key="synthetic-key", model="fixture-wire")
    with pytest.raises(LLMOutputTruncatedError):
        build(provider)


@pytest.mark.parametrize("provider_name", ["gemini", "openrouter", "alibaba"])
def test_unsupported_wire_model_is_a_route_failure_not_an_avm_request_error(monkeypatch, provider_name):
    from app.content.providers.gemini import GeminiProvider
    from app.content.providers.openrouter import OpenRouterProvider
    from app.content.providers.alibaba import AlibabaProvider
    from app.content.exceptions import LLMModelUnavailableError
    from app.production.narration_real import standard_transient_failure_category
    from types import SimpleNamespace
    data = {"error": {"message": "Model not found", "code": "ModelNotFound"}}
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: SimpleNamespace(status_code=404, text=json.dumps(data), json=lambda: data))
    provider = {"gemini": GeminiProvider, "openrouter": OpenRouterProvider, "alibaba": AlibabaProvider}[provider_name](api_key="synthetic-key", model="rejected-preview")
    with pytest.raises(LLMModelUnavailableError) as error:
        build(provider)
    assert standard_transient_failure_category(error.value) == "capability"


def test_gemini_stable_json_disables_shared_thinking_budget(monkeypatch):
    from app.content.providers.gemini import GeminiProvider
    from types import SimpleNamespace
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return SimpleNamespace(status_code=200, json=lambda: {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(standard_response())}]}}]})
    monkeypatch.setattr("requests.post", post)
    build(GeminiProvider(api_key="synthetic-key", model="gemini-2.5-flash"))
    assert calls[0]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


def test_bounded_output_repair_reuses_only_the_failed_window(monkeypatch):
    from app.production.narration_real import ai_grounded_plan
    asset, transcript = standard_evidence()
    class RepairProvider(StubNarrationProvider):
        def generate(self, *args, **kwargs):
            self.raw = '```json\n{"beats":[' if not self.calls else json.dumps(standard_response())
            return super().generate(*args, **kwargs)
    provider = RepairProvider()
    artifact = ai_grounded_plan(PID, UID, asset, transcript, target_duration_seconds=30, prov=provider, output_attempts=2)
    assert len(provider.calls) == 2 and len(artifact.beats) == 2
    assert "Previous output failed validation" in provider.calls[1]["prompt"]
    assert "```json" not in provider.calls[1]["prompt"]


def test_edge_tts_transport_diagnostics_never_expose_request_tokens(monkeypatch, tmp_path):
    import asyncio
    from app.voice.providers.edge_tts import EdgeTTSProvider
    from app.voice.exceptions import VoiceProviderError
    class HandshakeError(Exception):
        status = 403
    provider = EdgeTTSProvider(max_retries=0)
    provider._edge_tts = object()
    async def failure(*args):
        raise HandshakeError("Invalid response status, url='wss://tts.invalid/speech?token=private-token&key=private-key'")
    monkeypatch.setattr(provider, "_generate_once", failure)
    with pytest.raises(VoiceProviderError) as error:
        asyncio.run(provider.generate("Narration", tmp_path / "voice.mp3", "en-US-GuyNeural"))
    assert "403" in str(error.value) and "Invalid response status" in str(error.value)
    assert "private-token" not in str(error.value) and "private-key" not in str(error.value)
    assert "wss://" not in str(error.value)