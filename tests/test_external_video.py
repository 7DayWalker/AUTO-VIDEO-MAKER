"""Phase 7B — external video intake route tests.

Proves the ``external_video`` provider identity: a pre-existing externally
obtained video clip enters through the Phase 6C intake gate with truthful
provenance, is normalized by the existing LocalAsset behavior, and satisfies
the Phase 7A unified scene output contract.

All tests are offline: local FFmpeg fixtures only, no Flow, no browser, no
external calls, no credits.
"""

import json
from pathlib import Path

import pytest

from app.orchestrator.assets import load_asset_manifest, sha256_file
from app.flow.scene_generator import validate_video_file
from app.orchestrator.exceptions import StageFailedError
from app.orchestrator.visual_providers import (
    EXTERNAL_VIDEO_PROVIDER,
    ExternalVideoVisualProvider,
    create_visual_provider,
    normalize_provider,
)

from tests.test_asset_workflow import build_orchestrator, make_clip, make_image

PROJECT_ID = "20260103_assets_probe_001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _external_orchestrator(tmp_path, monkeypatch, *, assets, routing,
                           origin=None, scene_count=2):
    """Hybrid orchestrator with per-scene explicit assets and routing.

    ``assets``/``routing`` are {scene_id: kind/provider}; unspecified scenes
    fall back to image_motion with a mapped image so preflight passes.
    """
    routing = dict(routing)
    assets = dict(assets)
    for i in range(1, scene_count + 1):
        sid = f"scene_{i:03d}"
        routing.setdefault(sid, "image_motion")
        assets.setdefault(sid, "image")
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch,
        mode="hybrid",
        routing=routing,
        explicit_assets=assets,
    )
    if origin is not None:
        orch.config.external_video_source = origin
    return orch


def _run_scene_stage(orch):
    """Run the full scenes stage and return the raw asset manifest."""
    orch._execute_scenes_stage()
    return load_asset_manifest(orch.project_dir)


# ---------------------------------------------------------------------------
# Test 1 — external_video is a valid, distinct, routable provider
# ---------------------------------------------------------------------------

def test_external_video_is_valid_provider_and_factory_target(tmp_path):
    assert normalize_provider("external_video") == "external_video"
    assert normalize_provider("EXTERNAL_VIDEO") == "external_video"

    out_dir = tmp_path / "scenes"
    provider = create_visual_provider(
        "hybrid",
        scene_config={},
        scenes_output_dir=out_dir,
        provider_name="external_video",
    )
    assert isinstance(provider, ExternalVideoVisualProvider)
    assert provider.name == EXTERNAL_VIDEO_PROVIDER
    # Distinct identity: external_video is NOT local_asset.
    assert provider.name != "local_asset"

    # Unknown providers remain rejected.
    with pytest.raises(ValueError):
        normalize_provider("not_a_provider")

    # Zero-credit firewall: constructing the provider never touches Flow.
    assert provider.requires_browser is False


# ---------------------------------------------------------------------------
# Test 2 — external video intake: video accepted, verified, project-owned
# ---------------------------------------------------------------------------

def test_external_video_intake_verifies_and_stores_project_copy(tmp_path, monkeypatch):
    orch = _external_orchestrator(
        tmp_path, monkeypatch,
        assets={"scene_001": "video"}, routing={"scene_001": "external_video"},
    )
    manifest = _run_scene_stage(orch)
    entry = manifest["assets"]["scene_001"]

    assert entry["media_type"] == "video"
    assert entry["provider"] == EXTERNAL_VIDEO_PROVIDER
    assert entry["stored_as"].startswith("assets/source/")
    assert entry["stored_as"].endswith(".mp4")

    # SHA-256 verification against the project-owned copy.
    stored = Path(orch.project_dir) / entry["stored_as"]
    assert stored.is_file()
    assert entry["sha256"] == sha256_file(stored)
    assert entry["bytes"] == stored.stat().st_size


# ---------------------------------------------------------------------------
# Test 3 — external_video rejects an image honestly
# ---------------------------------------------------------------------------

def test_external_video_rejects_image_via_media_type_gate(tmp_path, monkeypatch):
    orch = _external_orchestrator(
        tmp_path, monkeypatch,
        assets={"scene_001": "image"}, routing={"scene_001": "external_video"},
    )
    with pytest.raises(StageFailedError) as excinfo:
        orch._execute_scenes_stage()
    message = str(excinfo.value)
    assert "scene_001" in message
    assert "media type mismatch" in message
    assert EXTERNAL_VIDEO_PROVIDER in message


# ---------------------------------------------------------------------------
# Test 4 — external video produces standard Phase 7A scene output
# ---------------------------------------------------------------------------

def test_external_video_produces_standard_scene_output(tmp_path, monkeypatch):
    orch = _external_orchestrator(
        tmp_path, monkeypatch,
        assets={"scene_001": "video"}, routing={"scene_001": "external_video"},
    )
    _run_scene_stage(orch)

    output = Path(orch.project_dir) / "scenes" / PROJECT_ID / "scene_001.mp4"
    assert output.is_file()
    assert output.suffix.lower() == ".mp4"

    # The authoritative validator — the Phase 7A scene output contract.
    validation = validate_video_file(output, "9:16")
    assert validation.get("valid"), validation.get("error")
    assert validation["duration"] > 0
    assert validation["width"] * 16 == validation["height"] * 9

    # Checkpoint records the truthful execution identity.
    checkpoint = orch.manifest.scene_checkpoints["scene_001"]
    assert checkpoint["provider"] == EXTERNAL_VIDEO_PROVIDER
    assert checkpoint["status"] == "completed"
    assert checkpoint["generation_cost"] == 0

    # Assembly consumes it provider-agnostically via scene_video_paths.
    assert str(output) in orch.manifest.scene_video_paths


# ---------------------------------------------------------------------------
# Test 5 — external provenance recorded; user provenance unchanged
# ---------------------------------------------------------------------------

def test_external_provenance_recorded_and_user_provenance_unchanged(tmp_path, monkeypatch):
    orch = _external_orchestrator(
        tmp_path, monkeypatch,
        assets={"scene_001": "video"}, routing={"scene_001": "external_video"},
        origin="google_flow_private",
    )
    manifest = _run_scene_stage(orch)

    external_entry = manifest["assets"]["scene_001"]
    assert external_entry["supplied_by"] == "external_generation"
    assert external_entry["source_provider"] == "google_flow_private"
    assert external_entry["source_status"] == "success"

    # Ordinary user-supplied asset provenance remains unchanged.
    user_entry = manifest["assets"]["scene_002"]
    assert user_entry["supplied_by"] == "user"
    assert "source_provider" not in user_entry
    assert user_entry["provider"] == "image_motion"

    # Provenance survives a manifest reload (durable, not in-memory only).
    reloaded = load_asset_manifest(orch.project_dir)
    assert reloaded["assets"]["scene_001"]["source_provider"] == "google_flow_private"


# ---------------------------------------------------------------------------
# Test 6 — resume uses the project-owned copy, not the original file
# ---------------------------------------------------------------------------

def test_resume_uses_project_owned_copy_not_original(tmp_path, monkeypatch):
    orch = _external_orchestrator(
        tmp_path, monkeypatch,
        assets={"scene_001": "video"}, routing={"scene_001": "external_video"},
    )
    _run_scene_stage(orch)

    # Capture the project-owned copy fingerprint.
    manifest = load_asset_manifest(orch.project_dir)
    entry = manifest["assets"]["scene_001"]
    stored = Path(orch.project_dir) / entry["stored_as"]
    original_hash = entry["sha256"]

    # Fresh orchestrator instance = fresh-process resume semantics. A real
    # resume (produce.py --resume) supplies NO --asset flags, so mappings are
    # empty and the project-owned copy is the only authority.
    orch2 = _external_orchestrator(
        tmp_path, monkeypatch,
        assets={"scene_001": "video"}, routing={"scene_001": "external_video"},
    )
    orch2.config.asset_mappings = {}
    # (build_orchestrator re-seeds external/legacy fixtures, so destroy ALL
    # original copies only AFTER it has run.)
    for candidate in (tmp_path / "external").glob("scene_001*"):
        candidate.unlink()
    for candidate in (tmp_path / "legacy_videos").glob("scene_001*"):
        candidate.unlink()
    # Remove the completed scene output so regeneration is REQUIRED — this
    # proves the provider consumed the project-owned copy, not any original.
    output = Path(orch2.project_dir) / "scenes" / PROJECT_ID / "scene_001.mp4"
    output.unlink()

    # The stage must succeed even though every original file is gone.
    orch2._execute_scenes_stage()

    reloaded = load_asset_manifest(orch2.project_dir)
    assert reloaded["assets"]["scene_001"]["sha256"] == original_hash
    assert Path(orch2.project_dir) / reloaded["assets"]["scene_001"]["stored_as"] == stored
    assert stored.is_file()
    assert sha256_file(stored) == original_hash
    # The scene output was regenerated and satisfies the contract again.
    assert output.is_file()
    assert validate_video_file(output, "9:16").get("valid")
    # scene_video_paths holds scene outputs (not source copies) — Phase 7A.
    assert str(output) in [str(Path(p)) for p in orch2.manifest.scene_video_paths]


# ---------------------------------------------------------------------------
# Test 7 — missing external video fails honestly (no fallback, no auto-source)
# ---------------------------------------------------------------------------

def test_missing_external_video_fails_honestly(tmp_path, monkeypatch):
    # scene_001 routed to external_video but given NO asset anywhere.
    orch = _external_orchestrator(
        tmp_path, monkeypatch,
        assets={"scene_001": None}, routing={"scene_001": "external_video"},
    )
    for candidate in (tmp_path / "external").glob("scene_001*"):
        candidate.unlink()
    for candidate in (tmp_path / "legacy_images").glob("scene_001*"):
        candidate.unlink()
    for candidate in (tmp_path / "legacy_videos").glob("scene_001*"):
        candidate.unlink()

    with pytest.raises(StageFailedError) as excinfo:
        orch._execute_scenes_stage()
    message = str(excinfo.value)
    assert "scene_001" in message
    assert "preflight failed" in message
    # Honest failure: no fabricated provider, no image fallback suggestion.
    assert "google_flow" not in message


# ---------------------------------------------------------------------------
# Test 8 — provider-level route: real normalization + image refusal
# ---------------------------------------------------------------------------

def test_external_video_provider_direct_route_and_image_refusal(tmp_path):
    asset_dir = tmp_path / "clips"
    out_dir = tmp_path / "scenes" / PROJECT_ID
    make_clip(asset_dir / "scene_001.mp4", seconds=2)
    provider = ExternalVideoVisualProvider(asset_dir, out_dir)

    from app.flow.scene_models import FlowGenerationConfig, SceneGenerationRequest

    request = SceneGenerationRequest(
        project_id=PROJECT_ID,
        scene_id="scene_001",
        sequence=1,
        visual_prompt="External clip probe.",
        config=FlowGenerationConfig(
            model="local_asset", aspect_ratio="9:16", duration=2,
            count=1, agent_enabled=False,
        ),
    )
    checkpoints = []
    result = provider.generate_scene(
        request, require_confirmation=False,
        checkpoint_callback=checkpoints.append,
    )
    assert result.success is True
    assert result.verification["provider"] == EXTERNAL_VIDEO_PROVIDER
    assert result.verification["classification"] == "zero_cost_external_video"
    assert result.file_path == out_dir / "scene_001.mp4"
    assert validate_video_file(result.file_path, "9:16").get("valid")
    # Checkpoint identity is the real route, not the inherited base class.
    assert checkpoints[-1]["provider"] == EXTERNAL_VIDEO_PROVIDER

    # Defense in depth: an image source is refused with truthful identity.
    image_dir = tmp_path / "imgs"
    make_image(image_dir / "scene_002.png")
    image_provider = ExternalVideoVisualProvider(image_dir, out_dir)
    image_request = SceneGenerationRequest(
        project_id=PROJECT_ID,
        scene_id="scene_002",
        sequence=2,
        visual_prompt="Should not pass.",
        config=FlowGenerationConfig(
            model="local_asset", aspect_ratio="9:16", duration=2,
            count=1, agent_enabled=False,
        ),
    )
    refusal = image_provider.generate_scene(
        image_request, require_confirmation=False,
        # The media-type gate blocks images upstream; defense in depth is
        # exercised by passing an image source directly (asset_override).
        asset_override=image_dir / "scene_002.png",
    )
    assert refusal.success is False
    assert refusal.verification["provider"] == EXTERNAL_VIDEO_PROVIDER
    assert "video asset" in refusal.error
