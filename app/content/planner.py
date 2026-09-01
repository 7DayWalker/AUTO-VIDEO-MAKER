"""
AUTO Video Maker - Scene Planner

High-level orchestration for content planning.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from app.content.models import ContentBrief, ProductionPlan, ContentGenerationResult
from app.content.generator import ContentGenerator
from app.content.providers.base import BaseLLMProvider
from app.content.providers.mock import MockLLMProvider
from app.content.config import (
    CONTENT_OUTPUT_DIR,
    ensure_content_directories,
)

logger = logging.getLogger(__name__)


class ScenePlanner:
    """
    High-level scene planning orchestrator.
    
    Coordinates content generation and output management.
    """
    
    def __init__(
        self,
        provider: BaseLLMProvider = None,
        output_dir: Path = None,
        mock_mode: bool = False,
    ):
        if mock_mode:
            self.provider = MockLLMProvider()
        elif provider:
            self.provider = provider
        else:
            # Default to mock if no provider specified
            self.provider = MockLLMProvider()
        
        self.output_dir = output_dir or CONTENT_OUTPUT_DIR
        self.generator = ContentGenerator(self.provider)
    
    def plan(self, brief: ContentBrief) -> ContentGenerationResult:
        """
        Generate a production plan from a content brief.
        
        Args:
            brief: The content brief.
        
        Returns:
            ContentGenerationResult with the plan or error.
        """
        logger.info(f"Planning content: {brief.topic}")
        return self.generator.generate(brief)
    
    def plan_and_save(
        self,
        brief: ContentBrief,
        save_brief: bool = True,
    ) -> ContentGenerationResult:
        """
        Generate a production plan and save to disk.
        
        Args:
            brief: The content brief.
            save_brief: Whether to also save the content brief.
        
        Returns:
            ContentGenerationResult with the plan or error.
        """
        # Ensure directories exist
        ensure_content_directories()
        
        # Generate plan
        result = self.plan(brief)
        
        if not result.success or not result.plan:
            return result
        
        # Create project directory
        project_dir = self.output_dir / result.plan.project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        
        # Save production plan
        plan_path = project_dir / "production_plan.json"
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(result.plan.model_dump(), f, indent=2, ensure_ascii=False)
        logger.info(f"Saved production plan: {plan_path}")
        
        # Save content brief
        if save_brief:
            brief_path = project_dir / "content_brief.json"
            with open(brief_path, "w", encoding="utf-8") as f:
                json.dump(brief.model_dump(), f, indent=2, ensure_ascii=False)
            logger.info(f"Saved content brief: {brief_path}")
        
        return result
    
    @staticmethod
    def load_plan(project_id: str, output_dir: Path = None) -> Optional[ProductionPlan]:
        """
        Load a production plan from disk.
        
        Args:
            project_id: The project ID.
            output_dir: Output directory (defaults to CONTENT_OUTPUT_DIR).
        
        Returns:
            ProductionPlan if found, None otherwise.
        """
        output_dir = output_dir or CONTENT_OUTPUT_DIR
        plan_path = output_dir / project_id / "production_plan.json"
        
        if not plan_path.exists():
            return None
        
        with open(plan_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return ProductionPlan(**data)
    
    @staticmethod
    def load_brief(project_id: str, output_dir: Path = None) -> Optional[ContentBrief]:
        """
        Load a content brief from disk.
        
        Args:
            project_id: The project ID.
            output_dir: Output directory (defaults to CONTENT_OUTPUT_DIR).
        
        Returns:
            ContentBrief if found, None otherwise.
        """
        output_dir = output_dir or CONTENT_OUTPUT_DIR
        brief_path = output_dir / project_id / "content_brief.json"
        
        if not brief_path.exists():
            return None
        
        with open(brief_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return ContentBrief(**data)


def create_scene_planner(*, mock_mode: bool = False, output_dir: Path = None) -> ScenePlanner:
    """Build the existing planner from runtime provider configuration."""
    if mock_mode:
        return ScenePlanner(mock_mode=True, output_dir=output_dir)
    from app.content.providers import get_default_provider

    provider = get_default_provider()
    if not provider.is_available():
        raise RuntimeError(f"content provider unavailable: {provider.name}")
    return ScenePlanner(provider=provider, output_dir=output_dir)
