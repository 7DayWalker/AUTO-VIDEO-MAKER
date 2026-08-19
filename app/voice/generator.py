"""
AUTO Video Maker - Voice Generator

Main voice generation orchestrator.
"""

import asyncio
import json
import re
import logging
from pathlib import Path
from typing import List, Optional

from app.content.models import ProductionPlan
from app.voice.models import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceMetadata,
    VoiceTiming,
    NarrationSegment,
    AssembledNarration,
)
from app.voice.providers.base import BaseVoiceProvider
from app.voice.providers.mock import MockVoiceProvider
from app.voice.providers.edge_tts import EdgeTTSProvider
from app.voice.validator import validate_audio_file
from app.voice.timing import create_voice_timing, check_duration_mismatch
from app.voice.config import (
    VOICE_OUTPUT_DIR,
    TTS_PROVIDER,
    TTS_VOICE,
    TTS_RATE,
    TTS_PITCH,
    TTS_VOLUME,
    TTS_OUTPUT_FORMAT,
    SCENE_PAUSE_DURATION,
    ensure_voice_directories,
)
from app.voice.exceptions import (
    VoiceError,
    VoiceGenerationError,
    NarrationExtractionError,
)

logger = logging.getLogger(__name__)


def clean_narration_text(text: str) -> str:
    """
    Clean narration text for TTS.
    
    Conservative cleaning:
    - Normalize excessive whitespace
    - Normalize line endings
    - Remove unsupported control characters
    
    Does NOT:
    - Rewrite sentences
    - Change punctuation
    - Paraphrase
    """
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    
    # Remove control characters except newlines
    text = re.sub(r'[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]', '', text)
    
    # Normalize excessive whitespace (but preserve single newlines)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # Strip leading/trailing whitespace
    text = text.strip()
    
    return text


def extract_narration_segments(plan: ProductionPlan) -> List[NarrationSegment]:
    """
    Extract narration segments from production plan in scene order.
    
    Args:
        plan: Production plan with scenes.
    
    Returns:
        List of NarrationSegment in sequence order.
    
    Raises:
        NarrationExtractionError: If extraction fails.
    """
    if not plan.scenes:
        raise NarrationExtractionError("Production plan has no scenes")
    
    # Sort scenes by sequence
    sorted_scenes = sorted(plan.scenes, key=lambda s: s.sequence)
    
    segments = []
    for scene in sorted_scenes:
        if not scene.narration or not scene.narration.strip():
            raise NarrationExtractionError(f"Scene {scene.id} has empty narration")
        
        segment = NarrationSegment(
            scene_id=scene.id,
            sequence=scene.sequence,
            text=clean_narration_text(scene.narration),
            estimated_duration=scene.estimated_duration,
        )
        segments.append(segment)
    
    return segments


def assemble_narration(
    plan: ProductionPlan,
    pause_duration: float = SCENE_PAUSE_DURATION,
) -> AssembledNarration:
    """
    Assemble complete narration from production plan.
    
    Args:
        plan: Production plan.
        pause_duration: Pause between scenes (seconds).
    
    Returns:
        AssembledNarration with full text and segments.
    """
    segments = extract_narration_segments(plan)
    return AssembledNarration.from_segments(
        project_id=plan.project_id,
        segments=segments,
        pause_duration=pause_duration,
    )


def get_provider(provider_name: str, mock_mode: bool = False) -> BaseVoiceProvider:
    """
    Get voice provider by name.
    
    Args:
        provider_name: Provider name.
        mock_mode: Force mock provider.
    
    Returns:
        Voice provider instance.
    """
    if mock_mode:
        return MockVoiceProvider()
    
    provider_name = provider_name.lower()
    
    if provider_name == "mock":
        return MockVoiceProvider()
    elif provider_name == "edge_tts":
        return EdgeTTSProvider()
    else:
        raise VoiceGenerationError(f"Unknown provider: {provider_name}")


class VoiceGenerator:
    """
    Main voice generation orchestrator.
    
    Coordinates narration extraction, TTS generation, and output management.
    """
    
    def __init__(
        self,
        provider: BaseVoiceProvider = None,
        output_dir: Path = None,
        mock_mode: bool = False,
    ):
        if mock_mode:
            self.provider = MockVoiceProvider()
        elif provider:
            self.provider = provider
        else:
            self.provider = get_provider(TTS_PROVIDER)
        
        self.output_dir = output_dir or VOICE_OUTPUT_DIR
    
    def generate_from_plan(
        self,
        plan: ProductionPlan,
        voice: str = None,
        rate: str = None,
        pitch: str = None,
        volume: str = None,
        output_format: str = None,
    ) -> VoiceGenerationResult:
        """
        Generate voiceover from production plan.
        
        Args:
            plan: Production plan with narration.
            voice: Voice to use (default from config).
            rate: Speech rate (default from config).
            pitch: Pitch adjustment (default from config).
            volume: Volume adjustment (default from config).
            output_format: Output format (default from config).
        
        Returns:
            VoiceGenerationResult with output details.
        """
        # Use defaults from config
        voice = voice or TTS_VOICE
        rate = rate or TTS_RATE
        pitch = pitch or TTS_PITCH
        volume = volume or TTS_VOLUME
        output_format = output_format or TTS_OUTPUT_FORMAT
        
        logger.info(f"Generating voiceover for project: {plan.project_id}")
        logger.info(f"Provider: {self.provider.name}, Voice: {voice}")
        
        try:
            # Ensure directories exist
            ensure_voice_directories()
            
            # Create project output directory
            project_dir = self.output_dir / plan.project_id
            project_dir.mkdir(parents=True, exist_ok=True)
            
            # Extract and assemble narration
            narration = assemble_narration(plan)
            
            # Save narration text
            narration_path = project_dir / "narration.txt"
            narration_path.write_text(narration.full_text, encoding="utf-8")
            logger.info(f"Saved narration: {narration_path}")
            
            # Create voice generation request
            request = VoiceGenerationRequest(
                project_id=plan.project_id,
                text=narration.full_text,
                provider=self.provider.name,
                voice=voice,
                rate=rate,
                pitch=pitch,
                volume=volume,
                output_format=output_format,
            )
            
            # Generate voiceover
            output_path = project_dir / f"voiceover.{output_format}"
            
            # Run async generation
            asyncio.run(self.provider.generate(
                text=request.text,
                output_path=output_path,
                voice=voice,
                rate=rate,
                pitch=pitch,
                volume=volume,
            ))
            
            # Validate output
            validation = validate_audio_file(output_path)
            
            if not validation.valid:
                return VoiceGenerationResult.failure(
                    error=f"Audio validation failed: {'; '.join(validation.errors)}",
                    provider=self.provider.name,
                )
            
            # Check duration mismatch
            warnings = list(validation.warnings)
            if validation.duration:
                has_warning, diff, msg = check_duration_mismatch(
                    validation.duration,
                    narration.total_estimated_duration,
                )
                if has_warning:
                    warnings.append(msg)
            
            # Create timing information
            if validation.duration:
                timing = create_voice_timing(
                    project_id=plan.project_id,
                    segments=narration.segments,
                    voiceover_duration=validation.duration,
                )
                
                # Save timing
                timing_path = project_dir / "voice_timing.json"
                with open(timing_path, "w", encoding="utf-8") as f:
                    json.dump(timing.to_dict(), f, indent=2)
                logger.info(f"Saved timing: {timing_path}")
            
            # Create and save metadata
            metadata = VoiceMetadata(
                project_id=plan.project_id,
                provider=self.provider.name,
                voice=voice,
                rate=rate,
                pitch=pitch,
                volume=volume,
                format=output_format,
                duration=validation.duration or 0,
                sample_rate=validation.sample_rate,
                channels=validation.channels,
                file_size=validation.file_size or 0,
            )
            
            metadata_path = project_dir / "voice_metadata.json"
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata.to_dict(), f, indent=2)
            logger.info(f"Saved metadata: {metadata_path}")
            
            # Return success result
            return VoiceGenerationResult.from_generation(
                output_path=output_path,
                provider=self.provider.name,
                voice=voice,
                duration=validation.duration or 0,
                file_size=validation.file_size or 0,
                format=output_format,
                sample_rate=validation.sample_rate,
                channels=validation.channels,
                warnings=warnings,
            )
            
        except VoiceError as e:
            logger.error(f"Voice generation error: {e}")
            return VoiceGenerationResult.failure(
                error=str(e),
                provider=self.provider.name,
            )
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return VoiceGenerationResult.failure(
                error=f"Unexpected error: {e}",
                provider=self.provider.name,
            )
    
    def generate_from_request(
        self,
        request: VoiceGenerationRequest,
    ) -> VoiceGenerationResult:
        """
        Generate voiceover from a request object.
        
        Args:
            request: Voice generation request.
        
        Returns:
            VoiceGenerationResult with output details.
        """
        logger.info(f"Generating voiceover for project: {request.project_id}")
        
        try:
            ensure_voice_directories()
            
            # Create project output directory
            project_dir = self.output_dir / request.project_id
            project_dir.mkdir(parents=True, exist_ok=True)
            
            # Save narration text
            narration_path = project_dir / "narration.txt"
            narration_path.write_text(request.text, encoding="utf-8")
            
            # Generate voiceover
            output_path = project_dir / f"voiceover.{request.output_format}"
            
            asyncio.run(self.provider.generate(
                text=request.text,
                output_path=output_path,
                voice=request.voice,
                rate=request.rate,
                pitch=request.pitch,
                volume=request.volume,
            ))
            
            # Validate output
            validation = validate_audio_file(output_path)
            
            if not validation.valid:
                return VoiceGenerationResult.failure(
                    error=f"Audio validation failed: {'; '.join(validation.errors)}",
                    provider=self.provider.name,
                )
            
            # Create and save metadata
            metadata = VoiceMetadata(
                project_id=request.project_id,
                provider=self.provider.name,
                voice=request.voice,
                rate=request.rate,
                pitch=request.pitch,
                volume=request.volume,
                format=request.output_format,
                duration=validation.duration or 0,
                sample_rate=validation.sample_rate,
                channels=validation.channels,
                file_size=validation.file_size or 0,
            )
            
            metadata_path = project_dir / "voice_metadata.json"
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata.to_dict(), f, indent=2)
            
            return VoiceGenerationResult.from_generation(
                output_path=output_path,
                provider=self.provider.name,
                voice=request.voice,
                duration=validation.duration or 0,
                file_size=validation.file_size or 0,
                format=request.output_format,
                sample_rate=validation.sample_rate,
                channels=validation.channels,
                warnings=list(validation.warnings),
            )
            
        except VoiceError as e:
            logger.error(f"Voice generation error: {e}")
            return VoiceGenerationResult.failure(
                error=str(e),
                provider=self.provider.name,
            )
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return VoiceGenerationResult.failure(
                error=f"Unexpected error: {e}",
                provider=self.provider.name,
            )
