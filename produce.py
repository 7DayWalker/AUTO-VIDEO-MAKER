#!/usr/bin/env python
"""
AUTO Video Maker - Production Orchestrator CLI

Phase 2E: End-to-end video production pipeline.

Usage:
    # Mock mode (no API calls, no Flow credits)
    python produce.py --topic "5 strange facts about the ocean" --mock --flow-project-url https://flow.google/...
    
    # Dry run (verify settings without generating)
    python produce.py --topic "5 strange facts about the ocean" --dry-run --flow-project-url https://flow.google/...
    
    # Real production (uses APIs and Flow credits)
    python produce.py --topic "5 strange facts about the ocean" --flow-project-url https://flow.google/...
    
    # Resume a failed/interrupted project
    python produce.py --resume <project_id> --flow-project-url https://flow.google/...
"""

import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.orchestrator.models import OrchestratorConfig, PipelineStage
from app.orchestrator.pipeline import (
    ProductionOrchestrator,
    ORCHESTRATOR_OUTPUT_DIR,
    ORCHESTRATOR_LOGS_DIR,
    ensure_orchestrator_directories,
)
from app.orchestrator.exceptions import (
    OrchestratorError,
    StageFailedError,
    ResumeError,
    ValidationError,
    DependencyError,
)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    ensure_orchestrator_directories()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = ORCHESTRATOR_LOGS_DIR / f"produce_{timestamp}.log"
    
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    
    # Reduce noise from some libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="AUTO Video Maker - Production Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Mock mode (no API calls, creates placeholder files)
    python produce.py --topic "5 strange facts about the ocean" --mock --flow-project-url https://flow.google/p/xxx
    
    # Dry run (verify settings without generating)
    python produce.py --topic "5 strange facts about the ocean" --dry-run --flow-project-url https://flow.google/p/xxx
    
    # Real production with Edge TTS and Google Flow
    python produce.py --topic "5 strange facts about the ocean" --flow-project-url https://flow.google/p/xxx
    
    # Resume a failed project
    python produce.py --resume 20260820_ocean_facts_001 --flow-project-url https://flow.google/p/xxx
        """,
    )
    
    # Required arguments (one of topic, resume, or publish-youtube)
    topic_group = parser.add_mutually_exclusive_group(required=True)
    topic_group.add_argument(
        "--topic", "-t",
        type=str,
        help="Video topic (required for new projects)",
    )
    topic_group.add_argument(
        "--resume", "-r",
        type=str,
        metavar="PROJECT_ID",
        help="Resume an existing project by ID",
    )
    topic_group.add_argument(
        "--publish-youtube", "-p",
        type=str,
        metavar="PROJECT_ID",
        help=(
            "Publish an existing PROJECT_COMPLETE project to YouTube. "
            "Safe and idempotent: re-running this command resumes the "
            "publication state machine and never uploads a duplicate."
        ),
    )
    topic_group.add_argument(
        "--update-youtube-metadata",
        type=str,
        metavar="PROJECT_ID",
        help=(
            "Update YouTube metadata of an ALREADY-PUBLISHED project "
            "(explicit operation). This command NEVER uploads; it targets "
            "the exact persisted YouTube video ID and verifies the result "
            "through the authoritative API."
        ),
    )
    topic_group.add_argument(
        "--publish-tiktok",
        type=str,
        metavar="PROJECT_ID",
        help=(
            "Publish an existing PROJECT_COMPLETE project to TikTok via the "
            "official Content Posting API. Safe and idempotent: re-running "
            "this command resumes the publication state machine and never "
            "submits a duplicate post. Requires TikTok developer credentials."
        ),
    )
    topic_group.add_argument(
        "--connect-tiktok",
        action="store_true",
        help=(
            "Connect/confirm the TikTok account via the official Login Kit "
            "OAuth flow (PKCE). Connection only: never prepares, uploads, or "
            "publishes anything."
        ),
    )
    topic_group.add_argument(
        "--connect-youtube",
        action="store_true",
        help=(
            "Connect/confirm the YouTube channel via the existing OAuth flow. "
            "Connection only: never uploads or modifies any project."
        ),
    )
    topic_group.add_argument(
        "--connections",
        action="store_true",
        help=(
            "Read-only summary of platform connection status (YouTube, "
            "TikTok). Never opens a browser, uploads, or publishes."
        ),
    )
    
    # Flow project URL (required only for production/resume runs)
    parser.add_argument(
        "--flow-project-url", "-u",
        type=str,
        required=False,
        default=None,
        help="Google Flow project URL (required for --topic/--resume, not for --publish-youtube)",
    )
    
    # Content settings
    content_group = parser.add_argument_group("Content Settings")
    content_group.add_argument(
        "--niche",
        type=str,
        default="educational",
        help="Content niche (default: educational)",
    )
    content_group.add_argument(
        "--audience",
        type=str,
        default="general audience",
        help="Target audience (default: general audience)",
    )
    content_group.add_argument(
        "--tone",
        type=str,
        default="curious and cinematic",
        help="Tone and style (default: curious and cinematic)",
    )
    content_group.add_argument(
        "--duration", "-d",
        type=int,
        default=30,
        help="Target duration in seconds (default: 30)",
    )
    content_group.add_argument(
        "--platform",
        type=str,
        default="youtube_shorts",
        choices=["youtube_shorts", "tiktok", "instagram_reels"],
        help="Target platform (default: youtube_shorts)",
    )
    content_group.add_argument(
        "--scenes",
        type=int,
        default=6,
        help="Number of scenes (default: 6)",
    )
    content_group.add_argument(
        "--goal",
        type=str,
        default="education",
        help="Content goal (default: education)",
    )
    
    # Voice settings
    voice_group = parser.add_argument_group("Voice Settings")
    voice_group.add_argument(
        "--voice",
        type=str,
        default="en-US-GuyNeural",
        help="TTS voice (default: en-US-GuyNeural)",
    )
    voice_group.add_argument(
        "--voice-rate",
        type=str,
        default="+0%",
        help="Speech rate (default: +0%%)",
    )
    
    # Scene settings
    scene_group = parser.add_argument_group("Scene Settings")
    scene_group.add_argument(
        "--scene-model",
        type=str,
        default="Veo 3.1 - Lite",
        help="Veo model (default: Veo 3.1 - Lite)",
    )
    scene_group.add_argument(
        "--scene-duration",
        type=int,
        default=4,
        help="Scene duration in seconds (default: 4)",
    )
    scene_group.add_argument(
        "--max-scenes",
        type=int,
        default=10,
        help="Maximum scenes per run (default: 10)",
    )
    scene_group.add_argument(
        "--credit-limit",
        type=int,
        default=100,
        help="Credit limit (default: 100)",
    )
    scene_group.add_argument(
        "--visual-mode",
        type=str,
        default="google_flow",
        choices=["google_flow", "local", "hybrid"],
        help=(
            "Visual provider mode (Step 6A). google_flow = premium Veo "
            "generation via Chrome/CDP (uses Flow credits). local = zero-cost "
            "normalization of your own assets in assets/local/ (never touches "
            "Flow). hybrid is reserved. The mode is persisted in the project "
            "manifest and is authoritative on resume."
        ),
    )
    
    # Assembly settings
    assembly_group = parser.add_argument_group("Assembly Settings")
    assembly_group.add_argument(
        "--no-captions",
        action="store_true",
        help="Disable caption generation",
    )
    assembly_group.add_argument(
        "--music-volume",
        type=float,
        default=0.15,
        help="Background music volume (default: 0.15)",
    )
    
    # Execution settings
    exec_group = parser.add_argument_group("Execution Settings")
    exec_group.add_argument(
        "--mock",
        action="store_true",
        help="Use mock providers (no API calls, creates placeholder files)",
    )
    exec_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify settings without generating (no files created)",
    )
    exec_group.add_argument(
        "--ui-verify",
        action="store_true",
        help="Run the real Flow UI prompt verification path, but never click Create or generate",
    )
    exec_group.add_argument(
        "--voice-only",
        action="store_true",
        help="Stop after validated voice generation; do not enter Flow, captions, or assembly",
    )
    exec_group.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip confirmation prompts for Flow generation",
    )
    exec_group.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)
    
    # Connection management commands are pure connection operations: they
    # never touch production artifacts or publication state machines.
    if args.connect_youtube:
        from app.publishing.connections import run_connect_youtube

        return run_connect_youtube()

    if args.connect_tiktok:
        from app.publishing.connections import run_connect_tiktok

        return run_connect_tiktok()

    if args.connections:
        from app.publishing.connections import run_connections_status

        return run_connections_status()

    # Publishing is a dedicated post-production flow with its own durable
    # state machine; it never touches Flow generation or production stages.
    if args.publish_youtube:
        from app.publishing.reconciliation import run_publish_flow
        
        print()
        print("=" * 60)
        print("AUTO Video Maker - YouTube Publishing")
        print("=" * 60)
        return run_publish_flow(args.publish_youtube)
    
    # Metadata updates are an explicitly separate operation that can never
    # create a new upload; they target the persisted YouTube video ID only.
    if args.update_youtube_metadata:
        from app.publishing.reconciliation import run_metadata_update_flow
        
        print()
        print("=" * 60)
        print("AUTO Video Maker - YouTube Metadata Update (no upload)")
        print("=" * 60)
        return run_metadata_update_flow(args.update_youtube_metadata)
    
    # TikTok publishing is a dedicated official-API flow with its own durable
    # state machine; it never touches Flow generation or production stages.
    if args.publish_tiktok:
        from app.publishing.tiktok_reconciliation import run_tiktok_publish_flow
        
        print()
        print("=" * 60)
        print("AUTO Video Maker - TikTok Publishing (official Content Posting API)")
        print("=" * 60)
        return run_tiktok_publish_flow(args.publish_tiktok)
    
    # --flow-project-url is required for Flow-based runs; local mode does not
    # touch Google Flow at all.
    if not args.flow_project_url and args.visual_mode != "local":
        parser.error("--flow-project-url is required for --topic and --resume")

    if not args.flow_project_url:
        args.flow_project_url = ""  # unused in local mode
    
    print()
    print("=" * 60)
    print("AUTO Video Maker - Production Orchestrator")
    print("=" * 60)
    print()
    
    # Build configuration
    try:
        config = OrchestratorConfig(
            topic=args.topic or "Resume Project",  # Placeholder for resume
            flow_project_url=args.flow_project_url,
            niche=args.niche,
            target_audience=args.audience,
            tone=args.tone,
            target_duration=args.duration,
            platform=args.platform,
            number_of_scenes=args.scenes,
            content_goal=args.goal,
            voice=args.voice,
            voice_rate=args.voice_rate,
            scene_model=args.scene_model,
            scene_duration=args.scene_duration,
            max_scenes_per_run=args.max_scenes,
            credit_limit=args.credit_limit,
            visual_mode=args.visual_mode,
            enable_captions=not args.no_captions,
            music_volume=args.music_volume,
            mock_mode=args.mock,
            dry_run=args.dry_run,
            ui_verify_only=args.ui_verify,
            voice_only=args.voice_only,
            skip_confirmation=args.no_confirm,
            resume_project_id=args.resume,
        )
    except Exception as e:
        print(f"ERROR: Invalid configuration: {e}")
        return 1
    
    # Display configuration
    if args.resume:
        print(f"Mode: RESUME")
        print(f"Project ID: {args.resume}")
    else:
        print(f"Topic: {config.topic}")
        print(f"Duration: {config.target_duration}s")
        print(f"Scenes: {config.number_of_scenes}")
        print(f"Platform: {config.platform}")
    
    print()
    
    if config.ui_verify_only:
        print("MODE: FLOW UI VERIFICATION (no Create click, no generation, zero credits)")
    elif config.dry_run:
        print("MODE: DRY RUN (no files will be generated)")
    elif config.mock_mode:
        print("MODE: MOCK (placeholder files, no API calls)")
    else:
        print("MODE: PRODUCTION (real APIs and Flow credits)")
    
    print(f"Flow URL: {config.flow_project_url}")
    print()
    
    # Confirm if not in mock/dry-run mode
    if not config.mock_mode and not config.dry_run and not config.ui_verify_only and not config.skip_confirmation:
        print("WARNING: This will use real API calls and may consume Flow credits.")
        response = input("Continue? [y/N]: ").strip().lower()
        if response != "y":
            print("Aborted.")
            return 0
        print()
    
    # Run orchestrator
    try:
        orchestrator = ProductionOrchestrator(config)
        manifest = orchestrator.run()
        
        # Print summary
        print()
        print("=" * 60)
        print("PRODUCTION COMPLETE")
        print("=" * 60)
        print()
        print(f"Project ID: {manifest.project_id}")
        print(f"Topic: {manifest.topic}")
        print()
        
        # Stage summary
        print("Stage Results:")
        for stage in PipelineStage.get_order():
            if stage == PipelineStage.COMPLETE:
                continue
            result = manifest.get_stage_result(stage)
            if result:
                status_icon = "✓" if result.status.value == "completed" else "○" if result.status.value == "skipped" else "✗"
                duration_str = f" ({result.duration_seconds:.1f}s)" if result.duration_seconds else ""
                print(f"  {status_icon} {stage.value.upper()}: {result.status.value}{duration_str}")
        
        print()
        
        # Output files
        print("Output Files:")
        if manifest.content_plan_path:
            print(f"  Content Plan: {manifest.content_plan_path}")
        if manifest.voice_audio_path:
            print(f"  Voiceover: {manifest.voice_audio_path}")
        if manifest.captions_path:
            print(f"  Captions: {manifest.captions_path}")
        if manifest.scene_video_paths:
            print(f"  Scenes: {len(manifest.scene_video_paths)} files")
        if manifest.final_video_path:
            print(f"  FINAL VIDEO: {manifest.final_video_path}")
        
        print()
        
        if manifest.total_credits_used > 0:
            print(f"Flow Credits Used: {manifest.total_credits_used}")
            print()
        
        print(f"Project Directory: {ORCHESTRATOR_OUTPUT_DIR / manifest.project_id}")
        print()
        
        return 0
        
    except StageFailedError as e:
        print()
        print("=" * 60)
        print(f"STAGE FAILED: {e.stage.upper()}")
        print("=" * 60)
        print()
        print(f"Error: {e}")
        if e.details:
            print(f"Details: {e.details}")
        print()
        print("The project state has been saved. You can resume with:")
        print(f"  python produce.py --resume <project_id> --flow-project-url {config.flow_project_url}")
        print()
        return 1
        
    except ResumeError as e:
        print()
        print(f"RESUME ERROR: {e}")
        print()
        return 1
        
    except ValidationError as e:
        print()
        print(f"VALIDATION ERROR at {e.stage}: {e}")
        if e.path:
            print(f"Path: {e.path}")
        print()
        return 1
        
    except DependencyError as e:
        print()
        print(f"DEPENDENCY ERROR: {e}")
        print()
        return 1
        
    except OrchestratorError as e:
        print()
        print(f"ORCHESTRATOR ERROR: {e}")
        print()
        return 1
        
    except KeyboardInterrupt:
        print()
        print("Interrupted by user.")
        print("The project state has been saved. You can resume later.")
        print()
        return 130
        
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        print()
        print(f"UNEXPECTED ERROR: {e}")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
