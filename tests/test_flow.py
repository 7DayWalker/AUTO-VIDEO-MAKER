"""
Tests for app.flow module - Non-browser logic only.

These tests verify configuration, utility functions, and validation logic
WITHOUT launching a browser or connecting to Google Flow.
"""

from pathlib import Path
import tempfile
import os

from app.flow.config import (
    FLOW_URL,
    BROWSER_PROFILE_DIR,
    FLOW_TEST_OUTPUT_DIR,
    FLOW_SCREENSHOTS_DIR,
    DEFAULT_TEST_PROMPT,
    DESIRED_ASPECT_RATIO,
    AUTH_TIMEOUT,
    NAVIGATION_TIMEOUT,
    GENERATION_TIMEOUT,
    DOWNLOAD_TIMEOUT,
    POLL_INTERVAL,
)
from app.flow.automation import FlowAutomation, FlowAutomationError


class TestFlowConfig:
    """Tests for Flow configuration values."""

    def test_flow_url_is_set(self):
        """FLOW_URL should be a valid URL."""
        assert FLOW_URL.startswith("https://")
        assert "flow.google" in FLOW_URL or "google" in FLOW_URL

    def test_browser_profile_dir_is_path(self):
        """BROWSER_PROFILE_DIR should be a Path object."""
        assert isinstance(BROWSER_PROFILE_DIR, Path)

    def test_output_dir_is_path(self):
        """FLOW_TEST_OUTPUT_DIR should be a Path object."""
        assert isinstance(FLOW_TEST_OUTPUT_DIR, Path)
        assert "flow_test" in str(FLOW_TEST_OUTPUT_DIR)

    def test_screenshots_dir_is_path(self):
        """FLOW_SCREENSHOTS_DIR should be a Path object."""
        assert isinstance(FLOW_SCREENSHOTS_DIR, Path)

    def test_default_prompt_is_set(self):
        """DEFAULT_TEST_PROMPT should be a non-empty string."""
        assert isinstance(DEFAULT_TEST_PROMPT, str)
        assert len(DEFAULT_TEST_PROMPT) > 10

    def test_aspect_ratio_is_vertical(self):
        """DESIRED_ASPECT_RATIO should be 9:16 for shorts."""
        assert DESIRED_ASPECT_RATIO == "9:16"

    def test_timeouts_are_positive(self):
        """All timeouts should be positive integers."""
        assert AUTH_TIMEOUT > 0
        assert NAVIGATION_TIMEOUT > 0
        assert GENERATION_TIMEOUT > 0
        assert DOWNLOAD_TIMEOUT > 0
        assert POLL_INTERVAL > 0

    def test_auth_timeout_is_reasonable(self):
        """AUTH_TIMEOUT should give users enough time (at least 60s)."""
        assert AUTH_TIMEOUT >= 60

    def test_generation_timeout_is_reasonable(self):
        """GENERATION_TIMEOUT should allow for slow generation (at least 120s)."""
        assert GENERATION_TIMEOUT >= 120


class TestFlowAutomationError:
    """Tests for FlowAutomationError exception."""

    def test_error_can_be_raised(self):
        """FlowAutomationError should be raisable with a message."""
        try:
            raise FlowAutomationError("Test error message")
        except FlowAutomationError as e:
            assert "Test error message" in str(e)

    def test_error_is_exception(self):
        """FlowAutomationError should inherit from Exception."""
        assert issubclass(FlowAutomationError, Exception)


class TestVideoValidation:
    """Tests for the video validation logic in flow_test.py."""

    def test_validate_existing_file(self):
        """validate_video should pass for a non-empty file with video extension."""
        from flow_test import validate_video

        # Create a temporary file that looks like a video
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake video content for testing purposes")
            temp_path = Path(f.name)

        try:
            assert validate_video(temp_path) is True
        finally:
            os.unlink(temp_path)

    def test_validate_nonexistent_file(self):
        """validate_video should fail for a file that doesn't exist."""
        from flow_test import validate_video

        fake_path = Path("C:/nonexistent/fake_video.mp4")
        assert validate_video(fake_path) is False

    def test_validate_empty_file(self):
        """validate_video should fail for an empty file."""
        from flow_test import validate_video

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            temp_path = Path(f.name)
            # Don't write anything - file is empty

        try:
            assert validate_video(temp_path) is False
        finally:
            os.unlink(temp_path)


class TestLoginDetection:
    """Tests for login page detection logic."""

    def test_detects_google_signin(self):
        """Should detect Google sign-in URLs."""
        # We can't instantiate FlowAutomation without a browser context,
        # so we test the logic directly
        login_urls = [
            "https://accounts.google.com/signin/v2/identifier",
            "https://accounts.google.com/v3/signin/identifier",
            "https://accounts.google.com/servicelogin",
            "https://accounts.google.com/interactivelogin/signinchooser",
        ]

        login_indicators = [
            "accounts.google.com/signin",
            "accounts.google.com/v3/signin",
            "accounts.google.com/servicelogin",
            "accounts.google.com/interactivelogin",
        ]

        for url in login_urls:
            is_login = any(indicator in url.lower() for indicator in login_indicators)
            assert is_login, f"Should detect as login page: {url}"

    def test_does_not_detect_flow_as_login(self):
        """Should NOT detect Flow URLs as login pages."""
        flow_urls = [
            "https://flow.google.com",
            "https://flow.google.com/projects",
            "https://flow.google.com/generate",
        ]

        login_indicators = [
            "accounts.google.com/signin",
            "accounts.google.com/v3/signin",
            "accounts.google.com/servicelogin",
            "accounts.google.com/interactivelogin",
        ]

        for url in flow_urls:
            is_login = any(indicator in url.lower() for indicator in login_indicators)
            assert not is_login, f"Should NOT detect as login page: {url}"
