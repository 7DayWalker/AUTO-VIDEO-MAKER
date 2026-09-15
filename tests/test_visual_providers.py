"""Step 6A — visual provider architecture tests.

All tests are offline. Flow automation is never launched; the cost-firewall
tests prove that local mode cannot even reach FlowSceneGenerator.
"""

import json
import subprocess
from pathlib import Path

import pytest

from app.orchestrator.models import OrchestratorConfig, ProjectManifest
from app.orchestrator.pipeline import ProductionOrchestrator
from app.orchestrator.visual_providers import (
    LOCAL_ASSET_PROVIDER,
    GoogleFlowVisualProvider,
    LocalAssetVisualProvider,
    create_visual_provider,
    normalize_visual_mode,
    resolve_visual_mode,
)

TOPIC = "local visual mode probe"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(visual_mode="google_flow", **overrides):
    return OrchestratorConfig(
        topic=TOPIC,
        flow_project_url="" if visual_mode == "local" else "https://flow.google/p/x",
        visual_mode=visual_mode,
        **overrides,
    )


def make_plan_file(tmp_path: Path, scene_count=1) -> Path:
    """Write a minimal but fully valid production_plan.json."""
    plan_id = "20260101_local_probe_001"
    scenes = []
    for i in range(1, scene_count + 1):
        scenes.append(
            {
                "id": f"scene_{i:03d}",
                "sequence": i,
                "narration": f"Narration for scene {i}.",
                "visual_prompt": f"A calm visual for scene {i}.",
                "estimated_duration": 4.0,
                "purpose": "fact",
            }
        )
    plan = {
        "project_id": plan_id,
        "title": "Local Probe Video",
        "hook": "A hook.",
        "script": "Full narration script.",
        "estimated_duration": 4.0 * scene_count,
        "scenes": scenes,
    }
    content_dir = tmp_path / "content" / plan_id
    content_dir.mkdir(parents=True, exist_ok=True)
    plan_path = content_dir / "production_plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return plan_path


def make_source_clip(path: Path, seconds=2):
    """Create a tiny valid MP4 with silent audio (same recipe as mock videos)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    from app.assembly.ffmpeg import resolve_ffmpeg

    subprocess.run(
        [
            resolve_ffmpeg() or "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=blue:s=720x1280:d={seconds}",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        capture_output=True, check=True, timeout=120,
    )


def build_local_orchestrator(tmp_path: Path, monkeypatch, scene_count=1):
    """Wire a ProductionOrchestrator against an isolated temp project."""
    plan_path = make_plan_file(tmp_path, scene_count)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    pid = plan["project_id"]

    config = make_config("local")
    orch = ProductionOrchestrator(config)
    orch.project_dir = tmp_path
    orch.manifest = ProjectManifest(
        project_id=pid,
        topic=TOPIC,
        content_plan_path=str(plan_path),
        scene_config=config.to_scene_config_dict(),
    )
    orch.manifest.initialize_stages()

    asset_dir = tmp_path / "assets_local"
    for scene in plan["scenes"]:
        seq = f"{scene['sequence']:03d}"
        make_source_clip(asset_dir / f"{seq}.mp4")

    monkeypatch.setenv("AVM_LOCAL_ASSET_DIR", str(asset_dir))
    return orch, asset_dir, pid


# ---------------------------------------------------------------------------
# A/B — legacy/default behavior
# ---------------------------------------------------------------------------

def test_a_default_config_is_google_flow():
    config = OrchestratorConfig(topic=TOPIC, flow_project_url="x")
    assert config.visual_mode == "google_flow"
    assert config.to_scene_config_dict()["visual_mode"] == "google_flow"


def test_b_legacy_manifest_without_visual_mode_resolves_flow(tmp_path):
    legacy = {"model": "Veo 3.1 - Lite"}  # pre-6A scene_config shape
    assert resolve_visual_mode("google_flow", legacy) == "google_flow"
    assert resolve_visual_mode(None, None) == "google_flow"
    manifest = ProjectManifest(project_id="p", topic=TOPIC, scene_config=legacy)
    assert manifest.scene_config.get("visual_mode") is None


# ---------------------------------------------------------------------------
# C/D — resolver mapping + hybrid refusal
# ---------------------------------------------------------------------------

def test_c_google_flow_mode_resolves_flow_provider(tmp_path):
    provider = create_visual_provider(
        "google_flow",
        scene_config={"model": "Veo 3.1 - Lite", "aspect_ratio": "9:16"},
        scenes_output_dir=tmp_path,
        flow_project_url="https://flow.google/p/x",
    )
    assert isinstance(provider, GoogleFlowVisualProvider)
    assert provider.requires_browser is True


def test_d_local_mode_resolves_local_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("AVM_LOCAL_ASSET_DIR", str(tmp_path / "assets"))
    provider = create_visual_provider(
        "local",
        scene_config={"aspect_ratio": "9:16"},
        scenes_output_dir=tmp_path,
    )
    assert isinstance(provider, LocalAssetVisualProvider)
    assert provider.requires_browser is False
    assert provider.name == LOCAL_ASSET_PROVIDER


def test_hybrid_refuses_without_routing(tmp_path):
    with pytest.raises(ValueError, match="hybrid"):
        create_visual_provider(
            "hybrid", scene_config={}, scenes_output_dir=tmp_path
        )


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        normalize_visual_mode("veo_only")


# ---------------------------------------------------------------------------
# E/F/G — local mode end-to-end through the real orchestrator stage
# ---------------------------------------------------------------------------

class _FlowForbidden:
    """Sentinel: any construction attempt means the cost firewall failed."""

    def __init__(self, *args, **kwargs):
        raise AssertionError(
            "COST FIREWALL VIOLATION: FlowSceneGenerator constructed "
            "during visual_mode='local'"
        )


def test_ef_local_mode_completes_without_touching_flow(tmp_path, monkeypatch):
    import app.flow.scene_generator as flow_module
    from app.orchestrator.models import PipelineStage

    monkeypatch.setattr(flow_module, "FlowSceneGenerator", _FlowForbidden)

    orch, _asset_dir, _pid = build_local_orchestrator(tmp_path, monkeypatch)
    orch._execute_scenes_stage()

    assert len(orch.manifest.scene_video_paths) == 1
    out = Path(orch.manifest.scene_video_paths[0])
    assert out.exists() and out.stat().st_size > 0

    checkpoint = orch.manifest.scene_checkpoints["scene_001"]
    assert checkpoint["provider"] == LOCAL_ASSET_PROVIDER
    assert checkpoint["generation_cost"] == 0

    # Zero credits: the Flow credit ledger must be untouched.
    assert orch.manifest.total_credits_used == 0

    stage_meta = orch.manifest.get_stage_result(
        PipelineStage.SCENES
    ).metadata
    assert LOCAL_ASSET_PROVIDER in stage_meta["provider"]
    assert stage_meta["credits_used"] == 0


def test_f_provider_result_contract(tmp_path, monkeypatch):
    asset_dir = tmp_path / "assets"
    make_source_clip(asset_dir / "scene_001.mp4")
    monkeypatch.setenv("AVM_LOCAL_ASSET_DIR", str(asset_dir))

    provider = create_visual_provider(
        "local",
        scene_config={"aspect_ratio": "9:16", "duration": 2},
        scenes_output_dir=tmp_path / "out",
    )
    from app.flow.scene_models import (
        FlowGenerationConfig,
        SceneGenerationRequest,
    )

    request = SceneGenerationRequest(
        project_id="p",
        scene_id="scene_001",
        sequence=1,
        visual_prompt="unused for local",
        config=FlowGenerationConfig(aspect_ratio="9:16", duration=2),
    )
    updates = []
    result = provider.generate_scene(request, checkpoint_callback=updates.append)

    assert result.success is True
    assert result.generation_cost == 0
    assert result.file_path.exists()
    assert result.verification["provider"] == LOCAL_ASSET_PROVIDER
    assert result.verification["asset_reference"].endswith("scene_001.mp4")
    assert updates and updates[0]["provider"] == LOCAL_ASSET_PROVIDER


def test_g_local_resume_stays_local(tmp_path, monkeypatch):
    orch, _asset_dir, _pid = build_local_orchestrator(tmp_path, monkeypatch)
    orch._execute_scenes_stage()

    persisted = dict(orch.manifest.scene_config)
    # Persisted decision is authoritative even if configuration flips.
    assert resolve_visual_mode("google_flow", persisted) == "local"


def test_h_flow_resume_stays_flow():
    persisted = {"visual_mode": "google_flow"}
    assert resolve_visual_mode("local", persisted) == "google_flow"


# ---------------------------------------------------------------------------
# I — provenance survives manifest save/load
# ---------------------------------------------------------------------------

def test_i_provenance_survives_manifest_roundtrip(tmp_path, monkeypatch):
    orch, _asset_dir, _pid = build_local_orchestrator(tmp_path, monkeypatch)
    orch._execute_scenes_stage()

    raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    checkpoint = raw["scene_checkpoints"]["scene_001"]
    assert checkpoint["provider"] == LOCAL_ASSET_PROVIDER
    assert checkpoint["generation_cost"] == 0
    assert raw["scene_config"]["visual_mode"] == "local"

    reloaded = ProjectManifest.from_dict(raw)
    assert (
        reloaded.scene_checkpoints["scene_001"]["provider"]
        == LOCAL_ASSET_PROVIDER
    )


# ---------------------------------------------------------------------------
# J/K — Flow wrapper delegation parity (no browser needed)
# ---------------------------------------------------------------------------

class _RecordingInnerFlow:
    """Records delegation so parity is assertable without a browser."""

    def __init__(self):
        self.calls = []

    def generate_scene(self, request, dry_run=False, require_confirmation=True,
                       checkpoint_callback=None):
        self.calls.append(
            ("generate_scene", request.scene_id, dry_run, require_confirmation)
        )
        from app.flow.scene_models import SceneGenerationResult

        return SceneGenerationResult.success_result(
            scene_id=request.scene_id,
            sequence=request.sequence,
            file_path=Path("unused.mp4"),
            file_size=1,
            actual_duration=4.0,
            width=1080,
            height=1920,
            codec="h264",
        )

    def resume_submitted_scene(self, request, checkpoint,
                               checkpoint_callback=None):
        self.calls.append(("resume_submitted_scene", request.scene_id))
        from app.flow.scene_models import SceneGenerationResult

        return SceneGenerationResult.failure(
            request.scene_id, request.sequence, "still generating",
            status="generating",
        )


def test_jk_wrapper_delegates_verbatim_and_tags_provider():
    inner = _RecordingInnerFlow()
    provider = GoogleFlowVisualProvider.__new__(GoogleFlowVisualProvider)
    provider._generator = inner
    provider.flow_project_url = None

    from app.flow.scene_models import (
        FlowGenerationConfig,
        SceneGenerationRequest,
    )

    request = SceneGenerationRequest(
        project_id="p",
        scene_id="scene_001",
        sequence=1,
        visual_prompt="prompt",
        config=FlowGenerationConfig(),
    )
    result = provider.generate_scene(request, require_confirmation=False)

    assert inner.calls == [("generate_scene", "scene_001", False, False)]
    assert result.verification["provider"] == "google_flow"

    resume_result = provider.resume_submitted_scene(request, {"status": "generating"})
    assert ("resume_submitted_scene", "scene_001") in inner.calls
    assert resume_result.status == "generating"


# ---------------------------------------------------------------------------
# L — completed local scenes skip on rerun
# ---------------------------------------------------------------------------

def test_l_completed_local_scenes_skip_on_rerun(tmp_path, monkeypatch):
    orch, _asset_dir, _pid = build_local_orchestrator(tmp_path, monkeypatch)
    orch._execute_scenes_stage()
    first_paths = list(orch.manifest.scene_video_paths)
    first_mtime = Path(first_paths[0]).stat().st_mtime_ns

    # Second pass must skip completed scenes (no regeneration).
    orch._execute_scenes_stage()
    assert orch.manifest.scene_video_paths == first_paths
    assert Path(first_paths[0]).stat().st_mtime_ns == first_mtime