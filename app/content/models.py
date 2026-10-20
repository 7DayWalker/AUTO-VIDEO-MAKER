"""
AUTO Video Maker - Content Engine Data Models

Pydantic models for content briefs, scripts, scenes, and production plans.
"""

import re
from datetime import datetime
from typing import Any, Dict, Optional, List, Literal, Tuple
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.content.config import (
    SUPPORTED_PLATFORMS,
    SUPPORTED_LANGUAGES,
    SUPPORTED_CONTENT_GOALS,
    SCENE_PURPOSES,
    DEFAULT_TARGET_DURATION,
    DEFAULT_PLATFORM,
    DEFAULT_LANGUAGE,
    DEFAULT_TONE,
    DEFAULT_NICHE,
    DEFAULT_NUMBER_OF_SCENES,
    MIN_SCENE_DURATION,
    MAX_SCENE_DURATION,
)


class PlanningConstraints(BaseModel):
    """Batch 1E: generic capability-aware PLANNING constraints.

    Derived upstream from Production Route requirements plus provider/model
    capability facts, and attached to the ContentBrief as a PLANNING INPUT
    only.  Deliberately generic: never a provider name, model id, vendor, or
    execution configuration; never persisted into ``ProductionPlan``; never
    carried into ``OrchestratorConfig``.  The provider adapters remain the
    final authority at generation time.
    """

    model_config = ConfigDict(frozen=True)

    #: Exact per-scene durations (integer seconds) the resolved video model
    #: accepts, when authoritatively known; None = no duration constraint.
    allowed_scene_durations: Optional[Tuple[int, ...]] = None
    #: Generic evidence label for where the constraint came from.
    source: str = "model_capability"


class ContentBrief(BaseModel):
    """
    Input specification for content generation.
    
    Defines what kind of video content to create.
    """
    topic: str = Field(min_length=3, max_length=500, description="Main topic or title")
    niche: str = Field(default=DEFAULT_NICHE, description="Content niche/category")
    target_audience: str = Field(default="general audience", description="Target audience")
    tone: str = Field(default=DEFAULT_TONE, description="Tone and style of narration")
    target_duration: int = Field(
        default=DEFAULT_TARGET_DURATION,
        ge=10,
        le=180,
        description="Target duration in seconds"
    )
    platform: str = Field(default=DEFAULT_PLATFORM, description="Target platform")
    language: str = Field(default=DEFAULT_LANGUAGE, description="Content language code")
    content_goal: str = Field(default="education", description="Primary content goal")
    call_to_action: Optional[str] = Field(
        default=None,
        description="Call to action text"
    )
    style: str = Field(default="engaging documentary", description="Visual/narrative style")
    number_of_scenes: int = Field(
        default=DEFAULT_NUMBER_OF_SCENES,
        ge=2,
        le=20,
        description="Target number of scenes"
    )
    # Phase 10: optional production-derived creative context.  None is the
    # exact legacy path; this field never carries execution state or routing.
    production_context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional production-derived planning context",
    )
    # Batch 1E: generic capability-aware planning constraints (e.g. allowed
    # scene durations) derived from route requirements + provider/model
    # capability facts.  PLANNING INPUT only — additive; legacy briefs
    # without this field behave exactly as before.
    planning_constraints: Optional[PlanningConstraints] = Field(
        default=None,
        description=(
            "Optional generic planning constraints for capability-aware "
            "planning (planning input only; never execution configuration)"
        ),
    )
    
    @field_validator("platform")
    @classmethod
    def validate_platform(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported platform '{v}'. Supported: {SUPPORTED_PLATFORMS}")
        return v
    
    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language '{v}'. Supported: {SUPPORTED_LANGUAGES}")
        return v
    
    @field_validator("content_goal")
    @classmethod
    def validate_content_goal(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in SUPPORTED_CONTENT_GOALS:
            raise ValueError(f"Unsupported goal '{v}'. Supported: {SUPPORTED_CONTENT_GOALS}")
        return v
    
    @field_validator("topic")
    @classmethod
    def validate_topic(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Topic cannot be empty")
        return v


class Script(BaseModel):
    """
    Generated script before scene breakdown.
    
    Contains the full narration text and metadata.
    """
    title: str = Field(min_length=1, max_length=200, description="Video title")
    hook: str = Field(min_length=1, description="Opening hook line")
    body: str = Field(min_length=1, description="Main script body")
    call_to_action: Optional[str] = Field(default=None, description="Closing CTA")
    estimated_duration: float = Field(ge=0, description="Estimated duration in seconds")
    
    @property
    def full_script(self) -> str:
        """Get the complete script text."""
        parts = [self.hook, self.body]
        if self.call_to_action:
            parts.append(self.call_to_action)
        return " ".join(parts)


class Scene(BaseModel):
    """
    A single scene in the production plan.
    
    Contains narration (what to say) and visual prompt (what to show).
    """
    id: str = Field(min_length=1, description="Unique scene identifier")
    sequence: int = Field(ge=1, description="Scene order (1-based)")
    narration: str = Field(min_length=1, description="Voiceover text for this scene")
    visual_prompt: str = Field(min_length=1, description="Visual generation prompt")
    estimated_duration: float = Field(
        ge=MIN_SCENE_DURATION,
        le=MAX_SCENE_DURATION,
        description="Scene duration in seconds"
    )
    purpose: str = Field(default="fact", description="Scene purpose/role")
    
    # Optional future-compatible fields
    transition: Optional[str] = Field(default=None, description="Transition to next scene")
    on_screen_text: Optional[str] = Field(default=None, description="Text overlay")
    visual_style: Optional[str] = Field(default=None, description="Visual style notes")
    camera_direction: Optional[str] = Field(default=None, description="Camera movement")
    sound_design: Optional[str] = Field(default=None, description="Sound effects notes")
    # Phase 11C Batch 1 hardening: minimal scene-level visual-source
    # intelligence hint for the visual ROUTING layer. "ai_image" = the scene
    # needs AI image generation (specific fictional/cinematic visual);
    # "stock" = real-world stock imagery can represent it; None = no hint
    # (routing uses the configured chain order). Additive planning signal
    # only — never execution state, never a provider identity.
    visual_source: Optional[str] = Field(
        default=None, description="Visual acquisition hint: ai_image | stock"
    )
    
    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        v = v.strip()
        # Ensure ID is filesystem-safe
        if not re.match(r'^[a-zA-Z0-9_-]+$', v):
            raise ValueError(f"Scene ID must be alphanumeric with underscores/hyphens: {v}")
        return v
    
    @field_validator("visual_source")
    @classmethod
    def validate_visual_source(cls, v: Optional[str]) -> Optional[str]:
        """Normalize the routing hint leniently (never reject a whole plan
        because a model phrased the hint slightly differently)."""
        if v is None:
            return None
        text = str(v).strip().lower().replace("-", "_").replace(" ", "_")
        if not text:
            return None
        ai_values = {
            "ai_image", "ai", "ai_generated", "ai_gen", "generated",
            "generate", "generation", "synthetic",
        }
        stock_values = {
            "stock", "stock_photo", "stock_image", "photo", "photography",
            "real", "realistic", "real_world",
        }
        if text in ai_values:
            return "ai_image"
        if text in stock_values:
            return "stock"
        return None
    
    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in SCENE_PURPOSES:
            # Allow custom purposes but normalize
            return v
        return v


class ProductionMetadata(BaseModel):
    """Metadata for the production plan."""
    platform: str = Field(default=DEFAULT_PLATFORM)
    language: str = Field(default=DEFAULT_LANGUAGE)
    niche: Optional[str] = None
    target_audience: Optional[str] = None
    content_goal: Optional[str] = None
    style: Optional[str] = None
    requires_fact_check: bool = Field(
        default=True,
        description="Whether content should be fact-checked"
    )
    generated_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="Generation timestamp"
    )
    provider: Optional[str] = Field(default=None, description="LLM provider used")
    model: Optional[str] = Field(default=None, description="LLM model used")


class ProductionPlan(BaseModel):
    """
    Complete production plan for video assembly.
    
    This is the main output of the content engine.
    """
    project_id: str = Field(min_length=1, description="Unique project identifier")
    title: str = Field(min_length=1, max_length=200, description="Video title")
    hook: str = Field(min_length=1, description="Opening hook")
    script: str = Field(min_length=1, description="Full narration script")
    call_to_action: Optional[str] = Field(default=None, description="Closing CTA")
    estimated_duration: float = Field(ge=0, description="Total estimated duration")
    scenes: List[Scene] = Field(min_length=1, description="List of scenes")
    metadata: ProductionMetadata = Field(default_factory=ProductionMetadata)
    
    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, v: str) -> str:
        v = v.strip()
        # Ensure project ID is filesystem-safe
        if not re.match(r'^[a-zA-Z0-9_-]+$', v):
            raise ValueError(f"Project ID must be alphanumeric with underscores/hyphens: {v}")
        return v
    
    @model_validator(mode="after")
    def validate_scenes(self):
        """Validate scene consistency."""
        if not self.scenes:
            raise ValueError("Production plan must have at least one scene")
        
        # Check for duplicate IDs
        ids = [s.id for s in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate scene IDs found")
        
        # Check for duplicate sequences
        seqs = [s.sequence for s in self.scenes]
        if len(seqs) != len(set(seqs)):
            raise ValueError("Duplicate scene sequences found")
        
        # Check sequence order
        expected_seqs = list(range(1, len(self.scenes) + 1))
        if sorted(seqs) != expected_seqs:
            raise ValueError(f"Scene sequences must be 1 to {len(self.scenes)}")
        
        return self
    
    def calculate_duration(self) -> float:
        """Calculate total duration from scene durations."""
        return sum(scene.estimated_duration for scene in self.scenes)
    
    def get_scene_by_id(self, scene_id: str) -> Optional[Scene]:
        """Get a scene by its ID."""
        for scene in self.scenes:
            if scene.id == scene_id:
                return scene
        return None
    
    def get_scene_by_sequence(self, sequence: int) -> Optional[Scene]:
        """Get a scene by its sequence number."""
        for scene in self.scenes:
            if scene.sequence == sequence:
                return scene
        return None


class ContentGenerationResult(BaseModel):
    """Result of content generation operation."""
    success: bool
    plan: Optional[ProductionPlan] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    attempts: int = Field(default=1, ge=1)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    # Step 6 (additive, model-centric AI Brain evidence):
    #
    # ``model`` above is the PROVIDER WIRE MODEL (e.g. ``qwen3.8-27b``) — that
    # established meaning is preserved, never silently changed. The user-facing
    # CANONICAL MODEL identity (e.g. ``qwen-3.8-flash``) therefore needs its
    # own field; no existing field can carry it without semantic corruption.
    #
    # None = UNKNOWN: the legacy provider-first path, mock and local execution
    # legitimately have no canonical model, and it is NEVER inferred from the
    # provider model id.
    canonical_model_id: Optional[str] = None
    #: Raw provider-reported execution evidence, projected by
    #: ``app.content.execution_evidence`` into the established
    #: provenance/accounting vocabulary at the persistence seam. Mirrors
    #: ``SceneGenerationResult.verification`` on the video side. Empty for
    #: legacy results; never carries raw bodies, prompts or credentials.
    verification: Dict[str, Any] = Field(default_factory=dict)
    
    @classmethod
    def failure(
        cls,
        error: str,
        provider: str = None,
        model: str = None,
        attempts: int = 1,
        canonical_model_id: str = None,
        verification: Dict[str, Any] = None,
    ) -> "ContentGenerationResult":
        """Create a failure result."""
        return cls(
            success=False,
            error=error,
            provider=provider,
            model=model,
            attempts=attempts,
            canonical_model_id=canonical_model_id,
            verification=verification or {},
        )
    
    @classmethod
    def from_plan(
        cls,
        plan: ProductionPlan,
        provider: str,
        model: str,
        attempts: int = 1,
        warnings: List[str] = None,
        canonical_model_id: str = None,
        verification: Dict[str, Any] = None,
    ) -> "ContentGenerationResult":
        """Create a success result from a production plan."""
        return cls(
            success=True,
            plan=plan,
            provider=provider,
            model=model,
            attempts=attempts,
            warnings=warnings or [],
            canonical_model_id=canonical_model_id,
            verification=verification or {},
        )


def generate_project_id(topic: str) -> str:
    """
    Generate a safe project ID from topic.
    
    Format: YYYYMMDD_topic_slug_XXX
    """
    # Get date prefix
    date_prefix = datetime.now().strftime("%Y%m%d")
    
    # Create slug from topic
    slug = topic.lower()
    slug = re.sub(r'[^a-z0-9\s]', '', slug)  # Remove special chars
    slug = re.sub(r'\s+', '_', slug)  # Replace spaces with underscores
    slug = slug[:30]  # Limit length
    slug = slug.strip('_')
    
    # Add random suffix for uniqueness
    import random
    suffix = f"{random.randint(0, 999):03d}"
    
    return f"{date_prefix}_{slug}_{suffix}"
