"""Phase 6F - automatic visual-source architecture tests.

Covers the pluggable visual-source abstraction, its deterministic fixture
source, the dorman external adapter boundary, and the Phase 6C integration
(intake -> project-owned copy -> SHA-256 -> preflight -> ImageMotion).

All tests are offline: no external image API is called, no Google Flow is
constructed, and no paid credits/quota are consumed.
"""

import json
from pathlib import Path

import pytest

import app.orchestrator.assets as asset_workflow
from app.orchestrator.visual_sources import (
    DormantExternalImageSource,
    FixtureImageSource,
    SourceAvailability,
    VisualSourceContext,
    VisualSourceResult,
    VisualSourceStatus,
    create_visual_source,
)
from app.orchestrator.visual_providers import (
    IMAGE_MOTION_PROVIDER,
)

from tests.test_asset_workflow import (
    build_orchestrator,
    load_plan_obj,
    make_image,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _missing_orchestrator(tmp_path, monkeypatch, *, image_source="fixture",
                          mode="hybrid", default_provider="image_motion"):
    """Fresh hybrid orchestrator whose scenes have NO assets anywhere, with
    an automatic visual source configured via AVM_IMAGE_SOURCE."""
    monkeypatch.setenv("AVM_IMAGE_SOURCE", image_source)
    return build_orchestrator(
        tmp_path, monkeypatch,
        mode=mode,
        default_provider=default_provider,
        explicit_assets={},
        sabotage_discovery=True,
    )


def _ctx(scene, output_dir, project_id="p1", aspect="9:16"):
    return VisualSourceContext(
        project_id=project_id,
        scene_id=scene.id,
        sequence=scene.sequence,
        visual_prompt=scene.visual_prompt,
        purpose=getattr(scene, "purpose", None),
        visual_style=getattr(scene, "visual_style", None),
        camera_direction=getattr(scene, "camera_direction", None),
        on_screen_text=getattr(scene, "on_screen_text", None),
        aspect_ratio=aspect,
        output_dir=output_dir,
    )


def _orch_plan(tmp_path):
    from app.flow.scene_generator import load_manifest as load_scene_manifest
    _, plan = load_plan_obj(tmp_path)
    scenes_dir = tmp_path / "scenes" / plan.project_id
    scene_manifest = load_scene_manifest(scenes_dir, plan.project_id)
    return plan, scene_manifest


# ---------------------------------------------------------------------------
# Factory / selection
# ---------------------------------------------------------------------------

def test_factory_returns_none_when_not_configured():
    assert create_visual_source("") is None
    assert create_visual_source(None) is None
    assert create_visual_source("   ") is None


def test_factory_maps_fixture_to_fixture_source(tmp_path):
    source = create_visual_source("fixture", output_dir=tmp_path)
    assert isinstance(source, FixtureImageSource)
    assert source.is_available()
    assert source.name == "fixture"


def test_factory_maps_unknown_selector_to_dormant_boundary():
    source = create_visual_source("my-image-provider")
    assert isinstance(source, DormantExternalImageSource)
    assert source.availability() == SourceAvailability.EXTERNAL_REQUIRED
    assert not source.is_available()

# ---------------------------------------------------------------------------
# Dormant external boundary (never makes real calls)
# ---------------------------------------------------------------------------

def test_dormant_source_never_generates_and_reports_external_required(tmp_path):
    source = DormantExternalImageSource("provider-x", output_dir=tmp_path)
    _, plan = load_plan_obj(tmp_path)
    result = source.source(_ctx(plan.scenes[0], tmp_path))
    assert result.status == VisualSourceStatus.WOULD_REQUIRE_EXTERNAL_GENERATION
    assert result.artifact_path is None
    # No artifact file was created.
    assert [p.name for p in tmp_path.iterdir()] == ["content"]


def test_dormant_source_reports_not_configured_when_unnamed(tmp_path):
    source = DormantExternalImageSource("", output_dir=tmp_path)
    assert source.availability() == SourceAvailability.NOT_CONFIGURED
    _, plan = load_plan_obj(tmp_path)
    result = source.source(_ctx(plan.scenes[0], tmp_path))
    assert result.status == VisualSourceStatus.REQUIRES_CONFIGURATION


# ---------------------------------------------------------------------------
# Fixture source
# ---------------------------------------------------------------------------

def test_fixture_source_produces_real_vertical_image(tmp_path):
    _, plan = load_plan_obj(tmp_path)
    out = tmp_path / "autosrc"
    result = FixtureImageSource().source(_ctx(plan.scenes[0], out, aspect="9:16"))
    assert result.status == VisualSourceStatus.SUCCESS
    assert result.artifact_path is not None
    assert result.artifact_path.exists()
    assert result.artifact_path.suffix.lower() == ".png"
    assert result.metadata.get("kind") == "fixture"
    assert result.provider_id == "fixture"


def test_fixture_source_is_deterministic(tmp_path):
    _, plan = load_plan_obj(tmp_path)
    out = tmp_path / "a"
    r1 = FixtureImageSource().source(_ctx(plan.scenes[0], out))
    r2 = FixtureImageSource().source(_ctx(plan.scenes[0], out))
    assert r1.artifact_path.read_bytes() == r2.artifact_path.read_bytes()


def test_fixture_never_masquerades_as_real_production(tmp_path):
    source = FixtureImageSource()
    summary = source.configuration_summary()
    assert "fixture" in summary and "NOT real AI-generated imagery" in summary


def test_source_context_carries_scene_visual_intent(tmp_path):
    _, plan = load_plan_obj(tmp_path)
    scene = plan.scenes[0]
    ctx = _ctx(scene, tmp_path)
    d = ctx.to_dict()
    assert d["visual_prompt"] == scene.visual_prompt
    assert d["scene_id"] == scene.id
    assert d["sequence"] == scene.sequence
    for field in ("purpose", "visual_style", "camera_direction", "on_screen_text"):
        assert field in d


# ---------------------------------------------------------------------------
# Phase 6C intake provenance (additive, backward compatible)
# ---------------------------------------------------------------------------

def test_intake_records_auto_source_provenance(tmp_path):
    _, plan = load_plan_obj(tmp_path)
    for scene in plan.scenes:
        make_image(tmp_path / "ext" / f"{scene.id}.jpg")
    mappings = {s.id: str(tmp_path / "ext" / f"{s.id}.jpg") for s in plan.scenes}
    provenance = {
        plan.scenes[0].id: {
            "supplied_by": "auto_source",
            "source_provider": "fixture",
            "source_status": "success",
        }
    }
    manifest, errors = asset_workflow.intake_assets(
        tmp_path, plan.scenes, mappings,
        {s.id: IMAGE_MOTION_PROVIDER for s in plan.scenes},
        provenance=provenance,
    )
    assert errors == []
    entry = manifest["assets"][plan.scenes[0].id]
    assert entry["supplied_by"] == "auto_source"
    assert entry["source_provider"] == "fixture"
    assert entry["source_status"] == "success"
    # The non-provenanced scene keeps the base schema (no phantom keys).
    other = manifest["assets"][plan.scenes[1].id]
    assert "media_type" in other and "sha256" in other
    assert "supplied_by" not in other


def test_intake_without_provenance_is_backward_compatible(tmp_path):
    _, plan = load_plan_obj(tmp_path)
    make_image(tmp_path / "ext" / f"{plan.scenes[0].id}.jpg")
    # Legacy call signature: no provenance arg.
    manifest, errors = asset_workflow.intake_assets(
        tmp_path, plan.scenes,
        {plan.scenes[0].id: str(tmp_path / "ext" / f"{plan.scenes[0].id}.jpg")},
        {plan.scenes[0].id: IMAGE_MOTION_PROVIDER},
    )
    assert errors == []
    assert "supplied_by" not in manifest["assets"][plan.scenes[0].id]

# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------

def test_auto_source_unconfigured_returns_nothing(tmp_path, monkeypatch):
    orch, _pid = _missing_orchestrator(tmp_path, monkeypatch, image_source="")
    plan, _sm = _orch_plan(tmp_path)
    auto, notes, prov = orch._auto_source_missing_assets(
        plan=plan, pending=list(plan.scenes),
        scoped_providers={s.id: IMAGE_MOTION_PROVIDER for s in plan.scenes},
        manifest=asset_workflow.load_asset_manifest(tmp_path),
    )
    assert auto == {}
    assert prov is None


def test_auto_source_fixture_sources_missing_scenes_into_intake(tmp_path, monkeypatch):
    orch, _pid = _missing_orchestrator(tmp_path, monkeypatch, image_source="fixture")
    plan, _sm = _orch_plan(tmp_path)
    auto, notes, prov = orch._auto_source_missing_assets(
        plan=plan, pending=list(plan.scenes),
        scoped_providers={s.id: IMAGE_MOTION_PROVIDER for s in plan.scenes},
        manifest=asset_workflow.load_asset_manifest(tmp_path),
    )
    assert notes == []
    assert prov == "fixture"
    assert set(auto) == {s.id for s in plan.scenes}
    for sid, path in auto.items():
        assert Path(path).exists()
        assert Path(path).suffix == ".png"

    # Push through Phase 6C intake and verify project ownership + SHA-256.
    manifest, errors = asset_workflow.intake_assets(
        tmp_path, plan.scenes, auto,
        {s.id: IMAGE_MOTION_PROVIDER for s in plan.scenes},
        provenance={sid: {"supplied_by": "auto_source",
                          "source_provider": "fixture",
                          "source_status": "success"} for sid in auto},
    )
    assert errors == []
    for sid in auto:
        entry = manifest["assets"][sid]
        assert entry["stored_as"].startswith("assets/source/")
        assert entry["media_type"] == "image"
        assert entry["supplied_by"] == "auto_source"
        assert entry["sha256"]
        stored = tmp_path / entry["stored_as"]
        assert stored.exists()
        assert asset_workflow.sha256_file(stored) == entry["sha256"]
        assert asset_workflow.verify_stored_asset(tmp_path, entry) is None


def test_auto_source_resume_does_not_resource_existing_assets(tmp_path, monkeypatch):
    orch, _pid = _missing_orchestrator(tmp_path, monkeypatch, image_source="fixture")
    plan, _sm = _orch_plan(tmp_path)
    scoped = {s.id: IMAGE_MOTION_PROVIDER for s in plan.scenes}
    manifest = asset_workflow.load_asset_manifest(tmp_path)

    first, _, _ = orch._auto_source_missing_assets(
        plan=plan, pending=list(plan.scenes), scoped_providers=scoped, manifest=manifest)
    manifest, _ = asset_workflow.intake_assets(
        tmp_path, plan.scenes, first, scoped,
        provenance={sid: {"supplied_by": "auto_source"} for sid in first},
    )

    # Resume-style second pass finds existing entries -> no re-sourcing.
    second, _, _ = orch._auto_source_missing_assets(
        plan=plan, pending=list(plan.scenes), scoped_providers=scoped, manifest=manifest)
    assert second == {}


def test_auto_source_existing_asset_takes_precedence(tmp_path, monkeypatch):
    orch, _pid = _missing_orchestrator(tmp_path, monkeypatch, image_source="fixture")
    plan, _sm = _orch_plan(tmp_path)
    scoped = {s.id: IMAGE_MOTION_PROVIDER for s in plan.scenes}

    ext = tmp_path / "ext"
    ext.mkdir(exist_ok=True)
    make_image(ext / f"{plan.scenes[0].id}.jpg")
    manifest, _ = asset_workflow.intake_assets(
        tmp_path, plan.scenes,
        {plan.scenes[0].id: str(ext / f"{plan.scenes[0].id}.jpg")},
        scoped, provenance={plan.scenes[0].id: {"supplied_by": "user"}},
    )

    auto, _, _ = orch._auto_source_missing_assets(
        plan=plan, pending=list(plan.scenes), scoped_providers=scoped, manifest=manifest)
    assert plan.scenes[0].id not in auto
    assert plan.scenes[1].id in auto



def test_auto_source_dormant_fails_honestly_without_flow(tmp_path, monkeypatch):
    # A dormant external source cannot produce an image; the pipeline must
    # report the failure honestly and never touch Google Flow.
    import app.orchestrator.visual_providers as vp
    import app.flow.scene_generator as flow_module

    class _Forbidden:
        def __init__(self, *a, **k):
            raise AssertionError("Flow constructed after source failure")

    monkeypatch.setattr(flow_module, "FlowSceneGenerator", _Forbidden)
    monkeypatch.setattr(vp, "GoogleFlowVisualProvider", _Forbidden)

    orch, _pid = _missing_orchestrator(
        tmp_path, monkeypatch, image_source="dormant-provider")
    plan, _sm = _orch_plan(tmp_path)
    auto, notes, prov = orch._auto_source_missing_assets(
        plan=plan, pending=list(plan.scenes),
        scoped_providers={s.id: IMAGE_MOTION_PROVIDER for s in plan.scenes},
        manifest=asset_workflow.load_asset_manifest(tmp_path),
    )
    assert auto == {}
    assert prov == "dormant-provider"
    assert notes
    assert not (tmp_path / "assets" / "source").exists()


def test_auto_source_failure_never_creates_fake_assets(tmp_path, monkeypatch):
    orch, _pid = _missing_orchestrator(tmp_path, monkeypatch, image_source="fixture")
    plan, _sm = _orch_plan(tmp_path)
    scoped = {s.id: IMAGE_MOTION_PROVIDER for s in plan.scenes}
    manifest = asset_workflow.load_asset_manifest(tmp_path)

    class _Failing:
        name = "broken-source"
        def is_available(self):
            return True
        def availability(self):
            return SourceAvailability.AVAILABLE
        def source(self, ctx):
            return VisualSourceResult.failure(
                ctx.scene_id, self.name, "simulated failure")
        def configuration_summary(self):
            return "broken"

    import app.orchestrator.visual_sources as visual_sources_mod
    monkeypatch.setattr(
        visual_sources_mod, "create_visual_source",
        lambda selector, output_dir=None: _Failing(),
    )
    auto, notes, _ = orch._auto_source_missing_assets(
        plan=plan, pending=list(plan.scenes), scoped_providers=scoped, manifest=manifest)
    assert auto == {}
    assert notes
    assert not (tmp_path / "assets" / "source").exists()


def test_fixture_end_to_end_scenes_stage(tmp_path, monkeypatch):
    """Fixture source -> intake -> preflight -> ImageMotion -> valid vertical
    scene MP4s, all offline (ffmpeg only)."""
    orch, _pid = _missing_orchestrator(tmp_path, monkeypatch, image_source="fixture")
    orch._execute_scenes_stage()
    assert len(orch.manifest.scene_video_paths) == 2
    for path in orch.manifest.scene_video_paths:
        assert Path(path).exists()
    # Manifest entry carries durable auto-source provenance.
    amf = tmp_path / "assets" / "asset_manifest.json"
    manifest = json.loads(amf.read_text("utf-8"))
    for sid in manifest["assets"]:
        assert manifest["assets"][sid]["supplied_by"] == "auto_source"
        assert manifest["assets"][sid]["source_provider"] == "fixture"
