"""Recap-specific voice settings and bounded, natural silence tightening.

The shared voice engine remains unchanged for other production workflows.
All beats are spoken in ONE continuous TTS request; no per-beat pause padding.
"""
from __future__ import annotations

import os
import re


DEFAULT_RECAP_VOICE = "en-US-AndrewMultilingualNeural"
DEFAULT_RECAP_RATE = "+15%"


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