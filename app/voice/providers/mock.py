"""
AUTO Video Maker - Mock Voice Provider

Mock provider for testing without TTS API calls.
"""

import subprocess
import logging
from pathlib import Path
from typing import List

from app.assembly.ffmpeg import resolve_ffmpeg
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
        """Generate synthetic audio using FFmpeg.

        Fails truthfully when FFmpeg is unavailable: a missing media
        toolchain must surface as a failed voice stage, never as a
        fabricated "valid" audio artifact.
        """
        self._call_count += 1
        logger.info(f"Mock provider generating audio (call #{self._call_count})")
        
        # Estimate duration based on text length (approx 2.5 words per second)
        word_count = len(text.split())
        duration = max(1.0, word_count / 2.5)
        
        ffmpeg = resolve_ffmpeg()
        if not ffmpeg:
            raise VoiceProviderError(
                self.name,
                "FFmpeg executable not found; synthetic voice generation is "
                "unavailable. Install FFmpeg and add it to PATH, or set "
                "FFMPEG_PATH.",
            )

        # Create output directory
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        await self._generate_with_ffmpeg(ffmpeg, output_path, duration)
        logger.info(f"Generated mock audio: {output_path} ({duration:.1f}s)")
    
    async def _generate_with_ffmpeg(
        self, ffmpeg: str, output_path: Path, duration: float
    ) -> None:
        """Generate audio using FFmpeg sine wave."""
        ext = output_path.suffix.lower()
        
        if ext == ".mp3":
            codec = "libmp3lame"
        elif ext == ".wav":
            codec = "pcm_s16le"
        else:
            codec = "libmp3lame"
        
        args = [
            ffmpeg,
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
