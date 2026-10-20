from __future__ import annotations

from pathlib import Path

import pytest

from app.production.models import NarrativeBeat, NarrationArtifact, NarrationSourceRef
from app.production.narration import synthesize_narration_audio
from app.production.storage import (
    load_narration_artifact,
    load_narration_audio,
    narration_audio_metadata_path,
    narration_audio_path,
)
from app.voice.exceptions import VoiceProviderError
from app.voice.generator import get_provider
from app.voice.validator import validate_audio_file


class _UnavailableProvider:
    name = "edge_tts"

    def is_available(self) -> bool:
        return False

    async def generate(self, **kwargs) -> None:
        raise AssertionError("unavailable provider must not generate")


def _artifact() -> NarrationArtifact:
    source_ref = NarrationSourceRef(
        asset_id="src-001", start_seconds=0.0, end_seconds=8.0,
        transcript_segment_ids=[0], excerpt="A grounded source excerpt.",
    )
    beat = NarrativeBeat(
        order=1, title="Opening",
        narration_text="A concise narration for a local synthesis test.",
        source_refs=[source_ref], transcript_refs=["0"],
    )
    return NarrationArtifact(
        narration_id="narr-track9_synthesis",
        production_id="prd_track9_synthesis", unit_id="unit-001",
        source_asset_id="src-001", target_duration_seconds=5.0,
        status="approved", narration_text=beat.narration_text,
        beats=[beat], source_refs=[source_ref], transcript_refs=["0"],
        generated_at="2026-10-04T00:00:00+00:00",
    )


def test_explicit_mock_synthesis_validates_persists_and_round_trips(tmp_path):
    artifact = _artifact()
    metadata = synthesize_narration_audio(
        artifact, root=tmp_path, provider_name="mock", voice="mock-voice-1",
        output_format="wav",
    )
    path = narration_audio_path(
        artifact.production_id, artifact.unit_id, root=tmp_path, output_format="wav"
    )
    validation = validate_audio_file(path)
    assert path.is_file() and path.stat().st_size > 0
    assert validation.valid, validation.errors
    assert validation.duration is not None and validation.duration > 0
    assert metadata.provider == "mock"
    assert metadata.audio_path == path.relative_to(tmp_path / artifact.production_id).as_posix()
    assert metadata.duration_seconds == pytest.approx(validation.duration)
    assert metadata.file_size == path.stat().st_size

    reloaded_audio = load_narration_audio(artifact.production_id, artifact.unit_id, root=tmp_path)
    reloaded = load_narration_artifact(artifact.production_id, artifact.unit_id, root=tmp_path)
    assert reloaded_audio == metadata
    assert reloaded.audio_artifact_path == metadata.audio_path
    assert reloaded.audio_duration_seconds == metadata.duration_seconds
    assert reloaded.audio_provider == "mock"
    assert reloaded.audio_voice == "mock-voice-1"
    assert reloaded.audio_format == "wav"
    assert reloaded.audio_status == "completed"
    assert narration_audio_metadata_path(artifact.production_id, artifact.unit_id, root=tmp_path).is_file()


def test_unavailable_real_provider_fails_without_mock_fallback(tmp_path, monkeypatch):
    artifact = _artifact()
    mock_provider = get_provider("mock")
    monkeypatch.setattr(
        "app.production.narration.get_provider",
        lambda name: _UnavailableProvider() if name == "edge_tts" else mock_provider,
    )
    with pytest.raises(VoiceProviderError, match="unavailable"):
        synthesize_narration_audio(artifact, root=tmp_path, provider_name="edge_tts")
    assert not narration_audio_path(artifact.production_id, artifact.unit_id, root=tmp_path).exists()
    reloaded = load_narration_artifact(artifact.production_id, artifact.unit_id, root=tmp_path)
    assert reloaded.audio_status == "failed"
    assert reloaded.audio_provider is None



def test_provider_generation_failure_persists_failed_state(tmp_path, monkeypatch):
    artifact = _artifact()

    class _FailingProvider:
        name = "edge_tts"

        def is_available(self) -> bool:
            return True

        async def generate(self, **kwargs) -> None:
            raise RuntimeError("provider generation failed")

    monkeypatch.setattr(
        "app.production.narration.get_provider", lambda _name: _FailingProvider()
    )
    with pytest.raises(RuntimeError, match="provider generation failed"):
        synthesize_narration_audio(artifact, root=tmp_path, provider_name="edge_tts")

    reloaded = load_narration_artifact(artifact.production_id, artifact.unit_id, root=tmp_path)
    assert reloaded.audio_status == "failed"
    assert not narration_audio_metadata_path(
        artifact.production_id, artifact.unit_id, root=tmp_path
    ).exists()


def test_invalid_audio_is_not_persisted_as_completed(tmp_path, monkeypatch):
    artifact = _artifact()

    class _InvalidAudioProvider:
        name = "edge_tts"

        def is_available(self) -> bool:
            return True

        async def generate(self, *, output_path: Path, **kwargs) -> None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"not audio")

    monkeypatch.setattr(
        "app.production.narration.get_provider", lambda _name: _InvalidAudioProvider()
    )
    with pytest.raises(ValueError, match="FFprobe failed|No audio stream|Invalid"):
        synthesize_narration_audio(artifact, root=tmp_path, provider_name="edge_tts")

    reloaded = load_narration_artifact(artifact.production_id, artifact.unit_id, root=tmp_path)
    assert reloaded.audio_status == "failed"
    assert not narration_audio_metadata_path(
        artifact.production_id, artifact.unit_id, root=tmp_path
    ).exists()
