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

import math
import re
from typing import Any, Dict, List, Optional, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.production.routes import KNOWN_ROUTES, PRIVATE_ONLY_ROUTES, PRODUCTION_ROUTES

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

#: LEGACY / COMPATIBILITY (Batch 1C bridge): user-selectable production-level
#: GENERATION STRATEGY.
#:
#: SUPERSEDED as the master abstraction by the first-class Production Route
#: (Batch 1D, see ``app/production/routes.py``).  This field remains the
#: COMPATIBILITY execution bridge so existing productions keep loading and
#: executing byte-for-byte; new code resolves the effective route through
#: ``app.production.routes.resolve_effective_route``.  The strategy values
#: are intentionally unchanged:
#:
#: - ``local``                  — the existing local visual pipeline
#:                                (image sourcing + ImageMotion/local assembly);
#:                                the default, preserving all prior behavior;
#: - ``alibaba_image_local_video`` — Alibaba generates scene IMAGES, the
#:                                existing local pipeline assembles/animates;
#: - ``alibaba_video``          — Alibaba generates actual MOTION-VIDEO scene
#:                                clips (Wan t2v via the existing adapter);
#:                                never a silent Ken-Burns substitution.
GENERATION_STRATEGIES = (
    "local",
    "alibaba_image_local_video",
    "alibaba_video",
)

#: Default preserves every existing production's behavior.
DEFAULT_GENERATION_STRATEGY = "local"

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

AUDIO_INTENT_NONE = "none"
AUDIO_INTENT_NARRATION = "narration"
AUDIO_INTENT_NATIVE = "native"
AUDIO_INTENT_NATIVE_PLUS_NARRATION = "native_plus_narration"
AUDIO_INTENTS = (
    AUDIO_INTENT_NARRATION,
    AUDIO_INTENT_NATIVE,
    AUDIO_INTENT_NATIVE_PLUS_NARRATION,
    AUDIO_INTENT_NONE,
)
DEFAULT_AUDIO_INTENT = AUDIO_INTENT_NARRATION


def normalize_audio_intent(value: Optional[str]) -> str:
    """Normalize a persisted audio-intent value to the canonical vocabulary."""
    if value is None:
        return DEFAULT_AUDIO_INTENT
    normalized = str(value).strip().lower().replace("-", "_")
    aliases = {
        "": DEFAULT_AUDIO_INTENT,
        "no_audio": AUDIO_INTENT_NONE,
        "none": AUDIO_INTENT_NONE,
        "voice": AUDIO_INTENT_NARRATION,
        "voiced": AUDIO_INTENT_NARRATION,
        "narration": AUDIO_INTENT_NARRATION,
        "native_audio": AUDIO_INTENT_NATIVE,
        "native": AUDIO_INTENT_NATIVE,
        "native_plus_voice": AUDIO_INTENT_NATIVE_PLUS_NARRATION,
        "native_plus_narration": AUDIO_INTENT_NATIVE_PLUS_NARRATION,
    }
    intent = aliases.get(normalized)
    if intent is not None:
        return intent
    if normalized in AUDIO_INTENTS:
        return normalized
    raise ValueError(
        "audio_intent must be one of: "
        + ", ".join(AUDIO_INTENTS)
        + f" (got {value!r})"
    )


def _relative_path_segments(location: str) -> List[str]:
    """Return forward-slash segments of a relative reference location."""
    return [segment for segment in location.replace("\\", "/").split("/") if segment]


def _is_relative_path(value: str) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if value.startswith(("/", "\\", "~")):
        return False
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return False
    if ".." in _relative_path_segments(value):
        return False
    return True


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


class SourceAsset(BaseModel):
    """A persisted source media asset owned by a production."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(pattern=r"^src-\d{3}$")
    original_filename: str = Field(min_length=1)
    stored_path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    duration: float = Field(gt=0.0)
    width: Optional[int] = Field(default=None, ge=1)
    height: Optional[int] = Field(default=None, ge=1)
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    mime: Optional[str] = None
    file_size: Optional[int] = Field(default=None, ge=0)
    ingested_at: str = Field(min_length=1)
    # Intake provenance is additive; legacy sources remain unknown, not inferred.
    source_kind: Optional[Literal["local_file", "browser_upload", "sandbox_fixture"]] = None

    @model_validator(mode="after")
    def _validate_relative_path(self) -> "SourceAsset":
        if not _is_relative_path(self.stored_path):
            raise ValueError("stored_path must be a relative path under the production root")
        return self


class TranscriptSegment(BaseModel):
    """One transcript segment emitted by a transcription provider."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    words: Optional[List[Dict[str, Any]]] = Field(default=None)

    @model_validator(mode="after")
    def _validate_time_bounds(self) -> "TranscriptSegment":
        if self.end <= self.start:
            raise ValueError("transcript end must be greater than start")
        if not math.isfinite(self.start) or not math.isfinite(self.end):
            raise ValueError("transcript timestamps must be finite")
        return self


class SourceTranscript(BaseModel):
    """Persisted transcript sidecar for one source asset."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(pattern=r"^src-\d{3}$")
    language: Optional[str] = None
    segments: List[TranscriptSegment] = Field(default_factory=list)
    provider: str = Field(min_length=1)
    provider_version: Optional[str] = None
    created_at: str = Field(min_length=1)
    transcript_path: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_relative_path(self) -> "SourceTranscript":
        if not _is_relative_path(self.transcript_path):
            raise ValueError("transcript_path must be relative to the production root")
        return self


class SourceSegment(BaseModel):
    """Operator-selected source range that references transcript segments."""

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(pattern=r"^seg-\d{3}$")
    asset_id: str = Field(pattern=r"^src-\d{3}$")
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)
    title: Optional[str] = Field(default=None, min_length=1)
    premise: Optional[str] = Field(default=None, min_length=1)
    transcript_segment_ids: List[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_segment(self) -> "SourceSegment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("segment end_seconds must be greater than start_seconds")
        if not math.isfinite(self.start_seconds) or not math.isfinite(self.end_seconds):
            raise ValueError("segment timestamps must be finite")
        if any(ref < 0 for ref in self.transcript_segment_ids):
            raise ValueError("transcript_segment_ids must be non-negative integers")
        return self


class SourceLineage(BaseModel):
    """Persisted bridge: a ProductionUnit derived from selected source segments."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_\-]*$")
    asset_id: str = Field(pattern=r"^src-\d{3}$")
    source_segment_ids: List[str] = Field(default_factory=list)
    source_path: Optional[str] = None

    @model_validator(mode="after")
    def _validate_lineage(self) -> "SourceLineage":
        if not self.source_segment_ids:
            raise ValueError("source_segment_ids cannot be empty")
        seen: set[str] = set()
        for segment_id in self.source_segment_ids:
            if not isinstance(segment_id, str) or not re.fullmatch(r"^seg-\d{3}$", segment_id):
                raise ValueError("source_segment_ids must be seg-NNN strings")
            if segment_id in seen:
                raise ValueError(f"duplicate source segment id: {segment_id}")
            seen.add(segment_id)
        if self.source_path is not None and not _is_relative_path(self.source_path):
            raise ValueError("source_path must be a relative path when supplied")
        return self


def derive_source_lineage(
    production: "Production",
    unit_id: str,
    *,
    asset_id: str,
    source_segment_ids: List[str],
) -> "SourceLineage":
    """Validate a unit-to-source segment derivation against the production asset set."""
    if production.source_assets is None:
        raise ValueError(f"production '{production.production_id}' has no source assets")
    unit = next((entry for entry in production.units if entry.unit_id == unit_id), None)
    if unit is None:
        raise ValueError(f"unit '{unit_id}' is not part of production '{production.production_id}'")
    asset = next((entry for entry in production.source_assets if entry.asset_id == asset_id), None)
    if asset is None:
        raise ValueError(f"source asset '{asset_id}' is not in production '{production.production_id}'")
    lineage = SourceLineage(
        unit_id=unit_id,
        asset_id=asset_id,
        source_segment_ids=list(source_segment_ids),
        source_path=asset.stored_path,
    )
    if unit.source_lineage is not None and unit.source_lineage.asset_id != asset_id:
        raise ValueError(f"unit '{unit_id}' already has lineage for a different source asset")
    return lineage


def _next_available_unit_id(production: "Production") -> str:
    """Generate the next canonical unit id for the production without randomization."""
    used = {unit.unit_id for unit in production.units}
    for index in range(1, 10000):
        candidate = f"unit-{index:03d}"
        if candidate not in used:
            return candidate
    raise ValueError(f"production '{production.production_id}' has no available unit_id")


def derive_production_unit_from_segment(
    production: "Production",
    source_asset: "SourceAsset",
    source_segment: "SourceSegment",
    *,
    unit_id: Optional[str] = None,
    title: Optional[str] = None,
    premise: Optional[str] = None,
    unit_type: str = "standalone_video",
    ordinal: Optional[int] = None,
    content_intent: Optional[Dict[str, Any]] = None,
    provider_intent: Optional[str] = None,
) -> "ProductionUnit":
    """Create a canonical ProductionUnit derived from an operator-selected SourceSegment.

    This is the model-level derivation seam. It validates explicit production/asset/
    segment ownership, constructs a normal ProductionUnit, derives the canonical
    SourceLineage, and returns the unit without creating a second execution model.
    """
    if not isinstance(production, Production):
        raise TypeError("production must be a Production")
    if not isinstance(source_asset, SourceAsset):
        raise TypeError("source_asset must be a SourceAsset")
    if not isinstance(source_segment, SourceSegment):
        raise TypeError("source_segment must be a SourceSegment")

    if production.source_assets is None:
        raise ValueError(f"production '{production.production_id}' has no source assets")

    asset = next(
        (entry for entry in production.source_assets if entry.asset_id == source_asset.asset_id),
        None,
    )
    if asset is None:
        raise ValueError(
            f"source asset '{source_asset.asset_id}' is not in production '{production.production_id}'"
        )
    if asset.asset_id != source_asset.asset_id:
        raise ValueError(
            f"source asset mismatch: production asset '{asset.asset_id}' != '{source_asset.asset_id}'"
        )

    if source_segment.asset_id != source_asset.asset_id:
        raise ValueError(
            "source segment asset_id must match the provided source asset: "
            f"segment {source_segment.asset_id!r} vs asset {source_asset.asset_id!r}"
        )

    if source_segment.start_seconds < 0 or not math.isfinite(source_segment.start_seconds):
        raise ValueError("source segment start_seconds must be finite and non-negative")
    if source_segment.end_seconds <= source_segment.start_seconds:
        raise ValueError("source segment end_seconds must be greater than start_seconds")
    if not math.isfinite(source_segment.end_seconds):
        raise ValueError("source segment end_seconds must be finite")
    if source_segment.end_seconds > source_asset.duration:
        raise ValueError(
            "source segment extends beyond the source asset duration "
            f"({source_segment.end_seconds} > {source_asset.duration})"
        )

    existing = next(
        (
            unit
            for unit in production.units
            if unit.source_lineage is not None
            and unit.source_lineage.asset_id == source_asset.asset_id
            and source_segment.segment_id in set(unit.source_lineage.source_segment_ids)
        ),
        None,
    )
    if existing is not None:
        return existing

    if unit_id is None:
        unit_id = _next_available_unit_id(production)
    if any(unit.unit_id == unit_id for unit in production.units):
        raise ValueError(f"unit_id '{unit_id}' is already in use for production '{production.production_id}'")
    if ordinal is None:
        ordinal = max((unit.ordinal for unit in production.units), default=0) + 1

    candidate = ProductionUnit(
        unit_id=unit_id,
        ordinal=ordinal,
        unit_type=unit_type,
        title=(title or source_segment.title or f"Derived from {source_asset.asset_id}"),
        premise=(premise or source_segment.premise or f"Derived from {source_segment.segment_id}"),
        content_intent=content_intent or {},
        provider_intent=provider_intent,
    )
    candidate_units = [*production.units, candidate]
    candidate_production = production.model_copy(update={"units": candidate_units})
    lineage = derive_source_lineage(
        candidate_production,
        candidate.unit_id,
        asset_id=source_asset.asset_id,
        source_segment_ids=[source_segment.segment_id],
    )
    return candidate.model_copy(update={"source_lineage": lineage})


class NarrationSourceRef(BaseModel):
    """Source-grounded evidence used to justify a single narrated beat."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(pattern=r"^src-\d{3}$")
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)
    transcript_segment_ids: List[int] = Field(default_factory=list)
    source_segment_id: Optional[str] = Field(default=None, pattern=r"^seg-\d{3}$")
    excerpt: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _validate_ref(self) -> "NarrationSourceRef":
        if not math.isfinite(self.start_seconds) or not math.isfinite(self.end_seconds):
            raise ValueError("source reference timestamps must be finite")
        if self.end_seconds <= self.start_seconds:
            raise ValueError("source reference end_seconds must be greater than start_seconds")
        return self


class NarrativeBeat(BaseModel):
    """One ordered narrative beat in the condensed narration plan."""

    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1)
    title: str = Field(min_length=1)
    narration_text: str = Field(min_length=1)
    summary: Optional[str] = Field(default=None, min_length=1)
    source_refs: List[NarrationSourceRef] = Field(default_factory=list)
    transcript_refs: List[str] = Field(default_factory=list)
    source_segment_ids: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_beat(self) -> "NarrativeBeat":
        if not self.source_refs:
            raise ValueError("a narrative beat must include at least one grounded source reference")
        for ref in self.source_refs:
            if ref.asset_id is None:
                raise ValueError("narrative beat source refs require asset_id")
        return self


class NarrationAudioArtifact(BaseModel):
    """Persisted synthesized narration audio metadata for a grounded narration artifact."""

    model_config = ConfigDict(extra="forbid")

    narration_id: str = Field(pattern=r"^narr-[a-z0-9_-]+$")
    production_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    audio_path: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    voice: str = Field(min_length=1)
    format: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0.0)
    file_size: int = Field(ge=1)
    sample_rate: Optional[int] = Field(default=None, ge=1)
    channels: Optional[int] = Field(default=None, ge=1)
    codec: Optional[str] = None
    status: str = Field(default="completed")
    generated_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_audio_path(self) -> "NarrationAudioArtifact":
        if not _is_relative_path(self.audio_path):
            raise ValueError("audio_path must be relative to the production root")
        if self.status not in {"pending", "completed", "failed"}:
            raise ValueError("audio status must be one of: pending, completed, failed")
        return self


class NarrationArtifact(BaseModel):
    """Unit-scoped, source-grounded planned narration artifact."""

    model_config = ConfigDict(extra="forbid")

    narration_id: str = Field(pattern=r"^narr-[a-z0-9_-]+$")
    production_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    source_asset_id: str = Field(pattern=r"^src-\d{3}$")
    target_duration_seconds: float = Field(gt=0.0)
    status: str = Field(default="draft")
    narration_text: str = Field(min_length=1)
    summary: Optional[str] = Field(default=None, min_length=1)
    beats: List[NarrativeBeat] = Field(default_factory=list)
    source_refs: List[NarrationSourceRef] = Field(default_factory=list)
    transcript_refs: List[str] = Field(default_factory=list)
    generated_at: str = Field(min_length=1)
    revised_at: Optional[str] = None
    # Exact sidecar used for grounding; sandbox and real STT never race for it.
    # Optional so accepted legacy Track 9 artifacts retain their old identity.
    source_transcript_path: Optional[str] = None
    source_media_path: Optional[str] = None
    audio_artifact_path: Optional[str] = Field(default=None, min_length=1)
    audio_duration_seconds: Optional[float] = Field(default=None, gt=0.0)
    audio_provider: Optional[str] = None
    audio_voice: Optional[str] = None
    audio_format: Optional[str] = None
    audio_status: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _validate_artifact(self) -> "NarrationArtifact":
        if self.source_media_path is not None and not _is_relative_path(self.source_media_path):
            raise ValueError("source_media_path must be relative to the production root")
        if self.source_transcript_path is not None and self.source_transcript_path not in {
            f"source/{self.source_asset_id}.transcript.json",
            f"source/{self.source_asset_id}.sandbox.transcript.json",
        }:
            raise ValueError("source_transcript_path must name this source asset's canonical or sandbox sidecar")
        if self.status not in {"draft", "grounded", "approved", "rejected"}:
            raise ValueError("status must be one of: draft, grounded, approved, rejected")
        if self.audio_status is not None and self.audio_status not in {"pending", "completed", "failed"}:
            raise ValueError("audio_status must be one of: pending, completed, failed")
        if self.audio_artifact_path is not None and not _is_relative_path(self.audio_artifact_path):
            raise ValueError("audio_artifact_path must be relative to the production root")
        if not self.beats:
            raise ValueError("a narration artifact requires at least one narrative beat")
        if not self.source_refs:
            raise ValueError("a narration artifact requires at least one grounded source reference")
        orders = [beat.order for beat in self.beats]
        if orders != sorted(orders):
            raise ValueError("narrative beats must be ordered deterministically")
        for beat in self.beats:
            if not any(ref.asset_id == self.source_asset_id for ref in beat.source_refs):
                raise ValueError(
                    f"beat '{beat.title}' must reference the canonical source asset '{self.source_asset_id}'"
                )
        return self


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
    source_lineage: Optional["SourceLineage"] = Field(default=None)
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
    audio_intent: Optional[str] = Field(
        default=None,
        description="Optional audio intent normalized to the canonical production vocabulary.",
    )
    generation_strategy: str = Field(
        default=DEFAULT_GENERATION_STRATEGY,
        description=(
            "LEGACY/COMPATIBILITY (Batch 1C bridge): production-level visual "
            "generation strategy — one of " + ", ".join(GENERATION_STRATEGIES)
            + ". Superseded by the first-class `route` field (Batch 1D); "
            "retained so existing productions load and execute unchanged."
        ),
    )
    route: Optional[str] = Field(
        default=None,
        description=(
            "Batch 1D: first-class PRODUCTION ROUTE — the product-level path "
            "this production is intended to be made through. New SaaS "
            "productions must use the SaaS surface (one of: "
            + ", ".join(PRODUCTION_ROUTES)
            + "); private-only routes ("
            + ", ".join(PRIVATE_ONLY_ROUTES)
            + ") remain loadable for EXISTING/private artifacts but can never "
            "be selected for a new SaaS production. Route is deliberately NOT "
            "a provider, model, media strategy, audio strategy, engine, "
            "assembly engine, quality rule, or distribution target. None "
            "(absent, e.g. legacy artifacts) = legacy resolution via the "
            "compatibility generation_strategy bridge; an explicit route "
            "takes precedence."
        ),
    )
    units: List[ProductionUnit] = Field(min_length=1)
    source_assets: Optional[List["SourceAsset"]] = Field(default=None)
    source_lineage: Optional[List["SourceLineage"]] = Field(default=None)
    created_at: str = Field(min_length=1)
    revised_at: Optional[str] = None
    revision_notes: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_production(self) -> "Production":
        # Batch 1D: first-class Production Route.  Absent (None) = legacy
        # resolution through the compatibility generation_strategy bridge;
        # an unknown route fails loudly (never rerouted, never defaulted).
        # SaaS/private boundary: the model accepts every KNOWN route so
        # EXISTING private (google_flow) artifacts keep loading; selecting a
        # route for a NEW SaaS production is gated at the creation seams
        # (save_new_production / server creation) via
        # assert_saas_production_route.
        if self.route is not None and self.route not in KNOWN_ROUTES:
            raise ValueError(
                "route must be one of: "
                + ", ".join(KNOWN_ROUTES)
                + f" (got {self.route!r})"
            )

        if self.audio_intent is not None:
            self.audio_intent = normalize_audio_intent(self.audio_intent)

        # Generation strategy is an explicit user selection persisted with the
        # production intent; unknown values fail loudly (never rerouted).
        if self.generation_strategy not in GENERATION_STRATEGIES:
            raise ValueError(
                "generation_strategy must be one of: "
                + ", ".join(GENERATION_STRATEGIES)
                + f" (got {self.generation_strategy!r})"
            )

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



