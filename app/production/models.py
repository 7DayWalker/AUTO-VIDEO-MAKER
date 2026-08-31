"""
AUTO Video Maker - Phase 9: Durable Production Plan (INTENT layer models).

The Production is the durable workload-level intent artifact: what AVM intends
to produce. It is deliberately separated from the execution layer
(ProjectManifest / scene_checkpoints / SceneManifest / publication records),
which describes what AVM has actually done.

Frozen authority (PHASE_9_DURABLE_PRODUCTION_PLAN.md):

- The Production Unit is the neutral workload primitive. Episode is optional
  metadata on a unit — never the architectural primitive.
- The Production Bible is an OPTIONAL first-class shared-creative-context
  component with canonical, extensible sections.
- Single-video, batch, and episodic productions use the same schema.
- Provider/routing intent is neutral provider NAMES only — no transport,
  sessions, job state, or credentials ever enter this layer.
- ``schema_version`` (structure compatibility) and ``plan_version`` (intent
  revision history) are separate concepts.

This module contains NO execution logic and invokes NO providers.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_NAME = "avm.production"

#: Structure compatibility: only this major schema version is supported.
SUPPORTED_SCHEMA_VERSION = 1

#: Neutral provider names allowed as routing INTENT (name-level only).
PROVIDER_INTENT_NAMES = (
    "image_motion",
    "local_asset",
    "external_video",
    "gemini_omni",
    "openrouter_video",
    "google_flow",
)

#: Sentinel accepted alongside a concrete provider name.
PROVIDER_INTENT_DEFAULT = "default"

#: Canonical Production Bible sections (extensible; not rigid limits).
BIBLE_CANONICAL_SECTIONS = (
    "characters",
    "cast",
    "world",
    "locations",
    "visual_language",
    "narrative",
    "recurring_elements",
    "brand_context",
)

#: Recommended initial unit types (open/extensible vocabulary — A6).
RECOMMENDED_UNIT_TYPES = (
    "standalone_video",
    "episode",
    "chapter",
    "campaign_asset",
    "batch_item",
)

_PLAN_REFERENCE_STATUSES = ("unresolved", "resolved")

_UNIT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_PRODUCTION_ID_PATTERN = re.compile(r"^prd_\d{8}_[a-z0-9]+(?:_[a-z0-9]+)*_\d{3}$")


def _relative_path_segments(location: str) -> List[str]:
    """Return forward-slash segments of a relative reference location."""
    return [segment for segment in location.replace("\\", "/").split("/") if segment]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ProductionCommand(BaseModel):
    """Durable provenance of the originating production instruction (A9).

    Captured once at production creation; never silently rewritten. Minimal
    capture: the original text plus explicitly supplied structured intent.
    NOT a command parser, scheduler, automation rule, retry, or fallback policy.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="Original command/request text")
    workload_shape: str = Field(
        description="Declared shape: single_video | unit_count | open_ended"
    )
    intended_unit_count: Optional[int] = Field(
        default=None, ge=1, description="Intended unit count when explicitly known"
    )
    explicit_constraints: Dict[str, Any] = Field(
        default_factory=dict,
        description="Only explicitly supplied intent (platform/duration/provider name)",
    )
    creation_context: Dict[str, str] = Field(
        default_factory=dict,
        description="Source/origin identity (e.g. {'source': 'cli'}); never credentials",
    )
    issued_at: str = Field(min_length=1, description="Creation timestamp (ISO-8601)")

    @model_validator(mode="after")
    def _validate_shape(self) -> "ProductionCommand":
        if self.workload_shape not in ("single_video", "unit_count", "open_ended"):
            raise ValueError(
                "workload_shape must be one of: single_video, unit_count, open_ended"
            )
        if self.workload_shape == "unit_count" and self.intended_unit_count is None:
            raise ValueError(
                "intended_unit_count is required when workload_shape == 'unit_count'"
            )
        return self


class BibleElement(BaseModel):
    """One extensible shared-creative-context entry in the Production Bible (A5).

    ``content`` is deliberately untyped structured data: Phase 9 does not design
    typed character/cast/world schemas. ``element_id`` is stable within the
    production so units can reference it without copying content.
    """

    model_config = ConfigDict(extra="forbid")

    element_id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9][a-z0-9_\-/]*$",
        description="Stable identifier within the production (e.g. characters/kai)",
    )
    section: str = Field(
        min_length=1,
        pattern=_UNIT_TYPE_PATTERN,
        description=(
            "Canonical section name (extensible): "
            + ", ".join(BIBLE_CANONICAL_SECTIONS)
            + ", ..."
        ),
    )
    content: Dict[str, Any] = Field(default_factory=dict)


class ProductionBible(BaseModel):
    """OPTIONAL shared creative context for a Production (A2)."""

    model_config = ConfigDict(extra="forbid")

    elements: List[BibleElement] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_element_ids(self) -> "ProductionBible":
        ids = [element.element_id for element in self.elements]
        duplicates = sorted({eid for eid in ids if ids.count(eid) > 1})
        if duplicates:
            raise ValueError(f"Duplicate Bible element_id values: {duplicates}")
        return self


class EpisodeMetadata(BaseModel):
    """OPTIONAL semantic metadata. A unit does not need to be an episode (A1)."""

    model_config = ConfigDict(extra="forbid")

    season_number: Optional[int] = Field(default=None, ge=1)
    episode_number: Optional[int] = Field(default=None, ge=1)
    episode_title: Optional[str] = Field(default=None, min_length=1)
    continuity_refs: List[str] = Field(default_factory=list)


class PlanReference(BaseModel):
    """A unit's link to its detailed per-video ProductionPlan.

    Phase 9 never generates or binds plans (A4: lazy, Phase 10). A newly
    created unit typically carries ``status='unresolved'``.
    """

    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="unresolved | resolved")
    relative_location: Optional[str] = Field(
        default=None,
        description="Plan artifact location RELATIVE to the production root",
    )
    referenced_plan_identity: Optional[str] = Field(default=None, min_length=1)
    referenced_plan_version: Optional[int] = Field(default=None, ge=1)
    content_hash: Optional[str] = Field(
        default=None,
        description="SHA-256 of the referenced artifact at reference time",
    )

    @model_validator(mode="after")
    def _validate_resolution(self) -> "PlanReference":
        if self.status not in _PLAN_REFERENCE_STATUSES:
            raise ValueError("status must be 'unresolved' or 'resolved'")
        if self.status == "resolved":
            missing = [
                name
                for name in ("relative_location", "content_hash")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(
                    f"resolved plan reference requires: {', '.join(missing)}"
                )
            if self.relative_location is not None:
                location = self.relative_location
                segments = _relative_path_segments(location)
                is_absolute = (
                    location.startswith(("/", "\\", "~"))
                    or re.match(r"^[A-Za-z]:[\\/]", location) is not None
                )
                if not segments or is_absolute or ".." in segments:
                    raise ValueError(
                        "plan reference location must be relative to the "
                        "production root (no absolute paths, no '..')"
                    )
        else:
            present = [
                name
                for name in (
                    "relative_location",
                    "referenced_plan_identity",
                    "referenced_plan_version",
                    "content_hash",
                )
                if getattr(self, name) is not None
            ]
            if present:
                raise ValueError(
                    f"unresolved plan reference must not carry: {', '.join(present)}"
                )
        return self


class ProductionUnit(BaseModel):
    """The NEUTRAL workload primitive: intent for ONE video output (A1/A6).

    Contains intent only — never execution state (see validation.py's
    exclusion scans and PHASE_9 spec §11/§14).
    """

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9][a-z0-9_\-]*$",
        description="Stable within the production; independent of ordering",
    )
    ordinal: int = Field(ge=1, description="Deterministic ordering; no ties")
    unit_type: str = Field(
        min_length=1,
        pattern=_UNIT_TYPE_PATTERN,
        description=(
            "Open/extensible vocabulary; recommended initial set: "
            + ", ".join(RECOMMENDED_UNIT_TYPES)
        ),
    )
    title: str = Field(min_length=1)
    description: Optional[str] = None
    premise: Optional[str] = None
    content_intent: Dict[str, Any] = Field(default_factory=dict)
    episode: Optional[EpisodeMetadata] = Field(
        default=None,
        description="OPTIONAL episodic metadata (requires unit_type == 'episode')",
    )
    provider_intent: Optional[str] = Field(
        default=None,
        description=(
            "Neutral provider NAME intent: one of "
            + ", ".join(PROVIDER_INTENT_NAMES)
            + " (or 'default'/None to defer to orchestration routing)"
        ),
    )
    bible_refs: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    plan_reference: Optional[PlanReference] = None

    @model_validator(mode="after")
    def _validate_unit(self) -> "ProductionUnit":
        if self.episode is not None and self.unit_type != "episode":
            raise ValueError(
                "episodic metadata requires unit_type == 'episode' "
                f"(got '{self.unit_type}')"
            )
        if self.provider_intent is not None and self.provider_intent not in (
            PROVIDER_INTENT_DEFAULT,
            *PROVIDER_INTENT_NAMES,
        ):
            raise ValueError(
                "provider_intent must be a neutral provider name "
                f"({', '.join(PROVIDER_INTENT_NAMES)}) or 'default'/None"
            )
        if self.unit_id in self.dependencies:
            raise ValueError("a unit cannot depend on itself")
        return self


class Production(BaseModel):
    """The durable workload-level INTENT artifact (root). Intent, not execution."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: str = Field(
        default=SCHEMA_NAME,
        validation_alias=AliasChoices("schema", "schema_name"),
        serialization_alias="schema",
        description="Artifact family discriminator",
    )
    schema_version: int = Field(
        default=SUPPORTED_SCHEMA_VERSION,
        ge=1,
        description="Structure compatibility version (separate from plan_version)",
    )
    production_id: str = Field(
        pattern=_PRODUCTION_ID_PATTERN.pattern,
        description="Stable identity: prd_YYYYMMDD_<slug>_<###> (A7)",
    )
    title: str = Field(min_length=1)
    command: ProductionCommand
    plan_version: int = Field(
        default=1, ge=1, description="Intent revision history (starts at 1)"
    )
    bible: Optional[ProductionBible] = Field(
        default=None, description="OPTIONAL shared creative context (A2)"
    )
    units: List[ProductionUnit] = Field(min_length=1)
    created_at: str = Field(min_length=1)
    revised_at: Optional[str] = None
    revision_notes: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_production(self) -> "Production":
        # Unique unit identities (identity is never ordering-derived).
        unit_ids = [unit.unit_id for unit in self.units]
        duplicates = sorted({uid for uid in unit_ids if unit_ids.count(uid) > 1})
        if duplicates:
            raise ValueError(f"Duplicate unit_id values: {duplicates}")

        # Deterministic total ordering: no ordinal ties.
        ordinals = [unit.ordinal for unit in self.units]
        duplicate_ordinals = sorted(
            {o for o in ordinals if ordinals.count(o) > 1}
        )
        if duplicate_ordinals:
            raise ValueError(
                "Duplicate ordinals (ordering must have no ties): "
                f"{duplicate_ordinals}"
            )

        # Bible reference integrity: refs resolve iff a Bible is present.
        bible_ids = (
            {element.element_id for element in self.bible.elements}
            if self.bible is not None
            else set()
        )
        for unit in self.units:
            if self.bible is None and unit.bible_refs:
                raise ValueError(
                    f"unit '{unit.unit_id}' references Bible elements but the "
                    "production has no Production Bible"
                )
            dangling = [ref for ref in unit.bible_refs if ref not in bible_ids]
            if dangling:
                raise ValueError(
                    f"unit '{unit.unit_id}' references unknown Bible elements: "
                    f"{dangling}"
                )

        # Dependency integrity: dependencies must reference existing units.
        known_units = set(unit_ids)
        for unit in self.units:
            unknown = [dep for dep in unit.dependencies if dep not in known_units]
            if unknown:
                raise ValueError(
                    f"unit '{unit.unit_id}' depends on unknown units: {unknown}"
                )
        _check_dependency_cycles(self.units)
        return self


def _check_dependency_cycles(units: List[ProductionUnit]) -> None:
    """Reject declared dependency cycles among units (data integrity only)."""
    edges = {unit.unit_id: list(unit.dependencies) for unit in units}
    visiting: set = set()
    visited: set = set()

    def visit(node: str, chain: List[str]) -> None:
        if node in visiting:
            cycle_start = chain.index(node)
            raise ValueError(
                "dependency cycle detected: "
                + " -> ".join([*chain[cycle_start:], node])
            )
        if node in visited:
            return
        visiting.add(node)
        for dep in edges.get(node, []):
            visit(dep, [*chain, node])
        visiting.remove(node)
        visited.add(node)

    for unit_id in edges:
        visit(unit_id, [])



