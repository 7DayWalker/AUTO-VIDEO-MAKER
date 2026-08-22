"""
AUTO Video Maker - Flow Scene Generator

Automated scene generation through Google Flow.
Reuses Phase 1 browser/CDP infrastructure.
"""

import json
import time
import re
import hashlib
import base64
import subprocess
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, List, Tuple
from urllib.parse import quote, urljoin

from playwright.sync_api import sync_playwright, Page, Browser, TimeoutError as PlaywrightTimeout

from app.content.models import ProductionPlan
from app.flow.browser import (
    connect_to_chrome,
    get_page_from_browser,
    enumerate_browser_pages,
    is_chrome_running_with_debugging,
    cdp_endpoint_diagnostics,
    launch_chrome_with_debugging,
    restart_dedicated_chrome,
    ChromeLifecycleError,
    get_last_lifecycle_diagnostics,
    take_screenshot,
    log,
)
from app.flow.config import (
    FLOW_URL,
    CDP_PORT,
    NAVIGATION_TIMEOUT,
    AUTH_TIMEOUT,
    POLL_INTERVAL,
    HEALTH_TIMEOUT,
    HEALTH_POLL_INTERVAL,
    MAX_REFRESH_ATTEMPTS,
    MAX_RESTART_ATTEMPTS,
    SCREENSHOT_TIMEOUT,
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
    FLOW_SCENE_HEARTBEAT_INTERVAL,
    FLOW_SCENE_MAX_POLL_RECOVERY_ATTEMPTS,
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
    FlowHealthResult,
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


def validate_video_file(file_path: Path, expected_aspect_ratio: str = None) -> dict:
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

        if duration <= 0 or not width or not height:
            return {
                "valid": False,
                "error": "Video stream has no positive duration or dimensions",
                "file_size": file_size,
            }

        if expected_aspect_ratio == "9:16" and (width * 16 != height * 9):
            return {
                "valid": False,
                "error": f"Expected 9:16 video, got {width}:{height}",
                "file_size": file_size,
                "duration": duration,
                "width": width,
                "height": height,
                "codec": codec,
            }
        
        return {
            "valid": True,
            "duration": duration,
            "width": width,
            "height": height,
            "codec": codec,
            "file_size": file_size,
            "has_video": True,
            "has_audio": any(
                stream.get("codec_type") == "audio"
                for stream in data.get("streams", [])
            ),
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
        self._baseline_snapshot: Dict[str, Any] = {}
        self._submission_id: Optional[str] = None
        self._submission_context: Dict[str, Any] = {}
        self._correlated_asset: Optional[Dict[str, Any]] = None
        # A persisted exact media record may safely corroborate a read-only
        # resume when Flow's projectInitialData endpoint is temporarily
        # unavailable. It is never populated from arbitrary DOM position.
        self._resume_asset_hint: Optional[Dict[str, Any]] = None
        self._last_prompt_diagnostics: dict = {}
        self._last_prompt_error: Optional[str] = None
        self._last_prompt_error_code: Optional[str] = None
        self._expected_project_url: Optional[str] = None
        self._last_health: Optional[FlowHealthResult] = None
        self._health_history: List[dict] = []
        self._refresh_attempts = 0
        self._restart_attempts = 0
        self._last_submission_evidence: Dict[str, Any] = {}
        self._last_poll_evidence: Dict[str, Any] = {}
        self._last_poll_state: Optional[str] = None
        self._last_download_evidence: Dict[str, Any] = {}
        self._download_path: Optional[Path] = None
    
    def connect(self, expected_url: str = None) -> bool:
        """
        Connect to Chrome via CDP.
        
        Returns:
            True if connected successfully.
        
        Raises:
            FlowConnectionError: If connection fails.
        """
        if not is_chrome_running_with_debugging():
            log("Dedicated Chrome/CDP unavailable; launching the configured dedicated profile")
            try:
                launch_chrome_with_debugging()
            except Exception as exc:
                raise FlowConnectionError(
                    f"Chrome is not running with debugging on port {CDP_PORT} and launch failed: {exc}"
                ) from exc
        
        try:
            self.playwright = sync_playwright().start()
            self.browser = connect_to_chrome(self.playwright)
            self.page = get_page_from_browser(self.browser, expected_url=expected_url)
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

        self._expected_project_url = project_url
        self._health_history = []
        self._refresh_attempts = 0
        self._restart_attempts = 0

        # Startup inspection is deliberately performed before navigation. It
        # records whether the attached browser/tab is already healthy, stale,
        # or crashed instead of misreporting every missing prompt as a selector
        # defect. If another Flow tab is already on the exact project, prefer it
        # over the currently attached tab.
        if self.browser:
            self.page = get_page_from_browser(self.browser, expected_url=project_url)
        initial_health = self.inspect_flow_health(project_url)
        self._health_history.append(initial_health.model_dump())
        log(f"Initial Flow health: {initial_health.classification}")
        
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
            
            health = self.ensure_flow_healthy(project_url)
            if not health.healthy:
                raise FlowSceneError(
                    f"Flow page did not recover to HEALTHY: {health.classification}"
                )
            take_screenshot(self.page, "project_loaded", self.screenshots_dir, SCREENSHOT_TIMEOUT)
            log(f"Project page healthy: {health.classification}")
            return True
            
        except PlaywrightTimeout:
            take_screenshot(self.page, "navigation_timeout", self.screenshots_dir, SCREENSHOT_TIMEOUT)
            raise FlowSceneError(f"Navigation timed out: {project_url}")

    def _safe_page_text(self) -> str:
        """Read page text/HTML without allowing a crashed page to hang the run."""
        if not self.page:
            return ""
        try:
            return self.page.content().lower()
        except Exception:
            return ""

    def _inspect_prompt_candidates(self) -> List[dict]:
        """Inspect prompt candidates without converting absence into recovery."""
        candidates = []
        if not self.page:
            return candidates
        for selector in self._prompt_selectors():
            try:
                locator = self.page.locator(selector)
                count = locator.count()
                for index in range(count):
                    item = {"selector": selector, "index": index, "count": count}
                    candidate = locator.nth(index)
                    try:
                        item["attached"] = True
                        item["visible"] = candidate.is_visible(timeout=1000)
                        item["editable"] = candidate.is_editable(timeout=1000)
                    except Exception as exc:
                        item["attached"] = False
                        item["error"] = str(exc)
                    candidates.append(item)
            except Exception as exc:
                candidates.append({"selector": selector, "error": str(exc)})
        return candidates

    def _inspect_create_button(self) -> dict:
        """Inspect Create without clicking it."""
        if not self.page:
            return {"found": False, "reason": "page unavailable"}
        for selector in ['button:has-text("Create")', '[aria-label*="Create"]']:
            try:
                candidate = self.page.locator(selector).first
                if candidate.is_visible(timeout=1000):
                    return {"found": True, "selector": selector, "visible": True}
            except Exception as exc:
                continue
        return {"found": False}

    def _inspect_control_state(self, selector: str, kind: str) -> dict:
        """Inspect a generation control without clicking it."""
        state = {
            "selector": selector,
            "kind": kind,
            "found": False,
            "attached": False,
            "visible": False,
            "editable": kind == "create",
            "enabled": False,
            "stable": False,
            "in_viewport": False,
            "pointer_interactable": False,
            "unobstructed": False,
        }
        if not self.page:
            return state
        try:
            locator = self.page.locator(selector).first
            if self.page.locator(selector).count() == 0:
                return state
            state["found"] = True
            state["attached"] = True
            state["visible"] = locator.is_visible(timeout=1000)
            if kind == "prompt":
                state["editable"] = locator.is_editable(timeout=1000)
            else:
                is_enabled = getattr(locator, "is_enabled", None)
                state["enabled"] = is_enabled(timeout=1000) if is_enabled else state["visible"]
            state["stable"] = bool(state["visible"] and (state["editable"] if kind == "prompt" else state["enabled"]))
            try:
                state.update(locator.evaluate(
                    """
                    el => {
                      const r = el.getBoundingClientRect();
                      const s = getComputedStyle(el);
                      const inViewport = r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;
                      const x = r.left + r.width / 2;
                      const y = r.top + r.height / 2;
                      const hit = inViewport && x >= 0 && y >= 0 && x < innerWidth && y < innerHeight
                        ? document.elementFromPoint(x, y) : null;
                      return {
                        rect: {x:r.x, y:r.y, width:r.width, height:r.height, top:r.top, right:r.right, bottom:r.bottom, left:r.left},
                        computed: {display:s.display, visibility:s.visibility, opacity:s.opacity, pointerEvents:s.pointerEvents, position:s.position, zIndex:s.zIndex},
                        in_viewport: inViewport,
                        pointer_interactable: s.pointerEvents !== 'none' && inViewport,
                        unobstructed: !!(hit && (hit === el || el.contains(hit))),
                        hit_tag: hit ? hit.tagName.toLowerCase() : null,
                      };
                    }
                    """
                ))
            except Exception:
                # The basic Playwright checks above remain useful in mocked or
                # partially hydrated pages where evaluate is unavailable.
                state["in_viewport"] = state["visible"]
                state["pointer_interactable"] = state["visible"]
                state["unobstructed"] = state["visible"]
            return state
        except Exception as exc:
            state["error"] = str(exc)
            return state

    def _inspect_error_elements(self, target_states: List[dict]) -> List[dict]:
        """Find contextual error evidence, excluding global text as a signal."""
        if not self.page:
            return []
        targets = [state.get("rect") for state in target_states if state.get("rect")]
        try:
            return self.page.evaluate(
                """
                ({terms, targets}) => {
                  const lower = value => String(value || '').toLowerCase();
                  const label = el => ({
                    tag: el.tagName.toLowerCase(), id: el.id || null,
                    classes: typeof el.className === 'string' ? el.className.slice(0, 240) : null,
                    role: el.getAttribute('role'), ariaLabel: el.getAttribute('aria-label'),
                    testId: el.getAttribute('data-testid') || el.getAttribute('data-test-id'),
                  });
                  const path = el => {
                    const parts = []; let cur = el; let depth = 0;
                    while (cur && cur.nodeType === 1 && depth++ < 10) {
                      let part = cur.tagName.toLowerCase();
                      if (cur.id) part += '#' + CSS.escape(cur.id);
                      parts.unshift(part); cur = cur.parentElement;
                    }
                    return parts.join(' > ');
                  };
                  const intersects = (a, b) => a && b && a.right > b.left && a.left < b.right && a.bottom > b.top && a.top < b.bottom;
                  const result = [];
                  for (const el of Array.from(document.querySelectorAll('*'))) {
                    const text = String(el.textContent || '').trim();
                    const term = terms.find(item => lower(text).includes(item));
                    if (!term) continue;
                    const descendants = Array.from(el.children).some(child => lower(child.textContent).includes(term));
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    const visible = r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity || '1') > 0;
                    const viewport = {left:0, top:0, right:innerWidth, bottom:innerHeight};
                    const inViewport = intersects(r, viewport);
                    const hiddenSerialized = ['script','style','template'].includes(el.tagName.toLowerCase()) || !visible;
                    const aggregate = ['html','body'].includes(el.tagName.toLowerCase()) || descendants;
                    const hitTargets = targets.filter(target => intersects(r, target)).length;
                    let modal = null; let cur = el;
                    while (cur && cur.nodeType === 1) {
                      const role = lower(cur.getAttribute('role'));
                      if (['dialog','alertdialog'].includes(role) || lower(cur.getAttribute('aria-modal')) === 'true' || cur.tagName.toLowerCase() === 'dialog') { modal = label(cur); break; }
                      cur = cur.parentElement;
                    }
                    result.push({
                      term, tag: el.tagName.toLowerCase(), selector_path: path(el),
                      identifying: label(el), text: text.slice(0, 500),
                      visible, in_viewport: inViewport, aggregate, hidden_serialized: hiddenSerialized,
                      rect: {x:r.x,y:r.y,width:r.width,height:r.height,top:r.top,right:r.right,bottom:r.bottom,left:r.left},
                      computed: {display:s.display, visibility:s.visibility, opacity:s.opacity, pointerEvents:s.pointerEvents, position:s.position, zIndex:s.zIndex},
                      modal_ancestor: modal, intersects_controls: hitTargets > 0,
                      control_intersections: hitTargets,
                      context_classification: hiddenSerialized ? 'BACKGROUND_ERROR' :
                        (hitTargets && !['html', 'body'].includes(el.tagName.toLowerCase()) && s.pointerEvents !== 'none')
                          ? 'BLOCKING_APPLICATION_ERROR'
                          : aggregate ? 'STALE_ERROR_ELEMENT' : 'NON_BLOCKING_ERROR_MESSAGE',
                    });
                  }
                  return result;
                }
                """,
                {"terms": ["application error: a client-side exception has occurred", "client-side exception", "something went wrong", "this site can't be reached", "err_connection", "network error", "404 not found"], "targets": targets},
            )
        except Exception as exc:
            log(f"Could not inspect contextual Flow errors: {exc}")
            return []

    @staticmethod
    def _classify_health_evidence(
        *, expected_url: Optional[str], actual_url: Optional[str], cdp_available: bool,
        browser_connected: bool, loading: List[str], prompt_state: dict,
        create_state: dict, error_elements: List[dict], page_available: bool,
    ) -> dict:
        """Classify health from explicit page/control evidence."""
        detected = sorted({item.get("term") for item in error_elements if item.get("term")})
        blocking = [item for item in error_elements if item.get("context_classification") == "BLOCKING_APPLICATION_ERROR" or item.get("modal_ancestor")]
        non_blocking = [item for item in error_elements if item not in blocking]
        prompt_ready = all(prompt_state.get(key) for key in ("found", "attached", "visible", "editable", "stable", "in_viewport", "pointer_interactable", "unobstructed"))
        create_ready = all(create_state.get(key) for key in ("found", "attached", "visible", "enabled", "stable", "in_viewport", "pointer_interactable", "unobstructed"))
        surface_ready = prompt_ready and create_ready
        if not cdp_available or not browser_connected or not page_available:
            classification = "NETWORK_ERROR"
        elif expected_url and actual_url and expected_url.rstrip('/').lower() != actual_url.rstrip('/').lower():
            classification = "STALE"
        elif not surface_ready and loading:
            classification = "LOADING"
        elif blocking:
            classification = "BLOCKING_APPLICATION_ERROR"
        elif not surface_ready:
            classification = "GENERATION_SURFACE_UNAVAILABLE"
        else:
            classification = "GENERATION_SURFACE_READY"
        if blocking:
            error_classification = "BLOCKING_APPLICATION_ERROR"
        elif non_blocking:
            error_classification = non_blocking[0].get("context_classification") or "NON_BLOCKING_ERROR_MESSAGE"
        else:
            error_classification = None
        return {
            "classification": classification,
            "error_classification": error_classification,
            "detected_error_text": detected,
            "blocking_error_elements": blocking,
            "non_blocking_error_elements": non_blocking,
            "generation_surface_state": {"ready": surface_ready, "prompt_ready": prompt_ready, "create_ready": create_ready},
            "overlay_state": {"blocking": bool(blocking), "intersects_generation_controls": any(item.get("intersects_controls") for item in blocking)},
        }

    def inspect_flow_health(self, expected_url: str = None) -> FlowHealthResult:
        """Inspect browser, tab, URL, Flow error/loading state, and controls."""
        timestamp = datetime.now().isoformat()
        expected_url = expected_url or self._expected_project_url
        cdp = cdp_endpoint_diagnostics() if self.playwright or self.browser else {
            "available": False,
            "error": "not connected",
        }
        pages = []
        if self.browser:
            try:
                for item in enumerate_browser_pages(self.browser):
                    pages.append({k: v for k, v in item.items() if k != "page"})
            except Exception as exc:
                pages.append({"error": str(exc)})

        actual_url = None
        title = None
        if self.page:
            try:
                actual_url = self.page.url
            except Exception:
                pass
            try:
                title = self.page.title()
            except Exception:
                pass
        text = self._safe_page_text()
        loading_patterns = ["loading", "please wait", "initializing", "skeleton"]
        loading = [pattern for pattern in loading_patterns if pattern in text]
        prompt_candidates = self._inspect_prompt_candidates()
        prompt_state = self._inspect_control_state(self._prompt_selectors()[0], "prompt")
        create_state = self._inspect_control_state('button:has-text("Create")', "create")
        create_button = dict(create_state)
        create_button["found"] = create_state.get("found", False)
        error_elements = self._inspect_error_elements([prompt_state, create_state])
        url_matches = bool(expected_url and actual_url and actual_url.rstrip("/").lower() == expected_url.rstrip("/").lower())
        evidence = {
            "classification": "NETWORK_ERROR",
            "error_classification": None,
            "detected_error_text": [],
            "blocking_error_elements": [],
            "non_blocking_error_elements": [],
            "generation_surface_state": {},
            "overlay_state": {},
        }
        if self.page is None or not actual_url:
            classification = "NETWORK_ERROR"
        else:
            evidence = self._classify_health_evidence(
                expected_url=expected_url, actual_url=actual_url,
                cdp_available=bool(cdp.get("available", False)),
                browser_connected=bool(self.browser and self.page), loading=loading,
                prompt_state=prompt_state, create_state=create_state,
                error_elements=error_elements, page_available=True,
            )
            classification = evidence["classification"]
        result = FlowHealthResult(
            timestamp=timestamp,
            expected_url=expected_url,
            actual_url=actual_url,
            title=title,
            browser_connected=bool(self.browser and self.page),
            cdp_available=bool(cdp.get("available", False)),
            candidate_pages=pages,
            prompt_candidates=prompt_candidates,
            create_button=create_button,
            detected_error_text=evidence.get("detected_error_text", []) if self.page else [],
            blocking_error_elements=evidence.get("blocking_error_elements", []) if self.page else [],
            non_blocking_error_elements=evidence.get("non_blocking_error_elements", []) if self.page else [],
            generation_surface_state=evidence.get("generation_surface_state", {}) if self.page else {},
            prompt_state=prompt_state,
            create_state=create_state,
            overlay_state=evidence.get("overlay_state", {}) if self.page else {},
            error_classification=evidence.get("error_classification") if self.page else None,
            loading_indicators=loading,
            classification=classification,
            recovery_history=list(self._health_history),
        )
        self._last_health = result
        return result

    def _persist_health_diagnostic(self, health: FlowHealthResult, label: str) -> None:
        """Persist structured health data and bounded screenshot evidence."""
        try:
            self.screenshots_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = self.screenshots_dir / f"flow_health_{label}_{timestamp}.json"
            path.write_text(json.dumps(health.model_dump(), indent=2, default=str), encoding="utf-8")
            health.diagnostic_path = str(path)
            if self.page:
                image = take_screenshot(self.page, f"flow_health_{label}", self.screenshots_dir, SCREENSHOT_TIMEOUT)
                health.screenshot_path = str(image) if image else None
                if not image:
                    health.screenshot_error = "screenshot failed or timed out"
            log(f"Flow health diagnostic saved: {path}")
        except Exception as exc:
            log(f"Could not save Flow health diagnostic: {exc}")

    def _poll_for_healthy(self, expected_url: str) -> FlowHealthResult:
        """Allow a loading Flow application to hydrate before recovery."""
        deadline = time.time() + HEALTH_TIMEOUT
        health = self.inspect_flow_health(expected_url)
        while health.classification == "LOADING" and time.time() < deadline:
            time.sleep(HEALTH_POLL_INTERVAL)
            health = self.inspect_flow_health(expected_url)
        return health

    def _refresh_page(self, expected_url: str) -> FlowHealthResult:
        self._refresh_attempts += 1
        action = {"action": "refresh", "attempt": self._refresh_attempts}
        log(f"Flow health recovery: page refresh {self._refresh_attempts}/{MAX_REFRESH_ATTEMPTS}")
        try:
            self.page.reload(wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT * 1000)
            self.page.wait_for_timeout(1000)
            health = self.inspect_flow_health(expected_url)
            action["result"] = health.classification
        except Exception as exc:
            action["error"] = str(exc)
            health = FlowHealthResult(
                timestamp=datetime.now().isoformat(), expected_url=expected_url,
                actual_url=getattr(self.page, "url", None), classification="NETWORK_ERROR",
            )
        self._health_history.append(action)
        health.recovery_history = list(self._health_history)
        self._last_health = health
        self._persist_health_diagnostic(health, f"refresh_{self._refresh_attempts}")
        return self._poll_for_healthy(expected_url) if health.classification == "LOADING" else health

    def _restart_and_reconnect(self, expected_url: str) -> FlowHealthResult:
        self._restart_attempts += 1
        self._health_history.append({"action": "browser_restart", "attempt": self._restart_attempts})
        log(f"Flow health recovery: dedicated Chrome restart {self._restart_attempts}/{MAX_RESTART_ATTEMPTS}")
        try:
            self.disconnect()
            restart_dedicated_chrome(timeout=NAVIGATION_TIMEOUT)
            self.playwright = sync_playwright().start()
            self.browser = connect_to_chrome(self.playwright)
            self.page = get_page_from_browser(self.browser, expected_url=expected_url)
            self.ui_state.connected = True
            # Never trust restored tabs after restart: navigate the exact URL.
            self.page.goto(expected_url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT * 1000)
            self.page.wait_for_timeout(1000)
            health = self.inspect_flow_health(expected_url)
            self._health_history[-1]["result"] = health.classification
        except ChromeLifecycleError as exc:
            self._health_history[-1]["error"] = str(exc)
            self._health_history[-1]["lifecycle_classification"] = exc.classification
            health = FlowHealthResult(
                timestamp=datetime.now().isoformat(), expected_url=expected_url,
                classification=exc.classification,
                lifecycle_classification=exc.classification,
                original_exception=str(exc),
                recovery_history=list(self._health_history),
            )
        except Exception as exc:
            self._health_history[-1]["error"] = str(exc)
            health = FlowHealthResult(
                timestamp=datetime.now().isoformat(), expected_url=expected_url,
                classification="NETWORK_ERROR",
                original_exception=str(exc),
                recovery_history=list(self._health_history),
            )
        health.recovery_history = list(self._health_history)
        self._last_health = health
        self._persist_health_diagnostic(health, f"restart_{self._restart_attempts}")
        return self._poll_for_healthy(expected_url) if health.classification == "LOADING" else health

    def ensure_flow_healthy(self, expected_url: str = None) -> FlowHealthResult:
        """Apply the bounded, failure-type-specific Flow recovery policy."""
        expected_url = expected_url or self._expected_project_url
        health = self.inspect_flow_health(expected_url)
        self._health_history.append(health.model_dump())
        if health.healthy:
            return health
        if health.classification == "LOADING":
            health = self._poll_for_healthy(expected_url)
            if health.healthy:
                return health
        self._persist_health_diagnostic(health, "initial_unhealthy")

        # No page refresh is attempted when CDP/browser is unavailable.
        if health.classification == "NETWORK_ERROR" and not self.browser:
            pass
        elif health.classification in {
            "APPLICATION_ERROR", "BLOCKING_APPLICATION_ERROR",
            "GENERATION_SURFACE_UNAVAILABLE", "STALE", "UNKNOWN", "NETWORK_ERROR",
        }:
            while self._refresh_attempts < MAX_REFRESH_ATTEMPTS:
                health = self._refresh_page(expected_url)
                if health.healthy:
                    return health
                if health.classification == "LOADING":
                    health = self._poll_for_healthy(expected_url)
                    if health.healthy:
                        return health

        while self._restart_attempts < MAX_RESTART_ATTEMPTS:
            health = self._restart_and_reconnect(expected_url)
            if health.healthy:
                return health
        self._persist_health_diagnostic(health, "recovery_exhausted")
        return health
    
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
            # Flow exposes Agent as a pressed toggle. Do not infer its state
            # from a generic ``Agent`` text match: the live page can report a
            # visible button with aria-pressed=true while the old verifier
            # incorrectly declared Agent OFF. Turn it off before Create.
            candidates = self.page.locator('button:has-text("Agent")')
            for index in range(candidates.count()):
                indicator = candidates.nth(index)
                if not indicator.is_visible(timeout=2000):
                    continue
                pressed = indicator.get_attribute("aria-pressed")
                checked = indicator.get_attribute("aria-checked")
                state = indicator.get_attribute("data-state")
                if pressed == "true" or checked == "true" or state == "on":
                    indicator.click()
                    self.page.wait_for_timeout(300)
                if (
                    indicator.get_attribute("aria-pressed") == "false"
                    or indicator.get_attribute("aria-checked") == "false"
                    or indicator.get_attribute("data-state") == "off"
                ):
                    log("Agent OFF verified")
                    return True

            # If Flow does not expose an Agent toggle, it is not applicable.
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
        element, details = self._locate_prompt_input("verification")
        return element is not None

    def _prompt_selectors(self) -> List[str]:
        """Return prompt selectors in production priority order."""
        return [
            '[role="textbox"][contenteditable="true"]',
            '[contenteditable="true"]:not(.g-recaptcha-response)',
            'textarea[placeholder*="escribe"]',
            'textarea[placeholder*="rompt"]',
            'textarea:not(.g-recaptcha-response)',
        ]

    def _locate_prompt_input(self, operation: str):
        """Find a fresh, visible, editable, attached Flow prompt element.

        The standalone diagnostic retained one locator across operations. The
        production path instead reacquires the candidate for every operation,
        because Flow can replace the editor during hydration or settings
        changes. The returned diagnostics are JSON-safe and are also used for
        failure screenshots/reports.
        """
        diagnostics = {
            "operation": operation,
            "url": self.page.url if self.page else None,
            "title": None,
            "candidates": [],
            "selected": None,
        }
        self._last_prompt_error = None
        self._last_prompt_error_code = None

        if not self.page:
            self._last_prompt_error_code = "FLOW_PAGE_UNHEALTHY"
            self._last_prompt_error = f"{operation}: page is not connected"
            self._last_prompt_diagnostics = diagnostics
            return None, diagnostics

        try:
            diagnostics["title"] = self.page.title()
        except Exception:
            pass

        for selector in self._prompt_selectors():
            try:
                locator = self.page.locator(selector)
                count = locator.count()
                for index in range(count):
                    candidate = locator.nth(index)
                    record = {"selector": selector, "index": index, "count": count}
                    try:
                        candidate.wait_for(state="attached", timeout=1000)
                        record["attached"] = True
                        record["visible"] = candidate.is_visible(timeout=1000)
                        record["editable"] = candidate.is_editable(timeout=1000)
                        if record["visible"] and record["editable"]:
                            # Re-read the same locator immediately to detect a
                            # Flow re-render between discovery and use.
                            candidate.wait_for(state="visible", timeout=1000)
                            candidate.is_editable(timeout=1000)
                            record["stable"] = True
                            diagnostics["candidates"].append(record)
                            diagnostics["selected"] = record
                            log(f"Prompt input found: {selector} (index {index})")
                            self._last_prompt_diagnostics = diagnostics
                            return candidate, diagnostics
                        record["stable"] = False
                    except Exception as exc:
                        record["error"] = str(exc)
                    diagnostics["candidates"].append(record)
            except Exception as exc:
                diagnostics["candidates"].append({"selector": selector, "error": str(exc)})

        self._last_prompt_diagnostics = diagnostics
        health = self.inspect_flow_health(self._expected_project_url)
        if health.classification in {
            "APPLICATION_ERROR", "BLOCKING_APPLICATION_ERROR", "STALE",
            "LOADING", "NETWORK_ERROR", "GENERATION_SURFACE_UNAVAILABLE",
        }:
            self._last_prompt_error_code = "FLOW_PAGE_UNHEALTHY"
        elif not any(item.get("attached") for item in diagnostics["candidates"]):
            self._last_prompt_error_code = "PROMPT_SELECTOR_NOT_FOUND"
        elif not any(item.get("visible") for item in diagnostics["candidates"]):
            self._last_prompt_error_code = "PROMPT_NOT_VISIBLE"
        elif not any(item.get("editable") for item in diagnostics["candidates"]):
            self._last_prompt_error_code = "PROMPT_NOT_EDITABLE"
        else:
            self._last_prompt_error_code = "PROMPT_RE_RENDERED"
        self._last_prompt_error = (
            f"{self._last_prompt_error_code}: {operation}: "
            "no visible, editable, attached prompt input"
        )
        log(self._last_prompt_error)
        self._capture_prompt_failure(operation)
        return None, diagnostics

    def _capture_prompt_failure(self, operation: str) -> None:
        """Persist exact prompt DOM diagnostics and a screenshot."""
        if not self.page:
            return
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.screenshots_dir.mkdir(parents=True, exist_ok=True)
            report_path = self.screenshots_dir / f"prompt_failure_{operation}_{timestamp}.json"
            report_path.write_text(json.dumps(self._last_prompt_diagnostics, indent=2), encoding="utf-8")
            take_screenshot(self.page, f"prompt_failure_{operation}", self.screenshots_dir)
            log(f"Prompt diagnostics saved: {report_path}")
        except Exception as exc:
            log(f"Could not save prompt diagnostics: {exc}")
    
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
        """Capture a structured, GET-only project baseline.

        The old implementation counted arbitrary DOM nodes.  Flow's durable
        identity is in ``flow.projectInitialData``; DOM data is retained only
        as corroboration for a later, already-correlated download.
        """
        snapshot = self._capture_project_snapshot()
        self._baseline_snapshot = snapshot
        return [item["media_id"] for item in snapshot.get("media", []) if item.get("media_id")]

    @staticmethod
    def _normalise_prompt(value: Any) -> str:
        """Normalise Flow prompt representations for exact correlation."""
        text = str(value or "")
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip().casefold()

    @staticmethod
    def _prompt_values(media: Dict[str, Any]) -> List[str]:
        """Extract the prompt forms observed in Flow's media payload."""
        metadata = media.get("mediaMetadata") or {}
        request = metadata.get("requestData") or {}
        values = [metadata.get("mediaTitle")]
        inputs = request.get("promptInputs") or []
        for item in inputs:
            structured = item.get("structuredPrompt") or {}
            for part in structured.get("parts") or []:
                values.append(part.get("text"))
        values.append((media.get("video") or {}).get("generatedVideo", {}).get("prompt"))
        return [FlowSceneGenerator._normalise_prompt(value) for value in values if value]

    @staticmethod
    def _media_snapshot(media: Dict[str, Any], workflows: Dict[str, Dict[str, Any]], base_url: str) -> Dict[str, Any]:
        """Convert one Flow media object to a stable checkpoint record."""
        metadata = media.get("mediaMetadata") or {}
        video = (media.get("video") or {}).get("generatedVideo") or {}
        operation = (media.get("video") or {}).get("operation") or {}
        workflow_id = media.get("workflowId")
        workflow = workflows.get(workflow_id, {})
        workflow_metadata = workflow.get("metadata") or {}
        media_id = media.get("name")
        redirect = urljoin(base_url, f"/fx/api/trpc/media.getMediaUrlRedirect?name={quote(str(media_id or ''))}") if media_id else None
        size = metadata.get("mediaBlobSize")
        try:
            size = int(size) if size is not None else 0
        except (TypeError, ValueError):
            size = 0
        return {
            "media_id": media_id,
            "project_id": media.get("projectId"),
            "workflow_id": workflow_id,
            "workflow_step_id": media.get("workflowStepId"),
            "workflow_name": workflow.get("name"),
            "workflow_create_time": workflow_metadata.get("createTime"),
            "workflow_update_time": workflow_metadata.get("updateTime"),
            "workflow_primary_media_id": workflow_metadata.get("primaryMediaId"),
            "workflow_batch_id": workflow_metadata.get("batchId"),
            "create_time": metadata.get("createTime"),
            "media_title": metadata.get("mediaTitle"),
            "prompt_values": FlowSceneGenerator._prompt_values(media),
            "status": (metadata.get("mediaStatus") or {}).get("mediaGenerationStatus"),
            "media_blob_size": size,
            "operation_name": operation.get("name"),
            "model": video.get("model"),
            "aspect_ratio": video.get("aspectRatio"),
            "duration": (video.get("dimensions") or {}).get("length"),
            "redirect_url": redirect,
        }

    @classmethod
    def _parse_project_snapshot(cls, payload: Dict[str, Any], source: str = "flow.projectInitialData") -> Dict[str, Any]:
        """Parse the saved/returned Flow projectInitialData response."""
        root = payload or {}
        if "result" in root:
            root = ((root.get("result") or {}).get("data") or {}).get("json") or {}
        contents = root.get("projectContents") or {}
        workflows = {
            item.get("name"): item
            for item in contents.get("workflows", [])
            if item.get("name")
        }
        base_url = "https://labs.google"
        media = [cls._media_snapshot(item, workflows, base_url) for item in contents.get("media", [])]
        return {
            "captured_at": datetime.now().isoformat(),
            "source": source,
            "project_id": root.get("projectId"),
            "workflows": [
                {
                    "workflow_id": item.get("name"),
                    "project_id": item.get("projectId"),
                    "metadata": item.get("metadata") or {},
                }
                for item in workflows.values()
            ],
            "media": media,
            "media_ids": [item["media_id"] for item in media if item.get("media_id")],
        }

    def _project_initial_data_url(self) -> Optional[str]:
        """Build Flow's projectInitialData URL without performing a mutation."""
        project_url = self._expected_project_url or getattr(self.page, "url", None)
        match = re.search(r"/project/([0-9a-f-]{20,})", project_url or "", re.I)
        if not match:
            return None
        input_value = quote(json.dumps({"json": {"projectId": match.group(1)}}, separators=(",", ":")))
        return f"https://labs.google/fx/api/trpc/flow.projectInitialData?input={input_value}"

    def _snapshot_from_resume_hint(
        self,
        endpoint: str,
        endpoint_diagnostics: Dict[str, Any],
        dom_tiles: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Build a read-only snapshot from one persisted, exact media identity.
        
        This is deliberately not a discovery mechanism. The hint must have
        come from an earlier authoritative Flow response, belong to the
        project in the current URL, and be visible in the current page by its
        exact media URL or workflow ID.
        """
        hint = self._resume_asset_hint or {}
        media_id = hint.get("media_id")
        if not media_id:
            return None
        project_url = self._expected_project_url or getattr(self.page, "url", None)
        project_match = re.search(r"/project/([0-9a-f-]{20,})", project_url or "", re.I)
        project_id = project_match.group(1) if project_match else None
        if hint.get("project_id") and project_id and hint["project_id"] != project_id:
            return None
        visible = any(
            media_id in str(tile.get("src") or "")
            or media_id in str(tile.get("poster") or "")
            or (
                hint.get("workflow_id")
                and hint["workflow_id"] == tile.get("workflow_id")
            )
            for tile in dom_tiles
        )
        if not visible:
            return None
        return {
            "captured_at": datetime.now().isoformat(),
            "source": "flow.dom_resume_hint",
            "project_id": project_id or hint.get("project_id"),
            "workflows": [],
            "media": [dict(hint)],
            "media_ids": [media_id],
            "endpoint": endpoint,
            "endpoint_status": endpoint_diagnostics.get("status"),
            "endpoint_diagnostics": endpoint_diagnostics,
            "dom_tiles": dom_tiles,
            "reconciliation": "exact_persisted_media_id_visible",
        }

    def _capture_project_snapshot(self) -> Dict[str, Any]:
        """Fetch project state using GET only and attach safe DOM diagnostics."""
        endpoint = self._project_initial_data_url()
        if not endpoint or not self.page:
            return {
                "captured_at": datetime.now().isoformat(), "source": "unavailable",
                "media": [], "media_ids": [], "error": "project endpoint unavailable",
            }
        project_url = self._expected_project_url or getattr(self.page, "url", None)
        project_match = re.search(r"/project/([0-9a-f-]{20,})", project_url or "", re.I)
        project_id = project_match.group(1) if project_match else None
        input_parameters = {"json": {"projectId": project_id}} if project_id else None
        dom_tiles = self._capture_dom_tiles()
        try:
            response = self.page.evaluate(
                """async endpoint => {
                    const r = await fetch(endpoint, {
                        method: 'GET',
                        credentials: 'include',
                        headers: {'Accept': 'application/json'}
                    });
                    const bodyText = await r.text();
                    let body = null;
                    try { body = bodyText ? JSON.parse(bodyText) : null; } catch (_) {}
                    return {
                        status: r.status,
                        ok: r.ok,
                        body,
                        body_text: bodyText.slice(0, 4096),
                        content_type: r.headers.get('content-type')
                    };
                }""",
                endpoint,
            )
            diagnostics = {
                "url": endpoint,
                "method": "GET",
                "project_id": project_id,
                "input": input_parameters,
                "status": response.get("status"),
                "content_type": response.get("content_type"),
                "response_body": response.get("body"),
                "response_text": response.get("body_text"),
            }
            if not response.get("ok"):
                fallback = self._snapshot_from_resume_hint(endpoint, diagnostics, dom_tiles)
                if fallback:
                    return fallback
                raise RuntimeError(
                    f"projectInitialData HTTP {response.get('status')}: "
                    f"{response.get('body_text') or '<empty response body>'}"
                )
            snapshot = self._parse_project_snapshot(response.get("body") or {})
            snapshot["endpoint"] = endpoint
            snapshot["endpoint_status"] = response.get("status")
            snapshot["endpoint_diagnostics"] = diagnostics
            snapshot["dom_tiles"] = dom_tiles
            return snapshot
        except Exception as exc:
            log(f"Could not capture Flow project snapshot: {exc}")
            return {
                "captured_at": datetime.now().isoformat(),
                "source": "flow.projectInitialData",
                "endpoint": endpoint,
                "endpoint_status": diagnostics.get("status") if "diagnostics" in locals() else None,
                "endpoint_diagnostics": diagnostics if "diagnostics" in locals() else {
                    "url": endpoint, "method": "GET", "project_id": project_id,
                    "input": input_parameters,
                },
                "media": [], "media_ids": [],
                "error": str(exc),
                "dom_tiles": dom_tiles,
            }

    def _capture_dom_tiles(self) -> List[Dict[str, Any]]:
        """Collect non-authoritative video URL/tile diagnostics."""
        if not self.page:
            return []
        try:
            result = self.page.evaluate("""() => Array.from(document.querySelectorAll('video')).map((video, index) => {
                const anchor = video.closest('a'); const tile = video.closest('[data-tile-id]');
                return {index, src: video.currentSrc || video.getAttribute('src'), poster: video.getAttribute('poster'),
                  ready_state: video.readyState, duration: Number.isFinite(video.duration) ? video.duration : null,
                  width: video.videoWidth || null, height: video.videoHeight || null,
                  workflow_id: anchor && (anchor.getAttribute('href') || '').split('/').pop(),
                  tile_id: tile && (tile.getAttribute('data-tile-id') || tile.id)};
            })""")
            return result or []
        except Exception:
            return []

    def _correlate_submission(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Rank only post-baseline media with matching prompt/metadata."""
        baseline_ids = set(self._baseline_snapshot.get("media_ids", self._assets_before_generation))
        expected_prompt = self._normalise_prompt(self._submission_context.get("prompt"))
        submitted_at = self._submission_context.get("submitted_at")
        candidates = []
        for media in snapshot.get("media", []):
            if not media.get("media_id") or media["media_id"] in baseline_ids:
                continue
            prompt_match = expected_prompt and expected_prompt in set(media.get("prompt_values", []))
            workflow_match = bool(media.get("workflow_id") and media.get("workflow_id") == self._submission_context.get("workflow_id"))
            score = (100 if prompt_match else 0) + (25 if workflow_match else 0) + (10 if media.get("media_blob_size", 0) > 0 else 0)
            if submitted_at and media.get("create_time") and media["create_time"] >= submitted_at:
                score += 15
            candidates.append({"score": score, "prompt_match": bool(prompt_match), "workflow_match": workflow_match, "media": media})
        candidates.sort(key=lambda item: (item["score"], item["media"].get("create_time") or ""), reverse=True)
        selected = candidates[0] if candidates and candidates[0]["prompt_match"] else None
        return {
            "selected": selected["media"] if selected else None,
            "selected_score": selected["score"] if selected else 0,
            "candidates": candidates[:10],
            "correlation": "exact_prompt" if selected else "unresolved",
        }
    
    def generate_scene(
        self,
        request: SceneGenerationRequest,
        dry_run: bool = True,
        require_confirmation: bool = True,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
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
            validation = validate_video_file(scene_file, self.config.aspect_ratio)
            if validation.get("valid"):
                log(f"Scene already exists: {scene_file}")
                return SceneGenerationResult.skipped(
                    scene_id=request.scene_id,
                    sequence=request.sequence,
                    reason=f"Scene already exists: {scene_file}",
                )
        
        # Verify settings
        self.verify_settings()

        if not self.ui_state.prompt_ready:
            return SceneGenerationResult.failure(
                scene_id=request.scene_id,
                sequence=request.sequence,
                error=self._last_prompt_error or "prompt verification: prompt input unavailable",
            )
        
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
                error=self._last_prompt_error or "prompt entry failed",
            )
        
        # Submit generation
        generation_started = datetime.now().isoformat()
        
        submission_timestamp = datetime.now().isoformat()
        prompt_hash = hashlib.sha256(request.visual_prompt.encode("utf-8")).hexdigest()
        self._submission_id = str(uuid.uuid4())
        self._submission_context = {
            "submission_id": self._submission_id,
            "prompt": request.visual_prompt,
            "prompt_hash": prompt_hash,
            "project_id": request.project_id,
            "submitted_at": submission_timestamp,
            "baseline_media_ids": list(self._baseline_snapshot.get("media_ids", self._assets_before_generation)),
        }

        def emit_checkpoint(update: Dict[str, Any]) -> None:
            """Attach immutable request identity to every scene checkpoint."""
            if not checkpoint_callback:
                return
            payload = {
                "scene_id": request.scene_id,
                "sequence": request.sequence,
                "flow_project_url": request.flow_project_url or self._expected_project_url,
                "prompt_hash": prompt_hash,
                "prompt": request.visual_prompt,
                "generation_cost": self.ui_state.generation_cost,
            }
            payload.update(update)
            checkpoint_callback(payload)

        if not self._submit_generation(
            checkpoint_callback=lambda evidence: emit_checkpoint({
                "status": "generation_submitted" if evidence.get("state") == "SUBMITTED" else "prepared",
                "submission_timestamp": submission_timestamp,
                "generation_started_at": generation_started,
                "checkpoint_evidence": evidence,
            }),
        ):
            return SceneGenerationResult.failure(
                scene_id=request.scene_id,
                sequence=request.sequence,
                error="Failed to submit generation",
            )
        
        # Wait for completion
        completed, error = self._wait_for_completion(
            checkpoint_callback=emit_checkpoint,
            expected_project_url=request.flow_project_url or self._expected_project_url,
        )
        
        if not completed:
            take_screenshot(self.page, f"generation_failed_{request.scene_id}", self.screenshots_dir)
            error_lower = (error or "").lower()
            result_status = (
                "timeout" if "timeout" in error_lower
                else "unknown_reconciliation_required" if "unknown_reconciliation_required" in error_lower
                else "failed"
            )
            return SceneGenerationResult.failure(
                scene_id=request.scene_id,
                sequence=request.sequence,
                error=error or "Generation failed",
                status=result_status,
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

        self._last_download_evidence = {
            "status": "downloaded",
            "path": str(downloaded_path),
            "downloaded_at": downloaded_at,
            "exists": downloaded_path.exists(),
            "size": downloaded_path.stat().st_size if downloaded_path.exists() else 0,
        }
        
        # Validate downloaded file
        validation = validate_video_file(downloaded_path, self.config.aspect_ratio)
        
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

        result = SceneGenerationResult.success_result(
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

        if checkpoint_callback:
            emit_checkpoint({
                "status": "validated",
                "submission_timestamp": submission_timestamp,
                "generation_started_at": generation_started,
                "generation_completed_at": generation_completed,
                "downloaded_at": downloaded_at,
                "checkpoint_evidence": {
                    "submission": self._last_submission_evidence,
                    "polling": self._last_poll_evidence,
                    "download": self._last_download_evidence,
                },
                "file_path": str(downloaded_path),
                "validation": validation,
            })
        return result

    def resume_submitted_scene(
        self,
        request: SceneGenerationRequest,
        checkpoint: Dict[str, Any],
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SceneGenerationResult:
        """Resume a previously submitted scene without clicking Create.

        The checkpoint is the durable proof that Create was already clicked.
        This method only reconnects/inspects, waits, downloads, and validates.
        """
        ensure_scene_directories()
        project_dir = self.output_dir / request.project_id
        scene_file = project_dir / f"{request.scene_id}.mp4"
        existing_validation = validate_video_file(scene_file, self.config.aspect_ratio)
        if existing_validation.get("valid"):
            if checkpoint_callback:
                checkpoint_callback({
                    "scene_id": request.scene_id, "sequence": request.sequence,
                    "status": "validated", "file_path": str(scene_file),
                    "validation": existing_validation,
                })
            return SceneGenerationResult.skipped(
                request.scene_id, request.sequence,
                f"Previously submitted scene already validated: {scene_file}",
            )

        checkpoint_evidence = checkpoint.get("checkpoint_evidence", {}) or {}
        submission_evidence = checkpoint_evidence.get("submission", checkpoint_evidence) or {}
        self._assets_before_generation = submission_evidence.get(
            "assets_before", self._assets_before_generation
        )
        self._last_submission_evidence = submission_evidence
        self._submission_id = submission_evidence.get("submission_id") or checkpoint.get("submission_id")
        self._submission_context = {
            "submission_id": self._submission_id,
            "prompt": checkpoint.get("prompt", request.visual_prompt),
            "prompt_hash": checkpoint.get("prompt_hash"),
            "project_id": request.project_id,
            "submitted_at": submission_evidence.get("clicked_at") or checkpoint.get("submission_timestamp"),
            "workflow_id": submission_evidence.get("workflow_id"),
        }
        self._baseline_snapshot = submission_evidence.get("baseline_snapshot", {
            "media_ids": list(self._assets_before_generation),
            "media": [],
        })
        polling_evidence = checkpoint_evidence.get("polling", {}) or {}
        self._resume_asset_hint = (
            polling_evidence.get("selected_asset")
            or checkpoint.get("selected_asset")
            or checkpoint.get("correlated_media")
        )
        def emit_checkpoint(update: Dict[str, Any]) -> None:
            if checkpoint_callback:
                payload = {
                    "scene_id": request.scene_id,
                    "sequence": request.sequence,
                    "flow_project_url": checkpoint.get("flow_project_url") or request.flow_project_url,
                    "prompt_hash": checkpoint.get("prompt_hash"),
                    "prompt": checkpoint.get("prompt", request.visual_prompt),
                    "generation_cost": checkpoint.get("generation_cost"),
                }
                payload.update(update)
                checkpoint_callback(payload)
        completed, error = self._wait_for_completion(
            checkpoint_callback=emit_checkpoint,
            expected_project_url=checkpoint.get("flow_project_url") or request.flow_project_url,
        )
        if not completed:
            error_lower = (error or "").lower()
            return SceneGenerationResult.failure(
                request.scene_id, request.sequence, error or "Submitted generation did not complete",
                status=(
                    "timeout" if "timeout" in error_lower
                    else "unknown_reconciliation_required" if "unknown_reconciliation_required" in error_lower
                    else "failed"
                ),
            )
        project_dir.mkdir(parents=True, exist_ok=True)
        downloaded_path = self._download_new_asset(scene_file)
        if not downloaded_path:
            return SceneGenerationResult.failure(
                request.scene_id, request.sequence, "Failed to download previously submitted generated video"
            )
        downloaded_at = datetime.now().isoformat()
        self._last_download_evidence = {
            "status": "downloaded", "path": str(downloaded_path),
            "downloaded_at": downloaded_at, "exists": downloaded_path.exists(),
            "size": downloaded_path.stat().st_size if downloaded_path.exists() else 0,
        }
        validation = validate_video_file(downloaded_path, self.config.aspect_ratio)
        if not validation.get("valid"):
            return SceneGenerationResult.failure(
                request.scene_id, request.sequence,
                f"Downloaded video invalid: {validation.get('error')}"
            )
        if checkpoint_callback:
            emit_checkpoint({
                "sequence": request.sequence,
                "status": "validated", "downloaded_at": downloaded_at,
                "file_path": str(downloaded_path), "validation": validation,
                "checkpoint_evidence": {
                    "submission": self._last_submission_evidence,
                    "polling": self._last_poll_evidence,
                    "download": self._last_download_evidence,
                },
            })
        return SceneGenerationResult.success_result(
            request.scene_id, request.sequence, downloaded_path,
            validation["file_size"], validation["duration"], validation["width"],
            validation["height"], validation["codec"],
            generation_cost=checkpoint.get("generation_cost"),
            generation_started_at=checkpoint.get("generation_started_at"),
            generation_completed_at=datetime.now().isoformat(),
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
        """Enter the visual prompt and verify it was entered correctly."""
        element, details = self._locate_prompt_input("entry")
        if element is None:
            return False
        selector = details["selected"]["selector"]
        try:
            element.click()
        except Exception as exc:
            self._last_prompt_error_code = "PROMPT_ENTRY_FAILED"
            self._last_prompt_error = f"prompt click/focus: {exc}"
            self._capture_prompt_failure("click")
            return False
        try:
            element.fill("")
            element.fill(prompt)
        except Exception as exc:
            self._last_prompt_error_code = "PROMPT_ENTRY_FAILED"
            self._last_prompt_error = f"prompt fill: {exc}"
            self._capture_prompt_failure("fill")
            return False
        self.page.wait_for_timeout(500)
        entered_text = self._read_prompt_content(element, selector)
        if not entered_text:
            self._last_prompt_error_code = "PROMPT_READBACK_FAILED"
            self._last_prompt_error = "prompt read-back: empty value"
            self._capture_prompt_failure("readback")
            return False
        if prompt[:50] not in entered_text:
            self._last_prompt_error_code = "PROMPT_READBACK_FAILED"
            self._last_prompt_error = (
                f"prompt read-back: expected prefix not found (got {entered_text[:80]!r})"
            )
            self._capture_prompt_failure("readback")
            return False
        log(f"Prompt verified: {len(entered_text)} chars entered")
        return True

    def verify_ui_only(self, request: SceneGenerationRequest) -> SceneGenerationResult:
        """Exercise production prompt flow without clicking Create.

        This intentionally stops after locating the Create button. It never
        calls `_submit_generation`, waits for generation, downloads, or spends
        credits.
        """
        if not self._expected_project_url and request.flow_project_url:
            self._expected_project_url = request.flow_project_url
        if self._expected_project_url:
            health = self.ensure_flow_healthy(self._expected_project_url)
        else:
            health = self._last_health
        if health and not health.healthy:
            result = SceneGenerationResult.failure(
                scene_id=request.scene_id,
                sequence=request.sequence,
                error=f"FLOW_PAGE_UNHEALTHY: {health.classification}",
            )
            result.verification = self._ui_verification_evidence()
            return result
        self.verify_settings()
        if not self.ui_state.prompt_ready:
            result = SceneGenerationResult.failure(
                scene_id=request.scene_id,
                sequence=request.sequence,
                error=self._last_prompt_error or "prompt verification failed",
            )
            result.verification = self._ui_verification_evidence()
            return result
        if not self._enter_prompt(request.visual_prompt):
            result = SceneGenerationResult.failure(
                scene_id=request.scene_id,
                sequence=request.sequence,
                error=self._last_prompt_error or "prompt entry failed",
            )
            result.verification = self._ui_verification_evidence()
            return result
        create = None
        create_selector = None
        for selector in ['button:has-text("Create")', '[aria-label*="Create"]']:
            candidate = self.page.locator(selector).first
            try:
                if candidate.is_visible(timeout=2000):
                    create = candidate
                    create_selector = selector
                    break
            except Exception:
                continue
        if create is None:
            self._last_prompt_error = "create discovery: no visible Create button"
            self._capture_prompt_failure("create")
            result = SceneGenerationResult.failure(
                scene_id=request.scene_id,
                sequence=request.sequence,
                error=self._last_prompt_error,
            )
            result.verification = self._ui_verification_evidence()
            return result
        verification = self._ui_verification_evidence()
        verification.update({
            "url": self.page.url,
            "title": self.page.title(),
            "prompt": self._last_prompt_diagnostics,
            "prompt_entered": True,
            "prompt_read_back": True,
            "create_button_found": True,
            "create_selector": create_selector,
            "create_clicked": False,
            "generation_submitted": False,
            "download_attempted": False,
            "credits_consumed": 0,
            "initial_health": self._health_history[0] if self._health_history else None,
            "final_health": self._last_health.model_dump() if self._last_health else None,
            "refresh_attempts": self._refresh_attempts,
            "browser_restart_attempts": self._restart_attempts,
            "health_recovery_history": self._health_history,
        })
        take_screenshot(self.page, "ui_verify_prompt_entered", self.screenshots_dir)
        return SceneGenerationResult.dry_run(
            scene_id=request.scene_id,
            sequence=request.sequence,
            generation_cost=self.ui_state.generation_cost,
            verification=verification,
        )

    def _ui_verification_evidence(self) -> dict:
        """Return safety evidence even when UI-only verification fails."""
        return {
            "initial_health": self._health_history[0] if self._health_history else None,
            "final_health": self._last_health.model_dump() if self._last_health else None,
            "refresh_attempts": self._refresh_attempts,
            "browser_restart_attempts": self._restart_attempts,
            "health_recovery_history": self._health_history,
            "create_clicked": False,
            "generation_submitted": False,
            "download_attempted": False,
            "credits_consumed": 0,
        }

    def _read_prompt_content(self, element, selector: str) -> Optional[str]:
        """Read back the content from a prompt input element."""
        try:
            # For contenteditable divs
            if "contenteditable" in selector or "textbox" in selector:
                return element.inner_text()
            # For textareas/inputs
            else:
                return element.input_value()
        except Exception:
            return None
    
    def _submit_generation(self, checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None) -> bool:
        """Submit the generation request."""
        self._last_submission_evidence = {}
        try:
            submit_selectors = [
                # Flow renders a toolbar "Create" menu before the composer
                # submit button. The toolbar button has aria-haspopup=dialog;
                # the composer button carries the arrow_forward icon.
                'button:has-text("arrow_forward"):has-text("Create")',
                'button:has-text("Create")',
                'button:has-text("Generate")',
                'button:has-text("Submit")',
                'button[type="submit"]',
                '[aria-label*="Create"]',
                '[aria-label*="Generate"]',
            ]
            
            for selector in submit_selectors:
                try:
                    locator = self.page.locator(selector)
                    for index in range(locator.count()):
                        element = locator.nth(index)
                        if not element.is_visible(timeout=2000):
                            continue
                        if element.get_attribute("aria-haspopup") == "dialog":
                            continue
                        if element.get_attribute("aria-disabled") == "true":
                            continue
                        # PREPARED is durable intent, not billing evidence. If
                        # the process dies before click, the orchestrator may
                        # safely retry; after click the SUBMITTED checkpoint is
                        # emitted before any wait or navigation.
                        prepared = {
                            "state": "PREPARED",
                            "submission_id": self._submission_id or str(uuid.uuid4()),
                            "prepared_at": datetime.now().isoformat(),
                            "baseline_snapshot": self._baseline_snapshot,
                            "assets_before": list(self._assets_before_generation),
                            "url": getattr(self.page, "url", None),
                        }
                        self._submission_id = prepared["submission_id"]
                        self._submission_context["submission_id"] = self._submission_id
                        if checkpoint_callback:
                            checkpoint_callback(dict(prepared))
                        element.click()
                        self._last_submission_evidence = {
                            "state": "SUBMITTED",
                            "submission_id": self._submission_id,
                            "selector": selector,
                            "index": index,
                            "clicked_at": datetime.now().isoformat(),
                            "url": getattr(self.page, "url", None),
                            "assets_before": list(self._assets_before_generation),
                            "baseline_snapshot": self._baseline_snapshot,
                        }
                        self._submission_context["submitted_at"] = self._last_submission_evidence["clicked_at"]
                        # This callback is deliberately before the wait and is
                        # the durable boundary preventing duplicate Create clicks.
                        if checkpoint_callback:
                            checkpoint_callback(dict(self._last_submission_evidence))
                        self.page.wait_for_timeout(2000)
                        log("Generation submitted")
                        take_screenshot(self.page, "generation_submitted", self.screenshots_dir)
                        return True
                except Exception:
                    # Once click() has returned, Create may already have
                    # spent credits. A checkpoint failure must abort rather
                    # than falling through to another selector/click.
                    if self._last_submission_evidence.get("state") == "SUBMITTED":
                        raise
                    continue
            
            log("Could not find submit button")
            return False
            
        except Exception as e:
            log(f"Failed to submit generation: {e}")
            return False
    
    def _generation_evidence(self) -> Dict[str, Any]:
        """Collect authoritative project state plus background diagnostics.

        Page-wide text, arbitrary videos, and generic download buttons are
        deliberately never completion signals. Only an exact correlated media
        record can transition this state machine to COMPLETED.
        """
        snapshot = self._capture_project_snapshot()
        correlation = self._correlate_submission(snapshot)
        selected = correlation.get("selected")
        page_content = self._safe_page_text()
        contextual_errors = []
        try:
            contextual_errors = self._inspect_error_elements([])
        except Exception:
            pass
        status = selected.get("status") if selected else None
        blob_size = selected.get("media_blob_size", 0) if selected else 0
        authoritative_failure = bool(selected and status and "FAILED" in status)
        authoritative_success = bool(
            selected
            and status and "SUCCESSFUL" in status
            and blob_size > 0
            and selected.get("redirect_url")
        )
        return {
            "captured_at": datetime.now().isoformat(),
            "snapshot": snapshot,
            "correlation": correlation,
            "selected_asset": selected,
            "authoritative_status": status,
            "authoritative_failure": authoritative_failure,
            "authoritative_success": authoritative_success,
            "background": {
                "page_error_terms": [term for term in ["generation failed", "error occurred", "please try again"] if term in page_content],
                "contextual_errors": contextual_errors[:20],
                "url": getattr(self.page, "url", None),
            },
        }

    def _emit_poll_state(
        self,
        state: str,
        evidence: Dict[str, Any],
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]],
    ) -> None:
        """Log and persist only meaningful polling state transitions."""
        self._last_poll_state = state
        self._last_poll_evidence = {"state": state, **evidence}
        log(f"Generation state={state} evidence={json.dumps(evidence, default=str)[:900]}")
        if checkpoint_callback:
            checkpoint_callback({
                # UI completion is not file validation. Keep it resumable so
                # a crash before download can never cause a second Create.
                "status": (
                    "generated" if state in {"COMPLETED", "DOWNLOAD_READY"}
                    else "failed" if state == "FAILED"
                    else "timeout" if state == "TIMEOUT"
                    else "unknown_reconciliation_required" if state == "UNKNOWN_RECONCILIATION_REQUIRED"
                    else "generating"
                ),
                "checkpoint_evidence": {
                    "submission": dict(self._last_submission_evidence),
                    "polling": {"state": state, **evidence},
                },
            })

    def _reconnect_for_polling(self, expected_project_url: Optional[str]) -> bool:
        """Reconnect to the exact project without touching Create."""
        if not expected_project_url:
            return False
        try:
            self.disconnect()
            self.connect(expected_url=expected_project_url)
            self.navigate_to_project(expected_project_url)
            log("Reconnected to Flow project for submitted-generation polling")
            return True
        except Exception as exc:
            log(f"Submitted-generation reconnect failed: {exc}")
            return False

    def _wait_for_completion(
        self,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        expected_project_url: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Wait for generation to complete.
        
        Returns:
            Tuple of (completed, error_message).
        """
        log(f"Waiting for generation (timeout: {FLOW_SCENE_TIMEOUT}s)...")
        
        start_time = time.time()
        last_heartbeat = start_time
        recovery_attempts = 0
        snapshot_error_attempts = 0
        last_signature = None
        self._emit_poll_state("WAITING", {"elapsed_seconds": 0}, checkpoint_callback)
        
        while time.time() - start_time < FLOW_SCENE_TIMEOUT:
            try:
                if not self.page:
                    raise RuntimeError("Flow page disconnected")
                evidence = self._generation_evidence()
                signature = json.dumps(evidence, sort_keys=True, default=str)
                if evidence.get("authoritative_failure"):
                    screenshot = take_screenshot(self.page, "generation_error", self.screenshots_dir)
                    if screenshot:
                        evidence["screenshot_path"] = str(screenshot)
                    self._emit_poll_state("FAILED", evidence, checkpoint_callback)
                    return False, f"Flow authoritative generation failure: {evidence.get('authoritative_status')}"
                if evidence.get("authoritative_success"):
                    self._emit_poll_state("COMPLETION_CANDIDATE", evidence, checkpoint_callback)
                    self._correlated_asset = evidence["selected_asset"]
                    self._emit_poll_state("COMPLETED", evidence, checkpoint_callback)
                    self._emit_poll_state("DOWNLOAD_READY", evidence, checkpoint_callback)
                    take_screenshot(self.page, "generation_complete", self.screenshots_dir)
                    return True, None
                elif evidence.get("snapshot", {}).get("error"):
                    snapshot_error_attempts += 1
                    self._emit_poll_state("UNKNOWN_RECONCILIATION_REQUIRED", evidence, checkpoint_callback)
                    if snapshot_error_attempts > FLOW_SCENE_MAX_POLL_RECOVERY_ATTEMPTS:
                        return False, (
                            "UNKNOWN_RECONCILIATION_REQUIRED: Flow project state remained unavailable "
                            f"after {snapshot_error_attempts} GET attempts"
                        )
                elif evidence.get("selected_asset"):
                    self._emit_poll_state("GENERATING", evidence, checkpoint_callback)
                elif signature != last_signature:
                    self._emit_poll_state("WAITING", evidence, checkpoint_callback)
                last_signature = signature
                if not evidence.get("snapshot", {}).get("error"):
                    snapshot_error_attempts = 0

                elapsed = int(time.time() - start_time)
                if time.time() - last_heartbeat >= max(1, FLOW_SCENE_HEARTBEAT_INTERVAL):
                    log(f"Generation heartbeat: state={self._last_poll_state} elapsed={elapsed}s")
                    last_heartbeat = time.time()
            except Exception as e:
                recovery_attempts += 1
                evidence = {"error": str(e), "elapsed_seconds": int(time.time() - start_time), "recovery_attempt": recovery_attempts}
                self._emit_poll_state("UNKNOWN_RECONCILIATION_REQUIRED", evidence, checkpoint_callback)
                if recovery_attempts > FLOW_SCENE_MAX_POLL_RECOVERY_ATTEMPTS:
                    return False, f"UNKNOWN_RECONCILIATION_REQUIRED: polling failed after {recovery_attempts - 1} reconnect attempts: {e}"
                if not self._reconnect_for_polling(expected_project_url):
                    continue
            
            time.sleep(FLOW_SCENE_POLL_INTERVAL)
        
        timeout_evidence = {"elapsed_seconds": int(time.time() - start_time)}
        screenshot = take_screenshot(self.page, "generation_timeout", self.screenshots_dir)
        if screenshot:
            timeout_evidence["screenshot_path"] = str(screenshot)
        self._emit_poll_state("TIMEOUT", timeout_evidence, checkpoint_callback)
        return False, f"TIMEOUT: generation timed out after {FLOW_SCENE_TIMEOUT}s"
    
    def retrieve_exact_media(self, media_id: str, save_path: Path) -> Optional[Path]:
        """Retrieve one exact Flow media item using the authenticated page session.

        The request is issued inside the Flow page so its cookies and session
        credentials are reused.  Only the supplied media ID is used; this
        method never discovers or selects an arbitrary asset.
        """
        if not self.page or not media_id:
            return None

        redirect_url = urljoin(
            "https://labs.google",
            f"/fx/api/trpc/media.getMediaUrlRedirect?name={quote(str(media_id))}",
        )
        diagnostics = {
            "method": "authenticated_page_fetch",
            "media_id": media_id,
            "initial_url": redirect_url,
        }

        try:
            request_context = getattr(self.page.context, "request", None)
            if request_context is not None:
                response = request_context.get(
                    redirect_url,
                    timeout=FLOW_SCENE_DOWNLOAD_TIMEOUT * 1000,
                    fail_on_status_code=False,
                )
                status_code = response.status
                final_url = getattr(response, "url", redirect_url)
                headers = response.headers
                content_type = headers.get("content-type", "")
                body = response.body()
                response_ok = 200 <= status_code < 300
                diagnostics.update({
                    "status": status_code,
                    "final_url": final_url,
                    "content_type": content_type,
                    "byte_size": len(body),
                    "transport": "playwright_browser_context_request",
                })
            else:
                response = self.page.evaluate(
                """async ({url}) => {
                    const r = await fetch(url, {
                        method: 'GET',
                        credentials: 'include',
                        redirect: 'follow'
                    });
                    const contentType = r.headers.get('content-type') || '';
                    const bytes = new Uint8Array(await r.arrayBuffer());
                    let binary = '';
                    const chunk = 0x8000;
                    for (let i = 0; i < bytes.length; i += chunk) {
                        binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
                    }
                    return {
                        status: r.status,
                        ok: r.ok,
                        final_url: r.url,
                        content_type: contentType,
                        byte_length: bytes.length,
                        body_base64: btoa(binary)
                    };
                }""",
                {"url": redirect_url},
                )
                status_code = response.get("status")
                final_url = response.get("final_url")
                content_type = response.get("content_type") or ""
                body = base64.b64decode(response.get("body_base64") or "", validate=True)
                response_ok = bool(response.get("ok"))
                diagnostics.update({
                    "status": status_code,
                    "final_url": final_url,
                    "content_type": content_type,
                    "byte_size": len(body),
                    "transport": "page_fetch",
                })

            self._last_download_evidence = diagnostics

            content_type = content_type.lower()
            if (
                not response_ok
                or len(body) < 1024
                or "text/html" in content_type
                or "application/json" in content_type
                or "text/plain" in content_type
            ):
                diagnostics.update({
                    "status": "rejected",
                    "reason": "non-media, empty, or placeholder response",
                    "byte_size": len(body),
                })
                self._last_download_evidence = diagnostics
                return None

            save_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = save_path.with_name(f".{save_path.name}.{uuid.uuid4().hex}.part")
            temporary_path.write_bytes(body)
            temporary_path.replace(save_path)
            diagnostics.update({
                "status": "downloaded",
                "path": str(save_path),
                "exists": save_path.exists(),
                "byte_size": save_path.stat().st_size,
            })
            self._last_download_evidence = diagnostics
            self._download_path = save_path
            return save_path
        except Exception as exc:
            diagnostics.update({"status": "failed", "error": str(exc)})
            self._last_download_evidence = diagnostics
            log(f"Authenticated retrieval failed for correlated media {media_id}: {exc}")
            return None

    def _download_new_asset(self, save_path: Path) -> Optional[Path]:
        """
        Download the newly generated asset.
        
        Args:
            save_path: Path to save the downloaded file.
        
        Returns:
            Path to downloaded file, or None if failed.
        """
        log("Attempting to download new asset...")
        
        media_id = (self._correlated_asset or {}).get("media_id")
        if not media_id:
            self._last_download_evidence = {"status": "not_attempted", "reason": "no correlated media"}
            take_screenshot(self.page, "download_failed", self.screenshots_dir)
            return None

        # Prefer the exact authenticated media endpoint.  If Flow does not
        # expose the bytes to page fetch, retain the scoped UI fallback below.
        direct_path = self.retrieve_exact_media(media_id, save_path)
        if direct_path:
            log(f"Retrieved exact correlated media {media_id} through authenticated Flow fetch")
            return direct_path

        tile = None
        tile_strategy = None
        tile_selectors = [
            f'[data-media-id="{media_id}"]',
            f'[data-media-name="{media_id}"]',
            f'[data-id="{media_id}"]',
            f'[data-tile-id="{media_id}"]',
            f'a[href*="{media_id}"]',
        ]
        for tile_selector in tile_selectors:
            try:
                candidate = self.page.locator(tile_selector).first
                if candidate.is_visible(timeout=1500):
                    tile = candidate
                    tile_strategy = tile_selector
                    break
            except Exception:
                continue
        if tile is None:
            try:
                video = self.page.locator(f'video[src*="{media_id}"]').first
                if video.is_visible(timeout=1500):
                    tile = video.locator('xpath=ancestor::*[@data-tile-id or @data-media-id][1]')
                    tile_strategy = "video-src-ancestor"
            except Exception:
                pass
        if tile is None:
            self._last_download_evidence = {
                "status": "not_attempted",
                "reason": "correlated media tile unavailable",
                "media_id": media_id,
                "tile_selectors_tried": tile_selectors,
            }
            take_screenshot(self.page, "download_failed", self.screenshots_dir)
            return None

        direct_download_selectors = [
            'button:has-text("Download")', 'a:has-text("Download")',
            '[aria-label*="Download"]', '[aria-label*="download"]',
            'button:has-text("Export")', 'a[download]',
        ]
        menu_button_selectors = [
            '[aria-label*="More"]', '[aria-label*="more"]',
            '[aria-label*="Options"]', '[aria-label*="options"]',
            '[data-testid*="menu"]', '[data-testid*="Menu"]',
            'button:has-text("more_vert")', 'button:has-text("More")',
        ]

        def save_download(element, selector: str) -> Optional[Path]:
            with self.page.expect_download(timeout=FLOW_SCENE_DOWNLOAD_TIMEOUT * 1000) as download_info:
                element.click()
            download = download_info.value
            save_path.parent.mkdir(parents=True, exist_ok=True)
            download.save_as(str(save_path))
            self._last_download_evidence = {
                "status": "downloaded",
                "media_id": media_id,
                "redirect_url": (self._correlated_asset or {}).get("redirect_url"),
                "tile_strategy": tile_strategy,
                "selector": selector,
            }
            log(f"Downloaded correlated media {media_id}: {save_path}")
            return save_path

        for selector in direct_download_selectors:
            try:
                element = tile.locator(selector).first
                if element.is_visible(timeout=2000):
                    return save_download(element, selector)
            except PlaywrightTimeout:
                continue
            except Exception as exc:
                log(f"Scoped direct download attempt failed ({selector}): {exc}")

        for selector in menu_button_selectors:
            try:
                menu_button = tile.locator(selector).first
                if not menu_button.is_visible(timeout=2000):
                    continue
                menu_button.click()
                menu_item = self.page.locator(
                    'button:has-text("Download"), a:has-text("Download"), '
                    '[role="menuitem"]:has-text("Download")'
                ).last
                if not menu_item.is_visible(timeout=3000):
                    continue
                return save_download(menu_item, f"{selector} -> Download")
            except PlaywrightTimeout:
                continue
            except Exception as exc:
                log(f"Scoped overflow-menu download attempt failed ({selector}): {exc}")

        self._last_download_evidence = {
            "status": "failed",
            "reason": "no correlated download control succeeded",
            "media_id": media_id,
            "tile_strategy": tile_strategy,
            "direct_selectors": direct_download_selectors,
            "menu_selectors": menu_button_selectors,
        }
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
