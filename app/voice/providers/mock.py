"""
AUTO Video Maker - Mock Voice Provider

Mock provider for testing without TTS API calls.
"""

import subprocess
import logging
from pathlib import Path
from typing import List

from app.voice.providers.base import BaseVoiceProvider, VoiceInfo
from app.voice.exceptions import VoiceProviderError

logger = logging.getLogger(__name__)


# Mock voices for testing
MOCK_VOICES = [
    VoiceInfo(name="Mock Voice 1", short_name="mock-voice-1", gender="Male", locale="en-US", language="English"),
    VoiceInfo(name="Mock Voice 2", short_name="mock-voice-2", gender="Female", locale="en-US", language="English"),
    VoiceInfo(name="Mock Voice 3", short_name="mock-voice-3", gender="Male", locale="en-GB", language="English"),
]


class MockVoiceProvider(BaseVoiceProvider):
    """
    Mock voice provider for testing.
    
    Generates synthetic audio using FFmpeg without TTS API calls.
    """
    
    def __init__(self):
        self._call_count = 0
    
    @property
    def name(self) -> str:
        return "mock"
    
    async def generate(
        self,
        text: str,
        output_path: Path,
        voice: str,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%",
    ) -> None:
        """Generate synthetic audio using FFmpeg."""
        self._call_count += 1
        logger.info(f"Mock provider generating audio (call #{self._call_count})")
        
        # Estimate duration based on text length (approx 2.5 words per second)
        word_count = len(text.split())
        duration = max(1.0, word_count / 2.5)
        
        # Create output directory
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Try to generate using FFmpeg
        try:
            await self._generate_with_ffmpeg(output_path, duration)
            logger.info(f"Generated mock audio: {output_path} ({duration:.1f}s)")
        except Exception as e:
            logger.warning(f"FFmpeg generation failed: {e}, creating minimal file")
            await self._generate_minimal_file(output_path)
    
    async def _generate_with_ffmpeg(self, output_path: Path, duration: float) -> None:
        """Generate audio using FFmpeg sine wave."""
        ext = output_path.suffix.lower()
        
        if ext == ".mp3":
            codec = "libmp3lame"
        elif ext == ".wav":
            codec = "pcm_s16le"
        else:
            codec = "libmp3lame"
        
        args = [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", f"sine=frequency=200:duration={duration}",
            "-c:a", codec,
            "-b:a", "128k",
            str(output_path),
        ]
        
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        if result.returncode != 0:
            raise VoiceProviderError(self.name, f"FFmpeg failed: {result.stderr[:200]}")
    
    async def _generate_minimal_file(self, output_path: Path) -> None:
        """Generate a minimal valid audio file as fallback."""
        # Create a minimal MP3 file (silent frame)
        # This is a valid MP3 frame header + minimal data
        minimal_mp3 = bytes([
            0xFF, 0xFB, 0x90, 0x00,  # MP3 frame header
            0x00, 0x00, 0x00, 0x00,  # Padding
            0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00,
        ] * 100)  # Repeat for minimal duration
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(minimal_mp3)
        logger.info(f"Created minimal mock audio: {output_path}")
    
    async def list_voices(self, locale: str = None) -> List[VoiceInfo]:
        """Return mock voices."""
        if locale:
            return [v for v in MOCK_VOICES if v.locale and locale.lower() in v.locale.lower()]
        return MOCK_VOICES.copy()
    
    def is_available(self) -> bool:
        """Mock provider is always available."""
        return True
    
    @property
    def call_count(self) -> int:
        """Number of times generate was called."""
        return self._call_count
    
    def reset_call_count(self) -> None:
        """Reset the call counter."""
        self._call_count = 0
