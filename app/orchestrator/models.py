"""
AUTO Video Maker - Orchestrator Models

Pydantic models for pipeline state management and configuration.
"""

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator

from app.publishing.models import PublicationRecord, TikTokPublicationRecord


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


class ProjectManifest(BaseModel):
    """
    Unified project manifest for tracking pipeline state.
    
    This is the single source of truth for project progress.
    Enables idempotent execution and resume capability.
    """
    # Project identification
    project_id: str = Field(min_length=1, description="Unique project identifier")
    topic: str = Field(min_length=1, description="Video topic")
    
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
    call_to_action: Optional[str] = None
    style: str = Field(default="fast-paced documentary")
    number_of_scenes: int = Field(default=6, ge=2, le=20)
    
    # Voice settings
    voice: str = Field(default="en-US-GuyNeural")
    voice_rate: str = Field(default="+0%")
    voice_pitch: str = Field(default="+0Hz")
    voice_volume: str = Field(default="+0%")
    
    # Scene settings
    scene_model: str = Field(default="Veo 3.1 - Lite")
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
    
    # Assembly settings
    resolution_width: int = Field(default=1080, ge=1)
    resolution_height: int = Field(default=1920, ge=1)
    music_volume: float = Field(default=0.15, ge=0, le=1.0)
    enable_captions: bool = Field(default=True)
    
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
        }
    
    def to_assembly_config_dict(self) -> dict:
        """Convert to assembly parameters."""
        return {
            "resolution_width": self.resolution_width,
            "resolution_height": self.resolution_height,
            "music_volume": self.music_volume,
            "enable_captions": self.enable_captions,
        }
