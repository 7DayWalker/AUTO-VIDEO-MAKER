"""
AUTO Video Maker - Google Flow Proof of Concept Test

This script runs the full Flow automation workflow:
1. Launches a persistent Chromium browser with saved session
2. Opens Google Flow
3. Handles authentication (manual login if needed)
4. Configures and submits a test video generation
5. Waits for generation to complete
6. Downloads the result
7. Validates the downloaded file
8. Reports success or failure

Usage:
    python flow_test.py

On first run, you will need to log in to Google manually in the browser window.
Subsequent runs will reuse your saved session.
"""

import sys
from pathlib import Path
from datetime import datetime

from playwright.sync_api import sync_playwright

from app.flow.browser import log, launch_persistent_browser
from app.flow.automation import FlowAutomation
from app.flow.config import FLOW_TEST_OUTPUT_DIR


def validate_video(filepath: Path) -> bool:
    """
    Basic validation of a downloaded video file.

    Checks:
    - File exists
    - File size > 0
    - Extension is a video format

    Args:
        filepath: Path to the video file.

    Returns:
        True if validation passes, False otherwise.
    """
    log("Validating downloaded video...")

    if not filepath.exists():
        log(f"VALIDATION FAILED: File does not exist: {filepath}")
        return False

    file_size = filepath.stat().st_size
    if file_size == 0:
        log(f"VALIDATION FAILED: File is empty (0 bytes): {filepath}")
        return False

    # Check extension
    valid_extensions = {".mp4", ".webm", ".mov", ".avi", ".mkv"}
    if filepath.suffix.lower() not in valid_extensions:
        log(f"VALIDATION WARNING: Unexpected extension: {filepath.suffix}")
        # Don't fail on this - Flow might use a different format

    # Report file info
    size_mb = file_size / (1024 * 1024)
    log(f"File: {filepath.name}")
    log(f"Size: {size_mb:.2f} MB ({file_size:,} bytes)")
    log(f"Extension: {filepath.suffix}")
    log(f"Path: {filepath}")

    log("VALIDATION PASSED.")
    return True


def main() -> int:
    """
    Run the Google Flow proof-of-concept test.

    Returns:
        0 on success, 1 on failure.
    """
    print()
    log("=" * 50)
    log("AUTO Video Maker - Flow Proof of Concept")
    log("=" * 50)
    print()

    log("Starting browser...")

    with sync_playwright() as playwright:
        # Launch persistent browser (preserves login session)
        context = launch_persistent_browser(playwright)

        try:
            # Create and run the automation
            automation = FlowAutomation(context)
            video_path = automation.run()

            if video_path is None:
                log("")
                log("=" * 50)
                log("TEST FAILED")
                log("Video generation or download did not complete.")
                log("Check screenshots in logs/flow/ for details.")
                log("=" * 50)
                return 1

            # Validate the downloaded video
            if validate_video(video_path):
                log("")
                log("=" * 50)
                log("TEST PASSED")
                log(f"Video saved: {video_path}")
                log("=" * 50)
                return 0
            else:
                log("")
                log("=" * 50)
                log("TEST FAILED")
                log("Video file validation failed.")
                log("=" * 50)
                return 1

        finally:
            # Close browser context
            log("Closing browser...")
            context.close()


if __name__ == "__main__":
    sys.exit(main())
