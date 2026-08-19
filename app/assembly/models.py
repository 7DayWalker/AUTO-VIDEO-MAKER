"""
AUTO Video Maker - Assembly Engine Data Models

Pydantic models for project definition and validation.
"""

from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator, model_validator


class VideoResolution(BaseModel):
    """Video resolution specification."""
    width: int = Field(ge=1, le=7680, description="Video width in pixels")
    height: int = Field(ge=1, le=4320, description="Video height in pixels")
    
    @property
    def aspect_ratio(self) -> str:
        """Calculate aspect ratio string."""
        from math import gcd
        divisor = gcd(self.width, self.height)
        w = self.width // divisor
        h = self.height // divisor
        return f"{w}:{h}"
    
    def is_vertical(self) -> bool:
        """Check if resolution is vertical (portrait)."""
        return self.height > self.width


class Scene(BaseModel):
    """A single video scene/clip in the project."""
    id: str = Field(min_length=1, description="Unique scene identifier")
    video: str = Field(min_length=1, description="Path to video file")
    duration: Optional[float] = Field(default=None, ge=0, description="Override duration in seconds")
    
    @field_validator("video")
    @classmethod
    def validate_video_path(cls, v: str) -> str:
        """Ensure video path is not empty."""
        if not v.strip():
            raise ValueError("Video path cannot be empty")
        return v.strip()


class AudioTrack(BaseModel):
    """Audio track configuration."""
    path: str = Field(min_length=1, description="Path to audio file")
    volume: float = Field(default=1.0, ge=0, le=2.0, description="Volume multiplier")
    loop: bool = Field(default=False, description="Loop audio if shorter than video")
    fade_out: Optional[float] = Field(default=None, ge=0, description="Fade out duration in seconds")


class CaptionConfig(BaseModel):
    """Caption/subtitle configuration."""
    enabled: bool = Field(default=False, description="Whether to render captions")
    source: Optional[str] = Field(default=None, description="Path to SRT/VTT file")
    font_size: Optional[int] = Field(default=None, ge=8, le=200, description="Override font size")
    font_color: Optional[str] = Field(default=None, description="Override font color")
    margin_bottom: Optional[int] = Field(default=None, ge=0, description="Override bottom margin")
    
    @model_validator(mode="after")
    def validate_source_when_enabled(self):
        """Ensure source is provided when captions are enabled."""
        if self.enabled and not self.source:
            raise ValueError("Caption source path required when captions are enabled")
        return self


class OutputConfig(BaseModel):
    """Output file configuration."""
    filename: str = Field(default="final_short.mp4", min_length=1, description="Output filename")
    directory: Optional[str] = Field(default=None, description="Output directory override")
    
    @field_validator("filename")
    @classmethod
    def validate_filename(cls, v: str) -> str:
        """Ensure filename has valid extension."""
        v = v.strip()
        if not v.lower().endswith(".mp4"):
            raise ValueError("Output filename must end with .mp4")
        return v


class AssemblyProject(BaseModel):
    """Complete project definition for video assembly."""
    project_id: str = Field(min_length=1, description="Unique project identifier")
    title: str = Field(default="Untitled", description="Project title")
    aspect_ratio: str = Field(default="9:16", description="Target aspect ratio")
    resolution: VideoResolution = Field(
        default_factory=lambda: VideoResolution(width=1080, height=1920)
    )
    scenes: List[Scene] = Field(min_length=1, description="List of video scenes")
    voiceover: Optional[str] = Field(default=None, description="Path to voiceover audio")
    background_music: Optional[str] = Field(default=None, description="Path to background music")
    music_volume: float = Field(default=0.15, ge=0, le=1.0, description="Background music volume")
    captions: CaptionConfig = Field(default_factory=CaptionConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    
    @field_validator("aspect_ratio")
    @classmethod
    def validate_aspect_ratio(cls, v: str) -> str:
        """Validate aspect ratio format."""
        v = v.strip()
        valid_ratios = {"9:16", "16:9", "1:1", "4:5", "4:3", "3:4"}
        if v not in valid_ratios:
            raise ValueError(f"Invalid aspect ratio '{v}'. Supported: {valid_ratios}")
        return v
    
    @model_validator(mode="after")
    def validate_resolution_matches_aspect(self):
        """Warn if resolution doesn't match declared aspect ratio."""
        # This is a soft check - we allow mismatches but could log a warning
        return self
    
    def get_scene_paths(self) -> List[Path]:
        """Get all scene video paths as Path objects."""
        return [Path(scene.video) for scene in self.scenes]
    
    def get_output_path(self, base_dir: Path) -> Path:
        """Get the full output path."""
        if self.output.directory:
            return Path(self.output.directory) / self.output.filename
        return base_dir / self.output.filename


class MediaInfo(BaseModel):
    """Information about a media file from ffprobe."""
    path: str
    duration: float = Field(ge=0)
    width: Optional[int] = Field(default=None, ge=0)
    height: Optional[int] = Field(default=None, ge=0)
    frame_rate: Optional[float] = Field(default=None, ge=0)
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    has_video: bool = False
    has_audio: bool = False
    file_size: int = Field(ge=0)
    format_name: Optional[str] = None


class AssemblyResult(BaseModel):
    """Result of a video assembly operation."""
    success: bool
    output_path: Optional[str] = None
    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    file_size: Optional[int] = None
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    
    @classmethod
    def failure(cls, error: str) -> "AssemblyResult":
        """Create a failure result."""
        return cls(success=False, errors=[error])
    
    @classmethod
    def from_output(
        cls,
        output_path: Path,
        media_info: "MediaInfo",
        warnings: List[str] = None
    ) -> "AssemblyResult":
        """Create a success result from output file info."""
        return cls(
            success=True,
            output_path=str(output_path),
            duration=media_info.duration,
            width=media_info.width,
            height=media_info.height,
            file_size=media_info.file_size,
            warnings=warnings or [],
        )
