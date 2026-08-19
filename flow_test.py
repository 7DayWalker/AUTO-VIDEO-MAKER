"""
AUTO Video Maker - Google Flow Project Workspace Discovery

This script:
1. Attaches to the already-running Chrome via CDP (localhost:9222)
2. Verifies authenticated Flow access
3. Clicks "New project" to open the creation workspace
4. Inspects and reports all video-generation controls

Usage:
    Chrome must already be running with debugging (use launch_chrome.py).
    User must already be authenticated with Google.

    python flow_test.py

NOTE: This script does NOT generate videos or spend credits.
      It only discovers the workspace UI.
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


def discover_ui_elements(page, max_elements: int = 80) -> list[dict]:
    """
    Discover visible interactive UI elements on the current page.
    """
    elements = page.evaluate("""(maxElements) => {
        const results = [];
        const selectors = 'button, a[href], textarea, input, [role="button"], [role="tab"], [role="menuitem"], [role="option"], [role="listbox"], [role="combobox"], [role="slider"], [contenteditable="true"], select';
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
                disabled: el.disabled || false,
            };
            if (info.text || info.ariaLabel || info.title || info.placeholder || info.contenteditable) {
                results.push(info);
            }
            if (results.length >= maxElements) break;
        }
        return results;
    }""", max_elements)
    return elements


def main() -> int:
    """
    Discover the Google Flow project workspace via CDP.

    Returns:
        0 on success, 1 on failure.
    """
    print()
    log("=" * 50)
    log("AUTO Video Maker - Flow Project Workspace Discovery")
    log("=" * 50)
    print()

    FLOW_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # --- Step 0: Verify Chrome is running ---
    if not is_chrome_running_with_debugging():
        log(f"ERROR: Chrome is NOT running with debugging on port {CDP_PORT}.")
        log("Please run: python launch_chrome.py")
        log("Then sign in to Google and navigate to Flow.")
        return 1

    # --- Step 1: Connect via CDP ---
    log("Connecting to Chrome via CDP...")

    with sync_playwright() as playwright:
        try:
            browser = connect_to_chrome(playwright)
        except Exception as e:
            log(f"ERROR: Could not connect to Chrome: {e}")
            return 1

        try:
            page = get_page_from_browser(browser)
            current_url = page.url
            current_title = page.title()
            log(f"Current page URL: {current_url}")
            log(f"Current page title: {current_title}")

            # --- Step 2: Navigate to Flow if not already there ---
            if "flow" not in current_url.lower() and "labs.google" not in current_url.lower():
                log(f"Not on Flow page. Navigating to: {FLOW_URL}")
                page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                current_url = page.url
                current_title = page.title()
                log(f"After navigation URL: {current_url}")
                log(f"After navigation title: {current_title}")

            # Check auth
            if is_login_page(current_url):
                log("ERROR: Authentication has expired. Please sign in again in Chrome.")
                take_screenshot(page, "auth_expired", FLOW_SCREENSHOTS_DIR)
                return 1

            # --- Step 3: Look for "New project" ---
            log("")
            log("Looking for 'New project' control...")

            new_project_button = None
            selectors = [
                'button:has-text("New project")',
                '[role="button"]:has-text("New project")',
                'a:has-text("New project")',
                'text="New project"',
                'button:has-text("New Project")',
                '[aria-label*="New project"]',
                '[aria-label*="new project"]',
            ]

            for selector in selectors:
                try:
                    locator = page.locator(selector).first
                    if locator.is_visible(timeout=3000):
                        new_project_button = locator
                        log(f"Found 'New project' via: {selector}")
                        break
                except (PlaywrightTimeout, Exception):
                    continue

            if not new_project_button:
                log("Could not find 'New project' button.")
                log("Taking screenshot of current state for diagnostics...")
                take_screenshot(page, "no_new_project", FLOW_SCREENSHOTS_DIR)
                log(f"Current URL: {page.url}")
                log(f"Current title: {page.title()}")

                # Try to discover what IS on the page
                log("")
                log("Discovering available elements on current page...")
                elements = discover_ui_elements(page)
                if elements:
                    log(f"Found {len(elements)} elements:")
                    for i, el in enumerate(elements, 1):
                        parts = [f"  {i}. <{el['tag']}>"]
                        if el.get('text'):
                            parts.append(f"text='{el['text'][:80]}'")
                        if el.get('ariaLabel'):
                            parts.append(f"aria-label='{el['ariaLabel']}'")
                        if el.get('role'):
                            parts.append(f"role='{el['role']}'")
                        log(" | ".join(parts))
                return 1

            # --- Step 4: Click "New project" ---
            log("Clicking 'New project'...")
            new_project_button.click()

            log("Waiting for project workspace to load...")
            page.wait_for_timeout(5000)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # Check for auth redirect
            if is_login_page(page.url):
                log("ERROR: Authentication required again after clicking New project.")
                log("Please sign in again in the Chrome window.")
                take_screenshot(page, "auth_after_new_project", FLOW_SCREENSHOTS_DIR)
                return 1

            # --- Step 5: Record workspace state ---
            workspace_url = page.url
            workspace_title = page.title()
            log(f"Project workspace URL: {workspace_url}")
            log(f"Project workspace title: {workspace_title}")

            # --- Step 6: Capture screenshot ---
            log("")
            log("Capturing project workspace...")
            screenshot_path = take_screenshot(page, "flow_project_workspace", FLOW_SCREENSHOTS_DIR)

            # --- Step 7: Discover all workspace controls ---
            log("")
            log("=" * 50)
            log("DISCOVERING PROJECT WORKSPACE CONTROLS")
            log("=" * 50)
            log("")

            elements = discover_ui_elements(page, max_elements=100)

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
                    if el.get('disabled'):
                        parts.append("DISABLED")
                    log(" | ".join(parts))
            else:
                log("No interactive elements found in workspace.")

            # --- Step 8: Final report ---
            log("")
            log("=" * 50)
            log("TEST PASSED - Project workspace discovered")
            log(f"  Flow URL (before New project): {current_url}")
            log(f"  Project Workspace URL: {workspace_url}")
            log(f"  Project Workspace Title: {workspace_title}")
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
            log("Disconnecting from Chrome (Chrome stays open)...")
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
