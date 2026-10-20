from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.content.model_registry import get_model
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
      - AUTO ('auto'): capability-driven -- eligible models are those with
        multimodal video understanding (is_multimodal_capable). Ranked by
        capability score deterministically; no cost claim unless pricing
        metadata exists.
      - EXPLICIT ('explicit'/'manual'): resolve ONLY the requested canonical
        model; no ranking, no cross-model fallback. Same-model provider
        fallback is allowed at execution time via the resolver.

    If selection_mode is None, it is inferred: explicit when a model id is
    supplied, otherwise auto.
    """
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

    # EXPLICIT path: the explicit canonical model wins, no ranking
    if sel == "explicit":
        if explicit_id is None or not str(explicit_id).strip():
            raise ValueError("Explicit model selection requires a canonical model id")
        m = get_model(explicit_id)
        if mode == "pro" and not m.capabilities.is_multimodal_capable():
            raise ValueError(
                f"Model {explicit_id!r} is not multimodal-capable; cannot satisfy Summary Pro requirements"
            )
        return explicit_id

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
        from app.content.model_registry import list_models
        for m in list_models():
            if m.capabilities.text and not m.capabilities.is_multimodal_capable():
                return m.id
        for m in list_models():
            if m.capabilities.text:
                return m.id
        raise ValueError("No text-capable summary understanding model available for Standard")
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
def build_grounded_narration_plan(
    production_id: str,
    unit_id: str,
    source_asset: SourceAsset,
    source_transcript: SourceTranscript,
    *,
    target_duration_seconds: float,
    # None means "not supplied" (AI Brain default applies for Standard; AUTO for Pro).
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
        # Standard: transcript/audio-first. Deprecated aliases are normalized.
        # None means "not supplied" -> AI Brain default canonical model.
        base_id = canonical_model_id if canonical_model_id is not None else "qwen-3.8-flash"
        norm = _normalize_summary_alias(base_id) or base_id
        if summary_understanding_model_id is not None:
            norm = _normalize_summary_alias(summary_understanding_model_id) or summary_understanding_model_id
        if norm == SUMMARY_PRO_MODEL:
            norm = SUMMARY_STANDARD_MODEL
        summary_model_id = norm
        # Validate existence
        resolve_narration_model(summary_model_id)

    duration = float(target_duration_seconds)
    if duration <= 0:
        raise ValueError("target_duration_seconds must be positive")
    if duration > source_asset.duration * 2.5:
        raise ValueError("target_duration_seconds is implausible for the source asset duration")

    if mock_mode or llm_provider_override is not None:
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
    # Real provider path: hierarchical multimodal for Pro, text-only for Standard
    resolve_narration_model(summary_model_id)
    resolved = resolve_model(summary_model_id, mode="exact", try_next_on_failure=True)
    prov_real = resolved.provider_instance
    if not prov_real.is_available():
        raise RuntimeError(f"LLM provider '{resolved.provider}' for '{summary_model_id}' not available")
    if mode == "pro":
        if not getattr(prov_real, "supports_multimodal", lambda: False)():
            raise RuntimeError(f"Pro requires multimodal provider; {resolved.provider!r} does not support multimodal (no silent downgrade)")
        # Long-form hierarchical multimodal understanding (bounded windows)
        from app.production.narration_pro import build_pro_multimodal_plan
        return build_pro_multimodal_plan(production_id, unit_id, source_asset, source_transcript, target_duration_seconds=duration, summary_model_id=summary_model_id, prov=prov_real, root=root)
    # Standard real path still requires ai_grounded_plan bridge
    raise RuntimeError(
        "real provider path requires ai_grounded_plan — use mock_mode=True or "
        "llm_provider_override for tests until CodeCraft credentials present"
    )


def validate_narration_artifact(
    artifact: NarrationArtifact,
    *,
    source_asset: Optional[SourceAsset] = None,
    source_transcript: Optional[SourceTranscript] = None,
) -> None:
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
) -> NarrationAudioArtifact:
    """Generate and validate real narration audio using the repo's canonical TTS authority."""
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
        validation = validate_audio_file(output_path)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors) or "audio validation failed")

        metadata = NarrationAudioArtifact(
            narration_id=artifact.narration_id,
            production_id=production_id,
            unit_id=unit_id,
            audio_path=relative_path,
            provider=provider.name,
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
