"""
AUTO Video Maker - Assembly Engine Exceptions

Structured exceptions for the video assembly pipeline.
All exceptions provide clear, actionable error messages.
"""


class AssemblyError(Exception):
    """Base exception for all assembly-related errors."""
    pass


class InvalidProjectError(AssemblyError):
    """Raised when the project definition is invalid."""
    pass


class MediaValidationError(AssemblyError):
    """Raised when a media file fails validation."""
    
    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"Media validation failed for '{path}': {reason}")


class FFmpegNotFoundError(AssemblyError):
    """Raised when FFmpeg or FFprobe is not available."""
    
    def __init__(self, tool: str = "ffmpeg"):
        self.tool = tool
        super().__init__(
            f"{tool} not found. Please install FFmpeg and ensure it's in your PATH. "
            f"On Windows, download from https://ffmpeg.org/download.html and add to PATH, "
            f"or set FFMPEG_PATH/FFPROBE_PATH environment variables."
        )


class FFmpegExecutionError(AssemblyError):
    """Raised when an FFmpeg command fails."""
    
    def __init__(self, command: str, exit_code: int, stderr: str):
        self.command = command
        self.exit_code = exit_code
        self.stderr = stderr
        # Truncate stderr for the message but keep full version available
        stderr_preview = stderr[:500] + "..." if len(stderr) > 500 else stderr
        super().__init__(
            f"FFmpeg command failed (exit code {exit_code}): {stderr_preview}"
        )


class AudioMismatchError(AssemblyError):
    """Raised when audio duration doesn't match video duration."""
    
    def __init__(self, audio_type: str, audio_duration: float, video_duration: float):
        self.audio_type = audio_type
        self.audio_duration = audio_duration
        self.video_duration = video_duration
        diff = audio_duration - video_duration
        direction = "longer" if diff > 0 else "shorter"
        super().__init__(
            f"{audio_type} ({audio_duration:.2f}s) is {abs(diff):.2f}s {direction} "
            f"than video ({video_duration:.2f}s). Review the project configuration."
        )


class CaptionError(AssemblyError):
    """Raised when caption processing fails."""
    
    def __init__(self, reason: str, path: str = None):
        self.reason = reason
        self.path = path
        msg = f"Caption error: {reason}"
        if path:
            msg += f" (file: {path})"
        super().__init__(msg)


class OutputValidationError(AssemblyError):
    """Raised when the output video fails validation."""
    
    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"Output validation failed for '{path}': {reason}")
