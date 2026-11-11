"""Read rendered acceptance media; measure decoded beat clocks and preservation."""
from __future__ import annotations

import array
import hashlib
import json
import math
import subprocess
import sys
import wave
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PID = "prd_20261107_the_last_dragon_queen_part_i_ai_short_fi_057"
BID = "rpb-3aedc87ee6141119b03fe46a"


def audio_energy(samples, center, window=192):
    left, right = max(0, center - window // 2), min(len(samples), center + window // 2)
    values = samples[left:right]
    return math.sqrt(sum(float(x) ** 2 for x in values) / max(1, len(values)))


def measure_lag(reference, rendered, boundary):
    """Envelope correlation tolerates mastering and the ducked movie bed."""
    centers = list(range(max(192, boundary - 4800), min(len(reference) - 192, boundary + 14400), 192))
    original = [audio_energy(reference, c) for c in centers]
    mean = sum(original) / len(original)
    original = [x - mean for x in original]
    norm = sum(x*x for x in original)
    best = (-2, None)
    for lag in range(-1600, 1601, 24):
        actual = [audio_energy(rendered, c + lag) for c in centers]
        mean = sum(actual) / len(actual)
        actual = [x - mean for x in actual]
        score = sum(x*y for x,y in zip(original, actual)) / max(1, math.sqrt(norm * sum(y*y for y in actual)))
        if score > best[0]:
            best = (score, lag)
    if best[1] is None or best[0] < .65:
        raise RuntimeError("Encoded audio could not be correlated reliably with retained beat PCM")
    return best[1], best[0]


def main():
    from app.assembly.ffmpeg import get_ffmpeg_runner, run_ffmpeg
    from app.repurpose.storage import content_hash, atomic_json, load_plan, load_render
    from app.repurpose.models import BatchRequest
    from app.repurpose.timeline import validate_execution
    production = ROOT / "productions" / PID
    plan = load_plan(PID, BID, ROOT / "productions")
    runner = get_ffmpeg_runner(); runner.ensure_ffprobe()
    temporary = ROOT / "temp/dragon-v2-final-verification"; temporary.mkdir(parents=True, exist_ok=True)
    summaries, beats = [], {}
    for clip in plan.clips:
        directory = production / "units" / clip.unit_id / "repurpose"
        media = directory / "short.mp4"
        render = load_render(PID, clip.unit_id, ROOT / "productions")
        validate_execution(clip, render.cuts)
        if content_hash(json.loads((directory / "execution.json").read_text(encoding="utf-8"))) != render.validation["execution_hash"]:
            raise RuntimeError("Execution fingerprint mismatch")
        probe = subprocess.run([runner.ffprobe_path, "-v", "error", "-select_streams", "v:0", "-show_frames",
            "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(media)],
            capture_output=True, text=True, check=True, timeout=180)
        frame_times = [float(f["best_effort_timestamp_time"]) for f in json.loads(probe.stdout)["frames"]]
        if len(frame_times) != sum(c["frame_count"] for c in render.cuts):
            raise RuntimeError("Decoded output frame count mismatch")
        raw = temporary / f"{clip.unit_id}-decoded.wav"
        run_ffmpeg(["-y", "-i", str(media), "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(raw)],
            "verifying encoded narrator beat alignment", timeout=180)
        with wave.open(str(raw), "rb") as stream:
            encoded = array.array("h", stream.readframes(stream.getnframes()))
        with wave.open(str(directory / "narration.wav"), "rb") as stream:
            original = array.array("h", stream.readframes(stream.getnframes()))
            channels = stream.getnchannels()
        original = original[::channels]
        rows = []
        captions = json.loads((directory / "caption_timing.json").read_text(encoding="utf-8"))["entries"]
        for beat, cut in zip(clip.beats, render.cuts):
            expected = cut["output_start_frame"] / 30
            actual = frame_times[cut["output_start_frame"]]
            lag, correlation = measure_lag(original, encoded, cut["output_start_frame"] * 1600)
            frame_delta_ms = (actual - expected) * 1000
            audio_delta_ms = lag / 48
            delta = audio_delta_ms - frame_delta_ms
            if abs(delta) > 1000 / 30:
                raise RuntimeError("Measured audio/video beat alignment exceeds one frame")
            for entry in captions:
                if entry.get("beat_order") == beat.order and not expected - 1e-6 <= entry["start"] < expected + cut["output_duration"] + 1e-6:
                    raise RuntimeError("Caption entry crosses its assigned beat timeline")
            time_base = Fraction(beat.source_time_base) if beat.source_time_base else None
            source_end_pts = round(cut["end_seconds"] / float(time_base)) if time_base else None
            rows.append({"beat": beat.order, "spoken_text": beat.spoken_text, "evidence_id": beat.evidence_id,
                "visual_anchor_id": beat.visual_anchor_id, "source_start_seconds": cut["start_seconds"], "source_end_seconds": cut["end_seconds"],
                "source_start_pts": beat.focal_source_pts, "source_end_pts": source_end_pts, "source_time_base": beat.source_time_base,
                "output_start_seconds": expected, "output_end_seconds": expected + cut["output_duration"],
                "output_start_frame": cut["output_start_frame"], "frame_count": cut["frame_count"],
                "audio_video_boundary_delta_ms": round(delta, 3), "audio_envelope_correlation": round(correlation, 5),
                "frame_pts_delta_ms": round(frame_delta_ms, 6), "tail_padding_ms": cut["tail_padding_samples"] / 48,
                "audio_tempo_factor": cut.get("audio_tempo_factor", 1), "video_slowdown_factor": cut["video_tempo_factor"]})
        beats[clip.unit_id] = rows
        summaries.append({"unit_id": clip.unit_id, "title": clip.title, "duration_seconds": render.duration_seconds,
            "resolution": [render.width, render.height], "beat_count": len(rows), "file_size": render.file_size,
            "file_fingerprint": render.file_fingerprint, "integrated_lufs": render.loudness["integrated_lufs"],
            "true_peak_db": render.loudness["true_peak_db"], "maximum_boundary_delta_ms": max(abs(r["audio_video_boundary_delta_ms"]) for r in rows),
            "decode_errors": render.validation["decode_errors"], "output_path": str(media)})
        print("verified", clip.unit_id, summaries[-1], flush=True)
    baseline = json.loads((ROOT / "diagnostics/repurpose-v2-preservation-baseline.json").read_text(encoding="utf-8"))
    for name, digest in baseline.items():
        with (production / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise RuntimeError("Protected v1 artifact changed: " + name)
    old = production / "repurpose/batches/rpb-9acb97d5a5a400ccbb011f62"
    job = json.loads((old / "job.json").read_text(encoding="utf-8"))
    if content_hash(load_plan(PID, old.name, ROOT / "productions")) != job["plan_hash"] or content_hash(BatchRequest.model_validate(job["request"])) != job["request_hash"]:
        raise RuntimeError("Protected legacy hash mismatch")
    report = {"ok": True, "production_id": PID, "batch_id": BID, "plan_hash": content_hash(plan),
        "method": "decoded video PTS + decoded narrator envelope correlation against retained PCM (0.5ms lag grid)",
        "v1_preservation_files": len(baseline), "v1_preserved": True, "units": summaries, "beats": beats}
    atomic_json(ROOT / "diagnostics/dragon-v2-final-sync-report.json", report)
    print("all_units_verified", True, flush=True)


if __name__ == "__main__":
    main()