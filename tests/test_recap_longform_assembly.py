"""Hundreds of recap cuts: bounded Windows argv and real tiny local concat."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.assembly.ffmpeg import probe_media, resolve_ffmpeg, run_ffmpeg
from app.production.media_concat import write_concat_manifest
from app.production.models import NarrativeBeat, NarrationArtifact, NarrationAudioArtifact, NarrationSourceRef
from app.production.summary_video import _concat_recap_cuts, assemble_narrated_summary


@pytest.mark.parametrize("count,seconds", [(120, 900), (240, 1200), (360, 1800), (400, 2100)])
@pytest.mark.parametrize("multi_chapter", [True, False])
def test_full_large_shot_assembly_uses_constant_inputs_and_retains_ducked_source(tmp_path, monkeypatch, count, seconds, multi_chapter):
    from app.production.storage import save_new_production, save_revision
    from tests.test_track9_phase3_a import _prod, PID, UID
    from tests.track9_narration_fakes import standard_evidence
    import app.production.summary_video as summary
    root = tmp_path / "productions"
    production = _prod()
    save_new_production(production, root=root)
    asset, _ = standard_evidence(14400)
    save_revision(production.model_copy(update={"plan_version": 2, "source_assets": [asset]}), root=root)
    source = root / PID / asset.stored_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"Synthetic media sentinel; probing and rendering are hermetically substituted.")
    refs = [NarrationSourceRef(asset_id=asset.asset_id, start_seconds=index * 30,
            end_seconds=index * 30 + 5.5, transcript_segment_ids=[index]) for index in range(count)]
    beats = [NarrativeBeat(order=index + 1, title=f"Event {index}",
             narration_text="A traveler faces a choice. Their companion finds an escape.",
             source_refs=[ref], transcript_refs=[str(index)]) for index, ref in enumerate(refs)]
    narration = NarrationArtifact(narration_id=f"narr-{PID}-{UID}", production_id=PID, unit_id=UID,
        source_asset_id=asset.asset_id, target_duration_seconds=seconds, narration_text=" ".join(beat.narration_text for beat in beats),
        beats=beats, source_refs=refs, transcript_refs=[str(i) for i in range(count)], generated_at="2026-11-06T00:00:00")
    if multi_chapter:
        narration.planning_strategy = "multi_chapter"
    voice = root / PID / "units" / UID / "plan" / "narration_audio.wav"
    voice.parent.mkdir(parents=True, exist_ok=True)
    voice.write_bytes(b"synthetic narration sentinel")
    audio = NarrationAudioArtifact(narration_id=narration.narration_id, production_id=PID, unit_id=UID,
        audio_path=voice.relative_to(root / PID).as_posix(), provider="fixture-tts", voice="fixture", format="wav",
        duration_seconds=seconds, file_size=voice.stat().st_size, generated_at="2026-11-06T00:00:00")
    media_durations = {str(source): 14400, str(voice): seconds}
    commands, manifest_entries = [], []
    def probe(path):
        return SimpleNamespace(has_video=Path(path).suffix == ".mp4", has_audio=True,
            duration=media_durations.get(str(path), seconds), width=320, height=180,
            video_codec="h264", audio_codec="aac")
    def clips(ordered, asset, source, scratch, **kwargs):
        paths = []
        for index in range(count):
            path = scratch / (f"clip_{index:04d}_" + "wide_windows_path_" * 4 + ".mp4")
            path.write_bytes(b"synthetic cut")
            media_durations[str(path)] = seconds / count
            paths.append(path)
        return paths
    def execute(args, description=""):
        # Measure CreateProcess's exact quoting rather than plain joined argv.
        length = len(subprocess.list2cmdline([resolve_ffmpeg() or "ffmpeg.exe", *args]))
        assert length < 32767
        assert args.count("-i") <= 2
        commands.append((args, length))
        if "concat" in args:
            manifest = Path(args[args.index("-i") + 1])
            rows = manifest.read_text(encoding="utf-8").splitlines()
            assert rows[0] == "ffconcat version 1.0"
            assert len(rows) == count + 1
            manifest_entries.extend(rows[1:])
        Path(args[-1]).write_bytes(b"synthetic assembled media")
        return SimpleNamespace(stderr="", returncode=0)
    monkeypatch.setattr(summary, "_paced_visual_shots", lambda *args: [(i + 1, ref) for i, ref in enumerate(refs)])
    monkeypatch.setattr(summary, "_build_intermediate_clips", clips)
    monkeypatch.setattr(summary, "run_ffmpeg", execute)
    monkeypatch.setattr(summary, "probe_media", probe)
    monkeypatch.setattr("app.assembly.ffmpeg.probe_media", probe)
    monkeypatch.setattr("app.assembly.pipeline.assemble_project", lambda *a, **k: pytest.fail("Hundreds of shots cannot use enumerated shared assembly inputs"))
    def master(src, dest, *, broadcast):
        shutil.copy2(src, dest)
        return {"amix_normalize": False}
    monkeypatch.setattr(summary, "_master_summary_mix", master)
    result = assemble_narrated_summary(PID, UID, root=root, narration_artifact=narration, narration_audio=audio)
    assert result.validation["video_assembly_method"] == "concat_demuxer_stream_copy"
    assert result.validation["concat_manifest_entries"] == count == len(manifest_entries)
    assert result.background_audio_mixed and result.background_audio_volume == .05
    assert result.output_duration_seconds == seconds
    assert result.validation["shot_manifest"][-1]["output_end_seconds"] == seconds
    assert max(length for _, length in commands) < 2500
    print(f"Validated {count}-shot / {seconds}s recap: maximum quoted FFmpeg argv {max(length for _, length in commands)} characters")
    mix = next(args for args, _ in commands if "-filter_complex" in args)
    filters = mix[mix.index("-filter_complex") + 1]
    assert "volume=0.0500" in filters and "equalizer=f=1500:t=q:w=1.0:g=-6" in filters
    assert "sidechaincompress=" in filters and "normalize=0" in filters
    assert result.validation["freeze_padding"] is False


@pytest.mark.parametrize("has_audio", [True, False])
def test_360_real_tiny_moving_cuts_concat_without_argv_growth(tmp_path, has_audio):
    source = tmp_path / "moving-cut.mp4"
    audio = ["-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000", "-c:a", "aac", "-ac", "2"] if has_audio else ["-an"]
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=24", *audio, "-t", "0.125",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source)], "hermetic three-frame cut")
    cut_duration = probe_media(source).duration
    output = _concat_recap_cuts([source] * 360, tmp_path)
    measured = probe_media(output)
    assert measured.has_video and measured.has_audio == has_audio
    assert measured.duration == pytest.approx(cut_duration * 360, abs=.15)
    rows = (tmp_path / "recap_cuts.ffconcat").read_text().splitlines()
    assert len(rows) == 361


def test_concat_manifest_handles_spaces_apostrophes_and_unicode_with_no_shell(tmp_path):
    directory = tmp_path / "a director's 电影 clips"
    directory.mkdir()
    cut = directory / "chapter one.mp4"
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=24", "-t", "0.25",
                "-c:v", "libx264", "-preset", "ultrafast", str(cut)], "hermetic quoted-path cut")
    output = _concat_recap_cuts([cut, cut], tmp_path)
    assert probe_media(output).duration == pytest.approx(.5, abs=.1)
    with pytest.raises(ValueError, match="control"):
        write_concat_manifest(tmp_path / "invalid.ffconcat", [tmp_path / "bad\npath.mp4"])
    with pytest.raises(ValueError, match="at least one"):
        write_concat_manifest(tmp_path / "empty.ffconcat", [])


@pytest.mark.parametrize("seconds,count,fps", [(900, 170, 24), (1200, 240, 30), (1800, 360, 29.97), (2100, 400, 24)])
def test_hundreds_of_cut_boundaries_do_not_accumulate_frame_rounding(seconds, count, fps):
    from app.production.summary_video import _quantized_clip_frames
    durations = [seconds / count] * count
    frames = _quantized_clip_frames(durations, fps)
    assert len(frames) == count
    assert sum(frames) / fps == pytest.approx(seconds, abs=.5 / fps + 1e-6)
    assert all(abs(actual / fps - planned) <= 1 / fps + 1e-6 for actual, planned in zip(frames, durations))


@pytest.mark.parametrize("has_audio", [True, False])
def test_real_frame_quantized_clips_preserve_source_bounds_and_concat_timing(tmp_path, has_audio):
    from app.production.summary_video import _extract_clip_range, _quantized_clip_frames
    source = tmp_path / "source.mp4"
    audio = ["-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000", "-c:a", "aac", "-ac", "2"] if has_audio else ["-an"]
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=24", *audio, "-t", "2",
                "-c:v", "libx264", "-preset", "ultrafast", str(source)], "hermetic frame quantization source")
    durations = [.175] * 5
    counts = _quantized_clip_frames(durations, 24)
    clips = []
    for index, (duration, count) in enumerate(zip(durations, counts)):
        clip = tmp_path / f"clip_{index}.mp4"
        _extract_clip_range(source, index * .25, duration, clip, source_has_audio=has_audio,
                            frame_count=count, frame_rate=24)
        clips.append(clip)
    output = _concat_recap_cuts(clips, tmp_path)
    measured = probe_media(output)
    assert measured.has_video and measured.has_audio == has_audio
    assert measured.duration == pytest.approx(sum(counts) / 24, abs=.04)