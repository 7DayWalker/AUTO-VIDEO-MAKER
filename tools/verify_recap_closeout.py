"""Read-only local recap closeout: encoded loudness, cached evidence and ending cuts.

Media is decoded by FFmpeg, never read as text or sent to a provider. Frame
comparisons use low-resolution grayscale samples in memory, not vision quota.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _gray_frame(path, timestamp):
    from app.assembly.ffmpeg import resolve_ffmpeg
    result = subprocess.run([resolve_ffmpeg(), "-v", "error", "-threads", "1", "-ss", f"{timestamp:.6f}",
        "-i", str(path), "-frames:v", "1", "-vf", "scale=64:36,format=gray", "-threads", "1",
        "-f", "rawvideo", "pipe:1"], capture_output=True, check=True, timeout=30)
    if len(result.stdout) != 64 * 36:
        raise ValueError("Ending frame could not be decoded")
    return result.stdout


def _frame_correlation(left, right):
    lm, rm = sum(left) / len(left), sum(right) / len(right)
    ll = sum((v - lm) ** 2 for v in left)
    rr = sum((v - rm) ** 2 for v in right)
    if min(ll, rr) < 1:
        raise ValueError("A blank frame cannot verify moving narrative footage")
    return sum((a - lm) * (b - rm) for a, b in zip(left, right)) / math.sqrt(ll * rr)


def verify(production_id, unit_id, *, before_dir=None):
    from app.assembly.ffmpeg import probe_media, resolve_ffmpeg
    from app.production.models import NarrationArtifact, SourceTranscript
    from app.production.narration import validate_narration_artifact
    from app.production.narration_real import validate_true_ending
    from app.production.recap_budget import validate_recap_depth
    from app.production.source import find_source_asset
    from app.production.storage import load_production, load_narration_artifact, load_narration_audio, compute_content_hash
    from app.production.summary_video import load_narrated_summary, narration_content_hash
    from app.production.synthesis import measure_audio_loudness, validate_broadcast_loudness

    root, base = ROOT / "productions", ROOT / "productions" / production_id
    narration = load_narration_artifact(production_id, unit_id, root=root)
    audio = load_narration_audio(production_id, unit_id, root=root)
    summary = load_narrated_summary(production_id, unit_id, root=root)
    asset = find_source_asset(load_production(production_id, root=root), narration.source_asset_id,
                              root=root, stored_path=narration.source_media_path)
    transcript = SourceTranscript.model_validate_json((base / narration.source_transcript_path).read_text(encoding="utf-8"))
    validate_narration_artifact(narration, source_asset=asset, source_transcript=transcript)
    validate_recap_depth(narration, narration.narration_budget, transcript, asset.duration)
    if narration.ending_anchor is None or not narration.cold_open_hook:
        raise ValueError("Closeout requires a validated cold open and narrative ending anchor")
    validate_true_ending(narration.beats, narration.ending_anchor, require_visual=narration.summary_mode == "pro")
    video_path, source_path = base / summary.output_path, base / asset.stored_path
    voice_levels = measure_audio_loudness(base / audio.audio_path)
    video_levels = measure_audio_loudness(video_path)
    validate_broadcast_loudness(voice_levels)
    validate_broadcast_loudness(video_levels)
    info = probe_media(video_path)
    if abs(float(info.duration) - audio.duration_seconds) > .25 or info.audio_stream_count != 1:
        raise ValueError("Closeout must preserve narration duration and one final soundtrack")
    if summary.file_fingerprint != compute_content_hash(video_path) or summary.narration_content_hash != narration_content_hash(narration):
        raise ValueError("Script/media provenance does not match the assembled recap")
    shots = summary.validation.get("shot_manifest", [])
    if len(shots) != summary.validation["visual_shots"]:
        raise ValueError("Shot count must match the persisted cut manifest")
    final_beats, frame_checks = [], []
    for beat in narration.beats[-3:]:
        selected = [shot for shot in shots if shot["beat_order"] == beat.order]
        if not selected:
            raise ValueError("Closing narration beat has no moving source cut")
        for shot in selected:
            if (shot["source_start_seconds"] < narration.ending_anchor.finale_start_seconds
                    or shot["source_end_seconds"] > narration.ending_anchor.narrative_end_seconds + .001):
                raise ValueError("Closing shot must remain inside the true finale, excluding credits")
            offset = min(.7, (shot["output_end_seconds"] - shot["output_start_seconds"]) / 2)
            target = shot["source_start_seconds"] + offset
            output = _gray_frame(video_path, shot["output_start_seconds"] + offset)
            # Frame-rate quantization/AAC cut rounding can shift a decoded frame
            # by a few source frames. Compare only inside a bounded +/-0.1s window.
            score = max(_frame_correlation(output, _gray_frame(source_path, target + delta))
                        for delta in (-.10, 0, .10))
            if score < .90:
                raise ValueError("Decoded ending footage does not match its registered source cut")
            frame_checks.append({"shot_index": shot["shot_index"], "source_timestamp_seconds": round(target, 6),
                                 "source_frame_correlation": round(score, 5)})
        final_beats.append({"order": beat.order, "narration_text": beat.narration_text,
            "source_refs": [{"start_seconds": r.start_seconds, "end_seconds": r.end_seconds,
                             "visual_frame_timestamps": r.visual_frame_timestamps} for r in beat.source_refs],
            "shots": selected})
    if abs(shots[-1]["source_end_seconds"] - narration.ending_anchor.narrative_end_seconds) > .05:
        raise ValueError("Final decoded cut does not reach the true narrative end")
    before = {}
    if before_dir:
        before_dir = Path(before_dir)
        before = {name: measure_audio_loudness(before_dir / name)
                  for name in ("narration_audio.mp3", "narrated_summary.mp4")}
        old = NarrationArtifact.model_validate_json((before_dir / "narration.json").read_text(encoding="utf-8"))
        before["unchanged_beats_2_through_50"] = all(a.model_dump() == b.model_dump()
                                                      for a, b in zip(narration.beats[1:-3], old.beats[1:-3]))
        before["unchanged_chapters_2_through_4"] = narration.chapters[1:4] == old.chapters[1:4]
        plan = video_path.parent
        before["stage1_cache_byte_identical"] = compute_content_hash(plan / "frame_observations.json") == compute_content_hash(before_dir / "frame_observations.json")
        if not all(before[key] for key in ("unchanged_beats_2_through_50", "unchanged_chapters_2_through_4", "stage1_cache_byte_identical")):
            raise ValueError("Selective closeout unexpectedly altered retained narration or visual evidence")
        separation_path = before_dir / "baseline_separation.json"
        if separation_path.is_file():
            before["mix_separation"] = json.loads(separation_path.read_text(encoding="utf-8"))
    decoded = subprocess.run([resolve_ffmpeg(), "-v", "error", "-threads", "2", "-i", str(video_path),
                               "-f", "null", "-"], capture_output=True, text=True, timeout=300)
    if decoded.returncode or decoded.stderr.strip():
        raise ValueError("Final MP4 has decode errors")
    return {"production_id": production_id, "unit_id": unit_id, "verified": True,
        "before": before, "narrator_after": voice_levels, "mp4_after": video_levels,
        "mix_processing": summary.audio_processing, "cold_open_hook": narration.cold_open_hook,
        "ending_anchor": narration.ending_anchor.model_dump(), "final_three_beats": final_beats,
        "decoded_source_frame_checks": frame_checks, "duration_seconds": info.duration,
        "shot_count": len(shots), "beat_count": len(narration.beats), "word_count": len(narration.narration_text.split()),
        "decode_errors": 0, "new_vision_calls": narration.live_vision_calls, "video_path": str(video_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("production_id")
    parser.add_argument("--unit-id", default="unit-001")
    parser.add_argument("--before-dir", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"prd_[a-z0-9_]+", args.production_id) or not re.fullmatch(r"unit-\d{3}", args.unit_id):
        parser.error("Use canonical production and unit IDs")
    report = verify(args.production_id, args.unit_id, before_dir=args.before_dir)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())