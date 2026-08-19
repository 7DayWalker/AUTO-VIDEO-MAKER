#!/usr/bin/env python
"""
AUTO Video Maker - Assembly CLI

Command-line interface for video assembly.

Usage:
    python assemble.py projects/test_project.json
    python assemble.py projects/test_project.json --output output/custom/
"""

import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.assembly.pipeline import assemble_project, load_project_from_json
from app.assembly.ffmpeg import check_ffmpeg_available, check_ffprobe_available
from app.assembly.config import ASSEMBLY_OUTPUT_DIR, ASSEMBLY_LOGS_DIR


def setup_logging(log_file: Path = None) -> None:
    """Configure logging for the assembly process."""
    ASSEMBLY_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    if log_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = ASSEMBLY_LOGS_DIR / f"assembly_{timestamp}.log"
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="AUTO Video Maker - Assemble videos from project definition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python assemble.py projects/my_project.json
    python assemble.py projects/my_project.json --output output/custom/
    python assemble.py projects/my_project.json --check-only
        """,
    )
    parser.add_argument(
        "project",
        type=Path,
        help="Path to project JSON file",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output directory (default: output/assembly/)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only validate project, don't assemble",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    logger = logging.getLogger(__name__)
    
    print()
    print("=" * 60)
    print("AUTO Video Maker - Assembly Engine")
    print("=" * 60)
    print()
    
    # Check FFmpeg
    print("Checking FFmpeg...")
    if not check_ffmpeg_available():
        print("ERROR: FFmpeg not found.")
        print("Please install FFmpeg and ensure it's in your PATH.")
        print("Download: https://ffmpeg.org/download.html")
        return 1
    
    if not check_ffprobe_available():
        print("ERROR: FFprobe not found.")
        print("Please install FFmpeg (includes FFprobe) and ensure it's in your PATH.")
        return 1
    
    print("FFmpeg: OK")
    print()
    
    # Load project
    print(f"Loading project: {args.project}")
    try:
        project = load_project_from_json(args.project)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 1
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON: {e}")
        return 1
    except Exception as e:
        print(f"ERROR: Failed to load project: {e}")
        return 1
    
    print(f"Project ID: {project.project_id}")
    print(f"Title: {project.title}")
    print(f"Scenes: {len(project.scenes)}")
    print(f"Resolution: {project.resolution.width}x{project.resolution.height}")
    print(f"Aspect Ratio: {project.aspect_ratio}")
    print()
    
    if args.check_only:
        print("Validation mode - checking project only...")
        from app.assembly.validator import validate_project
        try:
            scene_infos, warnings = validate_project(project, args.project.parent)
            print(f"Validated {len(scene_infos)} scenes")
            for warning in warnings:
                print(f"WARNING: {warning}")
            print()
            print("Project validation: PASSED")
            return 0
        except Exception as e:
            print(f"Project validation: FAILED")
            print(f"Error: {e}")
            return 1
    
    # Assemble
    print("Starting assembly...")
    print()
    
    output_dir = args.output or ASSEMBLY_OUTPUT_DIR
    result = assemble_project(
        project,
        base_dir=args.project.parent,
        output_dir=output_dir,
    )
    
    print()
    print("=" * 60)
    
    if result.success:
        print("ASSEMBLY COMPLETE")
        print("=" * 60)
        print()
        print(f"Output: {result.output_path}")
        print(f"Duration: {result.duration:.2f}s")
        print(f"Resolution: {result.width}x{result.height}")
        print(f"Size: {result.file_size / (1024*1024):.2f} MB")
        
        if result.warnings:
            print()
            print("Warnings:")
            for warning in result.warnings:
                print(f"  - {warning}")
        
        print()
        return 0
    else:
        print("ASSEMBLY FAILED")
        print("=" * 60)
        print()
        for error in result.errors:
            print(f"Error: {error}")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
