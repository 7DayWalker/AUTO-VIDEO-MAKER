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

from app.logging_setup import configure_logging
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
    """Configure logging (delegates to the shared Batch 1I setup)."""
    ensure_orchestrator_directories()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = ORCHESTRATOR_LOGS_DIR / f"produce_{timestamp}.log"

    level = logging.DEBUG if verbose else logging.INFO

    # Batch 1I: shared, idempotent configuration. Observable CLI behavior is
    # preserved exactly: INFO by default (DEBUG with --verbose), the AVM text
    # format, this timestamped file destination, stdout, and third-party
    # noise suppression (urllib3/httpx held at WARNING centrally).
    configure_logging(level=level, log_file=log_file)


def load_persisted_visual_config(resume_project_id):
    """Load a resumable project's authoritative visual configuration.

    Returns ``None`` for new projects (or unreadable/missing manifests); on
    resume the returned dict mirrors what the pipeline itself restores into
    runtime config, so validation can key off what the project REQUIRES.
    """
    if not resume_project_id:
        return None
    manifest_path = ORCHESTRATOR_OUTPUT_DIR / resume_project_id / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        import json

        raw = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    scene_config = raw.get("scene_config") or {}
    return {
        "visual_mode": scene_config.get("visual_mode"),
        "default_provider": scene_config.get("visual_default_provider"),
        "routing": scene_config.get("scene_provider_routing") or {},
        "checkpoints": raw.get("scene_checkpoints") or {},
    }


def flow_url_required_error(args, persisted=None) -> "Optional[str]":
    """Return an error message when a Flow URL is required but missing.

    NEW PROJECT: preserves the pre-6E contract exactly — google_flow needs a
    URL; hybrid needs one only when a scene is explicitly routed to
    google_flow via --route-scene; anything not local/hybrid falls back to
    requiring it.

    RESUME: the persisted project configuration is authoritative (the same
    values the pipeline restores). A URL is demanded ONLY when the Flow
    execution path is actually needed, i.e. some google_flow-routed scene is
    not yet completed/validated. Zero-cost resumes therefore require nothing.
    """
    has_url = bool(getattr(args, "flow_project_url", ""))
    mode = getattr(args, "visual_mode", "google_flow")
    route_items = list(getattr(args, "route_scene", []) or [])

    def _routed_flow(item) -> bool:
        if isinstance(item, dict):
            return item.get("provider") == "google_flow"
        return item == "google_flow"

    if persisted:
        persisted_mode = persisted.get("visual_mode")
        if persisted_mode:
            mode = persisted_mode  # persisted authority over CLI default
        routing = persisted.get("routing") or {}
        checkpoints = persisted.get("checkpoints") or {}

        def _pending(scene_ids):
            pending = []
            for sid in scene_ids:
                status = (checkpoints.get(sid) or {}).get("status")
                if status not in {"completed", "validated"}:
                    pending.append(sid)
            return pending

        if mode == "google_flow":
            flow_scenes = sorted(routing) or ["all scenes"]
            pending = _pending(flow_scenes)
            if not routing and not any(checkpoints):
                pending = flow_scenes
            if pending and not has_url:
                return (
                    "--flow-project-url is required for this resumed "
                    "project: google_flow scenes are routed/pending."
                )
            return None
        if mode == "hybrid":
            flow_routed = sorted(
                sid for sid, value in routing.items() if _routed_flow(value)
            )
            pending = _pending(flow_routed)
            if pending and not has_url:
                return (
                    "--flow-project-url is required for this resumed "
                    f"project: pending google_flow scenes {', '.join(pending)}."
                )
            return None
        return None  # local / zero-cost persisted modes need nothing

    # New projects: exact historical behavior.
    hybrid_routes_to_flow = (
        mode == "hybrid"
        and any(
            "=" in item and item.split("=", 1)[1].strip().lower() == "google_flow"
            for item in route_items
        )
    )
    if not has_url and (
        mode == "google_flow"
        or (mode == "hybrid" and hybrid_routes_to_flow)
        or (mode not in {"local", "hybrid"})
    ):
        return "--flow-project-url is required for --topic and --resume"
    return None


def build_final_summary(manifest, project_dir) -> "list":
    """Truthful end-of-run summary (Phase 6E, fixing 6D findings).

    Identity always derives from the ACTUAL durable project directory, never
    from the content plan ID (which may legitimately differ from the outer
    project ID). Mock runs are labelled honestly instead of masquerading as
    production completions. Returns printable lines.
    """
    from collections import Counter

    scene_config = getattr(manifest, "scene_config", {}) or {}
    generation_kind = scene_config.get("generation_kind")
    durable_id = Path(project_dir).name
    mock_run = generation_kind == "placeholder"
    complete_result = manifest.get_stage_result(PipelineStage.COMPLETE)
    is_accepted = bool(
        complete_result and complete_result.status.value == "completed"
    )

    checkpoints = getattr(manifest, "scene_checkpoints", {}) or {}
    provider_counts = Counter(
        str(ckpt.get("provider"))
        for ckpt in checkpoints.values()
        if isinstance(ckpt, dict) and ckpt.get("provider")
    )

    if is_accepted and not mock_run:
        banner = "PROJECT COMPLETE"
    elif mock_run:
        banner = "MOCK RUN COMPLETE (placeholder output — not real production)"
    else:
        banner = "RUN FINISHED (project not fully accepted yet)"
    lines = [
        "",
        "=" * 60,
        banner,
        "=" * 60,
        "",
        f"Project ID: {durable_id}",
    ]
    if manifest.project_id != durable_id:
        lines.append(f"Content Plan ID: {manifest.project_id}")
    lines.extend([
        f"Project Directory: {project_dir}",
        f"Topic: {manifest.topic}",
    ])
    if manifest.final_video_path:
        lines.append(f"Final Video: {manifest.final_video_path}")
    lines.append(f"Visual Mode: {scene_config.get('visual_mode') or 'unknown'}")
    lines.append(
        f"Visual Source: {scene_config.get('visual_source') or 'not configured'}"
    )
    lines.append("")
    if provider_counts:
        lines.append("Providers Used:")
        if mock_run:
            lines.append(f"  mock: {sum(provider_counts.values())} (placeholders)")
        else:
            for provider_name in sorted(provider_counts):
                lines.append(f"  {provider_name}: {provider_counts[provider_name]}")
        lines.append("")
    lines.append(f"Google Flow Credits Used: {manifest.total_credits_used}")
    lines.append(
        "Acceptance Verdict: "
        + ("PROJECT_COMPLETE" if is_accepted else "NOT_ACCEPTED")
    )
    return lines


def print_stage_results(manifest, stream=None) -> None:
    """Print the stage summary using console-safe ASCII status labels."""
    stream = stream or sys.stdout
    print("Stage Results:", file=stream)
    for stage in PipelineStage.get_order():
        if stage == PipelineStage.COMPLETE:
            continue
        result = manifest.get_stage_result(stage)
        if result:
            status_label = (
                "[OK]" if result.status.value == "completed"
                else "[SKIP]" if result.status.value == "skipped"
                else "[FAIL]"
            )
            duration_str = (
                f" ({result.duration_seconds:.1f}s)"
                if result.duration_seconds else ""
            )
            print(
                f"  {status_label} {stage.value.upper()}: "
                f"{result.status.value}{duration_str}",
                file=stream,
            )


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
    parser.add_argument(
        "--asset",
        action="append",
        default=[],
        metavar="SCENE_ID=PATH",
        help=(
            "Explicit per-scene asset for visual_mode 'local'/'hybrid', e.g. "
            '--asset scene_001="C:\\assets\\garden.jpg". Repeatable '
            "(one per scene). The file is COPIED into the project-owned "
            "assets/source/ directory, fingerprinted (SHA-256) into "
            "assets/asset_manifest.json, and the project copy is then the "
            "only authoritative source for that scene (external originals "
            "may change or disappear safely afterwards)."
        ),
    )
    topic_group.add_argument(
        "--resume", "-r",
        type=str,
        metavar="PROJECT_ID",
        help="Resume an existing project by ID",
    )
    topic_group.add_argument(
        "--production",
        type=str,
        metavar="PRODUCTION_ID",
        help=(
            "Execute or continue a durable Phase 9 Production through the "
            "existing per-project engine. Re-run the same command to resume."
        ),
    )
    parser.add_argument(
        "--unit",
        type=str,
        metavar="UNIT_ID",
        help="Restrict --production execution to one declared unit",
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
        "--visual-default-provider",
        type=str,
        default="image_motion",
        choices=["image_motion", "local_asset", "openrouter_video"],
        help=(
            "Hybrid mode only: provider for scenes without an explicit "
            "routing override (default: image_motion). google_flow is never "
            "a valid default. openrouter_video uses the configured model and "
            "never falls back to paid access automatically."
        ),
    )
    scene_group.add_argument(
        "--route-scene",
        action="append",
        metavar="SCENE_ID=PROVIDER",
        default=[],
        help=(
            "Hybrid mode only: explicit per-scene provider override, e.g. "
            "--route-scene scene_003=google_flow or "
            "scene_003=openrouter_video. Repeatable. This is the ONLY way a "
            "scene is routed to Google Flow or OpenRouter."
        ),
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
    scene_group.add_argument(
        "--image-source",
        type=str,
        default="",
        help=(
            "Automatic visual source for missing still images (Phase 6F). "
            "Empty (default) = not configured: missing assets fail honestly "
            "with an actionable error. 'fixture' = deterministic local "
            "fixture for offline validation (never real AI imagery). Any "
            "other value maps to the dormant external adapter boundary, "
            "which never consumes paid quota or makes an external call."
        ),
    )
    
    scene_group.add_argument(
        "--external-video-source",
        type=str,
        default="",
        help=(
            "Origin identifier recorded as provenance for externally "
            "obtained video clips routed to the external_video provider "
            "(Phase 7B), e.g. 'google_flow_private'. Provenance metadata "
            "only - never a routable provider identity. Defaults to "
            "AVM_EXTERNAL_VIDEO_SOURCE or a truthful generic value."
        ),
    )
    scene_group.add_argument(
        "--openrouter-model",
        type=str,
        default=None,
        help=(
            "OpenRouter video model ID. If omitted, the current compatible "
            "free model is selected deterministically."
        ),
    )
    scene_group.add_argument(
        "--openrouter-resolution",
        type=str,
        default=None,
        help=(
            "OpenRouter video resolution (for example 720p). It is checked "
            "against the live model capability catalog before submission."
        ),
    )
    scene_group.add_argument(
        "--openrouter-allow-paid",
        action="store_true",
        help=(
            "Explicitly authorize paid OpenRouter video generation for the "
            "selected model. Default is disabled."
        ),
    )
    scene_group.add_argument(
        "--openrouter-allow-paid-fallback",
        action="store_true",
        help=(
            "Persist the paid-fallback opt-in marker. Phase 7E still does not "
            "automatically switch models or submit paid fallback jobs."
        ),
    )
    scene_group.add_argument(
        "--openrouter-generate-audio",
        action="store_true",
        help=(
            "Request native audio from OpenRouter only when the selected model "
            "advertises generate_audio support."
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
    assembly_group.add_argument(
        "--audio-policy",
        type=str,
        default="narration_only",
        choices=["narration_only", "native_only", "native_plus_narration", "none"],
        help=(
            "Explicit final-audio policy: narration_only (legacy default), "
            "native_only, native_plus_narration, or none (no audio stream). "
            "Persisted per project. native_only NEVER falls back to narrator "
            "audio: with no native audio in the real scene media the output is "
            "silence. Productions created through the intent layer compile this "
            "from their declared audio_intent instead."
        ),
    )
    assembly_group.add_argument(
        "--caption-preset",
        type=str,
        default="",
        choices=["minimal", "standard", "bold", "viral"],
        help=(
            "Caption presentation preset (Phase 6F): minimal | standard | "
            "bold | viral. Defaults to 'standard' (or AVM_CAPTION_PRESET). "
            "Persisted per project so resume reproduces the same look against "
            "an explicit vertical play resolution."
        ),
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
    
    # --flow-project-url is required only when Google Flow execution is truly
    # possible (Phase 6E): new projects keep the historical contract; resumed
    # projects are validated against their PERSISTED configuration, which is
    # authoritative over CLI defaults.
    # Production-backed execution validates Flow only after the unit plan
    # exists and exact scene routing is known. Legacy topic/resume validation
    # remains byte-for-byte on this existing helper path.
    flow_error = None if args.production else flow_url_required_error(
        args, persisted=load_persisted_visual_config(args.resume)
    )
    if flow_error:
        parser.error(flow_error)

    if not args.flow_project_url:
        args.flow_project_url = ""  # unused: no scene is routed to Google Flow
    
    print()
    print("=" * 60)
    print("AUTO Video Maker - Production Orchestrator")
    print("=" * 60)
    print()
    
    # Build configuration
    try:
        config_kwargs = dict(
            topic=args.topic or "Production Unit",  # Replaced by bound brief
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
            openrouter_model=args.openrouter_model,
            openrouter_resolution=args.openrouter_resolution,
            openrouter_allow_paid=args.openrouter_allow_paid,
            openrouter_allow_paid_fallback=args.openrouter_allow_paid_fallback,
            openrouter_generate_audio=args.openrouter_generate_audio,
            visual_mode=args.visual_mode,
            visual_default_provider=args.visual_default_provider,
            # Phase 6C: explicit per-scene assets. Parsed leniently here;
            # malformed entries are validated truthfully downstream
            # (windows-safe split on the FIRST '=' only, so drive letters
            # like C:\... survive intact).
            asset_mappings={
                parts[0].strip(): parts[1]
                for parts in (
                    item.split("=", 1) for item in args.asset if "=" in item
                )
                if len(parts) == 2 and parts[0].strip() and parts[1].strip()
            },
            visual_scene_routing={
                parts[0].strip(): parts[1].strip()
                for parts in (
                    item.split("=", 1) for item in args.route_scene if "=" in item
                )
                if len(parts) == 2 and parts[0].strip() and parts[1].strip()
            },
            enable_captions=not args.no_captions,
            music_volume=args.music_volume,
            audio_policy=args.audio_policy,
            mock_mode=args.mock,
            dry_run=args.dry_run,
            ui_verify_only=args.ui_verify,
            voice_only=args.voice_only,
            skip_confirmation=args.no_confirm,
            resume_project_id=args.resume,
        )
        # Phase 6F: apply the optional automatic visual source / caption
        # preset only when actually supplied on the CLI, so the repository's
        # env-driven defaults (AVM_IMAGE_SOURCE / AVM_CAPTION_PRESET) still
        # take effect when the flags are omitted.
        if args.caption_preset:
            config_kwargs["caption_preset"] = args.caption_preset
        if args.image_source:
            config_kwargs["image_source"] = args.image_source
        # Phase 7B: optional origin provenance for external_video scenes.
        if args.external_video_source:
            config_kwargs["external_video_source"] = args.external_video_source
        config = OrchestratorConfig(**config_kwargs)
    except Exception as e:
        print(f"ERROR: Invalid configuration: {e}")
        return 1

    if args.unit and not args.production:
        parser.error("--unit requires --production")

    # Phase 6C: validate explicit asset mappings truthfully BEFORE any work.
    # Paths are NOT split on later '=' characters (Windows drive letters and
    # paths containing '=' survive intact).
    for entry in args.asset:
        if (
            "=" not in entry
            or not entry.split("=", 1)[0].strip()
            or not entry.split("=", 1)[1].strip()
        ):
            print(
                f"ERROR: Malformed --asset entry {entry!r}. Expected "
                'SCENE_ID=PATH, e.g. --asset scene_001="C:\\assets\\garden.jpg"'
            )
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
    
    # Phase 10 production composition. The executor invokes the same
    # ProductionOrchestrator once per eligible unit; re-invocation resumes from
    # durable bindings/manifests and never mutates Production intent.
    if args.production:
        try:
            from app.orchestrator.production_execution import ProductionExecutor

            executor = ProductionExecutor(args.production, base_config=config)
            initial = executor.derive_progress()
            print(f"Production: {initial.production_id} (v{initial.plan_version})")
            for item in initial.units:
                suffix = f" - {item.reason}" if item.reason else ""
                print(f"  {item.unit_id}: {item.state.value}{suffix}")
            print()
            final = executor.execute(unit_id=args.unit)
            print("Production progress:")
            for item in final.units:
                project = f" [{item.project_id}]" if item.project_id else ""
                print(f"  {item.unit_id}: {item.state.value}{project}")
            if args.unit:
                selected = next(
                    (item for item in final.units if item.unit_id == args.unit),
                    None,
                )
                return 0 if final.complete or (
                    selected is not None and selected.state.value == "COMPLETED"
                ) else 1
            return 0 if final.complete else 1
        except Exception as e:
            logger.exception("Production execution failed: %s", e)
            print(f"PRODUCTION EXECUTION ERROR: {e}")
            return 1

    # Run legacy single-project orchestrator
    try:
        orchestrator = ProductionOrchestrator(config)
        manifest = orchestrator.run()
        
        # Print truthful summary (Phase 6E): identity comes from the real
        # project directory, provider usage from real checkpoints, verdicts
        # from actual acceptance state.
        for summary_line in build_final_summary(
            manifest, orchestrator.project_dir
        ):
            print(summary_line)
        print()

        # Stage summary
        print_stage_results(manifest)
        
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
        print("  python produce.py --resume <project_id>")
        if config.flow_project_url:
            print(
                "  (add --flow-project-url "
                f"{config.flow_project_url} only for Flow-routed scenes)"
            )
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
