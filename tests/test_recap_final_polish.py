"""Hermetic broadcast/true-ending contracts with real local FFmpeg signals."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.production.models import NarrativeBeat, NarrationSourceRef
from app.production.narration_real import resolve_ending_anchor, validate_true_ending
from app.production.synthesis import (
    master_narration_audio, measure_audio_loudness, validate_broadcast_loudness,
)
from tests.track9_narration_fakes import story_evidence


def _beat(order, start, end, stamp=None):
    return NarrativeBeat(order=order, title="Ending consequence", narration_text=(
        "The companions face the consequences of their choice. The story ends with their fate unresolved."),
        source_refs=[NarrationSourceRef(asset_id="src-001", start_seconds=start, end_seconds=end,
                     visual_frame_timestamps=[stamp] if stamp is not None else [])])


def test_absent_dialogue_is_not_a_credits_detector():
    asset, transcript = story_evidence()
    transcript.segments = [s for s in transcript.segments if s.end < 1650]
    anchor = resolve_ending_anchor(asset, transcript)
    assert anchor.narrative_end_seconds == 1800 and anchor.excluded_tail_seconds == 0
    assert anchor.finale_start_seconds == pytest.approx(1656)


@pytest.mark.parametrize("explicit_flag", [True, False])
def test_only_explicit_non_narrative_tail_excludes_credits(explicit_flag):
    asset, transcript = story_evidence()
    transcript.segments = [s for s in transcript.segments if s.end < 1700]
    notes = [{"timestamp_seconds": 1700, "action": "The companions escape."},
             {"timestamp_seconds": 1720, "action": "Names scroll over a black background." if explicit_flag else "End credits",
              "credits_only": explicit_flag},
             {"timestamp_seconds": 1780, "action": "End credits", "credits_only": True}]
    anchor = resolve_ending_anchor(asset, transcript, notes)
    assert anchor.narrative_end_seconds == 1720 and anchor.excluded_tail_seconds == 80
    assert anchor.detection_method == "explicit_credits_only_tail"


def test_credits_over_ongoing_dialogue_do_not_truncate_the_story():
    asset, transcript = story_evidence()
    anchor = resolve_ending_anchor(asset, transcript, [
        {"timestamp_seconds": 1780, "action": "End credits", "credits_only": True}])
    assert anchor.narrative_end_seconds == 1800


@pytest.mark.parametrize("fault", ["early", "nonvisual", "misses_last_scene", "credits"])
def test_closing_beats_cannot_skip_or_fake_the_ending(fault):
    asset, transcript = story_evidence()
    anchor = resolve_ending_anchor(asset, transcript, narrative_end_seconds=1798)
    beats = [_beat(1, 1680, 1690, 1685), _beat(2, 1750, 1760, 1755), _beat(3, 1790, 1798, 1795)]
    if fault == "early": beats[0] = _beat(1, 1600, 1610, 1605)
    elif fault == "nonvisual": beats[1] = _beat(2, 1750, 1760)
    elif fault == "misses_last_scene": beats[-1] = _beat(3, 1770, 1780, 1775)
    else: beats[-1] = _beat(3, 1790, 1800, 1795)
    with pytest.raises(ValueError, match="Final|closing"):
        validate_true_ending(beats, anchor, require_visual=True)


def test_one_token_ending_beat_is_not_three_closing_beats():
    asset, transcript = story_evidence()
    with pytest.raises(ValueError, match="too few"):
        validate_true_ending([_beat(1, 1790, 1800, 1795)], resolve_ending_anchor(asset, transcript))


def test_last_three_beats_accept_a_grounded_unresolved_part_one_ending():
    asset, transcript = story_evidence()
    anchor = resolve_ending_anchor(asset, transcript)
    validate_true_ending([_beat(1, 1680, 1690, 1685), _beat(2, 1760, 1770, 1765),
                          _beat(3, 1792, 1800, 1795)], anchor, require_visual=True)


@pytest.mark.parametrize("duration", [5.0, 10.0, 18.0])
def test_sparse_final_shots_keep_the_last_citation_instead_of_the_first(duration):
    from app.production.summary_video import _paced_visual_shots
    refs = [NarrationSourceRef(asset_id="src-001", start_seconds=t, end_seconds=t + 5)
            for t in (1700, 1720, 1740, 1795)]
    beat = _beat(1, 1700, 1800)
    beat.source_refs = refs
    # A second beat makes this a closing scene, not the opening-only case.
    opening = _beat(1, 0, 6)
    beat.order = 2
    artifact = SimpleNamespace(beats=[opening, beat], planning_strategy="multi_chapter", narration_budget=None)
    shots = _paced_visual_shots(artifact, duration)
    assert shots[-1][1].end_seconds == pytest.approx(1800)
    assert shots[0][1].start_seconds == 0


def test_broadcast_master_measures_input_rms_and_preserves_duration(tmp_path):
    from app.assembly.ffmpeg import run_ffmpeg
    from app.voice.validator import validate_audio_file
    path = tmp_path / "quiet-voice.mp3"
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
                "-af", "volume=0.55", "-c:a", "libmp3lame", str(path)], "quiet hermetic narrator")
    original = validate_audio_file(path)
    result, metrics = master_narration_audio(path, original)
    assert result.duration == pytest.approx(original.duration, abs=.1)
    assert metrics["before"]["integrated_lufs"] < -25
    assert metrics["after"]["integrated_lufs"] == pytest.approx(-14, abs=.5)
    assert metrics["after"]["true_peak_db"] <= -1
    assert metrics["after"]["rms_db"] > metrics["before"]["rms_db"] + 8
    validate_broadcast_loudness(measure_audio_loudness(path))
    assert not path.with_name("quiet-voice.broadcast.mp3").exists()


def test_a_short_true_ending_citation_cannot_lose_to_a_long_battle_reference():
    from app.production.summary_video import _paced_visual_shots
    opening = _beat(1, 0, 8)
    ending = _beat(2, 1700, 1720)
    ending.source_refs.append(NarrationSourceRef(asset_id="src-001", start_seconds=1798, end_seconds=1800))
    narration = SimpleNamespace(beats=[opening, ending], planning_strategy="multi_chapter")
    shots = _paced_visual_shots(narration, 12)
    assert shots[-1][1].end_seconds == 1800


def test_silent_audio_cannot_be_claimed_as_a_broadcast_narrator(tmp_path):
    from app.assembly.ffmpeg import run_ffmpeg
    from app.voice.validator import validate_audio_file
    path = tmp_path / "silence.wav"
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "3", str(path)], "local silence")
    with pytest.raises(ValueError, match="Silent|finite"):
        master_narration_audio(path, validate_audio_file(path))


def test_vocal_pocket_eq_reduces_1500hz_but_retains_real_movie_bed(tmp_path):
    from app.assembly.ffmpeg import run_ffmpeg
    from app.production.summary_video import RECAP_BED_EQ
    from tests.test_recap_audio_pacing_mix import _spectral_amplitude
    original, carved = tmp_path / "bed.wav", tmp_path / "carved.wav"
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=1500:sample_rate=48000:duration=4", str(original)], "midrange fixture")
    run_ffmpeg(["-y", "-i", str(original), "-af", RECAP_BED_EQ, str(carved)], "vocal pocket fixture")
    ratio = _spectral_amplitude(carved, 1500) / _spectral_amplitude(original, 1500)
    assert .45 < ratio < .55


def test_chapter_prompts_scope_hook_and_finale_to_story_edges():
    from app.production.narration_chapters import build_chapter_plan
    from app.production.recap_budget import resolve_recap_budget
    from tests.test_track9_extended_chapters import frame_loader
    from tests.track9_narration_fakes import ChapterNarrationProvider
    asset, transcript = story_evidence()
    provider = ChapterNarrationProvider()
    artifact = build_chapter_plan("prd_finale_prompt", "unit-001", asset, transcript,
        budget=resolve_recap_budget(asset.duration), prov=provider, frame_loader=frame_loader, context_tokens=128000)
    assert "stakes-hook" in provider.chapters[0]["cold_open_instruction"]
    assert all("cold_open_instruction" not in p for p in provider.chapters[1:])
    assert provider.chapters[-1]["ending_anchor"]["finale_start_seconds"] == pytest.approx(1656)
    assert all("ending_anchor" not in p for p in provider.chapters[:-1])
    assert artifact.ending_anchor.required_final_beats == 3


@pytest.mark.parametrize("multi_chapter", [True, False])
def test_real_final_video_shot_reaches_verified_story_end_not_credits(tmp_path, multi_chapter):
    from app.assembly.ffmpeg import run_ffmpeg, probe_media
    from app.production.models import NarrationArtifact, NarrationAudioArtifact, NarrationChapter, SourceTranscript
    from app.production.summary_video import assemble_narrated_summary
    from app.production.narration_real import align_final_story_clip
    from tests.test_track9_phase3_a import _reg, PID, UID
    source = tmp_path / "movie-with-end-card.mp4"
    # Moving story for 119s, followed by an explicit red end card for 1s.
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12:duration=119",
                "-f", "lavfi", "-i", "color=red:size=160x90:rate=12:duration=1", "-filter_complex",
                "[0:v][1:v]concat=n=2:v=1:a=0[out]", "-map", "[out]", "-c:v", "libx264",
                "-preset", "ultrafast", str(source)], "hermetic true ending and credits fixture")
    asset = _reg(tmp_path, source)
    transcript = SourceTranscript(asset_id=asset.asset_id, language="en", segments=[], provider="fixture",
                                   created_at="2026-11-05T00:00:00", transcript_path="source/src-001.transcript.json")
    anchor = resolve_ending_anchor(asset, transcript, narrative_end_seconds=119)
    beats = [_beat(1, 0, 5, 2), _beat(2, 110, 112.5, 111),
             _beat(3, 113, 115.5, 114), _beat(4, 116, 118.9, 118)]
    align_final_story_clip(beats, anchor, require_visual=True)
    narration = NarrationArtifact(narration_id=f"narr-{PID}-{UID}", production_id=PID, unit_id=UID,
        source_asset_id=asset.asset_id, target_duration_seconds=8, beats=beats,
        source_refs=[r for b in beats for r in b.source_refs], narration_text=" ".join(b.narration_text for b in beats),
        ending_anchor=anchor, generated_at="2026-11-05T00:00:00", summary_mode="pro")
    narration.chapters = [NarrationChapter(chapter_index=1, start_seconds=0, end_seconds=120,
        beat_start_order=1, beat_end_order=4, word_count=len(narration.narration_text.split()),
        frame_timestamps=[2, 111, 114, 118], summary="The source ends before its credits.")]
    narration.visual_frame_count = 4
    # Branch-only fixture; chapter provenance is validated separately.
    if multi_chapter: narration.planning_strategy = "multi_chapter"
    audio_path = tmp_path / PID / "units" / UID / "plan" / "narration_audio.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8", str(audio_path)], "local finale voice")
    audio = NarrationAudioArtifact(narration_id=narration.narration_id, production_id=PID, unit_id=UID,
        audio_path=audio_path.relative_to(tmp_path / PID).as_posix(), provider="fixture", voice="fixture", format="wav",
        duration_seconds=8, file_size=audio_path.stat().st_size, generated_at="2026-11-05T00:00:00")
    result = assemble_narrated_summary(PID, UID, root=tmp_path, narration_artifact=narration, narration_audio=audio)
    assert probe_media(tmp_path / PID / result.output_path).has_video
    last = result.validation["shot_manifest"][-1]
    assert last["beat_order"] == 4 and last["source_end_seconds"] == pytest.approx(119)
    assert last["output_end_seconds"] == pytest.approx(8, abs=.04)


def test_selective_polish_reuses_all_other_beats_and_never_calls_vision(tmp_path, monkeypatch):
    import json
    from tools import polish_recap
    from app.production.narration_chapters import build_chapter_plan
    from app.production.recap_budget import resolve_recap_budget
    from app.production.storage import save_narration_artifact
    from app.production.visual_cache import save_frame_observations
    from tests.test_track9_extended_chapters import frame_loader
    from tests.track9_narration_fakes import ChapterNarrationProvider
    asset, transcript = story_evidence()
    pid, uid = "prd_cache_only_polish", "unit-001"
    artifact = build_chapter_plan(pid, uid, asset, transcript, budget=resolve_recap_budget(asset.duration),
        prov=ChapterNarrationProvider(), frame_loader=frame_loader, context_tokens=128000)
    artifact.summary_understanding_model_id = "gpt-6.1-sol"
    artifact.source_media_path = asset.stored_path
    artifact.source_transcript_path = transcript.transcript_path
    # Seed an older accepted narration, without the new hook/finale metadata.
    artifact.ending_anchor, artifact.cold_open_hook = None, None
    root = tmp_path / "productions"
    plan = root / pid / "units" / uid / "plan"
    save_narration_artifact(artifact, production_id=pid, unit_id=uid, root=root)
    sidecar = root / pid / transcript.transcript_path
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(transcript.model_dump_json(), encoding="utf-8")
    for chapter in artifact.chapters:
        frames = [(ts, "not-read") for ts in chapter.frame_timestamps]
        notes = [{"frame_id": i, "timestamp_seconds": ts, "action": "The companions confront the danger."}
                 for i, (ts, _) in enumerate(frames)]
        save_frame_observations(plan / "frame_observations.json", asset, transcript, frames, notes,
                                start=chapter.start_seconds, end=chapter.end_seconds)
    before = [b.model_dump() for b in artifact.beats[1:-3]]
    calls = []
    def local_json(prov, system, prompt, **kwargs):
        assert "JSON" in prompt
        payload = json.loads(prompt.split("evidence:\n", 1)[1].split("\nReturn JSON", 1)[0])
        calls.append(payload)
        frames = payload["visual_action_notes"]
        count = payload["beat_count"]
        selected = [frames[round(i * (len(frames) - 1) / max(1, count - 1))] for i in range(count)]
        text = ("Their freedom depends on a discovery the guards cannot control. "
                "The companions face a dangerous choice as the conflict reaches its final moment, "
                "leaving their fate unresolved at the end.")
        return {"summary": "The conflict ends unresolved.", "beats": [
            {"title": "Stakes and consequence", "narration_text": text,
             "source_refs": [{"frame_ids": [frame["frame_id"]], "transcript_segment_ids": []}]} for frame in selected]}
    monkeypatch.setattr(polish_recap, "ROOT", tmp_path)
    monkeypatch.setattr("app.production.storage.load_production", lambda *a, **k: SimpleNamespace(source_assets=[asset]))
    monkeypatch.setattr("app.content.model_resolver.resolve_model", lambda *a, **k: SimpleNamespace(provider_instance=object()))
    monkeypatch.setattr("app.production.narration_real._call_json", local_json)
    polished = polish_recap.polish(pid, uid)
    assert len(calls) == 2 and [b.model_dump() for b in polished.beats[1:-3]] == before
    assert polished.ending_anchor.narrative_end_seconds == 1800 and polished.cold_open_hook
    assert (plan / "narration_polish.json").is_file()
    polish_recap.polish(pid, uid)
    assert len(calls) == 2  # completed closeout never repeats provider requests


def test_closeout_verifier_decodes_synthetic_frames_without_vision(tmp_path):
    from app.assembly.ffmpeg import run_ffmpeg
    from tools.verify_recap_closeout import _gray_frame, _frame_correlation
    source, encoded = tmp_path / "moving-source.mp4", tmp_path / "encoded-cut.mp4"
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=3",
                "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", str(source)], "local frame-verification fixture")
    run_ffmpeg(["-y", "-ss", "1", "-i", str(source), "-t", "1", "-an", "-c:v", "libx264",
                "-preset", "ultrafast", "-threads", "1", str(encoded)], "local source-backed cut")
    assert _frame_correlation(_gray_frame(source, 1.5), _gray_frame(encoded, .5)) > .95


def test_closeout_verifier_refuses_blank_frame_evidence():
    from tools.verify_recap_closeout import _frame_correlation
    with pytest.raises(ValueError, match="blank"):
        _frame_correlation(bytes(64 * 36), bytes(64 * 36))


def test_final_chapter_coverage_uses_active_story_not_a_long_credits_tail():
    from app.production.narration_chapters import _chapter_beats
    asset, transcript = story_evidence()
    notes = [{"timestamp_seconds": 1660, "action": "The companions reach the outcome."},
             {"timestamp_seconds": 1680, "action": "End credits", "credits_only": True},
             {"timestamp_seconds": 1790, "action": "End credits", "credits_only": True}]
    transcript.segments = [s for s in transcript.segments if s.end <= 1680]
    anchor = resolve_ending_anchor(asset, transcript, notes)
    assert anchor.narrative_end_seconds == 1680
    frames = [(1450, "not-read"), (1520, "not-read"), (1590, "not-read"),
              (1668, "not-read"), (1740, "credits-not-citable")]
    rows = [{"id": i, "start_seconds": s.start, "end_seconds": s.end, "text": s.text}
            for i, s in enumerate(transcript.segments) if s.start >= 1440]
    data = {"summary": "The companions reach the ending before the credits.", "beats": [
        {"title": "Story", "narration_text": "The companions face the outcome. The conflict ends here.",
         "source_refs": [{"frame_ids": [i], "transcript_segment_ids": []}]} for i in range(4)]}
    _, beats = _chapter_beats(data, asset, rows, frames, 1440, anchor.narrative_end_seconds,
                              visual=True, offset=0)
    assert beats[-1].source_refs[-1].end_seconds < anchor.narrative_end_seconds
    with pytest.raises(ValueError, match="ending"):
        _chapter_beats(data, asset, rows, frames, 1440, asset.duration, visual=True, offset=0)