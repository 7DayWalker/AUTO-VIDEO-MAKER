"""Batch 1C - Alibaba video model configuration and capability declarations.

Model selection is CONFIGURATION-DRIVEN (``AVM_ALIBABA_VIDEO_MODEL``); the
defaults below are the real DashScope video families established for Batch
1C (Wan text-to-video). No unsupported model ID is ever invented here: an
unknown configured model is served with the configured duration/size bounds
and is capability-checked before submission, and a provider-side parameter
rejection fails truthfully instead of being retried with different values.

``model_capabilities`` is the single declared-capability table used by the
video adapter's pre-submission safety guards.
"""

from __future__ import annotations

import os

ALIBABA_API_KEY_ENV = "ALIBABA_API_KEY"
ALIBABA_VIDEO_BASE_URL_ENV = "AVM_ALIBABA_BASE_URL"
ALIBABA_VIDEO_MODEL_ENV = "AVM_ALIBABA_VIDEO_MODEL"
ALIBABA_VIDEO_SIZES_ENV = "AVM_ALIBABA_VIDEO_SIZES"
ALIBABA_VIDEO_DURATIONS_ENV = "AVM_ALIBABA_VIDEO_DURATIONS"
ALIBABA_POLL_INTERVAL_ENV = "AVM_ALIBABA_POLL_INTERVAL"
ALIBABA_POLL_MAX_ATTEMPTS_ENV = "AVM_ALIBABA_POLL_MAX_ATTEMPTS"

# Validated DashScope video families (Batch 1C provider investigation):
#   - Wan text-to-video (wan2.x-t2v-*) — the validated primary default.
#   - HappyHorse variants remain CONFIGURATION-DRIVEN via
#     AVM_ALIBABA_VIDEO_MODEL (never hardcoded here).
DEFAULT_VIDEO_MODEL = "wan2.2-t2v-plus"

# Model-declared supported durations (integer seconds). Wan t2v supports 5s
# and 10s clips; anything else is a truthful capability failure BEFORE a
# billable submission.
DEFAULT_ALLOWED_DURATIONS = (5, 10)

# Bounded polling (never an infinite loop).
DEFAULT_POLL_INTERVAL = 10.0
DEFAULT_POLL_MAX_ATTEMPTS = 60

# ---------------------------------------------------------------------------
# Model-aware capability declarations.
#
# There is deliberately NO universal AVM video-resolution hard-code: each
# Alibaba model carries its OWN provider-validated supported-size list and
# default size map. The live validation (2026-09-13, recorded in
# diagnostics/batch1c_live/REPORT.md) established the authoritative evidence
# below for wan2.2-t2v-plus — the provider rejected 480*880 and listed these
# exact sizes. Only sizes that appear in a model's declared
# ``supported_sizes`` may ever be submitted; an aspect whose mapping is
# missing (or whose mapped size is not in the model's supported list) fails
# truthfully BEFORE any billable submission. Adding a future model is a new
# table entry — the selection algorithm never changes.
#
# Family entries (prefix-keyed) carry only conservative, evidence-backed
# facts (durations); sizes are recorded ONLY for exact models that were
# live-validated or provider-documented. No speculative values are invented.
# ---------------------------------------------------------------------------
_FAMILY_CAPABILITIES = {
    "wan2.": {
        "modes": ("t2v",),
        "durations": (5, 10),
        "audio": None,  # native audio observed truthfully from the artifact
    },
    "happyhorse": {
        "modes": ("t2v",),
        "durations": (5, 10),
        "audio": None,
    },
}

_MODEL_CAPABILITIES = {
    "wan2.2-t2v-plus": {
        # LIVE-VALIDATED provider evidence (InvalidParameter truth):
        "supported_sizes": (
            "1080*1920",
            "1920*1080",
            "1440*1440",
            "1632*1248",
            "1248*1632",
            "480*832",
            "832*480",
            "624*624",
        ),
        # Only exact-ratio sizes satisfy AVM's Phase 7A exact-aspect gate:
        # 480*832 (0.577) is NOT 9:16 (0.5625) — live-proven — so the
        # smallest AVM-compatible 9:16 size for this model is 1080*1920.
        "size_map": {"9:16": "1080*1920", "16:9": "1920*1080"},
    },
}


def model_capabilities(model_id: str) -> dict:
    """Declared capabilities for a configured model id.

    Family defaults are merged with (and overridden by) the exact model
    entry when one exists. An unknown model is served conservatively
    (t2v only, family/default duration bounds, NO size assumptions): its
    sizes must then come from explicit configuration
    (``AVM_ALIBABA_VIDEO_SIZES``) and any provider-side rejection fails
    truthfully instead of being retried with different values.
    """
    key = (model_id or "").strip().lower()
    caps: dict = {
        "modes": ("t2v",),
        "durations": DEFAULT_ALLOWED_DURATIONS,
        "audio": None,
    }
    for prefix, family_caps in _FAMILY_CAPABILITIES.items():
        if key.startswith(prefix):
            caps.update(family_caps)
            break
    exact = _MODEL_CAPABILITIES.get(key)
    if exact:
        caps.update(exact)
    return caps


def select_video_size(
    model_id: str,
    aspect_ratio: str,
    configured_size_map: Optional[Dict[str, str]] = None,
) -> tuple:
    """Model-aware size selection for one AVM aspect ratio.

    Resolution of the submitted size, in order:

    1. an operator-configured mapping (``AVM_ALIBABA_VIDEO_SIZES`` /
       ``size_map`` constructor argument) for the requested aspect — an
       explicit operator choice always wins;
    2. the selected model's own declared default size map
       (provider-validated evidence).

    The chosen size is then validated against the model's declared
    ``supported_sizes`` (when the model declares them): a size the provider
    does not support is NEVER silently submitted to rely on Alibaba's
    rejection — it fails truthfully BEFORE any billable call.

    Returns ``(size, None)`` on success or ``(None, reason)`` with a
    truthful capability-failure message. This function contains no
    resolution hard-codes: every size it can return comes from a per-model
    capability entry or explicit configuration.
    """
    caps = model_capabilities(model_id)
    aspect = (aspect_ratio or "").strip()
    supported = caps.get("supported_sizes")

    configured = (configured_size_map or {}).get(aspect)
    declared_default = (caps.get("size_map") or {}).get(aspect)

    if configured:
        size = str(configured)
        source = "operator configuration"
    elif declared_default:
        size = str(declared_default)
        source = f"model '{model_id}' declared default"
    else:
        return (
            None,
            (
                f"Alibaba video model '{model_id}' has no declared size for "
                f"aspect ratio '{aspect}' and no operator mapping is "
                "configured (set AVM_ALIBABA_VIDEO_SIZES, e.g. "
                f"'{aspect}=W*H'); no provider default was substituted"
            ),
        )

    if supported is not None and size not in supported:
        return (
            None,
            (
                f"Alibaba video model '{model_id}' does not support size "
                f"'{size}' for aspect ratio '{aspect}' (from {source}); "
                f"provider-supported sizes: {list(supported)}; no provider "
                "default was substituted"
            ),
        )
    return size, None


def env_or_default(name: str, default: str) -> str:
    value = os.getenv(name, "")
    return value.strip() or default
