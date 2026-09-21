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
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from app.content.models import ContentBrief, ProductionPlan
from app.content.planner import ScenePlanner, create_scene_planner
from app.content.validator import validate_production_plan
from app.orchestrator.models import (
    OrchestratorConfig,
    ProjectManifest,
    StageStatus,
)
from app.production.models import Production, ProductionUnit
from app.production.routes import (
    ROUTE_FULL_AI_VIDEO,
    ROUTE_GOOGLE_FLOW,
    ROUTE_LOCAL_AI_ASSISTED,
    EffectiveRoute,
    resolve_effective_route,
)
from app.orchestrator.planning_constraints import (
    resolve_planning_constraints_for_production,
)
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
    # P0-01: raw parsed manifest.json dict, present only when produced by the
    # shared collection scan (derive_progress_many). Lets the batch path hand
    # reconcile_project the exact same bytes without a re-read. Never serialized.
    manifest_raw: Optional[dict] = None


@dataclass(frozen=True)
class UnitProgress:
    unit_id: str
    ordinal: int
    state: UnitExecutionState
    project_id: Optional[str] = None
    bound_plan_version: Optional[int] = None
    reason: Optional[str] = None
    # P0-02: additive, defaults False. True only when the unit is IN_FLIGHT and
    # its durable in-flight evidence is older than the longest provider-declared
    # polling window, so no live run can still own it. The six-state vocabulary
    # is deliberately unchanged — this DISCLOSES staleness instead of silently
    # reclassifying a state other readers depend on.
    stale_in_flight: bool = False


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


def _parse_stamp(value: object) -> Optional[datetime]:
    """Parse a naive Core ISO stamp, or None when it is unusable.

    Core timestamps carry no timezone, so they are compared against naive local
    time exactly as the rest of Core treats them. An unparseable stamp yields
    None so a staleness claim is never made from a value we could not read.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


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


def apply_generation_strategy(
    production: Production,
    config: OrchestratorConfig,
    plan: ProductionPlan,
) -> None:
    """LEGACY (Batch 1C bridge): apply the persisted GENERATION STRATEGY.

    Superseded as the master abstraction by the first-class Production Route
    (Batch 1D): :func:`apply_production_route` now owns this seam's
    responsibility, and ``_config_for_unit`` resolves the effective route.
    This bridge is RETAINED unchanged for backwards compatibility: existing
    productions and the frozen Batch 1C behavioral contract keep working,
    and route-compilation equivalence is tested against it.

    This is a pure translation (the same seam ``translate_provider_intent``
    uses): it only re-points the EXISTING routing/source configuration; it
    never builds providers, touches credentials, or performs requests.

    - ``local`` (default): no-op — the existing local pipeline is preserved
      byte-for-byte, including every legacy production that lacks the field.
    - ``alibaba_image_local_video``: scene IMAGES come from the existing
      Alibaba image source (``AlibabaImageSource`` via the single explicit
      ``alibaba`` selector — a configured-but-failed or unconfigured source
      fails truthfully with NO silent fallthrough), while the downstream
      local video/assembly pipeline is untouched.
    - ``alibaba_video``: every scene routes to the existing Batch 1C
      ``alibaba_video`` adapter (DashScope Wan t2v, model-aware size
      selection, async submit/poll/download). A user who selects this NEVER
      silently receives static-image Ken-Burns motion: the routing is
      explicit and unavailability fails truthfully.

    Mock mode is deliberately excluded: the explicit offline/test mode stays
    fully hermetic (fixture imagery, mock planner/voice) regardless of
    strategy.
    """
    strategy = getattr(production, "generation_strategy", None) or (
        "local"
    )
    if config.mock_mode or strategy == "local":
        return
    if strategy == "alibaba_image_local_video":
        config.image_source = "alibaba"
        return
    if strategy == "alibaba_video":
        config.visual_mode = "hybrid"
        config.visual_default_provider = "alibaba_video"
        config.visual_scene_routing = {
            scene.id: "alibaba_video" for scene in plan.scenes
        }
        return
    raise ValueError(
        f"unknown generation strategy: {strategy!r} "
        "(expected one of: local, alibaba_image_local_video, alibaba_video)"
    )


@dataclass(frozen=True)
class RouteCompilation:
    """Batch 1D: the result of compiling one Production Route at the seam.

    Route is INTENT; ``OrchestratorConfig`` remains the execution
    configuration.  This record is evidence of what the route compiled into
    (identity, version, source, surface, legacy overlays) so a production
    stays explainable after execution.
    """

    route_id: str
    route_version: int
    label: str
    source: str
    execution_surface: str
    legacy_strategy: Optional[str]
    requires_native_motion: bool
    overlays_applied: Tuple[str, ...]


def apply_production_route(
    config: OrchestratorConfig,
    *,
    effective_route: EffectiveRoute,
    plan: ProductionPlan,
) -> RouteCompilation:
    """Batch 1D: compile the first-class PRODUCTION ROUTE into the EXISTING
    execution configuration at the same seam the legacy generation-strategy
    bridge owned (``translate_provider_intent`` → route compilation).

    Pure translation: re-points the EXISTING routing/source configuration;
    never builds providers, touches credentials, or performs requests.

    Architectural invariants preserved here:

    - Route ≠ provider/model: ``full_ai_video`` compiles onto the CURRENT
      ``alibaba_video`` provider seam (the verified Batch 1C behavior) while
      naming no provider/model in its identity; a later provider-resolution
      layer will own this mapping.
    - Route ≠ media strategy: ``local_ai_assisted`` leaves media sourcing to
      the existing media-strategy configuration (``image_source``); legacy
      compat overlays reproduce the exact Batch 1C bridge behavior for
      pre-route productions.
    - Route ≠ engine: the compiler only re-points the existing routing map;
      per-scene engine selection and ImageMotion behavior are unchanged.
    - Route ≠ audio: no audio policy is read or written here.
    - Mock mode is deliberately excluded (fully hermetic), exactly like the
      legacy bridge.
    """
    definition = effective_route.definition
    overlays: Tuple[str, ...] = ()
    if not config.mock_mode:
        if definition.route_id == ROUTE_FULL_AI_VIDEO:
            # Native motion video for every scene, via the CURRENT provider
            # seam beneath the route (see invariant note above).  A user who
            # selects this route NEVER silently receives still-image
            # Ken-Burns motion: routing is explicit and unavailability fails
            # truthfully downstream.
            config.visual_mode = "hybrid"
            config.visual_default_provider = "alibaba_video"
            config.visual_scene_routing = {
                scene.id: "alibaba_video" for scene in plan.scenes
            }
        elif definition.route_id == ROUTE_GOOGLE_FLOW:
            # Existing Google Flow execution surface — no Flow redesign, no
            # browser/CDP/session changes.  The pipeline's Flow path and the
            # runtime flow_project_url validation remain authoritative.
            config.visual_mode = "google_flow"
        elif definition.route_id == ROUTE_LOCAL_AI_ASSISTED:
            # The verified local path, byte-for-byte: media sourcing stays
            # with the media-strategy configuration; the local engine and
            # FFmpeg assembly are untouched.
            pass
        else:  # pragma: no cover - the registry validates route ids upstream
            raise ValueError(
                f"unknown production route: {definition.route_id!r} "
                "(expected one of the registered Production Routes)"
            )
        # Legacy compatibility overlays (exact Batch 1C bridge behavior for
        # productions whose route came from the legacy strategy).  Applied
        # only when the mapped route equals the effective route, so an
        # explicit route choice always wins.
        for key, value in effective_route.compat_overlays.items():
            setattr(config, key, value)
            overlays = (*overlays, key)
    return RouteCompilation(
        route_id=definition.route_id,
        route_version=definition.route_version,
        label=definition.label,
        source=effective_route.source,
        execution_surface=definition.execution_surface,
        legacy_strategy=effective_route.legacy_strategy,
        requires_native_motion=definition.requires_native_motion,
        overlays_applied=overlays,
    )


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
        if not self.projects_root.is_dir():
            return {}
        scan: Dict[str, Tuple[Path, ProjectManifest]] = {}
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
            scan[project_dir.name] = (project_dir, manifest, None)
        return self.discover_bindings_from_scan(production, scan)

    def discover_bindings_from_scan(
        self,
        production: Production,
        scan: Mapping[str, Tuple[Path, ProjectManifest, Optional[dict]]],
    ) -> Dict[str, BoundProject]:
        """Binding verification against a pre-scanned projects tree.

        Identical logic to ``discover_bindings``; it simply reuses the parsed
        manifests so a collection read does not re-scan the tree per
        production (P0-01). The same ValueError guards are preserved.
        """
        known_units = {unit.unit_id for unit in production.units}
        found: Dict[str, BoundProject] = {}
        for directory_name in sorted(scan):
            project_dir, manifest, manifest_raw = scan[directory_name]
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
                manifest_raw=manifest_raw,
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
        # Batch 1E: capability-aware planning.  The effective route's generic
        # requirements plus provider-owned capability facts become generic
        # PlanningConstraints on the PLANNING INPUT only — never execution
        # configuration, never ProductionPlan.  Mock mode stays inert, and
        # the resolution reads local capability metadata only (never a
        # network call, never provider selection).
        constraints = None
        if not self.base_config.mock_mode:
            constraints = resolve_planning_constraints_for_production(production)
        if constraints is not None:
            brief = brief.model_copy(update={"planning_constraints": constraints})
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
            # Phase 11C Batch 1: truthful intelligence provenance — which
            # provider/model produced this plan (None for the explicit mock
            # planner, which must never masquerade as AI production).
            "planner_provider": result.provider,
            "planner_model": result.model,
            # Phase 11C Batch 1 hardening: durable record of planner-level
            # warnings (e.g. the duration-preserving scene-duration schema
            # normalization). Additive provenance; never execution state.
            "planner_warnings": list(result.warnings or []),
            # Batch 1E: generic capability-aware planning evidence — recorded
            # ONLY when constraints were actually applied.  Generic shape;
            # never provider/model identity; never execution state.
            **(
                {
                    "planning_constraints": {
                        "source": constraints.source,
                        "allowed_scene_durations": list(
                            constraints.allowed_scene_durations or ()
                        ),
                    }
                }
                if constraints is not None
                else {}
            ),
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
    def _in_flight_evidence(manifest: ProjectManifest) -> Tuple[bool, Optional[str]]:
        """Durable in-flight evidence: ``(is_in_flight, newest_evidence_stamp)``.

        P0-02 also fixes a vocabulary drift here. Task identity is kind-aware in
        the read API (server.py ``task_identity_present`` accepts ``job_id`` OR
        ``task_id``), but this check required ``job_id`` AND ``polling_url`` —
        both OpenRouter-only fields. Alibaba DashScope persists ``task_id`` with
        no ``polling_url``, so an Alibaba in-flight job was never detected as
        in flight. Detection now follows the same kind-aware convention.
        """
        in_flight = False
        newest: Optional[str] = None

        def note(stamp: object) -> None:
            nonlocal newest
            if isinstance(stamp, str) and stamp.strip():
                if newest is None or stamp > newest:
                    newest = stamp

        for result in manifest.stages.values():
            if result.status == StageStatus.RUNNING:
                in_flight = True
                note(result.started_at)

        for checkpoint in manifest.scene_checkpoints.values():
            checkpoint_status = str(checkpoint.get("status", "")).lower()
            if checkpoint_status in {"failed", "completed"}:
                continue
            evidence = checkpoint.get("checkpoint_evidence") or {}
            if not isinstance(evidence, dict):
                evidence = {}
            submission = evidence.get("submission", evidence)
            has_task_identity = bool(
                checkpoint.get("job_id")
                or checkpoint.get("task_id")
                or evidence.get("job_id")
                or evidence.get("task_id")
            )
            was_submitted = (
                isinstance(submission, dict) and submission.get("state") == "SUBMITTED"
            )
            if has_task_identity or was_submitted:
                in_flight = True
                note(checkpoint.get("submitted_at") or checkpoint.get("updated_at"))

        if in_flight and newest is None:
            # No per-item stamp: fall back to the manifest's own last write so a
            # staleness decision is still possible rather than unbounded.
            note(getattr(manifest, "updated_at", None))

        return in_flight, newest

    @staticmethod
    def _declared_remote_window_seconds() -> float:
        """Longest provider-DECLARED bounded polling window, in seconds.

        Every remote provider bounds its own polling and then fails truthfully
        (e.g. alibaba_video ``DEFAULT_POLL_INTERVAL * DEFAULT_POLL_MAX_ATTEMPTS``
        = 10s x 60 = 600s; openrouter_video 30s x 20 = 600s; alibaba_image
        2s x 60 = 120s). Evidence older than the LONGEST of those windows cannot
        belong to a run that is still polling, because that run would already
        have failed. The horizon is therefore derived from the providers' own
        declared constants — never an invented threshold.
        """
        windows: List[float] = []
        try:
            from app.orchestrator.alibaba_video_config import (
                DEFAULT_POLL_INTERVAL,
                DEFAULT_POLL_MAX_ATTEMPTS,
            )

            windows.append(float(DEFAULT_POLL_INTERVAL) * float(DEFAULT_POLL_MAX_ATTEMPTS))
        except Exception:  # pragma: no cover - defensive import guard
            pass
        try:
            from app.orchestrator.openrouter_video import (
                OPENROUTER_DEFAULT_POLL_INTERVAL,
                OPENROUTER_DEFAULT_POLL_MAX_ATTEMPTS,
            )

            windows.append(
                float(OPENROUTER_DEFAULT_POLL_INTERVAL)
                * float(OPENROUTER_DEFAULT_POLL_MAX_ATTEMPTS)
            )
        except Exception:  # pragma: no cover - defensive import guard
            pass
        try:
            from app.orchestrator.alibaba_image import (
                DEFAULT_ALIBABA_IMAGE_POLL_INTERVAL,
                DEFAULT_ALIBABA_IMAGE_POLL_MAX_ATTEMPTS,
            )

            windows.append(
                float(DEFAULT_ALIBABA_IMAGE_POLL_INTERVAL)
                * float(DEFAULT_ALIBABA_IMAGE_POLL_MAX_ATTEMPTS)
            )
        except Exception:  # pragma: no cover - defensive import guard
            pass
        return max(windows) if windows else 600.0

    @classmethod
    def _remote_in_flight_staleness(
        cls, manifest: ProjectManifest, *, now: Optional[datetime] = None
    ) -> Tuple[bool, bool, Optional[str]]:
        """``(in_flight, stale, reason)`` for one bound project's manifest.

        Read-only: it inspects durable evidence and never mutates the manifest,
        so a GET can never repair or reset a stage.
        """
        in_flight, newest = cls._in_flight_evidence(manifest)
        if not in_flight:
            return False, False, None

        stamp = _parse_stamp(newest)
        if stamp is None:
            # No usable timestamp: staleness cannot be PROVEN, so it is not
            # claimed. Absence of evidence is reported as unknown, not stale.
            return True, False, None

        window = cls._declared_remote_window_seconds()
        age = (now or datetime.now()) - stamp
        if age > timedelta(seconds=window):
            return True, True, (
                f"in-flight evidence is {int(age.total_seconds())}s old, beyond the "
                f"longest provider-declared polling window ({int(window)}s); no live "
                "run can still be polling this job, so the state is stale/orphaned "
                f"(evidence timestamp: {newest})"
            )
        return True, False, None

    @classmethod
    def _has_remote_in_flight(cls, manifest: ProjectManifest) -> bool:
        return cls._in_flight_evidence(manifest)[0]

    def _unit_progress_from(self, unit, bound: BoundProject, report) -> UnitProgress:
        """Single-unit state derivation — the exact logic formerly inline in
        ``derive_progress``. Extracted so the single-production and batch paths
        share it byte-for-byte."""
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
        stale = False
        reason: Optional[str] = None
        if state == UnitExecutionState.IN_FLIGHT:
            # P0-02: disclose — do not reclassify. The state stays IN_FLIGHT so
            # the six-state vocabulary and resume eligibility are untouched; the
            # staleness is carried alongside it with a human-readable reason.
            _, stale, reason = self._remote_in_flight_staleness(bound.manifest)
        return UnitProgress(
            unit.unit_id,
            unit.ordinal,
            state,
            bound.directory_id,
            bound.manifest.production_binding.production_plan_version,
            reason=reason,
            stale_in_flight=stale,
        )

    def _finalize_progress(
        self, production: Production, states: Dict[str, UnitProgress]
    ) -> ProductionExecutionReport:
        """Dependency-completion + ordering, shared by both derivation paths."""
        completed = {
            unit_id
            for unit_id, progress in states.items()
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
        ordered = tuple(
            states[unit.unit_id]
            for unit in sorted(production.units, key=lambda u: u.ordinal)
        )
        return ProductionExecutionReport(
            production.production_id, production.plan_version, ordered
        )

    def _derive_progress_report(
        self, production: Production, bindings: Dict[str, BoundProject]
    ) -> ProductionExecutionReport:
        states: Dict[str, UnitProgress] = {}
        for unit in production.units:
            bound = bindings.get(unit.unit_id)
            if bound is None:
                continue
            # ``manifest_raw`` is only forwarded when a shared scan supplied it
            # (the P0-01 batch path). Otherwise the call is byte-identical to the
            # original, which also keeps injected reconcile stubs — the existing
            # test seam — working unchanged.
            if bound.manifest_raw is not None:
                report = self._reconcile(
                    bound.manifest.project_id,
                    bound.project_dir,
                    persist=False,
                    manifest_raw=bound.manifest_raw,
                )
            else:
                report = self._reconcile(
                    bound.manifest.project_id, bound.project_dir, persist=False
                )
            states[unit.unit_id] = self._unit_progress_from(unit, bound, report)
        return self._finalize_progress(production, states)

    def derive_progress(self) -> ProductionExecutionReport:
        production = self.load()
        bindings = self.discover_bindings(production)
        return self._derive_progress_report(production, bindings)

    @staticmethod
    def derive_progress_many(
        production_ids: Iterable[str],
        *,
        productions_root: Optional[Path] = None,
        projects_root: Optional[Path] = None,
        reconcile: Optional[Callable[..., object]] = None,
    ) -> Dict[str, object]:
        """Batch progress derivation for the production collection.

        P0-01: per-production ``derive_progress()`` calls ``discover_bindings()``,
        which re-scans the ENTIRE projects tree (parse + content-hash every
        project manifest) once per production. With P productions and Q
        projects that is O(P*Q) manifest parses — the measured ~28 s on
        ``GET /api/productions``.

        This method performs ONE shared scan of the projects tree, then reuses
        the exact ``discover_bindings`` per-production binding verification and
        the exact ``reconcile_project`` call per bound project, so ``counts``,
        ``complete`` and the ``PROJECT_FAILED`` verdict are byte-identical to
        the single-production path. No counts-only shortcut; no caching.

        Malformed productions are not pre-validated here: the caller keeps the
        existing per-row ``_production_or_none`` error handling, and a failed
        derivation for one id leaves its entry simply absent (the caller adds
        its own truthful ``counts: None`` / ``error`` row, as today).
        """
        from app.orchestrator.reconciliation import reconcile_project

        reconcile_fn = reconcile or reconcile_project
        ids = list(production_ids)
        if not ids:
            return {}

        probe = ProductionExecutor(
            ids[0], productions_root=productions_root, projects_root=projects_root,
        )

        # One pass over the projects tree: parse each manifest once and keep
        # both the validated manifest and its raw dict, so every downstream
        # consumer sees byte-identical content.
        manifests_by_dir: Dict[str, Tuple[Path, ProjectManifest, dict]] = {}
        if probe.projects_root.is_dir():
            for project_dir in sorted(probe.projects_root.iterdir()):
                if not project_dir.is_dir():
                    continue
                manifest_path = project_dir / "manifest.json"
                if not manifest_path.exists():
                    continue
                raw = _load_json(manifest_path)
                manifest = ProjectManifest.from_dict(raw)
                manifests_by_dir[project_dir.name] = (project_dir, manifest, raw)

        results: Dict[str, object] = {}
        for production_id in ids:
            production = load_production(production_id, root=productions_root)
            executor = ProductionExecutor(
                production_id,
                productions_root=productions_root,
                projects_root=projects_root,
                reconcile=reconcile_fn,
            )
            bindings = executor.discover_bindings_from_scan(production, manifests_by_dir)
            states: Dict[str, UnitProgress] = {}
            for unit in production.units:
                bound = bindings.get(unit.unit_id)
                if bound is None:
                    continue
                if bound.manifest_raw is not None:
                    report = reconcile_fn(
                        bound.manifest.project_id,
                        bound.project_dir,
                        persist=False,
                        manifest_raw=bound.manifest_raw,
                    )
                else:
                    report = reconcile_fn(
                        bound.manifest.project_id, bound.project_dir, persist=False
                    )
                states[unit.unit_id] = executor._unit_progress_from(
                    unit, bound, report
                )
            results[production_id] = executor._finalize_progress(production, states)
        return results


    def _config_for_unit(
        self,
        production: Production,
        unit: ProductionUnit,
        artifacts: UnitPlanArtifacts,
        *,
        bound: Optional[BoundProject],
    ) -> OrchestratorConfig:
        config = self.base_config.model_copy(deep=True)
        brief_values = artifacts.brief.model_dump()
        # Batch 1E boundary: PlanningConstraints are PLANNING INPUT only and
        # must never leak into execution configuration.
        brief_values.pop("planning_constraints", None)
        for key, value in brief_values.items():
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
        # Batch 1D: the first-class PRODUCTION ROUTE is compiled at this
        # seam, replacing the Batch 1C generation-strategy bridge's
        # responsibility.  Precedence: explicit route > legacy
        # generation_strategy > default local; the centralized legacy compat
        # overlays keep pre-route productions byte-for-byte.  The legacy
        # bridge (apply_generation_strategy) remains available unchanged.
        effective = resolve_effective_route(
            route=production.route,
            generation_strategy=production.generation_strategy,
        )
        compilation = apply_production_route(
            config, effective_route=effective, plan=artifacts.plan
        )
        logger.info(
            "Route compiled: %s (v%s, %s; source=%s; surface=%s%s)",
            compilation.route_id,
            compilation.route_version,
            compilation.label,
            compilation.source,
            compilation.execution_surface,
            f"; overlays={list(compilation.overlays_applied)}"
            if compilation.overlays_applied
            else "",
        )
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
                "production.execution_started production=%s unit=%s "
                "route=%s legacy_strategy=%s action=%s",
                production.production_id,
                unit.unit_id,
                production.route or "not_specified",
                production.generation_strategy,
                "resume" if bound else "execute",
            )
            attempted.add(unit.unit_id)
            self._orchestrator_factory(config).run()
            # Re-derive from disk; never trust process-local completion state.


__all__ = [
    "BoundProject",
    "ProductionExecutionReport",
    "ProductionExecutor",
    "RouteCompilation",
    "UnitExecutionState",
    "UnitPlanArtifacts",
    "UnitProgress",
    "apply_generation_strategy",
    "apply_production_route",
    "translate_provider_intent",
    "unit_to_content_brief",
]