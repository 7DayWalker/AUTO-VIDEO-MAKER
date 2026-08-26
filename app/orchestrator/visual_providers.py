"""Step 6A — Pluggable scene visual providers.

A visual provider turns one :class:`SceneGenerationRequest` (from the
production plan) into one validated scene MP4 that satisfies the existing
downstream contracts:

- ``validate_video_file`` (ffprobe: exists, non-empty, video stream, aspect)
- ``SceneManifestEntry`` provenance
- ``AssemblyScene(video=path)`` consumption by the assembly stage

The Google Flow implementation wraps the existing ``FlowSceneGenerator``
verbatim; its automation core, credit parsing, confirmation gates and
SUBMITTED-resume semantics are intentionally untouched.

Visual modes (project-level, persisted in ``ProjectManifest.scene_config``):

- ``google_flow``  default / legacy behavior (Chrome CDP -> Flow/Veo, credits)
- ``local``        zero-cost normalization of user-supplied local assets
- ``hybrid``       architecturally reserved; Phase 6A refuses without durable
                   explicit routing data (never an ephemeral runtime decision)
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.assembly.ffmpeg import probe_media, run_ffmpeg
from app.flow.scene_generator import (
    FlowAuthenticationError,
    FlowConnectionError,
    FlowSceneGenerator,
    validate_video_file,
)
from app.flow.scene_models import (
    FlowGenerationConfig,
    SceneGenerationRequest,
    SceneGenerationResult,
)

VISUAL_MODES = {"local", "google_flow", "hybrid"}
DEFAULT_VISUAL_MODE = "google_flow"

GOOGLE_FLOW_PROVIDER = "google_flow"
LOCAL_ASSET_PROVIDER = "local_asset"

# Fallback root for user-supplied local scene assets.
PROJECT_ASSET_FALLBACK = Path(__file__).resolve().parents[2] / "assets" / "local"


def normalize_visual_mode(value: Optional[str]) -> str:
    """Validate/normalize a visual mode value; legacy/default -> google_flow."""
    mode = (value or DEFAULT_VISUAL_MODE).strip().lower()
    if mode not in VISUAL_MODES:
        raise ValueError(f"Invalid visual_mode '{value}'. Valid: {sorted(VISUAL_MODES)}")
    return mode


def resolve_visual_mode(
    config_value: Optional[str],
    persisted_scene_config: Optional[Dict[str, Any]],
) -> str:
    """Resolve the authoritative project visual mode.

    Once a project has persisted a mode in ``scene_config``, that decision is
    authoritative for every resume. Only projects that never recorded a mode
    fall back to configuration/default (preserving legacy Flow behavior).
    """
    persisted = (persisted_scene_config or {}).get("visual_mode")
    if persisted:
        return normalize_visual_mode(persisted)
    return normalize_visual_mode(config_value)


class GoogleFlowVisualProvider:
    """Thin wrapper around the EXISTING FlowSceneGenerator.

    Behavioral-parity rule: every method forwards verbatim to the wrapped
    generator. No automation logic lives here.
    """

    name = GOOGLE_FLOW_PROVIDER
    requires_browser = True

    def __init__(
        self,
        scene_config: Dict[str, Any],
        output_dir: Path,
        flow_project_url: Optional[str] = None,
    ):
        config_kwargs: Dict[str, Any] = {"count": 1, "agent_enabled": False}
        for key in ("model", "aspect_ratio", "duration"):
            value = scene_config.get(key)
            if value is not None:
                config_kwargs[key] = value
        self._generator = FlowSceneGenerator(
            config=FlowGenerationConfig(**config_kwargs),
            output_dir=output_dir,
        )
        self.flow_project_url = flow_project_url

    # -- lifecycle (forwarded verbatim) -------------------------------------
    def connect(self, expected_url: str = None) -> bool:
        try:
            return self._generator.connect(expected_url=expected_url)
        except FlowConnectionError as exc:
            raise ConnectionError(str(exc)) from exc

    def navigate_to_project(self, project_url: str) -> bool:
        try:
            return self._generator.navigate_to_project(project_url)
        except FlowAuthenticationError as exc:
            raise PermissionError(str(exc)) from exc

    def disconnect(self) -> None:
        self._generator.disconnect()

    def ensure_healthy(self, expected_url: str = None):
        return self._generator.ensure_flow_healthy(expected_url)

    def verify_ui_only(self, request: SceneGenerationRequest):
        return self._generator.verify_ui_only(request)

    # -- generation ----------------------------------------------------------
    def generate_scene(
        self,
        request: SceneGenerationRequest,
        *,
        dry_run: bool = False,
        require_confirmation: bool = True,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SceneGenerationResult:
        result = self._generator.generate_scene(
            request=request,
            dry_run=dry_run,
            require_confirmation=require_confirmation,
            checkpoint_callback=checkpoint_callback,
        )
        return self._tag(result)

    def resume_submitted_scene(
        self,
        request: SceneGenerationRequest,
        checkpoint: Dict[str, Any],
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SceneGenerationResult:
        """Historical name used by the shared scene loop; forwards verbatim."""
        result = self._generator.resume_submitted_scene(
            request=request,
            checkpoint=checkpoint,
            checkpoint_callback=checkpoint_callback,
        )
        return self._tag(result)

    def resume_scene(
        self,
        request: SceneGenerationRequest,
        checkpoint: Dict[str, Any],
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SceneGenerationResult:
        """Provider-protocol alias for :meth:`resume_submitted_scene`."""
        return self.resume_submitted_scene(request, checkpoint, checkpoint_callback)

    @staticmethod
    def _tag(result: SceneGenerationResult) -> SceneGenerationResult:
        result.verification.setdefault("provider", GOOGLE_FLOW_PROVIDER)
        return result


class LocalAssetVisualProvider:
    """Zero-credit provider: normalizes a local source asset into a valid
    scene MP4 using the existing FFmpeg infrastructure.

    Deterministic mapping (documented contract), first match wins:
      1. ``<asset_dir>/<scene_id>.<ext>``
      2. ``<asset_dir>/<sequence as 3 digits>.<ext>``   e.g. ``001.mp4``
      3. ``<asset_dir>/scene_<sequence>.<ext>``          e.g. ``scene_1.mp4``
    Extensions: mp4, mov, mkv, webm, jpg, jpeg, png.
    """

    name = LOCAL_ASSET_PROVIDER
    requires_browser = False

    _VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm")
    _IMAGE_EXTS = (".jpg", ".jpeg", ".png")

    def __init__(self, asset_dir: Path, output_dir: Path):
        self.asset_dir = Path(asset_dir)
        self.output_dir = Path(output_dir)

    def is_available(self) -> bool:
        return self.asset_dir.is_dir()

    def _candidate_names(self, request: SceneGenerationRequest) -> List[str]:
        names: List[str] = []
        stem = f"{request.sequence:03d}"
        for ext in self._VIDEO_EXTS + self._IMAGE_EXTS:
            names.append(f"{request.scene_id}{ext}")
            names.append(f"{stem}{ext}")
            names.append(f"scene_{int(request.sequence)}{ext}")
        return names

    def resolve_asset(self, request: SceneGenerationRequest) -> Optional[Path]:
        if not self.asset_dir.is_dir():
            return None
        for name in self._candidate_names(request):
            candidate = self.asset_dir / name
            if candidate.is_file():
                return candidate
        return None

    # -- generation ----------------------------------------------------------
    def generate_scene(
        self,
        request: SceneGenerationRequest,
        *,
        require_confirmation: bool = False,  # zero-cost: nothing to confirm
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SceneGenerationResult:
        del require_confirmation  # interface parity only

        def _fail(error: str) -> SceneGenerationResult:
            result = SceneGenerationResult.failure(
                request.scene_id, request.sequence, error
            )
            result.verification["provider"] = LOCAL_ASSET_PROVIDER
            return result

        source = self.resolve_asset(request)
        if source is None:
            return _fail(
                f"No local asset found for scene '{request.scene_id}' "
                f"(searched: {', '.join(self._candidate_names(request))}) in {self.asset_dir}"
            )

        target_duration = max(1, int(getattr(request.config, "duration", 4)))
        width, height = 1080, 1920
        aspect = getattr(request.config, "aspect_ratio", "9:16")
        if aspect == "16:9":
            width, height = 1920, 1080
        elif aspect == "1:1":
            width = height = 1080

        output_path = self.output_dir / f"{request.scene_id}.mp4"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        info = probe_media(source)
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps=30,format=yuv420p"
        )
        args: List[str] = ["-y", "-i", str(source)]
        if info.has_audio:
            args += ["-map", "0:v:0", "-map", "0:a:0"]
        else:
            args += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        args += [
            "-t", str(target_duration),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            str(output_path),
        ]
        proc = run_ffmpeg(args, description=f"LocalAsset normalize {source.name}")
        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "")[-300:]
            return _fail(f"FFmpeg normalization failed: {stderr_tail}")

        validation = validate_video_file(output_path, request.config.aspect_ratio)
        if not validation.get("valid"):
            return _fail(
                f"Normalized asset failed validation: {validation.get('error')}"
            )

        result = SceneGenerationResult.success_result(
            scene_id=request.scene_id,
            sequence=request.sequence,
            file_path=output_path,
            file_size=validation.get("file_size", output_path.stat().st_size),
            actual_duration=validation.get("duration", float(target_duration)),
            width=validation.get("width", width),
            height=validation.get("height", height),
            codec=validation.get("codec", "h264"),
            generation_cost=0,
            downloaded_at=datetime.now(timezone.utc).isoformat(),
            warnings=[f"source_asset={source.name}"],
        )
        result.verification.update(
            {
                "provider": LOCAL_ASSET_PROVIDER,
                "asset_reference": str(source),
                "classification": "zero_cost_local_asset",
            }
        )

        if checkpoint_callback is not None:
            checkpoint_callback(
                {
                    "scene_id": request.scene_id,
                    "provider": LOCAL_ASSET_PROVIDER,
                    "status": "completed",
                    "generation_cost": 0,
                    "file_path": str(output_path),
                    "validation": validation,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        return result

    def resume_scene(
        self,
        request: SceneGenerationRequest,
        checkpoint: Dict[str, Any],
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SceneGenerationResult:
        """Local scenes are deterministic; resume == regenerate/validate."""
        return self.generate_scene(
            request, require_confirmation=False, checkpoint_callback=checkpoint_callback
        )


def create_visual_provider(
    mode: str,
    *,
    scene_config: Dict[str, Any],
    scenes_output_dir: Path,
    flow_project_url: Optional[str] = None,
    local_asset_dir: Optional[Path] = None,
):
    """Deterministic provider factory.

    Cost firewall property: ``local`` mode can never construct
    FlowSceneGenerator because this factory never references it for that mode.
    """
    mode = normalize_visual_mode(mode)
    if mode == GOOGLE_FLOW_PROVIDER:
        return GoogleFlowVisualProvider(
            scene_config, scenes_output_dir, flow_project_url
        )
    if mode == "local":
        asset_dir = Path(
            local_asset_dir
            or os.getenv("AVM_LOCAL_ASSET_DIR")
            or PROJECT_ASSET_FALLBACK
        )
        return LocalAssetVisualProvider(asset_dir, scenes_output_dir)
    # hybrid without durable explicit routing data fails loudly (Phase 6A scope)
    raise ValueError(
        "visual_mode='hybrid' requires explicitly persisted per-scene routing "
        "data, which Phase 6A does not provide yet. Use 'local' or "
        "'google_flow'."
    )