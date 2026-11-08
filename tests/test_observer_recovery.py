"""No network/credentials: accurate diagnostics and timestamp-only recovery."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from app.content.exceptions import LLMConnectionError, LLMTimeoutError
from app.content.providers.gemini import GeminiProvider
from app.content.providers.alibaba import AlibabaProvider
from app.providers.fallback_policy import classify_provider_failure
from app.production import narration_real as recovery
from app.production.visual_cache import save_frame_observations, load_partial_frame_observations
from app.production.visual_observation import observe_chapter_frames
from tests.test_agentrouter_storyteller import Observer, frames
from tests.track9_narration_fakes import story_evidence


@pytest.mark.parametrize("provider", ["gemini", "alibaba"])
@pytest.mark.parametrize("status,code,category", [
    (400, "InvalidParameter", "invalid_request"), (404, "ModelNotFound", "capability"),
    (401, "InvalidApiKey", "authentication"), (403, "Forbidden", "authentication"),
    (402, "Quota", "quota"), (429, "Throttling", "rate_limit"),
    (503, "SystemError", "provider_5xx"), (504, "Timeout", "timeout"),
])
def test_http_diagnostics_are_precise_and_secret_free(provider, status, code, category):
    secret = "synthetic-secret-never-logged"
    adapter = GeminiProvider(api_key=secret) if provider == "gemini" else AlibabaProvider(api_key=secret)
    response = SimpleNamespace(status_code=status, headers={"x-request-id": "req-fixture"}, json=lambda: {
        "error": {"code": code, "message": f"bad parameter {secret} https://host/?key={secret}"},
        "access_token": secret, "prompt": secret,
    })
    with pytest.raises(Exception) as caught:
        adapter._raise_for_status(response)
    assert classify_provider_failure(caught.value) == category
    assert caught.value.diagnostics["http_status"] == status
    assert caught.value.diagnostics["request_id"] == "req-fixture"
    assert secret not in str(caught.value)
    assert "https://host" not in str(caught.value)


def test_gemini_quota_is_not_generic_rate_limit():
    response = SimpleNamespace(status_code=429, json=lambda: {"error": {"status": "RESOURCE_EXHAUSTED", "message": "Daily quota exceeded"}})
    with pytest.raises(Exception) as caught:
        GeminiProvider(api_key="fixture")._raise_for_status(response)
    assert classify_provider_failure(caught.value) == "quota"


@pytest.mark.parametrize("provider", ["gemini", "alibaba"])
def test_transport_timeout_retains_its_own_category(monkeypatch, tmp_path, provider):
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"fixture" * 200)
    monkeypatch.setattr(requests, "post", Mock(side_effect=requests.exceptions.Timeout()))
    adapter = GeminiProvider(api_key="fixture") if provider == "gemini" else AlibabaProvider(api_key="fixture")
    with pytest.raises(LLMTimeoutError):
        adapter.generate_multimodal("JSON", images=[(1, image)])


def test_explicit_resume_reopens_only_transient_observers():
    observer = Observer()
    @recovery.narration_request
    def fail():
        recovery.quarantine_vision_observer(observer, LLMConnectionError("gemini", "socket"))
        assert recovery.observer_is_quarantined(observer.name, observer.model)
    fail()
    @recovery.narration_request
    def resume():
        assert recovery.claim_vision_observer(observer.name, observer.model)
        recovery.release_vision_observer(observer.name, observer.model, succeeded=True)
    resume()
    from app.content.exceptions import LLMAuthenticationError
    recovery.quarantine_vision_observer(observer, LLMAuthenticationError("gemini"))
    @recovery.narration_request
    def cannot_bypass_auth():
        assert not recovery.claim_vision_observer(observer.name, observer.model)
    cannot_bypass_auth()


def test_cooldown_expires_and_only_one_half_open_owner_probes(monkeypatch):
    clock = [10]
    monkeypatch.setattr(recovery.time, "monotonic", lambda: clock[0])
    observer = Observer()
    recovery.quarantine_vision_observer(observer, LLMConnectionError("gemini"))
    assert recovery.observer_is_quarantined(observer.name, observer.model)
    clock[0] += 16
    token = recovery._NARRATION_REQUEST_ID.set("first")
    assert recovery.claim_vision_observer(observer.name, observer.model)
    recovery._NARRATION_REQUEST_ID.reset(token)
    token = recovery._NARRATION_REQUEST_ID.set("second")
    assert not recovery.claim_vision_observer(observer.name, observer.model)
    recovery._NARRATION_REQUEST_ID.reset(token)


def test_partial_cache_and_batch_resize_send_only_missing_frames(tmp_path, monkeypatch):
    asset, transcript = story_evidence()
    supplied = frames(0, 300, 0)[:8]
    observer = Observer()
    notes = observe_chapter_frames(observer, supplied[:4], [], start=0, end=150, continuity=[])
    path = tmp_path / "frame_observations.json"
    save_frame_observations(path, asset, transcript, supplied, notes[:3], start=0, end=300)
    partial = load_partial_frame_observations(path, asset, transcript, supplied, start=0, end=300)
    assert len(partial) == 3
    monkeypatch.setenv("AVM_RECAP_VISION_BATCH_SIZE", "8")
    resumed = Observer()
    result = observe_chapter_frames(resumed, supplied, [], start=0, end=300, continuity=[],
                                   cached_batches={"previous": list(partial.values())})
    assert len(result) == 8
    assert sum(len(call) for call in resumed.calls) == 5
    assert all(ts not in partial for call in resumed.calls for ts, _ in call)


def test_oversized_override_is_capped_at_eight_frames(monkeypatch):
    monkeypatch.setenv("AVM_RECAP_VISION_BATCH_SIZE", "32")
    observer = Observer()
    supplied = frames(0, 300, 0)
    observe_chapter_frames(observer, supplied, [], start=0, end=300, continuity=[])
    assert max(map(len, observer.calls)) == 8


def test_byte_bound_splits_batches_without_reading_image_data(monkeypatch, tmp_path):
    monkeypatch.setenv("AVM_RECAP_VISION_BATCH_SIZE", "8")
    supplied = []
    for i in range(4):
        path = tmp_path / f"frame-{i}.jpg"
        with path.open("wb") as file:
            file.truncate(800_000)
        supplied.append((i + 1., str(path)))
    observer = Observer()
    observe_chapter_frames(observer, supplied, [], start=0, end=5, continuity=[])
    assert list(map(len, observer.calls)) == [2, 2]


def test_selective_extraction_never_reextracts_retained_timestamps(monkeypatch, tmp_path):
    from app.production.narration_pro import _extract_window_frames
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    seeks = []
    def run(args, description, timeout):
        assert timeout == 30
        seeks.append(float(args[args.index("-ss") + 1]))
        Path(args[-1]).write_bytes(b"fixture" * 200)
    monkeypatch.setattr("app.assembly.ffmpeg.get_ffmpeg_runner", lambda: SimpleNamespace(run=run))
    result = _extract_window_frames(source, 0, 100, tmp_path, 0, frame_count=8, timestamps=[20., 40.])
    assert seeks == [20., 40.] and len(result) == 2


def test_real_composite_resume_reuses_completed_chapters_without_observer_resolution(monkeypatch, tmp_path):
    from app.production.narration_pro import build_pro_multimodal_plan
    from app.production.narration_chapters import build_chapter_plan
    from app.production.recap_budget import resolve_recap_budget
    from tests.track9_narration_fakes import ChapterNarrationProvider
    from app.production.narration_pro import _window_frame_timestamps
    asset, transcript = story_evidence()
    source = tmp_path / "p" / asset.stored_path
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source-fixture")
    checkpoint = tmp_path / "p" / "units" / "u" / "plan" / "narration_chapters.partial.json"
    def frame_loader(start, end, index):
        import math
        count = max(8, min(32, math.ceil((end - start) / 12)))
        return [(ts, "fixture-no-image-io.jpg") for ts in _window_frame_timestamps(start, end, count)]
    first = build_chapter_plan("p", "u", asset, transcript, budget=resolve_recap_budget(asset.duration),
        prov=ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol"),
        visual_observer=Observer(), frame_loader=frame_loader, checkpoint_path=checkpoint, context_tokens=128000)
    original = json.loads(checkpoint.read_text())["chapters"]
    monkeypatch.setattr("app.content.capability_router.configured_visual_observer_routes", lambda *a: pytest.fail("cache must precede live resolution"))
    monkeypatch.setattr("app.production.narration_pro._extract_window_frames", lambda *a, **kw: pytest.fail("accepted chapters must not reextract frames"))
    provider = ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol")
    result = build_pro_multimodal_plan("p", "u", asset, transcript, first.target_duration_seconds,
        summary_model_id="gpt-6.1-sol", prov=provider, root=tmp_path,
        recap_budget=resolve_recap_budget(asset.duration), minimum_cached_chapters=5)
    assert result.live_vision_calls == 0 and not provider.calls
    assert json.loads(checkpoint.read_text())["chapters"] == original