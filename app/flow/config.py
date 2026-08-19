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
