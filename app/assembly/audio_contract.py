"""Provider-agnostic audio facts and final-audio policy.

Providers describe the media they produced.  Assembly uses the policy in this
module to decide which validated tracks reach the final delivery file.

This module owns the ASSEMBLY-LEVEL selection contract only
(:class:`AudioPolicy`).  The PRODUCTION-LEVEL audio INTENT
(``narration`` / ``native`` / ``native_plus_narration`` / ``none``) lives with
the durable production intent (``app.production.models``) and is compiled into
an effective policy at the production/unit execution seam
(``app.orchestrator.production_execution``).  Keeping the two vocabularies
separate preserves the AVM invariant that intent, route, provider/model and
assembly policy are distinct concepts — and it keeps this module free of any
dependency on the intent layer.

``Scene.narration`` text is NEVER an audio decision: nothing here reads plan
narration, and :func:`audio_policy_requires_narration` is the single predicate
that decides whether narrator/TTS audio is required.
"""

from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AudioPolicy(str, Enum):
    """Supported final-audio selection policies."""

    NARRATION_ONLY = "narration_only"
    NATIVE_ONLY = "native_only"
    NATIVE_PLUS_NARRATION = "native_plus_narration"
    #: Explicit silence: no native scene audio, no narrator/TTS track, and no
    #: additive music.  The final artifact carries no audio stream at all.
    NONE = "none"


#: LEGACY / BACKWARD-COMPATIBILITY default for artifacts that predate explicit
#: audio intent (a manifest with no ``assembly_config.audio_policy`` snapshot).
#: The existing V1 product contract is a narrated production, so the safe
#: historical default is narration.  It is deliberately NOT applied as an
#: unconditional global policy: new executions compile their policy from the
#: declared production audio intent at the execution seam.
DEFAULT_AUDIO_POLICY = AudioPolicy.NARRATION_ONLY


def audio_policy_requires_narration(policy: Optional[str]) -> bool:
    """Whether the effective policy requires narrator/TTS audio to exist.

    This is the single predicate the pipeline uses to decide whether the VOICE
    stage runs.  ``native_only`` and ``none`` never require narration, so they
    never trigger TTS — regardless of how much ``Scene.narration`` text a plan
    holds, and regardless of which provider/model produced the scenes.
    """
    return normalize_audio_policy(policy) in {
        AudioPolicy.NARRATION_ONLY,
        AudioPolicy.NATIVE_PLUS_NARRATION,
    }


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
    #: RETAINED for backward compatibility of the serialized shape.  Since the
    #: V1 audio-intent fix, ``native_only`` NEVER falls back to narration (the
    #: explicit intent wins), so no policy sets this flag any more; native
    #: unavailability is reported through ``native_unavailable`` instead.
    fallback_used: bool = False
    #: True when the effective policy wanted native scene audio but the actual
    #: media evidence established none.  Truthful disclosure only: it never
    #: causes a narrator/TTS track to be substituted.
    native_unavailable: bool = False


def audio_selection_warnings(selection: AudioSelection) -> List[str]:
    """Human-readable, truthful warnings for one resolved audio selection.

    Single source of truth so the assembly pipeline and the low-level mixer
    never diverge on what an audio decision means.  A ``native`` intent with
    no native audio is reported as SILENCE, never as a narrator fallback.
    """
    warnings: List[str] = []
    if selection.policy == AudioPolicy.NONE:
        warnings.append(
            "Audio policy=none; the final video intentionally carries no audio "
            "stream (no narrator/TTS and no native audio)."
        )
    elif selection.native_unavailable:
        if selection.policy == AudioPolicy.NATIVE_ONLY:
            warnings.append(
                "Native audio was unavailable; audio intent=native forbids a "
                "narrator/TTS fallback, so the final video carries no audio "
                "stream."
            )
        else:  # native_plus_narration
            warnings.append(
                "Native audio was unavailable; narration is used alone "
                "(audio policy=native_plus_narration)."
            )
    return warnings


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

    Music remains an additive lower-level assembly capability for the policies
    that permit audio. The policy only controls competition between native
    scene audio and AVM narration.

    V1 AUDIO-INTENT CORRECTION (critical, never regress):

    - ``native_only`` NEVER substitutes narration when native audio is
      unavailable.  The user's explicit audio intent wins, so the selection is
      empty and the final artifact has no audio stream rather than a narrator
      track the user did not ask for.
    - ``none`` selects nothing at all — no native audio, no narration, and no
      additive music — so the final artifact carries no audio stream.
    - ``native_plus_narration`` still uses narration alone when native audio
      does not exist (that is the intended combination semantics, not a
      violation of intent).
    """
    resolved = normalize_audio_policy(policy)
    has_native = scene_native_audio is not None
    has_narration = narration is not None
    native_unavailable = False

    if resolved == AudioPolicy.NONE:
        # Explicit silence: every candidate track is suppressed, including the
        # additive music capability, so no audio stream can reach the output.
        return AudioSelection(
            policy=resolved,
            scene_native_audio=None,
            narration=None,
            music=None,
            native_unavailable=False,
        )

    if resolved == AudioPolicy.NARRATION_ONLY:
        selected_music = music
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
        selected_music = music
        if has_native:
            selected_native = scene_native_audio
            selected_narration = None
        else:
            # NO narrator/TTS fallback: intent=native means native audio or
            # silence.  Disclosed truthfully via ``native_unavailable``.
            selected_native = None
            selected_narration = None
            native_unavailable = True
    else:  # native_plus_narration
        selected_music = music
        selected_native = scene_native_audio
        selected_narration = narration
        native_unavailable = not has_native

    return AudioSelection(
        policy=resolved,
        scene_native_audio=selected_native,
        narration=selected_narration,
        music=selected_music,
        native_unavailable=native_unavailable,
    )