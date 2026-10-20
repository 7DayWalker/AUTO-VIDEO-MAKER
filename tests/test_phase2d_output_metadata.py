"""Phase 2D targeted tests — truthful final-video output metadata.

Contract under test (additive projection only, Option A):

* The pipeline's final acceptance gate persists ONE-TIME ffprobe facts of
  the actual completed artifact into
  ``ProjectManifest.stages["complete"].metadata`` (existing behavior, locked).
* ``GET …/units/{unit_id}`` (via ``_manifest_payload``) projects those
  recorded facts onto ``bound_project.artifacts.final_video`` as
  ``duration / width / height / file_size / resolution``.
* The projection performs NO media inspection, never fabricates:
  unknown stays ``None`` (never 0), facts never appear for a non-AVAILABLE
  artifact, stage wall-clock timing is never used as media duration, and
  existing keys (state/available/safe_path/media_url/reason) are unchanged.
"""

from __future__ import annotations

import json
import subprocess

import pytest

import app.orchestrator.reconciliation as recon
from app.orchestrator.models import (
    PipelineStage,
    ProjectManifest,
    StageResult,
)
from app.orchestrator.pipeline import ProductionOrchestrator
from app.orchestrator.reconciliation import ArtifactStatus
from app.web import server as web

RECORDED_FACTS = {
    "duration": 51.5,
    "width": 1080,
    "height": 1920,
    "file_size": 1600000,
    "video_codec": "h264",
    "audio_codec": "aac",
}


def _project_dir(tmp_path, monkeypatch, project_id="phase2d-project"):
    monkeypatch.setattr(web, "PROJECTS_DIR", tmp_path / "projects")
    directory = tmp_path / "projects" / project_id
    directory.mkdir(parents=True)
    return directory


def _completed_manifest(directory, *, facts=True, stage_wall_seconds=None):
    """Manifest of a genuinely completed project with a real final file."""
    video = directory / "final.mp4"
    video.write_bytes(b"final video bytes for phase2d")
    manifest = ProjectManifest(
        project_id=directory.name,
        topic="Phase 2D output metadata",
        final_video_path=str(video),
    )
    manifest.initialize_stages()
    manifest.set_stage_result(StageResult.completed(PipelineStage.ASSEMBLY))
    result = StageResult.completed(
        PipelineStage.COMPLETE,
        output_path=str(video),
        metadata=dict(RECORDED_FACTS) if facts else {},
    )
    if stage_wall_seconds is not None:
        # Stage wall-clock timing lives on its own field and must NEVER be
        # readable as a media duration through the artifact.
        result = result.model_copy(
            update={"duration_seconds": stage_wall_seconds}
        )
    manifest.set_stage_result(result)
    return manifest, video


# ---------------------------------------------------------------------------
# Origin: the acceptance gate already persists the probed facts (locked)
# ---------------------------------------------------------------------------


class _GateStub:
    """Minimal 'self' for ProductionOrchestrator._final_acceptance_gate."""

    def __init__(self, manifest, project_dir, save_ok=True):
        self.manifest = manifest
        self.project_dir = project_dir
        self.saved = 0
        self._save_ok = save_ok

    def _save_manifest(self):
        if not self._save_ok:
            raise AssertionError("invalid artifacts must never complete")
        self.saved += 1


def test_acceptance_gate_persists_real_probe_facts(tmp_path, monkeypatch):
    manifest = ProjectManifest(project_id="gate-project", topic="gate")
    manifest.initialize_stages()
    monkeypatch.setattr(
        recon, "_assembly_evidence",
        lambda _manifest, _dir: {
            "status": ArtifactStatus.VALID.value,
            "path": "output/gate/final.mp4",
            "validation": dict(RECORDED_FACTS),
        },
    )
    stub = _GateStub(manifest, tmp_path)
    ProductionOrchestrator._final_acceptance_gate(stub)

    assert stub.saved == 1
    completed = manifest.stages[PipelineStage.COMPLETE.value]
    assert completed.metadata["duration"] == 51.5
    assert completed.metadata["width"] == 1080
    assert completed.metadata["height"] == 1920
    assert completed.metadata["file_size"] == 1600000
    assert completed.metadata["ffprobe"] == "passed"


def test_acceptance_gate_refuses_invalid_artifact(tmp_path, monkeypatch):
    manifest = ProjectManifest(project_id="gate-bad", topic="gate")
    manifest.initialize_stages()
    monkeypatch.setattr(
        recon, "_assembly_evidence",
        lambda _manifest, _dir: {
            "status": ArtifactStatus.INVALID.value,
            "validation": {"valid": False, "error": "no video stream"},
        },
    )
    stub = _GateStub(manifest, tmp_path, save_ok=False)
    from app.orchestrator.pipeline import StageFailedError

    with pytest.raises(StageFailedError):
        ProductionOrchestrator._final_acceptance_gate(stub)


# ---------------------------------------------------------------------------
# Projection through the existing artifact payload
# ---------------------------------------------------------------------------


def test_completed_final_video_exposes_recorded_facts(tmp_path, monkeypatch):
    directory = _project_dir(tmp_path, monkeypatch)
    manifest, _video = _completed_manifest(directory, stage_wall_seconds=999.9)

    payload = web._manifest_payload(manifest, directory.name)
    final = payload["artifacts"]["final_video"]

    assert final["state"] == "AVAILABLE"
    assert final["available"] is True
    assert final["media_url"] == f"/api/projects/{directory.name}/media"
    # Actual recorded media facts...
    assert final["duration"] == 51.5
    assert final["file_size"] == 1600000
    assert final["width"] == 1080
    assert final["height"] == 1920
    assert final["resolution"] == "1080x1920"
    # ...and NEVER the stage wall-clock timing masquerading as media duration.
    assert payload["stages"]["COMPLETE"]["duration_seconds"] == 999.9
    assert final["duration"] != 999.9


def test_existing_artifact_keys_and_paths_are_unchanged(tmp_path, monkeypatch):
    directory = _project_dir(tmp_path, monkeypatch)
    manifest, video = _completed_manifest(directory)
    final = web._manifest_payload(manifest, directory.name)["artifacts"]["final_video"]

    # The frozen presentation keys all survive, with the new facts additive.
    for key in ("type", "state", "available", "safe_path", "media_url", "reason"):
        assert key in final
    assert final["type"] == "Final video"
    assert final["safe_path"]
    assert str(video) not in json.dumps(final)


def test_recorded_facts_are_unknown_not_zero_when_never_probed(
    tmp_path, monkeypatch
):
    """Legacy manifests (no acceptance metadata) stay honest: all nulls."""
    directory = _project_dir(tmp_path, monkeypatch, "phase2d-legacy")
    manifest, _video = _completed_manifest(directory, facts=False)

    final = web._manifest_payload(manifest, directory.name)["artifacts"]["final_video"]
    assert final["state"] == "AVAILABLE"
    assert final["media_url"] is not None  # handoff behavior unchanged
    assert final["duration"] is None
    assert final["file_size"] is None
    assert final["width"] is None
    assert final["height"] is None
    assert final["resolution"] is None


def test_non_positive_recorded_values_are_discarded_as_unknown(
    tmp_path, monkeypatch
):
    directory = _project_dir(tmp_path, monkeypatch, "phase2d-zero")
    video = directory / "final.mp4"
    video.write_bytes(b"x")
    manifest = ProjectManifest(
        project_id=directory.name, topic="zero", final_video_path=str(video)
    )
    manifest.initialize_stages()
    manifest.set_stage_result(StageResult.completed(PipelineStage.ASSEMBLY))
    manifest.set_stage_result(StageResult.completed(
        PipelineStage.COMPLETE,
        metadata={"duration": 0, "width": -1080, "height": True, "file_size": 0},
    ))
    final = web._manifest_payload(manifest, directory.name)["artifacts"]["final_video"]
    assert final["state"] == "AVAILABLE"
    # 0/-1/bool can never be facts of a legitimately accepted artifact; they
    # surface as unknown (null), never as values and never as replaced zeros.
    assert final["duration"] is None
    assert final["width"] is None
    assert final["height"] is None
    assert final["file_size"] is None
    assert final["resolution"] is None


def test_resolution_requires_both_actual_dimensions(tmp_path, monkeypatch):
    directory = _project_dir(tmp_path, monkeypatch, "phase2d-dims")
    video = directory / "final.mp4"
    video.write_bytes(b"x")
    manifest = ProjectManifest(
        project_id=directory.name, topic="dims", final_video_path=str(video)
    )
    manifest.initialize_stages()
    manifest.set_stage_result(StageResult.completed(PipelineStage.ASSEMBLY))
    manifest.set_stage_result(StageResult.completed(
        PipelineStage.COMPLETE,
        metadata={"duration": 10.5, "width": 1080, "file_size": 100},
    ))
    final = web._manifest_payload(manifest, directory.name)["artifacts"]["final_video"]
    assert final["width"] == 1080
    assert final["height"] is None
    assert final["resolution"] is None, "one real dimension cannot invent a resolution"


def test_non_available_artifact_never_echoes_stale_facts(tmp_path, monkeypatch):
    directory = _project_dir(tmp_path, monkeypatch, "phase2d-gone")
    manifest, video = _completed_manifest(directory)

    video.unlink()  # recorded as AVAILABLE elsewhere; the bytes are gone now
    final = web._manifest_payload(manifest, directory.name)["artifacts"]["final_video"]
    assert final["state"] == "RECORDED BUT MISSING"
    assert final["media_url"] is None
    assert all(
        final[key] is None
        for key in ("duration", "width", "height", "file_size", "resolution")
    ), "facts recorded for different bytes must never be echoed for a missing file"

    failed = ProjectManifest(
        project_id=directory.name, topic="failed", final_video_path=str(video)
    )
    failed.initialize_stages()
    failed.set_stage_result(StageResult.failed(PipelineStage.ASSEMBLY, "boom"))
    failed.set_stage_result(StageResult.completed(
        PipelineStage.COMPLETE, metadata=dict(RECORDED_FACTS)
    ))
    final = web._manifest_payload(failed, directory.name)["artifacts"]["final_video"]
    assert final["state"] == "FAILED"
    assert final["duration"] is None
    assert final["resolution"] is None


# ---------------------------------------------------------------------------
# The read path performs no media inspection whatsoever
# ---------------------------------------------------------------------------


def _block_all_media_inspection(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("output-metadata projection performed media inspection")

    import app.assembly.ffmpeg as assembly_ffmpeg

    monkeypatch.setattr(recon, "probe_media", _blocked)
    monkeypatch.setattr(assembly_ffmpeg, "probe_media", _blocked)
    monkeypatch.setattr(subprocess, "run", _blocked)
    monkeypatch.setattr(subprocess, "Popen", _blocked)


def test_projection_never_inspects_media(tmp_path, monkeypatch):
    directory = _project_dir(tmp_path, monkeypatch, "phase2d-noio")
    manifest, _video = _completed_manifest(directory)
    _block_all_media_inspection(monkeypatch)

    facts = web._final_video_output_facts(manifest)
    assert facts["duration"] == 51.5 and facts["resolution"] == "1080x1920"
    payload = web._manifest_payload(manifest, directory.name)
    assert payload["artifacts"]["final_video"]["file_size"] == 1600000


def test_production_list_never_inspects_media(tmp_path, monkeypatch):
    """The collection endpoint must not become a media-inspection operation."""
    _block_all_media_inspection(monkeypatch)
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    monkeypatch.setattr(web, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(web, "_production_runs", {})
    (tmp_path / "productions").mkdir()
    (tmp_path / "projects").mkdir()

    status, items = web.productions_index_payload()
    assert status == 200 and items == []


# ---------------------------------------------------------------------------
# The live unit-detail surface (single scan) delivers the facts end to end
# ---------------------------------------------------------------------------


def test_unit_detail_exposes_recorded_facts(tmp_path, monkeypatch):
    from pathlib import Path

    from app.orchestrator.production_execution import (
        BoundProject,
        ProductionExecutionReport,
        UnitExecutionState,
        UnitProgress,
    )
    from app.production.models import (
        Production,
        ProductionCommand,
        ProductionUnit,
    )
    from app.production.storage import save_new_production

    created = "2026-09-22T09:00:00+00:00"
    production_id = "prd_20260922_phase2d_test_001"
    save_new_production(
        Production(
            production_id=production_id,
            title="Phase 2D Unit Detail",
            command=ProductionCommand(
                text="One unit.",
                workload_shape="single_video",
                intended_unit_count=1,
                issued_at=created,
            ),
            units=[
                ProductionUnit(
                    unit_id="unit-001",
                    ordinal=1,
                    unit_type="standalone_video",
                    title="Unit 001",
                    content_intent={"target_duration": 10, "number_of_scenes": 2},
                )
            ],
            created_at=created,
        ),
        root=tmp_path / "productions",
    )
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    directory = _project_dir(tmp_path, monkeypatch, "phase2d-bound")
    manifest, _video = _completed_manifest(directory)
    monkeypatch.setattr(web, "_production_runs", {})

    class StubExecutor:
        def derive_progress_with_bindings(self, production=None):
            report = ProductionExecutionReport(
                production_id,
                1,
                (
                    UnitProgress(
                        "unit-001",
                        1,
                        UnitExecutionState.COMPLETED,
                        project_id=directory.name,
                        bound_plan_version=1,
                    ),
                ),
            )
            return report, {"unit-001": BoundProject(
                directory_id=directory.name,
                project_dir=Path(directory),
                manifest=manifest,
                manifest_raw=None,
            )}

    monkeypatch.setattr(
        web, "_production_executor", lambda production_id_arg: StubExecutor()
    )

    status, payload = web.unit_detail_payload(production_id, "unit-001")
    assert status == 200
    assert payload["execution"]["state"] == "COMPLETED"
    final = payload["bound_project"]["artifacts"]["final_video"]
    assert final["state"] == "AVAILABLE"
    assert final["media_url"] == f"/api/projects/{directory.name}/media"
    assert final["duration"] == 51.5
    assert final["resolution"] == "1080x1920"
    assert final["file_size"] == 1600000


def test_other_read_surfaces_keep_their_contracts(tmp_path, monkeypatch):
    """Capabilities and artifact state vocabulary are untouched by Phase 2D."""
    capabilities = web.capabilities_payload()
    assert set(capabilities["seams"]) == {
        "planner", "tts", "image_source", "alibaba_video", "openrouter_video",
    }
    assert capabilities["sample"] is False

    directory = _project_dir(tmp_path, monkeypatch, "phase2d-states")
    manifest, _video = _completed_manifest(directory)
    payload = web._manifest_payload(manifest, directory.name)
    artifact_keys = {
        name: artifact["state"]
        for name, artifact in payload["artifacts"].items()
    }
    assert artifact_keys["final_video"] == "AVAILABLE"
    assert artifact_keys["captions"] in {
        "AVAILABLE", "RECORDED BUT MISSING", "NOT GENERATED", "NOT APPLICABLE", "FAILED",
    }
    # Provenance/accounting concerns were not extended by Phase 2D: scene
    # rows keep their own vocabulary and carry no output-metadata fields.
    rows = payload["scene_checkpoint_rows"]
    for row in rows:
        assert "duration" not in row or row.get("duration") is None



