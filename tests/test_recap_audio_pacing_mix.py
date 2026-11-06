"""Local signal-level audio tests: brisk speech timing and retained film bed."""
from __future__ import annotations

import math
import struct
import subprocess
from types import SimpleNamespace

import pytest

from app.assembly.ffmpeg import probe_media, resolve_ffmpeg, run_ffmpeg
from app.production.narration import _pace_long_narration, synthesize_narration_audio
from app.production.recap_budget import resolve_recap_budget
from app.production.synthesis import recap_rate, recap_voice, spoken_recap_text, tighten_narration_silence
from app.voice.validator import validate_audio_file


def test_recap_defaults_and_explicit_overrides(monkeypatch):
    monkeypatch.delenv("AVM_RECAP_TTS_RATE", raising=False)
    monkeypatch.delenv("AVM_RECAP_TTS_VOICE", raising=False)
    assert recap_rate() == "+15%" and recap_voice() == "en-US-AndrewMultilingualNeural"
    monkeypatch.setenv("AVM_RECAP_TTS_RATE", "+20%")
    monkeypatch.setenv("AVM_RECAP_TTS_VOICE", "en-US-BrianMultilingualNeural")
    assert recap_rate() == "+20%" and recap_rate("+5%") == "+5%"
    assert recap_voice() == "en-US-BrianMultilingualNeural" and recap_voice("en-US-GuyNeural") == "en-US-GuyNeural"
    assert spoken_recap_text("Knowing the danger,\n\n she hides Ash.\tThe hunters follow.") == "Knowing the danger, she hides Ash. The hunters follow."


@pytest.mark.parametrize("rate", [15, "15%", "+99%", "-90%", "fast"])
def test_invalid_rate_is_rejected(rate):
    with pytest.raises(ValueError): recap_rate(rate)


def test_dead_gaps_are_tightened_but_speech_samples_are_preserved(tmp_path):
    path = tmp_path / "speech.wav"
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000:duration=6",
                "-af", "volume=enable='between(t,0,0.7)+between(t,2.0,3.5)+between(t,5.4,6)':volume=0", str(path)], "hermetic pause fixture")
    before = validate_audio_file(path)
    after, removed = tighten_narration_silence(path, before)
    assert after.valid and removed > 1.5 and 3.9 < after.duration < 4.5
    assert not (tmp_path / "speech.tight.wav").exists()


def test_sparse_script_is_never_stretched_into_slow_nine_minute_narration(tmp_path):
    path = tmp_path / "unused.wav"
    artifact = SimpleNamespace(planning_strategy="multi_chapter", narration_budget=resolve_recap_budget(1800),
                               narration_text=" ".join(["word"] * 1172))
    with pytest.raises(ValueError, match="word density"):
        _pace_long_narration(path, artifact, SimpleNamespace(valid=True, duration=473))


def test_synthesis_passes_brisk_rate_and_one_continuous_text_request(tmp_path, monkeypatch):
    from tests.test_track9_narration_synthesis import _artifact
    calls = []
    class LocalTTS:
        name = "edge_tts"
        def is_available(self): return True
        async def generate(self, **kwargs):
            calls.append(kwargs)
            run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000", "-t", "4", str(kwargs["output_path"])], "hermetic TTS fixture")
    monkeypatch.setattr("app.production.narration.get_provider", lambda name: LocalTTS())
    result = synthesize_narration_audio(_artifact(), root=tmp_path, provider_name="edge_tts", output_format="wav")
    assert len(calls) == 1 and calls[0]["rate"] == "+15%"
    assert calls[0]["voice"] == "en-US-AndrewMultilingualNeural"
    assert result.synthesis_rate == "+15%" and result.words_per_minute > 0


def _spectral_amplitude(path, frequency):
    # Decode only a synthetic test fixture through FFmpeg, never a tool file read.
    response = subprocess.run([resolve_ffmpeg(), "-v", "error", "-ss", "1", "-i", str(path), "-t", "2",
                               "-vn", "-ac", "1", "-ar", "12000", "-f", "f32le", "pipe:1"],
                              capture_output=True, check=True, timeout=10)
    samples = struct.unpack("<" + "f" * (len(response.stdout) // 4), response.stdout)
    real = sum(sample * math.cos(2 * math.pi * frequency * i / 12000) for i, sample in enumerate(samples))
    imag = sum(sample * math.sin(2 * math.pi * frequency * i / 12000) for i, sample in enumerate(samples))
    return 2 * math.hypot(real, imag) / len(samples)


@pytest.mark.parametrize("has_source_audio", [True, False])
@pytest.mark.parametrize("multi_chapter", [True, False])
def test_real_summary_mix_retains_quiet_film_bed_without_halving_voice(tmp_path, has_source_audio, multi_chapter):
    from app.production.models import NarrativeBeat, NarrationSourceRef, NarrationArtifact, NarrationAudioArtifact
    from app.production.summary_video import assemble_narrated_summary
    from tests.test_track9_phase3_a import _reg, PID, UID
    source = tmp_path / "movie.mp4"
    audio_input = ["-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000", "-c:a", "aac"] if has_source_audio else []
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", *audio_input,
                "-t", "12", "-c:v", "libx264", "-preset", "ultrafast", str(source)], "hermetic movie source")
    asset = _reg(tmp_path, source)
    beats = [NarrativeBeat(order=i + 1, title="Escape", narration_text="The keeper protects Ash. Knowing the hunters are close, her companion opens the escape route.",
             source_refs=[NarrationSourceRef(asset_id=asset.asset_id, start_seconds=i * 6, end_seconds=i * 6 + 5.5,
                                           transcript_segment_ids=[i])], transcript_refs=[str(i)]) for i in range(2)]
    narration = NarrationArtifact(narration_id=f"narr-{PID}-{UID}", production_id=PID, unit_id=UID,
        source_asset_id=asset.asset_id, target_duration_seconds=9, beats=beats,
        source_refs=[ref for beat in beats for ref in beat.source_refs], transcript_refs=["0", "1"],
        narration_text=" ".join(beat.narration_text for beat in beats), generated_at="2026-11-04T00:00:00")
    if multi_chapter: narration.planning_strategy = "multi_chapter"
    voice = tmp_path / PID / "units" / UID / "plan" / "narration_audio.wav"
    voice.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "9", str(voice)], "hermetic narrator")
    audio = NarrationAudioArtifact(narration_id=narration.narration_id, production_id=PID, unit_id=UID,
        audio_path=voice.relative_to(tmp_path / PID).as_posix(), provider="fixture-tts", voice="fixture",
        format="wav", duration_seconds=9, file_size=voice.stat().st_size, generated_at="2026-11-04T00:00:00")
    artifact = assemble_narrated_summary(PID, UID, root=tmp_path, narration_artifact=narration, narration_audio=audio)
    output = tmp_path / PID / artifact.output_path
    assert probe_media(output).has_audio and abs(probe_media(output).duration - 9) < .25
    assert artifact.background_audio_mixed == has_source_audio
    assert artifact.background_audio_volume == (.05 if has_source_audio else 0)
    ratio = _spectral_amplitude(output, 220) / _spectral_amplitude(output, 440)
    assert (.012 < ratio < .055) if has_source_audio else ratio < .01
    assert _spectral_amplitude(output, 440) / _spectral_amplitude(voice, 440) > .9
    assert artifact.audio_processing["amix_normalize"] is False


def test_live_mix_verifier_decodes_local_audio_without_emitting_pcm(tmp_path):
    from tools.verify_recap_audio_mix import _decode
    path = tmp_path / "verifier-tone.wav"
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=350:sample_rate=24000", "-t", "2", str(path)], "local verifier fixture")
    samples = _decode(path, .25, 1, 4000)
    assert 3900 <= len(samples) <= 4100
    assert max(samples) > .05 and min(samples) < -.05