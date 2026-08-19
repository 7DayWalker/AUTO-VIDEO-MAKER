"""
AUTO Video Maker - Google Flow Agent Settings Discovery

This script:
1. Attaches to the already-running Chrome via CDP (localhost:9222)
2. Confirms the Flow project workspace is available
3. Locates the CORRECT Agent Settings control (tune/sliders icon in the prompt composer,
   NOT the top-right "View Settings" gear)
4. Opens Agent Settings panel
5. Inspects all generation configuration (models, aspect ratio, duration, etc.)

Usage:
    Chrome must already be running with debugging (use launch_chrome.py).
    User must already be authenticated with Google.
    A Flow project should be open.

    python flow_test.py

NOTE: This script does NOT generate videos or spend credits.
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
    """Discover visible interactive UI elements on the current page."""
    elements = page.evaluate("""(maxElements) => {
        const results = [];
        const selectors = 'button, a[href], textarea, input, [role="button"], [role="tab"], [role="menuitem"], [role="menuitemradio"], [role="option"], [role="listbox"], [role="combobox"], [role="slider"], [role="radio"], [role="radiogroup"], [role="checkbox"], [role="switch"], [contenteditable="true"], select, label';
        const els = document.querySelectorAll(selectors);
        for (const el of els) {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;

            const info = {
                tag: el.tagName.toLowerCase(),
                text: (el.textContent || '').trim().substring(0, 200),
                ariaLabel: el.getAttribute('aria-label') || '',
                title: el.getAttribute('title') || '',
                role: el.getAttribute('role') || '',
                type: el.getAttribute('type') || '',
                placeholder: el.getAttribute('placeholder') || '',
                contenteditable: el.getAttribute('contenteditable') || '',
                disabled: el.disabled || false,
                checked: el.checked || false,
                value: (el.value || '').substring(0, 100),
                ariaSelected: el.getAttribute('aria-selected') || '',
                ariaExpanded: el.getAttribute('aria-expanded') || '',
                ariaPressed: el.getAttribute('aria-pressed') || '',
            };
            if (info.text || info.ariaLabel || info.title || info.placeholder || info.contenteditable || info.value) {
                results.push(info);
            }
            if (results.length >= maxElements) break;
        }
        return results;
    }""", max_elements)
    return elements


def discover_settings_panel_content(page) -> list[dict]:
    """
    Discover all content visible in the settings panel area.
    Captures text, labels, selected states, etc.
    """
    content = page.evaluate("""() => {
        const results = [];
        // Look for all visible elements with meaningful text
        const all = document.querySelectorAll('*');
        for (const el of all) {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            
            // Only leaf-like text nodes or interactive elements
            const children = el.children;
            const hasOnlyText = el.childNodes.length > 0 && 
                Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim().length > 0);
            
            if (!hasOnlyText && children.length > 0) continue;
            
            const text = el.textContent?.trim();
            if (!text || text.length > 100 || text.length < 1) continue;
            
            // Filter for settings-relevant keywords
            const lcText = text.toLowerCase();
            const keywords = ['agent', 'setting', 'confirm', 'generat', 'always', 'never',
                            'image', 'video', 'default', 'model', 'veo', 'gemini', 'nano',
                            '16:9', '9:16', '4:3', '3:4', '1:1', 'x1', 'x2', 'x3', 'x4',
                            'lite', 'pro', 'flash', 'banana', 'aspect', 'ratio', 'output',
                            'credit', 'cost', 'free', 'audio', 'duration'];
            
            if (keywords.some(k => lcText.includes(k))) {
                const info = {
                    tag: el.tagName.toLowerCase(),
                    text: text,
                    role: el.getAttribute('role') || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                    ariaSelected: el.getAttribute('aria-selected') || '',
                    ariaPressed: el.getAttribute('aria-pressed') || '',
                    className: (el.className || '').toString().substring(0, 50),
                };
                // Deduplicate
                if (!results.some(r => r.text === info.text && r.tag === info.tag)) {
                    results.push(info);
                }
            }
        }
        return results.slice(0, 80);
    }""")
    return content


def main() -> int:
    """
    Discover Google Flow Agent Settings (the correct one) via CDP.

    Returns:
        0 on success, 1 on failure.
    """
    print()
    log("=" * 50)
    log("AUTO Video Maker - Flow Agent Settings Discovery")
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
                log("Not in a project workspace. Opening one...")
                page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                if is_login_page(page.url):
                    log("ERROR: Authentication expired.")
                    return 1
                try:
                    new_proj = page.locator('button:has-text("New project")').first
                    if new_proj.is_visible(timeout=5000):
                        new_proj.click()
                        page.wait_for_timeout(5000)
                except Exception:
                    log("Could not open a project.")
                    return 1

            current_url = page.url
            log(f"Project URL: {current_url}")

            # --- Step 2: Find the CORRECT Agent Settings control ---
            # This is the tune/sliders icon in the prompt composer area,
            # positioned to the left of the "arrow_forward Create" button.
            # It is NOT the top-right "View Settings" (settings_2) gear.
            log("")
            log("Looking for Agent Settings control in prompt composer...")
            log("(The tune/sliders icon next to the Create arrow button)")

            # The button text is "tuneSettings" — it contains the "tune" material icon
            # and the text "Settings". We need the one that is NOT "settings_2 View Settings"
            agent_settings = None

            # Strategy: find button with text "tuneSettings" (not "settings_2View Settings")
            try:
                locator = page.locator('button:has-text("tuneSettings")').first
                if locator.is_visible(timeout=3000):
                    # Verify this is not the View Settings button
                    btn_text = locator.text_content()
                    if "View Settings" not in btn_text:
                        agent_settings = locator
                        log(f"Found Agent Settings button. Text: '{btn_text.strip()[:50]}'")
            except (PlaywrightTimeout, Exception):
                pass

            # Fallback: look for button with just "tune" icon text
            if not agent_settings:
                try:
                    buttons = page.locator('button').all()
                    for btn in buttons:
                        try:
                            text = btn.text_content() or ""
                            if "tune" in text.lower() and "view" not in text.lower() and "settings_2" not in text:
                                if btn.is_visible():
                                    agent_settings = btn
                                    log(f"Found Agent Settings via iteration. Text: '{text.strip()[:50]}'")
                                    break
                        except Exception:
                            continue
                except Exception:
                    pass

            if not agent_settings:
                log("ERROR: Could not find the Agent Settings (tune) control.")
                take_screenshot(page, "no_agent_settings", FLOW_SCREENSHOTS_DIR)
                elements = discover_ui_elements(page)
                log("Looking for any settings-related buttons:")
                for i, el in enumerate(elements, 1):
                    if el.get('text') and ('setting' in el['text'].lower() or 'tune' in el['text'].lower()):
                        log(f"  {i}. <{el['tag']}> text='{el['text'][:80]}'")
                return 1

            # Get accessibility info
            aria_label = agent_settings.get_attribute("aria-label") or "none"
            title_attr = agent_settings.get_attribute("title") or "none"
            role = agent_settings.get_attribute("role") or "none"
            log(f"  aria-label: {aria_label}")
            log(f"  title: {title_attr}")
            log(f"  role: {role}")

            # --- Step 3: Click Agent Settings ---
            log("")
            log("Clicking Agent Settings control...")
            agent_settings.click()
            page.wait_for_timeout(2000)

            # --- Step 4: Screenshot ---
            log("Capturing Agent Settings panel...")
            screenshot_path = take_screenshot(page, "flow_agent_settings", FLOW_SCREENSHOTS_DIR)

            # --- Step 5: Discover panel content ---
            log("")
            log("=" * 50)
            log("AGENT SETTINGS PANEL CONTENT")
            log("=" * 50)
            log("")

            # Get all interactive elements now visible
            elements = discover_ui_elements(page, max_elements=100)
            if elements:
                log(f"Found {len(elements)} interactive elements:")
                log("")
                for i, el in enumerate(elements, 1):
                    parts = [f"  {i}. <{el['tag']}>"]
                    if el.get('text'):
                        parts.append(f"text='{el['text'][:100]}'")
                    if el.get('ariaLabel'):
                        parts.append(f"aria-label='{el['ariaLabel']}'")
                    if el.get('role'):
                        parts.append(f"role='{el['role']}'")
                    if el.get('ariaSelected'):
                        parts.append(f"aria-selected='{el['ariaSelected']}'")
                    if el.get('ariaPressed'):
                        parts.append(f"aria-pressed='{el['ariaPressed']}'")
                    if el.get('ariaExpanded'):
                        parts.append(f"aria-expanded='{el['ariaExpanded']}'")
                    if el.get('checked'):
                        parts.append("CHECKED")
                    if el.get('disabled'):
                        parts.append("DISABLED")
                    log(" | ".join(parts))

            # Also discover settings-specific text/keywords
            log("")
            log("Settings-related content (keyword search):")
            log("")
            settings_content = discover_settings_panel_content(page)
            if settings_content:
                for i, item in enumerate(settings_content, 1):
                    parts = [f"  {i}. <{item['tag']}> text='{item['text']}'"]
                    if item.get('ariaSelected'):
                        parts.append(f"aria-selected='{item['ariaSelected']}'")
                    if item.get('ariaPressed'):
                        parts.append(f"aria-pressed='{item['ariaPressed']}'")
                    if item.get('role'):
                        parts.append(f"role='{item['role']}'")
                    log(" | ".join(parts))
            else:
                log("No settings-related keywords found.")

            # --- Step 6: Report ---
            log("")
            log("=" * 50)
            log("AGENT SETTINGS DISCOVERY COMPLETE")
            log(f"  Project URL: {current_url}")
            if screenshot_path:
                log(f"  Screenshot: {screenshot_path}")
            log(f"  Interactive elements: {len(elements)}")
            log(f"  Settings keywords: {len(settings_content)}")
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
