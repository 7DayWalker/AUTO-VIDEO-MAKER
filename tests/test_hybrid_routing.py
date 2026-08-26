"""Phase 6B — ImageMotion provider + durable hybrid scene routing tests.

All tests are offline. Google Flow is never launched; Flow construction is
verified with fakes/sentinels only.
"""

import json
import subprocess
from pathlib import Path

import pytest

from app.orchestrator.models import OrchestratorConfig, ProjectManifest
from app.orchestrator.pipeline import ProductionOrchestrator
from app.orchestrator.visual_providers import (
    GOOGLE_FLOW_PROVIDER,
    IMAGE_MOTION_PROVIDER,
    LOCAL_ASSET_PROVIDER,
    ImageMotionVisualProvider,
    build_hybrid_routing,
    create_visual_provider,
    motion_preset_for_scene,
    resolve_scene_provider,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_image(path: Path, color="red", size="720x1280"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"color=c={color}:s={size}",
         "-frames:v", "1", str(path)],
        capture_output=True, check=True, timeout=60,
    )


def make_clip(path: Path, seconds=2, size="720x1280"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"color=c=blue:s={size}:d={seconds}",
         "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", str(path)],
        capture_output=True, check=True, timeout=120,
    )


def make_plan_file(tmp_path: Path, scene_count=2) -> Path:
    plan_id = "20260102_hybrid_probe_001"
    scenes = [
        {
            "id": f"scene_{i:03d}",
            "sequence": i,
            "narration": f"Narration {i}.",
            "visual_prompt": f"Visual {i}.",
            "estimated_duration": 4.0,
            "purpose": "fact",
        }
        for i in range(1, scene_count + 1)
    ]
    plan = {
        "project_id": plan_id,
        "title": "Hybrid Probe",
        "hook": "Hook.",
        "script": "Script.",
        "estimated_duration": 4.0 * scene_count,
        "scenes": scenes,
    }
    content_dir = tmp_path / "content" / plan_id
    content_dir.mkdir(parents=True, exist_ok=True)
    plan_path = content_dir / "production_plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return plan_path


def build_orchestrator(tmp_path: Path, monkeypatch, *, mode, scene_count=2,
                       default_provider="image_motion", routing=None,
                       assets: dict = None):
    """assets: {scene_id: "image"|"video"} controls which fixtures exist."""
    plan_path = make_plan_file(tmp_path, scene_count)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    pid = plan["project_id"]

    config = OrchestratorConfig(
        topic="hybrid probe topic",
        flow_project_url="",
        visual_mode=mode,
        visual_default_provider=default_provider,
        visual_scene_routing=dict(routing or {}),
    )
    orch = ProductionOrchestrator(config)
    orch.project_dir = tmp_path
    orch.manifest = ProjectManifest(
        project_id=pid,
        topic=config.topic,
        content_plan_path=str(plan_path),
        scene_config=config.to_scene_config_dict(),
    )
    orch.manifest.initialize_stages()

    img_dir = tmp_path / "assets_images"
    vid_dir = tmp_path / "assets_videos"
    for scene in plan["scenes"]:
        kind = (assets or {}).get(scene["id"], "image")
        if kind == "image":
            make_image(img_dir / f"{scene['id']}.png")
        elif kind == "video":
            make_clip(vid_dir / f"{scene['id']}.mp4")

    monkeypatch.setenv("AVM_IMAGE_ASSET_DIR", str(img_dir))
    monkeypatch.setenv("AVM_LOCAL_ASSET_DIR", str(vid_dir))
    return orch, pid


# ---------------------------------------------------------------------------
# ImageMotionVisualProvider unit behavior (tests 1–6)
# ---------------------------------------------------------------------------

def _im_provider(tmp_path, images=True):
    img_dir = tmp_path / "images"
    if images:
        make_image(img_dir / "scene_001.png")
    return ImageMotionVisualProvider(img_dir, tmp_path / "out"), img_dir


def _request(duration=2):
    from app.flow.scene_models import (
        FlowGenerationConfig,
        SceneGenerationRequest,
    )

    return SceneGenerationRequest(
        project_id="p",
        scene_id="scene_001",
        sequence=1,
        visual_prompt="unused",
        config=FlowGenerationConfig(aspect_ratio="9:16", duration=duration),
    )


def test_1_still_image_becomes_valid_vertical_mp4(tmp_path):
    provider, _ = _im_provider(tmp_path)
    result = provider.generate_scene(_request(duration=2))
    assert result.success is True
    out = Path(result.file_path)
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(out)],
        capture_output=True, text=True,
    )
    data = json.loads(probe.stdout)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    assert video["width"] == 1080 and video["height"] == 1920
    assert video["codec_name"] == "h264"
    audio = [s for s in data["streams"] if s["codec_type"] == "audio"]
    assert audio, "silent audio stream required for downstream concat/mix"


def test_2_output_duration_matches_request(tmp_path):
    provider, _ = _im_provider(tmp_path)
    result = provider.generate_scene(_request(duration=3))
    assert abs(result.actual_duration - 3.0) <= 0.6


def test_3_zero_cost_and_provenance(tmp_path):
    provider, _ = _im_provider(tmp_path)
    result = provider.generate_scene(_request(duration=2))
    assert result.generation_cost == 0
    assert result.verification["provider"] == IMAGE_MOTION_PROVIDER
    assert result.verification["asset_reference"].endswith("scene_001.png")
    assert result.verification["motion_preset"].startswith(("slow_", "pan_"))


def test_4_motion_preset_deterministic():
    assert motion_preset_for_scene("scene_001") == motion_preset_for_scene("scene_001")
    # Different ids are distributed across presets (not all identical).
    presets = {motion_preset_for_scene(f"scene_{i:03d}") for i in range(1, 9)}
    assert len(presets) > 1


def test_5_missing_image_fails_truthfully_never_flow(tmp_path):
    provider, _ = _im_provider(tmp_path, images=False)
    result = provider.generate_scene(_request(duration=2))
    assert result.success is False
    assert result.verification["provider"] == IMAGE_MOTION_PROVIDER
    assert "No local image" in result.error
    assert "google_flow" not in (result.error or "").lower()


def test_6_corrupt_image_fails_truthfully(tmp_path):
    img_dir = tmp_path / "images"
    bad = img_dir / "scene_001.jpg"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"\x00\x01not-an-image")
    provider = ImageMotionVisualProvider(img_dir, tmp_path / "out")
    result = provider.generate_scene(_request(duration=2))
    assert result.success is False


# ---------------------------------------------------------------------------
# Hybrid routing model (tests 7–11)
# ---------------------------------------------------------------------------

class _FakeScene:
    def __init__(self, sid, seq):
        self.id = sid
        self.sequence = seq


def test_7_hybrid_default_is_image_motion():
    routing = build_hybrid_routing([_FakeScene("scene_001", 1)], scene_config={})
    assert routing["scene_001"]["provider"] == IMAGE_MOTION_PROVIDER


def test_8_cli_override_beats_everything():
    routing = build_hybrid_routing(
        [_FakeScene("scene_003", 3)],
        scene_config={"scene_provider_routing": {
            "scene_003": {"provider": "local_asset"}}},
        cli_overrides={"scene_003": GOOGLE_FLOW_PROVIDER},
    )
    assert routing["scene_003"]["provider"] == GOOGLE_FLOW_PROVIDER


def test_9_resolver_never_defaults_hybrid_to_flow():
    assert (
        resolve_scene_provider("scene_x", visual_mode="hybrid", scene_config={})
        == IMAGE_MOTION_PROVIDER
    )


def test_10_resolver_honors_explicit_flow_override():
    assert (
        resolve_scene_provider(
            "scene_003",
            visual_mode="hybrid",
            scene_config={"scene_provider_routing": {
                "scene_003": {"provider": "google_flow"}}},
        )
        == GOOGLE_FLOW_PROVIDER
    )


def test_11_legacy_scene_config_still_loads_and_resolves():
    assert (
        resolve_scene_provider(
            "scene_001",
            visual_mode="google_flow",
            scene_config={"model": "Veo 3.1 - Lite"},  # pre-6B shape
        )
        == GOOGLE_FLOW_PROVIDER
    )


# ---------------------------------------------------------------------------
# Hybrid orchestrator dispatch (tests 12–17)
# ---------------------------------------------------------------------------

class _NeverConstructed:
    def __init__(self, *a, **k):
        raise AssertionError("Flow provider constructed without routed scenes")


def raw_checkpoint(tmp_path, scene_id):
    raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    return raw.get("scene_checkpoints", {}).get(scene_id)


def test_12_mixed_hybrid_routing_persisted_before_generation(
    tmp_path, monkeypatch
):
    """scene_001 image is MISSING: generation fails truthfully, but the full
    routing map must already be durably persisted — proving persistence
    happens BEFORE any provider runs. Flow is never touched."""
    import app.flow.scene_generator as flow_module

    class _Forbidden:
        def __init__(self, *a, **k):
            raise AssertionError("Flow reached without explicit routing")

    monkeypatch.setattr(flow_module, "FlowSceneGenerator", _Forbidden)
    monkeypatch.setattr(
        "app.orchestrator.visual_providers.GoogleFlowVisualProvider",
        _Forbidden,
    )

    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid", scene_count=2,
        assets={"scene_002": "video"},  # scene_001 image deliberately absent
    )
    with pytest.raises(Exception, match="No local image"):
        orch._execute_scenes_stage()

    sc = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))[
        "scene_config"
    ]
    assert sc["visual_mode"] == "hybrid"
    assert sc["scene_provider_routing"]["scene_001"]["provider"] == "image_motion"
    # No override for scene_002 -> conservative hybrid default applies.
    assert sc["scene_provider_routing"]["scene_002"]["provider"] == "image_motion"

    ck = raw_checkpoint(tmp_path, "scene_001")
    if ck:
        assert ck.get("provider") == IMAGE_MOTION_PROVIDER


def test_13_mixed_providers_dispatch_correctly(tmp_path, monkeypatch):
    """scene_001 image_motion (real), scene_002 local_asset (real)."""
    monkeypatch.setattr(
        "app.orchestrator.visual_providers.GoogleFlowVisualProvider",
        _NeverConstructed,
    )
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid", scene_count=2,
        default_provider="image_motion",
        assets={"scene_001": "image", "scene_002": "video"},
        routing={"scene_002": "local_asset"},
    )
    orch._execute_scenes_stage()

    assert len(orch.manifest.scene_video_paths) == 2
    ck1 = orch.manifest.scene_checkpoints["scene_001"]
    ck2 = orch.manifest.scene_checkpoints["scene_002"]
    assert ck1["provider"] == IMAGE_MOTION_PROVIDER
    assert ck2["provider"] == LOCAL_ASSET_PROVIDER
    assert orch.manifest.total_credits_used == 0

    raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert set(raw["scene_config"]["scene_provider_routing"].keys()) == {
        "scene_001", "scene_002"
    }


constructions = []


class _RecordingFlowProvider:
    requires_browser = True

    def __init__(self, scene_config, output_dir, flow_project_url=None):
        constructions.append(1)
        self.output_dir = Path(output_dir)

    def connect(self, expected_url=None):
        return True

    def navigate_to_project(self, url):
        return True

    def disconnect(self):
        pass

    def ensure_healthy(self, expected_url=None):
        return True

    def generate_scene(self, request, *, dry_run=False,
                       require_confirmation=True, checkpoint_callback=None):
        out = self.output_dir / f"{request.scene_id}.mp4"
        make_clip(out)
        from app.flow.scene_models import SceneGenerationResult

        if checkpoint_callback:
            checkpoint_callback({
                "scene_id": request.scene_id,
                "provider": GOOGLE_FLOW_PROVIDER,
                "status": "completed",
                "file_path": str(out),
            })
        return SceneGenerationResult.success_result(
            scene_id=request.scene_id,
            sequence=request.sequence,
            file_path=out,
            file_size=out.stat().st_size,
            actual_duration=2.0,
            width=1080,
            height=1920,
            codec="h264",
            generation_cost=7,
        )


def test_15_flow_constructed_once_only_for_routed_scene(tmp_path, monkeypatch):
    global constructions
    constructions = []

    """scene_001 -> google_flow (fake), scene_002 -> image_motion (real)."""
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid", scene_count=2,
        assets={"scene_002": "image"},
        routing={"scene_001": GOOGLE_FLOW_PROVIDER},
    )
    monkeypatch.setattr(
        "app.orchestrator.visual_providers.GoogleFlowVisualProvider",
        _RecordingFlowProvider,
    )
    orch._execute_scenes_stage()

    assert constructions == [1]
    ck1 = orch.manifest.scene_checkpoints["scene_001"]
    ck2 = orch.manifest.scene_checkpoints["scene_002"]
    assert ck1["provider"] == GOOGLE_FLOW_PROVIDER
    assert ck2["provider"] == IMAGE_MOTION_PROVIDER
    # The fake Flow provider bypasses the real Flow loop's billing-evidence
    # checkpoint format, so no credits are bookable here (real accounting is
    # covered by the Flow regression suites).
    assert orch.manifest.total_credits_used == 0
    assert len(orch.manifest.scene_video_paths) == 2


def test_16_resume_preserves_image_motion_provider(tmp_path, monkeypatch):
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid", scene_count=1,
        default_provider="image_motion",
    )
    orch._execute_scenes_stage()
    persisted = json.loads(
        (tmp_path / "manifest.json").read_text(encoding="utf-8")
    )["scene_config"]

    fresh = OrchestratorConfig(
        topic="resumed probe topic",
        flow_project_url="",
        visual_mode="google_flow",
        visual_default_provider="local_asset",
        visual_scene_routing={},
    )
    # Resume sequence: _resume_project first restores the mode (and default/
    # routing) from persisted scene_config, THEN providers are resolved under
    # the restored mode — so a google_flow CLI value cannot take over.
    from app.orchestrator.visual_providers import resolve_visual_mode

    restored_mode = resolve_visual_mode(fresh.visual_mode, persisted)
    assert restored_mode == "hybrid"

    resolved = resolve_scene_provider(
        "scene_001", visual_mode=restored_mode, scene_config=persisted
    )
    assert resolved == IMAGE_MOTION_PROVIDER
    assert persisted["scene_provider_routing"]["scene_001"]["provider"] == (
        IMAGE_MOTION_PROVIDER
    )


def test_17_legacy_manifest_without_routing_keys_loads(tmp_path):
    legacy = {"model": "Veo 3.1 - Lite"}
    m = ProjectManifest(project_id="p", topic="legacy load topic", scene_config=legacy)
    assert m.scene_config.get("scene_provider_routing") is None
    assert m.scene_config.get("visual_default_provider") is None