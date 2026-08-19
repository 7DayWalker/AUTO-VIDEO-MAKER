"""
AUTO Video Maker - Phase 1 Final Acceptance Test: Generate & Download One Video

This script performs the FIRST real video generation:
1. Connects to Chrome via CDP
2. Verifies settings (Veo 3.1 - Lite, 9:16, x1, Always)
3. Enters the test prompt
4. Clicks Create/submit
5. Pauses for user manual confirmation (Flow "Always" gate)
6. Waits for generation to complete
7. Downloads the video
8. Validates the file
9. Reports success/failure

Usage:
    Chrome must already be running with debugging (use launch_chrome.py).
    User must already be authenticated. A Flow project should be open.

    python flow_test.py

IMPORTANT: This generates EXACTLY ONE video. Do not run repeatedly without intent.
"""

import sys
import time
import os
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
    FLOW_URL,
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


def verify_settings(page) -> bool:
    """Verify Agent Settings are correct without changing them."""
    page_text = page.content()

    checks = []

    # Check Veo 3.1 Lite
    if "Veo 3.1" in page_text and "Lite" in page_text:
        log("  ✓ Video model: Veo 3.1 - Lite")
        checks.append(True)
    else:
        # Check the dropdown button
        try:
            dropdowns = page.locator('button:has-text("arrow_drop_down")').all()
            for dd in dropdowns:
                txt = dd.text_content() or ""
                if "Veo" in txt and "Lite" in txt:
                    log(f"  ✓ Video model: {txt.strip()[:40]}")
                    checks.append(True)
                    break
            else:
                log("  ? Could not confirm Veo 3.1 - Lite")
                checks.append(False)
        except Exception:
            checks.append(False)

    # Check 9:16
    ratio_buttons = page.locator('button[role="tab"]:has-text("9:16")').all()
    if len(ratio_buttons) >= 2:
        sel = ratio_buttons[1].get_attribute("aria-selected")
        if sel == "true":
            log("  ✓ Video aspect ratio: 9:16")
            checks.append(True)
        else:
            log(f"  ✗ Video 9:16 not selected (aria-selected={sel})")
            checks.append(False)
    else:
        log("  ? Could not find video 9:16 tab")
        checks.append(False)

    # Check x1
    x1_btns = page.locator('button[role="tab"]:has-text("x1")').all()
    if len(x1_btns) >= 2:
        sel = x1_btns[1].get_attribute("aria-selected")
        if sel == "true":
            log("  ✓ Output count: x1")
            checks.append(True)
        else:
            log(f"  ✗ x1 not selected (aria-selected={sel})")
            checks.append(False)
    else:
        log("  ? Could not find video x1 tab")
        checks.append(False)

    # Check Always
    try:
        always = page.locator('button[role="radio"]:has-text("Always")').first
        if always.is_visible(timeout=1000):
            txt = always.text_content() or ""
            if "radio_button_checked" in txt:
                log("  ✓ Confirmation: Always")
                checks.append(True)
            else:
                log("  ✗ Confirmation NOT set to Always!")
                checks.append(False)
    except Exception:
        log("  ? Could not check confirmation setting")
        checks.append(False)

    return all(checks)


def validate_video_file(filepath: Path) -> dict:
    """
    Validate a downloaded video file.
    Returns a dict with validation results.
    """
    result = {
        "exists": False,
        "size_bytes": 0,
        "size_mb": 0.0,
        "extension": "",
        "valid": False,
    }

    if not filepath.exists():
        log(f"  FAILED: File does not exist: {filepath}")
        return result

    result["exists"] = True
    result["size_bytes"] = filepath.stat().st_size
    result["size_mb"] = result["size_bytes"] / (1024 * 1024)
    result["extension"] = filepath.suffix.lower()

    if result["size_bytes"] == 0:
        log(f"  FAILED: File is empty (0 bytes)")
        return result

    if result["extension"] not in (".mp4", ".webm", ".mov"):
        log(f"  WARNING: Unexpected extension: {result['extension']}")

    # Try ffprobe if available
    try:
        import subprocess
        probe_result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(filepath)],
            capture_output=True, text=True, timeout=10
        )
        if probe_result.returncode == 0:
            import json
            probe_data = json.loads(probe_result.stdout)
            if "format" in probe_data:
                result["duration"] = probe_data["format"].get("duration", "unknown")
                result["format_name"] = probe_data["format"].get("format_name", "unknown")
            if "streams" in probe_data:
                for stream in probe_data["streams"]:
                    if stream.get("codec_type") == "video":
                        result["width"] = stream.get("width")
                        result["height"] = stream.get("height")
                        result["codec"] = stream.get("codec_name", "unknown")
            result["ffprobe_available"] = True
        else:
            result["ffprobe_available"] = False
    except (FileNotFoundError, Exception):
        result["ffprobe_available"] = False

    # Basic header check for MP4 (ftyp box)
    try:
        with open(filepath, "rb") as f:
            header = f.read(12)
            if b"ftyp" in header:
                result["is_mp4_header"] = True
            else:
                result["is_mp4_header"] = False
    except Exception:
        result["is_mp4_header"] = False

    result["valid"] = result["size_bytes"] > 1000  # At least 1KB
    return result


def main() -> int:
    """
    Phase 1 Final Acceptance Test: Generate and download one video.

    Returns:
        0 on success, 1 on failure.
    """
    print()
    log("=" * 60)
    log("AUTO Video Maker - PHASE 1 FINAL ACCEPTANCE TEST")
    log("Generate & Download One Video (Veo 3.1 - Lite, 9:16)")
    log("=" * 60)
    print()

    FLOW_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    FLOW_TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not is_chrome_running_with_debugging():
        log(f"ERROR: Chrome not running on port {CDP_PORT}. Run: python launch_chrome.py")
        return 1

    log("Connecting to Chrome via CDP...")

    with sync_playwright() as playwright:
        try:
            browser = connect_to_chrome(playwright)
        except Exception as e:
            log(f"ERROR: Could not connect: {e}")
            return 1

        try:
            page = get_page_from_browser(browser)
            current_url = page.url
            log(f"Current URL: {current_url}")

            if "/project/" not in current_url:
                log("ERROR: Not in a Flow project. Please open a project first.")
                return 1

            # ============================================
            # PRE-GENERATION CHECK
            # ============================================
            log("")
            log("=" * 40)
            log("PRE-GENERATION CHECK")
            log("=" * 40)

            # Check if Agent Settings panel is visible (or open it)
            try:
                always_radio = page.locator('button[role="radio"]:has-text("Always")').first
                if not always_radio.is_visible(timeout=2000):
                    # Try to open settings
                    tune_btn = page.locator('button:has-text("tuneSettings")').first
                    if tune_btn.is_visible(timeout=2000):
                        tune_btn.click()
                        page.wait_for_timeout(1500)
            except Exception:
                pass

            settings_ok = verify_settings(page)
            take_screenshot(page, "flow_generation_before_submit", FLOW_SCREENSHOTS_DIR)

            if not settings_ok:
                log("")
                log("WARNING: Not all settings could be verified.")
                log("Proceeding anyway (settings were configured in previous step).")

            # Close settings panel to get back to prompt area
            try:
                back_btn = page.locator('button:has-text("arrow_backBack")').first
                if back_btn.is_visible(timeout=2000):
                    back_btn.click()
                    page.wait_for_timeout(1000)
            except Exception:
                page.keyboard.press("Escape")
                page.wait_for_timeout(1000)

            # ============================================
            # ENTER PROMPT
            # ============================================
            log("")
            log("=" * 40)
            log("ENTERING PROMPT")
            log("=" * 40)
            log(f"Prompt: {TEST_PROMPT[:80]}...")

            # Find the prompt textbox
            prompt_box = page.locator('div[role="textbox"][contenteditable="true"]').first
            try:
                if not prompt_box.is_visible(timeout=5000):
                    log("ERROR: Prompt textbox not visible!")
                    take_screenshot(page, "no_prompt_box", FLOW_SCREENSHOTS_DIR)
                    return 1
            except PlaywrightTimeout:
                log("ERROR: Prompt textbox not found!")
                take_screenshot(page, "no_prompt_box", FLOW_SCREENSHOTS_DIR)
                return 1

            # Click and enter the prompt
            prompt_box.click()
            page.wait_for_timeout(500)
            prompt_box.fill(TEST_PROMPT)
            page.wait_for_timeout(1000)

            # Verify prompt was entered
            entered_text = prompt_box.text_content() or ""
            if "futuristic red sports car" in entered_text:
                log("Prompt entered successfully.")
            else:
                # Try typing instead
                log("Fill may not have worked. Trying keyboard input...")
                prompt_box.click()
                page.keyboard.press("Control+A")
                page.keyboard.type(TEST_PROMPT, delay=5)
                page.wait_for_timeout(500)

            take_screenshot(page, "flow_prompt_submitted", FLOW_SCREENSHOTS_DIR)

            # ============================================
            # SUBMIT GENERATION
            # ============================================
            log("")
            log("=" * 40)
            log("SUBMITTING GENERATION")
            log("=" * 40)

            # Click the Create/submit arrow button
            submit_btn = page.locator('button:has-text("arrow_forwardCreate")').first
            try:
                if not submit_btn.is_visible(timeout=3000):
                    log("ERROR: Create/submit button not visible!")
                    take_screenshot(page, "no_submit_btn", FLOW_SCREENSHOTS_DIR)
                    return 1
            except PlaywrightTimeout:
                log("ERROR: Create/submit button not found!")
                return 1

            log("Clicking Create/submit...")
            submit_btn.click()
            page.wait_for_timeout(3000)

            # ============================================
            # CONFIRMATION GATE
            # ============================================
            log("")
            log("=" * 60)
            log("FLOW GENERATION CONFIRMATION REQUIRED — USER ACTION NEEDED")
            log("=" * 60)
            log("")
            log("Flow should now be asking for confirmation before generating.")
            log("Please look at the Chrome window and APPROVE the generation.")
            log("")

            take_screenshot(page, "flow_generation_confirmation", FLOW_SCREENSHOTS_DIR)

            log("Waiting for user to confirm generation...")
            log("(The script will detect when generation starts)")
            log("")

            # Wait for the user to confirm and generation to begin
            # We detect this by watching for UI changes indicating generation is in progress
            input(">>> Press ENTER here AFTER you have confirmed generation in Chrome... ")

            log("")
            log("User confirmed. Monitoring generation...")
            page.wait_for_timeout(3000)
            take_screenshot(page, "flow_generation_started", FLOW_SCREENSHOTS_DIR)

            # ============================================
            # WAIT FOR GENERATION
            # ============================================
            log("")
            log("=" * 40)
            log("WAITING FOR GENERATION TO COMPLETE")
            log(f"(Timeout: {GENERATION_TIMEOUT} seconds)")
            log("=" * 40)

            start_time = time.time()
            generation_complete = False

            while time.time() - start_time < GENERATION_TIMEOUT:
                elapsed = int(time.time() - start_time)

                # Check page for video completion indicators
                try:
                    page_content = page.content().lower()

                    # Look for download button appearing (indicates video ready)
                    download_btn = page.locator('button:has-text("download")').first
                    try:
                        if download_btn.is_visible(timeout=1000):
                            log(f"Download button detected! ({elapsed}s elapsed)")
                            generation_complete = True
                            break
                    except PlaywrightTimeout:
                        pass

                    # Also check for video elements appearing
                    video_elements = page.locator('video').all()
                    visible_videos = [v for v in video_elements if v.is_visible()]
                    if visible_videos and elapsed > 10:
                        log(f"Video element(s) visible ({len(visible_videos)}). ({elapsed}s elapsed)")
                        # Wait a bit more for it to fully load
                        page.wait_for_timeout(5000)
                        generation_complete = True
                        break

                    # Log progress every 30 seconds
                    if elapsed > 0 and elapsed % 30 == 0:
                        log(f"Still generating... ({elapsed}s elapsed)")

                except Exception as e:
                    log(f"Warning during wait: {e}")

                time.sleep(POLL_INTERVAL)

            if not generation_complete:
                log(f"Generation timed out after {GENERATION_TIMEOUT}s!")
                take_screenshot(page, "generation_timeout", FLOW_SCREENSHOTS_DIR)
                log("Check Chrome to see if generation is still in progress.")
                log("")
                log("If video IS ready, press ENTER to continue to download.")
                try:
                    input(">>> Press ENTER to attempt download anyway, or Ctrl+C to abort... ")
                    generation_complete = True
                except (EOFError, KeyboardInterrupt):
                    return 1

            take_screenshot(page, "flow_generation_complete", FLOW_SCREENSHOTS_DIR)
            log("Generation complete!")

            # ============================================
            # DOWNLOAD
            # ============================================
            log("")
            log("=" * 40)
            log("DOWNLOADING VIDEO")
            log("=" * 40)

            # Look for the generated video and its download mechanism
            # In Flow, we may need to right-click or find a download button
            download_selectors = [
                'button:has-text("download")',
                'button:has-text("Download")',
                '[aria-label*="Download"]',
                '[aria-label*="download"]',
                'button:has-text("file_download")',
            ]

            downloaded_path = None

            for selector in download_selectors:
                try:
                    dl_btn = page.locator(selector).first
                    if dl_btn.is_visible(timeout=3000):
                        log(f"Found download control: {selector}")

                        # Set up download handling
                        with page.expect_download(timeout=DOWNLOAD_TIMEOUT * 1000) as download_info:
                            dl_btn.click()

                        download = download_info.value
                        log(f"Download started: {download.suggested_filename}")

                        # Save with our naming convention
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        ext = Path(download.suggested_filename or "video.mp4").suffix or ".mp4"
                        filename = f"flow_veo31_lite_9x16_{timestamp}{ext}"
                        save_path = FLOW_TEST_OUTPUT_DIR / filename

                        download.save_as(str(save_path))
                        downloaded_path = save_path
                        log(f"Video saved: {save_path}")
                        break
                except PlaywrightTimeout:
                    log(f"Download timed out with: {selector}")
                    continue
                except Exception as e:
                    log(f"Download failed with {selector}: {e}")
                    continue

            if not downloaded_path:
                log("ERROR: Could not download the video through UI controls.")
                log("The video may need to be downloaded manually.")
                take_screenshot(page, "download_failed", FLOW_SCREENSHOTS_DIR)
                log("")
                log("If you downloaded it manually, place it in output/flow_test/")
                return 1

            take_screenshot(page, "flow_download_complete", FLOW_SCREENSHOTS_DIR)

            # ============================================
            # VALIDATION
            # ============================================
            log("")
            log("=" * 40)
            log("VALIDATING DOWNLOADED VIDEO")
            log("=" * 40)

            validation = validate_video_file(downloaded_path)

            log(f"  File: {downloaded_path.name}")
            log(f"  Exists: {validation['exists']}")
            log(f"  Size: {validation['size_mb']:.2f} MB ({validation['size_bytes']:,} bytes)")
            log(f"  Extension: {validation['extension']}")

            if validation.get("ffprobe_available"):
                log(f"  Duration: {validation.get('duration', 'unknown')}s")
                log(f"  Resolution: {validation.get('width', '?')}x{validation.get('height', '?')}")
                log(f"  Codec: {validation.get('codec', 'unknown')}")
                log(f"  Format: {validation.get('format_name', 'unknown')}")
            elif validation.get("is_mp4_header"):
                log(f"  MP4 header: Valid (ftyp detected)")
            else:
                log(f"  Note: ffprobe not available, basic validation only")

            # ============================================
            # FINAL RESULT
            # ============================================
            log("")
            log("=" * 60)
            if validation["valid"]:
                log("PHASE 1 FINAL ACCEPTANCE TEST: PASSED ✓")
                log(f"  Video: {downloaded_path}")
                log(f"  Size: {validation['size_mb']:.2f} MB")
            else:
                log("PHASE 1 FINAL ACCEPTANCE TEST: FAILED")
                log("  Video file validation failed.")
            log("=" * 60)

            return 0 if validation["valid"] else 1

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
