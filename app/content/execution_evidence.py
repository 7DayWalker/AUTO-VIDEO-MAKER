"""
AVM Step 6 -- AI Brain execution evidence NORMALIZATION (content layer).

This module creates NO provenance system and NO accounting system. It does two
things only:

1. ``normalize_llm_verification`` -- projects an ``LLMResponse`` onto the SAME
   raw-evidence shape the video engine already reads from
   ``SceneGenerationResult.verification`` (provider / model / usage /
   request_id / fallback_used / primary_provider / final_provider /
   fallback_reason).
2. ``resolve_execution_identity`` -- derives the four model-centric identities
   and the fallback facts EXACTLY ONCE, so the provenance and accounting
   projections built at the persistence seam
   (``app.orchestrator.planner_evidence``) can never disagree.

The authoritative provenance/accounting SHAPES and status vocabulary stay where
AVM already owns them (``app.orchestrator.scene_execution``). This module is
deliberately free of orchestrator imports so the content layer keeps its
existing direction of dependency.

Four identities that are NEVER collapsed:

    canonical model identity : qwen-3.8-flash  (user-facing, model registry)
    provider identity        : codecraft       (internal execution detail)
    provider model identity  : qwen3.8-27b     (wire id -- the meaning of the
                                                established ``model`` field)
    request identity         : provider/API request id, else UNKNOWN

Invariants:

* ``canonical_model_id`` is None when no canonical model was selected (legacy
  provider-first path, mock, local). It is NEVER inferred from a provider model
  id and never fabricated.
* The FINAL SUCCESSFUL provider is authoritative for ``provider``;
  ``primary_provider`` preserves the FIRST attempt when fallback occurred.
* Fallback recorded here is PROVIDER fallback WITHIN ONE canonical model --
  never a model substitution.
* UNKNOWN stays UNKNOWN: absent usage is never 0, absent cost is never 0, an
  absent request id is never invented (no local UUIDs).
* Explicit allow-lists only: raw response bodies, prompts and credentials can
  never enter persisted evidence.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

#: Established ``source`` value for freshly generated (non-reused) execution
#: evidence. The video engine uses exactly this vocabulary (``"generated"`` /
#: ``"reused"``); the AI Brain has no artifact-reuse concept, so its evidence is
#: always ``"generated"``. No new ``source`` vocabulary is invented.
SOURCE_GENERATED = "generated"

#: Metadata keys an LLM provider may contribute to execution evidence.
#: Explicit allow-list: anything else (raw bodies, credentials, prompts) is
#: never copied into persisted evidence.
LLM_METADATA_EVIDENCE_KEYS = (
    "request_id",
    "fallback_used",
    "primary_provider",
    "final_provider",
    "fallback_reason",
)

#: Resolver attempt keys preserved in the resolution trace (allow-list).
RESOLUTION_TRACE_KEYS = ("provider", "provider_model_id", "reason", "category")


def _text(value: Any) -> Optional[str]:
    """Normalize to a non-empty stripped string, else None (UNKNOWN)."""
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def normalize_llm_verification(response: Any) -> Dict[str, Any]:
    """Project an ``LLMResponse`` onto the SAME raw-evidence shape the video
    engine reads from ``SceneGenerationResult.verification``.

    Only allow-listed, non-secret facts are copied. ``raw_response`` is
    deliberately never carried: it can contain request bodies and is not part
    of the established evidence vocabulary.

    Absent facts stay absent -- never defaulted to 0, "" or a fake id.
    """
    verification: Dict[str, Any] = {}

    provider = _text(getattr(response, "provider", None))
    if provider:
        verification["provider"] = provider

    model = _text(getattr(response, "model", None))
    if model:
        verification["model"] = model

    usage = getattr(response, "usage", None)
    if isinstance(usage, Mapping) and usage:
        verification["usage"] = dict(usage)

    metadata = getattr(response, "metadata", None)
    if isinstance(metadata, Mapping):
        for key in LLM_METADATA_EVIDENCE_KEYS:
            if key not in metadata or metadata[key] is None:
                continue
            value = metadata[key]
            if key == "fallback_used":
                verification[key] = bool(value)
                continue
            text = _text(value)
            if text:
                verification[key] = text

    return verification


def resolution_trace_from_attempted(
    attempted: Optional[Iterable[Mapping[str, Any]]],
) -> Tuple[Dict[str, Any], ...]:
    """Normalize a resolver's ``attempted`` entries into a JSON-safe trace.

    The trace records the providers that were tried and FAILED before the final
    successful binding, in deterministic declared-binding order. It is the
    evidence that fallback was PROVIDER-level within one canonical model.

    No request ids and no usage are invented for failed attempts: only what the
    resolver actually observed (provider, provider model id, reason, category).
    """
    if not attempted:
        return ()
    trace: list = []
    for entry in attempted:
        if not isinstance(entry, Mapping):
            continue
        record: Dict[str, Any] = {}
        for key in RESOLUTION_TRACE_KEYS:
            if key not in entry or entry[key] is None:
                continue
            value = entry[key]
            text = value.strip() if isinstance(value, str) else _text(value)
            if text:
                record[key] = text
        if record:
            trace.append(record)
    return tuple(trace)


def resolve_execution_identity(
    *,
    canonical_model_id: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    verification: Optional[Mapping[str, Any]] = None,
    resolution_trace: Optional[Iterable[Mapping[str, Any]]] = (),
) -> Dict[str, Any]:
    """Derive the model-centric execution identity and fallback facts ONCE.

    This is the single derivation shared by the provenance and accounting
    projections, so the two can never disagree and no second authority exists.

    Fallback evidence precedence (deterministic):

    1. provider-reported ``LLMResponse.metadata`` (closest to the response --
       e.g. the global Alibaba -> OpenRouter ``FallbackLLMProvider``);
    2. the resolver's ``attempted`` trace (intra-model provider fallback).

    Only one of the two is populated for any given execution today; the
    precedence keeps the result deterministic if both ever are.

    The resolution trace may arrive explicitly (from the resolver) or inside
    the single raw-evidence object the generator produced -- both are the SAME
    evidence, normalized through one function.
    """
    evidence = verification if isinstance(verification, Mapping) else {}

    # The FINAL SUCCESSFUL provider is authoritative. ``final_provider`` is the
    # provider-reported fact; ``provider`` is the response's own identity; the
    # caller-supplied value is the last resort.
    final_provider = (
        _text(evidence.get("final_provider"))
        or _text(evidence.get("provider"))
        or _text(provider)
    )
    final_model = _text(evidence.get("model")) or _text(model)

    trace = tuple(resolution_trace or ()) or resolution_trace_from_attempted(
        evidence.get("resolution_trace")
    )

    fallback_used = bool(evidence.get("fallback_used", False)) or bool(trace)

    primary_provider = _text(evidence.get("primary_provider"))
    if not primary_provider and trace:
        primary_provider = _text(trace[0].get("provider"))

    fallback_reason = _text(evidence.get("fallback_reason"))
    if not fallback_reason and trace:
        fallback_reason = _text(trace[-1].get("category")) or _text(
            trace[-1].get("reason")
        )

    request_id = _text(evidence.get("request_id"))

    usage = evidence.get("usage")
    usage = dict(usage) if isinstance(usage, Mapping) and usage else None

    return {
        "canonical_model_id": _text(canonical_model_id),
        "provider": final_provider,
        "model": final_model,
        "request_id": request_id,
        "fallback_used": fallback_used,
        # Established rule: primary provider / fallback reason are only
        # meaningful when a classified fallback actually occurred.
        "primary_provider": primary_provider if fallback_used else None,
        "fallback_reason": fallback_reason if fallback_used else None,
        "usage": usage,
        "resolution_trace": trace,
    }


__all__ = [
    "SOURCE_GENERATED",
    "LLM_METADATA_EVIDENCE_KEYS",
    "RESOLUTION_TRACE_KEYS",
    "normalize_llm_verification",
    "resolution_trace_from_attempted",
    "resolve_execution_identity",
]

