"""
AVM TRACK 9 -- Provider Capability Discovery & Job-Specific Routing (Tasks B3-B5)

THE THREE AUTHORITIES STAY SEPARATE
-----------------------------------

1. CANONICAL MODEL CATALOGUE  -- ``app.content.model_registry``
   What a model IS: canonical identity, verified capabilities, metadata.

2. PROVIDER CATALOGUE         -- ``app.content.provider_registry`` + the
   internal ``ProviderBinding`` records on each canonical model.
   What a provider can EXECUTE: wire ids, modalities, operations.

3. JOB ELIGIBILITY / AVAILABILITY -- THIS MODULE.
   Whether a given canonical-model + provider route can do the requested job
   RIGHT NOW, judged on truthful evidence.

This module adds NO second canonical registry and NO second resolver. Execution
still goes through ``app.content.model_resolver.resolve_model``, which remains
the only resolver and preserves canonical identity.

TRUTHFULNESS RULES (Task B4)
----------------------------
* A valid API key proves ONLY that credentials are configured. It never proves
  credits remain.
* A provider model-list response proves the model is offered, never that THIS
  account has quota.
* With no reliable balance endpoint the quota state stays ``unknown``.
  "Unknown" is never rendered as "available".
* An explicit exhausted-quota/rate-limit response IS evidence: it is recorded as
  a blocker and the route is not retried while that evidence stands.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from app.content.model_registry import (
    JOB_REQUIREMENTS,
    JOB_SOURCE_VIDEO_RECAP,
    IdentityVerification,
    QuotaEvidence,
    get_model,
    job_video_understanding_method,
    model_meets_job,
)

#: Providers exposing a documented balance/quota endpoint that AVM can reuse.
#: CodeCraft was probed live: /balance, /credits, /usage, /account and /me all
#: return 404, so no balance endpoint exists and its quota state must stay
#: UNKNOWN rather than being invented.
PROVIDERS_WITH_BALANCE_ENDPOINT: frozenset = frozenset()

#: How long an explicit quota/rate-limit blocker is honoured (seconds).
DEFAULT_BLOCKER_TTL_SECONDS = 300


@dataclass
class ProviderEvidence:
    """Accumulated, truthful evidence about one provider route."""

    provider: str
    provider_model_id: str
    credentials_configured: bool = False
    missing_config_keys: Tuple[str, ...] = ()
    provider_registered: bool = False
    model_listed: bool = False
    capability_verified: bool = False
    identity_verification: str = IdentityVerification.VERIFIED
    identity_evidence: Optional[str] = None
    #: One of ``QuotaEvidence.*``. Defaults to UNKNOWN and is only upgraded by
    #: real evidence.
    quota_state: str = QuotaEvidence.UNKNOWN
    quota_detail: Optional[str] = None
    blocked_until: Optional[float] = None
    notes: Tuple[str, ...] = ()

    @property
    def quota_known(self) -> bool:
        """Whether remaining quota/balance is ACTUALLY known."""
        return self.quota_state in (
            QuotaEvidence.BALANCE_VERIFIED,
            QuotaEvidence.EXHAUSTED,
            QuotaEvidence.REQUEST_SUCCEEDED,
        )

    @property
    def currently_blocked(self) -> bool:
        if self.quota_state == QuotaEvidence.EXHAUSTED:
            if self.blocked_until is None:
                return True
            return time.time() < self.blocked_until
        return False

    def as_dict(self) -> Dict[str, object]:
        return {
            "provider": self.provider,
            "provider_model_id": self.provider_model_id,
            "credentials_configured": self.credentials_configured,
            "missing_config_keys": list(self.missing_config_keys),
            "provider_registered": self.provider_registered,
            "model_listed": self.model_listed,
            "capability_verified": self.capability_verified,
            "identity_verification": self.identity_verification,
            "identity_evidence": self.identity_evidence,
            "quota_state": self.quota_state,
            "quota_detail": self.quota_detail,
            "quota_known": self.quota_known,
            "currently_blocked": self.currently_blocked,
            "notes": list(self.notes),
        }


@dataclass
class RouteEligibility:
    """Eligibility of ONE (canonical model, provider binding) route."""

    canonical_model_id: str
    provider: str
    provider_model_id: str
    eligible: bool
    reasons: Tuple[str, ...]
    evidence: ProviderEvidence
    input_modality: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "canonical_model_id": self.canonical_model_id,
            "provider": self.provider,
            "provider_model_id": self.provider_model_id,
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "input_modality": self.input_modality,
            "evidence": self.evidence.as_dict(),
        }


@dataclass
class ModelEligibility:
    """Eligibility of ONE canonical model, including its provider routes."""

    canonical_model_id: str
    display_name: str
    job_id: str
    capability_ok: bool
    missing_capabilities: Tuple[str, ...]
    routes: Tuple[RouteEligibility, ...] = ()
    video_understanding_method: Optional[str] = None

    @property
    def eligible(self) -> bool:
        """Capable of the job AND has at least one eligible provider route."""
        return self.capability_ok and any(r.eligible for r in self.routes)

    def eligible_routes(self) -> Tuple[RouteEligibility, ...]:
        return tuple(r for r in self.routes if r.eligible)

    def as_dict(self) -> Dict[str, object]:
        return {
            "canonical_model_id": self.canonical_model_id,
            "display_name": self.display_name,
            "job_id": self.job_id,
            "capability_ok": self.capability_ok,
            "missing_capabilities": list(self.missing_capabilities),
            "eligible": self.eligible,
            "video_understanding_method": self.video_understanding_method,
            "routes": [r.as_dict() for r in self.routes],
        }


# ---------------------------------------------------------------------------
# Explicit quota blockers (Task B4): learned from real provider responses.
# ---------------------------------------------------------------------------

_QUOTA_BLOCKERS: Dict[Tuple[str, str], Tuple[float, str]] = {}


def record_quota_blocker(
    provider: str,
    provider_model_id: str,
    detail: str,
    *,
    retry_after_seconds: Optional[int] = None,
) -> None:
    """Record an EXPLICIT exhausted-quota / rate-limit response as evidence.

    Respects provider retry guidance so AVM stops hammering a route whose
    blocker is already established.
    """
    ttl = (
        retry_after_seconds
        if retry_after_seconds and retry_after_seconds > 0
        else DEFAULT_BLOCKER_TTL_SECONDS
    )
    _QUOTA_BLOCKERS[(provider.strip().lower(), provider_model_id.strip())] = (
        time.time() + float(ttl),
        str(detail)[:200],
    )


def clear_quota_blockers() -> None:
    """Clear recorded blockers (used by tests and after credential changes)."""
    _QUOTA_BLOCKERS.clear()


def _active_blocker(provider: str, provider_model_id: str) -> Optional[Tuple[float, str]]:
    key = (provider.strip().lower(), provider_model_id.strip())
    entry = _QUOTA_BLOCKERS.get(key)
    if entry is None:
        return None
    until, _detail = entry
    if time.time() >= until:
        _QUOTA_BLOCKERS.pop(key, None)
        return None
    return entry


# ---------------------------------------------------------------------------
# TASK B4 -- provider eligibility from REAL evidence
# ---------------------------------------------------------------------------


def assess_provider_route(
    canonical_model_id: str,
    binding,
    *,
    required_modality: Optional[str] = None,
) -> RouteEligibility:
    """Assess ONE provider binding for a canonical model.

    Pure and offline: reads only env presence and the provider registry. It
    never calls a provider API, so it cannot fabricate availability.
    """
    import app.content.provider_registry as provider_registry

    reasons: list[str] = []
    if binding.provider == "codecraft":
        from app.content.providers.codecraft import codecraft_runtime_enabled
        if not codecraft_runtime_enabled():
            reasons.append("runtime_quarantined")
    ev = ProviderEvidence(
        provider=binding.provider,
        provider_model_id=binding.provider_model_id,
        identity_verification=binding.identity_verification,
        identity_evidence=binding.identity_evidence,
    )

    # 1. credentials configured (NOT proof of credits)
    missing = tuple(k for k in binding.config_keys if not (os.getenv(k) or "").strip())
    ev.missing_config_keys = missing
    ev.credentials_configured = not missing
    if missing:
        reasons.append(f"credentials_missing:{','.join(missing)}")

    # 2. provider registered in the provider catalogue
    ev.provider_registered = provider_registry.has_provider(binding.provider)
    if not ev.provider_registered:
        reasons.append("provider_not_registered")

    # 3. the route is listed/declared for this exact model
    ev.model_listed = True  # registered binding == declared by AVM's catalogue
    ev.capability_verified = binding.identity_verification == IdentityVerification.VERIFIED

    # 4. modality support for the requested job
    modality_used: Optional[str] = None
    if required_modality is not None:
        supports = binding.supports_modality(required_modality)
        if supports is False:
            reasons.append(f"modality_unsupported:{required_modality}")
        elif supports is None:
            # Route declares no modality metadata -> fall back to the canonical
            # model's own capabilities rather than assuming either way.
            model = get_model(canonical_model_id)
            if not model.capabilities.supports_input_modality(required_modality):
                reasons.append(f"modality_unsupported:{required_modality}")
            else:
                modality_used = required_modality
                ev.notes = ev.notes + ("modality_from_canonical_capabilities",)
        else:
            modality_used = required_modality

    # 5. quota / balance evidence -- truthful, never invented
    blocker = _active_blocker(binding.provider, binding.provider_model_id)
    if blocker is not None:
        until, detail = blocker
        ev.quota_state = QuotaEvidence.EXHAUSTED
        ev.quota_detail = detail
        ev.blocked_until = until
        reasons.append("quota_exhausted_evidence")
    elif binding.provider in PROVIDERS_WITH_BALANCE_ENDPOINT:
        # A real balance endpoint would be consulted here; none exists today.
        ev.quota_state = QuotaEvidence.UNKNOWN
        ev.quota_detail = "balance endpoint available but not queried in this assessment"
    elif ev.credentials_configured:
        # The strongest HONEST statement: credentials exist; credits unknown.
        ev.quota_state = QuotaEvidence.CREDENTIALS_CONFIGURED
        ev.quota_detail = (
            "provider exposes no balance/quota endpoint; remaining credit is UNKNOWN"
        )
    else:
        ev.quota_state = QuotaEvidence.UNKNOWN
        ev.quota_detail = "credentials not configured; quota cannot be assessed"

    eligible = not reasons
    return RouteEligibility(
        canonical_model_id=canonical_model_id,
        provider=binding.provider,
        provider_model_id=binding.provider_model_id,
        eligible=eligible,
        reasons=tuple(reasons),
        evidence=ev,
        input_modality=modality_used,
    )


def assess_model_for_job(
    canonical_model_id: str,
    job_id: str = JOB_SOURCE_VIDEO_RECAP,
) -> ModelEligibility:
    """Assess a canonical model AND all its provider routes for *job_id*."""
    req = JOB_REQUIREMENTS.get((job_id or "").strip())
    if req is None:
        raise KeyError(f"unknown job requirement: '{job_id}'")

    model = get_model(canonical_model_id)
    capability_ok, missing = model_meets_job(model, req)
    # A visual-capable model still uses TEXT for Standard. Never impose Pro's
    # native-video/frame requirement on a transcript/text+JSON job.
    method = job_video_understanding_method(model)
    if method not in req.requires_any_input_modality:
        method = None
    required_modality = method or next(
        (m for m in req.requires_any_input_modality if model.capabilities.supports_input_modality(m)),
        None,
    )

    routes = tuple(
        assess_provider_route(model.id, b, required_modality=required_modality)
        for b in model.bindings
    )
    return ModelEligibility(
        canonical_model_id=model.id,
        display_name=model.display_name,
        job_id=req.job_id,
        capability_ok=capability_ok,
        missing_capabilities=missing,
        routes=routes,
        video_understanding_method=method,
    )


# ---------------------------------------------------------------------------
# TASK B5 -- AUTO at the CANONICAL-MODEL level
# ---------------------------------------------------------------------------


def capability_rank(model) -> int:
    """Deterministic capability score. CLEARLY NOT a cost ranking.

    True cheapest-capable selection needs trustworthy pricing AND a job-cost
    estimate; neither exists in Core, so this capability ranking is preserved
    as the explicitly-identified fallback and no cost claim is made.
    """
    caps = model.capabilities
    score = 0
    if caps.native_video_input:
        score += 8
    if caps.image_input:
        score += 4
    if caps.long_context:
        score += 2
    if caps.tool_use:
        score += 1
    if caps.reasoning:
        score += 1
    return score


@dataclass
class AutoSelection:
    """Result of Auto selection. The MODEL is chosen before any provider."""

    canonical_model_id: str
    display_name: str
    job_id: str
    selection_mode: str
    ranking_basis: str
    cost_optimized: bool
    video_understanding_method: Optional[str]
    considered: Tuple[str, ...]
    eligible: Tuple[str, ...]
    rejected: Tuple[Tuple[str, Tuple[str, ...]], ...]

    def as_dict(self) -> Dict[str, object]:
        return {
            "canonical_model_id": self.canonical_model_id,
            "display_name": self.display_name,
            "job_id": self.job_id,
            "selection_mode": self.selection_mode,
            "ranking_basis": self.ranking_basis,
            "cost_optimized": self.cost_optimized,
            "video_understanding_method": self.video_understanding_method,
            "considered": list(self.considered),
            "eligible": list(self.eligible),
            "rejected": [{"model": m, "reasons": list(r)} for m, r in self.rejected],
        }


class NoEligibleModelError(Exception):
    """No canonical model is both capable of the job and has an eligible route."""


def list_eligible_models_for_job(
    job_id: str = JOB_SOURCE_VIDEO_RECAP,
) -> Tuple[ModelEligibility, ...]:
    """Every job-capable canonical model with its route eligibility.

    Step 1 + 2 of Auto: capability discovery, then provider-route eligibility.
    """
    from app.content.model_registry import discover_models_for_job

    return tuple(
        assess_model_for_job(m.id, job_id) for m in discover_models_for_job(job_id)
    )


def select_model_auto(job_id: str = JOB_SOURCE_VIDEO_RECAP) -> AutoSelection:
    """AUTO: choose a CANONICAL MODEL, strictly before any provider routing.

    Order (Task B5):
      1. models capable of the job (capability metadata only)
      2. keep those with >= 1 eligible provider route
      3. rank by legitimate capability metadata
      4. select the canonical model
    Provider resolution happens AFTERWARDS via ``resolve_model`` and must not
    change the selected identity.
    """
    assessments = list_eligible_models_for_job(job_id)
    considered = tuple(a.canonical_model_id for a in assessments)
    eligible = [a for a in assessments if a.eligible]
    rejected = tuple(
        (
            a.canonical_model_id,
            tuple(a.missing_capabilities)
            or tuple(r for route in a.routes for r in route.reasons)
            or ("no_provider_binding",),
        )
        for a in assessments
        if not a.eligible
    )
    if not eligible:
        raise NoEligibleModelError(
            f"no canonical model is eligible for job '{job_id}' "
            f"(considered: {list(considered)})"
        )

    order = {mid: i for i, mid in enumerate(considered)}
    eligible.sort(
        key=lambda a: (-capability_rank(get_model(a.canonical_model_id)),
                       order[a.canonical_model_id])
    )
    chosen = eligible[0]
    return AutoSelection(
        canonical_model_id=chosen.canonical_model_id,
        display_name=chosen.display_name,
        job_id=chosen.job_id,
        selection_mode="auto",
        ranking_basis="capability_rank",
        cost_optimized=False,
        video_understanding_method=chosen.video_understanding_method,
        considered=considered,
        eligible=tuple(a.canonical_model_id for a in eligible),
        rejected=rejected,
    )


def select_provider_for_model(
    canonical_model_id: str,
    job_id: str = JOB_SOURCE_VIDEO_RECAP,
) -> RouteEligibility:
    """Choose an eligible provider route FOR AN ALREADY-SELECTED model.

    Never changes the canonical model. Raises when no route is eligible, so a
    different model is never silently substituted.
    """
    assessment = assess_model_for_job(canonical_model_id, job_id)
    routes = assessment.eligible_routes()
    if not routes:
        details = "; ".join(
            f"{r.provider}:{'|'.join(r.reasons)}" for r in assessment.routes
        ) or "no bindings"
        raise NoEligibleModelError(
            f"canonical model '{assessment.canonical_model_id}' has no eligible "
            f"provider route for job '{job_id}' ({details})"
        )
    return routes[0]


__all__ = [
    "PROVIDERS_WITH_BALANCE_ENDPOINT",
    "DEFAULT_BLOCKER_TTL_SECONDS",
    "ProviderEvidence",
    "RouteEligibility",
    "ModelEligibility",
    "AutoSelection",
    "NoEligibleModelError",
    "record_quota_blocker",
    "clear_quota_blockers",
    "assess_provider_route",
    "assess_model_for_job",
    "list_eligible_models_for_job",
    "capability_rank",
    "select_model_auto",
    "select_provider_for_model",
]
