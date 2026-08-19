"""
Google Flow Automation - Browser Management

Architecture:
1. Chrome is launched MANUALLY (or via a helper script) with:
   - A dedicated automation profile (separate from user's normal profile)
   - Remote debugging enabled on a local-only port
2. The user manually authenticates with Google in that Chrome window.
3. Playwright attaches to the already-running Chrome via Chrome DevTools Protocol (CDP).
4. Playwright can then navigate and interact with Flow using the authenticated session.

IMPORTANT:
- The user's normal Chrome profile is NEVER used.
- Google authentication is performed MANUALLY by the user.
- No passwords, cookies, or tokens are stored or extracted.
- The debugging port is bound to localhost only.
"""

import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

from app.flow.config import (
    BROWSER_PROFILE_DIR,
    CHROME_EXECUTABLE,
    NAVIGATION_TIMEOUT,
    CDP_PORT,
)


def log(message: str) -> None:
    """Print a timestamped log message."""
    from datetime import datetime
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[FLOW {timestamp}] {message}")


def ensure_browser_profile_dir() -> Path:
    """
    Ensure the dedicated browser profile directory exists.

    Returns:
        Path to the browser profile directory.
    """
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return BROWSER_PROFILE_DIR


def get_chrome_launch_command() -> list[str]:
    """
    Build the command to launch Chrome with remote debugging and dedicated profile.

    Returns:
        List of command-line arguments for subprocess.
    """
    profile_dir = ensure_browser_profile_dir()

    cmd = [
        CHROME_EXECUTABLE,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={profile_dir}",
        # Bind debugging to localhost only for security
        "--remote-allow-origins=http://localhost:*",
        # Don't show first-run dialogs
        "--no-first-run",
        "--no-default-browser-check",
        # Start with a useful page
        "https://flow.google",
    ]
    return cmd


def launch_chrome_with_debugging() -> subprocess.Popen:
    """
    Launch Chrome with remote debugging enabled and the dedicated profile.

    The user should manually authenticate with Google in this Chrome window.
    After authentication, Playwright can attach to this session.

    Returns:
        The subprocess.Popen object for the Chrome process.
    """
    cmd = get_chrome_launch_command()
    log(f"Launching Chrome with remote debugging on port {CDP_PORT}...")
    log(f"Chrome: {CHROME_EXECUTABLE}")
    log(f"Profile: {BROWSER_PROFILE_DIR}")
    log(f"CDP Port: {CDP_PORT} (localhost only)")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Give Chrome a moment to start
    time.sleep(3)

    if process.poll() is not None:
        raise RuntimeError(
            f"Chrome failed to start (exit code: {process.returncode}). "
            f"Command: {' '.join(cmd)}"
        )

    log("Chrome launched successfully.")
    return process


def connect_to_chrome(playwright) -> Browser:
    """
    Connect Playwright to an already-running Chrome instance via CDP.

    Chrome must already be running with --remote-debugging-port.

    Args:
        playwright: The Playwright instance from sync_playwright().

    Returns:
        A connected Browser instance.
    """
    cdp_url = f"http://localhost:{CDP_PORT}"
    log(f"Connecting to Chrome via CDP: {cdp_url}")

    browser = playwright.chromium.connect_over_cdp(cdp_url)
    log("Successfully connected to Chrome via CDP.")
    return browser


def get_page_from_browser(browser: Browser) -> Page:
    """
    Get an active page from the connected browser.

    If there are existing contexts/pages, use the first one.
    Otherwise, create a new page.

    Args:
        browser: The connected Browser instance.

    Returns:
        A Page instance.
    """
    contexts = browser.contexts
    if contexts:
        pages = contexts[0].pages
        if pages:
            return pages[0]
        return contexts[0].new_page()

    # Fallback: create new context
    context = browser.new_context()
    return context.new_page()


def take_screenshot(page: Page, name: str, screenshots_dir: Path) -> Path | None:
    """
    Take a screenshot and save it to the screenshots directory.

    Args:
        page: The Playwright page.
        name: Descriptive name for the screenshot (no extension).
        screenshots_dir: Directory to save screenshots.

    Returns:
        Path to the saved screenshot, or None if it failed.
    """
    try:
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = screenshots_dir / f"{name}_{timestamp}.png"
        page.screenshot(path=str(filepath))
        log(f"Screenshot saved: {filepath}")
        return filepath
    except Exception as e:
        log(f"Failed to take screenshot: {e}")
        return None


def is_chrome_running_with_debugging() -> bool:
    """
    Check if Chrome is already running with the debugging port open.

    Returns:
        True if a connection can be made to the CDP port.
    """
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            result = s.connect_ex(("localhost", CDP_PORT))
            return result == 0
    except Exception:
        return False
