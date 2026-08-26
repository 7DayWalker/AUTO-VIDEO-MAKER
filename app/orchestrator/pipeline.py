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

        # Step 6A: persisted visual mode is authoritative on resume. A project
        # created as 'local' can never silently become a Flow project.
        persisted_mode = (self.manifest.scene_config or {}).get("visual_mode")
        if persisted_mode:
            self.config.visual_mode = persisted_mode

        # Step 6B: persisted hybrid routing/default provider are equally
        # authoritative — runtime config can never re-route a scene.
        persisted_default = (self.manifest.scene_config or {}).get(
            "visual_default_provider"
        )
        if persisted_default:
            self.config.visual_default_provider = persisted_default
        persisted_routing = (self.manifest.scene_config or {}).get(
            "scene_provider_routing"
        )
        if persisted_routing:
            self.config.visual_scene_routing = {
                scene_id: item["provider"] if isinstance(item, dict) else item
                for scene_id, item in persisted_routing.items()
            }
        
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
    
    def _execute_scenes_stage(
        self,
        *,
        only_scenes: Optional[frozenset] = None,
        force_google_flow: bool = False,
    ) -> None:
        """Execute scene generation stage.

        Step 6B: ``only_scenes`` restricts the run to a subset of scene IDs
        (used by hybrid mode so each provider pass touches only its routed
        scenes). ``force_google_flow=True`` runs the proven Flow body for a
        hybrid project's explicitly Flow-routed subset without touching the
        project's persisted hybrid mode.
        """
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

        # Step 6A: resolve + persist the project visual mode BEFORE any
        # provider-specific work. Persisted mode is authoritative on resume.
        from app.orchestrator.visual_providers import (
            create_visual_provider,
            resolve_visual_mode,
        )
        # Phase 6E: bind SceneManifest BEFORE any branch uses it. The later
        # real-generation import binds this name function-locally for the
        # entire method, which previously made the earlier mock branch raise
        # UnboundLocalError when constructing scene-manifest entries.
        from app.flow.scene_models import SceneManifest
        from app.flow.scene_generator import (
            load_manifest as load_scene_manifest,
            save_manifest as save_scene_manifest,
        )
        if force_google_flow:
            # Hybrid Flow pass: run the proven Flow body without mutating the
            # project's persisted hybrid mode.
            visual_mode = "google_flow"
        else:
            visual_mode = resolve_visual_mode(
                self.config.visual_mode, self.manifest.scene_config
            )
            if self.manifest.scene_config.get("visual_mode") != visual_mode:
                self.manifest.scene_config["visual_mode"] = visual_mode
                self._save_manifest()
        
        try:
            from app.content.models import ProductionPlan
            
            # Load production plan
            plan_path = Path(self.manifest.content_plan_path)
            with open(plan_path, "r", encoding="utf-8") as f:
                plan_data = json.load(f)
            plan = ProductionPlan(**plan_data)
            
            logger.info(f"Loaded plan: {plan.project_id}")
            logger.info(f"Scenes to generate: {len(plan.scenes)}")
            logger.info(f"Visual mode: {visual_mode}")

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
            
            # Step 6A: local mode has its own zero-cost path. It must never
            # import or construct FlowSceneGenerator (cost firewall).
            if visual_mode == "local" and not self.config.mock_mode:
                return self._execute_scenes_stage_local(
                    plan=plan,
                    started_at=started_at,
                )

            # Step 6B: functional hybrid mode (durable per-scene routing).
            if visual_mode == "hybrid" and not self.config.mock_mode \
                    and not self.config.dry_run:
                return self._execute_scenes_stage_hybrid(
                    plan=plan,
                    started_at=started_at,
                )

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
                    # Phase 6E: durable per-scene placeholder provenance,
                    # mirroring the real-provider checkpoint shape.
                    self.manifest.scene_checkpoints[scene.id] = {
                        "provider": "mock",
                        "generation_kind": "placeholder",
                        "status": "completed",
                        "generation_cost": 0,
                    }
                
                self.manifest.scene_video_paths = scene_paths
                self.manifest.scene_manifest_path = str(scenes_output_dir / "scene_manifest.json")
                # Phase 6E: project-level marker so CLI summaries and
                # reconciliation can distinguish mock runs truthfully.
                self.manifest.scene_config["generation_kind"] = "placeholder"
                
                # Create scene manifest — Phase 6E: durably mark these as
                # PLACEHOLDER generations so reconciliation can never promote
                # them into an indistinguishable "real" SCENES-completed
                # state. Uses the same additive checkpoint_evidence field as
                # real providers (absence of the marker == real generation,
                # preserving legacy-manifest compatibility).
                scene_manifest = SceneManifest(project_id=plan.project_id)
                for scene in plan.scenes:
                    scene_manifest.add_scene(
                        scene_id=scene.id,
                        sequence=scene.sequence,
                        file=f"{scene.id}.mp4",
                        status="completed",
                        checkpoint_evidence={
                            "provider": "mock",
                            "generation_kind": "placeholder",
                        },
                    )
                save_scene_manifest(scene_manifest, scenes_output_dir)
                
                self.manifest.set_stage_result(StageResult.completed(
                    stage=PipelineStage.SCENES,
                    output_path=str(scenes_output_dir),
                    output_files=scene_paths,
                    metadata={
                        "mock_mode": True,
                        "generation_kind": "placeholder",
                        "scene_count": len(plan.scenes),
                    },
                    started_at=started_at,
                ))
                self._save_manifest()
                
                logger.info(f"MOCK MODE: Created {len(scene_paths)} placeholder scenes")
                return
            
            # Real scene generation via Google Flow (Step 6A provider wrapper).
            from app.flow.scene_generator import (
                FlowAuthenticationError as _FlowAuthErr,
                FlowConnectionError as _FlowConnErr,
                load_manifest as load_scene_manifest,
                save_manifest as save_scene_manifest,
                validate_video_file,
            )
            from app.flow.scene_models import (
                FlowGenerationConfig,
                SceneGenerationRequest,
                SceneGenerationResult,
                SceneManifest,
            )
            from app.orchestrator.visual_providers import GoogleFlowVisualProvider

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
            generator = GoogleFlowVisualProvider(
                scene_config, scenes_output_dir, self.config.flow_project_url
            )

            # Connect to Chrome
            try:
                generator.connect(expected_url=self.config.flow_project_url)
                logger.info("Connected to Chrome via CDP")
            except ConnectionError as e:
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
                except PermissionError as e:
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
                        config=FlowGenerationConfig(
                            model=scene_config.get("model"),
                            aspect_ratio=scene_config.get("aspect_ratio"),
                            duration=scene_config.get("duration"),
                            count=1,
                            agent_enabled=False,
                        ),
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
                if only_scenes is not None:
                    # Hybrid pass: carry forward artifacts owned by other
                    # provider passes so the final aggregation stays complete.
                    scene_paths.extend(
                        p for p in (self.manifest.scene_video_paths or [])
                        if Path(p).stem not in only_scenes
                    )
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

                    # Step 6B: hybrid sub-pass restriction — scenes owned by
                    # another provider pass are handled elsewhere.
                    if only_scenes is not None and scene.id not in only_scenes:
                        continue

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
    
    def _run_project_asset_gate(
        self,
        *,
        plan,
        scoped_scenes,
        scoped_providers,
        scene_manifest,
        validate_video_file,
        project_scenes_dir,
    ) -> Dict[str, Optional[str]]:
        """Phase 6C: durable per-project asset workflow gate (zero-cost only).

        Scenes-stage ordering contract:

          1-3. Visual mode + authoritative routing are resolved/persisted by
               callers BEFORE this gate runs (already durable).
          4.   Explicitly supplied assets (--asset SCENE_ID=PATH) are copied
               into project-owned storage under ``assets/source/``.
          5.   The asset manifest is persisted atomically (BEFORE validation,
               mirroring how routing persists before generation).
          6.   Full preflight runs (SHA-256 re-verification, media-type
               expectations, legacy discovery) across ALL pending zero-cost
               scenes in ONE pass — every problem is reported together.
          7.   Only on success may zero-cost generation begin.

        On failure raises StageFailedError with every reason embedded in the
        message (so they survive outer re-wrapping) plus structured details;
        the persisted routing and asset manifest remain intact, keeping the
        project resumable once the user supplies/fixes assets. Google Flow is
        structurally unreachable here: this gate never imports or constructs
        it, and google_flow-routed scenes are exempt from preflight scope.

        Completed+valid scenes are excluded from preflight scope; their
        established skip-if-complete/output-validation semantics are unchanged.

        Returns the per-scene authoritative source mapping (project-owned
        copy when an entry exists, else the legacy-discovered file) used BOTH
        as the generation-time ``asset_override`` (regeneration therefore uses
        stored copies regardless of external originals) AND as TRUE SOURCE
        provenance.
        """
        from types import SimpleNamespace

        import app.orchestrator.assets as asset_workflow
        from app.orchestrator.visual_providers import (
            LOCAL_ASSET_PROVIDER,
            create_visual_provider,
            zero_cost_missing_asset_error,
        )

        # Pending scenes only: completed scenes keep their established
        # semantics and are exempt from fingerprint/media checks.
        pending: List[Any] = []
        for scene in sorted(scoped_scenes, key=lambda s: s.sequence):
            existing = scene_manifest.get_scene(scene.id)
            if existing and existing.status in {"completed", "validated"}:
                existing_path = project_scenes_dir / existing.file
                if existing_path.exists():
                    validation = validate_video_file(
                        existing_path, self.config.scene_aspect_ratio
                    )
                    if validation.get("valid"):
                        logger.info(
                            f"Asset preflight: scene {scene.id} already "
                            "complete, exempt from preflight"
                        )
                        continue
            pending.append(scene)

        # Steps 4+5: intake explicit mappings, persist manifest immediately.
        # Resume runs usually carry no mappings and rely solely on the stored
        # project-owned copies (external originals are never consulted).
        errors: List[str] = []
        mappings = dict(self.config.asset_mappings or {})
        if mappings:
            manifest, intake_errors = asset_workflow.intake_assets(
                self.project_dir, plan.scenes, mappings, scoped_providers
            )
            errors.extend(intake_errors)
        else:
            manifest = asset_workflow.load_asset_manifest(self.project_dir)

        # Legacy filename-discovery fallback, delegating to the very providers
        # that will generate (identical naming conventions and asset dirs;
        # single source of truth — no duplicated candidate logic).
        provider_cache: Dict[str, Any] = {}

        def legacy_resolver(scene):
            name = scoped_providers.get(scene.id, LOCAL_ASSET_PROVIDER)
            instance = provider_cache.get(name)
            if instance is None:
                instance = create_visual_provider(
                    "hybrid",
                    scene_config=self.config.to_scene_config_dict(),
                    scenes_output_dir=project_scenes_dir,
                    provider_name=name,
                )
                provider_cache[name] = instance
            stub = SimpleNamespace(
                scene_id=scene.id,
                sequence=int(getattr(scene, "sequence", 0) or 0),
            )
            try:
                found = instance.resolve_asset(stub)
            except Exception:  # noqa: BLE001 - surfaced as truthful failure
                found = None
            if found is not None:
                return found
            # Provider-identical wording (single-sourced helper).
            return zero_cost_missing_asset_error(
                name,
                scene_id=scene.id,
                candidates=instance._candidate_names(stub),
                asset_dir=instance.asset_dir,
            )

        # Step 6: report ALL problems together, never one-scene-at-a-time.
        failures = list(errors)
        if pending:
            failures.extend(
                asset_workflow.preflight_assets(
                    self.project_dir,
                    pending,
                    {
                        scene_id: {"provider": provider}
                        for scene_id, provider in scoped_providers.items()
                    },
                    manifest,
                    legacy_resolver=legacy_resolver,
                )
            )
        if failures:
            raise StageFailedError(
                "scenes",
                "Asset preflight failed:\n"
                + "\n".join(f"- {failure}" for failure in failures),
                {
                    "failures": failures,
                    "hint": (
                        "Provide/correct assets with produce.py --asset "
                        "SCENE_ID=PATH (repeatable), then resume with "
                        "--resume <project_id>. Persisted routing and the "
                        "asset manifest were preserved."
                    ),
                },
            )

        # Step 7 prep: authoritative per-scene sources. A manifest entry is
        # ALWAYS the project-owned stored copy; scenes without an entry keep
        # their legacy-discovered file (the exact file the provider picks).
        source_by_scene: Dict[str, Optional[str]] = {}
        for scene in scoped_scenes:
            authoritative = asset_workflow.resolve_authoritative_source(
                self.project_dir, scene.id, manifest
            )
            if authoritative is not None:
                source_by_scene[scene.id] = str(authoritative)
                continue
            outcome = legacy_resolver(scene)
            source_by_scene[scene.id] = (
                str(outcome) if isinstance(outcome, Path) else None
            )
        return source_by_scene

    def _execute_scenes_stage_local(
        self,
        *,
        plan,
        started_at: str,
        only_scenes: Optional[frozenset] = None,
        provider_by_scene: Optional[Dict[str, str]] = None,
        mode: str = "local",
    ) -> None:
        """Zero-cost scene generation via local_asset/image_motion providers.

        Step 6B: ``provider_by_scene`` selects the provider per scene when
        called from hybrid mode; pure local mode leaves it empty (all scenes
        use LocalAssetVisualProvider). This path NEVER imports or constructs
        FlowSceneGenerator, never opens Chrome/CDP, and never touches Google
        Flow.
        """
        from app.flow.scene_generator import (
            load_manifest as load_scene_manifest,
            save_manifest as save_scene_manifest,
            validate_video_file,
        )
        from app.flow.scene_models import (
            FlowGenerationConfig,
            SceneGenerationRequest,
            SceneManifest,
        )
        from app.orchestrator.visual_providers import (
            create_visual_provider,
        )

        project_scenes_dir = self.project_dir / "scenes" / plan.project_id
        scene_manifest = load_scene_manifest(project_scenes_dir, plan.project_id)

        # Phase 6C ordering contract: intake explicit assets -> persist asset
        # manifest -> preflight ALL pending zero-cost scenes -> only then may
        # generation begin. Routing/mode were persisted durably upstream.
        # Returns authoritative per-scene sources (project-owned copies or
        # legacy-discovered files).
        scoped_scenes_for_gate = [
            s for s in plan.scenes if only_scenes is None or s.id in only_scenes
        ]
        source_by_scene = self._run_project_asset_gate(
            plan=plan,
            scoped_scenes=scoped_scenes_for_gate,
            scoped_providers={
                s.id: (provider_by_scene or {}).get(s.id, "local_asset")
                for s in scoped_scenes_for_gate
            },
            scene_manifest=scene_manifest,
            validate_video_file=validate_video_file,
            project_scenes_dir=project_scenes_dir,
        )

        provider = create_visual_provider(
            "local",
            scene_config=self.config.to_scene_config_dict(),
            scenes_output_dir=project_scenes_dir,
        )

        scene_paths: list = []

        for scene in sorted(plan.scenes, key=lambda s: s.sequence):
            if only_scenes is not None and scene.id not in only_scenes:
                continue
            logger.info(f"Local asset scene {scene.id} (seq {scene.sequence})")

            # Step 6B: per-scene provider selection (hybrid-aware).
            chosen_provider = (provider_by_scene or {}).get(
                scene.id, "local_asset"
            )
            logger.info(f"Provider: {chosen_provider}")

            # Phase 6C: TRUE SOURCE provenance. A project-owned stored copy
            # takes precedence; otherwise the legacy-discovered file (the
            # exact file this provider would pick itself) is pinned here so
            # the provider reuses the same asset and provenance is durable.
            source_ref = source_by_scene.get(scene.id)
            if source_ref:
                logger.info(f"Source asset: {source_ref}")

            # Skip-if-complete (same semantics as the Flow loop).
            existing = scene_manifest.get_scene(scene.id)
            if existing and existing.status in {"completed", "validated"}:
                existing_path = project_scenes_dir / existing.file
                if existing_path.exists():
                    validation = validate_video_file(
                        existing_path, self.config.scene_aspect_ratio
                    )
                    if validation.get("valid"):
                        logger.info(f"Scene already exists, skipping: {existing_path}")
                        scene_paths.append(str(existing_path))
                        continue

            request = SceneGenerationRequest(
                project_id=plan.project_id,
                scene_id=scene.id,
                sequence=scene.sequence,
                visual_prompt=scene.visual_prompt,
                config=FlowGenerationConfig(
                    model="local_asset",
                    aspect_ratio=self.config.scene_aspect_ratio,
                    duration=max(1, int(scene.estimated_duration) or 4),
                    count=1,
                    agent_enabled=False,
                ),
            )

            def persist_local_checkpoint(update: Dict[str, Any]) -> None:
                update = {**update, "provider": chosen_provider}
                current = self.manifest.scene_checkpoints.get(update["scene_id"], {})
                current.update(update)
                self.manifest.scene_checkpoints[update["scene_id"]] = current

                scene_manifest.add_scene(
                    scene_id=scene.id,
                    sequence=scene.sequence,
                    file=f"{scene.id}.mp4",
                    status=current.get("status", "completed"),
                    generation_cost=current.get("generation_cost", 0),
                    checkpoint_evidence={
                        "provider": chosen_provider,
                        # Kept for backward compatibility with existing
                        # readers (output MP4 path). The true source lives in
                        # ``source_asset`` below (Phase 6C provenance fix).
                        "asset_reference": update.get("file_path"),
                        "source_asset": source_ref,
                    },
                    file_path=current.get("file_path"),
                    validation=current.get("validation", {}),
                )
                save_scene_manifest(scene_manifest, project_scenes_dir)
                self._save_manifest()

            scene_provider = create_visual_provider(
                mode,
                scene_config=self.config.to_scene_config_dict(),
                scenes_output_dir=project_scenes_dir,
                provider_name=chosen_provider,
            )

            result = scene_provider.generate_scene(
                request,
                require_confirmation=False,
                checkpoint_callback=persist_local_checkpoint,
                # Phase 6C: the project-owned stored copy (or pinned legacy
                # discovery result) is authoritative; external originals are
                # never consulted during (re)generation.
                asset_override=Path(source_ref) if source_ref else None,
            )
            if not result.success:
                raise StageFailedError(
                    "scenes",
                    f"Scene {scene.id} failed: {result.error}",
                    {"scene_id": scene.id, "status": result.status},
                )

            validation = validate_video_file(
                Path(result.file_path), request.config.aspect_ratio
            )
            if not validation.get("valid"):
                raise ValidationError(
                    "scenes",
                    f"Scene {scene.id} validation failed: {validation.get('error')}",
                    path=str(result.file_path),
                )

            scene_paths.append(str(result.file_path))
            self.manifest.scene_checkpoints[scene.id] = {
                **self.manifest.scene_checkpoints.get(scene.id, {}),
                "provider": chosen_provider,
                "scene_id": scene.id,
                "sequence": scene.sequence,
                "status": "completed",
                "generation_cost": 0,
                # Phase 6C: TRUE SOURCE asset provenance (project-owned copy
                # or pinned legacy file), distinct from the output MP4 path.
                "source_asset": source_ref,
                "file_path": str(result.file_path),
                "validation": validation,
                "updated_at": datetime.now().isoformat(),
            }
            self._save_manifest()
            logger.info(f"Local scene {scene.id} complete: {result.file_path}")

        self.manifest.scene_video_paths = scene_paths
        self.manifest.scene_manifest_path = str(
            project_scenes_dir / "scene_manifest.json"
        )
        self.manifest.set_stage_result(StageResult.completed(
            stage=PipelineStage.SCENES,
            output_path=str(project_scenes_dir),
            output_files=scene_paths,
            metadata={
                "scene_count": len(scene_paths),
                "visual_mode": mode,
                "credits_used": 0,
                "provider": (
                    sorted(set((provider_by_scene or {}).values()))
                    or ["local_asset"]
                ),
            },
            started_at=started_at,
        ))
        self._save_manifest()

        logger.info(f"Local asset scene generation complete: {len(scene_paths)} scenes")

    def _execute_scenes_stage_hybrid(self, *, plan, started_at: str) -> None:
        """Step 6B: functional hybrid mode with durable per-scene routing.

        Sequence (ordering is mandatory):
          1. Build the complete routing map (CLI overrides > persisted
             entries > image_motion default).
          2. Persist it into manifest.scene_config and save — BEFORE any
             provider runs.
          3. Run all zero-cost scenes (local_asset / image_motion).
          4. Run explicitly Flow-routed scenes through the PROVEN Flow body
             (force_google_flow sub-pass), only if any exist.

        Cost firewall: a zero-cost scene failure NEVER falls back to Flow.
        """
        from app.orchestrator.visual_providers import (
            GOOGLE_FLOW_PROVIDER,
            build_hybrid_routing,
        )

        logger.info("Hybrid visual mode: building durable scene routing...")

        # 1+2) Authoritative routing map persisted BEFORE generation.
        routing = build_hybrid_routing(
            plan.scenes,
            scene_config=self.manifest.scene_config,
            cli_overrides=self.config.visual_scene_routing,
        )
        self.manifest.scene_config["visual_mode"] = "hybrid"
        self.manifest.scene_config["visual_default_provider"] = (
            self.config.visual_default_provider
        )
        self.manifest.scene_config["scene_provider_routing"] = routing
        self._save_manifest()
        for sid in sorted(routing):
            logger.info(
                "  %s -> %s (%s)", sid, routing[sid]["provider"], routing[sid]["reason"]
            )

        flow_ids = frozenset(
            sid
            for sid, item in routing.items()
            if item["provider"] == GOOGLE_FLOW_PROVIDER
        )
        zero_ids = {sid for sid in routing if sid not in flow_ids}
        credits_before = self.manifest.total_credits_used

        # 3) Zero-cost pass first: structurally cannot touch Flow.
        if zero_ids:
            self._execute_scenes_stage_local(
                plan=plan,
                started_at=started_at,
                only_scenes=frozenset(zero_ids),
                provider_by_scene={
                    sid: routing[sid]["provider"] for sid in zero_ids
                },
                mode="hybrid",
            )

        # 4) Explicit Flow pass through the PROVEN Flow body, restricted to
        # exactly the scenes whose persisted routing says google_flow.
        if flow_ids:
            logger.info(
                "Hybrid: running %d explicitly Flow-routed scene(s): %s",
                len(flow_ids), sorted(flow_ids),
            )
            self._execute_scenes_stage(
                only_scenes=flow_ids,
                force_google_flow=True,
            )

        # 5) Aggregate the final hybrid stage result.
        final_paths = list(self.manifest.scene_video_paths or [])
        providers_used = sorted({item["provider"] for item in routing.values()})
        self.manifest.set_stage_result(StageResult.completed(
            stage=PipelineStage.SCENES,
            output_path=str(self.project_dir / "scenes"),
            output_files=final_paths,
            metadata={
                "scene_count": len(final_paths),
                "visual_mode": "hybrid",
                "providers": providers_used,
                "credits_used": self.manifest.total_credits_used - credits_before,
                "routing": routing,
            },
            started_at=started_at,
        ))
        self._save_manifest()
        logger.info("Hybrid scene generation complete: %d scenes", len(final_paths))

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
