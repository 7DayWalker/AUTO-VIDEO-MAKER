"""Batch 1E - Route requirement -> capability-aware planning constraints.

The pure seam between:

- Production Route requirements (GENERIC, provider-agnostic — Batch 1D);
- provider-owned model capability FACTS (projected by each video provider
  adapter from its OWN existing capability sources; never re-registered
  here); and
- generic ``PlanningConstraints`` attached to the planning brief.

Architectural invariants:

- Route never learns provider/model specifics; the converter only reads the
  generic ``requires_native_motion`` requirement flag.
- The planner (content layer) never imports or depends on Alibaba/OpenRouter
  adapter classes; it only ever sees generic ``PlanningConstraints``.
- No provider selection, ranking, fallback, cost/latency/quality logic, and
  NO network calls: every fact is read from already-configured local
  metadata (capability tables / cached catalogs).
- The provider adapters remain the FINAL authority at generation time; these
  constraints are advisory planning input.

Fact resolution order mirrors the EXISTING fixed provider policy (Batch 1C):
Alibaba video primary first; the OpenRouter video fallback's facts only when
Alibaba yields none (e.g. unconfigured).  This is a READ of current
configuration state — it never selects or changes execution behavior.
"""

from __future__ import annotations

from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict

from app.content.models import PlanningConstraints
from app.production.routes import RouteDefinition, resolve_effective_route

__all__ = [
    "ModelCapabilityFacts",
    "resolve_planning_constraints",
    "resolve_video_model_capability_facts",
    "resolve_planning_constraints_for_production",
]


class ModelCapabilityFacts(BaseModel):
    """Generic, provider-anonymous capability facts for one resolved model.

    Projected by the owning provider adapter from its own capability source
    (Alibaba: ``alibaba_video_config.model_capabilities``; OpenRouter: its
    cached video-model catalog).  ``source`` stays generic on purpose.
    """

    model_config = ConfigDict(frozen=True)

    supported_durations: Optional[Tuple[int, ...]] = None
    source: str = "model_capability"


def resolve_planning_constraints(
    route_definition: RouteDefinition,
    capability_facts: Optional[ModelCapabilityFacts],
) -> Optional[PlanningConstraints]:
    """Convert generic route requirements + capability facts into generic
    planning constraints.

    Returns constraints ONLY when the route requires native motion video AND
    authoritative supported durations are known.  Everything else (local
    route, Google Flow without an authoritative capability source, unknown
    models) honestly yields ``None`` — never an invented default.
    """
    if route_definition is None or not route_definition.requires_native_motion:
        return None
    if capability_facts is None:
        return None
    durations = tuple(
        sorted({int(d) for d in (capability_facts.supported_durations or ()) if int(d) > 0})
    )
    if not durations:
        return None
    return PlanningConstraints(
        allowed_scene_durations=durations,
        source=capability_facts.source,
    )


def resolve_video_model_capability_facts() -> Optional[ModelCapabilityFacts]:
    """Read generic capability facts for the video model that would
    currently execute, in the existing fixed policy order.

    READ-ONLY: local capability metadata only (never a network call, never
    provider selection or ranking).  Adapters are imported lazily so pure
    planning-side imports stay light.
    """
    from app.orchestrator.alibaba_video import alibaba_video_planning_facts

    facts = alibaba_video_planning_facts()
    if facts is not None:
        return facts
    # Alibaba yielded no facts (e.g. unconfigured): the existing policy's
    # fallback source is the OpenRouter video adapter.
    from app.orchestrator.openrouter_video import (
        openrouter_video_planning_facts,
    )

    return openrouter_video_planning_facts()


def resolve_planning_constraints_for_production(
    production,
) -> Optional[PlanningConstraints]:
    """Convenience seam used by ``ProductionExecutor.resolve_plan``:
    resolve the production's effective route (Batch 1D precedence) and
    compile its generic requirements + capability facts into constraints.
    """
    effective = resolve_effective_route(
        route=getattr(production, "route", None),
        generation_strategy=production.generation_strategy,
    )
    facts = resolve_video_model_capability_facts()
    return resolve_planning_constraints(effective.definition, facts)
