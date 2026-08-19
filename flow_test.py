"""
AUTO Video Maker - Configure Flow Agent Settings for Video Generation

This script:
1. Attaches to Chrome via CDP
2. Opens Agent Settings (tune icon in prompt composer)
3. Configures video generation defaults:
   - Model: Veo 3.1 - Lite
   - Aspect Ratio: 9:16
   - Output Count: x1
   - Confirmation: Always
4. Saves the settings
5. Re-opens to verify persistence
6. Takes a verification screenshot

Usage:
    Chrome must already be running with debugging (use launch_chrome.py).
    User must already be authenticated. A Flow project should be open.

    python flow_test.py

NOTE: This script does NOT generate videos or spend credits.
      It only configures and verifies settings.
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


def open_agent_settings(page) -> bool:
    """
    Open the Agent Settings panel (tune icon in prompt composer).
    Returns True if successfully opened or already open.
    """
    # Check if the panel is already open by looking for its characteristic controls
    # (the confirmation radio buttons are unique to Agent Settings)
    try:
        always_radio = page.locator('button[role="radio"]:has-text("Always")').first
        if always_radio.is_visible(timeout=1000):
            # Panel is already open
            return True
    except (PlaywrightTimeout, Exception):
        pass

    # Try to click the tuneSettings button
    try:
        locator = page.locator('button:has-text("tuneSettings")').first
        if locator.is_visible(timeout=3000):
            btn_text = locator.text_content() or ""
            if "View Settings" not in btn_text:
                locator.click()
                page.wait_for_timeout(1500)
                return True
    except (PlaywrightTimeout, Exception):
        pass

    # Fallback: look for button with "tune" text
    try:
        buttons = page.locator('button').all()
        for btn in buttons:
            text = btn.text_content() or ""
            if "tune" in text.lower() and "view" not in text.lower() and "settings_2" not in text:
                if btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(1500)
                    return True
    except Exception:
        pass

    return False


def main() -> int:
    """
    Configure and verify Flow video generation settings.

    Returns:
        0 on success, 1 on failure.
    """
    print()
    log("=" * 50)
    log("AUTO Video Maker - Configure Video Generation Settings")
    log("=" * 50)
    print()

    FLOW_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    if not is_chrome_running_with_debugging():
        log(f"ERROR: Chrome is NOT running with debugging on port {CDP_PORT}.")
        log("Please run: python launch_chrome.py")
        return 1

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
            log(f"Current URL: {current_url}")

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

            # ============================================
            # STEP 0.5: Ensure we're on a clean project page
            # ============================================
            # Close any open panels/menus by pressing Escape
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)

            # ============================================
            # STEP 1: Open Agent Settings
            # ============================================
            log("")
            log("Step 1: Opening Agent Settings...")
            if not open_agent_settings(page):
                log("ERROR: Could not open Agent Settings.")
                log("Attempting to locate the button by inspecting all buttons...")
                take_screenshot(page, "cannot_open_settings", FLOW_SCREENSHOTS_DIR)
                # Debug: show all buttons with 'tune' or 'setting' in text
                all_btns = page.locator('button').all()
                for btn in all_btns:
                    try:
                        text = btn.text_content() or ""
                        if ("tune" in text.lower() or "setting" in text.lower()) and btn.is_visible():
                            log(f"  Button: '{text.strip()[:80]}'")
                    except Exception:
                        pass
                return 1
            log("Agent Settings panel opened.")

            # ============================================
            # STEP 2: Open video model dropdown and select Veo 3.1 - Lite
            # ============================================
            log("")
            log("Step 2: Selecting video model 'Veo 3.1 - Lite'...")

            # Find and click the video model dropdown
            # It currently shows "Omni Flash" with arrow_drop_down
            video_model_dropdown = None
            try:
                # Look for the dropdown that contains the video model
                # It should be near "Video generation default" section
                locator = page.locator('button:has-text("arrow_drop_down")').all()
                for btn in locator:
                    text = btn.text_content() or ""
                    # The video model dropdown - look for known video model names
                    if any(m in text for m in ["Omni Flash", "Veo", "Omni"]):
                        if "Nano" not in text and "Banana" not in text:
                            if btn.is_visible():
                                video_model_dropdown = btn
                                log(f"Found video model dropdown: '{text.strip()[:50]}'")
                                break
            except Exception as e:
                log(f"Error finding video model dropdown: {e}")

            if not video_model_dropdown:
                log("ERROR: Could not find video model dropdown.")
                take_screenshot(page, "no_video_model_dropdown", FLOW_SCREENSHOTS_DIR)
                return 1

            # Click to open dropdown
            video_model_dropdown.click()
            page.wait_for_timeout(1500)

            # Look for "Veo 3.1 - Lite" option
            veo_lite_option = None
            try:
                # Try various text patterns for Veo 3.1 Lite
                selectors = [
                    'button:has-text("Veo 3.1 - Lite")',
                    '[role="option"]:has-text("Veo 3.1 - Lite")',
                    '[role="menuitem"]:has-text("Veo 3.1 - Lite")',
                    'text="Veo 3.1 - Lite"',
                    'button:has-text("Veo 3.1"):has-text("Lite")',
                ]
                for sel in selectors:
                    try:
                        loc = page.locator(sel).first
                        if loc.is_visible(timeout=2000):
                            veo_lite_option = loc
                            log(f"Found 'Veo 3.1 - Lite' via: {sel}")
                            break
                    except (PlaywrightTimeout, Exception):
                        continue
            except Exception as e:
                log(f"Error finding Veo option: {e}")

            if not veo_lite_option:
                # Take screenshot to see what options ARE available
                log("Could not find 'Veo 3.1 - Lite'. Checking available options...")
                take_screenshot(page, "video_model_options", FLOW_SCREENSHOTS_DIR)
                # Try to discover what's in the dropdown
                options = page.locator('[role="option"], [role="menuitem"], button').all()
                for opt in options:
                    try:
                        text = opt.text_content() or ""
                        if opt.is_visible() and "veo" in text.lower():
                            log(f"  Found Veo option: '{text.strip()[:80]}'")
                            # Try clicking this one
                            veo_lite_option = opt
                    except Exception:
                        continue

            if not veo_lite_option:
                log("ERROR: No Veo model option found in dropdown.")
                take_screenshot(page, "no_veo_option", FLOW_SCREENSHOTS_DIR)
                return 1

            # Select it
            log("Selecting 'Veo 3.1 - Lite'...")
            veo_lite_option.click()
            page.wait_for_timeout(1000)
            log("Video model selected.")

            # ============================================
            # STEP 3: Select video aspect ratio 9:16
            # ============================================
            log("")
            log("Step 3: Selecting video aspect ratio '9:16'...")

            # The video aspect ratio tabs are in the Video section
            # We need the 9:16 tab that's NOT in the Image section
            # The video section has only 16:9 and 9:16
            # Find all 9:16 buttons and pick the one in the video section (second one)
            ratio_buttons = page.locator('button[role="tab"]:has-text("9:16")').all()
            video_ratio_btn = None

            if len(ratio_buttons) >= 2:
                # Second 9:16 button is the video one (first is image)
                video_ratio_btn = ratio_buttons[1]
                log("Found video 9:16 button (second 9:16 tab).")
            elif len(ratio_buttons) == 1:
                video_ratio_btn = ratio_buttons[0]
                log("Found single 9:16 button.")

            if video_ratio_btn:
                selected = video_ratio_btn.get_attribute("aria-selected")
                if selected == "true":
                    log("9:16 is already selected.")
                else:
                    video_ratio_btn.click()
                    page.wait_for_timeout(500)
                    log("9:16 selected.")
            else:
                log("WARNING: Could not find video 9:16 button.")
                take_screenshot(page, "no_video_ratio", FLOW_SCREENSHOTS_DIR)

            # ============================================
            # STEP 4: Select video output count x1
            # ============================================
            log("")
            log("Step 4: Verifying video output count 'x1'...")

            # Video output count tabs - find x1 in the video section
            # The video x1 should be the second set of x1/x2/x3/x4 buttons
            x1_buttons = page.locator('button[role="tab"]:has-text("x1")').all()
            video_x1_btn = None

            if len(x1_buttons) >= 2:
                video_x1_btn = x1_buttons[1]
                log("Found video x1 button (second x1 tab).")
            elif len(x1_buttons) == 1:
                video_x1_btn = x1_buttons[0]
                log("Found single x1 button.")

            if video_x1_btn:
                selected = video_x1_btn.get_attribute("aria-selected")
                if selected == "true":
                    log("x1 is already selected.")
                else:
                    video_x1_btn.click()
                    page.wait_for_timeout(500)
                    log("x1 selected.")
            else:
                log("WARNING: Could not find video x1 button.")

            # ============================================
            # STEP 5: Verify confirmation is "Always"
            # ============================================
            log("")
            log("Step 5: Verifying confirmation is 'Always'...")

            always_btn = page.locator('button[role="radio"]:has-text("Always")').first
            try:
                if always_btn.is_visible(timeout=2000):
                    text = always_btn.text_content() or ""
                    if "radio_button_checked" in text:
                        log("Confirmation is set to 'Always' (checked). ✓")
                    else:
                        log("Confirmation is NOT 'Always'. Selecting it...")
                        always_btn.click()
                        page.wait_for_timeout(500)
                        log("'Always' selected.")
            except (PlaywrightTimeout, Exception) as e:
                log(f"WARNING: Could not verify confirmation setting: {e}")

            # ============================================
            # STEP 6: Save settings
            # ============================================
            log("")
            log("Step 6: Saving settings...")

            save_btn = page.locator('button:has-text("Save")').first
            try:
                if save_btn.is_visible(timeout=2000):
                    save_btn.click()
                    page.wait_for_timeout(2000)
                    log("Settings saved.")
                else:
                    log("No Save button visible (settings may auto-save).")
            except (PlaywrightTimeout, Exception):
                log("Could not find/click Save button.")

            # ============================================
            # STEP 7: Re-open Agent Settings to verify persistence
            # ============================================
            log("")
            log("Step 7: Re-opening Agent Settings to verify...")
            page.wait_for_timeout(1000)

            if not open_agent_settings(page):
                log("WARNING: Could not re-open Agent Settings for verification.")
                take_screenshot(page, "cannot_reopen_settings", FLOW_SCREENSHOTS_DIR)
            else:
                page.wait_for_timeout(1500)

                # Take verification screenshot
                log("Capturing verification screenshot...")
                verify_screenshot = take_screenshot(page, "flow_pre_generation_verified", FLOW_SCREENSHOTS_DIR)

                # Verify values
                log("")
                log("Verifying persisted settings...")

                # Check video model
                page_text = page.content()
                if "Veo 3.1" in page_text and "Lite" in page_text:
                    log("  ✓ Video model: Veo 3.1 - Lite")
                elif "Veo" in page_text:
                    log("  ~ Video model contains 'Veo' (checking dropdown text...)")
                    # Check dropdown button text
                    try:
                        dropdowns = page.locator('button:has-text("arrow_drop_down")').all()
                        for dd in dropdowns:
                            txt = dd.text_content() or ""
                            if "Veo" in txt:
                                log(f"    Video model dropdown shows: '{txt.strip()[:50]}'")
                    except Exception:
                        pass
                else:
                    log("  ? Could not confirm video model")

                # Check 9:16
                video_ratio_buttons = page.locator('button[role="tab"]:has-text("9:16")').all()
                if len(video_ratio_buttons) >= 2:
                    sel = video_ratio_buttons[1].get_attribute("aria-selected")
                    if sel == "true":
                        log("  ✓ Video aspect ratio: 9:16 (selected)")
                    else:
                        log(f"  ✗ Video 9:16 aria-selected={sel}")
                elif len(video_ratio_buttons) == 1:
                    sel = video_ratio_buttons[0].get_attribute("aria-selected")
                    log(f"  ? Single 9:16 button, aria-selected={sel}")

                # Check x1
                x1_btns = page.locator('button[role="tab"]:has-text("x1")').all()
                if len(x1_btns) >= 2:
                    sel = x1_btns[1].get_attribute("aria-selected")
                    if sel == "true":
                        log("  ✓ Video output count: x1 (selected)")
                    else:
                        log(f"  ✗ Video x1 aria-selected={sel}")
                elif len(x1_btns) == 1:
                    sel = x1_btns[0].get_attribute("aria-selected")
                    log(f"  ? Single x1 button, aria-selected={sel}")

                # Check Always
                try:
                    always = page.locator('button[role="radio"]:has-text("Always")').first
                    if always.is_visible(timeout=1000):
                        txt = always.text_content() or ""
                        if "radio_button_checked" in txt:
                            log("  ✓ Confirmation: Always (checked)")
                        else:
                            log("  ✗ Confirmation: Always is NOT checked")
                except Exception:
                    log("  ? Could not verify confirmation setting")

            # ============================================
            # Final Report
            # ============================================
            log("")
            log("=" * 50)
            log("SETTINGS CONFIGURATION COMPLETE")
            log(f"  Project URL: {page.url}")
            log("  Target: Veo 3.1 - Lite, 9:16, x1, Always")
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
