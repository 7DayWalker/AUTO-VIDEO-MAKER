"""
AUTO Video Maker - Flow Scene Generation Configuration

Configuration for automated scene generation through Google Flow.
"""

import os
from pathlib import Path

from app.config import PROJECT_ROOT


# ----- Output Directories -----

SCENE_OUTPUT_DIR = PROJECT_ROOT / "output" / "scenes"
SCENE_SCREENSHOTS_DIR = PROJECT_ROOT / "logs" / "flow_scenes"


# ----- Flow Generation Settings -----

# Model selection (Veo 3.1 - Lite is the default for cost efficiency)
FLOW_SCENE_MODEL = os.getenv("FLOW_SCENE_MODEL", "Veo 3.1 - Lite")

# Aspect ratio (9:16 for vertical shorts)
FLOW_SCENE_ASPECT_RATIO = os.getenv("FLOW_SCENE_ASPECT_RATIO", "9:16")

# Duration in seconds
FLOW_SCENE_DURATION = int(os.getenv("FLOW_SCENE_DURATION", "4"))

# Output count (always 1 for scene generation)
FLOW_SCENE_COUNT = int(os.getenv("FLOW_SCENE_COUNT", "1"))

# Agent mode (must be OFF for predictable generation)
FLOW_SCENE_AGENT_ENABLED = os.getenv("FLOW_SCENE_AGENT_ENABLED", "false").lower() == "true"


# ----- Safety Settings -----

# Maximum scenes per run (safety limit during development)
FLOW_SCENE_MAX_PER_RUN = int(os.getenv("FLOW_SCENE_MAX_PER_RUN", "1"))

# Credit limit per generation (safety check)
FLOW_SCENE_CREDIT_LIMIT = int(os.getenv("FLOW_SCENE_CREDIT_LIMIT", "10"))

# Require explicit confirmation before generation
FLOW_SCENE_CONFIRMATION_REQUIRED = os.getenv(
    "FLOW_SCENE_CONFIRMATION_REQUIRED", "true"
).lower() == "true"


# ----- Timeouts -----

# Generation timeout (seconds)
FLOW_SCENE_TIMEOUT = int(os.getenv("FLOW_SCENE_TIMEOUT", "600"))

# Download timeout (seconds)
FLOW_SCENE_DOWNLOAD_TIMEOUT = int(os.getenv("FLOW_SCENE_DOWNLOAD_TIMEOUT", "120"))

# Polling interval (seconds)
FLOW_SCENE_POLL_INTERVAL = int(os.getenv("FLOW_SCENE_POLL_INTERVAL", "5"))


# ----- Duration Tolerance -----

# Acceptable deviation from requested duration (seconds)
FLOW_SCENE_DURATION_TOLERANCE = float(os.getenv("FLOW_SCENE_DURATION_TOLERANCE", "1.0"))


def ensure_scene_directories() -> None:
    """Create all required scene directories."""
    for directory in [
        SCENE_OUTPUT_DIR,
        SCENE_SCREENSHOTS_DIR,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def get_scene_config() -> dict:
    """Get current scene generation configuration as a dictionary."""
    return {
        "model": FLOW_SCENE_MODEL,
        "aspect_ratio": FLOW_SCENE_ASPECT_RATIO,
        "duration": FLOW_SCENE_DURATION,
        "count": FLOW_SCENE_COUNT,
        "agent_enabled": FLOW_SCENE_AGENT_ENABLED,
        "max_per_run": FLOW_SCENE_MAX_PER_RUN,
        "credit_limit": FLOW_SCENE_CREDIT_LIMIT,
        "confirmation_required": FLOW_SCENE_CONFIRMATION_REQUIRED,
        "timeout": FLOW_SCENE_TIMEOUT,
        "download_timeout": FLOW_SCENE_DOWNLOAD_TIMEOUT,
        "poll_interval": FLOW_SCENE_POLL_INTERVAL,
        "duration_tolerance": FLOW_SCENE_DURATION_TOLERANCE,
    }
