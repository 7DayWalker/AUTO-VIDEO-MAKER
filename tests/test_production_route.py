"""Batch 1D — first-class Production Route foundation.

Hermetic coverage for:

- the Route domain model (validation, persistence, reload);
- the centralized route registry (product vocabulary only — no
  provider/model names in route identities);
- the centralized legacy ``generation_strategy`` compatibility mapping;
- route resolution precedence (explicit route > legacy strategy > default);
- route compilation at the ProductionExecutor seam, proven EQUIVALENT to the
  verified Batch 1C generation-strategy bridge for all three legacy paths;
- mock-mode hermeticity and server creation wiring.

No network requests; no provider calls; persistence uses tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.content.models import (
    ContentBrief,
    ProductionMetadata,
    ProductionPlan,
    Scene,
)
from app.orchestrator.models import OrchestratorConfig
from app.orchestrator.production_execution import (
    ProductionExecutor,
    UnitPlanArtifacts,
    apply_generation_strategy,
    apply_production_route,
)
from app.production.models import (
    GENERATION_STRATEGIES,
    Production,
    ProductionCommand,
    ProductionUnit,
)
from app.production.routes import (
    DEFAULT_PRODUCTION_ROUTE,
    EXECUTION_SURFACE_STANDARD_VIDEO,
    KNOWN_ROUTES,
    LEGACY_GENERATION_STRATEGY_ROUTES,
    PRIVATE_ONLY_ROUTES,
    PRODUCTION_ROUTES,
    ROUTE_FULL_AI_VIDEO,
    ROUTE_GOOGLE_FLOW,
    ROUTE_LOCAL_AI_ASSISTED,
    ROUTE_REGISTRY,
    assert_saas_production_route,
    get_route_definition,
    resolve_effective_route,
    route_id_for_legacy_strategy,
)
from app.production.storage import (
    _serialize,
    load_production,
    save_new_production,
    save_revision,
)
from app.web import server as web


CREATED = "2026-09-14T09:00:00+00:00"
PID = "prd_20260914_route_001"


def make_production(
    *, strategy: str | None = None, route: str | None = None
) -> Production:
    values = dict(
        production_id=PID,
        title="Route foundation production",
        command=ProductionCommand(
            text="Create one video.",
            workload_shape="single_video",
            intended_unit_count=1,
            issued_at=CREATED,
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
        created_at=CREATED,
    )
    if strategy is not None:
        values["generation_strategy"] = strategy
    if route is not None:
        values["route"] = route
    return Production(**values)


def make_plan(*, scenes: int = 2) -> ProductionPlan:
    duration = 5
    return ProductionPlan(
        project_id="20260914_route_plan_001",
        title="Route plan",
        hook="Hook",
        script=" ".join(f"Narration {i}" for i in range(1, scenes + 1)),
        call_to_action=None,
        estimated_duration=scenes * duration,
        scenes=[
            Scene(
                id=f"scene_{i:02d}",
                sequence=i,
                narration=f"Narration {i}",
                visual_prompt=f"Visual {i}",
                estimated_duration=duration,
                purpose="hook" if i == 1 else "conclusion",
            )
            for i in range(1, scenes + 1)
        ],
        metadata=ProductionMetadata(
            platform="youtube_shorts",
            language="en",
            niche="education",
            target_audience="general audience",
            content_goal="education",
            style="documentary",
            requires_fact_check=True,
        ),
    )


def baseline_config() -> OrchestratorConfig:
    """The exact base configuration the web local path builds today."""
    return OrchestratorConfig(
        topic="Route production",
        mock_mode=False,
        visual_mode="local",
        visual_default_provider="image_motion",
        image_source="auto",
    )


def snapshot(config: OrchestratorConfig) -> dict:
    """The route-relevant execution concerns (the bridge's full surface)."""
    return {
        "visual_mode": config.visual_mode,
        "visual_default_provider": config.visual_default_provider,
        "visual_scene_routing": dict(config.visual_scene_routing),
        "image_source": config.image_source,
    }


def compile_route(production: Production, plan: ProductionPlan, config):
    """Resolve + compile the route exactly as the executor seam does."""
    effective = resolve_effective_route(
        route=production.route,
        generation_strategy=production.generation_strategy,
    )
    return effective, apply_production_route(
        config, effective_route=effective, plan=plan
    )


# ---------------------------------------------------------------------------
# A) Route domain model validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", PRODUCTION_ROUTES)
def test_production_route_field_accepts_registered_routes(route):
    production = make_production(route=route)
    assert production.route == route


def test_production_route_rejects_unknown_value_loudly():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="route"):
        make_production(route="alibaba_wan_video")


def test_route_defaults_to_none_for_legacy_artifacts():
    """A Production constructed without a route stays legacy-resolvable."""
    assert make_production().route is None


# ---------------------------------------------------------------------------
# B) Route registry (product vocabulary; separation invariants)
# ---------------------------------------------------------------------------


def test_route_registry_defines_expected_product_routes():
    """SaaS surface = the two commercial routes; google_flow is private-only."""
    assert PRODUCTION_ROUTES == (
        "local_ai_assisted",
        "full_ai_video",
    )
    assert "google_flow" not in PRODUCTION_ROUTES
    assert PRIVATE_ONLY_ROUTES == ("google_flow",)
    assert KNOWN_ROUTES == PRODUCTION_ROUTES + PRIVATE_ONLY_ROUTES
    assert set(ROUTE_REGISTRY) == set(KNOWN_ROUTES)
    assert ROUTE_REGISTRY[ROUTE_LOCAL_AI_ASSISTED].label == "Local AI-Assisted"
    assert ROUTE_REGISTRY[ROUTE_FULL_AI_VIDEO].label == "Full AI Video"
    assert ROUTE_REGISTRY[ROUTE_GOOGLE_FLOW].label == "Google Flow"
    for definition in ROUTE_REGISTRY.values():
        assert definition.execution_surface == EXECUTION_SURFACE_STANDARD_VIDEO
        assert definition.route_version >= 1


def test_route_vocabulary_is_product_level_not_provider_level():
    """Route identity must never embed provider/model/engine/media names."""
    forbidden_tokens = (
        "alibaba",
        "wan",
        "veo",
        "seedance",
        "kling",
        "openrouter",
        "qwen",
        "comfyui",
        "imagemotion",
        "image_motion",
        "ffmpeg",
        "t2v",
        "t2i",
    )
    for definition in ROUTE_REGISTRY.values():
        haystack = (definition.route_id + " " + definition.label).lower()
        for token in forbidden_tokens:
            assert token not in haystack, (
                f"route {definition.route_id!r} must not embed {token!r}"
            )


def test_route_registry_separates_engine_and_media_concerns():
    """Requirement flags stay descriptive; no engine/provider identities."""
    full = ROUTE_REGISTRY[ROUTE_FULL_AI_VIDEO]
    local = ROUTE_REGISTRY[ROUTE_LOCAL_AI_ASSISTED]
    flow = ROUTE_REGISTRY[ROUTE_GOOGLE_FLOW]
    assert full.requires_native_motion is True
    assert full.forbids_still_image_substitution is True
    assert full.allows_local_motion_engine is False
    assert local.requires_native_motion is False
    assert local.allows_local_motion_engine is True
    assert flow.requires_native_motion is True
    assert flow.forbids_still_image_substitution is True
    # The registry carries no provider/model/credential/media/audio data.
    for definition in ROUTE_REGISTRY.values():
        payload = definition.model_dump()
        for key in (
            "provider",
            "model",
            "api_key",
            "image_source",
            "audio_policy",
            "assembly",
            "publishing",
        ):
            assert key not in payload


def test_unknown_route_fails_loudly_at_registry():
    with pytest.raises(ValueError, match="route must be one of"):
        get_route_definition("alibaba_wan_video")


# ---------------------------------------------------------------------------
# C) Legacy strategy compatibility mapping (centralized, explicit)
# ---------------------------------------------------------------------------


def test_legacy_strategy_mapping_is_centralized_and_explicit():
    assert set(LEGACY_GENERATION_STRATEGY_ROUTES) == set(GENERATION_STRATEGIES)
    local = LEGACY_GENERATION_STRATEGY_ROUTES["local"]
    image = LEGACY_GENERATION_STRATEGY_ROUTES["alibaba_image_local_video"]
    video = LEGACY_GENERATION_STRATEGY_ROUTES["alibaba_video"]
    # local -> the default local route, no overlays (verified no-op path).
    assert local.route_id == ROUTE_LOCAL_AI_ASSISTED
    assert local.execution_overlays == {}
    # alibaba_image_local_video -> the SAME product route, with the exact
    # legacy media-strategy overlay preserved (explicit Alibaba images).
    assert image.route_id == ROUTE_LOCAL_AI_ASSISTED
    assert image.execution_overlays == {"image_source": "alibaba"}
    # alibaba_video -> Full AI Video (no provider name in the route).
    assert video.route_id == ROUTE_FULL_AI_VIDEO
    assert video.execution_overlays == {}


def test_route_id_for_legacy_strategy_maps_and_fails_loudly():
    assert route_id_for_legacy_strategy("local") == ROUTE_LOCAL_AI_ASSISTED
    assert (
        route_id_for_legacy_strategy("alibaba_image_local_video")
        == ROUTE_LOCAL_AI_ASSISTED
    )
    assert route_id_for_legacy_strategy("alibaba_video") == ROUTE_FULL_AI_VIDEO
    with pytest.raises(ValueError, match="unknown generation strategy"):
        route_id_for_legacy_strategy("comfyui")


# ---------------------------------------------------------------------------
# D) Resolution: derivation, precedence, defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("strategy", "expected_route"),
    [
        ("local", ROUTE_LOCAL_AI_ASSISTED),
        ("alibaba_image_local_video", ROUTE_LOCAL_AI_ASSISTED),
        ("alibaba_video", ROUTE_FULL_AI_VIDEO),
    ],
)
def test_legacy_strategy_derives_route(strategy, expected_route):
    effective = resolve_effective_route(
        route=None, generation_strategy=strategy
    )
    assert effective.route_id == expected_route
    assert effective.source == "legacy_strategy"
    assert effective.legacy_strategy == strategy


def test_default_resolution_is_the_local_route():
    effective = resolve_effective_route(route=None, generation_strategy=None)
    assert effective.route_id == DEFAULT_PRODUCTION_ROUTE
    assert effective.route_id == ROUTE_LOCAL_AI_ASSISTED
    assert effective.source == "legacy_strategy"


def test_explicit_route_takes_precedence_over_legacy_strategy():
    effective = resolve_effective_route(
        route=ROUTE_FULL_AI_VIDEO, generation_strategy="local"
    )
    assert effective.route_id == ROUTE_FULL_AI_VIDEO
    assert effective.source == "explicit_route"
    # The legacy strategy's overlays must never fight a different explicit
    # route choice.
    assert effective.compat_overlays == {}


def test_unknown_legacy_strategy_fails_loudly_never_rerouted():
    with pytest.raises(ValueError, match="unknown generation strategy"):
        resolve_effective_route(route=None, generation_strategy="comfyui")


# ---------------------------------------------------------------------------
# H) Route compilation into the existing execution configuration
# ---------------------------------------------------------------------------


def test_compile_local_ai_assisted_preserves_existing_local_path():
    config = baseline_config()
    before = snapshot(config)
    effective, compilation = compile_route(
        make_production(route=ROUTE_LOCAL_AI_ASSISTED), make_plan(), config
    )
    # Byte-for-byte: the verified local path is untouched.
    assert snapshot(config) == before
    assert effective.source == "explicit_route"
    assert compilation.route_id == ROUTE_LOCAL_AI_ASSISTED
    assert compilation.label == "Local AI-Assisted"
    assert compilation.execution_surface == EXECUTION_SURFACE_STANDARD_VIDEO
    assert compilation.route_version == 1
    assert compilation.requires_native_motion is False
    assert compilation.overlays_applied == ()


def test_compile_full_ai_video_routes_every_scene_to_video_provider():
    plan = make_plan(scenes=3)
    config = baseline_config()
    _, compilation = compile_route(
        make_production(route=ROUTE_FULL_AI_VIDEO), plan, config
    )
    assert config.visual_mode == "hybrid"
    assert config.visual_default_provider == "alibaba_video"
    assert config.visual_scene_routing == {
        scene.id: "alibaba_video" for scene in plan.scenes
    }
    assert compilation.route_id == ROUTE_FULL_AI_VIDEO
    assert compilation.requires_native_motion is True
    assert compilation.overlays_applied == ()


def test_compile_google_flow_selects_existing_flow_surface():
    config = baseline_config()
    _, compilation = compile_route(
        make_production(route=ROUTE_GOOGLE_FLOW), make_plan(), config
    )
    assert config.visual_mode == "google_flow"
    assert compilation.route_id == ROUTE_GOOGLE_FLOW
    assert compilation.execution_surface == EXECUTION_SURFACE_STANDARD_VIDEO


def test_executor_google_flow_route_requires_flow_project_url(tmp_path):
    """Existing truthful Flow validation stays authoritative (no redesign)."""
    production = make_production(route=ROUTE_GOOGLE_FLOW)
    config = baseline_config()
    config.production_id = production.production_id
    config.unit_id = "unit-001"
    executor = ProductionExecutor(
        PID, productions_root=tmp_path, base_config=config
    )
    with pytest.raises(ValueError, match="Flow project URL"):
        executor._config_for_unit(
            production,
            production.units[0],
            _make_artifacts(make_plan()),
            bound=None,
        )


# ---------------------------------------------------------------------------
# I) BEHAVIOR PRESERVATION: compiled route == verified Batch 1C bridge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "strategy",
    ["local", "alibaba_image_local_video", "alibaba_video"],
)
def test_compiled_route_matches_generation_strategy_bridge(strategy):
    """The migration must be behavior-preserving for all verified paths."""
    plan = make_plan()
    legacy_production = make_production(strategy=strategy)
    mapped_route = route_id_for_legacy_strategy(strategy)
    # Server-created shape: BOTH the legacy strategy and the derived route.
    server_shape = make_production(strategy=strategy, route=mapped_route)
    # Pre-route shape: ONLY the legacy strategy (existing productions).
    legacy_shape = make_production(strategy=strategy)

    bridge_config = baseline_config()
    apply_generation_strategy(legacy_production, bridge_config, plan)
    bridge = snapshot(bridge_config)

    server_config = baseline_config()
    _, server_compilation = compile_route(server_shape, plan, server_config)
    assert snapshot(server_config) == bridge

    legacy_config = baseline_config()
    _, legacy_compilation = compile_route(legacy_shape, plan, legacy_config)
    assert snapshot(legacy_config) == bridge

    # Compilation evidence stays coherent in both shapes.
    for compilation in (server_compilation, legacy_compilation):
        assert compilation.route_id == mapped_route
        assert compilation.route_version == 1
        assert compilation.execution_surface == EXECUTION_SURFACE_STANDARD_VIDEO
    expected_overlays = (
        ("image_source",)
        if strategy == "alibaba_image_local_video"
        else ()
    )
    assert legacy_compilation.overlays_applied == expected_overlays


def test_legacy_image_overlay_is_media_strategy_not_route_identity():
    """The overlay reproduces the exact bridge mutation, nothing more."""
    config = baseline_config()
    effective = resolve_effective_route(
        route=None, generation_strategy="alibaba_image_local_video"
    )
    assert effective.compat_overlays == {"image_source": "alibaba"}
    apply_production_route(
        config, effective_route=effective, plan=make_plan()
    )
    assert config.image_source == "alibaba"
    # Everything else stays exactly on the local path.
    assert config.visual_mode == "local"
    assert config.visual_default_provider == "image_motion"
    assert config.visual_scene_routing == {}


def _make_artifacts(plan) -> UnitPlanArtifacts:
    return UnitPlanArtifacts(
        plan=plan,
        brief=ContentBrief(topic="Route production"),
        plan_path=Path("plan.json"),
        content_hash="0" * 64,
        production_plan_version=1,
    )


# ---------------------------------------------------------------------------
# N) Precedence at the executor seam (route beats legacy strategy)
# ---------------------------------------------------------------------------


def test_executor_route_precedence_over_legacy_strategy(tmp_path):
    """An explicit route wins even when a legacy strategy disagrees."""
    production = make_production(
        strategy="alibaba_video", route=ROUTE_LOCAL_AI_ASSISTED
    )
    config = baseline_config()
    config.production_id = production.production_id
    config.unit_id = "unit-001"
    executor = ProductionExecutor(
        PID, productions_root=tmp_path, base_config=config
    )
    applied = executor._config_for_unit(
        production,
        production.units[0],
        _make_artifacts(make_plan()),
        bound=None,
    )
    # Full AI Video's routing must NOT appear: the explicit local route won.
    assert applied.visual_mode == "local"
    assert applied.visual_default_provider == "image_motion"
    assert applied.visual_scene_routing == {}
    assert applied.image_source == "auto"


# ---------------------------------------------------------------------------
# J) Mock/hermetic mode stays fully inert
# ---------------------------------------------------------------------------


def test_route_compilation_inert_in_mock_mode(tmp_path):
    """The explicit offline/mock contract stays fully hermetic."""
    production = make_production(
        strategy="alibaba_video", route=ROUTE_FULL_AI_VIDEO
    )
    plan = make_plan()
    config = baseline_config()
    config.mock_mode = True
    config.image_source = "fixture"
    config.production_id = production.production_id
    config.unit_id = "unit-001"
    executor = ProductionExecutor(
        PID, productions_root=tmp_path, base_config=config
    )
    applied = executor._config_for_unit(
        production, production.units[0], _make_artifacts(plan), bound=None
    )
    assert applied.image_source == "fixture"
    assert applied.visual_mode == "local"
    assert applied.visual_default_provider == "image_motion"
    assert applied.visual_scene_routing == {}


# ---------------------------------------------------------------------------
# L/M) Persistence, reload, and legacy artifact compatibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", PRODUCTION_ROUTES)
def test_route_persists_and_survives_reload(tmp_path, route):
    root = tmp_path / "productions"
    save_new_production(make_production(route=route), root=root)

    first = load_production(PID, root=root)
    assert first.route == route
    second = load_production(PID, root=root)
    assert second.route == route
    raw = json.loads(
        (root / PID / "production.json").read_text(encoding="utf-8")
    )
    assert raw["route"] == route


def test_legacy_production_without_route_loads_and_resolves(tmp_path):
    """Pre-1D artifacts (no route field) keep loading and keep behaving."""
    root = tmp_path / "productions"
    save_new_production(make_production(strategy="alibaba_video"), root=root)
    path = root / PID / "production.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("route", None)  # simulate a pre-route artifact
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_production(PID, root=root)
    assert loaded.route is None
    assert loaded.generation_strategy == "alibaba_video"

    config = baseline_config()
    _, compilation = compile_route(loaded, make_plan(), config)
    assert config.visual_mode == "hybrid"
    assert config.visual_default_provider == "alibaba_video"
    assert compilation.route_id == ROUTE_FULL_AI_VIDEO
    assert compilation.source == "legacy_strategy"


def test_artifact_without_route_and_strategy_resolves_to_local_route(tmp_path):
    root = tmp_path / "productions"
    save_new_production(make_production(), root=root)
    path = root / PID / "production.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("route", None)
    raw.pop("generation_strategy", None)
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_production(PID, root=root)
    assert loaded.route is None
    config = baseline_config()
    before = snapshot(config)
    _, compilation = compile_route(loaded, make_plan(), config)
    assert snapshot(config) == before
    assert compilation.route_id == ROUTE_LOCAL_AI_ASSISTED


def test_route_field_survives_validation_scans(tmp_path):
    """The intent artifact stays valid (no execution-state key collision)."""
    from app.production.validation import assert_valid_production

    production = make_production(route=ROUTE_FULL_AI_VIDEO)
    assert_valid_production(production)


# ---------------------------------------------------------------------------
# Server truth: creation derives/persists/exposes the route
# ---------------------------------------------------------------------------


def _creation_request(strategy=None, route=None):
    body = {
        "mode": "single_video",
        "creation_method": "local",
        "title": "Route server test",
        "brief": "A route wiring validation production.",
        "output_intent": "youtube_shorts",
        "target_duration": 30,
        "scene_count": 6,
        "tone": "clear and cinematic",
    }
    if strategy is not None:
        body["generation_strategy"] = strategy
    if route is not None:
        body["route"] = route
    return body


@pytest.mark.parametrize(
    ("strategy", "expected_route"),
    [
        ("local", ROUTE_LOCAL_AI_ASSISTED),
        ("alibaba_image_local_video", ROUTE_LOCAL_AI_ASSISTED),
        ("alibaba_video", ROUTE_FULL_AI_VIDEO),
    ],
)
def test_server_creation_persists_route_derived_from_legacy_strategy(
    tmp_path, monkeypatch, strategy, expected_route
):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    status, payload = web.create_production_payload(
        _creation_request(strategy=strategy)
    )
    assert status == 201
    # The UI contract is untouched; the first-class route is persisted too.
    assert payload["generation_strategy"] == strategy
    assert payload["route"] == expected_route
    loaded = load_production(
        payload["production_id"], root=tmp_path / "productions"
    )
    assert loaded.generation_strategy == strategy
    assert loaded.route == expected_route

    detail_status, detail = web.production_detail_payload(
        payload["production_id"]
    )
    assert detail_status == 200
    assert detail["route"] == expected_route


def test_server_creation_accepts_explicit_route(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    status, payload = web.create_production_payload(
        _creation_request(route=ROUTE_FULL_AI_VIDEO)
    )
    assert status == 201
    assert payload["route"] == ROUTE_FULL_AI_VIDEO
    assert payload["generation_strategy"] == "local"
    loaded = load_production(
        payload["production_id"], root=tmp_path / "productions"
    )
    assert loaded.route == ROUTE_FULL_AI_VIDEO


def test_server_creation_rejects_unknown_route(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    status, payload = web.create_production_payload(
        _creation_request(route="alibaba_wan_video")
    )
    assert status == 400
    assert payload["error"] == "INVALID_CREATION_REQUEST"
    assert "route" in payload["message"]


# ---------------------------------------------------------------------------
# N) SaaS / private route boundary (route-boundary correction)
# ---------------------------------------------------------------------------


def test_saas_route_surface_excludes_google_flow():
    """A. local_ai_assisted and full_ai_video are the SaaS surface;
    C. google_flow is a private-only capability, never a SaaS route."""
    assert PRODUCTION_ROUTES == (ROUTE_LOCAL_AI_ASSISTED, ROUTE_FULL_AI_VIDEO)
    assert ROUTE_GOOGLE_FLOW not in PRODUCTION_ROUTES
    assert PRIVATE_ONLY_ROUTES == (ROUTE_GOOGLE_FLOW,)
    assert KNOWN_ROUTES == PRODUCTION_ROUTES + PRIVATE_ONLY_ROUTES


def test_saas_routes_remain_selectable_for_new_productions(tmp_path):
    """A+B. Every SaaS route passes the creation gate and persists."""
    for route in PRODUCTION_ROUTES:
        assert_saas_production_route(route)
        root = tmp_path / f"productions_{route}"
        save_new_production(make_production(route=route), root=root)
        assert load_production(PID, root=root).route == route


def test_google_flow_is_never_a_valid_saas_product_route(tmp_path):
    """C. The creation gate refuses google_flow at the durable creation seam
    (the HTTP creation seam is covered by the dedicated server test)."""
    with pytest.raises(ValueError, match="private-only capability"):
        assert_saas_production_route(ROUTE_GOOGLE_FLOW)
    with pytest.raises(ValueError, match="private-only capability"):
        save_new_production(
            make_production(route=ROUTE_GOOGLE_FLOW), root=tmp_path / "p"
        )


def test_saas_server_creation_rejects_google_flow_route(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    status, payload = web.create_production_payload(
        _creation_request(route=ROUTE_GOOGLE_FLOW)
    )
    assert status == 400
    assert payload["error"] == "INVALID_CREATION_REQUEST"
    assert "route" in payload["message"]


def test_private_google_flow_artifact_remains_loadable_and_compilable(tmp_path):
    """D. Existing/private google_flow artifacts keep loading and compiling.

    The artifact is written directly (as a pre-boundary private production
    would exist on disk); persistence of a NEW google_flow production is
    refused, but loading, model validation, and route compilation are not.
    """
    root = tmp_path / "productions"
    directory = root / PID
    directory.mkdir(parents=True)
    (directory / "production.json").write_text(
        _serialize(make_production(route=ROUTE_GOOGLE_FLOW)), encoding="utf-8"
    )

    loaded = load_production(PID, root=root)
    assert loaded.route == ROUTE_GOOGLE_FLOW

    config = baseline_config()
    effective = resolve_effective_route(
        route=ROUTE_GOOGLE_FLOW, generation_strategy="local"
    )
    apply_production_route(config, effective_route=effective, plan=make_plan())
    assert config.visual_mode == "google_flow"


def test_private_google_flow_production_remains_revisable(tmp_path):
    """D. Revisions of existing private productions stay ungated."""
    root = tmp_path / "productions"
    directory = root / PID
    directory.mkdir(parents=True)
    production = make_production(route=ROUTE_GOOGLE_FLOW)
    (directory / "production.json").write_text(
        _serialize(production), encoding="utf-8"
    )

    revised = production.model_copy(
        update={"plan_version": 2, "revision_notes": ["legacy revision"]}
    )
    save_revision(revised, root=root)
    assert load_production(PID, root=root).route == ROUTE_GOOGLE_FLOW


def test_production_model_still_accepts_private_route_for_loading():
    """The model-level validator accepts every KNOWN route (legacy loading);
    SaaS selection is gated only at the creation seams."""
    assert make_production(route=ROUTE_GOOGLE_FLOW).route == ROUTE_GOOGLE_FLOW


def test_legacy_generation_strategies_unchanged():
    """E. The legacy strategy bridge is untouched by the boundary."""
    assert GENERATION_STRATEGIES == (
        "local",
        "alibaba_image_local_video",
        "alibaba_video",
    )
    assert set(LEGACY_GENERATION_STRATEGY_ROUTES) == {
        "local",
        "alibaba_image_local_video",
        "alibaba_video",
    }
    assert route_id_for_legacy_strategy("local") == ROUTE_LOCAL_AI_ASSISTED
    assert (
        route_id_for_legacy_strategy("alibaba_video") == ROUTE_FULL_AI_VIDEO
    )
    assert DEFAULT_PRODUCTION_ROUTE == ROUTE_LOCAL_AI_ASSISTED