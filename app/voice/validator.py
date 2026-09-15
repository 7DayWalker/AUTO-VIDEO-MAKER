"""
AUTO Video Maker - Voice Validator

Audio file validation using FFprobe.
"""

import subprocess
import json
import logging
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel

from app.assembly.ffmpeg import resolve_ffprobe
from app.voice.exceptions import VoiceValidationError

logger = logging.getLogger(__name__)


class AudioValidationResult(BaseModel):
    """Result of audio file validation."""
    valid: bool
    errors: List[str] = []
    warnings: List[str] = []
    duration: Optional[float] = None
    codec: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    bitrate: Optional[int] = None
    file_size: Optional[int] = None


def validate_audio_file(file_path: Path) -> AudioValidationResult:
    """
    Validate an audio file using FFprobe.
    
    Checks:
    - File exists
    - File size > 0
    - Valid audio container
    - Audio stream exists
    - Duration > 0
    
    Args:
        file_path: Path to the audio file.
    
    Returns:
        AudioValidationResult with validation details.
    """
    errors = []
    warnings = []
    
    # Check file exists
    if not file_path.exists():
        return AudioValidationResult(
            valid=False,
            errors=[f"File does not exist: {file_path}"],
        )
    
    # Check file size
    file_size = file_path.stat().st_size
    if file_size == 0:
        return AudioValidationResult(
            valid=False,
            errors=["File is empty (0 bytes)"],
            file_size=0,
        )
    
    # Use FFprobe to get audio info
    ffprobe = resolve_ffprobe()
    if not ffprobe:
        # The production contract requires FFprobe to confirm a playable
        # stream.  Fail truthfully and actionably; never accept unverified
        # audio.
        logger.error("FFprobe is not available; audio cannot be accepted")
        return AudioValidationResult(
            valid=False,
            errors=[
                "FFprobe executable not found; audio validation is unavailable. "
                "Install FFmpeg (both ffmpeg and ffprobe) and add it to PATH, "
                "or set FFMPEG_PATH (ffprobe is derived from the same "
                "directory) / FFPROBE_PATH."
            ],
            file_size=file_size,
        )
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        if result.returncode != 0:
            return AudioValidationResult(
                valid=False,
                errors=[f"FFprobe failed: {result.stderr[:200]}"],
                file_size=file_size,
            )
        
        data = json.loads(result.stdout)
        
    except FileNotFoundError:
        # The production contract requires FFprobe to confirm a playable stream.
        logger.error("FFprobe is not available; audio cannot be accepted")
        return AudioValidationResult(
            valid=False,
            errors=["FFprobe executable not found; audio validation is unavailable"],
            file_size=file_size,
        )
    
    except subprocess.TimeoutExpired:
        return AudioValidationResult(
            valid=False,
            errors=["FFprobe timed out"],
            file_size=file_size,
        )
    
    except json.JSONDecodeError as e:
        return AudioValidationResult(
            valid=False,
            errors=[f"Failed to parse FFprobe output: {e}"],
            file_size=file_size,
        )
    
    # Find audio stream
    audio_stream = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "audio":
            audio_stream = stream
            break
    
    if not audio_stream:
        return AudioValidationResult(
            valid=False,
            errors=["No audio stream found in file"],
            file_size=file_size,
        )
    
    # Extract audio info
    codec = audio_stream.get("codec_name")
    sample_rate = int(audio_stream.get("sample_rate", 0)) if audio_stream.get("sample_rate") else None
    channels = audio_stream.get("channels")
    
    # Get duration from format or stream
    duration = None
    if "format" in data and "duration" in data["format"]:
        duration = float(data["format"]["duration"])
    elif "duration" in audio_stream:
        duration = float(audio_stream["duration"])
    
    # Get bitrate
    bitrate = None
    if "format" in data and "bit_rate" in data["format"]:
        bitrate = int(data["format"]["bit_rate"])
    elif "bit_rate" in audio_stream:
        bitrate = int(audio_stream["bit_rate"])
    
    # Validate duration
    if duration is None or duration <= 0:
        errors.append("Invalid or zero duration")
    
    # Validate sample rate
    if sample_rate and sample_rate < 8000:
        warnings.append(f"Low sample rate: {sample_rate}Hz")
    
    return AudioValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        duration=duration,
        codec=codec,
        sample_rate=sample_rate,
        channels=channels,
        bitrate=bitrate,
        file_size=file_size,
    )


def get_audio_duration(file_path: Path) -> Optional[float]:
    """
    Get audio duration using FFprobe.
    
    Args:
        file_path: Path to the audio file.
    
    Returns:
        Duration in seconds, or None if unavailable.
    """
    result = validate_audio_file(file_path)
    return result.duration


def validate_and_raise(file_path: Path) -> AudioValidationResult:
    """
    Validate audio file and raise on errors.
    
    Args:
        file_path: Path to the audio file.
    
    Returns:
        AudioValidationResult if valid.
    
    Raises:
        VoiceValidationError: If validation fails.
    """
    result = validate_audio_file(file_path)
    
    if not result.valid:
        raise VoiceValidationError(result.errors)
    
    return result
