"""
AUTO Video Maker - Base Voice Provider

Abstract base class for TTS providers.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)


class VoiceInfo(BaseModel):
    """Information about an available voice."""
    name: str
    short_name: str
    gender: Optional[str] = None
    locale: Optional[str] = None
    language: Optional[str] = None


class BaseVoiceProvider(ABC):
    """
    Abstract base class for TTS providers.
    
    All voice providers must implement this interface.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g., 'edge_tts', 'elevenlabs')."""
        pass
    
    @abstractmethod
    async def generate(
        self,
        text: str,
        output_path: Path,
        voice: str,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%",
    ) -> None:
        """
        Generate speech from text.
        
        Args:
            text: Text to convert to speech.
            output_path: Path to save the audio file.
            voice: Voice name/ID to use.
            rate: Speech rate adjustment.
            pitch: Pitch adjustment.
            volume: Volume adjustment.
        
        Raises:
            VoiceProviderError: If generation fails.
            VoiceNotFoundError: If voice is not available.
        """
        pass
    
    @abstractmethod
    async def list_voices(self, locale: str = None) -> List[VoiceInfo]:
        """
        List available voices.
        
        Args:
            locale: Optional locale filter (e.g., 'en-US').
        
        Returns:
            List of available voices.
        
        Raises:
            VoiceProviderError: If listing fails.
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if the provider is available.
        
        Returns:
            True if provider can be used, False otherwise.
        """
        pass
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"
