"""
AVM Step 3 -- Model-bound Resolver (EXACT-model, intra-model fallback)

Connector between:

    Canonical Model Registry (app/content/model_registry.py)
            |
    Provider Registry (app/content/provider_registry.py)
            |
    BaseLLMProvider (existing contract, unchanged)
            |
    execution

The resolver preserves canonical model identity:

    Exact mode = intra-model provider fallback only.
    Inter-model selection belongs to future AVM Auto -- NOT here.

No network at import, no provider instantiation at import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.content.providers.base import BaseLLMProvider
from app.providers.fallback_policy import (
    FailureCategory,
    classify_provider_failure,
    is_fallback_eligible,
)


class ModelResolverError(Exception):
    """Base for resolver errors -- never exposes secrets/prompts."""


class UnknownCanonicalModelError(ModelResolverError):
    """Unknown canonical_model_id."""


class NoProviderBindingsError(ModelResolverError):
    """Canonical model has zero bindings."""


class ProviderNotRegisteredError(ModelResolverError):
    """Binding provider not in Provider Registry."""


class AllExactModelProvidersFailedError(ModelResolverError):
    """Every binding for the canonical model failed."""

    def __init__(
        self,
        canonical_model_id: str,
        attempted: List[Dict[str, Optional[str]]],
        last_error: Optional[BaseException] = None,
    ) -> None:
        self.canonical_model_id = canonical_model_id
        self.attempted = attempted
        self.last_error = last_error
        providers = ", ".join(f"{a.get('provider')}" for a in attempted)
        super().__init__(
            f"all providers failed for canonical model '{canonical_model_id}' "
            f"(attempted: {providers})"
        )


@dataclass(frozen=True)
class ResolvedModelProvider:
    """Successful exact-model resolution."""

    canonical_model_id: str
    provider: str
    provider_model_id: str
    provider_instance: BaseLLMProvider
    attempted: Tuple[Dict[str, Optional[str]], ...] = ()


def _short_reason(exc: BaseException) -> str:
    msg = str(exc).strip()
    if not msg:
        return type(exc).__name__
    return msg[:200]


def resolve_model(
    canonical_model_id: str,
    *,
    mode: str = "exact",
    try_next_on_failure: bool = False,
    job_id: Optional[str] = None,
) -> ResolvedModelProvider:
    """Resolve a canonical model to a provider instance.

    Args:
        canonical_model_id: Exact canonical id (e.g. qwen-3.8-flash).
        mode: Only "exact" is supported.
        try_next_on_failure: If False (default), resolve first binding
            deterministically. If True, eligible provider failures try
            next binding in declared order (intra-model fallback).
        job_id: Optional capability job. When supplied, only locally eligible
            bindings for that job can be instantiated; canonical identity and
            declared binding order are preserved.

    No network call; provider.generate() is never invoked here.
    """
    if mode != "exact":
        raise ValueError(f"unsupported resolver mode: '{mode}' (only 'exact' is supported)")
    key = (canonical_model_id or "").strip()
    if not key:
        raise ValueError("canonical_model_id must be a non-empty string")

    from app.content.model_registry import get_model
    import app.content.provider_registry as provider_registry

    try:
        canonical = get_model(key)
    except KeyError as exc:
        raise UnknownCanonicalModelError(str(exc)) from None

    bindings = canonical.bindings
    if not bindings:
        raise NoProviderBindingsError(
            f"canonical model '{canonical.id}' has no provider bindings"
        )

    attempted: List[Dict[str, Optional[str]]] = []
    last_exc: Optional[BaseException] = None
    eligible_bindings = None
    if job_id is not None:
        from app.content.capability_router import assess_model_for_job

        assessment = assess_model_for_job(canonical.id, job_id)
        if not assessment.capability_ok:
            raise ValueError(f"Canonical model {canonical.id!r} cannot satisfy job {job_id!r}")
        eligible_bindings = {
            (route.provider, route.provider_model_id) for route in assessment.eligible_routes()
        }

    for binding in bindings:
        provider_name = binding.provider
        wire_id = binding.provider_model_id
        if eligible_bindings is not None and (provider_name, wire_id) not in eligible_bindings:
            attempted.append({
                "provider": provider_name, "provider_model_id": wire_id,
                "reason": "route_not_eligible_for_job", "category": FailureCategory.CONFIGURATION,
            })
            continue

        if not provider_registry.has_provider(provider_name):
            if not try_next_on_failure:
                raise ProviderNotRegisteredError(
                    f"provider '{provider_name}' for canonical model '{canonical.id}' is not registered"
                )
            attempted.append(
                {"provider": provider_name, "provider_model_id": wire_id,
                 "reason": "provider_not_registered", "category": FailureCategory.CONFIGURATION}
            )
            last_exc = ProviderNotRegisteredError(provider_name)
            continue

        try:
            instance = provider_registry.create_provider(provider_name, wire_id)
        except Exception as exc:  # noqa: BLE001
            category = classify_provider_failure(exc)
            reason = _short_reason(exc)
            if not try_next_on_failure:
                raise ProviderNotRegisteredError(
                    f"failed to create provider '{provider_name}' for canonical model '{canonical.id}': {reason}"
                ) from exc
            if not is_fallback_eligible(category):
                attempted.append({"provider": provider_name, "provider_model_id": wire_id, "reason": reason, "category": category})
                last_exc = exc
                raise AllExactModelProvidersFailedError(canonical.id, attempted, last_exc) from exc
            attempted.append({"provider": provider_name, "provider_model_id": wire_id, "reason": reason, "category": category})
            last_exc = exc
            continue

        if not try_next_on_failure:
            return ResolvedModelProvider(
                canonical_model_id=canonical.id,
                provider=provider_name,
                provider_model_id=wire_id,
                provider_instance=instance,
                attempted=tuple(attempted),
            )

        # Fallback mode: check local availability
        try:
            available = instance.is_available()
        except Exception as exc:  # noqa: BLE001
            category = classify_provider_failure(exc)
            reason = _short_reason(exc)
            if not is_fallback_eligible(category):
                attempted.append({"provider": provider_name, "provider_model_id": wire_id, "reason": reason, "category": category})
                last_exc = exc
                raise AllExactModelProvidersFailedError(canonical.id, attempted, last_exc) from exc
            attempted.append({"provider": provider_name, "provider_model_id": wire_id, "reason": reason, "category": category})
            last_exc = exc
            continue

        if available:
            return ResolvedModelProvider(
                canonical_model_id=canonical.id,
                provider=provider_name,
                provider_model_id=wire_id,
                provider_instance=instance,
                attempted=tuple(attempted),
            )

        # Unavailable -- treat as provider_unavailable for intra-model fallback
        from app.content.exceptions import LLMConnectionError as _ConnErr
        synth = _ConnErr(provider_name, "provider unavailable (is_available==False)")
        attempted.append(
            {"provider": provider_name, "provider_model_id": wire_id,
             "reason": "provider_unavailable", "category": FailureCategory.PROVIDER_UNAVAILABLE}
        )
        last_exc = synth
        continue

    raise AllExactModelProvidersFailedError(canonical.id, attempted, last_exc)


__all__ = [
    "ModelResolverError",
    "UnknownCanonicalModelError",
    "NoProviderBindingsError",
    "ProviderNotRegisteredError",
    "AllExactModelProvidersFailedError",
    "ResolvedModelProvider",
    "resolve_model",
]
