"""
AUTO Video Maker - Caption Rendering

SRT caption burning using FFmpeg subtitles filter.
"""

from pathlib import Path
import logging

from app.assembly.config import (
    CAPTION_FONT_SIZE,
    CAPTION_FONT_COLOR,
    CAPTION_OUTLINE_COLOR,
    CAPTION_OUTLINE_WIDTH,
    CAPTION_MARGIN_BOTTOM,
    CAPTION_FONT,
    VIDEO_CODEC,
    VIDEO_PRESET,
    VIDEO_CRF,
    PIXEL_FORMAT,
    AUDIO_CODEC,
    AUDIO_BITRATE,
)
from app.assembly.ffmpeg import run_ffmpeg
from app.assembly.exceptions import CaptionError
from app.assembly.models import CaptionConfig

logger = logging.getLogger(__name__)


def build_subtitle_filter(
    srt_path: Path,
    config: CaptionConfig = None,
) -> str:
    """
    Build FFmpeg subtitles filter string.
    
    Args:
        srt_path: Path to SRT file.
        config: Optional caption configuration overrides.
    
    Returns:
        FFmpeg filter string for subtitle rendering.
    """
    # Get settings (use config overrides if provided)
    font_size = config.font_size if config and config.font_size else CAPTION_FONT_SIZE
    font_color = config.font_color if config and config.font_color else CAPTION_FONT_COLOR
    margin_bottom = config.margin_bottom if config and config.margin_bottom else CAPTION_MARGIN_BOTTOM
    
    # Escape path for FFmpeg (Windows paths need special handling)
    escaped_path = str(srt_path).replace("\\", "/").replace(":", "\\:")
    
    # Build style string
    # FFmpeg ASS style format
    style_parts = [
        f"FontSize={font_size}",
        f"PrimaryColour=&H00FFFFFF",  # White in ABGR
        f"OutlineColour=&H00000000",  # Black outline
        f"BorderStyle=1",
        f"Outline={CAPTION_OUTLINE_WIDTH}",
        f"Shadow=0",
        f"MarginV={margin_bottom}",
        f"Alignment=2",  # Bottom center
    ]
    
    if CAPTION_FONT:
        style_parts.append(f"FontName={CAPTION_FONT}")
    
    style_str = ",".join(style_parts)
    
    # Build filter
    filter_str = f"subtitles='{escaped_path}':force_style='{style_str}'"
    
    return filter_str


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
