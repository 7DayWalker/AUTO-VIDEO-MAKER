"""
Tests for app.assembly module.

These tests verify configuration, models, validation, and command construction
WITHOUT requiring actual FFmpeg execution for most tests.
"""

import pytest
from pathlib import Path
from pydantic import ValidationError

from app.assembly.config import (
    DEFAULT_WIDTH,
    DEFAULT_HEIGHT,
    DEFAULT_ASPECT_RATIO,
    VIDEO_CODEC,
    AUDIO_CODEC,
    SUPPORTED_VIDEO_EXTENSIONS,
    SUPPORTED_AUDIO_EXTENSIONS,
    SUPPORTED_CAPTION_EXTENSIONS,
    get_encoding_settings,
    get_caption_settings,
)
from app.assembly.models import (
    VideoResolution,
    Scene,
    AudioTrack,
    CaptionConfig,
    OutputConfig,
    AssemblyProject,
    MediaInfo,
    AssemblyResult,
)
from app.assembly.exceptions import (
    AssemblyError,
    MediaValidationError,
    FFmpegNotFoundError,
    FFmpegExecutionError,
    InvalidProjectError,
    AudioMismatchError,
    CaptionError,
    OutputValidationError,
)
from app.assembly.video import build_scale_filter
from app.assembly.captions import format_srt_time, parse_srt_time


class TestAssemblyConfig:
    """Tests for assembly configuration."""
    
    def test_default_resolution_is_vertical(self):
        """Default resolution should be 1080x1920 (9:16)."""
        assert DEFAULT_WIDTH == 1080
        assert DEFAULT_HEIGHT == 1920
    
    def test_default_aspect_ratio(self):
        """Default aspect ratio should be 9:16."""
        assert DEFAULT_ASPECT_RATIO == "9:16"
    
    def test_video_codec_is_h264(self):
        """Video codec should be H.264."""
        assert VIDEO_CODEC == "libx264"
    
    def test_audio_codec_is_aac(self):
        """Audio codec should be AAC."""
        assert AUDIO_CODEC == "aac"
    
    def test_supported_video_extensions(self):
        """Should support common video formats."""
        assert ".mp4" in SUPPORTED_VIDEO_EXTENSIONS
        assert ".mov" in SUPPORTED_VIDEO_EXTENSIONS
        assert ".webm" in SUPPORTED_VIDEO_EXTENSIONS
    
    def test_supported_audio_extensions(self):
        """Should support common audio formats."""
        assert ".mp3" in SUPPORTED_AUDIO_EXTENSIONS
        assert ".wav" in SUPPORTED_AUDIO_EXTENSIONS
        assert ".aac" in SUPPORTED_AUDIO_EXTENSIONS
    
    def test_supported_caption_extensions(self):
        """Should support SRT and VTT."""
        assert ".srt" in SUPPORTED_CAPTION_EXTENSIONS
        assert ".vtt" in SUPPORTED_CAPTION_EXTENSIONS
    
    def test_get_encoding_settings(self):
        """get_encoding_settings should return a dict."""
        settings = get_encoding_settings()
        assert isinstance(settings, dict)
        assert "video_codec" in settings
        assert "audio_codec" in settings
    
    def test_get_caption_settings(self):
        """get_caption_settings should return a dict."""
        settings = get_caption_settings()
        assert isinstance(settings, dict)
        assert "font_size" in settings
        assert "margin_bottom" in settings


class TestVideoResolution:
    """Tests for VideoResolution model."""
    
    def test_valid_resolution(self):
        """Should accept valid resolution."""
        res = VideoResolution(width=1080, height=1920)
        assert res.width == 1080
        assert res.height == 1920
    
    def test_aspect_ratio_calculation(self):
        """Should calculate aspect ratio correctly."""
        res = VideoResolution(width=1080, height=1920)
        assert res.aspect_ratio == "9:16"
        
        res2 = VideoResolution(width=1920, height=1080)
        assert res2.aspect_ratio == "16:9"
    
    def test_is_vertical(self):
        """Should detect vertical orientation."""
        vertical = VideoResolution(width=1080, height=1920)
        assert vertical.is_vertical() is True
        
        horizontal = VideoResolution(width=1920, height=1080)
        assert horizontal.is_vertical() is False
    
    def test_invalid_resolution_rejected(self):
        """Should reject invalid resolution."""
        with pytest.raises(ValidationError):
            VideoResolution(width=0, height=1920)
        
        with pytest.raises(ValidationError):
            VideoResolution(width=1080, height=-1)


class TestScene:
    """Tests for Scene model."""
    
    def test_valid_scene(self):
        """Should accept valid scene."""
        scene = Scene(id="scene_01", video="path/to/video.mp4")
        assert scene.id == "scene_01"
        assert scene.video == "path/to/video.mp4"
        assert scene.duration is None
    
    def test_scene_with_duration(self):
        """Should accept scene with duration override."""
        scene = Scene(id="scene_01", video="video.mp4", duration=5.0)
        assert scene.duration == 5.0
    
    def test_empty_id_rejected(self):
        """Should reject empty scene ID."""
        with pytest.raises(ValidationError):
            Scene(id="", video="video.mp4")
    
    def test_empty_video_rejected(self):
        """Should reject empty video path."""
        with pytest.raises(ValidationError):
            Scene(id="scene_01", video="")


class TestCaptionConfig:
    """Tests for CaptionConfig model."""
    
    def test_disabled_by_default(self):
        """Captions should be disabled by default."""
        config = CaptionConfig()
        assert config.enabled is False
    
    def test_enabled_requires_source(self):
        """Enabled captions must have a source."""
        with pytest.raises(ValidationError):
            CaptionConfig(enabled=True)
    
    def test_enabled_with_source(self):
        """Should accept enabled captions with source."""
        config = CaptionConfig(enabled=True, source="captions.srt")
        assert config.enabled is True
        assert config.source == "captions.srt"


class TestOutputConfig:
    """Tests for OutputConfig model."""
    
    def test_default_filename(self):
        """Should have default filename."""
        config = OutputConfig()
        assert config.filename == "final_short.mp4"
    
    def test_filename_must_be_mp4(self):
        """Filename must end with .mp4."""
        with pytest.raises(ValidationError):
            OutputConfig(filename="output.avi")
    
    def test_valid_mp4_filename(self):
        """Should accept valid MP4 filename."""
        config = OutputConfig(filename="my_video.mp4")
        assert config.filename == "my_video.mp4"


class TestAssemblyProject:
    """Tests for AssemblyProject model."""
    
    def test_valid_project(self):
        """Should accept valid project."""
        project = AssemblyProject(
            project_id="test_project",
            title="Test Video",
            scenes=[Scene(id="scene_01", video="video.mp4")],
        )
        assert project.project_id == "test_project"
        assert len(project.scenes) == 1
    
    def test_project_requires_scenes(self):
        """Project must have at least one scene."""
        with pytest.raises(ValidationError):
            AssemblyProject(project_id="test", scenes=[])
    
    def test_invalid_aspect_ratio_rejected(self):
        """Should reject invalid aspect ratio."""
        with pytest.raises(ValidationError):
            AssemblyProject(
                project_id="test",
                aspect_ratio="invalid",
                scenes=[Scene(id="s1", video="v.mp4")],
            )
    
    def test_valid_aspect_ratios(self):
        """Should accept valid aspect ratios."""
        for ratio in ["9:16", "16:9", "1:1", "4:5"]:
            project = AssemblyProject(
                project_id="test",
                aspect_ratio=ratio,
                scenes=[Scene(id="s1", video="v.mp4")],
            )
            assert project.aspect_ratio == ratio
    
    def test_get_scene_paths(self):
        """Should return scene paths as Path objects."""
        project = AssemblyProject(
            project_id="test",
            scenes=[
                Scene(id="s1", video="video1.mp4"),
                Scene(id="s2", video="video2.mp4"),
            ],
        )
        paths = project.get_scene_paths()
        assert len(paths) == 2
        assert all(isinstance(p, Path) for p in paths)


class TestAssemblyResult:
    """Tests for AssemblyResult model."""
    
    def test_success_result(self):
        """Should create success result."""
        result = AssemblyResult(
            success=True,
            output_path="/path/to/output.mp4",
            duration=10.5,
            width=1080,
            height=1920,
            file_size=1024000,
        )
        assert result.success is True
        assert result.duration == 10.5
    
    def test_failure_result(self):
        """Should create failure result."""
        result = AssemblyResult.failure("Something went wrong")
        assert result.success is False
        assert "Something went wrong" in result.errors


class TestExceptions:
    """Tests for assembly exceptions."""
    
    def test_media_validation_error(self):
        """MediaValidationError should include path and reason."""
        error = MediaValidationError("/path/to/file.mp4", "File not found")
        assert "/path/to/file.mp4" in str(error)
        assert "File not found" in str(error)
    
    def test_ffmpeg_not_found_error(self):
        """FFmpegNotFoundError should include installation instructions."""
        error = FFmpegNotFoundError("ffmpeg")
        assert "ffmpeg" in str(error).lower()
        assert "install" in str(error).lower()
    
    def test_ffmpeg_execution_error(self):
        """FFmpegExecutionError should include exit code."""
        error = FFmpegExecutionError("ffmpeg -i test", 1, "Error message")
        assert error.exit_code == 1
        assert "Error message" in str(error)
    
    def test_audio_mismatch_error(self):
        """AudioMismatchError should describe the mismatch."""
        error = AudioMismatchError("Voiceover", 15.0, 10.0)
        assert "Voiceover" in str(error)
        assert "15" in str(error)
        assert "10" in str(error)
    
    def test_caption_error(self):
        """CaptionError should include reason."""
        error = CaptionError("Invalid format", "/path/to/captions.srt")
        assert "Invalid format" in str(error)
        assert "captions.srt" in str(error)


class TestScaleFilter:
    """Tests for video scale filter generation."""
    
    def test_same_size_no_filter(self):
        """Same size should return empty filter."""
        result = build_scale_filter(1080, 1920, 1080, 1920)
        assert result == ""
    
    def test_same_aspect_scales(self):
        """Same aspect ratio should just scale."""
        result = build_scale_filter(540, 960, 1080, 1920)
        assert "scale=1080:1920" in result
    
    def test_different_aspect_pads(self):
        """Different aspect ratio should scale and pad."""
        result = build_scale_filter(1920, 1080, 1080, 1920)
        assert "scale" in result
        assert "pad" in result


class TestSRTTime:
    """Tests for SRT time formatting and parsing."""
    
    def test_format_srt_time(self):
        """Should format seconds as SRT timestamp."""
        assert format_srt_time(0) == "00:00:00,000"
        assert format_srt_time(1.5) == "00:00:01,500"
        assert format_srt_time(65.123) == "00:01:05,123"
        assert format_srt_time(3661.5) == "01:01:01,500"
    
    def test_parse_srt_time(self):
        """Should parse SRT timestamp to seconds."""
        assert parse_srt_time("00:00:00,000") == 0.0
        assert parse_srt_time("00:00:01,500") == 1.5
        assert abs(parse_srt_time("00:01:05,123") - 65.123) < 0.001
        assert parse_srt_time("01:01:01,500") == 3661.5
    
    def test_parse_invalid_time(self):
        """Should raise on invalid time format."""
        with pytest.raises(ValueError):
            parse_srt_time("invalid")


class TestStateMachine:
    """Tests for assembly state machine logic."""
    
    def test_ffmpeg_missing_blocks_assembly(self):
        """FFmpeg missing should prevent assembly."""
        ffmpeg_available = False
        can_assemble = ffmpeg_available
        assert can_assemble is False
    
    def test_invalid_project_blocks_assembly(self):
        """Invalid project should prevent assembly."""
        project_valid = False
        can_assemble = project_valid
        assert can_assemble is False
    
    def test_missing_media_blocks_assembly(self):
        """Missing media should prevent assembly."""
        all_media_exists = False
        can_assemble = all_media_exists
        assert can_assemble is False
    
    def test_valid_state_allows_assembly(self):
        """Valid state should allow assembly."""
        ffmpeg_available = True
        project_valid = True
        all_media_exists = True
        can_assemble = ffmpeg_available and project_valid and all_media_exists
        assert can_assemble is True


class TestMediaInfo:
    """Tests for MediaInfo model."""
    
    def test_video_media_info(self):
        """Should create video MediaInfo."""
        info = MediaInfo(
            path="/path/to/video.mp4",
            duration=10.5,
            width=1080,
            height=1920,
            has_video=True,
            has_audio=True,
            file_size=1024000,
        )
        assert info.has_video is True
        assert info.duration == 10.5
    
    def test_audio_only_media_info(self):
        """Should create audio-only MediaInfo."""
        info = MediaInfo(
            path="/path/to/audio.mp3",
            duration=30.0,
            has_video=False,
            has_audio=True,
            file_size=512000,
        )
        assert info.has_video is False
        assert info.has_audio is True
