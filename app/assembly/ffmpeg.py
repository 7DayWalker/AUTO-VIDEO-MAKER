"""
AUTO Video Maker - FFmpeg Abstraction Layer

Clean abstraction around FFmpeg for video/audio processing.
"""

import json
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

logger = logging.getLogger(__name__)


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
        
        # Try configured path first
        if self._check_executable(self.ffmpeg_path):
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
        
        # Try configured path first
        if self._check_executable(self.ffprobe_path):
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
        
        return MediaInfo(
            path=str(path),
            duration=duration,
            width=video_stream.get("width") if video_stream else None,
            height=video_stream.get("height") if video_stream else None,
            frame_rate=frame_rate,
            video_codec=video_stream.get("codec_name") if video_stream else None,
            audio_codec=audio_stream.get("codec_name") if audio_stream else None,
            has_video=video_stream is not None,
            has_audio=audio_stream is not None,
            file_size=path.stat().st_size,
            format_name=format_info.get("format_name"),
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
