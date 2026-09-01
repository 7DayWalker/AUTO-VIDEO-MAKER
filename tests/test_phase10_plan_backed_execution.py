"""Hermetic Phase 10 plan-backed execution tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

import produce

from app.content.models import (
    ContentBrief,
    ContentGenerationResult,
    ProductionPlan,
    Scene,
    ProductionMetadata,
)
from app.content.prompts.scene_planner import get_combined_generation_prompt
from app.orchestrator.models import (
    OrchestratorConfig,
    PipelineStage,
    ProductionBinding,
    ProjectManifest,
    StageResult,
)
from app.orchestrator.pipeline import ProductionOrchestrator
from app.orchestrator.production_execution import (
    ProductionExecutor,
    UnitExecutionState,
    translate_provider_intent,
    unit_to_content_brief,
)
from app.production.models import (
    BibleElement,
    EpisodeMetadata,
    Production,
    ProductionBible,
    ProductionCommand,
    ProductionUnit,
)
from app.production.storage import (
    compute_content_hash,
    current_path,
    list_versions,
    load_version_snapshot,
    save_new_production,
    save_revision,
    unit_plan_dir,
)


CREATED = "2026-08-31T09:00:00+00:00"


def make_plan(identity: str, *, scenes: int = 2) -> ProductionPlan:
    duration = 5
    return ProductionPlan(
        project_id=identity,
        title="Pinned plan",
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


def make_production(units=None, *, bible=None) -> Production:
    return Production(
        production_id="prd_20260831_phase10_001",
        title="Phase 10 Production",
        command=ProductionCommand(
            text="Create a production.",
            workload_shape="unit_count",
            intended_unit_count=len(units or [1]),
            issued_at=CREATED,
        ),
        bible=bible,
        units=units
        or [
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


class CountingPlanner:
    def __init__(self):
        self.calls = []

    def plan(self, brief: ContentBrief) -> ContentGenerationResult:
        self.calls.append(brief)
        return ContentGenerationResult.from_plan(
            make_plan(f"plan_{brief.production_context['unit_id']}"),
            provider="fake",
            model="fake",
        )


@dataclass
class FakeReconciliation:
    complete: bool


class FakeEngine:
    """Writes truthful binding/completion evidence without media providers."""

    def __init__(self, config, projects_root, calls, complete=True):
        self.config = config
        self.projects_root = projects_root
        self.calls = calls
        self.complete = complete

    def run(self):
        self.calls.append(self.config)
        directory_id = self.config.resume_project_id or f"exec_{self.config.unit_id}"
        project_dir = self.projects_root / directory_id
        project_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = project_dir / "manifest.json"
        if manifest_path.exists():
            return ProjectManifest.from_dict(json.loads(manifest_path.read_text("utf-8")))
        binding = ProductionBinding(
            production_id=self.config.production_id,
            unit_id=self.config.unit_id,
            production_plan_version=self.config.production_plan_version,
            unit_plan_identity=self.config.unit_plan_identity,
            unit_plan_version=self.config.unit_plan_version,
            unit_plan_content_hash=self.config.unit_plan_content_hash,
            project_id=directory_id,
            bound_at=datetime.now().isoformat(),
        )
        manifest = ProjectManifest(
            project_id=self.config.unit_plan_identity,  # Preserve A != B quirk.
            topic=self.config.topic,
            production_binding=binding,
            mock_mode=True,
        )
        manifest.initialize_stages()
        if self.complete:
            for stage in PipelineStage.get_order():
                manifest.set_stage_result(StageResult.completed(stage))
        manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2), "utf-8")
        return manifest


def test_content_brief_context_is_additive_and_legacy_prompt_unchanged():
    legacy = ContentBrief(topic="Legacy topic")
    _, before = get_combined_generation_prompt(legacy)
    explicit_none = ContentBrief(topic="Legacy topic", production_context=None)
    _, after = get_combined_generation_prompt(explicit_none)
    assert before == after
    assert "PRODUCTION CONTEXT" not in before

    production = make_production(
        units=[
            ProductionUnit(
                unit_id="episode-001",
                ordinal=1,
                unit_type="episode",
                title="Episode 1",
                premise="A reef adventure.",
                content_intent={"target_duration": 10, "number_of_scenes": 2},
                episode=EpisodeMetadata(season_number=1, episode_number=1),
                bible_refs=["world/reef"],
            )
        ],
        bible=ProductionBible(
            elements=[
                BibleElement(
                    element_id="world/reef",
                    section="world",
                    content={"rule": "No humans"},
                )
            ]
        ),
    )
    brief = unit_to_content_brief(production, production.units[0])
    assert brief.topic == "Episode 1"
    assert brief.production_context["bible"] == {"world": [{"rule": "No humans"}]}
    _, prompt = get_combined_generation_prompt(brief)
    assert "PRODUCTION CONTEXT" in prompt
    assert "No humans" in prompt
    assert "bible" not in production.units[0].content_intent


def test_provider_intent_translation_and_google_flow_is_explicit():
    plan = make_plan("plan-routing")
    unit = ProductionUnit(
        unit_id="flow-unit",
        ordinal=1,
        unit_type="standalone_video",
        title="Flow unit",
        provider_intent="google_flow",
    )
    config = OrchestratorConfig(topic="Flow unit", visual_mode="hybrid")
    translate_provider_intent(unit, plan, config)
    assert config.visual_default_provider == "image_motion"
    assert config.visual_scene_routing == {
        "scene_01": "google_flow",
        "scene_02": "google_flow",
    }


def test_plan_artifacts_are_atomic_hashed_and_reused(tmp_path):
    production = make_production()
    save_new_production(production, root=tmp_path / "productions")
    planner = CountingPlanner()
    executor = ProductionExecutor(
        production.production_id,
        productions_root=tmp_path / "productions",
        projects_root=tmp_path / "projects",
        base_config=OrchestratorConfig(
            topic="Production unit",
            mock_mode=True,
            target_duration=10,
            number_of_scenes=2,
            visual_mode="hybrid",
        ),
        planner_factory=lambda: planner,
    )
    loaded = executor.load()
    first = executor.resolve_plan(loaded, loaded.units[0])
    second = executor.resolve_plan(loaded, loaded.units[0])
    assert len(planner.calls) == 1
    assert first.content_hash == second.content_hash == compute_content_hash(first.plan_path)
    plan_dir = unit_plan_dir(production.production_id, "unit-001", tmp_path / "productions")
    assert {path.name for path in plan_dir.iterdir()} == {
        "production_plan.json", "content_brief.json", "plan_meta.json"
    }
    assert not list(plan_dir.glob("*.tmp"))


def test_pipeline_supplied_plan_never_invokes_scene_planner(tmp_path, monkeypatch):
    plan = make_plan("plan-supplied")
    supplied = tmp_path / "production_plan.json"
    supplied.write_text(json.dumps(plan.model_dump(), indent=2), "utf-8")
    plan_hash = compute_content_hash(supplied)
    config = OrchestratorConfig(
        topic="Supplied unit",
        target_duration=10,
        number_of_scenes=2,
        mock_mode=True,
        visual_mode="hybrid",
        supplied_content_plan_path=supplied,
        production_id="prd_20260831_phase10_001",
        unit_id="unit-001",
        production_plan_version=1,
        unit_plan_identity=plan.project_id,
        unit_plan_version=1,
        unit_plan_content_hash=plan_hash,
    )
    orchestrator = ProductionOrchestrator(config)
    orchestrator.project_dir = tmp_path / "exec-A"
    orchestrator.project_dir.mkdir()
    orchestrator.manifest = ProjectManifest(
        project_id="exec-A", topic=config.topic, mock_mode=True
    )
    orchestrator.manifest.initialize_stages()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("ScenePlanner must not run for a supplied plan")

    monkeypatch.setattr("app.content.planner.ScenePlanner.plan_and_save", forbidden)
    orchestrator._execute_content_stage()
    copied = Path(orchestrator.manifest.content_plan_path)
    assert copied.read_bytes() == supplied.read_bytes()
    assert orchestrator.manifest.project_id == "plan-supplied"
    assert orchestrator.manifest.get_stage_result(PipelineStage.CONTENT).metadata["provider"] == "supplied_plan"


def test_binding_is_saved_before_any_stage(tmp_path, monkeypatch):
    plan = make_plan("plan-bound")
    supplied = tmp_path / "plan.json"
    supplied.write_text(json.dumps(plan.model_dump(), indent=2), "utf-8")
    plan_hash = compute_content_hash(supplied)
    monkeypatch.setattr("app.orchestrator.pipeline.ORCHESTRATOR_OUTPUT_DIR", tmp_path / "projects")
    config = OrchestratorConfig(
        topic="Bound unit",
        target_duration=10,
        number_of_scenes=2,
        mock_mode=True,
        visual_mode="hybrid",
        supplied_content_plan_path=supplied,
        production_id="prd_20260831_phase10_001",
        unit_id="unit-001",
        production_plan_version=1,
        unit_plan_identity=plan.project_id,
        unit_plan_version=1,
        unit_plan_content_hash=plan_hash,
    )
    orchestrator = ProductionOrchestrator(config)

    def stop_at_content():
        raw = json.loads((orchestrator.project_dir / "manifest.json").read_text("utf-8"))
        binding = raw["production_binding"]
        assert binding["project_id"] == orchestrator.project_dir.name
        assert raw["stages"]["content"]["status"] == "pending"
        raise RuntimeError("stop after binding")

    monkeypatch.setattr(orchestrator, "_execute_content_stage", stop_at_content)
    with pytest.raises(Exception, match="stop after binding"):
        orchestrator.run()


def test_dependency_precedence_derived_progress_and_60_unit_execution(tmp_path):
    units = [
        ProductionUnit(
            unit_id=f"ep-{number:03d}",
            ordinal=number,
            unit_type="episode",
            title=f"Episode {number}",
            content_intent={"target_duration": 10, "number_of_scenes": 2},
            episode=EpisodeMetadata(
                season_number=1, episode_number=number, episode_title=f"Episode {number}"
            ),
            dependencies=[f"ep-{number - 1:03d}"] if number > 1 else [],
        )
        for number in range(1, 61)
    ]
    production = make_production(units)
    root = tmp_path / "productions"
    projects = tmp_path / "projects"
    save_new_production(production, root=root)
    original = current_path(production.production_id, root).read_bytes()
    planner = CountingPlanner()
    engine_calls = []

    def engine_factory(config):
        return FakeEngine(config, projects, engine_calls)

    def reconcile(_project_id, project_dir, persist=False):
        manifest = ProjectManifest.from_dict(
            json.loads((project_dir / "manifest.json").read_text("utf-8"))
        )
        return FakeReconciliation(
            complete=manifest.current_stage == PipelineStage.COMPLETE
        )

    executor = ProductionExecutor(
        production.production_id,
        productions_root=root,
        projects_root=projects,
        base_config=OrchestratorConfig(
            topic="Production unit",
            mock_mode=True,
            target_duration=10,
            number_of_scenes=2,
            visual_mode="hybrid",
        ),
        planner_factory=lambda: planner,
        orchestrator_factory=engine_factory,
        reconcile=reconcile,
    )
    initial = executor.derive_progress()
    assert initial.units[0].state == UnitExecutionState.NOT_STARTED
    assert all(item.state == UnitExecutionState.BLOCKED for item in initial.units[1:])
    final = executor.execute()
    assert final.complete
    assert len(planner.calls) == len(engine_calls) == 60
    assert [call.unit_id for call in engine_calls] == [f"ep-{i:03d}" for i in range(1, 61)]
    assert current_path(production.production_id, root).read_bytes() == original
    assert list_versions(production.production_id, root=root) == []
    assert len(executor.discover_bindings()) == 60
    hashes = {
        call.unit_id: compute_content_hash(call.supplied_content_plan_path)
        for call in engine_calls
    }
    assert all(call.unit_plan_content_hash == hashes[call.unit_id] for call in engine_calls)
    for bound in executor.discover_bindings().values():
        binding_payload = bound.manifest.production_binding.model_dump()
        serialized = json.dumps(binding_payload).lower()
        assert "provider" not in serialized
        assert "api_key" not in serialized
        assert "credential" not in serialized
        assert not Path(binding_payload["project_id"]).is_absolute()

    rerun = executor.execute()
    assert rerun.complete
    assert len(planner.calls) == len(engine_calls) == 60


def test_lower_ordinal_remains_blocked_by_higher_ordinal_dependency(tmp_path):
    units = [
        ProductionUnit(
            unit_id="unit-002", ordinal=2, unit_type="standalone_video",
            title="Second", dependencies=["unit-005"],
        ),
        ProductionUnit(
            unit_id="unit-005", ordinal=5, unit_type="standalone_video",
            title="Fifth",
        ),
    ]
    production = make_production(units)
    save_new_production(production, root=tmp_path / "productions")
    executor = ProductionExecutor(
        production.production_id,
        productions_root=tmp_path / "productions",
        projects_root=tmp_path / "projects",
    )
    report = executor.derive_progress()
    states = {item.unit_id: item.state for item in report.units}
    assert states == {
        "unit-002": UnitExecutionState.BLOCKED,
        "unit-005": UnitExecutionState.NOT_STARTED,
    }


def _write_bound_manifest(
    projects: Path,
    artifacts,
    *,
    unit_id="unit-001",
    directory_id="exec-unit-001",
    stage_result=None,
    checkpoint=None,
):
    directory = projects / directory_id
    directory.mkdir(parents=True, exist_ok=True)
    manifest = ProjectManifest(
        project_id=artifacts.plan.project_id,
        topic="Bound unit",
        production_binding=ProductionBinding(
            production_id="prd_20260831_phase10_001",
            unit_id=unit_id,
            production_plan_version=artifacts.production_plan_version,
            unit_plan_identity=artifacts.plan.project_id,
            unit_plan_version=artifacts.unit_plan_version,
            unit_plan_content_hash=artifacts.content_hash,
            project_id=directory_id,
            bound_at=CREATED,
        ),
    )
    manifest.initialize_stages()
    if stage_result:
        manifest.set_stage_result(stage_result)
    if checkpoint:
        manifest.scene_checkpoints["scene_01"] = checkpoint
    (directory / "manifest.json").write_text(
        json.dumps(manifest.to_dict(), indent=2), "utf-8"
    )
    return manifest


@pytest.mark.parametrize(
    ("stage_result", "checkpoint", "complete", "expected"),
    [
        (None, None, False, UnitExecutionState.INCOMPLETE),
        (StageResult.running(PipelineStage.SCENES), None, False, UnitExecutionState.IN_FLIGHT),
        (
            None,
            {"status": "submitted", "job_id": "job-1", "polling_url": "https://poll"},
            False,
            UnitExecutionState.IN_FLIGHT,
        ),
        (
            None,
            {"status": "submitted", "checkpoint_evidence": {"submission": {"state": "SUBMITTED"}}},
            False,
            UnitExecutionState.IN_FLIGHT,
        ),
        (StageResult.failed(PipelineStage.SCENES, "terminal"), None, False, UnitExecutionState.FAILED),
        (None, None, True, UnitExecutionState.COMPLETED),
    ],
)
def test_bound_progress_states(
    tmp_path, stage_result, checkpoint, complete, expected
):
    production = make_production()
    root = tmp_path / "productions"
    projects = tmp_path / "projects"
    save_new_production(production, root=root)
    planner = CountingPlanner()
    executor = ProductionExecutor(
        production.production_id,
        productions_root=root,
        projects_root=projects,
        base_config=OrchestratorConfig(
            topic="Production unit", target_duration=10, number_of_scenes=2,
            mock_mode=True, visual_mode="hybrid",
        ),
        planner_factory=lambda: planner,
        reconcile=lambda *_args, **_kwargs: FakeReconciliation(complete=complete),
    )
    artifacts = executor.resolve_plan(production, production.units[0])
    _write_bound_manifest(
        projects, artifacts, stage_result=stage_result, checkpoint=checkpoint
    )
    assert executor.derive_progress().units[0].state == expected


def test_binding_is_immutable_and_legacy_manifest_is_compatible():
    binding = ProductionBinding(
        production_id="prd_20260831_phase10_001",
        unit_id="unit-001",
        production_plan_version=1,
        unit_plan_identity="plan-1",
        unit_plan_version=1,
        unit_plan_content_hash="0" * 64,
        project_id="exec-1",
        bound_at=CREATED,
    )
    with pytest.raises(PydanticValidationError):
        binding.unit_id = "unit-002"
    legacy = ProjectManifest.from_dict({"project_id": "legacy", "topic": "Legacy"})
    assert legacy.production_binding is None


def test_corrupt_manifest_and_hash_mismatch_fail_loudly(tmp_path):
    production = make_production()
    root = tmp_path / "productions"
    projects = tmp_path / "projects"
    save_new_production(production, root=root)
    planner = CountingPlanner()
    executor = ProductionExecutor(
        production.production_id,
        productions_root=root,
        projects_root=projects,
        base_config=OrchestratorConfig(
            topic="Production unit", target_duration=10, number_of_scenes=2,
            mock_mode=True, visual_mode="hybrid",
        ),
        planner_factory=lambda: planner,
    )
    artifacts = executor.resolve_plan(production, production.units[0])
    corrupt = projects / "corrupt"
    corrupt.mkdir(parents=True)
    (corrupt / "manifest.json").write_text("not-json", "utf-8")
    with pytest.raises(ValueError, match="ambiguous project evidence"):
        executor.discover_bindings()
    (corrupt / "manifest.json").unlink()

    _write_bound_manifest(projects, artifacts)
    artifacts.plan_path.write_text("{}", "utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        executor.discover_bindings()


def test_bound_plan_stays_pinned_after_production_revision(tmp_path):
    production = make_production()
    root = tmp_path / "productions"
    projects = tmp_path / "projects"
    save_new_production(production, root=root)
    planner = CountingPlanner()
    executor = ProductionExecutor(
        production.production_id,
        productions_root=root,
        projects_root=projects,
        base_config=OrchestratorConfig(
            topic="Production unit", target_duration=10, number_of_scenes=2,
            mock_mode=True, visual_mode="hybrid",
        ),
        planner_factory=lambda: planner,
        reconcile=lambda *_args, **_kwargs: FakeReconciliation(complete=False),
    )
    artifacts = executor.resolve_plan(production, production.units[0])
    bound_manifest = _write_bound_manifest(projects, artifacts)
    revised_payload = production.model_dump()
    revised_payload.update(
        plan_version=2,
        revised_at="2026-08-31T10:00:00+00:00",
        revision_notes=["Change future intent"],
    )
    revised_payload["units"][0]["title"] = "Revised title"
    save_revision(Production.model_validate(revised_payload), root=root)
    snapshot_before = load_version_snapshot(
        production.production_id, 1, root=root
    ).model_dump()

    current = executor.load()
    bound = executor.discover_bindings(current)["unit-001"]
    pinned = executor.resolve_plan(current, current.units[0], binding=bound)
    assert pinned.production_plan_version == 1
    assert pinned.content_hash == bound_manifest.production_binding.unit_plan_content_hash
    assert len(planner.calls) == 1
    assert load_version_snapshot(
        production.production_id, 1, root=root
    ).model_dump() == snapshot_before


def test_resume_reuses_bound_directory_and_does_not_create_duplicate(tmp_path):
    production = make_production()
    root = tmp_path / "productions"
    projects = tmp_path / "projects"
    save_new_production(production, root=root)
    planner = CountingPlanner()
    engine_calls = []
    executor = ProductionExecutor(
        production.production_id,
        productions_root=root,
        projects_root=projects,
        base_config=OrchestratorConfig(
            topic="Production unit", target_duration=10, number_of_scenes=2,
            mock_mode=True, visual_mode="hybrid",
        ),
        planner_factory=lambda: planner,
        orchestrator_factory=lambda config: FakeEngine(
            config, projects, engine_calls, complete=False
        ),
        reconcile=lambda *_args, **_kwargs: FakeReconciliation(complete=False),
    )
    artifacts = executor.resolve_plan(production, production.units[0])
    _write_bound_manifest(projects, artifacts)
    bound = executor.discover_bindings(production)["unit-001"]
    config = executor._config_for_unit(
        production, production.units[0], artifacts, bound=bound
    )
    FakeEngine(config, projects, engine_calls, complete=False).run()
    assert config.resume_project_id == "exec-unit-001"
    assert [path.name for path in projects.iterdir()] == ["exec-unit-001"]


def test_cli_production_path_is_additive_and_unit_requires_production(monkeypatch):
    captured = {}

    class FakeReport:
        production_id = "prd_20260831_phase10_001"
        plan_version = 1
        units = ()
        complete = True

    class FakeExecutor:
        def __init__(self, production_id, base_config):
            captured["production_id"] = production_id
            captured["config"] = base_config

        def derive_progress(self):
            return FakeReport()

        def execute(self, unit_id=None):
            captured["unit_id"] = unit_id
            return FakeReport()

    monkeypatch.setattr(
        "app.orchestrator.production_execution.ProductionExecutor", FakeExecutor
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "produce.py", "--production", "prd_20260831_phase10_001",
            "--unit", "unit-001", "--mock", "--visual-mode", "hybrid",
            "--no-confirm",
        ],
    )
    assert produce.main() == 0
    assert captured["production_id"] == "prd_20260831_phase10_001"
    assert captured["unit_id"] == "unit-001"
    assert captured["config"].mock_mode is True


def test_cli_legacy_flow_validation_is_unchanged():
    class Args:
        flow_project_url = ""
        visual_mode = "google_flow"
        route_scene = []

    assert produce.flow_url_required_error(Args()) == (
        "--flow-project-url is required for --topic and --resume"
    )


def test_known_flow_requirement_fails_before_planning(tmp_path):
    unit = ProductionUnit(
        unit_id="flow-unit", ordinal=1, unit_type="standalone_video",
        title="Flow unit", provider_intent="google_flow",
    )
    production = make_production([unit])
    root = tmp_path / "productions"
    save_new_production(production, root=root)
    planner = CountingPlanner()
    executor = ProductionExecutor(
        production.production_id,
        productions_root=root,
        projects_root=tmp_path / "projects",
        base_config=OrchestratorConfig(
            topic="Production unit", mock_mode=True, visual_mode="hybrid",
            target_duration=10, number_of_scenes=2,
        ),
        planner_factory=lambda: planner,
    )
    with pytest.raises(ValueError, match="runtime Flow project URL"):
        executor.execute()
    assert planner.calls == []