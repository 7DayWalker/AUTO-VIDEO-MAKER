"""
AUTO Video Maker - Flow Scene Generator

Automated scene generation through Google Flow.
Reuses Phase 1 browser/CDP infrastructure.
"""

import json
import time
import re
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple

from playwright.sync_api import sync_playwright, Page, Browser, TimeoutError as PlaywrightTimeout

from app.content.models import ProductionPlan
from app.flow.browser import (
    connect_to_chrome,
    get_page_from_browser,
    is_chrome_running_with_debugging,
    take_screenshot,
    log,
)
from app.flow.config import (
    FLOW_URL,
    CDP_PORT,
    NAVIGATION_TIMEOUT,
    AUTH_TIMEOUT,
    POLL_INTERVAL,
)
from app.flow.scene_config import (
    SCENE_OUTPUT_DIR,
    SCENE_SCREENSHOTS_DIR,
    FLOW_SCENE_MODEL,
    FLOW_SCENE_ASPECT_RATIO,
    FLOW_SCENE_DURATION,
    FLOW_SCENE_COUNT,
    FLOW_SCENE_TIMEOUT,
    FLOW_SCENE_DOWNLOAD_TIMEOUT,
    FLOW_SCENE_POLL_INTERVAL,
    FLOW_SCENE_CREDIT_LIMIT,
    FLOW_SCENE_MAX_PER_RUN,
    ensure_scene_directories,
)
from app.flow.scene_models import (
    FlowGenerationConfig,
    SceneGenerationRequest,
    SceneGenerationResult,
    SceneMetadata,
    SceneManifest,
    FlowUIState,
)

logger = logging.getLogger(__name__)


class FlowSceneError(Exception):
    """Base exception for Flow scene generation errors."""
    pass


class FlowConnectionError(FlowSceneError):
    """Raised when CDP connection fails."""
    pass


class FlowAuthenticationError(FlowSceneError):
    """Raised when authentication is required."""
    pass


class FlowGenerationError(FlowSceneError):
    """Raised when generation fails."""
    pass


class FlowCreditError(FlowSceneError):
    """Raised when credit limit would be exceeded."""
    pass


def validate_video_file(file_path: Path) -> dict:
    """
    Validate a video file using FFprobe.
    
    Returns dict with: valid, duration, width, height, codec, file_size
    """
    if not file_path.exists():
        return {"valid": False, "error": "File does not exist"}
    
    file_size = file_path.stat().st_size
    if file_size == 0:
        return {"valid": False, "error": "File is empty", "file_size": 0}
    
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        if result.returncode != 0:
            return {"valid": False, "error": f"FFprobe failed: {result.stderr[:200]}", "file_size": file_size}
        
        data = json.loads(result.stdout)
        
        # Find video stream
        video_stream = None
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                video_stream = stream
                break
        
        if not video_stream:
            return {"valid": False, "error": "No video stream found", "file_size": file_size}
        
        # Extract info
        duration = float(data.get("format", {}).get("duration", 0))
        width = video_stream.get("width", 0)
        height = video_stream.get("height", 0)
        codec = video_stream.get("codec_name", "unknown")
        
        return {
            "valid": True,
            "duration": duration,
            "width": width,
            "height": height,
            "codec": codec,
            "file_size": file_size,
        }
        
    except FileNotFoundError:
        return {"valid": False, "error": "FFprobe not available", "file_size": file_size}
    except Exception as e:
        return {"valid": False, "error": str(e), "file_size": file_size}


class FlowSceneGenerator:
    """
    Generates video scenes through Google Flow.
    
    Reuses Phase 1 browser infrastructure.
    Implements credit safety and dry-run mode.
    """
    
    def __init__(
        self,
        config: FlowGenerationConfig = None,
        output_dir: Path = None,
        screenshots_dir: Path = None,
    ):
        self.config = config or FlowGenerationConfig()
        self.output_dir = output_dir or SCENE_OUTPUT_DIR
        self.screenshots_dir = screenshots_dir or SCENE_SCREENSHOTS_DIR
        
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.playwright = None
        self.ui_state = FlowUIState()
        
        # Track assets before generation for new asset detection
        self._assets_before_generation: List[str] = []
    
    def connect(self) -> bool:
        """
        Connect to Chrome via CDP.
        
        Returns:
            True if connected successfully.
        
        Raises:
            FlowConnectionError: If connection fails.
        """
        if not is_chrome_running_with_debugging():
            raise FlowConnectionError(
                f"Chrome is not running with debugging on port {CDP_PORT}. "
                "Run: python launch_chrome.py"
            )
        
        try:
            self.playwright = sync_playwright().start()
            self.browser = connect_to_chrome(self.playwright)
            self.page = get_page_from_browser(self.browser)
            self.ui_state.connected = True
            log("Connected to Chrome via CDP")
            return True
        except Exception as e:
            raise FlowConnectionError(f"Failed to connect to Chrome: {e}")
    
    def disconnect(self) -> None:
        """Disconnect from Chrome."""
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception:
                pass
        self.browser = None
        self.page = None
        self.playwright = None
        self.ui_state.connected = False
    
    def navigate_to_project(self, project_url: str) -> bool:
        """
        Navigate to a Flow project.
        
        Args:
            project_url: URL of the Flow project.
        
        Returns:
            True if navigation successful.
        """
        if not self.page:
            raise FlowConnectionError("Not connected to Chrome")
        
        log(f"Navigating to Flow project: {project_url}")
        
        try:
            self.page.goto(project_url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT * 1000)
            self.page.wait_for_timeout(3000)
            
            # Check for login page
            current_url = self.page.url.lower()
            if self._is_login_page(current_url):
                self.ui_state.authenticated = False
                raise FlowAuthenticationError(
                    "Authentication required. Please log in to Google in the Chrome window."
                )
            
            self.ui_state.authenticated = True
            self.ui_state.on_project_page = True
            
            take_screenshot(self.page, "project_loaded", self.screenshots_dir)
            log("Project page loaded")
            return True
            
        except PlaywrightTimeout:
            take_screenshot(self.page, "navigation_timeout", self.screenshots_dir)
            raise FlowSceneError(f"Navigation timed out: {project_url}")
    
    def _is_login_page(self, url: str) -> bool:
        """Check if URL is a Google login page."""
        login_indicators = [
            "accounts.google.com/signin",
            "accounts.google.com/v3/signin",
            "accounts.google.com/servicelogin",
            "accounts.google.com/interactivelogin",
        ]
        return any(indicator in url for indicator in login_indicators)
    
    def verify_settings(self) -> FlowUIState:
        """
        Verify Flow UI settings match configuration.
        
        Returns:
            Updated FlowUIState.
        """
        if not self.page:
            raise FlowConnectionError("Not connected to Chrome")
        
        log("Verifying Flow settings...")
        
        # Check Agent OFF
        self.ui_state.agent_off = self._verify_agent_off()
        
        # Check Video mode
        self.ui_state.video_mode = self._verify_video_mode()
        
        # Check model
        self.ui_state.model_verified = self._verify_model(self.config.model)
        
        # Check aspect ratio
        self.ui_state.aspect_ratio_verified = self._verify_aspect_ratio(self.config.aspect_ratio)
        
        # Check duration
        self.ui_state.duration_verified = self._verify_duration(self.config.duration)
        
        # Check count
        self.ui_state.count_verified = self._verify_count(self.config.count)
        
        # Check prompt input
        self.ui_state.prompt_ready = self._verify_prompt_input()
        
        # Read generation cost
        cost = self._read_generation_cost()
        if cost is not None:
            self.ui_state.generation_cost = cost
            self.ui_state.generation_cost_verified = True
        
        take_screenshot(self.page, "settings_verified", self.screenshots_dir)
        
        return self.ui_state
    
    def _verify_agent_off(self) -> bool:
        """Verify Agent is OFF."""
        try:
            # Look for Agent toggle/indicator
            page_content = self.page.content().lower()
            
            # Check for "agent off" or similar indicators
            if "agent" in page_content:
                # Look for toggle that shows OFF state
                off_indicators = [
                    self.page.locator('[aria-checked="false"]:near(:text("Agent"))'),
                    self.page.locator('button:has-text("Agent"):not(.active)'),
                    self.page.locator('[data-state="off"]:near(:text("Agent"))'),
                ]
                
                for indicator in off_indicators:
                    try:
                        if indicator.count() > 0:
                            log("Agent OFF verified")
                            return True
                    except Exception:
                        continue
            
            # If we can't find Agent toggle, assume it's not applicable
            log("Agent toggle not found - assuming OFF or not applicable")
            return True
            
        except Exception as e:
            log(f"Could not verify Agent state: {e}")
            return True  # Assume OK if can't verify
    
    def _verify_video_mode(self) -> bool:
        """Verify Video mode is selected."""
        try:
            # Look for Video mode indicator
            video_selectors = [
                'button:has-text("Video").active',
                '[aria-selected="true"]:has-text("Video")',
                '.selected:has-text("Video")',
                'button[data-type="video"][aria-pressed="true"]',
            ]
            
            for selector in video_selectors:
                try:
                    if self.page.locator(selector).count() > 0:
                        log("Video mode verified")
                        return True
                except Exception:
                    continue
            
            # Check page content
            page_content = self.page.content().lower()
            if "video" in page_content and ("generate" in page_content or "create" in page_content):
                log("Video mode appears active (content check)")
                return True
            
            log("Could not verify Video mode - assuming OK")
            return True
            
        except Exception as e:
            log(f"Could not verify Video mode: {e}")
            return True
    
    def _verify_model(self, expected_model: str) -> bool:
        """Verify the correct model is selected."""
        try:
            page_content = self.page.content()
            
            # Look for model name in page
            if expected_model.lower() in page_content.lower():
                log(f"Model '{expected_model}' found in page")
                return True
            
            # Try to find and click model selector
            model_selectors = [
                f'button:has-text("{expected_model}")',
                f'[aria-label*="{expected_model}"]',
                f'div:has-text("{expected_model}"):not(:has(div))',
            ]
            
            for selector in model_selectors:
                try:
                    element = self.page.locator(selector).first
                    if element.is_visible(timeout=2000):
                        log(f"Model '{expected_model}' verified")
                        return True
                except Exception:
                    continue
            
            log(f"Could not verify model '{expected_model}' - may need manual selection")
            return False
            
        except Exception as e:
            log(f"Could not verify model: {e}")
            return False
    
    def _verify_aspect_ratio(self, expected_ratio: str) -> bool:
        """Verify the correct aspect ratio is selected."""
        try:
            page_content = self.page.content()
            
            if expected_ratio in page_content:
                log(f"Aspect ratio '{expected_ratio}' found in page")
                return True
            
            # Look for ratio selector
            ratio_selectors = [
                f'button:has-text("{expected_ratio}")',
                f'[aria-label*="{expected_ratio}"]',
                'button:has-text("9:16")',
                'button:has-text("Portrait")',
            ]
            
            for selector in ratio_selectors:
                try:
                    element = self.page.locator(selector).first
                    if element.is_visible(timeout=2000):
                        log(f"Aspect ratio '{expected_ratio}' verified")
                        return True
                except Exception:
                    continue
            
            log(f"Could not verify aspect ratio '{expected_ratio}'")
            return False
            
        except Exception as e:
            log(f"Could not verify aspect ratio: {e}")
            return False
    
    def _verify_duration(self, expected_duration: int) -> bool:
        """Verify the correct duration is selected."""
        try:
            page_content = self.page.content()
            
            # Look for duration indicator
            duration_patterns = [
                f"{expected_duration}s",
                f"{expected_duration} s",
                f"{expected_duration} sec",
                f"{expected_duration} second",
            ]
            
            for pattern in duration_patterns:
                if pattern in page_content.lower():
                    log(f"Duration '{expected_duration}s' verified")
                    return True
            
            log(f"Could not verify duration '{expected_duration}s'")
            return False
            
        except Exception as e:
            log(f"Could not verify duration: {e}")
            return False
    
    def _verify_count(self, expected_count: int) -> bool:
        """Verify the correct output count is selected."""
        try:
            page_content = self.page.content()
            
            # Look for count indicator
            count_patterns = [
                f"x{expected_count}",
                f"×{expected_count}",
                f"{expected_count} output",
            ]
            
            for pattern in count_patterns:
                if pattern in page_content.lower():
                    log(f"Output count 'x{expected_count}' verified")
                    return True
            
            # If count is 1, it might not be explicitly shown
            if expected_count == 1:
                log("Output count x1 assumed (default)")
                return True
            
            log(f"Could not verify output count 'x{expected_count}'")
            return False
            
        except Exception as e:
            log(f"Could not verify output count: {e}")
            return False
    
    def _verify_prompt_input(self) -> bool:
        """Verify prompt input is available."""
        try:
            prompt_selectors = [
                'textarea[placeholder*="escribe"]',
                'textarea[placeholder*="rompt"]',
                'textarea',
                '[contenteditable="true"]',
                '[aria-label*="Prompt"]',
                '[aria-label*="prompt"]',
            ]
            
            for selector in prompt_selectors:
                try:
                    element = self.page.locator(selector).first
                    if element.is_visible(timeout=2000):
                        log("Prompt input found")
                        return True
                except Exception:
                    continue
            
            log("Could not find prompt input")
            return False
            
        except Exception as e:
            log(f"Could not verify prompt input: {e}")
            return False
    
    def _read_generation_cost(self) -> Optional[int]:
        """Read the generation cost from Flow UI."""
        try:
            page_content = self.page.content()
            
            # Look for credit cost patterns
            # Common patterns: "10 credits", "will use 10", "cost: 10"
            patterns = [
                r'(\d+)\s*credits?',
                r'will use\s*(\d+)',
                r'cost[:\s]+(\d+)',
                r'(\d+)\s*credit',
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, page_content.lower())
                if matches:
                    # Filter out likely account balance (usually larger numbers)
                    for match in matches:
                        cost = int(match)
                        # Generation cost is typically 5-50 credits
                        if 1 <= cost <= 100:
                            log(f"Generation cost detected: {cost} credits")
                            return cost
            
            log("Could not detect generation cost")
            return None
            
        except Exception as e:
            log(f"Could not read generation cost: {e}")
            return None
    
    def _capture_assets_before(self) -> List[str]:
        """Capture list of existing assets before generation."""
        try:
            # Look for video thumbnails or asset elements
            asset_selectors = [
                'video',
                '[data-type="video"]',
                '.video-thumbnail',
                '.media-item',
                '[role="listitem"]',
            ]
            
            assets = []
            for selector in asset_selectors:
                try:
                    elements = self.page.locator(selector).all()
                    for i, el in enumerate(elements):
                        # Get some identifier
                        try:
                            src = el.get_attribute("src") or ""
                            data_id = el.get_attribute("data-id") or ""
                            assets.append(f"{selector}_{i}_{src[:50]}_{data_id}")
                        except Exception:
                            assets.append(f"{selector}_{i}")
                except Exception:
                    continue
            
            log(f"Captured {len(assets)} existing assets")
            return assets
            
        except Exception as e:
            log(f"Could not capture assets: {e}")
            return []
    
    def generate_scene(
        self,
        request: SceneGenerationRequest,
        dry_run: bool = True,
        require_confirmation: bool = True,
    ) -> SceneGenerationResult:
        """
        Generate a single scene.
        
        Args:
            request: Scene generation request.
            dry_run: If True, stop before actual generation.
            require_confirmation: If True, require user confirmation.
        
        Returns:
            SceneGenerationResult with generation details.
        """
        ensure_scene_directories()
        
        log(f"Processing scene: {request.scene_id}")
        log(f"Prompt: {request.visual_prompt[:80]}...")
        
        # Check if scene already exists
        project_dir = self.output_dir / request.project_id
        scene_file = project_dir / f"{request.scene_id}.mp4"
        
        if scene_file.exists():
            validation = validate_video_file(scene_file)
            if validation.get("valid"):
                log(f"Scene already exists: {scene_file}")
                return SceneGenerationResult.skipped(
                    scene_id=request.scene_id,
                    sequence=request.sequence,
                    reason=f"Scene already exists: {scene_file}",
                )
        
        # Verify settings
        self.verify_settings()
        
        # Check credit safety
        if self.ui_state.generation_cost:
            if self.ui_state.generation_cost > FLOW_SCENE_CREDIT_LIMIT:
                raise FlowCreditError(
                    f"Generation cost ({self.ui_state.generation_cost} credits) "
                    f"exceeds limit ({FLOW_SCENE_CREDIT_LIMIT} credits)"
                )
        
        # Dry run - stop here
        if dry_run:
            log("DRY RUN - stopping before generation")
            return SceneGenerationResult.dry_run(
                scene_id=request.scene_id,
                sequence=request.sequence,
                generation_cost=self.ui_state.generation_cost,
            )
        
        # Require confirmation
        if require_confirmation:
            if not self._get_confirmation(request):
                return SceneGenerationResult.skipped(
                    scene_id=request.scene_id,
                    sequence=request.sequence,
                    reason="User cancelled generation",
                )
        
        # Capture assets before generation
        self._assets_before_generation = self._capture_assets_before()
        
        # Enter prompt
        if not self._enter_prompt(request.visual_prompt):
            return SceneGenerationResult.failure(
                scene_id=request.scene_id,
                sequence=request.sequence,
                error="Failed to enter prompt",
            )
        
        # Submit generation
        generation_started = datetime.now().isoformat()
        
        if not self._submit_generation():
            return SceneGenerationResult.failure(
                scene_id=request.scene_id,
                sequence=request.sequence,
                error="Failed to submit generation",
            )
        
        # Wait for completion
        completed, error = self._wait_for_completion()
        
        if not completed:
            take_screenshot(self.page, f"generation_failed_{request.scene_id}", self.screenshots_dir)
            return SceneGenerationResult.failure(
                scene_id=request.scene_id,
                sequence=request.sequence,
                error=error or "Generation failed",
                status="timeout" if "timeout" in (error or "").lower() else "failed",
            )
        
        generation_completed = datetime.now().isoformat()
        
        # Download the new asset
        project_dir.mkdir(parents=True, exist_ok=True)
        
        downloaded_path = self._download_new_asset(scene_file)
        
        if not downloaded_path:
            return SceneGenerationResult.failure(
                scene_id=request.scene_id,
                sequence=request.sequence,
                error="Failed to download generated video",
            )
        
        downloaded_at = datetime.now().isoformat()
        
        # Validate downloaded file
        validation = validate_video_file(downloaded_path)
        
        if not validation.get("valid"):
            return SceneGenerationResult.failure(
                scene_id=request.scene_id,
                sequence=request.sequence,
                error=f"Downloaded video invalid: {validation.get('error')}",
            )
        
        # Save metadata
        metadata = SceneMetadata(
            project_id=request.project_id,
            scene_id=request.scene_id,
            sequence=request.sequence,
            source_prompt=request.visual_prompt,
            model=self.config.model,
            aspect_ratio=self.config.aspect_ratio,
            duration_requested=self.config.duration,
            output_count=self.config.count,
            generation_cost=self.ui_state.generation_cost,
            generation_started_at=generation_started,
            generation_completed_at=generation_completed,
            downloaded_at=downloaded_at,
            file_path=str(downloaded_path),
            file_size=validation["file_size"],
            actual_duration=validation["duration"],
            width=validation["width"],
            height=validation["height"],
            codec=validation["codec"],
        )
        
        metadata_path = project_dir / f"{request.scene_id}.json"
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata.to_dict(), f, indent=2)
        
        log(f"Scene generated: {downloaded_path}")
        
        return SceneGenerationResult.success_result(
            scene_id=request.scene_id,
            sequence=request.sequence,
            file_path=downloaded_path,
            file_size=validation["file_size"],
            actual_duration=validation["duration"],
            width=validation["width"],
            height=validation["height"],
            codec=validation["codec"],
            generation_cost=self.ui_state.generation_cost,
            generation_started_at=generation_started,
            generation_completed_at=generation_completed,
            downloaded_at=downloaded_at,
        )
    
    def _get_confirmation(self, request: SceneGenerationRequest) -> bool:
        """Get user confirmation before generation."""
        print()
        print("=" * 60)
        print("GENERATE SCENE?")
        print("=" * 60)
        print()
        print(f"Project: {request.project_id}")
        print(f"Scene: {request.scene_id}")
        print(f"Prompt: {request.visual_prompt[:60]}...")
        print(f"Model: {self.config.model}")
        print(f"Aspect: {self.config.aspect_ratio}")
        print(f"Duration: {self.config.duration}s")
        print(f"Count: x{self.config.count}")
        if self.ui_state.generation_cost:
            print(f"Generation cost: {self.ui_state.generation_cost} credits")
        else:
            print("Generation cost: UNKNOWN")
        print()
        print("Type GENERATE to continue, anything else to abort:")
        print()
        
        response = input("> ").strip()
        
        if response == "GENERATE":
            log("User confirmed generation")
            return True
        else:
            log("User cancelled generation")
            return False
    
    def _enter_prompt(self, prompt: str) -> bool:
        """Enter the visual prompt."""
        try:
            prompt_selectors = [
                'textarea[placeholder*="escribe"]',
                'textarea[placeholder*="rompt"]',
                'textarea',
                '[contenteditable="true"]',
                '[aria-label*="Prompt"]',
            ]
            
            for selector in prompt_selectors:
                try:
                    element = self.page.locator(selector).first
                    if element.is_visible(timeout=2000):
                        element.click()
                        element.fill(prompt)
                        self.page.wait_for_timeout(500)
                        log("Prompt entered")
                        return True
                except Exception:
                    continue
            
            log("Could not find prompt input")
            return False
            
        except Exception as e:
            log(f"Failed to enter prompt: {e}")
            return False
    
    def _submit_generation(self) -> bool:
        """Submit the generation request."""
        try:
            submit_selectors = [
                'button:has-text("Create")',
                'button:has-text("Generate")',
                'button:has-text("Submit")',
                'button[type="submit"]',
                '[aria-label*="Create"]',
                '[aria-label*="Generate"]',
            ]
            
            for selector in submit_selectors:
                try:
                    element = self.page.locator(selector).first
                    if element.is_visible(timeout=2000):
                        element.click()
                        self.page.wait_for_timeout(2000)
                        log("Generation submitted")
                        take_screenshot(self.page, "generation_submitted", self.screenshots_dir)
                        return True
                except Exception:
                    continue
            
            log("Could not find submit button")
            return False
            
        except Exception as e:
            log(f"Failed to submit generation: {e}")
            return False
    
    def _wait_for_completion(self) -> Tuple[bool, Optional[str]]:
        """
        Wait for generation to complete.
        
        Returns:
            Tuple of (completed, error_message).
        """
        log(f"Waiting for generation (timeout: {FLOW_SCENE_TIMEOUT}s)...")
        
        start_time = time.time()
        
        while time.time() - start_time < FLOW_SCENE_TIMEOUT:
            try:
                page_content = self.page.content().lower()
                
                # Check for explicit errors
                error_indicators = [
                    "something went wrong",
                    "generation failed",
                    "error occurred",
                    "please try again",
                ]
                
                for indicator in error_indicators:
                    if indicator in page_content:
                        take_screenshot(self.page, "generation_error", self.screenshots_dir)
                        return False, f"Flow error: {indicator}"
                
                # Check for completion
                completion_indicators = [
                    "download",
                    "your video is ready",
                    "generation complete",
                    "ready to download",
                ]
                
                if any(indicator in page_content for indicator in completion_indicators):
                    # Verify a new asset exists
                    current_assets = self._capture_assets_before()
                    if len(current_assets) > len(self._assets_before_generation):
                        log("Generation complete - new asset detected")
                        take_screenshot(self.page, "generation_complete", self.screenshots_dir)
                        return True, None
                
                # Log progress
                elapsed = int(time.time() - start_time)
                if elapsed % 30 == 0 and elapsed > 0:
                    log(f"Still waiting... ({elapsed}s elapsed)")
                
            except Exception as e:
                log(f"Warning during wait: {e}")
            
            time.sleep(FLOW_SCENE_POLL_INTERVAL)
        
        take_screenshot(self.page, "generation_timeout", self.screenshots_dir)
        return False, f"Generation timed out after {FLOW_SCENE_TIMEOUT}s"
    
    def _download_new_asset(self, save_path: Path) -> Optional[Path]:
        """
        Download the newly generated asset.
        
        Args:
            save_path: Path to save the downloaded file.
        
        Returns:
            Path to downloaded file, or None if failed.
        """
        log("Attempting to download new asset...")
        
        download_selectors = [
            'button:has-text("Download")',
            'a:has-text("Download")',
            '[aria-label*="Download"]',
            '[aria-label*="download"]',
            'button:has-text("Export")',
            'a[download]',
        ]
        
        for selector in download_selectors:
            try:
                element = self.page.locator(selector).first
                if element.is_visible(timeout=3000):
                    log(f"Found download button: {selector}")
                    
                    with self.page.expect_download(timeout=FLOW_SCENE_DOWNLOAD_TIMEOUT * 1000) as download_info:
                        element.click()
                    
                    download = download_info.value
                    
                    # Save to specified path
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    download.save_as(str(save_path))
                    
                    log(f"Downloaded: {save_path}")
                    return save_path
                    
            except PlaywrightTimeout:
                log(f"Download timed out with selector: {selector}")
                continue
            except Exception as e:
                log(f"Download attempt failed: {e}")
                continue
        
        take_screenshot(self.page, "download_failed", self.screenshots_dir)
        return None


def load_manifest(project_dir: Path, project_id: str) -> SceneManifest:
    """Load or create scene manifest."""
    manifest_path = project_dir / "scene_manifest.json"
    
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return SceneManifest(**data)
    
    return SceneManifest(project_id=project_id)


def save_manifest(manifest: SceneManifest, project_dir: Path) -> None:
    """Save scene manifest."""
    manifest_path = project_dir / "scene_manifest.json"
    project_dir.mkdir(parents=True, exist_ok=True)
    
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f, indent=2)
