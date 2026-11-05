"""Explicit local Summary Pro smoke test; prints metrics, never keys or images.

Usage: python tools/verify_extended_summary.py PRODUCTION_ID --asset-id src-010
Every write command is sent once. If an HTTP deadline expires, inspect artifacts
before rerunning. Existing accepted output is backed up before regeneration.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("production_id")
    parser.add_argument("--unit-id", default="unit-001")
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--model", help="Optional exact canonical model; otherwise use eligible Pro Auto routes")
    parser.add_argument("--voice", default="en-US-AndrewMultilingualNeural")
    parser.add_argument("--rate", default="+15%")
    parser.add_argument("--stage", choices=("all", "narration", "synthesize", "video", "measure"), default="all")
    args = parser.parse_args()
    import re
    if (not re.fullmatch(r"prd_[a-z0-9_]+", args.production_id)
            or not re.fullmatch(r"unit-\d{3}", args.unit_id) or not re.fullmatch(r"src-\d{3}", args.asset_id)):
        parser.error("Use canonical unit and asset IDs")
    plan = ROOT / "productions" / args.production_id / "units" / args.unit_id / "plan"
    if args.stage in {"all", "narration"}:
        backup = ROOT / "temp" / ("summary_before_extended_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        backup.mkdir(parents=True)
        for name in ("narration.json", "narration_audio.json", "narration_audio.mp3", "narrated_summary.json", "narrated_summary.mp4"):
            if (plan / name).is_file():
                shutil.copy2(plan / name, backup / name)
        print("Previous artifacts backed up:", str(backup), flush=True)
    url = f"http://127.0.0.1:8787/api/productions/{args.production_id}/units/{args.unit_id}"
    timings = {}
    commands = [("narration", "/narration", {"summary_mode": "pro", "target_length_preset": "extended",
                 "mock_mode": False, "selection_mode": "explicit" if args.model else "auto", "asset_id": args.asset_id,
                 **({"model_id": args.model} if args.model else {})}),
                 ("synthesize", "/narration/synthesize", {"mock_mode": False, "format": "mp3", "voice": args.voice, "rate": args.rate}),
                ("video", "/summary-video", {"mock_mode": False})]
    for stage, suffix, body in commands:
        if args.stage not in {"all", stage}:
            continue
        started = time.monotonic()
        try:
            response = requests.post(url + suffix, json=body, timeout=900 if stage == "narration" and args.model in {"gpt-6.1-sol", "gpt-6-astra"} else 300)
        except requests.RequestException as exc:
            print(json.dumps({"stage": stage, "error": type(exc).__name__, "action": "Inspect artifacts; do not blindly retry."}), flush=True)
            return 1
        timings[stage] = round(time.monotonic() - started, 3)
        if response.status_code not in (200, 201):
            # Browser-facing errors are sanitized in Core; only codes are emitted here.
            try:
                code = response.json().get("error", "unknown")
            except ValueError:
                code = "NON_JSON"
            print(json.dumps({"stage": stage, "http_status": response.status_code, "error": code,
                              "elapsed_s": timings[stage]}), flush=True)
            return 1
        data = response.json()
        print(json.dumps({"stage": stage, "http_status": response.status_code, "elapsed_s": timings[stage],
                          "chapter_count": data.get("chapter_count"), "beat_count": len(data.get("beats", [])) or data.get("beat_count"),
                          "word_count": len(data.get("narration_text", "").split()) or data.get("word_count"),
                          "duration_s": data.get("duration_seconds", data.get("output_duration_seconds", data.get("audio_duration_seconds")))}), flush=True)
    if args.stage not in {"all", "video", "measure"}:
        return 0
    from app.assembly.ffmpeg import probe_media
    from app.production.models import NarrationArtifact
    from app.production.narration import validate_narration_artifact
    from app.production.narration_real import validate_storyteller_language
    from app.production.recap_budget import validate_recap_depth
    from app.production.storage import load_production
    from app.production.summary_video import narration_content_hash
    from app.transcription.transcribe import load_transcript
    artifact = NarrationArtifact.model_validate_json((plan / "narration.json").read_text(encoding="utf-8"))
    summary = json.loads((plan / "narrated_summary.json").read_text(encoding="utf-8"))
    audio_meta = json.loads((plan / "narration_audio.json").read_text(encoding="utf-8"))
    production = load_production(args.production_id, root=ROOT / "productions")
    asset = next(asset for asset in production.source_assets if asset.asset_id == args.asset_id)
    transcript = load_transcript(args.production_id, args.asset_id, root=ROOT / "productions")
    validate_narration_artifact(artifact, source_asset=asset, source_transcript=transcript)
    validate_recap_depth(artifact, artifact.narration_budget, transcript, asset.duration)
    validate_storyteller_language(artifact)
    audio = probe_media(ROOT / "productions" / args.production_id / audio_meta["audio_path"])
    video = probe_media(ROOT / "productions" / args.production_id / summary["output_path"])
    source = probe_media(ROOT / "productions" / args.production_id / asset.stored_path)
    metrics = {"production_id": args.production_id, "unit_id": args.unit_id, "asset_id": args.asset_id,
               "summary_mode": artifact.summary_mode, "canonical_model": artifact.summary_understanding_model_id,
               "understanding_method": artifact.understanding_method, "target_length_preset": artifact.target_length_preset,
               "chapter_count": artifact.chapter_count, "beat_count": len(artifact.beats),
               "word_count": len(artifact.narration_text.split()), "visual_frame_count": artifact.visual_frame_count,
               "multimodal_calls": artifact.multimodal_calls, "audio_duration_s": audio.duration,
               "visual_observer_model": artifact.visual_observer_model_id,
               "visual_understanding_strategy": artifact.visual_understanding_strategy,
               "text_storyteller_calls": artifact.text_storyteller_calls,
               "stage1_cache_hits": artifact.stage1_cache_hits,
               "stage1_prior_pro_hydrated_chapters": artifact.stage1_prior_pro_hydrated_chapters,
               "live_vision_calls": artifact.live_vision_calls,
               "WPM": round(len(artifact.narration_text.split()) * 60 / audio.duration, 3),
               "voice": audio_meta.get("voice"), "synthesis_rate": audio_meta.get("synthesis_rate"),
               "silence_trimmed_seconds": audio_meta.get("silence_trimmed_seconds"),
               "background_audio_mixed": summary.get("background_audio_mixed", False),
               "background_audio_volume": summary.get("background_audio_volume", 0),
               "audio_mix_method": summary.get("audio_mix_method"),
               "source_has_audio": source.has_audio,
               "script_matches_video": summary.get("narration_content_hash") == narration_content_hash(artifact),
               "video_duration_s": video.duration, "shot_count": summary["validation"]["visual_shots"],
               "audio_video_delta_s": round(video.duration - audio.duration, 6),
               "tempo_factor": audio_meta.get("tempo_factor", 1), "stage_elapsed_s": timings,
               "media_url": url + "/summary-video/media"}
    valid = (metrics["summary_mode"] == "pro" and metrics["understanding_method"] == "frame_sequence"
             and 480 <= audio.duration <= 600 and 480 <= video.duration <= 600
             and abs(video.duration - audio.duration) <= .25 and 45 <= metrics["beat_count"] <= 54
             and 1400 <= metrics["word_count"] <= 1680 and 155 <= metrics["WPM"] <= 180
             and (not args.model or artifact.summary_understanding_model_id == args.model)
             and metrics["background_audio_mixed"] == bool(source.has_audio)
             and metrics["script_matches_video"]
             and video.has_video and video.has_audio)
    metrics["verified"] = bool(valid)
    output = ROOT / "temp" / "extended_summary_live_metrics.json"
    output.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2), flush=True)
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())