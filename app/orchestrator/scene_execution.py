"""
AUTO Video Maker - Phase 8: provider-agnostic scene execution boundary.

The uniform boundary between central orchestration and the individual visual
providers. Orchestration selects a provider (routing stays an orchestration
concern) and then hands the already-selected provider to this module, which
owns the shared scene execution lifecycle:

    selected provider
        -> resume-or-generate dispatch (never re-submits a submitted remote job)
        -> provider invocation (existing provider contract)
        -> output validation (Phase 7A contract, FFprobe-based)
        -> truthful result

Provider-specific transport, polling, retrieval, media facts and provenance
remain inside the providers. No framework, no new runtime dependency, no
parallel result model: the existing ``SceneGenerationRequest`` /
``SceneGenerationResult`` contract is used verbatim.

Behavioral invariants (frozen by PHASE_8_PLANNING_EXECUTION_ARCHITECTURE.md):

- A checkpoint that references an already-submitted remote job is resumed via
  the provider's ``resume_scene``; it is NEVER submitted again.
- A provider result is not "complete" because the provider said so: the
  artifact must pass ``validate_video_file`` (aspect-ratio aware).
- Failures stay truthful: the boundary never substitutes another provider.
- Audio policy is never decided here (Phase 7D: assembly owns policy).
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.flow.scene_models import (
    FlowGenerationConfig,
    SceneGenerationRequest,
    SceneGenerationResult,
)
from app.orchestrator.exceptions import ValidationError
from app.orchestrator.visual_providers import OPENROUTER_VIDEO_PROVIDER

# Checkpoint keys that carry provider provenance persisted on completion.
# Providers emit these via checkpoint callbacks during generation; the
# completion update re-merges the final values reported by the provider.
PROVENANCE_KEYS = (
    "model",
    "access",
    "job_id",
    "polling_url",
    "estimated_cost",
    "actual_cost",
    "usage",
)


def remote_job_identity(checkpoint: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the already-submitted remote job identity in a checkpoint, if any.

    A checkpoint carries a remote job identity when it holds BOTH a ``job_id``
    and a ``polling_url`` (the durable evidence that a billable remote
    generation was submitted). Such a job must be resumed by polling, never
    submitted again.
    """
    cp = checkpoint or {}
    if cp.get("job_id") and cp.get("polling_url"):
        return {"job_id": cp["job_id"], "polling_url": cp["polling_url"]}
    return None


def provider_wants_resume(provider: Any, checkpoint: Optional[Dict[str, Any]]) -> bool:
    """Decide resume-vs-generate for one scene WITHOUT re-submitting jobs.

    A provider may override the generic remote-job-identity rule by exposing a
    ``wants_resume(checkpoint)`` hook (Google Flow does: its browser lifecycle
    carries additional submitted-state evidence). The hook is provider-owned
    decision logic; the dispatch itself stays here so the central pipeline
    never needs a per-provider resume branch.
    """
    hook = getattr(provider, "wants_resume", None)
    if callable(hook):
        return bool(hook(checkpoint or {}))
    return remote_job_identity(checkpoint) is not None

def invoke_scene_generation(
    *,
    request: SceneGenerationRequest,
    provider: Any,
    checkpoint: Optional[Dict[str, Any]] = None,
    checkpoint_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    require_confirmation: bool = False,
    asset_override: Optional[Path] = None,
    dry_run: Optional[bool] = None,
) -> SceneGenerationResult:
    """Execute one scene through the already-selected provider.

    Uniform dispatch: an already-submitted remote job is resumed through the
    provider's ``resume_scene``; everything else is a fresh
    ``provider.generate_scene`` invocation. Returns the provider's existing
    ``SceneGenerationResult`` truthfully; this function never substitutes a
    provider, retries, or falls back.
    """
    if provider_wants_resume(provider, checkpoint):
        resume_kwargs: Dict[str, Any] = {}
        if asset_override is not None:
            resume_kwargs["asset_override"] = asset_override
        return provider.resume_scene(
            request,
            checkpoint or {},
            checkpoint_callback=checkpoint_callback,
            **resume_kwargs,
        )

    generate_kwargs: Dict[str, Any] = {
        "require_confirmation": require_confirmation,
        "checkpoint_callback": checkpoint_callback,
    }
    if asset_override is not None:
        generate_kwargs["asset_override"] = asset_override
    if dry_run is not None:
        generate_kwargs["dry_run"] = dry_run
    return provider.generate_scene(request, **generate_kwargs)


def validate_scene_artifact(
    result: SceneGenerationResult,
    *,
    aspect_ratio: str,
    validate_video_file: Callable[[Path, Optional[str]], Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Authoritative Phase 7A output gate.

    A provider result is not accepted merely because the provider (or a remote
    job) reported success: the produced artifact must pass the existing
    FFprobe-based validation, including aspect-ratio verification. Raises the
    orchestrator ``ValidationError`` for an invalid artifact; returns the
    validation dict (or ``None`` when the result carries no artifact).
    """
    if result.file_path is None:
        return None
    validation = validate_video_file(Path(result.file_path), aspect_ratio)
    if not validation.get("valid"):
        raise ValidationError(
            "scenes",
            f"Scene {result.scene_id} validation failed: {validation.get('error')}",
            path=str(result.file_path),
        )
    return validation


def completion_checkpoint_update(
    *,
    result: SceneGenerationResult,
    validation: Optional[Dict[str, Any]],
    provider_name: str,
    sequence: int,
    extra_fields: Optional[Dict[str, Any]] = None,
    include_provenance: bool = True,
    audio_facts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Canonical post-validation completion checkpoint fields.

    Shared by every boundary-executed provider so completion state is persisted
    in exactly one shape. Provider-specific extras (e.g. zero-cost
    ``generation_cost``/``source_asset`` provenance) are passed via
    ``extra_fields``; remote-job provenance (model/access/job/cost fields) is
    merged from the provider's verification block when ``include_provenance``
    is set. Never includes secrets — callers only ever pass non-secret fields.
    """
    update: Dict[str, Any] = {
        "provider": provider_name,
        "scene_id": result.scene_id,
        "sequence": sequence,
        "status": "completed",
        "file_path": str(result.file_path) if result.file_path else None,
        "validation": validation,
    }
    if audio_facts is not None:
        update["audio_facts"] = audio_facts
    if include_provenance:
        verification = result.verification or {}
        for key in PROVENANCE_KEYS:
            value = verification.get(key)
            if value is not None:
                update[key] = value
    if extra_fields:
        update.update(extra_fields)
    update["updated_at"] = datetime.now().isoformat()
    return update


def build_scene_request(
    *,
    project_id: str,
    scene: Any,
    provider_name: str,
    scene_config: Optional[Dict[str, Any]] = None,
    aspect_ratio: str = "9:16",
) -> SceneGenerationRequest:
    """Shared request construction for boundary-executed providers.

    Request translation stays deterministic and provider-aware without
    introducing per-provider branches in the central pipeline: remote-model
    providers read their model from the persisted scene config; asset-based
    providers use the neutral ``local_asset`` request model tag (the actual
    asset selection remains provider-owned).
    """
    if provider_name == OPENROUTER_VIDEO_PROVIDER:
        model = (scene_config or {}).get("openrouter_model") or "openrouter_video"
    else:
        model = "local_asset"
    return SceneGenerationRequest(
        project_id=project_id,
        scene_id=scene.id,
        sequence=scene.sequence,
        visual_prompt=scene.visual_prompt,
        config=FlowGenerationConfig(
            model=model,
            aspect_ratio=aspect_ratio,
            duration=max(1, int(scene.estimated_duration) or 4),
            count=1,
            agent_enabled=False,
        ),
    )
