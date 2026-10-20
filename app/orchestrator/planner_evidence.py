"""
AVM Step 6 -- AI Brain (planner) provenance + accounting projection.

This module creates NO new evidence system. It projects the AI Brain's
``ContentGenerationResult`` onto the EXISTING authoritative AVM evidence
structures owned by ``app.orchestrator.scene_execution``:

* every accounting attempt is built by ``build_operation_accounting`` and the
  summary by ``summarize_accounting_attempts`` -- the SAME functions the video
  engine uses, so zero/unknown semantics can never diverge;
* provenance uses the SAME key vocabulary (``provider``, ``model``,
  ``request_id``, ``request_id_kind``, ``fallback_used``, ``primary_provider``,
  ``fallback_reason``, ``source``) and the same sparse "emit only what is
  known" discipline;
* the ONLY additive key is ``canonical_model_id`` -- the user-facing model
  identity. No existing field can carry it, because ``model`` already means the
  PROVIDER WIRE MODEL (``qwen3.8-27b``) and that meaning is preserved verbatim.

The video engine's builders are typed on ``SceneGenerationResult`` and read
media/remote-job facts, so they cannot serve an LLM result. The identity and
fallback DERIVATION is shared and lives in the content layer
(``app.content.execution_evidence.resolve_execution_identity``), so the
provenance and accounting projections can never disagree.

Persisted by ``ProductionExecutor.resolve_plan`` into ``plan_meta.json`` beside
the existing flat ``planner_provider`` / ``planner_model`` fields, whose meaning
is unchanged and which are never renamed. Never written into scene checkpoints,
never mixed into scene provenance/accounting, and no new endpoint is added.

Legacy records are never touched: this projection only runs when a NEW plan is
generated, and it never backfills or rewrites historical artifacts.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from app.content.execution_evidence import (
    SOURCE_GENERATED,
    resolve_execution_identity,
)
from app.orchestrator.scene_execution import (
    BILLING_STATUS_UNKNOWN,
    COST_SOURCE_NONE,
    build_operation_accounting,
    summarize_accounting_attempts,
)

#: ``request_id_kind`` for an LLM provider's own response/request identity
#: (CodeCraft's OpenAI-compatible ``raw["id"]`` -> ``metadata["request_id"]``).
#: This EXTENDS the established open kind vocabulary -- which names the native
#: field an id came from (``task_id`` / ``job_id`` for remote video jobs) -- and
#: never redefines those values. An absent request id stays absent: no local
#: UUID is ever substituted for a provider identity.
REQUEST_ID_KIND_LLM_RESPONSE = "llm_response_id"

#: Key carrying the user-facing canonical model identity. The ONLY additive
#: provenance/accounting concept introduced by the model-centric AI Brain.
CANONICAL_MODEL_ID_KEY = "canonical_model_id"


def build_planner_provenance(
    *,
    canonical_model_id: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    verification: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """AI Brain provenance: WHO/WHAT EXECUTED (established vocabulary).

    Emitted sparsely, exactly like ``build_scene_provenance``: a key appears
    only when the fact is actually known. ``canonical_model_id`` is therefore
    ABSENT (not null-filled, never inferred) for the legacy provider-first
    path, for mock and for local execution.

    ``fallback_used`` IS always emitted, because at this seam it is a KNOWN
    fact rather than an unreported one: one AI Brain plan generation is exactly
    one provider call through one resolved instance, so fallback either
    occurred (provider-reported metadata, or a resolver trace of failed
    bindings) or it definitively did not.

    Fallback here is PROVIDER fallback WITHIN ONE canonical model:
    ``canonical_model_id`` never changes, ``primary_provider`` is the first
    binding attempted and ``provider`` is the FINAL successful one.
    """
    identity = resolve_execution_identity(
        canonical_model_id=canonical_model_id,
        provider=provider,
        model=model,
        verification=verification,
    )

    provenance: Dict[str, Any] = {}
    if identity["canonical_model_id"]:
        provenance[CANONICAL_MODEL_ID_KEY] = identity["canonical_model_id"]
    if identity["provider"]:
        provenance["provider"] = identity["provider"]
    if identity["model"]:
        provenance["model"] = identity["model"]
    if identity["request_id"]:
        provenance["request_id"] = identity["request_id"]
        provenance["request_id_kind"] = REQUEST_ID_KIND_LLM_RESPONSE

    provenance["fallback_used"] = bool(identity["fallback_used"])
    if identity["fallback_used"]:
        if identity["primary_provider"]:
            provenance["primary_provider"] = identity["primary_provider"]
        if identity["fallback_reason"]:
            provenance["fallback_reason"] = identity["fallback_reason"]

    trace = identity["resolution_trace"]
    if trace:
        # Failed bindings only, in deterministic declared order. No request ids
        # and no usage are fabricated for attempts that never succeeded.
        provenance["resolution_trace"] = [dict(entry) for entry in trace]

    provenance["source"] = SOURCE_GENERATED
    return provenance


def build_planner_accounting(
    *,
    canonical_model_id: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    verification: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """AI Brain accounting: WHAT WAS CONSUMED / BILLED (established authority).

    Returns the SAME ``{"attempts": [...], "summary": {...}}`` shape the video
    engine returns, with attempts built by ``build_operation_accounting`` and
    the summary derived by ``summarize_accounting_attempts`` -- so the
    zero/unknown precedence (provider-reported > AVM-calculated > UNKNOWN) is
    literally the same code, not a parallel implementation.

    The only additive per-attempt key is ``canonical_model_id``.

    Cost: CodeCraft reports no authoritative monetary cost and AVM has no
    authoritative AI Brain text pricing, so ``billing_status`` is UNKNOWN,
    ``cost`` is None and ``cost_source`` is ``"none"``. It is NEVER recorded as
    0 -- a zero would falsely assert a known non-billable execution, and
    "no cost data" is not "free".

    Usage: passed through verbatim when the provider supplied it. When absent it
    stays empty/unknown (``build_operation_accounting`` normalizes absent usage
    to ``{}``) and is NEVER interpreted as zero tokens.
    """
    identity = resolve_execution_identity(
        canonical_model_id=canonical_model_id,
        provider=provider,
        model=model,
        verification=verification,
    )

    attempts: list = []

    if identity["fallback_used"] and identity["primary_provider"]:
        # The FAILED primary binding, mirroring the established
        # ``_build_primary_attempt_accounting`` discipline: no request id and no
        # usage are invented for an attempt that never succeeded, and its
        # billing state stays UNKNOWN.
        primary = build_operation_accounting(
            provider=identity["primary_provider"],
            model=None,
            request_id=None,
            request_id_kind=None,
            billing_status=BILLING_STATUS_UNKNOWN,
            usage=None,
            cost=None,
            cost_source=COST_SOURCE_NONE,
            currency=None,
            source=SOURCE_GENERATED,
        )
        primary[CANONICAL_MODEL_ID_KEY] = identity["canonical_model_id"]
        primary["fallback_used"] = True
        if identity["fallback_reason"]:
            primary["fallback_reason"] = identity["fallback_reason"]
        attempts.append(primary)

    final = build_operation_accounting(
        provider=identity["provider"],
        model=identity["model"],
        request_id=identity["request_id"],
        request_id_kind=(
            REQUEST_ID_KIND_LLM_RESPONSE if identity["request_id"] else None
        ),
        billing_status=BILLING_STATUS_UNKNOWN,
        usage=identity["usage"],
        cost=None,
        cost_source=COST_SOURCE_NONE,
        currency=None,
        source=SOURCE_GENERATED,
    )
    final[CANONICAL_MODEL_ID_KEY] = identity["canonical_model_id"]
    if identity["fallback_used"]:
        final["fallback_used"] = True
        if identity["primary_provider"]:
            final["primary_provider"] = identity["primary_provider"]
        if identity["fallback_reason"]:
            final["fallback_reason"] = identity["fallback_reason"]
    attempts.append(final)

    for number, attempt in enumerate(attempts, start=1):
        attempt["attempt_number"] = number

    accounting: Dict[str, Any] = dict(attempts=attempts)
    accounting["summary"] = summarize_accounting_attempts(attempts)
    return accounting


__all__ = [
    "REQUEST_ID_KIND_LLM_RESPONSE",
    "CANONICAL_MODEL_ID_KEY",
    "build_planner_provenance",
    "build_planner_accounting",
]
