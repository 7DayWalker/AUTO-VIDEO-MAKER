"""
Google Flow Automation - Browser Management

Architecture:
1. Chrome is launched MANUALLY (or via a helper script) with:
   - A dedicated automation profile (separate from user's normal profile)
   - Remote debugging enabled on a local-only port
2. The user manually authenticates with Google in that Chrome window.
3. Playwright attaches to the already-running Chrome via Chrome DevTools Protocol (CDP).
4. Playwright can then navigate and interact with Flow using the authenticated session.

IMPORTANT:
- The user's normal Chrome profile is NEVER used.
- Google authentication is performed MANUALLY by the user.
- No passwords, cookies, or tokens are stored or extracted.
- The debugging port is bound to localhost only.
"""

import subprocess
import time
import json
import socket
import urllib.request
import os
from pathlib import Path
from datetime import datetime
from typing import Optional

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

from app.flow.config import (
    BROWSER_PROFILE_DIR,
    CHROME_EXECUTABLE,
    NAVIGATION_TIMEOUT,
    CDP_PORT,
)


def log(message: str) -> None:
    """Print a timestamped log message."""
    from datetime import datetime
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[FLOW {timestamp}] {message}")


def ensure_browser_profile_dir() -> Path:
    """
    Ensure the dedicated browser profile directory exists.

    Returns:
        Path to the browser profile directory.
    """
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return BROWSER_PROFILE_DIR


def get_chrome_launch_command() -> list[str]:
    """
    Build the command to launch Chrome with remote debugging and dedicated profile.

    Returns:
        List of command-line arguments for subprocess.
    """
    profile_dir = ensure_browser_profile_dir()

    cmd = [
        CHROME_EXECUTABLE,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={profile_dir}",
        # Bind debugging to localhost only for security
        "--remote-allow-origins=http://localhost:*",
        # Don't show first-run dialogs
        "--no-first-run",
        "--no-default-browser-check",
        # Start with a useful page
        "https://flow.google",
    ]
    return cmd


class ChromeLifecycleError(RuntimeError):
    """A dedicated Chrome lifecycle transition could not be proven safe."""

    def __init__(self, classification: str, message: str, diagnostics: dict = None):
        super().__init__(message)
        self.classification = classification
        self.diagnostics = diagnostics or {}


_last_lifecycle_diagnostics: dict = {}


def get_last_lifecycle_diagnostics() -> dict:
    """Return a copy of the most recent launch/restart evidence."""
    return json.loads(json.dumps(_last_lifecycle_diagnostics, default=str))


def _profile_token() -> str:
    return os.path.normcase(str(BROWSER_PROFILE_DIR)).replace("/", "\\").rstrip("\\")


def _chrome_process_snapshot() -> list[dict]:
    """Inspect Chrome processes without assuming the Popen PID is long-lived."""
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=8, check=False,
        )
        raw = result.stdout.strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        return [
            {
                "pid": int(row["ProcessId"]),
                "ppid": int(row.get("ParentProcessId") or 0),
                "command_line": row.get("CommandLine") or "",
            }
            for row in data
            if str(row.get("ProcessId", "")).isdigit()
        ]
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def _dedicated_process_snapshot(snapshot: list[dict] = None) -> list[dict]:
    """Return only processes belonging to this automation profile."""
    token = _profile_token().lower()
    port_token = f"--remote-debugging-port={CDP_PORT}".lower()
    return [
        row for row in (snapshot if snapshot is not None else _chrome_process_snapshot())
        if token in row.get("command_line", "").replace("/", "\\").lower()
        and ("--user-data-dir=" in row.get("command_line", "").lower())
    ]


def _dedicated_root_and_tree(snapshot: list[dict]) -> tuple[Optional[dict], list[dict]]:
    """Find a root with the debug flag, then include its profile descendants."""
    dedicated = _dedicated_process_snapshot(snapshot)
    port_token = f"--remote-debugging-port={CDP_PORT}".lower()
    roots = [row for row in dedicated if port_token in row["command_line"].lower()]
    root = next((row for row in roots if row["ppid"] not in {item["pid"] for item in dedicated}), None)
    if root is None and roots:
        root = roots[0]
    if root is None:
        return None, dedicated
    tree = {root["pid"]: root}
    changed = True
    while changed:
        changed = False
        for row in dedicated:
            if row["pid"] not in tree and row["ppid"] in tree:
                tree[row["pid"]] = row
                changed = True
    return root, list(tree.values())


def _port_owner() -> dict:
    """Return TCP 9222 state and owner PID when Windows exposes it."""
    result = {"port": CDP_PORT, "listening": False, "owner_pid": None, "error": None}
    try:
        command = (
            f"Get-NetTCPConnection -LocalPort {CDP_PORT} -State Listen -ErrorAction SilentlyContinue | "
            "Select-Object -First 1 OwningProcess | ConvertTo-Json -Compress"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if completed.stdout.strip():
            data = json.loads(completed.stdout)
            owner = data.get("OwningProcess") if isinstance(data, dict) else None
            result["owner_pid"] = int(owner) if str(owner).isdigit() else None
            result["listening"] = result["owner_pid"] is not None
            return result
    except Exception as exc:
        result["error"] = str(exc)
    try:
        with socket.create_connection(("127.0.0.1", CDP_PORT), timeout=1):
            result["listening"] = True
    except OSError:
        pass
    return result


def _port_belongs_to_tree(port: dict, tree: list[dict]) -> bool:
    """Check the listener owner when Windows exposes an owner PID.

    Some Windows environments cannot expose the owner through the cmdlet. In
    that case CDP readiness remains usable evidence, but an explicitly wrong
    owner is never accepted.
    """
    owner = port.get("owner_pid")
    return owner is None or owner in {row["pid"] for row in tree}


def _lifecycle_snapshot(process=None, phase: str = "") -> dict:
    """Collect compact, serializable process/CDP evidence."""
    snapshot = _chrome_process_snapshot()
    root, tree = _dedicated_root_and_tree(snapshot)
    port = _port_owner()
    cdp = cdp_endpoint_diagnostics()
    return {
        "timestamp": datetime.now().isoformat(),
        "phase": phase,
        "profile": str(BROWSER_PROFILE_DIR),
        "cdp_port": CDP_PORT,
        "popen_pid": getattr(process, "pid", None),
        "popen_poll": process.poll() if process is not None else None,
        "popen_returncode": getattr(process, "returncode", None),
        "dedicated_root": root,
        "dedicated_processes": tree,
        "port": port,
        "cdp": cdp,
    }


def _startup_ready(evidence: dict) -> bool:
    """Accept Chrome's multiprocess startup only with profile/CDP evidence."""
    root = evidence.get("dedicated_root")
    tree = evidence.get("dedicated_processes", [])
    cdp = evidence.get("cdp", {})
    port = evidence.get("port", {})
    return bool(
        root
        and tree
        and port.get("listening")
        and cdp.get("available")
        and _port_belongs_to_tree(port, tree)
    )


def _wait_for_shutdown(previous_pids: set[int], timeout: int) -> tuple[bool, dict]:
    """Wait for the old tree and both CDP signals to disappear."""
    deadline = time.time() + timeout
    evidence = _lifecycle_snapshot(phase="shutdown_wait")
    while time.time() < deadline:
        current = _lifecycle_snapshot(phase="shutdown_wait")
        current_pids = {row["pid"] for row in current["dedicated_processes"]}
        if not (previous_pids & current_pids) and not current["port"].get("listening") and not current["cdp"].get("available"):
            return True, current
        evidence = current
        time.sleep(0.25)
    return False, evidence


def _record_lifecycle(data: dict) -> None:
    global _last_lifecycle_diagnostics
    _last_lifecycle_diagnostics = data


def launch_chrome_with_debugging(startup_timeout: int = 20) -> subprocess.Popen:
    """
    Launch Chrome with remote debugging enabled and the dedicated profile.

    The user should manually authenticate with Google in this Chrome window.
    After authentication, Playwright can attach to this session.

    Returns:
        The subprocess.Popen object for the Chrome process.
    """
    cmd = get_chrome_launch_command()
    log(f"Launching Chrome with remote debugging on port {CDP_PORT}...")
    log(f"Chrome: {CHROME_EXECUTABLE}")
    log(f"Profile: {BROWSER_PROFILE_DIR}")
    log(f"CDP Port: {CDP_PORT} (localhost only)")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    diagnostics = {
        "timestamp": datetime.now().isoformat(),
        "phase": "launch",
        "profile": str(BROWSER_PROFILE_DIR),
        "cdp_port": CDP_PORT,
        "launch_command": cmd,
        "popen_pid": process.pid,
        "startup_history": [],
        "launcher_exited_normally": False,
        "classification": None,
    }
    deadline = time.time() + startup_timeout
    while time.time() < deadline:
        evidence = _lifecycle_snapshot(process, phase="launch_poll")
        diagnostics["startup_history"].append(evidence)
        if process.poll() == 0 or process.returncode == 0:
            diagnostics["launcher_exited_normally"] = True
        if _startup_ready(evidence):
            diagnostics["final"] = evidence
            diagnostics["classification"] = "ready"
            _record_lifecycle(diagnostics)
            log("Chrome/CDP launched successfully (dedicated profile verified).")
            return process
        time.sleep(0.25)

    final = diagnostics["startup_history"][-1] if diagnostics["startup_history"] else _lifecycle_snapshot(process, phase="launch_timeout")
    diagnostics["final"] = final
    # Prefer the explicit normal-exit evidence, including the final process
    # snapshot.  A launcher may have exited cleanly while Chrome itself was
    # never exposed through CDP; that is a distinct failure from a crash.
    if (
        diagnostics["launcher_exited_normally"]
        or final.get("popen_poll") == 0
        or final.get("popen_returncode") == 0
    ):
        classification = "launch_returned_zero_cdp_unavailable"
    elif process.poll() is not None:
        classification = "chrome_process_exited"
    elif final.get("cdp", {}).get("available"):
        classification = "cdp_wrong_profile_owner"
    elif not final.get("dedicated_root"):
        classification = "dedicated_process_not_found"
    elif not final.get("port", {}).get("listening"):
        classification = "cdp_port_not_open"
    else:
        classification = "cdp_endpoint_unreachable"
    diagnostics["classification"] = classification
    _record_lifecycle(diagnostics)
    raise ChromeLifecycleError(
        classification,
        f"Chrome launch did not become CDP-ready: {classification}; command: {' '.join(cmd)}",
        diagnostics,
    )


def connect_to_chrome(playwright) -> Browser:
    """
    Connect Playwright to an already-running Chrome instance via CDP.

    Chrome must already be running with --remote-debugging-port.

    Args:
        playwright: The Playwright instance from sync_playwright().

    Returns:
        A connected Browser instance.
    """
    cdp_url = f"http://localhost:{CDP_PORT}"
    log(f"Connecting to Chrome via CDP: {cdp_url}")

    browser = playwright.chromium.connect_over_cdp(cdp_url)
    log("Successfully connected to Chrome via CDP.")
    return browser


def enumerate_browser_pages(browser: Browser) -> list[dict]:
    """Return safe metadata for every attached page/tab."""
    pages = []
    for context_index, context in enumerate(browser.contexts):
        for page_index, page in enumerate(context.pages):
            try:
                title = page.title()
            except Exception:
                title = None
            pages.append({
                "context_index": context_index,
                "page_index": page_index,
                "url": page.url,
                "title": title,
                "page": page,
            })
    return pages


def get_page_from_browser(browser: Browser, expected_url: str = None) -> Page:
    """
    Get an active page from the connected browser.

    If there are existing contexts/pages, use the first one.
    Otherwise, create a new page.

    Args:
        browser: The connected Browser instance.

    Returns:
        A Page instance.
    """
    pages = enumerate_browser_pages(browser)
    if pages:
        if expected_url:
            expected_path = expected_url.rstrip("/").lower()
            exact = [p for p in pages if p["url"].rstrip("/").lower() == expected_path]
            if exact:
                log(f"Selected exact Flow project tab: {exact[0]['url']}")
                return exact[0]["page"]
        flow_pages = [p for p in pages if "flow.google" in p["url"].lower() or "labs.google" in p["url"].lower()]
        if flow_pages:
            log(f"Selected Flow tab from {len(pages)} candidates: {flow_pages[0]['url']}")
            return flow_pages[0]["page"]
        log(f"Selected fallback tab from {len(pages)} candidates: {pages[0]['url']}")
        return pages[0]["page"]

    contexts = browser.contexts
    if contexts:
        return contexts[0].new_page()

    # Fallback: create new context
    context = browser.new_context()
    return context.new_page()


def take_screenshot(page: Page, name: str, screenshots_dir: Path, timeout: int = 5) -> Path | None:
    """
    Take a screenshot and save it to the screenshots directory.

    Args:
        page: The Playwright page.
        name: Descriptive name for the screenshot (no extension).
        screenshots_dir: Directory to save screenshots.

    Returns:
        Path to the saved screenshot, or None if it failed.
    """
    try:
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = screenshots_dir / f"{name}_{timestamp}.png"
        page.screenshot(path=str(filepath), timeout=timeout * 1000, animations="disabled")
        log(f"Screenshot saved: {filepath}")
        return filepath
    except Exception as e:
        log(f"Failed to take screenshot: {e}")
        return None


def is_chrome_running_with_debugging() -> bool:
    """
    Check if Chrome is already running with the debugging port open.

    Returns:
        True if a connection can be made to the CDP port.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            result = s.connect_ex(("localhost", CDP_PORT))
            return result == 0
    except Exception:
        return False


def cdp_endpoint_diagnostics() -> dict:
    """Probe the CDP HTTP endpoint without attaching Playwright."""
    endpoint = f"http://localhost:{CDP_PORT}/json/version"
    result = {"endpoint": endpoint, "available": False, "error": None, "version": None}
    try:
        with urllib.request.urlopen(endpoint, timeout=2) as response:
            result["version"] = json.loads(response.read().decode("utf-8"))
            result["available"] = True
    except Exception as exc:
        result["error"] = str(exc)
    return result


def wait_for_cdp(available: bool, timeout: int = 10) -> bool:
    """Wait for CDP to become available/unavailable."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = cdp_endpoint_diagnostics()["available"]
        if current == available:
            return True
        time.sleep(0.25)
    return cdp_endpoint_diagnostics()["available"] == available


def restart_dedicated_chrome(timeout: int = 15) -> subprocess.Popen:
    """Restart only the verified dedicated Chrome process tree."""
    before = _lifecycle_snapshot(phase="before_shutdown")
    root = before.get("dedicated_root")
    tree = before.get("dedicated_processes", [])
    previous_pids = {row["pid"] for row in tree}
    diagnostics = {
        "timestamp": datetime.now().isoformat(),
        "phase": "restart",
        "profile": str(BROWSER_PROFILE_DIR),
        "cdp_port": CDP_PORT,
        "before_shutdown": before,
        "shutdown_history": [],
        "launch_command": get_chrome_launch_command(),
        "classification": None,
    }
    if not root or not previous_pids:
        diagnostics["classification"] = "dedicated_process_not_found"
        _record_lifecycle(diagnostics)
        raise ChromeLifecycleError(
            "dedicated_process_not_found",
            "Dedicated Chrome process cannot be identified safely; refusing uncontrolled restart",
            diagnostics,
        )

    result = subprocess.run(
        ["taskkill", "/PID", str(root["pid"]), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    diagnostics["shutdown_history"].append({
        "root_pid": root["pid"],
        "tree_pids": sorted(previous_pids),
        "taskkill_returncode": result.returncode,
        "taskkill_stdout": result.stdout[-500:],
        "taskkill_stderr": result.stderr[-500:],
    })
    stopped, shutdown_evidence = _wait_for_shutdown(previous_pids, timeout)
    diagnostics["shutdown_evidence"] = shutdown_evidence
    if not stopped:
        current_pids = {row["pid"] for row in shutdown_evidence.get("dedicated_processes", [])}
        classification = "old_dedicated_process_alive" if previous_pids & current_pids else "shutdown_timeout"
        diagnostics["classification"] = classification
        _record_lifecycle(diagnostics)
        raise ChromeLifecycleError(
            classification,
            f"Dedicated Chrome shutdown was not proven complete: {classification}",
            diagnostics,
        )

    launched = launch_chrome_with_debugging(startup_timeout=timeout)
    diagnostics["launch"] = get_last_lifecycle_diagnostics()
    final = diagnostics["launch"].get("final", {})
    if not _startup_ready(final):
        diagnostics["classification"] = diagnostics["launch"].get("classification", "cdp_endpoint_unreachable")
        _record_lifecycle(diagnostics)
        raise ChromeLifecycleError(
            diagnostics["classification"],
            "Dedicated Chrome restart did not produce a verified CDP endpoint",
            diagnostics,
        )
    diagnostics["classification"] = "ready"
    diagnostics["final"] = final
    _record_lifecycle(diagnostics)
    return launched
