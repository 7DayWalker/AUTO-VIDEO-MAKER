"""
AUTO Video Maker - Google Flow Create Menu Discovery

This script:
1. Attaches to the already-running Chrome via CDP (localhost:9222)
2. Confirms the Flow project workspace is available
3. Locates and opens the "add_2 Create" dropdown menu
4. Inspects all menu items/options
5. Reports creation modes and generation settings exposed

Usage:
    Chrome must already be running with debugging (use launch_chrome.py).
    User must already be authenticated with Google.
    A Flow project should be open.

    python flow_test.py

NOTE: This script does NOT generate videos or spend credits.
      It only discovers the Create menu options.
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
        const selectors = 'button, a[href], textarea, input, [role="button"], [role="tab"], [role="menuitem"], [role="menuitemradio"], [role="menuitemcheckbox"], [role="option"], [role="listbox"], [role="combobox"], [role="slider"], [role="radio"], [role="checkbox"], [role="switch"], [contenteditable="true"], select, label, li';
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
                ariaHaspopup: el.getAttribute('aria-haspopup') || '',
            };
            if (info.text || info.ariaLabel || info.title || info.placeholder || info.contenteditable || info.value) {
                results.push(info);
            }
            if (results.length >= maxElements) break;
        }
        return results;
    }""", max_elements)
    return elements


def discover_menu_items(page) -> list[dict]:
    """
    Discover items that appear to be part of a dropdown/popover menu.
    Looks for elements that appeared after the dropdown opened.
    """
    items = page.evaluate("""() => {
        const results = [];
        // Look for menu-like containers (popover, menu, listbox, dialog)
        const menuSelectors = '[role="menu"], [role="listbox"], [role="dialog"], [data-floating-ui-portal], [class*="popover"], [class*="dropdown"], [class*="menu"]';
        const menus = document.querySelectorAll(menuSelectors);
        
        // Also look for any newly visible interactive elements
        const allInteractive = 'button, [role="menuitem"], [role="option"], [role="menuitemradio"], li, a';
        
        for (const menu of menus) {
            const items_in_menu = menu.querySelectorAll(allInteractive);
            for (const el of items_in_menu) {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                
                results.push({
                    tag: el.tagName.toLowerCase(),
                    text: (el.textContent || '').trim().substring(0, 150),
                    ariaLabel: el.getAttribute('aria-label') || '',
                    title: el.getAttribute('title') || '',
                    role: el.getAttribute('role') || '',
                    disabled: el.disabled || false,
                });
            }
        }
        
        // If no menus found, look for any element with menu-related roles
        if (results.length === 0) {
            const roleItems = document.querySelectorAll('[role="menuitem"], [role="option"], [role="menuitemradio"]');
            for (const el of roleItems) {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                results.push({
                    tag: el.tagName.toLowerCase(),
                    text: (el.textContent || '').trim().substring(0, 150),
                    ariaLabel: el.getAttribute('aria-label') || '',
                    title: el.getAttribute('title') || '',
                    role: el.getAttribute('role') || '',
                    disabled: el.disabled || false,
                });
            }
        }
        
        return results.slice(0, 50);
    }""")
    return items


def main() -> int:
    """
    Discover the Google Flow Create menu options via CDP.

    Returns:
        0 on success, 1 on failure.
    """
    print()
    log("=" * 50)
    log("AUTO Video Maker - Flow Create Menu Discovery")
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
                log("Not in a project workspace. Opening a new project...")
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
                    take_screenshot(page, "no_project", FLOW_SCREENSHOTS_DIR)
                    return 1

            current_url = page.url
            log(f"Project URL: {current_url}")

            # --- Step 2: Find the Create dropdown ---
            log("")
            log("Looking for 'add_2 Create' dropdown control...")

            create_dropdown = None
            create_selectors = [
                'button:has-text("add_2Create")',
                'button:has-text("add_2 Create")',
            ]

            for selector in create_selectors:
                try:
                    locator = page.locator(selector).first
                    if locator.is_visible(timeout=3000):
                        create_dropdown = locator
                        log(f"Found Create dropdown via: {selector}")
                        break
                except (PlaywrightTimeout, Exception):
                    continue

            # Fallback: look for aria-expanded button near Create text
            if not create_dropdown:
                try:
                    # The button contains "add_2" icon text + "Create"
                    locator = page.locator('button').filter(has_text="Create").filter(has_text="add").first
                    if locator.is_visible(timeout=3000):
                        create_dropdown = locator
                        log("Found Create dropdown via button filter (add + Create)")
                except (PlaywrightTimeout, Exception):
                    pass

            if not create_dropdown:
                log("ERROR: Could not find the 'add_2 Create' dropdown.")
                take_screenshot(page, "no_create_dropdown", FLOW_SCREENSHOTS_DIR)
                elements = discover_ui_elements(page)
                log(f"Available elements ({len(elements)}):")
                for i, el in enumerate(elements, 1):
                    if el.get('text') and 'create' in el['text'].lower():
                        log(f"  {i}. <{el['tag']}> text='{el['text'][:80]}' aria-expanded='{el.get('ariaExpanded', '')}'")
                return 1

            # Check current aria-expanded state
            expanded = create_dropdown.get_attribute("aria-expanded")
            log(f"Current aria-expanded state: {expanded}")

            # --- Step 3: Click to open the dropdown ---
            log("Clicking Create dropdown to open menu...")
            create_dropdown.click()
            page.wait_for_timeout(2000)

            # Verify it opened
            expanded_after = create_dropdown.get_attribute("aria-expanded")
            log(f"After click aria-expanded: {expanded_after}")

            # --- Step 4: Screenshot the menu ---
            log("Capturing Create menu...")
            menu_screenshot = take_screenshot(page, "flow_create_menu", FLOW_SCREENSHOTS_DIR)

            # --- Step 5: Discover menu items ---
            log("")
            log("=" * 50)
            log("DISCOVERING CREATE MENU ITEMS")
            log("=" * 50)
            log("")

            # Try to find menu-specific items
            menu_items = discover_menu_items(page)
            if menu_items:
                log(f"Found {len(menu_items)} menu items:")
                log("")
                for i, item in enumerate(menu_items, 1):
                    parts = [f"  {i}. <{item['tag']}>"]
                    if item.get('text'):
                        parts.append(f"text='{item['text'][:100]}'")
                    if item.get('ariaLabel'):
                        parts.append(f"aria-label='{item['ariaLabel']}'")
                    if item.get('role'):
                        parts.append(f"role='{item['role']}'")
                    if item.get('disabled'):
                        parts.append("DISABLED")
                    log(" | ".join(parts))
            else:
                log("No specific menu items found via menu selectors.")

            # Also get full page interactive elements to catch anything
            log("")
            log("Full interactive elements after menu opened:")
            log("")
            all_elements = discover_ui_elements(page, max_elements=100)
            if all_elements:
                log(f"Found {len(all_elements)} total interactive elements:")
                log("")
                for i, el in enumerate(all_elements, 1):
                    parts = [f"  {i}. <{el['tag']}>"]
                    if el.get('text'):
                        parts.append(f"text='{el['text'][:80]}'")
                    if el.get('ariaLabel'):
                        parts.append(f"aria-label='{el['ariaLabel']}'")
                    if el.get('role'):
                        parts.append(f"role='{el['role']}'")
                    if el.get('ariaExpanded'):
                        parts.append(f"aria-expanded='{el['ariaExpanded']}'")
                    if el.get('ariaSelected'):
                        parts.append(f"aria-selected='{el['ariaSelected']}'")
                    if el.get('disabled'):
                        parts.append("DISABLED")
                    log(" | ".join(parts))

            # --- Step 6: Report ---
            log("")
            log("=" * 50)
            log("CREATE MENU DISCOVERY COMPLETE")
            log(f"  Project URL: {current_url}")
            if menu_screenshot:
                log(f"  Screenshot: {menu_screenshot}")
            log(f"  Menu items found: {len(menu_items)}")
            log(f"  Total elements: {len(all_elements)}")
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
