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
from app.assembly.audio_contract import (
    AudioPolicy,
    DEFAULT_AUDIO_POLICY,
    select_audio_inputs,
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
    scene_native_audio: Path = None,
    native_audio_volume: float = 1.0,
    audio_policy: AudioPolicy = DEFAULT_AUDIO_POLICY,
) -> list:
    """
    Mix audio tracks with video.
    
    Args:
        video_path: Input video (may have existing audio).
        output_path: Output video with mixed audio.
        scene_native_audio: Explicit native scene audio input. Usually this is
            the concatenated scene video itself, but it may be a separate
            validated audio file.
        voiceover_path: Primary narration track.
        music_path: Background music track.
        voiceover_volume: Voiceover volume (0.0-2.0).
        music_volume: Music volume (0.0-1.0).
        native_audio_volume: Native scene-audio volume (0.0-2.0).
        audio_policy: Policy used when selecting native audio versus narration.
        video_duration: Video duration for sync checking.
    
    Returns:
        List of warnings generated during mixing.
    """
    warnings = []
    
    # Get durations for sync checking
    if video_duration is None:
        video_info = probe_media(video_path)
        video_duration = video_info.duration
    
    selection = select_audio_inputs(
        audio_policy,
        scene_native_audio=scene_native_audio,
        narration=voiceover_path,
        music=music_path,
    )
    if selection.fallback_used:
        warnings.append(
            "Native audio was unavailable; native_only fell back to narration."
        )

    # Build FFmpeg command. Stream 0 is always the video carrier. Audio is
    # never inherited by default: every selected input is mapped through an
    # explicit filter output below.
    inputs = ["-i", str(video_path)]
    filter_parts = []
    audio_inputs = []
    input_idx = 1

    if not selection.scene_native_audio and not selection.narration and not selection.music:
        args = [
            "-y",
            "-i", str(video_path),
            "-map", "0:v:0",
            "-c:v", "copy",
            "-an",
            "-t", str(video_duration),
            str(output_path),
        ]
        run_ffmpeg(args, "Writing video without selected audio")
        logger.info("No selected audio tracks, video written without audio")
        return warnings

    # Native scene audio is normally carried by input 0. A separate path is
    # supported explicitly so the mixer never depends on default stream
    # selection.
    if selection.scene_native_audio:
        native_path = Path(selection.scene_native_audio)
        if native_path.resolve() == Path(video_path).resolve():
            native_input_idx = 0
        else:
            inputs.extend(["-i", str(native_path)])
            native_input_idx = input_idx
            input_idx += 1
        filter_parts.append(
            f"[{native_input_idx}:a:0]atrim=0:{video_duration},"
            f"asetpts=PTS-STARTPTS,apad=whole_dur={video_duration},"
            f"atrim=0:{video_duration},volume={native_audio_volume}[native]"
        )
        audio_inputs.append("[native]")
    
    # Add voiceover
    if selection.narration:
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
        # Apply volume, trim, and pad to exactly the video duration.
        filter_parts.append(
            f"[{input_idx}:a:0]atrim=0:{video_duration},"
            f"asetpts=PTS-STARTPTS,volume={voiceover_volume},"
            f"apad=whole_dur={video_duration},atrim=0:{video_duration}[vo]"
        )
        audio_inputs.append("[vo]")
        input_idx += 1
    
    # Add background music
    if selection.music:
        music_info = probe_media(selection.music)
        music_duration = music_info.duration
        
        inputs.extend(["-i", str(selection.music)])
        
        if music_duration < video_duration:
            # Loop music to fill video duration
            filter_parts.append(
                f"[{input_idx}:a:0]aloop=loop=-1:size=2e+09,"
                f"atrim=0:{video_duration},volume={music_volume}[music]"
            )
            logger.info(f"Music will loop ({music_duration:.1f}s -> {video_duration:.1f}s)")
        else:
            # Trim music to video duration with fade out
            filter_parts.append(
                f"[{input_idx}:a:0]atrim=0:{video_duration},"
                f"asetpts=PTS-STARTPTS,volume={music_volume}[music]"
            )
        
        audio_inputs.append("[music]")
        input_idx += 1
    
    # Build mix filter
    if len(audio_inputs) == 1:
        # Single audio track
        mix_input = audio_inputs[0]
        filter_parts.append(
            f"{mix_input}aresample={AUDIO_SAMPLE_RATE},atrim=0:{video_duration},"
            f"asetpts=PTS-STARTPTS[aout]"
        )
    else:
        # Mix multiple tracks
        mix_str = "".join(audio_inputs)
        filter_parts.append(
            f"{mix_str}amix=inputs={len(audio_inputs)}:duration=longest:"
            f"normalize=0:dropout_transition=0,"
            f"atrim=0:{video_duration},asetpts=PTS-STARTPTS[aout]"
        )
    
    filter_complex = ";".join(filter_parts)
    
    args = inputs + [
        "-filter_complex", filter_complex,
        "-map", "0:v:0",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
        "-ar", str(AUDIO_SAMPLE_RATE),
        "-ac", str(AUDIO_CHANNELS),
        "-t", str(video_duration),
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
    # Simple sine wave at speech-like frequency
    # Use libmp3lame for .mp3 output
    ext = output_path.suffix.lower()
    if ext == ".mp3":
        codec = "libmp3lame"
    else:
        codec = AUDIO_CODEC
    
    args = [
        "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=200:duration={duration}",
        "-c:a", codec,
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
    # Use libmp3lame for .mp3 output
    ext = output_path.suffix.lower()
    if ext == ".mp3":
        codec = "libmp3lame"
    else:
        codec = AUDIO_CODEC
    
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
        "-c:a", codec,
        "-b:a", "128k",
        str(output_path),
    ]
    
    run_ffmpeg(args, f"Creating test music: {duration}s")
    logger.info(f"Created test music: {output_path}")
