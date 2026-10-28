"""Small, explicitly synthetic Track 9 media; never opens an operator's movie.

Sandbox still creates playable media and uses normal FFprobe/storage/acceptance
checks. Its work is bounded independently of source duration, resolution,
transcript size, configured models and the real production assembly pipeline.
"""
from __future__ import annotations

import math
from pathlib import Path


SANDBOX_DURATION_SECONDS = 4.0
SANDBOX_WIDTH = 320
SANDBOX_HEIGHT = 180
SANDBOX_FPS = 12


def sandbox_duration(duration: float) -> float:
    value = float(duration)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("sandbox duration must be positive and finite")
    return min(SANDBOX_DURATION_SECONDS, value)


def _run(args: list[str], description: str) -> None:
    from app.assembly.ffmpeg import get_ffmpeg_runner

    # No unbounded source decode or stdin wait. Even a broken local toolchain
    # fails within a short budget rather than leaving an offline dry run stuck.
    get_ffmpeg_runner().run(["-nostdin", "-hide_banner", *args], description, timeout=5)


def create_sandbox_source(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "-y", "-f", "lavfi", "-i",
        f"color=c=purple:s={SANDBOX_WIDTH}x{SANDBOX_HEIGHT}:r={SANDBOX_FPS}:d={SANDBOX_DURATION_SECONDS}",
        "-an", "-c:v", "libx264", "-threads", "1", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output_path),
    ], "creating explicit bounded sandbox source")


def create_sandbox_audio(output_path: Path, duration: float) -> None:
    duration = sandbox_duration(duration)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    codec = "pcm_s16le" if output_path.suffix.lower() == ".wav" else "libmp3lame"
    _run([
        "-y", "-f", "lavfi", "-i", f"sine=frequency=200:sample_rate=16000:duration={duration:.6f}",
        "-c:a", codec, "-ac", "1", "-threads", "1", str(output_path),
    ], "creating explicit bounded sandbox narration tone")


def create_sandbox_summary(output_path: Path, audio_path: Path, duration: float) -> None:
    duration = sandbox_duration(duration)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "-y", "-f", "lavfi", "-i",
        f"color=c=purple:s={SANDBOX_WIDTH}x{SANDBOX_HEIGHT}:r={SANDBOX_FPS}:d={duration:.6f}",
        "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0", "-t", f"{duration:.6f}",
        "-c:v", "libx264", "-threads", "1", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "1", "-ar", "16000", "-movflags", "+faststart", str(output_path),
    ], "assembling explicitly synthetic sandbox summary")