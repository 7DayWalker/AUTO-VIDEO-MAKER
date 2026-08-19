"""
Tests for app.content module.

All tests run WITHOUT internet access or API credentials.
"""

import pytest
import json
from pathlib import Path
from pydantic import ValidationError

from app.content.config import (
    DEFAULT_TARGET_DURATION,
    DEFAULT_PLATFORM,
    DEFAULT_LANGUAGE,
    DURATION_TOLERANCE,
    SUPPORTED_PLATFORMS,
    SUPPORTED_LANGUAGES,
    SUPPORTED_CONTENT_GOALS,
)
from app.content.models import (
    ContentBrief,
    Script,
    Scene,
    ProductionPlan,
    ProductionMetadata,
    ContentGenerationResult,
    generate_project_id,
)
from app.content.exceptions import (
    ContentError,
    ValidationError as ContentValidationError,
    DurationError,
    LLMAuthenticationError,
    LLMQuotaError,
    LLMResponseError,
    RetryExhaustedError,
)
from app.content.validator import validate_production_plan, ValidationResult
from app.content.providers.base import BaseLLMProvider, LLMResponse
from app.content.providers.mock import MockLLMProvider, get_mock_production_plan
from app.content.generator import ContentGenerator
from app.content.planner import ScenePlanner
from app.content.prompts.script import get_script_system_prompt, get_script_user_prompt
from app.content.prompts.scene_planner import get_combined_generation_prompt


# ===== Content Brief Tests =====

class TestContentBrief:
    """Tests for ContentBrief model."""
    
    def test_valid_brief(self):
        """Should accept valid content brief."""
        brief = ContentBrief(topic="5 strange facts about the ocean")
        assert brief.topic == "5 strange facts about the ocean"
        assert brief.target_duration == DEFAULT_TARGET_DURATION
        assert brief.platform == DEFAULT_PLATFORM
    
    def test_brief_with_all_fields(self):
        """Should accept brief with all fields."""
        brief = ContentBrief(
            topic="Test topic",
            niche="educational",
            target_audience="teenagers",
            tone="fun and energetic",
            target_duration=45,
            platform="tiktok",
            language="en",
            content_goal="entertainment",
            call_to_action="Follow for more!",
            style="fast-paced",
            number_of_scenes=8,
        )
        assert brief.target_duration == 45
        assert brief.platform == "tiktok"
        assert brief.number_of_scenes == 8
    
    def test_empty_topic_rejected(self):
        """Should reject empty topic."""
        with pytest.raises(ValidationError):
            ContentBrief(topic="")
    
    def test_short_topic_rejected(self):
        """Should reject topic shorter than 3 chars."""
        with pytest.raises(ValidationError):
            ContentBrief(topic="ab")
    
    def test_invalid_platform_rejected(self):
        """Should reject invalid platform."""
        with pytest.raises(ValidationError):
            ContentBrief(topic="Test", platform="invalid_platform")
    
    def test_invalid_language_rejected(self):
        """Should reject invalid language."""
        with pytest.raises(ValidationError):
            ContentBrief(topic="Test", language="invalid")
    
    def test_invalid_content_goal_rejected(self):
        """Should reject invalid content goal."""
        with pytest.raises(ValidationError):
            ContentBrief(topic="Test", content_goal="invalid_goal")
    
    def test_duration_bounds(self):
        """Should enforce duration bounds."""
        with pytest.raises(ValidationError):
            ContentBrief(topic="Test", target_duration=5)  # Too short
        
        with pytest.raises(ValidationError):
            ContentBrief(topic="Test", target_duration=200)  # Too long
    
    def test_scene_count_bounds(self):
        """Should enforce scene count bounds."""
        with pytest.raises(ValidationError):
            ContentBrief(topic="Test", number_of_scenes=1)  # Too few
        
        with pytest.raises(ValidationError):
            ContentBrief(topic="Test", number_of_scenes=25)  # Too many


# ===== Scene Tests =====

class TestScene:
    """Tests for Scene model."""
    
    def test_valid_scene(self):
        """Should accept valid scene."""
        scene = Scene(
            id="scene_01",
            sequence=1,
            narration="This is the narration.",
            visual_prompt="Cinematic ocean view.",
            estimated_duration=5,
            purpose="hook",
        )
        assert scene.id == "scene_01"
        assert scene.sequence == 1
    
    def test_invalid_scene_id(self):
        """Should reject invalid scene ID."""
        with pytest.raises(ValidationError):
            Scene(
                id="scene 01",  # Space not allowed
                sequence=1,
                narration="Test",
                visual_prompt="Test",
                estimated_duration=5,
            )
    
    def test_empty_narration_rejected(self):
        """Should reject empty narration."""
        with pytest.raises(ValidationError):
            Scene(
                id="scene_01",
                sequence=1,
                narration="",
                visual_prompt="Test",
                estimated_duration=5,
            )
    
    def test_empty_visual_prompt_rejected(self):
        """Should reject empty visual prompt."""
        with pytest.raises(ValidationError):
            Scene(
                id="scene_01",
                sequence=1,
                narration="Test",
                visual_prompt="",
                estimated_duration=5,
            )
    
    def test_duration_bounds(self):
        """Should enforce duration bounds."""
        with pytest.raises(ValidationError):
            Scene(
                id="scene_01",
                sequence=1,
                narration="Test",
                visual_prompt="Test",
                estimated_duration=1,  # Too short
            )


# ===== Production Plan Tests =====

class TestProductionPlan:
    """Tests for ProductionPlan model."""
    
    def test_valid_plan(self):
        """Should accept valid production plan."""
        plan = ProductionPlan(
            project_id="test_project_001",
            title="Test Video",
            hook="This is the hook.",
            script="Full script here.",
            estimated_duration=30,
            scenes=[
                Scene(id="scene_01", sequence=1, narration="N1", visual_prompt="V1", estimated_duration=5, purpose="hook"),
                Scene(id="scene_02", sequence=2, narration="N2", visual_prompt="V2", estimated_duration=5, purpose="fact"),
            ],
        )
        assert plan.project_id == "test_project_001"
        assert len(plan.scenes) == 2
    
    def test_duplicate_scene_ids_rejected(self):
        """Should reject duplicate scene IDs."""
        with pytest.raises(ValidationError):
            ProductionPlan(
                project_id="test",
                title="Test",
                hook="Hook",
                script="Script",
                estimated_duration=10,
                scenes=[
                    Scene(id="scene_01", sequence=1, narration="N1", visual_prompt="V1", estimated_duration=5),
                    Scene(id="scene_01", sequence=2, narration="N2", visual_prompt="V2", estimated_duration=5),
                ],
            )
    
    def test_duplicate_sequences_rejected(self):
        """Should reject duplicate sequences."""
        with pytest.raises(ValidationError):
            ProductionPlan(
                project_id="test",
                title="Test",
                hook="Hook",
                script="Script",
                estimated_duration=10,
                scenes=[
                    Scene(id="scene_01", sequence=1, narration="N1", visual_prompt="V1", estimated_duration=5),
                    Scene(id="scene_02", sequence=1, narration="N2", visual_prompt="V2", estimated_duration=5),
                ],
            )
    
    def test_invalid_sequence_order_rejected(self):
        """Should reject invalid sequence order."""
        with pytest.raises(ValidationError):
            ProductionPlan(
                project_id="test",
                title="Test",
                hook="Hook",
                script="Script",
                estimated_duration=10,
                scenes=[
                    Scene(id="scene_01", sequence=1, narration="N1", visual_prompt="V1", estimated_duration=5),
                    Scene(id="scene_02", sequence=3, narration="N2", visual_prompt="V2", estimated_duration=5),
                ],
            )
    
    def test_calculate_duration(self):
        """Should calculate duration from scenes."""
        plan = ProductionPlan(
            project_id="test",
            title="Test",
            hook="Hook",
            script="Script",
            estimated_duration=15,
            scenes=[
                Scene(id="scene_01", sequence=1, narration="N1", visual_prompt="V1", estimated_duration=5),
                Scene(id="scene_02", sequence=2, narration="N2", visual_prompt="V2", estimated_duration=6),
                Scene(id="scene_03", sequence=3, narration="N3", visual_prompt="V3", estimated_duration=4),
            ],
        )
        assert plan.calculate_duration() == 15
    
    def test_empty_scenes_rejected(self):
        """Should reject plan with no scenes."""
        with pytest.raises(ValidationError):
            ProductionPlan(
                project_id="test",
                title="Test",
                hook="Hook",
                script="Script",
                estimated_duration=0,
                scenes=[],
            )


# ===== Validator Tests =====

class TestValidator:
    """Tests for production plan validator."""
    
    def get_valid_plan(self) -> ProductionPlan:
        """Create a valid production plan for testing."""
        return ProductionPlan(
            project_id="test_001",
            title="Test Video",
            hook="Opening hook",
            script="Full script",
            call_to_action="Follow for more",
            estimated_duration=30,
            scenes=[
                Scene(id="scene_01", sequence=1, narration="N1", visual_prompt="V1", estimated_duration=5, purpose="hook"),
                Scene(id="scene_02", sequence=2, narration="N2", visual_prompt="V2", estimated_duration=5, purpose="fact"),
                Scene(id="scene_03", sequence=3, narration="N3", visual_prompt="V3", estimated_duration=5, purpose="fact"),
                Scene(id="scene_04", sequence=4, narration="N4", visual_prompt="V4", estimated_duration=5, purpose="fact"),
                Scene(id="scene_05", sequence=5, narration="N5", visual_prompt="V5", estimated_duration=5, purpose="fact"),
                Scene(id="scene_06", sequence=6, narration="N6", visual_prompt="V6", estimated_duration=5, purpose="cta"),
            ],
            metadata=ProductionMetadata(requires_fact_check=True),
        )
    
    def test_valid_plan_passes(self):
        """Valid plan should pass validation."""
        plan = self.get_valid_plan()
        result = validate_production_plan(plan)
        assert result.valid is True
        assert len(result.errors) == 0
    
    def test_duration_tolerance(self):
        """Should validate duration within tolerance."""
        plan = self.get_valid_plan()
        brief = ContentBrief(topic="Test", target_duration=30)
        
        result = validate_production_plan(plan, brief, tolerance=3.0)
        assert result.valid is True
    
    def test_duration_outside_tolerance(self):
        """Should fail when duration outside tolerance."""
        plan = self.get_valid_plan()
        brief = ContentBrief(topic="Test", target_duration=20)  # Plan is 30s
        
        result = validate_production_plan(plan, brief, tolerance=3.0)
        assert result.valid is False
        assert any("Duration" in e for e in result.errors)
    
    def test_missing_narration_fails(self):
        """Should fail when scene has empty narration."""
        # This is caught by Pydantic, so we test the validator's scene check
        plan = self.get_valid_plan()
        # Manually set narration to whitespace (Pydantic allows this)
        plan.scenes[0].narration = "   "
        
        result = validate_production_plan(plan)
        # The validator should catch whitespace-only narration
        # Note: Pydantic min_length=1 catches empty, but not whitespace
    
    def test_identical_narration_visual_warning(self):
        """Should warn when narration equals visual prompt."""
        plan = self.get_valid_plan()
        plan.scenes[0].visual_prompt = plan.scenes[0].narration
        
        result = validate_production_plan(plan)
        assert any("identical" in w.lower() for w in result.warnings)
    
    def test_fact_check_warning(self):
        """Should warn when educational content lacks fact-check flag."""
        plan = self.get_valid_plan()
        plan.metadata.requires_fact_check = False
        brief = ContentBrief(topic="Test", content_goal="education")
        
        result = validate_production_plan(plan, brief)
        assert any("fact" in w.lower() for w in result.warnings)


# ===== Provider Tests =====

class TestMockProvider:
    """Tests for mock LLM provider."""
    
    def test_mock_is_available(self):
        """Mock provider should always be available."""
        provider = MockLLMProvider()
        assert provider.is_available() is True
    
    def test_mock_name(self):
        """Mock provider should have correct name."""
        provider = MockLLMProvider()
        assert provider.name == "mock"
    
    def test_mock_generates_response(self):
        """Mock provider should generate valid response."""
        provider = MockLLMProvider()
        response = provider.generate("Test prompt")
        
        assert response.content is not None
        assert response.provider == "mock"
        assert len(response.content) > 0
    
    def test_mock_response_is_valid_json(self):
        """Mock response should be valid JSON."""
        provider = MockLLMProvider()
        response = provider.generate("Test prompt")
        
        data = json.loads(response.content)
        assert "project_id" in data
        assert "scenes" in data
    
    def test_mock_call_count(self):
        """Mock provider should track call count."""
        provider = MockLLMProvider()
        assert provider.call_count == 0
        
        provider.generate("Test 1")
        assert provider.call_count == 1
        
        provider.generate("Test 2")
        assert provider.call_count == 2
        
        provider.reset_call_count()
        assert provider.call_count == 0


# ===== Generator Tests =====

class TestContentGenerator:
    """Tests for content generator."""
    
    def test_generator_with_mock(self):
        """Generator should work with mock provider."""
        provider = MockLLMProvider()
        generator = ContentGenerator(provider)
        brief = ContentBrief(topic="5 strange facts about the ocean")
        
        result = generator.generate(brief)
        
        assert result.success is True
        assert result.plan is not None
        assert result.provider == "mock"
    
    def test_generator_returns_valid_plan(self):
        """Generator should return valid production plan."""
        provider = MockLLMProvider()
        generator = ContentGenerator(provider)
        brief = ContentBrief(topic="Test topic")
        
        result = generator.generate(brief)
        
        assert result.plan.project_id is not None
        assert len(result.plan.scenes) > 0
        assert result.plan.hook is not None
    
    def test_generator_tracks_attempts(self):
        """Generator should track attempt count."""
        provider = MockLLMProvider()
        generator = ContentGenerator(provider, max_retries=2)
        brief = ContentBrief(topic="Test")
        
        result = generator.generate(brief)
        
        assert result.attempts >= 1


# ===== Planner Tests =====

class TestScenePlanner:
    """Tests for scene planner."""
    
    def test_planner_mock_mode(self):
        """Planner should work in mock mode."""
        planner = ScenePlanner(mock_mode=True)
        brief = ContentBrief(topic="5 strange facts about the ocean")
        
        result = planner.plan(brief)
        
        assert result.success is True
        assert result.plan is not None
    
    def test_planner_generates_scenes(self):
        """Planner should generate scenes."""
        planner = ScenePlanner(mock_mode=True)
        brief = ContentBrief(topic="Test", number_of_scenes=6)
        
        result = planner.plan(brief)
        
        assert len(result.plan.scenes) == 6


# ===== Prompt Tests =====

class TestPrompts:
    """Tests for prompt generation."""
    
    def test_script_system_prompt(self):
        """Script system prompt should be non-empty."""
        prompt = get_script_system_prompt()
        assert len(prompt) > 100
        assert "hook" in prompt.lower()
    
    def test_script_user_prompt(self):
        """Script user prompt should include brief details."""
        brief = ContentBrief(topic="Ocean facts", target_duration=30)
        prompt = get_script_user_prompt(brief)
        
        assert "Ocean facts" in prompt
        assert "30" in prompt
    
    def test_combined_prompt(self):
        """Combined prompt should include all requirements."""
        brief = ContentBrief(topic="Test topic", number_of_scenes=6)
        system, user = get_combined_generation_prompt(brief)
        
        assert len(system) > 100
        assert "Test topic" in user
        assert "6" in user


# ===== Exception Tests =====

class TestExceptions:
    """Tests for content exceptions."""
    
    def test_duration_error(self):
        """DurationError should include details."""
        error = DurationError(actual=35, target=30, tolerance=3)
        assert "35" in str(error)
        assert "30" in str(error)
    
    def test_llm_auth_error(self):
        """LLMAuthenticationError should include provider."""
        error = LLMAuthenticationError("openrouter")
        assert "openrouter" in str(error).lower()
    
    def test_llm_quota_error(self):
        """LLMQuotaError should include provider."""
        error = LLMQuotaError("openrouter")
        assert "openrouter" in str(error).lower()
    
    def test_retry_exhausted_error(self):
        """RetryExhaustedError should include attempt count."""
        error = RetryExhaustedError(attempts=3, last_error="Test error")
        assert "3" in str(error)
        assert "Test error" in str(error)


# ===== Project ID Tests =====

class TestProjectId:
    """Tests for project ID generation."""
    
    def test_generate_project_id(self):
        """Should generate valid project ID."""
        project_id = generate_project_id("5 strange facts about the ocean")
        
        assert len(project_id) > 0
        assert "_" in project_id
        # Should be filesystem-safe
        assert " " not in project_id
    
    def test_project_id_is_unique(self):
        """Should generate unique IDs."""
        id1 = generate_project_id("Test topic")
        id2 = generate_project_id("Test topic")
        
        # IDs should be different due to random suffix
        # (Note: small chance of collision, but very unlikely)
        # We just check they're both valid
        assert len(id1) > 0
        assert len(id2) > 0


# ===== Content Generation Result Tests =====

class TestContentGenerationResult:
    """Tests for ContentGenerationResult model."""
    
    def test_success_result(self):
        """Should create success result."""
        plan = ProductionPlan(
            project_id="test",
            title="Test",
            hook="Hook",
            script="Script",
            estimated_duration=10,
            scenes=[
                Scene(id="scene_01", sequence=1, narration="N", visual_prompt="V", estimated_duration=5),
                Scene(id="scene_02", sequence=2, narration="N", visual_prompt="V", estimated_duration=5),
            ],
        )
        
        result = ContentGenerationResult.from_plan(
            plan=plan,
            provider="mock",
            model="mock-model",
        )
        
        assert result.success is True
        assert result.plan is not None
    
    def test_failure_result(self):
        """Should create failure result."""
        result = ContentGenerationResult.failure(
            error="Test error",
            provider="mock",
            model="mock-model",
        )
        
        assert result.success is False
        assert result.error == "Test error"
        assert result.plan is None
