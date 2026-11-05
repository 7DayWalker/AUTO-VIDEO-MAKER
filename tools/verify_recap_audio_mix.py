"""Measure an existing recap's narrator + ducked source bed without exposing PCM.

Reconstructs only the first accepted visual cut from recorded grounding. A
least-squares comparison of decoded audio against the narrator and aligned film
audio distinguishes a genuine mixed soundtrack from narrator-only metadata.
No media bytes/base64 are printed, persisted to text, or sent to a model.
"""
from __future__ import annotations

import argparse
import array
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _decode(path, start, seconds, rate):
    from app.assembly.ffmpeg import resolve_ffmpeg
    response = subprocess.run([resolve_ffmpeg(), "-v", "error", "-ss", f"{start:.6f}", "-i", str(path),
                               "-t", f"{seconds:.6f}", "-vn", "-ac", "1", "-ar", str(rate),
                               "-f", "f32le", "pipe:1"], capture_output=True, check=True, timeout=30)
    samples = array.array("f")
    samples.frombytes(response.stdout)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def measure_first_cut(production_id, unit_id):
    from app.production.storage import load_narration_artifact, load_narration_audio, load_production, production_dir
    from app.production.source import find_source_asset
    from app.production.summary_video import load_narrated_summary, _paced_visual_shots

    root = ROOT / "productions"
    narration = load_narration_artifact(production_id, unit_id, root=root)
    audio = load_narration_audio(production_id, unit_id, root=root)
    summary = load_narrated_summary(production_id, unit_id, root=root)
    production = load_production(production_id, root=root)
    asset = find_source_asset(production, narration.source_asset_id, root=root, stored_path=narration.source_media_path)
    if asset is None or not summary.background_audio_mixed:
        raise ValueError("No source-backed background mix was recorded for this recap")
    base = production_dir(production_id, root=root)
    shot = _paced_visual_shots(narration, audio.duration_seconds)[0][1]
    rate, lead = 4000, .25
    seconds = min(4.0, shot.end_seconds - shot.start_seconds - .5)
    if seconds < 1:
        raise ValueError("The first cut is too short for a stable mix measurement")
    margin = .10
    voice = _decode(base / audio.audio_path, lead, seconds, rate)
    mixed = _decode(base / summary.output_path, lead, seconds, rate)
    source = _decode(base / asset.stored_path, max(0, shot.start_seconds + lead - margin), seconds + 2 * margin, rate)
    n = min(len(voice), len(mixed), int(seconds * rate))
    x, y = voice[:n], mixed[:n]
    xx = sum(v * v for v in x)
    yy = sum(v * v for v in y)
    xy = sum(v * w for v, w in zip(x, y))
    if xx < 1e-9 or yy < 1e-9:
        raise ValueError("The first cut has insufficient narration energy")
    narrator_only_error = yy - xy * xy / xx
    best = None
    for offset in range(0, int(2 * margin * rate) + 1, 2):
        z = source[offset:offset + n]
        if len(z) < n:
            continue
        zz = sum(v * v for v in z)
        xz = sum(v * w for v, w in zip(x, z))
        yz = sum(v * w for v, w in zip(y, z))
        determinant = xx * zz - xz * xz
        if determinant <= 1e-12:
            continue
        narrator_gain = (xy * zz - yz * xz) / determinant
        source_gain = (yz * xx - xy * xz) / determinant
        error = yy - narrator_gain * xy - source_gain * yz
        if best is None or error < best[0]:
            best = (error, narrator_gain, source_gain, offset)
    if best is None:
        raise ValueError("Source audio has insufficient independent energy for verification")
    error, narrator_gain, source_gain, offset = best
    improvement = (narrator_only_error - error) / max(narrator_only_error, 1e-12)
    return {
        "production_id": production_id, "unit_id": unit_id, "sample_seconds": seconds,
        "expected_source_gain": summary.background_audio_volume,
        "measured_narrator_gain": round(narrator_gain, 4), "measured_source_gain": round(source_gain, 4),
        "source_alignment_offset_s": round(offset / rate - margin, 4),
        "source_explains_residual_fraction": round(improvement, 4),
        "mixture_verified": bool(.85 <= narrator_gain <= 1.15 and .04 <= source_gain <= .17 and improvement > .25),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("production_id")
    parser.add_argument("--unit-id", default="unit-001")
    args = parser.parse_args()
    result = measure_first_cut(args.production_id, args.unit_id)
    (ROOT / "temp" / "recap_audio_mix_verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["mixture_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())