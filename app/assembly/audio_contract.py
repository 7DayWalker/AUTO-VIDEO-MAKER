"""Provider-agnostic audio facts and final-audio policy.

Providers describe the media they produced.  Assembly uses the policy in this
module to decide which validated tracks reach the final delivery file.
"""

from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class AudioPolicy(str, Enum):
    """Supported final-audio selection policies."""

    NARRATION_ONLY = "narration_only"
    NATIVE_ONLY = "native_only"
    NATIVE_PLUS_NARRATION = "native_plus_narration"


DEFAULT_AUDIO_POLICY = AudioPolicy.NARRATION_ONLY


class AudioMediaFacts(BaseModel):
    """Facts observed for the first usable audio stream in a media file.

    ``is_synthetic`` is an additive provenance fact for streams deliberately
    created by AVM (for example, ImageMotion's required silent AAC stream). It
    prevents a topology-normalizing silence track from being treated as native
    scene audio while keeping the required facts provider-agnostic.
    """

    has_audio: bool = False
    audio_codec: Optional[str] = None
    audio_duration: Optional[float] = Field(default=None, ge=0)
    sample_rate: Optional[int] = Field(default=None, ge=1)
    channels: Optional[int] = Field(default=None, ge=1)
    is_synthetic: bool = False

    @property
    def has_native_audio(self) -> bool:
        """Whether the media contains usable non-synthetic scene audio."""
        return self.has_audio and not self.is_synthetic

    @classmethod
    def from_probe_data(
        cls,
        data: Optional[Dict[str, Any]],
        *,
        is_synthetic: bool = False,
    ) -> "AudioMediaFacts":
        """Build facts from a validation/probe dictionary safely."""
        data = data or {}
        has_audio = bool(data.get("has_audio", False))
        return cls(
            has_audio=has_audio,
            audio_codec=data.get("audio_codec") if has_audio else None,
            audio_duration=data.get("audio_duration") if has_audio else None,
            sample_rate=data.get("sample_rate") if has_audio else None,
            channels=data.get("channels") if has_audio else None,
            is_synthetic=bool(is_synthetic) if has_audio else False,
        )


class AudioSelection(BaseModel):
    """The concrete tracks selected for one assembly invocation."""

    policy: AudioPolicy
    scene_native_audio: Optional[Path] = None
    narration: Optional[Path] = None
    music: Optional[Path] = None
    fallback_used: bool = False


def normalize_audio_policy(value: Optional[str]) -> AudioPolicy:
    """Normalize a persisted/configured policy, defaulting legacy projects."""
    if value is None or not str(value).strip():
        return DEFAULT_AUDIO_POLICY
    if isinstance(value, AudioPolicy):
        return value
    try:
        return AudioPolicy(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(
            f"Invalid audio policy '{value}'. Valid: "
            f"{[policy.value for policy in AudioPolicy]}"
        ) from exc


def select_audio_inputs(
    policy: Optional[str],
    *,
    scene_native_audio: Optional[Path],
    narration: Optional[Path],
    music: Optional[Path],
) -> AudioSelection:
    """Resolve policy semantics into explicit mixer inputs.

    Music remains an additive lower-level assembly capability. The policy only
    controls competition between native scene audio and AVM narration.
    """
    resolved = normalize_audio_policy(policy)
    has_native = scene_native_audio is not None
    has_narration = narration is not None
    fallback_used = False

    if resolved == AudioPolicy.NARRATION_ONLY:
        if has_narration:
            selected_native = None
            selected_narration = narration
        elif has_native:
            # Backward-compatible fallback when no global narration exists.
            selected_native = scene_native_audio
            selected_narration = None
        else:
            selected_native = None
            selected_narration = None
    elif resolved == AudioPolicy.NATIVE_ONLY:
        if has_native:
            selected_native = scene_native_audio
            selected_narration = None
        else:
            # Deterministic fallback: native_only uses narration if native audio
            # is unavailable rather than silently producing no spoken track.
            selected_native = None
            selected_narration = narration
            fallback_used = has_narration
    else:  # native_plus_narration
        selected_native = scene_native_audio
        selected_narration = narration

    return AudioSelection(
        policy=resolved,
        scene_native_audio=selected_native,
        narration=selected_narration,
        music=music,
        fallback_used=fallback_used,
    )