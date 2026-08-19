"""
AUTO Video Maker - Video Configuration Discovery (Standard Prompt Box)

This script:
1. Connects to Chrome via CDP
2. Ensures Agent is OFF
3. Opens the generation settings panel
4. Switches to VIDEO tab
5. Discovers all video-specific controls (models, ratios, duration, cost)
6. Opens model dropdown to list available video models
7. Reports exact selectors for automation

Usage:
    python flow_test.py

NOTE: Does NOT generate anything or spend credits.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from app.flow.browser import (
    log,
    connect_to_chrome,
    get_page_from_browser,
    take_screenshot,
    is_chrome_running_with_debugging,
)
from app.flow.config import FLOW_SCREENSHOTS_DIR, CDP_PORT


def discover_ui_elements(page, max_elements=120):
    """Discover visible interactive UI elements."""
    return page.evaluate("""(maxElements) => {
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


def main():
    print()
    log("=" * 60)
    log("Video Configuration Discovery - Standard Prompt Box")
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
                log("Not in a Flow project. Navigating to Flow...")
                page.goto("https://flow.google", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                # Try to open existing project or create new one
                try:
                    new_proj = page.locator('button:has-text("New project")').first
                    if new_proj.is_visible(timeout=5000):
                        new_proj.click()
                        page.wait_for_timeout(5000)
                        log(f"Opened new project: {page.url}")
                except Exception:
                    log("ERROR: Could not open a project.")
                    return 1

            # Close any open panels
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

            # ---- Ensure Agent is OFF ----
            log("")
            log("Ensuring Agent is OFF...")
            agent_btn = page.locator('button:has-text("Agent")').first
            try:
                if agent_btn.is_visible(timeout=2000):
                    pressed = agent_btn.get_attribute("aria-pressed")
                    if pressed == "true":
                        agent_btn.click()
                        page.wait_for_timeout(1500)
                        log("Agent turned OFF.")
                    else:
                        log("Agent already OFF.")
            except Exception:
                log("Could not verify Agent state.")

            # ---- Open generation settings dropdown ----
            log("")
            log("Opening generation settings dropdown...")
            settings_btn = None
            all_btns = page.locator('button').all()
            for btn in all_btns:
                try:
                    text = (btn.text_content() or "").strip()
                    if btn.is_visible() and ("Nano" in text or "Veo" in text or "Omni" in text):
                        if "crop" in text or "x1" in text or "x2" in text or "x3" in text:
                            settings_btn = btn
                            log(f"Found: '{text[:60]}'")
                            break
                except Exception:
                    continue

            if not settings_btn:
                log("ERROR: Cannot find settings dropdown.")
                take_screenshot(page, "no_settings", FLOW_SCREENSHOTS_DIR)
                return 1

            # Check if already expanded
            expanded = settings_btn.get_attribute("aria-expanded")
            if expanded != "true":
                settings_btn.click()
                page.wait_for_timeout(1500)

            # ---- Switch to VIDEO tab ----
            log("")
            log("Switching to VIDEO tab...")
            video_tab = page.locator('button[role="tab"]:has-text("videocamVideo")').first
            try:
                if video_tab.is_visible(timeout=3000):
                    selected = video_tab.get_attribute("aria-selected")
                    log(f"Video tab current state: aria-selected={selected}")
                    if selected != "true":
                        video_tab.click()
                        page.wait_for_timeout(2000)
                        log("Video tab selected.")
                    else:
                        log("Video tab already selected.")
                else:
                    log("ERROR: Video tab not visible.")
                    take_screenshot(page, "no_video_tab", FLOW_SCREENSHOTS_DIR)
                    return 1
            except PlaywrightTimeout:
                log("ERROR: Video tab not found.")
                return 1

            # ---- Screenshot the video configuration ----
            log("")
            log("Capturing video configuration panel...")
            screenshot = take_screenshot(page, "flow_video_standard_configuration", FLOW_SCREENSHOTS_DIR)

            # ---- Discover all controls ----
            log("")
            log("=" * 60)
            log("VIDEO GENERATION CONTROLS")
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

            # ---- Open model dropdown ----
            log("")
            log("Opening video model dropdown...")
            model_dropdown = None
            for btn in page.locator('button').all():
                try:
                    text = (btn.text_content() or "").strip()
                    if btn.is_visible() and "arrow_drop_down" in text:
                        # Skip the ones that are not model selectors
                        if any(m in text for m in ["Veo", "Omni", "Nano", "Gemini", "Flash"]):
                            model_dropdown = btn
                            log(f"Found model dropdown: '{text[:60]}'")
                            break
                except Exception:
                    continue

            if model_dropdown:
                model_dropdown.click()
                page.wait_for_timeout(1500)

                # Screenshot model options
                take_screenshot(page, "flow_video_model_options", FLOW_SCREENSHOTS_DIR)

                # Discover dropdown items
                log("")
                log("Video model options:")
                model_options = page.evaluate("""() => {
                    const results = [];
                    const els = document.querySelectorAll('[role="option"], [role="menuitem"], [role="menuitemradio"], button');
                    for (const el of els) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width === 0 || rect.height === 0) continue;
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        const text = (el.textContent || '').trim();
                        const lc = text.toLowerCase();
                        if (lc.includes('veo') || lc.includes('omni') || lc.includes('nano') || lc.includes('gemini') || lc.includes('flash') || lc.includes('banana')) {
                            if (text.length < 80) {
                                results.push({
                                    text: text,
                                    role: el.getAttribute('role') || '',
                                    ariaSelected: el.getAttribute('aria-selected') || '',
                                    tag: el.tagName.toLowerCase(),
                                });
                            }
                        }
                    }
                    return results;
                }""")

                for i, opt in enumerate(model_options, 1):
                    parts = [f"  {i}. <{opt['tag']}> text='{opt['text']}'"]
                    if opt.get('role'):
                        parts.append(f"role='{opt['role']}'")
                    if opt.get('ariaSelected'):
                        parts.append(f"selected='{opt['ariaSelected']}'")
                    log(" | ".join(parts))

                # Close dropdown by pressing Escape
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            else:
                log("Could not find model dropdown.")

            # ---- Look for generation cost text ----
            log("")
            log("Looking for generation cost / credit information...")
            credit_text = page.evaluate("""() => {
                const results = [];
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    if (el.children.length > 2) continue;
                    const text = (el.textContent || '').trim();
                    if (text.toLowerCase().includes('credit') && text.length < 60) {
                        if (!results.includes(text)) results.push(text);
                    }
                }
                return results;
            }""")
            if credit_text:
                for txt in credit_text:
                    log(f"  Credit text: '{txt}'")
            else:
                log("  No credit-related text found on page.")

            # ---- Look for duration/length controls ----
            log("")
            log("Looking for duration/length controls...")
            duration_elements = page.evaluate("""() => {
                const results = [];
                const all = document.querySelectorAll('button, [role="tab"], [role="slider"]');
                for (const el of all) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const text = (el.textContent || '').trim();
                    const lc = text.toLowerCase();
                    if (lc.match(/\\d+s/) || lc.includes('duration') || lc.includes('length') || lc.includes('sec')) {
                        results.push({text: text, role: el.getAttribute('role') || '', ariaSelected: el.getAttribute('aria-selected') || ''});
                    }
                }
                return results;
            }""")
            if duration_elements:
                for i, el in enumerate(duration_elements, 1):
                    log(f"  {i}. text='{el['text']}' role='{el['role']}' selected='{el.get('ariaSelected', '')}'")
            else:
                log("  No duration controls found.")

            # ---- Final report ----
            log("")
            log("=" * 60)
            log("VIDEO CONFIGURATION DISCOVERY COMPLETE")
            log(f"  Screenshot: {screenshot}")
            log("=" * 60)
            return 0

        except Exception as e:
            log(f"ERROR: {type(e).__name__}: {e}")
            try:
                take_screenshot(get_page_from_browser(browser), "error", FLOW_SCREENSHOTS_DIR)
            except Exception:
                pass
            return 1
        finally:
            log("Disconnecting...")
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
