"""
AUTO Video Maker - Scene Planning Prompts

Prompts for converting scripts into structured scene plans.
"""

import json

from app.content.config import MAX_SCENE_DURATION, MIN_SCENE_DURATION
from app.content.models import ContentBrief


def _scene_duration_contract() -> str:
    """Render the HARD scene-duration schema contract for planner prompts.

    The planner contract and the ProductionPlan schema must agree: every
    scene is schema-constrained to [MIN_SCENE_DURATION, MAX_SCENE_DURATION]
    seconds, and the total target duration is achieved through MULTIPLE
    scenes — never through one over-long scene.
    """
    return (
        f"- EVERY scene's estimated_duration MUST be a number between "
        f"{MIN_SCENE_DURATION:g} and {MAX_SCENE_DURATION:g} seconds "
        "(INCLUSIVE). This is a hard schema limit: any value outside this "
        "range makes the WHOLE plan invalid and the production fails.\n"
        f"- The SUM of all scene estimated_duration values must equal the "
        "target duration (within the stated tolerance).\n"
        "- If the target duration cannot fit inside the requested number of "
        f"scenes at {MAX_SCENE_DURATION:g} seconds or less per scene, create "
        "the minimum number of ADDITIONAL scenes needed to cover the target "
        "duration (keeping ids/sequences sequential: scene_01, scene_02, "
        "...). Covering the target duration with valid per-scene durations "
        "takes precedence over the exact requested scene count."
    )


def _visual_source_hint_contract() -> str:
    """Render the optional per-scene visual-source hint guidance."""
    return (
        'Each scene MAY include an optional "visual_source" hint for visual '
        "acquisition routing:\n"
        '- "ai_image": the scene depicts a specific fictional character, an '
        "invented creature/robot/character name, or an impossible-to-film "
        "cinematic moment that real photography cannot represent.\n"
        '- "stock": the scene can be represented adequately with real-world '
        "photography (real people, places, nature, objects, technology, "
        "generic concepts).\n"
        "- Omit the field entirely when unsure. Never invent other values."
    )


def _production_context_section(brief: ContentBrief) -> str:
    """Render the additive Phase 10 context block, or nothing for legacy briefs."""
    if not brief.production_context:
        return ""
    rendered = json.dumps(
        brief.production_context,
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"\n\nPRODUCTION CONTEXT (creative planning input only):\n{rendered}"


def _planning_constraints_section(brief: ContentBrief) -> str:
    """Render the Batch 1E generic capability-aware planning constraint, or
    nothing when the brief carries no constraints.

    Generic wording only — never a provider, vendor, or model identity.
    """
    constraints = getattr(brief, "planning_constraints", None)
    if constraints is None:
        return ""
    allowed = sorted(
        {int(d) for d in (constraints.allowed_scene_durations or ()) if int(d) > 0}
    )
    if not allowed:
        return ""
    rendered = ", ".join(f"{d:g}" for d in allowed)
    return (
        "\n\nEXECUTION CAPABILITY CONSTRAINT:\n"
        "Scene durations must be selected from the following execution "
        f"capability constraint: {rendered} seconds.\n"
        "Do not invent any other duration. Cover the target duration with "
        "MULTIPLE scenes when needed."
    )


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

Scene Duration Contract (HARD schema limits):
""" + _scene_duration_contract() + """

Pacing guidance within those limits:
- Hook scene: 3-4 seconds
- Content scenes: 4-8 seconds
- CTA scene: 3-5 seconds

Visual Source Hints:
""" + _visual_source_hint_contract() + """

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
    
    production_context_section = _production_context_section(brief)
    constraints_section = _planning_constraints_section(brief)

    return f"""Create a production plan for this short-form video:

Topic: {brief.topic}
Niche: {brief.niche}
Target Audience: {brief.target_audience}
Tone: {brief.tone}
Style: {brief.style}
Target Duration: {brief.target_duration} seconds
Platform: {brief.platform}
Number of Scenes: {brief.number_of_scenes}{production_context_section}{constraints_section}
{script_section}
Requirements:
1. Create {brief.number_of_scenes} scenes — unless the target duration does
   not fit in that many scenes at {MAX_SCENE_DURATION:g} seconds or less per
   scene; then create the minimum number of additional scenes needed
2. Total duration must be approximately {brief.target_duration} seconds (±3
   seconds), and EVERY scene's estimated_duration MUST be between
   {MIN_SCENE_DURATION:g} and {MAX_SCENE_DURATION:g} seconds (hard schema
   limit; values outside this range are rejected)
3. Each scene needs both narration AND a separate visual_prompt
4. Scene IDs must be: scene_01, scene_02, etc.
5. Sequences must be: 1, 2, 3, etc.
6. First scene purpose should be "hook"
7. Last scene should include the CTA
8. Each scene MAY set "visual_source": "ai_image" (specific fictional /
   impossible-to-film visual) or "stock" (real-world photography suffices);
   omit when unsure

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
      "purpose": "hook",
      "visual_source": "stock"
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
- Visual prompts should be cinematic and detailed

Scene Duration Contract (HARD schema limits):
""" + _scene_duration_contract() + """

Visual Source Hints:
""" + _visual_source_hint_contract() + """

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
    production_context_section = _production_context_section(brief)
    constraints_section = _planning_constraints_section(brief)
    
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
Number of Scenes: {brief.number_of_scenes}{production_context_section}{constraints_section}

Create a production plan with:
1. {brief.number_of_scenes} scenes — unless the target duration does not fit
   in that many scenes at {MAX_SCENE_DURATION:g} seconds or less per scene;
   then create the minimum number of additional scenes needed
2. Total duration of approximately {brief.target_duration} seconds, with
   EVERY scene's estimated_duration between {MIN_SCENE_DURATION:g} and
   {MAX_SCENE_DURATION:g} seconds (hard schema limit; values outside this
   range are rejected and the production fails)
3. Each scene with narration AND visual_prompt (different content!)
4. Scene IDs: scene_01, scene_02, etc.
5. Sequences: 1, 2, 3, etc.
6. First scene purpose: "hook"
7. Last scene includes CTA
8. Optional per-scene "visual_source": "ai_image" (specific fictional /
   impossible-to-film visual) or "stock" (real-world photography suffices);
   omit when unsure

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
      "purpose": "hook",
      "visual_source": "stock"
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
