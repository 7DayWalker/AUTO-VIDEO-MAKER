"""
AUTO Video Maker - Content Engine Configuration

Centralized configuration for content generation and LLM providers.
"""

import os
from pathlib import Path

from app.config import PROJECT_ROOT


# ----- Output Directories -----

CONTENT_OUTPUT_DIR = PROJECT_ROOT / "output" / "content"
CONTENT_LOGS_DIR = PROJECT_ROOT / "logs" / "content"


# ----- LLM Provider Configuration -----

# OpenRouter API
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")

# Request timeout (seconds)
LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "60"))


# ----- Retry Configuration -----

# Maximum retry attempts for transient failures
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", "2"))

# Retry delay (seconds)
RETRY_DELAY = float(os.getenv("RETRY_DELAY", "2.0"))


# ----- Duration Configuration -----

# Default target duration for short-form content (seconds)
DEFAULT_TARGET_DURATION = int(os.getenv("DEFAULT_TARGET_DURATION", "30"))

# Duration tolerance (seconds) - acceptable deviation from target
DURATION_TOLERANCE = float(os.getenv("DURATION_TOLERANCE", "3.0"))

# Minimum scene duration (seconds)
MIN_SCENE_DURATION = float(os.getenv("MIN_SCENE_DURATION", "2.0"))

# Maximum scene duration (seconds)
MAX_SCENE_DURATION = float(os.getenv("MAX_SCENE_DURATION", "10.0"))

# Default scene duration for estimation (seconds)
DEFAULT_SCENE_DURATION = float(os.getenv("DEFAULT_SCENE_DURATION", "5.0"))


# ----- Content Defaults -----

DEFAULT_PLATFORM = os.getenv("DEFAULT_PLATFORM", "youtube_shorts")
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "en")
DEFAULT_TONE = os.getenv("DEFAULT_TONE", "engaging and informative")
DEFAULT_NICHE = os.getenv("DEFAULT_NICHE", "general")
DEFAULT_NUMBER_OF_SCENES = int(os.getenv("DEFAULT_NUMBER_OF_SCENES", "6"))


# ----- Supported Values -----

SUPPORTED_PLATFORMS = {
    "youtube_shorts",
    "tiktok",
    "instagram_reels",
}

SUPPORTED_LANGUAGES = {
    "en",  # English
    "es",  # Spanish
    "fr",  # French
    "de",  # German
    "pt",  # Portuguese
    "it",  # Italian
    "ja",  # Japanese
    "ko",  # Korean
    "zh",  # Chinese
}

SUPPORTED_CONTENT_GOALS = {
    "education",
    "entertainment",
    "inspiration",
    "promotion",
    "storytelling",
    "tutorial",
    "news",
    "review",
}

SCENE_PURPOSES = {
    "hook",
    "intro",
    "fact",
    "story",
    "example",
    "transition",
    "climax",
    "conclusion",
    "cta",
}


def ensure_content_directories() -> None:
    """Create all required content directories."""
    for directory in [
        CONTENT_OUTPUT_DIR,
        CONTENT_LOGS_DIR,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def get_content_config() -> dict:
    """Get current content configuration as a dictionary."""
    return {
        "openrouter_model": OPENROUTER_MODEL,
        "llm_timeout": LLM_REQUEST_TIMEOUT,
        "max_retries": MAX_RETRY_ATTEMPTS,
        "default_duration": DEFAULT_TARGET_DURATION,
        "duration_tolerance": DURATION_TOLERANCE,
        "default_platform": DEFAULT_PLATFORM,
        "default_language": DEFAULT_LANGUAGE,
        "default_scenes": DEFAULT_NUMBER_OF_SCENES,
    }
