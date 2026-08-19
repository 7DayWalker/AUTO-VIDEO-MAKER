#!/usr/bin/env python
"""
AUTO Video Maker - Script Planner CLI

Command-line interface for AI-powered script and scene planning.

Usage:
    python script_planner.py --topic "5 strange facts about the ocean"
    python script_planner.py --mock --topic "5 strange facts about the ocean"
"""

import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.content.models import ContentBrief
from app.content.planner import ScenePlanner
from app.content.validator import validate_production_plan
from app.content.providers.mock import MockLLMProvider
from app.content.providers.openrouter import OpenRouterProvider
from app.content.config import (
    CONTENT_OUTPUT_DIR,
    CONTENT_LOGS_DIR,
    DEFAULT_TARGET_DURATION,
    DEFAULT_PLATFORM,
    DEFAULT_LANGUAGE,
    DEFAULT_NUMBER_OF_SCENES,
    ensure_content_directories,
)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    ensure_content_directories()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = CONTENT_LOGS_DIR / f"planner_{timestamp}.log"
    
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="AUTO Video Maker - AI Script & Scene Planner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python script_planner.py --topic "5 strange facts about the ocean"
    python script_planner.py --mock --topic "5 strange facts about the ocean"
    python script_planner.py --topic "How to make coffee" --niche tutorial --duration 45
        """,
    )
    
    # Required
    parser.add_argument(
        "--topic", "-t",
        type=str,
        required=True,
        help="Video topic or title",
    )
    
    # Optional content parameters
    parser.add_argument(
        "--niche", "-n",
        type=str,
        default="educational",
        help="Content niche (default: educational)",
    )
    parser.add_argument(
        "--audience",
        type=str,
        default="general audience",
        help="Target audience (default: general audience)",
    )
    parser.add_argument(
        "--tone",
        type=str,
        default="curious and cinematic",
        help="Tone and style (default: curious and cinematic)",
    )
    parser.add_argument(
        "--duration", "-d",
        type=int,
        default=DEFAULT_TARGET_DURATION,
        help=f"Target duration in seconds (default: {DEFAULT_TARGET_DURATION})",
    )
    parser.add_argument(
        "--platform", "-p",
        type=str,
        default=DEFAULT_PLATFORM,
        choices=["youtube_shorts", "tiktok", "instagram_reels"],
        help=f"Target platform (default: {DEFAULT_PLATFORM})",
    )
    parser.add_argument(
        "--language", "-l",
        type=str,
        default=DEFAULT_LANGUAGE,
        help=f"Content language (default: {DEFAULT_LANGUAGE})",
    )
    parser.add_argument(
        "--goal",
        type=str,
        default="education",
        choices=["education", "entertainment", "inspiration", "promotion", "storytelling", "tutorial"],
        help="Content goal (default: education)",
    )
    parser.add_argument(
        "--cta",
        type=str,
        default=None,
        help="Call to action text",
    )
    parser.add_argument(
        "--style",
        type=str,
        default="fast-paced documentary",
        help="Visual/narrative style (default: fast-paced documentary)",
    )
    parser.add_argument(
        "--scenes",
        type=int,
        default=DEFAULT_NUMBER_OF_SCENES,
        help=f"Number of scenes (default: {DEFAULT_NUMBER_OF_SCENES})",
    )
    
    # Provider options
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock provider (no API calls)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="LLM model to use (for OpenRouter)",
    )
    
    # Output options
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output directory",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)
    
    print()
    print("=" * 60)
    print("AUTO Video Maker - Script & Scene Planner")
    print("=" * 60)
    print()
    
    # Create content brief
    try:
        brief = ContentBrief(
            topic=args.topic,
            niche=args.niche,
            target_audience=args.audience,
            tone=args.tone,
            target_duration=args.duration,
            platform=args.platform,
            language=args.language,
            content_goal=args.goal,
            call_to_action=args.cta,
            style=args.style,
            number_of_scenes=args.scenes,
        )
    except Exception as e:
        print(f"ERROR: Invalid content brief: {e}")
        return 1
    
    print(f"Topic: {brief.topic}")
    print(f"Niche: {brief.niche}")
    print(f"Target Duration: {brief.target_duration}s")
    print(f"Platform: {brief.platform}")
    print(f"Scenes: {brief.number_of_scenes}")
    print()
    
    # Select provider
    if args.mock:
        provider = MockLLMProvider()
        print("Provider: MOCK (no API calls)")
    else:
        provider = OpenRouterProvider(model=args.model)
        if not provider.is_available():
            print("ERROR: OpenRouter API key not configured.")
            print("Set OPENROUTER_API_KEY environment variable or use --mock mode.")
            return 1
        print(f"Provider: OpenRouter")
        print(f"Model: {provider.model}")
    
    print()
    print("Generating production plan...")
    print()
    
    # Create planner and generate
    output_dir = args.output or CONTENT_OUTPUT_DIR
    planner = ScenePlanner(provider=provider, output_dir=output_dir)
    
    result = planner.plan_and_save(brief)
    
    print()
    print("=" * 60)
    
    if result.success and result.plan:
        print("GENERATION COMPLETE")
        print("=" * 60)
        print()
        print(f"Project ID: {result.plan.project_id}")
        print(f"Title: {result.plan.title}")
        print(f"Hook: {result.plan.hook[:80]}..." if len(result.plan.hook) > 80 else f"Hook: {result.plan.hook}")
        print(f"Scenes: {len(result.plan.scenes)}")
        print(f"Calculated Duration: {result.plan.calculate_duration():.1f}s")
        print(f"Provider: {result.provider}")
        print(f"Model: {result.model}")
        print(f"Attempts: {result.attempts}")
        
        # Validate
        validation = validate_production_plan(result.plan, brief)
        print()
        print(f"Validation: {'PASSED' if validation.valid else 'FAILED'}")
        
        if validation.warnings:
            print()
            print("Warnings:")
            for warning in validation.warnings:
                print(f"  - {warning}")
        
        if result.warnings:
            print()
            print("Generation Warnings:")
            for warning in result.warnings:
                print(f"  - {warning}")
        
        # Show output path
        output_path = output_dir / result.plan.project_id / "production_plan.json"
        print()
        print(f"Output: {output_path}")
        
        # Show scene summary
        print()
        print("Scenes:")
        for scene in result.plan.scenes:
            narration_preview = scene.narration[:50] + "..." if len(scene.narration) > 50 else scene.narration
            print(f"  {scene.id} ({scene.purpose}, {scene.estimated_duration}s): {narration_preview}")
        
        print()
        return 0
    else:
        print("GENERATION FAILED")
        print("=" * 60)
        print()
        print(f"Error: {result.error}")
        print(f"Provider: {result.provider}")
        print(f"Model: {result.model}")
        print(f"Attempts: {result.attempts}")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
