"""Phase 6F - Caption presentation presets and the explicit play-resolution
rendering contract.

The caption defect fixed here is structural, not cosmetic: captions were
burned through the FFmpeg ``subtitles`` filter with ``force_style`` values
that carry NO explicit PlayResX/PlayResY. libass then interprets the Fontsize
and MarginV values against an *uncontrolled default script coordinate system*
(commonly 384x288) and scales them up to the 1080x1920 output, producing
oversized captions anchored near/above the top of the frame.

The fix is a durable, reproducible contract:

- Every rendered caption set is converted to an ASS script whose
  ``[Script Info]`` declares an explicit ``PlayResX``/``PlayResY`` matching
  the actual vertical output resolution (default 1080x1920).
- A small preset system (minimal / standard / bold / viral) selects style
  values that are only meaningful once the play resolution is known.
- Then ``MarginV`` etc. are literal pixels from the bottom of the frame at
  the declared play resolution, giving a deterministic lower-third layout.

This module owns the pure style contract and ASS generation; the FFmpeg
filter wiring lives in ``app.assembly.captions``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Vertical short-form output (TikTok / YouTube Shorts) play resolution. All
# style lengths are interpreted in this coordinate space.
DEFAULT_PLAY_WIDTH = 1080
DEFAULT_PLAY_HEIGHT = 1920

# Alignment constants (libass Alignment): 2 = bottom-centre.
ALIGNMENT_BOTTOM_CENTER = 2

DEFAULT_CAPTION_PRESET = "standard"

CAPTION_PRESETS: Dict[str, Dict] = {
    # `standard` is the Phase 6F default: readable, polished, lower-third.
    "standard": {
        "font_name": "Arial",
        "font_size": 64,
        "primary_colour": "&H00FFFFFF",  # white (AABBGGRR)
        "outline_colour": "&H00000000",  # black
        "back_colour": "&H80000000",     # translucent black shadow
        "outline": 3,
        "shadow": 1,
        "bold": 0,
        "alignment": ALIGNMENT_BOTTOM_CENTER,
        "margin_left": 80,
        "margin_right": 80,
        "margin_bottom": 400,  # 400px above bottom edge = lower third on 1920
    },
    "minimal": {
        "font_name": "Arial",
        "font_size": 52,
        "primary_colour": "&H00FFFFFF",
        "outline_colour": "&H00000000",
        "back_colour": "&H80000000",
        "outline": 2,
        "shadow": 0,
        "bold": 0,
        "alignment": ALIGNMENT_BOTTOM_CENTER,
        "margin_left": 100,
        "margin_right": 100,
        "margin_bottom": 320,
    },
    "bold": {
        "font_name": "Arial",
        "font_size": 68,
        "primary_colour": "&H00FFFFFF",
        "outline_colour": "&H00000000",
        "back_colour": "&H80000000",
        "outline": 4,
        "shadow": 2,
        "bold": 1,
        "alignment": ALIGNMENT_BOTTOM_CENTER,
        "margin_left": 70,
        "margin_right": 70,
        "margin_bottom": 420,
    },
    "viral": {
        "font_name": "Arial",
        "font_size": 76,
        "primary_colour": "&H00FFFFFF",
        "outline_colour": "&H00000000",
        "back_colour": "&H80000000",
        "outline": 5,
        "shadow": 3,
        "bold": 1,
        "alignment": ALIGNMENT_BOTTOM_CENTER,
        "margin_left": 60,
        "margin_right": 60,
        "margin_bottom": 460,
    },
}

_VALID_KEYS = {
    "font_name",
    "font_size",
    "primary_colour",
    "outline_colour",
    "back_colour",
    "outline",
    "shadow",
    "bold",
    "alignment",
    "margin_left",
    "margin_right",
    "margin_bottom",
}


def caption_preset_names() -> List[str]:
    """Return the supported preset names (sorted, stable)."""
    return sorted(CAPTION_PRESETS)


def validate_preset(name: Optional[str]) -> str:
    """Normalize a preset name; unknown/empty falls back to the default."""
    if not name:
        return DEFAULT_CAPTION_PRESET
    if name not in CAPTION_PRESETS:
        raise ValueError(
            f"Unknown caption preset '{name}'. "
            f"Valid: {', '.join(caption_preset_names())}"
        )
    return name


def resolve_caption_style(
    preset: Optional[str],
    play_width: Optional[int] = None,
    play_height: Optional[int] = None,
    overrides: Optional[Dict] = None,
) -> Dict:
    """Resolve a preset + overrides into a fully-specified style dict.

    Returns a dict with every value the ASS renderer consumes, plus the
    explicit play resolution. ``overrides`` may override font_size,
    margin_bottom, font_color, outline, shadow (values must already be
    validated by the caller against the play-resolution semantics).
    """
    name = validate_preset(preset)
    style: Dict = dict(CAPTION_PRESETS[name])
    style["name"] = name
    style["play_width"] = int(play_width or DEFAULT_PLAY_WIDTH)
    style["play_height"] = int(play_height or DEFAULT_PLAY_HEIGHT)

    for key, value in (overrides or {}).items():
        if value is None or key not in _VALID_KEYS:
            continue
        if key == "font_color":
            style["primary_colour"] = _colour_to_abgr(
                value, default=style["primary_colour"]
            )
        else:
            style[key] = value
    return style


def _colour_to_abgr(name: str, default: str) -> str:
    """Map simple colour names to ASS &HAABBGGRR, else keep default."""
    mapping = {
        "white": "&H00FFFFFF",
        "yellow": "&H0000FFFF",
        "black": "&H00000000",
    }
    return mapping.get(str(name or "").strip().lower(), default)


def _format_ass_time(seconds: float) -> str:
    """Format seconds as ASS time H:MM:SS.cc (centiseconds)."""
    cs = int(round(seconds * 100))
    hh = cs // 360000
    mm = (cs % 360000) // 6000
    ss = (cs % 6000) // 100
    cc = cs % 100
    return f"{hh}:{mm:02d}:{ss:02d}.{cc:02d}"


def parse_srt_entries(content: str) -> List[Tuple[float, float, str]]:
    """Parse SRT content into ``(start_seconds, end_seconds, text)`` entries.

    Invalid/zero-length entries are skipped rather than crashing, mirroring
    the tolerant behaviour of the existing SRT validator.
    """
    entries: List[Tuple[float, float, str]] = []
    blocks = content.replace("\r\n", "\n").split("\n\n")
    for block in blocks:
        lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        timeline = next((ln for ln in lines if " --> " in ln), None)
        if not timeline:
            continue
        try:
            start_raw, end_raw = timeline.split(" --> ", 1)
            start = _parse_srt_timestamp(start_raw)
            end = _parse_srt_timestamp(end_raw)
        except Exception:  # noqa: BLE001 - tolerant parser
            continue
        if end <= start:
            continue
        text_lines = [ln for ln in lines if ln != timeline]
        # Drop the numeric entry index line if it is present.
        if text_lines and text_lines[0].isdigit():
            text_lines = text_lines[1:]
        text = "\\N".join(text_lines).strip()
        if not text:
            continue
        entries.append((start, end, text))
    return entries


def _parse_srt_timestamp(ts: str) -> float:
    ts = ts.strip()
    hh, mm, rest = ts.split(":", 2)
    secs, millis = rest.replace(",", ":", 1).split(":", 1)
    return (
        int(hh) * 3600
        + int(mm) * 60
        + int(secs)
        + int(millis) / 1000.0
    )


def build_ass_script(entries: List[Tuple[float, float, str]], style: Dict) -> str:
    """Render a complete ASS script with explicit PlayRes and style."""
    style_line = (
        "Style: Default,{font_name},{font_size},{primary_colour},"
        "{primary_colour},{outline_colour},{back_colour},{bold},0,0,0,"
        "100,100,0,0,1,{outline},{shadow},{alignment},"
        "{margin_left},{margin_right},{margin_bottom},1".format(**style)
    )
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {style['play_width']}",
        f"PlayResY: {style['play_height']}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        style_line,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]
    for start, end, text in entries:
        lines.append(
            f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},"
            f"Default,,0,0,0,,{text}"
        )
    return "\n".join(lines) + "\n"


def generate_ass(srt_path: Path, ass_path: Path, style: Dict) -> Path:
    """Convert an SRT file into an ASS script at ``ass_path`` (atomic)."""
    content = srt_path.read_text(encoding="utf-8")
    entries = parse_srt_entries(content)
    script = build_ass_script(entries, style)
    ass_path = Path(ass_path)
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = ass_path.with_suffix(".ass.tmp")
    tmp.write_text(script, encoding="utf-8")
    tmp.replace(ass_path)
    return ass_path
