"""
AUTO Video Maker - Google Flow Generation Settings Discovery

This script:
1. Attaches to the already-running Chrome via CDP (localhost:9222)
2. Confirms the Flow project workspace is available
3. Opens the Settings panel
4. Inspects all generation settings (models, duration, aspect ratio, etc.)
5. Reports findings

Usage:
    Chrome must already be running with debugging (use launch_chrome.py).
    User must already be authenticated with Google.
    A Flow project must be open.

    python flow_test.py

NOTE: This script does NOT generate videos or spend credits.
      It only discovers settings.
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


def discover_ui_elements(page, max_elements: int = 100) -> list[dict]:
    """
    Discover visible interactive UI elements on the current page.
    """
    elements = page.evaluate("""(maxElements) => {
        const results = [];
        const selectors = 'button, a[href], textarea, input, [role="button"], [role="tab"], [role="menuitem"], [role="option"], [role="listbox"], [role="combobox"], [role="slider"], [role="radio"], [role="checkbox"], [role="switch"], [contenteditable="true"], select, label';
        const els = document.querySelectorAll(selectors);
        for (const el of els) {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;

            const info = {
                tag: el.tagName.toLowerCase(),
                text: (el.textContent || '').trim().substring(0, 150),
                ariaLabel: el.getAttribute('aria-label') || '',
                title: el.getAttribute('title') || '',
                role: el.getAttribute('role') || '',
                type: el.getAttribute('type') || '',
                placeholder: el.getAttribute('placeholder') || '',
                href: el.tagName === 'A' ? (el.getAttribute('href') || '').substring(0, 150) : '',
                contenteditable: el.getAttribute('contenteditable') || '',
                dataTestId: el.getAttribute('data-test-id') || el.getAttribute('data-testid') || '',
                disabled: el.disabled || false,
                checked: el.checked || false,
                value: (el.value || '').substring(0, 100),
                ariaSelected: el.getAttribute('aria-selected') || '',
                ariaExpanded: el.getAttribute('aria-expanded') || '',
            };
            if (info.text || info.ariaLabel || info.title || info.placeholder || info.contenteditable || info.value) {
                results.push(info);
            }
            if (results.length >= maxElements) break;
        }
        return results;
    }""", max_elements)
    return elements


def discover_panel_elements(page, panel_selector: str = None, max_elements: int = 100) -> list[dict]:
    """
    Discover elements within a specific panel/popover area, or the full page if no selector.
    Focus on settings-related controls.
    """
    # Get all visible text content that might be settings labels/values
    text_content = page.evaluate("""() => {
        const results = [];
        // Get all text nodes that look like settings
        const walker = document.createTreeWalker(
            document.body,
            NodeFilter.SHOW_ELEMENT,
            null,
            false
        );
        let node;
        while (node = walker.nextNode()) {
            const rect = node.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const style = window.getComputedStyle(node);
            if (style.display === 'none' || style.visibility === 'hidden') continue;

            const text = node.textContent?.trim();
            if (!text || text.length > 200 || text.length < 2) continue;

            // Look for settings-like content
            const lcText = text.toLowerCase();
            const keywords = ['model', 'veo', 'gemini', 'duration', 'aspect', 'ratio', 'resolution',
                            'quality', 'output', 'count', 'credit', 'cost', 'audio', 'video',
                            'image', 'mode', 'style', 'fps', 'frame', '9:16', '16:9', '1:1',
                            'portrait', 'landscape', 'square', 'ingredient', 'reference',
                            'seconds', 'sec', '4s', '5s', '6s', '8s', 'flash', 'pro'];
            if (keywords.some(k => lcText.includes(k))) {
                const info = {
                    tag: node.tagName.toLowerCase(),
                    text: text.substring(0, 150),
                    ariaLabel: node.getAttribute('aria-label') || '',
                    role: node.getAttribute('role') || '',
                };
                // Deduplicate
                if (!results.some(r => r.text === info.text)) {
                    results.push(info);
                }
            }
        }
        return results.slice(0, 50);
    }""")
    return text_content


def main() -> int:
    """
    Discover Google Flow generation settings via CDP.

    Returns:
        0 on success, 1 on failure.
    """
    print()
    log("=" * 50)
    log("AUTO Video Maker - Flow Generation Settings Discovery")
    log("=" * 50)
    print()

    FLOW_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # --- Step 0: Verify Chrome is running ---
    if not is_chrome_running_with_debugging():
        log(f"ERROR: Chrome is NOT running with debugging on port {CDP_PORT}.")
        log("Please run: python launch_chrome.py")
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
            log(f"Current URL: {current_url}")
            log(f"Current title: {current_title}")

            # Check if we're in a project workspace
            if "/project/" not in current_url:
                log("Not in a project workspace. Looking for a project to open...")
                # Try to navigate to Flow and open a new project
                page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)

                if is_login_page(page.url):
                    log("ERROR: Authentication expired. Please sign in again.")
                    return 1

                # Click New project
                try:
                    new_proj = page.locator('button:has-text("New project")').first
                    if new_proj.is_visible(timeout=5000):
                        new_proj.click()
                        page.wait_for_timeout(5000)
                except Exception:
                    log("Could not find New project button.")
                    take_screenshot(page, "no_project", FLOW_SCREENSHOTS_DIR)
                    return 1

            current_url = page.url
            current_title = page.title()
            log(f"Project URL: {current_url}")
            log(f"Project title: {current_title}")

            # --- Step 2: Find and click Settings ---
            log("")
            log("Looking for 'Settings' control...")

            settings_button = None
            settings_selectors = [
                'button:has-text("Settings")',
                'button:has-text("tuneSettings")',
                'button:has-text("tune")',
                '[aria-label*="Settings"]',
                '[aria-label*="settings"]',
                '[title*="Settings"]',
            ]

            for selector in settings_selectors:
                try:
                    locator = page.locator(selector).first
                    if locator.is_visible(timeout=3000):
                        settings_button = locator
                        log(f"Found Settings via: {selector}")
                        break
                except (PlaywrightTimeout, Exception):
                    continue

            if not settings_button:
                log("ERROR: Could not find Settings control.")
                take_screenshot(page, "no_settings", FLOW_SCREENSHOTS_DIR)
                # Show what IS available
                elements = discover_ui_elements(page)
                log(f"Available elements ({len(elements)}):")
                for i, el in enumerate(elements, 1):
                    if el.get('text'):
                        log(f"  {i}. <{el['tag']}> text='{el['text'][:60]}'")
                return 1

            # --- Step 3: Click Settings ---
            log("Clicking Settings...")
            settings_button.click()
            page.wait_for_timeout(2000)

            # --- Step 4: Screenshot the settings panel ---
            log("Capturing settings panel...")
            settings_screenshot = take_screenshot(page, "flow_generation_settings", FLOW_SCREENSHOTS_DIR)

            # --- Step 5: Discover settings controls ---
            log("")
            log("=" * 50)
            log("DISCOVERING GENERATION SETTINGS")
            log("=" * 50)
            log("")

            # First, get all interactive elements visible now
            elements = discover_ui_elements(page, max_elements=100)
            if elements:
                log(f"Found {len(elements)} interactive elements in settings view:")
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
                    if el.get('value'):
                        parts.append(f"value='{el['value']}'")
                    if el.get('ariaSelected'):
                        parts.append(f"aria-selected='{el['ariaSelected']}'")
                    if el.get('ariaExpanded'):
                        parts.append(f"aria-expanded='{el['ariaExpanded']}'")
                    if el.get('checked'):
                        parts.append("CHECKED")
                    if el.get('disabled'):
                        parts.append("DISABLED")
                    log(" | ".join(parts))

            # Also search for settings-related text content
            log("")
            log("Searching for settings-related keywords...")
            settings_text = discover_panel_elements(page)
            if settings_text:
                log(f"Found {len(settings_text)} settings-related text elements:")
                log("")
                for i, item in enumerate(settings_text, 1):
                    log(f"  {i}. <{item['tag']}> text='{item['text'][:100]}'")
            else:
                log("No settings-related keywords found in page text.")

            # --- Step 6: Final report ---
            log("")
            log("=" * 50)
            log("SETTINGS DISCOVERY COMPLETE")
            log(f"  Project URL: {current_url}")
            if settings_screenshot:
                log(f"  Screenshot: {settings_screenshot}")
            log(f"  Interactive elements: {len(elements)}")
            log(f"  Settings keywords found: {len(settings_text)}")
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
