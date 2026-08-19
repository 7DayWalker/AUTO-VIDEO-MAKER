"""
AUTO Video Maker - Content Validator

Validates production plans against requirements.
"""

from typing import List, Optional
from pydantic import BaseModel
import logging

from app.content.models import ProductionPlan, ContentBrief
from app.content.config import DURATION_TOLERANCE
from app.content.exceptions import ValidationError, DurationError

logger = logging.getLogger(__name__)


class ValidationResult(BaseModel):
    """Result of validation."""
    valid: bool
    errors: List[str] = []
    warnings: List[str] = []
    calculated_duration: Optional[float] = None
    target_duration: Optional[float] = None


def validate_production_plan(
    plan: ProductionPlan,
    brief: ContentBrief = None,
    tolerance: float = DURATION_TOLERANCE,
) -> ValidationResult:
    """
    Validate a production plan.
    
    Checks:
    - Project ID is valid
    - Title is not empty
    - Hook is not empty
    - Script is not empty
    - Scenes exist
    - Scene IDs are unique
    - Scene sequences are unique and valid
    - Each scene has narration
    - Each scene has visual_prompt
    - Scene durations are positive
    - Total duration is within tolerance
    - CTA is present when requested
    
    Args:
        plan: The production plan to validate.
        brief: Optional content brief for additional validation.
        tolerance: Duration tolerance in seconds.
    
    Returns:
        ValidationResult with errors and warnings.
    """
    errors = []
    warnings = []
    
    # Basic field validation
    if not plan.project_id or not plan.project_id.strip():
        errors.append("Project ID is empty")
    
    if not plan.title or not plan.title.strip():
        errors.append("Title is empty")
    
    if not plan.hook or not plan.hook.strip():
        errors.append("Hook is empty")
    
    if not plan.script or not plan.script.strip():
        errors.append("Script is empty")
    
    # Scene validation
    if not plan.scenes:
        errors.append("No scenes in production plan")
    else:
        # Check for duplicate IDs
        scene_ids = [s.id for s in plan.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            errors.append("Duplicate scene IDs found")
        
        # Check for duplicate sequences
        sequences = [s.sequence for s in plan.scenes]
        if len(sequences) != len(set(sequences)):
            errors.append("Duplicate scene sequences found")
        
        # Check sequence order
        expected_seqs = list(range(1, len(plan.scenes) + 1))
        if sorted(sequences) != expected_seqs:
            errors.append(f"Invalid scene sequences: expected 1-{len(plan.scenes)}")
        
        # Validate each scene
        for scene in plan.scenes:
            if not scene.narration or not scene.narration.strip():
                errors.append(f"Scene {scene.id}: narration is empty")
            
            if not scene.visual_prompt or not scene.visual_prompt.strip():
                errors.append(f"Scene {scene.id}: visual_prompt is empty")
            
            if scene.estimated_duration <= 0:
                errors.append(f"Scene {scene.id}: duration must be positive")
            
            # Check if narration and visual_prompt are too similar (warning)
            if scene.narration and scene.visual_prompt:
                if scene.narration.strip() == scene.visual_prompt.strip():
                    warnings.append(
                        f"Scene {scene.id}: narration and visual_prompt are identical "
                        "(they should be different)"
                    )
    
    # Calculate actual duration
    calculated_duration = plan.calculate_duration()
    
    # Duration validation
    if brief:
        target_duration = brief.target_duration
        min_duration = target_duration - tolerance
        max_duration = target_duration + tolerance
        
        if calculated_duration < min_duration or calculated_duration > max_duration:
            errors.append(
                f"Duration {calculated_duration:.1f}s outside acceptable range "
                f"({min_duration:.1f}s - {max_duration:.1f}s)"
            )
        
        # Check CTA if requested
        if brief.call_to_action and not plan.call_to_action:
            warnings.append("CTA was requested but not included in plan")
        
        # Check scene count
        if len(plan.scenes) != brief.number_of_scenes:
            warnings.append(
                f"Scene count ({len(plan.scenes)}) differs from requested "
                f"({brief.number_of_scenes})"
            )
    else:
        target_duration = plan.estimated_duration
    
    # Check estimated vs calculated duration
    if abs(plan.estimated_duration - calculated_duration) > 1.0:
        warnings.append(
            f"Estimated duration ({plan.estimated_duration:.1f}s) differs from "
            f"calculated ({calculated_duration:.1f}s)"
        )
    
    # Check for hook scene
    hook_scenes = [s for s in plan.scenes if s.purpose == "hook"]
    if not hook_scenes:
        warnings.append("No scene with purpose 'hook' found")
    elif plan.scenes[0].purpose != "hook":
        warnings.append("First scene should have purpose 'hook'")
    
    # Check for CTA scene
    cta_scenes = [s for s in plan.scenes if s.purpose == "cta"]
    if plan.call_to_action and not cta_scenes:
        warnings.append("CTA text provided but no scene with purpose 'cta'")
    
    # Fact-check flag for educational content
    if brief and brief.content_goal == "education":
        if not plan.metadata.requires_fact_check:
            warnings.append("Educational content should have requires_fact_check=true")
    
    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        calculated_duration=calculated_duration,
        target_duration=target_duration,
    )


def validate_and_raise(
    plan: ProductionPlan,
    brief: ContentBrief = None,
    tolerance: float = DURATION_TOLERANCE,
) -> ValidationResult:
    """
    Validate a production plan and raise on errors.
    
    Args:
        plan: The production plan to validate.
        brief: Optional content brief.
        tolerance: Duration tolerance.
    
    Returns:
        ValidationResult if valid.
    
    Raises:
        ValidationError: If validation fails.
    """
    result = validate_production_plan(plan, brief, tolerance)
    
    if not result.valid:
        raise ValidationError(result.errors)
    
    return result
