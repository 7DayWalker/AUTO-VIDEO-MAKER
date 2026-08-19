"""
AUTO Video Maker - Standard Prompt Box Settings Discovery (Agent OFF)

This script:
1. Connects to Chrome via CDP
2. Confirms the standard prompt box (Agent OFF)
3. Opens the generation settings dropdown (model/ratio/count compact control)
4. Discovers all available generation options
5. Reports exact selectors for Video mode automation

Usage:
    Chrome must already be running with debugging (use launch_chrome.py).
    User must already be authenticated. A Flow project should be open.
    Agent must be OFF (previous run turned it off).

    python flow_test.py

NOTE: This does NOT generate anything or spend credits.
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


def discover_ui_elements(page, max_elements=120):
    """Discover visible interactive UI elements."""
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
                text: (el.textContent || '').trim().substring(0, 150),
                ariaLabel: el.getAttribute('aria-label') || '',
                role: el.getAttribute('role') || '',
                ariaSelected: el.getAttribute('aria-selected') || '',
                ariaPressed: el.getAttribute('aria-pressed') || '',
                ariaExpanded: el.getAttribute('aria-expanded') || '',
                placeholder: el.getAttribute('placeholder') || '',
                contenteditable: el.getAttribute('contenteditable') || '',
            };
            if (info.text || info.ariaLabel || info.placeholder || info.contenteditable) {
                results.push(info);
            }
            if (results.length >= maxElements) break;
        }
        return results;
    }""", max_elements)
    return elements


def main():
    print()
    log("=" * 60)
    log("Standard Prompt Box - Generation Settings Discovery")
    log("=" * 60)
    print()

    FLOW_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    if not is_chrome_running_with_debugging():
        log(f"ERROR: Chrome not running on port {CDP_PORT}.")
        return 1

    log("Connecting to Chrome via CDP...")

    with sync_playwright() as playwright:
        try:
            browser = connect_to_chrome(playwright)
        except Exception as e:
            log(f"ERROR: {e}")
            return 1

        try:
            page = get_page_from_browser(browser)
            log(f"URL: {page.url}")

            if "/project/" not in page.url:
                log("ERROR: Not in a Flow project.")
                return 1

            # Close any open panels
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

            # Verify Agent is OFF
            agent_btn = page.locator('button:has-text("Agent")').first
            try:
                if agent_btn.is_visible(timeout=2000):
                    pressed = agent_btn.get_attribute("aria-pressed")
                    log(f"Agent button aria-pressed: {pressed}")
                    if pressed == "true":
                        log("Agent is ON. Turning OFF...")
                        agent_btn.click()
                        page.wait_for_timeout(1500)
            except Exception:
                pass

            # Find the compact settings dropdown
            # It shows current settings like "Nano Banana 2 crop_16_9 x2"
            log("")
            log("Looking for generation settings dropdown...")

            settings_btn = None
            # Look for button containing model name + crop info
            all_btns = page.locator('button').all()
            for btn in all_btns:
                try:
                    text = (btn.text_content() or "").strip()
                    # This button contains model name + aspect ratio icon + count
                    if btn.is_visible() and ("Nano" in text or "Veo" in text or "Omni" in text or "Gemini" in text):
                        if "crop" in text or "x1" in text or "x2" in text or "x3" in text or "x4" in text:
                            settings_btn = btn
                            log(f"Found settings dropdown: '{text[:60]}'")
                            break
                except Exception:
                    continue

            if not settings_btn:
                log("ERROR: Could not find the generation settings dropdown.")
                take_screenshot(page, "no_gen_settings_dropdown", FLOW_SCREENSHOTS_DIR)
                elements = discover_ui_elements(page)
                log(f"All {len(elements)} elements:")
                for i, el in enumerate(elements, 1):
                    log(f"  {i}. <{el['tag']}> text='{el['text'][:80]}'")
                return 1

            # Click to open the settings panel
            log("Clicking settings dropdown...")
            settings_btn.click()
            page.wait_for_timeout(2000)

            # Take screenshot
            screenshot = take_screenshot(page, "flow_standard_gen_settings_open", FLOW_SCREENSHOTS_DIR)
            log(f"Screenshot: {screenshot}")

            # Discover all elements now visible
            log("")
            log("=" * 60)
            log("GENERATION SETTINGS PANEL CONTROLS")
            log("=" * 60)
            log("")

            elements = discover_ui_elements(page, max_elements=120)
            log(f"Found {len(elements)} interactive elements:")
            log("")
            for i, el in enumerate(elements, 1):
                parts = [f"  {i}. <{el['tag']}>"]
                if el.get('text'):
                    parts.append(f"text='{el['text'][:90]}'")
                if el.get('ariaLabel'):
                    parts.append(f"aria-label='{el['ariaLabel']}'")
                if el.get('role'):
                    parts.append(f"role='{el['role']}'")
                if el.get('ariaSelected'):
                    parts.append(f"selected='{el['ariaSelected']}'")
                if el.get('ariaPressed'):
                    parts.append(f"pressed='{el['ariaPressed']}'")
                if el.get('ariaExpanded'):
                    parts.append(f"expanded='{el['ariaExpanded']}'")
                if el.get('contenteditable'):
                    parts.append(f"contenteditable='{el['contenteditable']}'")
                log(" | ".join(parts))

            # Also look for generation-specific keywords
            log("")
            log("Generation-related text visible:")
            gen_text = page.evaluate("""() => {
                const results = [];
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    if (el.children.length > 3) continue;
                    const text = (el.textContent || '').trim();
                    if (!text || text.length > 60 || text.length < 2) continue;
                    const lc = text.toLowerCase();
                    const kw = ['video', 'image', 'veo', 'gemini', 'nano', 'omni', 'flash',
                               '4s', '5s', '6s', '8s', '10s', 'duration', 'length',
                               '16:9', '9:16', '4:3', '3:4', '1:1',
                               'portrait', 'landscape', 'orientation',
                               'x1', 'x2', 'x3', 'x4', 'generate', 'model', 'type'];
                    if (kw.some(k => lc.includes(k))) {
                        if (!results.some(r => r === text)) results.push(text);
                    }
                }
                return results.slice(0, 40);
            }""")
            for i, txt in enumerate(gen_text, 1):
                log(f"  {i}. '{txt}'")

            log("")
            log("=" * 60)
            log("DISCOVERY COMPLETE")
            log("=" * 60)
            return 0

        except Exception as e:
            log(f"ERROR: {type(e).__name__}: {e}")
            take_screenshot(get_page_from_browser(browser), "error", FLOW_SCREENSHOTS_DIR)
            return 1
        finally:
            log("Disconnecting...")
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
