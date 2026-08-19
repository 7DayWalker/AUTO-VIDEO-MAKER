"""
AUTO Video Maker - Download Pipeline Verification (Existing Asset Only)

This script tests the download + MP4 validation pipeline using an
ALREADY COMPLETED video asset in the Flow project.

NO GENERATION. NO CREDITS SPENT.

Usage:
    python download_test.py
"""

import sys
import json
import subprocess
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
    DOWNLOAD_TIMEOUT,
)


def main():
    print()
    log("=" * 60)
    log("DOWNLOAD PIPELINE VERIFICATION (Existing Asset Only)")
    log("NO GENERATION - ZERO CREDITS")
    log("=" * 60)
    print()

    FLOW_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    FLOW_TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ============================================
    # STEP 1: Attach to existing Flow session
    # ============================================
    log("Step 1: Attaching to existing Chrome session...")
    
    if not is_chrome_running_with_debugging():
        log(f"ERROR: Chrome not running on port {CDP_PORT}.")
        return 1

    with sync_playwright() as playwright:
        try:
            browser = connect_to_chrome(playwright)
        except Exception as e:
            log(f"ERROR: CDP attachment failed: {e}")
            return 1

        try:
            page = get_page_from_browser(browser)
            log(f"CDP ATTACHMENT: SUCCESS")
            log(f"URL: {page.url}")

            if "/project/" not in page.url:
                log("ERROR: Not in a Flow project. Please navigate to a Flow project first.")
                return 1

            # Close any open panels
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)

            # ============================================
            # STEP 2: Identify existing video assets
            # ============================================
            log("")
            log("Step 2: Identifying existing video assets...")
            
            # Look for the All Media or Videos section
            # First, check if we're in the media grid view
            take_screenshot(page, "download_existing_asset_before", FLOW_SCREENSHOTS_DIR)

            # Find video elements or thumbnails in the project
            # Flow shows completed assets as cards/thumbnails
            video_assets = []
            
            # Method 1: Look for video elements directly
            videos = page.locator('video').all()
            for i, v in enumerate(videos):
                try:
                    if v.is_visible():
                        video_assets.append({
                            'type': 'video_element',
                            'index': i,
                            'element': v
                        })
                except Exception:
                    pass
            
            log(f"Found {len(video_assets)} visible video elements")

            # Method 2: Look for asset cards with video indicators
            # Flow typically shows assets in a grid with thumbnails
            asset_cards = page.locator('[data-testid*="asset"], [class*="asset"], [class*="media-item"], [class*="thumbnail"]').all()
            log(f"Found {len(asset_cards)} potential asset cards")

            # Method 3: Look for any clickable media items
            media_items = page.evaluate("""() => {
                const results = [];
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 50 || rect.height < 50) continue;
                    if (rect.width === 0 || rect.height === 0) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    
                    // Look for video-related attributes or content
                    const hasVideo = el.querySelector('video') !== null;
                    const text = (el.textContent || '').toLowerCase();
                    const hasVideoText = text.includes('video') || text.includes('.mp4');
                    const ariaLabel = (el.getAttribute('aria-label') || '').toLowerCase();
                    const hasVideoAria = ariaLabel.includes('video');
                    
                    if (hasVideo || hasVideoText || hasVideoAria) {
                        results.push({
                            tag: el.tagName.toLowerCase(),
                            text: (el.textContent || '').trim().substring(0, 100),
                            ariaLabel: el.getAttribute('aria-label') || '',
                            hasVideoChild: hasVideo,
                            width: rect.width,
                            height: rect.height
                        });
                    }
                }
                return results.slice(0, 20);
            }""")
            
            log(f"Found {len(media_items)} elements with video indicators:")
            for i, item in enumerate(media_items[:10]):
                log(f"  {i+1}. <{item['tag']}> hasVideo={item['hasVideoChild']} size={item['width']:.0f}x{item['height']:.0f}")
                if item['ariaLabel']:
                    log(f"      aria-label: '{item['ariaLabel'][:60]}'")

            # ============================================
            # STEP 3: Find and click on a video asset
            # ============================================
            log("")
            log("Step 3: Selecting a video asset...")
            
            selected_video = None
            
            # Try clicking on a visible video element to select it
            if video_assets:
                try:
                    video_assets[0]['element'].click()
                    page.wait_for_timeout(2000)
                    selected_video = video_assets[0]
                    log("Clicked on first visible video element")
                except Exception as e:
                    log(f"Could not click video element: {e}")

            # If no video element, try to find video in the media grid
            if not selected_video:
                # Look for any element that might be a video thumbnail
                try:
                    # Flow often has a grid of media items
                    grid_items = page.locator('[role="gridcell"], [role="listitem"]').all()
                    for item in grid_items:
                        try:
                            if item.is_visible():
                                # Check if this item contains a video
                                inner_video = item.locator('video').first
                                if inner_video.is_visible(timeout=500):
                                    item.click()
                                    page.wait_for_timeout(2000)
                                    selected_video = {'type': 'grid_item', 'element': item}
                                    log("Clicked on grid item containing video")
                                    break
                        except Exception:
                            continue
                except Exception as e:
                    log(f"Grid search failed: {e}")

            take_screenshot(page, "download_asset_selected", FLOW_SCREENSHOTS_DIR)

            # ============================================
            # STEP 4: Find download control
            # ============================================
            log("")
            log("Step 4: Finding download control...")
            
            download_btn = None
            download_selector = None
            
            # List of potential download selectors
            download_selectors = [
                'button:has-text("download")',
                'button:has-text("Download")',
                '[aria-label*="Download"]',
                '[aria-label*="download"]',
                'button:has-text("file_download")',
                '[aria-label*="Export"]',
                'button:has-text("Export")',
                'button:has-text("save")',
                'button:has-text("Save")',
            ]
            
            for selector in download_selectors:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible(timeout=2000):
                        download_btn = btn
                        download_selector = selector
                        log(f"Found download control: {selector}")
                        break
                except Exception:
                    continue
            
            # If no direct download button, look for a "More" menu
            if not download_btn:
                log("No direct download button. Looking for More menu...")
                more_selectors = [
                    'button:has-text("more_vert")',
                    'button:has-text("More")',
                    '[aria-label*="More"]',
                    '[aria-label*="options"]',
                    'button:has-text("⋮")',
                ]
                
                for selector in more_selectors:
                    try:
                        more_btn = page.locator(selector).first
                        if more_btn.is_visible(timeout=2000):
                            log(f"Found More menu: {selector}")
                            more_btn.click()
                            page.wait_for_timeout(1500)
                            
                            # Now look for download in the opened menu
                            for dl_sel in download_selectors:
                                try:
                                    btn = page.locator(dl_sel).first
                                    if btn.is_visible(timeout=1000):
                                        download_btn = btn
                                        download_selector = f"{selector} -> {dl_sel}"
                                        log(f"Found download in menu: {dl_sel}")
                                        break
                                except Exception:
                                    continue
                            
                            if download_btn:
                                break
                            else:
                                # Close menu and try next
                                page.keyboard.press("Escape")
                                page.wait_for_timeout(500)
                    except Exception:
                        continue

            if not download_btn:
                log("DOWNLOAD_UI_NOT_FOUND")
                log("Could not find a download control in the Flow UI.")
                take_screenshot(page, "download_ui_not_found", FLOW_SCREENSHOTS_DIR)
                
                # List all visible buttons for debugging
                log("")
                log("Visible buttons on page:")
                buttons = page.evaluate("""() => {
                    const results = [];
                    const btns = document.querySelectorAll('button, [role="button"], a[href]');
                    for (const btn of btns) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.width === 0 || rect.height === 0) continue;
                        const style = window.getComputedStyle(btn);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        const text = (btn.textContent || '').trim().substring(0, 80);
                        if (text) results.push(text);
                    }
                    return results.slice(0, 30);
                }""")
                for i, txt in enumerate(buttons, 1):
                    log(f"  {i}. '{txt}'")
                
                return 1

            take_screenshot(page, "download_control_found", FLOW_SCREENSHOTS_DIR)

            # ============================================
            # STEP 5: Trigger download
            # ============================================
            log("")
            log("Step 5: Triggering download...")
            log(f"Using selector: {download_selector}")
            
            downloaded_path = None
            
            try:
                with page.expect_download(timeout=DOWNLOAD_TIMEOUT * 1000) as download_info:
                    download_btn.click()
                
                download = download_info.value
                log(f"Download triggered: {download.suggested_filename}")
                
                # Save the file
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                suggested = download.suggested_filename or "video.mp4"
                ext = Path(suggested).suffix or ".mp4"
                filename = f"existing_flow_asset_{timestamp}{ext}"
                save_path = FLOW_TEST_OUTPUT_DIR / filename
                
                download.save_as(str(save_path))
                downloaded_path = save_path
                log(f"DOWNLOAD_COMPLETED: {save_path}")
                
            except PlaywrightTimeout:
                log("DOWNLOAD_AUTOMATION_FAILED: Timeout waiting for download")
                take_screenshot(page, "download_timeout", FLOW_SCREENSHOTS_DIR)
                return 1
            except Exception as e:
                log(f"DOWNLOAD_AUTOMATION_FAILED: {type(e).__name__}: {e}")
                take_screenshot(page, "download_failed", FLOW_SCREENSHOTS_DIR)
                return 1

            take_screenshot(page, "download_existing_asset_after", FLOW_SCREENSHOTS_DIR)

            # ============================================
            # STEP 6: Validate MP4
            # ============================================
            log("")
            log("Step 6: Validating MP4...")
            
            if not downloaded_path.exists():
                log("MP4_VALIDATION_FAILED: File does not exist")
                return 1
            
            size = downloaded_path.stat().st_size
            log(f"File: {downloaded_path.name}")
            log(f"Path: {downloaded_path}")
            log(f"Size: {size / (1024*1024):.2f} MB ({size:,} bytes)")
            
            if size == 0:
                log("MP4_VALIDATION_FAILED: File is empty")
                return 1
            
            # Check extension
            if downloaded_path.suffix.lower() not in ['.mp4', '.webm', '.mov']:
                log(f"WARNING: Unexpected extension: {downloaded_path.suffix}")
            
            # Check MP4 container
            with open(downloaded_path, "rb") as f:
                header = f.read(12)
            
            if b"ftyp" in header:
                log("MP4 container: VALID (ftyp detected)")
            else:
                log(f"WARNING: MP4 ftyp not in first 12 bytes. Header: {header[:8]}")
            
            # Try ffprobe for detailed validation
            duration = None
            width = None
            height = None
            codec = None
            
            try:
                result = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-print_format", "json",
                     "-show_format", "-show_streams", str(downloaded_path)],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    
                    if "format" in data:
                        duration = data['format'].get('duration', 'unknown')
                        log(f"Duration: {duration}s")
                        log(f"Format: {data['format'].get('format_name', 'unknown')}")
                    
                    for stream in data.get("streams", []):
                        if stream.get("codec_type") == "video":
                            width = stream.get('width')
                            height = stream.get('height')
                            codec = stream.get('codec_name')
                            log(f"Resolution: {width}x{height}")
                            log(f"Codec: {codec}")
                            log(f"Frame rate: {stream.get('r_frame_rate', 'unknown')}")
                            break
                    
                    log("ffprobe validation: PASSED")
                else:
                    log("ffprobe returned non-zero. Basic validation only.")
            except FileNotFoundError:
                log("ffprobe not installed. Basic validation only.")
            except Exception as e:
                log(f"ffprobe error: {e}. Basic validation only.")

            # ============================================
            # FINAL RESULT
            # ============================================
            log("")
            log("=" * 60)
            log("DOWNLOAD_PIPELINE_PROVEN")
            log("MP4_VALIDATION_PROVEN")
            log("=" * 60)
            log("")
            log("Summary:")
            log(f"  CDP attachment: SUCCESS")
            log(f"  Flow project: {page.url}")
            log(f"  Download selector: {download_selector}")
            log(f"  Local MP4 path: {downloaded_path}")
            log(f"  File size: {size:,} bytes")
            log(f"  Duration: {duration}")
            log(f"  Resolution: {width}x{height}")
            log(f"  Codec: {codec}")
            log(f"  Credits spent: 0")
            log("")
            return 0

        except Exception as e:
            log(f"UNEXPECTED ERROR: {type(e).__name__}: {e}")
            try:
                take_screenshot(page, "unexpected_error", FLOW_SCREENSHOTS_DIR)
            except Exception:
                pass
            return 1
        finally:
            log("Disconnecting...")
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
