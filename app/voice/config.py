"""
AUTO Video Maker - Voice Engine Configuration

Centralized configuration for TTS voice generation.
"""

import os
from pathlib import Path

from app.config import PROJECT_ROOT


# ----- Output Directories -----

VOICE_OUTPUT_DIR = PROJECT_ROOT / "output" / "voice"
VOICE_LOGS_DIR = PROJECT_ROOT / "logs" / "voice"


# ----- TTS Provider Configuration -----

# Provider selection
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "edge_tts")

# Edge TTS default voice (Microsoft neural voices)
# Common voices: en-US-GuyNeural, en-US-JennyNeural, en-GB-RyanNeural
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-GuyNeural")

# Speech rate adjustment (e.g., "+0%", "-10%", "+20%")
TTS_RATE = os.getenv("TTS_RATE", "+0%")

# Volume adjustment (e.g., "+0%", "-10%", "+20%")
TTS_VOLUME = os.getenv("TTS_VOLUME", "+0%")

# Pitch adjustment (e.g., "+0Hz", "-10Hz", "+20Hz")
TTS_PITCH = os.getenv("TTS_PITCH", "+0Hz")

# Output format
TTS_OUTPUT_FORMAT = os.getenv("TTS_OUTPUT_FORMAT", "mp3")

# Audio bitrate for MP3
TTS_BITRATE = os.getenv("TTS_BITRATE", "192k")


# ----- Narration Configuration -----

# Pause between scenes (seconds)
SCENE_PAUSE_DURATION = float(os.getenv("SCENE_PAUSE_DURATION", "0.3"))

# Maximum pause duration (seconds)
MAX_SCENE_PAUSE = float(os.getenv("MAX_SCENE_PAUSE", "1.0"))


# ----- Duration Configuration -----

# Warning threshold for duration mismatch (seconds)
VOICE_DURATION_WARNING_THRESHOLD = float(
    os.getenv("VOICE_DURATION_WARNING_THRESHOLD", "2.0")
)


# ----- Retry Configuration -----

# Maximum retry attempts for TTS generation
VOICE_MAX_RETRIES = int(os.getenv("VOICE_MAX_RETRIES", "2"))

# Retry delay (seconds)
VOICE_RETRY_DELAY = float(os.getenv("VOICE_RETRY_DELAY", "1.0"))


# ----- Supported Values -----

SUPPORTED_PROVIDERS = {
    "edge_tts",
    "mock",
}

SUPPORTED_OUTPUT_FORMATS = {
    "mp3",
    "wav",
}


def ensure_voice_directories() -> None:
    """Create all required voice directories."""
    for directory in [
        VOICE_OUTPUT_DIR,
        VOICE_LOGS_DIR,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def get_voice_config() -> dict:
    """Get current voice configuration as a dictionary."""
    return {
        "provider": TTS_PROVIDER,
        "voice": TTS_VOICE,
        "rate": TTS_RATE,
        "volume": TTS_VOLUME,
        "pitch": TTS_PITCH,
        "output_format": TTS_OUTPUT_FORMAT,
        "bitrate": TTS_BITRATE,
        "scene_pause": SCENE_PAUSE_DURATION,
        "duration_warning_threshold": VOICE_DURATION_WARNING_THRESHOLD,
        "max_retries": VOICE_MAX_RETRIES,
    }
