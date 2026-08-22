"""
AUTO Video Maker - Flow Scene Generation Models

Pydantic models for scene generation requests, results, and manifests.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, field_validator

from app.flow.scene_config import (
    FLOW_SCENE_MODEL,
    FLOW_SCENE_ASPECT_RATIO,
    FLOW_SCENE_DURATION,
    FLOW_SCENE_COUNT,
    FLOW_SCENE_AGENT_ENABLED,
)


class FlowGenerationConfig(BaseModel):
    """Configuration for a single Flow generation."""
    model: str = Field(default=FLOW_SCENE_MODEL, description="Veo model to use")
    aspect_ratio: str = Field(default=FLOW_SCENE_ASPECT_RATIO, description="Video aspect ratio")
    duration: int = Field(default=FLOW_SCENE_DURATION, ge=1, le=60, description="Duration in seconds")
    count: int = Field(default=FLOW_SCENE_COUNT, ge=1, le=4, description="Number of outputs")
    agent_enabled: bool = Field(default=FLOW_SCENE_AGENT_ENABLED, description="Agent mode")
    
    @field_validator("aspect_ratio")
    @classmethod
    def validate_aspect_ratio(cls, v: str) -> str:
        valid_ratios = {"9:16", "16:9", "1:1"}
        if v not in valid_ratios:
            raise ValueError(f"Invalid aspect ratio '{v}'. Valid: {valid_ratios}")
        return v


class SceneGenerationRequest(BaseModel):
    """Request to generate a single scene."""
    project_id: str = Field(min_length=1, description="Project identifier")
    scene_id: str = Field(min_length=1, description="Scene identifier")
    sequence: int = Field(ge=1, description="Scene sequence number")
    visual_prompt: str = Field(min_length=1, description="Visual prompt for generation")
    config: FlowGenerationConfig = Field(default_factory=FlowGenerationConfig)
    flow_project_url: Optional[str] = Field(default=None, description="Flow project URL")
    
    @field_validator("visual_prompt")
    @classmethod
    def validate_prompt(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Visual prompt cannot be empty")
        return v


class SceneGenerationResult(BaseModel):
    """Result of a scene generation attempt."""
    success: bool
    scene_id: str
    sequence: int
    status: str  # completed, failed, timeout, unknown_reconciliation_required, skipped, dry_run
    file_path: Optional[Path] = None
    file_size: Optional[int] = None
    actual_duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    codec: Optional[str] = None
    generation_cost: Optional[int] = None
    generation_started_at: Optional[str] = None
    generation_completed_at: Optional[str] = None
    downloaded_at: Optional[str] = None
    error: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    verification: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        arbitrary_types_allowed = True
    
    @classmethod
    def success_result(
        cls,
        scene_id: str,
        sequence: int,
        file_path: Path,
        file_size: int,
        actual_duration: float,
        width: int,
        height: int,
        codec: str,
        generation_cost: int = None,
        generation_started_at: str = None,
        generation_completed_at: str = None,
        downloaded_at: str = None,
        warnings: List[str] = None,
    ) -> "SceneGenerationResult":
        """Create a success result."""
        return cls(
            success=True,
            scene_id=scene_id,
            sequence=sequence,
            status="completed",
            file_path=file_path,
            file_size=file_size,
            actual_duration=actual_duration,
            width=width,
            height=height,
            codec=codec,
            generation_cost=generation_cost,
            generation_started_at=generation_started_at,
            generation_completed_at=generation_completed_at,
            downloaded_at=downloaded_at,
            warnings=warnings or [],
        )
    
    @classmethod
    def failure(
        cls,
        scene_id: str,
        sequence: int,
        error: str,
        status: str = "failed",
    ) -> "SceneGenerationResult":
        """Create a failure result."""
        return cls(
            success=False,
            scene_id=scene_id,
            sequence=sequence,
            status=status,
            error=error,
        )
    
    @classmethod
    def dry_run(
        cls,
        scene_id: str,
        sequence: int,
        generation_cost: int = None,
        verification: Dict[str, Any] = None,
    ) -> "SceneGenerationResult":
        """Create a dry-run result."""
        return cls(
            success=True,
            scene_id=scene_id,
            sequence=sequence,
            status="dry_run",
            generation_cost=generation_cost,
            verification=verification or {},
        )
    
    @classmethod
    def skipped(
        cls,
        scene_id: str,
        sequence: int,
        reason: str,
    ) -> "SceneGenerationResult":
        """Create a skipped result."""
        return cls(
            success=True,
            scene_id=scene_id,
            sequence=sequence,
            status="skipped",
            error=reason,
        )


class SceneMetadata(BaseModel):
    """Metadata for a generated scene."""
    project_id: str
    scene_id: str
    sequence: int
    source_prompt: str
    model: str
    aspect_ratio: str
    duration_requested: int
    output_count: int
    generation_cost: Optional[int] = None
    generation_started_at: Optional[str] = None
    generation_completed_at: Optional[str] = None
    downloaded_at: Optional[str] = None
    file_path: str
    file_size: int
    actual_duration: float
    width: int
    height: int
    codec: str
    status: str = "completed"
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return self.model_dump()


class SceneManifestEntry(BaseModel):
    """Entry in the scene manifest."""
    scene_id: str
    sequence: int
    file: str
    status: str
    submission_timestamp: Optional[str] = None
    generation_started_at: Optional[str] = None
    generation_completed_at: Optional[str] = None
    downloaded_at: Optional[str] = None
    flow_project_url: Optional[str] = None
    prompt_hash: Optional[str] = None
    prompt: Optional[str] = None
    generation_cost: Optional[int] = None
    checkpoint_evidence: Dict[str, Any] = Field(default_factory=dict)
    file_path: Optional[str] = None
    validation: Dict[str, Any] = Field(default_factory=dict)


class SceneManifest(BaseModel):
    """Manifest of all generated scenes for a project."""
    project_id: str
    scenes: List[SceneManifestEntry] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    
    def add_scene(
        self,
        scene_id: str,
        sequence: int,
        file: str,
        status: str,
        **metadata: Any,
    ) -> None:
        """Add or update a scene in the manifest."""
        # Remove existing entry if present
        self.scenes = [s for s in self.scenes if s.scene_id != scene_id]
        
        # Add new entry
        self.scenes.append(SceneManifestEntry(
            scene_id=scene_id,
            sequence=sequence,
            file=file,
            status=status,
            **{
                key: value for key, value in metadata.items()
                if key in SceneManifestEntry.model_fields
            },
        ))
        
        # Sort by sequence
        self.scenes.sort(key=lambda s: s.sequence)
        
        # Update timestamp
        self.updated_at = datetime.now().isoformat()
    
    def get_scene(self, scene_id: str) -> Optional[SceneManifestEntry]:
        """Get a scene entry by ID."""
        for scene in self.scenes:
            if scene.scene_id == scene_id:
                return scene
        return None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "project_id": self.project_id,
            "scenes": [s.model_dump() for s in self.scenes],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class FlowUIState(BaseModel):
    """Current state of the Flow UI."""
    connected: bool = False
    authenticated: bool = False
    on_project_page: bool = False
    agent_off: bool = False
    video_mode: bool = False
    model_verified: bool = False
    aspect_ratio_verified: bool = False
    duration_verified: bool = False
    count_verified: bool = False
    prompt_ready: bool = False
    generation_cost: Optional[int] = None
    generation_cost_verified: bool = False

    @property
    def ready_for_generation(self) -> bool:
        """Return whether every generation prerequisite has been verified."""
        return all([
            self.connected,
            self.authenticated,
            self.on_project_page,
            self.agent_off,
            self.video_mode,
            self.model_verified,
            self.aspect_ratio_verified,
            self.duration_verified,
            self.count_verified,
            self.prompt_ready,
        ])

    def get_missing_prerequisites(self) -> List[str]:
        """Return human-readable names for unverified prerequisites."""
        missing = []
        if not self.connected:
            missing.append("Chrome/CDP connection")
        if not self.authenticated:
            missing.append("Google authentication")
        if not self.on_project_page:
            missing.append("Flow project page")
        if not self.agent_off:
            missing.append("Agent OFF")
        if not self.video_mode:
            missing.append("Video mode")
        if not self.model_verified:
            missing.append("Model selection")
        if not self.aspect_ratio_verified:
            missing.append("Aspect ratio")
        if not self.duration_verified:
            missing.append("Duration")
        if not self.count_verified:
            missing.append("Output count")
        if not self.prompt_ready:
            missing.append("Prompt input")
        return missing


class FlowHealthResult(BaseModel):
    """Structured health inspection for the dedicated Flow page."""
    timestamp: str
    expected_url: Optional[str] = None
    actual_url: Optional[str] = None
    title: Optional[str] = None
    browser_connected: bool = False
    cdp_available: bool = False
    selected_page_reason: Optional[str] = None
    candidate_pages: List[Dict[str, Any]] = Field(default_factory=list)
    prompt_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    create_button: Dict[str, Any] = Field(default_factory=dict)
    detected_error_text: List[str] = Field(default_factory=list)
    blocking_error_elements: List[Dict[str, Any]] = Field(default_factory=list)
    non_blocking_error_elements: List[Dict[str, Any]] = Field(default_factory=list)
    generation_surface_state: Dict[str, Any] = Field(default_factory=dict)
    prompt_state: Dict[str, Any] = Field(default_factory=dict)
    create_state: Dict[str, Any] = Field(default_factory=dict)
    overlay_state: Dict[str, Any] = Field(default_factory=dict)
    error_classification: Optional[str] = None
    loading_indicators: List[str] = Field(default_factory=list)
    classification: Literal[
        "HEALTHY", "GENERATION_SURFACE_READY", "LOADING", "STALE",
        "GENERATION_SURFACE_UNAVAILABLE", "BLOCKING_APPLICATION_ERROR",
        "NON_BLOCKING_ERROR_MESSAGE", "BACKGROUND_ERROR", "STALE_ERROR_ELEMENT",
        "APPLICATION_ERROR",
        "NETWORK_ERROR", "UNKNOWN",
        "shutdown_timeout", "old_dedicated_process_alive",
        "profile_lock_conflict", "launch_returned_zero_cdp_unavailable",
        "dedicated_process_not_found", "cdp_port_not_open",
        "cdp_endpoint_unreachable", "cdp_wrong_profile_owner",
        "playwright_attach_failed",
    ] = "UNKNOWN"
    lifecycle_classification: Optional[str] = None
    original_exception: Optional[str] = None
    recovery_history: List[Dict[str, Any]] = Field(default_factory=list)
    diagnostic_path: Optional[str] = None
    screenshot_path: Optional[str] = None
    screenshot_error: Optional[str] = None

    @property
    def healthy(self) -> bool:
        # HEALTHY is retained for backwards-compatible persisted diagnostics;
        # new inspections use the more explicit generation-surface label.
        return self.classification in {"HEALTHY", "GENERATION_SURFACE_READY"}
