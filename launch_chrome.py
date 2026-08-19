"""
AUTO Video Maker - Launch Chrome for Authentication

This script launches Google Chrome with:
- A dedicated automation profile (NOT your normal Chrome profile)
- Remote debugging enabled on localhost:9222

USAGE:
    python launch_chrome.py

After Chrome opens:
1. Sign in to your Google account manually
2. Navigate to Google Flow and verify access
3. Leave Chrome open
4. In another terminal, run: python flow_test.py

The dedicated profile preserves your login session between runs.
You should only need to sign in once.

SECURITY:
- Your normal Chrome profile is untouched.
- No passwords are stored by this script.
- The debugging port is localhost-only.
"""

import sys
from app.flow.browser import log, launch_chrome_with_debugging, is_chrome_running_with_debugging
from app.flow.config import CDP_PORT


def main() -> int:
    """Launch Chrome with debugging enabled."""
    print()
    log("=" * 50)
    log("AUTO Video Maker - Chrome Launcher")
    log("=" * 50)
    print()

    # Check if Chrome is already running with debugging
    if is_chrome_running_with_debugging():
        log(f"Chrome is ALREADY running with debugging on port {CDP_PORT}.")
        log("You can proceed with: python flow_test.py")
        log("")
        log("If you need to restart Chrome, close it first and run this script again.")
        return 0

    try:
        process = launch_chrome_with_debugging()
    except RuntimeError as e:
        log(f"ERROR: {e}")
        return 1

    log("")
    log("=" * 50)
    log("Chrome is now running with remote debugging.")
    log("")
    log("NEXT STEPS:")
    log("  1. In the Chrome window, sign in to your Google account.")
    log("  2. Verify you can access https://flow.google")
    log("  3. Leave Chrome open.")
    log("  4. In another terminal, run: python flow_test.py")
    log("")
    log(f"CDP endpoint: http://localhost:{CDP_PORT}")
    log("=" * 50)
    log("")
    log("Press Ctrl+C here when you are done (Chrome will stay open).")

    try:
        process.wait()
    except KeyboardInterrupt:
        log("Script stopped. Chrome remains running.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
