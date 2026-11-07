"""Explicit live resume with protected chapters and safe counts-only reporting.

Never promotes/publishes. The caller must choose the existing production and
minimum number of completed chapters; the server refuses their regeneration.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-id", required=True)
    parser.add_argument("--unit-id", default="unit-001")
    parser.add_argument("--asset-id", default="src-001")
    parser.add_argument("--preset", default="blockbuster")
    parser.add_argument("--mode", choices=("standard", "pro"), default="pro")
    parser.add_argument("--model-id", default="gpt-6.1-sol")
    parser.add_argument("--minimum-cached-chapters", type=int, required=True)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError("Use a new report path; existing reports are immutable")
    report = {"production_id": args.production_id, "unit_id": args.unit_id, "preset": args.preset,
        "mode": args.mode, "minimum_cached_chapters": args.minimum_cached_chapters,
        "started_at": datetime.now(timezone.utc).isoformat(), "steps": [], "published": False}

    def persist():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")

    def http(path, body=None):
        request = urllib.request.Request("http://127.0.0.1:8787" + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Accept": "application/json", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=14400) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    plan = ROOT / "productions" / args.production_id / "units" / args.unit_id / "plan"
    checkpoint = plan / "narration_chapters.partial.json"
    original = json.loads(checkpoint.read_text(encoding="utf-8"))
    protected = original.get("chapters", [])[:args.minimum_cached_chapters]
    if len(protected) != args.minimum_cached_chapters:
        raise ValueError("Required protected chapters are not present")
    report["protected_chapter_hashes"] = [hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest() for c in protected]
    unit = f"/api/productions/{args.production_id}/units/{args.unit_id}"
    persist()
    status, progress = http(unit + "/summary-progress")
    if status != 200 or progress.get("running") is not False:
        raise RuntimeError("Core already running or unavailable; no duplicate command was sent")
    status, existing = http(unit + "/narration")
    matches = (status == 200 and existing.get("summary_mode") == args.mode
        and existing.get("target_length_preset") == args.preset
        and existing.get("summary_understanding_model_id") == args.model_id
        and existing.get("source_asset_id") == args.asset_id and existing.get("status") == "grounded")
    commands = [] if matches else [("narration", "/narration", {"summary_mode": args.mode,
        "target_length_preset": args.preset, "selection_mode": "explicit",
        "summary_understanding_model_id": args.model_id, "asset_id": args.asset_id,
        "minimum_cached_chapters": args.minimum_cached_chapters, "mock_mode": False})]
    if args.full:
        if not matches or existing.get("audio_status") != "completed":
            commands.append(("voiceover", "/narration/synthesize", {"format": "mp3", "mock_mode": False}))
        status, video = http(unit + "/summary-video")
        current_video = False
        if matches and status == 200:
            from app.production.models import NarrationArtifact
            from app.production.summary_video import narration_content_hash
            current_video = (video.get("source_asset_id") == args.asset_id
                and video.get("narration_content_hash") == narration_content_hash(NarrationArtifact.model_validate(existing)))
        if not current_video:
            commands.append(("assembly", "/summary-video", {"mock_mode": False}))
    for name, suffix, body in commands:
        print("LIVE_STEP_STARTED", name, flush=True)
        started = time.monotonic()
        try:
            status, data = http(unit + suffix, body)
        except Exception as error:
            report.update(ok=False, transport_error=type(error).__name__)
            persist()
            return 1  # Read progress before any new command, never blindly retry.
        entry = {"step": name, "http_status": status, "elapsed_seconds": round(time.monotonic() - started, 3)}
        report["steps"].append(entry)
        if status not in (200, 201):
            from app.web.server import _browser_safe_diagnostic
            entry.update(error=data.get("error"), message=_browser_safe_diagnostic(data.get("message", "Core refused")))
            report["ok"] = False
            persist()
            print("LIVE_STEP_FAILED", json.dumps(entry), flush=True)
            return 1
        if name == "narration":
            after = json.loads(checkpoint.read_text(encoding="utf-8"))
            if after["chapters"][:len(protected)] != protected:
                raise RuntimeError("Protected chapters changed; refusing downstream processing")
            entry.update(chapters=data.get("chapter_count"), beats=len(data.get("beats", [])),
                words=len(data.get("narration_text", "").split()), protected_chapters_unchanged=True)
        elif name == "voiceover":
            entry.update({key: data.get("audio", {}).get(key) for key in ("duration_seconds", "status", "file_size")})
        else:
            entry.update({key: data.get(key) for key in ("output_duration_seconds", "file_size", "file_fingerprint", "media_url")})
        persist()
        print("LIVE_STEP_COMPLETED", json.dumps(entry), flush=True)
    report.update(ok=True, completed_at=datetime.now(timezone.utc).isoformat())
    persist()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())