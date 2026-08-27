"""
AUTO Video Maker - Assembly Engine Configuration

Centralized configuration for video assembly, encoding, and audio settings.
"""

import os
from pathlib import Path

from app.config import PROJECT_ROOT
from app.assembly.caption_presets import DEFAULT_CAPTION_PRESET


# ----- FFmpeg Paths -----

# Path to FFmpeg executable. If not set, assumes ffmpeg is in PATH.
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

# Path to FFprobe executable. If not set, assumes ffprobe is in PATH.
FFPROBE_PATH = os.getenv("FFPROBE_PATH", "ffprobe")


# ----- Output Directories -----

ASSEMBLY_OUTPUT_DIR = PROJECT_ROOT / "output" / "assembly"
ASSEMBLY_TEST_OUTPUT_DIR = PROJECT_ROOT / "output" / "assembly_test"
ASSEMBLY_LOGS_DIR = PROJECT_ROOT / "logs" / "assembly"
ASSEMBLY_TEMP_DIR = PROJECT_ROOT / "temp" / "assembly"


# ----- Video Settings -----

# Target resolution for 9:16 vertical shorts
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
DEFAULT_ASPECT_RATIO = "9:16"

# Frame rate
DEFAULT_FRAME_RATE = 30

# Video encoding settings
VIDEO_CODEC = "libx264"
VIDEO_PRESET = os.getenv("VIDEO_PRESET", "medium")  # ultrafast, fast, medium, slow
VIDEO_CRF = int(os.getenv("VIDEO_CRF", "23"))  # 18-28 typical, lower = better quality
VIDEO_PROFILE = "high"
VIDEO_LEVEL = "4.0"
PIXEL_FORMAT = "yuv420p"


# ----- Audio Settings -----

AUDIO_CODEC = "aac"
AUDIO_BITRATE = "192k"
AUDIO_SAMPLE_RATE = 44100
AUDIO_CHANNELS = 2

# Voiceover is primary, music is secondary
VOICEOVER_VOLUME = 1.0  # 100%
MUSIC_VOLUME = float(os.getenv("MUSIC_VOLUME", "0.15"))  # 15% default - conservative


# ----- Caption Settings -----

CAPTION_FONT_SIZE = int(os.getenv("CAPTION_FONT_SIZE", "48"))
CAPTION_FONT_COLOR = os.getenv("CAPTION_FONT_COLOR", "white")
CAPTION_OUTLINE_COLOR = os.getenv("CAPTION_OUTLINE_COLOR", "black")
CAPTION_OUTLINE_WIDTH = int(os.getenv("CAPTION_OUTLINE_WIDTH", "2"))
CAPTION_BACKGROUND = os.getenv("CAPTION_BACKGROUND", "")  # Empty = no background box
CAPTION_MARGIN_BOTTOM = int(os.getenv("CAPTION_MARGIN_BOTTOM", "150"))  # Pixels from bottom
CAPTION_FONT = os.getenv("CAPTION_FONT", "Arial")


# ----- Validation Settings -----

# Supported video extensions
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi", ".mkv"}

# Supported audio extensions
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}

# Supported caption extensions
SUPPORTED_CAPTION_EXTENSIONS = {".srt", ".vtt"}

# Maximum duration mismatch (seconds) before warning
DURATION_MISMATCH_THRESHOLD = 2.0

# Maximum voiceover overage (seconds) before error
VOICEOVER_MAX_OVERAGE = 5.0


# ----- Timeouts -----

# FFmpeg command timeout (seconds)
FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT", "600"))  # 10 minutes


def ensure_assembly_directories() -> None:
    """Create all required assembly directories."""
    for directory in [
        ASSEMBLY_OUTPUT_DIR,
        ASSEMBLY_TEST_OUTPUT_DIR,
        ASSEMBLY_LOGS_DIR,
        ASSEMBLY_TEMP_DIR,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def get_encoding_settings() -> dict:
    """Get current video encoding settings as a dictionary."""
    return {
        "video_codec": VIDEO_CODEC,
        "video_preset": VIDEO_PRESET,
        "video_crf": VIDEO_CRF,
        "video_profile": VIDEO_PROFILE,
        "video_level": VIDEO_LEVEL,
        "pixel_format": PIXEL_FORMAT,
        "audio_codec": AUDIO_CODEC,
        "audio_bitrate": AUDIO_BITRATE,
        "audio_sample_rate": AUDIO_SAMPLE_RATE,
        "audio_channels": AUDIO_CHANNELS,
    }


def get_caption_settings() -> dict:
    """Get current caption settings as a dictionary."""
    return {
        "font_size": CAPTION_FONT_SIZE,
        "font_color": CAPTION_FONT_COLOR,
        "outline_color": CAPTION_OUTLINE_COLOR,
        "outline_width": CAPTION_OUTLINE_WIDTH,
        "background": CAPTION_BACKGROUND,
        "margin_bottom": CAPTION_MARGIN_BOTTOM,
        "font": CAPTION_FONT,
        "preset": DEFAULT_CAPTION_PRESET,
    }
