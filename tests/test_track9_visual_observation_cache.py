"""Source-bound Stage 1 caching, honest legacy hydration, and vision failover."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from app.content.exceptions import LLMConnectionError, LLMRateLimitError
from app.production.narration_chapters import build_chapter_plan
from app.production.narration_real import cached_visual_observations, observe_with_vision_failover
from app.production.recap_budget import resolve_recap_budget
from app.production.visual_cache import CACHE_FILENAME, hydrate_prior_pro, load_frame_observations, save_frame_observations
from app.production.visual_observation import validate_visual_action_notes
from tests.test_agentrouter_storyteller import Observer, frames
from tests.track9_narration_fakes import ChapterNarrationProvider, story_evidence


def _notes(supplied):
    return [{"frame_id": i, "timestamp_seconds": ts, "action": "The keeper protects the hatchling while hunters approach."}
            for i, (ts, _) in enumerate(supplied)]


def test_stage1_cache_is_independent_of_storyteller_and_narration_length(tmp_path):
    asset, transcript = story_evidence()
    supplied = frames(0, 360, 0)
    path = tmp_path / CACHE_FILENAME
    save_frame_observations(path, asset, transcript, supplied, _notes(supplied), start=0, end=360)
    for model, preset in (("gpt-6.1-sol", "extended"), ("gpt-6-astra", "deep")):
        notes, origin = cached_visual_observations(tmp_path, asset, transcript, supplied, start=0, end=360,
                                                   production_id="prd_cache_test", unit_id="unit-001")
        assert notes and origin == "cache"
        assert all(n["citable"] for n in notes)
    stored = path.read_text()
    assert "gpt-6.1-sol" not in stored and "gpt-6-astra" not in stored and '"preset"' not in stored


@pytest.mark.parametrize("changed", ["source", "transcript", "timestamp"])
def test_cache_rejects_changed_source_dialogue_or_frame_timestamp(tmp_path, changed):
    asset, transcript = story_evidence()
    supplied = frames(0, 360, 0)
    path = tmp_path / CACHE_FILENAME
    save_frame_observations(path, asset, transcript, supplied, _notes(supplied), start=0, end=360)
    if changed == "source": asset = asset.model_copy(update={"sha256": "f" * 64})
    elif changed == "transcript": transcript.segments[0].text = "Changed evidence"
    else: supplied[0] = (supplied[0][0] + 1, supplied[0][1])
    assert load_frame_observations(path, asset, transcript, supplied, start=0, end=360) is None
    notes, _ = cached_visual_observations(tmp_path, asset, transcript, supplied, start=0, end=360,
                                          production_id="prd_cache_test", unit_id="unit-001")
    assert notes is None


def test_reusable_cache_survives_deleting_model_bound_partial_checkpoint(tmp_path):
    asset, transcript = story_evidence()
    checkpoint = tmp_path / "narration_chapters.partial.json"
    first = build_chapter_plan("prd_cache_test", "unit-001", asset, transcript,
        budget=resolve_recap_budget(asset.duration), prov=ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol"),
        visual_observer=Observer(), frame_loader=frames, checkpoint_path=checkpoint)
    assert not checkpoint.exists() and (tmp_path / CACHE_FILENAME).is_file()
    class NoNetwork(Observer):
        def generate_multimodal(self, *args, **kwargs):
            raise AssertionError("Cached observations must not make a vision request")
    second = build_chapter_plan("prd_cache_test", "unit-001", asset, transcript,
        budget=resolve_recap_budget(asset.duration, target_length_preset="deep"),
        prov=ChapterNarrationProvider(name="agentrouter", model="gpt-6-astra"), visual_observer=NoNetwork(),
        frame_loader=frames, checkpoint_path=checkpoint)
    assert first.live_vision_calls > 0 and second.live_vision_calls == 0 and second.stage1_cache_hits == 5
    assert second.chapter_count == 5


def test_prior_pro_hydration_retains_scene_granularity_and_marks_missing_descriptions(tmp_path):
    asset, transcript = story_evidence()
    prior = build_chapter_plan("prd_cache_test", "unit-001", asset, transcript,
        budget=resolve_recap_budget(asset.duration), prov=ChapterNarrationProvider(), frame_loader=frames)
    prior.source_media_path, prior.source_transcript_path = asset.stored_path, transcript.transcript_path
    path = tmp_path / "narration.json"
    path.write_text(prior.model_dump_json())
    supplied = frames(0, 360, 0)
    notes, origin = cached_visual_observations(tmp_path, asset, transcript, supplied, start=0, end=360,
                                               production_id=prior.production_id, unit_id=prior.unit_id)
    assert origin == "prior_pro_artifact" and len(notes) == len(supplied)
    validate_visual_action_notes(notes, supplied)
    assert any(not n["citable"] and n["evidence_kind"] == "unavailable" for n in notes)
    assert all(n["source_beat_orders"] and n["evidence_frame_timestamps"] for n in notes if n["citable"])
    assert "not an individual-frame caption" in next(n["action"] for n in notes if n["citable"])
    assert not hydrate_prior_pro(tmp_path / "wrong.json", path, asset, transcript, supplied, start=0, end=360,
                                production_id="prd_different", unit_id=prior.unit_id)


def test_live_frame_observation_is_not_overwritten_by_legacy_description(tmp_path):
    asset, transcript = story_evidence()
    supplied = frames(0, 360, 0)
    path = tmp_path / CACHE_FILENAME
    save_frame_observations(path, asset, transcript, supplied, _notes(supplied), start=0, end=360)
    legacy = [{**note, "evidence_kind": "prior_pro_scene", "action": "Less detailed prior scene."} for note in _notes(supplied)]
    save_frame_observations(path, asset, transcript, supplied, legacy, start=0, end=360)
    cached = load_frame_observations(path, asset, transcript, supplied, start=0, end=360)
    assert all(n["evidence_kind"] == "live_frame" for n in cached)


def test_gemini_rate_limit_uses_only_admitted_alternate_vision_routes(monkeypatch):
    class RateLimited(Observer):
        def generate_multimodal(self, *args, **kwargs): raise LLMRateLimitError("gemini")
    alternate = Observer()
    alternate.name, alternate.model = "openrouter", "fixture-vision"
    route = SimpleNamespace(canonical_model_id="qwen2.5-vl-72b-instruct", provider="openrouter", provider_model_id="fixture-vision")
    monkeypatch.setattr("app.content.capability_router.configured_visual_observer_routes", lambda *a: [route])
    alternate.is_available = lambda: True
    monkeypatch.setattr("app.content.provider_registry.create_provider", lambda *a: alternate)
    supplied = frames(0, 300, 0)[:4]
    notes = observe_with_vision_failover(RateLimited(), supplied, [], start=0, end=300, continuity=[], deadline=time.monotonic() + 10)
    assert all(n["observer_provider"] == "openrouter" for n in notes) and len(alternate.calls) == 1


def test_http_model_id_alias_is_a_locked_canonical_selection(monkeypatch, tmp_path):
    from tests.test_track9_api_routes import _server, _make_prod, _ingest, _transcribe, _post, PID, UID
    from app.production.narration_mock import mock_grounded_plan
    selected = []
    def build(pid, uid, asset, transcript, **kwargs):
        selected.append(kwargs["canonical_model_id"])
        return mock_grounded_plan(pid, uid, asset, transcript, target_duration_seconds=5)
    monkeypatch.setattr("app.production.narration.build_grounded_narration_plan", build)
    base, server, _ = _server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path); _ingest(tmp_path, duration=10); _transcribe(tmp_path)
        status, payload, _ = _post(base, f"/api/productions/{PID}/units/{UID}/narration",
                                  {"model_id": "gpt-6.1-sol", "summary_mode": "pro", "mock_mode": True})
        assert status == 201, payload
        assert selected == ["gpt-6.1-sol"]
    finally:
        server.shutdown(); server.server_close()


def test_transient_storyteller_transport_failure_retries_only_missing_text_batch(monkeypatch, tmp_path):
    from app.production.narration_storyteller import write_storyteller_chapter
    from app.production.narration_chapters import _chapter_budget, chapter_windows
    monkeypatch.setattr("app.production.narration_storyteller.time.sleep", lambda seconds: None)
    asset, transcript = story_evidence()
    start, end, rows = chapter_windows(asset.duration, transcript)[0]
    supplied = frames(start, end, 0)
    local = _chapter_budget(resolve_recap_budget(asset.duration), 0, 5)
    class Flaky(ChapterNarrationProvider):
        failures = 0
        def generate(self, prompt, *args, **kwargs):
            payload = json.loads(prompt.split("evidence:\n", 1)[1].split("\nRequired JSON schema:", 1)[0])
            if payload["batch_index"] == 1 and not self.failures:
                self.failures += 1
                raise LLMConnectionError("agentrouter", "Server error: 502")
            return super().generate(prompt, *args, **kwargs)
    provider = Flaky(name="agentrouter", model="gpt-6.1-sol")
    payload = {"chapter_index": 1, "start_seconds": start, "end_seconds": end, "transcript_segments": rows,
        "frames": [{"id": i, "timestamp_seconds": ts} for i, (ts, _) in enumerate(supplied)],
        "visual_action_notes": _notes(supplied), "prior_chapter_summaries": [], "previous_narration_tail": "",
        "preferred_word_count": 308}
    cache = {}
    result, calls = write_storyteller_chapter(provider, "Grounded recap", payload, asset=asset, frames=supplied,
        local_budget=local, deadline=time.monotonic() + 30, on_batch=lambda index, value: cache.update({str(index): value}))
    assert calls == 5 and provider.failures == 1 and len(cache) == 4
    original_call_count = len(provider.calls)
    again, calls = write_storyteller_chapter(provider, "Grounded recap", payload, asset=asset, frames=supplied,
        local_budget=local, deadline=time.monotonic() + 30, cached_batches=cache)
    assert result == again and len(provider.calls) == original_call_count and calls == 5


def test_cached_pro_route_does_not_decode_or_extract_any_frame(monkeypatch, tmp_path):
    from app.production.narration_pro import build_pro_multimodal_plan, _window_frame_timestamps
    from app.production.narration_chapters import chapter_windows
    from app.content import provider_registry
    asset, transcript = story_evidence()
    pid, uid = "prd_cache_no_io", "unit-001"
    source = tmp_path / pid / asset.stored_path
    source.parent.mkdir(parents=True); source.write_bytes(b"not decoded source fixture")
    plan_dir = tmp_path / pid / "units" / uid / "plan"
    for start, end, rows in chapter_windows(asset.duration, transcript):
        supplied = [(ts, "not-read") for ts in _window_frame_timestamps(start, end, 30)]
        save_frame_observations(plan_dir / CACHE_FILENAME, asset, transcript, supplied, _notes(supplied), start=start, end=end)
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-observer-key")
    observer = Observer()
    observer.is_available = lambda: True
    monkeypatch.setitem(provider_registry._REGISTRY, "gemini", lambda model=None: observer)
    monkeypatch.setattr("app.production.narration_pro._extract_window_frames", lambda *a, **k: pytest.fail("Cache hit must not extract frames"))
    result = build_pro_multimodal_plan(pid, uid, asset, transcript, target_duration_seconds=540,
        summary_model_id="gpt-6.1-sol", prov=ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol"), root=tmp_path,
        recap_budget=resolve_recap_budget(asset.duration))
    assert result.stage1_cache_hits == 5 and result.live_vision_calls == 0 and result.visual_frame_count == 150


def test_changed_source_cannot_be_rehydrated_from_an_old_prior_pro_snapshot(tmp_path):
    asset, transcript = story_evidence()
    prior = build_chapter_plan("prd_cache_test", "unit-001", asset, transcript,
        budget=resolve_recap_budget(asset.duration), prov=ChapterNarrationProvider(), frame_loader=frames)
    prior.source_media_path, prior.source_transcript_path = asset.stored_path, transcript.transcript_path
    path = tmp_path / "visual_evidence_prior_pro.json"
    path.write_text(prior.model_dump_json())
    supplied = frames(0, 360, 0)
    cache = tmp_path / CACHE_FILENAME
    assert hydrate_prior_pro(cache, path, asset, transcript, supplied, start=0, end=360,
                             production_id=prior.production_id, unit_id=prior.unit_id)
    changed = asset.model_copy(update={"sha256": "f" * 64})
    notes, origin = cached_visual_observations(tmp_path, changed, transcript, supplied, start=0, end=360,
                                               production_id=prior.production_id, unit_id=prior.unit_id)
    assert notes is None and origin is None