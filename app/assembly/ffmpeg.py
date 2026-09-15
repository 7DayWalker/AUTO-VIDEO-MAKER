"""
AUTO Video Maker - FFmpeg Abstraction Layer

Clean abstraction around FFmpeg for video/audio processing.
"""

import json
import os
import subprocess
import shutil
from pathlib import Path
from typing import Optional, List
import logging

from app.assembly.config import (
    FFMPEG_PATH,
    FFPROBE_PATH,
    FFMPEG_TIMEOUT,
)
from app.assembly.exceptions import (
    FFmpegNotFoundError,
    FFmpegExecutionError,
)
from app.assembly.models import MediaInfo
from app.assembly.audio_contract import AudioMediaFacts

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Authoritative media-executable resolution (single source of truth)
#
# Every runtime ffmpeg/ffprobe invocation MUST resolve its executable through
# resolve_ffmpeg()/resolve_ffprobe() instead of assuming a bare command name.
# Resolution order:
#   1. FFMPEG_PATH / FFPROBE_PATH (exact executable file, or a directory
#      containing <name>.exe on Windows / <name> elsewhere);
#   2. for ffprobe: the sibling executable in the resolved ffmpeg's directory
#      (Windows FFmpeg builds ship ffmpeg.exe and ffprobe.exe together);
#   3. normal PATH lookup.
# Candidates are existence-verified; when nothing resolves, the caller must
# fail truthfully rather than fabricate a media artifact.
# ---------------------------------------------------------------------------

_EXE_SUFFIX = ".exe" if os.name == "nt" else ""


def _resolve_candidate(candidate: str, name: str) -> Optional[str]:
    """Resolve one configured candidate: a file, a tool directory, or a name."""
    if not candidate:
        return None
    path = Path(candidate)
    if path.is_file():
        return str(path)
    if path.is_dir():
        inside = path / f"{name}{_EXE_SUFFIX}"
        if inside.is_file():
            return str(inside)
    return None


def _resolve_tool(name: str) -> Optional[str]:
    configured = FFMPEG_PATH if name == "ffmpeg" else FFPROBE_PATH
    resolved = _resolve_candidate(configured, name)
    if resolved:
        return resolved
    if name == "ffprobe":
        # Derive the sibling ffprobe from the resolved ffmpeg directory.
        ffmpeg_resolved = _resolve_tool("ffmpeg")
        if ffmpeg_resolved:
            sibling = Path(ffmpeg_resolved).parent / f"ffprobe{_EXE_SUFFIX}"
            if sibling.is_file():
                return str(sibling)
    return shutil.which(name)


def resolve_ffmpeg() -> Optional[str]:
    """Resolve the ffmpeg executable path, or None when unavailable."""
    return _resolve_tool("ffmpeg")


def resolve_ffprobe() -> Optional[str]:
    """Resolve the ffprobe executable path, or None when unavailable."""
    return _resolve_tool("ffprobe")


class FFmpegRunner:
    """
    Abstraction layer for executing FFmpeg commands.
    
    Handles command execution, logging, and error handling.
    """
    
    def __init__(
        self,
        ffmpeg_path: str = FFMPEG_PATH,
        ffprobe_path: str = FFPROBE_PATH,
        timeout: int = FFMPEG_TIMEOUT,
    ):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.timeout = timeout
        self._ffmpeg_available: Optional[bool] = None
        self._ffprobe_available: Optional[bool] = None
    
    def check_ffmpeg(self) -> bool:
        """Check if FFmpeg is available."""
        if self._ffmpeg_available is not None:
            return self._ffmpeg_available
        
        # Try configured path (exact executable or a tool directory)
        resolved = _resolve_candidate(self.ffmpeg_path, "ffmpeg")
        if resolved and self._check_executable(resolved):
            self.ffmpeg_path = resolved
            self._ffmpeg_available = True
            return True
        
        # Try finding in PATH
        found = shutil.which("ffmpeg")
        if found:
            self.ffmpeg_path = found
            self._ffmpeg_available = True
            return True
        
        self._ffmpeg_available = False
        return False
    
    def check_ffprobe(self) -> bool:
        """Check if FFprobe is available."""
        if self._ffprobe_available is not None:
            return self._ffprobe_available
        
        # Try configured path (exact executable or a tool directory)
        resolved = _resolve_candidate(self.ffprobe_path, "ffprobe")
        if resolved and self._check_executable(resolved):
            self.ffprobe_path = resolved
            self._ffprobe_available = True
            return True

        # Derive the sibling ffprobe from the resolved ffmpeg directory
        # (Windows FFmpeg builds ship ffmpeg.exe and ffprobe.exe together).
        if self.check_ffmpeg():
            sibling = Path(self.ffmpeg_path).parent / f"ffprobe{_EXE_SUFFIX}"
            if sibling.is_file() and self._check_executable(str(sibling)):
                self.ffprobe_path = str(sibling)
                self._ffprobe_available = True
                return True
        
        # Try finding in PATH
        found = shutil.which("ffprobe")
        if found:
            self.ffprobe_path = found
            self._ffprobe_available = True
            return True
        
        self._ffprobe_available = False
        return False
    
    def _check_executable(self, path: str) -> bool:
        """Check if an executable exists and runs."""
        try:
            result = subprocess.run(
                [path, "-version"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
    
    def ensure_ffmpeg(self) -> None:
        """Raise FFmpegNotFoundError if FFmpeg is not available."""
        if not self.check_ffmpeg():
            raise FFmpegNotFoundError("ffmpeg")
    
    def ensure_ffprobe(self) -> None:
        """Raise FFmpegNotFoundError if FFprobe is not available."""
        if not self.check_ffprobe():
            raise FFmpegNotFoundError("ffprobe")
    
    def run(
        self,
        args: List[str],
        description: str = "FFmpeg command",
        timeout: Optional[int] = None,
    ) -> subprocess.CompletedProcess:
        """
        Execute an FFmpeg command.
        
        Args:
            args: Command arguments (without ffmpeg executable).
            description: Human-readable description for logging.
            timeout: Override default timeout.
        
        Returns:
            CompletedProcess with stdout and stderr.
        
        Raises:
            FFmpegNotFoundError: If FFmpeg is not available.
            FFmpegExecutionError: If the command fails.
        """
        self.ensure_ffmpeg()
        
        cmd = [self.ffmpeg_path] + args
        cmd_str = self._format_command(cmd)
        
        logger.info(f"Running: {description}")
        logger.debug(f"Command: {cmd_str}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
            
            if result.returncode != 0:
                logger.error(f"FFmpeg failed: {result.stderr[:500]}")
                raise FFmpegExecutionError(
                    command=cmd_str,
                    exit_code=result.returncode,
                    stderr=result.stderr,
                )
            
            logger.debug(f"FFmpeg completed successfully")
            return result
            
        except subprocess.TimeoutExpired as e:
            raise FFmpegExecutionError(
                command=cmd_str,
                exit_code=-1,
                stderr=f"Command timed out after {timeout or self.timeout} seconds",
            )
    
    def probe(self, path: Path) -> MediaInfo:
        """
        Get media information using ffprobe.
        
        Args:
            path: Path to media file.
        
        Returns:
            MediaInfo with file details.
        
        Raises:
            FFmpegNotFoundError: If FFprobe is not available.
            FFmpegExecutionError: If probing fails.
        """
        self.ensure_ffprobe()
        
        if not path.exists():
            raise FFmpegExecutionError(
                command=f"ffprobe {path}",
                exit_code=-1,
                stderr=f"File not found: {path}",
            )
        
        cmd = [
            self.ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            
            if result.returncode != 0:
                raise FFmpegExecutionError(
                    command=self._format_command(cmd),
                    exit_code=result.returncode,
                    stderr=result.stderr,
                )
            
            data = json.loads(result.stdout)
            return self._parse_probe_result(path, data)
            
        except json.JSONDecodeError as e:
            raise FFmpegExecutionError(
                command=self._format_command(cmd),
                exit_code=-1,
                stderr=f"Failed to parse ffprobe output: {e}",
            )
    
    def _parse_probe_result(self, path: Path, data: dict) -> MediaInfo:
        """Parse ffprobe JSON output into MediaInfo."""
        format_info = data.get("format", {})
        streams = data.get("streams", [])
        
        # Find video and audio streams
        video_stream = None
        audio_stream = None
        for stream in streams:
            codec_type = stream.get("codec_type")
            if codec_type == "video" and video_stream is None:
                video_stream = stream
            elif codec_type == "audio" and audio_stream is None:
                audio_stream = stream

        video_stream_count = sum(
            1 for stream in streams if stream.get("codec_type") == "video"
        )
        audio_stream_count = sum(
            1 for stream in streams if stream.get("codec_type") == "audio"
        )
        
        # Parse duration
        duration = 0.0
        if "duration" in format_info:
            try:
                duration = float(format_info["duration"])
            except (ValueError, TypeError):
                pass
        
        # Parse frame rate
        frame_rate = None
        if video_stream and "r_frame_rate" in video_stream:
            try:
                fr = video_stream["r_frame_rate"]
                if "/" in fr:
                    num, den = fr.split("/")
                    frame_rate = float(num) / float(den)
                else:
                    frame_rate = float(fr)
            except (ValueError, TypeError, ZeroDivisionError):
                pass

        # Parse facts for the first audio stream. Missing/unparseable values
        # remain null; the container duration is not substituted because it may
        # describe the video rather than the audio stream.
        audio_duration = None
        if audio_stream:
            try:
                duration_value = audio_stream.get("duration")
                if duration_value is not None:
                    candidate = float(duration_value)
                    if candidate >= 0:
                        audio_duration = candidate
            except (ValueError, TypeError):
                pass

        sample_rate = None
        channels = None
        if audio_stream:
            try:
                if audio_stream.get("sample_rate") is not None:
                    candidate = int(audio_stream["sample_rate"])
                    if candidate > 0:
                        sample_rate = candidate
            except (ValueError, TypeError):
                pass
            try:
                if audio_stream.get("channels") is not None:
                    candidate = int(audio_stream["channels"])
                    if candidate > 0:
                        channels = candidate
            except (ValueError, TypeError):
                pass

        audio_facts = AudioMediaFacts(
            has_audio=audio_stream is not None,
            audio_codec=audio_stream.get("codec_name") if audio_stream else None,
            audio_duration=audio_duration,
            sample_rate=sample_rate,
            channels=channels,
        )
        
        return MediaInfo(
            path=str(path),
            duration=duration,
            width=video_stream.get("width") if video_stream else None,
            height=video_stream.get("height") if video_stream else None,
            frame_rate=frame_rate,
            video_codec=video_stream.get("codec_name") if video_stream else None,
            audio_codec=audio_stream.get("codec_name") if audio_stream else None,
            audio_duration=audio_duration,
            sample_rate=sample_rate,
            channels=channels,
            has_video=video_stream is not None,
            has_audio=audio_stream is not None,
            video_stream_count=video_stream_count,
            audio_stream_count=audio_stream_count,
            file_size=path.stat().st_size,
            format_name=format_info.get("format_name"),
            audio_facts=audio_facts,
        )
    
    def _format_command(self, cmd: List[str]) -> str:
        """Format command for logging (safe, no secrets)."""
        # Truncate very long arguments
        formatted = []
        for arg in cmd:
            if len(arg) > 200:
                formatted.append(arg[:200] + "...")
            else:
                formatted.append(arg)
        return " ".join(formatted)


# Global instance for convenience
_runner: Optional[FFmpegRunner] = None


def get_ffmpeg_runner() -> FFmpegRunner:
    """Get the global FFmpegRunner instance."""
    global _runner
    if _runner is None:
        _runner = FFmpegRunner()
    return _runner


def check_ffmpeg_available() -> bool:
    """Check if FFmpeg is available on the system."""
    return get_ffmpeg_runner().check_ffmpeg()


def check_ffprobe_available() -> bool:
    """Check if FFprobe is available on the system."""
    return get_ffmpeg_runner().check_ffprobe()


def probe_media(path: Path) -> MediaInfo:
    """Get media information for a file."""
    return get_ffmpeg_runner().probe(path)


def run_ffmpeg(args: List[str], description: str = "FFmpeg command") -> subprocess.CompletedProcess:
    """Run an FFmpeg command."""
    return get_ffmpeg_runner().run(args, description)
