"""
Google Flow Automation - Browser Management

Handles launching and managing a dedicated Chrome browser profile
used for Google Flow automation.

IMPORTANT:
- Uses the installed Google Chrome browser (not bundled Chromium).
- Uses a DEDICATED automation profile (not the user's normal profile).
- The user's everyday Chrome profile is never touched.
- The dedicated profile is stored in browser_profile/ (gitignored).
"""

from pathlib import Path
from playwright.sync_api import sync_playwright, BrowserContext, Page

from app.flow.config import (
    BROWSER_PROFILE_DIR,
    CHROME_EXECUTABLE,
    NAVIGATION_TIMEOUT,
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


def launch_persistent_browser(playwright) -> BrowserContext:
    """
    Launch a persistent Chrome browser context using the dedicated profile.

    This uses the installed Google Chrome browser with a dedicated
    automation profile that is separate from the user's normal Chrome profile.

    The profile preserves the Google login session between runs.
    The browser is launched visibly (NOT headless) so the user can
    interact with it for manual authentication if needed.

    Args:
        playwright: The Playwright instance from sync_playwright().

    Returns:
        A persistent BrowserContext.
    """
    profile_dir = ensure_browser_profile_dir()
    log(f"Using Chrome executable: {CHROME_EXECUTABLE}")
    log(f"Using dedicated profile: {profile_dir}")

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        # Use the installed Google Chrome instead of bundled Chromium
        executable_path=CHROME_EXECUTABLE,
        # Use a specific channel to indicate we want Chrome behavior
        # (executable_path takes precedence but channel helps with defaults)
        # Reasonable viewport for Flow UI
        viewport={"width": 1280, "height": 900},
        # Accept downloads
        accept_downloads=True,
        # Standard timeout for navigation
        timeout=NAVIGATION_TIMEOUT * 1000,  # Playwright uses milliseconds
    )

    return context


def get_page(context: BrowserContext) -> Page:
    """
    Get the active page from the browser context, or create a new one.

    Args:
        context: The browser context.

    Returns:
        A Page instance.
    """
    if context.pages:
        return context.pages[0]
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
