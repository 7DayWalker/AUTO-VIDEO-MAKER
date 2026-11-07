"""Extended recaps: hermetic providers, real local media, no workstation keys."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.content.exceptions import LLMConnectionError, LLMOutputTruncatedError
from app.production.narration import build_grounded_narration_plan, validate_narration_artifact
from app.production.narration_chapters import build_chapter_plan, chapter_windows
from app.production.recap_budget import resolve_recap_budget, requires_chapter_planning
from app.production.storage import load_narration_artifact, save_narration_artifact
from tests.track9_narration_fakes import ChapterNarrationProvider, story_evidence, chapter_prompt_evidence

PID, UID = "prd_20261104_extended_test_001", "unit-001"


def frame_loader(start, end, index):
    # Fake frame IO only; adapter transmission has separate byte-level tests.
    from app.production.narration_pro import _window_frame_timestamps
    return [(ts, f"fixture-{index}-{i}.jpg") for i, ts in enumerate(_window_frame_timestamps(start, end, 30))]


@pytest.mark.parametrize("preset,min_beats,words", [("extended", 45, 1400), ("deep", 28, 750), ("auto", 45, 1400)])
@pytest.mark.parametrize("visual", [False, True])
def test_long_recap_uses_final_chapter_beats_full_transcript_and_rolling_context(tmp_path, preset, min_beats, words, visual):
    asset, transcript = story_evidence(count=365)
    provider = ChapterNarrationProvider()
    budget = resolve_recap_budget(asset.duration, target_length_preset=preset)
    artifact = build_chapter_plan(PID, UID, asset, transcript, budget=budget, prov=provider,
                                 frame_loader=frame_loader if visual else None, context_tokens=128000)
    assert artifact.planning_strategy == "multi_chapter" and artifact.chapter_count == 5
    assert min_beats <= len(artifact.beats) <= budget.max_beats
    assert words <= len(artifact.narration_text.split()) <= budget.max_narration_words
    assert len(provider.calls) == 5  # No final reduction that can discard the middle acts.
    supplied = {row["id"] for chapter in provider.chapters for row in chapter["transcript_segments"]}
    assert supplied == set(range(len(transcript.segments)))
    for chapter in provider.chapters:
        for row in chapter["transcript_segments"]:
            assert row["text"] == transcript.segments[row["id"]].text
            assert chapter["start_seconds"] <= row["start_seconds"] < row["end_seconds"] <= chapter["end_seconds"]
    assert not provider.chapters[0]["prior_chapter_summaries"]
    assert len(provider.chapters[-1]["prior_chapter_summaries"]) == 4
    assert provider.chapters[1]["previous_narration_tail"]
    assert [b.order for b in artifact.beats] == list(range(1, len(artifact.beats) + 1))
    assert artifact.visual_frame_count == (150 if visual else 0)
    assert artifact.multimodal_calls == (5 if visual else 0)
    assert all(call["max_tokens"] == 16384 for call in provider.calls)
    save_narration_artifact(artifact, production_id=PID, unit_id=UID, root=tmp_path,
                            source_asset=asset, source_transcript=transcript)
    assert load_narration_artifact(PID, UID, root=tmp_path).model_dump() == artifact.model_dump()


def test_silent_chapter_uses_visual_citations_without_fabricated_dialogue():
    asset, transcript = story_evidence()
    transcript.segments = [s for s in transcript.segments if not (700 < s.start < 1090)]
    artifact = build_chapter_plan(PID, UID, asset, transcript, budget=resolve_recap_budget(1800),
                                 prov=ChapterNarrationProvider(), frame_loader=frame_loader, context_tokens=128000)
    silent = [beat for beat in artifact.beats if not beat.transcript_refs]
    assert silent and all(ref.visual_frame_timestamps for beat in silent for ref in beat.source_refs)
    validate_narration_artifact(artifact, source_asset=asset, source_transcript=transcript)
    artifact.chapters[2].frame_timestamps = []
    with pytest.raises(ValueError, match="not inspected"):
        validate_narration_artifact(artifact, source_asset=asset, source_transcript=transcript)


def test_validated_chapters_resume_after_provider_failure_without_final_artifact(tmp_path):
    asset, transcript = story_evidence()
    path = tmp_path / "narration_chapters.partial.json"
    class Interrupted(ChapterNarrationProvider):
        def generate_multimodal(self, prompt, *args, **kwargs):
            evidence = chapter_prompt_evidence(prompt)
            if evidence["chapter_index"] == 3:
                raise ValueError("local interruption")
            return super().generate_multimodal(prompt, *args, **kwargs)
    with pytest.raises(ValueError, match="local interruption"):
        build_chapter_plan(PID, UID, asset, transcript, budget=resolve_recap_budget(1800),
            prov=Interrupted(), frame_loader=frame_loader, context_tokens=128000, checkpoint_path=path)
    assert len(json.loads(path.read_text())["chapters"]) == 2
    assert not (tmp_path / "narration.json").exists()
    provider = ChapterNarrationProvider()
    artifact = build_chapter_plan(PID, UID, asset, transcript, budget=resolve_recap_budget(1800),
        prov=provider, frame_loader=frame_loader, context_tokens=128000, checkpoint_path=path)
    assert len(provider.calls) == 3 and artifact.multimodal_calls == 5
    assert artifact.chapter_count == 5 and json.loads(path.read_text())["complete"]


@pytest.mark.parametrize("fault", [
    lambda data: data["beats"][0]["source_refs"][0].update(frame_ids=[999]),
    lambda data: data["beats"][0]["source_refs"][0].update(transcript_segment_ids=[9999]),
    lambda data: data["beats"].reverse(),
    lambda data: data.update(beats=data["beats"][:3]),
])
def test_invalid_chapter_is_repaired_once_and_never_saved_on_failure(tmp_path, fault):
    asset, transcript = story_evidence()
    provider = ChapterNarrationProvider(fault=fault, always_fail=True)
    with pytest.raises(ValueError):
        build_chapter_plan(PID, UID, asset, transcript, budget=resolve_recap_budget(1800),
                           prov=provider, frame_loader=frame_loader, context_tokens=128000)
    assert len(provider.calls) == 2
    assert not list(tmp_path.rglob("narration.json"))


def test_chapter_policy_and_dense_context_never_truncate_segments():
    asset, transcript = story_evidence()
    budget = resolve_recap_budget(1800, target_duration_seconds=400)
    assert requires_chapter_planning(1800, budget)
    assert not requires_chapter_planning(899, budget)
    for segment in transcript.segments:
        segment.text *= 8
    windows = chapter_windows(1800, transcript, context_tokens=32768)
    assert len(windows) > 5
    assert {row["id"] for _, _, rows in windows for row in rows} == set(range(365))
    assert all(row["text"] == transcript.segments[row["id"]].text for _, _, rows in windows for row in rows)


def test_chapter_truncation_retries_once_at_16k_without_salvaging_partial_beats():
    asset, transcript = story_evidence()
    class Truncated(ChapterNarrationProvider):
        def generate_multimodal(self, *args, **kwargs):
            response = super().generate_multimodal(*args, **kwargs)
            if len(self.calls) == 1:
                response.metadata = {"finish_reason": "MAX_TOKENS"}
            return response
    provider = Truncated()
    artifact = build_chapter_plan(PID, UID, asset, transcript, budget=resolve_recap_budget(1800),
        prov=provider, frame_loader=frame_loader, context_tokens=128000)
    assert [call["max_tokens"] for call in provider.calls[:2]] == [16384, 16384]
    assert artifact.chapter_count == 5 and artifact.multimodal_calls == 6


def test_pro_never_hides_local_or_moderation_errors(monkeypatch, tmp_path):
    from app.content.providers.alibaba import AlibabaContentModerationError
    providers = [ChapterNarrationProvider(error=AlibabaContentModerationError("fixture policy")), ChapterNarrationProvider()]
    _install_pro_pool(monkeypatch, providers)
    asset, transcript = story_evidence()
    source = tmp_path / PID / asset.stored_path
    source.parent.mkdir(parents=True); source.write_bytes(b"fixture")
    monkeypatch.setattr("app.production.narration_pro._extract_window_frames", lambda src, start, end, directory, index, **kwargs: frame_loader(start, end, index))
    with pytest.raises(AlibabaContentModerationError):
        build_grounded_narration_plan(PID, UID, asset, transcript, summary_mode="pro", root=tmp_path)
    assert len(providers[0].calls) == 1 and not providers[1].calls


def test_short_dialogue_refs_do_not_force_hundreds_of_visual_cuts():
    from app.production.models import NarrationSourceRef
    from app.production.summary_video import _paced_visual_shots
    asset, transcript = story_evidence()
    artifact = build_chapter_plan(PID, UID, asset, transcript, budget=resolve_recap_budget(1800),
        prov=ChapterNarrationProvider(), frame_loader=frame_loader, context_tokens=128000)
    for beat in artifact.beats:
        visual = next(ref for ref in beat.source_refs if ref.visual_frame_timestamps)
        short = NarrationSourceRef(asset_id=asset.asset_id, start_seconds=max(0, visual.start_seconds - 2),
                                   end_seconds=visual.start_seconds, transcript_segment_ids=[0])
        beat.source_refs.insert(0, short)
    shots = _paced_visual_shots(artifact, 540)
    assert 90 <= len(shots) <= 120
    assert sum(ref.end_seconds - ref.start_seconds for _, ref in shots) == pytest.approx(540)


@pytest.mark.parametrize("kind", ["gemini", "openrouter", "alibaba"])
@pytest.mark.parametrize("truncated", [False, True])
def test_real_vision_adapters_transmit_timestamped_bytes_and_reject_truncation(monkeypatch, tmp_path, kind, truncated):
    from app.content.providers.gemini import GeminiProvider
    from app.content.providers.openrouter import OpenRouterProvider
    from app.content.providers.alibaba import AlibabaProvider
    providers = {"gemini": lambda: GeminiProvider(api_key="fixture-key", model="gemini-3-flash-preview"),
                 "openrouter": lambda: OpenRouterProvider(api_key="fixture-key", model="openai/gpt-4o"),
                 "alibaba": lambda: AlibabaProvider(api_key="fixture-key", model="qwen-vl-max", vision_model="qwen-vl-max")}
    path = tmp_path / "frame.jpg"
    image_bytes = b"\xff\xd8\xff" + b"fixture-image" * 120
    path.write_bytes(image_bytes)
    payloads = []
    data = ({"candidates": [{"finishReason": "MAX_TOKENS" if truncated else "STOP", "content": {"parts": [
        {"thought": True, "text": "private reasoning"}, {"text": '{"summary":"visible action","beats":[]}'}]}}]}
        if kind == "gemini" else {"choices": [{"finish_reason": "length" if truncated else "stop",
                                  "message": {"content": '{"summary":"visible action","beats":[]}'}}]})
    provider = providers[kind]()
    if kind == "alibaba":
        monkeypatch.setattr(provider, "_post_chat", lambda payload: payloads.append(payload) or data)
    else:
        monkeypatch.setattr("requests.post", lambda *args, **kwargs: payloads.append(kwargs["json"]) or
                            SimpleNamespace(status_code=200, json=lambda: data))
    def generate():
        return provider.generate_multimodal("Analyze frames and transcript", images=[(12.5, path)],
                                            response_schema=dict, max_tokens=8192)
    if truncated:
        with pytest.raises(LLMOutputTruncatedError):
            generate()
    else:
        response = generate()
        assert "private reasoning" not in response.content
        assert response.metadata["frame_count"] == 1
    text = json.dumps(payloads[0])
    assert base64.b64encode(image_bytes).decode() in text and "12.500s" in text
    assert not provider.supports_native_video()
    if kind == "gemini":
        assert payloads[0]["generationConfig"]["maxOutputTokens"] == 8192
        assert payloads[0]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "minimal"}
        assert payloads[0]["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_LOW"
    with pytest.raises(ValueError, match="Visual frame"):
        provider.generate_multimodal("missing", images=[(5.0, tmp_path / "missing.jpg")])
    assert len(payloads) == 1  # Failed frame loading never becomes a text-only request.


def _install_pro_pool(monkeypatch, providers):
    from app.content import provider_registry
    from app.content.model_registry import CanonicalModel, ModelCapabilities, ProviderBinding, MODEL_REGISTRY
    models = []
    for index, provider in enumerate(providers):
        mid = f"fixture-pro-{index}"
        provider._name = provider._model = mid
        model = CanonicalModel(id=mid, display_name=mid, family="fixture", capabilities=ModelCapabilities(
            vision=True, multimodal=True, context_tokens=128000), bindings=(ProviderBinding(
            provider=mid, provider_model_id=mid, config_keys=(f"FIXTURE_PRO_{index}",)),), default_binding=mid)
        monkeypatch.setenv(f"FIXTURE_PRO_{index}", "local-fixture-only")
        monkeypatch.setitem(MODEL_REGISTRY, mid, model)
        monkeypatch.setitem(provider_registry._REGISTRY, mid, lambda wire_id=None, p=provider: p)
        models.append(model)
    monkeypatch.setattr("app.content.model_registry.list_models_for_pro", lambda: tuple(models))
    return models


@pytest.mark.parametrize("explicit", [False, True])
def test_pro_auto_and_explicit_never_renew_spent_chapter_allowance_via_another_model(monkeypatch, tmp_path, explicit):
    providers = [ChapterNarrationProvider(error=LLMConnectionError("fixture", "HTTP 503")), ChapterNarrationProvider()]
    models = _install_pro_pool(monkeypatch, providers)
    asset, transcript = story_evidence()
    source = tmp_path / PID / asset.stored_path
    source.parent.mkdir(parents=True); source.write_bytes(b"fixture source - no media decode")
    monkeypatch.setattr("app.production.narration_pro._extract_window_frames", lambda src, start, end, directory, index, **kwargs: frame_loader(start, end, index))
    kwargs = {"canonical_model_id": models[0].id} if explicit else {}
    def run():
        return build_grounded_narration_plan(PID, UID, asset, transcript, summary_mode="pro", mock_mode=False,
                                            target_length_preset="extended", root=tmp_path, **kwargs)
    with pytest.raises(LLMConnectionError): run()
    assert not providers[1].calls
    assert len(providers[0].calls) == 2  # At most one retry, including fast overloads.


@pytest.mark.parametrize("duration", [480, 540, 599.8])
def test_extended_shots_are_dense_chronological_grounded_and_audio_matched(duration):
    from app.production.summary_video import _paced_visual_shots
    asset, transcript = story_evidence()
    artifact = build_chapter_plan(PID, UID, asset, transcript, budget=resolve_recap_budget(1800),
                                 prov=ChapterNarrationProvider(), frame_loader=frame_loader, context_tokens=128000)
    shots = _paced_visual_shots(artifact, duration)
    assert 90 <= len(shots) <= 120
    assert sum(ref.end_seconds - ref.start_seconds for _, ref in shots) == pytest.approx(duration)
    assert max(ref.end_seconds - ref.start_seconds for _, ref in shots) <= 5.5 + 1e-6
    assert [ref.start_seconds for _, ref in shots] == sorted(ref.start_seconds for _, ref in shots)
    for order, shot in shots:
        assert any(ref.start_seconds <= shot.start_seconds < shot.end_seconds <= ref.end_seconds
                   for ref in artifact.beats[order - 1].source_refs)


def test_actual_ffmpeg_frames_never_leave_the_python_vision_boundary(tmp_path):
    from app.assembly.ffmpeg import run_ffmpeg
    from app.production.narration_pro import _extract_window_frames
    source = tmp_path / "moving.mp4"
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-t", "3",
                "-c:v", "libx264", "-preset", "ultrafast", str(source)], "hermetic moving source")
    frames = _extract_window_frames(source, 0, 3, tmp_path, 0, frame_count=8)
    assert len(frames) == 8 and all(0 < ts < 3 and Path(path).stat().st_size > 1024 for ts, path in frames)


def test_alibaba_exact_model_factory_locks_the_vision_operation(monkeypatch):
    from app.content.provider_registry import _create_alibaba
    monkeypatch.setenv("ALIBABA_VISION_MODEL", "wrong-model-fixture")
    provider = _create_alibaba("qwen-vl-max")
    assert provider.model == provider.vision_model == "qwen-vl-max"


def test_pitch_preserving_audio_pacing_is_measured_and_has_no_silence_padding(tmp_path):
    from app.production.narration import _pace_long_narration
    from app.assembly.ffmpeg import run_ffmpeg
    from app.voice.validator import validate_audio_file
    path = tmp_path / "speech-fixture.wav"
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=350:sample_rate=24000", "-t", "4.0", str(path)], "hermetic audio")
    budget = resolve_recap_budget(1800, target_length_preset="extended").model_copy(update={
        "target_duration_seconds": 5, "min_duration_seconds": 4.5, "max_duration_seconds": 5.5})
    artifact = SimpleNamespace(planning_strategy="multi_chapter", narration_budget=budget, narration_text=" ".join(["word"] * 14))
    result, original, factor = _pace_long_narration(path, artifact, validate_audio_file(path))
    assert original == pytest.approx(4, abs=.05) and factor == pytest.approx(4 / (14 * 60 / 170), abs=.01)
    assert 4.5 <= result.duration <= 5.5 and result.valid
    assert not (tmp_path / "speech-fixture.paced.wav").exists()


def test_multichapter_ffmpeg_mux_is_real_and_cuts_to_audio_duration(tmp_path):
    from app.assembly.ffmpeg import run_ffmpeg, probe_media
    from app.production.models import NarrativeBeat, NarrationSourceRef, NarrationArtifact, NarrationAudioArtifact
    from app.production.summary_video import assemble_narrated_summary
    from tests.test_track9_phase3_a import _reg, PID as media_pid, UID as media_uid
    source = tmp_path / "moving.mp4"
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-t", "12",
                "-c:v", "libx264", "-preset", "ultrafast", str(source)], "hermetic moving recap source")
    asset = _reg(tmp_path, source)
    beats = [NarrativeBeat(order=i + 1, title=f"Moment {i + 1}", narration_text="A traveler crosses the scene. A companion follows closely.",
             source_refs=[NarrationSourceRef(asset_id=asset.asset_id, start_seconds=i * 6, end_seconds=i * 6 + 5.5,
                                           transcript_segment_ids=[i])], transcript_refs=[str(i)]) for i in range(2)]
    narration = NarrationArtifact(narration_id=f"narr-{media_pid}-{media_uid}", production_id=media_pid, unit_id=media_uid,
        source_asset_id=asset.asset_id, target_duration_seconds=9, beats=beats,
        source_refs=[ref for beat in beats for ref in beat.source_refs], transcript_refs=["0", "1"],
        narration_text=" ".join(beat.narration_text for beat in beats), generated_at="2026-11-04T00:00:00")
    # Focus this fixture on the assembly branch; chapter provenance/grounding
    # is exercised separately with real ChapterNarrationProvider completions.
    narration.planning_strategy = "multi_chapter"
    audio_path = tmp_path / media_pid / "units" / media_uid / "plan" / "narration_audio.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000", "-t", "9", str(audio_path)], "hermetic recap audio")
    audio = NarrationAudioArtifact(narration_id=narration.narration_id, production_id=media_pid, unit_id=media_uid,
        audio_path=audio_path.relative_to(tmp_path / media_pid).as_posix(), provider="fixture-tts", voice="fixture",
        format="wav", duration_seconds=9, file_size=audio_path.stat().st_size, generated_at="2026-11-04T00:00:00")
    result = assemble_narrated_summary(media_pid, media_uid, root=tmp_path,
                                      narration_artifact=narration, narration_audio=audio, mock_mode=False)
    measured = probe_media(tmp_path / media_pid / result.output_path)
    assert measured.has_video and measured.has_audio and abs(measured.duration - 9) < .25
    assert result.validation["visual_shots"] == 2 and result.validation["freeze_padding"] is False


@pytest.mark.parametrize("source,preset,chapters", [
    (5400, "feature", (7, 8)), (9000, "blockbuster", (10, 12)), (14400, "epic", (14, 16)),
    (5400, "auto", (7, 8)), (9000, "auto", (10, 12)), (14400, "auto", (14, 16)),
    (3300, "feature", (7, 8)), (14400, "custom", (14, 16)),
])
@pytest.mark.parametrize("visual", [False, True])
def test_feature_blockbuster_epic_plan_preserves_full_story_and_budget(tmp_path, source, preset, chapters, visual):
    from app.production.recap_budget import validate_recap_depth
    from app.production.summary_video import _paced_visual_shots
    from app.production.synthesis import recap_tts_chunks, TTS_CHUNK_MAX_WORDS, TTS_CHUNK_MAX_BYTES
    asset, transcript = story_evidence(duration=source, count=720)
    budget = resolve_recap_budget(source, target_length_preset=preset,
                                  target_minutes=35 if preset == "custom" else None)
    provider = ChapterNarrationProvider()
    artifact = build_chapter_plan(PID, UID, asset, transcript, budget=budget, prov=provider,
        frame_loader=frame_loader if visual else None, context_tokens=128000)
    assert chapters[0] <= artifact.chapter_count <= chapters[1]
    assert len(provider.calls) == artifact.chapter_count
    assert {row["id"] for chapter in provider.chapters for row in chapter["transcript_segments"]} == set(range(720))
    assert len(provider.chapters[-1]["prior_chapter_summaries"]) == 4
    validate_recap_depth(artifact, budget, transcript, source)
    validate_narration_artifact(artifact, source_asset=asset, source_transcript=transcript)
    assert artifact.ending_anchor.narrative_end_seconds == source
    shots = _paced_visual_shots(artifact, budget.target_duration_seconds)
    assert sum(ref.end_seconds - ref.start_seconds for _, ref in shots) == pytest.approx(budget.target_duration_seconds)
    assert max(ref.end_seconds - ref.start_seconds for _, ref in shots) <= 5.5 + 1e-6
    assert all(any(ref.start_seconds <= shot.start_seconds < shot.end_seconds <= ref.end_seconds
                   for ref in artifact.beats[order - 1].source_refs) for order, shot in shots)
    chunks = recap_tts_chunks(artifact)
    assert len(chunks) >= artifact.chapter_count
    assert all(len(chunk.text.split()) <= TTS_CHUNK_MAX_WORDS
               and len(chunk.text.encode("utf-8")) <= TTS_CHUNK_MAX_BYTES for chunk in chunks)
    assert " ".join(chunk.text for chunk in chunks) == artifact.narration_text
    save_narration_artifact(artifact, production_id=PID, unit_id=UID, root=tmp_path,
                           source_asset=asset, source_transcript=transcript)
    assert load_narration_artifact(PID, UID, root=tmp_path).model_dump() == artifact.model_dump()


def test_epic_chapter_resume_does_not_discard_twelve_completed_chapters(tmp_path):
    asset, transcript = story_evidence(duration=14400, count=720)
    budget = resolve_recap_budget(14400, target_length_preset="epic")
    checkpoint = tmp_path / "narration_chapters.partial.json"
    class Interrupted(ChapterNarrationProvider):
        def generate(self, prompt, *args, **kwargs):
            evidence = chapter_prompt_evidence(prompt)
            if evidence["chapter_index"] == 13:
                raise ValueError("hermetic chapter interruption")
            return super().generate(prompt, *args, **kwargs)
    with pytest.raises(ValueError, match="interruption"):
        build_chapter_plan(PID, UID, asset, transcript, budget=budget, prov=Interrupted(),
                           checkpoint_path=checkpoint, context_tokens=128000)
    assert len(json.loads(checkpoint.read_text())["chapters"]) == 12
    provider = ChapterNarrationProvider()
    artifact = build_chapter_plan(PID, UID, asset, transcript, budget=budget, prov=provider,
                                  checkpoint_path=checkpoint, context_tokens=128000)
    assert artifact.chapter_count == 16 and len(provider.calls) == 4
    assert json.loads(checkpoint.read_text())["complete"]