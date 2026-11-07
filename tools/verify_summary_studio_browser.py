"""Headless K3 creator-workflow verification; screenshots are written, never read.

Uses the installed Playwright/Chrome and local Core/Vite only. The operator's
movie is inspected read-only. Optional sandbox creates one isolated dry run,
never modifies a selected movie production or promotes/publishes anything.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-id", required=True)
    parser.add_argument("--preset", default="auto")
    parser.add_argument("--sandbox", action="store_true")
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report = {"checked_at": datetime.now().isoformat(), "production_id": args.production_id, "checks": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chrome")
        for name, width, height in (("desktop", 1440, 900), ("mobile", 430, 932)):
            context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
            context.add_init_script("localStorage.setItem('avm-studio-pro',JSON.stringify({state:{launchModalSeen:true},version:2}));")
            context.add_init_script("sessionStorage.setItem('avm-summary:recap-intent'," + json.dumps(json.dumps({
                "productionId": args.production_id, "summaryMode": "pro", "summarySelectionMode": "explicit",
                "modelId": "gpt-6.1-sol", "recapLength": args.preset, "customMinutes": "15",
            })) + ");")
            page = context.new_page()
            errors = []
            posts = []
            page.on("pageerror", lambda error: errors.append(type(error).__name__))
            page.on("request", lambda request: posts.append(request.url.split("5173")[-1]) if request.method == "POST" else None)
            page.goto("http://127.0.0.1:5173", wait_until="networkidle", timeout=45000)
            page.get_by_role("button", name="AI Summary", exact=True).click()
            page.wait_for_timeout(600)
            page.get_by_title("Collapse", exact=True).click()
            page.locator("[data-summary-movie-state]").wait_for(timeout=20000)
            page.wait_for_function("!document.querySelector('.summary-protection-note')?.textContent.includes('Checking')")
            assert page.locator("#summary-production").input_value() == args.production_id
            # Source/registry are independent reads. A loaded movie does not
            # imply the slower capability catalogue has populated this select.
            page.wait_for_function("document.querySelector('#summary-understanding-model')?.value === 'gpt-6.1-sol'", timeout=30000)
            assert page.locator("#summary-understanding-model").input_value() == "gpt-6.1-sol"
            assert "496.4 MB" in page.locator("[data-summary-movie-state]").inner_text()
            assert "Dialogue already saved" in page.locator("[data-summary-movie-state]").inner_text()
            assert page.locator("[data-summary-progress] > li").count() == 5
            assert not page.locator("[data-summary-model-table]").is_visible()
            assert not page.locator("#src-path").is_visible()
            assert not page.locator("#summary-mock-mode").is_visible()
            assert page.locator("#summary-create-from-movie").is_checked()
            duplicate_ids = page.evaluate("() => { const ids=[...document.querySelectorAll('[id]')].map(e=>e.id); return ids.filter((id,i)=>ids.indexOf(id)!==i); }")
            assert not duplicate_ids
            sizing = page.locator(".summary-studio").evaluate("e => ({width:e.clientWidth, scrollWidth:e.scrollWidth})")
            assert sizing["scrollWidth"] <= sizing["width"] + 1, sizing
            workspace = page.locator('[data-summary-workflow]').evaluate("""e => {
              const config=e.querySelector('[data-summary-configuration]').getBoundingClientRect();
              const cinema=e.querySelector('[data-summary-cinema]').getBoundingClientRect();
              const button=e.querySelector('#summary-autopilot');
              return {columns:getComputedStyle(e).gridTemplateColumns, config:{x:config.x,y:config.y,width:config.width},
                cinema:{x:cinema.x,y:cinema.y,width:cinema.width}, buttonBottom:button.getBoundingClientRect().bottom,
                gradient:getComputedStyle(button).backgroundImage};
            }""")
            assert "124, 58, 237" in workspace["gradient"] and "79, 70, 229" in workspace["gradient"]
            if name == "desktop":
                assert workspace["cinema"]["x"] > workspace["config"]["x"] + workspace["config"]["width"]
                assert abs(workspace["cinema"]["y"] - workspace["config"]["y"]) <= 2
                assert workspace["cinema"]["y"] < height / 2
                assert workspace["buttonBottom"] <= height, "Primary CTA must be visible beside the monitor without scrolling"
            assert page.locator('.summary-number').count() == 0
            status = page.locator("[data-summary-live-status]").inner_text()
            button = page.locator("#summary-autopilot").inner_text()
            preview = page.locator('video[aria-label="Narrated summary preview"]')
            playback = None
            if preview.count():
                preview.scroll_into_view_if_needed()
                page.wait_for_function("Number.isFinite(document.querySelector('video[aria-label=\"Narrated summary preview\"]').duration)", timeout=20000)
                preview.evaluate("async e => { e.muted=true; await e.play(); }")
                page.wait_for_function("document.querySelector('video[aria-label=\"Narrated summary preview\"]').currentTime > 0", timeout=15000)
                playback = preview.evaluate("e => { e.pause(); return {duration:e.duration,time:e.currentTime,readyState:e.readyState,error:e.error?.code ?? null}; }")
                assert playback["duration"] > 720 and playback["time"] > 0 and playback["error"] is None, playback
                page.locator(".summary-heading").scroll_into_view_if_needed()
            page.screenshot(path=str(args.report_dir / f"summary-{name}.png"), full_page=True)
            page.locator("[data-summary-advanced] > summary").click()
            assert page.locator("[data-summary-model-table]").is_visible()
            assert page.locator("#src-path").is_visible() and page.locator("#summary-mock-mode").is_visible()
            for label in ("Register source", "Transcribe", "Build narration", "Synthesize audio", "Assemble video"):
                assert page.get_by_role("button", name=label, exact=True).is_visible()
            page.locator("[data-summary-advanced] > summary").click()
            assert not posts, "Movie inspection must not run generation or publishing"
            assert not errors
            report["checks"].append({"viewport": name, "size": sizing, "duplicate_ids": duplicate_ids,
                                     "workspace": workspace,
                                     "button": button, "status": status, "read_only_posts": posts, "page_errors": errors,
                                     "advanced_collapsed": True, "legacy_controls_verified": True, "video_playback": playback})
            context.close()
        if args.sandbox:
            context = browser.new_context(viewport={"width": 1280, "height": 1000}, reduced_motion="reduce")
            context.add_init_script("localStorage.setItem('avm-studio-pro',JSON.stringify({state:{launchModalSeen:true},version:2}));")
            # Opt into sandbox BEFORE mount so a running real project cannot
            # capture/lock the test context during its initial evidence read.
            context.add_init_script("sessionStorage.setItem('avm-summary:mock-mode','true');")
            page = context.new_page()
            page.goto("http://127.0.0.1:5173", wait_until="networkidle", timeout=45000)
            page.get_by_role("button", name="AI Summary", exact=True).click()
            page.locator("#summary-production").wait_for()
            page.wait_for_timeout(700)
            page.locator("#summary-production").select_option("")
            page.locator("[data-summary-advanced] > summary").click()
            page.locator("#summary-mock-mode").check()
            page.locator("[data-summary-advanced] > summary").click()
            commands = []
            page.on("request", lambda request: commands.append(request.url.split("5173")[-1]) if request.method == "POST" else None)
            page.locator("#summary-autopilot").click()
            page.locator('video[aria-label="Narrated summary preview"]').wait_for(timeout=45000)
            assert page.locator('[data-summary-progress] > li[data-status="completed"]').count() == 5
            assert page.locator("#summary-autopilot").is_disabled()
            assert not any("publish" in command or "promote" in command for command in commands)
            assert len(commands) == 6, commands  # create, register, STT, script, voice, assemble
            report["sandbox"] = {"production_id": page.locator("#summary-production").input_value(),
                                 "commands": commands, "all_five_steps_saved": True, "published": False}
            page.screenshot(path=str(args.report_dir / "summary-sandbox-autopilot.png"), full_page=True)
            context.close()
        browser.close()
    report["ok"] = True
    (args.report_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())