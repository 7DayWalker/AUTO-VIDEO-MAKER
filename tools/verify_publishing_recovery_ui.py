"""Headless local UI checks; intercept publishing writes and never open OAuth.

Desktop/mobile Summary inspection uses the real completed recap. Export uses
fixture responses for a completed binding and disconnected/connected accounts,
so verification cannot promote, upload, generate or alter operator credentials.
Screenshots are written only, never read as text or sent to a provider.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-id", required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    args.report_dir.mkdir(parents=True, exist_ok=True)
    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chrome")
        for width, height in ((1440, 900), (430, 932)):
            context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
            context.add_init_script("localStorage.setItem('avm-studio-pro', JSON.stringify({state:{launchModalSeen:true,sidebarCollapsed:true},version:2}));")
            context.add_init_script("sessionStorage.setItem('avm-summary:recap-intent'," + json.dumps(json.dumps({
                "productionId": args.production_id, "summaryMode": "pro", "summarySelectionMode": "explicit",
                "modelId": "gpt-6.1-sol", "recapLength": "extended", "customMinutes": "15"})) + ");")
            page = context.new_page()
            errors, writes = [], []
            page.on("pageerror", lambda exc: errors.append(str(exc)[:160]))
            def prevent_writes(route):
                if route.request.method != "GET":
                    writes.append(route.request.url.split("5173")[-1])
                    route.fulfill(status=409, content_type="application/json", body='{"error":"UI_VERIFICATION_NO_WRITES"}')
                else:
                    route.continue_()
            page.route("**/api/**", prevent_writes)
            page.goto("http://127.0.0.1:5173", wait_until="networkidle", timeout=60000)
            page.get_by_role("button", name="AI Summary", exact=True).click()
            page.locator("[data-summary-movie-state]").wait_for(timeout=45000)
            page.locator('video[aria-label="Narrated summary preview"]').wait_for(timeout=45000)
            page.wait_for_function("document.querySelector('video')?.readyState >= 2", timeout=45000)
            grid = page.locator("[data-summary-workflow]").evaluate("element => getComputedStyle(element).gridTemplateColumns")
            assert (len(grid.split()) == 2 if width > 820 else len(grid.split()) == 1), grid
            assert page.locator("[data-summary-progress] > li").count() == 5
            assert page.locator("#summary-autopilot").is_disabled()
            assert not page.locator("[data-summary-advanced]").get_attribute("open")
            assert not writes and not errors, {"writes": writes, "errors": errors}
            media = page.locator("video").evaluate("element => ({duration:element.duration, error:element.error?.code ?? null})")
            assert 520 < media["duration"] < 530 and media["error"] is None
            page.screenshot(path=str(args.report_dir / f"summary-{width}.png"), full_page=True)
            checks.append({"view": "summary", "width": width, "grid": grid, "five_steps": True, "duration": media["duration"], "writes": writes, "errors": errors})
            context.close()
        for connected in (False, True):
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
            pid, uid, project = "prd_20261108_ui_fixture_001", "unit-001", "ui-fixture-project"
            context.add_init_script("localStorage.setItem('avm-studio-pro'," + json.dumps(json.dumps({"state": {
                "launchModalSeen": True, "view": "production", "sub": "export", "sidebarCollapsed": True,
                "workspace": {"id": pid, "selectedUnitId": uid, "detail": None, "loading": False, "artifact": None}}, "version": 2})) + ");")
            page = context.new_page()
            reads, writes, errors = [], [], []
            page.on("pageerror", lambda exc: errors.append(str(exc)[:160]))
            def fixture(route):
                request = route.request
                path = request.url.split("5173")[-1]
                if request.method != "GET":
                    writes.append(path)
                    return route.fulfill(status=409, content_type="application/json", body='{"error":"UI_VERIFICATION_NO_WRITES"}')
                reads.append(path)
                unit = {"production_id": pid, "unit_intent": {"unit_id": uid}, "intent": {"unit_id": uid, "title": "Fixture master"},
                    "execution": {"unit_id": uid, "project_id": project, "state": "COMPLETED"}, "bound_project": {"project_id": project,
                        "publications": [], "artifacts": {"final_video": {"state": "AVAILABLE", "available": True, "media_url": "/api/fixture-master", "duration": 30, "file_size": 1000}}}}
                if path == f"/api/productions/{pid}":
                    data = {"production_id": pid, "title": "Read-only publishing fixture", "units": [unit]}
                elif path == f"/api/productions/{pid}/units/{uid}":
                    data = unit
                elif path.endswith("/auto-publish"):
                    data = {"production_id": pid, "unit_id": uid, "enabled": False, "state": "disabled"}
                elif path == "/api/connections":
                    data = {platform: {"status": "CONNECTED" if connected else "ERROR", "account": "Fixture account" if connected else None,
                        "account_id": "fixture-account" if connected else None, "reason_code": None if connected else "INVALID_GRANT",
                        "reconnect_required": not connected, "detail": None if connected else "Stored credentials unusable; reconnect explicitly"} for platform in ("youtube", "tiktok")}
                elif path.endswith("/readiness"):
                    platform = "youtube" if "/youtube/" in path else "tiktok"
                    data = {"platform": platform, "project_id": project, "state": "NOT_STARTED", "connected": connected,
                        "can_publish": connected, "inspection_only": True, "reconnect_required": not connected}
                elif path.endswith("/audio"):
                    return route.fulfill(status=404, content_type="application/json", body='{"error":"NO_AUDIO_EVIDENCE"}')
                elif path == "/api/productions":
                    data = []
                else:
                    return route.continue_()
                route.fulfill(content_type="application/json", body=json.dumps(data))
            page.route("**/api/**", fixture)
            page.goto("http://127.0.0.1:5173", wait_until="networkidle", timeout=60000)
            # Persisted view restores identity but intentionally not evidence.
            refresh = page.get_by_role("button", name="Refresh", exact=True)
            if refresh.count():
                refresh.first.click()
            page.get_by_role("button", name="Export", exact=True).click()
            page.get_by_role("button", name="Publish to YouTube", exact=True).wait_for(timeout=45000)
            page.wait_for_function("!document.body.textContent.includes('Checking exact master and platform readiness')", timeout=45000)
            for platform in ("YouTube", "TikTok"):
                button = page.get_by_role("button", name=f"Publish to {platform}", exact=True)
                assert button.is_enabled() == connected
                if not connected:
                    assert page.get_by_role("button", name=f"Reconnect {platform}", exact=True).is_visible()
            assert not page.get_by_text("Connection not read", exact=True).count()
            assert any(path.endswith("/readiness") for path in reads)
            assert not writes and not errors, {"writes": writes, "errors": errors}
            page.screenshot(path=str(args.report_dir / f"export-{'connected' if connected else 'reconnect'}.png"), full_page=True)
            checks.append({"view": "export", "connected": connected, "automatic_readiness": True, "reads": reads, "writes": writes, "errors": errors})
            context.close()
        browser.close()
    report = {"ok": True, "checks": checks}
    (args.report_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())