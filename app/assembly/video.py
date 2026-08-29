"""
AUTO Video Maker - Video Processing

Video normalization and concatenation using FFmpeg.
"""

from pathlib import Path
from typing import List
import logging

from app.assembly.config import (
    DEFAULT_WIDTH,
    DEFAULT_HEIGHT,
    DEFAULT_FRAME_RATE,
    VIDEO_CODEC,
    VIDEO_PRESET,
    VIDEO_CRF,
    VIDEO_PROFILE,
    VIDEO_LEVEL,
    PIXEL_FORMAT,
    AUDIO_CODEC,
    AUDIO_BITRATE,
    AUDIO_SAMPLE_RATE,
    AUDIO_CHANNELS,
)
from app.assembly.ffmpeg import run_ffmpeg
from app.assembly.models import MediaInfo

logger = logging.getLogger(__name__)


def build_scale_filter(
    source_width: int,
    source_height: int,
    target_width: int = DEFAULT_WIDTH,
    target_height: int = DEFAULT_HEIGHT,
) -> str:
    """
    Build FFmpeg scale filter for video normalization.
    
    Strategy:
    - If source is already target size: no scaling
    - If source is 9:16: scale to fit
    - If source is not 9:16: scale and pad to fit (letterbox/pillarbox)
    """
    source_ratio = source_width / source_height
    target_ratio = target_width / target_height
    
    # Check if already correct size
    if source_width == target_width and source_height == target_height:
        return ""
    
    # Check if same aspect ratio (within tolerance)
    if abs(source_ratio - target_ratio) < 0.01:
        # Same aspect, just scale
        return f"scale={target_width}:{target_height}"
    
    # Different aspect ratio - scale to fit and pad
    if source_ratio > target_ratio:
        # Source is wider - fit width, pad top/bottom
        scale = f"scale={target_width}:-2"
        pad = f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black"
    else:
        # Source is taller - fit height, pad left/right
        scale = f"scale=-2:{target_height}"
        pad = f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black"
    
    return f"{scale},{pad}"


def normalize_video(
    input_path: Path,
    output_path: Path,
    target_width: int = DEFAULT_WIDTH,
    target_height: int = DEFAULT_HEIGHT,
    target_fps: int = DEFAULT_FRAME_RATE,
    source_info: MediaInfo = None,
) -> None:
    """
    Normalize a video to target specifications.
    
    Ensures consistent:
    - Resolution (1080x1920 default)
    - Frame rate (30fps default)
    - Codec (H.264)
    - Pixel format (yuv420p)
    - Audio (exactly one AAC stereo stream; source audio or deterministic silence)
    """
    # Build filter chain
    filters = []
    
    # Scale filter
    if source_info and source_info.width and source_info.height:
        scale = build_scale_filter(
            source_info.width,
            source_info.height,
            target_width,
            target_height,
        )
        if scale:
            filters.append(scale)
    else:
        # Force scale if we don't know source dimensions
        filters.append(f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease")
        filters.append(f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black")
    
    # Frame rate filter
    filters.append(f"fps={target_fps}")
    
    # Pixel format
    filters.append(f"format={PIXEL_FORMAT}")
    
    filter_str = ",".join(filters)
    
    # Every normalized scene has exactly one compatible audio stream. Native
    # source audio is selected explicitly; silent scenes receive an explicit
    # lavfi silence input so concat never depends on optional stream selection.
    has_source_audio = bool(source_info and source_info.has_audio)
    args = ["-y", "-i", str(input_path)]
    if not has_source_audio:
        args.extend([
            "-f", "lavfi",
            "-i", f"anullsrc=r={AUDIO_SAMPLE_RATE}:cl=stereo",
        ])
    args.extend([
        "-map", "0:v:0",
        "-map", "0:a:0" if has_source_audio else "1:a:0",
        "-vf", filter_str,
        "-c:v", VIDEO_CODEC,
        "-preset", VIDEO_PRESET,
        "-crf", str(VIDEO_CRF),
        "-profile:v", VIDEO_PROFILE,
        "-level", VIDEO_LEVEL,
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
        "-ar", str(AUDIO_SAMPLE_RATE),
        "-ac", str(AUDIO_CHANNELS),
        "-af", f"apad=whole_dur={source_info.duration}" if source_info and source_info.duration > 0 else "anull",
        "-t", str(source_info.duration) if source_info and source_info.duration > 0 else "0",
        "-movflags", "+faststart",
        str(output_path),
    ])
    if not source_info or source_info.duration <= 0:
        # A probe-backed assembly always has a duration. This fallback keeps
        # the helper usable in isolation without creating an unbounded file.
        args[args.index("-t") + 1] = "1"
    
    run_ffmpeg(args, f"Normalizing video: {input_path.name}")
    logger.info(f"Normalized: {input_path.name} -> {output_path.name}")


def concatenate_videos(
    input_paths: List[Path],
    output_path: Path,
    temp_dir: Path,
) -> None:
    """
    Concatenate multiple normalized videos.
    
    Uses FFmpeg concat demuxer for frame-accurate concatenation.
    Input videos MUST be normalized first.
    """
    if not input_paths:
        raise ValueError("No input videos to concatenate")
    
    if len(input_paths) == 1:
        # Single normalized video: explicitly retain only the canonical video
        # and first audio stream, preserving the one-stream topology.
        args = [
            "-y", "-i", str(input_paths[0]),
            "-map", "0:v:0",
            "-map", "0:a:0",
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]
        run_ffmpeg(args, "Copying single normalized video")
        logger.info(f"Single normalized video copied to {output_path}")
        return
    
    # Create concat file list
    concat_file = temp_dir / "concat_list.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for path in input_paths:
            # Escape single quotes in path
            escaped = str(path).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    
    args = [
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-map", "0:v:0",
        "-map", "0:a:0",
        "-c", "copy",  # Stream copy since videos are already normalized
        "-movflags", "+faststart",
        str(output_path),
    ]
    
    run_ffmpeg(args, f"Concatenating {len(input_paths)} videos")
    logger.info(f"Concatenated {len(input_paths)} videos -> {output_path}")


def create_test_video(
    output_path: Path,
    duration: float = 3.0,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FRAME_RATE,
    color: str = "blue",
) -> None:
    """
    Create a synthetic test video using FFmpeg.
    
    Useful for testing without real media files.
    """
    args = [
        "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s={width}x{height}:d={duration}:r={fps}",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", VIDEO_CODEC,
        "-preset", "ultrafast",
        "-crf", "23",
        "-pix_fmt", PIXEL_FORMAT,
        "-c:a", AUDIO_CODEC,
        "-b:a", "128k",
        "-shortest",
        str(output_path),
    ]
    
    run_ffmpeg(args, f"Creating test video: {color}, {duration}s")
    logger.info(f"Created test video: {output_path}")
