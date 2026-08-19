#!/usr/bin/env python
"""
AUTO Video Maker - Flow Scene Generator CLI

Command-line interface for automated scene generation through Google Flow.

Usage:
    python flow_scene_generator.py --plan <path> --scene scene_01 --flow-project-url <url> --dry-run
    python flow_scene_generator.py --plan <path> --scene scene_01 --flow-project-url <url>
    python flow_scene_generator.py --plan <path> --all --flow-project-url <url>
"""

import sys
import argparse
import json
import logging
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.content.models import ProductionPlan
from app.flow.scene_config import (
    SCENE_OUTPUT_DIR,
    SCENE_SCREENSHOTS_DIR,
    FLOW_SCENE_MODEL,
    FLOW_SCENE_ASPECT_RATIO,
    FLOW_SCENE_DURATION,
    FLOW_SCENE_COUNT,
    FLOW_SCENE_MAX_PER_RUN,
    FLOW_SCENE_CREDIT_LIMIT,
    ensure_scene_directories,
)
from app.flow.scene_models import (
    FlowGenerationConfig,
    SceneGenerationRequest,
    SceneManifest,
)
from app.flow.scene_generator import (
    FlowSceneGenerator,
    FlowSceneError,
    FlowConnectionError,
    FlowAuthenticationError,
    FlowCreditError,
    load_manifest,
    save_manifest,
)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    ensure_scene_directories()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = SCENE_SCREENSHOTS_DIR / f"scene_gen_{timestamp}.log"
    
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


def select_scenes(plan: ProductionPlan, scene_arg: str = None, all_scenes: bool = False) -> list:
    """
    Select scenes to generate.
    
    Args:
        plan: Production plan.
        scene_arg: Scene ID or sequence number.
        all_scenes: If True, select all scenes.
    
    Returns:
        List of scenes to generate.
    """
    if all_scenes:
        return sorted(plan.scenes, key=lambda s: s.sequence)
    
    if not scene_arg:
        raise ValueError("Must specify --scene or --all")
    
    # Try to match by ID
    for scene in plan.scenes:
        if scene.id == scene_arg:
            return [scene]
    
    # Try to match by sequence number
    try:
        seq = int(scene_arg)
        for scene in plan.scenes:
            if scene.sequence == seq:
                return [scene]
    except ValueError:
        pass
    
    raise ValueError(f"Scene not found: {scene_arg}")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="AUTO Video Maker - Flow Scene Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Dry run (no generation)
    python flow_scene_generator.py --plan output/content/project/production_plan.json --scene scene_01 --flow-project-url https://flow.google/... --dry-run
    
    # Generate single scene
    python flow_scene_generator.py --plan output/content/project/production_plan.json --scene scene_01 --flow-project-url https://flow.google/...
    
    # Generate all scenes (requires confirmation)
    python flow_scene_generator.py --plan output/content/project/production_plan.json --all --flow-project-url https://flow.google/...
        """,
    )
    
    # Required arguments
    parser.add_argument(
        "--plan", "-p",
        type=Path,
        required=True,
        help="Path to production_plan.json",
    )
    parser.add_argument(
        "--flow-project-url", "-u",
        type=str,
        required=True,
        help="URL of the Flow project",
    )
    
    # Scene selection
    scene_group = parser.add_mutually_exclusive_group(required=True)
    scene_group.add_argument(
        "--scene", "-s",
        type=str,
        help="Scene ID or sequence number to generate",
    )
    scene_group.add_argument(
        "--all",
        action="store_true",
        help="Generate all scenes (requires confirmation)",
    )
    
    # Generation options
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify settings without generating (default: True)",
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip confirmation prompt (use with caution)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing scene files",
    )
    
    # Configuration overrides
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help=f"Model to use (default: {FLOW_SCENE_MODEL})",
    )
    parser.add_argument(
        "--aspect-ratio",
        type=str,
        default=None,
        choices=["9:16", "16:9", "1:1"],
        help=f"Aspect ratio (default: {FLOW_SCENE_ASPECT_RATIO})",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help=f"Duration in seconds (default: {FLOW_SCENE_DURATION})",
    )
    parser.add_argument(
        "--max-scenes",
        type=int,
        default=FLOW_SCENE_MAX_PER_RUN,
        help=f"Maximum scenes per run (default: {FLOW_SCENE_MAX_PER_RUN})",
    )
    parser.add_argument(
        "--credit-limit",
        type=int,
        default=FLOW_SCENE_CREDIT_LIMIT,
        help=f"Credit limit per generation (default: {FLOW_SCENE_CREDIT_LIMIT})",
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
    print("AUTO Video Maker - Flow Scene Generator")
    print("=" * 60)
    print()
    
    # Default to dry-run for safety
    dry_run = args.dry_run
    if not dry_run:
        print("WARNING: Real generation mode - credits will be consumed!")
    else:
        print("DRY RUN MODE - no generation will occur")
    print()
    
    # Load production plan
    try:
        plan = load_production_plan(args.plan)
        print(f"Loaded plan: {plan.project_id}")
        print(f"Title: {plan.title}")
        print(f"Total scenes: {len(plan.scenes)}")
    except Exception as e:
        print(f"ERROR: Failed to load production plan: {e}")
        return 1
    
    # Select scenes
    try:
        scenes = select_scenes(plan, args.scene, args.all)
        print(f"Selected scenes: {len(scenes)}")
        for scene in scenes:
            print(f"  - {scene.id} (seq {scene.sequence})")
    except Exception as e:
        print(f"ERROR: {e}")
        return 1
    
    # Check max scenes limit
    if len(scenes) > args.max_scenes:
        print(f"ERROR: Selected {len(scenes)} scenes, but max is {args.max_scenes}")
        print("Use --max-scenes to increase the limit")
        return 1
    
    # Build configuration
    config = FlowGenerationConfig(
        model=args.model or FLOW_SCENE_MODEL,
        aspect_ratio=args.aspect_ratio or FLOW_SCENE_ASPECT_RATIO,
        duration=args.duration or FLOW_SCENE_DURATION,
        count=FLOW_SCENE_COUNT,
        agent_enabled=False,
    )
    
    print()
    print("Configuration:")
    print(f"  Model: {config.model}")
    print(f"  Aspect ratio: {config.aspect_ratio}")
    print(f"  Duration: {config.duration}s")
    print(f"  Count: x{config.count}")
    print(f"  Credit limit: {args.credit_limit}")
    print()
    
    # Create generator
    output_dir = args.output or SCENE_OUTPUT_DIR
    generator = FlowSceneGenerator(config=config, output_dir=output_dir)
    
    # Connect to Chrome
    print("Connecting to Chrome...")
    try:
        generator.connect()
        print("Connected to Chrome via CDP")
    except FlowConnectionError as e:
        print(f"ERROR: {e}")
        print()
        print("Make sure Chrome is running with debugging enabled:")
        print("  python launch_chrome.py")
        return 1
    
    try:
        # Navigate to Flow project
        print()
        print(f"Navigating to Flow project...")
        try:
            generator.navigate_to_project(args.flow_project_url)
            print("Flow project loaded")
        except FlowAuthenticationError as e:
            print(f"ERROR: {e}")
            print()
            print("Please log in to Google in the Chrome window and try again.")
            return 1
        except FlowSceneError as e:
            print(f"ERROR: {e}")
            return 1
        
        # Process scenes
        project_dir = output_dir / plan.project_id
        manifest = load_manifest(project_dir, plan.project_id)
        
        results = []
        
        for i, scene in enumerate(scenes):
            print()
            print(f"Processing scene {i+1}/{len(scenes)}: {scene.id}")
            print("-" * 40)
            
            # Create request
            request = SceneGenerationRequest(
                project_id=plan.project_id,
                scene_id=scene.id,
                sequence=scene.sequence,
                visual_prompt=scene.visual_prompt,
                config=config,
                flow_project_url=args.flow_project_url,
            )
            
            # Generate
            try:
                result = generator.generate_scene(
                    request=request,
                    dry_run=dry_run,
                    require_confirmation=not args.no_confirm,
                )
                results.append(result)
                
                # Update manifest
                if result.success and result.status == "completed":
                    manifest.add_scene(
                        scene_id=scene.id,
                        sequence=scene.sequence,
                        file=f"{scene.id}.mp4",
                        status="completed",
                    )
                    save_manifest(manifest, project_dir)
                
                # Report result
                print()
                print(f"Result: {result.status.upper()}")
                if result.file_path:
                    print(f"File: {result.file_path}")
                if result.generation_cost:
                    print(f"Cost: {result.generation_cost} credits")
                if result.error:
                    print(f"Error: {result.error}")
                
            except FlowCreditError as e:
                print(f"CREDIT ERROR: {e}")
                results.append(None)
                break
            except FlowSceneError as e:
                print(f"ERROR: {e}")
                results.append(None)
                # Don't continue on error
                break
        
        # Summary
        print()
        print("=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print()
        
        completed = sum(1 for r in results if r and r.status == "completed")
        dry_runs = sum(1 for r in results if r and r.status == "dry_run")
        skipped = sum(1 for r in results if r and r.status == "skipped")
        failed = sum(1 for r in results if r and r.status in ("failed", "timeout"))
        
        print(f"Total scenes: {len(scenes)}")
        print(f"Completed: {completed}")
        print(f"Dry runs: {dry_runs}")
        print(f"Skipped: {skipped}")
        print(f"Failed: {failed}")
        
        if dry_run:
            print()
            print("DRY_RUN_COMPLETE")
            print("NO_GENERATION_PERFORMED")
        
        print()
        print(f"Output directory: {project_dir}")
        print()
        
        return 0 if failed == 0 else 1
        
    finally:
        generator.disconnect()


if __name__ == "__main__":
    sys.exit(main())
