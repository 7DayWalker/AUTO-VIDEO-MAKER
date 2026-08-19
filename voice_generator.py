#!/usr/bin/env python
"""
AUTO Video Maker - Voice Generator CLI

Command-line interface for TTS voice generation.

Usage:
    python voice_generator.py --plan output/content/<project_id>/production_plan.json
    python voice_generator.py --mock --plan output/content/<project_id>/production_plan.json
    python voice_generator.py --list-voices
"""

import sys
import asyncio
import argparse
import json
import logging
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.content.models import ProductionPlan
from app.voice.generator import VoiceGenerator, get_provider
from app.voice.providers.mock import MockVoiceProvider
from app.voice.providers.edge_tts import EdgeTTSProvider
from app.voice.config import (
    VOICE_OUTPUT_DIR,
    VOICE_LOGS_DIR,
    TTS_VOICE,
    TTS_RATE,
    TTS_PITCH,
    TTS_VOLUME,
    TTS_OUTPUT_FORMAT,
    ensure_voice_directories,
)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    ensure_voice_directories()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = VOICE_LOGS_DIR / f"voice_{timestamp}.log"
    
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_production_plan(plan_path: Path) -> ProductionPlan:
    """Load production plan from JSON file."""
    if not plan_path.exists():
        raise FileNotFoundError(f"Production plan not found: {plan_path}")
    
    with open(plan_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    return ProductionPlan(**data)


async def list_voices_async(provider, locale: str = None) -> None:
    """List available voices."""
    voices = await provider.list_voices(locale)
    
    print(f"\nAvailable voices ({len(voices)}):\n")
    
    for voice in voices:
        locale_str = f" [{voice.locale}]" if voice.locale else ""
        gender_str = f" ({voice.gender})" if voice.gender else ""
        print(f"  {voice.short_name}{locale_str}{gender_str}")
        if voice.name != voice.short_name:
            print(f"    Name: {voice.name}")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="AUTO Video Maker - TTS Voice Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python voice_generator.py --plan output/content/project_id/production_plan.json
    python voice_generator.py --mock --plan output/content/project_id/production_plan.json
    python voice_generator.py --list-voices
    python voice_generator.py --list-voices --locale en-US
        """,
    )
    
    # Main options
    parser.add_argument(
        "--plan", "-p",
        type=Path,
        help="Path to production_plan.json",
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="List available voices",
    )
    
    # Voice options
    parser.add_argument(
        "--voice", "-v",
        type=str,
        default=None,
        help=f"Voice to use (default: {TTS_VOICE})",
    )
    parser.add_argument(
        "--rate",
        type=str,
        default=None,
        help=f"Speech rate (default: {TTS_RATE})",
    )
    parser.add_argument(
        "--pitch",
        type=str,
        default=None,
        help=f"Pitch adjustment (default: {TTS_PITCH})",
    )
    parser.add_argument(
        "--volume",
        type=str,
        default=None,
        help=f"Volume adjustment (default: {TTS_VOLUME})",
    )
    parser.add_argument(
        "--format",
        type=str,
        default=None,
        choices=["mp3", "wav"],
        help=f"Output format (default: {TTS_OUTPUT_FORMAT})",
    )
    parser.add_argument(
        "--locale",
        type=str,
        default=None,
        help="Filter voices by locale (e.g., en-US)",
    )
    
    # Provider options
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock provider (no TTS API calls)",
    )
    
    # Output options
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output directory",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)
    
    print()
    print("=" * 60)
    print("AUTO Video Maker - Voice Generator")
    print("=" * 60)
    print()
    
    # Select provider
    if args.mock:
        provider = MockVoiceProvider()
        print("Provider: MOCK (no TTS API calls)")
    else:
        provider = EdgeTTSProvider()
        if not provider.is_available():
            print("WARNING: edge-tts not installed. Using mock provider.")
            print("Install with: pip install edge-tts")
            provider = MockVoiceProvider()
        else:
            print("Provider: Edge TTS")
    
    print()
    
    # Handle list-voices
    if args.list_voices:
        try:
            asyncio.run(list_voices_async(provider, args.locale))
            return 0
        except Exception as e:
            print(f"ERROR: Failed to list voices: {e}")
            return 1
    
    # Require plan for generation
    if not args.plan:
        print("ERROR: --plan is required for voice generation")
        print("Use --list-voices to see available voices")
        return 1
    
    # Load production plan
    try:
        plan = load_production_plan(args.plan)
        print(f"Loaded plan: {plan.project_id}")
        print(f"Title: {plan.title}")
        print(f"Scenes: {len(plan.scenes)}")
    except Exception as e:
        print(f"ERROR: Failed to load production plan: {e}")
        return 1
    
    # Create generator
    output_dir = args.output or VOICE_OUTPUT_DIR
    generator = VoiceGenerator(provider=provider, output_dir=output_dir)
    
    print()
    print("Generating voiceover...")
    print()
    
    # Generate
    result = generator.generate_from_plan(
        plan=plan,
        voice=args.voice,
        rate=args.rate,
        pitch=args.pitch,
        volume=args.volume,
        output_format=args.format,
    )
    
    print()
    print("=" * 60)
    
    if result.success:
        print("GENERATION COMPLETE")
        print("=" * 60)
        print()
        print(f"Provider: {result.provider}")
        print(f"Voice: {result.voice}")
        print(f"Duration: {result.duration:.2f}s")
        print(f"File Size: {result.file_size / 1024:.1f} KB")
        print(f"Format: {result.format}")
        if result.sample_rate:
            print(f"Sample Rate: {result.sample_rate} Hz")
        if result.channels:
            print(f"Channels: {result.channels}")
        
        if result.warnings:
            print()
            print("Warnings:")
            for warning in result.warnings:
                print(f"  - {warning}")
        
        print()
        print(f"Output: {result.output_path}")
        print()
        return 0
    else:
        print("GENERATION FAILED")
        print("=" * 60)
        print()
        print(f"Error: {result.error}")
        print(f"Provider: {result.provider}")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
