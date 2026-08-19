"""
AUTO Video Maker - Google Flow Workspace Access Test (CDP Attach)

Architecture:
1. Chrome must already be running with --remote-debugging-port=9222
   (use launch_chrome.py to start it)
2. The user must have already authenticated with Google in that Chrome session.
3. This script attaches to the running Chrome via CDP.
4. It navigates to Flow and inspects the workspace.

Usage:
    Step 1: python launch_chrome.py   (in one terminal)
    Step 2: Sign in to Google in the Chrome window
    Step 3: python flow_test.py       (in another terminal)

NOTE: This script does NOT generate videos.
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from app.flow.browser import (
    log,
    connect_to_chrome,
    get_page_from_browser,
    take_screenshot,
    is_chrome_running_with_debugging,
    launch_chrome_with_debugging,
)
from app.flow.config import (
    FLOW_URL,
    FLOW_SCREENSHOTS_DIR,
    AUTH_TIMEOUT,
    POLL_INTERVAL,
    CDP_PORT,
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

    Returns a list of dicts with element info.
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


def main() -> int:
    """
    Run the Google Flow workspace access test via CDP.

    Returns:
        0 on success, 1 on failure.
    """
    print()
    log("=" * 50)
    log("AUTO Video Maker - Flow Workspace Access Test (CDP)")
    log("=" * 50)
    print()

    FLOW_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # --- Step 0: Check if Chrome is running with debugging ---
    if not is_chrome_running_with_debugging():
        log(f"Chrome is NOT running with debugging on port {CDP_PORT}.")
        log("")
        log("Starting Chrome for you...")
        log("Please sign in to Google in the Chrome window that opens.")
        log("")

        try:
            launch_chrome_with_debugging()
        except RuntimeError as e:
            log(f"ERROR: {e}")
            return 1

        log("Waiting for you to sign in to Google...")
        log(f"(Waiting up to {AUTH_TIMEOUT} seconds)")
        log("")
        log("After signing in, press ENTER in this terminal to continue.")
        log("")

        try:
            input(">>> Press ENTER after you have signed in to Google... ")
        except (EOFError, KeyboardInterrupt):
            log("Cancelled.")
            return 1

        log("")

    log("Connecting to Chrome via CDP...")

    with sync_playwright() as playwright:
        try:
            browser = connect_to_chrome(playwright)
        except Exception as e:
            log(f"ERROR: Could not connect to Chrome via CDP: {e}")
            log(f"Make sure Chrome is running with --remote-debugging-port={CDP_PORT}")
            log("Run: python launch_chrome.py")
            return 1

        try:
            # Get an existing page or create a new one
            page = get_page_from_browser(browser)

            # --- Step 1: Navigate to Flow ---
            log(f"Navigating to: {FLOW_URL}")
            page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            landing_url = page.url
            landing_title = page.title()
            log(f"Landing URL: {landing_url}")
            log(f"Landing title: {landing_title}")

            # Check for errors
            if is_error_page(landing_url, landing_title):
                log("ERROR: Page is a 404 or error page!")
                take_screenshot(page, "error_page", FLOW_SCREENSHOTS_DIR)
                return 1

            # Check if we're on a login page
            if is_login_page(landing_url):
                log("")
                log("WARNING: You are on a Google login page.")
                log("Please sign in to Google in the Chrome window.")
                log(f"Waiting up to {AUTH_TIMEOUT} seconds...")
                log("")

                start_time = time.time()
                while time.time() - start_time < AUTH_TIMEOUT:
                    time.sleep(POLL_INTERVAL)
                    current_url = page.url
                    if not is_login_page(current_url):
                        log("Authentication detected! Continuing...")
                        page.wait_for_timeout(3000)
                        landing_url = page.url
                        landing_title = page.title()
                        break
                else:
                    log("Authentication timed out.")
                    take_screenshot(page, "auth_timeout", FLOW_SCREENSHOTS_DIR)
                    return 1

            # --- Step 2: Look for "Create with Google Flow" button ---
            log("")
            log("Looking for 'Create with Google Flow' button...")

            create_button = None
            selectors = [
                'button:has-text("Create with Google Flow")',
                '[role="button"]:has-text("Create with Google Flow")',
                'a:has-text("Create with Google Flow")',
                'text="Create with Google Flow"',
            ]

            for selector in selectors:
                try:
                    locator = page.locator(selector).first
                    if locator.is_visible(timeout=3000):
                        create_button = locator
                        log(f"Found button via: {selector}")
                        break
                except (PlaywrightTimeout, Exception):
                    continue

            if not create_button:
                # Maybe we're already in the workspace
                log("Could not find 'Create with Google Flow' button.")
                log("Checking if we're already in the workspace...")
                take_screenshot(page, "current_state", FLOW_SCREENSHOTS_DIR)
            else:
                # Click it
                log("Clicking 'Create with Google Flow'...")
                create_button.click()
                log("Waiting for workspace to load...")
                page.wait_for_timeout(5000)
                page.wait_for_load_state("domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)

            # --- Step 3: Check for auth redirect after clicking ---
            if is_login_page(page.url):
                log("")
                log("Authentication required for workspace.")
                log("Please sign in in the Chrome window...")
                log(f"Waiting up to {AUTH_TIMEOUT} seconds...")

                start_time = time.time()
                while time.time() - start_time < AUTH_TIMEOUT:
                    time.sleep(POLL_INTERVAL)
                    if not is_login_page(page.url):
                        log("Authentication complete!")
                        page.wait_for_timeout(5000)
                        break
                else:
                    log("Authentication timed out.")
                    take_screenshot(page, "workspace_auth_timeout", FLOW_SCREENSHOTS_DIR)
                    return 1

            # --- Step 4: Record workspace state ---
            workspace_url = page.url
            workspace_title = page.title()
            log(f"Workspace URL: {workspace_url}")
            log(f"Workspace title: {workspace_title}")

            # --- Step 5: Capture screenshot ---
            log("")
            log("Capturing workspace interface...")
            screenshot_path = take_screenshot(page, "flow_workspace", FLOW_SCREENSHOTS_DIR)

            # --- Step 6: Discover UI elements ---
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
                log("No interactive elements found.")

            # --- Step 7: Report ---
            log("")
            log("=" * 50)
            log("TEST PASSED - Flow workspace accessible via CDP")
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
                take_screenshot(get_page_from_browser(browser), "unexpected_error", FLOW_SCREENSHOTS_DIR)
            except Exception:
                pass
            return 1

        finally:
            # Disconnect (don't close — Chrome stays running)
            log("Disconnecting from Chrome (Chrome stays open)...")
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
