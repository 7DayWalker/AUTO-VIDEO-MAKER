"""Phase 7A — unified scene output contract tests.

Freezes the existing downstream contract every scene provider already
satisfies: a completed scene is interchangeable when it resolves to a valid
MP4 (per ``validate_video_file``) at the established output location and is
recorded through the established manifest/checkpoint path.

All tests are offline: local FFmpeg fixtures only, no Flow, no browser, no
external calls, no credits.
"""

from pathlib import Path

import pytest

from app.assembly.models import Scene as AssemblyScene
from app.flow.scene_generator import validate_video_file
from app.flow.scene_models import (
    FlowGenerationConfig,
    SceneGenerationRequest,
)
from app.orchestrator.visual_providers import (
    IMAGE_MOTION_PROVIDER,
    LOCAL_ASSET_PROVIDER,
    ImageMotionVisualProvider,
    LocalAssetVisualProvider,
)

from tests.test_asset_workflow import (
    build_orchestrator,
    make_clip,
    make_image,
)

PROJECT_ID = "20260103_assets_probe_001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_request(scene_id: str, sequence: int, duration: int = 2) -> SceneGenerationRequest:
    """Minimal scene generation request matching the shared loop's wiring."""
    return SceneGenerationRequest(
        project_id=PROJECT_ID,
        scene_id=scene_id,
        sequence=sequence,
        visual_prompt="A calm visual for the scene output contract probe.",
        config=FlowGenerationConfig(
            model="local_asset",
            aspect_ratio="9:16",
            duration=duration,
            count=1,
            agent_enabled=False,
        ),
    )


def render_imagemotion_scene(tmp_path: Path, scene_id="scene_001", sequence=1):
    """Render one scene through the real ImageMotion route (still -> MP4)."""
    image_dir = tmp_path / "images"
    out_dir = tmp_path / "scenes" / PROJECT_ID
    make_image(image_dir / f"{scene_id}.png")
    provider = ImageMotionVisualProvider(image_dir, out_dir)
    result = provider.generate_scene(
        make_request(scene_id, sequence), require_confirmation=False
    )
    return result


def render_localasset_video_scene(tmp_path: Path, scene_id="scene_001", sequence=1):
    """Normalize one scene through the real LocalAsset video route (MP4 -> MP4)."""
    asset_dir = tmp_path / "videos"
    out_dir = tmp_path / "scenes" / PROJECT_ID
    make_clip(asset_dir / f"{scene_id}.mp4")
    provider = LocalAssetVisualProvider(asset_dir, out_dir)
    result = provider.generate_scene(
        make_request(scene_id, sequence), require_confirmation=False
    )
    return result


def assert_satisfies_scene_output_contract(result, out_dir: Path) -> None:
    """The single downstream contract every provider output must satisfy."""
    assert result.success is True
    assert result.status == "completed"
    assert result.file_path is not None
    assert result.file_path.exists()
    assert result.file_path.suffix.lower() == ".mp4"
    assert result.file_path.parent == out_dir

    # The authoritative validator — same call the shared scene loop makes.
    validation = validate_video_file(result.file_path, "9:16")
    assert validation.get("valid"), validation.get("error")
    assert validation["duration"] > 0
    assert validation["width"] * 16 == validation["height"] * 9

    # Assembly-facing representation constructs without provider knowledge.
    scene = AssemblyScene(id=result.file_path.stem, video=str(result.file_path))
    assert scene.id == result.file_path.stem
    assert scene.video == str(result.file_path)
    assert Path(scene.video).exists()


# ---------------------------------------------------------------------------
# Test 1 — ImageMotion output satisfies the unified scene output contract
# ---------------------------------------------------------------------------

def test_imagemotion_output_satisfies_scene_output_contract(tmp_path):
    result = render_imagemotion_scene(tmp_path, scene_id="scene_001", sequence=1)
    out_dir = tmp_path / "scenes" / PROJECT_ID
    assert_satisfies_scene_output_contract(result, out_dir)

    # Provider provenance is recorded on the result (not needed downstream).
    assert result.verification["provider"] == IMAGE_MOTION_PROVIDER


# ---------------------------------------------------------------------------
# Test 2 — LocalAsset video output satisfies the same contract
# ---------------------------------------------------------------------------

def test_localasset_video_output_satisfies_same_contract(tmp_path):
    result = render_localasset_video_scene(tmp_path, scene_id="scene_001", sequence=1)
    out_dir = tmp_path / "scenes" / PROJECT_ID
    assert_satisfies_scene_output_contract(result, out_dir)

    assert result.verification["provider"] == LOCAL_ASSET_PROVIDER


# ---------------------------------------------------------------------------
# Test 3 — Provider outputs are interchangeable downstream
# ---------------------------------------------------------------------------

def test_provider_outputs_are_interchangeable_downstream(tmp_path):
    """Both routes reduce to the same downstream representation:
    scene_id + validated MP4 path — exactly what the assembly stage consumes
    when it builds AssemblyScene objects from manifest.scene_video_paths."""
    motion_result = render_imagemotion_scene(
        tmp_path / "a", scene_id="scene_001", sequence=1
    )
    asset_result = render_localasset_video_scene(
        tmp_path / "b", scene_id="scene_002", sequence=2
    )
    assert motion_result.success and asset_result.success

    # Mirror the assembly stage's construction loop
    # (pipeline._execute_assembly_stage): it iterates scene video paths and
    # builds AssemblyScene(id=stem, video=path) with zero provider awareness.
    scene_video_paths = [str(motion_result.file_path), str(asset_result.file_path)]
    assembly_scenes = []
    for video_path in scene_video_paths:
        assert Path(video_path).exists()
        assembly_scenes.append(
            AssemblyScene(id=Path(video_path).stem, video=video_path)
        )

    # Both routes produce identically shaped, provider-agnostic scenes.
    assert [s.id for s in assembly_scenes] == ["scene_001", "scene_002"]
    for scene, video_path in zip(assembly_scenes, scene_video_paths):
        assert scene.video == video_path
        # Same validation contract applies identically to both origins.
        validation = validate_video_file(Path(scene.video), "9:16")
        assert validation.get("valid"), validation.get("error")


# ---------------------------------------------------------------------------
# Test 4 — Completed valid scenes are reused on resume; missing/invalid are not
# ---------------------------------------------------------------------------

def test_completed_valid_scene_is_reused_and_provider_not_invoked(tmp_path, monkeypatch):
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch,
        mode="hybrid",
        explicit_assets={"scene_001": "image", "scene_002": "image"},
    )
    orch._execute_scenes_stage()
    first_paths = list(orch.manifest.scene_video_paths)
    first_mtime = Path(first_paths[0]).stat().st_mtime_ns

    # Instrument provider invocation at the provider factory's module: the
    # pipeline imports the factory function-locally, so patching the module
    # attribute reaches every construction site without touching production
    # code or provider contracts.
    import app.orchestrator.visual_providers as vp_module

    real_factory = vp_module.create_visual_provider
    generate_calls = {"count": 0}

    def counting_factory(*args, **kwargs):
        provider = real_factory(*args, **kwargs)
        real_generate = provider.generate_scene

        def spy_generate(*g_args, **g_kwargs):
            generate_calls["count"] += 1
            return real_generate(*g_args, **g_kwargs)

        provider.generate_scene = spy_generate
        return provider

    monkeypatch.setattr(vp_module, "create_visual_provider", counting_factory)

    # Resume pass: completed + valid scenes must be reused as-is.
    orch._execute_scenes_stage()
    assert generate_calls["count"] == 0
    assert orch.manifest.scene_video_paths == first_paths
    assert Path(first_paths[0]).stat().st_mtime_ns == first_mtime

    # Negative case 1: a missing output is NOT reusable -> provider regenerates.
    missing_path = Path(first_paths[0])
    missing_path.unlink()
    orch._execute_scenes_stage()
    assert generate_calls["count"] == 1
    assert missing_path.exists()

    # Negative case 2: an invalid (corrupt) output is NOT reusable either.
    corrupt_path = Path(orch.manifest.scene_video_paths[0])
    corrupt_path.write_bytes(b"\x00" * 2048)
    orch._execute_scenes_stage()
    assert generate_calls["count"] == 2
    validation = validate_video_file(corrupt_path, "9:16")
    assert validation.get("valid")
