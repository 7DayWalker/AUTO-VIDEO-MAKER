"""Recap-specific voice settings and bounded, natural silence tightening.

The shared voice engine remains unchanged for other production workflows.
All beats are spoken in ONE continuous TTS request; no per-beat pause padding.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path


DEFAULT_RECAP_VOICE = "en-US-AndrewMultilingualNeural"
DEFAULT_RECAP_RATE = "+15%"
BROADCAST_LUFS = -14.0
BROADCAST_TRUE_PEAK_DB = -1.0
# Lossy MP3/AAC can overshoot a PCM ceiling. Leave 0.5 dB codec headroom.
NORMALIZATION_TRUE_PEAK_DB = -1.5
BROADCAST_VOCAL_CHAIN = (
    "highpass=f=70,"
    "acompressor=threshold=0.089:ratio=4:attack=3:release=100:knee=4:makeup=1"
)
BROADCAST_CHAIN_VERSION = "recap-broadcast-v1"


def recap_voice(override=None):
    return override or os.getenv("AVM_RECAP_TTS_VOICE") or DEFAULT_RECAP_VOICE


def recap_rate(override=None):
    value = override if override is not None else os.getenv("AVM_RECAP_TTS_RATE", DEFAULT_RECAP_RATE)
    if not isinstance(value, str) or not re.fullmatch(r"[+-]\d{1,2}%", value) or not -25 <= int(value[:-1]) <= 60:
        raise ValueError("Recap rate must be a signed percentage between -25% and +60%")
    return value


def spoken_recap_text(text):
    # Whitespace only: do not silently rewrite approved content or citations.
    return " ".join(text.split())


def measure_audio_loudness(path, *, audio_filter=""):
    """Measure decoded input LUFS, true/sample peaks and RMS, never print PCM.

    astats precedes loudnorm: its RMS/peak numbers describe the INPUT, not
    loudnorm's trial output. Only finite numeric diagnostics are retained.
    """
    from app.assembly.ffmpeg import run_ffmpeg

    filters = ([audio_filter] if audio_filter else []) + [
        "astats=metadata=0:reset=0",
        f"loudnorm=I={BROADCAST_LUFS}:TP={NORMALIZATION_TRUE_PEAK_DB}:LRA=7:print_format=json",
    ]
    result = run_ffmpeg(["-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                         ",".join(filters), "-f", "null", "-"], "measuring recap audio loudness")
    blocks = re.findall(r'\{\s*"input_i".*?\}', result.stderr, re.S)
    if not blocks:
        raise ValueError("FFmpeg returned no recap loudness measurement")
    raw = json.loads(blocks[-1])

    def finite(value):
        number = float(value)
        return number if math.isfinite(number) else None

    def statistic(label):
        values = re.findall(re.escape(label) + r":\s*([^\s]+)", result.stderr)
        return finite(values[-1]) if values else None

    return {
        "integrated_lufs": finite(raw["input_i"]),
        "true_peak_db": finite(raw["input_tp"]),
        "loudness_range_lu": finite(raw["input_lra"]),
        "threshold_lufs": finite(raw["input_thresh"]),
        "target_offset_db": finite(raw["target_offset"]),
        "rms_db": statistic("RMS level dB"),
        "sample_peak_db": statistic("Peak level dB"),
    }


def measured_loudnorm_filter(levels):
    """Second pass must use measurements from the exact same processing chain."""
    keys = ("integrated_lufs", "true_peak_db", "loudness_range_lu", "threshold_lufs", "target_offset_db")
    if any(levels.get(key) is None or not math.isfinite(levels[key]) for key in keys):
        raise ValueError("Silent or non-finite audio cannot be mastered as broadcast narration")
    return (
        f"loudnorm=I={BROADCAST_LUFS}:TP={NORMALIZATION_TRUE_PEAK_DB}:LRA=7:"
        f"measured_I={levels['integrated_lufs']}:measured_TP={levels['true_peak_db']}:"
        f"measured_LRA={levels['loudness_range_lu']}:measured_thresh={levels['threshold_lufs']}:"
        f"offset={levels['target_offset_db']}:linear=true:print_format=json"
    )


def master_narration_audio(output_path, validation):
    """Compress, two-pass normalize and validate the actual encoded narrator.

    This is recap-only and runs AFTER silence tightening/tempo changes. Failure
    leaves the input intact; mock/sandbox audio is never claimed as a narrator.
    """
    from app.assembly.ffmpeg import run_ffmpeg
    from app.voice.validator import validate_audio_file

    output_path = Path(output_path)
    temporary = output_path.with_name(output_path.stem + ".broadcast" + output_path.suffix)
    before = measure_audio_loudness(output_path)
    compressed = measure_audio_loudness(output_path, audio_filter=BROADCAST_VOCAL_CHAIN)
    codec = (["-c:a", "libmp3lame", "-b:a", "192k"] if output_path.suffix.lower() == ".mp3"
             else ["-c:a", "pcm_s24le"])
    try:
        run_ffmpeg(["-y", "-i", str(output_path), "-vn", "-af",
                    BROADCAST_VOCAL_CHAIN + "," + measured_loudnorm_filter(compressed),
                    "-ar", "48000", "-ac", str(validation.channels or 1), *codec, str(temporary)],
                   "mastering recap narrator to -14 LUFS with codec peak headroom")
        measured = validate_audio_file(temporary)
        if not measured.valid or abs(float(measured.duration or 0) - float(validation.duration or 0)) > .15:
            raise ValueError("Broadcast processing must preserve the complete narration duration")
        after = measure_audio_loudness(temporary)
        after = correct_encoded_loudness(temporary, after)
        measured = validate_audio_file(temporary)
        if not measured.valid or abs(float(measured.duration or 0) - float(validation.duration or 0)) > .15:
            raise ValueError("Encoded loudness correction must preserve complete speech and duration")
        validate_broadcast_loudness(after)
        temporary.replace(output_path)
        return measured, {"chain_version": BROADCAST_CHAIN_VERSION, "target_lufs": BROADCAST_LUFS,
                          "true_peak_ceiling_db": BROADCAST_TRUE_PEAK_DB, "vocal_filter": BROADCAST_VOCAL_CHAIN,
                          "normalization": "two_pass_loudnorm", "before": before, "after": after}
    finally:
        temporary.unlink(missing_ok=True)


def correct_encoded_loudness(path, levels):
    """Bounded sub-dB correction for dynamic loudnorm/codec rounding, not AGC.

    A measured second pass can still land ~0.5 LU below target on peaky speech.
    Correct that measured deficit, with a look-ahead limiter and codec headroom.
    """
    from app.assembly.ffmpeg import run_ffmpeg
    path = Path(path)
    temporary = path.with_name(path.stem + ".level" + path.suffix)
    try:
        for _ in range(3):
            if levels["integrated_lufs"] is None or abs(BROADCAST_LUFS - levels["integrated_lufs"]) <= .15:
                break
            gain = max(-2.0, min(2.0, BROADCAST_LUFS - levels["integrated_lufs"]))
            limit = 10 ** (NORMALIZATION_TRUE_PEAK_DB / 20)
            codec = (["-c:a", "libmp3lame", "-b:a", "192k"] if path.suffix.lower() == ".mp3"
                     else ["-c:a", "aac", "-b:a", "192k"] if path.suffix.lower() == ".mp4"
                     else ["-c:a", "pcm_s24le"])
            maps = ["-map", "0:v:0", "-c:v", "copy"] if path.suffix.lower() == ".mp4" else ["-vn"]
            run_ffmpeg(["-y", "-i", str(path), *maps, "-map", "0:a:0", "-af",
                        f"volume={gain:.3f}dB,alimiter=limit={limit:.6f}:level=0:latency=1",
                        "-ar", "48000", *codec, str(temporary)], "correcting measured encoded recap loudness")
            levels = measure_audio_loudness(temporary)
            temporary.replace(path)
        return levels
    finally:
        temporary.unlink(missing_ok=True)


def validate_broadcast_loudness(levels):
    if (levels.get("integrated_lufs") is None or abs(levels["integrated_lufs"] - BROADCAST_LUFS) > .5
            or levels.get("true_peak_db") is None or levels["true_peak_db"] > BROADCAST_TRUE_PEAK_DB):
        raise ValueError("Encoded recap audio must measure -14 LUFS (+/-0.5 LU) and at most -1 dBTP")


def tighten_narration_silence(output_path, validation):
    """Keep short conversational pauses; reduce only long near-silent gaps.

    The -50 dB threshold protects quiet speech. A 0.55-second trigger preserves
    ordinary punctuation pauses; longer dead gaps retain a 0.16-second breath.
    Reverse trimming also removes excessive final silence without chopping speech.
    """
    from app.assembly.ffmpeg import run_ffmpeg
    from app.voice.validator import validate_audio_file

    original = float(validation.duration or 0)
    tightened = output_path.with_name(output_path.stem + ".tight" + output_path.suffix)
    trim = ("silenceremove=start_periods=1:start_duration=0.03:start_threshold=-50dB:start_silence=0.04:"
            "stop_periods=-1:stop_duration=0.55:stop_threshold=-50dB:stop_silence=0.16,"
            "areverse,silenceremove=start_periods=1:start_duration=0.03:start_threshold=-50dB:start_silence=0.04,areverse")
    try:
        run_ffmpeg(["-y", "-i", str(output_path), "-vn", "-af", trim, str(tightened)],
                   "tightening recap breaths without per-beat silence padding")
        measured = validate_audio_file(tightened)
        if not measured.valid or float(measured.duration or 0) < original * .65:
            raise ValueError("Tightened narration failed validation; speech must not be truncated")
        tightened.replace(output_path)
        return measured, max(0.0, original - float(measured.duration or 0))
    finally:
        tightened.unlink(missing_ok=True)