"""
Google Flow Automation - Core Automation Logic

Handles the end-to-end workflow of:
1. Opening Google Flow
2. Checking/waiting for authentication
3. Creating/opening a project
4. Configuring video generation
5. Submitting a prompt
6. Waiting for generation
7. Downloading the result
"""

import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, BrowserContext, TimeoutError as PlaywrightTimeout

from app.flow.config import (
    FLOW_URL,
    DEFAULT_TEST_PROMPT,
    DESIRED_ASPECT_RATIO,
    AUTH_TIMEOUT,
    NAVIGATION_TIMEOUT,
    GENERATION_TIMEOUT,
    DOWNLOAD_TIMEOUT,
    POLL_INTERVAL,
    FLOW_TEST_OUTPUT_DIR,
    FLOW_SCREENSHOTS_DIR,
)
from app.flow.browser import log, take_screenshot, get_page


class FlowAutomationError(Exception):
    """Raised when Flow automation encounters an unrecoverable error."""
    pass


class FlowAutomation:
    """
    Automates a single video generation workflow on Google Flow.

    This class operates through the user's normal authenticated browser session.
    It does NOT bypass authentication, CAPTCHAs, or any anti-abuse systems.
    """

    def __init__(self, context: BrowserContext, prompt: str | None = None):
        """
        Initialize the Flow automation.

        Args:
            context: Persistent Playwright browser context with user session.
            prompt: The video generation prompt. Defaults to test prompt.
        """
        self.context = context
        self.page: Page = get_page(context)
        self.prompt = prompt or DEFAULT_TEST_PROMPT
        self.output_dir = FLOW_TEST_OUTPUT_DIR
        self.screenshots_dir = FLOW_SCREENSHOTS_DIR

        # Ensure output directories exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> Path | None:
        """
        Execute the full Flow automation workflow.

        Returns:
            Path to the downloaded video file, or None if failed.
        """
        try:
            self._open_flow()
            self._check_authentication()
            self._navigate_to_video_creation()
            self._configure_generation()
            self._enter_prompt()
            self._submit_generation()
            video_path = self._wait_and_download()
            return video_path
        except FlowAutomationError as e:
            log(f"AUTOMATION ERROR: {e}")
            take_screenshot(self.page, "error", self.screenshots_dir)
            return None
        except PlaywrightTimeout as e:
            log(f"TIMEOUT ERROR: {e}")
            take_screenshot(self.page, "timeout", self.screenshots_dir)
            return None
        except Exception as e:
            log(f"UNEXPECTED ERROR: {e}")
            take_screenshot(self.page, "unexpected_error", self.screenshots_dir)
            return None

    def _open_flow(self) -> None:
        """Navigate to Google Flow."""
        log("Opening Google Flow...")
        try:
            self.page.goto(FLOW_URL, wait_until="domcontentloaded",
                          timeout=NAVIGATION_TIMEOUT * 1000)
            log(f"Navigated to: {self.page.url}")
        except PlaywrightTimeout:
            take_screenshot(self.page, "open_failed", self.screenshots_dir)
            raise FlowAutomationError(
                f"Failed to open Google Flow at {FLOW_URL}. "
                "Check your internet connection."
            )

    def _check_authentication(self) -> None:
        """
        Check if the user is authenticated on Google Flow.

        If not authenticated, pause and instruct the user to log in manually.
        Waits up to AUTH_TIMEOUT seconds for authentication to complete.
        """
        log("Checking authentication status...")

        # Give the page a moment to settle after navigation
        self.page.wait_for_timeout(3000)

        # Check if we're on a Google sign-in page
        current_url = self.page.url.lower()

        if self._is_login_page(current_url):
            log("=" * 50)
            log("AUTHENTICATION REQUIRED")
            log("Please log in to your Google account in the browser window.")
            log(f"Waiting up to {AUTH_TIMEOUT} seconds...")
            log("=" * 50)

            # Wait for the user to complete authentication
            # We detect success when the URL is no longer a login page
            start_time = time.time()
            while time.time() - start_time < AUTH_TIMEOUT:
                time.sleep(POLL_INTERVAL)
                current_url = self.page.url.lower()
                if not self._is_login_page(current_url):
                    log("Authentication completed!")
                    # Give Flow a moment to fully load after auth
                    self.page.wait_for_timeout(3000)
                    return

            take_screenshot(self.page, "auth_timeout", self.screenshots_dir)
            raise FlowAutomationError(
                "Authentication timed out. Please try again and complete "
                "login within the time limit."
            )
        else:
            log("Already authenticated (or no login required).")

    def _is_login_page(self, url: str) -> bool:
        """Check if the current URL indicates a Google login page."""
        login_indicators = [
            "accounts.google.com/signin",
            "accounts.google.com/v3/signin",
            "accounts.google.com/servicelogin",
            "accounts.google.com/interactivelogin",
        ]
        return any(indicator in url for indicator in login_indicators)

    def _navigate_to_video_creation(self) -> None:
        """
        Navigate to the video creation interface within Flow.

        This method inspects the current UI to find the video generation area.
        It adapts to the current Flow interface rather than assuming fixed selectors.
        """
        log("Navigating to video creation interface...")

        # Wait for the page to be fully loaded
        self.page.wait_for_timeout(3000)

        # Take a screenshot to document the current state of Flow UI
        take_screenshot(self.page, "flow_home", self.screenshots_dir)

        # Look for common patterns in the Flow UI
        # The UI may have changed, so we try multiple approaches

        # Strategy 1: Look for a "Create" or "New" button/link
        create_selectors = [
            'button:has-text("Create")',
            'a:has-text("Create")',
            'button:has-text("New")',
            'button:has-text("New video")',
            'a:has-text("New video")',
            '[aria-label*="Create"]',
            '[aria-label*="New"]',
        ]

        for selector in create_selectors:
            try:
                element = self.page.locator(selector).first
                if element.is_visible(timeout=2000):
                    log(f"Found creation button: {selector}")
                    element.click()
                    self.page.wait_for_timeout(2000)
                    return
            except (PlaywrightTimeout, Exception):
                continue

        # Strategy 2: Check if we're already on a creation/project page
        page_content = self.page.content().lower()
        if any(word in page_content for word in ["prompt", "generate", "describe"]):
            log("Already appears to be on a creation page.")
            return

        # Strategy 3: Look for video-specific options
        video_selectors = [
            'button:has-text("Video")',
            'a:has-text("Video")',
            '[data-type="video"]',
            'div:has-text("Video"):not(:has(div))',
        ]

        for selector in video_selectors:
            try:
                element = self.page.locator(selector).first
                if element.is_visible(timeout=2000):
                    log(f"Found video option: {selector}")
                    element.click()
                    self.page.wait_for_timeout(2000)
                    return
            except (PlaywrightTimeout, Exception):
                continue

        # If nothing found, log the situation and take a screenshot
        take_screenshot(self.page, "nav_unclear", self.screenshots_dir)
        log("WARNING: Could not find obvious video creation button.")
        log("The Flow UI may have changed. Proceeding to check for prompt input...")

    def _configure_generation(self) -> None:
        """
        Configure video generation settings (aspect ratio, model, etc.).

        Attempts to set vertical 9:16 format and lowest/fastest generation option.
        """
        log("Configuring generation settings...")

        # Look for aspect ratio selector
        ratio_selectors = [
            f'button:has-text("{DESIRED_ASPECT_RATIO}")',
            f'[aria-label*="{DESIRED_ASPECT_RATIO}"]',
            'button:has-text("9:16")',
            'button:has-text("Portrait")',
            'button:has-text("Vertical")',
            '[data-ratio="9:16"]',
        ]

        for selector in ratio_selectors:
            try:
                element = self.page.locator(selector).first
                if element.is_visible(timeout=2000):
                    log(f"Setting aspect ratio with: {selector}")
                    element.click()
                    self.page.wait_for_timeout(1000)
                    break
            except (PlaywrightTimeout, Exception):
                continue
        else:
            log("Could not find aspect ratio selector. Using default.")

        # Look for model/quality selector (prefer cheapest option)
        # Try to find "Flash" or similar quick/cheap option
        model_selectors = [
            'button:has-text("Flash")',
            'button:has-text("Omni Flash")',
            'button:has-text("Fast")',
            'button:has-text("Quick")',
        ]

        for selector in model_selectors:
            try:
                element = self.page.locator(selector).first
                if element.is_visible(timeout=2000):
                    log(f"Selected model/quality: {selector}")
                    element.click()
                    self.page.wait_for_timeout(1000)
                    break
            except (PlaywrightTimeout, Exception):
                continue
        else:
            log("Could not find model selector. Using default.")

        take_screenshot(self.page, "configured", self.screenshots_dir)

    def _enter_prompt(self) -> None:
        """
        Enter the video generation prompt into the text input.
        """
        log("Entering prompt...")
        log(f"Prompt: {self.prompt[:80]}...")

        # Look for the prompt input field
        prompt_selectors = [
            'textarea[placeholder*="escribe"]',
            'textarea[placeholder*="rompt"]',
            'textarea[placeholder*="nter"]',
            'textarea',
            '[contenteditable="true"]',
            'input[type="text"][placeholder*="escribe"]',
            'input[type="text"][placeholder*="rompt"]',
            '[aria-label*="Prompt"]',
            '[aria-label*="prompt"]',
            '[aria-label*="Describe"]',
        ]

        for selector in prompt_selectors:
            try:
                element = self.page.locator(selector).first
                if element.is_visible(timeout=2000):
                    log(f"Found prompt input: {selector}")
                    element.click()
                    element.fill(self.prompt)
                    self.page.wait_for_timeout(500)
                    # Verify text was entered
                    log("Prompt entered successfully.")
                    return
            except (PlaywrightTimeout, Exception):
                continue

        take_screenshot(self.page, "no_prompt_input", self.screenshots_dir)
        raise FlowAutomationError(
            "Could not find the prompt input field. "
            "The Flow UI may have changed significantly."
        )

    def _submit_generation(self) -> None:
        """
        Submit the video generation request.
        """
        log("Submitting generation...")

        submit_selectors = [
            'button:has-text("Generate")',
            'button:has-text("Create")',
            'button:has-text("Submit")',
            'button[type="submit"]',
            '[aria-label*="Generate"]',
            '[aria-label*="Create"]',
            '[aria-label*="Submit"]',
            'button:has-text("Go")',
        ]

        for selector in submit_selectors:
            try:
                element = self.page.locator(selector).first
                if element.is_visible(timeout=2000):
                    log(f"Found submit button: {selector}")
                    element.click()
                    self.page.wait_for_timeout(2000)
                    log("Generation submitted!")
                    take_screenshot(self.page, "submitted", self.screenshots_dir)
                    return
            except (PlaywrightTimeout, Exception):
                continue

        take_screenshot(self.page, "no_submit_button", self.screenshots_dir)
        raise FlowAutomationError(
            "Could not find the generate/submit button. "
            "The Flow UI may have changed."
        )

    def _wait_and_download(self) -> Path | None:
        """
        Wait for video generation to complete and download the result.

        Returns:
            Path to the downloaded video file, or None if failed.
        """
        log("Waiting for video generation to complete...")
        log(f"Maximum wait time: {GENERATION_TIMEOUT} seconds")

        start_time = time.time()
        generation_complete = False

        while time.time() - start_time < GENERATION_TIMEOUT:
            # Check for completion indicators
            try:
                page_content = self.page.content().lower()

                # Look for signs that generation is complete
                completion_indicators = [
                    "download",
                    "your video is ready",
                    "generation complete",
                    "ready to download",
                    "export",
                ]

                if any(indicator in page_content for indicator in completion_indicators):
                    log("Generation appears to be complete!")
                    generation_complete = True
                    break

                # Check for error indicators
                error_indicators = [
                    "generation failed",
                    "error occurred",
                    "try again",
                    "something went wrong",
                ]

                if any(indicator in page_content for indicator in error_indicators):
                    take_screenshot(self.page, "generation_error", self.screenshots_dir)
                    raise FlowAutomationError(
                        "Video generation failed according to the UI."
                    )

                # Check for progress indicators to log status
                elapsed = int(time.time() - start_time)
                if elapsed % 30 == 0 and elapsed > 0:
                    log(f"Still waiting... ({elapsed}s elapsed)")

            except FlowAutomationError:
                raise
            except Exception as e:
                log(f"Warning during wait: {e}")

            time.sleep(POLL_INTERVAL)

        if not generation_complete:
            take_screenshot(self.page, "generation_timeout", self.screenshots_dir)
            raise FlowAutomationError(
                f"Video generation timed out after {GENERATION_TIMEOUT} seconds."
            )

        # Now try to download
        self.page.wait_for_timeout(2000)
        take_screenshot(self.page, "generation_complete", self.screenshots_dir)
        return self._download_video()

    def _download_video(self) -> Path | None:
        """
        Download the generated video through the normal Flow UI.

        Returns:
            Path to the downloaded file, or None if failed.
        """
        log("Attempting to download video...")

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

                    # Start waiting for download before clicking
                    with self.page.expect_download(
                        timeout=DOWNLOAD_TIMEOUT * 1000
                    ) as download_info:
                        element.click()

                    download = download_info.value

                    # Generate timestamped filename
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    # Use the original filename extension, or default to .mp4
                    original_name = download.suggested_filename or "video.mp4"
                    extension = Path(original_name).suffix or ".mp4"
                    filename = f"flow_test_{timestamp}{extension}"

                    # Save to output directory
                    save_path = self.output_dir / filename
                    download.save_as(str(save_path))

                    log(f"Video saved: {save_path}")
                    return save_path

            except PlaywrightTimeout:
                log(f"Download timed out with selector: {selector}")
                continue
            except Exception as e:
                log(f"Download attempt failed with {selector}: {e}")
                continue

        take_screenshot(self.page, "download_failed", self.screenshots_dir)
        raise FlowAutomationError(
            "Could not find or activate the download button. "
            "The Flow UI may have changed."
        )
