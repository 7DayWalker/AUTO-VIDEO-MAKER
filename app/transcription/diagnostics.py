"""Concise media diagnostics shared by STT and browser error boundaries."""
from __future__ import annotations

import re


SOURCE_AUDIO_MISSING_MESSAGE = (
    "Source video has no audio track to transcribe. Please select a video with audio "
    "or enable Mock / sandbox mode."
)
_NO_AUDIO = re.compile(r"output file does not contain any stream|matches no streams|stream map[^\n]*no streams", re.I)
_FFMPEG_NOISE = re.compile(
    r"^\s*(?:ff(?:mpeg|probe) version\b|built with\b|configuration\s*:|--(?:enable|disable)-|"
    r"lib(?:av\w+|sw\w+|postproc)\s+\d|Input #|Output #|Metadata\s*:|Duration\s*:|"
    r"Stream #|Stream mapping\s*:|Press \[q\]|frame\s*=|encoder\s*:|major_brand\s*:|"
    r"minor_version\s*:|compatible_brands\s*:|creation_time\s*:)", re.I,
)
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_INLINE_BUILD_BANNER = re.compile(r"\bff(?:mpeg|probe) version\b|configuration\s*:\s*--(?:enable|disable)-", re.I)


def media_error_reason(value) -> str:
    """Drop build/container chatter without losing the terminal failure reason.

    Non-media diagnostics retain their old content. URL/path/token redaction
    remains the web layer's authority; this helper never imports the server.
    """
    # AVM's FFmpegExecutionError keeps full stderr even when str(exc) contains
    # only the first 500 characters. Read it here to preserve the real reason.
    stderr = getattr(value, "stderr", None)
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    text = _ANSI.sub("", stderr if isinstance(stderr, str) and stderr.strip() else str(value))
    if _NO_AUDIO.search(text):
        return SOURCE_AUDIO_MISSING_MESSAGE
    lines = text.splitlines()
    is_media = bool(_INLINE_BUILD_BANNER.search(text)) or any(_FFMPEG_NOISE.match(line) for line in lines)
    if not is_media:
        return text
    useful = []
    for line in lines:
        line = line.strip()
        if (not line or _FFMPEG_NOISE.match(line) or _INLINE_BUILD_BANNER.search(line) or line.lower() == "conversion failed!"
                or re.fullmatch(r"(?:TRANSCRIPTION_FAILED:\s*)?(?:Failed to load audio:)?", line, re.I)):
            continue
        if "--enable-" in line or "--disable-" in line:
            continue
        useful.append(line)
    return " ".join(useful[-2:])[:360] if useful else "Media processing failed. Check the source file and local FFmpeg installation."