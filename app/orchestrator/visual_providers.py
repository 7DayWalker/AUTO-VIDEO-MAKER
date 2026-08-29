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

import base64
import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.assembly.ffmpeg import probe_media, run_ffmpeg
from app.assembly.audio_contract import AudioMediaFacts
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
from app.orchestrator.openrouter_video import (
    OPENROUTER_VIDEO_PROVIDER,
    OpenRouterVideoVisualProvider,
    default_openrouter_cache_path,
)

VISUAL_MODES = {"local", "google_flow", "hybrid"}
DEFAULT_VISUAL_MODE = "google_flow"

GOOGLE_FLOW_PROVIDER = "google_flow"
LOCAL_ASSET_PROVIDER = "local_asset"
IMAGE_MOTION_PROVIDER = "image_motion"
# Phase 7B: intake route identity for an externally obtained video clip.
# Zero-credit, video-only; reuses LocalAsset normalization behavior verbatim.
EXTERNAL_VIDEO_PROVIDER = "external_video"
# Phase 7C: cost-bearing text-to-video provider backed by the Gemini API.
GEMINI_OMNI_PROVIDER = "gemini_omni"
GEMINI_OMNI_DEFAULT_MODEL = "gemini-omni-1.1-flash"
GEMINI_API_KEY_ENV = "AVM_GEMINI_API_KEY"
GEMINI_OMNI_MODEL_ENV = "AVM_GEMINI_OMNI_MODEL"
GEMINI_OMNI_RESOLUTION_ENV = "AVM_GEMINI_OMNI_RESOLUTION"
GEMINI_OMNI_DEFAULT_RESOLUTION = "720p"

# Providers that never touch Google Flow (Phase 6B cost firewall).
# gemini_omni is cost-bearing and deliberately NOT zero-cost: it must never
# enter zero-cost auto-sourcing behavior (Phase 6F targets image_motion /
# local_asset scenes only).
ZERO_COST_PROVIDERS = {LOCAL_ASSET_PROVIDER, IMAGE_MOTION_PROVIDER, EXTERNAL_VIDEO_PROVIDER}
VALID_PROVIDERS = ZERO_COST_PROVIDERS | {
    GOOGLE_FLOW_PROVIDER,
    GEMINI_OMNI_PROVIDER,
    OPENROUTER_VIDEO_PROVIDER,
}

DEFAULT_HYBRID_PROVIDER = IMAGE_MOTION_PROVIDER

# Fallback root for user-supplied local scene assets.
# Fallback root for user-supplied local scene assets.
PROJECT_ASSET_FALLBACK = Path(__file__).resolve().parents[2] / "assets" / "local"


def zero_cost_missing_asset_error(
    provider_name: str,
    *,
    scene_id: str,
    candidates: List[str],
    asset_dir: Path,
) -> str:
    """Single-sourced truthful wording when a zero-cost provider finds no asset.

    Shared by the providers themselves AND by the Phase 6C asset preflight so
    an early preflight failure reports exactly what the provider would have
    said (byte-identical messages; no second vocabulary for the same problem).
    """
    noun = "image" if provider_name == IMAGE_MOTION_PROVIDER else "asset"
    return (
        f"No local {noun} found for scene '{scene_id}' "
        f"(searched: {', '.join(candidates)}) in {asset_dir}"
    )
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
        asset_override: Optional[Path] = None,
    ) -> SceneGenerationResult:
        del require_confirmation  # interface parity only

        def _fail(error: str) -> SceneGenerationResult:
            result = SceneGenerationResult.failure(
                request.scene_id, request.sequence, error
            )
            result.verification["provider"] = LOCAL_ASSET_PROVIDER
            return result

        source = Path(asset_override) if asset_override else self.resolve_asset(request)
        if source is None:
            return _fail(
                zero_cost_missing_asset_error(
                    LOCAL_ASSET_PROVIDER,
                    scene_id=request.scene_id,
                    candidates=self._candidate_names(request),
                    asset_dir=self.asset_dir,
                )
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
        try:
            proc = run_ffmpeg(args, description=f"LocalAsset normalize {source.name}")
            failed = proc.returncode != 0
            stderr_tail = (getattr(proc, "stderr", "") or "")[-300:]
        except Exception as exc:  # noqa: BLE001 - FFmpegExecutionError etc.
            failed = True
            stderr_tail = str(exc)[-300:]
        if failed:
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
            audio_facts=AudioMediaFacts.model_validate(
                {**validation.get("audio_facts", {}), "is_synthetic": not info.has_audio}
            ),
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
        asset_override: Optional[Path] = None,
    ) -> SceneGenerationResult:
        """Local scenes are deterministic; resume == regenerate/validate."""
        return self.generate_scene(
            request,
            require_confirmation=False,
            checkpoint_callback=checkpoint_callback,
            asset_override=asset_override,
        )


# ---------------------------------------------------------------------------
# Phase 7B — external video intake route
# ---------------------------------------------------------------------------

class ExternalVideoVisualProvider(LocalAssetVisualProvider):
    """Zero-credit intake route for an EXTERNALLY OBTAINED video clip.

    NOT a generation provider: it never calls an API, never opens a browser,
    and never consumes credits. It does not know (or record) where the clip
    came from — origin provenance lives in the Phase 6C asset manifest
    (``supplied_by`` / ``source_provider`` / ``source_status``).

    Video-only by contract: an image mapped to an ``external_video`` scene
    fails honestly at the Phase 6C media-type gate, and this provider refuses
    image sources directly as defense in depth. Normalization behavior is
    inherited verbatim from :class:`LocalAssetVisualProvider` — no duplicated
    FFmpeg logic.
    """

    name = EXTERNAL_VIDEO_PROVIDER

    # Video-only identity: no image discovery candidates for this route.
    _IMAGE_EXTS: tuple = ()

    def generate_scene(
        self,
        request: SceneGenerationRequest,
        *,
        require_confirmation: bool = False,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        asset_override: Optional[Path] = None,
    ) -> SceneGenerationResult:
        # Defense in depth: refuse non-video sources even if one is passed
        # directly (the Phase 6C media-type gate already blocks images upstream).
        source = Path(asset_override) if asset_override else self.resolve_asset(request)
        if source is not None and source.suffix.lower() not in self._VIDEO_EXTS:
            result = SceneGenerationResult.failure(
                request.scene_id,
                request.sequence,
                (
                    "external_video requires a video asset; "
                    f"'{source.name}' is not a supported video file"
                ),
            )
            result.verification["provider"] = EXTERNAL_VIDEO_PROVIDER
            return result

        # Truthful execution identity inside checkpoint callbacks too.
        def _tagged_checkpoint(update: Dict[str, Any]) -> None:
            if checkpoint_callback is not None:
                checkpoint_callback({**update, "provider": EXTERNAL_VIDEO_PROVIDER})

        result = super().generate_scene(
            request,
            require_confirmation=require_confirmation,
            checkpoint_callback=_tagged_checkpoint,
            asset_override=asset_override,
        )
        # Truthful execution identity on success AND failure.
        result.verification["provider"] = EXTERNAL_VIDEO_PROVIDER
        if result.success:
            result.verification["classification"] = "zero_cost_external_video"
        return result


# ---------------------------------------------------------------------------
# Phase 7C — Gemini Omni text-to-video provider (cost-bearing)
# ---------------------------------------------------------------------------

class _GeminiRetrievalError(Exception):
    """Retrieval/polling failure. NEVER triggers a second billable submission."""


class GeminiOmniVisualProvider:
    """Cost-bearing text-to-video provider backed by the Gemini API.

    Submits ONE billable generation per ``generate_scene()`` invocation
    (``client.interactions.create``). Retrieval/polling retries are safe
    (non-billable); generation submission is NEVER automatically retried.

    Output delivery per the official SDK contract (google-genai >= 2.x):
    - inline: ``Interaction.output_video.data`` (base64 or bytes) —
      materialized directly;
    - URI: ``Interaction.output_video.uri`` — polled via
      ``client.files.get`` until ACTIVE (FAILED = truthful failure), then
      ``client.files.download``.

    Resume semantics: there is NO invented remote-job resumption. The
    pipeline-level completed-and-validated reuse check is authoritative; if
    the output is missing/corrupt, ``resume_scene`` regenerates through the
    normal generation path, which is a NEW billable submission.

    Output follows the Phase 7A contract exactly: a validated MP4 at
    ``<output_dir>/<scene_id>.mp4`` validated by ``validate_video_file()``.
    A Gemini response that does not satisfy the AVM contract fails
    truthfully — the provider performs NO normalization/transcoding.
    """

    name = GEMINI_OMNI_PROVIDER
    requires_browser = False
    classification = "cost_bearing_text_to_video"

    # Retrieval polling (URI delivery / async interaction completion).
    _poll_interval_seconds = 2.0
    _poll_max_attempts = 60

    # Duck-typed members required by the pipeline's legacy asset-discovery
    # helper. The Gemini route owns generation and never resolves local
    # assets, so discovery is inert by design.
    asset_dir = None

    def __init__(
        self,
        output_dir: Path,
        *,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        resolution: Optional[str] = None,
        poll_interval: Optional[float] = None,
        poll_max_attempts: Optional[int] = None,
    ):
        self.output_dir = Path(output_dir)
        self.model = (
            model or os.getenv(GEMINI_OMNI_MODEL_ENV) or GEMINI_OMNI_DEFAULT_MODEL
        )
        self.resolution = (
            resolution
            or os.getenv(GEMINI_OMNI_RESOLUTION_ENV)
            or GEMINI_OMNI_DEFAULT_RESOLUTION
        )
        self._api_key = (
            api_key if api_key is not None else os.getenv(GEMINI_API_KEY_ENV, "")
        )
        if poll_interval is not None:
            self._poll_interval_seconds = poll_interval
        if poll_max_attempts is not None:
            self._poll_max_attempts = poll_max_attempts
        self._client = None


    # -- legacy-discovery duck-typing (inert for this provider) --------------
    def resolve_asset(self, request) -> Optional[Path]:
        return None

    def _candidate_names(self, request):
        return []

    # -- client lifecycle ----------------------------------------------------
    def _get_client(self):
        """Build the official Gemini client. No network traffic here."""
        if self._client is None:
            if not self._api_key:
                raise RuntimeError(
                    f"missing Gemini API key (set {GEMINI_API_KEY_ENV})"
                )
            try:
                from google import genai  # official SDK, imported lazily
            except ImportError as exc:  # SDK unavailable -> truthful failure
                raise RuntimeError(
                    "the official google-genai SDK is not installed"
                ) from exc
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def is_available(self) -> bool:
        """Configuration-only availability check. No network calls."""
        if not self._api_key:
            return False
        try:
            self._get_client()
            return True
        except Exception:  # noqa: BLE001 - unavailable, never a crash
            return False

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(seconds)

    @staticmethod
    def _field(obj: Any, name: str) -> Any:
        """Read a field from an SDK object or plain dict, tolerantly."""
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    @staticmethod
    def _enum_value(value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(getattr(value, "value", value))

    @staticmethod
    def _video_content(interaction: Any) -> Any:
        content = GeminiOmniVisualProvider._field(interaction, "output_video")
        if isinstance(content, (list, tuple)):
            return content[0] if content else None
        return content

    def _fail(
        self,
        request: SceneGenerationRequest,
        error: str,
        *,
        interaction_id: Optional[str] = None,
        delivery: Optional[str] = None,
    ) -> SceneGenerationResult:
        result = SceneGenerationResult.failure(
            request.scene_id, request.sequence, error
        )
        result.verification.update(
            {
                "provider": GEMINI_OMNI_PROVIDER,
                "model": self.model,
                "classification": self.classification,
            }
        )
        if interaction_id:
            result.verification["interaction_id"] = interaction_id
        if delivery:
            result.verification["output_delivery"] = delivery
        return result


    # -- retrieval (non-billable) --------------------------------------------
    def _extract_video(self, interaction: Any):
        """Return (kind, content) where kind is 'inline' | 'uri' | None."""
        content = self._video_content(interaction)
        if content is None:
            return None, None
        if self._field(content, "data") is not None:
            return "inline", content
        if self._field(content, "uri") is not None:
            return "uri", content
        return None, content

    def _interaction_failed(self, interaction: Any) -> Optional[str]:
        status = (self._enum_value(self._field(interaction, "status")) or "").lower()
        if "fail" in status:
            errors = self._field(interaction, "errors")
            return f"Gemini Omni interaction failed (status={status}; errors={errors})"
        return None

    def _await_interaction_video(self, client: Any, interaction: Any) -> Any:
        """Poll a still-processing interaction. Retrieval-only (non-billable)."""
        interaction_id = self._field(interaction, "id")
        if not interaction_id:
            return None
        for _ in range(self._poll_max_attempts):
            self._sleep(self._poll_interval_seconds)
            fetched = client.interactions.get(id=interaction_id)
            failed = self._interaction_failed(fetched)
            if failed:
                raise _GeminiRetrievalError(failed)
            kind, content = self._extract_video(fetched)
            if kind is not None:
                return content
        raise _GeminiRetrievalError(
            "Gemini Omni interaction produced no video output after "
            f"{self._poll_max_attempts} polls"
        )

    def _download_via_files(self, client: Any, uri: str) -> bytes:
        """Poll file state to ACTIVE, then download. Retrieval-only."""
        for _ in range(self._poll_max_attempts):
            file = client.files.get(name=uri)
            state = (
                self._enum_value(self._field(file, "state")) or ""
            ).upper()
            if state == "ACTIVE":
                return client.files.download(file=uri)
            if state == "FAILED":
                error = self._field(file, "error")
                raise _GeminiRetrievalError(
                    f"Gemini Omni file processing failed (state=FAILED; error={error})"
                )
            self._sleep(self._poll_interval_seconds)
        raise _GeminiRetrievalError(
            f"Gemini Omni file did not become ACTIVE after "
            f"{self._poll_max_attempts} polls"
        )

    def _retrieve_video(self, client: Any, interaction: Any):
        """Return (delivery, video_bytes, error_detail).

        Never re-submits a generation: every failure here is retrieval-side.
        """
        failed = self._interaction_failed(interaction)
        if failed:
            return None, None, failed
        kind, content = self._extract_video(interaction)
        if kind is None:
            content = self._await_interaction_video(client, interaction)
            if content is None:
                return None, None, "Gemini Omni response contained no video output"
            kind, _ = self._extract_video(content)
        if kind == "inline":
            data = self._field(content, "data")
            raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
            return "inline", raw, None
        if kind == "uri":
            uri = self._field(content, "uri")
            return "uri", self._download_via_files(client, uri), None
        return None, None, (
            "Gemini Omni video output had neither inline data nor a URI"
        )


    # -- provider protocol ---------------------------------------------------
    def generate_scene(
        self,
        request: SceneGenerationRequest,
        *,
        require_confirmation: bool = False,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        asset_override: Optional[Path] = None,
    ) -> SceneGenerationResult:
        del require_confirmation  # interface parity only
        del asset_override  # text-to-video: intent comes from the request

        # Cost-safety gate: no network call without configuration.
        if not self._api_key:
            return self._fail(
                request,
                "Gemini Omni provider is not configured: set "
                f"{GEMINI_API_KEY_ENV} (no generation call was made).",
            )
        try:
            client = self._get_client()
        except Exception as exc:  # noqa: BLE001 - truthful unavailability
            return self._fail(request, f"Gemini Omni provider is unavailable: {exc}")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"{request.scene_id}.mp4"
        aspect = request.config.aspect_ratio

        # THE billable submission. Exactly one per invocation.
        try:
            interaction = client.interactions.create(
                model=self.model,
                input=request.visual_prompt,
                response_format={
                    "type": "video",
                    "aspect_ratio": aspect,
                    "resolution": self.resolution,
                },
            )
        except Exception as exc:  # noqa: BLE001 - API rejection / network
            return self._fail(request, f"Gemini Omni generation request failed: {exc}")

        interaction_id = self._field(interaction, "id")

        # Retrieval phase — failures here never resubmit.
        try:
            delivery, payload, detail = self._retrieve_video(client, interaction)
        except _GeminiRetrievalError as exc:
            return self._fail(request, str(exc), interaction_id=interaction_id)
        except Exception as exc:  # noqa: BLE001 - surfaced truthfully
            return self._fail(
                request,
                f"Gemini Omni video retrieval failed: {exc}",
                interaction_id=interaction_id,
            )
        if delivery is None:
            return self._fail(request, detail, interaction_id=interaction_id)

        # Materialize via safe temp file, then validate (Phase 7A contract).
        temp_path = output_path.with_name(output_path.name + ".part")
        try:
            temp_path.write_bytes(payload)
            validation = validate_video_file(temp_path, aspect)
            if not validation.get("valid"):
                temp_path.unlink(missing_ok=True)
                return self._fail(
                    request,
                    f"Gemini Omni output failed scene validation: "
                    f"{validation.get('error')}",
                    interaction_id=interaction_id,
                    delivery=delivery,
                )
            os.replace(temp_path, output_path)  # atomic on same volume
        except Exception as exc:  # noqa: BLE001 - surfaced truthfully
            temp_path.unlink(missing_ok=True)
            return self._fail(
                request,
                f"Gemini Omni output materialization failed: {exc}",
                interaction_id=interaction_id,
                delivery=delivery,
            )


        result = SceneGenerationResult.success_result(
            scene_id=request.scene_id,
            sequence=request.sequence,
            file_path=output_path,
            file_size=validation.get("file_size", output_path.stat().st_size),
            actual_duration=validation.get("duration", 0.0),
            width=validation.get("width", 0),
            height=validation.get("height", 0),
            codec=validation.get("codec", "h264"),
            generation_cost=None,  # never fabricate cost values
            downloaded_at=datetime.now(timezone.utc).isoformat(),
            warnings=[f"model={self.model}", f"output_delivery={delivery}"],
            audio_facts=AudioMediaFacts.model_validate(validation.get("audio_facts", {})),
        )
        result.verification.update(
            {
                "provider": GEMINI_OMNI_PROVIDER,
                "model": self.model,
                "classification": self.classification,
                "output_delivery": delivery,
                "resolution": self.resolution,
            }
        )
        if interaction_id:
            result.verification["interaction_id"] = interaction_id

        if checkpoint_callback is not None:
            checkpoint_callback(
                {
                    "scene_id": request.scene_id,
                    "provider": GEMINI_OMNI_PROVIDER,
                    "status": "completed",
                    "generation_cost": None,
                    "interaction_id": interaction_id,
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
        asset_override: Optional[Path] = None,
    ) -> SceneGenerationResult:
        """Conform to the provider protocol.

        The current Gemini API contract offers no remote-job resumption for
        this flow. The pipeline's completed-and-validated reuse check is
        authoritative; reaching this method means the output was missing or
        invalid, so this is a NEW billable generation event through the normal
        generation path.
        """
        return self.generate_scene(
            request,
            require_confirmation=False,
            checkpoint_callback=checkpoint_callback,
            asset_override=asset_override,
        )


# ---------------------------------------------------------------------------
# Step 6B — durable hybrid routing
# ---------------------------------------------------------------------------

def normalize_provider(value: Optional[str]) -> str:
    provider = (value or "").strip().lower()
    if provider not in VALID_PROVIDERS:
        raise ValueError(
            f"Invalid visual provider '{value}'. Valid: {sorted(VALID_PROVIDERS)}"
        )
    return provider


def resolve_scene_provider(
    scene_id: str,
    *,
    visual_mode: str,
    scene_config: Dict[str, Any],
) -> str:
    """Resolve ONE scene's provider from durable project state.

    Resolution order:
      1. explicit persisted entry in ``scene_provider_routing[scene_id]``
      2. hybrid ``default_provider`` (never google_flow)
      3. mode defaults: google_flow -> google_flow; local -> local_asset

    In hybrid mode an unresolved scene can NEVER default to google_flow.
    """
    mode = normalize_visual_mode(visual_mode)
    if mode == GOOGLE_FLOW_PROVIDER:
        return GOOGLE_FLOW_PROVIDER
    if mode == "local":
        return LOCAL_ASSET_PROVIDER

    # hybrid
    explicit = ((scene_config or {}).get("scene_provider_routing") or {}).get(scene_id)
    if isinstance(explicit, dict) and explicit.get("provider"):
        return normalize_provider(explicit["provider"])
    if isinstance(explicit, str) and explicit:
        return normalize_provider(explicit)
    return DEFAULT_HYBRID_PROVIDER


def build_hybrid_routing(
    plan_scenes: List[Any],
    *,
    scene_config: Dict[str, Any],
    cli_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build the complete authoritative routing map for a hybrid project.

    Called BEFORE any generation. Precedence: CLI overrides > persisted
    explicit entries > hybrid default (image_motion). Flow is reachable only
    through an explicit override.
    """
    routing: Dict[str, Dict[str, Any]] = {}
    existing = (scene_config or {}).get("scene_provider_routing") or {}
    for scene in sorted(plan_scenes, key=lambda s: s.sequence):
        provider = DEFAULT_HYBRID_PROVIDER
        reason = "hybrid default"
        prior = existing.get(scene.id)
        if isinstance(prior, dict) and prior.get("provider"):
            provider = normalize_provider(prior["provider"])
            reason = prior.get("reason", "persisted routing")
        override = (cli_overrides or {}).get(scene.id)
        if override:
            provider = normalize_provider(override)
            reason = "explicit CLI/config override"
        routing[scene.id] = {"provider": provider, "reason": reason}
    return routing


# ---------------------------------------------------------------------------
# Step 6B — ImageMotionVisualProvider
# ---------------------------------------------------------------------------

_MOTION_PRESETS = (
    "slow_zoom_in",
    "slow_zoom_out",
    "pan_left_to_right",
    "pan_right_to_left",
)


def motion_preset_for_scene(scene_id: str) -> str:
    """Deterministic preset selection: same scene id => same preset forever."""
    digest = hashlib.sha256(scene_id.encode("utf-8")).hexdigest()
    return _MOTION_PRESETS[int(digest, 16) % len(_MOTION_PRESETS)]


def _motion_filter(preset: str, frames: int, width: int, height: int) -> str:
    """Build the zoompan filter string for a deterministic motion preset."""
    upscale_w, upscale_h = width * 2, height * 2  # headroom: zoom never upscales
    center_x = "iw/2-(iw/zoom/2)"
    center_y = "ih/2-(ih/zoom/2)"
    last = max(1, frames - 1)
    if preset == "slow_zoom_in":
        z = f"min(1+0.12*on/{last},1.12)"
        x, y = center_x, center_y
    elif preset == "slow_zoom_out":
        z = f"if(eq(on,1),1.12,max(1.12-0.12*on/{last},1.001))"
        x, y = center_x, center_y
    elif preset == "pan_left_to_right":
        z = "1.15"
        x = f"(iw-iw/zoom)*on/{last}"
        y = center_y
    else:  # pan_right_to_left
        z = "1.15"
        x = f"(iw-iw/zoom)*(1-on/{last})"
        y = center_y
    return (
        f"scale={upscale_w}:{upscale_h}:force_original_aspect_ratio=increase,"
        f"crop={upscale_w}:{upscale_h},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps=30,"
        "format=yuv420p"
    )


class ImageMotionVisualProvider:
    """Zero-credit provider: still image -> deterministic Ken Burns/pan MP4.

    Input discovery mirrors LocalAssetVisualProvider but restricted to safe
    still-image formats (jpg/jpeg/png/webp). No downloads, no search.
    """

    name = IMAGE_MOTION_PROVIDER
    requires_browser = False

    _IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")

    def __init__(self, asset_dir: Path, output_dir: Path):
        self.asset_dir = Path(asset_dir)
        self.output_dir = Path(output_dir)

    def is_available(self) -> bool:
        return self.asset_dir.is_dir()

    def _candidate_names(self, request: SceneGenerationRequest) -> List[str]:
        names: List[str] = []
        stem = f"{request.sequence:03d}"
        for ext in self._IMAGE_EXTS:
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

    def generate_scene(
        self,
        request: SceneGenerationRequest,
        *,
        require_confirmation: bool = False,  # zero-cost: nothing to confirm
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        asset_override: Optional[Path] = None,
    ) -> SceneGenerationResult:
        del require_confirmation  # interface parity only

        def _fail(error: str) -> SceneGenerationResult:
            result = SceneGenerationResult.failure(
                request.scene_id, request.sequence, error
            )
            result.verification["provider"] = IMAGE_MOTION_PROVIDER
            return result

        source = Path(asset_override) if asset_override else self.resolve_asset(request)
        if source is None:
            return _fail(
                zero_cost_missing_asset_error(
                    IMAGE_MOTION_PROVIDER,
                    scene_id=request.scene_id,
                    candidates=self._candidate_names(request),
                    asset_dir=self.asset_dir,
                )
            )

        try:
            probe_media(source)
        except Exception as exc:  # noqa: BLE001 - corrupt/unreadable image
            return _fail(f"Invalid or unreadable source image: {exc}")

        target_duration = max(1, int(getattr(request.config, "duration", 4)))
        width, height = 1080, 1920
        aspect = getattr(request.config, "aspect_ratio", "9:16")
        if aspect == "16:9":
            width, height = 1920, 1080
        elif aspect == "1:1":
            width = height = 1080

        preset = motion_preset_for_scene(request.scene_id)
        frames = max(2, target_duration * 30)

        output_path = self.output_dir / f"{request.scene_id}.mp4"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        args: List[str] = [
            "-y",
            "-i", str(source),
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-vf", _motion_filter(preset, frames, width, height),
            "-t", str(target_duration),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            str(output_path),
        ]
        try:
            proc = run_ffmpeg(args, description=f"ImageMotion {preset} {source.name}")
            failed = proc.returncode != 0
            stderr_tail = (getattr(proc, "stderr", "") or "")[-300:]
        except Exception as exc:  # noqa: BLE001 - FFmpegExecutionError etc.
            failed = True
            stderr_tail = str(exc)[-300:]
        if failed:
            return _fail(f"FFmpeg image-motion failed ({preset}): {stderr_tail}")

        validation = validate_video_file(output_path, request.config.aspect_ratio)
        if not validation.get("valid"):
            return _fail(
                f"Image-motion output failed validation: {validation.get('error')}"
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
            warnings=[f"source_image={source.name}", f"motion_preset={preset}"],
            audio_facts=AudioMediaFacts.model_validate(
                {**validation.get("audio_facts", {}), "is_synthetic": True}
            ),
        )
        result.verification.update(
            {
                "provider": IMAGE_MOTION_PROVIDER,
                "asset_reference": str(source),
                "motion_preset": preset,
                "classification": "zero_cost_image_motion",
            }
        )

        if checkpoint_callback is not None:
            checkpoint_callback(
                {
                    "scene_id": request.scene_id,
                    "provider": IMAGE_MOTION_PROVIDER,
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
        asset_override: Optional[Path] = None,
    ) -> SceneGenerationResult:
        """Deterministic rendering: resume == regenerate/validate."""
        return self.generate_scene(
            request,
            require_confirmation=False,
            checkpoint_callback=checkpoint_callback,
            asset_override=asset_override,
        )


def create_visual_provider(
    mode: str,
    *,
    scene_config: Dict[str, Any],
    scenes_output_dir: Path,
    flow_project_url: Optional[str] = None,
    local_asset_dir: Optional[Path] = None,
    provider_name: Optional[str] = None,
):
    """Deterministic provider factory.

    Cost firewall properties:
    - ``local`` mode can never construct FlowSceneGenerator.
    - In ``hybrid`` mode, ``provider_name`` comes from the PERSISTED routing
      map; a zero-cost provider name never constructs FlowSceneGenerator.
    """
    mode = normalize_visual_mode(mode)

    # Scene-level provider override (hybrid only).
    if provider_name is not None and mode == "hybrid":
        provider = normalize_provider(provider_name)
        if provider == GOOGLE_FLOW_PROVIDER:
            return GoogleFlowVisualProvider(
                scene_config, scenes_output_dir, flow_project_url
            )
        if provider == LOCAL_ASSET_PROVIDER:
            asset_dir = Path(
                local_asset_dir
                or os.getenv("AVM_LOCAL_ASSET_DIR")
                or PROJECT_ASSET_FALLBACK
            )
            return LocalAssetVisualProvider(asset_dir, scenes_output_dir)
        if provider == IMAGE_MOTION_PROVIDER:
            image_dir = Path(
                os.getenv("AVM_IMAGE_ASSET_DIR")
                or local_asset_dir
                or PROJECT_ASSET_FALLBACK
            )
            return ImageMotionVisualProvider(image_dir, scenes_output_dir)
        if provider == EXTERNAL_VIDEO_PROVIDER:
            asset_dir = Path(
                local_asset_dir
                or os.getenv("AVM_LOCAL_ASSET_DIR")
                or PROJECT_ASSET_FALLBACK
            )
            return ExternalVideoVisualProvider(asset_dir, scenes_output_dir)
        if provider == GEMINI_OMNI_PROVIDER:
            # Configuration (key/model/resolution) comes from the environment
            # inside the provider; construction never touches the network.
            return GeminiOmniVisualProvider(scenes_output_dir)
        if provider == OPENROUTER_VIDEO_PROVIDER:
            # OpenRouter configuration is read without network access. The
            # provider performs discovery only when a scene is actually run.
            return OpenRouterVideoVisualProvider(
                scenes_output_dir,
                model=scene_config.get("openrouter_model"),
                resolution=scene_config.get("openrouter_resolution"),
                allow_paid=scene_config.get("openrouter_allow_paid"),
                allow_paid_fallback=scene_config.get("openrouter_allow_paid_fallback"),
                cache_path=(
                    scene_config.get("openrouter_model_cache")
                    or default_openrouter_cache_path()
                ),
                generate_audio=scene_config.get("openrouter_generate_audio"),
            )

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
    # hybrid without durable explicit routing data fails loudly (Phase 6A scope;
    # the scenes stage resolves per-scene providers before reaching this point
    # for zero-cost scenes, and passes provider_name for Flow-routed scenes).
    raise ValueError(
        "visual_mode='hybrid' requires explicitly persisted per-scene routing "
        "data. Route every scene explicitly, or use 'local'/'google_flow'."
    )