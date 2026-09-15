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

Lightweight semantic-relevance gate (Batch 1 hardening): search-based sources
rank their candidates by word overlap between the scene's search terms and
the provider's own description of each candidate (Pexels ``alt``; Commons
title/description) and REJECT candidates with zero overlap on a multi-term
query — a technically valid but semantically unrelated image (the
manual-test excavator failure) never stops the search; the existing
relaxation and routing fall-through continue instead. No embeddings, no
vision model, no LLM judge — deliberately.

The concrete sources are:

- ``RoutedVisualSource`` (``AVM_IMAGE_SOURCE=auto``, the Batch 1 default, or
  an explicit comma chain like ``openrouter,pexels``) — the minimal visual
  ROUTING layer: it selects among the configured child sources per scene
  using the scene's own optional ``visual_source`` intelligence hint
  (``ai_image`` / ``stock``) and falls through the explicitly configured
  chain truthfully (an unconfigured child is skipped with a note; a failed
  child is reported; only when EVERY configured child fails does the scene
  fail, with all reasons). Batch 1C: the default ``auto`` chain begins with
  the Alibaba primary AI source, then OpenRouter (the classified fallback);
  a generation child that reports a NON-ELIGIBLE Batch 1C failure stops the
  chain truthfully instead of re-sending the same request;
- ``AlibabaImageSource`` (``AVM_IMAGE_SOURCE=alibaba``) — Batch 1C PRIMARY
  AI image generation via Alibaba Cloud Model Studio (DashScope async
  text-to-image), model-driven by ``AVM_ALIBABA_IMAGE_MODEL``; artifacts are
  materialized locally and verified before Phase 6C intake;
- ``OpenRouterImageSource`` (``AVM_IMAGE_SOURCE=openrouter``) — real AI
  image generation through the project's OpenRouter gateway (its dedicated
  Image API), model-driven by ``AVM_IMAGE_MODEL``; never hardcodes a model;
  now the FALLBACK generation source;
- ``PexelsImageSource`` (``AVM_IMAGE_SOURCE=pexels``) — quality stock
  acquisition from the Pexels API (``PEXELS_API_KEY``) with license,
  photographer and landing-page provenance;
- ``WikimediaCommonsImageSource`` (``AVM_IMAGE_SOURCE=wikimedia``) — free,
  keyless acquisition of freely-licensed imagery from Wikimedia Commons
  with full provenance. It is retained as an explicit selector and the
  keyless fallback of the ``auto`` chain, but it is NOT the primary visual
  strategy for cinematic storytelling;
- ``FixtureImageSource`` (``AVM_IMAGE_SOURCE=fixture``) — the deterministic
  fixture used for offline validation and testing under an explicit
  selection. It is surfaced as a fixture forever and never masquerades as
  genuine AI production output;
- ``DormantExternalImageSource`` — the honest adapter boundary for any other
  selector: it never makes a call and reports what would be required.

Explicit user-supplied assets always win: the pipeline never auto-sources a
scene that already has an explicit mapping or a project-owned asset.
"""

from __future__ import annotations

import os
import re
import subprocess
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.assembly.ffmpeg import probe_media, resolve_ffmpeg
from app.orchestrator.scene_execution import (
    BILLING_STATUS_BILLABLE,
    COST_SOURCE_NONE,
    COST_SOURCE_PROVIDER_REPORTED,
    build_operation_accounting,
)

IMAGE_SOURCE_ENV = "AVM_IMAGE_SOURCE"
# Phase 11C Batch 1 hardening: configuration for the real visual strategy.
# AVM_IMAGE_MODEL selects the OpenRouter image-capable model slug (empty =
# the AI-image source is not configured; nothing is ever hardcoded).
IMAGE_MODEL_ENV = "AVM_IMAGE_MODEL"
# Pexels API key for the quality stock source (free key: pexels.com/api).
PEXELS_API_KEY_ENV = "PEXELS_API_KEY"

# Phase 11C Batch 1 hardening: default visual source for REAL local
# production runs is the minimal ROUTING layer ("auto"): AI image generation
# when an image model is configured, quality stock (Pexels) when its key is
# configured, and free keyless Wikimedia Commons as the fallback of last
# resort. Wikimedia is deliberately NO LONGER the primary strategy.
# "fixture" remains available for explicit offline/test mode only.
DEFAULT_IMAGE_SOURCE = "auto"

# Scene-level visual-source hint vocabulary (Scene.visual_source).
VISUAL_SOURCE_HINT_AI = "ai_image"
VISUAL_SOURCE_HINT_STOCK = "stock"


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
    # Phase 11C Batch 1 hardening: the scene's own optional visual-source
    # intelligence hint ("ai_image" | "stock" | None) from Scene.visual_source.
    # It is an additive planning signal for the routing layer only — a source
    # that does not route simply ignores it.
    source_hint: Optional[str] = None
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
            "source_hint": self.source_hint,
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


# ---------------------------------------------------------------------------
# Shared visual-intent term extraction (used by every search-based source)
# ---------------------------------------------------------------------------

# Words that describe presentation rather than picture content; they make
# poor stock-library search terms and are stripped from scene visual intent.
STYLE_WORDS = frozenset({
    "4k", "8k", "hd", "aerial", "angle", "atmospheric", "backdrop",
    "background", "beautiful", "bokeh", "cinematic", "close", "closeup",
    "close-up", "color", "colour", "composition", "detailed", "documentary",
    "dolly", "dramatic", "epic", "footage", "frame", "generative",
    "gentle", "glowing", "gradient", "high", "horizontal", "illustration",
    "lighting", "macro", "moody", "motion", "movement", "pan", "panning",
    "photograph", "photorealistic", "push", "quality", "render", "rendered",
    "resolution", "scene", "sequence", "shot", "slow", "soft", "style",
    "styled", "stylized", "sweeping", "telephoto", "tilt",
    "tracking", "uhd", "vertical", "video", "view", "warm", "wide", "zoom",
    "camera", "lens", "depth", "feel", "mood", "tone", "vibrant", "vivid",
    "visual", "visuals", "imagery", "focused", "focus",
})

STOPWORDS = frozenset({
    "a", "an", "the", "of", "and", "or", "with", "in", "on", "at", "to",
    "for", "from", "by", "as", "is", "are", "was", "were", "be", "been",
    "it", "its", "this", "that", "these", "those", "into", "over",
    "under", "toward", "towards", "across", "through", "up", "down",
    "out", "about", "showing", "shows", "shown", "featured", "featuring",
    "depicting", "depicts", "image", "picture", "photo",
})


def visual_search_terms(ctx: VisualSourceContext, limit: int = 6) -> List[str]:
    """Reduce a scene's visual intent to concrete stock-search terms.

    Shared by every search-based source (Wikimedia Commons, Pexels) so the
    AI-derived visual intent is interpreted identically across the stock
    family. Presentation vocabulary and stopwords are dropped; the first
    ``limit`` content terms are kept in prompt order.
    """
    raw_parts = [
        ctx.visual_prompt,
        ctx.purpose or "",
        ctx.visual_style or "",
    ]
    terms: List[str] = []
    for chunk in raw_parts:
        for token in re.findall(r"[a-zA-Z][a-zA-Z\-']+", chunk.lower()):
            token = token.strip("-'")
            if not token or len(token) < 3:
                continue
            if token in STOPWORDS or token in STYLE_WORDS:
                continue
            # Hyphenated compounds (e.g. "push-in") are presentation
            # vocabulary whenever every part is; drop them too.
            parts = [part for part in token.split("-") if part]
            if parts and all(
                part in STOPWORDS or part in STYLE_WORDS
                for part in parts
            ):
                continue
            if token not in terms:
                terms.append(token)
    return terms[:limit]


def _normalize_relevance_token(token: str) -> str:
    """Lightweight normalization for relevance matching.

    Lowercase, strip possessives and simple plural endings. Deliberately NOT
    a stemmer — this gate must stay simple, deterministic, and dependency-free.
    """
    token = (token or "").strip().lower().strip("-'")
    if token.endswith("'s"):
        token = token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token


def candidate_relevance_score(text: Optional[str], terms: List[str]) -> int:
    """Count how many DISTINCT scene search-terms appear in candidate text.

    This is the deliberate, lightweight semantic-relevance gate: word overlap
    between the scene's AI-derived search intent and the provider's own
    description of the candidate (Pexels ``alt``; Commons title/description).
    No embeddings, no vision model, no LLM judge — by design.
    """
    if not text or not terms:
        return 0
    words: set = set()
    for raw in re.findall(r"[a-zA-Z][a-zA-Z\-']+", text.lower()):
        token = _normalize_relevance_token(raw)
        if token:
            words.add(token)
    score = 0
    for term in terms:
        normalized = _normalize_relevance_token(term)
        if normalized and normalized in words:
            score += 1
    return score


def relevance_threshold(terms: List[str]) -> int:
    """Minimum relevance score a candidate must reach to be acceptable.

    A multi-term query must match at least ONE term in the provider's own
    description of the candidate; a candidate matching NOTHING is semantically
    unrelated (the manual-test excavator failure: a technically valid,
    high-resolution image for an unrelated subject) and must be rejected so
    the existing relaxation/fallback behavior can keep searching. A
    single-term query is exempt — one word cannot meaningfully demand more.
    """
    return 1 if len(terms) >= 2 else 0


def verify_sourced_image(path: Path) -> None:
    """FFprobe-verify a sourced still image; raise on ANY defect.

    ``probe_media`` alone is insufficient for image files: ffprobe trusts the
    file extension via the image2 demuxer and "probes" arbitrary garbage
    bytes as a 0x0 mjpeg frame with rc=0. A genuine still image always
    reports positive decoded dimensions, so the dimension check is the
    truthful discriminator — corrupt downloads are rejected instead of being
    handed to intake (no new dependencies, no fabricated tolerance).

    Raises:
        FFmpegExecutionError: ffprobe could not process the file at all.
        ValueError: the file probed but has no decodable image dimensions.
    """
    info = probe_media(path)
    width = int(getattr(info, "width", 0) or 0)
    height = int(getattr(info, "height", 0) or 0)
    if width <= 0 or height <= 0:
        raise ValueError(
            f"file has no decodable image dimensions ({width}x{height}); "
            "content is corrupt or not a real image"
        )


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
            ffmpeg = resolve_ffmpeg()
            if not ffmpeg:
                return VisualSourceResult.failure(
                    ctx.scene_id, self.name,
                    "FFmpeg executable not found; fixture image generation is "
                    "unavailable. Install FFmpeg and add it to PATH, or set "
                    "FFMPEG_PATH.",
                )
            proc = subprocess.run(
                [ffmpeg, "-y", "-f", "lavfi",
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


class WikimediaCommonsImageSource(VisualSource):
    """Phase 11C Batch 1: free stock-family still-image acquisition.

    Backed by the public Wikimedia Commons search API (no API key, no paid
    credits). Each scene's existing visual intent (``visual_prompt`` plus
    optional purpose/style fields) is reduced to search terms; matching
    freely-licensed imagery is downloaded into the scene's temp workspace and
    handed to the Phase 6C intake exactly like the fixture source. Full
    provenance (source URL, license, creator, landing page) is returned in
    ``metadata`` so the asset manifest records where every image came from.

    Honest failure semantics (never fake production output):

    - scenes whose visual intent yields no usable search terms report
      ``FAILURE`` with an actionable message;
    - network/API/download/verification failures report ``FAILURE``;
    - only a downloaded, FFprobe-verified image reports ``SUCCESS``.

    The ``search_fn``/``download_fn`` constructor seams exist so tests can
    exercise this source offline; production uses the real HTTP functions.
    """

    name = "wikimedia"

    _API_URL = "https://commons.wikimedia.org/w/api.php"
    _USER_AGENT = (
        "AVM-LocalProduction/1.0 "
        "(local development; https://github.com/7DayWalker/AUTO-VIDEO-MAKER)"
    )
    _THUMB_WIDTH = 1600
    _MIN_WIDTH = 640
    _TIMEOUT_SECONDS = 30.0

    # Shared stock-family term extraction (module level); kept as class
    # attributes for backward compatibility with existing consumers/tests.
    _STYLE_WORDS = STYLE_WORDS
    _STOPWORDS = STOPWORDS

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        *,
        timeout: float = _TIMEOUT_SECONDS,
        search_fn=None,
        download_fn=None,
    ):
        self.output_dir = Path(output_dir) if output_dir else None
        self._timeout = float(timeout)
        self._search_fn = search_fn or self._http_search
        self._download_fn = download_fn or self._http_download

    # -- public interface ----------------------------------------------------

    def availability(self) -> SourceAvailability:
        return SourceAvailability.AVAILABLE

    def configuration_summary(self) -> str:
        return (
            "visual source 'wikimedia' (free Wikimedia Commons acquisition; "
            "freely-licensed imagery with recorded provenance; no API key)"
        )

    def source(self, ctx: VisualSourceContext) -> VisualSourceResult:
        terms = self.search_terms(ctx)
        if not terms:
            return VisualSourceResult.failure(
                ctx.scene_id,
                self.name,
                (
                    f"Scene '{ctx.scene_id}': visual intent produced no usable "
                    f"search terms (prompt: '{ctx.visual_prompt[:160]}'). "
                    "Refine the scene's visual_prompt with concrete subjects."
                ),
            )

        # Progressive relaxation: full-text Commons search is strict, so a
        # long query may match nothing. Retry with progressively shorter
        # queries (dropping the last term) until something usable is found or
        # only a minimal query remains. The final query is recorded as
        # provenance either way.
        last_failure = ""
        attempt = list(terms)
        while len(attempt) >= min(2, len(terms)):
            query = " ".join(attempt)
            try:
                payload = self._search_fn(self._API_URL, {
                    "action": "query",
                    "format": "json",
                    "generator": "search",
                    "gsrsearch": f"filetype:bitmap {query}",
                    "gsrnamespace": 6,
                    "gsrlimit": 8,
                    "prop": "imageinfo",
                    "iiprop": "url|size|mime|extmetadata",
                    "iiurlwidth": self._THUMB_WIDTH,
                })
            except Exception as exc:  # noqa: BLE001 - surfaced truthfully
                return VisualSourceResult.failure(
                    ctx.scene_id,
                    self.name,
                    (
                        f"Scene '{ctx.scene_id}': Wikimedia Commons search "
                        f"failed for '{query}': {exc}"
                    ),
                )
            chosen = self._choose_candidate(payload, ctx.aspect_ratio, terms)
            if chosen is not None:
                return self._acquire(ctx, chosen, query)
            last_failure = query
            attempt = attempt[:-1]

        return VisualSourceResult.failure(
            ctx.scene_id,
            self.name,
            (
                f"Scene '{ctx.scene_id}': no usable freely-licensed image "
                f"found on Wikimedia Commons (queries from '{' '.join(terms)}' "
                f"down to '{last_failure}'). Provide an asset explicitly or "
                "choose another visual source."
            ),
        )

    def _acquire(
        self,
        ctx: VisualSourceContext,
        chosen: Dict[str, Any],
        query: str,
    ) -> VisualSourceResult:
        """Download and verify the chosen candidate; record full provenance."""
        base = ctx.output_dir if ctx.output_dir else (self.output_dir or Path())
        base.mkdir(parents=True, exist_ok=True)
        download_url = chosen["thumburl"] or chosen["url"]
        suffix = ".jpg"
        lowered = str(download_url).lower()
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            if lowered.split("?")[0].endswith(ext):
                suffix = ".jpeg" if ext == ".jpg" else ext
                break
        output = base / f"autosrc_{ctx.scene_id}{suffix}"

        try:
            self._download_fn(download_url, output)
        except Exception as exc:  # noqa: BLE001 - surfaced truthfully
            return VisualSourceResult.failure(
                ctx.scene_id,
                self.name,
                (
                    f"Scene '{ctx.scene_id}': image download failed for "
                    f"'{query}': {exc}"
                ),
            )

        if not output.is_file() or output.stat().st_size <= 0:
            return VisualSourceResult.failure(
                ctx.scene_id,
                self.name,
                f"Scene '{ctx.scene_id}': downloaded image is missing or empty.",
            )
        try:
            verify_sourced_image(output)
        except Exception as exc:  # noqa: BLE001 - corrupt/unreadable image
            return VisualSourceResult.failure(
                ctx.scene_id,
                self.name,
                (
                    f"Scene '{ctx.scene_id}': downloaded image failed media "
                    f"verification: {exc}"
                ),
            )

        metadata = {
            "kind": "stock",
            "library": "wikimedia_commons",
            "search_query": query,
            "source_url": chosen["url"],
            "license": chosen["license"],
            "creator": chosen["creator"],
            "landing_page": chosen["landing_page"],
            "width": chosen["width"],
            "height": chosen["height"],
            "relevance_score": chosen["relevance_score"],
        }
        return VisualSourceResult.success(
            ctx.scene_id,
            self.name,
            output,
            metadata=metadata,
        )

    # -- search / selection helpers ------------------------------------------

    @classmethod
    def search_terms(cls, ctx: VisualSourceContext) -> List[str]:
        """Reduce a scene's visual intent to concrete Commons search terms.

        Delegates to the shared stock-family extraction so every search-based
        source interprets scene visual intent identically.
        """
        return visual_search_terms(ctx)

    def _choose_candidate(
        self,
        payload: Dict[str, Any],
        aspect_ratio: str,
        terms: List[str],
    ) -> Optional[Dict[str, Any]]:
        pages = (payload or {}).get("query", {}).get("pages", {})
        candidates: List[Dict[str, Any]] = []
        for order, page in enumerate(pages.values()):
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            mime = str(info.get("mime") or "")
            if mime and mime not in ("image/jpeg", "image/png", "image/webp"):
                continue
            width = int(info.get("width") or 0)
            height = int(info.get("height") or 0)
            if width < self._MIN_WIDTH:
                continue
            extmetadata = info.get("extmetadata") or {}
            # The candidate's own descriptive text — the Commons file title
            # (always present) plus ImageDescription when provided — is the
            # basis of the lightweight semantic-relevance gate below.
            candidate_text = " ".join(
                part
                for part in (
                    str(page.get("title") or ""),
                    self._plain_text(
                        (extmetadata.get("ImageDescription") or {}).get("value")
                    ),
                )
                if part
            )
            score = candidate_relevance_score(candidate_text, terms)
            if score < relevance_threshold(terms):
                # Semantically unrelated (zero term overlap on a multi-term
                # query): reject so the relaxation loop keeps searching —
                # never accept an image merely because the bytes are valid.
                continue
            candidates.append({
                "_order": order,
                "url": info.get("url") or "",
                "thumburl": info.get("thumburl") or "",
                "width": width,
                "height": height,
                "license": (
                    (extmetadata.get("LicenseShortName") or {}).get("value")
                    or "unknown"
                ),
                "creator": self._plain_text(
                    (extmetadata.get("Artist") or {}).get("value")
                ) or "unknown",
                "landing_page": info.get("descriptionurl") or "",
                "relevance_score": score,
            })
        if not candidates:
            return None

        want_tall = aspect_ratio == "9:16"
        want_wide = aspect_ratio == "16:9"

        def orientation_ok(item: Dict[str, Any]) -> bool:
            if want_tall:
                return item["height"] > item["width"]
            if want_wide:
                return item["width"] >= item["height"]
            return True

        matching = [item for item in candidates if orientation_ok(item)]
        pool = matching or candidates
        # Rank by semantic relevance FIRST; Commons' own relevance order (the
        # API/dict order, preserved via ``_order``) next; width only as the
        # final tie-break. The previous width-first sort discarded relevance
        # entirely and was the root cause of the manual-test failure.
        ranked = sorted(
            pool,
            key=lambda item: (
                -item["relevance_score"],
                item["_order"],
                -item["width"],
            ),
        )
        chosen = dict(ranked[0])
        chosen.pop("_order", None)
        return chosen

    @staticmethod
    def _plain_text(maybe_html: Optional[str]) -> str:
        if not maybe_html:
            return ""
        return re.sub(r"<[^>]+>", "", str(maybe_html)).strip()

    # -- default HTTP seams ---------------------------------------------------

    @staticmethod
    def _http_search(url: str, params: Dict[str, str]) -> Dict[str, Any]:
        import requests

        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": WikimediaCommonsImageSource._USER_AGENT},
            timeout=WikimediaCommonsImageSource._TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _http_download(url: str, destination: Path) -> None:
        import requests

        with requests.get(
            url,
            headers={"User-Agent": WikimediaCommonsImageSource._USER_AGENT},
            timeout=WikimediaCommonsImageSource._TIMEOUT_SECONDS,
            stream=True,
        ) as response:
            response.raise_for_status()
            with open(destination, "wb") as handle:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        handle.write(chunk)


class PexelsImageSource(VisualSource):
    """Phase 11C Batch 1 hardening: quality stock-image acquisition.

    Backed by the official Pexels search API (``PEXELS_API_KEY``; free key at
    https://www.pexels.com/api/). Pexels is the Batch 1 quality stock choice:
    curated professional photography, a simple key-based REST contract, an
    ``orientation`` filter that matches vertical short-form framing, and the
    permissive Pexels License (free for commercial use, no attribution
    required).

    Like every source here it is a SUPPLIER to the Phase 6C intake: the
    chosen photo is downloaded into the scene's temp workspace, verified with
    FFprobe, and handed to ``intake_assets()`` by the caller. Full provenance
    (photo id, photographer, landing page, license, download URL) is returned
    in ``metadata`` for the asset manifest.

    Honest failure semantics: no key -> ``REQUIRES_CONFIGURATION``; empty
    search terms / no usable photo / API / download / verification problems
    -> ``FAILURE`` with an actionable message; only a downloaded,
    FFprobe-verified image reports ``SUCCESS``.

    The ``search_fn``/``download_fn`` constructor seams exist so tests can
    exercise this source offline; production uses the real HTTP functions.
    """

    name = "pexels"

    _API_URL = "https://api.pexels.com/v1/search"
    _USER_AGENT = (
        "AVM-LocalProduction/1.0 "
        "(local development; https://github.com/7DayWalker/AUTO-VIDEO-MAKER)"
    )
    _MIN_DIMENSION = 640
    _PER_PAGE = 8
    _TIMEOUT_SECONDS = 30.0
    _LICENSE = "Pexels License (https://www.pexels.com/license/)"

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        *,
        api_key: Optional[str] = None,
        timeout: float = _TIMEOUT_SECONDS,
        search_fn=None,
        download_fn=None,
    ):
        self.output_dir = Path(output_dir) if output_dir else None
        self._api_key = (
            api_key if api_key is not None
            else os.getenv(PEXELS_API_KEY_ENV, "")
        ).strip()
        self._timeout = float(timeout)
        self._search_fn = search_fn or self._http_search
        self._download_fn = download_fn or self._http_download

    # -- public interface ----------------------------------------------------

    def availability(self) -> SourceAvailability:
        if not self._api_key:
            return SourceAvailability.REQUIRES_CONFIGURATION
        return SourceAvailability.AVAILABLE

    def configuration_summary(self) -> str:
        if not self._api_key:
            return (
                "visual source 'pexels' requires configuration: set "
                "PEXELS_API_KEY (free key at https://www.pexels.com/api/)"
            )
        return (
            "visual source 'pexels' (quality stock acquisition via the "
            "Pexels API; Pexels License; API key configured)"
        )

    def source(self, ctx: VisualSourceContext) -> VisualSourceResult:
        if not self._api_key:
            return VisualSourceResult(
                status=VisualSourceStatus.REQUIRES_CONFIGURATION,
                scene_id=ctx.scene_id,
                provider_id=self.name,
                message=(
                    f"Scene '{ctx.scene_id}': Pexels API key not configured "
                    "(set PEXELS_API_KEY)."
                ),
            )

        terms = visual_search_terms(ctx)
        if not terms:
            return VisualSourceResult.failure(
                ctx.scene_id,
                self.name,
                (
                    f"Scene '{ctx.scene_id}': visual intent produced no usable "
                    f"search terms (prompt: '{ctx.visual_prompt[:160]}'). "
                    "Refine the scene's visual_prompt with concrete subjects."
                ),
            )

        orientation = self._orientation_for(ctx.aspect_ratio)

        # Progressive relaxation (same discipline as the Wikimedia source):
        # full query first, then progressively shorter queries. The first
        # (full) query is additionally retried WITHOUT the orientation filter
        # before shorter queries are attempted.
        last_failure = ""
        attempt = list(terms)
        while len(attempt) >= min(2, len(terms)):
            query = " ".join(attempt)
            orientation_options = (True, False) if (
                orientation and attempt == terms
            ) else ((True,) if orientation else (False,))
            for use_orientation in orientation_options:
                params: Dict[str, str] = {
                    "query": query,
                    "per_page": str(self._PER_PAGE),
                }
                if use_orientation and orientation:
                    params["orientation"] = orientation
                try:
                    payload = self._search_fn(
                        self._API_URL, params, self._api_key
                    )
                except Exception as exc:  # noqa: BLE001 - surfaced truthfully
                    return VisualSourceResult.failure(
                        ctx.scene_id,
                        self.name,
                        (
                            f"Scene '{ctx.scene_id}': Pexels search failed "
                            f"for '{query}': {exc}"
                        ),
                    )
                chosen = self._choose_candidate(payload, ctx.aspect_ratio, terms)
                if chosen is not None:
                    return self._acquire(ctx, chosen, query)
            last_failure = query
            attempt = attempt[:-1]

        return VisualSourceResult.failure(
            ctx.scene_id,
            self.name,
            (
                f"Scene '{ctx.scene_id}': no usable Pexels image found "
                f"(queries from '{' '.join(terms)}' down to '{last_failure}'). "
                "Provide an asset explicitly, configure an AI image model "
                "(AVM_IMAGE_MODEL), or choose another visual source."
            ),
        )

    # -- acquisition ------------------------------------------------------------

    def _acquire(
        self,
        ctx: VisualSourceContext,
        chosen: Dict[str, Any],
        query: str,
    ) -> VisualSourceResult:
        """Download and verify the chosen photo; record full provenance."""
        base = ctx.output_dir if ctx.output_dir else (self.output_dir or Path())
        base.mkdir(parents=True, exist_ok=True)
        download_url = chosen["download_url"]
        suffix = ".jpeg"
        lowered = str(download_url).lower()
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            if lowered.split("?")[0].endswith(ext):
                suffix = ".jpeg" if ext == ".jpg" else ext
                break
        output = base / f"autosrc_{ctx.scene_id}{suffix}"

        try:
            self._download_fn(download_url, output)
        except Exception as exc:  # noqa: BLE001 - surfaced truthfully
            return VisualSourceResult.failure(
                ctx.scene_id,
                self.name,
                (
                    f"Scene '{ctx.scene_id}': Pexels image download failed "
                    f"for '{query}': {exc}"
                ),
            )

        if not output.is_file() or output.stat().st_size <= 0:
            return VisualSourceResult.failure(
                ctx.scene_id,
                self.name,
                f"Scene '{ctx.scene_id}': downloaded Pexels image is missing or empty.",
            )
        try:
            verify_sourced_image(output)
        except Exception as exc:  # noqa: BLE001 - corrupt/unreadable image
            return VisualSourceResult.failure(
                ctx.scene_id,
                self.name,
                (
                    f"Scene '{ctx.scene_id}': downloaded Pexels image failed "
                    f"media verification: {exc}"
                ),
            )

        metadata = {
            "kind": "stock",
            "library": "pexels",
            "search_query": query,
            "photo_id": chosen["photo_id"],
            "source_url": download_url,
            "original_url": chosen["original_url"],
            "landing_page": chosen["landing_page"],
            "license": self._LICENSE,
            "creator": chosen["photographer"],
            "photographer_url": chosen["photographer_url"],
            "width": chosen["width"],
            "height": chosen["height"],
            "relevance_score": chosen["relevance_score"],
        }
        return VisualSourceResult.success(
            ctx.scene_id,
            self.name,
            output,
            metadata=metadata,
        )



    # -- search / selection helpers ---------------------------------------------

    @staticmethod
    def _orientation_for(aspect_ratio: str) -> Optional[str]:
        if aspect_ratio == "9:16":
            return "portrait"
        if aspect_ratio == "16:9":
            return "landscape"
        if aspect_ratio == "1:1":
            return "square"
        return None

    def _choose_candidate(
        self,
        payload: Dict[str, Any],
        aspect_ratio: str,
        terms: List[str],
    ) -> Optional[Dict[str, Any]]:
        photos = (payload or {}).get("photos") or []
        candidates: List[Dict[str, Any]] = []
        for order, photo in enumerate(photos):
            src = photo.get("src") or {}
            download_url = (
                src.get("large2x") or src.get("large") or src.get("original")
            )
            if not download_url:
                continue
            width = int(photo.get("width") or 0)
            height = int(photo.get("height") or 0)
            if max(width, height) < self._MIN_DIMENSION:
                continue
            # The candidate's own ``alt`` description is the basis of the
            # lightweight semantic-relevance gate below.
            score = candidate_relevance_score(
                str(photo.get("alt") or ""), terms
            )
            if score < relevance_threshold(terms):
                # Semantically unrelated (zero term overlap on a multi-term
                # query): reject so the relaxation loop keeps searching —
                # never accept an image merely because the bytes are valid.
                continue
            candidates.append({
                "_order": order,
                "photo_id": photo.get("id"),
                "download_url": download_url,
                "original_url": src.get("original") or download_url,
                "landing_page": photo.get("url") or "",
                "photographer": photo.get("photographer") or "unknown",
                "photographer_url": photo.get("photographer_url") or "",
                "width": width,
                "height": height,
                "relevance_score": score,
            })
        if not candidates:
            return None

        want_tall = aspect_ratio == "9:16"
        want_wide = aspect_ratio == "16:9"

        def orientation_ok(item: Dict[str, Any]) -> bool:
            if want_tall:
                return item["height"] > item["width"]
            if want_wide:
                return item["width"] >= item["height"]
            return True

        matching = [item for item in candidates if orientation_ok(item)]
        pool = matching or candidates
        # Rank by semantic relevance FIRST; Pexels' own relevance order (the
        # API order, preserved via ``_order``) breaks ties. A stable sort on
        # the original order keeps equal-scoring candidates in API ranking.
        ranked = sorted(
            pool,
            key=lambda item: (-item["relevance_score"], item["_order"]),
        )
        chosen = dict(ranked[0])
        chosen.pop("_order", None)
        return chosen

    # -- default HTTP seams -------------------------------------------------------

    def _http_search(
        self, url: str, params: Dict[str, str], api_key: str
    ) -> Dict[str, Any]:
        import requests

        response = requests.get(
            url,
            params=params,
            headers={
                "Authorization": api_key,
                "User-Agent": self._USER_AGENT,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _http_download(url: str, destination: Path) -> None:
        import requests

        with requests.get(
            url,
            headers={"User-Agent": PexelsImageSource._USER_AGENT},
            timeout=PexelsImageSource._TIMEOUT_SECONDS,
            stream=True,
        ) as response:
            response.raise_for_status()
            with open(destination, "wb") as handle:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        handle.write(chunk)
class OpenRouterImageSource(VisualSource):
    """Phase 11C Batch 1 hardening: real AI image generation.

    Uses the project's OpenRouter gateway — the SAME existing
    ``OpenRouterProvider`` client (never a second client) — through
    OpenRouter's dedicated Image API. The image-capable model slug comes
    exclusively from configuration (``AVM_IMAGE_MODEL`` or an explicit
    constructor argument); this source never assumes or hardcodes that any
    particular model can generate images (capability discovery lives at
    ``GET {base_url}/images/models``).

    The generated image is decoded, written into the scene's temp workspace,
    verified with FFprobe, and handed to the Phase 6C intake exactly like
    every other source. Provenance (kind=ai_generated, provider, model,
    media type, usage/cost, prompt) is returned in ``metadata``.

    Honest failure semantics: unconfigured key/model ->
    ``REQUIRES_CONFIGURATION`` (no call is made, no credits consumed);
    generation/decode/verification failure -> ``FAILURE``; only a written,
    FFprobe-verified image reports ``SUCCESS``. A failed generation NEVER
    fabricates an image.

    The ``generate_fn`` constructor seam exists so tests can exercise this
    source offline; production uses ``OpenRouterProvider.generate_image``.
    """

    name = "openrouter"

    _EXT_BY_MEDIA = {
        "image/png": ".png",
        "image/jpeg": ".jpeg",
        "image/jpg": ".jpeg",
        "image/webp": ".webp",
    }

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        *,
        model: Optional[str] = None,
        provider=None,
        generate_fn=None,
    ):
        self.output_dir = Path(output_dir) if output_dir else None
        self._model = (
            model if model is not None
            else os.getenv(IMAGE_MODEL_ENV, "")
        ).strip()
        self._provider = provider
        self._generate_fn = generate_fn

    # -- public interface ----------------------------------------------------

    def _resolve_provider(self):
        if self._provider is None:
            from app.content.providers.openrouter import OpenRouterProvider
            self._provider = OpenRouterProvider()
        return self._provider

    def availability(self) -> SourceAvailability:
        if not self._model:
            return SourceAvailability.REQUIRES_CONFIGURATION
        try:
            if not self._resolve_provider().is_available():
                return SourceAvailability.REQUIRES_CONFIGURATION
        except Exception:  # noqa: BLE001 - unresolvable provider config
            return SourceAvailability.REQUIRES_CONFIGURATION
        return SourceAvailability.AVAILABLE

    def configuration_summary(self) -> str:
        if not self._model:
            return (
                "visual source 'openrouter' requires configuration: set "
                "AVM_IMAGE_MODEL to an image-capable OpenRouter model slug "
                "(discovery: GET /api/v1/images/models)"
            )
        try:
            configured = self._resolve_provider().is_available()
        except Exception:  # noqa: BLE001
            configured = False
        if not configured:
            return (
                "visual source 'openrouter' requires configuration: "
                "OPENROUTER_API_KEY is not set"
            )
        return (
            "visual source 'openrouter' (AI image generation via the "
            f"OpenRouter Image API; model '{self._model}'; cost-bearing "
            "per completed generation)"
        )


    def source(self, ctx: VisualSourceContext) -> VisualSourceResult:
        availability = self.availability()
        if availability != SourceAvailability.AVAILABLE:
            return VisualSourceResult(
                status=VisualSourceStatus.REQUIRES_CONFIGURATION,
                scene_id=ctx.scene_id,
                provider_id=self.name,
                message=(
                    f"Scene '{ctx.scene_id}': OpenRouter image generation is "
                    f"not configured ({self.configuration_summary()})."
                ),
            )

        prompt = self._build_prompt(ctx)
        try:
            if self._generate_fn is not None:
                image = self._generate_fn(
                    prompt, model=self._model, aspect_ratio=ctx.aspect_ratio
                )
            else:
                image = self._resolve_provider().generate_image(
                    prompt,
                    model=self._model,
                    aspect_ratio=ctx.aspect_ratio or None,
                )
        except Exception as exc:  # noqa: BLE001 - surfaced truthfully
            return VisualSourceResult.failure(
                ctx.scene_id,
                self.name,
                (
                    f"Scene '{ctx.scene_id}': OpenRouter image generation "
                    f"failed (model '{self._model}'): {exc}"
                ),
            )

        image_bytes = getattr(image, "image_bytes", None)
        if not image_bytes:
            return VisualSourceResult.failure(
                ctx.scene_id,
                self.name,
                f"Scene '{ctx.scene_id}': OpenRouter returned no image bytes.",
            )

        base = ctx.output_dir if ctx.output_dir else (self.output_dir or Path())
        base.mkdir(parents=True, exist_ok=True)
        media_type = str(getattr(image, "media_type", "") or "")
        suffix = self._EXT_BY_MEDIA.get(media_type.lower(), ".png")
        output = base / f"autosrc_{ctx.scene_id}{suffix}"
        try:
            output.write_bytes(image_bytes)
        except Exception as exc:  # noqa: BLE001 - surfaced truthfully
            return VisualSourceResult.failure(
                ctx.scene_id,
                self.name,
                f"Scene '{ctx.scene_id}': could not write generated image: {exc}",
            )

        if not output.is_file() or output.stat().st_size <= 0:
            return VisualSourceResult.failure(
                ctx.scene_id,
                self.name,
                f"Scene '{ctx.scene_id}': generated image is missing or empty.",
            )
        try:
            verify_sourced_image(output)
        except Exception as exc:  # noqa: BLE001 - corrupt/unreadable image
            output.unlink(missing_ok=True)
            return VisualSourceResult.failure(
                ctx.scene_id,
                self.name,
                (
                    f"Scene '{ctx.scene_id}': generated image failed media "
                    f"verification: {exc}"
                ),
            )

        usage = getattr(image, "usage", None) or {}
        # Batch 1H: generic provider accounting. OpenRouter image generation
        # is billable and its API reports a monetary cost inside ``usage``
        # (USD — provider semantics); it is captured as COST evidence here,
        # never as usage.
        reported_cost = usage.get("cost") if isinstance(usage, dict) else None
        metadata = {
            "kind": "ai_generated",
            "provider": "openrouter",
            "model": getattr(image, "model", self._model),
            "media_type": media_type or "unknown",
            "prompt": prompt[:500],
            "usage": usage,
            "accounting": build_operation_accounting(
                provider="openrouter",
                model=getattr(image, "model", self._model),
                billing_status=BILLING_STATUS_BILLABLE,
                usage=usage,
                cost=reported_cost,
                cost_source=(
                    COST_SOURCE_PROVIDER_REPORTED
                    if reported_cost is not None
                    else COST_SOURCE_NONE
                ),
                currency="USD" if reported_cost is not None else None,
            ),
        }
        return VisualSourceResult.success(
            ctx.scene_id,
            self.name,
            output,
            metadata=metadata,
        )

    @staticmethod
    def _build_prompt(ctx: VisualSourceContext) -> str:
        """Compose the generation prompt from the scene's visual intent."""
        parts = [ctx.visual_prompt.strip()]
        if ctx.visual_style:
            parts.append(f"Style: {ctx.visual_style.strip()}")
        if ctx.camera_direction:
            parts.append(f"Camera: {ctx.camera_direction.strip()}")
        parts.append(
            "Single cinematic still frame; no text, no watermark, no captions."
        )
        return " ".join(part for part in parts if part)




class RoutedVisualSource(VisualSource):
    """Phase 11C Batch 1 hardening: the minimal visual ROUTING layer.

    Routes each scene among EXPLICITLY configured child sources — this is a
    selector over existing sources, never a new acquisition engine and never
    an AI routing framework:

    1. the scene's own intelligence hint (``ctx.source_hint`` from
       ``Scene.visual_source``) decides the preferred family:
       ``ai_image`` -> generation-capable children first; ``stock`` ->
       stock-family children first;
    2. otherwise the configured chain order is used (for the default "auto"
       chain: AI image generation when a model is configured, then quality
       stock (Pexels), then free Wikimedia Commons);
    3. a child that is not AVAILABLE is skipped with a truthful note (it
       never makes a call); a child that FAILS is reported and the next
       configured child is tried — falling through an explicitly configured
       chain is not a silent provider fallback;
    4. only when EVERY configured child was skipped or failed does the scene
       fail, and the failure message carries ALL reasons.

    Explicit user-supplied assets never reach this layer: the pipeline only
    auto-sources scenes without an explicit mapping or project-owned asset.
    A successful result keeps the CHILD's provider_id and metadata (true
    provenance), tagged with ``routed_via`` for routing evidence.
    """

    def __init__(
        self,
        children: List[VisualSource],
        *,
        name: str = "auto",
        output_dir: Optional[Path] = None,
    ):
        self.children = list(children)
        self.name = name or "auto"
        self.output_dir = Path(output_dir) if output_dir else None

    # -- public interface ----------------------------------------------------

    def availability(self) -> SourceAvailability:
        states = [child.availability() for child in self.children]
        if any(state == SourceAvailability.AVAILABLE for state in states):
            return SourceAvailability.AVAILABLE
        if any(
            state in (
                SourceAvailability.REQUIRES_CONFIGURATION,
                SourceAvailability.NOT_CONFIGURED,
            )
            for state in states
        ):
            return SourceAvailability.REQUIRES_CONFIGURATION
        if any(state == SourceAvailability.EXTERNAL_REQUIRED for state in states):
            return SourceAvailability.EXTERNAL_REQUIRED
        return SourceAvailability.UNAVAILABLE

    def configuration_summary(self) -> str:
        described = "; ".join(
            child.configuration_summary() for child in self.children
        )
        return (
            f"visual routing '{self.name}' over {len(self.children)} "
            f"source(s): {described}"
        )

    # -- routing ----------------------------------------------------------------

    @staticmethod
    def _generation_capable(child: VisualSource) -> bool:
        from app.orchestrator.alibaba_image import AlibabaImageSource

        return isinstance(child, (OpenRouterImageSource, AlibabaImageSource))

    @staticmethod
    def _stock_family(child: VisualSource) -> bool:
        return isinstance(child, (PexelsImageSource, WikimediaCommonsImageSource))

    def _ordered_children(self, ctx: VisualSourceContext) -> List[VisualSource]:
        hint = (ctx.source_hint or "").strip().lower()
        if hint == VISUAL_SOURCE_HINT_AI:
            preferred = [c for c in self.children if self._generation_capable(c)]
        elif hint == VISUAL_SOURCE_HINT_STOCK:
            preferred = [c for c in self.children if self._stock_family(c)]
        else:
            preferred = []
        rest = [c for c in self.children if not any(c is p for p in preferred)]
        return preferred + rest

    def source(self, ctx: VisualSourceContext) -> VisualSourceResult:
        if not self.children:
            return VisualSourceResult.failure(
                ctx.scene_id,
                self.name,
                f"Scene '{ctx.scene_id}': visual routing '{self.name}' has no "
                "configured child sources.",
            )

        attempts: List[str] = []
        for child in self._ordered_children(ctx):
            availability = child.availability()
            if availability != SourceAvailability.AVAILABLE:
                attempts.append(
                    f"{child.name}: skipped (not available: {availability.value})"
                )
                continue
            result = child.source(ctx)
            if result.status == VisualSourceStatus.SUCCESS:
                result.metadata.setdefault("routed_via", self.name)
                if ctx.source_hint:
                    result.metadata.setdefault("source_hint", ctx.source_hint)
                return result
            # Batch 1C: a generation-capable child may label its failure with
            # a Batch 1C classification. A NON-ELIGIBLE failure (invalid AVM
            # request, moderation, credential class, configuration) must stop
            # the chain truthfully — the same bad request must never be
            # silently re-sent to the next generation-capable child.
            if result.metadata.get("fallback_eligible") is False:
                attempts.append(
                    f"{child.name}: {result.message or result.status.value}"
                )
                stopped = VisualSourceResult.failure(
                    ctx.scene_id,
                    self.name,
                    (
                        f"Scene '{ctx.scene_id}': visual routing stopped at "
                        f"'{child.name}' (failure classified NOT "
                        "fallback-eligible; the request was not re-sent to "
                        "the remaining sources): "
                        + (result.message or "")
                    ),
                )
                stopped.metadata = dict(result.metadata)
                return stopped
            attempts.append(
                f"{child.name}: {result.message or result.status.value}"
            )

        hint_note = (
            f" (scene hint: '{ctx.source_hint}')" if ctx.source_hint else ""
        )
        return VisualSourceResult.failure(
            ctx.scene_id,
            self.name,
            (
                f"Scene '{ctx.scene_id}': every configured visual source "
                f"failed{hint_note}: " + " | ".join(attempts)
            ),
        )


def resolve_image_source_config() -> str:
    """Return the configured visual-source selector (never a secret)."""
    return (os.getenv(IMAGE_SOURCE_ENV) or "").strip().lower()


def _create_single_visual_source(
    sel: str,
    output_dir: Optional[Path] = None,
) -> Optional[VisualSource]:
    """Map ONE normalized selector token to a VisualSource instance."""
    if sel == "fixture":
        return FixtureImageSource(output_dir)
    if sel in ("wikimedia", "wikimedia_commons"):
        return WikimediaCommonsImageSource(output_dir)
    if sel == "pexels":
        return PexelsImageSource(output_dir)
    if sel in ("openrouter", "openrouter_image"):
        return OpenRouterImageSource(output_dir)
    if sel in ("alibaba", "alibaba_image"):
        # Batch 1C PRIMARY AI image source (Alibaba Cloud Model Studio).
        from app.orchestrator.alibaba_image import AlibabaImageSource

        return AlibabaImageSource(output_dir)
    if sel in ("auto", "routed"):
        # Batch 1C default routed chain: Alibaba AI image generation first
        # (the primary provider; an unconfigured child is skipped truthfully
        # at source time and never called), then OpenRouter AI image
        # generation (the fallback), then quality stock (Pexels), then the
        # free keyless Wikimedia Commons fallback of last resort.
        from app.orchestrator.alibaba_image import AlibabaImageSource

        return RoutedVisualSource(
            [
                AlibabaImageSource(output_dir),
                OpenRouterImageSource(output_dir),
                PexelsImageSource(output_dir),
                WikimediaCommonsImageSource(output_dir),
            ],
            name="auto",
            output_dir=output_dir,
        )
    return DormantExternalImageSource(provider_id=sel, output_dir=output_dir)


def create_visual_source(
    selector: Optional[str],
    output_dir: Optional[Path] = None,
) -> Optional[VisualSource]:
    """Factory: map a selector string to a VisualSource instance.

    Returns ``None`` when no source is configured (honest "not configured").

    - ``auto`` (the default) maps to the minimal routing layer over the
      configured children (openrouter -> pexels -> wikimedia);
    - ``pexels`` / ``openrouter`` / ``wikimedia`` / ``fixture`` map to their
      concrete sources;
    - an explicit comma chain (e.g. ``openrouter,pexels``) maps to the
      routing layer over exactly those children, in the given order;
    - any other non-empty single value maps to the dormant external adapter
      boundary (which never makes a real call).
    """
    sel = (selector or "").strip().lower()
    if not sel:
        return None
    if "," in sel:
        parts = [part.strip() for part in sel.split(",") if part.strip()]
        children: List[VisualSource] = []
        for part in parts:
            child = _create_single_visual_source(part, output_dir)
            if child is not None:
                children.append(child)
        if not children:
            return None
        if len(children) == 1:
            return children[0]
        return RoutedVisualSource(
            children,
            name="routed[" + ",".join(parts) + "]",
            output_dir=output_dir,
        )
    return _create_single_visual_source(sel, output_dir)
