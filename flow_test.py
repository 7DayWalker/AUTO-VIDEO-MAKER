"""
AUTO Video Maker - Google Flow Browser Access Test

This script tests that we can:
1. Launch Chrome with a dedicated persistent profile
2. Navigate to Google Flow
3. Detect authentication status
4. Wait for manual login if needed
5. Capture the current Flow UI
6. Report visible UI elements

Usage:
    python flow_test.py

On first run, you will need to log in to Google manually in the browser window.
Subsequent runs will reuse your saved session.

NOTE: This script does NOT generate videos. It only verifies browser access.
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

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
        // Look for buttons, links, textareas, inputs, and elements with role
        const selectors = 'button, a[href], textarea, input, [role="button"], [role="tab"], [role="menuitem"], [contenteditable="true"]';
        const els = document.querySelectorAll(selectors);
        for (const el of els) {
            // Only visible elements
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;

            const info = {
                tag: el.tagName.toLowerCase(),
                text: (el.textContent || '').trim().substring(0, 100),
                ariaLabel: el.getAttribute('aria-label') || '',
                title: el.getAttribute('title') || '',
                role: el.getAttribute('role') || '',
                type: el.getAttribute('type') || '',
                placeholder: el.getAttribute('placeholder') || '',
                href: el.tagName === 'A' ? (el.getAttribute('href') || '').substring(0, 100) : '',
            };
            // Only include elements with some identifying info
            if (info.text || info.ariaLabel || info.title || info.placeholder) {
                results.push(info);
            }
        }
        return results.slice(0, 50);  // Limit to 50 elements
    }""")
    return elements


def main() -> int:
    """
    Run the Google Flow browser access test.

    Returns:
        0 on success, 1 on failure.
    """
    print()
    log("=" * 50)
    log("AUTO Video Maker - Flow Browser Access Test")
    log("=" * 50)
    print()

    # Ensure screenshots directory exists
    FLOW_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    log("Starting Chrome browser...")

    with sync_playwright() as playwright:
        # Launch persistent browser (preserves login session)
        context = launch_persistent_browser(playwright)

        try:
            page = get_page(context)

            # Navigate to Flow
            log(f"Navigating to: {FLOW_URL}")
            page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=30000)

            # Wait for page to settle
            page.wait_for_timeout(3000)

            current_url = page.url
            page_title = page.title()
            log(f"Current URL: {current_url}")
            log(f"Page title: {page_title}")

            # Check for 404/error
            if is_error_page(current_url, page_title):
                log("ERROR: Page appears to be a 404 or error page!")
                take_screenshot(page, "error_page", FLOW_SCREENSHOTS_DIR)
                log("")
                log("TEST FAILED - Error page detected")
                return 1

            # Check authentication
            if is_login_page(current_url):
                log("")
                log("=" * 50)
                log("AUTHENTICATION REQUIRED")
                log("Please log in to your Google account in the browser window.")
                log(f"Waiting up to {AUTH_TIMEOUT} seconds...")
                log("=" * 50)
                log("")

                # Wait for user to complete authentication
                start_time = time.time()
                while time.time() - start_time < AUTH_TIMEOUT:
                    time.sleep(POLL_INTERVAL)
                    current_url = page.url
                    if not is_login_page(current_url):
                        log("Authentication completed!")
                        page.wait_for_timeout(3000)
                        current_url = page.url
                        page_title = page.title()
                        log(f"Post-auth URL: {current_url}")
                        log(f"Post-auth title: {page_title}")
                        break
                else:
                    log("Authentication timed out!")
                    take_screenshot(page, "auth_timeout", FLOW_SCREENSHOTS_DIR)
                    log("TEST FAILED - Authentication timeout")
                    return 1
            else:
                log("Already authenticated (or no login required).")

            # Take screenshot of the loaded Flow interface
            log("")
            log("Capturing Flow interface...")
            page.wait_for_timeout(2000)

            # Update URL/title after any redirects
            current_url = page.url
            page_title = page.title()
            log(f"Final URL: {current_url}")
            log(f"Final title: {page_title}")

            screenshot_path = take_screenshot(page, "flow_interface", FLOW_SCREENSHOTS_DIR)

            # Discover UI elements
            log("")
            log("Discovering UI elements...")
            elements = discover_ui_elements(page)

            if elements:
                log(f"Found {len(elements)} interactive elements:")
                log("")
                for i, el in enumerate(elements, 1):
                    parts = [f"  {i}. <{el['tag']}>"]
                    if el.get('text'):
                        parts.append(f"text='{el['text'][:60]}'")
                    if el.get('ariaLabel'):
                        parts.append(f"aria-label='{el['ariaLabel']}'")
                    if el.get('title'):
                        parts.append(f"title='{el['title']}'")
                    if el.get('role'):
                        parts.append(f"role='{el['role']}'")
                    if el.get('placeholder'):
                        parts.append(f"placeholder='{el['placeholder']}'")
                    if el.get('href'):
                        parts.append(f"href='{el['href']}'")
                    log(" | ".join(parts))
            else:
                log("No interactive elements found on the page.")

            # Report results
            log("")
            log("=" * 50)
            log("TEST PASSED - Flow interface accessible")
            log(f"  URL: {current_url}")
            log(f"  Title: {page_title}")
            if screenshot_path:
                log(f"  Screenshot: {screenshot_path}")
            log(f"  UI Elements found: {len(elements)}")
            log("=" * 50)

            return 0

        except Exception as e:
            log(f"UNEXPECTED ERROR: {e}")
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
