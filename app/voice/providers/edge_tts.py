"""
AUTO Video Maker - Edge TTS Provider

Microsoft Edge TTS provider using edge-tts library.
"""

import logging
from pathlib import Path
from typing import List

from app.voice.providers.base import BaseVoiceProvider, VoiceInfo
from app.voice.exceptions import VoiceProviderError, VoiceNotFoundError

logger = logging.getLogger(__name__)


class EdgeTTSProvider(BaseVoiceProvider):
    """
    Edge TTS provider using Microsoft's neural voices.
    
    Uses the edge-tts library for free TTS generation.
    """
    
    def __init__(self):
        self._edge_tts = None
        self._available = None
    
    @property
    def name(self) -> str:
        return "edge_tts"
    
    def _ensure_imported(self) -> None:
        """Lazy import edge-tts to avoid import errors if not installed."""
        if self._edge_tts is None:
            try:
                import edge_tts
                self._edge_tts = edge_tts
                self._available = True
            except ImportError:
                self._available = False
                raise VoiceProviderError(
                    self.name,
                    "edge-tts package not installed. Run: pip install edge-tts"
                )
    
    async def generate(
        self,
        text: str,
        output_path: Path,
        voice: str,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%",
    ) -> None:
        """Generate speech using Edge TTS."""
        self._ensure_imported()
        
        logger.info(f"Generating speech with Edge TTS: voice={voice}")
        
        # Create output directory
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # Create communicate object
            communicate = self._edge_tts.Communicate(
                text=text,
                voice=voice,
                rate=rate,
                pitch=pitch,
                volume=volume,
            )
            
            # Generate and save
            await communicate.save(str(output_path))
            
            logger.info(f"Generated audio: {output_path}")
            
        except Exception as e:
            error_msg = str(e)
            
            # Check for voice not found
            if "voice" in error_msg.lower() and "not found" in error_msg.lower():
                raise VoiceNotFoundError(voice, self.name)
            
            raise VoiceProviderError(self.name, f"Generation failed: {error_msg}")
    
    async def list_voices(self, locale: str = None) -> List[VoiceInfo]:
        """List available Edge TTS voices."""
        self._ensure_imported()
        
        try:
            voices = await self._edge_tts.list_voices()
            
            result = []
            for voice in voices:
                # Filter by locale if specified
                voice_locale = voice.get("Locale", "")
                if locale and locale.lower() not in voice_locale.lower():
                    continue
                
                result.append(VoiceInfo(
                    name=voice.get("FriendlyName", voice.get("ShortName", "")),
                    short_name=voice.get("ShortName", ""),
                    gender=voice.get("Gender"),
                    locale=voice_locale,
                    language=voice.get("Language"),
                ))
            
            return result
            
        except Exception as e:
            raise VoiceProviderError(self.name, f"Failed to list voices: {e}")
    
    def is_available(self) -> bool:
        """Check if edge-tts is installed."""
        if self._available is not None:
            return self._available
        
        try:
            import edge_tts
            self._edge_tts = edge_tts
            self._available = True
            return True
        except ImportError:
            self._available = False
            return False
