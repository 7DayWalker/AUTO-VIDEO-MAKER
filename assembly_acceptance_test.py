#!/usr/bin/env python
"""
AUTO Video Maker - Phase 2A Acceptance Test

Creates synthetic test assets and runs the complete assembly pipeline.
Verifies the output meets all acceptance criteria.

Usage:
    python assembly_acceptance_test.py
"""

import sys
import json
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.assembly.ffmpeg import check_ffmpeg_available, check_ffprobe_available, probe_media
from app.assembly.video import create_test_video
from app.assembly.audio import create_test_voiceover, create_test_music
from app.assembly.captions import create_test_srt
from app.assembly.pipeline import assemble_project
from app.assembly.models import AssemblyProject, Scene, CaptionConfig, OutputConfig
from app.assembly.config import ASSEMBLY_TEST_OUTPUT_DIR


def log(msg: str) -> None:
    """Print timestamped log message."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")


def main() -> int:
    print()
    print("=" * 60)
    print("PHASE 2A - ASSEMBLY ENGINE ACCEPTANCE TEST")
    print("=" * 60)
    print()
    
    # Step 1: Check FFmpeg
    log("Step 1: Checking FFmpeg availability...")
    if not check_ffmpeg_available():
        log("ERROR: FFmpeg not found.")
        log("Please install FFmpeg: https://ffmpeg.org/download.html")
        return 1
    if not check_ffprobe_available():
        log("ERROR: FFprobe not found.")
        return 1
    log("FFmpeg: AVAILABLE")
    print()
    
    # Step 2: Create test assets directory
    log("Step 2: Creating test assets...")
    test_assets_dir = ASSEMBLY_TEST_OUTPUT_DIR / "test_assets"
    test_assets_dir.mkdir(parents=True, exist_ok=True)
    
    # Create 4 synthetic video clips (different colors)
    colors = ["red", "green", "blue", "yellow"]
    scene_paths = []
    total_video_duration = 0.0
    
    for i, color in enumerate(colors):
        video_path = test_assets_dir / f"scene_{i+1:02d}_{color}.mp4"
        duration = 3.0  # 3 seconds each
        log(f"  Creating scene {i+1}: {color}, {duration}s")
        create_test_video(video_path, duration=duration, color=color)
        scene_paths.append(video_path)
        total_video_duration += duration
    
    log(f"  Total video duration: {total_video_duration}s")
    
    # Create voiceover (slightly shorter than video)
    voiceover_path = test_assets_dir / "voiceover.mp3"
    voiceover_duration = total_video_duration - 1.0  # 11 seconds
    log(f"  Creating voiceover: {voiceover_duration}s")
    create_test_voiceover(voiceover_path, duration=voiceover_duration)
    
    # Create background music (longer than video, will be trimmed)
    music_path = test_assets_dir / "music.mp3"
    music_duration = total_video_duration + 5.0  # 17 seconds
    log(f"  Creating background music: {music_duration}s")
    create_test_music(music_path, duration=music_duration)
    
    # Create captions
    captions_path = test_assets_dir / "captions.srt"
    log(f"  Creating captions: 4 entries")
    create_test_srt(captions_path, duration=total_video_duration, num_captions=4)
    
    log("Test assets created.")
    print()
    
    # Step 3: Create project definition
    log("Step 3: Creating project definition...")
    project = AssemblyProject(
        project_id="acceptance_test",
        title="Phase 2A Acceptance Test Video",
        aspect_ratio="9:16",
        scenes=[
            Scene(id=f"scene_{i+1:02d}", video=str(path))
            for i, path in enumerate(scene_paths)
        ],
        voiceover=str(voiceover_path),
        background_music=str(music_path),
        music_volume=0.15,
        captions=CaptionConfig(enabled=True, source=str(captions_path)),
        output=OutputConfig(filename="final_short.mp4"),
    )
    
    # Save project JSON for reference
    project_json_path = test_assets_dir / "project.json"
    with open(project_json_path, "w", encoding="utf-8") as f:
        json.dump(project.model_dump(), f, indent=2)
    log(f"Project saved: {project_json_path}")
    print()
    
    # Step 4: Run assembly
    log("Step 4: Running assembly pipeline...")
    print()
    
    result = assemble_project(
        project,
        base_dir=test_assets_dir,
        output_dir=ASSEMBLY_TEST_OUTPUT_DIR,
    )
    
    print()
    
    # Step 5: Validate result
    log("Step 5: Validating result...")
    
    if not result.success:
        log("ASSEMBLY FAILED")
        for error in result.errors:
            log(f"  Error: {error}")
        return 1
    
    output_path = Path(result.output_path)
    
    # Check file exists
    if not output_path.exists():
        log("FAILED: Output file does not exist")
        return 1
    log(f"  File exists: YES")
    
    # Check file size
    if result.file_size == 0:
        log("FAILED: Output file is empty")
        return 1
    log(f"  File size: {result.file_size / (1024*1024):.2f} MB")
    
    # Check MP4 container
    with open(output_path, "rb") as f:
        header = f.read(12)
    if b"ftyp" not in header:
        log("FAILED: Invalid MP4 container")
        return 1
    log(f"  MP4 container: VALID")
    
    # Check resolution
    if result.width != 1080 or result.height != 1920:
        log(f"WARNING: Resolution {result.width}x{result.height} differs from target 1080x1920")
    else:
        log(f"  Resolution: {result.width}x{result.height} (9:16)")
    
    # Check duration
    log(f"  Duration: {result.duration:.2f}s")
    
    # Probe for detailed validation
    info = probe_media(output_path)
    
    # Check video stream
    if not info.has_video:
        log("FAILED: No video stream")
        return 1
    log(f"  Video stream: YES ({info.video_codec})")
    
    # Check audio stream
    if not info.has_audio:
        log("FAILED: No audio stream")
        return 1
    log(f"  Audio stream: YES ({info.audio_codec})")
    
    # Check aspect ratio
    if info.width and info.height:
        aspect = info.width / info.height
        expected_aspect = 9 / 16
        if abs(aspect - expected_aspect) < 0.01:
            log(f"  Aspect ratio: 9:16 (correct)")
        else:
            log(f"  Aspect ratio: {aspect:.3f} (expected {expected_aspect:.3f})")
    
    # Report warnings
    if result.warnings:
        print()
        log("Warnings:")
        for warning in result.warnings:
            log(f"  - {warning}")
    
    print()
    print("=" * 60)
    print("PHASE 2A ACCEPTANCE TEST: PASSED ✓")
    print("=" * 60)
    print()
    print(f"Output: {output_path}")
    print(f"Duration: {result.duration:.2f}s")
    print(f"Resolution: {result.width}x{result.height}")
    print(f"Size: {result.file_size / (1024*1024):.2f} MB")
    print()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
