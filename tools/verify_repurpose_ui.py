"""Headless K3 Repurpose/Export verification. Never publishes or runs generation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance-report", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.acceptance_report.read_text(encoding="utf-8"))
    args.report_dir.mkdir(parents=True, exist_ok=True)
    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chrome")
        for width, height in ((1600, 1000), (430, 932)):
            context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
            context.add_init_script("localStorage.setItem('avm-studio-pro',JSON.stringify({state:{launchModalSeen:true,sidebarCollapsed:true},version:2}));")
            page = context.new_page()
            errors, writes = [], []
            page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))
            def readonly(route):
                if route.request.method not in {"GET", "HEAD"}:
                    writes.append(route.request.url)
                    route.fulfill(status=409, content_type="application/json", body='{"error":"UI_VERIFICATION_NO_WRITES"}')
                else:
                    route.continue_()
            page.route("**/api/**", readonly)
            page.goto("http://127.0.0.1:5173", wait_until="networkidle", timeout=60000)
            page.get_by_role("button", name="Repurpose Studio", exact=True).click()
            page.locator("[data-repurpose-studio]").wait_for()
            page.get_by_label("Existing production", exact=True).select_option(report["production_id"])
            page.get_by_label("Cached film", exact=True).select_option("src-001")
            field = page.get_by_label("Inspect an existing batch", exact=True)
            field.fill(report["batch_id"]); field.press("Tab")
            if not report.get("ok"):
                page.locator(".rp-status").filter(has_text="failed").wait_for(timeout=45000)
                grid = page.locator(".rp-workflow").evaluate("element => getComputedStyle(element).gridTemplateColumns")
                assert len(grid.split()) == (2 if width > 850 else 1), grid
                assert page.locator(".rp-clip").count() == 0
                assert page.locator(".rp-error").count() > 0
                assert not errors and not writes, {"errors": errors, "writes": writes}
                page.screenshot(path=str(args.report_dir/f"repurpose-blocked-{width}.png"), full_page=True)
                checks.append({"width": width, "grid": grid, "live_planning_blocked": True,
                    "clips": 0, "error_visible": True, "errors": errors, "writes": writes})
                context.close()
                continue
            page.locator(".rp-clip").first.wait_for(timeout=45000)
            page.wait_for_function("document.querySelector('.rp-phone video')?.readyState >= 2", timeout=45000)
            grid = page.locator(".rp-workflow").evaluate("element => getComputedStyle(element).gridTemplateColumns")
            assert len(grid.split()) == (2 if width > 850 else 1), grid
            assert page.locator(".rp-clip").count() == 3
            measured = page.locator(".rp-phone video").evaluate("v => ({duration:v.duration,error:v.error?.code ?? null})")
            assert 30 <= measured["duration"] <= 60.1 and measured["error"] is None
            assert not errors and not writes, {"errors": errors, "writes": writes}
            page.screenshot(path=str(args.report_dir/f"repurpose-{width}.png"), full_page=True)
            checks.append({"width": width, "grid": grid, "clips": 3, "video": measured, "errors": errors, "writes": writes})
            if width > 850:
                # The already accepted promotion is replayed as a READ fixture;
                # the button must open the exact third short, never publish it.
                selected = report["clips"][2]
                pid, uid, project = report["production_id"], selected["unit_id"], selected["project_id"]
                def accepted_promotion(route):
                    route.fulfill(content_type="application/json", body=json.dumps({"production_id": pid, "unit_id": uid,
                        "project_id": project, "promoted": True, "bound_project": {"production_binding": {
                            "production_id": pid, "unit_id": uid, "project_id": project, "handoff_kind": "repurpose_short"}}}))
                page.route(f"**/api/productions/{pid}/units/{uid}/repurpose-video/promote", accepted_promotion)
                page.locator(".rp-clip").nth(2).get_by_role("button", name="Promote & Open Export").click()
                page.get_by_role("button", name="Publish to YouTube", exact=True).wait_for(timeout=45000)
                selected_unit = page.get_by_label("Target unit", exact=True).input_value()
                assert selected_unit == uid, selected_unit
                checks.append({"export_unit": uid, "project_id": project, "published": False})
            context.close()
        browser.close()
    result = {"ok": True, "checks": checks}
    (args.report_dir/"report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()