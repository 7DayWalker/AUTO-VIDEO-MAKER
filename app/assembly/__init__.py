"""
AUTO Video Maker - Local Video Assembly Engine

This module provides deterministic video assembly capabilities for
creating short-form vertical videos (9:16) suitable for YouTube Shorts
and TikTok.

Core components:
- models: Pydantic data models for project definition
- validator: Media and project validation
- ffmpeg: FFmpeg abstraction layer
- video: Video normalization and concatenation
- audio: Voiceover and music mixing
- captions: SRT caption rendering
- pipeline: High-level assembly orchestration
- exceptions: Structured error handling
"""

from app.assembly.models import (
    AssemblyProject,
    Scene,
    AudioTrack,
    CaptionConfig,
    OutputConfig,
    VideoResolution,
    AssemblyResult,
)
from app.assembly.audio_contract import AudioMediaFacts, AudioPolicy
from app.assembly.pipeline import assemble_project
from app.assembly.media import inspect_audio
from app.assembly.exceptions import (
    AssemblyError,
    MediaValidationError,
    FFmpegNotFoundError,
    FFmpegExecutionError,
    InvalidProjectError,
    AudioMismatchError,
    CaptionError,
    OutputValidationError,
)

__all__ = [
    # Models
    "AssemblyProject",
    "Scene",
    "AudioTrack",
    "CaptionConfig",
    "OutputConfig",
    "VideoResolution",
    "AssemblyResult",
    "AudioMediaFacts",
    "AudioPolicy",
    # Pipeline
    "assemble_project",
    "inspect_audio",
    # Exceptions
    "AssemblyError",
    "MediaValidationError",
    "FFmpegNotFoundError",
    "FFmpegExecutionError",
    "InvalidProjectError",
    "AudioMismatchError",
    "CaptionError",
    "OutputValidationError",
]
