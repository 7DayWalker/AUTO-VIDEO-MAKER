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
    CHROME_EXECUTABLE,
    CDP_PORT,
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


class TestChromeConfig:
    """Tests for Chrome configuration."""

    def test_chrome_executable_is_set(self):
        """CHROME_EXECUTABLE should be a non-empty string."""
        assert isinstance(CHROME_EXECUTABLE, str)
        assert len(CHROME_EXECUTABLE) > 0

    def test_chrome_executable_path_looks_valid(self):
        """CHROME_EXECUTABLE should point to a chrome.exe path."""
        assert "chrome" in CHROME_EXECUTABLE.lower()

    def test_cdp_port_is_valid(self):
        """CDP_PORT should be a valid port number."""
        assert isinstance(CDP_PORT, int)
        assert 1024 <= CDP_PORT <= 65535

    def test_cdp_port_default(self):
        """CDP_PORT should default to 9222."""
        assert CDP_PORT == 9222


class TestCDPConnection:
    """Tests for CDP connection helpers."""

    def test_get_chrome_launch_command(self):
        """get_chrome_launch_command should return a valid command list."""
        from app.flow.browser import get_chrome_launch_command
        cmd = get_chrome_launch_command()
        assert isinstance(cmd, list)
        assert len(cmd) > 0
        assert CHROME_EXECUTABLE in cmd[0]
        # Should contain the debugging port flag
        port_flag = f"--remote-debugging-port={CDP_PORT}"
        assert port_flag in cmd
        # Should contain user-data-dir flag
        assert any("--user-data-dir=" in arg for arg in cmd)

    def test_is_chrome_running_returns_bool(self):
        """is_chrome_running_with_debugging should return a boolean."""
        from app.flow.browser import is_chrome_running_with_debugging
        result = is_chrome_running_with_debugging()
        assert isinstance(result, bool)


class TestErrorPageDetection:
    """Tests for error page detection logic."""

    def _is_error_page(self, url: str, title: str) -> bool:
        """Local helper implementing error page detection logic."""
        indicators = ["404", "not found", "error"]
        return any(ind in title.lower() for ind in indicators) or "404" in url

    def test_detects_404_in_title(self):
        """Should detect 404 in page title."""
        assert self._is_error_page("https://flow.google", "404 Not Found") is True

    def test_detects_error_in_title(self):
        """Should detect error in page title."""
        assert self._is_error_page("https://flow.google", "Error - Something went wrong") is True

    def test_does_not_flag_normal_page(self):
        """Should NOT flag a normal Flow page as error."""
        assert self._is_error_page("https://flow.google", "Google Flow") is False


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
