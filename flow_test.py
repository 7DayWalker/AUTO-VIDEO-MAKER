"""
AUTO Video Maker - Google Flow Workspace Access Test

This script tests that we can:
1. Launch Chrome with a dedicated persistent profile
2. Navigate to Google Flow landing page
3. Detect authentication status (pause for manual login if needed)
4. Click "Create with Google Flow" to enter the workspace
5. Capture the workspace UI
6. Identify video-creation controls

Usage:
    python flow_test.py

On first run, you will need to log in to Google manually in the browser window.
Subsequent runs will reuse your saved session.

NOTE: This script does NOT generate videos. It only verifies workspace access
and identifies available controls.
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from app.flow.browser import log, launch_persistent_browser, get_page, take_screenshot
from app.flow.config import (
    FLOW_URL,
    FLOW_SCREENSHOTS_DIR,
    AUTH_TIMEOUT,
    POLL_INTERVAL,
)


def is_login_page(url: str) -> bool:
    """Check if the current URL indicates a Google login page."""
    url_lower = url.lower()
    login_indicators = [
        "accounts.google.com/signin",
        "accounts.google.com/v3/signin",
        "accounts.google.com/servicelogin",
        "accounts.google.com/interactivelogin",
    ]
    return any(indicator in url_lower for indicator in login_indicators)


def is_error_page(url: str, title: str) -> bool:
    """Check if the page is a 404 or error page."""
    indicators = ["404", "not found", "error"]
    return any(ind in title.lower() for ind in indicators) or "404" in url


def discover_ui_elements(page) -> list[dict]:
    """
    Discover visible interactive UI elements on the current page.

    Returns a list of dicts with element info (tag, text, aria-label, role, etc).
    """
    elements = page.evaluate("""() => {
        const results = [];
        const selectors = 'button, a[href], textarea, input, [role="button"], [role="tab"], [role="menuitem"], [role="option"], [role="listbox"], [role="combobox"], [contenteditable="true"], select';
        const els = document.querySelectorAll(selectors);
        for (const el of els) {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;

            const info = {
                tag: el.tagName.toLowerCase(),
                text: (el.textContent || '').trim().substring(0, 120),
                ariaLabel: el.getAttribute('aria-label') || '',
                title: el.getAttribute('title') || '',
                role: el.getAttribute('role') || '',
                type: el.getAttribute('type') || '',
                placeholder: el.getAttribute('placeholder') || '',
                href: el.tagName === 'A' ? (el.getAttribute('href') || '').substring(0, 150) : '',
                contenteditable: el.getAttribute('contenteditable') || '',
                dataTestId: el.getAttribute('data-test-id') || el.getAttribute('data-testid') || '',
            };
            if (info.text || info.ariaLabel || info.title || info.placeholder || info.contenteditable) {
                results.push(info);
            }
        }
        return results.slice(0, 80);
    }""")
    return elements


def handle_authentication(page) -> bool:
    """
    Check and handle authentication. Returns True if authenticated.
    Pauses for manual login if needed.
    """
    current_url = page.url

    if is_login_page(current_url):
        log("")
        log("=" * 50)
        log("AUTHENTICATION REQUIRED")
        log("Please log in to your Google account in the browser window.")
        log(f"Waiting up to {AUTH_TIMEOUT} seconds...")
        log("=" * 50)
        log("")

        start_time = time.time()
        while time.time() - start_time < AUTH_TIMEOUT:
            time.sleep(POLL_INTERVAL)
            current_url = page.url
            if not is_login_page(current_url):
                log("Authentication completed!")
                page.wait_for_timeout(3000)
                return True

        log("Authentication timed out!")
        take_screenshot(page, "auth_timeout", FLOW_SCREENSHOTS_DIR)
        return False
    else:
        log("Already authenticated (or no login required).")
        return True


def main() -> int:
    """
    Run the Google Flow workspace access test.

    Returns:
        0 on success, 1 on failure.
    """
    print()
    log("=" * 50)
    log("AUTO Video Maker - Flow Workspace Access Test")
    log("=" * 50)
    print()

    FLOW_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    log("Starting Chrome browser...")

    with sync_playwright() as playwright:
        context = launch_persistent_browser(playwright)

        try:
            page = get_page(context)

            # --- Step 1: Navigate to Flow landing page ---
            log(f"Navigating to: {FLOW_URL}")
            page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            landing_url = page.url
            landing_title = page.title()
            log(f"Landing URL: {landing_url}")
            log(f"Landing title: {landing_title}")

            # Check for errors
            if is_error_page(landing_url, landing_title):
                log("ERROR: Landing page is a 404 or error page!")
                take_screenshot(page, "landing_error", FLOW_SCREENSHOTS_DIR)
                return 1

            # Handle authentication
            if not handle_authentication(page):
                return 1

            # --- Step 2: Click "Create with Google Flow" ---
            log("")
            log("Looking for 'Create with Google Flow' button...")

            # Try multiple robust selector strategies
            create_button = None
            selectors_tried = []

            # Strategy 1: Button with exact text
            selector = 'button:has-text("Create with Google Flow")'
            selectors_tried.append(selector)
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=3000):
                    create_button = locator
                    log(f"Found button via: {selector}")
            except (PlaywrightTimeout, Exception):
                pass

            # Strategy 2: Any element with role=button and text
            if not create_button:
                selector = '[role="button"]:has-text("Create with Google Flow")'
                selectors_tried.append(selector)
                try:
                    locator = page.locator(selector).first
                    if locator.is_visible(timeout=2000):
                        create_button = locator
                        log(f"Found button via: {selector}")
                except (PlaywrightTimeout, Exception):
                    pass

            # Strategy 3: Link with text
            if not create_button:
                selector = 'a:has-text("Create with Google Flow")'
                selectors_tried.append(selector)
                try:
                    locator = page.locator(selector).first
                    if locator.is_visible(timeout=2000):
                        create_button = locator
                        log(f"Found button via: {selector}")
                except (PlaywrightTimeout, Exception):
                    pass

            # Strategy 4: Any clickable element containing that text
            if not create_button:
                selector = 'text="Create with Google Flow"'
                selectors_tried.append(selector)
                try:
                    locator = page.locator(selector).first
                    if locator.is_visible(timeout=2000):
                        create_button = locator
                        log(f"Found element via: {selector}")
                except (PlaywrightTimeout, Exception):
                    pass

            if not create_button:
                log("ERROR: Could not find 'Create with Google Flow' button!")
                log(f"Selectors tried: {selectors_tried}")
                take_screenshot(page, "no_create_button", FLOW_SCREENSHOTS_DIR)
                # Dump page info for diagnostics
                log(f"Current URL: {page.url}")
                log(f"Current title: {page.title()}")
                return 1

            # Click the button
            log("Clicking 'Create with Google Flow'...")
            create_button.click()

            # --- Step 3: Wait for workspace to load ---
            log("Waiting for workspace to load...")
            page.wait_for_timeout(5000)

            # Wait for URL to change or page to stabilize
            # The workspace may take a moment to load
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # Check if we got redirected to login
            if is_login_page(page.url):
                log("Authentication required to access workspace.")
                if not handle_authentication(page):
                    return 1
                page.wait_for_timeout(5000)

            workspace_url = page.url
            workspace_title = page.title()
            log(f"Workspace URL: {workspace_url}")
            log(f"Workspace title: {workspace_title}")

            # --- Step 4: Capture workspace screenshot ---
            log("")
            log("Capturing workspace interface...")
            screenshot_path = take_screenshot(page, "flow_workspace", FLOW_SCREENSHOTS_DIR)

            # --- Step 5: Discover workspace UI elements ---
            log("")
            log("Discovering workspace UI elements...")
            elements = discover_ui_elements(page)

            if elements:
                log(f"Found {len(elements)} interactive elements:")
                log("")
                for i, el in enumerate(elements, 1):
                    parts = [f"  {i}. <{el['tag']}>"]
                    if el.get('text'):
                        parts.append(f"text='{el['text'][:80]}'")
                    if el.get('ariaLabel'):
                        parts.append(f"aria-label='{el['ariaLabel']}'")
                    if el.get('title'):
                        parts.append(f"title='{el['title']}'")
                    if el.get('role'):
                        parts.append(f"role='{el['role']}'")
                    if el.get('placeholder'):
                        parts.append(f"placeholder='{el['placeholder']}'")
                    if el.get('contenteditable'):
                        parts.append(f"contenteditable='{el['contenteditable']}'")
                    if el.get('href'):
                        parts.append(f"href='{el['href'][:80]}'")
                    if el.get('dataTestId'):
                        parts.append(f"data-testid='{el['dataTestId']}'")
                    log(" | ".join(parts))
            else:
                log("No interactive elements found in workspace.")

            # --- Step 6: Report results ---
            log("")
            log("=" * 50)
            log("TEST PASSED - Flow workspace accessible")
            log(f"  Landing URL: {landing_url}")
            log(f"  Workspace URL: {workspace_url}")
            log(f"  Workspace Title: {workspace_title}")
            if screenshot_path:
                log(f"  Screenshot: {screenshot_path}")
            log(f"  UI Elements found: {len(elements)}")
            log("=" * 50)

            return 0

        except Exception as e:
            log(f"UNEXPECTED ERROR: {type(e).__name__}: {e}")
            try:
                page = get_page(context)
                take_screenshot(page, "unexpected_error", FLOW_SCREENSHOTS_DIR)
            except Exception:
                pass
            return 1

        finally:
            log("Closing browser...")
            context.close()


if __name__ == "__main__":
    sys.exit(main())
