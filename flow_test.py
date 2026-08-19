"""
AUTO Video Maker - Phase 1 Second Generation Test (Standard Video Workflow)

This script performs ONE controlled video generation using the STANDARD Flow
prompt box with Agent OFF.

Configuration:
- Agent: OFF
- Type: Video
- Model: Veo 3.1 - Lite
- Aspect: 9:16
- Duration: 4s
- Output: x1

Usage:
    python flow_test.py

IMPORTANT: This generates EXACTLY ONE video. Do not run repeatedly.
"""

import sys
import time
import json
import subprocess
import re
from datetime import datetime
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
    FLOW_SCREENSHOTS_DIR,
    FLOW_TEST_OUTPUT_DIR,
    CDP_PORT,
    GENERATION_TIMEOUT,
    DOWNLOAD_TIMEOUT,
    POLL_INTERVAL,
)

TEST_PROMPT = (
    "Create a cinematic vertical video of a small futuristic red sports car slowly "
    "driving through a rain-soaked neon city at night. Wet asphalt reflects colorful "
    "city lights. The camera smoothly tracks alongside the car at low height. "
    "Realistic reflections, cinematic lighting, subtle atmospheric rain, "
    "photorealistic detail, natural motion, no text, no logos."
)

ACCOUNT_BALANCE = 50  # Verified by user: 50 AI credits


def find_visible_button(page, text_contains):
    """Find a visible button containing the given text."""
    for btn in page.locator('button').all():
        try:
            text = (btn.text_content() or "").strip()
            if btn.is_visible() and text_contains in text:
                return btn, text
        except Exception:
            continue
    return None, None


def main():
    print()
    log("=" * 60)
    log("PHASE 1 - SECOND GENERATION TEST (Standard Video)")
    log("=" * 60)
    print()

    FLOW_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    FLOW_TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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

            # Navigate to Flow project if needed
            if "/project/" not in page.url:
                log("Not in a Flow project. Navigating...")
                page.goto("https://flow.google", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                try:
                    new_proj = page.locator('button:has-text("New project")').first
                    if new_proj.is_visible(timeout=5000):
                        new_proj.click()
                        page.wait_for_timeout(5000)
                except Exception:
                    log("ERROR: Could not open project.")
                    return 1

            # Close any open panels
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

            # ============================================
            # STEP 1: Ensure Agent is OFF
            # ============================================
            log("")
            log("Step 1: Ensuring Agent is OFF...")
            agent_btn, _ = find_visible_button(page, "Agent")
            if agent_btn:
                pressed = agent_btn.get_attribute("aria-pressed")
                if pressed == "true":
                    agent_btn.click()
                    page.wait_for_timeout(1500)
                    log("Agent turned OFF.")
                else:
                    log("Agent already OFF.")
            else:
                log("WARNING: Agent button not found.")

            # ============================================
            # STEP 2: Open generation settings
            # ============================================
            log("")
            log("Step 2: Opening generation settings...")
            settings_btn = None
            for btn in page.locator('button').all():
                try:
                    text = (btn.text_content() or "").strip()
                    if btn.is_visible() and ("Nano" in text or "Veo" in text or "Omni" in text or "Video" in text):
                        if "crop" in text or "x1" in text or "x2" in text or "x3" in text or "x4" in text:
                            settings_btn = btn
                            log(f"Found settings: '{text[:60]}'")
                            break
                except Exception:
                    continue

            if not settings_btn:
                log("ERROR: Cannot find settings dropdown.")
                take_screenshot(page, "no_settings", FLOW_SCREENSHOTS_DIR)
                return 1

            expanded = settings_btn.get_attribute("aria-expanded")
            if expanded != "true":
                settings_btn.click()
                page.wait_for_timeout(1500)

            # ============================================
            # STEP 3: Select VIDEO tab
            # ============================================
            log("")
            log("Step 3: Selecting VIDEO tab...")
            video_tab = page.locator('button[role="tab"]:has-text("videocamVideo")').first
            try:
                if video_tab.is_visible(timeout=3000):
                    if video_tab.get_attribute("aria-selected") != "true":
                        video_tab.click()
                        page.wait_for_timeout(2000)
                        log("Video tab selected.")
                    else:
                        log("Video tab already selected.")
            except PlaywrightTimeout:
                log("ERROR: Video tab not found.")
                return 1

            # ============================================
            # STEP 4: Select model Veo 3.1 - Lite
            # ============================================
            log("")
            log("Step 4: Selecting model 'Veo 3.1 - Lite'...")
            model_dropdown = None
            for btn in page.locator('button').all():
                try:
                    text = (btn.text_content() or "").strip()
                    if btn.is_visible() and "arrow_drop_down" in text:
                        if any(m in text for m in ["Veo", "Omni", "Nano", "Gemini", "Flash"]):
                            model_dropdown = btn
                            log(f"Found model dropdown: '{text[:50]}'")
                            break
                except Exception:
                    continue

            if not model_dropdown:
                log("ERROR: Model dropdown not found.")
                return 1

            current_model_text = model_dropdown.text_content() or ""
            if "Veo 3.1 - Lite" in current_model_text:
                log("Model already set to Veo 3.1 - Lite.")
            else:
                model_dropdown.click()
                page.wait_for_timeout(1500)
                veo_lite = page.locator('[role="menuitem"]:has-text("Veo 3.1 - Lite")').first
                try:
                    if veo_lite.is_visible(timeout=3000):
                        veo_lite.click()
                        page.wait_for_timeout(1000)
                        log("Veo 3.1 - Lite selected.")
                    else:
                        log("ERROR: Veo 3.1 - Lite option not found.")
                        return 1
                except PlaywrightTimeout:
                    log("ERROR: Veo 3.1 - Lite option not found.")
                    return 1

            # ============================================
            # STEP 5: Select aspect ratio 9:16
            # ============================================
            log("")
            log("Step 5: Selecting aspect ratio 9:16...")
            ratio_btn = page.locator('button[role="tab"]:has-text("crop_9_169:16")').first
            try:
                if ratio_btn.is_visible(timeout=3000):
                    if ratio_btn.get_attribute("aria-selected") != "true":
                        ratio_btn.click()
                        page.wait_for_timeout(500)
                        log("9:16 selected.")
                    else:
                        log("9:16 already selected.")
            except PlaywrightTimeout:
                log("ERROR: 9:16 button not found.")
                return 1

            # ============================================
            # STEP 6: Select duration 4s
            # ============================================
            log("")
            log("Step 6: Selecting duration 4s...")
            dur_btn = page.locator('button[role="tab"]:has-text("4s")').first
            try:
                if dur_btn.is_visible(timeout=3000):
                    if dur_btn.get_attribute("aria-selected") != "true":
                        dur_btn.click()
                        page.wait_for_timeout(500)
                        log("4s selected.")
                    else:
                        log("4s already selected.")
            except PlaywrightTimeout:
                log("ERROR: 4s button not found.")
                return 1

            # ============================================
            # STEP 7: Select output count x1
            # ============================================
            log("")
            log("Step 7: Selecting output count x1...")
            x1_btn = page.locator('button[role="tab"]:has-text("x1")').first
            try:
                if x1_btn.is_visible(timeout=3000):
                    if x1_btn.get_attribute("aria-selected") != "true":
                        x1_btn.click()
                        page.wait_for_timeout(500)
                        log("x1 selected.")
                    else:
                        log("x1 already selected.")
            except PlaywrightTimeout:
                log("ERROR: x1 button not found.")
                return 1

            # ============================================
            # STEP 8: Read generation cost
            # ============================================
            log("")
            log("Step 8: Reading generation cost...")
            page.wait_for_timeout(500)
            cost_text = page.evaluate("""() => {
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const text = (el.textContent || '').trim();
                    if (text.toLowerCase().includes('generating will use') && text.length < 60) {
                        return text;
                    }
                }
                return null;
            }""")

            if cost_text:
                log(f"GENERATION COST: '{cost_text}'")
                match = re.search(r'(\d+)', cost_text)
                if match:
                    cost = int(match.group(1))
                    log(f"ACCOUNT BALANCE: {ACCOUNT_BALANCE} AI credits")
                    if cost > ACCOUNT_BALANCE:
                        log(f"ERROR: Generation cost ({cost}) exceeds balance ({ACCOUNT_BALANCE}). STOPPING.")
                        return 1
                    log(f"Cost ({cost}) <= Balance ({ACCOUNT_BALANCE}). Safe to proceed.")
            else:
                log("WARNING: Could not read generation cost text.")

            # Close the settings panel by pressing Escape or clicking outside
            log("")
            log("Closing settings panel...")
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)

            # ============================================
            # STEP 9: Pre-generation verification screenshot
            # ============================================
            log("")
            log("Step 9: Capturing pre-generation verification...")
            take_screenshot(page, "flow_video_pre_generation", FLOW_SCREENSHOTS_DIR)

            log("")
            log("Verifying settings:")
            page_text = page.content()
            log(f"  Veo 3.1 - Lite in page: {'Veo 3.1 - Lite' in page_text}")
            log(f"  Video tab: {'videocamVideo' in page_text}")
            log(f"  9:16: {'crop_9_169:16' in page_text}")
            log(f"  4s: {'4s' in page_text}")
            log(f"  x1: {'x1' in page_text}")

            # ============================================
            # STEP 10: Enter prompt
            # ============================================
            log("")
            log("Step 10: Entering prompt...")
            prompt_box = page.locator('div[role="textbox"][contenteditable="true"]').first
            try:
                if not prompt_box.is_visible(timeout=5000):
                    log("ERROR: Prompt textbox not visible.")
                    return 1
            except PlaywrightTimeout:
                log("ERROR: Prompt textbox not found.")
                return 1

            prompt_box.click()
            page.wait_for_timeout(500)
            prompt_box.fill(TEST_PROMPT)
            page.wait_for_timeout(1000)

            entered = prompt_box.text_content() or ""
            if "futuristic red sports car" in entered:
                log("Prompt entered successfully.")
            else:
                log("Fill may not have worked. Trying keyboard...")
                prompt_box.click()
                page.keyboard.press("Control+A")
                page.keyboard.type(TEST_PROMPT, delay=5)
                page.wait_for_timeout(500)

            take_screenshot(page, "flow_video_prompt", FLOW_SCREENSHOTS_DIR)

            # ============================================
            # STEP 11: Click Generate/Create
            # ============================================
            log("")
            log("Step 11: Clicking Generate/Create...")
            submit_btn = page.locator('button:has-text("arrow_forwardCreate")').first
            try:
                if not submit_btn.is_visible(timeout=3000):
                    log("ERROR: Create button not visible.")
                    return 1
            except PlaywrightTimeout:
                log("ERROR: Create button not found.")
                return 1

            submit_btn.click()
            page.wait_for_timeout(3000)

            # ============================================
            # STEP 12: Monitor generation
            # ============================================
            log("")
            log("=" * 60)
            log("GENERATION_STARTED - Monitoring...")
            log("=" * 60)
            take_screenshot(page, "flow_video_generation_started", FLOW_SCREENSHOTS_DIR)

            start_time = time.time()
            generation_complete = False
            flow_error = False

            while time.time() - start_time < GENERATION_TIMEOUT:
                elapsed = int(time.time() - start_time)

                # Check for explicit error
                page_text = page.content().lower()
                if "something went wrong" in page_text:
                    log("")
                    log("FLOW_GENERATION_ERROR: 'Something went wrong. Please try again.'")
                    take_screenshot(page, "flow_video_generation_error", FLOW_SCREENSHOTS_DIR)
                    flow_error = True
                    break

                # Check for completed video asset
                try:
                    dl_btn = page.locator('button:has-text("download")').first
                    if dl_btn.is_visible(timeout=1000):
                        log(f"Download button detected! ({elapsed}s elapsed)")
                        generation_complete = True
                        break

                    dl_btn2 = page.locator('button:has-text("Download")').first
                    if dl_btn2.is_visible(timeout=500):
                        log(f"Download button detected! ({elapsed}s elapsed)")
                        generation_complete = True
                        break
                except Exception:
                    pass

                if elapsed > 0 and elapsed % 30 == 0:
                    log(f"Still waiting... ({elapsed}s elapsed)")

                time.sleep(POLL_INTERVAL)

            if flow_error:
                log("")
                log("TEST STOPPED: Flow reported a generation error.")
                log("No retry will be attempted.")
                return 1

            if not generation_complete:
                log("")
                log(f"GENERATION_TIMEOUT after {GENERATION_TIMEOUT}s.")
                log("No objective evidence of a completed video asset.")
                take_screenshot(page, "generation_timeout", FLOW_SCREENSHOTS_DIR)
                return 1

            log("")
            log("GENERATION_COMPLETED - Objective evidence found.")
            take_screenshot(page, "flow_video_generation_complete", FLOW_SCREENSHOTS_DIR)

            # ============================================
            # STEP 13: Download
            # ============================================
            log("")
            log("=" * 60)
            log("DOWNLOAD_STARTED")
            log("=" * 60)

            downloaded_path = None
            download_selectors = [
                'button:has-text("download")',
                'button:has-text("Download")',
                '[aria-label*="Download"]',
                '[aria-label*="download"]',
                'button:has-text("file_download")',
            ]

            for selector in download_selectors:
                try:
                    dl_btn = page.locator(selector).first
                    if dl_btn.is_visible(timeout=3000):
                        log(f"Found download control: {selector}")
                        with page.expect_download(timeout=DOWNLOAD_TIMEOUT * 1000) as download_info:
                            dl_btn.click()
                        download = download_info.value
                        log(f"Download started: {download.suggested_filename}")

                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        ext = Path(download.suggested_filename or "video.mp4").suffix or ".mp4"
                        filename = f"flow_veo31_lite_9x16_4s_{timestamp}{ext}"
                        save_path = FLOW_TEST_OUTPUT_DIR / filename
                        download.save_as(str(save_path))
                        downloaded_path = save_path
                        log(f"DOWNLOAD_COMPLETED: {save_path}")
                        break
                except PlaywrightTimeout:
                    log(f"Download timed out with: {selector}")
                    continue
                except Exception as e:
                    log(f"Download failed with {selector}: {e}")
                    continue

            if not downloaded_path:
                log("ERROR: Could not download video.")
                take_screenshot(page, "download_failed", FLOW_SCREENSHOTS_DIR)
                return 1

            take_screenshot(page, "flow_download_complete", FLOW_SCREENSHOTS_DIR)

            # ============================================
            # STEP 14: Validate
            # ============================================
            log("")
            log("=" * 60)
            log("VALIDATING VIDEO")
            log("=" * 60)

            if not downloaded_path.exists():
                log("FAILED: File does not exist.")
                return 1

            size = downloaded_path.stat().st_size
            log(f"File: {downloaded_path.name}")
            log(f"Size: {size / (1024*1024):.2f} MB ({size:,} bytes)")

            if size == 0:
                log("FAILED: File is empty.")
                return 1

            with open(downloaded_path, "rb") as f:
                header = f.read(12)
            if b"ftyp" in header:
                log("MP4 container: Valid (ftyp detected)")
            else:
                log(f"WARNING: MP4 header not detected. First bytes: {header[:4]}")

            try:
                result = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-print_format", "json",
                     "-show_format", "-show_streams", str(downloaded_path)],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    if "format" in data:
                        log(f"Duration: {data['format'].get('duration', 'unknown')}s")
                        log(f"Format: {data['format'].get('format_name', 'unknown')}")
                    for stream in data.get("streams", []):
                        if stream.get("codec_type") == "video":
                            log(f"Resolution: {stream.get('width')}x{stream.get('height')}")
                            log(f"Codec: {stream.get('codec_name', 'unknown')}")
                    log("ffprobe validation: PASSED")
                else:
                    log("ffprobe not available or failed. Basic validation only.")
            except (FileNotFoundError, Exception):
                log("ffprobe not available. Basic validation only.")

            # ============================================
            # FINAL RESULT
            # ============================================
            log("")
            log("=" * 60)
            log("PHASE 1 SECOND GENERATION TEST: PASSED ✓")
            log(f"  Video: {downloaded_path}")
            log(f"  Size: {size / (1024*1024):.2f} MB")
            log("=" * 60)
            return 0

        except Exception as e:
            log(f"UNEXPECTED ERROR: {type(e).__name__}: {e}")
            try:
                take_screenshot(get_page_from_browser(browser), "unexpected_error", FLOW_SCREENSHOTS_DIR)
            except Exception:
                pass
            return 1
        finally:
            log("Disconnecting...")
            browser.close()


if __name__ == "__main__":
    sys.exit(main())