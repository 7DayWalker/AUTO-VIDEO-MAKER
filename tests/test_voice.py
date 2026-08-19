"""
Tests for app.voice module.

All tests run WITHOUT internet access or TTS API calls.
"""

import pytest
import asyncio
from pathlib import Path
from pydantic import ValidationError

from app.voice.config import (
    TTS_PROVIDER,
    TTS_VOICE,
    TTS_RATE,
    TTS_OUTPUT_FORMAT,
    SUPPORTED_PROVIDERS,
    SUPPORTED_OUTPUT_FORMATS,
    VOICE_DURATION_WARNING_THRESHOLD,
)
from app.voice.models import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceMetadata,
    SceneTiming,
    VoiceTiming,
    NarrationSegment,
    AssembledNarration,
)
from app.voice.exceptions import (
    VoiceError,
    VoiceProviderError,
    VoiceGenerationError,
    VoiceValidationError,
    VoiceTimingError,
    VoiceNotFoundError,
    NarrationExtractionError,
)
from app.voice.timing import (
    calculate_scene_timings,
    create_voice_timing,
    check_duration_mismatch,
    get_scene_at_time,
)
from app.voice.generator import (
    clean_narration_text,
    extract_narration_segments,
    assemble_narration,
    get_provider,
    VoiceGenerator,
)
from app.voice.providers.base import BaseVoiceProvider, VoiceInfo
from app.voice.providers.mock import MockVoiceProvider, MOCK_VOICES
from app.content.models import ProductionPlan, Scene


# ===== Model Tests =====

class TestVoiceGenerationRequest:
    """Tests for VoiceGenerationRequest model."""
    
    def test_valid_request(self):
        """Should accept valid request."""
        request = VoiceGenerationRequest(
            project_id="test_001",
            text="Hello world",
        )
        assert request.project_id == "test_001"
        assert request.text == "Hello world"
        assert request.provider == TTS_PROVIDER
    
    def test_request_with_all_fields(self):
        """Should accept request with all fields."""
        request = VoiceGenerationRequest(
            project_id="test_001",
            text="Hello world",
            provider="mock",
            voice="test-voice",
            rate="+10%",
            pitch="+5Hz",
            volume="-5%",
            output_format="wav",
        )
        assert request.voice == "test-voice"
        assert request.output_format == "wav"
    
    def test_empty_text_rejected(self):
        """Should reject empty text."""
        with pytest.raises(ValidationError):
            VoiceGenerationRequest(project_id="test", text="")
    
    def test_empty_project_id_rejected(self):
        """Should reject empty project ID."""
        with pytest.raises(ValidationError):
            VoiceGenerationRequest(project_id="", text="Hello")
    
    def test_invalid_provider_rejected(self):
        """Should reject invalid provider."""
        with pytest.raises(ValidationError):
            VoiceGenerationRequest(
                project_id="test",
                text="Hello",
                provider="invalid_provider",
            )
    
    def test_invalid_format_rejected(self):
        """Should reject invalid output format."""
        with pytest.raises(ValidationError):
            VoiceGenerationRequest(
                project_id="test",
                text="Hello",
                output_format="invalid",
            )


class TestVoiceGenerationResult:
    """Tests for VoiceGenerationResult model."""
    
    def test_success_result(self):
        """Should create success result."""
        result = VoiceGenerationResult.from_generation(
            output_path=Path("/test/output.mp3"),
            provider="mock",
            voice="test-voice",
            duration=10.5,
            file_size=50000,
            format="mp3",
        )
        assert result.success is True
        assert result.duration == 10.5
    
    def test_failure_result(self):
        """Should create failure result."""
        result = VoiceGenerationResult.failure(
            error="Test error",
            provider="mock",
        )
        assert result.success is False
        assert result.error == "Test error"


class TestVoiceMetadata:
    """Tests for VoiceMetadata model."""
    
    def test_valid_metadata(self):
        """Should create valid metadata."""
        metadata = VoiceMetadata(
            project_id="test_001",
            provider="mock",
            voice="test-voice",
            rate="+0%",
            pitch="+0Hz",
            volume="+0%",
            format="mp3",
            duration=10.5,
            file_size=50000,
        )
        assert metadata.project_id == "test_001"
        assert metadata.duration == 10.5


class TestSceneTiming:
    """Tests for SceneTiming model."""
    
    def test_valid_timing(self):
        """Should create valid timing."""
        timing = SceneTiming(
            scene_id="scene_01",
            sequence=1,
            estimated_duration=5.0,
            start=0.0,
            end=5.0,
        )
        assert timing.actual_duration == 5.0
    
    def test_actual_duration_calculation(self):
        """Should calculate actual duration from start/end."""
        timing = SceneTiming(
            scene_id="scene_01",
            sequence=1,
            estimated_duration=5.0,
            start=2.5,
            end=7.5,
        )
        assert timing.actual_duration == 5.0


class TestNarrationSegment:
    """Tests for NarrationSegment model."""
    
    def test_valid_segment(self):
        """Should create valid segment."""
        segment = NarrationSegment(
            scene_id="scene_01",
            sequence=1,
            text="This is the narration.",
            estimated_duration=5.0,
        )
        assert segment.scene_id == "scene_01"
        assert segment.text == "This is the narration."


class TestAssembledNarration:
    """Tests for AssembledNarration model."""
    
    def test_from_segments(self):
        """Should assemble narration from segments."""
        segments = [
            NarrationSegment(scene_id="scene_01", sequence=1, text="First.", estimated_duration=3.0),
            NarrationSegment(scene_id="scene_02", sequence=2, text="Second.", estimated_duration=3.0),
        ]
        
        narration = AssembledNarration.from_segments(
            project_id="test",
            segments=segments,
        )
        
        assert narration.scene_count == 2
        assert "First." in narration.full_text
        assert "Second." in narration.full_text
        assert narration.total_estimated_duration == 6.0


# ===== Timing Tests =====

class TestTiming:
    """Tests for timing calculations."""
    
    def test_calculate_scene_timings(self):
        """Should calculate cumulative timings."""
        segments = [
            NarrationSegment(scene_id="scene_01", sequence=1, text="A", estimated_duration=5.0),
            NarrationSegment(scene_id="scene_02", sequence=2, text="B", estimated_duration=5.0),
            NarrationSegment(scene_id="scene_03", sequence=3, text="C", estimated_duration=5.0),
        ]
        
        timings = calculate_scene_timings(segments)
        
        assert len(timings) == 3
        assert timings[0].start == 0.0
        assert timings[0].end == 5.0
        assert timings[1].start == 5.0
        assert timings[1].end == 10.0
        assert timings[2].start == 10.0
        assert timings[2].end == 15.0
    
    def test_calculate_timings_with_actual_duration(self):
        """Should scale timings to actual duration."""
        segments = [
            NarrationSegment(scene_id="scene_01", sequence=1, text="A", estimated_duration=5.0),
            NarrationSegment(scene_id="scene_02", sequence=2, text="B", estimated_duration=5.0),
        ]
        
        # Actual duration is 20s instead of estimated 10s
        timings = calculate_scene_timings(segments, voiceover_duration=20.0)
        
        assert timings[0].end == 10.0  # Scaled from 5 to 10
        assert timings[1].end == 20.0  # Scaled from 10 to 20
    
    def test_empty_segments_raises(self):
        """Should raise on empty segments."""
        with pytest.raises(VoiceTimingError):
            calculate_scene_timings([])
    
    def test_check_duration_mismatch_no_warning(self):
        """Should not warn when within threshold."""
        has_warning, diff, msg = check_duration_mismatch(
            actual_duration=30.0,
            estimated_duration=31.0,
            threshold=2.0,
        )
        assert has_warning is False
        assert msg is None
    
    def test_check_duration_mismatch_warning(self):
        """Should warn when outside threshold."""
        has_warning, diff, msg = check_duration_mismatch(
            actual_duration=35.0,
            estimated_duration=30.0,
            threshold=2.0,
        )
        assert has_warning is True
        assert "longer" in msg
    
    def test_get_scene_at_time(self):
        """Should find scene at specific time."""
        timings = [
            SceneTiming(scene_id="scene_01", sequence=1, estimated_duration=5, start=0, end=5),
            SceneTiming(scene_id="scene_02", sequence=2, estimated_duration=5, start=5, end=10),
        ]
        
        scene = get_scene_at_time(timings, 3.0)
        assert scene.scene_id == "scene_01"
        
        scene = get_scene_at_time(timings, 7.0)
        assert scene.scene_id == "scene_02"


# ===== Narration Tests =====

class TestNarrationExtraction:
    """Tests for narration extraction."""
    
    def get_test_plan(self) -> ProductionPlan:
        """Create a test production plan."""
        return ProductionPlan(
            project_id="test_001",
            title="Test Video",
            hook="Opening hook",
            script="Full script",
            estimated_duration=15,
            scenes=[
                Scene(id="scene_01", sequence=1, narration="First scene.", visual_prompt="V1", estimated_duration=5),
                Scene(id="scene_02", sequence=2, narration="Second scene.", visual_prompt="V2", estimated_duration=5),
                Scene(id="scene_03", sequence=3, narration="Third scene.", visual_prompt="V3", estimated_duration=5),
            ],
        )
    
    def test_extract_segments(self):
        """Should extract segments in order."""
        plan = self.get_test_plan()
        segments = extract_narration_segments(plan)
        
        assert len(segments) == 3
        assert segments[0].scene_id == "scene_01"
        assert segments[1].scene_id == "scene_02"
        assert segments[2].scene_id == "scene_03"
    
    def test_assemble_narration(self):
        """Should assemble full narration."""
        plan = self.get_test_plan()
        narration = assemble_narration(plan)
        
        assert narration.scene_count == 3
        assert "First scene." in narration.full_text
        assert "Second scene." in narration.full_text
        assert "Third scene." in narration.full_text
    
    def test_scene_order_preserved(self):
        """Should preserve scene order."""
        plan = self.get_test_plan()
        narration = assemble_narration(plan)
        
        # Check order in full text
        first_pos = narration.full_text.find("First")
        second_pos = narration.full_text.find("Second")
        third_pos = narration.full_text.find("Third")
        
        assert first_pos < second_pos < third_pos


class TestTextCleaning:
    """Tests for narration text cleaning."""
    
    def test_normalize_whitespace(self):
        """Should normalize excessive whitespace."""
        text = "Hello    world"
        cleaned = clean_narration_text(text)
        assert cleaned == "Hello world"
    
    def test_normalize_line_endings(self):
        """Should normalize line endings."""
        text = "Hello\r\nworld\rtest"
        cleaned = clean_narration_text(text)
        assert "\r" not in cleaned
    
    def test_preserve_content(self):
        """Should preserve narration content."""
        text = "The ocean is deep. Very deep!"
        cleaned = clean_narration_text(text)
        assert cleaned == text
    
    def test_strip_whitespace(self):
        """Should strip leading/trailing whitespace."""
        text = "  Hello world  "
        cleaned = clean_narration_text(text)
        assert cleaned == "Hello world"


# ===== Provider Tests =====

class TestMockProvider:
    """Tests for mock voice provider."""
    
    def test_mock_is_available(self):
        """Mock provider should always be available."""
        provider = MockVoiceProvider()
        assert provider.is_available() is True
    
    def test_mock_name(self):
        """Mock provider should have correct name."""
        provider = MockVoiceProvider()
        assert provider.name == "mock"
    
    def test_mock_list_voices(self):
        """Mock provider should list voices."""
        provider = MockVoiceProvider()
        voices = asyncio.run(provider.list_voices())
        
        assert len(voices) > 0
        assert all(isinstance(v, VoiceInfo) for v in voices)
    
    def test_mock_list_voices_with_locale(self):
        """Mock provider should filter by locale."""
        provider = MockVoiceProvider()
        voices = asyncio.run(provider.list_voices(locale="en-US"))
        
        assert all("en-US" in v.locale for v in voices)
    
    def test_mock_call_count(self):
        """Mock provider should track call count."""
        provider = MockVoiceProvider()
        assert provider.call_count == 0
        
        # Note: We don't actually call generate here to avoid file creation
        provider._call_count = 1
        assert provider.call_count == 1
        
        provider.reset_call_count()
        assert provider.call_count == 0


class TestGetProvider:
    """Tests for provider factory."""
    
    def test_get_mock_provider(self):
        """Should return mock provider."""
        provider = get_provider("mock")
        assert isinstance(provider, MockVoiceProvider)
    
    def test_get_provider_mock_mode(self):
        """Should return mock in mock mode."""
        provider = get_provider("edge_tts", mock_mode=True)
        assert isinstance(provider, MockVoiceProvider)
    
    def test_unknown_provider_raises(self):
        """Should raise on unknown provider."""
        with pytest.raises(VoiceGenerationError):
            get_provider("unknown_provider")


# ===== Exception Tests =====

class TestExceptions:
    """Tests for voice exceptions."""
    
    def test_voice_provider_error(self):
        """VoiceProviderError should include provider."""
        error = VoiceProviderError("mock", "Test error")
        assert "mock" in str(error)
        assert "Test error" in str(error)
    
    def test_voice_generation_error(self):
        """VoiceGenerationError should include reason."""
        error = VoiceGenerationError("Test reason", provider="mock")
        assert "Test reason" in str(error)
    
    def test_voice_validation_error(self):
        """VoiceValidationError should include errors."""
        error = VoiceValidationError(["Error 1", "Error 2"])
        assert "Error 1" in str(error)
        assert "Error 2" in str(error)
    
    def test_voice_timing_error(self):
        """VoiceTimingError should include reason."""
        error = VoiceTimingError("Test reason")
        assert "Test reason" in str(error)
    
    def test_voice_not_found_error(self):
        """VoiceNotFoundError should include voice name."""
        error = VoiceNotFoundError("test-voice", "mock")
        assert "test-voice" in str(error)
    
    def test_narration_extraction_error(self):
        """NarrationExtractionError should include reason."""
        error = NarrationExtractionError("Test reason")
        assert "Test reason" in str(error)


# ===== Generator Tests =====

class TestVoiceGenerator:
    """Tests for voice generator."""
    
    def test_generator_mock_mode(self):
        """Generator should work in mock mode."""
        generator = VoiceGenerator(mock_mode=True)
        assert isinstance(generator.provider, MockVoiceProvider)
    
    def test_generator_with_provider(self):
        """Generator should accept provider."""
        provider = MockVoiceProvider()
        generator = VoiceGenerator(provider=provider)
        assert generator.provider is provider


# ===== VoiceTiming Tests =====

class TestVoiceTiming:
    """Tests for VoiceTiming model."""
    
    def test_from_scenes(self):
        """Should create timing from scenes."""
        scene_timings = [
            SceneTiming(scene_id="scene_01", sequence=1, estimated_duration=5, start=0, end=5),
            SceneTiming(scene_id="scene_02", sequence=2, estimated_duration=5, start=5, end=10),
        ]
        
        timing = VoiceTiming.from_scenes(
            project_id="test",
            voiceover_duration=12.0,
            scene_timings=scene_timings,
        )
        
        assert timing.voiceover_duration == 12.0
        assert timing.estimated_total_duration == 10.0
        assert timing.duration_difference == 2.0
    
    def test_to_dict(self):
        """Should convert to dictionary."""
        scene_timings = [
            SceneTiming(scene_id="scene_01", sequence=1, estimated_duration=5, start=0, end=5),
        ]
        
        timing = VoiceTiming.from_scenes(
            project_id="test",
            voiceover_duration=5.0,
            scene_timings=scene_timings,
        )
        
        data = timing.to_dict()
        assert "project_id" in data
        assert "voiceover_duration" in data
        assert "scenes" in data
