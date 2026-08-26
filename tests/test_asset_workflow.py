"""Phase 6C — per-project asset workflow tests.

All tests are offline. Google Flow is never launched; Flow construction is
proven impossible for zero-cost failures via sentinel fakes.
"""

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.orchestrator.assets as asset_workflow
from app.flow.scene_generator import load_manifest as load_scene_manifest
from app.orchestrator.models import OrchestratorConfig, ProjectManifest
from app.orchestrator.pipeline import ProductionOrchestrator
from app.orchestrator.visual_providers import (
    GOOGLE_FLOW_PROVIDER,
    IMAGE_MOTION_PROVIDER,
    LOCAL_ASSET_PROVIDER,
    build_hybrid_routing,
    create_visual_provider,
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
    plan_id = "20260103_assets_probe_001"
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
        "title": "Asset Probe",
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


def load_plan_obj(tmp_path: Path):
    """Return (plan_path, parsed plan) using the repo's real plan model."""
    from app.content.models import ProductionPlan

    plan_path = make_plan_file(tmp_path)
    plan = ProductionPlan(
        **json.loads(plan_path.read_text(encoding="utf-8"))
    )
    return plan_path, plan


PLAN_ID = "20260103_assets_probe_001"


def _fresh_orchestrator(tmp_path: Path, *, mode="hybrid",
                        default_provider="image_motion", routing=None,
                        asset_mappings=None):
    """Wire a NEW ProductionOrchestrator over the same temp project.

    Call repeatedly for one tmp_path to simulate resume-style runs; each
    instance reloads persisted state from disk like a fresh process.
    """
    plan_path = tmp_path / "content" / PLAN_ID / "production_plan.json"
    if not plan_path.exists():
        make_plan_file(tmp_path)
    config = OrchestratorConfig(
        topic="asset workflow probe topic",
        flow_project_url="",
        visual_mode=mode,
        visual_default_provider=default_provider,
        visual_scene_routing=dict(routing or {}),
        asset_mappings=dict(asset_mappings or {}),
    )
    orch = ProductionOrchestrator(config)
    orch.project_dir = tmp_path
    orch.manifest = ProjectManifest(
        project_id=PLAN_ID,
        topic=config.topic,
        content_plan_path=str(plan_path),
        scene_config=config.to_scene_config_dict(),
    )
    orch.manifest.initialize_stages()
    return orch


def build_orchestrator(tmp_path: Path, monkeypatch, *, mode="hybrid",
                       scene_count=2, default_provider="image_motion",
                       routing=None, explicit_assets: dict = None,
                       sabotage_discovery=False):
    """Wire an orchestrator against an isolated temp project.

    explicit_assets: {scene_id: "image"|"video"} -> generates fixture files
    under tmp_path/external/ AND feeds them through config.asset_mappings,
    exactly like repeated --asset SCENE_ID=PATH usage. Mapped scenes also get
    legacy twins (different extension/name) so manifest-over-legacy
    precedence is genuinely observable.

    Pure-legacy mode (explicit_assets empty): ALL scenes are seeded into the
    legacy discovery trees only — no mappings, no asset manifest.

    Partially-mapped projects leave unmapped scenes nowhere-to-be-found so
    preflight failures exercise truthful reporting.

    sabotage_discovery: leaves the legacy env dirs EMPTY so only
    project-owned copies can satisfy providers.
    """
    _, plan = load_plan_obj(tmp_path)

    img_dir = tmp_path / "legacy_images"
    vid_dir = tmp_path / "legacy_videos"
    img_dir.mkdir(exist_ok=True)
    vid_dir.mkdir(exist_ok=True)

    external = tmp_path / "external"
    external.mkdir(exist_ok=True)
    asset_mappings: dict = {}

    for scene in sorted(plan.scenes, key=lambda s: s.sequence):
        kind = (explicit_assets or {}).get(scene.id)
        if kind == "image":
            p = external / f"{scene.id}_garden.jpg"
            if not p.exists():
                make_image(p)
            asset_mappings[scene.id] = str(p)
            if not sabotage_discovery:
                make_image(img_dir / f"{scene.id}.png")  # legacy twin
        elif kind == "video":
            p = external / f"{scene.id}_broll.mp4"
            if not p.exists():
                make_clip(p)
            asset_mappings[scene.id] = str(p)
            if not sabotage_discovery:
                make_clip(vid_dir / f"{scene.id}.mp4")   # legacy twin
        elif not asset_mappings and not sabotage_discovery:
            # Pure-legacy project: seed both discovery trees, map nothing.
            make_image(img_dir / f"{scene.id}.png")
            make_clip(vid_dir / f"{scene.id}.mp4")

    monkeypatch.setenv("AVM_IMAGE_ASSET_DIR", str(img_dir))
    monkeypatch.setenv("AVM_LOCAL_ASSET_DIR", str(vid_dir))
    orch = _fresh_orchestrator(
        tmp_path,
        mode=mode,
        default_provider=default_provider,
        routing=routing,
        asset_mappings=asset_mappings,
    )
    return orch, PLAN_ID


# ---------------------------------------------------------------------------
# Unit: intake, fingerprinting, parsing (required tests 1–5)
# ---------------------------------------------------------------------------

def _unit_plan(tmp_path):
    _, plan = load_plan_obj(tmp_path)
    return plan


def test_01_intake_copies_into_project_owned_directory(tmp_path):
    plan = _unit_plan(tmp_path)
    src = tmp_path / "external" / "garden.jpg"
    make_image(src)
    manifest, errors = asset_workflow.intake_assets(
        tmp_path, plan.scenes,
        {"scene_001": str(src)},
        {"scene_001": IMAGE_MOTION_PROVIDER},
    )
    assert errors == []
    stored = tmp_path / "assets" / "source" / "scene_001.jpg"
    assert stored.is_file(), "must copy into project-owned assets/source/"
    entry = manifest["assets"]["scene_001"]
    assert entry["stored_as"] == "assets/source/scene_001.jpg"
    assert entry["media_type"] == "image"


def test_02_asset_manifest_written_with_full_schema(tmp_path):
    plan = _unit_plan(tmp_path)
    src = tmp_path / "external" / "garden.jpg"
    make_image(src)
    asset_workflow.intake_assets(
        tmp_path, plan.scenes, {"scene_001": str(src)},
        {"scene_001": IMAGE_MOTION_PROVIDER},
    )
    raw = json.loads(
        (tmp_path / "assets" / "asset_manifest.json").read_text("utf-8")
    )
    entry = raw["assets"]["scene_001"]
    assert raw["version"] == 1
    assert entry["source_name"] == "garden.jpg"
    assert entry["media_type"] == "image"
    assert entry["provider"] == IMAGE_MOTION_PROVIDER
    assert isinstance(entry["bytes"], int) and entry["bytes"] > 0
    assert len(entry["sha256"]) == 64


def test_03_sha256_recorded_correctly(tmp_path):
    plan = _unit_plan(tmp_path)
    src = tmp_path / "external" / "garden.jpg"
    make_image(src)
    asset_workflow.intake_assets(
        tmp_path, plan.scenes, {"scene_001": str(src)},
        {"scene_001": IMAGE_MOTION_PROVIDER},
    )
    expected = hashlib.sha256(src.read_bytes()).hexdigest()
    stored = tmp_path / "assets" / "source" / "scene_001.jpg"
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == expected
    raw = json.loads(
        (tmp_path / "assets" / "asset_manifest.json").read_text("utf-8")
    )
    assert raw["assets"]["scene_001"]["sha256"] == expected


def test_04_verification_passes_when_unchanged(tmp_path):
    plan = _unit_plan(tmp_path)
    src = tmp_path / "external" / "garden.jpg"
    make_image(src)
    manifest, errors = asset_workflow.intake_assets(
        tmp_path, plan.scenes, {"scene_001": str(src)},
        {"scene_001": IMAGE_MOTION_PROVIDER},
    )
    assert errors == []
    assert (
        asset_workflow.verify_stored_asset(
            tmp_path, manifest["assets"]["scene_001"]
        )
        is None
    )


def test_05_verification_detects_mutation(tmp_path):
    plan = _unit_plan(tmp_path)
    src = tmp_path / "external" / "garden.jpg"
    make_image(src)
    manifest, _ = asset_workflow.intake_assets(
        tmp_path, plan.scenes, {"scene_001": str(src)},
        {"scene_001": IMAGE_MOTION_PROVIDER},
    )
    entry = manifest["assets"]["scene_001"]
    stored = tmp_path / entry["stored_as"]
    stored.write_bytes(b"\x00\x01tampered")  # same size class, different hash
    reason = asset_workflow.verify_stored_asset(tmp_path, entry)
    assert reason and "SHA-256 integrity mismatch" in reason


def test_06_windows_safe_mapping_parsing():
    parse = asset_workflow.parse_asset_mappings
    parsed = parse(["scene_001=C:\\assets\\garden.jpg"])
    assert parsed == {"scene_001": "C:\\assets\\garden.jpg"}
    parsed = parse(['scene_001="C:\\my dir\\gar=den.jpg"'])
    assert list(parsed) == ["scene_001"]
    assert parsed["scene_001"].endswith("gar=den.jpg")
    parsed = parse(["scene_001=C:\\a.jpg", "scene_001=C:\\a.jpg"])  # identical ok
    assert len(parsed) == 1
    with pytest.raises(ValueError):
        parse(["scene_001=C:\\a.jpg", "scene_001=C:\\b.jpg"])  # conflict
    with pytest.raises(ValueError):
        parse(["scene_001="])
    with pytest.raises(ValueError):
        parse(["=C:\\a.jpg"])


# ---------------------------------------------------------------------------
# Resolution precedence (required tests 6–7)
# ---------------------------------------------------------------------------

def _gate_sources(orch, provider=IMAGE_MOTION_PROVIDER):
    """Run the Phase 6C gate directly and return its source map."""
    from app.flow.scene_generator import validate_video_file

    _, plan = load_plan_obj(orch.project_dir)
    scenes_dir = orch.project_dir / "scenes" / plan.project_id
    scene_manifest = load_scene_manifest(scenes_dir, plan.project_id)
    return orch._run_project_asset_gate(
        plan=plan,
        scoped_scenes=list(plan.scenes),
        scoped_providers={s.id: provider for s in plan.scenes},
        scene_manifest=scene_manifest,
        validate_video_file=validate_video_file,
        project_scenes_dir=scenes_dir,
    )


def test_07_image_motion_prefers_manifest_over_legacy(tmp_path, monkeypatch):
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid",
        explicit_assets={"scene_001": "image", "scene_002": "image"},
    )
    # Legacy copies also exist and differ (scene_NNN.png vs stored .jpg).
    sources = _gate_sources(orch)
    for sid in ("scene_001", "scene_002"):
        assert Path(sources[sid]).parts[-3:-1] == ("assets", "source")
        assert sources[sid].endswith(f"{sid}.jpg")


def test_08_local_asset_prefers_manifest_over_legacy(tmp_path, monkeypatch):
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid",
        default_provider="local_asset",
        explicit_assets={"scene_001": "video", "scene_002": "video"},
    )
    sources = _gate_sources(orch, LOCAL_ASSET_PROVIDER)
    for sid in ("scene_001", "scene_002"):
        normalized = sources[sid].replace("\\", "/")
        assert "/assets/source/" in normalized
        assert normalized.endswith(f"{sid}.mp4")


# ---------------------------------------------------------------------------
# Legacy compatibility (required tests 8 + 18 partial)
# ---------------------------------------------------------------------------

def test_09_legacy_discovery_without_asset_manifest(tmp_path, monkeypatch):
    """No --asset mappings at all: legacy dirs satisfy the local stage."""
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="local",
        explicit_assets={},  # nothing explicit -> pure legacy discovery
    )
    assert not (tmp_path / "assets" / "asset_manifest.json").exists()
    orch._execute_scenes_stage()
    assert len(orch.manifest.scene_video_paths) == 2
    ck = orch.manifest.scene_checkpoints["scene_001"]
    assert ck["provider"] == LOCAL_ASSET_PROVIDER
    # Provenance pins the legacy-discovered file.
    assert "legacy_videos" in ck["source_asset"]
    # Reading a missing manifest must not write one (no unnecessary rewrite).
    assert not (tmp_path / "assets" / "asset_manifest.json").exists()


# ---------------------------------------------------------------------------
# Preflight truthfulness + firewall (required tests 9, 10, 11, 12, 13, 14, 15)
# ---------------------------------------------------------------------------

def test_10_missing_image_fails_and_routing_persisted_first(
    tmp_path, monkeypatch
):
    import app.flow.scene_generator as flow_module

    class _Forbidden:
        def __init__(self, *a, **k):
            raise AssertionError("Flow constructed on zero-cost failure")

    monkeypatch.setattr(flow_module, "FlowSceneGenerator", _Forbidden)
    monkeypatch.setattr(
        "app.orchestrator.visual_providers.GoogleFlowVisualProvider",
        _Forbidden,
    )
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid",
        explicit_assets={"scene_001": "image"},  # scene_002 image absent
    )
    with pytest.raises(Exception, match="No local image"):
        orch._execute_scenes_stage()

    raw = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    routing = raw["scene_config"]["scene_provider_routing"]
    assert set(routing) == {"scene_001", "scene_002"}
    assert raw["scene_config"]["visual_mode"] == "hybrid"
    # The asset manifest was durably persisted BEFORE the generation failure.
    amf = tmp_path / "assets" / "asset_manifest.json"
    assert amf.exists()
    assert set(json.loads(amf.read_text("utf-8"))["assets"]) == {"scene_001"}


def test_11_missing_local_asset_fails_truthfully(tmp_path, monkeypatch):
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid",
        # Both scenes explicitly routed local_asset (Phase 6B override path).
        routing={
            "scene_001": LOCAL_ASSET_PROVIDER,
            "scene_002": LOCAL_ASSET_PROVIDER,
        },
        explicit_assets={"scene_001": "video"},  # scene_002 video absent
    )
    with pytest.raises(Exception, match="No local asset found"):
        orch._execute_scenes_stage()


def test_12_mutated_stored_asset_fails_fingerprint(tmp_path, monkeypatch):
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid",
        explicit_assets={"scene_001": "image", "scene_002": "image"},
    )
    from app.flow.scene_generator import validate_video_file

    _, plan = load_plan_obj(orch.project_dir)
    scenes_dir = orch.project_dir / "scenes" / plan.project_id
    scene_manifest = load_scene_manifest(scenes_dir, plan.project_id)
    orch._run_project_asset_gate(  # first pass OK; persists manifest
        plan=plan, scoped_scenes=list(plan.scenes),
        scoped_providers={s.id: IMAGE_MOTION_PROVIDER for s in plan.scenes},
        scene_manifest=scene_manifest,
        validate_video_file=validate_video_file,
        project_scenes_dir=scenes_dir,
    )
    (tmp_path / "assets" / "source" / "scene_002.jpg").write_bytes(b"junk")
    failures = asset_workflow.preflight_assets(
        tmp_path, list(plan.scenes),
        {s.id: {"provider": IMAGE_MOTION_PROVIDER} for s in plan.scenes},
        asset_workflow.load_asset_manifest(tmp_path),
    )
    assert any("scene_002" in f and "SHA-256" in f for f in failures)


def test_13_multiple_missing_scenes_reported_together(tmp_path, monkeypatch):
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid", explicit_assets={},
        sabotage_discovery=True,  # zero assets anywhere
    )
    with pytest.raises(Exception) as excinfo:
        orch._execute_scenes_stage()
    message = str(excinfo.value)
    assert "scene_001" in message and "scene_002" in message
    assert "Asset preflight failed" in message


def test_14_flow_routed_scene_exempt_from_preflight(tmp_path, monkeypatch):
    """Flow scene has NO assets anywhere; zero-cost sibling missing too.

    The preflight report must mention ONLY the zero-cost sibling — never the
    Flow-routed scene — proving exemption without weakening strictness."""
    import app.flow.scene_generator as flow_module

    class _Forbidden:
        def __init__(self, *a, **k):
            raise AssertionError("Flow reached during preflight failure")

    monkeypatch.setattr(flow_module, "FlowSceneGenerator", _Forbidden)
    monkeypatch.setattr(
        "app.orchestrator.visual_providers.GoogleFlowVisualProvider",
        _Forbidden,
    )
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid",
        routing={"scene_001": GOOGLE_FLOW_PROVIDER},
        explicit_assets={},      # both scenes lack assets
        sabotage_discovery=True,
    )
    with pytest.raises(Exception) as excinfo:
        orch._execute_scenes_stage()
    message = str(excinfo.value)
    assert "scene_002" in message          # zero-cost sibling reported
    # Flow-routed scene exempt & untouched ("flow" as a WORD never appears;
    # beware tmp-dir names embedding 'flow', hence matching google_flow only).
    assert "google_flow" not in message.lower()
    assert "Google Flow" not in message


def test_15_zero_cost_failures_never_construct_flow(tmp_path, monkeypatch):
    import app.flow.scene_generator as flow_module

    class _Forbidden:
        def __init__(self, *a, **k):
            raise AssertionError("Flow reached")

    monkeypatch.setattr(flow_module, "FlowSceneGenerator", _Forbidden)
    monkeypatch.setattr(
        "app.orchestrator.visual_providers.GoogleFlowVisualProvider",
        _Forbidden,
    )
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid", explicit_assets={},
        sabotage_discovery=True,  # zero assets anywhere
    )
    with pytest.raises(Exception) as excinfo:
        orch._execute_scenes_stage()
    # Failed at preflight — if either forbidden class had been constructed,
    # an AssertionError would have surfaced instead of this message.
    assert "No local image" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Resume determinism + true-source provenance (required tests 16 + 17)
# ---------------------------------------------------------------------------

def test_16_resume_uses_stored_copies_not_external_originals(
    tmp_path, monkeypatch
):
    """Two-round resume: the original external file is DELETED after intake;
    the second run must succeed purely from the project-owned stored copy."""
    img_dir = tmp_path / "empty_images"
    vid_dir = tmp_path / "empty_vids"
    img_dir.mkdir()
    vid_dir.mkdir()
    monkeypatch.setenv("AVM_IMAGE_ASSET_DIR", str(img_dir))
    monkeypatch.setenv("AVM_LOCAL_ASSET_DIR", str(vid_dir))

    # Round 1: only scene_001 mapped; scene_002 has nothing -> truthful fail.
    ext1 = tmp_path / "external" / "only_scene1.jpg"
    make_image(ext1)
    orch1 = _fresh_orchestrator(
        tmp_path, asset_mappings={"scene_001": str(ext1)}
    )
    with pytest.raises(Exception, match="No local image"):
        orch1._execute_scenes_stage()

    manifest_after_r1 = asset_workflow.load_asset_manifest(tmp_path)
    fingerprint_r1 = manifest_after_r1["assets"]["scene_001"]["sha256"]
    stored1 = tmp_path / "assets" / "source" / "scene_001.jpg"
    assert stored1.is_file()

    ext1.unlink()  # the original external source disappears forever

    # Round 2: supply ONLY what is still missing; scene_001 must resolve
    # from its stored project copy (the external path no longer exists).
    ext2 = tmp_path / "external" / "only_scene2.jpg"
    make_image(ext2)
    orch2 = _fresh_orchestrator(
        tmp_path, asset_mappings={"scene_002": str(ext2)}
    )
    orch2._execute_scenes_stage()
    assert len(orch2.manifest.scene_video_paths) == 2

    reloaded = asset_workflow.load_asset_manifest(tmp_path)
    assert reloaded["assets"]["scene_001"]["sha256"] == fingerprint_r1


def test_17_true_source_provenance_persisted(tmp_path, monkeypatch):
    """Provenance must name the project-owned SOURCE asset, not the MP4."""
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="hybrid",
        explicit_assets={"scene_001": "image", "scene_002": "image"},
        sabotage_discovery=True,  # nothing discoverable except stored copies
    )
    orch._execute_scenes_stage()

    raw = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    ck = raw["scene_checkpoints"]["scene_001"]
    normalized = ck["source_asset"].replace("\\", "/")
    assert "/assets/source/scene_001.jpg" in normalized
    assert ck["file_path"].endswith("scene_001.mp4")
    assert ck["file_path"] != ck["source_asset"]

    # Additive provenance also lands in the flow scene manifest evidence.
    scenes_dir = tmp_path / "scenes"
    sm_raw = json.loads(
        next(scenes_dir.rglob("scene_manifest.json")).read_text("utf-8")
    )
    scene_entry = next(
        s for s in sm_raw["scenes"] if s.get("scene_id") == "scene_001"
    )
    evidence = scene_entry.get("checkpoint_evidence", {})
    assert "/assets/source/" in evidence["source_asset"].replace("\\", "/")
    # Backward-compatible field still present.
    assert evidence["asset_reference"]


def test_17b_legacy_provenance_pins_discovered_file(tmp_path, monkeypatch):
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch, mode="local", explicit_assets={},
    )
    orch._execute_scenes_stage()
    raw = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    ck = raw["scene_checkpoints"]["scene_002"]
    assert "legacy_videos" in ck["source_asset"]


# ---------------------------------------------------------------------------
# Intake validation edge cases revealed by implementation review
# ---------------------------------------------------------------------------

def test_18_unknown_scene_mapping_rejected(tmp_path, monkeypatch):
    plan = _unit_plan(tmp_path)
    src = tmp_path / "external" / "garden.jpg"
    make_image(src)
    _, errors = asset_workflow.intake_assets(
        tmp_path, plan.scenes, {"scene_999": str(src)}, {}
    )
    assert any("unknown scene ID" in e for e in errors)


def test_19_mapping_flow_routed_scene_rejected(tmp_path, monkeypatch):
    plan = _unit_plan(tmp_path)
    src = tmp_path / "external" / "garden.jpg"
    make_image(src)
    _, errors = asset_workflow.intake_assets(
        tmp_path, plan.scenes, {"scene_001": str(src)},
        {"scene_001": GOOGLE_FLOW_PROVIDER},
    )
    assert any("google_flow" in e for e in errors)


def test_20_unsupported_extension_rejected(tmp_path, monkeypatch):
    plan = _unit_plan(tmp_path)
    src = tmp_path / "external" / "garden.docx"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"not media")
    _, errors = asset_workflow.intake_assets(
        tmp_path, plan.scenes, {"scene_001": str(src)},
        {"scene_001": IMAGE_MOTION_PROVIDER},
    )
    assert any("unsupported extension" in e for e in errors)


def test_21_media_type_mismatch_detected_in_preflight(tmp_path, monkeypatch):
    """An intaked VIDEO mapped against an image_motion-routed scene must be
    reported as a media-type mismatch, never silently consumed."""
    plan = _unit_plan(tmp_path)
    src = tmp_path / "external" / "clip.mp4"
    make_clip(src)
    asset_workflow.intake_assets(
        tmp_path, plan.scenes, {"scene_001": str(src)},
        {"scene_001": IMAGE_MOTION_PROVIDER},  # provenance says image_motion
    )
    failures = asset_workflow.preflight_assets(
        tmp_path, list(plan.scenes),
        {s.id: {"provider": IMAGE_MOTION_PROVIDER} for s in plan.scenes},
        asset_workflow.load_asset_manifest(tmp_path),
    )
    assert any("media type mismatch" in f and "scene_001" in f
               for f in failures)