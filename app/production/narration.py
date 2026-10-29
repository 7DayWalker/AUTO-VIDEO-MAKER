from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.content.model_registry import (
    JOB_REQUIREMENTS,
    JOB_SOURCE_TRANSCRIPT_RECAP,
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
    if mode == "standard" and explicit_id is not None:
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
        if mode == "pro" and not m.capabilities.is_multimodal_capable():
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


def _build_standard_routed(production_id, unit_id, source_asset, transcript, *, duration, status, root, model_id=None):
    from app.content.capability_router import assess_model_for_job
    from app.content.model_resolver import AllExactModelProvidersFailedError
    from app.production.narration_real import (
        ai_grounded_plan, standard_transient_failure_category, SummaryStandardTemporarilyUnavailableError,
    )
    candidates = (model_id,) if model_id is not None else standard_auto_model_ids()
    if not candidates:
        raise RuntimeError("Summary Standard has no eligible route: configure an available text/JSON provider.")
    segments = _standard_source_segments(production_id, source_asset, root)
    failed = False
    last_error = None
    attempted = set()
    for candidate_id in candidates:
        # Existing exact-model resolver remains the only provider factory gate.
        # Each route is visited once, with bounded per-window output repair.
        # Explicit selection can change routes, never canonical model identity.
        while True:
            routes = assess_model_for_job(candidate_id, JOB_SOURCE_TRANSCRIPT_RECAP).eligible_routes()
            if not any((route.provider, route.provider_model_id) not in attempted for route in routes):
                break
            try:
                resolved = resolve_model(candidate_id, mode="exact", try_next_on_failure=True,
                    job_id=JOB_SOURCE_TRANSCRIPT_RECAP, excluded_bindings=tuple(attempted))
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
                artifact = ai_grounded_plan(production_id, unit_id, source_asset, transcript,
                    target_duration_seconds=duration, prov=resolved.provider_instance,
                    status=status, source_segments=segments,
                    context_tokens=get_model(candidate_id).capabilities.context_tokens, output_attempts=2)
            except Exception as exc:
                category = standard_transient_failure_category(exc)
                if category is None:
                    raise
                logger.warning("Standard narration route failed: canonical=%s provider=%s model=%s category=%s",
                               resolved.canonical_model_id, resolved.provider, resolved.provider_model_id, category)
                last_error = exc
                failed = True
                continue
            artifact.summary_understanding_model_id = resolved.canonical_model_id
            artifact.summary_selection_mode = "explicit" if model_id is not None else "auto"
            artifact.summary_failover_used = failed
            logger.info("Standard narration grounded: canonical=%s provider=%s model=%s beats=%d failover=%s",
                        resolved.canonical_model_id, resolved.provider, resolved.provider_model_id, len(artifact.beats), failed)
            return artifact
    if model_id is not None:
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Canonical model {model_id!r} has no eligible route for Summary Standard text/JSON planning")
    # No raw provider text, credential values, prompt or transcript in the
    # browser-facing error. Failed attempts never persist a narration artifact.
    raise SummaryStandardTemporarilyUnavailableError(
        "Summary Standard's eligible providers are temporarily unavailable or returned unusable narration. "
        "No narration was saved; check provider access/model availability and retry."
    )


def build_grounded_narration_plan(
    production_id: str,
    unit_id: str,
    source_asset: SourceAsset,
    source_transcript: SourceTranscript,
    *,
    target_duration_seconds: float,
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
        effective_explicit: Optional[str] = None
        sel_mode: Optional[str] = None
        if summary_understanding_model_id is not None:
            effective_explicit = summary_understanding_model_id
            sel_mode = summary_understanding_mode
        elif summary_understanding_mode is not None:
            # Mode given without id: if manual/explicit but no id -> will be rejected as missing explicit
            sel_mode = summary_understanding_mode
            if sel_mode.strip().lower() in ("manual", "explicit") and canonical_model_id is not None:
                effective_explicit = canonical_model_id
        elif canonical_model_id is not None:
            # Back-compat: legacy caller passed explicit canonical model via canonical_model_id.
            # Deprecated aliases are normalized inside resolve_summary_model.
            effective_explicit = canonical_model_id
            # sel_mode inferred as explicit inside resolver
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

    duration = float(target_duration_seconds)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("target_duration_seconds must be positive")
    if duration > source_asset.duration * 2.5:
        raise ValueError("target_duration_seconds is implausible for the source asset duration")

    # Pro's historical mock override seam is retained; Standard overrides are
    # real planner adapters unless mock_mode was explicitly requested.
    if mock_mode or (mode == "pro" and llm_provider_override is not None):
        if mode == "pro":
            from app.content.model_registry import get_model as _get_model
            try:
                _m = _get_model(summary_model_id)
                if not _m.capabilities.is_multimodal_capable():
                    raise ValueError(f"Pro requires multimodal model; {summary_model_id!r} is not multimodal")
            except KeyError:
                pass
            prov_mm = llm_provider_override
            if prov_mm is not None and hasattr(prov_mm, "supports_multimodal"):
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
            if prov_mm is not None and hasattr(prov_mm, "generate_multimodal"):
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
    if mode == "standard" and llm_provider_override is None:
        explicit = canonical_model_id is not None or summary_understanding_model_id is not None
        return _build_standard_routed(production_id, unit_id, source_asset, source_transcript,
            duration=duration, status=status, root=root, model_id=summary_model_id if explicit else None)
    # Real provider path: hierarchical multimodal for Pro, text-only for Standard
    if mode == "pro":
        resolve_narration_model(summary_model_id)
    if llm_provider_override is not None:
        prov_real = llm_provider_override
    else:
        if mode == "standard":
            from app.content.capability_router import assess_model_for_job
            eligibility = assess_model_for_job(summary_model_id, JOB_SOURCE_TRANSCRIPT_RECAP)
            if not eligibility.eligible:
                raise RuntimeError(
                    f"Canonical model {summary_model_id!r} has no eligible route for Summary Standard text/JSON planning"
                )
        resolve_kwargs = {"job_id": JOB_SOURCE_TRANSCRIPT_RECAP} if mode == "standard" else {}
        resolved = resolve_model(summary_model_id, mode="exact", try_next_on_failure=True, **resolve_kwargs)
        prov_real = resolved.provider_instance
    if not prov_real.is_available():
        raise RuntimeError(f"Canonical model {summary_model_id!r} is not available for narration planning")
    if mode == "pro":
        if not getattr(prov_real, "supports_multimodal", lambda: False)():
            raise RuntimeError(f"Pro requires multimodal provider; {resolved.provider!r} does not support multimodal (no silent downgrade)")
        # Long-form hierarchical multimodal understanding (bounded windows)
        from app.production.narration_pro import build_pro_multimodal_plan
        return build_pro_multimodal_plan(production_id, unit_id, source_asset, source_transcript, target_duration_seconds=duration, summary_model_id=summary_model_id, prov=prov_real, root=root)
    from app.production.narration_real import ai_grounded_plan
    source_segments = _standard_source_segments(production_id, source_asset, root)
    artifact = ai_grounded_plan(
        production_id, unit_id, source_asset, source_transcript,
        target_duration_seconds=duration, prov=prov_real, status=status, source_segments=source_segments,
    )
    artifact.summary_understanding_model_id = summary_model_id
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
        if not beat.transcript_refs:
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


def synthesize_narration_audio(
    artifact: NarrationArtifact,
    *,
    root: Optional[Path] = None,
    provider_name: Optional[str] = None,
    voice: Optional[str] = None,
    rate: str = TTS_RATE,
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

    chosen_voice = voice or TTS_VOICE
    relative_root = production_dir(production_id, root=root)
    relative_path = output_path.relative_to(relative_root).as_posix()

    try:
        if mock_mode or (provider_name or "").strip().lower() == "mock":
            from app.production.sandbox import create_sandbox_audio, sandbox_duration
            create_sandbox_audio(output_path, sandbox_duration(artifact.target_duration_seconds))
            chosen_voice = "sandbox-tone" if mock_mode else voice or "sandbox-tone"
            provider_id = "mock"
        else:
            provider = _resolve_narration_provider(provider_name)
            asyncio.run(
                provider.generate(
                    text=artifact.narration_text,
                    output_path=output_path,
                    voice=chosen_voice,
                    rate=rate,
                    pitch=pitch,
                    volume=volume,
                )
            )
            provider_id = provider.name
        validation = validate_audio_file(output_path)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors) or "audio validation failed")

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
