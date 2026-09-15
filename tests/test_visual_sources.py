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
    visual_search_terms,
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
# Phase 11C Batch 1: Wikimedia Commons acquisition source
# ---------------------------------------------------------------------------

def test_factory_maps_wikimedia_to_commons_source(tmp_path):
    from app.orchestrator.visual_sources import WikimediaCommonsImageSource

    source = create_visual_source("wikimedia", output_dir=tmp_path)
    assert isinstance(source, WikimediaCommonsImageSource)
    assert source.is_available()
    assert source.name == "wikimedia"
    assert "no API key" in source.configuration_summary()


def test_commons_search_terms_come_from_visual_intent(tmp_path):
    from app.orchestrator.visual_sources import WikimediaCommonsImageSource

    ctx = VisualSourceContext(
        project_id="p1",
        scene_id="scene_01",
        sequence=1,
        visual_prompt=(
            "Cinematic aerial shot of vast dark ocean surface, deep blue "
            "water stretching to horizon, dramatic lighting, 4K quality"
        ),
        aspect_ratio="9:16",
        output_dir=tmp_path,
    )
    terms = WikimediaCommonsImageSource.search_terms(ctx)
    assert "ocean" in terms
    assert "blue" in terms
    # Style/camera vocabulary is stripped — it is not Commons search material.
    assert "cinematic" not in terms
    assert "aerial" not in terms
    assert "dramatic" not in terms
    assert all(term not in WikimediaCommonsImageSource._STOPWORDS for term in terms)


def test_commons_source_fails_truthfully_without_search_terms(tmp_path):
    from app.orchestrator.visual_sources import WikimediaCommonsImageSource

    ctx = VisualSourceContext(
        project_id="p1",
        scene_id="scene_01",
        sequence=1,
        visual_prompt="Cinematic vertical 4K shot, slow push-in, moody",
        aspect_ratio="9:16",
        output_dir=tmp_path,
    )
    source = WikimediaCommonsImageSource(output_dir=tmp_path)
    result = source.source(ctx)
    assert result.status == VisualSourceStatus.FAILURE
    assert "no usable search terms" in result.message
    assert result.artifact_path is None
    # No files were fabricated.
    assert not any(tmp_path.iterdir())


def test_commons_source_fails_truthfully_when_search_fails(tmp_path):
    from app.orchestrator.visual_sources import WikimediaCommonsImageSource

    ctx = VisualSourceContext(
        project_id="p1",
        scene_id="scene_01",
        sequence=1,
        visual_prompt="deep blue ocean surface at sunrise",
        aspect_ratio="9:16",
        output_dir=tmp_path,
    )

    def _boom(url, params):
        raise ConnectionError("network unavailable")

    source = WikimediaCommonsImageSource(output_dir=tmp_path, search_fn=_boom)
    result = source.source(ctx)
    assert result.status == VisualSourceStatus.FAILURE
    assert "search failed" in result.message
    assert "network unavailable" in result.message
    assert result.artifact_path is None


def test_commons_source_downloads_verified_image_with_provenance(tmp_path):
    from app.orchestrator.visual_sources import WikimediaCommonsImageSource

    ctx = VisualSourceContext(
        project_id="p1",
        scene_id="scene_01",
        sequence=1,
        visual_prompt="deep blue ocean surface at sunrise",
        aspect_ratio="9:16",
        output_dir=tmp_path / "autosrc",
    )
    payload = {
        "query": {
            "pages": {
                "1": {
                    "title": "File:Deep blue ocean at sunrise.jpg",
                    "imageinfo": [{
                        "url": "https://upload.wikimedia.org/ocean.jpg",
                        "thumburl": "https://upload.wikimedia.org/ocean_1600.jpg",
                        "width": 4000, "height": 3000, "mime": "image/jpeg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Ocean.jpg",
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC BY 4.0"},
                            "Artist": {"value": "<b>Example</b> Photographer"},
                        },
                    }],
                },
            },
        },
    }

    def _search(url, params):
        assert "filetype:bitmap" in params["gsrsearch"]
        assert "ocean" in params["gsrsearch"]
        return payload

    def _download(url, destination):
        # Produce a REAL, probe-able image exactly like an actual download.
        make_image(Path(destination))

    source = WikimediaCommonsImageSource(
        output_dir=tmp_path, search_fn=_search, download_fn=_download
    )
    result = source.source(ctx)

    assert result.status == VisualSourceStatus.SUCCESS
    assert result.artifact_path is not None and result.artifact_path.is_file()
    assert result.artifact_path.stat().st_size > 0
    assert result.metadata["kind"] == "stock"
    assert result.metadata["library"] == "wikimedia_commons"
    assert result.metadata["license"] == "CC BY 4.0"
    assert result.metadata["creator"] == "Example Photographer"
    assert result.metadata["source_url"] == "https://upload.wikimedia.org/ocean.jpg"
    assert result.metadata["search_query"].startswith("deep blue ocean")


def test_commons_source_prefers_tall_orientation_for_vertical(tmp_path):
    from app.orchestrator.visual_sources import WikimediaCommonsImageSource

    payload = {
        "query": {
            "pages": {
                "1": {
                    "title": "File:Wide waterfall landscape.jpg",
                    "imageinfo": [{
                        "url": "https://upload.wikimedia.org/landscape.jpg",
                        "thumburl": "https://upload.wikimedia.org/landscape_1600.jpg",
                        "width": 4000, "height": 3000, "mime": "image/jpeg",
                        "extmetadata": {},
                    }],
                },
                "2": {
                    "title": "File:Tall waterfall in a forest.jpg",
                    "imageinfo": [{
                        "url": "https://upload.wikimedia.org/portrait.jpg",
                        "thumburl": "https://upload.wikimedia.org/portrait_1600.jpg",
                        "width": 3000, "height": 4000, "mime": "image/jpeg",
                        "extmetadata": {},
                    }],
                },
            },
        },
    }
    ctx = VisualSourceContext(
        project_id="p1", scene_id="scene_01", sequence=1,
        visual_prompt="waterfall in a forest", aspect_ratio="9:16",
        output_dir=tmp_path,
    )
    source = WikimediaCommonsImageSource(
        output_dir=tmp_path,
        search_fn=lambda url, params: payload,
        download_fn=lambda url, dest: make_image(Path(dest)),
    )
    result = source.source(ctx)
    assert result.status == VisualSourceStatus.SUCCESS
    # The vertical (taller-than-wide) candidate wins for a 9:16 render.
    assert result.metadata["source_url"].endswith("portrait.jpg")


# ---------------------------------------------------------------------------
# Lightweight semantic-relevance gate (Batch 1 hardening)
# ---------------------------------------------------------------------------

def test_commons_rejects_technically_valid_but_unrelated_candidate(tmp_path):
    """The manual-test failure mode, deterministically: a high-resolution,
    technically valid candidate whose description shares NOTHING with the
    scene's search intent must be rejected — never accepted merely because
    the bytes are valid. Rejection must also survive the full relaxation
    ladder (truthful failure, no fabricated artifact)."""
    from app.orchestrator.visual_sources import WikimediaCommonsImageSource

    ctx = VisualSourceContext(
        project_id="p1", scene_id="scene_01", sequence=1,
        visual_prompt="deep blue ocean surface at sunrise",
        aspect_ratio="9:16", output_dir=tmp_path / "autosrc",
    )
    # A real-looking Commons response: huge, portrait, freely licensed —
    # but its title describes a construction vehicle, not the scene intent.
    payload = {
        "query": {
            "pages": {
                "1": {
                    "title": "File:Excavator on a construction site.jpg",
                    "imageinfo": [{
                        "url": "https://upload.wikimedia.org/excavator.jpg",
                        "thumburl": "https://upload.wikimedia.org/excavator_1600.jpg",
                        "width": 4016, "height": 6016, "mime": "image/jpeg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Excavator.jpg",
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC BY 2.0"},
                        },
                    }],
                },
            },
        },
    }
    calls = []

    def _search(url, params):
        calls.append(params["gsrsearch"])
        return payload

    source = WikimediaCommonsImageSource(
        output_dir=tmp_path, search_fn=_search,
        download_fn=lambda url, dest: make_image(Path(dest)),
    )
    result = source.source(ctx)

    assert result.status == VisualSourceStatus.FAILURE
    assert "no usable freely-licensed image" in result.message
    assert result.artifact_path is None
    # The relaxation ladder ran to its end (5-term down to 2-term queries).
    assert len(calls) == len(visual_search_terms(ctx)) - 1
    # Nothing was fabricated anywhere.
    autosrc = tmp_path / "autosrc"
    assert not autosrc.exists() or not any(autosrc.iterdir())

def test_commons_relaxation_recovers_with_relevant_shorter_query(tmp_path):
    """Rejection must keep searching: the full query yields only an
    unrelated (yet technically valid) candidate, but the relaxed shorter
    query finds a relevant one — the source must recover, not fail."""
    from app.orchestrator.visual_sources import WikimediaCommonsImageSource

    ctx = VisualSourceContext(
        project_id="p1", scene_id="scene_01", sequence=1,
        visual_prompt="weathered farmer portrait standing in his field",
        aspect_ratio="9:16", output_dir=tmp_path / "autosrc",
    )
    unrelated = {
        "query": {"pages": {"1": {
            "title": "File:Excavator on a construction site.jpg",
            "imageinfo": [{
                "url": "https://upload.wikimedia.org/excavator.jpg",
                "thumburl": "https://upload.wikimedia.org/excavator_1600.jpg",
                "width": 4016, "height": 6016, "mime": "image/jpeg",
                "extmetadata": {"LicenseShortName": {"value": "CC BY 2.0"}},
            }],
        }}},
    }
    relevant = {
        "query": {"pages": {"2": {
            "title": "File:Weathered farmer portrait.jpg",
            "imageinfo": [{
                "url": "https://upload.wikimedia.org/farmer.jpg",
                "thumburl": "https://upload.wikimedia.org/farmer_1600.jpg",
                "width": 3000, "height": 4500, "mime": "image/jpeg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Farmer.jpg",
                "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}},
            }],
        }}},
    }
    queries = []

    def _search(url, params):
        query = params["gsrsearch"].replace("filetype:bitmap", "").strip()
        queries.append(query)
        # While the query still carries three-plus terms the only candidate
        # is the unrelated excavator; the relaxed shorter query finds the
        # relevant farmer portrait.
        if len(query.split()) >= 3:
            return unrelated
        return relevant

    source = WikimediaCommonsImageSource(
        output_dir=tmp_path, search_fn=_search,
        download_fn=lambda url, dest: make_image(Path(dest)),
    )
    result = source.source(ctx)

    assert result.status == VisualSourceStatus.SUCCESS, result.message
    assert result.metadata["source_url"].endswith("farmer.jpg")
    assert result.metadata["relevance_score"] >= 1
    # The ladder actually ran: at least one rejected longer query preceded
    # the successful relaxed query.
    assert len(queries) >= 2


def test_commons_ranks_by_relevance_not_width(tmp_path):
    """The pre-fix behavior picked the WIDEST candidate. The gate must rank
    semantic relevance first and use width only as the final tie-break."""
    from app.orchestrator.visual_sources import WikimediaCommonsImageSource

    ctx = VisualSourceContext(
        project_id="p1", scene_id="scene_01", sequence=1,
        visual_prompt="elderly farmer portrait in his field",
        aspect_ratio="9:16", output_dir=tmp_path / "autosrc",
    )
    payload = {
        "query": {"pages": {
            # API-relevance order: the weaker match first.
            "1": {
                "title": "File:Old farmer.jpg",
                "imageinfo": [{
                    "url": "https://upload.wikimedia.org/wide.jpg",
                    "thumburl": "https://upload.wikimedia.org/wide_1600.jpg",
                    "width": 8000, "height": 9000, "mime": "image/jpeg",
                    "extmetadata": {},
                }],
            },
            "2": {
                "title": "File:Elderly farmer portrait in his field.jpg",
                "imageinfo": [{
                    "url": "https://upload.wikimedia.org/best.jpg",
                    "thumburl": "https://upload.wikimedia.org/best_1600.jpg",
                    "width": 1200, "height": 1800, "mime": "image/jpeg",
                    "extmetadata": {},
                }],
            },
        }},
    }
    source = WikimediaCommonsImageSource(
        output_dir=tmp_path,
        search_fn=lambda url, params: payload,
        download_fn=lambda url, dest: make_image(Path(dest)),
    )
    result = source.source(ctx)

    assert result.status == VisualSourceStatus.SUCCESS, result.message
    # The highest-overlap candidate wins even though the other candidate
    # is far wider and comes first in the API's own ordering.
    assert result.metadata["source_url"].endswith("best.jpg")
    assert result.metadata["relevance_score"] > 1


def test_commons_single_term_query_remains_usable(tmp_path):
    """A single-term query cannot demand overlap meaningfully — the gate is
    exempt there so the keyless fallback path is never starved."""
    from app.orchestrator.visual_sources import WikimediaCommonsImageSource

    ctx = VisualSourceContext(
        project_id="p1", scene_id="scene_01", sequence=1,
        visual_prompt="mountains", aspect_ratio="9:16",
        output_dir=tmp_path / "autosrc",
    )
    assert len(visual_search_terms(ctx)) == 1
    payload = {
        "query": {"pages": {"1": {
            "title": "File:Alpine scenery above the lake.jpg",
            "imageinfo": [{
                "url": "https://upload.wikimedia.org/alpine.jpg",
                "thumburl": "https://upload.wikimedia.org/alpine_1600.jpg",
                "width": 3000, "height": 4000, "mime": "image/jpeg",
                "extmetadata": {},
            }],
        }}},
    }
    source = WikimediaCommonsImageSource(
        output_dir=tmp_path,
        search_fn=lambda url, params: payload,
        download_fn=lambda url, dest: make_image(Path(dest)),
    )
    result = source.source(ctx)
    assert result.status == VisualSourceStatus.SUCCESS, result.message
    assert result.metadata["relevance_score"] == 0


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
    auto, notes, prov, results = orch._auto_source_missing_assets(
        plan=plan, pending=list(plan.scenes),
        scoped_providers={s.id: IMAGE_MOTION_PROVIDER for s in plan.scenes},
        manifest=asset_workflow.load_asset_manifest(tmp_path),
    )
    assert auto == {}
    assert prov is None
    assert results == {}


def test_auto_source_fixture_sources_missing_scenes_into_intake(tmp_path, monkeypatch):
    orch, _pid = _missing_orchestrator(tmp_path, monkeypatch, image_source="fixture")
    plan, _sm = _orch_plan(tmp_path)
    auto, notes, prov, results = orch._auto_source_missing_assets(
        plan=plan, pending=list(plan.scenes),
        scoped_providers={s.id: IMAGE_MOTION_PROVIDER for s in plan.scenes},
        manifest=asset_workflow.load_asset_manifest(tmp_path),
    )
    assert notes == []
    assert prov == "fixture"
    assert set(auto) == {s.id for s in plan.scenes}
    # Per-scene truthful results are returned for provenance recording.
    assert set(results) == set(auto)
    for sid, result in results.items():
        assert result.provider_id == "fixture"
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

    first, _, _, _ = orch._auto_source_missing_assets(
        plan=plan, pending=list(plan.scenes), scoped_providers=scoped, manifest=manifest)
    manifest, _ = asset_workflow.intake_assets(
        tmp_path, plan.scenes, first, scoped,
        provenance={sid: {"supplied_by": "auto_source"} for sid in first},
    )

    # Resume-style second pass finds existing entries -> no re-sourcing.
    second, _, _, _ = orch._auto_source_missing_assets(
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

    auto, _, _, _ = orch._auto_source_missing_assets(
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
    auto, notes, prov, results = orch._auto_source_missing_assets(
        plan=plan, pending=list(plan.scenes),
        scoped_providers={s.id: IMAGE_MOTION_PROVIDER for s in plan.scenes},
        manifest=asset_workflow.load_asset_manifest(tmp_path),
    )
    assert auto == {}
    assert prov == "dormant-provider"
    assert notes
    assert results == {}
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
    auto, notes, _, results = orch._auto_source_missing_assets(
        plan=plan, pending=list(plan.scenes), scoped_providers=scoped, manifest=manifest)
    assert auto == {}
    assert notes
    assert results == {}
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
