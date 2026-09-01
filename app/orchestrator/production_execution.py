"""Phase 10 production-level composition around ProductionOrchestrator.

This module owns no media generation.  It translates durable Production intent,
pins one exact per-unit plan, binds it to one existing execution project, and
derives progress from manifests plus the existing reconciliation oracle.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from app.content.models import ContentBrief, ProductionPlan
from app.content.planner import ScenePlanner, create_scene_planner
from app.content.validator import validate_production_plan
from app.orchestrator.models import (
    OrchestratorConfig,
    ProjectManifest,
    StageStatus,
)
from app.production.models import Production, ProductionUnit
from app.production.storage import (
    compute_content_hash,
    load_production,
    unit_plan_dir,
)

logger = logging.getLogger(__name__)

PLAN_FILE = "production_plan.json"
BRIEF_FILE = "content_brief.json"
META_FILE = "plan_meta.json"
UNIT_PLAN_VERSION = 1


class UnitExecutionState(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    BLOCKED = "BLOCKED"
    IN_FLIGHT = "IN_FLIGHT"
    INCOMPLETE = "INCOMPLETE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class UnitPlanArtifacts:
    plan: ProductionPlan
    brief: ContentBrief
    plan_path: Path
    content_hash: str
    production_plan_version: int
    unit_plan_version: int = UNIT_PLAN_VERSION


@dataclass(frozen=True)
class BoundProject:
    directory_id: str
    project_dir: Path
    manifest: ProjectManifest


@dataclass(frozen=True)
class UnitProgress:
    unit_id: str
    ordinal: int
    state: UnitExecutionState
    project_id: Optional[str] = None
    bound_plan_version: Optional[int] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class ProductionExecutionReport:
    production_id: str
    plan_version: int
    units: Tuple[UnitProgress, ...]

    @property
    def complete(self) -> bool:
        return bool(self.units) and all(
            item.state == UnitExecutionState.COMPLETED for item in self.units
        )

    def counts(self) -> Dict[UnitExecutionState, int]:
        return {
            state: sum(item.state == state for item in self.units)
            for state in UnitExecutionState
        }


_BRIEF_KEYS = {
    "topic_override",
    "niche",
    "target_audience",
    "tone",
    "target_duration",
    "platform",
    "language",
    "content_goal",
    "call_to_action",
    "style",
    "number_of_scenes",
}


def unit_to_content_brief(
    production: Production,
    unit: ProductionUnit,
    *,
    defaults: Optional[Mapping[str, object]] = None,
) -> ContentBrief:
    """Pure ProductionUnit -> ContentBrief translation; never mutates intent."""
    values = dict(defaults or {})
    unknown = sorted(set(unit.content_intent) - _BRIEF_KEYS)
    if unknown:
        logger.warning("Unit %s has ignored content_intent keys: %s", unit.unit_id, unknown)
    for key in _BRIEF_KEYS:
        if key != "topic_override" and key in unit.content_intent:
            values[key] = unit.content_intent[key]
    values["topic"] = unit.content_intent.get("topic_override") or unit.title

    bible_by_id = {
        element.element_id: element
        for element in (production.bible.elements if production.bible else [])
    }
    bible_context: Dict[str, List[object]] = {}
    for reference in unit.bible_refs:
        element = bible_by_id[reference]  # Production validation guarantees this.
        bible_context.setdefault(element.section, []).append(element.content)

    context = {
        "production_title": production.title,
        "unit_id": unit.unit_id,
        "unit_type": unit.unit_type,
        "ordinal": unit.ordinal,
    }
    if unit.description:
        context["description"] = unit.description
    if unit.premise:
        context["premise"] = unit.premise
    if unit.episode:
        context["episode"] = unit.episode.model_dump()
    if bible_context:
        context["bible"] = bible_context
    values["production_context"] = context
    return ContentBrief(**values)


def _serialize(model) -> str:
    return json.dumps(model.model_dump(), indent=2, ensure_ascii=False)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed durable artifact {path}: {exc}") from exc


def translate_provider_intent(
    unit: ProductionUnit,
    plan: ProductionPlan,
    config: OrchestratorConfig,
) -> None:
    """Translate name-level intent into existing routing config only."""
    intent = unit.provider_intent
    if intent in (None, "default"):
        return
    config.visual_mode = "hybrid"
    if intent == "google_flow":
        desired = {scene.id: "google_flow" for scene in plan.scenes}
        conflicts = {
            scene_id: config.visual_scene_routing[scene_id]
            for scene_id in desired
            if scene_id in config.visual_scene_routing
            and config.visual_scene_routing[scene_id] != "google_flow"
        }
        if conflicts:
            raise ValueError(
                f"unit-derived google_flow routing conflicts with operator routing: {conflicts}"
            )
        config.visual_default_provider = "image_motion"
        config.visual_scene_routing = {**config.visual_scene_routing, **desired}
    else:
        config.visual_default_provider = intent


class ProductionExecutor:
    """Re-entrant production-level composer around the existing engine."""

    def __init__(
        self,
        production_id: str,
        *,
        productions_root: Optional[Path] = None,
        projects_root: Optional[Path] = None,
        base_config: Optional[OrchestratorConfig] = None,
        planner_factory: Optional[Callable[[], ScenePlanner]] = None,
        orchestrator_factory: Optional[Callable[[OrchestratorConfig], object]] = None,
        reconcile: Optional[Callable[..., object]] = None,
    ) -> None:
        from app.orchestrator.pipeline import ORCHESTRATOR_OUTPUT_DIR, ProductionOrchestrator
        from app.orchestrator.reconciliation import reconcile_project

        self.production_id = production_id
        self.productions_root = Path(productions_root) if productions_root else None
        self.projects_root = Path(projects_root or ORCHESTRATOR_OUTPUT_DIR)
        self.base_config = base_config or OrchestratorConfig(
            topic="Production unit", mock_mode=True, visual_mode="hybrid"
        )
        self._planner_factory = planner_factory or self._default_planner
        self._orchestrator_factory = orchestrator_factory or ProductionOrchestrator
        self._reconcile = reconcile or reconcile_project

    def _default_planner(self) -> ScenePlanner:
        return create_scene_planner(mock_mode=self.base_config.mock_mode)

    def load(self) -> Production:
        return load_production(self.production_id, root=self.productions_root)

    def _plan_directory(self, unit_id: str) -> Path:
        return unit_plan_dir(
            self.production_id, unit_id, root=self.productions_root
        )

    def discover_bindings(self, production: Optional[Production] = None) -> Dict[str, BoundProject]:
        production = production or self.load()
        known_units = {unit.unit_id for unit in production.units}
        found: Dict[str, BoundProject] = {}
        if not self.projects_root.is_dir():
            return found
        for project_dir in sorted(self.projects_root.iterdir()):
            if not project_dir.is_dir():
                continue
            manifest_path = project_dir / "manifest.json"
            if not manifest_path.exists():
                continue  # Never adopt or guess manifest-less directories.
            try:
                manifest = ProjectManifest.from_dict(_load_json(manifest_path))
            except Exception as exc:
                raise ValueError(
                    f"ambiguous project evidence; manifest cannot be loaded: {manifest_path}: {exc}"
                ) from exc
            binding = manifest.production_binding
            if binding is None or binding.production_id != production.production_id:
                continue
            if binding.unit_id not in known_units:
                raise ValueError(
                    f"project {project_dir.name} binds unknown unit {binding.unit_id}"
                )
            if binding.project_id != project_dir.name:
                raise ValueError(
                    "binding project directory identity mismatch: "
                    f"asserted {binding.project_id}, actual {project_dir.name}"
                )
            if binding.unit_id in found:
                raise ValueError(f"unit {binding.unit_id} is bound to multiple projects")
            plan_path = self._plan_directory(binding.unit_id) / PLAN_FILE
            meta_path = self._plan_directory(binding.unit_id) / META_FILE
            if not plan_path.is_file():
                raise ValueError(f"bound plan artifact missing: {plan_path}")
            if not meta_path.is_file():
                raise ValueError(f"bound plan metadata missing: {meta_path}")
            actual_hash = compute_content_hash(plan_path)
            if actual_hash != binding.unit_plan_content_hash:
                raise ValueError(
                    f"bound plan hash mismatch for unit {binding.unit_id}: "
                    f"expected {binding.unit_plan_content_hash}, actual {actual_hash}"
                )
            plan = ProductionPlan.model_validate(_load_json(plan_path))
            meta = _load_json(meta_path)
            expected_binding = {
                "production_id": binding.production_id,
                "unit_id": binding.unit_id,
                "production_plan_version": binding.production_plan_version,
                "unit_plan_version": binding.unit_plan_version,
                "plan_identity": binding.unit_plan_identity,
                "content_hash": binding.unit_plan_content_hash,
            }
            actual_binding = {
                "production_id": meta.get("production_id"),
                "unit_id": meta.get("unit_id"),
                "production_plan_version": meta.get("production_plan_version"),
                "unit_plan_version": meta.get("unit_plan_version"),
                "plan_identity": plan.project_id,
                "content_hash": meta.get("content_hash"),
            }
            if actual_binding != expected_binding:
                raise ValueError(
                    f"bound plan identity/version metadata mismatch for unit {binding.unit_id}"
                )
            found[binding.unit_id] = BoundProject(
                directory_id=project_dir.name,
                project_dir=project_dir,
                manifest=manifest,
            )
        return found

    def _brief_defaults(self) -> dict:
        values = self.base_config.to_content_brief_dict()
        values.pop("topic", None)
        values.pop("production_context", None)
        return values

    def resolve_plan(
        self,
        production: Production,
        unit: ProductionUnit,
        *,
        binding: Optional[BoundProject] = None,
    ) -> UnitPlanArtifacts:
        directory = self._plan_directory(unit.unit_id)
        plan_path = directory / PLAN_FILE
        brief_path = directory / BRIEF_FILE
        meta_path = directory / META_FILE
        if plan_path.is_file() or brief_path.is_file() or meta_path.is_file():
            if not all(path.is_file() for path in (plan_path, brief_path, meta_path)):
                raise ValueError(f"partial unit plan artifacts: {directory}")
            meta = _load_json(meta_path)
            actual_hash = compute_content_hash(plan_path)
            artifact_version = int(meta.get("production_plan_version", 0))
            if actual_hash != meta.get("content_hash"):
                # A stale, unbound artifact may have been interrupted while
                # being superseded. It is safe to regenerate because no
                # execution binding can refer to these bytes.
                if binding is not None or artifact_version == production.plan_version:
                    raise ValueError(f"unit plan artifact hash mismatch: {plan_path}")
            elif meta.get("production_id") != production.production_id or meta.get("unit_id") != unit.unit_id:
                raise ValueError(f"unit plan metadata identity mismatch: {meta_path}")
            elif binding is not None or artifact_version == production.plan_version:
                plan = ProductionPlan.model_validate(_load_json(plan_path))
                brief = ContentBrief.model_validate(_load_json(brief_path))
                return UnitPlanArtifacts(
                    plan, brief, plan_path, actual_hash, artifact_version,
                    int(meta.get("unit_plan_version", UNIT_PLAN_VERSION)),
                )

        brief = unit_to_content_brief(
            production, unit, defaults=self._brief_defaults()
        )
        result = self._planner_factory().plan(brief)
        if not result.success or result.plan is None:
            raise RuntimeError(result.error or f"plan generation failed for {unit.unit_id}")
        validation = validate_production_plan(result.plan, brief)
        if not validation.valid:
            raise ValueError(
                f"unit plan validation failed for {unit.unit_id}: {validation.errors}"
            )
        directory.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(plan_path, _serialize(result.plan))
        content_hash = compute_content_hash(plan_path)
        _atomic_write_text(brief_path, _serialize(brief))
        metadata = {
            "production_id": production.production_id,
            "unit_id": unit.unit_id,
            "production_plan_version": production.plan_version,
            "unit_plan_version": UNIT_PLAN_VERSION,
            "plan_identity": result.plan.project_id,
            "content_hash": content_hash,
            "generated_at": datetime.now().isoformat(),
        }
        _atomic_write_text(meta_path, json.dumps(metadata, indent=2, ensure_ascii=False))
        return UnitPlanArtifacts(
            result.plan,
            brief,
            plan_path,
            content_hash,
            production.plan_version,
        )

    @staticmethod
    def _has_remote_in_flight(manifest: ProjectManifest) -> bool:
        if any(result.status == StageStatus.RUNNING for result in manifest.stages.values()):
            return True
        for checkpoint in manifest.scene_checkpoints.values():
            checkpoint_status = str(checkpoint.get("status", "")).lower()
            if checkpoint_status in {"failed", "completed"}:
                continue
            if checkpoint.get("job_id") and checkpoint.get("polling_url"):
                return True
            evidence = checkpoint.get("checkpoint_evidence") or {}
            submission = evidence.get("submission", evidence)
            if isinstance(submission, dict) and submission.get("state") == "SUBMITTED":
                return True
        return False

    def derive_progress(self) -> ProductionExecutionReport:
        production = self.load()
        bindings = self.discover_bindings(production)
        states: Dict[str, UnitProgress] = {}
        for unit in production.units:
            bound = bindings.get(unit.unit_id)
            if bound is None:
                continue
            report = self._reconcile(
                bound.manifest.project_id, bound.project_dir, persist=False
            )
            if getattr(report, "complete", False):
                state = UnitExecutionState.COMPLETED
            elif self._has_remote_in_flight(bound.manifest):
                state = UnitExecutionState.IN_FLIGHT
            elif (
                str(getattr(getattr(report, "verdict", None), "value", ""))
                == "PROJECT_FAILED"
                or any(
                result.status == StageStatus.FAILED
                for result in bound.manifest.stages.values()
                )
            ):
                state = UnitExecutionState.FAILED
            else:
                state = UnitExecutionState.INCOMPLETE
            states[unit.unit_id] = UnitProgress(
                unit.unit_id,
                unit.ordinal,
                state,
                bound.directory_id,
                bound.manifest.production_binding.production_plan_version,
            )

        completed = {
            unit_id for unit_id, progress in states.items()
            if progress.state == UnitExecutionState.COMPLETED
        }
        for unit in production.units:
            if unit.unit_id in states:
                continue
            missing = [dep for dep in unit.dependencies if dep not in completed]
            states[unit.unit_id] = UnitProgress(
                unit.unit_id,
                unit.ordinal,
                UnitExecutionState.BLOCKED if missing else UnitExecutionState.NOT_STARTED,
                reason=f"waiting for: {', '.join(missing)}" if missing else None,
            )
        ordered = tuple(states[unit.unit_id] for unit in sorted(production.units, key=lambda u: u.ordinal))
        return ProductionExecutionReport(
            production.production_id, production.plan_version, ordered
        )

    def _config_for_unit(
        self,
        production: Production,
        unit: ProductionUnit,
        artifacts: UnitPlanArtifacts,
        *,
        bound: Optional[BoundProject],
    ) -> OrchestratorConfig:
        config = self.base_config.model_copy(deep=True)
        for key, value in artifacts.brief.model_dump().items():
            setattr(config, key, value)
        config.supplied_content_plan_path = artifacts.plan_path
        config.production_id = production.production_id
        config.unit_id = unit.unit_id
        config.production_plan_version = artifacts.production_plan_version
        config.unit_plan_identity = artifacts.plan.project_id
        config.unit_plan_version = artifacts.unit_plan_version
        config.unit_plan_content_hash = artifacts.content_hash
        config.resume_project_id = bound.directory_id if bound else None
        translate_provider_intent(unit, artifacts.plan, config)
        routes_to_flow = config.visual_mode == "google_flow" or (
            config.visual_mode == "hybrid"
            and any(provider == "google_flow" for provider in config.visual_scene_routing.values())
        )
        if routes_to_flow and not config.flow_project_url:
            raise ValueError("google_flow execution requires runtime Flow project URL")
        return config

    def _requires_flow_before_plan(self, unit: ProductionUnit) -> bool:
        """Return true when intent/runtime config already proves Flow is required."""
        if unit.provider_intent == "google_flow":
            return True
        if unit.provider_intent not in (None, "default"):
            return False
        return self.base_config.visual_mode == "google_flow" or (
            self.base_config.visual_mode == "hybrid"
            and any(
                provider == "google_flow"
                for provider in self.base_config.visual_scene_routing.values()
            )
        )

    def execute(self, *, unit_id: Optional[str] = None) -> ProductionExecutionReport:
        """Execute/continue eligible units serially; re-derive after each unit."""
        production = self.load()
        if unit_id and unit_id not in {unit.unit_id for unit in production.units}:
            raise ValueError(f"unit {unit_id!r} is not part of {production.production_id}")

        attempted: set[str] = set()
        while True:
            report = self.derive_progress()
            if report.complete:
                return report
            progress_by_id = {item.unit_id: item for item in report.units}
            bindings = self.discover_bindings(production)
            candidates: List[ProductionUnit] = []
            for unit in sorted(production.units, key=lambda item: item.ordinal):
                if unit_id and unit.unit_id != unit_id:
                    continue
                state = progress_by_id[unit.unit_id].state
                if state in {
                    UnitExecutionState.NOT_STARTED,
                    UnitExecutionState.IN_FLIGHT,
                    UnitExecutionState.INCOMPLETE,
                } and unit.unit_id not in attempted:
                    candidates.append(unit)
            if not candidates:
                return report

            unit = candidates[0]
            bound = bindings.get(unit.unit_id)
            if self._requires_flow_before_plan(unit) and not self.base_config.flow_project_url:
                raise ValueError("google_flow execution requires runtime Flow project URL")
            artifacts = self.resolve_plan(production, unit, binding=bound)
            config = self._config_for_unit(
                production, unit, artifacts, bound=bound
            )
            logger.info(
                "%s unit %s",
                "Resuming" if bound else "Executing",
                unit.unit_id,
            )
            attempted.add(unit.unit_id)
            self._orchestrator_factory(config).run()
            # Re-derive from disk; never trust process-local completion state.


__all__ = [
    "BoundProject",
    "ProductionExecutionReport",
    "ProductionExecutor",
    "UnitExecutionState",
    "UnitPlanArtifacts",
    "UnitProgress",
    "translate_provider_intent",
    "unit_to_content_brief",
]