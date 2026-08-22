"""
Phase 2G focused tests: evidence-based reconciliation, completion integrity,
and resume repair behavior.

All tests are offline: media validation is exercised through the same
validator call sites used by production, but backed by lightweight fakes so
no Flow credits, API calls, or real generation occur.
"""

import json
from pathlib import Path

import pytest

from app.orchestrator.models import (
    PipelineStage,
    ProjectManifest,
    StageStatus,
)
from app.orchestrator.reconciliation import (
    ArtifactStatus,
    ProjectVerdict,
    apply_reconciliation,
    reconcile_project,
)


PROJECT_ID = "20231024_test_project_001"
SCENE_IDS = [f"scene_{i:02d}" for i in range(1, 7)]


def make_plan_payload() -> dict:
    scenes = []
    for i, scene_id in enumerate(SCENE_IDS, start=1):
        scenes.append(
            {
                "id": scene_id,
                "sequence": i,
                "narration": f"Narration for scene {i}.",
                "visual_prompt": f"Visual prompt {i}",
                "estimated_duration": 5,
                "purpose": "hook" if i == 1 else "fact",
            }
        )
    return {
        "project_id": PROJECT_ID,
        "title": "Test Project",
        "hook": "Hook line.",
        "script": "Full script text.",
        "estimated_duration": 30,
        "scenes": scenes,
    }


def make_timing_payload() -> dict:
    scenes = []
    start = 0.0
    for i, scene_id in enumerate(SCENE_IDS, start=1):
        end = start + 5.0
        scenes.append({"scene_id": scene_id, "sequence": i, "start": start, "end": end})
        start = end
    return {
        "project_id": PROJECT_ID,
        "voiceover_duration": 30.0,
        "estimated_total_duration": 30.0,
        "scenes": scenes,
    }


def make_manifest(tmp_path: Path) -> ProjectManifest:
    manifest = ProjectManifest(project_id=PROJECT_ID, topic="Test Topic")
    manifest.initialize_stages()
    manifest.content_plan_path = str(
        tmp_path / "content" / PROJECT_ID / "production_plan.json"
    )
    manifest.voice_audio_path = str(tmp_path / "voice" / PROJECT_ID / "voiceover.mp3")
    manifest.voice_timing_path = str(tmp_path / "voice" / PROJECT_ID / "voice_timing.json")
    manifest.scene_manifest_path = str(
        tmp_path / "scenes" / PROJECT_ID / "scene_manifest.json"
    )
    manifest.scene_video_paths = [
        str(tmp_path / "scenes" / PROJECT_ID / f"{scene_id}.mp4")
        for scene_id in SCENE_IDS
    ]
    manifest.captions_path = str(tmp_path / "captions" / "captions.srt")
    manifest.final_video_path = str(tmp_path / "final" / f"{PROJECT_ID}_final.mp4")
    manifest.assembly_config = {
        "resolution_width": 1080,
        "resolution_height": 1920,
        "enable_captions": True,
    }
    manifest.scene_config = {"aspect_ratio": "9:16"}
    return manifest


def build_project_tree(tmp_path: Path) -> None:
    plan = make_plan_payload()
    plan_path = tmp_path / "content" / PROJECT_ID / "production_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    timing_path = tmp_path / "voice" / PROJECT_ID / "voice_timing.json"
    timing_path.parent.mkdir(parents=True, exist_ok=True)
    timing_path.write_text(json.dumps(make_timing_payload()), encoding="utf-8")

    audio_path = tmp_path / "voice" / PROJECT_ID / "voiceover.mp3"
    audio_path.write_bytes(b"fake-but-present-audio")

    scenes_dir = tmp_path / "scenes" / PROJECT_ID
    scenes_dir.mkdir(parents=True, exist_ok=True)
    for scene_id in SCENE_IDS:
        (scenes_dir / f"{scene_id}.mp4").write_bytes(b"fake-but-present-video")

    captions_path = tmp_path / "captions" / "captions.srt"
    captions_path.parent.mkdir(parents=True, exist_ok=True)
    captions_path.write_text(
        chr(10).join(
            [
                "1",
                "00:00:00,000 --> 00:00:02,000",
                "First caption.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    final_path = tmp_path / "final" / f"{PROJECT_ID}_final.mp4"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"fake-but-present-final")


@pytest.fixture()
def fake_media_valid(monkeypatch):
    """Make every validator report success without invoking FFprobe."""
    from app.voice.validator import AudioValidationResult

    def fake_validate_audio(path):
        return AudioValidationResult(valid=True, duration=30.0, file_size=path.stat().st_size)

    def fake_validate_video(path, expected_aspect_ratio=None):
        if not path.exists():
            return {"valid": False, "error": "File does not exist"}
        file_size = path.stat().st_size
        if file_size == 0:
            return {"valid": False, "error": "File is empty", "file_size": 0}
        return {
            "valid": True,
            "duration": 8.0,
            "width": 720,
            "height": 1280,
            "codec": "h264",
            "file_size": file_size,
            "has_video": True,
            "has_audio": True,
        }

    class FakeMediaInfo:
        duration = 48.0
        width = 1080
        height = 1920
        video_codec = "h264"
        audio_codec = "aac"
        has_video = True
        has_audio = True
        file_size = 12345678

    def fake_probe(path):
        info = FakeMediaInfo()
        info.file_size = path.stat().st_size
        return info

    monkeypatch.setattr("app.orchestrator.reconciliation.validate_audio_file", fake_validate_audio)
    monkeypatch.setattr("app.orchestrator.reconciliation.validate_video_file", fake_validate_video)
    monkeypatch.setattr("app.orchestrator.reconciliation.probe_media", fake_probe)
    monkeypatch.setattr("app.orchestrator.reconciliation.check_ffprobe_available", lambda: True)


def save_manifest(manifest: ProjectManifest, project_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict(), indent=2), encoding="utf-8"
    )


class TestCompletionIntegrity:
    def test_all_artifacts_valid_is_complete(self, tmp_path, fake_media_valid):
        build_project_tree(tmp_path)
        manifest = make_manifest(tmp_path)
        save_manifest(manifest, tmp_path)

        report = reconcile_project(PROJECT_ID, tmp_path)

        assert report.verdict == ProjectVerdict.PROJECT_COMPLETE
        assert report.content["status"] == ArtifactStatus.VALID.value
        assert report.voice["status"] == ArtifactStatus.VALID.value
        assert report.scenes["status"] == ArtifactStatus.VALID.value
        assert report.captions["status"] == ArtifactStatus.VALID.value
        assert report.assembly["status"] == ArtifactStatus.VALID.value
        assert report.reasons == []

    def test_final_mp4_missing_is_not_complete(self, tmp_path, fake_media_valid):
        build_project_tree(tmp_path)
        (tmp_path / "final" / f"{PROJECT_ID}_final.mp4").unlink()
        manifest = make_manifest(tmp_path)
        save_manifest(manifest, tmp_path)

        report = reconcile_project(PROJECT_ID, tmp_path)

        assert report.verdict != ProjectVerdict.PROJECT_COMPLETE
        assert report.assembly["status"] == ArtifactStatus.MISSING.value
        assert any(reason.startswith("assembly:") for reason in report.reasons)

    def test_final_mp4_corrupted_is_not_complete(self, tmp_path, monkeypatch):
        build_project_tree(tmp_path)
        manifest = make_manifest(tmp_path)
        save_manifest(manifest, tmp_path)

        # Corrupt file: ffprobe-equivalent reports failure.
        from app.voice.validator import AudioValidationResult

        monkeypatch.setattr(
            "app.orchestrator.reconciliation.validate_audio_file",
            lambda path: AudioValidationResult(valid=True, duration=30.0),
        )
        monkeypatch.setattr(
            "app.orchestrator.reconciliation.validate_video_file",
            lambda path, expected_aspect_ratio=None: {
                "valid": True, "duration": 8.0, "width": 720, "height": 1280,
                "file_size": path.stat().st_size,
            },
        )
        monkeypatch.setattr(
            "app.orchestrator.reconciliation.probe_media",
            lambda path: (_ for _ in ()).throw(RuntimeError("moov atom not found")),
        )
        monkeypatch.setattr(
            "app.orchestrator.reconciliation.check_ffprobe_available", lambda: True
        )

        report = reconcile_project(PROJECT_ID, tmp_path)

        assert report.verdict != ProjectVerdict.PROJECT_COMPLETE
        assert report.assembly["status"] == ArtifactStatus.INVALID.value

    def test_assembly_pending_but_final_valid_repairs_state(self, tmp_path, fake_media_valid):
        build_project_tree(tmp_path)
        manifest = make_manifest(tmp_path)
        # Simulate Scenario F: assembly physically done but stage left pending.
        manifest.stages["assembly"].status = StageStatus.PENDING
        manifest.stages["complete"].status = StageStatus.PENDING
        manifest.current_stage = PipelineStage.ASSEMBLY
        save_manifest(manifest, tmp_path)

        report = reconcile_project(PROJECT_ID, tmp_path, persist=True)

        assert report.verdict == ProjectVerdict.PROJECT_COMPLETE
        assert report.persisted is True

        repaired = ProjectManifest.from_dict(
            json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        )
        assert repaired.is_stage_completed(PipelineStage.ASSEMBLY)
        assert repaired.is_stage_completed(PipelineStage.COMPLETE)
        assert repaired.completed_at is not None
        assert repaired.current_stage == PipelineStage.COMPLETE


class TestSceneIntegrity:
    def test_zero_byte_scene_is_invalid(self, tmp_path, fake_media_valid):
        build_project_tree(tmp_path)
        zero_scene = tmp_path / "scenes" / PROJECT_ID / "scene_03.mp4"
        zero_scene.write_bytes(b"")
        manifest = make_manifest(tmp_path)
        save_manifest(manifest, tmp_path)

        report = reconcile_project(PROJECT_ID, tmp_path)

        item = next(i for i in report.scenes["items"] if i["scene_id"] == "scene_03")
        assert item["status"] == ArtifactStatus.INVALID.value
        assert report.verdict != ProjectVerdict.PROJECT_COMPLETE

    def test_five_valid_one_missing_is_resumable(self, tmp_path, fake_media_valid):
        build_project_tree(tmp_path)
        (tmp_path / "scenes" / PROJECT_ID / "scene_04.mp4").unlink()
        manifest = make_manifest(tmp_path)
        save_manifest(manifest, tmp_path)

        report = reconcile_project(PROJECT_ID, tmp_path)

        statuses = {i["scene_id"]: i["status"] for i in report.scenes["items"]}
        assert statuses["scene_04"] == ArtifactStatus.MISSING.value
        assert sum(s == ArtifactStatus.VALID.value for s in statuses.values()) == 5
        assert report.verdict != ProjectVerdict.PROJECT_COMPLETE

        # Resume repair leaves the incomplete scenes stage resumable without
        # regenerating anything; valid scene files remain untouched on disk.
        apply_reconciliation(manifest, report)
        assert not manifest.is_stage_completed(PipelineStage.SCENES)
        assert manifest.can_resume()
        for scene_id in SCENE_IDS:
            if scene_id != "scene_04":
                assert (tmp_path / "scenes" / PROJECT_ID / f"{scene_id}.mp4").exists()


class TestVoiceIntegrity:
    def test_invalid_voice_fails_stage(self, tmp_path, monkeypatch):
        build_project_tree(tmp_path)
        manifest = make_manifest(tmp_path)
        save_manifest(manifest, tmp_path)

        from app.voice.validator import AudioValidationResult

        monkeypatch.setattr(
            "app.orchestrator.reconciliation.validate_audio_file",
            lambda path: AudioValidationResult(valid=False, errors=["No audio stream found"]),
        )
        monkeypatch.setattr(
            "app.orchestrator.reconciliation.validate_video_file",
            lambda path, expected_aspect_ratio=None: {"valid": True},
        )
        monkeypatch.setattr(
            "app.orchestrator.reconciliation.probe_media",
            lambda path: type("M", (), {
                "duration": 48.0, "width": 1080, "height": 1920,
                "video_codec": "h264", "audio_codec": "aac",
                "has_video": True, "has_audio": True,
                "file_size": path.stat().st_size,
            })(),
        )
        monkeypatch.setattr(
            "app.orchestrator.reconciliation.check_ffprobe_available", lambda: True
        )

        report = reconcile_project(PROJECT_ID, tmp_path)

        assert report.voice["status"] == ArtifactStatus.INVALID.value
        assert report.verdict != ProjectVerdict.PROJECT_COMPLETE

        apply_reconciliation(manifest, report)
        assert not manifest.is_stage_completed(PipelineStage.VOICE)

    def test_valid_voice_and_timing_pass(self, tmp_path, fake_media_valid):
        build_project_tree(tmp_path)
        manifest = make_manifest(tmp_path)
        save_manifest(manifest, tmp_path)

        report = reconcile_project(PROJECT_ID, tmp_path)

        assert report.voice["status"] == ArtifactStatus.VALID.value
        assert report.voice["timing_validation"]["valid"] is True


class TestResumeRecovery:
    def test_interrupted_after_content_resume_skips_content(self, tmp_path, fake_media_valid):
        build_project_tree(tmp_path)
        # Simulate an interruption right after Content: downstream artifacts
        # were never produced.
        (tmp_path / "voice" / PROJECT_ID / "voiceover.mp3").unlink()
        (tmp_path / "voice" / PROJECT_ID / "voice_timing.json").unlink()
        for scene_id in SCENE_IDS:
            (tmp_path / "scenes" / PROJECT_ID / f"{scene_id}.mp4").unlink()
        (tmp_path / "captions" / "captions.srt").unlink()
        (tmp_path / "final" / f"{PROJECT_ID}_final.mp4").unlink()

        manifest = make_manifest(tmp_path)
        for stage in ("voice", "scenes", "captions", "assembly", "complete"):
            manifest.stages[stage].status = StageStatus.PENDING
        manifest.current_stage = PipelineStage.VOICE
        save_manifest(manifest, tmp_path)

        report = reconcile_project(PROJECT_ID, tmp_path)
        apply_reconciliation(manifest, report)

        assert manifest.is_stage_completed(PipelineStage.CONTENT)
        assert not manifest.is_stage_completed(PipelineStage.VOICE)
        assert manifest.can_resume()

    def test_complete_claim_with_deleted_final_becomes_resumable(self, tmp_path, fake_media_valid):
        build_project_tree(tmp_path)
        manifest = make_manifest(tmp_path)
        # Manifest claims full completion...
        for stage in ("content", "voice", "scenes", "captions", "assembly", "complete"):
            manifest.stages[stage].status = StageStatus.COMPLETED
        manifest.current_stage = PipelineStage.COMPLETE
        manifest.completed_at = "2026-08-21T17:12:57"
        save_manifest(manifest, tmp_path)
        # ...but the final MP4 was deleted afterwards.
        (tmp_path / "final" / f"{PROJECT_ID}_final.mp4").unlink()

        report = reconcile_project(PROJECT_ID, tmp_path)
        apply_reconciliation(manifest, report)

        assert report.verdict != ProjectVerdict.PROJECT_COMPLETE
        assert manifest.completed_at is None
        assert manifest.can_resume()

    def test_persist_repairs_stale_sentinel_on_disk(self, tmp_path, fake_media_valid):
        build_project_tree(tmp_path)
        manifest = make_manifest(tmp_path)
        for stage in ("content", "voice", "scenes", "captions", "assembly"):
            manifest.stages[stage].status = StageStatus.COMPLETED
        manifest.stages["complete"].status = StageStatus.PENDING
        manifest.current_stage = PipelineStage.COMPLETE
        save_manifest(manifest, tmp_path)

        report = reconcile_project(PROJECT_ID, tmp_path, persist=True)

        assert report.verdict == ProjectVerdict.PROJECT_COMPLETE
        repaired = ProjectManifest.from_dict(
            json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        )
        assert repaired.is_stage_completed(PipelineStage.COMPLETE)
        assert repaired.completed_at is not None


class TestCreditSafety:
    def test_reconciliation_never_touches_credits(self, tmp_path, fake_media_valid):
        build_project_tree(tmp_path)
        manifest = make_manifest(tmp_path)
        manifest.total_credits_used = 90
        manifest.scene_checkpoints = {
            "scene_01": {
                "status": "completed",
                "generation_cost": 10,
                "credits_accounted": True,
                "attempts": [
                    {
                        "attempt_number": 1,
                        "generation_cost": 10,
                        "credits_accounted": True,
                        "checkpoint_evidence": {
                            "submission": {"state": "SUBMITTED"},
                        },
                    }
                ],
            }
        }
        save_manifest(manifest, tmp_path)

        before = manifest.total_credits_used
        checkpoint_before = json.dumps(manifest.scene_checkpoints, sort_keys=True)

        report = reconcile_project(PROJECT_ID, tmp_path, persist=True)

        assert report.verdict == ProjectVerdict.PROJECT_COMPLETE
        repaired = ProjectManifest.from_dict(
            json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        )
        assert repaired.total_credits_used == before
        assert json.dumps(repaired.scene_checkpoints, sort_keys=True) == checkpoint_before