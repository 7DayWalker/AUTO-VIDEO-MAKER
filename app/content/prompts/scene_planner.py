"""
AUTO Video Maker - Scene Planning Prompts

Prompts for converting scripts into structured scene plans.
"""

from app.content.models import ContentBrief


def get_scene_planner_system_prompt() -> str:
    """Get the system prompt for scene planning."""
    return """You are an expert video production planner specializing in short-form content for YouTube Shorts and TikTok.

Your role is to convert scripts into structured production plans with:
1. Clear scene breakdowns
2. Narration text for each scene (what the voiceover says)
3. Visual prompts for each scene (what the AI video generator should create)

CRITICAL: Narration and Visual Prompt are DIFFERENT fields:
- NARRATION: The exact text the voiceover will speak
- VISUAL PROMPT: A detailed description for AI video generation (NOT the narration)

Visual Prompt Guidelines:
- Describe the visual scene in detail
- Include camera angles, lighting, mood
- Specify style (cinematic, documentary, etc.)
- Include motion/movement descriptions
- Do NOT include text or narration in visual prompts
- Make prompts suitable for AI video generation (like Google Flow/Veo)

Scene Duration Guidelines:
- Each scene should be 3-6 seconds for short-form content
- Hook scene: 3-4 seconds
- Content scenes: 4-6 seconds
- CTA scene: 3-5 seconds
- Total duration must match target duration (within tolerance)

Scene Purposes:
- hook: Opening attention-grabber
- intro: Brief context setting
- fact: Information delivery
- story: Narrative element
- example: Illustration or demonstration
- transition: Bridge between sections
- climax: Peak moment
- conclusion: Wrap-up
- cta: Call to action

Output Format:
You must output valid JSON matching the ProductionPlan schema exactly."""


def get_scene_planner_user_prompt(brief: ContentBrief, script: str = None) -> str:
    """
    Generate the user prompt for scene planning.
    
    Args:
        brief: The content brief.
        script: Optional pre-generated script.
    
    Returns:
        Formatted user prompt string.
    """
    script_section = ""
    if script:
        script_section = f"""
Script to convert:
\"\"\"
{script}
\"\"\"
"""
    
    return f"""Create a production plan for this short-form video:

Topic: {brief.topic}
Niche: {brief.niche}
Target Audience: {brief.target_audience}
Tone: {brief.tone}
Style: {brief.style}
Target Duration: {brief.target_duration} seconds
Platform: {brief.platform}
Number of Scenes: {brief.number_of_scenes}
{script_section}
Requirements:
1. Create exactly {brief.number_of_scenes} scenes
2. Total duration must be approximately {brief.target_duration} seconds (±3 seconds)
3. Each scene needs both narration AND a separate visual_prompt
4. Scene IDs must be: scene_01, scene_02, etc.
5. Sequences must be: 1, 2, 3, etc.
6. First scene purpose should be "hook"
7. Last scene should include the CTA

Output valid JSON with this exact structure:
{{
  "project_id": "unique_id_here",
  "title": "Video Title",
  "hook": "Opening hook line",
  "script": "Full narration script",
  "call_to_action": "CTA text or null",
  "estimated_duration": {brief.target_duration},
  "scenes": [
    {{
      "id": "scene_01",
      "sequence": 1,
      "narration": "What the voiceover says",
      "visual_prompt": "Detailed visual description for AI video generation",
      "estimated_duration": 5,
      "purpose": "hook"
    }}
  ],
  "metadata": {{
    "platform": "{brief.platform}",
    "language": "{brief.language}",
    "niche": "{brief.niche}",
    "target_audience": "{brief.target_audience}",
    "content_goal": "{brief.content_goal}",
    "style": "{brief.style}",
    "requires_fact_check": true
  }}
}}"""


def get_combined_generation_prompt(brief: ContentBrief) -> tuple:
    """
    Get combined system and user prompts for single-call generation.
    
    This generates both script and scene plan in one LLM call.
    
    Args:
        brief: The content brief.
    
    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    system_prompt = """You are an expert short-form video content creator and production planner.

Your task is to create a complete production plan including:
1. An engaging script optimized for short-form video
2. A structured scene breakdown with visual prompts

Script Requirements:
- Start with a powerful hook (first 2 seconds)
- Use concise, punchy sentences for spoken narration
- Natural spoken language (not textbook style)
- No unnecessary introductions or filler
- Clear progression and satisfying payoff
- Include CTA when requested

Scene Planning Requirements:
- NARRATION: Exact text for voiceover (what is spoken)
- VISUAL PROMPT: Detailed description for AI video generation (what is shown)
- These are DIFFERENT - visual prompts describe visuals, not narration
- Each scene: 3-6 seconds
- Visual prompts should be cinematic and detailed

Visual Prompt Style:
- Describe camera angles, lighting, mood
- Include motion and movement
- Specify visual style (cinematic, documentary, etc.)
- Make suitable for AI video generation
- NO text or narration in visual prompts

For factual/educational content:
- Present information accurately
- Do not invent facts
- Set requires_fact_check to true

Output ONLY valid JSON matching the ProductionPlan schema. No markdown, no explanation."""

    cta_text = brief.call_to_action or "Follow for more"
    
    user_prompt = f"""Create a complete production plan for this short-form video:

Topic: {brief.topic}
Niche: {brief.niche}
Target Audience: {brief.target_audience}
Tone: {brief.tone}
Style: {brief.style}
Target Duration: {brief.target_duration} seconds
Platform: {brief.platform}
Language: {brief.language}
Content Goal: {brief.content_goal}
Call to Action: {cta_text}
Number of Scenes: {brief.number_of_scenes}

Create a production plan with:
1. Exactly {brief.number_of_scenes} scenes
2. Total duration of approximately {brief.target_duration} seconds
3. Each scene with narration AND visual_prompt (different content!)
4. Scene IDs: scene_01, scene_02, etc.
5. Sequences: 1, 2, 3, etc.
6. First scene purpose: "hook"
7. Last scene includes CTA

Output this exact JSON structure:
{{
  "project_id": "YYYYMMDD_topic_slug_XXX",
  "title": "Engaging Video Title",
  "hook": "Opening hook line from script",
  "script": "Complete narration script (all scenes combined)",
  "call_to_action": "{cta_text}",
  "estimated_duration": {brief.target_duration},
  "scenes": [
    {{
      "id": "scene_01",
      "sequence": 1,
      "narration": "Spoken text for this scene",
      "visual_prompt": "Detailed visual description for AI video generation - camera, lighting, mood, motion",
      "estimated_duration": 4,
      "purpose": "hook"
    }},
    // ... more scenes
  ],
  "metadata": {{
    "platform": "{brief.platform}",
    "language": "{brief.language}",
    "niche": "{brief.niche}",
    "target_audience": "{brief.target_audience}",
    "content_goal": "{brief.content_goal}",
    "style": "{brief.style}",
    "requires_fact_check": {"true" if brief.content_goal == "education" else "false"}
  }}
}}

Output ONLY the JSON. No markdown code blocks. No explanation."""

    return system_prompt, user_prompt
