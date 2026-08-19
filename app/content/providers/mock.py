"""
AUTO Video Maker - Mock LLM Provider

Mock provider for testing without API calls.
"""

import json
from typing import Optional, Type
from pydantic import BaseModel
import logging

from app.content.providers.base import BaseLLMProvider, LLMResponse

logger = logging.getLogger(__name__)


# Deterministic mock response for ocean facts topic
MOCK_PRODUCTION_PLAN = {
    "project_id": "20260819_ocean_facts_mock",
    "title": "5 Ocean Facts That Sound Fake",
    "hook": "The ocean hides things that sound like science fiction.",
    "script": "The ocean hides things that sound like science fiction. The Mariana Trench is deeper than Mount Everest is tall. If you placed Everest at the bottom, you'd still have over a mile of water above it. The ocean produces over fifty percent of Earth's oxygen. Tiny phytoplankton are the real lungs of our planet. There's a lake at the bottom of the Gulf of Mexico. It's so salty that it kills almost anything that swims into it. The longest mountain range on Earth is underwater. The Mid-Atlantic Ridge stretches over ten thousand miles. We've explored less than five percent of the ocean floor. There could be millions of undiscovered species down there. Follow for more fascinating facts.",
    "call_to_action": "Follow for more fascinating facts.",
    "estimated_duration": 31,
    "scenes": [
        {
            "id": "scene_01",
            "sequence": 1,
            "narration": "The ocean hides things that sound like science fiction.",
            "visual_prompt": "Cinematic aerial shot of vast dark ocean surface, mysterious deep blue water stretching to horizon, dramatic lighting, documentary style, 4K quality",
            "estimated_duration": 4,
            "purpose": "hook"
        },
        {
            "id": "scene_02",
            "sequence": 2,
            "narration": "The Mariana Trench is deeper than Mount Everest is tall. If you placed Everest at the bottom, you'd still have over a mile of water above it.",
            "visual_prompt": "Cross-section visualization comparing Mount Everest to Mariana Trench depth, dramatic scale comparison, deep ocean darkness below, educational infographic style, cinematic lighting",
            "estimated_duration": 6,
            "purpose": "fact"
        },
        {
            "id": "scene_03",
            "sequence": 3,
            "narration": "The ocean produces over fifty percent of Earth's oxygen. Tiny phytoplankton are the real lungs of our planet.",
            "visual_prompt": "Microscopic view of glowing phytoplankton, bioluminescent particles floating in dark water, oxygen bubbles rising, ethereal underwater lighting, documentary macro photography",
            "estimated_duration": 5,
            "purpose": "fact"
        },
        {
            "id": "scene_04",
            "sequence": 4,
            "narration": "There's a lake at the bottom of the Gulf of Mexico. It's so salty that it kills almost anything that swims into it.",
            "visual_prompt": "Underwater brine pool at ocean floor, distinct boundary between water layers, eerie glow, dead marine life around edges, deep sea documentary footage style",
            "estimated_duration": 5,
            "purpose": "fact"
        },
        {
            "id": "scene_05",
            "sequence": 5,
            "narration": "The longest mountain range on Earth is underwater. The Mid-Atlantic Ridge stretches over ten thousand miles.",
            "visual_prompt": "Dramatic underwater mountain range, volcanic ridges stretching into darkness, hydrothermal vents glowing, epic scale, cinematic deep ocean exploration footage",
            "estimated_duration": 5,
            "purpose": "fact"
        },
        {
            "id": "scene_06",
            "sequence": 6,
            "narration": "We've explored less than five percent of the ocean floor. There could be millions of undiscovered species down there. Follow for more fascinating facts.",
            "visual_prompt": "Mysterious deep ocean abyss, strange bioluminescent creatures swimming in darkness, sense of vast unexplored space, documentary style, ending with subtle upward camera movement",
            "estimated_duration": 6,
            "purpose": "cta"
        }
    ],
    "metadata": {
        "platform": "youtube_shorts",
        "language": "en",
        "niche": "educational",
        "target_audience": "general audience",
        "content_goal": "education",
        "style": "curious and cinematic",
        "requires_fact_check": True,
        "provider": "mock",
        "model": "mock-model"
    }
}


class MockLLMProvider(BaseLLMProvider):
    """
    Mock LLM provider for testing.
    
    Returns deterministic responses without making API calls.
    """
    
    def __init__(self, model_name: str = "mock-model"):
        self._model = model_name
        self._call_count = 0
    
    @property
    def name(self) -> str:
        return "mock"
    
    @property
    def model(self) -> str:
        return self._model
    
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate a mock response."""
        self._call_count += 1
        logger.info(f"Mock provider called (call #{self._call_count})")
        
        # Return the mock production plan as JSON
        content = json.dumps(MOCK_PRODUCTION_PLAN, indent=2)
        
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self.model,
            usage={
                "prompt_tokens": len(prompt.split()),
                "completion_tokens": len(content.split()),
                "total_tokens": len(prompt.split()) + len(content.split()),
            },
        )
    
    def is_available(self) -> bool:
        """Mock provider is always available."""
        return True
    
    @property
    def call_count(self) -> int:
        """Number of times generate was called."""
        return self._call_count
    
    def reset_call_count(self) -> None:
        """Reset the call counter."""
        self._call_count = 0


def get_mock_production_plan() -> dict:
    """Get the mock production plan data."""
    return MOCK_PRODUCTION_PLAN.copy()
