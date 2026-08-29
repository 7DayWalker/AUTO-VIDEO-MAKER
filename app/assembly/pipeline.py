"""
AUTO Video Maker - Assembly Pipeline

High-level orchestration of the video assembly process.
"""

import shutil
import logging
from pathlib import Path
from typing import List
from datetime import datetime

from app.assembly.config import (
    ASSEMBLY_OUTPUT_DIR,
    ASSEMBLY_TEMP_DIR,
    ensure_assembly_directories,
)
from app.assembly.models import AssemblyProject, AssemblyResult, MediaInfo
from app.assembly.audio_contract import select_audio_inputs
from app.assembly.validator import validate_project
from app.assembly.ffmpeg import check_ffmpeg_available, check_ffprobe_available, probe_media
from app.assembly.video import normalize_video, concatenate_videos
from app.assembly.audio import mix_audio
from app.assembly.captions import burn_captions
from app.assembly.exceptions import (
    AssemblyError,
    FFmpegNotFoundError,
    OutputValidationError,
)

logger = logging.getLogger(__name__)


def assemble_project(
    project: AssemblyProject,
    base_dir: Path = None,
    output_dir: Path = None,
) -> AssemblyResult:
    """
    Assemble a complete video from project definition.
    
    Pipeline:
    1. Validate project and media
    2. Normalize video clips
    3. Concatenate scenes
    4. Mix audio (voiceover + music)
    5. Burn captions
    6. Validate output
    
    Args:
        project: The assembly project definition.
        base_dir: Base directory for resolving relative paths.
        output_dir: Output directory (defaults to ASSEMBLY_OUTPUT_DIR).
    
    Returns:
        AssemblyResult with success status and output details.
    """
    base_dir = base_dir or Path.cwd()
    output_dir = output_dir or ASSEMBLY_OUTPUT_DIR
    
    # Ensure directories exist
    ensure_assembly_directories()
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Create unique temp directory for this assembly
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_dir = ASSEMBLY_TEMP_DIR / f"{project.project_id}_{timestamp}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    warnings = []
    
    try:
        logger.info(f"Starting assembly: {project.project_id}")
        logger.info(f"Title: {project.title}")
        
        # Step 1: Check FFmpeg
        logger.info("Step 1: Checking FFmpeg...")
        if not check_ffmpeg_available():
            raise FFmpegNotFoundError("ffmpeg")
        if not check_ffprobe_available():
            raise FFmpegNotFoundError("ffprobe")
        logger.info("FFmpeg available")
        
        # Step 2: Validate project
        logger.info("Step 2: Validating project...")
        scene_infos, validation_warnings = validate_project(project, base_dir)
        warnings.extend(validation_warnings)
        logger.info(f"Validated {len(scene_infos)} scenes")
        
        # Step 3: Normalize videos
        logger.info("Step 3: Normalizing videos...")
        normalized_paths = []
        for i, scene in enumerate(project.scenes):
            video_path = Path(scene.video)
            if not video_path.is_absolute():
                video_path = base_dir / video_path
            
            normalized_path = temp_dir / f"normalized_{i:03d}.mp4"
            normalize_video(
                video_path,
                normalized_path,
                target_width=project.resolution.width,
                target_height=project.resolution.height,
                source_info=scene_infos[i],
            )
            normalized_paths.append(normalized_path)
        logger.info(f"Normalized {len(normalized_paths)} videos")
        
        # Step 4: Concatenate
        logger.info("Step 4: Concatenating scenes...")
        concatenated_path = temp_dir / "concatenated.mp4"
        concatenate_videos(normalized_paths, concatenated_path, temp_dir)
        
        # Get concatenated video duration
        concat_info = probe_media(concatenated_path)
        video_duration = concat_info.duration
        logger.info(f"Concatenated duration: {video_duration:.2f}s")
        
        # Step 5: Mix audio
        logger.info("Step 5: Mixing audio...")
        audio_mixed_path = temp_dir / "audio_mixed.mp4"
        
        voiceover_path = None
        if project.voiceover:
            voiceover_path = Path(project.voiceover)
            if not voiceover_path.is_absolute():
                voiceover_path = base_dir / voiceover_path
        
        music_path = None
        if project.background_music:
            music_path = Path(project.background_music)
            if not music_path.is_absolute():
                music_path = base_dir / music_path

        # Native eligibility is a media fact, not a provider decision. A
        # normalized concat carries one audio stream even for silent scenes;
        # only a non-synthetic scene fact makes it eligible here.
        scene_native_audio = (
            concatenated_path
            if any(
                info.audio_facts and info.audio_facts.has_native_audio
                for info in scene_infos
            )
            else None
        )
        selection = select_audio_inputs(
            project.audio_policy,
            scene_native_audio=scene_native_audio,
            narration=voiceover_path,
            music=music_path,
        )
        if selection.fallback_used:
            warnings.append(
                "Native audio was unavailable; native_only fell back to narration."
            )
        logger.info(
            "Audio policy=%s native=%s narration=%s music=%s",
            selection.policy.value,
            bool(selection.scene_native_audio),
            bool(selection.narration),
            bool(selection.music),
        )
        
        audio_warnings = mix_audio(
            concatenated_path,
            audio_mixed_path,
            scene_native_audio=selection.scene_native_audio,
            voiceover_path=selection.narration,
            music_path=selection.music,
            music_volume=project.music_volume,
            native_audio_volume=project.native_audio_volume,
            audio_policy=selection.policy,
            video_duration=video_duration,
        )
        warnings.extend(audio_warnings)
        
        # Step 6: Burn captions
        if project.captions.enabled and project.captions.source:
            logger.info("Step 6: Burning captions...")
            caption_path = Path(project.captions.source)
            if not caption_path.is_absolute():
                caption_path = base_dir / caption_path
            
            captioned_path = temp_dir / "captioned.mp4"
            burn_captions(
                audio_mixed_path,
                captioned_path,
                caption_path,
                project.captions,
            )
            final_temp_path = captioned_path
        else:
            logger.info("Step 6: Skipping captions (not enabled)")
            final_temp_path = audio_mixed_path
        
        # Step 7: Move to output
        logger.info("Step 7: Finalizing output...")
        output_path = project.get_output_path(output_dir)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(final_temp_path, output_path)
        
        # Step 8: Validate output
        logger.info("Step 8: Validating output...")
        output_info = validate_output(output_path, project)
        
        logger.info(f"Assembly complete: {output_path}")
        logger.info(f"Duration: {output_info.duration:.2f}s")
        logger.info(f"Resolution: {output_info.width}x{output_info.height}")
        logger.info(f"Size: {output_info.file_size / (1024*1024):.2f} MB")
        
        return AssemblyResult.from_output(output_path, output_info, warnings)
        
    except AssemblyError as e:
        logger.error(f"Assembly failed: {e}")
        return AssemblyResult.failure(str(e))
    except Exception as e:
        logger.exception(f"Unexpected error during assembly: {e}")
        return AssemblyResult.failure(f"Unexpected error: {e}")
    finally:
        # Cleanup temp directory
        try:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
                logger.debug(f"Cleaned up temp directory: {temp_dir}")
        except Exception as e:
            logger.warning(f"Failed to cleanup temp directory: {e}")


def validate_output(output_path: Path, project: AssemblyProject) -> MediaInfo:
    """
    Validate the final output video.
    
    Checks:
    - File exists
    - File size > 0
    - Valid MP4 container
    - Has video stream
    - Has audio stream
    - Correct resolution
    """
    if not output_path.exists():
        raise OutputValidationError(str(output_path), "File does not exist")
    
    if output_path.stat().st_size == 0:
        raise OutputValidationError(str(output_path), "File is empty")
    
    # Check MP4 header
    with open(output_path, "rb") as f:
        header = f.read(12)
    if b"ftyp" not in header:
        raise OutputValidationError(str(output_path), "Invalid MP4 container")
    
    # Probe for detailed validation
    info = probe_media(output_path)
    
    if not info.has_video:
        raise OutputValidationError(str(output_path), "No video stream")
    
    if not info.has_audio:
        logger.warning("Output has no audio stream")
    elif info.audio_stream_count != 1:
        raise OutputValidationError(
            str(output_path),
            f"Expected exactly one audio stream, found {info.audio_stream_count}",
        )
    
    # Check resolution
    if info.width != project.resolution.width or info.height != project.resolution.height:
        logger.warning(
            f"Output resolution {info.width}x{info.height} differs from "
            f"target {project.resolution.width}x{project.resolution.height}"
        )
    
    return info


def load_project_from_json(json_path: Path) -> AssemblyProject:
    """
    Load an assembly project from a JSON file.
    
    Args:
        json_path: Path to the JSON project file.
    
    Returns:
        AssemblyProject instance.
    """
    import json
    
    if not json_path.exists():
        raise FileNotFoundError(f"Project file not found: {json_path}")
    
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    return AssemblyProject(**data)
