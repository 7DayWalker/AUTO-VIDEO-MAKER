"""Phase 6E — operational hardening tests.

Offline: temp directories only. FFmpeg-generated fixture MP4s follow the
established repo test-fixture recipe (no Flow, no browser, no paid APIs).
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import app.orchestrator.reconciliation as reconciliation
import produce
from app.content.models import ProductionPlan
from app.orchestrator.models import (
    OrchestratorConfig,
    PipelineStage,
    ProjectManifest,
    StageResult,
)

TOPIC = "phase 6e hardening probe"
PLAN_ID = "20260101_phase6e_probe_001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _acceptance_stamp(manifest):
    manifest.initialize_stages()
    manifest.set_stage_result(StageResult.completed(
        stage=PipelineStage.COMPLETE,
        started_at=datetime.now().isoformat(),
    ))


def _summary_manifest(project_id="20231017_inner_plan_001", *, accepted=True,
                      providers=("image_motion",), placeholders=False):
    man = ProjectManifest(
        project_id=project_id,
        topic=TOPIC,
        scene_config={
            "visual_mode": "hybrid",
            "generation_kind": "placeholder" if placeholders else "real",
        },
    )
    if placeholders:
        man.scene_checkpoints.update({
            f"scene_{i}": {"provider": "mock",
                           "generation_kind": "placeholder"}
            for i in range(1, 4)
        })
    else:
        man.scene_checkpoints.update({
            f"scene_{i:03d}": {"provider": provider}
            for i, provider in enumerate(providers, start=1)
        })
    if accepted:
        _acceptance_stamp(man)
    return man


def _make_video(path: Path, seconds=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    from app.assembly.ffmpeg import resolve_ffmpeg

    subprocess.run(
        [resolve_ffmpeg() or "ffmpeg", "-y", "-f", "lavfi", "-i",
         f"color=c=navy:s=720x1280:d={seconds}",
         "-f", "lavfi", "-i",
         f"anullsrc=r=44100:cl=stereo:d={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", str(path)],
        capture_output=True, check=True, timeout=120,
    )


def _write_project_tree(tmp_path: Path, *, placeholders: bool, scene_count=2):
    """Craft a physically-complete mini project for reconciliation runs."""
    project_dir = tmp_path / f"proj_{int(placeholders)}"
    scenes_dir = project_dir / "scenes" / PLAN_ID
    plan = {
        "project_id": PLAN_ID,
        "title": "Phase6E Probe",
        "hook": "h",
        "script": "s",
        "estimated_duration": 4.0 * scene_count,
        "scenes": [
            {"id": f"scene_{i:02d}", "sequence": i,
             "narration": f"n{i}", "visual_prompt": "v",
             "estimated_duration": 4.0, "purpose": "fact"}
            for i in range(1, scene_count + 1)
        ],
    }
    plan_path = project_dir / "content" / PLAN_ID / "production_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    scene_paths = []
    for i in range(1, scene_count + 1):
        vid = scenes_dir / f"scene_{i:02d}.mp4"
        _make_video(vid)
        scene_paths.append(str(vid))

    man = ProjectManifest(
        project_id=PLAN_ID, topic=TOPIC,
        content_plan_path=str(plan_path),
        assembly_config={"enable_captions": False},
    )
    man.scene_config["aspect_ratio"] = "9:16"
    if placeholders:
        man.scene_config["generation_kind"] = "placeholder"
        # Canonical app.flow.scene_models.SceneManifest serialization:
        # "scenes" is a LIST of SceneManifestEntry dicts.
        entries = []
        for p in scene_paths:
            sid = Path(p).stem
            man.scene_checkpoints[sid] = {
                "provider": "mock", "generation_kind": "placeholder",
                "status": "completed", "generation_cost": 0,
            }
            entries.append({
                "scene_id": sid,
                "sequence": int(sid[-2:]),
                "file": Path(p).name,
                "status": "completed",
                "checkpoint_evidence": {
                    "provider": "mock", "generation_kind": "placeholder",
                },
            })
        sm_path = scenes_dir / "scene_manifest.json"
        sm_path.write_text(
            json.dumps({"project_id": PLAN_ID, "scenes": entries}),
            encoding="utf-8",
        )
    else:
        for p in scene_paths:
            man.scene_checkpoints[Path(p).stem] = {
                "provider": "image_motion", "status": "completed",
            }

    man.scene_video_paths = list(scene_paths)
    man.scene_manifest_path = str(scenes_dir / "scene_manifest.json")
    man.content_plan_path = str(plan_path)
    (project_dir / "manifest.json").write_text(
        json.dumps(man.to_dict(), indent=2), encoding="utf-8"
    )
    return project_dir


def _patch_valid(monkeypatch):
    """Neutralize cross-cutting evidence getters so SCENES verdict logic is
    exercised in isolation (content evidence stays REAL in these tests)."""
    monkeypatch.setattr(
        reconciliation, "_voice_evidence",
        lambda m, d, c: {"status": "VALID"},
    )
    monkeypatch.setattr(
        reconciliation, "_assembly_evidence",
        lambda m, d: {"status": "VALID",
                      "validation": {"valid": True, "path": "x"}},
    )


def _cli_args(**kw):
    base = dict(flow_project_url="", visual_mode="google_flow",
                route_scene=(), resume=None)
    base.update(kw)
    return NS(**base)


def _persisted_on_disk(tmp_path, project_id, *, mode, routing=None,
                       checkpoints=None, default_provider=None):
    cfg = {"visual_mode": mode}
    if routing is not None:
        cfg["scene_provider_routing"] = routing
    if default_provider is not None:
        cfg["visual_default_provider"] = default_provider
    raw = {
        "project_id": project_id, "topic": TOPIC,
        "scene_config": cfg, "scene_checkpoints": checkpoints or {},
    }
    d = tmp_path / project_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")


# ---------------------------------------------------------------------------
# D-summary correctness (required tests 1, 11, 12, 13)
# ---------------------------------------------------------------------------

def test_01_summary_reports_real_project_directory():
    man = _summary_manifest()
    project_dir = Path("some/root") / "20260826_actual_durable_project"
    lines = produce.build_final_summary(man, project_dir)
    assert f"Project Directory: {project_dir}" in lines
    assert "Project ID: 20260826_actual_durable_project" in lines
    # Inner plan ID stays visible but clearly distinguished.
    assert any(l.startswith("Content Plan ID: ") for l in lines)
    assert not any("Project Directory:" in l and "20231017" in l
                   for l in lines)


def test_11_provider_histogram_from_real_checkpoints():
    man = _summary_manifest(
        providers=("image_motion", "image_motion", "local_asset")
    )
    lines = produce.build_final_summary(man, Path("root/proj"))
    assert "Providers Used:" in lines
    assert "  image_motion: 2" in lines
    assert "  local_asset: 1" in lines


def test_12_mock_run_not_labelled_as_production_completion():
    man = _summary_manifest(placeholders=True)
    joined = "\n".join(produce.build_final_summary(man, Path("root/mock")))
    assert "MOCK RUN COMPLETE" in joined
    assert "not real production" in joined
    assert "\nPROJECT COMPLETE\n" not in joined
    assert "  mock: 3 (placeholders)" in joined


def test_13_accepted_real_run_complete_and_zero_credits():
    lines = produce.build_final_summary(_summary_manifest(), Path("root/p"))
    assert "PROJECT COMPLETE" in lines
    assert "Google Flow Credits Used: 0" in lines
    assert "Acceptance Verdict: PROJECT_COMPLETE" in lines


def test_stage_summary_is_safe_for_cp1252_console():
    """Status output must not require Unicode glyphs on Windows consoles."""

    class Cp1252Stream:
        encoding = "cp1252"

        def __init__(self):
            self.parts = []

        def write(self, text):
            # Reproduce a Windows cp1252 stream: unrepresentable characters
            # raise instead of being silently replaced.
            text.encode(self.encoding)
            self.parts.append(text)
            return len(text)

        def flush(self):
            return None

    manifest = ProjectManifest(
        project_id="phase7c_console_probe",
        topic=TOPIC,
    )
    manifest.initialize_stages()
    manifest.set_stage_result(StageResult.completed(PipelineStage.CONTENT))
    manifest.set_stage_result(StageResult.skipped(
        PipelineStage.VOICE,
        reason="not required",
    ))
    manifest.set_stage_result(StageResult.failed(
        PipelineStage.SCENES,
        error="probe failure",
    ))

    stream = Cp1252Stream()
    produce.print_stage_results(manifest, stream=stream)
    output = "".join(stream.parts)

    assert "[OK] CONTENT: completed" in output
    assert "[SKIP] VOICE: skipped" in output
    assert "[FAIL] SCENES: failed" in output
    assert "✓" not in output
    assert "○" not in output
    assert "✗" not in output


# ---------------------------------------------------------------------------
# B/E — resume ergonomics + configuration sanity matrix
# ---------------------------------------------------------------------------

def test_02_resume_local_without_visual_mode_needs_no_flow_url(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(produce, "ORCHESTRATOR_OUTPUT_DIR", tmp_path)
    _persisted_on_disk(tmp_path, "p_local", mode="local")
    persisted = produce.load_persisted_visual_config("p_local")
    args = _cli_args(resume="p_local")   # CLI default is google_flow!
    assert produce.flow_url_required_error(args, persisted=persisted) is None


def test_03_resume_image_motion_hybrid_without_flow_url(tmp_path,
                                                        monkeypatch):
    monkeypatch.setattr(produce, "ORCHESTRATOR_OUTPUT_DIR", tmp_path)
    _persisted_on_disk(
        tmp_path, "p_img", mode="hybrid", default_provider="image_motion",
        routing={"scene_01": {"provider": "image_motion"}},
        checkpoints={"scene_01": {"status": "completed"}},
    )
    persisted = produce.load_persisted_visual_config("p_img")
    assert produce.flow_url_required_error(
        _cli_args(resume="p_img"), persisted=persisted
    ) is None


def test_04_resume_all_zero_cost_hybrid_without_flow_url(tmp_path,
                                                         monkeypatch):
    monkeypatch.setattr(produce, "ORCHESTRATOR_OUTPUT_DIR", tmp_path)
    _persisted_on_disk(
        tmp_path, "p_hyb", mode="hybrid",
        routing={"scene_01": {"provider": "image_motion"},
                 "scene_02": {"provider": "local_asset"}},
    )
    persisted = produce.load_persisted_visual_config("p_hyb")
    args = _cli_args(resume="p_hyb", visual_mode="local")
    assert produce.flow_url_required_error(args, persisted=persisted) is None


def test_05_new_google_flow_without_url_fails_with_legacy_message():
    err = produce.flow_url_required_error(_cli_args())
    assert err == "--flow-project-url is required for --topic and --resume"
    err = produce.flow_url_required_error(
        _cli_args(visual_mode="hybrid",
                  route_scene=("scene_01=google_flow",))
    )
    assert err == "--flow-project-url is required for --topic and --resume"


def test_06_persisted_authority_beats_cli_google_flow_default(
    tmp_path, monkeypatch
):
    """CLI defaults must not turn a zero-cost persisted project into a Flow
    URL requirement during resume validation."""
    monkeypatch.setattr(produce, "ORCHESTRATOR_OUTPUT_DIR", tmp_path)
    _persisted_on_disk(tmp_path, "p_cli_local", mode="local")
    persisted = produce.load_persisted_visual_config("p_cli_local")
    assert produce.flow_url_required_error(
        _cli_args(resume="p_cli_local"), persisted=persisted
    ) is None

    _persisted_on_disk(tmp_path, "p_cli_img", mode="hybrid",
                       routing={"scene_01": {"provider": "image_motion"}})
    persisted = produce.load_persisted_visual_config("p_cli_img")
    assert produce.flow_url_required_error(
        _cli_args(resume="p_cli_img"), persisted=persisted
    ) is None


def test_07_pending_flow_routed_scene_requires_url_when_needed(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(produce, "ORCHESTRATOR_OUTPUT_DIR", tmp_path)
    _persisted_on_disk(
        tmp_path, "p_flow_pending", mode="hybrid",
        routing={"scene_01": {"provider": "google_flow"},
                 "scene_02": {"provider": "image_motion"}},
    )
    persisted = produce.load_persisted_visual_config("p_flow_pending")
    err = produce.flow_url_required_error(
        _cli_args(resume="p_flow_pending"), persisted=persisted
    )
    assert err and "scene_01" in err

    # Fully completed flow scenes need no further execution => no URL.
    _persisted_on_disk(
        tmp_path, "p_flow_done", mode="hybrid",
        routing={"scene_01": {"provider": "google_flow"}},
        checkpoints={"scene_01": {"status": "completed"}},
    )
    persisted = produce.load_persisted_visual_config("p_flow_done")
    assert produce.flow_url_required_error(
        _cli_args(resume="p_flow_done"), persisted=persisted
    ) is None

    # A persisted pure google_flow project still demands the URL upfront.
    _persisted_on_disk(tmp_path, "p_flow_mode", mode="google_flow")
    persisted = produce.load_persisted_visual_config("p_flow_mode")
    assert produce.flow_url_required_error(
        _cli_args(resume="p_flow_mode"), persisted=persisted
    )


def test_08_load_persisted_config_handles_missing_and_malformed(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(produce, "ORCHESTRATOR_OUTPUT_DIR", tmp_path)
    assert produce.load_persisted_visual_config(None) is None
    assert produce.load_persisted_visual_config("ghost") is None
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{not json", encoding="utf-8")
    assert produce.load_persisted_visual_config("bad") is None


# ---------------------------------------------------------------------------
# C — durable mock/placeholder provenance
# ---------------------------------------------------------------------------

MOCK_PLAN_ID = "20260101_phase6e_mock_001"


def _mock_scenes_orchestrator(tmp_path, monkeypatch):
    import app.orchestrator.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "ORCHESTRATOR_OUTPUT_DIR", tmp_path)

    plan = {
        "project_id": MOCK_PLAN_ID,
        "title": "Mock Provenance Probe",
        "hook": "h",
        "script": "s",
        "estimated_duration": 12.0,
        "scenes": [
            {"id": f"scene_{i}", "sequence": i,
             "narration": f"n{i}", "visual_prompt": "v",
             "estimated_duration": 4.0, "purpose": "fact"}
            for i in range(1, 4)
        ],
    }
    project_dir = tmp_path / "proj_mock"
    plan_path = (
        project_dir / "content" / MOCK_PLAN_ID / "production_plan.json"
    )
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    config = OrchestratorConfig(
        topic=TOPIC, flow_project_url="",
        visual_mode="hybrid", mock_mode=True,
    )
    orch = pipeline_mod.ProductionOrchestrator(config)
    orch.project_dir = project_dir
    orch.manifest = ProjectManifest(
        project_id="outer_mock_id",
        topic=TOPIC,
        content_plan_path=str(plan_path),
        scene_config={"visual_mode": "hybrid"},
    )
    orch.manifest.initialize_stages()
    return orch


def test_09_placeholder_generations_are_durably_marked(tmp_path, monkeypatch):
    orch = _mock_scenes_orchestrator(tmp_path, monkeypatch)
    orch._execute_scenes_stage()

    sm_raw = json.loads(
        (orch.project_dir / "scenes" / MOCK_PLAN_ID /
         "scene_manifest.json").read_text(encoding="utf-8")
    )
    assert isinstance(sm_raw["scenes"], list)
    for entry in sm_raw["scenes"]:
        assert entry["checkpoint_evidence"] == {
            "provider": "mock", "generation_kind": "placeholder",
        }, "scene manifest entries must carry durable placeholder marks"

    for sid, ck in orch.manifest.scene_checkpoints.items():
        assert ck["provider"] == "mock"
        assert ck["generation_kind"] == "placeholder"

    raw = json.loads(
        (orch.project_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert raw["scene_config"]["generation_kind"] == "placeholder"


def test_10_reconciliation_never_promotes_placeholders_to_complete(
    tmp_path, monkeypatch
):
    _patch_valid(monkeypatch)
    project_dir = _write_project_tree(tmp_path, placeholders=True)
    report = reconciliation.reconcile_project(
        project_id="any", project_dir=project_dir, persist=False
    )
    # Physically-valid videos still validate...
    assert report.scenes["status"] == "VALID"
    assert report.scenes["placeholder_count"] == 2
    # ...but the verdict refuses a genuine-completion claim.
    assert report.verdict.value == "PROJECT_REQUIRES_RECONCILIATION"
    assert any("placeholder" in r.lower() for r in report.reasons)


def test_10b_real_provider_evidence_still_reaches_complete_verdict(
    tmp_path, monkeypatch
):
    """Legacy-shaped manifests without any marker remain loadable and are
    classified as genuine generation (backward compatibility)."""
    _patch_valid(monkeypatch)
    project_dir = _write_project_tree(tmp_path, placeholders=False)
    report = reconciliation.reconcile_project(
        project_id="any", project_dir=project_dir, persist=False
    )
    assert report.verdict.value == "PROJECT_COMPLETE"
    for item in report.scenes["items"]:
        assert item.get("generation") != "placeholder"

    # Round-trip through the public loader stays lossless for such projects.
    raw = json.loads(
        (project_dir / "manifest.json").read_text(encoding="utf-8-sig")
    )
    assert ProjectManifest.from_dict(raw).project_id == PLAN_ID


def test_10c_placeholder_provenance_detected_from_scene_manifest_alone(
    tmp_path, monkeypatch
):
    """Durable per-entry provenance in scene_manifest.json must identify
    placeholder generations EVEN IF the project-level scene_config marker is
    missing or tampered away."""
    _patch_valid(monkeypatch)
    project_dir = _write_project_tree(tmp_path, placeholders=True)
    raw = json.loads(
        (project_dir / "manifest.json").read_text(encoding="utf-8-sig")
    )
    del raw["scene_config"]["generation_kind"]
    (project_dir / "manifest.json").write_text(
        json.dumps(raw), encoding="utf-8"
    )

    report = reconciliation.reconcile_project(
        project_id="any", project_dir=project_dir, persist=False
    )
    assert report.scenes["placeholder_count"] == 2
    assert all(
        i.get("generation") == "placeholder"
        for i in report.scenes["items"]
    )
    assert report.verdict.value == "PROJECT_REQUIRES_RECONCILIATION"
    assert any("placeholder" in r.lower() for r in report.reasons)


def test_15_unaccepted_run_never_claims_completion():
    """A run that finishes without acceptance stays truthful: no PROJECT
    COMPLETE claim, explicit NOT_ACCEPTED verdict, and honest banner."""
    man = _summary_manifest(
        providers=("google_flow", "image_motion"), accepted=False
    )
    lines = produce.build_final_summary(man, Path("root/late"))
    joined = "\n".join(lines)
    assert "RUN FINISHED (project not fully accepted yet)" in lines
    assert "\nPROJECT COMPLETE\n" not in joined
    assert "MOCK RUN COMPLETE" not in joined
    assert "Acceptance Verdict: NOT_ACCEPTED" in lines
    assert "Project ID: late" in lines
    assert "  google_flow: 1" in lines
    assert "  image_motion: 1" in lines
