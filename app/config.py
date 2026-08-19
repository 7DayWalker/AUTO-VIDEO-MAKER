"""
AUTO Video Maker - Centralized Configuration

Loads settings from environment variables (.env file) and provides
a single source of truth for all configuration values.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


# ----- Project Paths -----

# Root directory of the project (parent of the 'app' folder)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env file from project root
load_dotenv(PROJECT_ROOT / ".env")


# ----- General Settings -----

APP_NAME = "AUTO Video Maker"
CURRENT_PHASE = "Phase 0 — Project Foundation"
APP_ENV = os.getenv("APP_ENV", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


# ----- Directory Paths -----

PROJECTS_DIR = PROJECT_ROOT / "projects"
OUTPUT_DIR = PROJECT_ROOT / "output"
ASSETS_DIR = PROJECT_ROOT / "assets"
LOGS_DIR = PROJECT_ROOT / "logs"
CREDENTIALS_DIR = PROJECT_ROOT / "credentials"

# All directories that must exist for the system to operate
REQUIRED_DIRS = [
    PROJECTS_DIR,
    OUTPUT_DIR,
    ASSETS_DIR,
    LOGS_DIR,
    CREDENTIALS_DIR,
]


def ensure_directories_exist() -> dict[str, bool]:
    """
    Check that all required project directories exist.
    Creates them if they don't exist.

    Returns:
        Dictionary mapping directory names to their existence status (True = exists/created).
    """
    results = {}
    for directory in REQUIRED_DIRS:
        directory.mkdir(parents=True, exist_ok=True)
        results[directory.name] = directory.exists()
    return results


def get_config_summary() -> dict:
    """
    Return a summary of the current configuration for health checks.

    Returns:
        Dictionary with key configuration values.
    """
    return {
        "app_name": APP_NAME,
        "current_phase": CURRENT_PHASE,
        "environment": APP_ENV,
        "log_level": LOG_LEVEL,
        "project_root": str(PROJECT_ROOT),
    }
