"""Explicit live recap verification against an already transcribed production.

Local HTTP only; Core owns provider calls. Reports identities/counts, never
credentials or complete provider/artifact bodies. Does not promote/publish.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-id", required=True)
    parser.add_argument("--unit-id", default="unit-001")
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--model-id", default="gpt-6.1-sol")
    parser.add_argument("--mode", choices=("standard", "pro"), default="pro")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Keep a matching successful script; run only missing voiceover/assembly")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError("Refusing to replace an existing live verification report")
    report = {"production_id": args.production_id, "unit_id": args.unit_id, "asset_id": args.asset_id,
              "mode": args.mode, "model_id": args.model_id, "started_at": datetime.now().isoformat(), "steps": []}

    def persist():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")

    def http(path, body=None, timeout=30):
        request = urllib.request.Request("http://127.0.0.1:8787" + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Accept": "application/json", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    persist()
    status, runtime = http("/api/summary/runtime")
    if status != 200 or not runtime.get("ok"):
        raise RuntimeError("Live Core imports have not been verified")
    report["runtime"] = runtime
    base = f"/api/productions/{args.production_id}"
    status, production = http(base)
    source = next((asset for asset in production.get("source_assets", []) if asset["asset_id"] == args.asset_id), None)
    status, transcript = http(f"{base}/source/{args.asset_id}/transcript")
    if source is None or status != 200 or not transcript.get("segments") or transcript.get("provider") == "mock":
        raise RuntimeError("Exact registered movie/completed real transcript is required")
    report["source"] = {key: source.get(key) for key in ("asset_id", "sha256", "file_size", "duration")}
    report["transcript"] = {"provider": transcript["provider"], "segment_count": len(transcript["segments"])}
    persist()
    unit = f"{base}/units/{args.unit_id}"
    # Refuse to overwrite a prior recap during a live diagnostic.
    status, existing = http(unit + "/narration")
    commands = []
    if args.resume:
        if (status != 200 or existing.get("status") != "grounded" or existing.get("production_id") != args.production_id
                or existing.get("unit_id") != args.unit_id or existing.get("source_asset_id") != args.asset_id
                or existing.get("summary_mode") != args.mode or existing.get("summary_understanding_model_id") != args.model_id):
            raise RuntimeError("A matching completed script is required; resume never rewrites narration")
        report["narration_reused"] = {"narration_id": existing["narration_id"], "chapter_count": existing.get("chapter_count"),
                                      "beat_count": len(existing.get("beats", [])), "word_count": len(existing.get("narration_text", "").split())}
        status, progress = http(unit + "/summary-progress")
        if status != 200 or progress.get("running") is not False:
            raise RuntimeError("Inspect active processing before resuming; no duplicate command was sent")
    elif status != 404:
        raise RuntimeError("Narration already exists or its state is unknown; inspect before running another live build")
    else:
        commands = [("narration", unit + "/narration", {"summary_mode": args.mode, "target_length_preset": "auto",
            "selection_mode": "explicit", "summary_understanding_model_id": args.model_id,
            "mock_mode": False, "asset_id": args.asset_id}, 1830)]
    if args.full:
        if not (args.resume and existing.get("audio_status") == "completed"):
            commands += [("voiceover", unit + "/narration/synthesize", {"format": "mp3", "mock_mode": False}, 1830)]
        status, video = http(unit + "/summary-video")
        if status not in (200, 404):
            raise RuntimeError("Assembly state could not be read; refusing an uninspected render")
        if status == 404:
            commands += [("assembly", unit + "/summary-video", {"mock_mode": False}, 1200)]
        else:
            report["video_already_exists"] = True
    persist()
    for name, path, body, timeout in commands:
        start = time.monotonic()
        print("LIVE_STEP_STARTED", name, flush=True)
        status, data = http(path, body, timeout=timeout)
        result = {"step": name, "http_status": status, "elapsed_seconds": round(time.monotonic() - start, 2)}
        if status not in (200, 201):
            from app.web.server import _browser_safe_diagnostic
            result["error_code"] = data.get("error")
            result["message"] = _browser_safe_diagnostic(data.get("message", "Core refused this step"))
            report["steps"].append(result)
            report["ok"] = False
            persist()
            print("LIVE_STEP_FAILED", json.dumps(result), flush=True)
            return 1
        if name == "narration":
            result.update({"narration_id": data.get("narration_id"), "status": data.get("status"),
                "chapter_count": data.get("chapter_count"), "beat_count": len(data.get("beats", [])),
                "word_count": len(data.get("narration_text", "").split()),
                "target_duration_seconds": data.get("target_duration_seconds"),
                "model_id": data.get("summary_understanding_model_id")})
        elif name == "voiceover":
            result.update({key: data.get("audio", {}).get(key) for key in ("duration_seconds", "status", "file_size")})
        else:
            result.update({key: data.get(key) for key in ("output_duration_seconds", "file_size", "media_url", "file_fingerprint")})
        report["steps"].append(result)
        persist()
        print("LIVE_STEP_COMPLETED", json.dumps(result), flush=True)
    report["ok"] = True
    report["completed_at"] = datetime.now().isoformat()
    persist()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())