"""
AUTO Video Maker - Media and Project Validation

Validates media files and project definitions before processing.
"""

from pathlib import Path
from typing import List, Tuple
import logging

from app.assembly.config import (
    SUPPORTED_VIDEO_EXTENSIONS,
    SUPPORTED_AUDIO_EXTENSIONS,
    SUPPORTED_CAPTION_EXTENSIONS,
)
from app.assembly.exceptions import (
    MediaValidationError,
    InvalidProjectError,
)
from app.assembly.models import AssemblyProject, MediaInfo
from app.assembly.ffmpeg import probe_media, check_ffprobe_available

logger = logging.getLogger(__name__)


def validate_file_exists(path: Path, file_type: str = "file") -> None:
    """Validate that a file exists."""
    if not path.exists():
        raise MediaValidationError(str(path), f"{file_type} not found")
    if not path.is_file():
        raise MediaValidationError(str(path), f"{file_type} is not a file")


def validate_video_extension(path: Path) -> None:
    """Validate video file extension."""
    ext = path.suffix.lower()
    if ext not in SUPPORTED_VIDEO_EXTENSIONS:
        raise MediaValidationError(
            str(path),
            f"Unsupported video extension '{ext}'. Supported: {SUPPORTED_VIDEO_EXTENSIONS}"
        )


def validate_audio_extension(path: Path) -> None:
    """Validate audio file extension."""
    ext = path.suffix.lower()
    if ext not in SUPPORTED_AUDIO_EXTENSIONS:
        raise MediaValidationError(
            str(path),
            f"Unsupported audio extension '{ext}'. Supported: {SUPPORTED_AUDIO_EXTENSIONS}"
        )


def validate_caption_extension(path: Path) -> None:
    """Validate caption file extension."""
    ext = path.suffix.lower()
    if ext not in SUPPORTED_CAPTION_EXTENSIONS:
        raise MediaValidationError(
            str(path),
            f"Unsupported caption extension '{ext}'. Supported: {SUPPORTED_CAPTION_EXTENSIONS}"
        )


def validate_video_file(path: Path) -> MediaInfo:
    """
    Validate a video file completely.
    
    Returns MediaInfo if valid.
    Raises MediaValidationError if invalid.
    """
    validate_file_exists(path, "Video file")
    validate_video_extension(path)
    
    if not check_ffprobe_available():
        logger.warning("FFprobe not available, skipping deep validation")
        return MediaInfo(
            path=str(path),
            duration=0.0,
            file_size=path.stat().st_size,
            has_video=True,
        )
    
    info = probe_media(path)
    
    if not info.has_video:
        raise MediaValidationError(str(path), "File has no video stream")
    
    if info.duration <= 0:
        raise MediaValidationError(str(path), "Video has zero or negative duration")
    
    return info


def validate_audio_file(path: Path) -> MediaInfo:
    """
    Validate an audio file completely.
    
    Returns MediaInfo if valid.
    Raises MediaValidationError if invalid.
    """
    validate_file_exists(path, "Audio file")
    validate_audio_extension(path)
    
    if not check_ffprobe_available():
        logger.warning("FFprobe not available, skipping deep validation")
        return MediaInfo(
            path=str(path),
            duration=0.0,
            file_size=path.stat().st_size,
            has_audio=True,
        )
    
    info = probe_media(path)
    
    if not info.has_audio:
        raise MediaValidationError(str(path), "File has no audio stream")
    
    if info.duration <= 0:
        raise MediaValidationError(str(path), "Audio has zero or negative duration")
    
    return info


def validate_caption_file(path: Path) -> None:
    """Validate a caption/subtitle file."""
    validate_file_exists(path, "Caption file")
    validate_caption_extension(path)
    
    # Basic SRT validation
    if path.suffix.lower() == ".srt":
        try:
            content = path.read_text(encoding="utf-8")
            if not content.strip():
                raise MediaValidationError(str(path), "Caption file is empty")
            # Check for at least one timestamp pattern
            if "-->" not in content:
                raise MediaValidationError(str(path), "Invalid SRT format: no timestamps found")
        except UnicodeDecodeError:
            raise MediaValidationError(str(path), "Caption file is not valid UTF-8")


def validate_project(project: AssemblyProject, base_dir: Path = None) -> Tuple[List[MediaInfo], List[str]]:
    """
    Validate an entire assembly project.
    
    Args:
        project: The project to validate.
        base_dir: Base directory for resolving relative paths.
    
    Returns:
        Tuple of (list of MediaInfo for scenes, list of warnings).
    
    Raises:
        InvalidProjectError: If project is invalid.
        MediaValidationError: If any media file is invalid.
    """
    base_dir = base_dir or Path.cwd()
    warnings = []
    scene_infos = []
    
    # Validate scenes
    if not project.scenes:
        raise InvalidProjectError("Project must have at least one scene")
    
    seen_ids = set()
    for scene in project.scenes:
        # Check for duplicate IDs
        if scene.id in seen_ids:
            raise InvalidProjectError(f"Duplicate scene ID: {scene.id}")
        seen_ids.add(scene.id)
        
        # Validate video file
        video_path = Path(scene.video)
        if not video_path.is_absolute():
            video_path = base_dir / video_path
        
        info = validate_video_file(video_path)
        scene_infos.append(info)
        logger.info(f"Scene '{scene.id}': {info.duration:.2f}s, {info.width}x{info.height}")
    
    # Validate voiceover if provided
    if project.voiceover:
        vo_path = Path(project.voiceover)
        if not vo_path.is_absolute():
            vo_path = base_dir / vo_path
        validate_audio_file(vo_path)
        logger.info(f"Voiceover validated: {vo_path}")
    
    # Validate background music if provided
    if project.background_music:
        music_path = Path(project.background_music)
        if not music_path.is_absolute():
            music_path = base_dir / music_path
        validate_audio_file(music_path)
        logger.info(f"Background music validated: {music_path}")
    
    # Validate captions if enabled
    if project.captions.enabled:
        if not project.captions.source:
            raise InvalidProjectError("Captions enabled but no source file specified")
        
        caption_path = Path(project.captions.source)
        if not caption_path.is_absolute():
            caption_path = base_dir / caption_path
        validate_caption_file(caption_path)
        logger.info(f"Captions validated: {caption_path}")
    
    # Validate resolution
    if project.resolution.width <= 0 or project.resolution.height <= 0:
        raise InvalidProjectError("Invalid resolution: width and height must be positive")
    
    # Check aspect ratio consistency
    expected_ratio = project.aspect_ratio
    actual_ratio = project.resolution.aspect_ratio
    if expected_ratio == "9:16" and actual_ratio != "9:16":
        warnings.append(
            f"Resolution {project.resolution.width}x{project.resolution.height} "
            f"has aspect ratio {actual_ratio}, expected {expected_ratio}"
        )
    
    return scene_infos, warnings
