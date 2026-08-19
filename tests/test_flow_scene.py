"""
Tests for app.flow scene generation module.

All tests run WITHOUT consuming Google Flow credits.
Tests mock Flow interactions where necessary.
"""

import pytest
from pathlib import Path
from pydantic import ValidationError

from app.flow.scene_config import (
    FLOW_SCENE_MODEL,
    FLOW_SCENE_ASPECT_RATIO,
    FLOW_SCENE_DURATION,
    FLOW_SCENE_COUNT,
    FLOW_SCENE_MAX_PER_RUN,
    FLOW_SCENE_CREDIT_LIMIT,
    FLOW_SCENE_TIMEOUT,
    get_scene_config,
)
from app.flow.scene_models import (
    FlowGenerationConfig,
    SceneGenerationRequest,
    SceneGenerationResult,
    SceneMetadata,
    SceneManifest,
    SceneManifestEntry,
    FlowUIState,
)
from app.flow.scene_generator import (
    FlowSceneError,
    FlowConnectionError,
    FlowAuthenticationError,
    FlowGenerationError,
    FlowCreditError,
    validate_video_file,
)


# ===== Config Tests =====

class TestSceneConfig:
    """Tests for scene generation configuration."""
    
    def test_default_model(self):
        """Default model should be Veo 3.1 - Lite."""
        assert FLOW_SCENE_MODEL == "Veo 3.1 - Lite"
    
    def test_default_aspect_ratio(self):
        """Default aspect ratio should be 9:16."""
        assert FLOW_SCENE_ASPECT_RATIO == "9:16"
    
    def test_default_duration(self):
        """Default duration should be 4 seconds."""
        assert FLOW_SCENE_DURATION == 4
    
    def test_default_count(self):
        """Default count should be 1."""
        assert FLOW_SCENE_COUNT == 1
    
    def test_default_max_per_run(self):
        """Default max scenes per run should be 1."""
        assert FLOW_SCENE_MAX_PER_RUN == 1
    
    def test_default_credit_limit(self):
        """Default credit limit should be 10."""
        assert FLOW_SCENE_CREDIT_LIMIT == 10
    
    def test_default_timeout(self):
        """Default timeout should be 600 seconds."""
        assert FLOW_SCENE_TIMEOUT == 600
    
    def test_get_scene_config(self):
        """get_scene_config should return all settings."""
        config = get_scene_config()
        assert "model" in config
        assert "aspect_ratio" in config
        assert "duration" in config
        assert "count" in config
        assert "credit_limit" in config


# ===== FlowGenerationConfig Tests =====

class TestFlowGenerationConfig:
    """Tests for FlowGenerationConfig model."""
    
    def test_default_config(self):
        """Should create config with defaults."""
        config = FlowGenerationConfig()
        assert config.model == FLOW_SCENE_MODEL
        assert config.aspect_ratio == FLOW_SCENE_ASPECT_RATIO
        assert config.duration == FLOW_SCENE_DURATION
        assert config.count == FLOW_SCENE_COUNT
        assert config.agent_enabled is False
    
    def test_custom_config(self):
        """Should accept custom values."""
        config = FlowGenerationConfig(
            model="Custom Model",
            aspect_ratio="16:9",
            duration=8,
            count=2,
        )
        assert config.model == "Custom Model"
        assert config.aspect_ratio == "16:9"
        assert config.duration == 8
        assert config.count == 2
    
    def test_invalid_aspect_ratio(self):
        """Should reject invalid aspect ratio."""
        with pytest.raises(ValidationError):
            FlowGenerationConfig(aspect_ratio="4:3")
    
    def test_valid_aspect_ratios(self):
        """Should accept valid aspect ratios."""
        for ratio in ["9:16", "16:9", "1:1"]:
            config = FlowGenerationConfig(aspect_ratio=ratio)
            assert config.aspect_ratio == ratio
    
    def test_duration_bounds(self):
        """Should enforce duration bounds."""
        with pytest.raises(ValidationError):
            FlowGenerationConfig(duration=0)
        
        with pytest.raises(ValidationError):
            FlowGenerationConfig(duration=100)
    
    def test_count_bounds(self):
        """Should enforce count bounds."""
        with pytest.raises(ValidationError):
            FlowGenerationConfig(count=0)
        
        with pytest.raises(ValidationError):
            FlowGenerationConfig(count=10)


# ===== SceneGenerationRequest Tests =====

class TestSceneGenerationRequest:
    """Tests for SceneGenerationRequest model."""
    
    def test_valid_request(self):
        """Should create valid request."""
        request = SceneGenerationRequest(
            project_id="test_project",
            scene_id="scene_01",
            sequence=1,
            visual_prompt="A beautiful sunset over the ocean",
        )
        assert request.project_id == "test_project"
        assert request.scene_id == "scene_01"
        assert request.sequence == 1
    
    def test_empty_project_id_rejected(self):
        """Should reject empty project ID."""
        with pytest.raises(ValidationError):
            SceneGenerationRequest(
                project_id="",
                scene_id="scene_01",
                sequence=1,
                visual_prompt="Test prompt",
            )
    
    def test_empty_scene_id_rejected(self):
        """Should reject empty scene ID."""
        with pytest.raises(ValidationError):
            SceneGenerationRequest(
                project_id="test",
                scene_id="",
                sequence=1,
                visual_prompt="Test prompt",
            )
    
    def test_empty_prompt_rejected(self):
        """Should reject empty visual prompt."""
        with pytest.raises(ValidationError):
            SceneGenerationRequest(
                project_id="test",
                scene_id="scene_01",
                sequence=1,
                visual_prompt="",
            )
    
    def test_whitespace_prompt_rejected(self):
        """Should reject whitespace-only prompt."""
        with pytest.raises(ValidationError):
            SceneGenerationRequest(
                project_id="test",
                scene_id="scene_01",
                sequence=1,
                visual_prompt="   ",
            )
    
    def test_sequence_must_be_positive(self):
        """Should reject non-positive sequence."""
        with pytest.raises(ValidationError):
            SceneGenerationRequest(
                project_id="test",
                scene_id="scene_01",
                sequence=0,
                visual_prompt="Test prompt",
            )


# ===== SceneGenerationResult Tests =====

class TestSceneGenerationResult:
    """Tests for SceneGenerationResult model."""
    
    def test_success_result(self):
        """Should create success result."""
        result = SceneGenerationResult.success_result(
            scene_id="scene_01",
            sequence=1,
            file_path=Path("/test/scene_01.mp4"),
            file_size=1000000,
            actual_duration=4.1,
            width=1080,
            height=1920,
            codec="h264",
        )
        assert result.success is True
        assert result.status == "completed"
        assert result.scene_id == "scene_01"
    
    def test_failure_result(self):
        """Should create failure result."""
        result = SceneGenerationResult.failure(
            scene_id="scene_01",
            sequence=1,
            error="Test error",
        )
        assert result.success is False
        assert result.status == "failed"
        assert result.error == "Test error"
    
    def test_dry_run_result(self):
        """Should create dry-run result."""
        result = SceneGenerationResult.dry_run(
            scene_id="scene_01",
            sequence=1,
            generation_cost=10,
        )
        assert result.success is True
        assert result.status == "dry_run"
        assert result.generation_cost == 10
    
    def test_skipped_result(self):
        """Should create skipped result."""
        result = SceneGenerationResult.skipped(
            scene_id="scene_01",
            sequence=1,
            reason="Already exists",
        )
        assert result.success is True
        assert result.status == "skipped"


# ===== SceneMetadata Tests =====

class TestSceneMetadata:
    """Tests for SceneMetadata model."""
    
    def test_valid_metadata(self):
        """Should create valid metadata."""
        metadata = SceneMetadata(
            project_id="test_project",
            scene_id="scene_01",
            sequence=1,
            source_prompt="Test prompt",
            model="Veo 3.1 - Lite",
            aspect_ratio="9:16",
            duration_requested=4,
            output_count=1,
            file_path="/test/scene_01.mp4",
            file_size=1000000,
            actual_duration=4.1,
            width=1080,
            height=1920,
            codec="h264",
        )
        assert metadata.project_id == "test_project"
        assert metadata.scene_id == "scene_01"
    
    def test_to_dict(self):
        """Should convert to dictionary."""
        metadata = SceneMetadata(
            project_id="test",
            scene_id="scene_01",
            sequence=1,
            source_prompt="Test",
            model="Veo",
            aspect_ratio="9:16",
            duration_requested=4,
            output_count=1,
            file_path="/test.mp4",
            file_size=1000,
            actual_duration=4.0,
            width=1080,
            height=1920,
            codec="h264",
        )
        data = metadata.to_dict()
        assert "project_id" in data
        assert "scene_id" in data
        assert "source_prompt" in data


# ===== SceneManifest Tests =====

class TestSceneManifest:
    """Tests for SceneManifest model."""
    
    def test_create_manifest(self):
        """Should create empty manifest."""
        manifest = SceneManifest(project_id="test_project")
        assert manifest.project_id == "test_project"
        assert len(manifest.scenes) == 0
    
    def test_add_scene(self):
        """Should add scene to manifest."""
        manifest = SceneManifest(project_id="test")
        manifest.add_scene(
            scene_id="scene_01",
            sequence=1,
            file="scene_01.mp4",
            status="completed",
        )
        assert len(manifest.scenes) == 1
        assert manifest.scenes[0].scene_id == "scene_01"
    
    def test_add_scene_updates_existing(self):
        """Should update existing scene."""
        manifest = SceneManifest(project_id="test")
        manifest.add_scene("scene_01", 1, "scene_01.mp4", "pending")
        manifest.add_scene("scene_01", 1, "scene_01.mp4", "completed")
        
        assert len(manifest.scenes) == 1
        assert manifest.scenes[0].status == "completed"
    
    def test_scenes_sorted_by_sequence(self):
        """Should sort scenes by sequence."""
        manifest = SceneManifest(project_id="test")
        manifest.add_scene("scene_03", 3, "scene_03.mp4", "completed")
        manifest.add_scene("scene_01", 1, "scene_01.mp4", "completed")
        manifest.add_scene("scene_02", 2, "scene_02.mp4", "completed")
        
        assert manifest.scenes[0].sequence == 1
        assert manifest.scenes[1].sequence == 2
        assert manifest.scenes[2].sequence == 3
    
    def test_get_scene(self):
        """Should get scene by ID."""
        manifest = SceneManifest(project_id="test")
        manifest.add_scene("scene_01", 1, "scene_01.mp4", "completed")
        
        scene = manifest.get_scene("scene_01")
        assert scene is not None
        assert scene.scene_id == "scene_01"
        
        missing = manifest.get_scene("scene_99")
        assert missing is None
    
    def test_to_dict(self):
        """Should convert to dictionary."""
        manifest = SceneManifest(project_id="test")
        manifest.add_scene("scene_01", 1, "scene_01.mp4", "completed")
        
        data = manifest.to_dict()
        assert "project_id" in data
        assert "scenes" in data
        assert len(data["scenes"]) == 1


# ===== FlowUIState Tests =====

class TestFlowUIState:
    """Tests for FlowUIState model."""
    
    def test_default_state(self):
        """Should create default state."""
        state = FlowUIState()
        assert state.connected is False
        assert state.authenticated is False
        assert state.ready_for_generation is False
    
    def test_ready_for_generation(self):
        """Should be ready when all prerequisites met."""
        state = FlowUIState(
            connected=True,
            authenticated=True,
            on_project_page=True,
            agent_off=True,
            video_mode=True,
            model_verified=True,
            aspect_ratio_verified=True,
            duration_verified=True,
            count_verified=True,
            prompt_ready=True,
        )
        assert state.ready_for_generation is True
    
    def test_not_ready_if_missing_prerequisite(self):
        """Should not be ready if any prerequisite missing."""
        state = FlowUIState(
            connected=True,
            authenticated=True,
            on_project_page=True,
            agent_off=True,
            video_mode=True,
            model_verified=True,
            aspect_ratio_verified=True,
            duration_verified=True,
            count_verified=True,
            prompt_ready=False,  # Missing
        )
        assert state.ready_for_generation is False
    
    def test_get_missing_prerequisites(self):
        """Should list missing prerequisites."""
        state = FlowUIState(
            connected=True,
            authenticated=False,
            on_project_page=True,
        )
        missing = state.get_missing_prerequisites()
        assert "Google authentication" in missing
        assert "Chrome/CDP connection" not in missing


# ===== Exception Tests =====

class TestExceptions:
    """Tests for scene generation exceptions."""
    
    def test_flow_scene_error(self):
        """FlowSceneError should be raisable."""
        with pytest.raises(FlowSceneError):
            raise FlowSceneError("Test error")
    
    def test_flow_connection_error(self):
        """FlowConnectionError should be FlowSceneError."""
        error = FlowConnectionError("Connection failed")
        assert isinstance(error, FlowSceneError)
    
    def test_flow_authentication_error(self):
        """FlowAuthenticationError should be FlowSceneError."""
        error = FlowAuthenticationError("Auth required")
        assert isinstance(error, FlowSceneError)
    
    def test_flow_generation_error(self):
        """FlowGenerationError should be FlowSceneError."""
        error = FlowGenerationError("Generation failed")
        assert isinstance(error, FlowSceneError)
    
    def test_flow_credit_error(self):
        """FlowCreditError should be FlowSceneError."""
        error = FlowCreditError("Credit limit exceeded")
        assert isinstance(error, FlowSceneError)


# ===== Credit Safety Tests =====

class TestCreditSafety:
    """Tests for credit safety mechanisms."""
    
    def test_credit_limit_default(self):
        """Default credit limit should be reasonable."""
        assert FLOW_SCENE_CREDIT_LIMIT > 0
        assert FLOW_SCENE_CREDIT_LIMIT <= 50
    
    def test_max_scenes_default(self):
        """Default max scenes should be 1 for safety."""
        assert FLOW_SCENE_MAX_PER_RUN == 1
    
    def test_generation_cost_in_result(self):
        """Result should include generation cost."""
        result = SceneGenerationResult.dry_run(
            scene_id="scene_01",
            sequence=1,
            generation_cost=10,
        )
        assert result.generation_cost == 10


# ===== Dry Run Tests =====

class TestDryRun:
    """Tests for dry-run functionality."""
    
    def test_dry_run_result_status(self):
        """Dry run should have correct status."""
        result = SceneGenerationResult.dry_run(
            scene_id="scene_01",
            sequence=1,
        )
        assert result.status == "dry_run"
        assert result.success is True
    
    def test_dry_run_no_file_path(self):
        """Dry run should not have file path."""
        result = SceneGenerationResult.dry_run(
            scene_id="scene_01",
            sequence=1,
        )
        assert result.file_path is None


# ===== Scene Selection Tests =====

class TestSceneSelection:
    """Tests for scene selection logic."""
    
    def test_valid_scene_id(self):
        """Should accept valid scene ID format."""
        request = SceneGenerationRequest(
            project_id="test",
            scene_id="scene_01",
            sequence=1,
            visual_prompt="Test",
        )
        assert request.scene_id == "scene_01"
    
    def test_sequence_ordering(self):
        """Manifest should maintain sequence order."""
        manifest = SceneManifest(project_id="test")
        manifest.add_scene("scene_02", 2, "scene_02.mp4", "completed")
        manifest.add_scene("scene_01", 1, "scene_01.mp4", "completed")
        
        assert manifest.scenes[0].sequence < manifest.scenes[1].sequence


# ===== Video Validation Tests =====

class TestVideoValidation:
    """Tests for video validation."""
    
    def test_nonexistent_file(self):
        """Should report nonexistent file."""
        result = validate_video_file(Path("/nonexistent/file.mp4"))
        assert result["valid"] is False
        assert "not exist" in result["error"].lower()
    
    def test_validation_returns_dict(self):
        """Validation should return dictionary."""
        result = validate_video_file(Path("/test.mp4"))
        assert isinstance(result, dict)
        assert "valid" in result


# ===== Manifest Persistence Tests =====

class TestManifestPersistence:
    """Tests for manifest save/load."""
    
    def test_manifest_to_dict_structure(self):
        """Manifest dict should have correct structure."""
        manifest = SceneManifest(project_id="test")
        manifest.add_scene("scene_01", 1, "scene_01.mp4", "completed")
        
        data = manifest.to_dict()
        
        assert "project_id" in data
        assert "scenes" in data
        assert "created_at" in data
        assert "updated_at" in data
    
    def test_manifest_entry_structure(self):
        """Manifest entry should have correct fields."""
        entry = SceneManifestEntry(
            scene_id="scene_01",
            sequence=1,
            file="scene_01.mp4",
            status="completed",
        )
        assert entry.scene_id == "scene_01"
        assert entry.sequence == 1
        assert entry.file == "scene_01.mp4"
        assert entry.status == "completed"
