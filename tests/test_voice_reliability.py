"""
Focused tests for voice reliability and voice-only resume behavior.
"""

import asyncio
from pathlib import Path

import pytest

from app.orchestrator.models import (
    OrchestratorConfig,
    PipelineStage,
    ProjectManifest,
    StageResult,
)
from app.orchestrator.pipeline import ProductionOrchestrator
from app.voice.exceptions import VoiceProviderError
from app.voice.providers.edge_tts import EdgeTTSProvider
from app.voice.generator import VoiceGenerator
from app.voice.models import VoiceGenerationResult


class _TransientProvider:
    name = "edge_tts"

    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0

    async def generate(self, **kwargs):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        Path(kwargs["output_path"]).write_bytes(b"not-a-real-audio-file")


class _SuccessfulProvider:
    name = "edge_tts"

    async def generate(self, **kwargs):
        Path(kwargs["output_path"]).write_bytes(b"non-empty")


def test_edge_tts_retries_transient_failure_then_succeeds(monkeypatch, tmp_path):
    provider = EdgeTTSProvider(max_retries=2, backoff_seconds=0)
    provider._edge_tts = object()
    provider._available = True

    calls = {"count": 0}

    async def generate_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ConnectionError("temporary network failure")
        Path(args[1]).write_bytes(b"audio")

    monkeypatch.setattr(provider, "_generate_once", generate_once)

    asyncio.run(
        provider.generate(
            text="Validation",
            output_path=tmp_path / "voice.mp3",
            voice="en-US-GuyNeural",
        )
    )

    assert calls["count"] == 2
    assert (tmp_path / "voice.mp3").stat().st_size > 0


def test_edge_tts_retries_are_bounded_and_preserve_diagnostics(monkeypatch, tmp_path):
    provider = EdgeTTSProvider(max_retries=2, backoff_seconds=0)
    provider._edge_tts = object()
    provider._available = True

    async def generate_once(*args, **kwargs):
        raise ConnectionError("persistent provider outage")

    monkeypatch.setattr(provider, "_generate_once", generate_once)

    with pytest.raises(VoiceProviderError) as exc_info:
        asyncio.run(
            provider.generate(
                text="Validation",
                output_path=tmp_path / "voice.mp3",
                voice="en-US-GuyNeural",
            )
        )

    assert "3/3" in str(exc_info.value)
    assert "persistent provider outage" in str(exc_info.value)


def test_generator_rejects_invalid_provider_output(monkeypatch, tmp_path):
    provider = _SuccessfulProvider()
    generator = VoiceGenerator(provider=provider, output_dir=tmp_path)

    plan = {
        "project_id": "test_voice_reliability",
        "title": "Test",
        "hook": "Hook",
        "script": "Narration",
        "estimated_duration": 10,
        "scenes": [
            {
                "id": "scene_01",
                "sequence": 1,
                "narration": "A short narration.",
                "visual_prompt": "A plant",
                "estimated_duration": 10,
            }
        ],
    }

    from app.content.models import ProductionPlan

    monkeypatch.setattr(
        "app.voice.generator.validate_audio_file",
        lambda path: type(
            "Validation",
            (),
            {
                "valid": False,
                "errors": ["No audio stream found"],
                "warnings": [],
                "duration": None,
                "sample_rate": None,
                "channels": None,
                "file_size": path.stat().st_size,
            },
        )(),
    )

    result = generator.generate_from_plan(ProductionPlan(**plan))
    assert result.success is False
    assert "Audio validation failed" in result.error


def test_voice_only_resume_skips_content_and_scenes(tmp_path, monkeypatch):
    project_id = "resume_voice_only"
    project_dir = tmp_path / project_id
    project_dir.mkdir()
    manifest = ProjectManifest(
        project_id=project_id,
        topic="Resume test",
        current_stage=PipelineStage.VOICE,
        content_plan_path=str(project_dir / "production_plan.json"),
    )
    manifest.initialize_stages()
    manifest.set_stage_result(
        StageResult.completed(PipelineStage.CONTENT, output_path=manifest.content_plan_path)
    )
    # A completed Content claim must be backed by a real, valid production
    # plan on disk; otherwise resume reconciliation correctly regenerates it.
    plan_payload = {
        "project_id": project_id,
        "title": "Resume test",
        "hook": "Hook line.",
        "script": "Full script text.",
        "estimated_duration": 10,
        "scenes": [
            {
                "id": "scene_01",
                "sequence": 1,
                "narration": "Narration one.",
                "visual_prompt": "Visual prompt one",
                "estimated_duration": 5,
                "purpose": "hook",
            },
            {
                "id": "scene_02",
                "sequence": 2,
                "narration": "Narration two.",
                "visual_prompt": "Visual prompt two",
                "estimated_duration": 5,
                "purpose": "fact",
            },
        ],
    }
    project_dir.joinpath("production_plan.json").write_text(
        __import__("json").dumps(plan_payload),
        encoding="utf-8",
    )
    manifest_path = project_dir / "manifest.json"
    manifest_path.write_text(
        __import__("json").dumps(manifest.to_dict()),
        encoding="utf-8",
    )

    config = OrchestratorConfig(
        topic="Resume test",
        flow_project_url="https://example.invalid/flow",
        resume_project_id=project_id,
        voice_only=True,
        skip_confirmation=True,
    )
    orchestrator = ProductionOrchestrator(config)
    monkeypatch.setattr(
        "app.orchestrator.pipeline.ORCHESTRATOR_OUTPUT_DIR",
        tmp_path,
    )

    called = {"content": 0, "voice": 0, "scenes": 0}

    def content_stage():
        called["content"] += 1

    def voice_stage():
        called["voice"] += 1
        orchestrator.manifest.set_stage_result(
            StageResult.completed(PipelineStage.VOICE, output_path="voice.mp3")
        )

    def scenes_stage():
        called["scenes"] += 1

    monkeypatch.setattr(orchestrator, "_execute_content_stage", content_stage)
    monkeypatch.setattr(orchestrator, "_execute_voice_stage", voice_stage)
    monkeypatch.setattr(orchestrator, "_execute_scenes_stage", scenes_stage)

    result = orchestrator.run()

    assert called == {"content": 0, "voice": 1, "scenes": 0}
    assert result.is_stage_completed(PipelineStage.CONTENT)
    assert result.is_stage_completed(PipelineStage.VOICE)
    assert result.get_stage_result(PipelineStage.SCENES).status.value == "pending"
    assert result.total_credits_used == 0