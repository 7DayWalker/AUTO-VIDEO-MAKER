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
        build_grounded_narration_plan(PID, UID, asset, transcript, target_duration_seconds=30.0, mock_mode=False)
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