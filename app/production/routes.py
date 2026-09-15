"""Batch 1D - first-class PRODUCTION ROUTE (product/domain concept).

A Production Route is the PRODUCT-LEVEL answer to "how is this production
intended to be made?".  It lives in the Phase 9 intent domain beside MODE
(``ProductionCommand.workload_shape``) and the Production Bible, and it is
deliberately NOT:

- a provider or model        (Alibaba / Wan / Veo / OpenRouter / Kling / ...)
- a media strategy           (AI images / stock / uploads / AI video / ...)
- an audio strategy          (narration / native audio / music / silence ...)
- a motion/video engine      (ImageMotion / ComfyUI / AI video models / ...)
- an assembly engine         (FFmpeg / ...)
- a quality/validation rule  (capability tables stay with providers/models)
- a distribution target      (publishing stays downstream)

The permanent vocabulary is PRODUCT-level (``local_ai_assisted``,
``full_ai_video``); provider/model/media/engine decisions
are made BENEATH the route at the existing execution seams.

SaaS / PRIVATE BOUNDARY (route-boundary correction):

``PRODUCTION_ROUTES`` is the SaaS PRODUCT route surface — the routes the
public/commercial architecture offers. ``google_flow`` is a PRIVATE-ONLY
capability (the owner's personal, fragile Flow adapter): it remains a fully
registered and loadable route for EXISTING/private artifacts and private
execution, but it is NOT part of ``PRODUCTION_ROUTES`` and can never be
selected for a NEW SaaS production (``assert_saas_production_route`` gates
every creation seam). Legacy artifacts carrying ``route=google_flow`` keep
loading, compiling, and revising unchanged.

``generation_strategy`` (Batch 1C) is the LEGACY / COMPATIBILITY execution
bridge.  It stays valid and loadable; new code resolves the effective route
through :func:`resolve_effective_route`:

1. the explicit ``route`` on the Production, if present;
2. otherwise the centralized legacy mapping of ``generation_strategy``;
3. otherwise the default (``local_ai_assisted``, which is exactly the
   verified legacy ``local`` behavior).

Legacy mappings carry exact EXECUTION OVERLAYS so productions created
through the Batch 1C bridge keep their verified byte-for-byte behavior
(notably ``alibaba_image_local_video``'s explicit Alibaba image source —
a MEDIA-STRATEGY decision beneath the route, never part of route identity).
Overlays apply only when the effective route is the route the legacy
strategy maps to; an explicit route choice always wins.

This module is pure intent-domain: no orchestrator imports, no execution
logic, no I/O.
"""

from __future__ import annotations

from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Route vocabulary (PRODUCT-level identity; never provider/model names)
# ---------------------------------------------------------------------------

ROUTE_LOCAL_AI_ASSISTED = "local_ai_assisted"
ROUTE_FULL_AI_VIDEO = "full_ai_video"
ROUTE_GOOGLE_FLOW = "google_flow"

#: The SaaS PRODUCT route surface (public/commercial architecture).
#: ``google_flow`` is deliberately NOT here: it is a PRIVATE-ONLY capability
#: (see ``PRIVATE_ONLY_ROUTES``) — never a SaaS product route.
PRODUCTION_ROUTES = (
    ROUTE_LOCAL_AI_ASSISTED,
    ROUTE_FULL_AI_VIDEO,
)

#: Private-only route identities. These remain registered and loadable so
#: EXISTING (legacy/private) artifacts keep working verbatim, but they are
#: not part of the SaaS product surface and can never be selected for a new
#: SaaS production. Do not extend this with future commercial routes.
PRIVATE_ONLY_ROUTES = (
    ROUTE_GOOGLE_FLOW,
)

#: Every route identity that can appear on a loadable production artifact:
#: the SaaS surface plus private-only legacy routes.
KNOWN_ROUTES = PRODUCTION_ROUTES + PRIVATE_ONLY_ROUTES


def assert_saas_production_route(route: Optional[str]) -> None:
    """Gate every NEW-production creation seam to the SaaS route surface.

    ``None`` (legacy default resolution) passes. A SaaS route passes. A
    private-only route (``google_flow``) or an unknown value raises — Google
    Flow remains a private capability/adapter and is never a SaaS product
    route. Loading/revising EXISTING artifacts is intentionally NOT gated
    here: legacy private productions keep loading, compiling, and revising.
    """
    if route is None:
        return
    if route in PRIVATE_ONLY_ROUTES:
        raise ValueError(
            f"route {route!r} is a private-only capability, not a SaaS "
            "product route (SaaS routes: "
            + ", ".join(PRODUCTION_ROUTES) + ")"
        )
    if route not in PRODUCTION_ROUTES:
        raise ValueError(
            "route must be one of: " + ", ".join(PRODUCTION_ROUTES)
            + f" (got {route!r})"
        )

#: Default route == the verified legacy default behavior (strategy "local").
DEFAULT_PRODUCTION_ROUTE = ROUTE_LOCAL_AI_ASSISTED

# ---------------------------------------------------------------------------
# Execution surfaces
# ---------------------------------------------------------------------------

#: The existing five-stage scene pipeline (CONTENT -> VOICE -> SCENES ->
#: CAPTIONS -> ASSEMBLY).  Future routes (e.g. long-form repurposing,
#: upload -> AI narration) will register their own surfaces; this batch
#: deliberately does NOT implement them.
EXECUTION_SURFACE_STANDARD_VIDEO = "standard_video"

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class RouteDefinition(BaseModel):
    """One registered Production Route (frozen; authored only here).

    ``route_version`` is the smallest viable fingerprint: when the meaning or
    compilation of a route changes materially in a future batch, its version
    increments so persisted productions remain explainable.
    """

    model_config = ConfigDict(frozen=True)

    route_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    execution_surface: str = EXECUTION_SURFACE_STANDARD_VIDEO
    route_version: int = Field(default=1, ge=1)
    # Requirement/forbidden-behavior preparation ONLY (Batch 1D foundation).
    # Enforcement stays with the existing adapters/tests until a later
    # increment wires route requirements into planning/validation.
    requires_native_motion: bool = False
    forbids_still_image_substitution: bool = False
    allows_local_motion_engine: bool = True


ROUTE_REGISTRY: Dict[str, RouteDefinition] = {
    ROUTE_LOCAL_AI_ASSISTED: RouteDefinition(
        route_id=ROUTE_LOCAL_AI_ASSISTED,
        label="Local AI-Assisted",
        description=(
            "AI-assisted local production: imagery is acquired through the "
            "existing media-strategy rules (auto routing layer or an "
            "explicit source; user assets always win), the local motion "
            "engine animates stills, and FFmpeg assembles the final video. "
            "Providers/models are resolved beneath the route."
        ),
    ),
    ROUTE_FULL_AI_VIDEO: RouteDefinition(
        route_id=ROUTE_FULL_AI_VIDEO,
        label="Full AI Video",
        description=(
            "Scenes are generated as native motion video by an AI video "
            "model resolved beneath the route (currently the existing "
            "alibaba_video adapter seam). A silent still-image substitution "
            "is forbidden: unavailability fails truthfully."
        ),
        requires_native_motion=True,
        forbids_still_image_substitution=True,
        allows_local_motion_engine=False,
    ),
    ROUTE_GOOGLE_FLOW: RouteDefinition(
        route_id=ROUTE_GOOGLE_FLOW,
        label="Google Flow",
        description=(
            "PRIVATE-ONLY capability (never a SaaS product route): scenes "
            "are produced through the existing Google Flow execution "
            "surface (interactive workflow per scene). This route registers "
            "the private product identity only; Flow execution is unchanged. "
            "Registered so existing/private artifacts keep loading and "
            "compiling verbatim."
        ),
        requires_native_motion=True,
        forbids_still_image_substitution=True,
        allows_local_motion_engine=False,
    ),
}


def get_route_definition(route_id: str) -> RouteDefinition:
    """Return the registered definition; unknown routes fail loudly."""
    key = (route_id or "").strip()
    definition = ROUTE_REGISTRY.get(key)
    if definition is None:
        raise ValueError(
            "route must be one of: "
            + ", ".join(KNOWN_ROUTES)
            + f" (got {route_id!r})"
        )
    return definition


def is_known_route(route_id: Optional[str]) -> bool:
    """Whether one (already normalized) route id is registered."""
    return bool(route_id) and (route_id in ROUTE_REGISTRY)


# ---------------------------------------------------------------------------
# Legacy generation_strategy compatibility mapping (centralized)
# ---------------------------------------------------------------------------


class LegacyStrategyRouteMapping(BaseModel):
    """The centralized legacy ``generation_strategy`` -> route mapping.

    ``execution_overlays`` are the EXACT execution-configuration mutations
    the Batch 1C bridge performed for that strategy.  They are applied by the
    route compiler only when the effective route is the route this strategy
    maps to, so pre-route productions keep their verified behavior.
    """

    model_config = ConfigDict(frozen=True)

    strategy: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    execution_overlays: Dict[str, str] = Field(default_factory=dict)


LEGACY_GENERATION_STRATEGY_ROUTES: Dict[str, LegacyStrategyRouteMapping] = {
    "local": LegacyStrategyRouteMapping(
        strategy="local",
        route_id=ROUTE_LOCAL_AI_ASSISTED,
    ),
    "alibaba_image_local_video": LegacyStrategyRouteMapping(
        strategy="alibaba_image_local_video",
        # Product-level route: local AI-assisted production.  The explicit
        # Alibaba image source is a MEDIA-STRATEGY decision beneath the
        # route, preserved verbatim for compatibility.
        route_id=ROUTE_LOCAL_AI_ASSISTED,
        execution_overlays={"image_source": "alibaba"},
    ),
    "alibaba_video": LegacyStrategyRouteMapping(
        strategy="alibaba_video",
        route_id=ROUTE_FULL_AI_VIDEO,
    ),
}


def route_id_for_legacy_strategy(strategy: str) -> str:
    """Map one legacy strategy to its route id; unknown values fail loudly."""
    mapping = LEGACY_GENERATION_STRATEGY_ROUTES.get((strategy or "").strip())
    if mapping is None:
        raise ValueError(
            "unknown generation strategy: "
            f"{strategy!r} (expected one of: "
            + ", ".join(LEGACY_GENERATION_STRATEGY_ROUTES)
            + ")"
        )
    return mapping.route_id


# ---------------------------------------------------------------------------
# Effective-route resolution (precedence: explicit route > legacy > default)
# ---------------------------------------------------------------------------

ROUTE_SOURCE_EXPLICIT = "explicit_route"
ROUTE_SOURCE_LEGACY = "legacy_strategy"


class EffectiveRoute(BaseModel):
    """The resolved route for one production, plus its resolution evidence."""

    model_config = ConfigDict(frozen=True)

    route_id: str
    source: str
    legacy_strategy: Optional[str] = None
    definition: RouteDefinition

    @property
    def compat_overlays(self) -> Dict[str, str]:
        """Exact legacy execution overlays for this production (or {}).

        Applied only when the effective route is the route the legacy
        strategy maps to — an explicit route choice is never overridden by
        legacy overlays (and overlays for a different route are irrelevant).
        """
        if not self.legacy_strategy:
            return {}
        mapping = LEGACY_GENERATION_STRATEGY_ROUTES.get(self.legacy_strategy)
        if mapping is None or mapping.route_id != self.route_id:
            return {}
        return dict(mapping.execution_overlays)


def resolve_effective_route(
    *,
    route: Optional[str] = None,
    generation_strategy: Optional[str] = None,
) -> EffectiveRoute:
    """Resolve the effective Production Route (never reroutes silently).

    - unknown legacy strategies fail loudly (mirrors the Batch 1C bridge);
    - unknown explicit routes fail loudly;
    - a missing route + missing/legacy-default strategy resolves to the
      default local route (the verified legacy behavior).
    """
    strategy = (generation_strategy or "").strip() or "local"
    mapping = LEGACY_GENERATION_STRATEGY_ROUTES.get(strategy)
    if mapping is None:
        raise ValueError(
            "unknown generation strategy: "
            f"{strategy!r} (expected one of: "
            + ", ".join(LEGACY_GENERATION_STRATEGY_ROUTES)
            + ")"
        )
    if route:
        definition = get_route_definition(route)
        return EffectiveRoute(
            route_id=definition.route_id,
            source=ROUTE_SOURCE_EXPLICIT,
            legacy_strategy=strategy,
            definition=definition,
        )
    definition = ROUTE_REGISTRY[mapping.route_id]
    return EffectiveRoute(
        route_id=definition.route_id,
        source=ROUTE_SOURCE_LEGACY,
        legacy_strategy=strategy,
        definition=definition,
    )


__all__ = [
    "ROUTE_LOCAL_AI_ASSISTED",
    "ROUTE_FULL_AI_VIDEO",
    "ROUTE_GOOGLE_FLOW",
    "PRODUCTION_ROUTES",
    "PRIVATE_ONLY_ROUTES",
    "KNOWN_ROUTES",
    "DEFAULT_PRODUCTION_ROUTE",
    "EXECUTION_SURFACE_STANDARD_VIDEO",
    "RouteDefinition",
    "ROUTE_REGISTRY",
    "get_route_definition",
    "is_known_route",
    "assert_saas_production_route",
    "LegacyStrategyRouteMapping",
    "LEGACY_GENERATION_STRATEGY_ROUTES",
    "route_id_for_legacy_strategy",
    "ROUTE_SOURCE_EXPLICIT",
    "ROUTE_SOURCE_LEGACY",
    "EffectiveRoute",
    "resolve_effective_route",
]
