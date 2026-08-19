"""
AUTO Video Maker - Content Prompts

Prompt templates for script generation and scene planning.
"""

from app.content.prompts.script import get_script_system_prompt, get_script_user_prompt
from app.content.prompts.scene_planner import (
    get_scene_planner_system_prompt,
    get_scene_planner_user_prompt,
    get_combined_generation_prompt,
)

__all__ = [
    "get_script_system_prompt",
    "get_script_user_prompt",
    "get_scene_planner_system_prompt",
    "get_scene_planner_user_prompt",
    "get_combined_generation_prompt",
]
