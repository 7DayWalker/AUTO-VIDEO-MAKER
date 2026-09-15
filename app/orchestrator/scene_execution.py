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

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from app.flow.scene_models import (
    FlowGenerationConfig,
    SceneGenerationRequest,
    SceneGenerationResult,
)
from app.orchestrator.exceptions import ValidationError
from app.orchestrator.visual_providers import (
    OPENROUTER_VIDEO_PROVIDER,
    ZERO_COST_PROVIDERS,
)
from app.providers.fallback_policy import FailureCategory

# Batch 1I: runtime diagnostic visibility at the capture seam. The durable
# provenance/accounting records remain the authoritative truth (Batch 1F/1H);
# logging only confirms that capture occurred, with compact context.
logger = logging.getLogger(__name__)

# Checkpoint keys that carry provider provenance persisted on completion.
# Providers emit these via checkpoint callbacks during generation; the
# completion update re-merges the final values reported by the provider.
# Batch 1C: ``task_id`` is the Alibaba DashScope remote-task identity.
# Batch 1F: the actual-provider/fallback annotation keys are merged too so a
# classified Alibaba -> OpenRouter fallback is no longer lost at completion.
# The native remote identities (``task_id`` / ``job_id``) are preserved
# verbatim; the generic artifact-level ``request_id`` mapping lives only in
# the provenance block produced by :func:`build_scene_provenance`.
PROVENANCE_KEYS = (
    "model",
    "access",
    "job_id",
    "polling_url",
    "task_id",
    "estimated_cost",
    "actual_cost",
    "usage",
    "provider",
    "final_provider",
    "primary_provider",
    "fallback_used",
    "fallback_reason",
    "reuse",
    "currency",
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


def build_scene_provenance(
    result: SceneGenerationResult,
    *,
    selected_provider: Optional[str] = None,
) -> Dict[str, Any]:
    """Batch 1F: generic artifact-level ACTUAL execution provenance.

    Answers "what actually generated this delivered artifact?" using ONLY the
    provider result's ``verification`` data (what the provider reported) with
    the routed/selected provider as a last-resort identity fallback. Unknown
    facts are omitted — never fabricated:

    - ``provider`` is the ACTUAL provider that delivered the artifact. When a
      classified fallback succeeded, the wrapper's ``final_provider`` wins
      over the provider's own ``provider`` (both are the fallback provider in
      that case, but the wrapper value is the explicit final annotation).
    - ``model`` is the ACTUAL model reported by the provider result. A
      configured model is configuration evidence and is never substituted.
    - ``request_id`` maps the provider-native remote identity (Alibaba
      ``task_id`` / OpenRouter ``job_id``) onto one generic field, and
      ``request_id_kind`` records which native vocabulary it came from. The
      native keys themselves stay untouched in the operational checkpoint.
    - ``fallback_used`` / ``primary_provider`` / ``fallback_reason`` are
      present only when the provider (or fallback wrapper) actually reported
      them. An absent ``fallback_used`` means UNKNOWN — never false.
    - ``source`` is ``"reused"`` only when the provider explicitly marked the
      result as a reuse of an existing valid artifact.
    """
    verification = result.verification or {}
    actual_provider = (
        verification.get("final_provider")
        or verification.get("provider")
        or selected_provider
    )
    provenance: Dict[str, Any] = {}
    if actual_provider:
        provenance["provider"] = actual_provider
    model = verification.get("model")
    if model:
        provenance["model"] = model
    if verification.get("task_id"):
        provenance["request_id"] = verification["task_id"]
        provenance["request_id_kind"] = "task_id"
    elif verification.get("job_id"):
        provenance["request_id"] = verification["job_id"]
        provenance["request_id_kind"] = "job_id"
    fallback_used = verification.get("fallback_used")
    if fallback_used is not None:
        provenance["fallback_used"] = bool(fallback_used)
    primary_provider = verification.get("primary_provider")
    if primary_provider:
        provenance["primary_provider"] = primary_provider
    fallback_reason = verification.get("fallback_reason")
    if fallback_reason is not None and fallback_reason != "":
        provenance["fallback_reason"] = fallback_reason
    provenance["source"] = "reused" if verification.get("reuse") else "generated"
    return provenance


# ---------------------------------------------------------------------------
# Batch 1H — provider accounting (generic; provider-agnostic)
#
# Provenance (above) answers WHO/WHAT EXECUTED. Accounting answers WHAT WAS
# CONSUMED / BILLED. They share request identities but are never conflated.
# There is deliberately NO pricing table and NO monetary fabrication here:
# a billable operation without cost evidence stays cost=unknown, and zero is
# recorded only where the execution seam can establish it as a real fact
# (non-billable local execution, reuse, provider-reported zero).
# ---------------------------------------------------------------------------

BILLING_STATUS_BILLABLE = "billable"
BILLING_STATUS_NON_BILLABLE = "non_billable"
BILLING_STATUS_UNKNOWN = "unknown"

COST_SOURCE_PROVIDER_REPORTED = "provider_reported"
COST_SOURCE_AVM_CALCULATED = "avm_calculated"
COST_SOURCE_NONE = "none"

# Classified failure categories that imply the primary provider actually
# created a remote operation (task accepted, then failed) versus never
# consuming anything. A network failure during submission is genuinely
# uncertain (the request may or may not have landed).
_SUBMITTED_FAILURE_CATEGORIES = frozenset({FailureCategory.TASK_FAILURE})
_UNCERTAIN_SUBMISSION_CATEGORIES = frozenset({FailureCategory.NETWORK})


def build_operation_accounting(
    *,
    provider: Optional[str],
    model: Optional[str] = None,
    request_id: Optional[str] = None,
    request_id_kind: Optional[str] = None,
    billing_status: str = BILLING_STATUS_UNKNOWN,
    usage: Optional[Mapping[str, Any]] = None,
    cost: Any = None,
    cost_source: str = COST_SOURCE_NONE,
    currency: Optional[str] = None,
    source: str = "generated",
    estimated_cost: Any = None,
) -> Dict[str, Any]:
    """One generic provider-operation accounting record (Batch 1H).

    ``usage`` carries provider-native consumption quantities ONLY (never a
    monetary value). ``cost`` carries monetary evidence verbatim in the
    provider's own representation/precision, or ``None`` (unknown) — it is
    never fabricated and never silently zeroed. ``cost_source`` states where
    the cost evidence came from. No provider-specific fields exist here.
    """
    return {
        "provider": provider,
        "model": model,
        "request_id": request_id,
        "request_id_kind": request_id_kind,
        "billing_status": billing_status,
        "usage": dict(usage) if isinstance(usage, Mapping) else {},
        "cost": cost,
        "cost_source": cost_source,
        "currency": currency,
        "source": source,
        **({"estimated_cost": estimated_cost} if estimated_cost is not None else {}),
    }


def _accounting_request_identity(
    verification: Mapping[str, Any],
) -> tuple:
    if verification.get("task_id"):
        return verification["task_id"], "task_id"
    if verification.get("job_id"):
        return verification["job_id"], "job_id"
    return None, None

def _build_primary_attempt_accounting(
    verification: Mapping[str, Any],
    checkpoint: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Accounting for the FAILED primary attempt of a classified fallback.

    The final result's verification carries the primary's provider and the
    classified failure reason; the operational checkpoint carries the
    primary's native request identity (the identity kind that differs from
    the final attempt's). Usage/cost for a failed attempt stay unknown
    unless real evidence exists — never invented, never zeroed.
    """
    category = verification.get("fallback_reason")
    if category in _SUBMITTED_FAILURE_CATEGORIES:
        billing_status = BILLING_STATUS_BILLABLE
    elif category in _UNCERTAIN_SUBMISSION_CATEGORIES:
        billing_status = BILLING_STATUS_UNKNOWN
    else:
        # No remote operation was consumed (rejected before/at submission,
        # capability gap, configuration, moderation, ...).
        billing_status = BILLING_STATUS_NON_BILLABLE

    request_id = request_id_kind = None
    cp = checkpoint or {}
    _final_request_id, final_kind = _accounting_request_identity(verification)
    if final_kind == "job_id" and cp.get("task_id"):
        request_id, request_id_kind = cp.get("task_id"), "task_id"
    elif final_kind == "task_id" and cp.get("job_id"):
        request_id, request_id_kind = cp.get("job_id"), "job_id"

    return build_operation_accounting(
        provider=verification.get("primary_provider"),
        model=None,
        request_id=request_id,
        request_id_kind=request_id_kind,
        billing_status=billing_status,
        usage=None,
        cost=None,
        cost_source=COST_SOURCE_NONE,
        currency=None,
        source="generated",
    )


def summarize_accounting_attempts(
    attempts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Derive the scene-level accounting summary from attempt records.

    Zero/unknown semantics are preserved exactly:

    - every attempt known non-billable       -> cost is a real 0;
    - any billable attempt with unknown cost -> cost stays unknown
      (``partial=True``); it is NEVER silently zeroed;
    - exactly one billable attempt with a known cost -> that provider-reported
      value verbatim (no arithmetic on provider money in this batch);
    - multiple known costs                   -> aggregation is deferred to the
      future AVM economics layer (cost stays unknown; attempts retain the
      evidence).
    """
    billable = [
        a for a in attempts if a.get("billing_status") == BILLING_STATUS_BILLABLE
    ]
    non_billable = [
        a for a in attempts if a.get("billing_status") == BILLING_STATUS_NON_BILLABLE
    ]
    unknown_status = [
        a for a in attempts if a.get("billing_status") == BILLING_STATUS_UNKNOWN
    ]

    if not attempts:
        billing_status = BILLING_STATUS_UNKNOWN
    elif billable:
        billing_status = BILLING_STATUS_BILLABLE
    elif not unknown_status:
        billing_status = BILLING_STATUS_NON_BILLABLE
    else:
        billing_status = BILLING_STATUS_UNKNOWN

    known_cost_attempts = [a for a in billable if a.get("cost") is not None]
    if not billable and non_billable and not unknown_status:
        cost: Any = 0
        cost_source = COST_SOURCE_NONE
        currency = None
    elif len(known_cost_attempts) == 1 and len(known_cost_attempts) == len(billable):
        source_attempt = known_cost_attempts[0]
        cost = source_attempt.get("cost")
        cost_source = source_attempt.get("cost_source", COST_SOURCE_NONE)
        currency = source_attempt.get("currency")
    else:
        cost = None
        cost_source = COST_SOURCE_NONE
        currency = None

    summary: Dict[str, Any] = {
        "attempt_count": len(attempts),
        "billable_attempt_count": len(billable),
        "non_billable_attempt_count": len(non_billable),
        "unknown_billing_attempt_count": len(unknown_status),
        "billing_status": billing_status,
        "cost": cost,
        "cost_source": cost_source,
        "currency": currency,
        "partial": billing_status == BILLING_STATUS_BILLABLE and cost is None,
    }
    if attempts:
        final = attempts[-1]
        summary["final_provider"] = final.get("provider")
        summary["final_request_id"] = final.get("request_id")
        summary["final_request_id_kind"] = final.get("request_id_kind")
        summary["final_usage"] = final.get("usage")
    return summary

# [1H-PART-3]


def build_scene_accounting(
    result: SceneGenerationResult,
    *,
    selected_provider: Optional[str] = None,
    checkpoint: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Batch 1H: generic, provider-agnostic scene accounting.

    Built deterministically from execution evidence so repeated completion
    updates for the SAME request identity are idempotent (replace, never
    increment). Rules:

    - reuse of an existing valid artifact is not a new billable operation
      (``non_billable`` with a real cost 0 for this execution event);
    - the known non-billable local family (ImageMotion/local assets/external
      clips) is ``non_billable`` with a real 0;
    - every other provider operation is ``billable`` with the provider's own
      usage quantities and its provider-reported cost when it supplies one —
      otherwise cost stays UNKNOWN (never zero);
    - a classified fallback retains the failed primary attempt (provider,
      request identity from the operational checkpoint, billing status derived
      from the failure category) alongside the final successful attempt.
    """
    verification = result.verification or {}
    final_provider = (
        verification.get("final_provider")
        or verification.get("provider")
        or selected_provider
    )
    request_id, request_id_kind = _accounting_request_identity(verification)
    is_reuse = bool(verification.get("reuse"))
    provider_reported_cost = verification.get("actual_cost")
    if provider_reported_cost is None:
        provider_reported_cost = verification.get("cost")

    if is_reuse:
        final_attempt = build_operation_accounting(
            provider=final_provider,
            model=verification.get("model"),
            billing_status=BILLING_STATUS_NON_BILLABLE,
            usage=None,
            cost=0,
            cost_source=COST_SOURCE_NONE,
            currency=None,
            source="reused",
        )
    elif final_provider in ZERO_COST_PROVIDERS:
        final_attempt = build_operation_accounting(
            provider=final_provider,
            model=verification.get("model"),
            billing_status=BILLING_STATUS_NON_BILLABLE,
            usage=None,
            cost=0,
            cost_source=COST_SOURCE_NONE,
            currency=None,
            source="generated",
        )
    else:
        final_attempt = build_operation_accounting(
            provider=final_provider,
            model=verification.get("model"),
            request_id=request_id,
            request_id_kind=request_id_kind,
            billing_status=BILLING_STATUS_BILLABLE,
            usage=verification.get("usage"),
            cost=provider_reported_cost,
            cost_source=(
                COST_SOURCE_PROVIDER_REPORTED
                if provider_reported_cost is not None
                else COST_SOURCE_NONE
            ),
            currency=(
                verification.get("currency")
                if provider_reported_cost is not None
                else None
            ),
            estimated_cost=verification.get("estimated_cost"),
            source="generated",
        )

    attempts: List[Dict[str, Any]] = []
    if verification.get("fallback_used") is True and verification.get(
        "primary_provider"
    ):
        attempts.append(
            _build_primary_attempt_accounting(verification, checkpoint)
        )
    attempts.append(final_attempt)
    for number, attempt in enumerate(attempts, start=1):
        attempt["attempt_number"] = number

    accounting = dict(attempts=attempts)
    accounting["summary"] = summarize_accounting_attempts(attempts)
    return accounting


def completion_checkpoint_update(
    *,
    result: SceneGenerationResult,
    validation: Optional[Dict[str, Any]],
    provider_name: str,
    sequence: int,
    extra_fields: Optional[Dict[str, Any]] = None,
    include_provenance: bool = True,
    audio_facts: Optional[Dict[str, Any]] = None,
    checkpoint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Canonical post-validation completion checkpoint fields.

    Shared by every boundary-executed provider so completion state is persisted
    in exactly one shape. Provider-specific extras (e.g. zero-cost
    ``generation_cost``/``source_asset`` provenance) are passed via
    ``extra_fields``; remote-job provenance (model/access/job/cost fields) is
    merged from the provider's verification block when ``include_provenance``
    is set. Never includes secrets — callers only ever pass non-secret fields.

    Batch 1F: this function is the SINGLE capture seam for the generic
    artifact-level provenance block (``update["provenance"]``), derived from
    the ACTUAL provider result — never from routing/selection configuration.
    ``provider_name`` stays the routed/selected provider and is used only as
    the identity fallback when the provider result does not identify itself;
    when the result carries the actual (possibly fallback) provider identity,
    that identity wins for ``update["provider"]`` as well. The provenance
    block is captured even when the flat remote-job merge is disabled
    (``include_provenance=False``), so zero-cost checkpoints keep their
    existing shape while every completion still records truthful provenance.

    Batch 1H: the same seam captures the generic provider ACCOUNTING block
    (``update["accounting"]``) — per-attempt usage/billing/cost evidence.
    ``checkpoint`` (the operational scene checkpoint) is optional and is used
    only to recover the FAILED primary attempt's native request identity when
    a classified fallback occurred. Accounting is derived deterministically
    from execution evidence, so repeated completion updates for the same
    request identity are idempotent.
    """
    verification = result.verification or {}
    actual_provider = (
        verification.get("final_provider")
        or verification.get("provider")
        or provider_name
    )
    update: Dict[str, Any] = {
        "provider": actual_provider,
        "scene_id": result.scene_id,
        "sequence": sequence,
        "status": "completed",
        "file_path": str(result.file_path) if result.file_path else None,
        "validation": validation,
    }
    if audio_facts is not None:
        update["audio_facts"] = audio_facts
    if include_provenance:
        for key in PROVENANCE_KEYS:
            value = verification.get(key)
            if value is not None:
                update[key] = value
    update["provenance"] = build_scene_provenance(
        result, selected_provider=provider_name
    )
    update["accounting"] = build_scene_accounting(
        result, selected_provider=provider_name, checkpoint=checkpoint
    )
    # Batch 1I: one compact capture-confirmation line. Deliberately NOT a dump
    # of the provenance/accounting objects — the scene manifest is the
    # authoritative record.
    final_attempt = update["accounting"].get("attempts", [{}])[-1]
    logger.info(
        "provenance.accounting.captured scene=%s provider=%s model=%s "
        "request_id=%s request_id_kind=%s billing_status=%s "
        "cost_present=%s fallback_used=%s",
        result.scene_id,
        final_attempt.get("provider"),
        final_attempt.get("model"),
        final_attempt.get("request_id"),
        final_attempt.get("request_id_kind"),
        final_attempt.get("billing_status"),
        final_attempt.get("cost") is not None,
        bool(verification.get("fallback_used")),
    )
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
    from app.orchestrator.visual_providers import ALIBABA_VIDEO_PROVIDER

    if provider_name == OPENROUTER_VIDEO_PROVIDER:
        model = (scene_config or {}).get("openrouter_model") or "openrouter_video"
    elif provider_name == ALIBABA_VIDEO_PROVIDER:
        model = (scene_config or {}).get("alibaba_model") or "alibaba_video"
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
