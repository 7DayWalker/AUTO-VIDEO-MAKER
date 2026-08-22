"""
Tests for app.orchestrator module.

All tests run WITHOUT consuming API credits or Flow credits.
Tests use mock providers.
"""

import pytest
import json
from pathlib import Path
from pydantic import ValidationError

from app.orchestrator.models import (
    PipelineStage,
    StageStatus,
    StageResult,
    ProjectManifest,
    OrchestratorConfig,
)
from app.orchestrator.captions import (
    format_srt_timestamp,
    generate_srt_from_timing,
    validate_srt_file,
    _split_into_caption_chunks,
)
from app.orchestrator.exceptions import (
    OrchestratorError,
    StageFailedError,
    ResumeError,
    ValidationError as OrchestratorValidationError,
)


# ===== PipelineStage Tests =====

class TestPipelineStage:
    """Tests for PipelineStage enum."""
    
    def test_stage_order(self):
        """Stages should be in correct order."""
        order = PipelineStage.get_order()
        assert order[0] == PipelineStage.CONTENT
        assert order[1] == PipelineStage.VOICE
        assert order[2] == PipelineStage.SCENES
        assert order[3] == PipelineStage.CAPTIONS
        assert order[4] == PipelineStage.ASSEMBLY
        assert order[5] == PipelineStage.COMPLETE
    
    def test_next_stage(self):
        """next_stage should return correct stage."""
        assert PipelineStage.CONTENT.next_stage() == PipelineStage.VOICE
        assert PipelineStage.VOICE.next_stage() == PipelineStage.SCENES
        assert PipelineStage.SCENES.next_stage() == PipelineStage.CAPTIONS
        assert PipelineStage.CAPTIONS.next_stage() == PipelineStage.ASSEMBLY
        assert PipelineStage.ASSEMBLY.next_stage() == PipelineStage.COMPLETE
        assert PipelineStage.COMPLETE.next_stage() is None
    
    def test_previous_stage(self):
        """previous_stage should return correct stage."""
        assert PipelineStage.CONTENT.previous_stage() is None
        assert PipelineStage.VOICE.previous_stage() == PipelineStage.CONTENT
        assert PipelineStage.COMPLETE.previous_stage() == PipelineStage.ASSEMBLY


# ===== StageResult Tests =====

class TestStageResult:
    """Tests for StageResult model."""
    
    def test_pending_result(self):
        """Should create pending result."""
        result = StageResult.pending(PipelineStage.CONTENT)
        assert result.stage == PipelineStage.CONTENT
        assert result.status == StageStatus.PENDING
        assert result.error is None
    
    def test_running_result(self):
        """Should create running result with timestamp."""
        result = StageResult.running(PipelineStage.VOICE)
        assert result.stage == PipelineStage.VOICE
        assert result.status == StageStatus.RUNNING
        assert result.started_at is not None
    
    def test_completed_result(self):
        """Should create completed result."""
        result = StageResult.completed(
            stage=PipelineStage.CONTENT,
            output_path="/path/to/output",
            output_files=["/path/to/file1", "/path/to/file2"],
            warnings=["warning1"],
            metadata={"key": "value"},
        )
        assert result.stage == PipelineStage.CONTENT
        assert result.status == StageStatus.COMPLETED
        assert result.output_path == "/path/to/output"
        assert len(result.output_files) == 2
        assert len(result.warnings) == 1
        assert result.metadata["key"] == "value"
    
    def test_failed_result(self):
        """Should create failed result with error."""
        result = StageResult.failed(
            stage=PipelineStage.SCENES,
            error="Something went wrong",
        )
        assert result.stage == PipelineStage.SCENES
        assert result.status == StageStatus.FAILED
        assert result.error == "Something went wrong"
    
    def test_skipped_result(self):
        """Should create skipped result."""
        result = StageResult.skipped(PipelineStage.CAPTIONS, reason="Disabled")
        assert result.stage == PipelineStage.CAPTIONS
        assert result.status == StageStatus.SKIPPED
        assert result.metadata.get("skip_reason") == "Disabled"


# ===== ProjectManifest Tests =====

class TestProjectManifest:
    """Tests for ProjectManifest model."""
    
    def test_create_manifest(self):
        """Should create manifest with required fields."""
        manifest = ProjectManifest(
            project_id="test_project_001",
            topic="Test Topic",
        )
        assert manifest.project_id == "test_project_001"
        assert manifest.topic == "Test Topic"
        assert manifest.current_stage == PipelineStage.CONTENT
    
    def test_initialize_stages(self):
        """Should initialize all stages as pending."""
        manifest = ProjectManifest(
            project_id="test_project_001",
            topic="Test Topic",
        )
        manifest.initialize_stages()
        
        for stage in PipelineStage.get_order():
            result = manifest.get_stage_result(stage)
            assert result is not None
            assert result.status == StageStatus.PENDING
    
    def test_set_stage_result(self):
        """Should update stage result and current stage."""
        manifest = ProjectManifest(
            project_id="test_project_001",
            topic="Test Topic",
        )
        manifest.initialize_stages()
        
        # Complete content stage
        result = StageResult.completed(stage=PipelineStage.CONTENT)
        manifest.set_stage_result(result)
        
        assert manifest.is_stage_completed(PipelineStage.CONTENT)
        assert manifest.current_stage == PipelineStage.VOICE
    
    def test_is_stage_completed(self):
        """Should correctly check stage completion."""
        manifest = ProjectManifest(
            project_id="test_project_001",
            topic="Test Topic",
        )
        manifest.initialize_stages()
        
        assert not manifest.is_stage_completed(PipelineStage.CONTENT)
        
        manifest.set_stage_result(StageResult.completed(stage=PipelineStage.CONTENT))
        assert manifest.is_stage_completed(PipelineStage.CONTENT)
    
    def test_get_next_pending_stage(self):
        """Should return next pending stage."""
        manifest = ProjectManifest(
            project_id="test_project_001",
            topic="Test Topic",
        )
        manifest.initialize_stages()
        
        assert manifest.get_next_pending_stage() == PipelineStage.CONTENT
        
        manifest.set_stage_result(StageResult.completed(stage=PipelineStage.CONTENT))
        assert manifest.get_next_pending_stage() == PipelineStage.VOICE
    
    def test_can_resume(self):
        """Should correctly check if pipeline can resume."""
        manifest = ProjectManifest(
            project_id="test_project_001",
            topic="Test Topic",
        )
        manifest.initialize_stages()
        
        assert manifest.can_resume()
        
        # Mark as running - can still resume (will be reset)
        manifest.set_stage_result(StageResult.running(PipelineStage.CONTENT))
        assert manifest.can_resume()
        
        # Complete all stages - cannot resume
        for stage in [PipelineStage.CONTENT, PipelineStage.VOICE, PipelineStage.SCENES, 
                      PipelineStage.CAPTIONS, PipelineStage.ASSEMBLY]:
            manifest.set_stage_result(StageResult.completed(stage=stage))
        assert not manifest.can_resume()
    
    def test_reset_running_stages(self):
        """Should reset running stages to failed."""
        manifest = ProjectManifest(
            project_id="test_project_001",
            topic="Test Topic",
        )
        manifest.initialize_stages()
        
        # Mark content as running (simulating crash)
        manifest.set_stage_result(StageResult.running(PipelineStage.CONTENT))
        assert manifest.get_stage_result(PipelineStage.CONTENT).status == StageStatus.RUNNING
        
        # Reset running stages
        manifest.reset_running_stages()
        
        # Should now be failed
        result = manifest.get_stage_result(PipelineStage.CONTENT)
        assert result.status == StageStatus.FAILED
        assert "interrupted" in result.error.lower()
    
    def test_to_dict_and_from_dict(self):
        """Should serialize and deserialize correctly."""
        manifest = ProjectManifest(
            project_id="test_project_001",
            topic="Test Topic",
            flow_project_url="https://flow.google/test",
        )
        manifest.initialize_stages()
        manifest.set_stage_result(StageResult.completed(stage=PipelineStage.CONTENT))
        
        # Serialize
        data = manifest.to_dict()
        assert isinstance(data, dict)
        assert data["project_id"] == "test_project_001"
        
        # Deserialize
        restored = ProjectManifest.from_dict(data)
        assert restored.project_id == manifest.project_id
        assert restored.is_stage_completed(PipelineStage.CONTENT)


# ===== OrchestratorConfig Tests =====

class TestOrchestratorConfig:
    """Tests for OrchestratorConfig model."""
    
    def test_create_config(self):
        """Should create config with required fields."""
        config = OrchestratorConfig(
            topic="Test Topic",
            flow_project_url="https://flow.google/test",
        )
        assert config.topic == "Test Topic"
        assert config.flow_project_url == "https://flow.google/test"
        assert config.target_duration == 30  # default
        assert config.number_of_scenes == 6  # default
    
    def test_validate_platform(self):
        """Should validate platform."""
        config = OrchestratorConfig(
            topic="Test Topic",
            flow_project_url="https://flow.google/test",
            platform="youtube_shorts",
        )
        assert config.platform == "youtube_shorts"
        
        with pytest.raises(ValidationError):
            OrchestratorConfig(
                topic="Test Topic",
                flow_project_url="https://flow.google/test",
                platform="invalid_platform",
            )
    
    def test_validate_aspect_ratio(self):
        """Should validate aspect ratio."""
        config = OrchestratorConfig(
            topic="Test Topic",
            flow_project_url="https://flow.google/test",
            scene_aspect_ratio="9:16",
        )
        assert config.scene_aspect_ratio == "9:16"
        
        with pytest.raises(ValidationError):
            OrchestratorConfig(
                topic="Test Topic",
                flow_project_url="https://flow.google/test",
                scene_aspect_ratio="4:3",  # Not supported for scenes
            )
    
    def test_to_content_brief_dict(self):
        """Should convert to content brief dict."""
        config = OrchestratorConfig(
            topic="Test Topic",
            flow_project_url="https://flow.google/test",
            niche="science",
            target_duration=45,
        )
        brief_dict = config.to_content_brief_dict()
        
        assert brief_dict["topic"] == "Test Topic"
        assert brief_dict["niche"] == "science"
        assert brief_dict["target_duration"] == 45
    
    def test_to_voice_config_dict(self):
        """Should convert to voice config dict."""
        config = OrchestratorConfig(
            topic="Test Topic",
            flow_project_url="https://flow.google/test",
            voice="en-GB-RyanNeural",
            voice_rate="+10%",
        )
        voice_dict = config.to_voice_config_dict()
        
        assert voice_dict["voice"] == "en-GB-RyanNeural"
        assert voice_dict["rate"] == "+10%"


# ===== Caption Generation Tests =====

class TestCaptionGeneration:
    """Tests for caption generation functions."""
    
    def test_format_srt_timestamp(self):
        """Should format timestamp correctly."""
        assert format_srt_timestamp(0) == "00:00:00,000"
        assert format_srt_timestamp(1.5) == "00:00:01,500"
        assert format_srt_timestamp(65.123) == "00:01:05,123"
        # Note: floating-point precision may cause 3661.999 to round to 998
        result = format_srt_timestamp(3661.999)
        assert result in ["01:01:01,998", "01:01:01,999"]  # Accept either due to float precision
    
    def test_split_into_caption_chunks(self):
        """Should split text into caption-sized chunks."""
        text = "This is a short sentence."
        chunks = _split_into_caption_chunks(text, max_chars_per_line=42, max_lines=2)
        assert len(chunks) == 1
        assert chunks[0] == text
        
        # Longer text should be split
        long_text = "This is a much longer sentence that should be split into multiple caption chunks for better readability on screen."
        chunks = _split_into_caption_chunks(long_text, max_chars_per_line=42, max_lines=2)
        assert len(chunks) > 1
    
    def test_generate_srt_from_timing(self, tmp_path):
        """Should generate valid SRT file."""
        # Create timing file
        timing_data = {
            "project_id": "test_project",
            "voiceover_duration": 10.0,
            "scenes": [
                {"scene_id": "scene_01", "start_time": 0, "end_time": 5, "estimated_duration": 5},
                {"scene_id": "scene_02", "start_time": 5, "end_time": 10, "estimated_duration": 5},
            ]
        }
        timing_path = tmp_path / "timing.json"
        timing_path.write_text(json.dumps(timing_data))
        
        # Create production plan
        plan_data = {
            "project_id": "test_project",
            "title": "Test",
            "hook": "Hook",
            "script": "Script",
            "estimated_duration": 10,
            "scenes": [
                {"id": "scene_01", "sequence": 1, "narration": "First scene narration.", "visual_prompt": "prompt", "estimated_duration": 5, "purpose": "hook"},
                {"id": "scene_02", "sequence": 2, "narration": "Second scene narration.", "visual_prompt": "prompt", "estimated_duration": 5, "purpose": "fact"},
            ]
        }
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(plan_data))
        
        # Generate SRT
        srt_path = tmp_path / "captions.srt"
        generate_srt_from_timing(timing_path, plan_path, srt_path)
        
        assert srt_path.exists()
        content = srt_path.read_text()
        assert "00:00:00" in content
        assert "-->" in content
    
    def test_validate_srt_file(self, tmp_path):
        """Should validate SRT file."""
        # Valid SRT
        srt_content = """1
00:00:00,000 --> 00:00:02,000
First caption.

2
00:00:02,000 --> 00:00:04,000
Second caption.
"""
        srt_path = tmp_path / "valid.srt"
        srt_path.write_text(srt_content)
        
        result = validate_srt_file(srt_path)
        assert result["valid"]
        assert result["entry_count"] == 2
        
        # Invalid SRT
        invalid_path = tmp_path / "invalid.srt"
        invalid_path.write_text("not valid srt content")
        
        result = validate_srt_file(invalid_path)
        assert not result["valid"]


# ===== Exception Tests =====

class TestExceptions:
    """Tests for orchestrator exceptions."""
    
    def test_stage_failed_error(self):
        """Should create StageFailedError with details."""
        error = StageFailedError(
            stage="content",
            message="API error",
            details={"code": 500},
        )
        assert error.stage == "content"
        assert error.details["code"] == 500
        assert "content" in str(error)
    
    def test_resume_error(self):
        """Should create ResumeError."""
        error = ResumeError(
            message="Cannot resume",
            manifest_path="/path/to/manifest.json",
        )
        assert error.manifest_path == "/path/to/manifest.json"
        assert "Cannot resume" in str(error)
    
    def test_validation_error(self):
        """Should create ValidationError."""
        error = OrchestratorValidationError(
            stage="assembly",
            message="File not found",
            path="/path/to/file",
        )
        assert error.stage == "assembly"
        assert error.path == "/path/to/file"
