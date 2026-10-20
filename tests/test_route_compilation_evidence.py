"""G3 — persisted ROUTE COMPILATION EVIDENCE (Core V1 should-fix #1).

The production route is resolved and compiled at EXECUTION time:

    ProductionExecutor._config_for_unit
        -> app.production.routes.resolve_effective_route
        -> app.orchestrator.production_execution.apply_production_route
        -> RouteCompilation   (previously: ONLY a log line)

These tests pin the seam that now freezes that compiled decision into a
durable, read-only evidence record
(``ProjectManifest.route_compilation`` -> ``manifest.json``) and the semantic
boundary it must never cross:

    MODE != ROUTE != PROVIDER/MODEL != MEDIA STRATEGY != VIDEO ENGINE

Route compilation evidence answers exactly ONE question — "given the persisted
intent, which route did Core compile for execution?" — and never answers
"which provider/model executed this scene?" (Batch 1F provenance) or "what did
it cost?" (Batch 1H accounting).

Everything here is hermetic: no provider is constructed, no network call is
made, and no billable production happens; the engine seam is exercised through
project initialization only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.content.models import (
    ContentBrief,
    ContentGenerationResult,
    ProductionMetadata,
    ProductionPlan,
    Scene,
)
from app.orchestrator import pipeline as pipeline_module
from app.orchestrator.models import (
    OrchestratorConfig,
    ProjectManifest,
    RouteCompilationRecord,
)
from app.orchestrator.production_execution import (
    ProductionExecutor,
    UnitPlanArtifacts,
    apply_production_route,
)
from app.production.models import (
    Production,
    ProductionCommand,
    ProductionUnit,
)
from app.production.routes import (
    EXECUTION_SURFACE_STANDARD_VIDEO,
    KNOWN_ROUTES,
    ROUTE_FULL_AI_VIDEO,
    ROUTE_GOOGLE_FLOW,
    ROUTE_LOCAL_AI_ASSISTED,
    ROUTE_REGISTRY,
    ROUTE_SOURCE_EXPLICIT,
    ROUTE_SOURCE_LEGACY,
    resolve_effective_route,
)
from app.production.storage import load_production, save_new_production
from app.web import server as web


CREATED = "2026-09-25T09:00:00+00:00"
PID = "prd_20260925_route_evidence_001"
UNIT_ID = "unit-001"

#: The EXACT persisted shape of the evidence record. Any change here is a
#: deliberate schema decision, never an incidental one.
RECORD_FIELDS = {
    "route_id",
    "route_version",
    "route_source",
    "execution_surface",
    "legacy_strategy",
    "overlays_applied",
}


# ---------------------------------------------------------------------------
# Fixtures / helpers (same shapes the existing route tests use)
# ---------------------------------------------------------------------------


def make_production(*, strategy=None, route=None) -> Production:
    values = dict(
        production_id=PID,
        title="Route evidence production",
        command=ProductionCommand(
            text="Create one video.",
            workload_shape="single_video",
            intended_unit_count=1,
            issued_at=CREATED,
        ),
        units=[
            ProductionUnit(
                unit_id=UNIT_ID,
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


def make_plan(*, scenes: int = 2, identity: str = "plan_route_evidence"):
    duration = 5
    return ProductionPlan(
        project_id=identity,
        title="Route evidence plan",
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


def baseline_config(*, mock_mode: bool = False) -> OrchestratorConfig:
    """The exact base configuration the web local path builds today."""
    return OrchestratorConfig(
        topic="Route evidence",
        mock_mode=mock_mode,
        visual_mode="local",
        visual_default_provider="image_motion",
        image_source="auto",
    )


def route_snapshot(config: OrchestratorConfig) -> dict:
    """The execution surface the route compiler is allowed to touch."""
    return {
        "visual_mode": config.visual_mode,
        "visual_default_provider": config.visual_default_provider,
        "visual_scene_routing": dict(config.visual_scene_routing),
        "image_source": config.image_source,
    }


def make_artifacts(plan) -> UnitPlanArtifacts:
    return UnitPlanArtifacts(
        plan=plan,
        brief=ContentBrief(topic="Route evidence"),
        plan_path=Path("production_plan.json"),
        content_hash="0" * 64,
        production_plan_version=1,
    )


def compile_at_seam(production, plan, config):
    """Resolve + compile EXACTLY as ``_config_for_unit`` does, then freeze."""
    effective = resolve_effective_route(
        route=production.route,
        generation_strategy=production.generation_strategy,
    )
    compilation = apply_production_route(
        config, effective_route=effective, plan=plan
    )
    return effective, compilation, compilation.to_record()


# ---------------------------------------------------------------------------
# A) The record reflects the ACTUAL compiled route at the compilation boundary
# ---------------------------------------------------------------------------


def test_explicit_route_is_compiled_into_the_record():
    production = make_production(route=ROUTE_FULL_AI_VIDEO)
    plan = make_plan(scenes=3)
    config = baseline_config()
    _, compilation, record = compile_at_seam(production, plan, config)

    assert record.route_id == ROUTE_FULL_AI_VIDEO
    assert record.route_source == ROUTE_SOURCE_EXPLICIT
    assert record.execution_surface == EXECUTION_SURFACE_STANDARD_VIDEO
    assert record.route_version == ROUTE_REGISTRY[ROUTE_FULL_AI_VIDEO].route_version
    # The persisted intent still carries the legacy bridge input verbatim.
    assert record.legacy_strategy == "local"
    assert record.overlays_applied == ()
    # The record mirrors the compilation; it never adds or re-derives anything.
    assert record.route_id == compilation.route_id
    assert record.route_version == compilation.route_version
    assert record.route_source == compilation.source
    assert record.execution_surface == compilation.execution_surface
    assert record.overlays_applied == compilation.overlays_applied
    assert set(record.model_dump()) == RECORD_FIELDS


def test_legacy_strategy_compiles_to_mapped_route_and_records_overlay():
    """alibaba_image_local_video -> local_ai_assisted + image-source overlay."""
    production = make_production(strategy="alibaba_image_local_video")
    config = baseline_config()
    _, _, record = compile_at_seam(production, make_plan(), config)

    assert record.route_id == ROUTE_LOCAL_AI_ASSISTED
    assert record.route_source == ROUTE_SOURCE_LEGACY
    assert record.legacy_strategy == "alibaba_image_local_video"
    assert record.overlays_applied == ("image_source",)


def test_alibaba_video_legacy_strategy_compiles_full_ai_video():
    production = make_production(strategy="alibaba_video")
    config = baseline_config()
    _, _, record = compile_at_seam(production, make_plan(), config)

    assert record.route_id == ROUTE_FULL_AI_VIDEO
    assert record.route_source == ROUTE_SOURCE_LEGACY
    assert record.legacy_strategy == "alibaba_video"
    # full_ai_video's provider routing is NOT a legacy overlay and never
    # appears as one: overlay names come from the compatibility bridge only.
    assert record.overlays_applied == ()


def test_default_route_resolution_is_unchanged_and_recorded_honestly():
    """A pre-route artifact (no explicit route) still compiles local."""
    production = make_production()
    config = baseline_config()
    before = route_snapshot(config)
    _, _, record = compile_at_seam(production, make_plan(), config)

    assert record.route_id == ROUTE_LOCAL_AI_ASSISTED
    assert record.route_source == ROUTE_SOURCE_LEGACY
    assert record.legacy_strategy == "local"
    assert record.overlays_applied == ()
    # Byte-for-byte: the default path mutates nothing.
    assert route_snapshot(config) == before


@pytest.mark.parametrize("route", sorted(KNOWN_ROUTES))
def test_route_version_is_persisted_for_every_registered_route(route):
    production = make_production(route=route)
    _, _, record = compile_at_seam(production, make_plan(), baseline_config())

    definition = ROUTE_REGISTRY[route]
    assert record.route_id == route
    assert record.route_version == definition.route_version
    assert record.execution_surface == definition.execution_surface


def test_record_is_deterministic_and_read_only():
    production = make_production(strategy="alibaba_image_local_video")
    first = compile_at_seam(production, make_plan(), baseline_config())[2]
    second = compile_at_seam(production, make_plan(), baseline_config())[2]
    assert first.model_dump() == second.model_dump()
    assert isinstance(first.overlays_applied, tuple)

    with pytest.raises(Exception):
        first.route_id = "silently_rewritten"  # frozen: evidence is read-only


def test_compiled_route_precedence_is_recorded_not_changed():
    """Explicit route > legacy strategy > default, and the record says so."""
    production = make_production(
        strategy="alibaba_video", route=ROUTE_LOCAL_AI_ASSISTED
    )
    config = baseline_config()
    _, _, record = compile_at_seam(production, make_plan(), config)

    assert record.route_id == ROUTE_LOCAL_AI_ASSISTED
    assert record.route_source == ROUTE_SOURCE_EXPLICIT
    # The disagreeing legacy strategy is recorded AS INPUT, never as the route.
    assert record.legacy_strategy == "alibaba_video"
    assert record.overlays_applied == ()
    # The execution config proves the precedence, not only the record.
    assert config.visual_mode == "local"
    assert config.visual_scene_routing == {}


# ---------------------------------------------------------------------------
# B) Route evidence is NOT provider provenance, media strategy, or accounting
# ---------------------------------------------------------------------------

#: Provider / model / engine identity names.  Route evidence may never assert
#: one of them: which provider or model executes a scene is Batch 1F
#: provenance, and which engine animates or assembles is execution detail —
#: neither is a route fact.  (``google_flow`` is deliberately absent: it is
#: BOTH a registered route identity and a provider name, so the route side is
#: checked against the PRODUCT vocabulary instead of by name exclusion.)
NON_ROUTE_IDENTITY_NAMES = (
    "image_motion",
    "local_asset",
    "external_video",
    "gemini_omni",
    "openrouter_video",
    "alibaba",
    "alibaba_video",
    "dashscope",
    "wan",
    "veo",
    "kling",
    "ffmpeg",
    "edge_tts",
    "mock",
)

#: Anything that would turn route evidence into accounting or provenance.
FORBIDDEN_FIELD_FRAGMENTS = (
    "provider",
    "model",
    "cost",
    "credit",
    "amount",
    "price",
    "usage",
    "engine",
    "audio_policy",
    "request_id",
    "task_id",
    "job_id",
    "api_key",
    "scene",
    "image_source",
    "visual_mode",
    "visual_default",
    "routing",
    "path",
    "url",
)


def _record_cases():
    return [
        make_production(route=ROUTE_LOCAL_AI_ASSISTED),
        make_production(route=ROUTE_FULL_AI_VIDEO),
        make_production(route=ROUTE_GOOGLE_FLOW),
        make_production(strategy="local"),
        make_production(strategy="alibaba_image_local_video"),
        make_production(strategy="alibaba_video"),
        make_production(),
    ]


def test_record_shape_carries_no_provider_model_media_or_accounting_field():
    for production in _record_cases():
        record = compile_at_seam(
            production, make_plan(), baseline_config()
        )[2]
        dumped = record.model_dump()
        assert set(dumped) == RECORD_FIELDS
        for key in dumped:
            for fragment in FORBIDDEN_FIELD_FRAGMENTS:
                assert fragment not in key.lower(), (key, fragment)


def test_record_values_never_name_a_provider_or_engine():
    """Route evidence never asserts which provider/model/engine executed.

    ``legacy_strategy`` is intentionally excluded from the name check: it
    records the pre-existing COMPATIBILITY-bridge input name (the persisted
    ``generation_strategy`` vocabulary), which is resolution input evidence —
    not a provider/model assertion.  What must never happen is the ROUTE
    record duplicating the provider routing the compiler performed; that is
    checked explicitly below.
    """
    for production in _record_cases():
        config = baseline_config()
        plan = make_plan()
        record = compile_at_seam(production, plan, config)[2]
        dumped = record.model_dump()

        assert dumped["route_id"] in KNOWN_ROUTES
        for key, value in dumped.items():
            if key == "legacy_strategy":
                continue
            values = value if isinstance(value, tuple) else (value,)
            for item in values:
                if isinstance(item, str):
                    assert item not in NON_ROUTE_IDENTITY_NAMES, (key, item)

        # The compiled PROVIDER routing stays provider configuration: it is
        # never mirrored into the route record.
        routing_values = set(config.visual_scene_routing.values())
        if config.visual_default_provider:
            routing_values.add(config.visual_default_provider)
        serialized = json.dumps(
            {
                key: value
                for key, value in dumped.items()
                if key != "legacy_strategy"
            }
        )
        for name in sorted(routing_values & set(NON_ROUTE_IDENTITY_NAMES)):
            assert name not in serialized


def test_overlay_evidence_names_keys_only_never_media_values():
    """The legacy overlay is recorded as the config KEY mutated, not its value."""
    production = make_production(strategy="alibaba_image_local_video")
    config = baseline_config()
    record = compile_at_seam(production, make_plan(), config)[2]

    assert record.overlays_applied == ("image_source",)
    # The media-strategy VALUE stays where media strategy belongs: in the
    # execution configuration / scene config snapshot, never in route evidence.
    assert config.image_source == "alibaba"
    assert "alibaba" not in [
        value for value in record.model_dump().values() if isinstance(value, str)
    ]
    dumped = record.model_dump()
    assert "alibaba" not in dumped["overlays_applied"]
    assert dumped["route_id"] == ROUTE_LOCAL_AI_ASSISTED


def test_full_ai_video_evidence_does_not_record_its_video_provider_seam():
    """The route compiled onto a provider seam; the evidence says only ROUTE."""
    production = make_production(route=ROUTE_FULL_AI_VIDEO)
    plan = make_plan(scenes=3)
    config = baseline_config()
    record = compile_at_seam(production, plan, config)[2]

    # Execution really did route to the current provider seam...
    assert config.visual_default_provider == "alibaba_video"
    assert set(config.visual_scene_routing.values()) == {"alibaba_video"}
    # ...but the persisted route evidence names no provider and records no
    # overlay (this routing is the route's own compilation, not a legacy
    # compatibility overlay).
    assert record.overlays_applied == ()
    assert record.route_id == ROUTE_FULL_AI_VIDEO
    assert "alibaba" not in json.dumps(record.model_dump())


# ---------------------------------------------------------------------------
# C) DURABLE PERSISTENCE: manifest.json, written once at project creation
# ---------------------------------------------------------------------------


def _with_binding_inputs(config: OrchestratorConfig) -> OrchestratorConfig:
    """The Phase 10 binding inputs every production-backed run carries."""
    config.production_id = PID
    config.unit_id = UNIT_ID
    config.production_plan_version = 1
    config.unit_plan_identity = "plan_route_evidence"
    config.unit_plan_version = 1
    config.unit_plan_content_hash = "0" * 64
    return config


def _initialize_project(config, monkeypatch, projects):
    """Run the REAL project-initialization seam (no stages, no media)."""
    monkeypatch.setattr(pipeline_module, "ORCHESTRATOR_OUTPUT_DIR", projects)
    monkeypatch.setattr(
        pipeline_module, "ORCHESTRATOR_LOGS_DIR", projects.parent / "logs"
    )
    orchestrator = pipeline_module.ProductionOrchestrator(config)
    orchestrator._initialize_project()
    orchestrator._save_manifest()
    return orchestrator


def _manifest_raw(orchestrator) -> dict:
    return json.loads(
        (orchestrator.project_dir / "manifest.json").read_text(encoding="utf-8")
    )


def test_manifest_records_the_compiled_route_at_project_initialization(
    tmp_path, monkeypatch
):
    production = make_production(strategy="alibaba_image_local_video")
    config = _with_binding_inputs(baseline_config())
    _, _, record = compile_at_seam(production, make_plan(), config)
    config.route_compilation = record

    orchestrator = _initialize_project(config, monkeypatch, tmp_path / "projects")
    raw = _manifest_raw(orchestrator)

    assert raw["route_compilation"] == {
        "route_id": ROUTE_LOCAL_AI_ASSISTED,
        "route_version": ROUTE_REGISTRY[ROUTE_LOCAL_AI_ASSISTED].route_version,
        "route_source": ROUTE_SOURCE_LEGACY,
        "execution_surface": EXECUTION_SURFACE_STANDARD_VIDEO,
        "legacy_strategy": "alibaba_image_local_video",
        "overlays_applied": ["image_source"],
    }
    # The persisted bytes are exactly the compilation output — nothing more.
    assert raw["route_compilation"] == json.loads(json.dumps(record.model_dump()))
    # The plan binding stays the binding authority; evidence is NOT folded
    # into it (route != binding).
    assert raw["production_binding"]["unit_id"] == UNIT_ID
    assert "route_compilation" not in raw["production_binding"]
    assert "route" not in raw["production_binding"]


def test_record_survives_manifest_round_trips(tmp_path, monkeypatch):
    production = make_production(route=ROUTE_FULL_AI_VIDEO)
    config = _with_binding_inputs(baseline_config())
    _, _, record = compile_at_seam(production, make_plan(scenes=2), config)
    config.route_compilation = record

    orchestrator = _initialize_project(config, monkeypatch, tmp_path / "projects")
    raw = _manifest_raw(orchestrator)

    reloaded = ProjectManifest.from_dict(json.loads(json.dumps(raw)))
    assert reloaded.route_compilation == record
    assert reloaded.route_compilation.overlays_applied == ()
    again = ProjectManifest.from_dict(reloaded.to_dict())
    assert again.route_compilation == record


def test_resume_never_rewrites_route_compilation_evidence(tmp_path, monkeypatch):
    """Read-only after compilation: resume keeps the creation-time record."""
    config = _with_binding_inputs(baseline_config())
    config.route_compilation = RouteCompilationRecord(
        route_id=ROUTE_LOCAL_AI_ASSISTED,
        route_version=ROUTE_REGISTRY[ROUTE_LOCAL_AI_ASSISTED].route_version,
        route_source=ROUTE_SOURCE_EXPLICIT,
        execution_surface=EXECUTION_SURFACE_STANDARD_VIDEO,
        legacy_strategy="local",
        overlays_applied=(),
    )
    orchestrator = _initialize_project(config, monkeypatch, tmp_path / "projects")
    directory = orchestrator.project_dir.name
    persisted = _manifest_raw(orchestrator)["route_compilation"]

    resumed = _with_binding_inputs(baseline_config())
    resumed.resume_project_id = directory
    resumed.route_compilation = RouteCompilationRecord(
        route_id=ROUTE_FULL_AI_VIDEO,
        route_version=ROUTE_REGISTRY[ROUTE_FULL_AI_VIDEO].route_version,
        route_source=ROUTE_SOURCE_EXPLICIT,
        execution_surface=EXECUTION_SURFACE_STANDARD_VIDEO,
        legacy_strategy="local",
        overlays_applied=(),
    )
    orchestrator2 = pipeline_module.ProductionOrchestrator(resumed)
    orchestrator2._resume_project(directory)

    assert orchestrator2.manifest.route_compilation.model_dump(mode="json") == persisted
    manifest_path = tmp_path / "projects" / directory / "manifest.json"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))[
        "route_compilation"
    ] == persisted


def test_pre_g3_manifest_and_direct_engine_runs_stay_unknown(tmp_path, monkeypatch):
    """Nothing is fabricated for records that never compiled a route."""
    config = _with_binding_inputs(baseline_config())  # no route compiled at all
    orchestrator = _initialize_project(config, monkeypatch, tmp_path / "projects")
    raw = _manifest_raw(orchestrator)
    assert raw["route_compilation"] is None

    # A pre-G3 artifact (key absent entirely) loads as UNKNOWN, never inferred.
    raw.pop("route_compilation")
    legacy = ProjectManifest.from_dict(json.loads(json.dumps(raw)))
    assert legacy.route_compilation is None
    assert legacy.production_binding is not None


# ---------------------------------------------------------------------------
# D) END-TO-END at the real production seam: executor -> engine -> manifest
# ---------------------------------------------------------------------------


class _StubPlanner:
    """Deterministic planner: no provider, no network, nothing billable."""

    def __init__(self, plan):
        self._plan = plan

    def plan(self, brief):
        return ContentGenerationResult.from_plan(
            self._plan, provider="stub", model="stub"
        )


class _ProjectInitializingEngine:
    """Runs ONLY the real project-initialization seam (no stages, no media)."""

    def __init__(self, config):
        self.config = config
        self.manifest = None

    def run(self):
        orchestrator = pipeline_module.ProductionOrchestrator(self.config)
        orchestrator._initialize_project()
        orchestrator._save_manifest()
        self.manifest = orchestrator.manifest
        return self.manifest


def _wire(
    tmp_path,
    monkeypatch,
    production,
    *,
    mock_mode=False,
    extra_config=None,
    intent_patch=None,
):
    productions = tmp_path / "productions"
    projects = tmp_path / "projects"
    save_new_production(production, root=productions)
    if intent_patch is not None:
        # Edit the persisted artifact directly: this is how a LEGACY/private
        # artifact (e.g. route=google_flow, which the SaaS creation seam refuses
        # for NEW productions) legitimately reaches the loader.
        intent_patch(productions / PID / "production.json")
    monkeypatch.setattr(pipeline_module, "ORCHESTRATOR_OUTPUT_DIR", projects)
    monkeypatch.setattr(pipeline_module, "ORCHESTRATOR_LOGS_DIR", tmp_path / "logs")

    base = baseline_config(mock_mode=mock_mode)
    for key, value in (extra_config or {}).items():
        setattr(base, key, value)

    engines = []

    def factory(config):
        engine = _ProjectInitializingEngine(config)
        engines.append(engine)
        return engine

    executor = ProductionExecutor(
        PID,
        productions_root=productions,
        projects_root=projects,
        base_config=base,
        planner_factory=lambda: _StubPlanner(
            make_plan(identity="plan_route_evidence")
        ),
        orchestrator_factory=factory,
    )
    return executor, productions, projects, engines


def _project_manifest_raw(projects: Path):
    directories = [p for p in sorted(projects.iterdir()) if p.is_dir()]
    assert len(directories) == 1, directories
    raw = json.loads(
        (directories[0] / "manifest.json").read_text(encoding="utf-8")
    )
    return raw, directories[0].name


def test_executor_persists_compiled_route_for_a_legacy_strategy(tmp_path, monkeypatch):
    production = make_production(strategy="alibaba_image_local_video")
    executor, productions, projects, engines = _wire(tmp_path, monkeypatch, production)

    executor.execute()

    raw, _ = _project_manifest_raw(projects)
    assert raw["route_compilation"] == {
        "route_id": ROUTE_LOCAL_AI_ASSISTED,
        "route_version": ROUTE_REGISTRY[ROUTE_LOCAL_AI_ASSISTED].route_version,
        "route_source": ROUTE_SOURCE_LEGACY,
        "execution_surface": EXECUTION_SURFACE_STANDARD_VIDEO,
        "legacy_strategy": "alibaba_image_local_video",
        "overlays_applied": ["image_source"],
    }
    # The overlay really executed (media strategy beneath the route)...
    assert engines[0].config.image_source == "alibaba"
    assert raw["scene_config"]["visual_source"] == "alibaba"
    # ...and the intent artifact still carries BOTH original inputs verbatim.
    persisted = load_production(PID, root=productions)
    assert persisted.route is None
    assert persisted.generation_strategy == "alibaba_image_local_video"


def test_executor_persists_explicit_route_and_keeps_provider_out_of_evidence(
    tmp_path, monkeypatch
):
    production = make_production(route=ROUTE_FULL_AI_VIDEO)
    executor, productions, projects, engines = _wire(tmp_path, monkeypatch, production)

    executor.execute()

    raw, _ = _project_manifest_raw(projects)
    record = raw["route_compilation"]
    assert record["route_id"] == ROUTE_FULL_AI_VIDEO
    assert record["route_source"] == ROUTE_SOURCE_EXPLICIT
    assert record["overlays_applied"] == []
    # Provider/model truth lives in the provider configuration and in scene
    # provenance — NOT in route evidence. Route evidence must not duplicate it.
    assert raw["scene_config"]["visual_default_provider"] == "alibaba_video"
    assert "alibaba" not in json.dumps(record)
    assert raw["production_binding"]["unit_id"] == UNIT_ID


def _set_route(value: str):
    """Patch the persisted intent artifact to carry a legacy/private route."""

    def patch(path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["route"] = value
        path.write_text(json.dumps(raw), encoding="utf-8")

    return patch


def test_executor_persists_private_flow_route_evidence(tmp_path, monkeypatch):
    """A legacy/private ``google_flow`` artifact compiles AND is recorded."""
    production = make_production(route=ROUTE_LOCAL_AI_ASSISTED)
    executor, productions, projects, engines = _wire(
        tmp_path,
        monkeypatch,
        production,
        extra_config={"flow_project_url": "https://flow.example/project/1"},
        intent_patch=_set_route(ROUTE_GOOGLE_FLOW),
    )

    executor.execute()

    raw, _ = _project_manifest_raw(projects)
    assert raw["route_compilation"]["route_id"] == ROUTE_GOOGLE_FLOW
    assert raw["route_compilation"]["route_version"] == ROUTE_REGISTRY[
        ROUTE_GOOGLE_FLOW
    ].route_version
    assert raw["route_compilation"]["route_source"] == ROUTE_SOURCE_EXPLICIT
    assert raw["route_compilation"]["execution_surface"] == (
        EXECUTION_SURFACE_STANDARD_VIDEO
    )
    # The Flow execution SURFACE is what the route compiled onto; the Flow
    # project URL and provider routing remain runtime/provider configuration,
    # never route evidence.
    assert raw["scene_config"]["visual_mode"] == "google_flow"
    assert "https://flow.example" not in json.dumps(raw["route_compilation"])


# ---------------------------------------------------------------------------
# E) Intent stays intent; reads stay reads; legacy records stay untouched
# ---------------------------------------------------------------------------


def _tree_bytes(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_execution_never_rewrites_production_intent(tmp_path, monkeypatch):
    production = make_production(
        strategy="alibaba_image_local_video", route=ROUTE_LOCAL_AI_ASSISTED
    )
    executor, productions, projects, engines = _wire(tmp_path, monkeypatch, production)
    intent_path = productions / PID / "production.json"
    before = intent_path.read_bytes()

    executor.execute()

    assert intent_path.read_bytes() == before
    raw = json.loads(before.decode("utf-8"))
    assert raw["route"] == ROUTE_LOCAL_AI_ASSISTED
    assert raw["generation_strategy"] == "alibaba_image_local_video"
    # Route COMPILATION EVIDENCE is execution-side: it never enters intent.
    assert "route_compilation" not in raw
    loaded = load_production(PID, root=productions)
    assert loaded.route == ROUTE_LOCAL_AI_ASSISTED
    assert loaded.generation_strategy == "alibaba_image_local_video"


def test_reads_never_write_or_backfill_route_evidence(tmp_path, monkeypatch):
    """GET-side derivation is non-mutating: no evidence is added on read."""
    production = make_production(route=ROUTE_FULL_AI_VIDEO)
    executor, productions, projects, engines = _wire(tmp_path, monkeypatch, production)
    executor.execute()

    executor = ProductionExecutor(
        PID, productions_root=productions, projects_root=projects
    )
    before = _tree_bytes(productions), _tree_bytes(projects)

    for _ in range(3):
        executor.derive_progress()
        executor.discover_bindings(executor.load())
        executor.resolve_plan(executor.load(), executor.load().units[0])

    assert (_tree_bytes(productions), _tree_bytes(projects)) == before


def test_historical_productions_are_not_rewritten_by_the_new_seam(
    tmp_path, monkeypatch
):
    """A pre-existing (pre-G3) project keeps its evidence-free record."""
    production = make_production(strategy="alibaba_video")
    executor, productions, projects, engines = _wire(tmp_path, monkeypatch, production)
    executor.execute()
    raw, directory = _project_manifest_raw(projects)
    assert raw["route_compilation"] is not None

    # Simulate a historical project: strip the evidence bytes entirely.
    historical = dict(raw)
    historical["route_compilation"] = None
    manifest_path = projects / directory / "manifest.json"
    manifest_path.write_text(json.dumps(historical, indent=2), encoding="utf-8")

    before = _tree_bytes(projects)
    reader = ProductionExecutor(
        PID, productions_root=productions, projects_root=projects
    )
    report = reader.derive_progress()
    assert len(report.units) == 1
    assert _tree_bytes(projects) == before
    assert json.loads(manifest_path.read_text(encoding="utf-8"))[
        "route_compilation"
    ] is None


# ---------------------------------------------------------------------------
# F) API projection: existing structures, additive field, provenance intact
# ---------------------------------------------------------------------------


def _sample_record(route_id: str = ROUTE_FULL_AI_VIDEO) -> RouteCompilationRecord:
    definition = ROUTE_REGISTRY[route_id]
    return RouteCompilationRecord(
        route_id=definition.route_id,
        route_version=definition.route_version,
        route_source=ROUTE_SOURCE_EXPLICIT,
        execution_surface=definition.execution_surface,
        legacy_strategy="local",
        overlays_applied=(),
    )


def test_manifest_projection_exposes_the_persisted_evidence():
    record = _sample_record()
    manifest = ProjectManifest(
        project_id="proj_route_evidence",
        topic="Route evidence",
        route_compilation=record,
        mock_mode=True,
    )
    payload = web._manifest_payload(manifest, "proj_route_evidence")

    assert payload["route_compilation"] == record.model_dump(mode="json")
    assert payload["route_compilation"]["overlays_applied"] == []
    assert payload["production_binding"] is None


def test_manifest_projection_reports_unknown_for_legacy_projects():
    """Legacy records expose null/unknown — never inferred compilation."""
    manifest = ProjectManifest(
        project_id="proj_legacy",
        topic="Legacy project",
        scene_config={"visual_mode": "hybrid", "visual_default_provider": "alibaba_video"},
        mock_mode=True,
    )
    payload = web._manifest_payload(manifest, "proj_legacy")

    assert payload["route_compilation"] is None


def test_route_evidence_never_alters_scene_provenance_or_accounting_projection():
    """Provenance/accounting payloads are byte-identical with and without it."""
    checkpoint = {
        "status": "COMPLETED",
        "provider": "openrouter_video",
        "model": "some-model",
        "provenance": {
            "provider": "openrouter_video",
            "model": "some-model",
            "source": "generated",
        },
        "accounting": {"generation_cost": 0.42, "currency": "USD"},
    }
    with_evidence = web._manifest_payload(
        ProjectManifest(
            project_id="proj_a",
            topic="Route evidence",
            scene_checkpoints={"scene_01": dict(checkpoint)},
            route_compilation=_sample_record(),
            mock_mode=True,
        ),
        "proj_a",
    )
    without_evidence = web._manifest_payload(
        ProjectManifest(
            project_id="proj_a",
            topic="Route evidence",
            scene_checkpoints={"scene_01": dict(checkpoint)},
            mock_mode=True,
        ),
        "proj_a",
    )

    assert with_evidence["scene_checkpoints"] == without_evidence["scene_checkpoints"]
    assert (
        with_evidence["scene_checkpoint_rows"]
        == without_evidence["scene_checkpoint_rows"]
    )
    assert (
        with_evidence["production_binding"]
        == without_evidence["production_binding"]
    )
    # The ONLY difference is the route evidence itself.
    assert with_evidence["route_compilation"] is not None
    assert without_evidence["route_compilation"] is None
    # And route evidence carries no provenance/accounting payload to confuse it.
    assert with_evidence["route_compilation"]["route_id"] == ROUTE_FULL_AI_VIDEO
    assert "provenance" not in json.dumps(with_evidence["route_compilation"])
    assert "cost" not in json.dumps(with_evidence["route_compilation"])


def test_route_evidence_is_the_only_manifest_difference(tmp_path, monkeypatch):
    """No other manifest field moves when the evidence is recorded."""
    plain = _with_binding_inputs(baseline_config())
    plain_orchestrator = _initialize_project(plain, monkeypatch, tmp_path / "a")

    evidenced = _with_binding_inputs(baseline_config())
    evidenced.route_compilation = _sample_record(ROUTE_LOCAL_AI_ASSISTED)
    evidenced_orchestrator = _initialize_project(
        evidenced, monkeypatch, tmp_path / "b"
    )

    before = _manifest_raw(plain_orchestrator)
    after = _manifest_raw(evidenced_orchestrator)

    # Same key set: the additive field is a value, not a schema rewrite.
    assert set(after) == set(before)
    assert before["route_compilation"] is None
    assert after["route_compilation"] is not None
    volatile = {
        "route_compilation",
        "project_id",
        "created_at",
        "updated_at",
        "production_binding",
    }
    for key in before:
        if key in volatile:
            continue
        assert before[key] == after[key], key
    # Accounting stays where accounting lives, and it did not move.
    assert before["total_credits_used"] == after["total_credits_used"] == 0





def test_record_never_leaks_into_persisted_config_snapshots():
    """Route evidence is its own field; it is not config, not provenance."""
    production = make_production(strategy="alibaba_video")
    config = baseline_config()
    config.route_compilation = compile_at_seam(
        production, make_plan(), config
    )[2]

    for snapshot in (
        config.to_content_brief_dict(),
        config.to_voice_config_dict(),
        config.to_scene_config_dict(),
        config.to_assembly_config_dict(),
    ):
        assert "route_compilation" not in snapshot
        assert "route_version" not in snapshot
    # The provider routing that full_ai_video compiled remains provider
    # configuration — recorded as config, and NOT mirrored into route evidence.
    assert config.to_scene_config_dict()["visual_default_provider"] == "alibaba_video"
    record = config.route_compilation.model_dump()
    assert record["route_id"] == ROUTE_FULL_AI_VIDEO
    assert record["overlays_applied"] == ()
    assert "alibaba_video" not in json.dumps(
        {key: value for key, value in record.items() if key != "legacy_strategy"}
    )


def test_mock_mode_still_records_the_compiled_route_truthfully(tmp_path):
    """Hermetic mock mode resolves the route but applies nothing."""
    production = make_production(strategy="alibaba_video")
    config = baseline_config(mock_mode=True)
    config.image_source = "fixture"
    config.production_id = PID
    config.unit_id = UNIT_ID
    executor = ProductionExecutor(
        PID, productions_root=tmp_path, base_config=config
    )
    applied = executor._config_for_unit(
        production, production.units[0], make_artifacts(make_plan()), bound=None
    )

    assert applied.route_compilation is not None
    assert applied.route_compilation.route_id == ROUTE_FULL_AI_VIDEO
    assert applied.route_compilation.overlays_applied == ()
    # Nothing was compiled into the hermetic execution configuration.
    assert applied.visual_mode == "local"
    assert applied.visual_scene_routing == {}
    assert applied.image_source == "fixture"



