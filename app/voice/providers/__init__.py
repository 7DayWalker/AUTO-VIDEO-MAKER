"""
AUTO Video Maker - Voice Providers

TTS provider abstraction layer.
"""

from app.voice.providers.base import BaseVoiceProvider, VoiceInfo
from app.voice.providers.mock import MockVoiceProvider

__all__ = [
    "BaseVoiceProvider",
    "VoiceInfo",
    "MockVoiceProvider",
]
