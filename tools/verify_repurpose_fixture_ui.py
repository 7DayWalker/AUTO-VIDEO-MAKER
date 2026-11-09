"""Headless happy-path UI using isolated synthetic accepted masters as fixtures.

No Core production writes, LLM/TTS/transcription, OAuth or uploads are permitted.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from playwright.sync_api import sync_playwright
    from app.repurpose.storage import load_render, load_unit_plan
    from app.repurpose.cache import sources_payload
    from app.web import server as web

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-report", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    synthetic = json.loads(args.synthetic_report.read_text(encoding="utf-8"))
    root = Path(synthetic["root"])
    productions, projects = root/"productions", root/"projects"
    pid, bid = "prd_20261109_repurpose_synthetic_001", "rpb-"+"f"*24
    args.report_dir.mkdir(parents=True, exist_ok=True)
    media, units, clips = {}, {}, []
    with patch.object(web, "PRODUCTIONS_DIR", productions), patch.object(web, "PROJECTS_DIR", projects):
        for case in synthetic["cases"]:
            uid = case["unit_id"]
            artifact = load_render(pid, uid, productions)
            production, unit, plan, clip = load_unit_plan(pid, uid, productions)
            detail = web.unit_detail_payload(pid, uid)[1]
            units[uid] = detail
            media[f"/api/productions/{pid}/units/{uid}/repurpose-video/media"] = productions/pid/artifact.output_path
            clips.append({**clip.model_dump(mode="json"), "mode": case["mode"], "status": "rendered",
                "project_id": case["project_id"], "render": artifact.model_dump(mode="json"),
                "media_url": f"/api/productions/{pid}/units/{uid}/repurpose-video/media"})
        source_response = sources_payload(pid, root=productions)
        production_response = web.production_detail_payload(pid)[1]
        production_response["units"] = [u for u in production_response["units"] if u["intent"]["unit_id"] in units]
    batch = {"production_id": pid, "batch_id": bid, "status": "rendered", "plan_hash": "f"*64,
        "llm_calls": 0, "uploads": 0, "stt_calls": 0, "vision_calls": 0, "clips": clips}
    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chrome")
        for width, height in ((1600, 1000), (430, 932)):
            context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
            context.add_init_script("localStorage.setItem('avm-studio-pro',JSON.stringify({state:{launchModalSeen:true,sidebarCollapsed:true},version:2}));")
            page = context.new_page()
            errors, writes, fixture_promotions = [], [], []
            page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))
            def fixture(route):
                request, path = route.request, urlsplit(route.request.url).path
                if request.method == "POST" and path.endswith("/repurpose-video/promote"):
                    uid = path.split("/")[-3]
                    fixture_promotions.append(uid)
                    detail = units[uid]
                    data = {"production_id": pid, "unit_id": uid, "project_id": detail["bound_project"]["project_id"],
                        "promoted": True, "bound_project": detail["bound_project"], "repurpose_video": detail["repurpose_video"]}
                elif request.method not in {"GET", "HEAD"}:
                    writes.append(path)
                    return route.fulfill(status=409, content_type="application/json", body='{"error":"NO_WRITES_IN_UI_FIXTURE"}')
                elif path in media:
                    binary = media[path].read_bytes()
                    size, left, right = len(binary), 0, len(binary)-1
                    rh = request.headers.get("range")
                    if rh and re.fullmatch(r"bytes=\d*-\d*", rh):
                        a,b = rh[6:].split("-")
                        left,right = (int(a),min(size-1,int(b)) if b else size-1) if a else (max(0,size-int(b)),size-1)
                    headers = {"Accept-Ranges": "bytes", "Content-Length": str(right-left+1)}
                    if rh: headers["Content-Range"] = f"bytes {left}-{right}/{size}"
                    return route.fulfill(status=206 if rh else 200, content_type="video/mp4", headers=headers, body=binary[left:right+1])
                elif path == "/api/productions":
                    data = [{"production_id": pid, "title": "Synthetic renderer UI fixture", "unit_count": 3, "sample": False}]
                elif path == f"/api/productions/{pid}/repurpose/sources": data = source_response
                elif path == f"/api/productions/{pid}/repurpose/batches/{bid}": data = batch
                elif path == f"/api/productions/{pid}": data = production_response
                elif path.startswith(f"/api/productions/{pid}/units/"):
                    uid = path.split("/")[5]
                    if path.endswith("/auto-publish"): data = {"production_id": pid, "unit_id": uid, "intent": None}
                    elif path.endswith("/audio"): data = {"production_id": pid, "unit_id": uid, "available": False, "sample": True}
                    else: data = units.get(uid, {})
                elif path == "/api/connections": data = {p: {"status": "NOT_CONNECTED"} for p in ("youtube","tiktok")}
                elif path.endswith("/readiness"): data = {"can_publish": False, "connected": False, "sample": True}
                else: return route.continue_()
                route.fulfill(content_type="application/json", body=json.dumps(data))
            page.route("**/api/**", fixture)
            page.goto("http://127.0.0.1:5173", wait_until="networkidle", timeout=60000)
            page.get_by_role("button", name="Repurpose Studio", exact=True).click()
            page.get_by_label("Existing production", exact=True).select_option(pid)
            page.get_by_label("Cached film", exact=True).select_option("src-001")
            field = page.get_by_label("Inspect an existing batch", exact=True)
            field.fill(bid); field.press("Tab")
            page.locator(".rp-clip").first.wait_for(timeout=45000)
            page.wait_for_function("document.querySelector('.rp-phone video')?.readyState >= 2", timeout=45000)
            assert page.locator(".rp-clip").count() == 3
            grid = page.locator(".rp-workflow").evaluate("e => getComputedStyle(e).gridTemplateColumns")
            assert len(grid.split()) == (2 if width > 850 else 1)
            page.screenshot(path=str(args.report_dir/f"synthetic-preview-{width}.png"), full_page=True)
            if width > 850:
                page.locator(".rp-clip").nth(2).get_by_role("button", name="Promote & Open Export").click()
                page.get_by_role("button", name="Publish to YouTube", exact=True).wait_for(timeout=45000)
                actual = page.get_by_label("Target unit").input_value()
                assert actual == clips[2]["unit_id"], actual
                assert page.get_by_role("button", name="Publish to YouTube", exact=True).is_disabled()
            assert not errors and not writes, {"errors":errors,"writes":writes}
            checks.append({"width":width,"grid":grid,"clip_count":3,"errors":errors,"writes":writes,
                "fixture_promotions":fixture_promotions,"export_unit":clips[2]["unit_id"] if width > 850 else None})
            context.close()
        browser.close()
    result = {"ok":True,"synthetic_only":True,"checks":checks}
    (args.report_dir/"report.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))


if __name__ == "__main__":
    main()