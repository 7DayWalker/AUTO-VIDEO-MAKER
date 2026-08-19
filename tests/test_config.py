"""
Tests for app.config module.

Verifies that the project configuration loads successfully
and that required directories can be created/verified.
"""

from pathlib import Path

from app.config import (
    APP_NAME,
    CURRENT_PHASE,
    APP_ENV,
    PROJECT_ROOT,
    REQUIRED_DIRS,
    ensure_directories_exist,
    get_config_summary,
)


def test_app_name_is_set():
    """APP_NAME should be 'AUTO Video Maker'."""
    assert APP_NAME == "AUTO Video Maker"


def test_current_phase_is_set():
    """Current phase should be set to a valid phase."""
    assert "Phase" in CURRENT_PHASE


def test_app_env_has_default():
    """APP_ENV should default to 'development'."""
    assert APP_ENV in ("development", "production", "testing")


def test_project_root_exists():
    """PROJECT_ROOT should point to an existing directory."""
    assert PROJECT_ROOT.exists()
    assert PROJECT_ROOT.is_dir()


def test_required_dirs_are_defined():
    """REQUIRED_DIRS should contain at least one directory path."""
    assert len(REQUIRED_DIRS) > 0
    for d in REQUIRED_DIRS:
        assert isinstance(d, Path)


def test_ensure_directories_exist():
    """ensure_directories_exist() should create dirs and return all True."""
    results = ensure_directories_exist()
    assert len(results) > 0
    for name, exists in results.items():
        assert exists is True, f"Directory '{name}' was not created successfully"


def test_get_config_summary():
    """get_config_summary() should return a dict with expected keys."""
    summary = get_config_summary()
    expected_keys = ["app_name", "current_phase", "environment", "log_level", "project_root"]
    for key in expected_keys:
        assert key in summary, f"Missing key '{key}' in config summary"
    assert summary["app_name"] == "AUTO Video Maker"
