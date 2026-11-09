"""Explicit live acceptance: one cached-source plan, bounded renders, no uploads.

Re-running uses a stable request ID and persisted batch; it never plans again.
Invoke with --live to authorize the one provider dispatch and local media jobs.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def api(path, body=None):
    request = Request("http://127.0.0.1:8787/api"+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=120) as response:
        return json.loads(response.read())


def wait(production_id, batch_id):
    deadline = time.monotonic()+3600
    while time.monotonic() < deadline:
        job = api(f"/productions/{production_id}/repurpose/batches/{batch_id}")
        if job["status"] not in {"planning", "rendering"}:
            return job
        time.sleep(3)
    raise RuntimeError("Acceptance timeout. Inspect the persisted batch; no planner was retried.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-id", default="prd_20261107_the_last_dragon_queen_part_i_ai_short_fi_057")
    parser.add_argument("--request-id", default="track8-dragon-live-acceptance-v1")
    parser.add_argument("--report", type=Path, default=Path(__file__).resolve().parents[1]/"temp/track8_live_acceptance.json")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required to authorize planning and rendering")
    pid = args.production_id
    sources = api(f"/productions/{pid}/repurpose/sources")["sources"]
    source = next(s for s in sources if s["asset_id"] == "src-001" and s["ready"])
    accepted = api(f"/productions/{pid}/repurpose/batches", {"request_id": args.request_id, "confirm": True,
        "source_asset_id": "src-001", "source_unit_id": "unit-001", "evidence_revision": source["evidence_revision"],
        "mode": "narrated", "count": 3, "target_seconds": 45, "framing": "cinema_canvas",
        "model_id": "gpt-6.1-sol", "cache_only": True, "allow_overlap": False})
    batch_id = accepted["batch_id"]
    print("Acceptance batch", batch_id, flush=True)
    job = wait(pid, batch_id)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    if job["status"] in {"failed", "uncertain", "interrupted"}:
        print(json.dumps({"batch_id": batch_id, "status": job["status"], "error": job.get("error"),
            "llm_calls": job["llm_calls"]}, indent=2), flush=True)
        return 1
    assert all(job[k] == value for k, value in {"llm_calls": 1, "uploads": 0, "stt_calls": 0, "vision_calls": 0}.items())
    missing = [c["unit_id"] for c in job["clips"] if not c.get("render")]
    if missing:
        api(f"/productions/{pid}/repurpose/batches/{batch_id}/render", {"request_id": "accept-render-"+str(time.time_ns()),
            "confirm": True, "expected_plan_hash": job["plan_hash"], "unit_ids": missing})
        job = wait(pid, batch_id)
    rows = []
    for clip in job.get("clips", []):
        artifact = clip.get("render")
        if artifact is None:
            continue
        promotion = api(f"/productions/{pid}/units/{clip['unit_id']}/repurpose-video/promote", {"confirm": True,
            "expected_plan_hash": artifact["plan_hash"], "expected_file_fingerprint": artifact["file_fingerprint"]})
        rows.append({"unit_id": clip["unit_id"], "title": clip["title"], "hook": clip["hook"], "cuts": len(clip["cuts"]),
            "duration_seconds": artifact["duration_seconds"], "width": artifact["width"], "height": artifact["height"],
            "loudness": artifact["loudness"], "decode_errors": artifact["validation"]["decode_errors"],
            "media_url": "http://127.0.0.1:8787"+clip["media_url"], "project_id": promotion["project_id"]})
    report = {"ok": len(rows) == 3, "production_id": pid, "batch_id": batch_id,
        "llm_calls": job["llm_calls"], "uploads": job["uploads"], "stt_calls": job["stt_calls"], "vision_calls": job["vision_calls"],
        "clips": rows, "units": job["units"], "published": False}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())