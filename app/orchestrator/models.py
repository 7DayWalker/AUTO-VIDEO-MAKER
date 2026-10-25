"""
AUTO Video Maker - Orchestrator Models

Pydantic models for pipeline state management and configuration.
"""

from datetime import datetime
from enum import Enum
from pathlib import Path
import os
from typing import Optional, List, Dict, Any, Tuple, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.publishing.models import PublicationRecord, TikTokPublicationRecord
from app.assembly.audio_contract import (
    AudioPolicy,
    DEFAULT_AUDIO_POLICY,
    normalize_audio_policy,
)


class PipelineStage(str, Enum):
    """Pipeline stages in execution order."""
    CONTENT = "content"
    VOICE = "voice"
    SCENES = "scenes"
    CAPTIONS = "captions"
    ASSEMBLY = "assembly"
    COMPLETE = "complete"
    
    @classmethod
    def get_order(cls) -> List["PipelineStage"]:
        """Get stages in execution order."""
        return [cls.CONTENT, cls.VOICE, cls.SCENES, cls.CAPTIONS, cls.ASSEMBLY, cls.COMPLETE]
    
    def next_stage(self) -> Optional["PipelineStage"]:
        """Get the next stage after this one."""
        order = self.get_order()
        try:
            idx = order.index(self)
            if idx < len(order) - 1:
                return order[idx + 1]
        except ValueError:
            pass
        return None
    
    def previous_stage(self) -> Optional["PipelineStage"]:
        """Get the previous stage before this one."""
        order = self.get_order()
        try:
            idx = order.index(self)
            if idx > 0:
                return order[idx - 1]
        except ValueError:
            pass
        return None


class StageStatus(str, Enum):
    """Status of a pipeline stage."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StageResult(BaseModel):
    """Result of a pipeline stage execution."""
    stage: PipelineStage
    status: StageStatus
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    output_path: Optional[str] = None
    output_files: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    @classmethod
    def pending(cls, stage: PipelineStage) -> "StageResult":
        """Create a pending result."""
        return cls(stage=stage, status=StageStatus.PENDING)
    
    @classmethod
    def running(cls, stage: PipelineStage) -> "StageResult":
        """Create a running result."""
        return cls(
            stage=stage,
            status=StageStatus.RUNNING,
            started_at=datetime.now().isoformat(),
        )
    
    @classmethod
    def completed(
        cls,
        stage: PipelineStage,
        output_path: str = None,
        output_files: List[str] = None,
        warnings: List[str] = None,
        metadata: Dict[str, Any] = None,
        started_at: str = None,
    ) -> "StageResult":
        """Create a completed result."""
        completed_at = datetime.now().isoformat()
        duration = None
        if started_at:
            try:
                start = datetime.fromisoformat(started_at)
                end = datetime.fromisoformat(completed_at)
                duration = (end - start).total_seconds()
            except Exception:
                pass
        
        return cls(
            stage=stage,
            status=StageStatus.COMPLETED,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
            output_path=output_path,
            output_files=output_files or [],
            warnings=warnings or [],
            metadata=metadata or {},
        )
    
    @classmethod
    def failed(
        cls,
        stage: PipelineStage,
        error: str,
        started_at: str = None,
    ) -> "StageResult":
        """Create a failed result."""
        return cls(
            stage=stage,
            status=StageStatus.FAILED,
            started_at=started_at,
            completed_at=datetime.now().isoformat(),
            error=error,
        )
    
    @classmethod
    def skipped(cls, stage: PipelineStage, reason: str = None) -> "StageResult":
        """Create a skipped result."""
        return cls(
            stage=stage,
            status=StageStatus.SKIPPED,
            metadata={"skip_reason": reason} if reason else {},
        )


class RouteCompilationRecord(BaseModel):
    """Durable evidence of the PRODUCTION ROUTE Core compiled for one unit.

    G3 (V1 should-fix — persisted route compilation evidence): route
    resolution and compilation happen at execution time
    (``ProductionExecutor._config_for_unit`` ->
    ``app.production.routes.resolve_effective_route`` ->
    ``apply_production_route``).  Until now the compiled decision existed only
    in a log line, so a persisted production could not explain, from durable
    evidence alone, which route Core actually compiled for execution.

    SEMANTIC (deliberately narrow).  This record answers exactly one question:
    "given the persisted production intent, what route did Core compile for
    execution?"  It is the frozen output of the existing route-compilation
    seam and is therefore attributable to Core.  It is:

    - NOT provider/model provenance.  Which provider/model executed each
      scene is the Batch 1F scene provenance record; no provider, model,
      request/task id, or fallback fact may ever appear here, and nothing
      here may be read as provenance.
    - NOT media strategy.  ``overlays_applied`` names the execution-config
      KEYS the legacy compatibility bridge mutated (e.g. ``image_source``);
      it never records their values, which stay in the existing scene config.
    - NOT a motion/video engine choice, an AI-brain choice, an audio policy,
      or an assembly engine.
    - NOT accounting.  No cost class, amount, credit, or usage is recorded
      (the creation-time cost class stays where it is today).
    - NOT user intent.  The intent artifact (``production.json``) keeps its
      explicit ``route`` and legacy ``generation_strategy`` verbatim; this
      record is a separate, execution-side fact and never overwrites intent.

    Only fields that are ALREADY authoritative outputs of compilation are
    recorded:

    - ``route_id`` + ``route_version``: the registered route identity together
      with the registry version it compiled against, so a later change to the
      route registry can never rewrite history.  Everything the frozen
      ``RouteDefinition`` additionally implies (``label``,
      ``requires_native_motion``, ``forbids_still_image_substitution``,
      ``allows_local_motion_engine``) is fully DERIVABLE from that pair and is
      deliberately not duplicated — and requirements are not yet enforced, so
      recording them as evidence would overstate them.
    - ``execution_surface``: the surface the route compiled onto.
    - ``route_source``: how the route was resolved, i.e. the precedence
      evidence — ``"explicit_route"`` means the intent carried a route and it
      was honoured verbatim, ``"legacy_strategy"`` means Core derived the
      route from the legacy ``generation_strategy`` bridge.  This is exactly
      what keeps compiled result distinguishable from intent.
    - ``legacy_strategy``: the compatibility-bridge input actually used.
    - ``overlays_applied``: which legacy overlay keys the compiler applied.
      Empty for explicit-route runs and in mock mode, which is truthful: the
      compiler applied nothing there.

    Deliberately NOT a registry-membership validator: the record is a
    historical fact, so it must still load even if the registry later drops or
    renames a route.  ``label`` (registry display text) is absent because it
    is presentation, not evidence.

    Written ONCE, when the execution project is initialized.  Resume never
    rewrites it (mirroring the existing rule that the persisted scene config
    is authoritative on resume), and projects created before this seam keep
    returning ``None`` = UNKNOWN, which is never back-inferred from today's
    registry.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    route_id: str = Field(min_length=1)
    route_version: int = Field(ge=1)
    route_source: str = Field(min_length=1)
    execution_surface: str = Field(min_length=1)
    legacy_strategy: Optional[str] = None
    overlays_applied: Tuple[str, ...] = ()


class ProductionBinding(BaseModel):
    """Immutable Phase 10 execution-side binding for one production unit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    production_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    production_plan_version: int = Field(ge=1)
    unit_plan_identity: str = Field(min_length=1)
    unit_plan_version: int = Field(default=1, ge=1)
    unit_plan_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Captured directory identity assertion; never re-derived from the legacy
    # mutable top-level manifest.project_id field.
    project_id: str = Field(min_length=1)
    bound_at: str = Field(min_length=1)
    # Additive import lineage. Directory identity is relative to projects_root,
    # never an absolute workstation path. Legacy engine bindings omit both.
    execution_dir: Optional[str] = None
    handoff_kind: Optional[Literal["narrated_summary"]] = None


class SourceSummaryHandoff(BaseModel):
    """Accepted Track 9 import provenance, NOT generated-stage checkpoints.

    Project-owned snapshots and media are fingerprinted independently of the
    mutable production sidecars. Neither platform's publication record lives
    here; the existing publication state machines remain their sole owners.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    handoff_kind: Literal["narrated_summary"] = "narrated_summary"
    production_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    narration_id: str = Field(pattern=r"^narr-[a-z0-9_-]+$")
    source_asset_id: str = Field(pattern=r"^src-\d{3}$")
    source_summary_path: str = Field(min_length=1)
    snapshot_dir: str = Field(min_length=1)
    artifact_hashes: Dict[str, str]
    narration_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_size: int = Field(ge=1)
    promoted_at: str = Field(min_length=1)
    summary_generated_at: str = Field(min_length=1)

    @field_validator("source_summary_path", "snapshot_dir")
    @classmethod
    def _relative_handoff_path(cls, value: str) -> str:
        import re

        normalized = value.replace("\\", "/")
        if (not re.fullmatch(r"[a-zA-Z0-9_./-]+", normalized)
                or normalized.startswith("/")
                or any(part in {"", ".", ".."} for part in normalized.split("/"))):
            raise ValueError("summary handoff paths must be safe relative paths")
        return normalized

    @field_validator("artifact_hashes")
    @classmethod
    def _snapshot_hashes(cls, value: Dict[str, str]) -> Dict[str, str]:
        import re

        expected = {"narration.json", "narrated_summary.json", "source_asset.json", "transcript.json"}
        if set(value) != expected or any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in value.values()):
            raise ValueError("summary handoff requires four SHA-256 snapshot hashes")
        return value


class ProjectManifest(BaseModel):
    """
    Unified project manifest for tracking pipeline state.
    
    This is the single source of truth for project progress.
    Enables idempotent execution and resume capability.
    """
    # Project identification
    project_id: str = Field(min_length=1, description="Unique project identifier")
    topic: str = Field(min_length=1, description="Video topic")
    # Phase 10: optional execution-side binding. Legacy manifests omit it.
    production_binding: Optional[ProductionBinding] = None
    # Explicit Track 9 -> canonical project import. None for every standard
    # execution; it must never be inferred from a filename or COMPLETE flag.
    source_summary_handoff: Optional[SourceSummaryHandoff] = None
    # G3: the PRODUCTION ROUTE Core compiled for this unit's execution
    # (route-compilation evidence).  Distinct from the plan binding above,
    # from scene provider provenance, from media/engine configuration, and
    # from accounting.  Captured once at project initialization; direct
    # engine runs that compiled no production route and every pre-G3 legacy
    # manifest keep this None, which reads as UNKNOWN and is never inferred.
    route_compilation: Optional[RouteCompilationRecord] = None

    
    # Pipeline state
    current_stage: PipelineStage = Field(default=PipelineStage.CONTENT)
    stages: Dict[str, StageResult] = Field(default_factory=dict)
    
    # Timestamps
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    
    # Artifact paths (populated as stages complete)
    content_plan_path: Optional[str] = None
    voice_audio_path: Optional[str] = None
    voice_timing_path: Optional[str] = None
    scene_manifest_path: Optional[str] = None
    scene_video_paths: List[str] = Field(default_factory=list)
    # Scene-level checkpoints survive an interrupted Flow generation and are
    # intentionally separate from the stage-level running/completed status.
    scene_checkpoints: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    captions_path: Optional[str] = None
    final_video_path: Optional[str] = None
    
    # Configuration snapshot
    flow_project_url: Optional[str] = None
    content_config: Dict[str, Any] = Field(default_factory=dict)
    voice_config: Dict[str, Any] = Field(default_factory=dict)
    scene_config: Dict[str, Any] = Field(default_factory=dict)
    assembly_config: Dict[str, Any] = Field(default_factory=dict)
    
    # Execution metadata
    dry_run: bool = Field(default=False)
    mock_mode: bool = Field(default=False)
    total_credits_used: int = Field(default=0)
    
    # Publishing state (Phase 7A). Deliberately isolated from production
    # stages; None until a publishing flow first touches the project.
    publication: Optional[PublicationRecord] = None
    # Phase 8A: platform-specific durable publication record for TikTok.
    # Kept separate from the YouTube record so neither platform's state
    # machine can interfere with the other.
    tiktok_publication: Optional[TikTokPublicationRecord] = None
    
    def initialize_stages(self) -> None:
        """Initialize all stages as pending."""
        for stage in PipelineStage.get_order():
            if stage.value not in self.stages:
                self.stages[stage.value] = StageResult.pending(stage)
    
    def get_stage_result(self, stage: PipelineStage) -> Optional[StageResult]:
        """Get result for a specific stage."""
        return self.stages.get(stage.value)
    
    def set_stage_result(self, result: StageResult) -> None:
        """Set result for a stage and update timestamp."""
        self.stages[result.stage.value] = result
        self.updated_at = datetime.now().isoformat()
        
        # Update current stage if completed
        if result.status == StageStatus.COMPLETED:
            next_stage = result.stage.next_stage()
            if next_stage:
                self.current_stage = next_stage
            else:
                self.current_stage = PipelineStage.COMPLETE
                self.completed_at = datetime.now().isoformat()
    
    def is_stage_completed(self, stage: PipelineStage) -> bool:
        """Check if a stage is completed."""
        result = self.get_stage_result(stage)
        return result is not None and result.status == StageStatus.COMPLETED
    
    def is_stage_failed(self, stage: PipelineStage) -> bool:
        """Check if a stage has failed."""
        result = self.get_stage_result(stage)
        return result is not None and result.status == StageStatus.FAILED
    
    def get_next_pending_stage(self) -> Optional[PipelineStage]:
        """Get the next stage that needs to be executed."""
        for stage in PipelineStage.get_order():
            if stage == PipelineStage.COMPLETE:
                continue
            result = self.get_stage_result(stage)
            if result is None or result.status in (StageStatus.PENDING, StageStatus.FAILED):
                return stage
        return None
    
    def can_resume(self) -> bool:
        """Check if pipeline can be resumed."""
        # Can resume if not complete
        if self.current_stage == PipelineStage.COMPLETE:
            return False
        return True
    
    def reset_running_stages(self) -> None:
        """Reset any 'running' stages to 'failed' for resume."""
        for stage_key, result in self.stages.items():
            if result.status == StageStatus.RUNNING:
                # Mark as failed so it will be retried
                self.stages[stage_key] = StageResult.failed(
                    stage=result.stage,
                    error="Stage was interrupted (running at resume time)",
                    started_at=result.started_at,
                )
    
    def get_completed_stages(self) -> List[PipelineStage]:
        """Get list of completed stages."""
        completed = []
        for stage in PipelineStage.get_order():
            if self.is_stage_completed(stage):
                completed.append(stage)
        return completed
    
    def get_failed_stage(self) -> Optional[PipelineStage]:
        """Get the failed stage if any."""
        for stage in PipelineStage.get_order():
            if self.is_stage_failed(stage):
                return stage
        return None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        data = self.model_dump()
        # Convert StageResult objects
        data["stages"] = {
            k: v.model_dump() if isinstance(v, StageResult) else v
            for k, v in self.stages.items()
        }
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> "ProjectManifest":
        """Create from dictionary."""
        # Convert stage dicts back to StageResult
        if "stages" in data:
            data["stages"] = {
                k: StageResult(**v) if isinstance(v, dict) else v
                for k, v in data["stages"].items()
            }
        # Convert the durable publication records back to their models
        if isinstance(data.get("publication"), dict):
            data["publication"] = PublicationRecord.model_validate(data["publication"])
        if isinstance(data.get("tiktok_publication"), dict):
            data["tiktok_publication"] = TikTokPublicationRecord.model_validate(
                data["tiktok_publication"]
            )
        return cls(**data)


class OrchestratorConfig(BaseModel):
    """Configuration for the production orchestrator."""
    # Required
    topic: str = Field(min_length=3, description="Video topic")
    # Empty string is valid for visual_mode='local' runs, which never touch
    # Google Flow; Flow-based runs must supply a real project URL (produce.py
    # enforces this at the CLI boundary).
    flow_project_url: str = Field(default="", description="Google Flow project URL")
    
    # Content settings
    niche: str = Field(default="educational")
    target_audience: str = Field(default="general audience")
    tone: str = Field(default="curious and cinematic")
    target_duration: int = Field(default=30, ge=10, le=180)
    platform: str = Field(default="youtube_shorts")
    language: str = Field(default="en")
    content_goal: str = Field(default="education")
    production_context: Optional[Dict[str, Any]] = None
    # Phase 10 exact-plan seam. Runtime path only; never durable identity.
    supplied_content_plan_path: Optional[Path] = None

    # Phase 10 binding inputs. The engine captures the actual directory name
    # as ProductionBinding.project_id during initialization.
    production_id: Optional[str] = None
    unit_id: Optional[str] = None
    production_plan_version: Optional[int] = Field(default=None, ge=1)
    unit_plan_identity: Optional[str] = None
    unit_plan_version: Optional[int] = Field(default=None, ge=1)
    unit_plan_content_hash: Optional[str] = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    # G3: one-way runtime carrier for the route-compilation evidence produced
    # at the production route seam
    # (``ProductionExecutor._config_for_unit`` -> ``apply_production_route``).
    # The pipeline captures it into the freshly initialized project manifest
    # (``ProjectManifest.route_compilation``) — the durable home of the
    # evidence.  It is NOT a routing knob: nothing reads it back to decide
    # execution behaviour, it is never part of the content/voice/scene/
    # assembly config snapshots, and it carries no provider, model, media
    # source, engine, audio, cost, or accounting data.
    route_compilation: Optional[RouteCompilationRecord] = None

    call_to_action: Optional[str] = None
    style: str = Field(default="fast-paced documentary")
    number_of_scenes: int = Field(default=6, ge=2, le=20)
    
    # Voice settings
    voice: str = Field(default="en-US-GuyNeural")
    voice_rate: str = Field(default="+0%")
    voice_pitch: str = Field(default="+0Hz")
    voice_volume: str = Field(default="+0%")
    # Local-first product runs may use the existing synthetic voice provider so
    # validation never requires an external TTS network call.  Edge TTS remains
    # the legacy default for explicitly configured non-local runs.
    voice_provider: str = Field(default="edge_tts")
    
    # Scene settings
    # ``scene_model`` is the Flow-engine scene model (the Flow CLI supplies
    # it explicitly via produce.py --scene-model, defaulting to the Flow
    # module's own FLOW_SCENE_MODEL).  It is deliberately NOT defaulted to
    # the Flow-era identity here: non-Flow executions must never persist a
    # fake "Veo 3.1 - Lite" model label (Phase 11C Track 1 hardening,
    # defect D7).  A non-Flow scene_config records model=None; the ACTUAL
    # provider/model truth of executed scenes lives in execution provenance
    # (Batch 1F) and accounting (Batch 1H).
    scene_model: Optional[str] = Field(default=None)
    scene_aspect_ratio: str = Field(default="9:16")
    scene_duration: int = Field(default=4, ge=1, le=60)
    max_scenes_per_run: int = Field(default=10, ge=1)
    credit_limit: int = Field(default=100, ge=1)
    # Step 6A: project-level visual mode. Persisted into manifest.scene_config
    # before any scene generation; the persisted value is authoritative on resume.
    visual_mode: str = Field(default="google_flow")
    # Step 6B (hybrid mode only): default provider for unrouted scenes
    # (never google_flow) plus explicit per-scene overrides, e.g.
    # {"scene_003": "google_flow"}. Persisted into scene_config before generation.
    visual_default_provider: str = Field(default="image_motion")
    visual_scene_routing: Dict[str, str] = Field(default_factory=dict)
    # Step 6C: explicit --asset SCENE_ID=PATH intake mappings. Used ONLY for
    # first-time intake; never restored on resume (project-owned copies are
    # authoritative afterwards).
    asset_mappings: Dict[str, str] = Field(default_factory=dict)
    # Phase 6F: automatic visual source selector. Empty = not configured
    # (missing assets fail honestly). "fixture" = deterministic local fixture
    # for offline validation; any other value maps to the dormant external
    # adapter boundary, which never consumes paid quota or makes a call.
    image_source: str = Field(
        default_factory=lambda: (os.getenv("AVM_IMAGE_SOURCE") or "").strip()
    )
    # Phase 7B: origin identifier recorded as provenance for externally
    # obtained video clips routed through the external_video provider
    # (e.g. "google_flow_private"). Provenance metadata only — never a
    # routable provider identity. Empty = truthful generic provenance
    # ("unspecified_external").
    external_video_source: str = Field(
        default_factory=lambda: (os.getenv("AVM_EXTERNAL_VIDEO_SOURCE") or "").strip()
    )
    # Phase 7E: OpenRouter is a generic provider; model and billing controls
    # are persisted with the project so resume cannot silently change them.
    openrouter_model: Optional[str] = Field(
        default_factory=lambda: (os.getenv("AVM_OPENROUTER_MODEL") or "").strip() or None
    )
    # Batch 1F (configured-model evidence): the configured Alibaba video model
    # rides with the scene configuration snapshot so CONFIGURED vs ACTUAL
    # provenance stays distinguishable. Model selection/resolution itself is
    # unchanged (the adapter still reads the env/default); this is
    # configuration evidence and is never actual execution provenance.
    alibaba_model: Optional[str] = Field(
        default_factory=lambda: (os.getenv("AVM_ALIBABA_VIDEO_MODEL") or "").strip() or None
    )
    openrouter_resolution: Optional[str] = Field(
        default_factory=lambda: (os.getenv("AVM_OPENROUTER_RESOLUTION") or "").strip() or None
    )
    openrouter_allow_paid: bool = Field(
        default_factory=lambda: os.getenv("AVM_OPENROUTER_ALLOW_PAID", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    openrouter_allow_paid_fallback: bool = Field(
        default_factory=lambda: os.getenv("AVM_OPENROUTER_ALLOW_PAID_FALLBACK", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    openrouter_generate_audio: bool = Field(
        default_factory=lambda: os.getenv("AVM_OPENROUTER_GENERATE_AUDIO", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    
    # Assembly settings
    resolution_width: int = Field(default=1080, ge=1)
    resolution_height: int = Field(default=1920, ge=1)
    music_volume: float = Field(default=0.15, ge=0, le=1.0)
    audio_policy: AudioPolicy = Field(
        default=DEFAULT_AUDIO_POLICY,
        description="Explicit final-audio policy persisted in the project manifest",
    )
    # V1 AUDIO POLICY FIX: the production-level audio INTENT that compiled into
    # ``audio_policy`` above, plus how it was established.  Both are EVIDENCE
    # fields persisted into the assembly config snapshot so a project can
    # explain "requested intent" separately from "effective policy".  They are
    # runtime carriers compiled at the production/unit seam
    # (``ProductionExecutor._config_for_unit`` -> ``compile_audio_policy``);
    # a direct engine run leaves them None, which reads as UNKNOWN and is never
    # back-inferred from narration text, the route, or a provider name.
    audio_intent: Optional[str] = Field(
        default=None,
        description="Requested production audio intent (narration/native/"
        "native_plus_narration/none), when compiled from production intent",
    )
    audio_intent_source: Optional[str] = Field(
        default=None,
        description="How the effective audio intent was established: "
        "explicit_audio_intent | backward_compatible_default",
    )
    enable_captions: bool = Field(default=True)
    # Phase 6F: caption presentation preset (minimal/standard/bold/viral).
    # Persisted into assembly_config so resume reproduces the same
    # presentation instead of silently changing with future defaults.
    caption_preset: str = Field(
        default_factory=lambda: (os.getenv("AVM_CAPTION_PRESET") or "standard").strip()
    )
    
    # Execution settings
    dry_run: bool = Field(default=False, description="Verify without generating")
    ui_verify_only: bool = Field(default=False, description="Verify Flow UI without submitting generation")
    voice_only: bool = Field(default=False, description="Stop after successful voice generation")
    mock_mode: bool = Field(default=False, description="Use mock providers")
    skip_confirmation: bool = Field(default=False)
    resume_project_id: Optional[str] = Field(default=None, description="Resume existing project")
    
    @field_validator("platform")
    @classmethod
    def validate_platform(cls, v: str) -> str:
        valid = {"youtube_shorts", "tiktok", "instagram_reels"}
        if v.lower() not in valid:
            raise ValueError(f"Invalid platform. Valid: {valid}")
        return v.lower()
    
    @field_validator("scene_aspect_ratio")
    @classmethod
    def validate_aspect_ratio(cls, v: str) -> str:
        valid = {"9:16", "16:9", "1:1"}
        if v not in valid:
            raise ValueError(f"Invalid aspect ratio. Valid: {valid}")
        return v

    @field_validator("audio_policy", mode="before")
    @classmethod
    def validate_audio_policy(cls, v) -> AudioPolicy:
        return normalize_audio_policy(v)

    @field_validator("audio_intent", mode="before")
    @classmethod
    def validate_audio_intent(cls, v):
        """Normalize the requested audio intent; ``None`` stays UNKNOWN.

        ``None`` is preserved verbatim (a direct engine run compiled no
        production intent, so the requested intent is genuinely unknown and is
        never back-inferred).  A declared value must be a real intent: an
        unknown string fails loudly rather than silently becoming narration.
        """
        if v is None or not str(v).strip():
            return None
        from app.production.models import normalize_audio_intent

        return normalize_audio_intent(v)
    
    def to_content_brief_dict(self) -> dict:
        """Convert to ContentBrief parameters."""
        return {
            "topic": self.topic,
            "niche": self.niche,
            "target_audience": self.target_audience,
            "tone": self.tone,
            "target_duration": self.target_duration,
            "platform": self.platform,
            "language": self.language,
            "content_goal": self.content_goal,
            "call_to_action": self.call_to_action,
            "style": self.style,
            "number_of_scenes": self.number_of_scenes,
            "production_context": self.production_context,
        }
    
    def to_voice_config_dict(self) -> dict:
        """Convert to voice generation parameters."""
        return {
            "voice": self.voice,
            "rate": self.voice_rate,
            "pitch": self.voice_pitch,
            "volume": self.voice_volume,
        }
    
    @field_validator("visual_mode")
    @classmethod
    def validate_visual_mode(cls, v: str) -> str:
        from app.orchestrator.visual_providers import normalize_visual_mode
        return normalize_visual_mode(v)

    @field_validator("visual_default_provider")
    @classmethod
    def validate_visual_default_provider(cls, v: str) -> str:
        from app.orchestrator.visual_providers import (
            normalize_provider,
            GOOGLE_FLOW_PROVIDER,
        )
        provider = normalize_provider(v)
        if provider == GOOGLE_FLOW_PROVIDER:
            raise ValueError(
                "visual_default_provider cannot be google_flow; Flow scenes "
                "require explicit per-scene routing overrides."
            )
        return provider

    @field_validator("visual_scene_routing")
    @classmethod
    def validate_visual_scene_routing(cls, v: Dict[str, str]) -> Dict[str, str]:
        from app.orchestrator.visual_providers import normalize_provider
        return {scene_id.strip(): normalize_provider(p) for scene_id, p in v.items()}

    @field_validator("caption_preset")
    @classmethod
    def validate_caption_preset(cls, v):
        from app.assembly.caption_presets import validate_preset
        return validate_preset(v)

    @field_validator("image_source")
    @classmethod
    def normalize_image_source(cls, v):
        return (v or "").strip()

    def to_scene_config_dict(self) -> dict:
        """Convert to scene generation parameters."""
        return {
            "model": self.scene_model,
            "aspect_ratio": self.scene_aspect_ratio,
            "duration": self.scene_duration,
            "max_per_run": self.max_scenes_per_run,
            "credit_limit": self.credit_limit,
            "visual_mode": self.visual_mode,
            "visual_default_provider": self.visual_default_provider,
            "visual_scene_routing": dict(self.visual_scene_routing),
            "visual_source": self.image_source,
            "openrouter_model": self.openrouter_model,
            # Batch 1F: configured-model evidence for the Alibaba adapter
            # (selection stays env/default inside the adapter; this snapshot
            # only records what was configured, never what actually ran).
            "alibaba_model": self.alibaba_model,
            "openrouter_resolution": self.openrouter_resolution,
            "openrouter_allow_paid": self.openrouter_allow_paid,
            "openrouter_allow_paid_fallback": self.openrouter_allow_paid_fallback,
            "openrouter_generate_audio": self.openrouter_generate_audio,
        }
    
    def to_assembly_config_dict(self) -> dict:
        """Convert to assembly parameters."""
        return {
            "resolution_width": self.resolution_width,
            "resolution_height": self.resolution_height,
            "music_volume": self.music_volume,
            # Effective, compiled policy — what assembly actually obeys.
            "audio_policy": self.audio_policy.value,
            # V1 AUDIO POLICY FIX: requested-intent evidence, persisted beside
            # the effective policy so the audio decision stays auditable
            # (requested intent vs compiled policy vs what actually ran).
            # None on a direct engine run = UNKNOWN, never back-inferred.
            "audio_intent": self.audio_intent,
            "audio_intent_source": self.audio_intent_source,
            "enable_captions": self.enable_captions,
            "caption_preset": self.caption_preset,
        }
