from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.content.model_registry import (
    JOB_REQUIREMENTS,
    JOB_SOURCE_TRANSCRIPT_RECAP,
    JOB_SOURCE_VIDEO_RECAP,
    get_model,
    model_meets_job,
)
from app.content.model_resolver import resolve_model
from app.production.models import (
    NarrativeBeat,
    NarrationArtifact,
    NarrationAudioArtifact,
    NarrationSourceRef,
    SourceAsset,
    SourceTranscript,
)
from app.production.storage import (
    narration_audio_path,
    production_dir,
    save_narration_artifact,
    save_narration_audio,
)
from app.production.recap_budget import resolve_recap_budget, chapter_planning_timeout_seconds
from app.voice.config import (
    TTS_OUTPUT_FORMAT,
    TTS_PITCH,
    TTS_PROVIDER,
    TTS_RATE,
    TTS_VOICE,
    TTS_VOLUME,
)
from app.voice.exceptions import VoiceProviderError
from app.voice.generator import get_provider
from app.voice.validator import validate_audio_file


logger = logging.getLogger(__name__)


# True canonical identities (provider-neutral). Deprecated aliases retained via registry.
SUMMARY_STANDARD_MODEL = "qwen-3.8-flash"
SUMMARY_PRO_MODEL = "gemini-3-flash-preview"

# Deprecated aliases retained for backward compat -- NOT active canonical identities.
# Resolved via app.content.model_registry._DEPRECATED_ALIASES / _normalize_summary_alias.
_SUMMARY_PRO_LEGACY_ALIAS = "summary-pro-gemini-2.0-flash"
_SUMMARY_ALIAS_MAP: dict[str, str] = {
    "summary-pro-gemini-3-flash-preview": "gemini-3-flash-preview",
    "summary-pro-gemini-2.0-flash": "gemini-3-flash-preview",
    "summary-pro-qwen-vl-max": "qwen-vl-max",
    "summary-pro-gpt4o": "gpt-4o",
    "summary-standard-qwen-flash": "qwen-3.8-flash",
}


def _normalize_summary_alias(model_id: str | None) -> str | None:
    if model_id is None:
        return None
    key = model_id.strip()
    if not key:
        return key
    return _SUMMARY_ALIAS_MAP.get(key, key)


def standard_auto_model_ids() -> tuple[str, ...]:
    """Configured text/JSON candidates in deterministic catalogue order.

    Keep Standard's existing text-only preference, then the other capable
    canonical identities. Route admission/quarantine/quota remain owned by
    capability_router; this function makes no provider or network request.
    """
    from app.content.model_registry import list_models
    from app.content.capability_router import assess_model_for_job

    pool = [model for model in list_models()
            if model_meets_job(model, JOB_REQUIREMENTS[JOB_SOURCE_TRANSCRIPT_RECAP])[0]]
    pool.sort(key=lambda model: model.capabilities.is_multimodal_capable())
    return tuple(model.id for model in pool if assess_model_for_job(model.id, JOB_SOURCE_TRANSCRIPT_RECAP).eligible)


def pro_auto_model_ids() -> tuple[str, ...]:
    from app.content.model_registry import list_models_for_pro
    from app.content.capability_router import assess_model_for_job, capability_rank

    from app.content.model_registry import VISUAL_OBSERVER_PRIORITY
    pool = sorted(list_models_for_pro(), key=lambda model: (
        model.id == VISUAL_OBSERVER_PRIORITY[-1], -capability_rank(model)))
    return tuple(model.id for model in pool if (model.capabilities.image_input or model.visual_observer_model_id)
                 and assess_model_for_job(model.id, JOB_SOURCE_VIDEO_RECAP).eligible)


def resolve_summary_model(
    mode: str,
    preferred_model_id: str | None = None,
    *,
    selection_mode: str | None = None,
    summary_understanding_model_id: str | None = None,
) -> str:
    """Resolve Summary Understanding Model with AUTO vs EXPLICIT semantics.

    Canonical identity is provider-agnostic (true model id). Provider identity
    is separate and resolved later via app.content.model_resolver.

    Selection modes:
      - AUTO ('auto'): capability-driven -- Standard needs text/JSON; Pro
        needs multimodal video understanding (is_multimodal_capable). Ranked
        deterministically; no cost claim unless pricing metadata exists.
      - EXPLICIT ('explicit'/'manual'): resolve ONLY the requested canonical
        model; no ranking, no cross-model fallback. Same-model provider
        fallback is allowed at execution time via the resolver.

    If selection_mode is None, it is inferred: explicit when a model id is
    supplied, otherwise auto.
    """
    mode = (mode or "standard").strip().lower()
    if mode not in ("standard", "pro"):
        raise ValueError(f"Unknown summary_mode: {mode!r}")
    # Normalize aliases / handle legacy call pattern where explicit id is passed
    # as preferred_model_id or as summary_understanding_model_id.
    explicit_id = summary_understanding_model_id if summary_understanding_model_id is not None else preferred_model_id
    explicit_id = _normalize_summary_alias(explicit_id)

    sel = (selection_mode or "").strip().lower() if selection_mode is not None else ""
    if sel == "manual":
        sel = "explicit"
    if sel and sel not in ("auto", "explicit"):
        raise ValueError(f"Unknown selection_mode: {selection_mode!r} (expected 'auto' or 'explicit'/'manual')")

    # Infer selection mode when not explicitly given
    if not sel:
        if explicit_id is not None:
            sel = "explicit"
        else:
            sel = "auto"
    if explicit_id is not None:
        # A supplied canonical identity is always a lock, even if an older
        # caller also supplied selection_mode='auto'. Never silently ignore it.
        sel = "explicit"

    # EXPLICIT path: the explicit canonical model wins, no ranking
    if sel == "explicit":
        if explicit_id is None or not str(explicit_id).strip():
            raise ValueError("Explicit model selection requires a canonical model id")
        m = get_model(explicit_id)
        if mode == "standard":
            capable, missing = model_meets_job(m, JOB_REQUIREMENTS[JOB_SOURCE_TRANSCRIPT_RECAP])
            if not capable:
                raise ValueError(
                    f"Model {m.id!r} cannot satisfy Summary Standard requirements: {', '.join(missing)}"
                )
        if mode == "pro" and not model_meets_job(m, JOB_REQUIREMENTS[JOB_SOURCE_VIDEO_RECAP])[0]:
            raise ValueError(
                f"Model {explicit_id!r} is not multimodal-capable; cannot satisfy Summary Pro requirements"
            )
        return m.id

    # AUTO path: capability-driven (no hardcoded Gemini owner, no provider privilege)
    if mode == "pro":
        from app.content.model_registry import list_models_for_pro
        pool = list(list_models_for_pro())
        if not pool:
            raise ValueError("No multimodal-capable summary understanding model available for Pro")
        def _score(m):
            s = 0
            if m.capabilities.multimodal: s += 8
            if m.capabilities.video: s += 4
            if m.capabilities.vision: s += 2
            if m.capabilities.long_context: s += 1
            return s
        # Stable deterministic capability ranking: higher score first, then registry order
        # No "cheapest" claim -- no trustworthy pricing metadata exists yet.
        pool.sort(key=_score, reverse=True)
        return pool[0].id
    if mode == "standard":
        configured = standard_auto_model_ids()
        if configured:
            return configured[0]
        # Catalogue fallback is metadata only (also used by offline mock runs).
        # Real Auto execution below refuses when no configured route exists.
        from app.content.model_registry import list_models
        for m in list_models():
            if model_meets_job(m, JOB_REQUIREMENTS[JOB_SOURCE_TRANSCRIPT_RECAP])[0] and not m.capabilities.is_multimodal_capable():
                return m.id
        for m in list_models():
            if model_meets_job(m, JOB_REQUIREMENTS[JOB_SOURCE_TRANSCRIPT_RECAP])[0]:
                return m.id
        raise ValueError("No text/JSON-capable summary understanding model available for Standard")
    raise ValueError(f"Unknown summary mode: {mode!r}")


def resolve_narration_model(canonical_model_id: str = "qwen-3.8-flash") -> str:
    get_model(canonical_model_id)
    try:
        resolved = resolve_model(canonical_model_id, mode="exact", try_next_on_failure=True)
        if not resolved.provider_instance.is_available():
            return canonical_model_id
        return resolved.canonical_model_id
    except Exception:
        return canonical_model_id


def _segment_windows(segments: Sequence, max_per_beat: int = 8) -> List[List[Tuple[int, object]]]:
    if not segments:
        return []
    size = max(1, int(max_per_beat))
    windows: List[List[Tuple[int, object]]] = []
    for start in range(0, len(segments), size):
        chunk = list(enumerate(segments[start : start + size], start=start))
        if chunk:
            windows.append(chunk)
    return windows


def _resolve_source_video_path(source_asset: SourceAsset, production_id: str, root: Optional[Path] = None) -> Path:
    from app.production.storage import production_dir as _pd
    rel = Path(source_asset.stored_path)
    base = _pd(production_id, root=root)
    return base / rel


def _standard_source_segments(production_id, source_asset, root):
    from app.production.storage import segments_path_for
    if not segments_path_for(production_id, source_asset.asset_id, root=root).is_file():
        return []
    from app.production.models import SourceSegment
    from app.production.segments import load_segments
    segment_status, segment_payload = load_segments(production_id, source_asset.asset_id, root=root)
    if segment_status != 200:
        raise ValueError("Cannot load validated source-segment evidence for narration")
    return [SourceSegment.model_validate(item) for item in segment_payload["segments"]]


def _build_summary_routed(production_id, unit_id, source_asset, transcript, *, duration, status, root, model_id=None, recap_budget=None, summary_mode="standard"):
    from app.content.capability_router import assess_model_for_job
    from app.content.model_resolver import AllExactModelProvidersFailedError
    from app.production.narration_real import (
        ai_grounded_plan, standard_transient_failure_category, SummaryStandardTemporarilyUnavailableError,
        SummaryProTemporarilyUnavailableError,
    )
    is_pro = summary_mode == "pro"
    # Two-stage chapters include a separate vision request and reasoning-model
    # text request. Keep a bounded operation without the old single-stage limit.
    job_id = JOB_SOURCE_VIDEO_RECAP if is_pro else JOB_SOURCE_TRANSCRIPT_RECAP
    label = "Pro" if is_pro else "Standard"
    candidates = (model_id,) if model_id is not None else (pro_auto_model_ids() if is_pro else standard_auto_model_ids())
    composite = is_pro and any(get_model(candidate).visual_observer_model_id for candidate in candidates)
    long_form = recap_budget is not None and recap_budget.target_duration_seconds >= 720
    deadline = (time.monotonic() + chapter_planning_timeout_seconds(recap_budget, composite=composite) + 30
                if is_pro or long_form else None)
    if not candidates:
        raise RuntimeError(f"Summary {label} has no eligible route: configure an available {'vision' if is_pro else 'text/JSON'} provider.")
    segments = [] if is_pro else _standard_source_segments(production_id, source_asset, root)
    failed = False
    last_error = None
    attempted = set()
    for candidate_id in candidates:
        # Existing exact-model resolver remains the only provider factory gate.
        # Each route is visited once, with bounded per-window output repair.
        # Explicit selection can change routes, never canonical model identity.
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                break
            routes = assess_model_for_job(candidate_id, job_id).eligible_routes()
            if not any((route.provider, route.provider_model_id) not in attempted for route in routes):
                break
            try:
                resolved = resolve_model(candidate_id, mode="exact", try_next_on_failure=True,
                    job_id=job_id, excluded_bindings=tuple(attempted))
            except AllExactModelProvidersFailedError as exc:
                if exc.last_error is not None and standard_transient_failure_category(exc.last_error) is None:
                    raise
                previous_attempts = len(attempted)
                attempted.update((item["provider"], item["provider_model_id"]) for item in exc.attempted)
                last_error = exc.last_error or exc
                failed = True
                if len(attempted) == previous_attempts:
                    break
                continue
            attempted.update((item["provider"], item["provider_model_id"]) for item in resolved.attempted)
            failed = failed or bool(resolved.attempted)
            route_id = (resolved.provider, resolved.provider_model_id)
            attempted.add(route_id)
            try:
                if is_pro:
                    composite = get_model(candidate_id).visual_observer_model_id is not None
                    if not composite and not resolved.provider_instance.supports_multimodal():
                        from app.content.exceptions import LLMModelUnavailableError
                        raise LLMModelUnavailableError()
                    # A single overloaded model must not consume the entire
                    # five-minute command budget before Auto can try another.
                    if not composite and hasattr(resolved.provider_instance, "_timeout"):
                        resolved.provider_instance._timeout = min(resolved.provider_instance._timeout, 45)
                    from app.production.narration_pro import build_pro_multimodal_plan
                    artifact = build_pro_multimodal_plan(production_id, unit_id, source_asset, transcript,
                        target_duration_seconds=duration, summary_model_id=resolved.canonical_model_id,
                        prov=resolved.provider_instance, root=root, recap_budget=recap_budget, deadline=deadline)
                else:
                    artifact = ai_grounded_plan(production_id, unit_id, source_asset, transcript,
                        target_duration_seconds=duration, prov=resolved.provider_instance,
                        status=status, source_segments=segments,
                        context_tokens=get_model(candidate_id).capabilities.context_tokens, output_attempts=2,
                        recap_budget=recap_budget, deadline=deadline)
            except Exception as exc:
                category = standard_transient_failure_category(exc)
                if category is None:
                    raise
                logger.warning("%s narration route failed: canonical=%s provider=%s model=%s category=%s",
                               label, resolved.canonical_model_id, resolved.provider, resolved.provider_model_id, category)
                last_error = exc
                failed = True
                continue
            artifact.summary_understanding_model_id = resolved.canonical_model_id
            artifact.summary_selection_mode = "explicit" if model_id is not None else "auto"
            artifact.summary_failover_used = failed
            artifact.summary_mode = summary_mode
            artifact.target_length_preset, artifact.narration_budget = recap_budget.preset, recap_budget
            if artifact.understanding_method is None:
                artifact.understanding_method = ("frame_sequence" if getattr(artifact, "_pro_visual_method", "") == "frame_based_visual_analysis"
                    else "native_video") if is_pro else "transcript_audio"
            logger.info("%s narration grounded: canonical=%s provider=%s model=%s beats=%d failover=%s",
                        label, resolved.canonical_model_id, resolved.provider, resolved.provider_model_id, len(artifact.beats), failed)
            return artifact
    if model_id is not None:
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Canonical model {model_id!r} has no eligible route for Summary {label} planning")
    # No raw provider text, credential values, prompt or transcript in the
    # browser-facing error. Failed attempts never persist a narration artifact.
    error_type = SummaryProTemporarilyUnavailableError if is_pro else SummaryStandardTemporarilyUnavailableError
    raise error_type(
        f"Summary {label}'s eligible providers are temporarily unavailable or returned unusable narration. "
        "No narration was saved; check provider access/model availability and retry."
    )


def build_grounded_narration_plan(
    production_id: str,
    unit_id: str,
    source_asset: SourceAsset,
    source_transcript: SourceTranscript,
    *,
    target_duration_seconds: Optional[float] = None,
    target_minutes: Optional[float] = None,
    target_length_preset: str = "auto",
    # None means "not supplied" (capability-driven AUTO for Standard and Pro).
    # An explicit value is ALWAYS treated as explicit model selection.
    canonical_model_id: Optional[str] = None,
    # New explicit Summary Understanding Model seam (auto vs explicit).
    # Kept alongside canonical_model_id for backwards compat; if both supplied,
    # summary_understanding_model_id wins.
    summary_understanding_model_id: Optional[str] = None,
    summary_understanding_mode: Optional[str] = None,
    status: str = "grounded",
    llm_provider_override: Optional[Any] = None,
    mock_mode: bool = False,
    summary_mode: str = "standard",
    root: Optional[Path] = None,
) -> NarrationArtifact:
    # summary_mode is the product-level AI Summary mode (standard vs pro)
    mode = (summary_mode or "standard").strip().lower()
    if mode not in ("standard", "pro"):
        raise ValueError(f"Unknown summary_mode: {summary_mode!r}")
    if not source_transcript.segments:
        raise ValueError("narration generation requires at least one transcript segment")
    if source_asset.asset_id != source_transcript.asset_id:
        raise ValueError("source asset and transcript asset_id mismatch")
    # Resolve summary understanding model provider-neutrally (capability-driven)
    # USER selects canonical model (true model id); AVM selects provider internally.
    # Pro eligibility = multimodal video understanding capability, not provider name.
    if mode == "pro":
        # Determine explicit vs auto for Pro
        # Precedence: summary_understanding_model_id > canonical_model_id (when non-default)
        # summary_understanding_mode: auto | manual | explicit (manual is alias for explicit)
        effective_explicit = summary_understanding_model_id if summary_understanding_model_id is not None else canonical_model_id
        sel_mode = summary_understanding_mode
        # Delegate to resolver which enforces AUTO vs EXPLICIT contract (no cross-model fallback, no downgrade)
        if effective_explicit is not None or sel_mode is not None:
            summary_model_id = resolve_summary_model(
                "pro",
                preferred_model_id=effective_explicit,
                selection_mode=sel_mode,
                summary_understanding_model_id=effective_explicit,
            )
        else:
            summary_model_id = resolve_summary_model("pro", selection_mode="auto")
    else:
        # Standard uses the same canonical AUTO/EXPLICIT seam, with text+JSON
        # eligibility. A visual-capable explicit model is never substituted.
        summary_model_id = resolve_summary_model(
            "standard",
            preferred_model_id=canonical_model_id,
            selection_mode=summary_understanding_mode,
            summary_understanding_model_id=summary_understanding_model_id,
        )

    budget = resolve_recap_budget(source_asset.duration, target_length_preset=target_length_preset,
                                  target_duration_seconds=target_duration_seconds, target_minutes=target_minutes,
                                  mock_mode=mock_mode)
    duration = budget.target_duration_seconds

    # An injected adapter still exercises real planning unless mock is explicit.
    if mock_mode:
        if mode == "pro":
            from app.content.model_registry import get_model as _get_model
            _m = None
            try:
                _m = _get_model(summary_model_id)
                if not model_meets_job(_m, JOB_REQUIREMENTS[JOB_SOURCE_VIDEO_RECAP])[0]:
                    raise ValueError(f"Pro requires multimodal model; {summary_model_id!r} is not multimodal")
            except KeyError:
                pass
            prov_mm = llm_provider_override
            if prov_mm is not None and not getattr(_m, "visual_observer_model_id", None) and hasattr(prov_mm, "supports_multimodal"):
                try:
                    if not prov_mm.supports_multimodal():
                        raise RuntimeError(f"Provider {getattr(prov_mm,'name','?')} does not support multimodal Pro (no silent downgrade)")
                except NotImplementedError:
                    raise RuntimeError("Provider does not support multimodal Pro")
            from app.production.narration_mock import mock_grounded_plan
            _src_path = _resolve_source_video_path(source_asset, production_id, root=root)
            if not _src_path.is_file():
                pass
            # Pro mock exercises multimodal seam: count as multimodal call
            if prov_mm is not None and not getattr(_m, "visual_observer_model_id", None) and hasattr(prov_mm, "generate_multimodal"):
                try:
                    src_arg = str(_src_path) if _src_path.is_file() else source_asset.stored_path
                    prov_mm.generate_multimodal(prompt="Pro multimodal probe: source video + transcript", system_prompt="multimodal", video=src_arg, images=None)
                except NotImplementedError:
                    pass
                except Exception:
                    pass
            return mock_grounded_plan(production_id, unit_id, source_asset, source_transcript, target_duration_seconds=duration, status=status, provider_override=llm_provider_override)
        from app.production.narration_mock import mock_grounded_plan
        return mock_grounded_plan(production_id, unit_id, source_asset, source_transcript, target_duration_seconds=duration, status=status, provider_override=llm_provider_override)
    if llm_provider_override is None:
        explicit = canonical_model_id is not None or summary_understanding_model_id is not None
        return _build_summary_routed(production_id, unit_id, source_asset, source_transcript,
            duration=duration, status=status, root=root, model_id=summary_model_id if explicit else None,
            recap_budget=budget, summary_mode=mode)
    # Real provider path: hierarchical multimodal for Pro, text-only for Standard
    prov_real = llm_provider_override
    if not prov_real.is_available():
        raise RuntimeError(f"Canonical model {summary_model_id!r} is not available for narration planning")
    if mode == "pro":
        if not get_model(summary_model_id).visual_observer_model_id and not getattr(prov_real, "supports_multimodal", lambda: False)():
            raise RuntimeError("Pro requires a multimodal provider (no silent downgrade)")
        # Long-form hierarchical multimodal understanding (bounded windows)
        from app.production.narration_pro import build_pro_multimodal_plan
        artifact = build_pro_multimodal_plan(production_id, unit_id, source_asset, source_transcript, target_duration_seconds=duration, summary_model_id=summary_model_id, prov=prov_real, root=root, recap_budget=budget)
        artifact.target_length_preset = budget.preset
        artifact.narration_budget = budget
        artifact.summary_selection_mode = "explicit" if canonical_model_id is not None or summary_understanding_model_id is not None else "auto"
        return artifact
    from app.production.narration_real import ai_grounded_plan
    source_segments = _standard_source_segments(production_id, source_asset, root)
    artifact = ai_grounded_plan(
        production_id, unit_id, source_asset, source_transcript,
        target_duration_seconds=duration, prov=prov_real, status=status, source_segments=source_segments,
        recap_budget=budget, output_attempts=2 if budget.enforce_depth else 1,
    )
    artifact.summary_understanding_model_id = summary_model_id
    artifact.summary_mode = "standard"
    artifact.understanding_method = "transcript_audio"
    artifact.summary_selection_mode = "explicit" if canonical_model_id is not None or summary_understanding_model_id is not None else "auto"
    return artifact


def validate_narration_artifact(
    artifact: NarrationArtifact,
    *,
    source_asset: Optional[SourceAsset] = None,
    source_transcript: Optional[SourceTranscript] = None,
) -> None:
    if (source_asset is not None and artifact.source_media_path is not None
            and artifact.source_media_path != source_asset.stored_path):
        raise ValueError("narration source media provenance does not match its registered asset")
    if (source_transcript is not None and artifact.source_transcript_path is not None
            and artifact.source_transcript_path != source_transcript.transcript_path):
        raise ValueError("narration source transcript provenance does not match its grounding sidecar")
    if source_asset is not None and artifact.source_asset_id != source_asset.asset_id:
        raise ValueError("artifact source_asset_id does not match the supplied source_asset")
    if artifact.target_duration_seconds <= 0:
        raise ValueError("target_duration_seconds must be positive")
    if source_asset is not None and artifact.target_duration_seconds > source_asset.duration * 2.5:
        raise ValueError("target_duration_seconds is implausible for the source asset duration")
    if not artifact.narration_text.strip():
        raise ValueError("narration_text must not be empty")
    if not artifact.beats:
        raise ValueError("narration artifact must contain at least one beat")

    orders = [beat.order for beat in artifact.beats]
    if orders != sorted(orders):
        raise ValueError("narrative beats must be ordered deterministically")

    for beat in artifact.beats:
        if beat.order < 1:
            raise ValueError(f"beat order must be positive: {beat.order}")
        if not beat.source_refs:
            raise ValueError(f"beat '{beat.title}' has no grounded source refs")
        if not beat.transcript_refs and not any(ref.visual_frame_timestamps for ref in beat.source_refs):
            raise ValueError(f"beat '{beat.title}' has no transcript references")
        for ref in beat.source_refs:
            if source_asset is not None and ref.asset_id != source_asset.asset_id:
                raise ValueError(
                    f"beat '{beat.title}' references asset '{ref.asset_id}' but expected '{source_asset.asset_id}'"
                )
            if ref.start_seconds < 0 or ref.end_seconds <= ref.start_seconds:
                raise ValueError(f"beat '{beat.title}' has invalid source timestamps")
            if source_asset is not None and ref.end_seconds > source_asset.duration:
                raise ValueError(f"beat '{beat.title}' extends beyond source duration")
            if ref.visual_frame_timestamps:
                if artifact.summary_mode != "pro":
                    raise ValueError("Only Summary Pro may use frame-grounded evidence")
                supplied = {ts for chapter in artifact.chapters for ts in chapter.frame_timestamps}
                if any(ts not in supplied for ts in ref.visual_frame_timestamps):
                    raise ValueError("Narration cites a visual frame not inspected in its chapter evidence")

        if source_transcript is not None:
            transcript_count = len(source_transcript.segments)
            for idx in beat.transcript_refs:
                try:
                    position = int(idx)
                except ValueError:
                    raise ValueError(f"transcript reference '{idx}' is not an integer index") from None
                if position < 0 or position >= transcript_count:
                    raise ValueError(
                        f"transcript reference '{idx}' is outside the source transcript"
                    )

    if source_asset is not None:
        for ref in artifact.source_refs:
            if ref.asset_id != source_asset.asset_id:
                raise ValueError("artifact source refs must all target the canonical source asset")
            if ref.end_seconds > source_asset.duration:
                raise ValueError("artifact source ref ends beyond source duration")

    return None


def _resolve_narration_provider(provider_name: Optional[str] = None):
    """Resolve the configured canonical provider without changing synthesis modes."""
    chosen = (provider_name or TTS_PROVIDER or "edge_tts").strip().lower()
    if not chosen:
        chosen = "edge_tts"

    provider = get_provider(chosen)
    if not provider.is_available():
        raise VoiceProviderError(
            chosen,
            "Provider is unavailable; select provider_name='mock' explicitly "
            "to generate test-only synthetic audio.",
        )
    return provider


def _pace_long_narration(output_path, artifact, validation):
    """Pitch-preserving speech pacing, never silence padding or clipped words.

    Word targets alone cannot guarantee runtime across TTS voices. Only a real
    multi-chapter preset with measured duration bounds opts into this correction.
    """
    budget = artifact.narration_budget
    original = float(validation.duration or 0)
    if (artifact.planning_strategy != "multi_chapter" or budget is None
            or budget.min_duration_seconds is None or not validation.valid):
        return validation, original, 1.0
    words = len(artifact.narration_text.split())
    # Intersect runtime with a natural spoken cadence. A sparse script must be
    # rebuilt, not stretched to nine minutes at 130 WPM.
    minimum = max(budget.min_duration_seconds, words * 60 / 180)
    maximum = min(budget.max_duration_seconds, words * 60 / 155)
    if minimum > maximum:
        raise ValueError("Narration word density cannot meet both runtime and 155-180 WPM; rebuild the script")
    if minimum <= original <= maximum:
        return validation, original, 1.0
    preferred = words * 60 / 170
    target = min(max(preferred, minimum + .03), maximum - .03) if maximum - minimum > .06 else (minimum + maximum) / 2
    factor = original / target
    if not .75 <= factor <= 1.35:
        raise ValueError("Narration cannot meet the requested runtime at a natural speaking pace; rebuild with a suitable word budget or voice")
    from app.assembly.ffmpeg import run_ffmpeg
    paced = output_path.with_name(output_path.stem + ".paced" + output_path.suffix)
    try:
        run_ffmpeg(["-y", "-i", str(output_path), "-vn", "-af", f"atempo={factor:.8f}", str(paced)],
                   "pacing long recap narration without silence padding")
        measured = validate_audio_file(paced)
        if not measured.valid or not minimum <= float(measured.duration or 0) <= maximum:
            raise ValueError("Paced narration did not meet the requested measured duration range")
        paced.replace(output_path)
        return measured, original, factor
    finally:
        paced.unlink(missing_ok=True)


def synthesize_narration_audio(
    artifact: NarrationArtifact,
    *,
    root: Optional[Path] = None,
    provider_name: Optional[str] = None,
    voice: Optional[str] = None,
    rate: Optional[str] = None,
    pitch: str = TTS_PITCH,
    volume: str = TTS_VOLUME,
    output_format: str = TTS_OUTPUT_FORMAT,
    mock_mode: bool = False,
) -> NarrationAudioArtifact:
    """Validate canonical TTS audio, or an explicitly requested bounded sandbox tone."""
    if not isinstance(mock_mode, bool):
        raise ValueError("mock_mode must be a boolean")
    validate_narration_artifact(artifact)
    if not artifact.narration_text.strip():
        raise ValueError("narration_text must not be empty")

    production_id = artifact.production_id
    unit_id = artifact.unit_id
    output_path = narration_audio_path(
        production_id,
        unit_id,
        root=root,
        output_format=output_format,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    from app.production.synthesis import (
        recap_voice, recap_rate, synthesize_recap_segments, tighten_narration_silence, master_narration_audio,
    )
    chosen_voice = recap_voice(voice)
    chosen_rate = recap_rate(rate)
    relative_root = production_dir(production_id, root=root)
    relative_path = output_path.relative_to(relative_root).as_posix()

    try:
        synthesis_processing = {}
        if mock_mode or (provider_name or "").strip().lower() == "mock":
            from app.production.sandbox import create_sandbox_audio, sandbox_duration
            create_sandbox_audio(output_path, sandbox_duration(artifact.target_duration_seconds))
            chosen_voice = "sandbox-tone" if mock_mode else voice or "sandbox-tone"
            provider_id = "mock"
        else:
            provider = _resolve_narration_provider(provider_name)
            synthesis_processing = asyncio.run(synthesize_recap_segments(
                artifact, provider, output_path, voice=chosen_voice, rate=chosen_rate, pitch=pitch, volume=volume))
            provider_id = provider.name
        validation = validate_audio_file(output_path)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors) or "audio validation failed")
        if provider_id != "mock":
            logger.info("Recap TTS complete: words=%d duration=%.3fs", len(artifact.narration_text.split()), validation.duration)
        original_duration, tempo_factor = float(validation.duration or 0), 1.0
        silence_trimmed = 0.0
        audio_processing = {}
        if provider_id != "mock":
            validation, silence_trimmed = tighten_narration_silence(output_path, validation)
            validation, _, tempo_factor = _pace_long_narration(output_path, artifact, validation)
            logger.info("Recap timing complete; mastering broadcast narrator: duration=%.3fs", validation.duration)
            validation, audio_processing = master_narration_audio(output_path, validation)
            audio_processing["synthesis"] = synthesis_processing

        metadata = NarrationAudioArtifact(
            narration_id=artifact.narration_id,
            production_id=production_id,
            unit_id=unit_id,
            audio_path=relative_path,
            provider=provider_id,
            voice=chosen_voice,
            format=(output_format or TTS_OUTPUT_FORMAT).lower(),
            duration_seconds=float(validation.duration or 0.0),
            file_size=int(validation.file_size or output_path.stat().st_size),
            sample_rate=validation.sample_rate,
            channels=validation.channels,
            codec=validation.codec,
            status="completed",
            generated_at=datetime.now().isoformat(),
            original_duration_seconds=original_duration,
            tempo_factor=tempo_factor,
            synthesis_rate=chosen_rate if provider_id != "mock" else None,
            words_per_minute=(len(artifact.narration_text.split()) * 60 / float(validation.duration)) if provider_id != "mock" else None,
            silence_trimmed_seconds=silence_trimmed,
            audio_processing=audio_processing,
        )
        save_narration_audio(metadata, production_id=production_id, unit_id=unit_id, root=root)

        artifact.audio_artifact_path = relative_path
        artifact.audio_duration_seconds = metadata.duration_seconds
        artifact.audio_provider = metadata.provider
        artifact.audio_voice = metadata.voice
        artifact.audio_format = metadata.format
        artifact.audio_status = "completed"
        save_narration_artifact(
            artifact,
            production_id=production_id,
            unit_id=unit_id,
            root=root,
        )
        return metadata
    except Exception:
        artifact.audio_artifact_path = relative_path
        artifact.audio_status = "failed"
        save_narration_artifact(
            artifact,
            production_id=production_id,
            unit_id=unit_id,
            root=root,
        )
        raise


__all__ = [
    "resolve_narration_model",
    "build_grounded_narration_plan",
    "validate_narration_artifact",
    "synthesize_narration_audio",
]
