"""Batch 1E — Route requirement -> capability-aware planning.

Hermetic coverage for:

- the generic ``PlanningConstraints`` planning-input model (additive,
  execution-unaware);
- the pure route-requirement + capability-facts converter;
- the provider-owned capability-fact projections (Alibaba capability table;
  OpenRouter cached catalog — never a network call);
- deterministic allowed-set duration normalization (legacy behavior intact
  when unconstrained; truthful infeasibility handling);
- executor injection at ``resolve_plan`` (Full AI Video receives
  constraints; local/Google Flow/mock do not) and the
  ``_config_for_unit`` planning/execution boundary;
- generic ``plan_meta.json`` constraint evidence;
- legacy ``generation_strategy`` compatibility.

No network requests; no provider calls; persistence uses tmp_path.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.content.models import (
    ContentBrief,
    ContentGenerationResult,
    PlanningConstraints,
    ProductionMetadata,
    ProductionPlan,
    Scene,
)
from app.content.generator import normalize_scene_durations
from app.orchestrator import planning_constraints as pc
from app.orchestrator.alibaba_video import alibaba_video_planning_facts
from app.orchestrator.models import OrchestratorConfig
from app.orchestrator.openrouter_video import (
    OpenRouterHTTPClient,
    OpenRouterVideoCatalog,
    OpenRouterVideoModel,
    openrouter_video_planning_facts,
)
from app.orchestrator.production_execution import (
    ProductionExecutor,
    UnitPlanArtifacts,
)
from app.production.models import (
    Production,
    ProductionCommand,
    ProductionUnit,
)
from app.production.routes import (
    ROUTE_FULL_AI_VIDEO,
    ROUTE_GOOGLE_FLOW,
    ROUTE_LOCAL_AI_ASSISTED,
    ROUTE_REGISTRY,
)
from app.production.storage import save_new_production

CREATED = "2026-09-14T09:00:00+00:00"
PID = "prd_20260914_caps_001"


def make_production(
    *, strategy: str | None = None, route: str | None = None
) -> Production:
    values = dict(
        production_id=PID,
        title="Capability-aware planning production",
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


def make_plan(
    *,
    scenes: int = 2,
    durations: tuple | None = None,
) -> ProductionPlan:
    durations = durations or tuple(5.0 for _ in range(scenes))
    return ProductionPlan(
        project_id="20260914_caps_plan_001",
        title="Capability plan",
        hook="Hook",
        script=" ".join(f"Narration {i}" for i in range(1, scenes + 1)),
        call_to_action=None,
        estimated_duration=sum(float(d) for d in durations),
        scenes=[
            Scene(
                id=f"scene_{i:02d}",
                sequence=i,
                narration=f"Narration {i}",
                visual_prompt=f"Visual {i}",
                estimated_duration=float(durations[i - 1]),
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


def baseline_config(**overrides) -> OrchestratorConfig:
    values = dict(
        topic="Capability production",
        mock_mode=False,
        visual_mode="local",
        visual_default_provider="image_motion",
        image_source="auto",
    )
    values.update(overrides)
    return OrchestratorConfig(**values)


class _CapturePlanner:
    """Hermetic planner double that records the briefs it receives."""

    def __init__(self) -> None:
        self.briefs: list[ContentBrief] = []

    def plan(self, brief: ContentBrief) -> ContentGenerationResult:
        self.briefs.append(brief)
        target = brief.target_duration
        return ContentGenerationResult.from_plan(
            plan=make_plan(
                scenes=2,
                durations=(float(target) / 2, float(target) / 2),
            ),
            provider="test-provider",
            model="test-model",
            attempts=1,
            warnings=[],
        )


def _executor_with_capture(
    tmp_path: Path, production: Production, config: OrchestratorConfig
):
    root = tmp_path / "productions"
    save_new_production(production, root=root)
    planner = _CapturePlanner()
    executor = ProductionExecutor(
        production.production_id,
        productions_root=root,
        base_config=config,
        planner_factory=lambda: planner,
    )
    return root, executor, planner


def _facts(durations=(5, 10)) -> pc.ModelCapabilityFacts:
    return pc.ModelCapabilityFacts(supported_durations=tuple(durations))


def _scene_dicts(*durations: float) -> list[dict]:
    return [
        {
            "id": f"scene_{i:02d}",
            "sequence": i,
            "narration": f"Narration {i}",
            "visual_prompt": f"Visual {i}",
            "estimated_duration": value,
            "purpose": "hook" if i == 1 else "conclusion",
        }
        for i, value in enumerate(durations, start=1)
    ]


# ---------------------------------------------------------------------------
# 1-3) PlanningConstraints model + ContentBrief compatibility
# ---------------------------------------------------------------------------


def test_planning_constraints_model_is_generic_and_frozen():
    constraints = PlanningConstraints(allowed_scene_durations=(5, 10))
    assert constraints.allowed_scene_durations == (5, 10)
    assert constraints.source == "model_capability"
    with pytest.raises(Exception):
        constraints.source = "other"  # frozen
    payload = constraints.model_dump()
    # Generic shape only — never provider/model/execution keys.
    assert set(payload) == {"allowed_scene_durations", "source"}


def test_content_brief_without_constraints_stays_compatible():
    brief = ContentBrief(topic="Legacy brief")
    assert brief.planning_constraints is None
    # A pre-1E brief payload still validates unchanged.
    legacy = ContentBrief.model_validate({"topic": "Legacy brief"})
    assert legacy.planning_constraints is None


def test_content_brief_constraints_roundtrip():
    brief = ContentBrief(
        topic="Constrained brief",
        planning_constraints=PlanningConstraints(
            allowed_scene_durations=(5, 10)
        ),
    )
    payload = brief.model_dump(mode="json")
    assert payload["planning_constraints"]["allowed_scene_durations"] == [5, 10]
    restored = ContentBrief.model_validate(payload)
    assert restored.planning_constraints is not None
    assert restored.planning_constraints.allowed_scene_durations == (5, 10)


# ---------------------------------------------------------------------------
# 4-7) Pure converter
# ---------------------------------------------------------------------------


def test_converter_full_ai_video_with_supported_durations():
    constraints = pc.resolve_planning_constraints(
        ROUTE_REGISTRY[ROUTE_FULL_AI_VIDEO], _facts((5, 10))
    )
    assert constraints is not None
    assert constraints.allowed_scene_durations == (5, 10)
    assert constraints.source == "model_capability"


def test_converter_local_route_yields_none():
    assert (
        pc.resolve_planning_constraints(
            ROUTE_REGISTRY[ROUTE_LOCAL_AI_ASSISTED], _facts((5, 10))
        )
        is None
    )


def test_converter_google_flow_without_facts_yields_none():
    assert (
        pc.resolve_planning_constraints(
            ROUTE_REGISTRY[ROUTE_GOOGLE_FLOW], None
        )
        is None
    )


def test_converter_native_motion_route_without_facts_yields_none():
    assert pc.resolve_planning_constraints(
        ROUTE_REGISTRY[ROUTE_FULL_AI_VIDEO], None
    ) is None


def test_converter_never_carries_provider_identity():
    constraints = pc.resolve_planning_constraints(
        ROUTE_REGISTRY[ROUTE_FULL_AI_VIDEO], _facts((5, 10))
    )
    rendered = repr(constraints.model_dump())
    for token in ("alibaba", "wan", "openrouter", "qwen", "veo"):
        assert token not in rendered.lower()


# ---------------------------------------------------------------------------
# 8-10) Provider-owned capability fact projections (local metadata only)
# ---------------------------------------------------------------------------


def test_alibaba_projection_uses_existing_capability_source(monkeypatch):
    monkeypatch.setenv("ALIBABA_API_KEY", "test-key")
    monkeypatch.delenv("AVM_ALIBABA_VIDEO_DURATIONS", raising=False)
    monkeypatch.delenv("AVM_ALIBABA_VIDEO_MODEL", raising=False)
    facts = alibaba_video_planning_facts()
    assert facts is not None
    # The currently resolved default model's capability system.
    assert facts.supported_durations == (5, 10)
    assert facts.source == "model_capability"


def test_alibaba_projection_respects_operator_duration_override(monkeypatch):
    monkeypatch.setenv("ALIBABA_API_KEY", "test-key")
    monkeypatch.setenv("AVM_ALIBABA_VIDEO_DURATIONS", "10")
    facts = alibaba_video_planning_facts()
    assert facts is not None
    assert facts.supported_durations == (10,)


def test_alibaba_projection_unconfigured_yields_no_facts(monkeypatch):
    monkeypatch.delenv("ALIBABA_API_KEY", raising=False)
    assert alibaba_video_planning_facts() is None


def _write_openrouter_cache(tmp_path: Path, model_id: str, durations=(5, 10)):
    cache_path = tmp_path / "openrouter_models_cache.json"
    catalog = OpenRouterVideoCatalog(
        OpenRouterHTTPClient(api_key=""), cache_path=cache_path
    )
    entry = OpenRouterVideoModel.from_dict(
        {"id": model_id, "supported_durations": list(durations)}
    )
    catalog._write_cache([entry])
    return cache_path


def test_openrouter_projection_uses_cached_catalog(monkeypatch, tmp_path):
    cache_path = _write_openrouter_cache(tmp_path, "vendor/video-model-x")
    monkeypatch.setenv("AVM_OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AVM_OPENROUTER_MODEL", "vendor/video-model-x")
    monkeypatch.setenv("AVM_OPENROUTER_MODEL_CACHE", str(cache_path))
    facts = openrouter_video_planning_facts()
    assert facts is not None
    assert facts.supported_durations == (5, 10)
    assert facts.source == "model_capability"


def test_openrouter_projection_without_usable_cache_yields_none(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AVM_OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AVM_OPENROUTER_MODEL", "vendor/video-model-x")
    monkeypatch.setenv(
        "AVM_OPENROUTER_MODEL_CACHE", str(tmp_path / "missing.json")
    )
    # Missing cache: honest no-facts outcome (never a network fetch).
    assert openrouter_video_planning_facts() is None


def test_openrouter_projection_unknown_model_yields_none(
    monkeypatch, tmp_path
):
    cache_path = _write_openrouter_cache(tmp_path, "vendor/video-model-x")
    monkeypatch.setenv("AVM_OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AVM_OPENROUTER_MODEL", "vendor/other-model")
    monkeypatch.setenv("AVM_OPENROUTER_MODEL_CACHE", str(cache_path))
    assert openrouter_video_planning_facts() is None


# ---------------------------------------------------------------------------
# 11-13) Deterministic allowed-set normalization (legacy intact)
# ---------------------------------------------------------------------------


def test_normalize_legacy_behavior_when_unconstrained():
    scenes = _scene_dicts(15.0, 2.0, 2.0)
    new_scenes, warnings = normalize_scene_durations(scenes)
    durations = [s["estimated_duration"] for s in new_scenes]
    # Schema-bounds redistribution preserved exactly (total kept at 19s).
    assert all(2.0 <= d <= 10.0 for d in durations)
    assert abs(sum(durations) - 19.0) < 0.05
    assert warnings, "legacy redistribution must surface warnings"
    # Explicit None behaves identically to the omitted default.
    scenes2 = _scene_dicts(15.0, 2.0, 2.0)
    normalize_scene_durations(scenes2, allowed_durations=None)
    assert [s["estimated_duration"] for s in scenes2] == durations


def test_normalize_projects_onto_allowed_set():
    scenes = _scene_dicts(4.0, 6.0)
    new_scenes, warnings = normalize_scene_durations(
        scenes, allowed_durations=(5, 10)
    )
    durations = [s["estimated_duration"] for s in new_scenes]
    # Total 10s is exactly representable: [4, 6] -> [5, 5].
    assert durations == [5.0, 5.0]
    assert any("execution capability" in w for w in warnings)


def test_normalize_projection_is_idempotent():
    scenes = _scene_dicts(5.0, 5.0)
    new_scenes, warnings = normalize_scene_durations(
        scenes, allowed_durations=(5, 10)
    )
    durations = [s["estimated_duration"] for s in new_scenes]
    assert durations == [5.0, 5.0]
    assert warnings == []


def test_normalize_total_shift_within_tolerance_is_projected():
    # Total 11s is not representable with 2 allowed scenes {5,10}; the
    # closest representable total (10s) stays within the plan tolerance.
    scenes = _scene_dicts(4.0, 7.0)
    new_scenes, warnings = normalize_scene_durations(
        scenes, allowed_durations=(5, 10)
    )
    durations = [s["estimated_duration"] for s in new_scenes]
    assert durations == [5.0, 5.0]
    assert any("11s -> 10s" in w for w in warnings)


def test_normalize_infeasible_total_is_truthful_and_untouched():
    # 30s target with 2 scenes at {5,10}: closest achievable total (20s) is
    # outside the plan tolerance -> NOTHING is projected; the truthful
    # warning is recorded and the adapter remains the final authority.
    scenes = _scene_dicts(15.0, 15.0)
    new_scenes, warnings = normalize_scene_durations(
        scenes, allowed_durations=(5, 10)
    )
    assert [s["estimated_duration"] for s in new_scenes] == [15.0, 15.0]
    assert any("cannot be represented within tolerance" in w for w in warnings)


def test_normalize_infeasible_count_is_truthful_and_untouched():
    # 10s across 3 scenes can never be built from {5,10} (min total 15).
    scenes = _scene_dicts(4.0, 3.0, 3.0)
    new_scenes, warnings = normalize_scene_durations(
        scenes, allowed_durations=(5, 10)
    )
    assert [s["estimated_duration"] for s in new_scenes] == [4.0, 3.0, 3.0]
    assert any("cannot be represented within tolerance" in w for w in warnings)


# ---------------------------------------------------------------------------
# 14-19) Executor injection, boundaries, compatibility
# ---------------------------------------------------------------------------


def test_executor_full_ai_video_planner_receives_constraints(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        pc, "resolve_video_model_capability_facts", lambda: _facts((5, 10))
    )
    production = make_production(route=ROUTE_FULL_AI_VIDEO)
    root, executor, planner = _executor_with_capture(
        tmp_path, production, baseline_config()
    )
    executor.resolve_plan(production, production.units[0], binding=None)
    assert len(planner.briefs) == 1
    brief = planner.briefs[0]
    assert brief.planning_constraints is not None
    assert brief.planning_constraints.allowed_scene_durations == (5, 10)

    # Generic plan_meta.json evidence recorded when constraints applied.
    meta_path = (
        root
        / production.production_id
        / "units"
        / "unit-001"
        / "plan"
        / "plan_meta.json"
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["planning_constraints"] == {
        "source": "model_capability",
        "allowed_scene_durations": [5, 10],
    }


def test_executor_local_route_receives_no_motion_constraints(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        pc, "resolve_video_model_capability_facts", lambda: _facts((5, 10))
    )
    production = make_production(route=ROUTE_LOCAL_AI_ASSISTED)
    root, executor, planner = _executor_with_capture(
        tmp_path, production, baseline_config()
    )
    executor.resolve_plan(production, production.units[0], binding=None)
    assert planner.briefs[0].planning_constraints is None
    meta_path = (
        root
        / production.production_id
        / "units"
        / "unit-001"
        / "plan"
        / "plan_meta.json"
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert "planning_constraints" not in meta


def test_executor_legacy_strategy_receives_constraints(tmp_path, monkeypatch):
    """Legacy alibaba_video -> Full AI Video: same constraint behavior."""
    monkeypatch.setattr(
        pc, "resolve_video_model_capability_facts", lambda: _facts((5, 10))
    )
    production = make_production(strategy="alibaba_video")
    _, executor, planner = _executor_with_capture(
        tmp_path, production, baseline_config()
    )
    executor.resolve_plan(production, production.units[0], binding=None)
    brief = planner.briefs[0]
    assert brief.planning_constraints is not None
    assert brief.planning_constraints.allowed_scene_durations == (5, 10)
    assert production.generation_strategy == "alibaba_video"


def test_executor_mock_mode_stays_inert(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pc, "resolve_video_model_capability_facts", lambda: _facts((5, 10))
    )
    production = make_production(route=ROUTE_FULL_AI_VIDEO)
    _, executor, planner = _executor_with_capture(
        tmp_path,
        production,
        baseline_config(mock_mode=True, image_source="fixture"),
    )
    executor.resolve_plan(production, production.units[0], binding=None)
    assert planner.briefs[0].planning_constraints is None


def test_planning_constraints_never_leak_into_execution_config():
    brief = ContentBrief(
        topic="Boundary brief",
        planning_constraints=PlanningConstraints(
            allowed_scene_durations=(5, 10)
        ),
    )
    artifacts = UnitPlanArtifacts(
        plan=make_plan(),
        brief=brief,
        plan_path=Path("plan.json"),
        content_hash="0" * 64,
        production_plan_version=1,
    )
    production = make_production()
    executor = ProductionExecutor(
        PID,
        productions_root=Path("unused"),
        base_config=baseline_config(),
    )
    applied = executor._config_for_unit(
        production,
        production.units[0],
        artifacts,
        bound=None,
    )
    # Planning constraints are planning input only — never execution config.
    assert not hasattr(applied, "planning_constraints")