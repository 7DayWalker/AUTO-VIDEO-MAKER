"""
AUTO Video Maker - Production Pipeline Orchestrator

Phase 2E: End-to-end video production pipeline.
Coordinates Content → Voice → Scenes → Captions → Assembly.

Features:
- Persistent manifest/state management
- Idempotent stage execution
- Resume capability
- Strict validation at each stage
"""

import json
import logging
import shutil
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from app.config import PROJECT_ROOT
from app.orchestrator.models import (
    ProjectManifest,
    PipelineStage,
    StageStatus,
    StageResult,
    OrchestratorConfig,
)
from app.orchestrator.captions import generate_srt_from_timing, validate_srt_file
from app.voice.validator import validate_audio_file
from app.orchestrator.exceptions import (
    OrchestratorError,
    StageFailedError,
    ResumeError,
    ValidationError,
    DependencyError,
)

logger = logging.getLogger(__name__)

MAX_GENERATION_ATTEMPTS = 3


# Output directories
ORCHESTRATOR_OUTPUT_DIR = PROJECT_ROOT / "output" / "projects"
ORCHESTRATOR_LOGS_DIR = PROJECT_ROOT / "logs" / "orchestrator"


def ensure_orchestrator_directories() -> None:
    """Create required directories."""
    for directory in [ORCHESTRATOR_OUTPUT_DIR, ORCHESTRATOR_LOGS_DIR]:
        directory.mkdir(parents=True, exist_ok=True)


class ProductionOrchestrator:
    """
    End-to-end video production orchestrator.
    
    Coordinates all phases:
    1. Content: Generate production plan from topic
    2. Voice: Generate voiceover from narration
    3. Scenes: Generate video scenes from visual prompts
    4. Captions: Generate SRT from voice timing
    5. Assembly: Combine all assets into final video
    """
    
    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.manifest: Optional[ProjectManifest] = None
        self.project_dir: Optional[Path] = None
        
        # Ensure directories exist
        ensure_orchestrator_directories()
    
    def run(self) -> ProjectManifest:
        """
        Run the complete production pipeline.
        
        Returns:
            ProjectManifest with final state and artifact paths.
        
        Raises:
            StageFailedError: If any stage fails.
            OrchestratorError: For other errors.
        """
        logger.info("=" * 60)
        logger.info("AUTO Video Maker - Production Orchestrator")
        logger.info("=" * 60)
        
        # Initialize or resume project
        if self.config.resume_project_id:
            self._resume_project(self.config.resume_project_id)
        else:
            self._initialize_project()
        
        logger.info(f"Project ID: {self.manifest.project_id}")
        logger.info(f"Topic: {self.manifest.topic}")
        logger.info(f"Dry Run: {self.config.dry_run}")
        logger.info(f"Mock Mode: {self.config.mock_mode}")
        
        # Save initial manifest
        self._save_manifest()
        
        # Execute pipeline stages
        try:
            # Stage 1: Content
            if not self.manifest.is_stage_completed(PipelineStage.CONTENT):
                self._execute_content_stage()
            else:
                logger.info("Stage CONTENT: Already completed, skipping")
            
            # Stage 2: Voice
            if not self.manifest.is_stage_completed(PipelineStage.VOICE):
                self._execute_voice_stage()
            else:
                logger.info("Stage VOICE: Already completed, skipping")
            
            # Voice-only mode is terminal for this run. It deliberately leaves
            # scenes pending so no Flow connection, Create click, or credit use
            # can occur during provider validation/resume.
            if self.config.voice_only:
                logger.info("Voice-only mode complete; downstream stages not run")
                return self.manifest

            # Stage 3: Scenes
            if not self.manifest.is_stage_completed(PipelineStage.SCENES):
                self._execute_scenes_stage()
            else:
                logger.info("Stage SCENES: Already completed, skipping")

            # UI verification is intentionally terminal. It must exercise the
            # real Flow page but must not continue into captions/assembly with
            # no generated scene files, and it must leave SCENES resumable.
            if self.config.ui_verify_only:
                logger.info("UI verification complete; downstream stages not run")
                return self.manifest
            
            # Stage 4: Captions
            if not self.manifest.is_stage_completed(PipelineStage.CAPTIONS):
                self._execute_captions_stage()
            else:
                logger.info("Stage CAPTIONS: Already completed, skipping")
            
            # Stage 5: Assembly
            if not self.manifest.is_stage_completed(PipelineStage.ASSEMBLY):
                self._execute_assembly_stage()
            else:
                logger.info("Stage ASSEMBLY: Already completed, skipping")
            
            # Final acceptance gate: the completion sentinel is only set when
            # the final artifact physically validates right now.
            self._final_acceptance_gate()
            
            # Mark complete
            logger.info("=" * 60)
            logger.info("PIPELINE COMPLETE")
            logger.info("=" * 60)
            logger.info(f"Final video: {self.manifest.final_video_path}")
            
            return self.manifest
            
        except StageFailedError:
            # Re-raise stage failures
            raise
        except Exception as e:
            logger.exception(f"Pipeline error: {e}")
            raise OrchestratorError(f"Pipeline failed: {e}")
    
    def _final_acceptance_gate(self) -> None:
        """Validate the final artifact before setting the completion sentinel."""
        from app.orchestrator.reconciliation import (
            ArtifactStatus,
            ProjectVerdict,
            _assembly_evidence,
        )
        
        evidence = _assembly_evidence(self.manifest, self.project_dir)
        if evidence.get("status") != ArtifactStatus.VALID.value:
            validation = evidence.get("validation", {})
            error = (
                validation.get("error")
                or "; ".join(validation.get("errors", []))
                or evidence.get("status")
            )
            raise StageFailedError("assembly", f"Final artifact acceptance failed: {error}")
        
        validation = evidence.get("validation", {})
        self.manifest.set_stage_result(StageResult.completed(
            stage=PipelineStage.COMPLETE,
            output_path=evidence.get("path"),
            output_files=[evidence.get("path")],
            metadata={
                "duration": validation.get("duration"),
                "width": validation.get("width"),
                "height": validation.get("height"),
                "file_size": validation.get("file_size"),
                "video_codec": validation.get("video_codec"),
                "audio_codec": validation.get("audio_codec"),
                "ffprobe": "passed",
            },
        ))
        self._save_manifest()
        logger.info(f"Acceptance verdict: {ProjectVerdict.PROJECT_COMPLETE.value}")
    
    def _initialize_project(self) -> None:
        """Initialize a new project."""
        from app.content.models import generate_project_id
        
        project_id = generate_project_id(self.config.topic)
        
        self.manifest = ProjectManifest(
            project_id=project_id,
            topic=self.config.topic,
            flow_project_url=self.config.flow_project_url,
            content_config=self.config.to_content_brief_dict(),
            voice_config=self.config.to_voice_config_dict(),
            scene_config=self.config.to_scene_config_dict(),
            assembly_config=self.config.to_assembly_config_dict(),
            dry_run=self.config.dry_run,
            mock_mode=self.config.mock_mode,
        )
        self.manifest.initialize_stages()
        
        # Create project directory
        self.project_dir = ORCHESTRATOR_OUTPUT_DIR / project_id
        self.project_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Initialized new project: {project_id}")
    
    def _resume_project(self, project_id: str) -> None:
        """Resume an existing project."""
        self.project_dir = ORCHESTRATOR_OUTPUT_DIR / project_id
        manifest_path = self.project_dir / "manifest.json"
        
        if not manifest_path.exists():
            raise ResumeError(
                f"Cannot resume: manifest not found at {manifest_path}",
                manifest_path=str(manifest_path),
            )
        
        # Load manifest
        # PowerShell's UTF-8 writer may leave a BOM in a preserved manifest;
        # accept it while keeping all subsequent writes BOM-free.
        with open(manifest_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        
        self.manifest = ProjectManifest.from_dict(data)
        
        # Evidence-based reconciliation before deciding resumability. A
        # manifest that claims completion but fails physical validation must
        # become resumable instead of being rejected as already complete.
        from app.orchestrator.reconciliation import reconcile_project, apply_reconciliation
        
        report = reconcile_project(self.manifest.project_id, self.project_dir)
        logger.info(f"Reconciliation verdict: {report.verdict.value}")
        for reason in report.reasons:
            logger.warning(f"Reconciliation finding: {reason}")
        apply_reconciliation(self.manifest, report)
        
        if not self.manifest.can_resume():
            raise ResumeError(
                f"Cannot resume project {project_id}: pipeline is already complete"
            )
        
        # Reset any stages that were left in "running" state (from crash/interrupt)
        self.manifest.reset_running_stages()
        
        # Update config from manifest if needed
        if self.manifest.flow_project_url:
            self.config.flow_project_url = self.manifest.flow_project_url
        
        logger.info(f"Resumed project: {project_id}")
        logger.info(f"Current stage: {self.manifest.current_stage.value}")
        logger.info(f"Completed stages: {[s.value for s in self.manifest.get_completed_stages()]}")
    
    def _save_manifest(self) -> None:
        """Save manifest to disk."""
        manifest_path = self.project_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(self.manifest.to_dict(), f, indent=2, ensure_ascii=False)
        logger.debug(f"Saved manifest: {manifest_path}")
    
    def _execute_content_stage(self) -> None:
        """Execute content generation stage."""
        logger.info("")
        logger.info("=" * 40)
        logger.info("Stage 1: CONTENT GENERATION")
        logger.info("=" * 40)
        
        # Mark stage as running
        started_at = datetime.now().isoformat()
        self.manifest.set_stage_result(StageResult.running(PipelineStage.CONTENT))
        self._save_manifest()
        
        try:
            from app.content.models import ContentBrief
            from app.content.planner import ScenePlanner
            from app.content.providers.mock import MockLLMProvider
            from app.content.providers import get_default_provider
            from app.content.validator import validate_production_plan
            
            # Create content brief
            brief = ContentBrief(**self.config.to_content_brief_dict())
            logger.info(f"Topic: {brief.topic}")
            logger.info(f"Duration: {brief.target_duration}s")
            logger.info(f"Scenes: {brief.number_of_scenes}")
            
            # Select provider
            if self.config.mock_mode:
                provider = MockLLMProvider()
                logger.info("Provider: MOCK")
            else:
                provider = get_default_provider()
                if not provider.is_available():
                    raise DependencyError(
                        provider.name.upper(),
                        f"{provider.name.upper()} API key not configured. Use --mock or set the API key."
                    )
                logger.info(f"Provider: {provider.name} ({provider.model})")
            
            # Generate production plan
            content_output_dir = self.project_dir / "content"
            planner = ScenePlanner(provider=provider, output_dir=content_output_dir)
            
            result = planner.plan_and_save(brief)
            
            if not result.success or not result.plan:
                raise StageFailedError(
                    "content",
                    result.error or "Content generation failed",
                    {"provider": result.provider, "model": result.model},
                )
            
            # Validate production plan
            validation = validate_production_plan(result.plan, brief)
            if not validation.valid:
                raise ValidationError(
                    "content",
                    f"Production plan validation failed: {validation.errors}",
                )
            
            # Update manifest
            plan_path = content_output_dir / result.plan.project_id / "production_plan.json"
            self.manifest.content_plan_path = str(plan_path)
            
            # Update project_id to match content plan
            self.manifest.project_id = result.plan.project_id
            
            # Mark stage complete
            self.manifest.set_stage_result(StageResult.completed(
                stage=PipelineStage.CONTENT,
                output_path=str(plan_path),
                output_files=[str(plan_path)],
                warnings=result.warnings + validation.warnings,
                metadata={
                    "provider": result.provider,
                    "model": result.model,
                    "scene_count": len(result.plan.scenes),
                    "estimated_duration": result.plan.estimated_duration,
                },
                started_at=started_at,
            ))
            self._save_manifest()
            
            logger.info(f"Content generation complete: {len(result.plan.scenes)} scenes")
            logger.info(f"Output: {plan_path}")
            
        except StageFailedError:
            raise
        except ValidationError:
            raise
        except DependencyError:
            raise
        except Exception as e:
            self.manifest.set_stage_result(StageResult.failed(
                stage=PipelineStage.CONTENT,
                error=str(e),
                started_at=started_at,
            ))
            self._save_manifest()
            raise StageFailedError("content", str(e))
    
    def _execute_voice_stage(self) -> None:
        """Execute voice generation stage."""
        logger.info("")
        logger.info("=" * 40)
        logger.info("Stage 2: VOICE GENERATION")
        logger.info("=" * 40)
        
        # Verify content stage completed
        if not self.manifest.content_plan_path:
            raise StageFailedError("voice", "Content plan not available")
        
        started_at = datetime.now().isoformat()
        self.manifest.set_stage_result(StageResult.running(PipelineStage.VOICE))
        self._save_manifest()
        
        try:
            from app.content.models import ProductionPlan
            from app.voice.generator import VoiceGenerator
            from app.voice.providers.mock import MockVoiceProvider
            from app.voice.providers.edge_tts import EdgeTTSProvider
            
            # Load production plan
            plan_path = Path(self.manifest.content_plan_path)
            with open(plan_path, "r", encoding="utf-8") as f:
                plan_data = json.load(f)
            plan = ProductionPlan(**plan_data)
            
            logger.info(f"Loaded plan: {plan.project_id}")
            logger.info(f"Scenes: {len(plan.scenes)}")
            
            # Select provider
            if self.config.mock_mode:
                provider = MockVoiceProvider()
                logger.info("Provider: MOCK")
            else:
                provider = EdgeTTSProvider()
                if not provider.is_available():
                    raise DependencyError(
                        "edge-tts",
                        "edge-tts not installed. Use --mock or: pip install edge-tts"
                    )
                logger.info("Provider: Edge TTS")
            
            # Generate voiceover
            voice_output_dir = self.project_dir / "voice"
            generator = VoiceGenerator(provider=provider, output_dir=voice_output_dir)
            
            voice_config = self.config.to_voice_config_dict()
            result = generator.generate_from_plan(
                plan=plan,
                voice=voice_config.get("voice"),
                rate=voice_config.get("rate"),
                pitch=voice_config.get("pitch"),
                volume=voice_config.get("volume"),
            )
            
            if not result.success:
                raise StageFailedError(
                    "voice",
                    result.error or "Voice generation failed",
                    {"provider": result.provider},
                )
            
            # Validate output strictly through FFprobe. This requires a
            # non-empty file, a readable audio stream, and positive duration.
            output_validation = validate_audio_file(Path(result.output_path))
            if not output_validation.valid:
                raise ValidationError(
                    "voice",
                    f"Audio validation failed: {'; '.join(output_validation.errors)}",
                    path=str(result.output_path),
                )
            if not output_validation.duration or output_validation.duration <= 0:
                raise ValidationError(
                    "voice",
                    "Audio validation failed: duration must be greater than zero",
                    path=str(result.output_path),
                )

            # Timing is required downstream and must correspond to the plan.
            timing_path = voice_output_dir / plan.project_id / "voice_timing.json"
            if not timing_path.exists() or timing_path.stat().st_size == 0:
                raise ValidationError(
                    "voice",
                    f"Voice timing file missing or empty: {timing_path}",
                    path=str(timing_path),
                )
            try:
                with open(timing_path, "r", encoding="utf-8") as timing_file:
                    timing_data = json.load(timing_file)
                timing_scenes = timing_data.get("scenes", [])
                if (
                    timing_data.get("project_id") != plan.project_id
                    or len(timing_scenes) != len(plan.scenes)
                    or timing_data.get("voiceover_duration", 0) <= 0
                    or abs(
                        float(timing_data["voiceover_duration"])
                        - float(output_validation.duration)
                    ) > 0.1
                ):
                    raise ValueError("timing metadata does not match validated audio/plan")
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValidationError(
                    "voice",
                    f"Voice timing validation failed: {exc}",
                    path=str(timing_path),
                ) from exc
            
            # Update manifest
            self.manifest.voice_audio_path = str(result.output_path)
            
            # Find timing file
            timing_path = voice_output_dir / plan.project_id / "voice_timing.json"
            if timing_path.exists():
                self.manifest.voice_timing_path = str(timing_path)
            
            # Mark stage complete
            output_files = [str(result.output_path)]
            if self.manifest.voice_timing_path:
                output_files.append(self.manifest.voice_timing_path)
            
            self.manifest.set_stage_result(StageResult.completed(
                stage=PipelineStage.VOICE,
                output_path=str(result.output_path),
                output_files=output_files,
                warnings=result.warnings,
                metadata={
                    "provider": result.provider,
                    "voice": result.voice,
                    "duration": result.duration,
                    "file_size": result.file_size,
                },
                started_at=started_at,
            ))
            self._save_manifest()
            
            logger.info(f"Voice generation complete: {result.duration:.2f}s")
            logger.info(f"Output: {result.output_path}")
            
        except StageFailedError:
            raise
        except ValidationError as e:
            self.manifest.set_stage_result(StageResult.failed(
                stage=PipelineStage.VOICE,
                error=str(e),
                started_at=started_at,
            ))
            self._save_manifest()
            raise StageFailedError("voice", str(e), {"path": e.path} if e.path else {})
        except DependencyError:
            raise
        except Exception as e:
            self.manifest.set_stage_result(StageResult.failed(
                stage=PipelineStage.VOICE,
                error=str(e),
                started_at=started_at,
            ))
            self._save_manifest()
            raise StageFailedError("voice", str(e))
    
    def _execute_scenes_stage(self) -> None:
        """Execute scene generation stage."""
        logger.info("")
        logger.info("=" * 40)
        logger.info("Stage 3: SCENE GENERATION")
        logger.info("=" * 40)
        
        # Verify content stage completed
        if not self.manifest.content_plan_path:
            raise StageFailedError("scenes", "Content plan not available")
        
        started_at = datetime.now().isoformat()
        self.manifest.set_stage_result(StageResult.running(PipelineStage.SCENES))
        self._save_manifest()
        
        try:
            from app.content.models import ProductionPlan
            from app.flow.scene_generator import (
                FlowSceneGenerator,
                FlowConnectionError,
                FlowAuthenticationError,
                FlowCreditError,
                validate_video_file,
                load_manifest as load_scene_manifest,
                save_manifest as save_scene_manifest,
            )
            from app.flow.scene_models import (
                FlowGenerationConfig,
                SceneGenerationRequest,
                SceneGenerationResult,
                SceneManifest,
            )
            
            # Load production plan
            plan_path = Path(self.manifest.content_plan_path)
            with open(plan_path, "r", encoding="utf-8") as f:
                plan_data = json.load(f)
            plan = ProductionPlan(**plan_data)
            
            logger.info(f"Loaded plan: {plan.project_id}")
            logger.info(f"Scenes to generate: {len(plan.scenes)}")
            
            # Check for dry run
            if self.config.dry_run:
                logger.info("DRY RUN: Skipping actual scene generation")
                
                # Create mock scene files for testing
                scenes_output_dir = self.project_dir / "scenes" / plan.project_id
                scenes_output_dir.mkdir(parents=True, exist_ok=True)
                
                scene_paths = []
                for scene in plan.scenes:
                    # In dry run, we don't create actual files
                    scene_path = scenes_output_dir / f"{scene.id}.mp4"
                    scene_paths.append(str(scene_path))
                
                self.manifest.scene_video_paths = scene_paths
                self.manifest.scene_manifest_path = str(scenes_output_dir / "scene_manifest.json")
                
                self.manifest.set_stage_result(StageResult.completed(
                    stage=PipelineStage.SCENES,
                    output_path=str(scenes_output_dir),
                    output_files=scene_paths,
                    metadata={"dry_run": True, "scene_count": len(plan.scenes)},
                    started_at=started_at,
                ))
                self._save_manifest()
                
                logger.info("DRY RUN: Scene stage marked complete (no files generated)")
                return
            
            # Check for mock mode
            if self.config.mock_mode:
                logger.info("MOCK MODE: Creating placeholder scene files")
                
                scenes_output_dir = self.project_dir / "scenes" / plan.project_id
                scenes_output_dir.mkdir(parents=True, exist_ok=True)
                
                scene_paths = []
                for scene in plan.scenes:
                    scene_path = scenes_output_dir / f"{scene.id}.mp4"
                    # Create a minimal valid MP4 placeholder
                    self._create_mock_video(scene_path, scene.estimated_duration)
                    scene_paths.append(str(scene_path))
                
                self.manifest.scene_video_paths = scene_paths
                self.manifest.scene_manifest_path = str(scenes_output_dir / "scene_manifest.json")
                
                # Create scene manifest
                scene_manifest = SceneManifest(project_id=plan.project_id)
                for scene in plan.scenes:
                    scene_manifest.add_scene(
                        scene_id=scene.id,
                        sequence=scene.sequence,
                        file=f"{scene.id}.mp4",
                        status="completed",
                    )
                save_scene_manifest(scene_manifest, scenes_output_dir)
                
                self.manifest.set_stage_result(StageResult.completed(
                    stage=PipelineStage.SCENES,
                    output_path=str(scenes_output_dir),
                    output_files=scene_paths,
                    metadata={"mock_mode": True, "scene_count": len(plan.scenes)},
                    started_at=started_at,
                ))
                self._save_manifest()
                
                logger.info(f"MOCK MODE: Created {len(scene_paths)} placeholder scenes")
                return
            
            # Real scene generation via Google Flow
            logger.info("Connecting to Chrome for Flow generation...")
            
            scene_config = self.config.to_scene_config_dict()
            config = FlowGenerationConfig(
                model=scene_config.get("model"),
                aspect_ratio=scene_config.get("aspect_ratio"),
                duration=scene_config.get("duration"),
                count=1,
                agent_enabled=False,
            )
            
            scenes_output_dir = self.project_dir / "scenes"
            generator = FlowSceneGenerator(config=config, output_dir=scenes_output_dir)
            
            # Connect to Chrome
            try:
                generator.connect(expected_url=self.config.flow_project_url)
                logger.info("Connected to Chrome via CDP")
            except FlowConnectionError as e:
                raise StageFailedError(
                    "scenes",
                    str(e),
                    {"hint": "Run: python launch_chrome.py"},
                )
            
            try:
                # Navigate to Flow project
                logger.info(f"Navigating to Flow project: {self.config.flow_project_url}")
                try:
                    generator.navigate_to_project(self.config.flow_project_url)
                    logger.info("Flow project loaded")
                except FlowAuthenticationError as e:
                    raise StageFailedError(
                        "scenes",
                        str(e),
                        {"hint": "Log in to Google in the Chrome window"},
                    )

                if self.config.ui_verify_only:
                    first_scene = sorted(plan.scenes, key=lambda s: s.sequence)[0]
                    request = SceneGenerationRequest(
                        project_id=plan.project_id,
                        scene_id=first_scene.id,
                        sequence=first_scene.sequence,
                        visual_prompt=first_scene.visual_prompt,
                        config=config,
                        flow_project_url=self.config.flow_project_url,
                    )
                    verification = generator.verify_ui_only(request)
                    if not verification.success:
                        raise StageFailedError(
                            "scenes",
                            f"UI verification failed for scene {first_scene.id}: {verification.error}",
                            {
                                "scene_id": first_scene.id,
                                "status": verification.status,
                                "verification": verification.verification,
                            },
                        )

                    # Keep the scene stage explicitly resumable. A completed
                    # result here would cause a later real acceptance run to
                    # skip generation, which is unsafe.
                    self.manifest.set_stage_result(StageResult(
                        stage=PipelineStage.SCENES,
                        status=StageStatus.SKIPPED,
                        output_path=str(self.project_dir / "ui_verification"),
                        warnings=["UI verification only; no scene generation performed"],
                        metadata=verification.verification,
                        started_at=started_at,
                        completed_at=datetime.now().isoformat(),
                    ))
                    self._save_manifest()
                    logger.info("UI verification passed; Create was not clicked")
                    return
                
                # Load or create scene manifest
                project_scenes_dir = scenes_output_dir / plan.project_id
                scene_manifest = load_scene_manifest(project_scenes_dir, plan.project_id)
                
                # Generate each scene
                scene_paths = []
                stage_credits = 0

                def account_checkpoint_credit(scene_id: str, attempt_number: int = None) -> None:
                    """Account a submitted Flow attempt exactly once.

                    The cost is recorded at the durable post-Create
                    checkpoint, not only after a successful download. This
                    preserves billing evidence when Flow fails before an
                    asset can be downloaded.
                    """
                    nonlocal stage_credits
                    current = self.manifest.scene_checkpoints.get(scene_id, {})
                    attempts = current.setdefault("attempts", [])
                    if attempt_number is None:
                        attempt_number = current.get("attempt_number")
                    attempt = next(
                        (item for item in attempts if item.get("attempt_number") == attempt_number),
                        current,
                    )
                    evidence = attempt.get("checkpoint_evidence", {}) or {}
                    submission = evidence.get("submission", evidence) or {}
                    if (
                        submission.get("state") == "SUBMITTED"
                        and attempt.get("generation_cost")
                        and not attempt.get("credits_accounted")
                    ):
                        cost = int(attempt["generation_cost"])
                        attempt["credits_accounted"] = True
                        attempt["credits_accounted_at"] = datetime.now().isoformat()
                        if attempt is current:
                            current["credits_accounted"] = True
                            current["credits_accounted_at"] = attempt["credits_accounted_at"]
                        self.manifest.scene_checkpoints[scene_id] = current
                        self.manifest.total_credits_used += cost
                        stage_credits += cost
                        logger.info(
                            "Accounted %s Flow credits for submitted scene %s",
                            cost,
                            scene_id,
                        )
                
                for i, scene in enumerate(sorted(plan.scenes, key=lambda s: s.sequence)):
                    logger.info(f"")
                    logger.info(f"Generating scene {i+1}/{len(plan.scenes)}: {scene.id}")
                    logger.info(f"Prompt: {scene.visual_prompt[:100]}...")
                    
                    # Check if already completed
                    existing = scene_manifest.get_scene(scene.id)
                    if existing and existing.status in {"completed", "validated"}:
                        existing_path = project_scenes_dir / existing.file
                        existing_validation = validate_video_file(
                            existing_path, config.aspect_ratio
                        ) if existing_path.exists() else {"valid": False}
                        if existing_validation.get("valid"):
                            logger.info(f"Scene already exists, skipping: {existing_path}")
                            scene_paths.append(str(existing_path))
                            continue
                    
                    # Create request
                    request = SceneGenerationRequest(
                        project_id=plan.project_id,
                        scene_id=scene.id,
                        sequence=scene.sequence,
                        visual_prompt=scene.visual_prompt,
                        config=config,
                        flow_project_url=self.config.flow_project_url,
                    )

                    def prepare_attempt(attempt_number: int) -> None:
                        """Create a durable attempt record before a new Create click."""
                        current = self.manifest.scene_checkpoints.get(scene.id, {})
                        attempts = current.setdefault("attempts", [])
                        if not attempts:
                            # Preserve the already executed attempt from the
                            # pre-retry manifest without charging it again.
                            prior = {
                                key: current.get(key)
                                for key in (
                                    "scene_id", "sequence", "flow_project_url", "prompt_hash",
                                    "prompt", "generation_cost", "status", "submission_timestamp",
                                    "generation_started_at", "generation_completed_at", "downloaded_at",
                                    "checkpoint_evidence", "file_path", "validation",
                                    "credits_accounted", "credits_accounted_at",
                                )
                                if key in current
                            }
                            prior["attempt_number"] = 1
                            attempts.append(prior)
                        if not any(item.get("attempt_number") == attempt_number for item in attempts):
                            attempts.append({
                                "attempt_number": attempt_number,
                                "status": "pending",
                                "prompt_hash": hashlib.sha256(
                                    scene.visual_prompt.encode("utf-8")
                                ).hexdigest(),
                                "prompt": scene.visual_prompt,
                                "settings": {
                                    "model": config.model,
                                    "aspect_ratio": config.aspect_ratio,
                                    "duration": config.duration,
                                    "count": config.count,
                                    "agent_enabled": config.agent_enabled,
                                },
                                "started_at": datetime.now().isoformat(),
                            })
                        current["attempt_number"] = attempt_number
                        current["attempts"] = attempts
                        current["updated_at"] = datetime.now().isoformat()
                        self.manifest.scene_checkpoints[scene.id] = current
                        self._save_manifest()
                    
                    def persist_scene_checkpoint(update: Dict[str, Any]) -> None:
                        """Persist every Flow state transition before continuing."""
                        scene_id = update.get("scene_id", scene.id)
                        current = self.manifest.scene_checkpoints.get(scene_id, {})
                        current.update(update)
                        current["updated_at"] = datetime.now().isoformat()
                        self.manifest.scene_checkpoints[scene_id] = current
                        account_checkpoint_credit(scene_id)

                        checkpoint_status = current.get("status", "pending")
                        scene_manifest.add_scene(
                            scene_id=scene.id,
                            sequence=scene.sequence,
                            file=f"{scene.id}.mp4",
                            status=checkpoint_status,
                            submission_timestamp=current.get("submission_timestamp"),
                            generation_started_at=current.get("generation_started_at"),
                            generation_completed_at=current.get("generation_completed_at"),
                            downloaded_at=current.get("downloaded_at"),
                            flow_project_url=current.get("flow_project_url"),
                            prompt_hash=current.get("prompt_hash"),
                            prompt=current.get("prompt"),
                            generation_cost=current.get("generation_cost"),
                            checkpoint_evidence=current.get("checkpoint_evidence", {}),
                            file_path=current.get("file_path"),
                            validation=current.get("validation", {}),
                        )
                        save_scene_manifest(scene_manifest, project_scenes_dir)
                        self._save_manifest()

                    # A submitted/generating/generated checkpoint is resumed
                    # by inspection/download only. It must never enter the
                    # Create-click path again.
                    checkpoint = self.manifest.scene_checkpoints.get(scene.id)
                    if not checkpoint and existing and existing.status in {
                        "generation_submitted", "generating", "generated", "downloaded",
                        "timeout", "unknown_reconciliation_required",
                    }:
                        checkpoint = existing.model_dump()
                    if checkpoint:
                        account_checkpoint_credit(scene.id, checkpoint.get("attempt_number"))
                    submitted_evidence = (checkpoint or {}).get("checkpoint_evidence", {}) or {}
                    submitted_state = submitted_evidence.get("submission", submitted_evidence) or {}
                    resumable_checkpoint = checkpoint and checkpoint.get("status") not in {"failed", "prepared"}
                    if resumable_checkpoint and (
                        checkpoint.get("status") in {
                            "generation_submitted", "generating", "generated", "downloaded",
                            "timeout", "unknown_reconciliation_required",
                        }
                        or submitted_state.get("state") == "SUBMITTED"
                    ):
                        logger.info(
                            "Resuming submitted scene %s without clicking Create (status=%s)",
                            scene.id, checkpoint.get("status"),
                        )
                        result = generator.resume_submitted_scene(
                            request=request,
                            checkpoint=checkpoint,
                            checkpoint_callback=persist_scene_checkpoint,
                        )
                    else:
                        prior_attempts = (checkpoint or {}).get("attempts", [])
                        if not prior_attempts and checkpoint:
                            prior_attempts = [checkpoint]
                        attempt_number = len(prior_attempts) + 1
                        result = None
                        last_error = None
                        while attempt_number <= MAX_GENERATION_ATTEMPTS:
                            if attempt_number > 1:
                                logger.warning(
                                    "Retrying scene %s: attempt %s/%s",
                                    scene.id, attempt_number, MAX_GENERATION_ATTEMPTS,
                                )
                                health = generator.ensure_flow_healthy(self.config.flow_project_url)
                                if not health.healthy:
                                    last_error = f"Flow surface unavailable before retry: {health.classification}"
                                    break
                            prepare_attempt(attempt_number)
                            active_attempt = attempt_number

                            def persist_attempt_checkpoint(update: Dict[str, Any]) -> None:
                                update = {**update, "attempt_number": active_attempt}
                                scene_checkpoint = self.manifest.scene_checkpoints.get(scene.id, {})
                                current_attempts = scene_checkpoint.setdefault("attempts", [])
                                attempt_record = next(
                                    item for item in current_attempts
                                    if item.get("attempt_number") == active_attempt
                                )
                                attempt_record.update({
                                    key: value for key, value in update.items()
                                    if key != "attempt_number"
                                })
                                attempt_record["updated_at"] = datetime.now().isoformat()
                                scene_checkpoint.update(update)
                                scene_checkpoint["attempts"] = current_attempts
                                self.manifest.scene_checkpoints[scene.id] = scene_checkpoint
                                account_checkpoint_credit(scene.id, active_attempt)
                                persist_scene_checkpoint(update)

                            result = generator.generate_scene(
                                request=request,
                                dry_run=False,
                                require_confirmation=not self.config.skip_confirmation,
                                checkpoint_callback=persist_attempt_checkpoint,
                            )
                            if result.success:
                                break
                            last_error = result.error
                            attempt_record = next(
                                item for item in self.manifest.scene_checkpoints[scene.id]["attempts"]
                                if item.get("attempt_number") == active_attempt
                            )
                            submitted = (
                                (attempt_record.get("checkpoint_evidence", {}) or {})
                                .get("submission", {})
                                .get("state")
                                == "SUBMITTED"
                            )
                            if not submitted:
                                # Prompt/setup failures did not spend credits
                                # and are not generation retries.
                                break
                            if result.status in {"timeout", "unknown_reconciliation_required"}:
                                # A timeout or an inability to reconcile is
                                # deliberately terminal for this run. The
                                # durable SUBMITTED identity must be inspected
                                # on resume, never charged/clicked again via
                                # an automatic retry.
                                logger.warning(
                                    "Leaving submitted scene %s resumable after %s",
                                    scene.id,
                                    result.status,
                                )
                                break
                            if attempt_number == MAX_GENERATION_ATTEMPTS:
                                break
                            attempt_number += 1
                        if result is None:
                            result = SceneGenerationResult.failure(
                                scene.id, scene.sequence, last_error or "No generation attempt was made"
                            )
                    
                    if not result.success:
                        raise StageFailedError(
                            "scenes",
                            f"Scene {scene.id} failed: {result.error}",
                            {"scene_id": scene.id, "status": result.status},
                        )
                    
                    # Validate generated video
                    if result.file_path:
                        validation = validate_video_file(result.file_path, config.aspect_ratio)
                        if not validation.get("valid"):
                            raise ValidationError(
                                "scenes",
                                f"Scene {scene.id} validation failed: {validation.get('error')}",
                                path=str(result.file_path),
                            )
                        
                        scene_paths.append(str(result.file_path))
                        
                        # Update manifest
                        scene_manifest.add_scene(
                            scene_id=scene.id,
                            sequence=scene.sequence,
                            file=f"{scene.id}.mp4",
                            status="completed",
                            file_path=str(result.file_path),
                            validation=validation,
                            generation_cost=result.generation_cost,
                            generation_started_at=result.generation_started_at,
                            generation_completed_at=result.generation_completed_at,
                            downloaded_at=result.downloaded_at,
                        )
                        save_scene_manifest(scene_manifest, project_scenes_dir)
                        self.manifest.scene_checkpoints[scene.id] = {
                            **self.manifest.scene_checkpoints.get(scene.id, {}),
                            "scene_id": scene.id,
                            "sequence": scene.sequence,
                            "status": "completed",
                            "file_path": str(result.file_path),
                            "validation": validation,
                            "generation_cost": result.generation_cost,
                            "generation_started_at": result.generation_started_at,
                            "generation_completed_at": result.generation_completed_at,
                            "downloaded_at": result.downloaded_at,
                            "updated_at": datetime.now().isoformat(),
                        }
                        self._save_manifest()
                    
                    logger.info(f"Scene {scene.id} complete: {result.file_path}")
                
                # Update orchestrator manifest
                self.manifest.scene_video_paths = scene_paths
                self.manifest.scene_manifest_path = str(project_scenes_dir / "scene_manifest.json")
                
                self.manifest.set_stage_result(StageResult.completed(
                    stage=PipelineStage.SCENES,
                    output_path=str(project_scenes_dir),
                    output_files=scene_paths,
                    metadata={
                        "scene_count": len(scene_paths),
                        "credits_used": stage_credits,
                    },
                    started_at=started_at,
                ))
                self._save_manifest()
                
                logger.info(f"Scene generation complete: {len(scene_paths)} scenes")
                logger.info(f"Credits used: {stage_credits}")
                
            finally:
                generator.disconnect()
            
        except StageFailedError as e:
            self.manifest.set_stage_result(StageResult.failed(
                stage=PipelineStage.SCENES,
                error=str(e),
                started_at=started_at,
            ))
            self._save_manifest()
            raise
        except ValidationError:
            raise
        except Exception as e:
            self.manifest.set_stage_result(StageResult.failed(
                stage=PipelineStage.SCENES,
                error=str(e),
                started_at=started_at,
            ))
            self._save_manifest()
            raise StageFailedError("scenes", str(e))
    
    def _execute_captions_stage(self) -> None:
        """Execute caption generation stage."""
        logger.info("")
        logger.info("=" * 40)
        logger.info("Stage 4: CAPTION GENERATION")
        logger.info("=" * 40)
        
        # Check if captions are enabled
        if not self.config.enable_captions:
            logger.info("Captions disabled, skipping")
            self.manifest.set_stage_result(StageResult.skipped(
                PipelineStage.CAPTIONS,
                reason="Captions disabled in config",
            ))
            self._save_manifest()
            return
        
        # Verify voice timing available
        if not self.manifest.voice_timing_path:
            logger.warning("Voice timing not available, skipping captions")
            self.manifest.set_stage_result(StageResult.skipped(
                PipelineStage.CAPTIONS,
                reason="Voice timing not available",
            ))
            self._save_manifest()
            return
        
        started_at = datetime.now().isoformat()
        self.manifest.set_stage_result(StageResult.running(PipelineStage.CAPTIONS))
        self._save_manifest()
        
        try:
            timing_path = Path(self.manifest.voice_timing_path)
            plan_path = Path(self.manifest.content_plan_path)
            
            # Output path
            captions_dir = self.project_dir / "captions"
            captions_dir.mkdir(parents=True, exist_ok=True)
            srt_path = captions_dir / "captions.srt"
            
            # Generate SRT
            generate_srt_from_timing(
                timing_path=timing_path,
                narration_path=plan_path,
                output_path=srt_path,
            )
            
            # Validate SRT
            validation = validate_srt_file(srt_path)
            if not validation.get("valid"):
                raise ValidationError(
                    "captions",
                    f"SRT validation failed: {validation.get('errors')}",
                    path=str(srt_path),
                )
            
            # Update manifest
            self.manifest.captions_path = str(srt_path)
            
            self.manifest.set_stage_result(StageResult.completed(
                stage=PipelineStage.CAPTIONS,
                output_path=str(srt_path),
                output_files=[str(srt_path)],
                metadata={
                    "entry_count": validation.get("entry_count"),
                    "total_duration": validation.get("total_duration"),
                },
                started_at=started_at,
            ))
            self._save_manifest()
            
            logger.info(f"Caption generation complete: {validation.get('entry_count')} entries")
            logger.info(f"Output: {srt_path}")
            
        except ValidationError:
            raise
        except Exception as e:
            self.manifest.set_stage_result(StageResult.failed(
                stage=PipelineStage.CAPTIONS,
                error=str(e),
                started_at=started_at,
            ))
            self._save_manifest()
            raise StageFailedError("captions", str(e))
    
    def _execute_assembly_stage(self) -> None:
        """Execute video assembly stage."""
        logger.info("")
        logger.info("=" * 40)
        logger.info("Stage 5: VIDEO ASSEMBLY")
        logger.info("=" * 40)
        
        # Verify required inputs
        if not self.manifest.scene_video_paths:
            raise StageFailedError("assembly", "No scene videos available")
        
        # In dry run, skip actual assembly
        if self.config.dry_run:
            logger.info("DRY RUN: Skipping actual assembly")
            self.manifest.set_stage_result(StageResult.completed(
                stage=PipelineStage.ASSEMBLY,
                metadata={"dry_run": True},
                started_at=datetime.now().isoformat(),
            ))
            self._save_manifest()
            return
        
        started_at = datetime.now().isoformat()
        self.manifest.set_stage_result(StageResult.running(PipelineStage.ASSEMBLY))
        self._save_manifest()
        
        try:
            from app.assembly.pipeline import assemble_project
            from app.assembly.models import (
                AssemblyProject,
                Scene as AssemblyScene,
                VideoResolution,
                CaptionConfig,
                OutputConfig,
            )
            from app.assembly.ffmpeg import check_ffmpeg_available, check_ffprobe_available
            
            # Check FFmpeg
            if not check_ffmpeg_available():
                raise DependencyError("ffmpeg", "FFmpeg not found in PATH")
            if not check_ffprobe_available():
                raise DependencyError("ffprobe", "FFprobe not found in PATH")
            
            # Load production plan for title
            plan_path = Path(self.manifest.content_plan_path)
            with open(plan_path, "r", encoding="utf-8") as f:
                plan_data = json.load(f)
            
            # Build assembly scenes from scene video paths
            assembly_scenes = []
            for i, video_path in enumerate(self.manifest.scene_video_paths):
                if not Path(video_path).exists():
                    raise ValidationError(
                        "assembly",
                        f"Scene video not found: {video_path}",
                        path=video_path,
                    )
                
                # Extract scene ID from path
                scene_id = Path(video_path).stem
                
                assembly_scenes.append(AssemblyScene(
                    id=scene_id,
                    video=video_path,
                ))
            
            logger.info(f"Assembling {len(assembly_scenes)} scenes")
            
            # Build caption config
            caption_config = CaptionConfig(enabled=False)
            if self.manifest.captions_path and Path(self.manifest.captions_path).exists():
                caption_config = CaptionConfig(
                    enabled=True,
                    source=self.manifest.captions_path,
                )
                logger.info(f"Captions: {self.manifest.captions_path}")
            
            # Build assembly project
            assembly_config = self.config.to_assembly_config_dict()
            
            project = AssemblyProject(
                project_id=self.manifest.project_id,
                title=plan_data.get("title", "Untitled"),
                aspect_ratio=self.config.scene_aspect_ratio,
                resolution=VideoResolution(
                    width=assembly_config.get("resolution_width", 1080),
                    height=assembly_config.get("resolution_height", 1920),
                ),
                scenes=assembly_scenes,
                voiceover=self.manifest.voice_audio_path,
                music_volume=assembly_config.get("music_volume", 0.15),
                captions=caption_config,
                output=OutputConfig(
                    filename=f"{self.manifest.project_id}_final.mp4",
                ),
            )
            
            # Run assembly
            assembly_output_dir = self.project_dir / "final"
            result = assemble_project(
                project=project,
                base_dir=self.project_dir,
                output_dir=assembly_output_dir,
            )
            
            if not result.success:
                raise StageFailedError(
                    "assembly",
                    "; ".join(result.errors),
                    {"warnings": result.warnings},
                )
            
            # Validate output
            if not result.output_path or not Path(result.output_path).exists():
                raise ValidationError("assembly", "Output file not created")
            
            # Update manifest
            self.manifest.final_video_path = result.output_path
            
            self.manifest.set_stage_result(StageResult.completed(
                stage=PipelineStage.ASSEMBLY,
                output_path=result.output_path,
                output_files=[result.output_path],
                warnings=result.warnings,
                metadata={
                    "duration": result.duration,
                    "width": result.width,
                    "height": result.height,
                    "file_size": result.file_size,
                },
                started_at=started_at,
            ))
            self._save_manifest()
            
            logger.info(f"Assembly complete!")
            logger.info(f"Duration: {result.duration:.2f}s")
            logger.info(f"Resolution: {result.width}x{result.height}")
            logger.info(f"Size: {result.file_size / (1024*1024):.2f} MB")
            logger.info(f"Output: {result.output_path}")
            
        except StageFailedError:
            raise
        except ValidationError as e:
            self.manifest.set_stage_result(StageResult.failed(
                stage=PipelineStage.ASSEMBLY,
                error=str(e),
                started_at=started_at,
            ))
            self._save_manifest()
            raise
        except DependencyError as e:
            self.manifest.set_stage_result(StageResult.failed(
                stage=PipelineStage.ASSEMBLY,
                error=str(e),
                started_at=started_at,
            ))
            self._save_manifest()
            raise
        except Exception as e:
            self.manifest.set_stage_result(StageResult.failed(
                stage=PipelineStage.ASSEMBLY,
                error=str(e),
                started_at=started_at,
            ))
            self._save_manifest()
            raise StageFailedError("assembly", str(e))
    
    def _create_mock_video(self, output_path: Path, duration: float) -> None:
        """Create a mock video file for testing."""
        import subprocess
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Use FFmpeg to create a test video
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=blue:s=1080x1920:d={duration}",
            "-f", "lavfi",
            "-i", f"anullsrc=r=44100:cl=stereo:d={duration}",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-c:a", "aac",
            "-shortest",
            str(output_path),
        ]
        
        try:
            subprocess.run(cmd, capture_output=True, check=True, timeout=60)
            logger.debug(f"Created mock video: {output_path}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to create mock video: {e}")
            # Create empty file as fallback
            output_path.touch()
        except FileNotFoundError:
            logger.warning("FFmpeg not available for mock video creation")
            output_path.touch()
