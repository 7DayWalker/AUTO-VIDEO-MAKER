"""
AUTO Video Maker - Caption Rendering

SRT caption burning using FFmpeg subtitles filter.
"""

from pathlib import Path
import logging

from app.assembly.config import (
    VIDEO_CODEC,
    VIDEO_PRESET,
    VIDEO_CRF,
    PIXEL_FORMAT,
    AUDIO_CODEC,
    AUDIO_BITRATE,
)
from app.assembly.caption_presets import (
    DEFAULT_CAPTION_PRESET,
    DEFAULT_PLAY_WIDTH,
    DEFAULT_PLAY_HEIGHT,
    generate_ass,
    resolve_caption_style,
    validate_preset,
)
from app.assembly.ffmpeg import run_ffmpeg
from app.assembly.exceptions import CaptionError
from app.assembly.models import CaptionConfig

logger = logging.getLogger(__name__)


def _escape_filter_path(path) -> str:
    """Escape a filesystem path for embedding inside an FFmpeg filter.

    Backslashes become forward slashes and the drive colon is escaped with a
    backslash so the filter parser does not treat it as an option separator.
    """
    return str(path).replace("\\", "/").replace(":", "\\:")


def build_subtitle_filter(
    srt_path: Path,
    config: CaptionConfig = None,
    ass_path: Path = None,
) -> str:
    """Build an FFmpeg subtitles filter carrying an explicit caption contract.

    Phase 6F: instead of passing an SRT through ``force_style`` (whose font
    size / margins libass interprets against an uncontrolled default script
    resolution, causing oversized captions high on a 1080x1920 frame), this
    converts the SRT into an ASS script that declares an explicit
    PlayResX/PlayResY matching the output and applies a selected presentation
    preset. The result is deterministic and reproducible.

    The filter uses the named ``filename=`` option form (not the bare
    positional form) which newer FFmpeg builds parse unambiguously.

    Args:
        srt_path: Path to the SRT captions file.
        config: Optional caption configuration (preset / play resolution /
            per-field overrides).
        ass_path: Optional explicit destination for the rendered ASS script.
            Defaults to ``<srt>`` with the extension replaced by ``.ass``.

    Returns:
        FFmpeg subtitles filter string referencing the rendered ASS file.
    """
    if config is None or not getattr(config, "preset", None):
        preset = DEFAULT_CAPTION_PRESET
    else:
        preset = validate_preset(config.preset)

    play_w = getattr(config, "play_width", None) or DEFAULT_PLAY_WIDTH
    play_h = getattr(config, "play_height", None) or DEFAULT_PLAY_HEIGHT

    # Only carry genuinely-provided overrides; None means "use the preset".
    overrides = {}
    for key in ("font_size", "margin_bottom", "font_color", "outline", "shadow"):
        value = getattr(config, key, None)
        if value is not None:
            overrides[key] = value

    style = resolve_caption_style(preset, play_w, play_h, overrides)
    srt = Path(srt_path)
    ass = Path(ass_path) if ass_path else srt.with_suffix(".ass")
    generate_ass(srt, ass, style)

    return f"subtitles=filename='{_escape_filter_path(ass)}'"



def burn_captions(
    video_path: Path,
    output_path: Path,
    srt_path: Path,
    config: CaptionConfig = None,
) -> None:
    """
    Burn captions into video.
    
    Args:
        video_path: Input video.
        output_path: Output video with burned captions.
        srt_path: Path to SRT subtitle file.
        config: Optional caption configuration.
    """
    if not srt_path.exists():
        raise CaptionError("SRT file not found", str(srt_path))
    
    # Validate SRT content
    try:
        content = srt_path.read_text(encoding="utf-8")
        if not content.strip():
            raise CaptionError("SRT file is empty", str(srt_path))
        if "-->" not in content:
            raise CaptionError("Invalid SRT format", str(srt_path))
    except UnicodeDecodeError:
        raise CaptionError("SRT file is not valid UTF-8", str(srt_path))
    
    # Build subtitle filter
    sub_filter = build_subtitle_filter(srt_path, config)
    
    args = [
        "-y",
        "-i", str(video_path),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-vf", sub_filter,
        "-c:v", VIDEO_CODEC,
        "-preset", VIDEO_PRESET,
        "-crf", str(VIDEO_CRF),
        "-pix_fmt", PIXEL_FORMAT,
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
        "-movflags", "+faststart",
        str(output_path),
    ]
    
    run_ffmpeg(args, "Burning captions")
    logger.info(f"Captions burned -> {output_path}")


def create_test_srt(
    output_path: Path,
    duration: float = 10.0,
    num_captions: int = 3,
) -> None:
    """
    Create a synthetic test SRT file.
    
    Args:
        output_path: Path to write SRT file.
        duration: Total duration to cover.
        num_captions: Number of caption entries.
    """
    interval = duration / num_captions
    
    lines = []
    for i in range(num_captions):
        start = i * interval
        end = (i + 1) * interval - 0.1
        
        start_str = format_srt_time(start)
        end_str = format_srt_time(end)
        
        lines.append(str(i + 1))
        lines.append(f"{start_str} --> {end_str}")
        lines.append(f"Caption {i + 1}: Test subtitle text")
        lines.append("")
    
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Created test SRT: {output_path}")


def format_srt_time(seconds: float) -> str:
    """Format seconds as SRT timestamp (HH:MM:SS,mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def parse_srt_time(time_str: str) -> float:
    """Parse SRT timestamp to seconds."""
    time_str = time_str.strip().replace(",", ".")
    parts = time_str.split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid SRT time format: {time_str}")
    
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    
    return hours * 3600 + minutes * 60 + seconds


def get_srt_duration(srt_path: Path) -> float:
    """
    Get the end time of the last caption in an SRT file.
    
    Returns the duration covered by captions.
    """
    content = srt_path.read_text(encoding="utf-8")
    
    max_time = 0.0
    for line in content.split("\n"):
        if "-->" in line:
            parts = line.split("-->")
            if len(parts) == 2:
                try:
                    end_time = parse_srt_time(parts[1])
                    max_time = max(max_time, end_time)
                except ValueError:
                    continue
    
    return max_time
