"""
AUTO Video Maker - Voice Generation Engine

Text-to-speech voice generation for video narration.
"""

from app.voice.models import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceMetadata,
    SceneTiming,
    VoiceTiming,
)
from app.voice.generator import VoiceGenerator
from app.voice.validator import validate_audio_file, AudioValidationResult

__all__ = [
    "VoiceGenerationRequest",
    "VoiceGenerationResult",
    "VoiceMetadata",
    "SceneTiming",
    "VoiceTiming",
    "VoiceGenerator",
    "validate_audio_file",
    "AudioValidationResult",
]
