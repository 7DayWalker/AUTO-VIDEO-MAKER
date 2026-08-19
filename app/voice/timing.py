"""
AUTO Video Maker - Voice Timing

Scene timing calculation for voice generation.
"""

import logging
from typing import List

from app.voice.models import SceneTiming, VoiceTiming, NarrationSegment
from app.voice.config import VOICE_DURATION_WARNING_THRESHOLD
from app.voice.exceptions import VoiceTimingError

logger = logging.getLogger(__name__)


def calculate_scene_timings(
    segments: List[NarrationSegment],
    voiceover_duration: float = None,
) -> List[SceneTiming]:
    """
    Calculate cumulative scene timings from narration segments.
    
    Uses estimated durations to create timing boundaries.
    If actual voiceover duration is provided, scales timings proportionally.
    
    Args:
        segments: List of narration segments with estimated durations.
        voiceover_duration: Actual voiceover duration (optional).
    
    Returns:
        List of SceneTiming objects.
    """
    if not segments:
        raise VoiceTimingError("No segments provided for timing calculation")
    
    # Calculate total estimated duration
    total_estimated = sum(s.estimated_duration for s in segments)
    
    if total_estimated <= 0:
        raise VoiceTimingError("Total estimated duration must be positive")
    
    # Calculate scale factor if actual duration provided
    scale_factor = 1.0
    if voiceover_duration and voiceover_duration > 0:
        scale_factor = voiceover_duration / total_estimated
        logger.debug(f"Timing scale factor: {scale_factor:.3f}")
    
    # Build cumulative timings
    timings = []
    current_time = 0.0
    
    for segment in segments:
        # Scale the duration
        scaled_duration = segment.estimated_duration * scale_factor
        
        timing = SceneTiming(
            scene_id=segment.scene_id,
            sequence=segment.sequence,
            estimated_duration=segment.estimated_duration,
            start=round(current_time, 3),
            end=round(current_time + scaled_duration, 3),
        )
        timings.append(timing)
        
        current_time += scaled_duration
    
    return timings


def create_voice_timing(
    project_id: str,
    segments: List[NarrationSegment],
    voiceover_duration: float,
) -> VoiceTiming:
    """
    Create complete voice timing information.
    
    Args:
        project_id: Project identifier.
        segments: Narration segments.
        voiceover_duration: Actual voiceover duration.
    
    Returns:
        VoiceTiming with all timing information.
    """
    scene_timings = calculate_scene_timings(segments, voiceover_duration)
    
    return VoiceTiming.from_scenes(
        project_id=project_id,
        voiceover_duration=voiceover_duration,
        scene_timings=scene_timings,
    )


def check_duration_mismatch(
    actual_duration: float,
    estimated_duration: float,
    threshold: float = VOICE_DURATION_WARNING_THRESHOLD,
) -> tuple:
    """
    Check if duration mismatch exceeds threshold.
    
    Args:
        actual_duration: Actual voiceover duration.
        estimated_duration: Estimated total duration.
        threshold: Warning threshold in seconds.
    
    Returns:
        Tuple of (has_warning, difference, message).
    """
    difference = actual_duration - estimated_duration
    abs_difference = abs(difference)
    
    if abs_difference > threshold:
        if difference > 0:
            message = (
                f"Voiceover ({actual_duration:.1f}s) is {abs_difference:.1f}s longer "
                f"than estimated ({estimated_duration:.1f}s)"
            )
        else:
            message = (
                f"Voiceover ({actual_duration:.1f}s) is {abs_difference:.1f}s shorter "
                f"than estimated ({estimated_duration:.1f}s)"
            )
        return True, difference, message
    
    return False, difference, None


def get_scene_at_time(
    timings: List[SceneTiming],
    time_seconds: float,
) -> SceneTiming:
    """
    Get the scene that contains a specific time.
    
    Args:
        timings: List of scene timings.
        time_seconds: Time in seconds.
    
    Returns:
        SceneTiming containing the time, or last scene if past end.
    """
    if not timings:
        raise VoiceTimingError("No timings provided")
    
    for timing in timings:
        if timing.start <= time_seconds < timing.end:
            return timing
    
    # Return last scene if past end
    return timings[-1]
