"""Hermetic text firewall, exact model routing and two-stage Pro narration."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from app.content.exceptions import LLMConnectionError, LLMModelUnavailableError, LLMOutputTruncatedError, LLMResponseError
from app.content.model_registry import JOB_SOURCE_TRANSCRIPT_RECAP, JOB_SOURCE_VIDEO_RECAP, get_model, list_pro_model_ids
from app.content.providers.agentrouter import AgentRouterProvider, require_text_only
from app.production.narration_chapters import build_chapter_plan
from app.production.narration_real import NarrationModelOutputError, validate_storyteller_language
from app.production.recap_budget import resolve_recap_budget
from app.production.visual_observation import validate_visual_action_notes
from tests.track9_narration_fakes import ChapterNarrationProvider, story_evidence


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    for name in ("AGENTROUTER_API_KEY", "AGENTROUTER_BASE_URL", "AGENTROUTER_MODEL", "AGENTROUTER_REQUEST_TIMEOUT", "AGENTROUTER_REASONING_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AVM_RECAP_VISION_MODEL", raising=False)
    monkeypatch.delenv("AVM_RECAP_VISION_BATCH_SIZE", raising=False)


@pytest.mark.parametrize("model_id,label", [("gpt-6.1-sol", "GPT-6.1 Sol"), ("gpt-6-astra", "GPT-6 Astra")])
def test_canonical_models_are_text_only_with_explicit_observer_dependency(monkeypatch, model_id, label):
    from app.content.capability_router import assess_model_for_job
    from app.content.model_resolver import resolve_model
    model = get_model(model_id)
    assert model.display_name == label and model.id in list_pro_model_ids()
    assert not model.capabilities.vision and not model.capabilities.video and not model.capabilities.multimodal
    assert model.bindings[0].supported_input_modalities == ("text",)
    assert model.bindings[0].identity_verification == "declared"
    monkeypatch.setenv("AGENTROUTER_API_KEY", "synthetic-test-credential")
    monkeypatch.setenv("AGENTROUTER_BASE_URL", "http://127.0.0.1:8318/v1")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert assess_model_for_job(model_id, JOB_SOURCE_TRANSCRIPT_RECAP).eligible
    assert not assess_model_for_job(model_id, JOB_SOURCE_VIDEO_RECAP).eligible
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-observer-credential")
    assessment = assess_model_for_job(model_id, JOB_SOURCE_VIDEO_RECAP)
    assert assessment.eligible and assessment.video_understanding_method == "frame_sequence"
    assert assessment.routes[0].input_modality == "text"
    resolved = resolve_model(model_id, job_id=JOB_SOURCE_VIDEO_RECAP)
    assert resolved.canonical_model_id == model_id and resolved.provider == "agentrouter"
    assert resolved.provider_instance.model == model_id and not resolved.provider_instance.supports_multimodal()


@pytest.mark.parametrize("payload", [b"binary", [{"type": "input_image"}], {"image_url": "bad"},
                                      "data:image/jpeg;base64,AAAA", "input_image", "A" * 2048])
def test_media_is_rejected_before_http(monkeypatch, payload):
    post = Mock(side_effect=AssertionError("Media must not reach HTTP"))
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    provider = AgentRouterProvider(api_key="synthetic-credential")
    with pytest.raises(ValueError, match="pure text"):
        provider.generate(payload)
    with pytest.raises(LLMModelUnavailableError):
        provider.generate_multimodal("probe", images=[payload])
    assert not post.called


def test_text_envelope_json_mode_and_usage_are_safe(monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {"model": "gpt-6-astra", "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
                                  "usage": {"total_tokens": 12, "details": {"x": 1}}}
    post = Mock(return_value=response)
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    provider = AgentRouterProvider(api_key="synthetic-credential", model="gpt-6-astra", base_url="http://127.0.0.1:8318/v1")
    result = provider.generate("Pure text dialogue", system_prompt="Tell the grounded story", response_schema=dict, max_tokens=8192)
    body = post.call_args.kwargs["json"]
    assert body["model"] == "gpt-6-astra" and body["max_tokens"] == 8192
    assert body["reasoning_effort"] == "low"
    assert body["response_format"] == {"type": "json_object"}
    assert all(isinstance(message["content"], str) for message in body["messages"])
    assert result.usage == {"total_tokens": 12} and result.raw_response is None
    assert result.metadata["image_payloads_sent"] is False


@pytest.mark.parametrize("status", [400, 401, 402, 403, 429, 500])
def test_errors_never_echo_credentials_or_upstream_body(monkeypatch, status):
    credential = "synthetic-private-token"
    response = Mock(status_code=status)
    response.json.return_value = {"error": {"message": credential}}
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", Mock(return_value=response))
    with pytest.raises(Exception) as error:
        AgentRouterProvider(api_key=credential).generate("probe")
    assert credential not in str(error.value)
    assert not response.json.called


def test_transport_error_is_not_chained_or_exposed(monkeypatch):
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", Mock(side_effect=requests.ConnectionError("synthetic-private-token")))
    with pytest.raises(LLMConnectionError) as error:
        AgentRouterProvider(api_key="synthetic-private-token").generate("probe")
    assert "synthetic-private-token" not in str(error.value) and error.value.__suppress_context__


@pytest.mark.parametrize("model,finish,error_type", [("different-model", "stop", LLMResponseError), ("gpt-6.1-sol", "length", LLMOutputTruncatedError)])
def test_model_substitution_and_incomplete_output_fail_closed(monkeypatch, model, finish, error_type):
    response = Mock(status_code=200)
    response.json.return_value = {"model": model, "choices": [{"finish_reason": finish, "message": {"content": "{}"}}]}
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", Mock(return_value=response))
    with pytest.raises(error_type):
        AgentRouterProvider(api_key="synthetic-credential", base_url="http://127.0.0.1:8318/v1").generate("probe")


def frames(start, end, index):
    return [(round(start + (end - start) * (i + 1) / 31, 3), f"never-read-{index}-{i}.jpg") for i in range(30)]


class Observer:
    name, model = "gemini", "gemini-3.5-flash-lite"

    def __init__(self):
        self.calls = []

    def supports_multimodal(self):
        return True

    def generate_multimodal(self, prompt, system_prompt=None, *, images, video, **kwargs):
        self.calls.append(images)
        return SimpleNamespace(content=json.dumps({"observations": [
            {"frame_id": i, "timestamp_seconds": ts, "action": "The keeper shields the hatchling while the companion distracts the approaching hunters."}
            for i, (ts, _) in enumerate(images)]}))


@pytest.mark.parametrize("model_id", ["gpt-6.1-sol", "gpt-6-astra"])
def test_two_stage_chapters_send_frames_only_to_observer(model_id):
    asset, transcript = story_evidence()
    storyteller = ChapterNarrationProvider(name="agentrouter", model=model_id)
    observer = Observer()
    result = build_chapter_plan("prd_agentrouter_hermetic", "unit-001", asset, transcript,
        budget=resolve_recap_budget(asset.duration, target_length_preset="extended"),
        prov=storyteller, visual_observer=observer, frame_loader=frames)
    assert len(observer.calls) == result.multimodal_calls == 40
    assert all(len(batch) <= 4 for batch in observer.calls)
    assert result.text_storyteller_calls == len(storyteller.calls) == 5
    assert all(call["max_tokens"] == 16384 and call["reasoning"] == {"effort": "low"} for call in storyteller.calls)
    assert not storyteller.visual_calls
    assert result.visual_understanding_strategy == "visual_notes_then_text_storyteller"
    assert result.visual_observer_model_id == observer.model and result.storytelling_provider == "agentrouter"
    assert 45 <= len(result.beats) <= 54 and 1400 <= len(result.narration_text.split()) <= 1680
    assert all(any(ref.visual_frame_timestamps for ref in beat.source_refs) for beat in result.beats)
    for payload in storyteller.chapters:
        assert len(payload["visual_action_notes"]) == len(payload["frames"])
        require_text_only(json.dumps(payload))
        assert "never-read" not in json.dumps(payload)
        assert len(payload["frames"]) == 30
    assert {row["id"] for batch in storyteller.chapters for row in batch["transcript_segments"]} == set(range(len(transcript.segments)))
    starts = [min(ref.start_seconds for ref in beat.source_refs) for beat in result.beats]
    assert starts == sorted(starts)


def test_visual_observation_has_one_bounded_overload_retry(monkeypatch):
    monkeypatch.setattr("app.production.narration_chapters.time.sleep", lambda seconds: None)
    class FlakyObserver(Observer):
        failures = 0
        def generate_multimodal(self, *args, **kwargs):
            if not self.failures:
                self.failures += 1
                raise LLMConnectionError("gemini", "Server error: 503")
            return super().generate_multimodal(*args, **kwargs)
    asset, transcript = story_evidence()
    observer = FlakyObserver()
    result = build_chapter_plan("prd_agentrouter_hermetic", "unit-001", asset, transcript,
        budget=resolve_recap_budget(asset.duration, target_length_preset="extended"),
        prov=ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol"), visual_observer=observer, frame_loader=frames)
    assert observer.failures == 1 and result.multimodal_calls == 41


def test_validated_visual_notes_survive_storyteller_failure_without_resending_frames(tmp_path):
    asset, transcript = story_evidence()
    checkpoint = tmp_path / "chapters.partial.json"
    observer = Observer()
    class UnavailableStoryteller(ChapterNarrationProvider):
        def generate(self, *args, **kwargs):
            raise ValueError("synthetic local failure")
    with pytest.raises(ValueError, match="synthetic local failure"):
        build_chapter_plan("prd_agentrouter_hermetic", "unit-001", asset, transcript,
            budget=resolve_recap_budget(asset.duration, target_length_preset="extended"),
            prov=UnavailableStoryteller(name="agentrouter", model="gpt-6.1-sol"), visual_observer=observer,
            frame_loader=frames, checkpoint_path=checkpoint)
    assert len(observer.calls) == 8 and checkpoint.is_file()
    saved = json.loads(checkpoint.read_text())
    assert not saved["chapters"] and len(saved["pending_visual"]["visual_action_notes"]) == 30
    result = build_chapter_plan("prd_agentrouter_hermetic", "unit-001", asset, transcript,
        budget=resolve_recap_budget(asset.duration, target_length_preset="extended"),
        prov=ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol"), visual_observer=observer,
        frame_loader=frames, checkpoint_path=checkpoint)
    assert result.multimodal_calls == len(observer.calls) == 40
    assert json.loads(checkpoint.read_text())["complete"]


@pytest.mark.parametrize("fault", ["missing", "timestamp", "media"])
def test_observations_require_every_real_timestamp_and_pure_text(fault):
    supplied = frames(0, 300, 0)
    notes = [{"frame_id": i, "timestamp_seconds": ts, "action": "The companion escapes the pursuing hunters."} for i, (ts, _) in enumerate(supplied)]
    if fault == "missing": notes.pop()
    elif fault == "timestamp": notes[0]["timestamp_seconds"] += 1
    else: notes[0]["action"] = "data:image/jpeg;base64,AAAA"
    with pytest.raises(ValueError):
        validate_visual_action_notes(notes, supplied)


@pytest.mark.parametrize("caption", ["A hand displays the pendant.", "A woman gently touches the hatchling.",
                                     "Armed guards direct the weary line.", "The frame shows the king."])
def test_caption_syndrome_is_rejected_for_repair(caption):
    with pytest.raises(NarrationModelOutputError, match="camera-caption"):
        validate_storyteller_language(SimpleNamespace(beats=[SimpleNamespace(order=1, narration_text=caption)]))


@pytest.mark.parametrize("model_id", ["gpt-6.1-sol", "gpt-6-astra"])
def test_http_admits_declared_two_stage_pro_models_without_claiming_image_input(monkeypatch, tmp_path, model_id):
    from tests.test_track9_api_routes import _server, _make_prod, _ingest, _transcribe, _post, PID, UID
    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path)
        _ingest(tmp_path, duration=10)
        _transcribe(tmp_path)
        status, payload, _ = _post(base, f"/api/productions/{PID}/units/{UID}/narration", {
            "summary_mode": "pro", "mock_mode": True, "selection_mode": "explicit",
            "summary_understanding_model_id": model_id,
        })
        assert status == 201, payload
        assert not get_model(model_id).capabilities.is_multimodal_capable()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_storyteller_does_not_admit_citations_from_a_neighboring_batch():
    from app.production.narration_storyteller import write_storyteller_chapter
    from app.production.narration_chapters import _chapter_budget, chapter_windows
    import time
    asset, transcript = story_evidence()
    start, end, rows = chapter_windows(asset.duration, transcript)[0]
    supplied_frames = frames(start, end, 0)
    budget = _chapter_budget(resolve_recap_budget(asset.duration), 0, 5)
    provider = ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol",
        fault=lambda data: data["beats"][0]["source_refs"][0].update(frame_ids=[999]), always_fail=True)
    payload = {"chapter_index": 1, "start_seconds": start, "end_seconds": end, "transcript_segments": rows,
        "frames": [{"id": i, "timestamp_seconds": ts} for i, (ts, _) in enumerate(supplied_frames)],
        "visual_action_notes": [{"frame_id": i, "timestamp_seconds": ts, "action": "The companion escapes."}
                                for i, (ts, _) in enumerate(supplied_frames)],
        "prior_chapter_summaries": [], "previous_narration_tail": "", "preferred_word_count": 308}
    with pytest.raises(NarrationModelOutputError, match="outside its supplied window"):
        write_storyteller_chapter(provider, "Grounded recap", payload, asset=asset, frames=supplied_frames,
                                 local_budget=budget, deadline=time.monotonic() + 30)


def test_observer_override_changes_only_the_gemini_dependency(monkeypatch):
    from app.content.capability_router import configured_visual_observer, assess_model_for_job
    model = get_model("gpt-6.1-sol")
    monkeypatch.setenv("AVM_RECAP_VISION_MODEL", "gemini-3-flash-preview")
    monkeypatch.setenv("AGENTROUTER_API_KEY", "synthetic-key")
    monkeypatch.setenv("AGENTROUTER_BASE_URL", "http://127.0.0.1:8318/v1")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-vision-key")
    assert configured_visual_observer(model).id == "gemini-3-flash-preview"
    assessment = assess_model_for_job(model.id, JOB_SOURCE_VIDEO_RECAP)
    assert assessment.eligible and assessment.canonical_model_id == "gpt-6.1-sol"
    assert assessment.routes[0].provider == "agentrouter" and assessment.routes[0].input_modality == "text"
    monkeypatch.setenv("AVM_RECAP_VISION_MODEL", "gpt-6-astra")
    with pytest.raises(ValueError, match="image route"):
        configured_visual_observer(model)


def test_observer_reuses_successful_small_batches_after_overload(monkeypatch):
    from app.production.visual_observation import observe_chapter_frames
    monkeypatch.setattr("app.production.visual_observation.time.sleep", lambda seconds: None)
    supplied = frames(0, 300, 0)[:8]
    class FailingSecondBatch(Observer):
        blocked = True
        def generate_multimodal(self, *args, **kwargs):
            if self.blocked and kwargs["images"][0][0] == supplied[4][0]:
                raise LLMConnectionError("gemini", "Server error: 503")
            return super().generate_multimodal(*args, **kwargs)
    observer = FailingSecondBatch()
    cache = {}
    with pytest.raises(LLMConnectionError):
        observe_chapter_frames(observer, supplied, [], start=0, end=300, continuity=[],
                               cached_batches=cache, on_batch=lambda index, notes: cache.update({str(index): notes}))
    assert list(cache) == ["0"] and len(observer.calls) == 1
    observer.blocked = False
    result = observe_chapter_frames(observer, supplied, [], start=0, end=300, continuity=[], cached_batches=cache)
    assert len(result) == 8 and len(observer.calls) == 2


def test_checkpoint_retries_brief_windows_replace_lock_without_losing_data(monkeypatch, tmp_path):
    from app.production.narration_chapters import _write_checkpoint
    from app.production import storage
    original = storage._atomic_write
    attempts = []
    def locked_once(path, text):
        attempts.append(path)
        if len(attempts) == 1:
            raise PermissionError("synthetic transient file lock")
        original(path, text)
    monkeypatch.setattr(storage, "_atomic_write", locked_once)
    monkeypatch.setattr("app.production.narration_chapters.time.sleep", lambda seconds: None)
    path = tmp_path / "chapter.partial.json"
    _write_checkpoint(path, {"chapters": [{"validated": True}]})
    assert len(attempts) == 2 and json.loads(path.read_text()) == {"chapters": [{"validated": True}]}


def test_completed_chapter_revalidation_keeps_later_visual_batches_on_disk(monkeypatch, tmp_path):
    asset, transcript = story_evidence()
    checkpoint = tmp_path / "resumable.partial.json"
    observer = Observer()
    class StopInSecondChapter(ChapterNarrationProvider):
        def generate(self, prompt, *args, **kwargs):
            evidence = json.loads(prompt.split("evidence:\n", 1)[1].split("\nRequired JSON schema:", 1)[0])
            if evidence["chapter_index"] == 2:
                raise ValueError("stop after second chapter observation")
            return super().generate(prompt, *args, **kwargs)
    with pytest.raises(ValueError, match="stop after second"):
        build_chapter_plan("prd_agentrouter_resume", "unit-001", asset, transcript,
            budget=resolve_recap_budget(asset.duration), prov=StopInSecondChapter(name="agentrouter", model="gpt-6.1-sol"),
            visual_observer=observer, frame_loader=frames, checkpoint_path=checkpoint)
    saved = json.loads(checkpoint.read_text())
    assert len(saved["chapters"]) == 1 and saved["pending_visual"]["chapter_index"] == 1
    def interrupted_loader(start, end, index):
        if index == 1:
            raise OSError("local restart interrupted before the next frame load")
        return frames(start, end, index)
    with pytest.raises(OSError, match="local restart"):
        build_chapter_plan("prd_agentrouter_resume", "unit-001", asset, transcript,
            budget=resolve_recap_budget(asset.duration), prov=ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol"),
            visual_observer=observer, frame_loader=interrupted_loader, checkpoint_path=checkpoint)
    assert json.loads(checkpoint.read_text())["pending_visual"] == saved["pending_visual"]