"""
AUTO Video Maker - Voice Engine Data Models

Pydantic models for voice generation requests, results, and timing.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator

from app.voice.config import (
    TTS_PROVIDER,
    TTS_VOICE,
    TTS_RATE,
    TTS_VOLUME,
    TTS_PITCH,
    TTS_OUTPUT_FORMAT,
    SUPPORTED_PROVIDERS,
    SUPPORTED_OUTPUT_FORMATS,
)


class VoiceGenerationRequest(BaseModel):
    """Request for voice generation."""
    project_id: str = Field(min_length=1, description="Project identifier")
    text: str = Field(min_length=1, description="Text to convert to speech")
    provider: str = Field(default=TTS_PROVIDER, description="TTS provider")
    voice: str = Field(default=TTS_VOICE, description="Voice name/ID")
    rate: str = Field(default=TTS_RATE, description="Speech rate adjustment")
    pitch: str = Field(default=TTS_PITCH, description="Pitch adjustment")
    volume: str = Field(default=TTS_VOLUME, description="Volume adjustment")
    output_format: str = Field(default=TTS_OUTPUT_FORMAT, description="Output format")
    
    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported provider '{v}'. Supported: {SUPPORTED_PROVIDERS}")
        return v
    
    @field_validator("output_format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in SUPPORTED_OUTPUT_FORMATS:
            raise ValueError(f"Unsupported format '{v}'. Supported: {SUPPORTED_OUTPUT_FORMATS}")
        return v
    
    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Text cannot be empty")
        return v


class VoiceGenerationResult(BaseModel):
    """Result of voice generation."""
    success: bool
    output_path: Optional[Path] = None
    provider: Optional[str] = None
    voice: Optional[str] = None
    duration: Optional[float] = None
    file_size: Optional[int] = None
    format: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    
    class Config:
        arbitrary_types_allowed = True
    
    @classmethod
    def failure(cls, error: str, provider: str = None) -> "VoiceGenerationResult":
        """Create a failure result."""
        return cls(
            success=False,
            error=error,
            provider=provider,
        )
    
    @classmethod
    def from_generation(
        cls,
        output_path: Path,
        provider: str,
        voice: str,
        duration: float,
        file_size: int,
        format: str,
        sample_rate: int = None,
        channels: int = None,
        warnings: List[str] = None,
    ) -> "VoiceGenerationResult":
        """Create a success result."""
        return cls(
            success=True,
            output_path=output_path,
            provider=provider,
            voice=voice,
            duration=duration,
            file_size=file_size,
            format=format,
            sample_rate=sample_rate,
            channels=channels,
            warnings=warnings or [],
        )


class VoiceMetadata(BaseModel):
    """Metadata for generated voice file."""
    project_id: str
    provider: str
    voice: str
    rate: str
    pitch: str
    volume: str
    format: str
    duration: float
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    file_size: int
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return self.model_dump()


class SceneTiming(BaseModel):
    """Timing information for a single scene."""
    scene_id: str
    sequence: int
    estimated_duration: float
    start: float
    end: float
    
    @property
    def actual_duration(self) -> float:
        """Calculate actual duration from start/end."""
        return self.end - self.start


class VoiceTiming(BaseModel):
    """Complete timing information for voice generation."""
    project_id: str
    voiceover_duration: float
    estimated_total_duration: float
    duration_difference: float
    scenes: List[SceneTiming]
    
    @classmethod
    def from_scenes(
        cls,
        project_id: str,
        voiceover_duration: float,
        scene_timings: List[SceneTiming],
    ) -> "VoiceTiming":
        """Create timing from scene list."""
        estimated_total = sum(s.estimated_duration for s in scene_timings)
        return cls(
            project_id=project_id,
            voiceover_duration=voiceover_duration,
            estimated_total_duration=estimated_total,
            duration_difference=voiceover_duration - estimated_total,
            scenes=scene_timings,
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "project_id": self.project_id,
            "voiceover_duration": self.voiceover_duration,
            "estimated_total_duration": self.estimated_total_duration,
            "duration_difference": self.duration_difference,
            "scenes": [s.model_dump() for s in self.scenes],
        }


class NarrationSegment(BaseModel):
    """A segment of narration from a scene."""
    scene_id: str
    sequence: int
    text: str
    estimated_duration: float


class AssembledNarration(BaseModel):
    """Complete assembled narration from production plan."""
    project_id: str
    full_text: str
    segments: List[NarrationSegment]
    total_estimated_duration: float
    scene_count: int
    
    @classmethod
    def from_segments(
        cls,
        project_id: str,
        segments: List[NarrationSegment],
        pause_duration: float = 0.3,
    ) -> "AssembledNarration":
        """Create assembled narration from segments."""
        # Build full text with pauses represented as newlines
        text_parts = []
        for i, segment in enumerate(segments):
            text_parts.append(segment.text)
            # Add pause marker (newline) between scenes, not after last
            if i < len(segments) - 1:
                text_parts.append("\n")
        
        full_text = "".join(text_parts)
        total_duration = sum(s.estimated_duration for s in segments)
        
        return cls(
            project_id=project_id,
            full_text=full_text,
            segments=segments,
            total_estimated_duration=total_duration,
            scene_count=len(segments),
        )
