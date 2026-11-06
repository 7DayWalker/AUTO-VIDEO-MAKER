"""Bounded and resumable TTS with local tones, never Edge or provider calls."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.assembly.ffmpeg import run_ffmpeg
from app.production.synthesis import (
    TTS_CHUNK_MAX_BYTES, TTS_CHUNK_MAX_WORDS, TTS_CHUNK_TRIGGER_WORDS,
    recap_tts_chunks, spoken_recap_text, synthesize_recap_segments,
)
from app.voice.validator import validate_audio_file


def script_fixture(word_count=5000, chapters=16):
    counts = [(word_count * (index + 1) // chapters) - word_count * index // chapters for index in range(chapters)]
    beats = [SimpleNamespace(order=index + 1, narration_text=" ".join(f"word{index}_{i}" for i in range(count)) + ".")
             for index, count in enumerate(counts)]
    chapter_rows = [SimpleNamespace(chapter_index=index + 1, beat_start_order=index + 1, beat_end_order=index + 1)
                    for index in range(chapters)]
    return SimpleNamespace(narration_text=" ".join(beat.narration_text for beat in beats), beats=beats, chapters=chapter_rows)


@pytest.mark.parametrize("words,chapters", [(2050, 8), (3750, 12), (5000, 16), (5400, 16), (5950, 16)])
def test_long_scripts_have_bounded_chapter_payloads_without_lost_or_repeated_words(words, chapters):
    artifact = script_fixture(words, chapters)
    chunks = recap_tts_chunks(artifact)
    assert len(chunks) == chapters
    assert " ".join(chunk.text for chunk in chunks) == spoken_recap_text(artifact.narration_text)
    assert [chunk.chapter_index for chunk in chunks] == list(range(1, chapters + 1))
    assert all(len(chunk.text.split()) <= TTS_CHUNK_MAX_WORDS
               and len(chunk.text.encode("utf-8")) <= TTS_CHUNK_MAX_BYTES for chunk in chunks)


def test_small_scripts_stay_one_request_and_long_unstructured_text_is_bounded():
    assert len(recap_tts_chunks(script_fixture(TTS_CHUNK_TRIGGER_WORDS, 4))) == 1
    artifact = script_fixture(TTS_CHUNK_TRIGGER_WORDS + 1, 1)
    chunks = recap_tts_chunks(artifact)
    assert len(chunks) > 1 and all(len(chunk.text.split()) <= TTS_CHUNK_MAX_WORDS for chunk in chunks)
    unicode_text = " ".join(["电影人物的故事" * 6] * 1000)
    artifact.narration_text = unicode_text  # An approved edit differs from stale beat prose.
    chunks = recap_tts_chunks(artifact)
    assert " ".join(chunk.text for chunk in chunks) == unicode_text
    assert all(len(chunk.text.encode("utf-8")) <= TTS_CHUNK_MAX_BYTES for chunk in chunks)
    artifact.narration_text = "x" * (TTS_CHUNK_MAX_BYTES + 1) + " " + "word " * 2000
    with pytest.raises(ValueError, match="token"):
        recap_tts_chunks(artifact)


def test_long_form_tts_deadlines_allow_real_time_chapters_below_ui_operation_budget():
    from app.production.synthesis import TTS_REQUEST_TIMEOUT_SECONDS, TTS_SYNTHESIS_TIMEOUT_SECONDS
    # A 450-word chapter at 150WPM needs 180s even without startup overhead.
    assert TTS_REQUEST_TIMEOUT_SECONDS >= TTS_CHUNK_MAX_WORDS * 60 / 150 + 30
    assert TTS_REQUEST_TIMEOUT_SECONDS < TTS_SYNTHESIS_TIMEOUT_SECONDS < 1800


def test_bounded_batches_prefer_complete_beats_instead_of_mid_sentence_cuts():
    artifact = script_fixture(4200, 60)
    artifact.chapters = []
    chunks = recap_tts_chunks(artifact)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text.endswith(".")
        assert len(chunk.text.split()) % 70 == 0
    assert " ".join(chunk.text for chunk in chunks) == artifact.narration_text


class LocalTTS:
    name = "fixture-tts"
    def __init__(self, *, fail_at=None):
        self.calls = []
        self.fail_at = fail_at
    def is_available(self):
        return True
    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_at == len(self.calls):
            raise RuntimeError("local TTS interruption")
        assert len(kwargs["text"].split()) <= TTS_CHUNK_MAX_WORDS
        assert len(kwargs["text"].encode("utf-8")) <= TTS_CHUNK_MAX_BYTES
        run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "0.35",
                    "-c:a", "libmp3lame", "-b:a", "192k", str(kwargs["output_path"])], "hermetic bounded TTS tone")


def synthesize(artifact, provider, path):
    return asyncio.run(synthesize_recap_segments(artifact, provider, path, voice="fixture-voice",
                       rate="+15%", pitch="+0Hz", volume="+0%"))


def test_interrupted_tts_resumes_validated_pcm_and_concatenates_exactly_once(tmp_path):
    artifact = script_fixture()
    output = tmp_path / "narration.wav"
    provider = LocalTTS(fail_at=4)
    with pytest.raises(RuntimeError, match="interruption"):
        synthesize(artifact, provider, output)
    checkpoint = next(tmp_path.rglob("checkpoint.json"))
    assert len(json.loads(checkpoint.read_text())["chunks"]) == 3
    assert not output.exists() and not list(tmp_path.rglob("*.partial.*"))
    resumed = LocalTTS()
    metadata = synthesize(artifact, resumed, output)
    assert metadata["chunk_count"] == 16 and metadata["resumed_chunks"] == 3
    assert len(resumed.calls) == 13
    assert metadata["concat_method"] == "ffmpeg_concat_demuxer"
    measured = validate_audio_file(output)
    assert measured.valid and measured.codec == "pcm_s24le" and measured.sample_rate == 48000
    assert measured.duration == pytest.approx(sum(chunk["duration_seconds"] for chunk in metadata["chunks"]), abs=.02)
    assert " ".join(call["text"] for call in provider.calls[:3] + resumed.calls) == artifact.narration_text
    assert all(call["voice"] == "fixture-voice" and call["rate"] == "+15%" for call in resumed.calls)
    untouched = LocalTTS()
    assert synthesize(artifact, untouched, output)["resumed_chunks"] == 16
    assert not untouched.calls


def test_cached_chunk_bytes_and_voice_identity_are_revalidated(tmp_path):
    artifact = script_fixture(2000, 5)
    output = tmp_path / "narration.wav"
    synthesize(artifact, LocalTTS(), output)
    corrupt = next(tmp_path.rglob("chunk_0002.wav"))
    corrupt.write_bytes(b"invalid cached audio")
    provider = LocalTTS()
    metadata = synthesize(artifact, provider, output)
    assert metadata["resumed_chunks"] == 4 and len(provider.calls) == 1
    assert validate_audio_file(output).valid
    new_voice = LocalTTS()
    metadata = asyncio.run(synthesize_recap_segments(artifact, new_voice, output, voice="different-voice",
                         rate="+15%", pitch="+0Hz", volume="+0%"))
    assert len(new_voice.calls) == 5 and metadata["resumed_chunks"] == 0


def test_chunk_timeout_is_bounded_and_leaves_no_completed_audio(tmp_path, monkeypatch):
    from app.voice.exceptions import VoiceProviderError
    monkeypatch.setattr("app.production.synthesis.TTS_REQUEST_TIMEOUT_SECONDS", .01)
    class HangingTTS(LocalTTS):
        async def generate(self, **kwargs):
            await asyncio.sleep(60)
    with pytest.raises(VoiceProviderError, match="timed out"):
        synthesize(script_fixture(2000, 5), HangingTTS(), tmp_path / "narration.wav")
    assert not (tmp_path / "narration.wav").exists()
    assert not list(tmp_path.rglob("*.partial.*"))


def test_full_synthesis_masters_once_after_concat_and_persists_chunk_metadata(tmp_path, monkeypatch):
    from app.production.narration import synthesize_narration_audio
    from app.production.synthesis import master_narration_audio
    from app.production.storage import load_narration_audio
    from tests.test_track9_narration_synthesis import _artifact
    script = script_fixture(2000, 5)
    artifact = _artifact()
    artifact.narration_text = script.narration_text
    artifact.beats[0].narration_text = artifact.narration_text
    provider, mastered = LocalTTS(), []
    def master(path, validation):
        assert len(provider.calls) >= 5 and validate_audio_file(path).valid
        mastered.append(path)
        return master_narration_audio(path, validation)
    monkeypatch.setattr("app.production.narration.get_provider", lambda name: provider)
    monkeypatch.setattr("app.production.synthesis.master_narration_audio", master)
    metadata = synthesize_narration_audio(artifact, root=tmp_path, provider_name="fixture-tts", output_format="wav")
    assert len(mastered) == 1
    assert metadata.audio_processing["normalization"] == "two_pass_loudnorm"
    assert metadata.audio_processing["after"]["integrated_lufs"] == pytest.approx(-14, abs=.5)
    assert metadata.audio_processing["synthesis"]["chunk_count"] >= 5
    assert load_narration_audio(artifact.production_id, artifact.unit_id, root=tmp_path) == metadata