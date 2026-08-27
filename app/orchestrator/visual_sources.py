"""Phase 6F - Pluggable visual-source abstraction.

A visual source turns a scene's existing visual intent (its
``visual_prompt`` plus related fields such as ``purpose``, ``visual_style``,
``camera_direction`` and ``on_screen_text``) into a still-image source
artifact.

This layer is a SUPPLIER to the Phase 6C asset workflow, never a bypass:

    scene visual intent
        -> visual source (produces a source artifact in a temp workspace)
        -> Phase 6C intake_assets() (project-owned copy + SHA-256 manifest)
        -> preflight_assets() (authoritative verification)
        -> ImageMotion / local provider consumes the verified project copy

A visual source therefore never writes into ``assets/source/`` directly and
never pretends intake happened.

Honest failure semantics (never fake production output):

- an unconfigured source reports ``REQUIRES_CONFIGURATION``
- a configured-but-dormant or speculative source reports
  ``WOULD_REQUIRE_EXTERNAL_GENERATION`` *without ever making a call* (no
  paid credits, no quota, no credentials round-trip)
- a real failure reports ``FAILURE``
- only a source that actually produced a valid artifact reports ``SUCCESS``

The only concrete source that can produce an artifact today is the
deterministic ``FixtureImageSource`` (used for offline validation and testing
under an explicit ``AVM_IMAGE_SOURCE=fixture`` selection). It is surfaced as
a fixture forever and never masquerades as genuine AI production output.
"""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

IMAGE_SOURCE_ENV = "AVM_IMAGE_SOURCE"


class VisualSourceStatus(str, Enum):
    """Authoritative outcome of attempting to source a scene image."""
    SUCCESS = "success"
    FAILURE = "failure"
    REQUIRES_CONFIGURATION = "requires_configuration"
    WOULD_REQUIRE_EXTERNAL_GENERATION = "would_require_external_generation"
    UNAVAILABLE = "unavailable"


class SourceAvailability(str, Enum):
    """Static capability of a source before a per-scene attempt."""
    AVAILABLE = "available"
    NOT_CONFIGURED = "not_configured"
    REQUIRES_CONFIGURATION = "requires_configuration"
    EXTERNAL_REQUIRED = "external_required"
    UNAVAILABLE = "unavailable"


class VisualSourceContext(BaseModel):
    """Everything a source needs from one scene (no schema rewrite)."""
    project_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    visual_prompt: str = Field(min_length=1)
    purpose: Optional[str] = None
    visual_style: Optional[str] = None
    camera_direction: Optional[str] = None
    on_screen_text: Optional[str] = None
    aspect_ratio: str = "9:16"
    # Temp workspace for produced artifacts BEFORE they enter Phase 6C intake.
    output_dir: Path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "scene_id": self.scene_id,
            "sequence": self.sequence,
            "visual_prompt": self.visual_prompt,
            "purpose": self.purpose,
            "visual_style": self.visual_style,
            "camera_direction": self.camera_direction,
            "on_screen_text": self.on_screen_text,
            "aspect_ratio": self.aspect_ratio,
        }


class VisualSourceResult(BaseModel):
    """Outcome of one per-scene visual-sourcing attempt."""
    status: VisualSourceStatus
    scene_id: str
    provider_id: str
    artifact_path: Optional[Path] = None
    message: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def success(cls, scene_id, provider_id, artifact_path, metadata=None):
        return cls(
            status=VisualSourceStatus.SUCCESS,
            scene_id=scene_id,
            provider_id=provider_id,
            artifact_path=Path(artifact_path),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def failure(cls, scene_id, provider_id, message):
        return cls(
            status=VisualSourceStatus.FAILURE,
            scene_id=scene_id,
            provider_id=provider_id,
            message=message,
        )


class VisualSource(ABC):
    """Abstraction for a still-image source driven by scene visual intent."""

    # Provider identifier persisted as provenance (never a secret).
    name: str = "visual_source"

    @abstractmethod
    def availability(self) -> SourceAvailability:
        """Static capability of this source in the current configuration."""

    def is_available(self) -> bool:
        return self.availability() == SourceAvailability.AVAILABLE

    def configuration_summary(self) -> str:
        """Human-readable, secret-free description of this source's config."""
        return f"visual source '{self.name}'"

    def source(self, ctx: VisualSourceContext) -> VisualSourceResult:
        """Attempt to produce a still-image source artifact for one scene."""
        raise NotImplementedError


class DormantExternalImageSource(VisualSource):
    """Adapter boundary for a future programmatic AI image source.

    This source *validates* that a source is configured/selected but never
    performs a real generation call. It returns either
    ``REQUIRES_CONFIGURATION`` (nothing to act on), ``EXTERNAL_REQUIRED``
    (a real generation would be required - NOT performed), or a truthful
    ``FAILURE``. No paid credits or quota are consumed.
    """

    def __init__(self, provider_id: str, output_dir: Optional[Path] = None):
        self.provider_id = provider_id or ""
        self.name = self.provider_id or "external_image"
        self.output_dir = Path(output_dir) if output_dir else None

    def availability(self) -> SourceAvailability:
        if not self.provider_id:
            return SourceAvailability.NOT_CONFIGURED
        return SourceAvailability.EXTERNAL_REQUIRED

    def configuration_summary(self) -> str:
        return (
            f"visual source '{self.name}' is an external image-generation "
            "adapter boundary; it is NOT wired to make real generation "
            "calls in this build (no paid credits / quota are consumed)."
        )

    def source(self, ctx: VisualSourceContext) -> VisualSourceResult:
        if not self.provider_id:
            return VisualSourceResult(
                status=VisualSourceStatus.REQUIRES_CONFIGURATION,
                scene_id=ctx.scene_id,
                provider_id=self.name,
                message="Visual source not configured: set AVM_IMAGE_SOURCE.",
            )
        return VisualSourceResult(
            status=VisualSourceStatus.WOULD_REQUIRE_EXTERNAL_GENERATION,
            scene_id=ctx.scene_id,
            provider_id=self.name,
            message=(
                f"Scene '{ctx.scene_id}': automatic source '{self.name}' "
                "would require a real external image-generation call, which "
                "is deliberately not performed automatically. Configure a "
                "programmatic image source or supply an asset to continue."
            ),
        )


class FixtureImageSource(VisualSource):
    """Deterministic local fixture source for offline validation/tests.

    Produces a real, probe-able PNG via FFmpeg's lavfi ``color`` source with
    a colour derived deterministically from the scene. It is permanently
    surfaced as a fixture (``provider_id="fixture"``,
    ``metadata["kind"]="fixture"``) and must never be presented as genuine
    AI-generated production output.
    """

    name = "fixture"

    _SIZES = {
        "9:16": "1080x1920",
        "16:9": "1920x1080",
        "1:1": "1080x1080",
    }
    _PALETTE = [
        "red", "green", "blue", "purple", "orange", "teal",
        "darkred", "navy", "maroon", "olive",
    ]

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = Path(output_dir) if output_dir else None

    def availability(self) -> SourceAvailability:
        return SourceAvailability.AVAILABLE

    def configuration_summary(self) -> str:
        return (
            "visual source 'fixture' (deterministic local fixture; NOT real "
            "AI-generated imagery)"
        )

    def source(self, ctx: VisualSourceContext) -> VisualSourceResult:
        base = ctx.output_dir if ctx.output_dir else (self.output_dir or Path())
        base.mkdir(parents=True, exist_ok=True)
        output = base / f"autosrc_{ctx.scene_id}.png"

        import hashlib
        digest = hashlib.sha256(
            f"{ctx.scene_id}:{ctx.visual_prompt}:{ctx.sequence}".encode("utf-8")
        ).hexdigest()
        colour = self._PALETTE[int(digest, 16) % len(self._PALETTE)]
        size = self._SIZES.get(ctx.aspect_ratio, "1080x1920")

        try:
            proc = subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi",
                 "-i", f"color=c={colour}:s={size}",
                 "-frames:v", "1", str(output)],
                capture_output=True, timeout=60,
            )
            if proc.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
                return VisualSourceResult.failure(
                    ctx.scene_id, self.name,
                    f"fixture image generation failed (ffmpeg rc={proc.returncode})",
                )
        except Exception as exc:  # noqa: BLE001 - surfaced truthfully
            return VisualSourceResult.failure(
                ctx.scene_id, self.name, f"fixture image generation error: {exc}"
            )

        return VisualSourceResult.success(
            ctx.scene_id,
            self.name,
            output,
            metadata={
                "kind": "fixture",
                "source_color": colour,
                "size": size,
                "derived_from": ["scene_id", "visual_prompt", "sequence"],
            },
        )


def resolve_image_source_config() -> str:
    """Return the configured visual-source selector (never a secret)."""
    return (os.getenv(IMAGE_SOURCE_ENV) or "").strip().lower()


def create_visual_source(
    selector: Optional[str],
    output_dir: Optional[Path] = None,
) -> Optional[VisualSource]:
    """Factory: map a selector string to a VisualSource instance.

    Returns ``None`` when no source is configured. ``fixture`` maps to the
    deterministic fixture source; any other non-empty value maps to the
    dormant external adapter boundary (which never makes a real call).
    Unknown empty selectors return ``None`` (honest "not configured").
    """
    sel = (selector or "").strip().lower()
    if not sel:
        return None
    if sel == "fixture":
        return FixtureImageSource(output_dir)
    return DormantExternalImageSource(provider_id=sel, output_dir=output_dir)
