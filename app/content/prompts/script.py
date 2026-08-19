"""
AUTO Video Maker - Script Generation Prompts

Prompts for generating video scripts from content briefs.
"""

from app.content.models import ContentBrief


def get_script_system_prompt() -> str:
    """Get the system prompt for script generation."""
    return """You are an expert short-form video scriptwriter specializing in engaging, viral content for platforms like YouTube Shorts and TikTok.

Your scripts must:
- Start with a powerful hook that grabs attention in the first 2 seconds
- Use concise, punchy sentences suitable for spoken narration
- Maintain rapid information delivery with no filler
- Create curiosity gaps that keep viewers watching
- Use natural spoken language (not textbook style)
- Avoid unnecessary introductions like "Hey guys" or "Welcome to my channel"
- Avoid repetitive conclusions
- End with a satisfying payoff
- Include a clear call-to-action when requested

Your writing style:
- Short sentences (5-15 words ideal)
- Active voice
- Present tense when possible
- Conversational but authoritative
- No excessive punctuation that sounds unnatural when spoken
- No emojis or special characters

For factual/educational content:
- Present information accurately
- Do not invent facts
- Flag content that requires fact-checking"""


def get_script_user_prompt(brief: ContentBrief) -> str:
    """
    Generate the user prompt for script generation.
    
    Args:
        brief: The content brief with topic and requirements.
    
    Returns:
        Formatted user prompt string.
    """
    cta_instruction = ""
    if brief.call_to_action:
        cta_instruction = f"\nCall to Action: End with \"{brief.call_to_action}\""
    else:
        cta_instruction = "\nCall to Action: Include a brief, natural CTA like \"Follow for more\" or similar."
    
    return f"""Write a script for a short-form video with these specifications:

Topic: {brief.topic}
Niche: {brief.niche}
Target Audience: {brief.target_audience}
Tone: {brief.tone}
Style: {brief.style}
Target Duration: {brief.target_duration} seconds
Platform: {brief.platform}
Language: {brief.language}
Content Goal: {brief.content_goal}
{cta_instruction}

Requirements:
1. The script should be approximately {brief.target_duration} seconds when read at a natural pace (about 2.5 words per second)
2. Start with an attention-grabbing hook
3. Deliver {brief.number_of_scenes - 2} main points or story beats
4. End with a satisfying conclusion and CTA

Output the script as plain text, with the hook clearly identifiable as the opening line.
Do not include stage directions, timestamps, or scene markers.
Write only the narration text that will be spoken."""
