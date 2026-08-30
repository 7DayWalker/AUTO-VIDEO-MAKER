"""Phase 6B — ImageMotion provider + durable hybrid scene routing tests.

All tests are offline. Google Flow is never launched; Flow construction is
verified with fakes/sentinels only.
"""

import json
import subprocess
from pathlib import Path

import pytest

from app.flow.scene_models import SceneGenerationResult
from app.orchestrator.models import OrchestratorConfig, ProjectManifest
from app.orchestrator.pipeline import ProductionOrchestrator
from app.orchestrator.visual_providers import (
    GOOGLE_FLOW_PROVIDER,
    IMAGE_MOTION_PROVIDER,
    LOCAL_ASSET_PROVIDER,
    OPENROUTER_VIDEO_PROVIDER,
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
# ---------------------------------------------------------------------------
# Phase 7E pre-live hardening:
#  - configured hybrid global default is actually effective (item 1)
#  - pipeline-level OpenRouter routing (/resume) without network (item 2)
# ---------------------------------------------------------------------------

openrouter_calls = []
openrouter_fail = set()


class _FakeOpenRouterProvider:
    """Hermetic double for OpenRouterVideoVisualProvider at the factory boundary.

    Records every invocation (generate/resume) and either writes a valid local
    MP4 fixture or fails a scene whose id is listed in ``openrouter_fail``.
    """

    name = OPENROUTER_VIDEO_PROVIDER
    requires_browser = False

    def __init__(self, output_dir, **kwargs):
        self.output_dir = Path(output_dir)

    def is_available(self):
        return True

    def generate_scene(self, request, *, require_confirmation=False, checkpoint_callback=None, **_):
        openrouter_calls.append(("generate", request.scene_id))
        return self._finish(request, f"job_{request.scene_id}", checkpoint_callback)

    def resume_scene(self, request, checkpoint, *, checkpoint_callback=None, **_):
        openrouter_calls.append(("resume", request.scene_id))
        job_id = checkpoint.get("job_id") or f"job_{request.scene_id}"
        polling_url = checkpoint.get("polling_url") or f"https://openrouter.test/videos/{job_id}"
        result = self._finish(request, job_id, checkpoint_callback)
        result.verification["job_id"] = job_id
        result.verification["polling_url"] = polling_url
        return result

    def _finish(self, request, job_id, checkpoint_callback=None):
        polling_url = f"https://openrouter.test/videos/{job_id}"
        common = {
            "provider": OPENROUTER_VIDEO_PROVIDER,
            "job_id": job_id,
            "polling_url": polling_url,
            "model": "vendor/fake-video",
            "access": "free",
            "access_route": "free",
        }
        if request.scene_id in openrouter_fail:
            if checkpoint_callback:
                checkpoint_callback({
                    **common,
                    "scene_id": request.scene_id,
                    "status": "failed",
                    "error": "simulated openrouter failure",
                })
            return SceneGenerationResult.failure(
                request.scene_id, request.sequence, "simulated openrouter failure"
            )
        out = self.output_dir / f"{request.scene_id}.mp4"
        make_clip(out)
        if checkpoint_callback:
            checkpoint_callback({
                **common,
                "scene_id": request.scene_id,
                "status": "generation_submitted",
            })
        result = SceneGenerationResult.success_result(
            scene_id=request.scene_id,
            sequence=request.sequence,
            file_path=out,
            file_size=out.stat().st_size,
            actual_duration=2.0,
            width=720,
            height=1280,
            codec="h264",
            generation_cost=None,
        )
        result.verification.update(common)
        if checkpoint_callback:
            checkpoint_callback({
                **common,
                "scene_id": request.scene_id,
                "status": "completed",
                "file_path": str(out),
            })
        return result


class _Scene:
    """Minimal scene object with the attributes build_hybrid_routing reads."""

    def __init__(self, scene_id, sequence):
        self.id = scene_id
        self.sequence = sequence


def _routing_plan(tmp_path, scene_count=2):
    plan = json.loads(make_plan_file(tmp_path, scene_count).read_text(encoding="utf-8"))
    return [_Scene(item["id"], item["sequence"]) for item in plan["scenes"]]


def test_18_no_explicit_default_keeps_image_motion_for_unrouted_scenes(tmp_path):
    scenes = _routing_plan(tmp_path)
    routing = build_hybrid_routing(scenes, scene_config={})
    assert routing["scene_001"]["provider"] == IMAGE_MOTION_PROVIDER
    assert routing["scene_002"]["provider"] == IMAGE_MOTION_PROVIDER
    assert routing["scene_001"]["reason"] == "hybrid default"
    assert resolve_scene_provider(
        "scene_001", visual_mode="hybrid", scene_config={}
    ) == IMAGE_MOTION_PROVIDER


def test_19_explicit_openrouter_default_routes_unrouted_scenes_to_openrouter(tmp_path):
    scenes = _routing_plan(tmp_path)
    config = {"visual_default_provider": OPENROUTER_VIDEO_PROVIDER}
    routing = build_hybrid_routing(scenes, scene_config=config)
    assert routing["scene_001"]["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert routing["scene_002"]["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert resolve_scene_provider(
        "scene_001", visual_mode="hybrid", scene_config=config
    ) == OPENROUTER_VIDEO_PROVIDER


def test_20_explicit_route_override_beats_configured_default(tmp_path):
    scenes = _routing_plan(tmp_path)
    config = {"visual_default_provider": OPENROUTER_VIDEO_PROVIDER}
    routing = build_hybrid_routing(
        scenes,
        scene_config=config,
        cli_overrides={"scene_001": IMAGE_MOTION_PROVIDER},
    )
    assert routing["scene_001"]["provider"] == IMAGE_MOTION_PROVIDER
    assert routing["scene_001"]["reason"] == "explicit CLI/config override"
    # The remaining unrouted scene still follows the configured default.
    assert routing["scene_002"]["provider"] == OPENROUTER_VIDEO_PROVIDER


def test_21_explicit_flow_route_stays_isolated_from_openrouter_default(tmp_path):
    scenes = _routing_plan(tmp_path)
    config = {
        "visual_default_provider": OPENROUTER_VIDEO_PROVIDER,
        "scene_provider_routing": {
            "scene_002": {"provider": GOOGLE_FLOW_PROVIDER, "reason": "persisted routing"},
        },
    }
    routing = build_hybrid_routing(scenes, scene_config=config)
    assert routing["scene_001"]["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert routing["scene_002"]["provider"] == GOOGLE_FLOW_PROVIDER
def test_22_pipeline_openrouter_route_uses_openrouter_path_not_image_motion(
    tmp_path, monkeypatch
):
    openrouter_calls.clear()
    openrouter_fail.clear()
    monkeypatch.setattr(
        "app.orchestrator.visual_providers.OpenRouterVideoVisualProvider",
        _FakeOpenRouterProvider,
    )
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid", scene_count=2,
        default_provider=OPENROUTER_VIDEO_PROVIDER,
    )
    orch._execute_scenes_stage()

    assert openrouter_calls == [("generate", "scene_001"), ("generate", "scene_002")]
    cks = orch.manifest.scene_checkpoints
    assert cks["scene_001"]["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert cks["scene_001"]["status"] == "completed"
    assert cks["scene_002"]["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert cks["scene_002"]["status"] == "completed"
    assert {Path(p).name for p in orch.manifest.scene_video_paths} == {
        "scene_001.mp4", "scene_002.mp4",
    }
    raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    routing = raw["scene_config"]["scene_provider_routing"]
    assert all(item["provider"] == OPENROUTER_VIDEO_PROVIDER for item in routing.values())
    # Every scene stayed on the OpenRouter path; no image_motion/local/Flow route.
    assert all(ck.get("provider") == OPENROUTER_VIDEO_PROVIDER for ck in cks.values())


def test_23_pipeline_failed_openrouter_scene_never_falls_back(tmp_path, monkeypatch):
    openrouter_calls.clear()
    openrouter_fail.clear()
    openrouter_fail.add("scene_001")
    monkeypatch.setattr(
        "app.orchestrator.visual_providers.OpenRouterVideoVisualProvider",
        _FakeOpenRouterProvider,
    )
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid", scene_count=2,
        assets={"scene_002": "image"},
        routing={"scene_001": OPENROUTER_VIDEO_PROVIDER},
    )
    with pytest.raises(Exception, match="Scene scene_001 failed"):
        orch._execute_scenes_stage()

    ck1 = orch.manifest.scene_checkpoints.get("scene_001") or {}
    assert ck1.get("provider") == OPENROUTER_VIDEO_PROVIDER
    assert ck1.get("status") == "failed"
    assert "simulated" in (ck1.get("error") or "")
    # The failed OpenRouter scene does NOT appear as a successful scene and no
    # alternate provider (image_motion/local/Flow) substituted it.
    assert "scene_001" not in {Path(p).stem for p in orch.manifest.scene_video_paths}
    ck2 = orch.manifest.scene_checkpoints["scene_002"]
    assert ck2["provider"] == IMAGE_MOTION_PROVIDER
    assert ck2["status"] == "completed"


def test_24_pipeline_resume_polls_remote_job_without_new_submit(tmp_path, monkeypatch):
    openrouter_calls.clear()
    openrouter_fail.clear()
    monkeypatch.setattr(
        "app.orchestrator.visual_providers.OpenRouterVideoVisualProvider",
        _FakeOpenRouterProvider,
    )
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid", scene_count=1,
        routing={"scene_001": OPENROUTER_VIDEO_PROVIDER},
    )
    # Pre-seed a submitted remote job, as if a previous run had already paid
    # for generation. Resume must poll/recover it, never submit another job.
    orch.manifest.scene_checkpoints["scene_001"] = {
        "provider": OPENROUTER_VIDEO_PROVIDER,
        "status": "generation_submitted",
        "job_id": "job_001",
        "polling_url": "https://openrouter.test/videos/job_001",
        "model": "vendor/fake-video",
        "access": "free",
    }
    orch._execute_scenes_stage()

    assert openrouter_calls == [("resume", "scene_001")]
    ck = orch.manifest.scene_checkpoints["scene_001"]
    assert ck["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert ck["status"] == "completed"
    assert ck["job_id"] == "job_001"
    assert ck["polling_url"] == "https://openrouter.test/videos/job_001"
    assert "scene_001.mp4" in {Path(p).name for p in orch.manifest.scene_video_paths}
    raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert raw["scene_config"]["scene_provider_routing"]["scene_001"][
        "provider"
    ] == OPENROUTER_VIDEO_PROVIDER

# ---------------------------------------------------------------------------
# Phase 8 hardening: ONE hybrid run partitioned between the OpenRouter pass
# and the provider-agnostic boundary pass (audit gap: mixed successful run).
# ---------------------------------------------------------------------------

def test_25_mixed_openrouter_and_boundary_pass_partition(tmp_path, monkeypatch):
    """A single hybrid production routes scene_001 explicitly to
    openrouter_video and leaves scene_002 on the image_motion hybrid default.

    Through the REAL ``_execute_scenes_stage()`` hybrid partition this proves:
    - the OpenRouter execution pass handled exactly its routed scene
      (one fresh ``generate``, no second submission, no other scene);
    - the boundary scene executed through the provider-agnostic boundary pass
      with the real ImageMotion provider (zero-cost provenance, no remote-job
      identity);
    - checkpoints carry truthful per-provider identity with no
      cross-provider contamination in either direction;
    - both scene paths are recorded in ``scene_video_paths`` and validate;
    - Flow is never constructed and no provider substitution/fallback occurs.

    Fully hermetic: the OpenRouter provider is faked at the factory boundary
    (established Phase 7E pattern); ImageMotion renders a local fixture;
    no network, no credentials, no live generation.
    """
    from app.flow.scene_generator import validate_video_file
    from app.orchestrator.models import PipelineStage

    openrouter_calls.clear()
    openrouter_fail.clear()
    monkeypatch.setattr(
        "app.orchestrator.visual_providers.OpenRouterVideoVisualProvider",
        _FakeOpenRouterProvider,
    )
    # Cost firewall sentinel: Flow must never be constructed in this run.
    monkeypatch.setattr(
        "app.orchestrator.visual_providers.GoogleFlowVisualProvider",
        _NeverConstructed,
    )
    monkeypatch.setattr(
        "app.flow.scene_generator.FlowSceneGenerator",
        _NeverConstructed,
    )

    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid", scene_count=2,
        # scene_001: explicit route to OpenRouter (highest precedence).
        # scene_002: unrouted -> visual default (image_motion) -> boundary pass.
        routing={"scene_001": OPENROUTER_VIDEO_PROVIDER},
    )
    orch._execute_scenes_stage()

    # Partition: the OpenRouter pass ran exactly once, for its routed scene
    # only, as a fresh generation (never a duplicate submission, never the
    # boundary scene).
    assert openrouter_calls == [("generate", "scene_001")]

    # Both scenes completed; both paths recorded; both artifacts valid (7A).
    paths = {Path(p).name: p for p in orch.manifest.scene_video_paths}
    assert set(paths.keys()) == {"scene_001.mp4", "scene_002.mp4"}
    for path in paths.values():
        assert validate_video_file(Path(path), "9:16").get("valid")

    # OpenRouter checkpoint: truthful OpenRouter identity + remote-job
    # provenance, and NO boundary-pass (zero-cost source-asset) provenance.
    ck1 = orch.manifest.scene_checkpoints["scene_001"]
    assert ck1["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert ck1["status"] == "completed"
    assert ck1["model"] == "vendor/fake-video"
    assert ck1["access"] == "free"
    assert ck1["job_id"] == "job_scene_001"
    assert ck1["polling_url"] == "https://openrouter.test/videos/job_scene_001"
    assert ck1["validation"]["valid"] is True
    assert ck1["file_path"].endswith("scene_001.mp4")
    assert "source_asset" not in ck1

    # Boundary checkpoint: truthful ImageMotion identity + zero-cost
    # provenance, and NO remote-job identity (a fake OpenRouter result cannot
    # leak into the boundary scene, and vice versa).
    ck2 = orch.manifest.scene_checkpoints["scene_002"]
    assert ck2["provider"] == IMAGE_MOTION_PROVIDER
    assert ck2["status"] == "completed"
    assert ck2["generation_cost"] == 0
    assert "source_asset" in ck2
    assert "job_id" not in ck2
    assert "polling_url" not in ck2
    assert "model" not in ck2
    assert ck2["validation"]["valid"] is True

    # Durable persisted state agrees with the in-memory checkpoints.
    raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    raw_cks = raw["scene_checkpoints"]
    assert raw_cks["scene_001"]["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert raw_cks["scene_002"]["provider"] == IMAGE_MOTION_PROVIDER
    routing = raw["scene_config"]["scene_provider_routing"]
    assert routing["scene_001"]["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert routing["scene_002"]["provider"] == IMAGE_MOTION_PROVIDER

    # Hybrid stage result records both providers; zero credits consumed
    # (fake access is "free"; ImageMotion is zero-cost).
    meta = orch.manifest.get_stage_result(PipelineStage.SCENES).metadata
    assert sorted(meta["providers"]) == [
        IMAGE_MOTION_PROVIDER,
        OPENROUTER_VIDEO_PROVIDER,
    ]
    assert meta["scene_count"] == 2
    assert orch.manifest.total_credits_used == 0
