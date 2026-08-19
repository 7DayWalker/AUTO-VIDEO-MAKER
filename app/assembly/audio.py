"""
AUTO Video Maker - Audio Processing

Voiceover and background music mixing using FFmpeg.
"""

from pathlib import Path
import logging

from app.assembly.config import (
    VOICEOVER_VOLUME,
    MUSIC_VOLUME,
    AUDIO_CODEC,
    AUDIO_BITRATE,
    AUDIO_SAMPLE_RATE,
    AUDIO_CHANNELS,
    DURATION_MISMATCH_THRESHOLD,
    VOICEOVER_MAX_OVERAGE,
)
from app.assembly.ffmpeg import run_ffmpeg, probe_media
from app.assembly.exceptions import AudioMismatchError

logger = logging.getLogger(__name__)


def mix_audio(
    video_path: Path,
    output_path: Path,
    voiceover_path: Path = None,
    music_path: Path = None,
    voiceover_volume: float = VOICEOVER_VOLUME,
    music_volume: float = MUSIC_VOLUME,
    video_duration: float = None,
) -> list:
    """
    Mix audio tracks with video.
    
    Args:
        video_path: Input video (may have existing audio).
        output_path: Output video with mixed audio.
        voiceover_path: Primary narration track.
        music_path: Background music track.
        voiceover_volume: Voiceover volume (0.0-2.0).
        music_volume: Music volume (0.0-1.0).
        video_duration: Video duration for sync checking.
    
    Returns:
        List of warnings generated during mixing.
    """
    warnings = []
    
    if not voiceover_path and not music_path:
        # No audio to mix - just copy video
        import shutil
        shutil.copy2(video_path, output_path)
        logger.info("No audio tracks to mix, video copied")
        return warnings
    
    # Get durations for sync checking
    if video_duration is None:
        video_info = probe_media(video_path)
        video_duration = video_info.duration
    
    # Build FFmpeg command
    inputs = ["-i", str(video_path)]
    filter_parts = []
    audio_inputs = []
    input_idx = 1
    
    # Add voiceover
    if voiceover_path:
        vo_info = probe_media(voiceover_path)
        vo_duration = vo_info.duration
        
        # Check duration mismatch
        if vo_duration > video_duration + VOICEOVER_MAX_OVERAGE:
            raise AudioMismatchError("Voiceover", vo_duration, video_duration)
        elif vo_duration > video_duration + DURATION_MISMATCH_THRESHOLD:
            warnings.append(
                f"Voiceover ({vo_duration:.1f}s) is longer than video ({video_duration:.1f}s). "
                f"Audio will be trimmed."
            )
        elif vo_duration < video_duration - DURATION_MISMATCH_THRESHOLD:
            warnings.append(
                f"Voiceover ({vo_duration:.1f}s) is shorter than video ({video_duration:.1f}s). "
                f"Silence will fill the gap."
            )
        
        inputs.extend(["-i", str(voiceover_path)])
        # Apply volume and pad to video duration
        filter_parts.append(
            f"[{input_idx}:a]volume={voiceover_volume},apad=whole_dur={video_duration}[vo]"
        )
        audio_inputs.append("[vo]")
        input_idx += 1
    
    # Add background music
    if music_path:
        music_info = probe_media(music_path)
        music_duration = music_info.duration
        
        inputs.extend(["-i", str(music_path)])
        
        if music_duration < video_duration:
            # Loop music to fill video duration
            filter_parts.append(
                f"[{input_idx}:a]aloop=loop=-1:size=2e+09,atrim=0:{video_duration},"
                f"volume={music_volume},afade=t=out:st={video_duration-2}:d=2[music]"
            )
            logger.info(f"Music will loop ({music_duration:.1f}s -> {video_duration:.1f}s)")
        else:
            # Trim music to video duration with fade out
            filter_parts.append(
                f"[{input_idx}:a]atrim=0:{video_duration},"
                f"volume={music_volume},afade=t=out:st={video_duration-2}:d=2[music]"
            )
        
        audio_inputs.append("[music]")
        input_idx += 1
    
    # Build mix filter
    if len(audio_inputs) == 1:
        # Single audio track
        mix_input = audio_inputs[0]
        filter_parts.append(f"{mix_input}anull[aout]")
    else:
        # Mix multiple tracks
        mix_str = "".join(audio_inputs)
        filter_parts.append(f"{mix_str}amix=inputs={len(audio_inputs)}:normalize=0[aout]")
    
    filter_complex = ";".join(filter_parts)
    
    args = inputs + [
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
        "-ar", str(AUDIO_SAMPLE_RATE),
        "-ac", str(AUDIO_CHANNELS),
        "-shortest",
        "-y",
        str(output_path),
    ]
    
    run_ffmpeg(args, "Mixing audio tracks")
    logger.info(f"Audio mixed -> {output_path}")
    
    return warnings


def create_test_audio(
    output_path: Path,
    duration: float = 5.0,
    frequency: int = 440,
) -> None:
    """
    Create a synthetic test audio file using FFmpeg.
    
    Generates a simple sine wave tone.
    """
    args = [
        "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency={frequency}:duration={duration}",
        "-c:a", AUDIO_CODEC,
        "-b:a", "128k",
        str(output_path),
    ]
    
    run_ffmpeg(args, f"Creating test audio: {frequency}Hz, {duration}s")
    logger.info(f"Created test audio: {output_path}")


def create_test_voiceover(
    output_path: Path,
    duration: float = 5.0,
) -> None:
    """
    Create a synthetic voiceover-like audio file.
    
    Uses a lower frequency to simulate speech-like audio.
    """
    # Use multiple frequencies to create a more complex sound
    args = [
        "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=200:duration={duration}",
        "-af", "volume=0.5,lowpass=f=3000",
        "-c:a", AUDIO_CODEC,
        "-b:a", "128k",
        str(output_path),
    ]
    
    run_ffmpeg(args, f"Creating test voiceover: {duration}s")
    logger.info(f"Created test voiceover: {output_path}")


def create_test_music(
    output_path: Path,
    duration: float = 10.0,
) -> None:
    """
    Create a synthetic background music file.
    
    Uses a chord-like combination of frequencies.
    """
    # Create a simple chord (C major: C4, E4, G4)
    args = [
        "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=261.63:duration={duration}",  # C4
        "-f", "lavfi",
        "-i", f"sine=frequency=329.63:duration={duration}",  # E4
        "-f", "lavfi",
        "-i", f"sine=frequency=392.00:duration={duration}",  # G4
        "-filter_complex", "[0:a][1:a][2:a]amix=inputs=3:normalize=0,volume=0.3[out]",
        "-map", "[out]",
        "-c:a", AUDIO_CODEC,
        "-b:a", "128k",
        str(output_path),
    ]
    
    run_ffmpeg(args, f"Creating test music: {duration}s")
    logger.info(f"Created test music: {output_path}")
