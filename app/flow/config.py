"""
Google Flow Automation - Configuration

All Flow-related settings and paths.
"""

import os
from pathlib import Path

from app.config import PROJECT_ROOT


# ----- Flow URLs -----

# Google Flow entry point
FLOW_URL = os.getenv("FLOW_URL", "https://flow.google")

# ----- Chrome Executable -----

# Path to the installed Google Chrome browser.
# Playwright will use this instead of the bundled Chromium.
CHROME_EXECUTABLE = os.getenv(
    "CHROME_EXECUTABLE",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe"
)

# ----- Chrome DevTools Protocol (CDP) -----

# Port for Chrome remote debugging (localhost only).
# Playwright attaches to Chrome via this port after user authenticates manually.
CDP_PORT = int(os.getenv("CDP_PORT", "9222"))

# ----- Browser Profile -----

# Dedicated persistent browser profile for automation.
# This is separate from the user's normal Chrome profile.
# Stored inside the project root but gitignored.
BROWSER_PROFILE_DIR = PROJECT_ROOT / "browser_profile"

# ----- Output Paths -----

FLOW_TEST_OUTPUT_DIR = PROJECT_ROOT / "output" / "flow_test"
FLOW_SCREENSHOTS_DIR = PROJECT_ROOT / "logs" / "flow"

# ----- Generation Settings -----

# Default test prompt for proof of concept
DEFAULT_TEST_PROMPT = (
    "A cinematic miniature luxury car sitting on a clean studio turntable, "
    "realistic lighting, slow camera movement."
)

# Desired aspect ratio (vertical for shorts)
DESIRED_ASPECT_RATIO = "9:16"

# ----- Timeouts (seconds) -----

# How long to wait for manual authentication
AUTH_TIMEOUT = int(os.getenv("FLOW_AUTH_TIMEOUT", "300"))  # 5 minutes

# How long to wait for page navigation
NAVIGATION_TIMEOUT = int(os.getenv("FLOW_NAV_TIMEOUT", "30"))  # 30 seconds

# How long to wait for video generation to complete
GENERATION_TIMEOUT = int(os.getenv("FLOW_GEN_TIMEOUT", "600"))  # 10 minutes

# How long to wait for download to complete
DOWNLOAD_TIMEOUT = int(os.getenv("FLOW_DOWNLOAD_TIMEOUT", "120"))  # 2 minutes

# Polling interval when waiting for generation (seconds)
POLL_INTERVAL = int(os.getenv("FLOW_POLL_INTERVAL", "5"))

# ----- Browser/page health and bounded recovery -----
HEALTH_TIMEOUT = int(os.getenv("FLOW_HEALTH_TIMEOUT", "20"))
HEALTH_POLL_INTERVAL = float(os.getenv("FLOW_HEALTH_POLL_INTERVAL", "1"))
MAX_REFRESH_ATTEMPTS = int(os.getenv("FLOW_MAX_REFRESH_ATTEMPTS", "1"))
MAX_RESTART_ATTEMPTS = int(os.getenv("FLOW_MAX_RESTART_ATTEMPTS", "1"))
SCREENSHOT_TIMEOUT = int(os.getenv("FLOW_SCREENSHOT_TIMEOUT", "5"))
