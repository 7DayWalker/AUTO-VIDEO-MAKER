"""
AUTO Video Maker — Local Web Experience Server (stdlib only)

Serves the premium web UI (webapp/) plus a THIN JSON API that adapts the
existing connection layer and reads real project manifests read-only.

Design rules:
- NO publishing/OAuth logic lives here. Connection endpoints call the Step-1
  connection module; project endpoints parse output/projects manifests.
- Binds to 127.0.0.1 only. Secrets are never returned or logged.
- Connect endpoints run the existing interactive OAuth flows in a background
  thread and respond 202 immediately; the UI polls /api/connections.

Run:
    python -m app.web.server            (default port from AVM_WEB_PORT, else 8787)
"""

import json
import logging
import os
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote

from app.config import PROJECT_ROOT
from app.assembly.audio_contract import (
    AudioPolicy,
    audio_policy_requires_narration,
    normalize_audio_policy,
)

logger = logging.getLogger(__name__)

WEBAPP_DIR = PROJECT_ROOT / "webapp"
PROJECTS_DIR = PROJECT_ROOT / "output" / "projects"
PRODUCTIONS_DIR = PROJECT_ROOT / "productions"
DEFAULT_PORT = int(os.getenv("AVM_WEB_PORT", "8787"))

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".json": "application/json",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".mkv": "video/x-matroska",
}

MEDIA_SUFFIXES = {".mp4", ".webm", ".mov", ".m4v"}

# Tracks an in-flight interactive connect so repeated POSTs don't stack flows.
_connect_running = {"platform": None}


# ---------------------------------------------------------------------------
# Data adapters (thin!)
# ---------------------------------------------------------------------------

def connections_payload() -> dict:
    """Connection status via the Step-1 connection layer (non-interactive)."""
    from app.publishing.connections import _tiktok_status, _youtube_status
    from app.publishing.tiktok import TikTokPublisher
    from app.publishing.youtube import YouTubePublisher

    payload = {}
    try:
        yt = dict(_youtube_status(YouTubePublisher.from_env()))
    except Exception as exc:
        logger.warning("YouTube status failed: %s", exc)
        yt = {"status": "ERROR"}
    yt.pop("detail", None)
    yt["sample"] = False
    payload["youtube"] = yt

    try:
        tk = dict(_tiktok_status(TikTokPublisher.from_env()))
    except Exception as exc:
        logger.warning("TikTok status failed: %s", exc)
        tk = {"status": "ERROR"}
    tk.pop("detail", None)
    tk["sample"] = False
    payload["tiktok"] = tk

    payload["instagram"] = {"status": "COMING SOON", "sample": False}
    payload["facebook"] = {"status": "COMING SOON", "sample": False}
    return payload


# ---------------------------------------------------------------------------
# Phase 2C — READ-ONLY LOCAL CONFIGURATION READINESS (GET /api/capabilities)
#
# Smallest honest answer to "is the selected generation path locally
# configured?" BEFORE execution reaches a generation stage. Every fact is a
# projection of the SAME configuration sources/predicates the executor itself
# consults (no parallel configuration system):
#   planner        -> app.content.providers.get_default_provider().is_available()
#   tts            -> app.voice.config.TTS_PROVIDER + SUPPORTED_PROVIDERS
#   image_source   -> app.orchestrator.visual_sources availability()
#   alibaba_video  -> ALIBABA_API_KEY presence (the gateway's real gate)
#   openrouter_video -> resolve_openrouter_video_api_key() (bool only)
#
# Guarantees this surface makes: NONE about provider uptime, connectivity,
# quota, balance, or execution success. "ready" means LOCALLY CONFIGURED —
# never "provider available". It never contacts a provider, never opens a
# network connection, never runs a subprocess, never reads credentials into
# the response, and never touches production/project state (GET read-only).
# production_routes below is a PRESENTATION projection of these facts only —
# it is not route resolution and must never replace it.
# ---------------------------------------------------------------------------


def _canonical_models_projection() -> dict:
    """AVM Step 7 — MODEL-FIRST projection of the canonical model registry.

    The user-facing AI Brain vocabulary is the CANONICAL MODEL owned by
    ``app/content/model_registry.py`` (Step 1). This projection lets a client
    render ``models.map(...)`` for an arbitrary registry — N canonical models,
    N providers, N bindings — without duplicating the registry and without ever
    hardcoding a model or provider.

    Deliberate omissions (model-first product rule + secret discipline):

    * NO provider identity and NO ``provider_model_id``: bindings are COUNTED,
      never named, so this projection can never be turned into a provider
      selector. Provider identity stays internal execution evidence and reaches
      a UI only through persisted execution evidence (``_plan_evidence``).
    * NO ``config_keys`` (environment variable names) and NO credential values.
    * NO cost/price: AVM has no authoritative AI Brain pricing (Step 6 records
      ``billing_status=unknown``, ``cost=None``), so none is projected.
    * NO ranking/recommendation of any kind — registry order is preserved.

    Readiness reuses the SAME configuration-only predicate the executor itself
    consults (``BaseLLMProvider.is_available()``, which every provider
    implements as a local configuration check), so ``status`` uses the existing
    seam vocabulary and "ready" still means LOCALLY CONFIGURED — never
    "provider available". No network call, no subprocess, no provider request.

    A registry/probe failure degrades to an honest empty list with
    ``status="unknown"``; a model is NEVER fabricated to fill the gap.
    """
    projection: dict = {
        "note": (
            "Canonical AI Brain models from AVM's model registry. The user "
            "selects a canonical model; provider bindings are internal "
            "execution detail and are deliberately not exposed here. "
            "'ready' means LOCALLY CONFIGURED, never 'provider available'."
        ),
        "source": "app.content.model_registry",
        "status": "ready",
        "models": [],
    }
    try:
        from app.content import provider_registry
        from app.content.model_registry import list_models
    except Exception as exc:  # noqa: BLE001 - readiness must never crash
        logger.warning("capability model registry import failed: %s", exc)
        projection["status"] = "unknown"
        return projection

    try:
        canonical_models = list_models()
    except Exception as exc:  # noqa: BLE001
        logger.warning("capability model registry read failed: %s", exc)
        projection["status"] = "unknown"
        return projection

    for canonical in canonical_models:
        capabilities = getattr(canonical, "capabilities", None)
        entry = {
            "id": canonical.id,
            "display_name": canonical.display_name,
            "family": getattr(canonical, "family", None),
            # Generic, provider-agnostic capability flags only (Step 1
            # vocabulary). Never derived from what a provider happens to do.
            "capabilities": {
                "text": bool(getattr(capabilities, "text", False)),
                "json": bool(getattr(capabilities, "json_", False)),
                "reasoning": bool(getattr(capabilities, "reasoning", False)),
                "vision": bool(getattr(capabilities, "vision", False)),
                "long_context": bool(getattr(capabilities, "long_context", False)),
                "tool_use": bool(getattr(capabilities, "tool_use", False)),
            },
            # Binding COUNT only — provider identity stays internal.
            "binding_count": len(canonical.bindings or ()),
            "configured": False,
            "status": "not_configured",
        }

        configured = 0
        unknown = 0
        bindings = tuple(canonical.bindings or ())
        for binding in bindings:
            try:
                if not provider_registry.has_provider(binding.provider):
                    # Registered model, provider not registered → honestly not
                    # configured. The provider name is never surfaced.
                    continue
                instance = provider_registry.create_provider(
                    binding.provider, binding.provider_model_id
                )
                if bool(instance.is_available()):
                    configured += 1
            except Exception as exc:  # noqa: BLE001 - never crash readiness
                logger.warning(
                    "capability model binding probe failed for '%s': %s",
                    canonical.id, exc,
                )
                unknown += 1

        entry["configured"] = configured > 0
        if configured > 0:
            entry["status"] = "ready"
        elif bindings and unknown == len(bindings):
            # Every binding probe failed: truthfully unknown, NOT "unavailable".
            entry["status"] = "unknown"
        else:
            entry["status"] = "not_configured"
        projection["models"].append(entry)

    return projection


def _ai_summary_projection() -> dict:
    """AVM Track 9 -- AI Summary understanding model projection (model-first, provider-neutral).

    Additive ai_summary surface for GET /api/capabilities:
      - modes: metadata for standard (transcript/audio-first, default SUMMARY_STANDARD_MODEL)
        and pro (frame-based/multimodal visual understanding, default SUMMARY_PRO_MODEL).
      - understanding_models: canonical models eligible for AI Summary, exposing ONLY
        canonical id, display_name, family, capabilities, eligible_modes,
        configured, status -- never provider names, wire IDs, quota or balance.

    Truthfulness: readiness reuses the SAME BaseLLMProvider.is_available predicate
    the executor consults; quota/balance stays unknown when no balance endpoint exists.
    No network, no subprocess, no provider instantiation beyond local availability probe.
    """
    modes = {}
    understanding_models = []
    status = "ready"
    try:
        from app.production.narration import SUMMARY_PRO_MODEL, SUMMARY_STANDARD_MODEL
        modes = {
            "standard": {
                "id": "standard",
                "label": "Standard",
                "description": "transcript/audio-first understanding",
                "understanding_method": "transcript_audio",
                "default_model_id": SUMMARY_STANDARD_MODEL,
            },
            "pro": {
                "id": "pro",
                "label": "Pro",
                "description": "frame-based/multimodal visual understanding",
                "understanding_method": "frame_sequence",
                "default_model_id": SUMMARY_PRO_MODEL,
            },
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai_summary modes projection failed: %s", exc)
        modes = {
            "standard": {"id": "standard", "default_model_id": "qwen-3.8-flash"},
            "pro": {"id": "pro", "default_model_id": "gemini-3-flash-preview"},
        }
        status = "unknown"
    try:
        from app.content import provider_registry
        from app.content.model_registry import list_models
        canonical_models = list(list_models())
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai_summary model list failed: %s", exc)
        return {"modes": modes, "understanding_models": [], "status": "unknown"}
    for canonical in canonical_models:
        caps = getattr(canonical, "capabilities", None)
        if caps is None:
            continue
        is_standard = bool(getattr(caps, "text", False) and getattr(caps, "json_", False))
        try:
            is_pro = bool(caps.is_pro_eligible())
        except Exception:
            try:
                is_pro = bool(caps.is_multimodal_capable())
            except Exception:
                is_pro = False
        if not is_standard and not is_pro:
            continue
        eligible_modes = []
        if is_standard:
            eligible_modes.append("standard")
        if is_pro:
            eligible_modes.append("pro")
        cap_proj = {
            "text": bool(getattr(caps, "text", False)),
            "json": bool(getattr(caps, "json_", False)),
            "reasoning": bool(getattr(caps, "reasoning", False)),
            "vision": bool(getattr(caps, "vision", False)),
            "long_context": bool(getattr(caps, "long_context", False)),
            "tool_use": bool(getattr(caps, "tool_use", False)),
        }
        configured = 0
        unknown = 0
        bindings = tuple(getattr(canonical, "bindings", None) or ())
        for binding in bindings:
            try:
                if not provider_registry.has_provider(binding.provider):
                    continue
                instance = provider_registry.create_provider(binding.provider, binding.provider_model_id)
                if bool(instance.is_available()):
                    configured += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("ai_summary binding probe failed for '%s': %s", canonical.id, exc)
                unknown += 1
        entry_configured = configured > 0
        if entry_configured:
            entry_status = "ready"
        elif bindings and unknown == len(bindings):
            entry_status = "unknown"
        else:
            entry_status = "not_configured"
        understanding_models.append({
            "id": canonical.id,
            "display_name": canonical.display_name,
            "family": getattr(canonical, "family", None),
            "capabilities": cap_proj,
            "eligible_modes": eligible_modes,
            "configured": entry_configured,
            "status": entry_status,
        })
    return {"modes": modes, "understanding_models": understanding_models, "status": status}


def capabilities_payload():
    """Build the read-only configuration-readiness projection."""
    seam_status_ready = "ready"
    seam_status_missing = "not_configured"

    # -- planner / AI Brain: the exact predicate create_scene_planner uses ---
    planner = {"provider": None, "model": None, "configured": False,
               "status": seam_status_missing}
    try:
        from app.content import providers as content_providers

        try:
            provider = content_providers.get_default_provider()
        except ValueError:
            # Unrecognized DEFAULT_LLM_PROVIDER: honestly unknown, never a
            # guessed fallback (mirrors the executor's fail-loudly behavior).
            planner["provider"] = str(
                getattr(content_providers, "DEFAULT_LLM_PROVIDER", "") or ""
            ).strip().lower() or None
            planner["status"] = "unknown"
        else:
            available = bool(provider.is_available())
            planner["provider"] = getattr(provider, "name", None)
            planner["model"] = getattr(provider, "model", None)
            planner["configured"] = available
            if planner["provider"] == "mock":
                # Explicit offline/test mode: usable, but never real AI
                # planning — disclosed, not presented as production readiness.
                planner["status"] = "offline_mode"
                planner["configured"] = True
            else:
                planner["status"] = (
                    seam_status_ready if available else seam_status_missing
                )
    except Exception as exc:  # noqa: BLE001 - readiness must never crash
        logger.warning("capability planner projection failed: %s", exc)
        planner["status"] = "unknown"

    # -- TTS: existing configuration semantics (no external verification) ---
    tts = {"provider": None, "configured": False, "status": seam_status_missing}
    try:
        from app.voice import config as voice_config

        tts["provider"] = str(voice_config.TTS_PROVIDER or "").strip() or None
        supported = tts["provider"] in voice_config.SUPPORTED_PROVIDERS
        tts["configured"] = supported
        if tts["provider"] == "mock":
            tts["status"] = "offline_mode"
            tts["configured"] = True
        elif not supported:
            tts["status"] = "unsupported"
        else:
            tts["status"] = seam_status_ready
    except Exception as exc:  # noqa: BLE001
        logger.warning("capability tts projection failed: %s", exc)
        tts["status"] = "unknown"

    # -- image source: reuse the visual_sources availability() authority -----
    image_source = {
        "selector": None, "source": None, "configured": False,
        "status": seam_status_missing, "children": [],
    }
    try:
        from app.orchestrator.visual_sources import (
            DEFAULT_IMAGE_SOURCE,
            SourceAvailability,
            create_visual_source,
            resolve_image_source_config,
        )

        selector = resolve_image_source_config() or DEFAULT_IMAGE_SOURCE
        image_source["selector"] = selector
        source = create_visual_source(selector)
        if source is None:
            image_source["status"] = SourceAvailability.NOT_CONFIGURED.value
        else:
            availability = source.availability()
            image_source["source"] = source.name
            image_source["status"] = availability.value
            image_source["configured"] = (
                availability == SourceAvailability.AVAILABLE
            )
            for child in getattr(source, "children", []) or []:
                image_source["children"].append({
                    "name": child.name,
                    "status": child.availability().value,
                })
    except Exception as exc:  # noqa: BLE001
        logger.warning("capability image-source projection failed: %s", exc)
        image_source["status"] = "unknown"

    # -- Alibaba video seam: the gateway's actual gate is the key presence --
    alibaba_video = {"configured": False, "status": seam_status_missing,
                     "model": None}
    try:
        from app.orchestrator.alibaba_video_config import (
            ALIBABA_API_KEY_ENV,
            ALIBABA_VIDEO_MODEL_ENV,
            DEFAULT_VIDEO_MODEL,
            env_or_default,
        )

        configured = bool((os.getenv(ALIBABA_API_KEY_ENV) or "").strip())
        alibaba_video["configured"] = configured
        alibaba_video["status"] = (
            seam_status_ready if configured else seam_status_missing
        )
        # Configuration-driven model identifier (non-secret by convention).
        alibaba_video["model"] = env_or_default(
            ALIBABA_VIDEO_MODEL_ENV, DEFAULT_VIDEO_MODEL
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("capability alibaba_video projection failed: %s", exc)
        alibaba_video["status"] = "unknown"

    # -- OpenRouter video seam: reuse its existing key resolver (bool only) --
    openrouter_video = {"configured": False, "status": seam_status_missing,
                        "model": None}
    try:
        from app.orchestrator.openrouter_video import (
            OPENROUTER_MODEL_ENV,
            resolve_openrouter_video_api_key,
        )

        configured = bool(resolve_openrouter_video_api_key())
        openrouter_video["configured"] = configured
        openrouter_video["status"] = (
            seam_status_ready if configured else seam_status_missing
        )
        model = (os.getenv(OPENROUTER_MODEL_ENV) or "").strip()
        openrouter_video["model"] = model or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("capability openrouter_video projection failed: %s", exc)
        openrouter_video["status"] = "unknown"
    seams = {
        "planner": planner,
        "tts": tts,
        "image_source": image_source,
        "alibaba_video": alibaba_video,
        "openrouter_video": openrouter_video,
    }

    # -- route-level readiness: PURE PRESENTATION PROJECTION -----------------
    # Maps each existing SaaS route onto the seams its CURRENT compilation
    # consults. It re-implements no route logic, is never consulted by the
    # executor, and must not become a replacement for route resolution:
    # resolve_effective_route/apply_production_route remain the sole
    # execution authority.
    from app.production.routes import (
        PRODUCTION_ROUTES,
        ROUTE_FULL_AI_VIDEO,
        ROUTE_LOCAL_AI_ASSISTED,
    )

    route_requirements = {
        ROUTE_LOCAL_AI_ASSISTED: ("planner", "tts", "image_source"),
        ROUTE_FULL_AI_VIDEO: ("planner", "tts", "alibaba_video"),
    }
    production_routes = {}
    for route_id in PRODUCTION_ROUTES:
        required = route_requirements.get(route_id, ())
        missing = [name for name in required if not seams[name]["configured"]]
        statuses = {name: seams[name]["status"] for name in required}
        if missing:
            status = "not_ready"
        elif "offline_mode" in statuses.values():
            status = "offline_mode"
        else:
            status = seam_status_ready
        production_routes[route_id] = {"status": status, "missing": missing}

    return _redact_secrets({
        "notice": (
            "Local configuration readiness only. Does NOT verify provider "
            "uptime, connectivity, quota, balance, or generation success; "
            "'ready' means locally configured, never 'provider available'. "
            "This surface is independent of production state."
        ),
        "seams": seams,
        # AVM Step 7 (additive): the canonical model registry projection that
        # makes a client MODEL-FIRST. It never names a provider, never exposes
        # a wire model id, and never ranks or prices a model.
        "models": _canonical_models_projection(),
        "ai_summary": _ai_summary_projection(),  # Track 9: understanding model projection; never exposes provider/wire/quota
        "production_routes": {
            "note": (
                "Presentation projection of the configuration facts above; "
                "NOT route resolution. The executor's route compilation "
                "remains the sole execution authority."
            ),
            "routes": production_routes,
        },
        "sample": False,
    })


def _load_manifest(project_dir: Path):
    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except Exception as exc:
        logger.warning("Could not parse %s: %s", manifest_path, exc)
        return None


def _project_publications_summary(manifest: dict) -> list:
    """Safe durable evidence only; reading this projection never contacts a provider."""
    out = []
    for platform, field in (("youtube", "publication"), ("tiktok", "tiktok_publication")):
        record = manifest.get(field)
        if isinstance(record, dict) and record.get("state"):
            out.append(_publication_payload(record, platform))
    return out


def _publication_summary(manifest: dict) -> list:
    """Compatibility name used by the existing project and unit read surfaces."""
    return _project_publications_summary(manifest)


def _binding_production_id(manifest: dict, directory_name: str):
    """Presentational-only cross-reference: the production id asserted by the
    manifest's OWN production_binding. Surfaced only when structurally
    well-formed and self-consistent (binding.project_id == directory name).
    Full binding validation (production/unit existence) remains the authority
    of project_detail; this adds no I/O — the manifest is already loaded."""
    binding = manifest.get("production_binding")
    if not isinstance(binding, dict):
        return None
    if not all(
        binding.get(field) not in (None, "")
        for field in ("production_id", "unit_id", "project_id")
    ):
        return None
    if str(binding.get("project_id")) != directory_name:
        return None
    return str(binding.get("production_id"))


def projects_index() -> list:
    if not PROJECTS_DIR.exists():
        return []
    items = []
    for child in sorted(PROJECTS_DIR.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = _load_manifest(child)
        if not manifest:
            items.append({
                "project_id": child.name,
                "directory_id": child.name,
                "topic": child.name,
                "created_at": None,
                "current_stage": "",
                "stage_status": "unavailable",
                "final_video_available": False,
                "publications": [],
                "error": "PROJECT_MANIFEST_UNAVAILABLE",
                "sample": False,
            })
            continue
        stages = manifest.get("stages") or {}
        stage_status = "running"
        if any(
            isinstance(stage, dict)
            and str(stage.get("status") or "").upper() == "FAILED"
            for stage in stages.values()
        ):
            stage_status = "failed"
        elif manifest.get("completed_at"):
            stage_status = "complete"
        final_video = manifest.get("final_video_path")
        final_path, final_reason = _safe_project_path(child, final_video)
        failed_stage = any(
            str(name).lower() in {"assembly", "complete"}
            and str((stage or {}).get("status", "")).upper() == "FAILED"
            for name, stage in stages.items()
            if isinstance(stage, dict)
        )
        items.append({
            # The execution directory ID is the unique routing identity (it is
            # also ProjectManifest.production_binding.project_id).  The plan's
            # project_id is evidence only and is not unique across projects.
            "project_id": child.name,
            "directory_id": child.name,
            "topic": manifest.get("topic") or child.name,
            "created_at": manifest.get("created_at"),
            "current_stage": str(manifest.get("current_stage", "")),
            "stage_status": stage_status,
            "final_video_available": bool(
                not failed_stage
                and final_path is not None
                and final_reason is None
                and final_path.suffix.lower() in MEDIA_SUFFIXES
            ),
            "publications": _publication_summary(manifest),
            "binding_production_id": _binding_production_id(manifest, child.name),
            "sample": False,
        })
    return items


def _project_directory_for(project_id: str):
    """Resolve either an execution directory ID or legacy manifest project ID."""
    direct = PROJECTS_DIR / project_id
    try:
        direct.resolve().relative_to(PROJECTS_DIR.resolve())
    except (ValueError, OSError):
        return None
    if direct.is_dir() and _load_manifest(direct):
        return direct
    if not PROJECTS_DIR.is_dir():
        return None
    for child in PROJECTS_DIR.iterdir():
        if not child.is_dir():
            continue
        manifest = _load_manifest(child)
        if manifest and manifest.get("project_id") == project_id:
            return child
    return None


def project_detail(project_id: str):
    directory = _project_directory_for(project_id)
    if directory is None:
        return None
    manifest = _load_manifest(directory)
    if not manifest:
        return None
    for item in projects_index():
        if item["directory_id"] == directory.name:
            result = dict(item)
            result["directory_id"] = directory.name
            binding = manifest.get("production_binding")
            result["production_binding"] = None
            if binding is not None:
                fields = (
                    "production_id", "unit_id", "production_plan_version",
                    "unit_plan_identity", "unit_plan_version",
                    "unit_plan_content_hash", "project_id", "bound_at",
                )
                valid = (
                    isinstance(binding, dict)
                    and all(binding.get(field) not in (None, "") for field in fields)
                    and str(binding.get("project_id")) == directory.name
                )
                if valid:
                    try:
                        from app.production.storage import load_production

                        production = load_production(
                            str(binding["production_id"]), root=PRODUCTIONS_DIR
                        )
                        valid = any(
                            unit.unit_id == binding["unit_id"]
                            for unit in production.units
                        )
                    except Exception:
                        valid = False
                if valid:
                    result["production_binding"] = {
                        field: binding.get(field) for field in fields
                    }
                else:
                    result["binding_error"] = "PROJECT_BINDING_INVALID"
            return result
    return None


def media_path_for(project_id: str):
    directory = _project_directory_for(project_id)
    if directory is None:
        return None
    manifest = _load_manifest(directory) or {}
    raw = manifest.get("final_video_path")
    if not raw:
        return None
    try:
        path = Path(str(raw))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        resolved = path.resolve(strict=False)
        resolved.relative_to(PROJECT_ROOT.resolve())
        resolved.relative_to(directory.resolve())
        stages = manifest.get("stages") or {}
        if any(
            str(name).lower() in {"assembly", "complete"}
            and str((stage or {}).get("status", "")).upper() == "FAILED"
            for name, stage in stages.items()
            if isinstance(stage, dict)
        ):
            return None
        if (resolved.suffix.lower() not in MEDIA_SUFFIXES or
                not path.is_file() or path.stat().st_size <= 0):
            return None
    except (ValueError, OSError):
        return None
    return resolved


# ---------------------------------------------------------------------------
# Visual Asset Library — additive, read-only surfaces over the durable asset
# manifest (app/orchestrator/assets.py). Never invents entries, never serves
# files outside the project directory. SHA-256 re-verification is an explicit
# on-demand endpoint (/verify), never per-byte-range work on media delivery.
# ---------------------------------------------------------------------------

def _stored_asset_path(project_dir: Path, stored_rel):
    """Resolve a project-dir-relative 'stored_as' path; refuse any escape."""
    if not stored_rel:
        return None, "no stored path recorded"
    try:
        resolved = (Path(project_dir) / str(stored_rel)).resolve(strict=False)
        resolved.relative_to(Path(project_dir).resolve())
        if not resolved.is_file():
            return None, "stored asset file is missing"
        if resolved.stat().st_size <= 0:
            return None, "stored asset file is empty"
        return resolved, None
    except (OSError, ValueError) as exc:
        return None, f"unsafe asset path: {type(exc).__name__}"


def _project_asset_entry(project_id: str, scene_id: str):
    from app.orchestrator.assets import load_asset_manifest

    directory = _project_directory_for(project_id)
    if directory is None:
        return None, None
    manifest = load_asset_manifest(directory)
    entry = (manifest.get("assets") or {}).get(scene_id)
    return directory, (entry if isinstance(entry, dict) else None)


def project_assets_payload(project_id: str):
    """Read-only asset index for one project, straight from the durable
    asset manifest. Provenance fields are surfaced verbatim when present."""
    from app.orchestrator.assets import load_asset_manifest

    directory = _project_directory_for(project_id)
    if directory is None:
        return 404, {
            "error": "PROJECT_NOT_FOUND",
            "message": f"Project {project_id!r} was not found.",
        }
    manifest = load_asset_manifest(directory)
    assets = manifest.get("assets") or {}
    items = []
    for scene_id in sorted(assets):
        raw = assets.get(scene_id)
        entry = raw if isinstance(raw, dict) else {}
        _, issue = _stored_asset_path(directory, entry.get("stored_as"))
        items.append({
            "scene_id": scene_id,
            "source_name": entry.get("source_name"),
            "stored_as": entry.get("stored_as"),
            "media_type": entry.get("media_type"),
            "provider": entry.get("provider"),
            "sha256": entry.get("sha256"),
            "bytes": entry.get("bytes"),
            "supplied_by": entry.get("supplied_by"),
            "source_provider": entry.get("source_provider"),
            "source_status": entry.get("source_status"),
            "source_metadata": entry.get("source_metadata"),
            "file_present": issue is None,
            "file_issue": issue,
        })
    return 200, _redact_secrets({
        "project_id": directory.name,
        "manifest_version": manifest.get("version"),
        "asset_count": len(items),
        "assets": items,
        "sample": False,
    })


def asset_media_path_for(project_id: str, scene_id: str):
    """Root-confined stored asset file for one scene, or (None, error-code)."""
    directory, entry = _project_asset_entry(project_id, scene_id)
    if directory is None:
        return None, "PROJECT_NOT_FOUND"
    if entry is None:
        return None, "ASSET_NOT_FOUND"
    path, _reason = _stored_asset_path(directory, entry.get("stored_as"))
    if path is None:
        return None, "ASSET_FILE_UNAVAILABLE"
    if path.suffix.lower() not in MIME:
        return None, "ASSET_TYPE_UNSUPPORTED"
    return path, None


def asset_verify_payload(project_id: str, scene_id: str):
    """Explicit on-demand SHA-256 re-verification of one stored asset."""
    from app.orchestrator.assets import verify_stored_asset

    directory, entry = _project_asset_entry(project_id, scene_id)
    if directory is None:
        return 404, {
            "error": "PROJECT_NOT_FOUND",
            "message": f"Project {project_id!r} was not found.",
        }
    if entry is None:
        return 404, {
            "error": "ASSET_NOT_FOUND",
            "message": f"Scene {scene_id!r} has no asset manifest entry.",
        }
    reason = verify_stored_asset(directory, entry)
    return 200, {
        "project_id": directory.name,
        "scene_id": scene_id,
        "verified": reason is None,
        "reason": reason,
        "sha256": entry.get("sha256"),
        "sample": False,
    }


def _safe_project_path(project_dir: Path, raw_path) -> tuple[Path | None, str | None]:
    """Resolve an existing manifest path without permitting workspace escape."""
    if not raw_path:
        return None, "no path recorded"
    try:
        candidate = Path(str(raw_path))
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(project_dir.resolve())
        if not candidate.exists():
            return resolved, "recorded path is missing"
        if not candidate.is_file():
            return resolved, "recorded path is not a regular file"
        if resolved.stat().st_size <= 0:
            return resolved, "recorded file is empty"
        return resolved, None
    except (OSError, ValueError) as exc:
        return None, f"unsafe artifact path: {type(exc).__name__}"


def _artifact_state(
    project_dir: Path,
    *,
    artifact_type: str,
    raw_path=None,
    failed: bool = False,
    applicable: bool = True,
    media: bool = False,
) -> dict:
    """Derive the frozen presentation state without persisting or mutating data."""
    if failed:
        state = "FAILED"
        reason = "The authoritative producing stage reports failure."
        safe_path = None
    else:
        safe_path, reason = _safe_project_path(project_dir, raw_path)
        if raw_path and safe_path is not None and reason is None:
            if media and safe_path.suffix.lower() not in MEDIA_SUFFIXES:
                reason = "recorded file type is not supported for browser preview"
            if reason is None:
                state = "AVAILABLE"
            else:
                state = "RECORDED BUT MISSING"
        elif raw_path:
            state = "RECORDED BUT MISSING"
        elif not applicable:
            state = "NOT APPLICABLE"
        else:
            state = "NOT GENERATED"
    return {
        "type": artifact_type,
        "state": state,
        "available": state == "AVAILABLE",
        "safe_path": _relative_display(safe_path) if state == "AVAILABLE" else None,
        "media_url": None,
        "reason": reason,
    }


def _stage_failed(manifest, *stage_names: str) -> bool:
    wanted = {name.lower() for name in stage_names}
    return any(
        name.lower() in wanted
        and str(getattr(result.status, "value", result.status)).upper() == "FAILED"
        for name, result in manifest.stages.items()
    )


def _artifact_payload(manifest, project_id: str | None = None) -> dict:
    """Browser-safe artifact summary; raw internal paths never cross this boundary."""
    project_dir = PROJECTS_DIR / (project_id or manifest.project_id)
    return {
        "content_plan": _artifact_state(
            project_dir, artifact_type="Content plan", raw_path=manifest.content_plan_path,
            failed=_stage_failed(manifest, "content"),
        ),
        "voice_audio": _artifact_state(
            project_dir, artifact_type="Voice audio", raw_path=manifest.voice_audio_path,
            failed=_stage_failed(manifest, "voice"), media=True,
            applicable=manifest.source_summary_handoff is None,
        ),
        "voice_timing": _artifact_state(
            project_dir, artifact_type="Voice timing", raw_path=manifest.voice_timing_path,
            failed=_stage_failed(manifest, "voice"),
            applicable=manifest.source_summary_handoff is None,
        ),
        "scene_manifest": _artifact_state(
            project_dir, artifact_type="Scene manifest", raw_path=manifest.scene_manifest_path,
            failed=_stage_failed(manifest, "scenes"),
            applicable=manifest.source_summary_handoff is None,
        ),
        "captions": _artifact_state(
            project_dir, artifact_type="Captions", raw_path=manifest.captions_path,
            failed=_stage_failed(manifest, "captions"),
            applicable=manifest.assembly_config.get("enable_captions", True) is not False,
        ),
        "final_video": _artifact_state(
            project_dir, artifact_type="Final video", raw_path=manifest.final_video_path,
            failed=_stage_failed(manifest, "assembly", "complete"), media=True,
        ),
    }


def _scene_media_path(project_id: str, scene_id: str):
    """Return a validated scene media path from the bound manifest/checkpoint evidence."""
    directory = _project_directory_for(project_id)
    if directory is None:
        return None, "PROJECT_NOT_FOUND"
    manifest = _load_manifest(directory)
    if not manifest:
        return None, "PROJECT_NOT_FOUND"
    checkpoints = manifest.get("scene_checkpoints") or {}
    checkpoint = checkpoints.get(scene_id)
    if not isinstance(checkpoint, dict):
        checkpoint = {}
        scene_paths = manifest.get("scene_video_paths") or []
        match = re.search(r"(?:^|[_-])(\d+)$", str(scene_id))
        if not match or not (0 <= int(match.group(1)) - 1 < len(scene_paths)):
            return None, "SCENE_NOT_FOUND"
    if str(checkpoint.get("status") or "").upper() == "FAILED":
        return None, "SCENE_MEDIA_FAILED"
    stages = manifest.get("stages") or {}
    scenes_stage = stages.get("scenes") or {}
    if str(scenes_stage.get("status") or "").upper() == "FAILED":
        return None, "SCENE_MEDIA_FAILED"
    raw = checkpoint.get("video_path") or checkpoint.get("file_path")
    evidence = checkpoint.get("checkpoint_evidence") or {}
    raw = raw or evidence.get("video_path") or evidence.get("file_path")
    if not raw:
        scene_paths = manifest.get("scene_video_paths") or []
        match = re.search(r"(?:^|[_-])(\d+)$", str(scene_id))
        if match:
            index = int(match.group(1)) - 1
            if 0 <= index < len(scene_paths):
                raw = scene_paths[index]
    if not raw:
        return None, "SCENE_MEDIA_NOT_GENERATED"
    return _validated_scene_file(directory, raw)


def _validated_scene_file(directory: Path, raw_path):
    """Validate one authoritative scene path beneath its project directory."""
    try:
        candidate = Path(str(raw_path))
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        safe = candidate.resolve(strict=False)
        safe.relative_to(directory.resolve())
        if not candidate.exists():
            return None, "SCENE_MEDIA_MISSING"
        if not candidate.is_file() or safe.stat().st_size <= 0:
            return None, "SCENE_MEDIA_UNAVAILABLE"
        if safe.suffix.lower() not in MEDIA_SUFFIXES:
            return None, "SCENE_MEDIA_TYPE_UNSUPPORTED"
        return safe, None
    except (OSError, ValueError):
        return None, "SCENE_MEDIA_UNAVAILABLE"


def _start_connect(platform: str):
    """Run the existing Step-1 interactive connect flow in the background."""
    from app.publishing import connections as conn

    if platform not in ("youtube", "tiktok"):
        return 404, {"detail": "unknown platform"}
    if not _connect_running["platform"]:
        def runner():
            try:
                if platform == "youtube":
                    conn.run_connect_youtube()
                else:
                    conn.run_connect_tiktok()
            finally:
                _connect_running["platform"] = None

        _connect_running["platform"] = platform
        threading.Thread(target=runner, daemon=True).start()
    return 202, {
        "detail": "authorization flow started; a browser window will open on this machine",
        "platform": platform,
    }


# ---------------------------------------------------------------------------
# Manual publishing bridges — THIN adapters over the EXISTING state machines
#
# Every real operation delegates to app/publishing.tiktok_reconciliation
# (prepare_tiktok_publish / submit_tiktok_publish / verify_tiktok_publish /
# reconcile_tiktok_publication). The web layer adds ONLY:
#   - request validation (project exists, TikTok connected)
#   - a single-flight lock so double-clicks/tabs cannot stack submissions
#   - truthful JSON mapping of durable states and errors
# It never implements upload, duplicate-prevention, or reconciliation logic.
# ---------------------------------------------------------------------------

_publish_lock = threading.Lock()


def _new_publisher():
    """Factory indirection so tests can inject a fake publisher."""
    from app.publishing.tiktok import TikTokPublisher

    return TikTokPublisher.from_env(allow_interactive=False)


def _new_youtube_publisher():
    """HTTP-only publisher; token expiry can never launch browser consent."""
    from app.publishing.youtube import YouTubePublisher

    return YouTubePublisher.from_env(allow_interactive=False)


def _publishing_project_directory(project_id: str) -> Path:
    from app.publishing.models import PublishingError

    directory = _project_directory_for(project_id)
    if directory is None:
        raise PublishingError("Project manifest not found")
    return directory


def _prepare(project_id: str, privacy_level=None):
    from app.publishing.tiktok_reconciliation import prepare_tiktok_publish

    return prepare_tiktok_publish(
        project_id, project_dir=_publishing_project_directory(project_id),
        privacy_level=privacy_level,
    )


def _submit(project_id: str, publisher):
    from app.publishing.tiktok_reconciliation import submit_tiktok_publish

    return submit_tiktok_publish(
        project_id, publisher, project_dir=_publishing_project_directory(project_id),
    )


def _verify(project_id: str, publisher):
    from app.publishing.tiktok_reconciliation import verify_tiktok_publish

    return verify_tiktok_publish(
        project_id, publisher, project_dir=_publishing_project_directory(project_id),
        poll_attempts=5, poll_interval_seconds=3.0,
    )


def _reconcile(project_id: str, publisher=None):
    from app.publishing.tiktok_reconciliation import reconcile_tiktok_publication

    return reconcile_tiktok_publication(
        project_id, publisher=publisher, project_dir=_publishing_project_directory(project_id),
    )


def _youtube_prepare(project_id: str, privacy_status=None):
    from app.publishing.reconciliation import prepare_publish

    return prepare_publish(
        project_id, project_dir=_publishing_project_directory(project_id),
        privacy_status=privacy_status,
    )


def _youtube_submit(project_id: str, publisher):
    from app.publishing.reconciliation import submit_upload

    return submit_upload(
        project_id, publisher, project_dir=_publishing_project_directory(project_id),
    )


def _youtube_verify(project_id: str, publisher):
    from app.publishing.reconciliation import verify_upload

    return verify_upload(
        project_id, publisher, project_dir=_publishing_project_directory(project_id),
        poll_attempts=5, poll_interval_seconds=3.0,
    )


def _youtube_reconcile(project_id: str, publisher=None):
    from app.publishing.reconciliation import reconcile_publication

    return reconcile_publication(
        project_id, publisher=publisher, project_dir=_publishing_project_directory(project_id),
    )


def _current_publication_record(project_id: str, platform: str):
    """Read only from the resolved project directory, including legacy aliases."""
    from app.orchestrator.models import ProjectManifest

    directory = _project_directory_for(project_id)
    if directory is None:
        return None
    data = _load_manifest(directory)
    if not data:
        return None
    try:
        manifest = ProjectManifest.from_dict(data)
    except Exception as exc:
        logger.warning("Could not parse publication manifest: %s", _sanitize_diagnostic_text(exc))
        return None
    return manifest.publication if platform == "youtube" else manifest.tiktok_publication


def _current_record(project_id: str):
    return _current_publication_record(project_id, "tiktok")


def _current_youtube_record(project_id: str):
    return _current_publication_record(project_id, "youtube")


def _tiktok_connected(publisher) -> bool:
    """Non-interactive connectivity check; NEVER opens a browser."""
    try:
        publisher.allow_interactive = False
        return publisher.validate_stored_token() is not None
    except Exception:
        return False


def _youtube_connected(publisher) -> bool:
    try:
        publisher.allow_interactive = False
        return publisher.validate_stored_credentials() is not None
    except Exception:
        return False


def _enum_value(value):
    return getattr(value, "value", value)


def _publication_verdict(record: dict, platform: str) -> str:
    """Presentation mapping of existing durable evidence, never a state transition."""
    state = _enum_value(record.get("state"))
    identity = record.get("youtube_video_id" if platform == "youtube" else "tiktok_publish_id")
    if state == "FAILED":
        return "PUBLICATION_FAILED"
    if state == "UNKNOWN_RECONCILIATION_REQUIRED":
        return "PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED"
    if platform == "youtube" and identity and record.get("pending_metadata"):
        return "PUBLICATION_METADATA_UPDATE_PENDING"
    if state == "VERIFIED":
        if identity and (platform == "youtube" or record.get("tiktok_post_id")):
            return "PUBLICATION_VERIFIED"
        return "PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED"
    if state == "VERIFIED_SANDBOX_PRIVATE":
        meta = record.get("tiktok_metadata") or {}
        meta = meta if isinstance(meta, dict) else {}
        if platform == "tiktok" and identity and meta.get("privacy_level") == "SELF_ONLY":
            return "PUBLICATION_VERIFIED"
        return "PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED"
    if state in (None, "NOT_STARTED", "PREPARED") and not identity:
        return "PUBLICATION_NOT_STARTED"
    if identity:
        return "PUBLICATION_PROCESSING"
    return "PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED"


def _publication_payload(record, platform: str) -> dict:
    """Allowlisted record fields; no paths, tokens, raw responses or upload URLs."""
    if isinstance(record, dict):
        data = record
    elif record is not None and hasattr(record, "model_dump"):
        data = record.model_dump(mode="json")
    else:
        fields = (
            "state", "youtube_video_id", "tiktok_publish_id", "tiktok_post_id", "error",
            "prepared_at", "upload_submitted_at", "upload_completed_at", "submission_started_at",
            "submitted_at", "verified_at", "updated_at", "privacy_status", "verification_evidence",
        )
        data = {field: getattr(record, field, None) for field in fields}
        for field in ("youtube_metadata", "tiktok_metadata"):
            meta = getattr(record, field, None)
            data[field] = {
                "privacy_status": getattr(meta, "privacy_status", None),
                "privacy_level": getattr(meta, "privacy_level", None),
            }
    yt_meta = data.get("youtube_metadata") or {}
    tk_meta = data.get("tiktok_metadata") or {}
    yt_meta = yt_meta if isinstance(yt_meta, dict) else {}
    tk_meta = tk_meta if isinstance(tk_meta, dict) else {}
    payload = {
        "platform": platform, "state": _enum_value(data.get("state")),
        "verdict": _publication_verdict(data, platform),
        "video_id": data.get("youtube_video_id") if platform == "youtube" else None,
        "publish_id": data.get("tiktok_publish_id") if platform == "tiktok" else None,
        # YouTube's historical post_id alias is intentionally retained.
        "post_id": data.get("youtube_video_id") if platform == "youtube" else data.get("tiktok_post_id"),
        "privacy_status": (yt_meta.get("privacy_status") or data.get("privacy_status")) if platform == "youtube" else None,
        "privacy_level": tk_meta.get("privacy_level") if platform == "tiktok" else None,
        "error": data.get("error"), "sample": False,
    }
    if platform == "tiktok":
        payload["publish_id_present"] = bool(payload["publish_id"])
    for field in (
        "prepared_at", "upload_submitted_at", "upload_completed_at", "submission_started_at",
        "submitted_at", "verified_at", "updated_at", "metadata_update_submitted_at", "metadata_verified_at",
    ):
        payload[field] = data.get(field)
    evidence = data.get("verification_evidence") or {}
    if isinstance(evidence, dict):
        payload["verification_evidence"] = {
            key: evidence[key] for key in (
                "checked_at", "uploadStatus", "status", "privacyStatus", "privacy_level",
                "classification", "reason", "metadata_mismatches",
            ) if key in evidence
        }
    return _sanitize_publishing_payload(payload)


def _record_payload(record) -> dict:
    return _publication_payload(record, "tiktok")


def _publishing_project_complete(project_id: str) -> bool:
    """Use the canonical acceptance oracle, not presentation completion claims."""
    from app.orchestrator.reconciliation import ProjectVerdict, reconcile_project

    try:
        report = reconcile_project(
            project_id, _publishing_project_directory(project_id), persist=False,
        )
        return report.verdict == ProjectVerdict.PROJECT_COMPLETE
    except Exception as exc:
        logger.warning("Publishing eligibility unavailable: %s", _sanitize_diagnostic_text(exc))
        return False


def _publication_status_payload(project_id: str, platform: str):
    """Stored evidence plus optional existing-ID reconciliation; never submits.

    Reconciliation may persist fresh verification evidence. It is serialized
    with publishing, and ALL record fields are reloaded afterwards.
    """
    detail = project_detail(project_id)
    if detail is None:
        return 404, {"detail": "project not found"}
    if platform == "youtube":
        current, factory, connectivity, reconcile = (
            _current_youtube_record, _new_youtube_publisher, _youtube_connected, _youtube_reconcile,
        )
        identity_field = "youtube_video_id"
    else:
        current, factory, connectivity, reconcile = (
            _current_record, _new_publisher, _tiktok_connected, _reconcile,
        )
        identity_field = "tiktok_publish_id"
    record = current(project_id)
    note = None
    report = None
    try:
        publisher = factory()
        connected = connectivity(publisher)
    except Exception as exc:
        connected = False
        note = f"Connection check unavailable: {_sanitize_diagnostic_text(exc)}"
    state = _enum_value(getattr(record, "state", None))
    if connected and getattr(record, identity_field, None) and state not in (
        "VERIFIED", "VERIFIED_SANDBOX_PRIVATE", "FAILED", "UNKNOWN_RECONCILIATION_REQUIRED",
    ):
        if _publish_lock.acquire(blocking=False):
            try:
                report = reconcile(project_id, publisher=publisher)
                note = "; ".join(report.reasons) or None
            except Exception as exc:
                note = f"Live reconciliation unavailable: {_sanitize_diagnostic_text(exc)}"
                logger.warning("Publication status reconciliation unavailable: %s", note)
            finally:
                try:
                    record = current(project_id)
                finally:
                    _publish_lock.release()
        else:
            note = "Publishing is active; displaying stored evidence without reconciliation."
    payload = _publication_payload(record, platform)
    if report is not None and _enum_value(report.state) == payload["state"]:
        payload["verdict"] = _enum_value(report.verdict)
    payload.update({
        "project_id": detail.get("directory_id") or project_id,
        "connected": connected,
        "can_publish": bool(
            connected and payload["state"] in (None, "NOT_STARTED", "PREPARED")
            and not (payload["video_id"] or payload["publish_id"] or payload["post_id"])
            and not _publish_lock.locked()
            and _publishing_project_complete(project_id)
        ),
        "note": note,
    })
    return 200, _sanitize_publishing_payload(payload)


def tiktok_status_payload(project_id: str):
    return _publication_status_payload(project_id, "tiktok")


def youtube_status_payload(project_id: str):
    return _publication_status_payload(project_id, "youtube")


def _publication_outcome(payload: dict, http_status: int = 200) -> str:
    """Transport success never overrides a durable state or evidence verdict."""
    verdict = _enum_value(payload.get("verdict"))
    state = _enum_value(payload.get("state"))
    if verdict == "PUBLICATION_FAILED" or state == "FAILED":
        return "failed"
    if verdict == "PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED" or state == "UNKNOWN_RECONCILIATION_REQUIRED":
        return "unknown_reconciliation_required"
    if http_status >= 400 and payload.get("error"):
        return "failed"
    if verdict in ("PUBLICATION_METADATA_UPDATE_PENDING", "PUBLICATION_PROCESSING"):
        return "processing"
    if state == "VERIFIED_SANDBOX_PRIVATE":
        return "verified_sandbox_private"
    if state == "VERIFIED":
        return "verified"
    if state in ("UPLOAD_SUBMITTED", "IDENTITY_PERSISTED", "PROCESSING"):
        return "processing"
    if state is None and verdict == "PUBLICATION_VERIFIED":
        return "verified"
    return "failed" if payload.get("error") else "not_started"


def _publishing_exception_payload(project_id: str, platform: str, exc: Exception):
    mapping = {
        "PublishingEligibilityError": (422, "NOT_ELIGIBLE"),
        "MetadataValidationError": (422, "METADATA_INVALID"),
        "DuplicateUploadBlockedError": (409, "DUPLICATE_BLOCKED"),
        "UploadIdentityMissingError": (409, "UNKNOWN_RECONCILIATION_REQUIRED"),
        "TikTokAuthError": (502, "AUTH_FAILED"),
        "PermissionError": (502, "AUTH_FAILED"),
    }
    status, error = mapping.get(type(exc).__name__, (502, "SUBMISSION_FAILED"))
    try:
        stored = _current_publication_record(project_id, platform)
    except Exception:
        stored = None
    payload = _publication_payload(stored, platform)
    payload.update({
        "publication_error": payload["error"], "error": error,
        "detail": _sanitize_diagnostic_text(exc),
        "note": "Inspect durable publication evidence before any further action; never retry blindly.",
    })
    return status, _sanitize_publishing_payload(payload)


def tiktok_publish_now(project_id: str, privacy_level=None):
    """
    Explicitly requested publish. Runs the EXISTING sequence synchronously
    (acceptable for the local single-user demo): offline reconcile pre-check
    → prepare → creator capability check → submit → verify. All durability
    and duplicate protection belong to the existing modules; this wrapper
    refuses rather than retries.
    """
    if not _publish_lock.acquire(blocking=False):
        return 409, {
            "error": "PUBLISH_ALREADY_RUNNING",
            "detail": "another publishing operation is already in progress; "
                      "the durable state machine prevents duplicates either way",
        }
    try:
        detail = project_detail(project_id)
        if detail is None:
            return 404, {"detail": "project not found"}

        publisher = _new_publisher()
        if not _tiktok_connected(publisher):
            stored = _current_record(project_id)
            payload = _record_payload(stored)
            payload.update({
                "error": "TIKTOK_NOT_CONNECTED",
                "detail": "TikTok is not connected on this machine; connect via "
                          "Platforms first. Nothing was uploaded.",
            })
            return 409, _sanitize_publishing_payload(payload)

        # Offline pre-check through authoritative reconciliation (no network).
        report = _reconcile(project_id, publisher=None)
        verdict = report.verdict.value
        stored = _current_record(project_id)
        if verdict == "PUBLICATION_VERIFIED":
            payload = _record_payload(_current_record(project_id))
            payload.update({
                "published_already": True,
                "verdict": verdict,
                "detail": "Already published and verified; no second submission.",
            })
            return 200, payload
        if verdict == "PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED":
            return 409, _sanitize_publishing_payload({
                "error": "UNKNOWN_RECONCILIATION_REQUIRED",
                "detail": "A previous submission may have reached TikTok without "
                          "a durable identity. Blind retry is forbidden; manual "
                          "reconciliation required.",
                "reasons": report.reasons,
                "state": _enum_value(getattr(stored, "state", None)), "verdict": verdict,
            })
        if verdict == "PUBLICATION_FAILED":
            return 409, _sanitize_publishing_payload({
                "error": "PREVIOUS_FAILURE",
                "detail": "A previous publication failed; inspect the manifest "
                          "before any new attempt.",
                "reasons": report.reasons,
                "state": _enum_value(getattr(stored, "state", None)), "verdict": verdict,
            })
        if stored and (stored.tiktok_publish_id or stored.tiktok_post_id):
            payload = _record_payload(stored)
            payload.update({
                "error": "EXISTING_PUBLICATION_ID",
                "detail": "A durable TikTok identity already exists; inspect status, do not submit again.",
            })
            return 409, payload

        # Existing eligibility gate + artifact fingerprint binding.
        record = _prepare(project_id, privacy_level=privacy_level)

        # Creator capability check — mirrors the existing operator flow.
        try:
            creator = publisher.get_creator_info() or {}
            options = creator.get("privacy_level_options") or []
            wanted = record.tiktok_metadata.privacy_level
            if options and wanted not in options:
                return 409, _sanitize_publishing_payload({
                    "error": "PRIVACY_LEVEL_NOT_PERMITTED",
                    "detail": f"Privacy level {wanted} is not permitted for this "
                              f"account {options}. Nothing was submitted.",
                    "allowed_privacy_levels": options,
                })
        except Exception as exc:
            if type(exc).__name__ == "TikTokAuthError":
                raise
            logger.info("Creator capability check unavailable: %s", _sanitize_diagnostic_text(exc))

        # Existing submit (duplicate protection inside) then existing verify.
        record = _submit(project_id, publisher)
        record = _verify(project_id, publisher)

        payload = _record_payload(record)
        payload["detail"] = (
            "Published and verified from official platform APIs"
            if record.state.value == "VERIFIED"
            else f"Submission handled by the durable state machine; current state: {record.state.value}"
        )
        return 200, payload

    except Exception as exc:
        logger.warning("TikTok publish failed: %s", _sanitize_diagnostic_text(exc))
        return _publishing_exception_payload(project_id, "tiktok", exc)
    finally:
        _publish_lock.release()


def youtube_publish_now(project_id: str, privacy_status=None):
    """Explicit manual upload through the existing YouTube publication engine."""
    if not _publish_lock.acquire(blocking=False):
        return 409, {"error": "PUBLISH_ALREADY_RUNNING", "detail": "Another publishing operation is active."}
    try:
        if project_detail(project_id) is None:
            return 404, {"detail": "project not found"}
        publisher = _new_youtube_publisher()
        if not _youtube_connected(publisher):
            payload = _publication_payload(_current_youtube_record(project_id), "youtube")
            payload.update({
                "error": "YOUTUBE_NOT_CONNECTED",
                "detail": "Connect YouTube through Platforms first. Nothing was uploaded.",
            })
            return 409, payload
        report = _youtube_reconcile(project_id, publisher=None)
        stored = _current_youtube_record(project_id)
        payload = _publication_payload(stored, "youtube")
        payload["verdict"] = _enum_value(report.verdict)
        if payload["verdict"] == "PUBLICATION_VERIFIED":
            payload.update({"published_already": True, "detail": "Already verified; no second upload."})
            return 200, payload
        if payload["verdict"] in ("PUBLICATION_FAILED", "PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED"):
            payload.update({
                "error": "PREVIOUS_FAILURE" if payload["verdict"] == "PUBLICATION_FAILED" else "UNKNOWN_RECONCILIATION_REQUIRED",
                "detail": "Inspect and explicitly reconcile the previous publication; blind retry is forbidden.",
                "reasons": report.reasons,
            })
            return 409, _sanitize_publishing_payload(payload)
        if payload["video_id"]:
            payload.update({
                "error": "EXISTING_PUBLICATION_ID",
                "detail": "An existing YouTube video identity is durable; inspect status instead of uploading.",
            })
            return 409, payload
        _youtube_prepare(project_id, privacy_status=privacy_status)
        _youtube_submit(project_id, publisher)
        record = _youtube_verify(project_id, publisher)
        final_report = _youtube_reconcile(project_id, publisher=publisher)
        record = _current_youtube_record(project_id) or record
        payload = _publication_payload(record, "youtube")
        payload["verdict"] = _enum_value(final_report.verdict)
        payload["detail"] = (
            "Published and verified from official platform APIs"
            if _publication_outcome(payload) == "verified"
            else "Upload handled by the existing engine; inspect the durable state and verdict."
        )
        return 200, _sanitize_publishing_payload(payload)
    except Exception as exc:
        logger.warning("YouTube publish failed: %s", _sanitize_diagnostic_text(exc))
        return _publishing_exception_payload(project_id, "youtube", exc)
    finally:
        _publish_lock.release()


# ---------------------------------------------------------------------------
# Phase 11 — PERSONAL COMMAND CENTER (thin production inspection/control)
#
# Every read derives ON DEMAND from the three disk authorities:
#   1. INTENT    : productions/<id>/production.json        (Phase 9)
#   2. EXECUTION : output/projects/<id>/manifest.json      (Phase 10)
#   3. BINDING   : ProjectManifest.production_binding      (Phase 10)
#
# Every state classification comes from the Phase 10 ProductionExecutor
# (derive_progress()); this layer NEVER re-implements the six-state
# vocabulary and NEVER caches derived state. Execution commands flow
# EXCLUSIVELY through ProductionExecutor.execute() in a background
# thread (one operator command per production at a time); polling
# endpoints are read-only and never spawn, continue, or retry execution.
# No database, no scheduler, no daemon, no queue.
# ---------------------------------------------------------------------------

# Single-flight registry: at most one in-flight operator execution command
# per production. Ephemeral presentation state only — never durable.
_production_runs: dict = {}
_production_runs_lock = threading.Lock()

_SECRET_KEY_PATTERN = re.compile(
    r"(secret|token|api[_-]?key|password|credential|authori[sz]ation|bearer|"
    r"refresh[_-]?token|access[_-]?key|private[_-]?key|cookie|session|"
    r"webhook|client[_-]?secret|signature)",
    re.IGNORECASE,
)


def _redact_secrets(value):
    """Recursively redact secret-looking keys from any disk-derived payload."""
    if isinstance(value, dict):
        return {
            key: (
                "<redacted>"
                if _SECRET_KEY_PATTERN.search(str(key))
                else _redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


_REMOTE_URL_IN_TEXT = re.compile(r"(?:https?|wss?|ftp|file)://[^\s\"'<>]+", re.IGNORECASE)
_WINDOWS_PATH_IN_TEXT = re.compile(r"[a-zA-Z]:[\\/][^\s;,\"'<>]+")
_UNC_PATH_IN_TEXT = re.compile(r"\\\\[^\s;,\"'<>]+")
_UNIX_PATH_IN_TEXT = re.compile(
    r"(?<![\w:])/(?:Users|home|var|tmp|private|etc|opt|srv|mnt|media|root)/[^\s;,\"'<>]+"
)
_SECRET_VALUE_IN_TEXT = re.compile(r"(?:Bearer\s+|sk-)[A-Za-z0-9._~-]+", re.IGNORECASE)


def _browser_safe_diagnostic(value) -> str | None:
    """Preserve useful error context without returning paths, URLs, or tokens."""
    if value is None:
        return None
    text = str(value)
    text = _REMOTE_URL_IN_TEXT.sub("<remote-url-omitted>", text)
    text = _WINDOWS_PATH_IN_TEXT.sub("<absolute-path-omitted>", text)
    text = _UNC_PATH_IN_TEXT.sub("<absolute-path-omitted>", text)
    text = _UNIX_PATH_IN_TEXT.sub("<absolute-path-omitted>", text)
    return _SECRET_VALUE_IN_TEXT.sub("<secret-omitted>", text)


_PUBLISHING_SECRET_ASSIGNMENT = re.compile(
    r"\b(?:access[_-]?token|refresh[_-]?token|id[_-]?token|token|api[_-]?key|"
    r"client[_-]?(?:secret|key)|password|secret|authorization|cookie)\b[\"']?"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;\}\]]+)",
    re.IGNORECASE,
)


def _sanitize_diagnostic_text(value) -> str | None:
    """Use the existing browser sanitizer and redact labelled/configured secrets."""
    if value is None:
        return None
    text = str(value)
    # Values are used only for redaction; no environment/credential content is
    # returned or logged. Short configuration flags are not credential values.
    for key, secret in os.environ.items():
        if len(secret) >= 8 and (_SECRET_KEY_PATTERN.search(key) or key.endswith("CLIENT_KEY")):
            text = text.replace(secret, "<secret-omitted>")
    # Redact Bearer values before removing an Authorization label, otherwise
    # an unquoted "Authorization: Bearer ..." can leave the token unlabelled.
    text = _browser_safe_diagnostic(text)
    return _PUBLISHING_SECRET_ASSIGNMENT.sub("<secret-omitted>", text)


def _sanitize_publishing_payload(value):
    """Sanitize strings as well as secret-looking keys on publishing surfaces."""
    if isinstance(value, dict):
        return {
            key: "<redacted>" if _SECRET_KEY_PATTERN.search(str(key))
            else _sanitize_publishing_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_publishing_payload(item) for item in value]
    if isinstance(value, str):
        return _sanitize_diagnostic_text(value)
    return value


# Phase 11C Batch 1 hardening (Finding 4): browsers routinely abort in-flight
# polling requests (navigation away, tab close, poll timeout). WinError 10053
# ("established connection was aborted by the software in your host machine")
# and its siblings are NORMAL client disconnects while writing a response —
# not production execution failures and not server defects. They are logged
# cleanly at INFO and never answered with a 500 (the socket is already gone).
_CLIENT_DISCONNECT_EXCEPTIONS = (
    ConnectionAbortedError,
    ConnectionResetError,
    BrokenPipeError,
)
_CLIENT_DISCONNECT_WINERRORS = {10053, 10054}


def _is_client_disconnect(exc: Exception) -> bool:
    """True when an exception is a client-side connection abort, not a defect."""
    if isinstance(exc, _CLIENT_DISCONNECT_EXCEPTIONS):
        return True
    return (
        isinstance(exc, OSError)
        and getattr(exc, "winerror", None) in _CLIENT_DISCONNECT_WINERRORS
    )


def _relative_display(path: Path) -> str:
    """Forward-slash display path relative to the project root (never absolute)."""
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        return path.name


def _intent_malformed(production_id: str, exc: Exception) -> dict:
    """F-03 (audit): structured, actionable error for unreadable intent."""
    return {
        "error": "PRODUCTION_INTENT_MALFORMED",
        "production_id": production_id,
        "message": (
            f"Production intent for {production_id} could not be loaded: "
            f"{type(exc).__name__}: {_browser_safe_diagnostic(exc)}"
        ),
        "guidance": (
            "The on-disk production.json is corrupt or no longer matches "
            "the Phase 9 schema. The Command Center never silently repairs "
            "or mutates intent. Fix the file on disk or issue a revised "
            "intent via produce.py, then retry. Genuinely unknown "
            "production IDs still return 404."
        ),
    }


def _production_executor(production_id: str):
    """Build the Phase 10 ProductionExecutor — the ONLY execution engine."""
    from app.orchestrator.production_execution import ProductionExecutor
    from app.orchestrator.models import OrchestratorConfig
    from app.content.planner import ScenePlanner, create_scene_planner
    from app.orchestrator.visual_sources import DEFAULT_IMAGE_SOURCE
    from app.voice import config as voice_config

    # Phase 11C Batch 1: web-created local productions execute through the
    # REAL production pipeline with the REAL intelligence layer. Content
    # planning runs on the configured content provider (DEFAULT_LLM_PROVIDER;
    # OpenRouter is the project's AI API router and "mock" is an explicit
    # offline mode), narration uses the configured TTS provider (Edge TTS by
    # default; "mock" only when explicitly configured), and scene imagery is
    # acquired through the configured visual source. Phase 11C Batch 1
    # hardening: the default is the ROUTING layer ("auto") — AI image
    # generation (AVM_IMAGE_MODEL), quality stock (PEXELS_API_KEY), and the
    # free Wikimedia Commons fallback, selected per scene by the plan's
    # visual-source hints; Wikimedia is no longer the primary strategy.
    # Mock mode remains available ONLY as an explicit offline/test mode
    # (AVM_MOCK_MODE=1 or DEFAULT_LLM_PROVIDER=mock) and its fixture output
    # never masquerades as AI production.
    import os as _os

    from app.content.config import DEFAULT_LLM_PROVIDER
    from app.production.storage import load_production

    production = load_production(production_id, root=PRODUCTIONS_DIR)
    local_units = [
        unit for unit in production.units
        if unit.provider_intent in {"image_motion", "local_asset"}
    ]
    base_config = None
    planner_factory = None
    if local_units and len(local_units) == len(production.units):
        explicit_mock = (
            _os.getenv("AVM_MOCK_MODE", "").strip().lower()
            in {"1", "true", "yes", "on"}
            or str(DEFAULT_LLM_PROVIDER).strip().lower() == "mock"
        )
        if explicit_mock:
            base_config = OrchestratorConfig(
                topic=production.title,
                mock_mode=True,
                visual_mode="local",
                visual_default_provider="image_motion",
                image_source="fixture",
                voice_provider="mock",
            )
            planner_factory = lambda: ScenePlanner(mock_mode=True)
        else:
            base_config = OrchestratorConfig(
                topic=production.title,
                mock_mode=False,
                visual_mode="local",
                visual_default_provider="image_motion",
                image_source=(
                    _os.getenv("AVM_IMAGE_SOURCE") or DEFAULT_IMAGE_SOURCE
                ).strip(),
                voice_provider=voice_config.TTS_PROVIDER,
            )
            # Existing factory: resolves the configured provider at plan time
            # and fails truthfully when it is unavailable (no silent mock
            # fallback in real production).
            planner_factory = create_scene_planner

    kwargs = {
        "productions_root": PRODUCTIONS_DIR,
        "projects_root": PROJECTS_DIR,
    }
    if base_config is not None:
        kwargs["base_config"] = base_config
        kwargs["planner_factory"] = planner_factory
    return ProductionExecutor(production_id, **kwargs)


def create_production_payload(body: dict):
    """Create one durable, local-first Single Video production intent.

    This is deliberately an intent-only boundary.  It allocates and persists
    ``Production``/``ProductionUnit`` through the Phase 9 storage mechanism;
    project creation and execution remain owned by Phase 10's executor and
    are never triggered as a side effect of creation.
    """
    from app.production.identity import generate_production_id
    from app.production.models import Production, ProductionCommand, ProductionUnit
    from app.production.storage import save_new_production

    if not isinstance(body, dict):
        return 400, {
            "error": "INVALID_REQUEST",
            "message": "Creation request must be a JSON object.",
        }

    def text(name: str, *, required: bool = False, maximum: int = 500) -> str:
        value = body.get(name, "")
        if not isinstance(value, str):
            raise ValueError(f"{name} must be text")
        value = value.strip()
        if required and not value:
            raise ValueError(f"{name} is required")
        if len(value) > maximum:
            raise ValueError(f"{name} must be {maximum} characters or fewer")
        if "\x00" in value:
            raise ValueError(f"{name} contains an invalid character")
        return value

    try:
        mode = text("mode", required=True, maximum=32).lower()
        creation_method = text("creation_method", required=True, maximum=32).lower()
        if mode not in {"single_video", "unit_count"}:
            raise ValueError("mode must be 'single_video' or 'unit_count'")
        if creation_method != "local":
            raise ValueError("Batch 1 supports only creation_method='local'")

        # Track 7 Mode B: Multiple Independent Videos — additive validation.
        # Only applies when mode == unit_count; single_video path unchanged.
        unit_count = None
        unit_titles = None
        is_episodic = False
        episodic_bible = None
        episodic_episodes = None
        if mode == "unit_count":
            raw_count = body.get("unit_count")
            if raw_count is None:
                raise ValueError("unit_count is required when mode='unit_count'")
            if isinstance(raw_count, bool) or not isinstance(raw_count, int):
                raise ValueError("unit_count must be an integer from 2 to 20")
            if not 2 <= raw_count <= 20:
                raise ValueError("unit_count must be an integer from 2 to 20")
            unit_count = raw_count
            raw_titles = body.get("unit_titles")
            if raw_titles is not None:
                if not isinstance(raw_titles, list):
                    raise ValueError("unit_titles must be a list of titles")
                if len(raw_titles) != unit_count:
                    raise ValueError("unit_titles length must equal unit_count")
                cleaned: list[str] = []
                for t in raw_titles:
                    if not isinstance(t, str):
                        raise ValueError("each unit_titles entry must be text")
                    tt = t.strip()
                    if not tt:
                        raise ValueError("each unit_titles entry must be non-empty text")
                    if len(tt) > 200:
                        raise ValueError("each unit_titles entry must be 200 characters or fewer")
                    if "\x00" in tt:
                        raise ValueError("unit_titles entry contains an invalid character")
                    cleaned.append(tt)
                unit_titles = cleaned
            _raw_ut = body.get("unit_type")
            if _raw_ut is not None:
                if not isinstance(_raw_ut, str):
                    raise ValueError("unit_type must be text")
                _ut = _raw_ut.strip().lower()
                if _ut not in {"episode", "standalone_video"}:
                    raise ValueError("unit_type must be 'episode' or 'standalone_video'")
                if _ut == "episode":
                    is_episodic = True
                body["_normalized_unit_type"] = _ut
            _raw_bible = body.get("bible")
            _raw_eps = body.get("episodes")
            if _raw_bible is not None or _raw_eps is not None:
                is_episodic = True
            if is_episodic and raw_titles is not None and _raw_eps is not None:
                raise ValueError("unit_titles cannot be combined with episodes")

        title = text("title", maximum=200)
        brief = text("brief", maximum=500)
        title = title or brief
        if len(title) < 3:
            raise ValueError("title or brief must contain at least 3 characters")
        if not brief:
            brief = title

        output_intent = text("output_intent", required=True, maximum=32).lower()
        if output_intent not in {"youtube_shorts", "tiktok"}:
            raise ValueError("output_intent must be youtube_shorts or tiktok")

        # Batch 1C follow-up: production-level generation strategy (user
        # selected; default preserves the existing local pipeline).
        from app.production.models import (
            DEFAULT_GENERATION_STRATEGY,
            GENERATION_STRATEGIES,
        )

        generation_strategy = text(
            "generation_strategy", maximum=64
        ).lower()
        if not generation_strategy:
            generation_strategy = DEFAULT_GENERATION_STRATEGY
        if generation_strategy not in GENERATION_STRATEGIES:
            raise ValueError(
                "generation_strategy must be one of: "
                + ", ".join(GENERATION_STRATEGIES)
            )

        # Batch 1D: first-class Production Route.  An explicitly provided
        # route wins; otherwise the route is derived from the (already
        # validated) legacy generation strategy, so new productions persist
        # the first-class product intent while generation_strategy remains
        # the compatibility field.  Unknown routes fail loudly (never
        # rerouted, never defaulted silently).
        from app.production.routes import (
            PRODUCTION_ROUTES,
            creation_cost_class,
            route_id_for_legacy_strategy,
        )

        route = text("route", maximum=64).lower()
        if route and route not in PRODUCTION_ROUTES:
            raise ValueError(
                "route must be one of: " + ", ".join(PRODUCTION_ROUTES)
            )
        if not route:
            route = route_id_for_legacy_strategy(generation_strategy)

        # AVM Step 7 (additive): optional CANONICAL MODEL creation intent.
        #
        # This is the model-first user choice (``qwen-3.8-flash``), validated
        # against the Step-1 canonical model registry so an unknown or
        # misspelled id fails loudly here instead of being persisted and later
        # silently reinterpreted. A client that omits the field gets ``None``
        # = NOT DECLARED, which is exactly today's behavior.
        #
        # RUNTIME BOUNDARY (critical): this records INTENT only. The Step-3
        # model-bound resolver stays DORMANT — nothing here calls
        # ``resolve_model()`` and no planner/execution path reads this value, so
        # planning keeps using the configured provider exactly as before. A
        # provider or provider wire model id is NEVER accepted from a client:
        # provider identity is internal execution evidence, recorded by Step 6
        # in ``plan_meta.json``, never a creation input.
        from app.content.model_registry import has_model, list_models

        canonical_model_id = text("canonical_model_id", maximum=64).lower()
        if canonical_model_id and not has_model(canonical_model_id):
            known = ", ".join(m.id for m in list_models()) or "(none registered)"
            raise ValueError(
                f"canonical_model_id '{canonical_model_id}' is not a registered "
                f"canonical model. Registered: {known}"
            )

        # V1 AUDIO POLICY FIX: optional, backward-compatible production AUDIO
        # INTENT.  A client that does not send the field (the current frontend
        # does not) gets ``None`` = NOT DECLARED, which the execution seam
        # resolves to the documented backward-compatible default
        # (``DEFAULT_AUDIO_INTENT`` = narration) — exactly the existing V1
        # creation behavior.  An unknown value is rejected loudly here so it can
        # never be persisted and later silently reinterpreted.  This is intent,
        # not route: ROUTE ≠ AUDIO, and it is never derived from plan narration.
        from app.production.models import (
            AUDIO_INTENTS,
            DEFAULT_AUDIO_INTENT,
            normalize_audio_intent,
        )

        audio_intent = text("audio_intent", maximum=64).lower()
        if audio_intent and audio_intent not in AUDIO_INTENTS:
            raise ValueError(
                "audio_intent must be one of: " + ", ".join(AUDIO_INTENTS)
            )
        if audio_intent:
            audio_intent = normalize_audio_intent(audio_intent)

        duration = body.get("target_duration", 30)
        scenes = body.get("scene_count", 6)
        if isinstance(duration, bool) or not isinstance(duration, int) or not 10 <= duration <= 180:
            raise ValueError("target_duration must be an integer from 10 to 180 seconds")
        if isinstance(scenes, bool) or not isinstance(scenes, int) or not 2 <= scenes <= 20:
            raise ValueError("scene_count must be an integer from 2 to 20")

        if is_episodic:
            _rb = body.get("bible")
            if _rb is not None:
                if not isinstance(_rb, dict):
                    raise ValueError("bible must be an object")
                _re = _rb.get("elements")
                if _re is None:
                    raise ValueError("bible.elements is required when bible is supplied")
                if not isinstance(_re, list):
                    raise ValueError("bible.elements must be a list")
                from app.production.models import BibleElement as _BEX, ProductionBible as _PBX
                _els = []
                for _e in _re:
                    if not isinstance(_e, dict):
                        raise ValueError("each bible element must be an object")
                    _eid = _e.get("element_id"); _sec = _e.get("section")
                    if not isinstance(_eid, str) or not _eid.strip():
                        raise ValueError("each bible element requires element_id text")
                    if not isinstance(_sec, str) or not _sec.strip():
                        raise ValueError("each bible element requires section text")
                    if "\x00" in _eid or "\x00" in _sec:
                        raise ValueError("bible element contains an invalid character")
                    _rc = _e.get("content"); _ct = {}
                    if isinstance(_rc, dict):
                        _ct = dict(_rc)
                    elif isinstance(_rc, str):
                        if _rc.strip():
                            _ct["text"] = _rc.strip()
                        if "\x00" in _rc:
                            raise ValueError("bible element content contains an invalid character")
                    elif _rc is not None:
                        raise ValueError("bible element content must be text or object")
                    _tt = _e.get("title")
                    if isinstance(_tt, str) and _tt.strip():
                        if "\x00" in _tt:
                            raise ValueError("bible element title contains an invalid character")
                        _ct.setdefault("title", _tt.strip())
                    elif _tt is not None and not isinstance(_tt, str):
                        raise ValueError("bible element title must be text")
                    if _e.get("tags") is not None:
                        _tgs = _e.get("tags")
                        if not isinstance(_tgs, list):
                            raise ValueError("bible element tags must be a list")
                        for _tg in _tgs:
                            if not isinstance(_tg, str):
                                raise ValueError("each bible tag must be text")
                            if "\x00" in _tg:
                                raise ValueError("bible tag contains an invalid character")
                        _ct.setdefault("tags", [x.strip() for x in _tgs if isinstance(x, str) and x.strip()])
                    _els.append(_BEX(element_id=_eid.strip(), section=_sec.strip().lower(), content=_ct))
                episodic_bible = _PBX(elements=_els)
            _re2 = body.get("episodes")
            if _re2 is not None:
                if not isinstance(_re2, list):
                    raise ValueError("episodes must be a list")
                if len(_re2) != unit_count:
                    raise ValueError("episodes length must equal unit_count")
                for _ep in _re2:
                    if not isinstance(_ep, dict):
                        raise ValueError("each episodes entry must be an object")
                    if "episode_number" in _ep and _ep["episode_number"] is not None:
                        _en = _ep["episode_number"]
                        if isinstance(_en, bool) or not isinstance(_en, int) or _en < 1:
                            raise ValueError("episode_number must be an integer >= 1")
                    if "season_number" in _ep and _ep["season_number"] is not None:
                        _sn = _ep["season_number"]
                        if isinstance(_sn, bool) or not isinstance(_sn, int) or _sn < 1:
                            raise ValueError("season_number must be an integer >= 1")
                    if "title" in _ep and _ep["title"] is not None:
                        _t2 = _ep["title"]
                        if not isinstance(_t2, str):
                            raise ValueError("episode title must be text")
                        if not _t2.strip():
                            raise ValueError("episode title must be non-empty text")
                        if len(_t2.strip()) > 200:
                            raise ValueError("episode title must be 200 characters or fewer")
                        if "\x00" in _t2:
                            raise ValueError("episode title contains an invalid character")
                    if "premise" in _ep and _ep["premise"] is not None:
                        _pv = _ep["premise"]
                        if not isinstance(_pv, str):
                            raise ValueError("episode premise must be text")
                        if "\x00" in _pv:
                            raise ValueError("episode premise contains an invalid character")
                        if len(_pv) > 500:
                            raise ValueError("episode premise must be 500 characters or fewer")
                    if "bible_refs" in _ep and _ep["bible_refs"] is not None:
                        _br = _ep["bible_refs"]
                        if not isinstance(_br, list):
                            raise ValueError("bible_refs must be a list")
                        for _rf in _br:
                            if not isinstance(_rf, str) or not _rf.strip():
                                raise ValueError("each bible_ref must be non-empty text")
                            if "\x00" in _rf:
                                raise ValueError("bible_ref contains an invalid character")
                    if "dependencies" in _ep and _ep["dependencies"] is not None:
                        _dp = _ep["dependencies"]
                        if not isinstance(_dp, list):
                            raise ValueError("dependencies must be a list")
                        for _d in _dp:
                            if not isinstance(_d, str) or not _d.strip():
                                raise ValueError("each dependency must be non-empty text")
                            if "\x00" in _d:
                                raise ValueError("dependency contains an invalid character")
                episodic_episodes = _re2

        tone = text("tone", maximum=120) or "curious and cinematic"
        issued_at = datetime.now().isoformat()
        production_id = generate_production_id(title, productions_dir=PRODUCTIONS_DIR)

        # Track 7 Mode B: shape/intent is mode-derived; single_video == 1 unit,
        # unit_count == N independent standalone_video units.
        if mode == "single_video":
            workload_shape = "single_video"
            intended_count: int | None = 1
            mode_constraint = "single_video"
            units: list[ProductionUnit] = [
                ProductionUnit(
                    unit_id="unit-001",
                    ordinal=1,
                    unit_type="standalone_video",
                    title=title,
                    description=brief,
                    premise=brief,
                    provider_intent="image_motion",
                    content_intent={
                        "topic_override": brief,
                        "target_duration": duration,
                        "platform": output_intent,
                        "tone": tone,
                        "style": "curious and cinematic",
                        "number_of_scenes": scenes,
                    },
                )
            ]
        else:
            workload_shape = "unit_count"
            intended_count = unit_count  # type: ignore[assignment]
            mode_constraint = "unit_count"
            # Mode C: create episode units when episodic payload detected
            if is_episodic:
                from app.production.models import EpisodeMetadata as _EM
                # bible already parsed above; episodes validated above (episodic_episodes)
                # Re-parse episodic_bible if we took early short path (unit_type only)
                if episodic_bible is None and body.get("bible") is not None:
                    from app.production.models import BibleElement as _BE2, ProductionBible as _PB2
                    _rb2 = body.get("bible")
                    _elems2 = []
                    for _e2 in _rb2.get("elements", []):  # type: ignore[union-attr]
                        _rc2 = _e2.get("content")
                        _c2 = dict(_rc2) if isinstance(_rc2, dict) else ({"text": _rc2.strip()} if isinstance(_rc2, str) and _rc2.strip() else {})
                        if isinstance(_e2.get("title"), str) and _e2.get("title").strip():
                            _c2.setdefault("title", _e2.get("title").strip())
                        if isinstance(_e2.get("tags"), list):
                            _c2.setdefault("tags", [x.strip() for x in _e2.get("tags") if isinstance(x, str) and x.strip()])
                        _elems2.append(_BE2(element_id=_e2.get("element_id").strip(), section=_e2.get("section").strip().lower(), content=_c2))
                    episodic_bible = _PB2(elements=_elems2)
                if episodic_episodes is None and body.get("episodes") is not None:
                    episodic_episodes = body.get("episodes")  # already validated shape above when present
                # If still no episodic_episodes (episodic via unit_type/bible only), treat as bare episodic
                units = []
                for i in range(1, unit_count + 1):  # type: ignore[operator]
                    ep_raw = episodic_episodes[i - 1] if episodic_episodes is not None else {}
                    ep_title = ep_raw.get("title") if isinstance(ep_raw.get("title"), str) and ep_raw.get("title").strip() else f"{title} \u2014 Episode {i}"
                    ep_premise = ep_raw.get("premise") if isinstance(ep_raw.get("premise"), str) and ep_raw.get("premise").strip() else brief
                    ep_bible_refs = [r.strip() for r in ep_raw.get("bible_refs", [])] if isinstance(ep_raw.get("bible_refs"), list) else []
                    ep_deps = [d.strip() for d in ep_raw.get("dependencies", [])] if isinstance(ep_raw.get("dependencies"), list) else []
                    # episode_number / season_number / continuity
                    en = ep_raw.get("episode_number") if isinstance(ep_raw.get("episode_number"), int) and not isinstance(ep_raw.get("episode_number"), bool) else i
                    sn = ep_raw.get("season_number") if isinstance(ep_raw.get("season_number"), int) and not isinstance(ep_raw.get("season_number"), bool) else None
                    ep_meta = _EM(season_number=sn, episode_number=en, episode_title=ep_title, continuity_refs=[])
                    units.append(ProductionUnit(unit_id=f"unit-{i:03d}", ordinal=i, unit_type="episode", title=ep_title.strip(), description=ep_premise, premise=ep_premise, provider_intent="image_motion", content_intent={"topic_override": ep_premise, "target_duration": duration, "platform": output_intent, "tone": tone, "style": "curious and cinematic", "number_of_scenes": scenes}, episode=ep_meta, bible_refs=ep_bible_refs, dependencies=ep_deps))
            else:
                titles: list[str] = unit_titles or [f"{title} \u2014 Part {i}" for i in range(1, unit_count + 1)]  # type: ignore[operator]
                units = [
                    ProductionUnit(
                        unit_id=f"unit-{i:03d}",
                        ordinal=i,
                        unit_type="standalone_video",
                        title=titles[i - 1],
                        description=brief,
                        premise=brief,
                        provider_intent="image_motion",
                        content_intent={
                            "topic_override": brief,
                            "target_duration": duration,
                            "platform": output_intent,
                            "tone": tone,
                            "style": "curious and cinematic",
                            "number_of_scenes": scenes,
                        },
                    )
                    for i in range(1, unit_count + 1)  # type: ignore[operator]
                ]

        production = Production(
            production_id=production_id,
            title=title,
            bible=episodic_bible,
            generation_strategy=generation_strategy,
            route=route,
            # None (omitted by the client) = undeclared -> documented
            # backward-compatible default applied at the execution seam.
            audio_intent=audio_intent or None,
            command=ProductionCommand(
                text=brief,
                workload_shape=workload_shape,
                intended_unit_count=intended_count,
                explicit_constraints={
                    "mode": mode_constraint,
                    "output_intent": output_intent,
                    "target_duration": duration,
                    "scene_count": scenes,
                    "creation_method": "local",
                    # Track 1 hardening (defect D3): truthful creation-time
                    # cost CLASSIFICATION of the selected path — never a
                    # monetary amount, never accounting (that truth already
                    # lives in the execution provenance/accounting records).
                    "cost_class": creation_cost_class(
                        route=route, generation_strategy=generation_strategy
                    ),
                    # Step 7: the user's canonical model choice, recorded as
                    # creation INTENT (never as execution truth, never as a
                    # provider choice). Absent when the client did not declare
                    # one — an undeclared model is not a defaulted model.
                    **(
                        {"canonical_model_id": canonical_model_id}
                        if canonical_model_id
                        else {}
                    ),
                },
                creation_context={"source": "web_create_video"},
                issued_at=issued_at,
            ),
            units=units,
            created_at=issued_at,
        )
        save_new_production(production, root=PRODUCTIONS_DIR)
        logger.info(
            "production.created production=%s route=%s mode=%s "
            "legacy_strategy=%s units=%d",
            production.production_id,
            production.route,
            production.command.workload_shape,
            production.generation_strategy,
            len(production.units),
        )
    except ValueError as exc:
        return 400, {
            "error": "INVALID_CREATION_REQUEST",
            "message": str(exc),
        }
    except Exception as exc:
        logger.exception("Durable production creation failed")
        return 500, {
            "error": "PRODUCTION_CREATION_FAILED",
            "message": f"Could not persist production intent: {type(exc).__name__}.",
        }

    return 201, _redact_secrets({
        "status": "created",
        "production_id": production.production_id,
        "title": production.title,
        "unit_count": len(production.units),
        "creation_method": "local",
        "generation_strategy": production.generation_strategy,
        "route": production.route,
        # V1 AUDIO POLICY FIX: the declared intent (None = client did not send
        # it) and the effective intent it resolves to.  Reported separately so
        # the response never claims an explicit user choice that was not made.
        "audio_intent": production.audio_intent,
        "effective_audio_intent": production.audio_intent or DEFAULT_AUDIO_INTENT,
        # AVM Step 7: the declared canonical model INTENT, echoed exactly as
        # persisted (None = the client did not declare one). This is never a
        # claim about what will execute, and no provider / provider wire model
        # is echoed — provider identity is execution evidence only.
        "canonical_model_id": production.command.explicit_constraints.get(
            "canonical_model_id"
        ),
        # Track 1 hardening (defect D3): the response must never claim
        # "local_free" for a billable production path.
        "cost_class": creation_cost_class(
            route=production.route,
            generation_strategy=production.generation_strategy,
        ),
        "message": "Production intent created. Execute it explicitly from Production Detail.",
    })


def _production_or_none(production_id: str):
    from app.production.storage import load_production, production_exists

    if not production_exists(production_id, root=PRODUCTIONS_DIR):
        return None
    try:
        return load_production(production_id, root=PRODUCTIONS_DIR)
    except Exception as exc:
        logger.warning("malformed production intent %s: %s", production_id, exc)
        raise ValueError(
            f"malformed production intent for {production_id}: {exc}"
        ) from exc


def _production_not_found(production_id: str) -> dict:
    return {
        "error": "PRODUCTION_NOT_FOUND",
        "production_id": production_id,
        "message": "Production not found in the productions/ intent area. "
                   "Verify the production_id and try again.",
    }


def _executor_error(exc: Exception) -> dict:
    logger.exception("ProductionExecutor failed truthfully")
    return {
        "error": "EXECUTOR_ERROR",
        "message": f"ProductionExecutor raised {type(exc).__name__}: {_browser_safe_diagnostic(exc)}. "
                   "Check logs for the full trace; durable artifacts on disk "
                   "are the source of truth.",
    }


def _confirmation_required(action: str) -> dict:
    return {
        "error": "CONFIRMATION_REQUIRED",
        "message": f"Confirmation required: pass \"confirm\": true to "
                   f"{action}. State-changing operations are never implicit.",
    }


def _unit_progress_dict(item) -> dict:
    return {
        "unit_id": item.unit_id,
        "ordinal": item.ordinal,
        "state": item.state.value,
        "project_id": item.project_id,
        "bound_plan_version": item.bound_plan_version,
        "reason": item.reason,
        # P0-02: additive disclosure. True when the unit is IN_FLIGHT but its
        # durable evidence is older than the longest provider-declared polling
        # window, so no live run can own it. The state itself is unchanged.
        "stale_in_flight": bool(getattr(item, "stale_in_flight", False)),
    }


def _state_counts(report) -> dict:
    """Counts keyed by the Phase 10 six-state vocabulary, verbatim."""
    from app.orchestrator.production_execution import UnitExecutionState

    counts = report.counts()
    return {state.value: counts[state] for state in UnitExecutionState}


def _stale_in_flight_units(report) -> list:
    """Unit ids whose IN_FLIGHT evidence is stale/orphaned.

    P0-02: ``counts.IN_FLIGHT`` is derived from durable manifest state while
    ``run.running`` comes from the process-local run registry, so an interrupted
    run can leave a unit IN_FLIGHT with no live owner. This names those units
    instead of letting the two fields silently disagree.
    """
    return [
        item.unit_id
        for item in getattr(report, "units", ())
        if getattr(item, "stale_in_flight", False)
    ]


def _blocking_units(unit, progress_by_id: dict) -> list:
    """Presentation-only: dependencies that are not COMPLETED per the executor."""
    completed = {
        unit_id
        for unit_id, progress in progress_by_id.items()
        if progress["state"] == "COMPLETED"
    }
    return [dep for dep in unit.dependencies if dep not in completed]


def _derive_progress(production_id: str):
    """Fresh, uncached derivation via the Phase 10 authority."""
    return _production_executor(production_id).derive_progress()


def _unit_intent(unit) -> dict:
    data = {
        "unit_id": unit.unit_id,
        "ordinal": unit.ordinal,
        "unit_type": unit.unit_type,
        "title": unit.title,
        "description": unit.description,
        "premise": unit.premise,
        "dependencies": list(unit.dependencies),
        "content_intent": dict(unit.content_intent),
        "bible_refs": list(unit.bible_refs),
        "provider_intent": unit.provider_intent,
    }
    if unit.episode is not None:
        data["episode"] = unit.episode.model_dump()
    return data


#: AVM Step 7 — plan-metadata keys exposed to a browser.
#:
#: The FIRST SEVEN are the original Phase 11B allow-list, preserved verbatim:
#: same spelling, same meaning, same "only when present" discipline.
_PLAN_META_EVIDENCE_KEYS = (
    "production_plan_version", "unit_plan_version",
    "plan_identity", "content_hash",
    "planner_provider", "planner_model",
    "planner_warnings",
)

#: Step 6 AI Brain provenance keys (the ESTABLISHED provenance vocabulary plus
#: the one additive ``canonical_model_id``). Anything else persisted beside them
#: — prompts, raw bodies, credentials, absolute paths — stays server-side.
_PLANNER_PROVENANCE_KEYS = (
    "canonical_model_id", "provider", "model",
    "request_id", "request_id_kind",
    "fallback_used", "primary_provider", "fallback_reason",
    "resolution_trace", "source",
)

try:  # single authoritative trace vocabulary (Step 6) — never re-declared here
    from app.content.execution_evidence import RESOLUTION_TRACE_KEYS
except Exception:  # pragma: no cover - defensive only
    RESOLUTION_TRACE_KEYS = ("provider", "provider_model_id", "reason", "category")


def _planner_provenance_evidence(raw_meta: dict):
    """Project the Step-6 AI Brain provenance block for a model-first UI.

    This is the ONLY additive key on the existing plan-evidence projection. It
    exposes what actually executed — canonical model identity first, provider
    and provider wire model as secondary execution evidence — and nothing else:

    * explicit allow-list per key (``_PLANNER_PROVENANCE_KEYS``) and per
      resolution-trace entry (``RESOLUTION_TRACE_KEYS``), so a malformed or
      hostile ``plan_meta.json`` cannot smuggle prompts, credentials or paths;
    * only scalars are forwarded, so no nested structure is ever passed through;
    * ``None`` when the record carries no provenance at all (legacy productions,
      mock/local planning). Absence stays absence: no canonical model, provider
      or request id is ever inferred or fabricated here.

    Deliberately NOT projected: ``planner_accounting``. Step 7 surfaces model
    identity and provider fallback only — AVM has no authoritative AI Brain
    pricing (Step 6 records ``billing_status=unknown`` / ``cost=None``), and no
    cost/usage figure is rendered, so the block stays server-side.
    """
    raw = raw_meta.get("planner_provenance")
    if not isinstance(raw, dict):
        return None

    scalar = (str, bool, int, float)
    projected: dict = {}
    for key in _PLANNER_PROVENANCE_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if key == "resolution_trace":
            trace = []
            for entry in value if isinstance(value, list) else ():
                if not isinstance(entry, dict):
                    continue
                record = {
                    trace_key: entry[trace_key]
                    for trace_key in RESOLUTION_TRACE_KEYS
                    if trace_key in entry and isinstance(entry[trace_key], scalar)
                }
                if record:
                    trace.append(record)
            if trace:
                projected[key] = trace
            continue
        if value is None or isinstance(value, scalar):
            projected[key] = value
    return projected or None


def _plan_evidence(production_id: str, unit_id: str) -> dict:
    """Plan artifacts shown as SUPPORTING EVIDENCE only — never binding authority."""
    from app.production.storage import unit_plan_dir

    directory = unit_plan_dir(production_id, unit_id, root=PRODUCTIONS_DIR)
    plan_path = directory / "production_plan.json"
    brief_path = directory / "content_brief.json"
    meta_path = directory / "plan_meta.json"
    evidence = {
        "plan_directory": _relative_display(directory),
        "exists": any(p.is_file() for p in (plan_path, brief_path, meta_path)),
        "complete": all(p.is_file() for p in (plan_path, brief_path, meta_path)),
        "meta": None,
        "note": "Plan artifacts are evidence only; the binding authority is "
                "ProjectManifest.production_binding.",
    }
    if meta_path.is_file():
        try:
            raw_meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
            meta = {
                key: raw_meta.get(key)
                for key in _PLAN_META_EVIDENCE_KEYS
                if key in raw_meta
            }
            # AVM Step 7 (additive): model-centric AI Brain execution evidence,
            # allow-listed key by key. ``planner_provenance`` appears only when
            # the record actually carries it — legacy records stay legacy.
            provenance = _planner_provenance_evidence(raw_meta)
            if provenance is not None:
                meta["planner_provenance"] = provenance
            evidence["meta"] = meta
        except Exception as exc:
            evidence["meta_error"] = (
                "malformed plan metadata: " + _browser_safe_diagnostic(exc)
            )
    return evidence


def _final_video_output_facts(manifest) -> dict:
    """Project the ACCEPTANCE-TIME media facts of the final artifact.

    Phase 2D (Option A — reuse persisted authoritative metadata, zero new
    media inspection). The pipeline's final acceptance gate already probes
    the actual completed output once with ffprobe and persists the facts in
    ``ProjectManifest.stages["complete"].metadata`` (duration/width/height/
    file_size from the real media stream/container and the real artifact on
    disk). This helper ONLY re-reads those recorded values; it never probes,
    never infers from route/provider/config/targets, and never substitutes
    zeros: anything unrecorded, malformed, or non-positive stays ``None``
    (UNKNOWN != ZERO). A legitimately accepted artifact can never carry a
    non-positive duration (acceptance requires a positive probe), so 0/-1 is
    treated as unknown, not as a fact.

    Deliberately NOT read here: ``StageResult.duration_seconds`` — that is
    stage wall-clock timing and is never a media duration.
    """
    facts = {
        "duration": None,
        "width": None,
        "height": None,
        "file_size": None,
        "resolution": None,
    }

    def _positive_number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value if value > 0 else None

    def _positive_int(value):
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if value > 0 else None

    stages = getattr(manifest, "stages", None) or {}
    try:
        from app.orchestrator.models import PipelineStage

        result = stages.get(PipelineStage.COMPLETE.value)
    except Exception:  # pragma: no cover - defensive only
        result = stages.get("complete")
    metadata = dict(getattr(result, "metadata", None) or {})
    if not metadata:
        return facts

    facts["duration"] = _positive_number(metadata.get("duration"))
    facts["width"] = _positive_int(metadata.get("width"))
    facts["height"] = _positive_int(metadata.get("height"))
    facts["file_size"] = _positive_int(metadata.get("file_size"))
    # Resolution is a derived label over the ACTUAL probed dimensions only
    # (same "WxH" convention the assembly validator uses); never invented.
    if facts["width"] and facts["height"]:
        facts["resolution"] = f"{facts['width']}x{facts['height']}"
    return facts


def _manifest_payload(manifest, project_id: str | None = None) -> dict:
    """Read-only, secret-free snapshot of a bound project manifest."""
    data = manifest.model_dump()
    stages = {}
    for name, result in manifest.stages.items():
        status = getattr(result.status, "value", str(result.status))
        stages[name.upper()] = {
            "status": str(status).upper(),
            "error": _browser_safe_diagnostic(result.error),
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "duration_seconds": result.duration_seconds,
        }
    # Config snapshots and the Flow project URL are deliberately omitted:
    # they are runtime transport detail, never operator inspection surface.
    safe_project_id = project_id or manifest.project_id
    artifact_payload = _artifact_payload(manifest, safe_project_id)
    for artifact in artifact_payload.values():
        if artifact["available"] and artifact["type"] == "Final video":
            artifact["media_url"] = f"/api/projects/{safe_project_id}/media"
    # Phase 2D: expose the recorded acceptance-time media facts on the
    # final-video artifact. Additive only: existing keys/semantics are
    # unchanged. Facts appear ONLY while the artifact is genuinely
    # AVAILABLE — a RECORDED BUT MISSING / FAILED / NOT GENERATED artifact
    # never echoes facts recorded for different bytes — and each fact stays
    # null when it was never authoritatively recorded (UNKNOWN != ZERO).
    # No media inspection happens on this read path.
    final_video = artifact_payload.get("final_video")
    if final_video is not None:
        facts = _final_video_output_facts(manifest)
        if final_video["state"] != "AVAILABLE":
            facts = {key: None for key in facts}
        final_video.update(facts)
    return {
        "project_id": safe_project_id,
        "topic": manifest.topic,
        "created_at": manifest.created_at,
        "updated_at": manifest.updated_at,
        "completed_at": manifest.completed_at,
        "current_stage": str(getattr(manifest.current_stage, "value", manifest.current_stage)),
        "dry_run": manifest.dry_run,
        "mock_mode": manifest.mock_mode,
        "stages": stages,
        "scene_checkpoints": {
            str(scene_id): _redact_secrets(_safe_checkpoint_payload(checkpoint))
            for scene_id, checkpoint in (data.get("scene_checkpoints") or {}).items()
            if isinstance(checkpoint, dict)
        },
        "scene_checkpoint_rows": _scene_checkpoint_rows(
            data.get("scene_checkpoints") or {},
            safe_project_id,
            manifest.scene_video_paths,
        ),
        "production_binding": data.get("production_binding"),
        "source_summary_handoff": data.get("source_summary_handoff"),
        # G3: the route Core COMPILED for this unit (route-compilation
        # evidence, written once at project initialization).  This is a route
        # fact only: it is not scene provider/model provenance, not media
        # strategy, and not accounting.  None = UNKNOWN for projects created
        # before the evidence seam (and for direct engine runs); it is never
        # inferred from the current route registry or from the intent.
        "route_compilation": (
            manifest.route_compilation.model_dump(mode="json")
            if getattr(manifest, "route_compilation", None) is not None
            else None
        ),
        "publications": _publication_summary(data),
        "artifacts": artifact_payload,
    }


_CHECKPOINT_TRANSPORT_KEY_PATTERN = re.compile(
    r"(url|uri|path|file|asset|source|endpoint|header|cookie|request|input|response)",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[a-zA-Z]:[\\/]")


def _unsafe_checkpoint_string(value: str) -> bool:
    """Return true for remote URLs or absolute filesystem paths."""
    text = value.strip()
    return bool(
        re.match(r"^(?:https?|wss?|ftp|file)://", text, re.IGNORECASE)
        or _WINDOWS_ABSOLUTE_PATH.match(text)
        or text.startswith(("/", "\\\\"))
    )


def _safe_checkpoint_payload(checkpoint: dict) -> dict:
    """Keep diagnostic checkpoint evidence while removing transport/path data."""
    if not isinstance(checkpoint, dict):
        return {}
    safe = {}
    for key, value in checkpoint.items():
        if _CHECKPOINT_TRANSPORT_KEY_PATTERN.search(str(key)):
            continue
        if isinstance(value, dict):
            safe[key] = _safe_checkpoint_payload(value)
        elif isinstance(value, list):
            safe[key] = [
                cleaned
                for item in value
                if not (isinstance(item, str) and _unsafe_checkpoint_string(item))
                for cleaned in [
                    _safe_checkpoint_payload(item) if isinstance(item, dict) else item
                ]
            ]
        elif isinstance(value, str) and _unsafe_checkpoint_string(value):
            continue
        else:
            safe[key] = value
    return safe


def _failure_details(manifest) -> list:
    """Failing stages with truthful error text (empty when healthy)."""
    failing = []
    for name, result in manifest.stages.items():
        status = getattr(result.status, "value", str(result.status))
        if str(status).upper() == "FAILED":
            failing.append({
                "stage": name.upper(),
                "error": _browser_safe_diagnostic(result.error),
                "completed_at": result.completed_at,
            })
    return failing


def _scene_checkpoint_rows(
    scene_checkpoints: dict,
    project_id: str | None = None,
    scene_video_paths=None,
) -> list:
    """Browser-safe scene evidence rows; transport URLs and raw paths stay server-side."""
    rows = []

    def scene_order(item):
        """Preserve recorded scene order instead of lexicographic ID order."""
        scene_id, checkpoint = item
        if isinstance(checkpoint, dict):
            for value in (
                checkpoint.get("sequence"),
                (checkpoint.get("checkpoint_evidence") or {}).get("sequence"),
            ):
                if isinstance(value, int) and value >= 1:
                    return (0, value, str(scene_id))
        match = re.search(r"(?:^|[_-])(\d+)$", str(scene_id))
        if match:
            return (0, int(match.group(1)), str(scene_id))
        return (1, 0, str(scene_id))

    for scene_id, checkpoint in sorted(scene_checkpoints.items(), key=scene_order):
        if not isinstance(checkpoint, dict):
            continue
        evidence = checkpoint.get("checkpoint_evidence") or {}
        # Batch 1F: actual execution provenance for the delivered artifact.
        # The authoritative scene-manifest ``provenance`` block is mirrored
        # onto the operational checkpoint at completion; unknown stays None.
        provenance = checkpoint.get("provenance") or {}
        # Batch 1H: generic provider accounting for the delivered artifact —
        # per-attempt usage/billing/cost evidence plus the derived summary.
        # Unknown stays None (a legacy generation_cost=0 is ambiguous and is
        # never reinterpreted as a known provider monetary zero).
        accounting = checkpoint.get("accounting") or {}
        accounting_summary = accounting.get("summary") or {}
        raw_path = (
            checkpoint.get("video_path")
            or checkpoint.get("file_path")
            or evidence.get("video_path")
            or evidence.get("file_path")
        )
        if not raw_path and scene_video_paths:
            match = re.search(r"(?:^|[_-])(\d+)$", str(scene_id))
            if match:
                index = int(match.group(1)) - 1
                if 0 <= index < len(scene_video_paths):
                    raw_path = scene_video_paths[index]
        status = str(checkpoint.get("status") or "").upper()
        media_path, media_error = (None, "project context unavailable")
        if project_id and raw_path:
            media_path, media_error = _validated_scene_file(
                PROJECTS_DIR / project_id, raw_path
            )
        if status == "FAILED":
            artifact_state = "FAILED"
        elif raw_path and media_path is not None and media_error is None:
            artifact_state = "AVAILABLE"
        elif raw_path:
            artifact_state = "RECORDED BUT MISSING"
        else:
            artifact_state = "NOT GENERATED"
        # P2-03 (legacy honesty): records written BEFORE the Batch 1F/1H capture
        # seam have no provenance block at all, so every actual_* field
        # legitimately reads as unknown. Their FLAT fields do carry real observed
        # facts, so those are surfaced in an explicitly labelled legacy block
        # built ONLY from values actually present.
        #
        # Deliberately NOT done here:
        #  - ``model`` is never promoted to ``actual_model``. On these records it
        #    is the CONFIGURED provider model (selection), and Batch 1F requires
        #    actual identity to come from the provider result. It is exposed
        #    under its own name so it cannot be mistaken for actual.
        #  - ``generation_cost`` is never reinterpreted as monetary cost: a
        #    legacy 0 on a billable provider is ambiguous, not a known zero.
        legacy_evidence = None
        if not provenance:
            legacy_facts = {}
            for key in (
                "task_id", "job_id", "remote_status", "attempt",
                "submitted_at", "usage",
            ):
                value = checkpoint.get(key)
                if value is not None and value != "":
                    legacy_facts[key] = value
            configured_model = checkpoint.get("model")
            if isinstance(configured_model, str) and configured_model.strip():
                legacy_facts["configured_model"] = configured_model
            if legacy_facts:
                legacy_evidence = {
                    "source": (
                        "pre-Batch-1F checkpoint fields; actual-vs-requested "
                        "provider/model is not distinguishable on this record"
                    ),
                    **legacy_facts,
                }
        rows.append({
            "scene_id": scene_id,
            "status": checkpoint.get("status"),
            "artifact_state": artifact_state,
            "provider": checkpoint.get("provider") or evidence.get("provider"),
            # Batch 1F: ``job_id_present`` is kept unchanged for backward
            # compatibility (OpenRouter vocabulary only). Alibaba carries a
            # DashScope ``task_id`` instead, so task identity is now reported
            # kind-aware via ``task_identity_present``.
            "job_id_present": bool(checkpoint.get("job_id") or evidence.get("job_id")),
            "task_identity_present": bool(
                checkpoint.get("job_id")
                or checkpoint.get("task_id")
                or evidence.get("job_id")
                or evidence.get("task_id")
                or provenance.get("request_id")
            ),
            # Batch 1F: actual execution provenance (Batch 1G will present
            # this; None/absent = unknown, never fabricated).
            "provenance": provenance or None,
            # P2-03: additive, explicitly labelled legacy evidence. Null for any
            # record that carries a real Batch 1F provenance block.
            "legacy_evidence": legacy_evidence,
            "actual_provider": provenance.get("provider"),
            "actual_model": provenance.get("model"),
            "request_id": provenance.get("request_id"),
            "fallback_used": provenance.get("fallback_used"),
            # Batch 1H: additive provider accounting (truthful backend
            # evidence; the UI decides presentation later). ``cost`` is
            # provider-reported monetary evidence or None (unknown) — never a
            # fabricated zero for billable operations.
            "accounting": accounting or None,
            "billing_status": accounting_summary.get("billing_status"),
            "usage": accounting_summary.get("final_usage"),
            "cost": accounting_summary.get("cost"),
            "cost_source": accounting_summary.get("cost_source"),
            "currency": accounting_summary.get("currency"),
            "attempt_count": accounting_summary.get("attempt_count"),
            "generation_cost": checkpoint.get("generation_cost"),
            "video_available": artifact_state == "AVAILABLE",
            "media_url": (
                f"/api/projects/{project_id}/scenes/{scene_id}/media"
                if artifact_state == "AVAILABLE" and project_id else None
            ),
            "updated_at": checkpoint.get("updated_at") or checkpoint.get("timestamp"),
        })
    return rows


# ---------------------------------------------------------------------------
# Phase 11 — read-only inspection payloads
# ---------------------------------------------------------------------------


def productions_index_payload():
    """Production overview: intent + freshly derived aggregate progress."""
    from app.production.storage import list_productions

    ids = list_productions(root=PRODUCTIONS_DIR)
    # P0-01: derive progress for the whole collection with ONE shared projects
    # scan instead of a per-production full rescan. Semantics are unchanged:
    # reconcile_project still runs once per bound project, so `counts` /
    # `complete` / PROJECT_FAILED are identical to the per-id path.
    derived = {}
    if ids:
        try:
            from app.orchestrator.production_execution import ProductionExecutor

            derived = ProductionExecutor.derive_progress_many(
                ids, productions_root=PRODUCTIONS_DIR, projects_root=PROJECTS_DIR,
            )
        except Exception as exc:
            logger.warning(
                "batch progress derivation failed truthfully: %s", exc
            )
            derived = {}

    items = []
    for production_id in ids:
        try:
            production = _production_or_none(production_id)
        except ValueError as exc:
            items.append({
                "production_id": production_id,
                "title": production_id,
                "counts": None,
                "error": str(exc),
                "sample": False,
            })
            continue
        item = {
            "production_id": production.production_id,
            "title": production.title,
            "command_text": production.command.text,
            "workload_shape": production.command.workload_shape,
            "intended_unit_count": production.command.intended_unit_count,
            "unit_count": len(production.units),
            "plan_version": production.plan_version,
            "generation_strategy": production.generation_strategy,
            "route": production.route,
            "created_at": production.created_at,
            "revised_at": production.revised_at,
            "run": dict(_production_runs.get(production.production_id) or {"running": False}),
            "sample": False,
        }
        try:
            report = derived.get(production_id)
            if report is None:
                report = _derive_progress(production_id)
            item["counts"] = _state_counts(report)
            item["complete"] = report.complete
            item["stale_in_flight_units"] = _stale_in_flight_units(report)
        except Exception as exc:
            logger.warning(
                "progress derivation failed truthfully for %s: %s", production_id, exc
            )
            item["counts"] = None
            item["error"] = (
                "progress derivation failed: "
                f"{type(exc).__name__}: {_browser_safe_diagnostic(exc)}"
            )
        items.append(item)
    return 200, _redact_secrets(items)


def production_detail_payload(production_id: str):
    try:
        production = _production_or_none(production_id)
    except ValueError as exc:
        return 500, _intent_malformed(production_id, exc)
    if production is None:
        return 404, _production_not_found(production_id)
    try:
        report = _derive_progress(production_id)
    except Exception as exc:
        return 500, _executor_error(exc)
    progress_by_id = {
        item.unit_id: _unit_progress_dict(item) for item in report.units
    }
    units = []
    for unit in sorted(production.units, key=lambda item: item.ordinal):
        progress = progress_by_id[unit.unit_id]
        units.append({
            "intent": _unit_intent(unit),
            "execution": progress,
            "blocking_units": _blocking_units(unit, progress_by_id),
            "plan_exists": _plan_evidence(production_id, unit.unit_id)["exists"],
        })
    payload = {
        "production_id": production.production_id,
        "title": production.title,
        "generation_strategy": production.generation_strategy,
        "route": production.route,
        "command": production.command.model_dump(),
        "plan_version": production.plan_version,
        "created_at": production.created_at,
        "revised_at": production.revised_at,
        "unit_count": len(production.units),
        "counts": _state_counts(report),
        "complete": report.complete,
        "units": units,
        "run": dict(_production_runs.get(production.production_id) or {"running": False}),
        "stale_in_flight_units": _stale_in_flight_units(report),
        "sample": False,
    }
    return 200, _redact_secrets(payload)


def production_units_payload(production_id: str):
    status, payload = production_detail_payload(production_id)
    if status != 200:
        return status, payload
    return 200, {
        "production_id": payload["production_id"],
        "counts": payload["counts"],
        "units": payload["units"],
    }


def production_progress_payload(production_id: str):
    """Polling surface: re-derived on EVERY call; never persisted, never executes."""
    try:
        production = _production_or_none(production_id)
    except ValueError as exc:
        return 500, _intent_malformed(production_id, exc)
    if production is None:
        return 404, _production_not_found(production_id)
    try:
        report = _derive_progress(production_id)
    except Exception as exc:
        return 500, _executor_error(exc)
    payload = {
        "production_id": production_id,
        "plan_version": production.plan_version,
        "units": [_unit_progress_dict(item) for item in report.units],
        "counts": _state_counts(report),
        "complete": report.complete,
        "derived_at": datetime.now().isoformat(),
        "run": dict(_production_runs.get(production_id) or {"running": False}),
        "stale_in_flight_units": _stale_in_flight_units(report),
        "sample": False,
    }
    return 200, _redact_secrets(payload)


def unit_detail_payload(production_id: str, unit_id: str):
    try:
        production = _production_or_none(production_id)
    except ValueError as exc:
        return 500, _intent_malformed(production_id, exc)
    if production is None:
        return 404, _production_not_found(production_id)
    unit = next(
        (item for item in production.units if item.unit_id == unit_id), None
    )
    if unit is None:
        return 400, {
            "error": "UNIT_NOT_FOUND",
            "production_id": production_id,
            "unit_id": unit_id,
            "message": f"unit {unit_id!r} is not part of {production_id}.",
        }
    try:
        executor = _production_executor(production_id)
        # Phase 2B: ONE shared scan serves both the progress report and the
        # bound-project snapshot below.  Previously derive_progress() and
        # discover_bindings() scanned the entire projects tree twice per
        # request and could observe two different disk views; every payload
        # field is derived from the same values as before, once.
        report, bindings = executor.derive_progress_with_bindings(production)
    except Exception as exc:
        return 500, _executor_error(exc)
    progress_by_id = {
        item.unit_id: _unit_progress_dict(item) for item in report.units
    }
    bound = bindings.get(unit_id)
    manifest_payload = _manifest_payload(bound.manifest, bound.directory_id) if bound else None
    payload = {
        "production_id": production_id,
        "production_title": production.title,
        "plan_version": production.plan_version,
        "unit_intent": _unit_intent(unit),
        "execution": progress_by_id.get(unit_id),
        "run": dict(_production_runs.get(production_id) or {"running": False}),
        "blocking_units": _blocking_units(unit, progress_by_id),
        "plan_evidence": _plan_evidence(production_id, unit_id),
        "bound_project": manifest_payload,
        "summary_video": _summary_video_status(
            production_id, unit_id, bound=bound, inspect_binding=False,
        ),
        "scene_checkpoint_rows": (
            _scene_checkpoint_rows(
                bound.manifest.scene_checkpoints, bound.directory_id
            )
            if bound else []
        ),
        "failure_details": _failure_details(bound.manifest) if bound else [],
        "sample": False,
    }
    return 200, _redact_secrets(payload)


def unit_reconciliation_payload(production_id: str, unit_id: str):
    """On-demand, read-only reconciliation through the existing Phase 10 oracle."""
    try:
        production = _production_or_none(production_id)
    except ValueError as exc:
        return 500, _intent_malformed(production_id, exc)
    if production is None:
        return 404, _production_not_found(production_id)
    unit = next(
        (item for item in production.units if item.unit_id == unit_id), None
    )
    if unit is None:
        return 400, {
            "error": "UNIT_NOT_FOUND",
            "production_id": production_id,
            "unit_id": unit_id,
            "message": f"unit {unit_id!r} is not part of {production_id}.",
        }
    try:
        bindings = _production_executor(production_id).discover_bindings(production)
    except Exception as exc:
        return 500, _executor_error(exc)
    bound = bindings.get(unit_id)
    if bound is None:
        return 400, {
            "error": "UNIT_NOT_BOUND",
            "unit_id": unit_id,
            "message": "Unit has no bound execution project; there is nothing "
                       "to reconcile yet.",
        }
    from app.orchestrator.reconciliation import reconcile_project

    # Identical oracle call to Phase 10's derive_progress(); persist=False
    # keeps this strictly read-only.
    report = reconcile_project(
        bound.manifest.project_id, bound.project_dir, persist=False
    )
    payload = {
        "production_id": production_id,
        "unit_id": unit_id,
        "project_id": bound.directory_id,
        "reconciliation": _reconciliation_summary(report),
        "persisted": False,
        "sample": False,
    }
    return 200, _redact_secrets(payload)


def _reconciliation_summary(report) -> dict:
    """Return only path-free reconciliation evidence consumed by Unit Detail."""
    data = report.to_dict()

    def status_for(name):
        evidence = data.get(name) or {}
        return {"status": evidence.get("status")} if isinstance(evidence, dict) else {}

    return {
        "verdict": data.get("verdict"),
        "reasons": [
            _browser_safe_diagnostic(reason) for reason in (data.get("reasons") or [])
        ],
        "content": status_for("content"),
        "voice": status_for("voice"),
        "scenes": status_for("scenes"),
        "captions": status_for("captions"),
        "assembly": status_for("assembly"),
        "persisted": False,
    }


# ---------------------------------------------------------------------------
# AUDIO PREVIEW + AUDIO EVIDENCE — read-only projection over real evidence
# ---------------------------------------------------------------------------
#
# What this surface answers is narrow and specific: "what audio does the
# finished production ACTUALLY contain, and where can the browser hear it?"
#
# Authority order (never inverted):
#   1. the final assembled media artifact  (existing final-media evidence seam)
#   2. persisted final-media acceptance metadata (stages["complete"].metadata)
#   3. persisted assembly audio evidence    (stages["assembly"].metadata["audio"])
#   4. persisted voice/native evidence      (stages["voice"], scene audio_facts)
#   5. persisted manifest configuration     (assembly_config snapshot)
#
# The declared ``audio_intent`` is REPORTED, never used to decide what the
# final file contains: intent is an input to execution, while execution
# evidence and the delivered bytes are the truth.  ``Scene.narration`` is never
# read here at all.  Nothing on this path generates, repairs, executes, or
# persists anything; it reuses the media-inspection and artifact-serving seams
# that already exist.

#: Audio states.  Every value except ``NO_AUDIO`` is an EXISTING AVM word:
#: ``AVAILABLE`` / ``FAILED`` come from the frozen artifact-presentation
#: states, ``NOT_REQUIRED`` is ``ArtifactStatus.NOT_REQUIRED``, and
#: ``UNKNOWN`` is the established "evidence does not establish it" reading
#: (``UNKNOWN != ZERO``).  ``NO_AUDIO`` is the one genuinely new word: a
#: complete, valid artifact that deliberately carries no audio stream is
#: neither AVAILABLE nor a failure, and no existing state expresses that.
AUDIO_STATE_AVAILABLE = "AVAILABLE"
AUDIO_STATE_NO_AUDIO = "NO_AUDIO"
AUDIO_STATE_NOT_REQUIRED = "NOT_REQUIRED"
AUDIO_STATE_INCOMPLETE = "INCOMPLETE"
AUDIO_STATE_FAILED = "FAILED"
AUDIO_STATE_UNKNOWN = "UNKNOWN"

#: What the final artifact's audio actually IS.  ``unknown`` is a real answer:
#: the file carries audio but no persisted evidence attributes it to a narrator
#: or to native source media, and inventing that difference would be a lie.
AUDIO_SOURCE_NARRATION = "narration"
AUDIO_SOURCE_NATIVE = "native"
AUDIO_SOURCE_NATIVE_PLUS_NARRATION = "native_plus_narration"
AUDIO_SOURCE_NONE = "none"
AUDIO_SOURCE_UNKNOWN = "unknown"


def _stage_status_value(manifest, stage) -> str | None:
    """The persisted status string of one pipeline stage, or None if absent."""
    result = manifest.get_stage_result(stage)
    if result is None:
        return None
    return str(getattr(result.status, "value", result.status)).upper()


def _stage_metadata(manifest, stage) -> dict:
    result = manifest.get_stage_result(stage)
    return dict(getattr(result, "metadata", None) or {})


def _audio_execution_evidence(manifest) -> dict:
    """Narration/native evidence taken ONLY from persisted execution evidence.

    Prefers the assembly stage's audio evidence block (written by the accepted
    audio-policy fix).  For projects that predate it, it falls back to other
    durable evidence — a COMPLETED VOICE stage with a recorded audio artifact,
    and scene-level ``AudioMediaFacts`` — and labels which path was used
    through ``evidence_source``, so a reader always knows how a claim was
    established.  Synthetic normalization silence is excluded by
    ``has_native_audio`` semantics and is never reported as native audio.

    Native provenance is then adjudicated by ``scene_audio_provenance`` (the
    persisted synthetic identity), which can tell four things apart that a bare
    probe cannot: established native audio, established synthetic silence, an
    audio stream whose identity was never declared (historical evidence), and no
    audio evidence at all.  A recorded native claim is only downgraded when
    evidence actually contradicts it; it is never upgraded, and an unestablished
    identity is reported as ``None`` rather than resolved by assumption.
    """
    from app.orchestrator.models import PipelineStage
    from app.orchestrator.reconciliation import (
        AUDIO_PROVENANCE_SYNTHETIC,
        AUDIO_PROVENANCE_UNKNOWN,
        native_audio_observed,
        scene_audio_provenance,
        summarize_audio_provenance,
    )

    persisted = _stage_metadata(manifest, PipelineStage.ASSEMBLY).get("audio")
    voice_metadata = _stage_metadata(manifest, PipelineStage.VOICE)
    voice_status = _stage_status_value(manifest, PipelineStage.VOICE)

    if isinstance(persisted, dict) and persisted:
        narration_present = bool(persisted.get("narration_track_present"))
        native_present = bool(persisted.get("native_audio_present"))
        native_scenes = [
            str(scene_id)
            for scene_id in (persisted.get("scenes_with_native_audio") or [])
        ]
        evidence_source = "assembly_audio_evidence"
    else:
        # Legacy path: a COMPLETED VOICE stage that recorded an audio artifact
        # IS durable evidence that a narrator track exists.  A SKIPPED,
        # FAILED, RUNNING, or absent one is not.  Never inferred from plan text.
        narration_present = bool(
            voice_status == "COMPLETED" and manifest.voice_audio_path
        )
        native_present = native_audio_observed(manifest)
        native_scenes = []
        evidence_source = "persisted_voice_and_scene_audio_evidence"

    provenance = scene_audio_provenance(manifest)
    summary = summarize_audio_provenance(provenance)
    aggregate = summary["aggregate"]
    established_native = summary["established_native"]
    established_synthetic = summary["established_synthetic"]
    unverified = summary["unverified"]

    if established_native:
        # Evidence positively establishes genuine scene audio.
        native_present = True
        native_scenes = sorted(set(native_scenes) | set(established_native))
    elif aggregate == AUDIO_PROVENANCE_SYNTHETIC:
        # Every recorded stream is declared synthetic silence: it is audio, but
        # it is not native audio, whatever the codec or the older claim says.
        native_present = False
    elif aggregate in (AUDIO_PROVENANCE_UNKNOWN, "mixed"):
        # Streams exist but their identity was never (fully) declared: the
        # native question stays open instead of being answered by default.
        native_present = None

    return {
        "narration_present": narration_present,
        "native_present": native_present,
        "native_scenes": native_scenes,
        "synthetic_identity": aggregate,
        "established_native_scenes": established_native,
        "established_synthetic_scenes": established_synthetic,
        "unverified_scenes": unverified,
        "evidence_source": evidence_source,
        "voice_stage_status": voice_status,
        "voice_provider": voice_metadata.get("provider"),
        "voice_audio_duration": voice_metadata.get("duration"),
        "voice_skip_reason": voice_metadata.get("skip_reason"),
    }


def _final_audio_stream_facts(manifest, project_dir, artifact_available: bool) -> dict:
    """Audio stream facts of the delivered artifact, labelled by provenance.

    ``final_media_artifact``: read through the EXISTING authoritative
    final-media evidence seam (one FFprobe pass over the accepted artifact) —
    the top of the authority order, and the only source that can prove what the
    delivered file actually carries today.

    ``persisted_acceptance_metadata``: the facts the acceptance gate already
    recorded for that artifact.  Used when a live probe is impossible (FFprobe
    unavailable) or the recorded artifact is no longer present.

    ``unavailable``: neither established anything.  Every field stays null —
    ``UNKNOWN`` is never rendered as ``False``/``0``.
    """
    from app.orchestrator.models import PipelineStage
    from app.orchestrator.reconciliation import (
        final_audio_required,
        final_media_evidence,
    )

    def _pack(**values):
        facts = {
            "has_audio": None,
            "audio_stream_count": None,
            "audio_codec": None,
            "sample_rate": None,
            "channels": None,
            "audio_duration": None,
            "audio_required": None,
            "facts_source": "unavailable",
            "artifact_inspected": False,
        }
        facts.update(values)
        return facts

    if artifact_available:
        try:
            evidence = final_media_evidence(manifest, project_dir)
        except Exception as exc:  # noqa: BLE001 - a read must not become a 500
            logger.info(
                "Final media evidence could not be read (%s); falling back to "
                "persisted acceptance metadata", type(exc).__name__
            )
            evidence = {}
        validation = evidence.get("validation") or {}
        if validation.get("exists") and "has_audio" in validation:
            return _pack(
                has_audio=bool(validation.get("has_audio")),
                audio_stream_count=validation.get("audio_stream_count"),
                audio_codec=validation.get("audio_codec"),
                sample_rate=validation.get("audio_sample_rate"),
                channels=validation.get("audio_channels"),
                audio_duration=validation.get("audio_stream_duration"),
                audio_required=validation.get("audio_required"),
                facts_source="final_media_artifact",
                artifact_inspected=True,
            )

    metadata = _stage_metadata(manifest, PipelineStage.COMPLETE)
    recorded = metadata.get("audio_stream")
    if isinstance(recorded, dict) and recorded:
        return _pack(
            has_audio=recorded.get("has_audio"),
            audio_stream_count=recorded.get("audio_stream_count"),
            audio_codec=recorded.get("audio_codec") or metadata.get("audio_codec"),
            sample_rate=recorded.get("sample_rate"),
            channels=recorded.get("channels"),
            audio_duration=recorded.get("duration"),
            # The REQUIREMENT is a policy fact, not a media fact, so it is
            # re-derived from the recorded policy and native evidence through
            # the single existing authority rather than being guessed.
            audio_required=(
                final_audio_required(manifest)
                if recorded.get("has_audio") is not None
                else None
            ),
            facts_source="persisted_acceptance_metadata",
        )
    if metadata.get("audio_codec"):
        # Manifests written before the audio-stream block existed recorded only
        # the acceptance codec: enough to establish THAT audio was delivered,
        # not enough to describe the stream.  The rest stays null.
        return _pack(
            has_audio=True,
            audio_codec=metadata.get("audio_codec"),
            audio_required=final_audio_required(manifest),
            facts_source="persisted_acceptance_metadata",
        )
    return _pack()


def _final_audio_tracks(policy, facts, evidence) -> dict:
    """Which tracks the DELIVERED artifact carries, per policy + evidence.

    Scene-level evidence answers "does native scene audio exist"; the applied
    policy decides whether that audio was allowed into the final mix at all.
    Under ``narration_only`` an existing narrator track SUPPRESSES native audio
    (the frozen ``select_audio_inputs`` behavior), so native evidence must never
    be reported as delivered audio merely because some scene had sound.  Both
    claims are returned separately so neither can be mistaken for the other.
    """
    has_audio = bool(facts.get("has_audio"))
    narration_exists = evidence["narration_present"]
    native_exists = evidence["native_present"]
    permits_narration = audio_policy_requires_narration(policy)
    permits_native = policy in (
        AudioPolicy.NATIVE_ONLY,
        AudioPolicy.NATIVE_PLUS_NARRATION,
    )

    narration_delivered = has_audio and narration_exists and permits_narration
    if policy == AudioPolicy.NARRATION_ONLY and narration_exists:
        native_delivered = False
    else:
        # ``native_exists`` is deliberately tri-state: True (established native),
        # False (established non-native, e.g. declared synthetic silence) and
        # ``None`` (an audio stream whose synthetic identity was never declared).
        # Only an established True may be delivered as native audio; ``None`` must
        # never be resolved to True by a policy that merely permits native audio,
        # and it is not the same answer as an established False either.
        native_delivered = bool(has_audio and native_exists is True and permits_native)

    return {
        "narration_exists": narration_exists,
        "native_exists": native_exists,
        "native_unverified": native_exists is None,
        "narration_delivered": narration_delivered,
        "native_delivered": native_delivered,
    }


def _classify_final_audio_source(policy, facts, evidence) -> str:
    """What the FINAL artifact's audio actually is, from real evidence.

    Tolerant of every combination the frozen policy layer legitimately
    produces (``native_plus_narration`` may carry only one track; a policy that
    suppresses native audio delivers narration alone) and answers ``unknown``
    whenever the file carries audio that no evidence attributes.
    """
    has_audio = facts.get("has_audio")
    if has_audio is False:
        return AUDIO_SOURCE_NONE
    if has_audio is not True:
        return AUDIO_SOURCE_UNKNOWN
    tracks = _final_audio_tracks(policy, facts, evidence)
    if tracks["narration_delivered"] and tracks["native_delivered"]:
        return AUDIO_SOURCE_NATIVE_PLUS_NARRATION
    if tracks["narration_delivered"]:
        return AUDIO_SOURCE_NARRATION
    if tracks["native_delivered"]:
        return AUDIO_SOURCE_NATIVE
    # Audio exists but nothing establishes whose track it is — for example a
    # stream delivered under a policy that permits none.  Never invent a source.
    return AUDIO_SOURCE_UNKNOWN


def _audio_policy_from_manifest(manifest) -> AudioPolicy:
    """The persisted EFFECTIVE policy (hierarchy #5), via the shared normalizer.

    A legacy manifest with no snapshot resolves to the historical
    ``narration_only`` default exactly as reconciliation and the pipeline do —
    this reads the recorded decision, it never recompiles intent.
    """
    return normalize_audio_policy(
        (manifest.assembly_config or {}).get("audio_policy")
    )


def _resolve_audio_preview_state(final_video, facts, policy) -> tuple[str, str, bool]:
    """``(state, reason, playable)`` from the artifact and its real streams.

    Existing artifact states decide the mapping, so this can never disagree
    with the frozen presentation contract:

    - AVAILABLE + audio stream present -> ``AVAILABLE``  (playable)
    - AVAILABLE + no audio stream      -> ``NOT_REQUIRED`` when the applied
      policy required nothing, ``NO_AUDIO`` when audio simply was not there,
      ``FAILED`` only when the policy REQUIRED audio that is absent
    - RECORDED BUT MISSING / FAILED    -> ``FAILED`` (the delivered bytes are
      not there to preview; stale facts are never echoed as availability)
    - NOT GENERATED                    -> ``INCOMPLETE``
    - unprobed                         -> ``UNKNOWN`` (never guessed)
    """
    artifact_state = (final_video or {}).get("state")
    has_audio = facts.get("has_audio")

    if artifact_state == "AVAILABLE":
        if has_audio is True:
            return (
                AUDIO_STATE_AVAILABLE,
                "The final artifact carries an audio stream: play it through "
                "media_url to hear exactly what the production delivered.",
                True,
            )
        if has_audio is False:
            if policy == AudioPolicy.NONE:
                return (
                    AUDIO_STATE_NOT_REQUIRED,
                    "This production intentionally carries no audio: the "
                    "applied audio policy is 'none'.",
                    False,
                )
            if facts.get("audio_required") is False:
                return (
                    AUDIO_STATE_NO_AUDIO,
                    "The final artifact carries no audio stream. This is not "
                    "an error: the applied policy did not require narrator "
                    "audio and no native scene audio was available.",
                    False,
                )
            if facts.get("audio_required") is True:
                return (
                    AUDIO_STATE_FAILED,
                    "The applied audio policy requires an audio stream, but "
                    "the final artifact carries none.",
                    False,
                )
            return (
                AUDIO_STATE_UNKNOWN,
                "The final artifact carries no audio stream, and no recorded "
                "evidence establishes whether audio was required. Nothing was "
                "inferred.",
                False,
            )
        if policy == AudioPolicy.NONE:
            # An applied policy of ``none`` already establishes that NO audio is
            # required, so the outcome is known from executed policy evidence
            # even when the stream itself cannot be inspected.  It is still not
            # "available"/"playable": nothing is claimed about the bytes.
            return (
                AUDIO_STATE_NOT_REQUIRED,
                "This production's applied audio policy is 'none' "
                "(intentionally audio-free); the artifact's stream could not be "
                "inspected, so nothing was claimed about the bytes.",
                False,
            )
        return (
            AUDIO_STATE_UNKNOWN,
            "The final artifact exists, but its audio stream could not be "
            "inspected (no recorded acceptance metadata and FFprobe is "
            "unavailable). Nothing was inferred.",
            False,
        )

    if artifact_state in ("FAILED", "RECORDED BUT MISSING"):
        return (
            AUDIO_STATE_FAILED,
            "The final video is not deliverable"
            + (
                ": the producing stage reported failure."
                if artifact_state == "FAILED"
                else ": the recorded artifact path is missing on disk."
            ),
            False,
        )

    return (
        AUDIO_STATE_INCOMPLETE,
        "No final video has been assembled for this unit yet, so there is no "
        "production audio to preview.",
        False,
    )


def unit_audio_payload(production_id: str, unit_id: str):
    """AUDIO PREVIEW: read-only truth about the finished unit's audio.

    Strictly a read: it never generates TTS, never executes or resumes a
    production, never repairs an artifact, never writes a manifest, and never
    calls a provider.  It reports the declared ``audio_intent`` alongside the
    EFFECTIVE policy and the ACTUAL delivered media, so a caller can always
    distinguish "what was asked for" from "what exists".
    """
    try:
        production = _production_or_none(production_id)
    except ValueError as exc:
        return 500, _intent_malformed(production_id, exc)
    if production is None:
        return 404, _production_not_found(production_id)
    unit = next(
        (item for item in production.units if item.unit_id == unit_id), None
    )
    if unit is None:
        return 400, {
            "error": "UNIT_NOT_FOUND",
            "production_id": production_id,
            "unit_id": unit_id,
            "message": f"unit {unit_id!r} is not part of {production_id}.",
        }
    try:
        bindings = _production_executor(production_id).discover_bindings(production)
    except Exception as exc:
        return 500, _executor_error(exc)
    bound = bindings.get(unit_id)

    payload = {
        "production_id": production_id,
        "unit_id": unit_id,
        "project_id": bound.directory_id if bound else None,
        # Declared intent (what was ASKED for), reported verbatim and never
        # used below to decide what the final file contains.
        "audio_intent": production.audio_intent,
        "audio_intent_declared": production.audio_intent is not None,
        "audio_intent_source": None,
        "effective_audio_policy": None,
        "narration_required": None,
        "audio_source": None,
        "state": AUDIO_STATE_INCOMPLETE,
        "available": False,
        "playable": False,
        "media_url": None,
        "media_content_type": None,
        "narration": None,
        "native_audio": None,
        "audio_stream": None,
        "final_video": None,
        "evidence": None,
        "reason": (
            "This unit has no bound execution project yet; nothing has been "
            "assembled, so there is no production audio to preview."
        ),
        "sample": False,
    }
    if bound is None:
        return 200, _redact_secrets(payload)

    manifest = bound.manifest
    final_video = _artifact_payload(manifest, bound.directory_id).get("final_video") or {}
    final_available = final_video.get("state") == "AVAILABLE"
    policy = _audio_policy_from_manifest(manifest)
    evidence = _audio_execution_evidence(manifest)
    facts = _final_audio_stream_facts(manifest, bound.project_dir, final_available)
    tracks = _final_audio_tracks(policy, facts, evidence)
    state, reason, playable = _resolve_audio_preview_state(final_video, facts, policy)
    if (
        tracks["native_unverified"]
        and not tracks["narration_delivered"]
        and facts.get("has_audio")
    ):
        # Audio is present, but nothing declared whether it is genuine scene
        # audio or AVM-generated silence.  Report the open question instead of
        # naming a source, and say what would settle it.
        reason = (
            "The final artifact carries an audio stream, but its synthetic "
            "identity was never declared for "
            + ", ".join(evidence["unverified_scenes"])
            + ", so native audio is not claimed.  Scenes completed after this "
            "evidence seam persist their declared audio facts, which resolves "
            "the question."
        )

    payload.update({
        "audio_intent_source": (manifest.assembly_config or {}).get(
            "audio_intent_source"
        ),
        "effective_audio_policy": policy.value,
        "narration_required": audio_policy_requires_narration(policy),
        "audio_source": _classify_final_audio_source(policy, facts, evidence),
        "state": state,
        "available": state == AUDIO_STATE_AVAILABLE,
        "playable": playable,
        # The EXISTING range-aware final-media route is the preview surface:
        # a browser plays the audio that is actually in the finished file.  No
        # separate audio file, extraction, or transcoding is invented.
        "media_url": (
            f"/api/projects/{bound.directory_id}/media" if playable else None
        ),
        "media_content_type": MIME[".mp4"] if playable else None,
        "narration": {
            # "present" answers the delivered artifact; "track_recorded" keeps
            # the execution fact visible without implying it reached the mix
            # (a narrator track is suppressed by a native-only policy).
            "present": tracks["narration_delivered"],
            "track_recorded": tracks["narration_exists"],
            "voice_stage_status": evidence["voice_stage_status"],
            "voice_provider": evidence["voice_provider"],
            "voice_audio_duration": evidence["voice_audio_duration"],
            "intentionally_skipped": evidence["voice_stage_status"] == "SKIPPED",
            "skip_reason": evidence["voice_skip_reason"],
        },
        "native_audio": {
            # Same distinction: scene media may carry real audio that the
            # applied policy deliberately excluded from the final file.
            "present": tracks["native_delivered"],
            "scene_audio_recorded": tracks["native_exists"],
            "scenes": evidence["native_scenes"],
            # Synthetic identity, exactly as persisted.  ``native`` and
            # ``synthetic`` are established answers; ``unknown`` means audio was
            # recorded but no provider declared what it is (historical scenes);
            # ``absent`` means no audio evidence exists for any scene.  A known
            # synthetic stream is never reported as native audio, and an unknown
            # one is never silently promoted to either answer.
            "synthetic_identity": evidence["synthetic_identity"],
            "established_native_scenes": evidence["established_native_scenes"],
            "established_synthetic_scenes": evidence["established_synthetic_scenes"],
            "unverified_scenes": evidence["unverified_scenes"],
        },
        "audio_stream": facts,
        "final_video": {
            "state": final_video.get("state"),
            "available": final_available,
            "reason": final_video.get("reason"),
        },
        "evidence": {"source": evidence["evidence_source"]},
        "reason": reason,
    })
    return 200, _redact_secrets(payload)


# ---------------------------------------------------------------------------
# G5 — plan / script CONTENT read projection (strictly read-only)
# ---------------------------------------------------------------------------

#: The ONLY creative fields this surface returns.  Everything else the
#: authoritative plan model carries — plan/project identity, ``metadata``
#: (planner provenance: provider, model, generated_at), and per-scene
#: production hints (transition, on_screen_text, visual_style,
#: camera_direction, sound_design, visual_source) — stays server-side.
#: Planner provenance, route-compilation evidence, scene provenance and
#: accounting each already have their own surfaces and are never mixed into
#: creative plan content.
PLAN_CREATIVE_FIELDS = (
    "title",
    "hook",
    "script",
    "call_to_action",
    "estimated_duration",
)
PLAN_SCENE_CREATIVE_FIELDS = (
    "id",
    "sequence",
    "narration",
    "visual_prompt",
    "estimated_duration",
    "purpose",
)

#: Reuses the existing artifact-state vocabulary (``_artifact_state``).
PLAN_STATE_AVAILABLE = "AVAILABLE"
PLAN_STATE_NOT_GENERATED = "NOT_GENERATED"


def _plan_not_generated(production_id: str, unit_id: str) -> dict:
    """Honest absence: the plan is produced by execution, never by a GET."""
    return {
        "production_id": production_id,
        "unit_id": unit_id,
        "available": False,
        "state": PLAN_STATE_NOT_GENERATED,
        "plan": None,
        "reason": (
            "No plan has been generated for this unit yet. Plans are produced "
            "by execution; this read never generates, repairs, or backfills "
            "one."
        ),
        "sample": False,
    }


def _plan_evidence_invalid(production_id: str, unit_id: str, reason) -> dict:
    """Structured, path-free refusal to serve untrusted plan bytes."""
    return {
        "error": "PLAN_EVIDENCE_INVALID",
        "production_id": production_id,
        "unit_id": unit_id,
        "available": False,
        "message": (
            "The pinned unit plan could not be served as trusted content: "
            f"{_browser_safe_diagnostic(reason)}. Core never regenerates, "
            "repairs, or backfills a plan on a read; the durable plan "
            "artifacts and the production binding remain the source of truth."
        ),
    }


def _plan_creative_content(plan) -> dict:
    """Explicit allow-list projection of the plan's creative content."""
    return {
        "title": plan.title,
        "hook": plan.hook,
        "script": plan.script,
        "call_to_action": plan.call_to_action,
        "estimated_duration": plan.estimated_duration,
        "scenes": [
            {
                "id": scene.id,
                "sequence": scene.sequence,
                "narration": scene.narration,
                "visual_prompt": scene.visual_prompt,
                "estimated_duration": scene.estimated_duration,
                "purpose": scene.purpose,
            }
            for scene in plan.scenes
        ],
    }


def unit_plan_payload(production_id: str, unit_id: str):
    """Read-only GET projection of one unit's authoritative creative plan.

    The plan/script content is ALREADY durable and hash-pinned; this endpoint
    only PROJECTS it.  No persistence, no manifest, and no planning behavior is
    touched here.

    Truth rules, all reused from existing Core semantics:

    - ONE authoritative source for a production unit: the pinned plan artifact
      ``productions/<production_id>/units/<unit_id>/plan/production_plan.json``
      written by ``ProductionExecutor.resolve_plan``.
    - A bound unit runs the existing binding authority FIRST
      (``ProductionExecutor.discover_bindings``), which re-hashes the pinned
      bytes against ``ProjectManifest.production_binding`` and compares plan
      identity/versions; any contradiction raises there and is reported
      truthfully, never served as content.
    - The bytes must parse and validate as the existing ``ProductionPlan``
      model, must hash (``compute_content_hash`` — the same SHA-256 helper the
      executor uses) to the hash recorded in ``plan_meta.json``, and the plan's
      own identity must equal the recorded ``plan_identity``.  The artifact's
      production/unit identity must equal what was requested.
    - Missing plan = honest ``NOT_GENERATED`` with ``available: false``,
      because plans are created by execution.  Content is never fabricated
      from production intent.
    - Strictly non-mutating: no writes, no regeneration, no repair, no
      backfill, no timestamp updates.
    """
    try:
        production = _production_or_none(production_id)
    except ValueError as exc:
        return 500, _intent_malformed(production_id, exc)
    if production is None:
        return 404, _production_not_found(production_id)
    unit = next(
        (item for item in production.units if item.unit_id == unit_id), None
    )
    if unit is None:
        return 400, {
            "error": "UNIT_NOT_FOUND",
            "production_id": production_id,
            "unit_id": unit_id,
            "message": f"unit {unit_id!r} is not part of {production_id}.",
        }

    from app.orchestrator.production_execution import META_FILE, PLAN_FILE
    from app.production.storage import compute_content_hash, unit_plan_dir

    # Existing binding authority (read-only): for a bound unit this is what
    # proves the pinned bytes are the bytes the bound execution actually ran.
    try:
        bindings = _production_executor(production_id).discover_bindings(
            production
        )
    except Exception as exc:
        return 500, _executor_error(exc)
    bound = bindings.get(unit_id)

    directory = unit_plan_dir(production_id, unit_id, root=PRODUCTIONS_DIR)
    plan_path = directory / PLAN_FILE
    meta_path = directory / META_FILE
    if not plan_path.is_file() and not meta_path.is_file():
        return 200, _plan_not_generated(production_id, unit_id)
    if not plan_path.is_file() or not meta_path.is_file():
        return 500, _plan_evidence_invalid(
            production_id, unit_id, "partial unit plan artifacts"
        )

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return 500, _plan_evidence_invalid(
            production_id, unit_id,
            f"malformed plan metadata ({type(exc).__name__})",
        )
    if not isinstance(meta, dict):
        return 500, _plan_evidence_invalid(
            production_id, unit_id, "plan metadata is not an object"
        )

    # The artifact must belong to exactly the unit that was asked for.
    if (
        meta.get("production_id") != production_id
        or meta.get("unit_id") != unit_id
    ):
        return 500, _plan_evidence_invalid(
            production_id, unit_id, "plan metadata identity mismatch"
        )

    # The served bytes must be the bytes the recorded hash vouches for.
    recorded_hash = meta.get("content_hash")
    if not recorded_hash:
        return 500, _plan_evidence_invalid(
            production_id, unit_id, "plan metadata records no content hash"
        )
    try:
        actual_hash = compute_content_hash(plan_path)
    except OSError as exc:
        return 500, _plan_evidence_invalid(
            production_id, unit_id,
            f"plan artifact unreadable ({type(exc).__name__})",
        )
    if actual_hash != recorded_hash:
        return 500, _plan_evidence_invalid(
            production_id, unit_id,
            "plan content hash does not match plan metadata",
        )

    # Validate as the real plan model; malformed content is never projected.
    from app.content.models import ProductionPlan

    try:
        plan = ProductionPlan.model_validate(
            json.loads(plan_path.read_text(encoding="utf-8-sig"))
        )
    except Exception as exc:
        return 500, _plan_evidence_invalid(
            production_id, unit_id,
            f"invalid production plan ({type(exc).__name__})",
        )

    if meta.get("plan_identity") and plan.project_id != meta.get("plan_identity"):
        return 500, _plan_evidence_invalid(
            production_id, unit_id, "plan identity does not match plan metadata"
        )
    if bound is not None:
        # Defensive belt-and-braces: discover_bindings above already fails
        # loudly on any binding contradiction, so this can only fire on a
        # future divergence. Either way, an unbacked plan is never served.
        binding = bound.manifest.production_binding
        if (
            binding is None
            or plan.project_id != binding.unit_plan_identity
            or actual_hash != binding.unit_plan_content_hash
            or int(meta.get("production_plan_version") or 0)
            != binding.production_plan_version
        ):
            return 500, _plan_evidence_invalid(
                production_id, unit_id, "plan does not match the production binding"
            )

    return 200, _redact_secrets({
        "production_id": production_id,
        "unit_id": unit_id,
        "available": True,
        "state": PLAN_STATE_AVAILABLE,
        "plan": _plan_creative_content(plan),
        "sample": False,
    })


def production_command_payload(
    production_id: str, unit_id, action: str, body: dict
):
    """Execute/Resume production or unit — delegating ONLY to Phase 10.

    ``action`` is presentation terminology ("execute"/"resume"); both map to
    ``ProductionExecutor.execute()``, whose re-entrant semantics never restart
    COMPLETED units and respect remote-job protection.
    """
    try:
        production = _production_or_none(production_id)
    except ValueError as exc:
        return 500, _intent_malformed(production_id, exc)
    if production is None:
        return 404, _production_not_found(production_id)
    if not body.get("confirm"):
        return 400, _confirmation_required(
            f"POST /api/productions/{production_id}/{action}"
        )
    try:
        report = _derive_progress(production_id)
    except Exception as exc:
        return 500, _executor_error(exc)
    progress_by_id = {item.unit_id: item for item in report.units}

    current_state = None
    if unit_id:
        if unit_id not in progress_by_id:
            return 400, {
                "error": "UNIT_NOT_FOUND",
                "production_id": production_id,
                "unit_id": unit_id,
                "message": f"unit {unit_id!r} is not part of {production_id}.",
            }
        progress = progress_by_id[unit_id]
        current_state = progress.state.value
        unit = next(
            (item for item in production.units if item.unit_id == unit_id)
        )
        if current_state == "BLOCKED":
            blocking = _blocking_units(
                unit,
                {key: _unit_progress_dict(value) for key, value in progress_by_id.items()},
            )
            return 400, {
                "error": "BLOCKED_DEPENDENCY",
                "production_id": production_id,
                "unit_id": unit_id,
                "blocking_units": blocking,
                "message": (
                    f"Cannot execute {unit_id}: dependencies "
                    f"{', '.join(blocking)} are not COMPLETED. Complete them "
                    "first (Phase 10 enforces dependencies)."
                ),
            }
        if current_state == "FAILED":
            # F-01 (audit): ProductionExecutor.execute() never treats FAILED
            # as an actionable candidate (frozen Phase 10 semantics), so a
            # 202 "executing" here would be a lie. Refuse truthfully BEFORE
            # any worker, registry entry, or executor call exists, and point
            # only at recovery mechanisms that already exist.
            bound_project_id = progress.project_id
            return 409, {
                "error": "FAILED_UNIT_REQUIRES_EXPLICIT_RECOVERY",
                "production_id": production_id,
                "unit_id": unit_id,
                "state": "FAILED",
                "bound_project_id": bound_project_id,
                "message": (
                    f"Unit {unit_id} is FAILED. The Phase 10 "
                    "ProductionExecutor does not automatically resume FAILED "
                    "units, so nothing was executed, no worker was started, "
                    "and the production intent was not modified. Phase 11 "
                    "deliberately adds no retry, skip, or fallback. Existing "
                    "recovery options: (1) inspect this unit's failure "
                    "details, then resume the bound project through the "
                    "existing engine recovery path (python produce.py "
                    "--resume <project_id>"
                    + (
                        f"; currently bound project: {bound_project_id}"
                        if bound_project_id
                        else "; no project is bound to this unit yet"
                    )
                    + "), or (2) issue a revised Phase 9 intent."
                ),
            }
        if current_state == "COMPLETED":
            return 200, {
                "status": "complete",
                "production_id": production_id,
                "unit_id": unit_id,
                "message": "Unit is already COMPLETED; Phase 10 never "
                           "re-executes completed units. Nothing was done.",
            }

    started_at = datetime.now().isoformat()
    with _production_runs_lock:
        run = _production_runs.get(production_id)
        if run and run.get("running"):
            return 409, {
                "error": "EXECUTION_ALREADY_RUNNING",
                "production_id": production_id,
                "message": "An execution command for this production is "
                           "already in progress. The Phase 10 executor "
                           "continues from durable state; poll progress or "
                           "wait for it to finish. Nothing was re-submitted.",
            }
        _production_runs[production_id] = {
            "running": True,
            "action": action,
            "unit_id": unit_id,
            "started_at": started_at,
            "finished_at": None,
            "complete": False,
            "error": None,
        }

    def worker():
        try:
            _production_executor(production_id).execute(unit_id=unit_id)
            with _production_runs_lock:
                # F-09 (audit): successful runs carry no display value (the
                # run badge only renders "executing" or "last run failed"),
                # so evict them to keep the process-lifetime registry small.
                # FAILED runs are retained for truthful error display.
                _production_runs.pop(production_id, None)
        except Exception as exc:
            logger.exception("Production execution failed: %s", production_id)
            with _production_runs_lock:
                _production_runs[production_id].update({
                    "running": False,
                    "finished_at": datetime.now().isoformat(),
                    "error": f"{type(exc).__name__}: {_browser_safe_diagnostic(exc)}",
                })

    thread = threading.Thread(target=worker, daemon=True)
    try:
        thread.start()
    except Exception as exc:  # noqa: BLE001 - the worker thread never existed
        # Phase 2B correctness guard: if the OS/runtime refuses the thread
        # spawn, the just-registered "running" entry must NOT survive — an
        # uncleaned entry would pin every later execute/resume to a false
        # 409 EXECUTION_ALREADY_RUNNING until the process restarts.  The
        # failure surfaces through the existing 500 EXECUTOR_ERROR contract;
        # nothing was executed.
        logger.exception("Production execution worker could not start: %s", production_id)
        with _production_runs_lock:
            entry = _production_runs.get(production_id)
            if entry is not None and entry.get("started_at") == started_at:
                _production_runs.pop(production_id, None)
        return 500, _executor_error(exc)
    scope = f"unit {unit_id}" if unit_id else f"production {production_id}"
    message = (
        f"{action.capitalize()} command accepted for {scope}. The Phase 10 "
        "ProductionExecutor is running in the background; COMPLETED units are "
        "never restarted and remote in-flight jobs are never re-submitted. "
        f"Poll GET /api/productions/{production_id}/progress for live state."
    )
    return 202, _redact_secrets({
        "status": "executing",
        "production_id": production_id,
        "unit_id": unit_id,
        "current_state": current_state,
        "message": message,
    })


def _summary_handoff_required(production_id: str, unit_id: str):
    """A standalone Track 9 artifact is not an execution-project binding."""
    return 422, {
        "error": "SUMMARY_HANDOFF_REQUIRED",
        "production_id": production_id, "unit_id": unit_id,
        "message": (
            "Promote the assembled narrated summary first: POST "
            f"/api/productions/{production_id}/units/{unit_id}/summary-video/promote. "
            "This explicitly binds a canonical PROJECT_COMPLETE project. "
            "Then repeat this confirmed publish request; promotion itself never uploads."
        ),
        "inspection_url": f"/api/productions/{production_id}/units/{unit_id}/summary-video",
        "promote_url": f"/api/productions/{production_id}/units/{unit_id}/summary-video/promote",
    }


def _unit_has_summary(production_id: str, unit_id: str) -> bool:
    from app.production.summary_video import summary_video_output_path

    path = summary_video_output_path(production_id, unit_id, root=PRODUCTIONS_DIR)
    try:
        path.resolve().relative_to(PRODUCTIONS_DIR.resolve())
        return path.is_file()
    except (OSError, ValueError):
        return False


def unit_publish_payload(production_id: str, unit_id: str, body: dict):
    """Sanitize every unit-publishing response, including early refusals."""
    status, payload = _unit_publish_payload(production_id, unit_id, body)
    return status, _sanitize_publishing_payload(payload)


def _unit_publish_payload(production_id: str, unit_id: str, body: dict):
    """One confirmed unit, explicit destinations, existing platform bridges only."""
    try:
        production = _production_or_none(production_id)
    except ValueError as exc:
        return 500, _intent_malformed(production_id, exc)
    if production is None:
        return 404, _production_not_found(production_id)
    if not isinstance(body, dict) or body.get("confirm") is not True:
        return 400, _confirmation_required(
            f"POST /api/productions/{production_id}/units/{unit_id}/publish"
        )
    if not any(unit.unit_id == unit_id for unit in production.units):
        return 400, {"error": "UNIT_NOT_FOUND", "production_id": production_id, "unit_id": unit_id}
    platforms = body.get("platforms", ["tiktok"])
    if (not isinstance(platforms, list) or not platforms
            or any(not isinstance(item, str) for item in platforms)
            or len(set(platforms)) != len(platforms)):
        return 400, {"error": "INVALID_PUBLISH_PLATFORMS", "message": "Choose a non-empty list of distinct platforms."}
    if any(item not in ("tiktok", "youtube") for item in platforms):
        return 400, {
            "error": "PUBLISH_PLATFORM_NOT_SUPPORTED", "production_id": production_id,
            "unit_id": unit_id, "platforms": platforms,
            "message": "Only the existing manual YouTube and TikTok bridges are supported.",
        }
    has_summary = _unit_has_summary(production_id, unit_id)
    summary_binding = None
    if has_summary:
        try:
            summary_binding = _production_executor(production_id).discover_bindings(production).get(unit_id)
        except Exception as exc:
            return 409, {"error": "BOUND_PROJECT_CONFLICT", "message": _browser_safe_diagnostic(exc)}
    try:
        report = _derive_progress(production_id)
    except Exception as exc:
        if has_summary and summary_binding is None:
            return _summary_handoff_required(production_id, unit_id)
        return 500, _executor_error(exc)
    progress_by_id = {item.unit_id: item for item in report.units}
    if unit_id not in progress_by_id:
        return 400, {
            "error": "UNIT_NOT_FOUND",
            "production_id": production_id,
            "unit_id": unit_id,
            "message": f"unit {unit_id!r} is not part of {production_id}.",
        }
    progress = progress_by_id[unit_id]
    if progress.state.value != "COMPLETED":
        if has_summary and summary_binding is None:
            return _summary_handoff_required(production_id, unit_id)
        if summary_binding is not None and summary_binding.manifest.source_summary_handoff is not None:
            return 422, {
                "error": "SUMMARY_HANDOFF_INVALID", "production_id": production_id,
                "unit_id": unit_id, "project_id": summary_binding.directory_id,
                "message": (
                    "The promoted summary's canonical project no longer passes acceptance. "
                    "Restore its accepted evidence or explicitly rebuild/promote the summary. "
                    "Existing publication records are protected; do not resume it through scene generation."
                ),
            }
        return 400, {
            "error": "UNIT_NOT_COMPLETED",
            "production_id": production_id,
            "unit_id": unit_id,
            "state": progress.state.value,
            "message": (
                "Publish Unit requires a COMPLETED unit; this unit is "
                f"{progress.state.value}. Resume the unit and let the Phase 10 "
                "executor finish it before publishing."
            ),
        }
    project_id = progress.project_id
    if not project_id:
        if has_summary:
            return _summary_handoff_required(production_id, unit_id)
        return 400, {
            "error": "UNIT_NOT_BOUND",
            "production_id": production_id,
            "unit_id": unit_id,
            "message": "Unit is COMPLETED but has no bound execution project.",
        }

    results = {}
    for platform in platforms:
        if platform == "youtube":
            status, result = youtube_publish_now(project_id, privacy_status=body.get("privacy_status"))
        else:
            status, result = tiktok_publish_now(project_id, privacy_level=body.get("privacy_level"))
        results[platform] = {
            "http_status": status, "outcome": _publication_outcome(result, status), "result": result,
        }
    # This is only response aggregation; each existing state machine still
    # owns its independent durable outcome. No retry, scheduler or queue.
    outcomes = {entry["outcome"] for entry in results.values()}
    outcome = next((value for value in (
        "unknown_reconciliation_required", "failed", "not_started", "processing",
        "verified_sandbox_private", "verified",
    ) if value in outcomes), "not_started")
    status = next((entry["http_status"] for entry in results.values() if entry["http_status"] != 200), 200)
    result = next(iter(results.values()))["result"] if len(results) == 1 else {
        platform: entry["result"] for platform, entry in results.items()
    }
    payload = {
        # Preserve the legacy verified-success label, never use it for 200/PROCESSING.
        "status": "published" if outcome == "verified" else outcome,
        "outcome": outcome,
        "production_id": production_id,
        "unit_id": unit_id,
        "project_id": project_id,
        "platforms": platforms,
        "result": result,
        "results": results,
        "message": (
            "Delegated to the existing publishing flow; the durable "
            "publication state machine owns all outcomes."
        ),
    }
    return status, payload


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Track 9 -- thin HTTP adapters (no logic duplication)
# ---------------------------------------------------------------------------

def _track9_check_unit(production_id: str, unit_id: str):
    try:
        from app.production.storage import load_production
        prod = load_production(production_id, root=PRODUCTIONS_DIR)
    except FileNotFoundError:
        return None, (404, {"error": "PRODUCTION_NOT_FOUND", "production_id": production_id})
    except Exception as exc:
        return None, (500, {"error": "LOAD_FAILED", "message": f"{exc.__class__.__name__}: {_browser_safe_diagnostic(exc)}"})
    unit = next((u for u in (prod.units or []) if u.unit_id == unit_id), None)
    if unit is None:
        return None, (400, {"error": "UNIT_NOT_FOUND", "production_id": production_id, "unit_id": unit_id})
    return prod, None


def narration_get_payload(production_id: str, unit_id: str):
    _, err = _track9_check_unit(production_id, unit_id)
    if err:
        return err
    try:
        from app.production.storage import load_narration_artifact
        artifact = load_narration_artifact(production_id, unit_id, root=PRODUCTIONS_DIR)
    except FileNotFoundError:
        return 404, {"error": "NARRATION_NOT_FOUND", "production_id": production_id, "unit_id": unit_id, "message": "Narration has not been generated for this unit yet."}
    except Exception as exc:
        return 500, {"error": "LOAD_FAILED", "message": f"{exc.__class__.__name__}: {_browser_safe_diagnostic(exc)}"}
    return 200, artifact.model_dump()


def _track9_mock_mode(body: dict) -> bool:
    if not isinstance(body, dict):
        raise ValueError("Track 9 commands require a JSON object")
    value = body.get("mock_mode", False)
    if not isinstance(value, bool):
        raise ValueError("mock_mode must be a JSON boolean")
    return value


def transcribe_payload(production_id: str, body: dict, asset_id=None):
    """Both transcript routes share explicit, validated sandbox admission."""
    try:
        mock_mode = _track9_mock_mode(body)
    except ValueError as exc:
        return 400, {"error": "INVALID_REQUEST", "message": str(exc)}
    provider = body.get("provider")
    if provider is not None and (not isinstance(provider, str) or "\x00" in provider):
        return 400, {"error": "INVALID_REQUEST", "message": "provider must be text without null bytes"}
    if asset_id is None:
        asset_id = body.get("asset_id")
        if asset_id is None:
            try:
                from app.production.storage import load_production
                production = load_production(production_id, root=PRODUCTIONS_DIR)
            except FileNotFoundError:
                return 404, {"error": "PRODUCTION_NOT_FOUND", "message": "Select a valid production first."}
            except Exception as exc:
                return 500, {"error": "LOAD_FAILED", "message": _browser_safe_diagnostic(exc)}
            assets = list(production.source_assets or [])
            if not assets:
                return 404, {"error": "SOURCE_NOT_FOUND", "message": "Register a source first."}
            asset_id = assets[-1].asset_id
    from app.transcription.transcribe import transcribe_source
    return transcribe_source(production_id, asset_id, root=PRODUCTIONS_DIR, provider=provider, mock_mode=mock_mode)


def narration_build_payload(production_id: str, unit_id: str, body: dict):
    try:
        mock_mode = _track9_mock_mode(body)
    except ValueError as exc:
        return 400, {"error": "INVALID_REQUEST", "message": str(exc)}
    prod, err = _track9_check_unit(production_id, unit_id)
    if err:
        return err
    if not isinstance(body, dict):
        body = {}
    summary_mode = str(body.get("summary_mode") or "standard").strip().lower()
    if summary_mode not in ("standard", "pro"):
        return 400, {"error": "INVALID_REQUEST", "message": "summary_mode must be standard or pro"}
    sel = body.get("selection_mode")
    if sel is not None:
        if not isinstance(sel, str) or not sel.strip():
            return 400, {"error": "INVALID_REQUEST", "message": "selection_mode must be text"}
        sel = sel.strip().lower()
        if sel not in ("auto", "explicit", "manual"):
            return 400, {"error": "INVALID_REQUEST", "message": "selection_mode must be auto or explicit/manual"}
    explicit = None
    for k in ("summary_understanding_model_id", "understanding_model_id", "preferred_model_id"):
        v = body.get(k)
        if isinstance(v, str) and v.strip():
            explicit = v.strip()
            break
        if v is not None and isinstance(v, str) and not v.strip():
            return 400, {"error": "INVALID_REQUEST", "message": "model id must be non-empty text"}
        if v is not None and not isinstance(v, str):
            return 400, {"error": "INVALID_REQUEST", "message": "model id must be text"}
    if sel in ("explicit", "manual") and not explicit:
        return 400, {"error": "INVALID_REQUEST", "message": "explicit selection_mode requires a model id"}
    raw_dur = body.get("target_duration_seconds")
    tds = None
    if raw_dur is not None:
        try:
            if isinstance(raw_dur, bool):
                raise ValueError
            tds = float(raw_dur)
        except Exception:
            return 400, {"error": "INVALID_REQUEST", "message": "target_duration_seconds must be a number"}
        if not tds > 0:
            return 400, {"error": "INVALID_REQUEST", "message": "target_duration_seconds must be positive"}
    aid = body.get("asset_id")
    if aid is not None:
        if not isinstance(aid, str) or not aid.strip():
            return 400, {"error": "INVALID_REQUEST", "message": "asset_id must be text src-NNN"}
        if not re.match(r"^src-\d{3}$", aid.strip()):
            return 400, {"error": "INVALID_REQUEST", "message": "asset_id must match src-NNN"}
        aid = aid.strip()
    assets = list(prod.source_assets or [])
    sa = None
    if aid is not None:
        sa = next((a for a in assets if a.asset_id == aid), None)
        if sa is None:
            return 404, {"error": "SOURCE_NOT_FOUND", "production_id": production_id, "asset_id": aid}
    else:
        if assets:
            sa = assets[-1]
    if sa is None:
        return 404, {"error": "SOURCE_NOT_FOUND", "production_id": production_id, "message": "No source asset ingested; register a source first."}
    if tds is None:
        # Default must not be implausible for the actual source duration
        tds = min(30.0, float(sa.duration) * 0.6) if float(sa.duration) > 0 else 30.0
    try:
        from app.transcription.transcribe import load_transcript as _lt
        tr = _lt(production_id, sa.asset_id, root=PRODUCTIONS_DIR, mock_mode=mock_mode)
    except FileNotFoundError:
        return 404, {"error": "TRANSCRIPT_NOT_FOUND", "production_id": production_id, "asset_id": sa.asset_id, "message": "Transcribe the source first."}
    except Exception as exc:
        return 500, {"error": "LOAD_FAILED", "message": f"{exc.__class__.__name__}: {_browser_safe_diagnostic(exc)}"}
    if explicit is not None:
        try:
            from app.content.model_registry import get_model as _gm
            m = _gm(explicit)
            if summary_mode == "pro" and not m.capabilities.is_multimodal_capable():
                return 422, {"error": "MODEL_NOT_ELIGIBLE", "message": f"Model {explicit!r} is not multimodal-capable"}
        except KeyError:
            return 404, {"error": "MODEL_NOT_FOUND", "message": f"Unknown canonical model {explicit!r}"}
    try:
        from app.production.narration import build_grounded_narration_plan
        art = build_grounded_narration_plan(production_id, unit_id, sa, tr, target_duration_seconds=float(tds), canonical_model_id=explicit, summary_understanding_model_id=explicit, summary_understanding_mode=sel, summary_mode=summary_mode, mock_mode=mock_mode, root=PRODUCTIONS_DIR)
        art.source_transcript_path = tr.transcript_path
        from app.production.storage import save_narration_artifact
        save_narration_artifact(art, production_id=production_id, unit_id=unit_id, root=PRODUCTIONS_DIR, source_asset=sa, source_transcript=tr)
    except FileNotFoundError as exc:
        return 404, {"error": "SOURCE_NOT_FOUND", "message": f"{exc.__class__.__name__}: {_browser_safe_diagnostic(exc)}"}
    except ValueError as exc:
        msg=str(exc); lo=msg.lower()
        if "unknown canonical" in lo or "unknown summary" in lo or "unknown selection_mode" in lo:
            return 400, {"error": "INVALID_REQUEST", "message": msg}
        if "not multimodal" in lo or "cannot satisfy" in lo:
            return 422, {"error": "MODEL_NOT_ELIGIBLE", "message": msg}
        return 400, {"error": "INVALID_REQUEST", "message": msg}
    except KeyError as exc:
        return 404, {"error": "MODEL_NOT_FOUND", "message": str(exc)}
    except RuntimeError as exc:
        return 422, {"error": "MODEL_NOT_ELIGIBLE", "message": str(exc)}
    except Exception as exc:
        return 500, {"error": "NARRATION_BUILD_FAILED", "message": f"{exc.__class__.__name__}: {_browser_safe_diagnostic(exc)}"}
    d=art.model_dump(); d["media_url"]=f"/api/productions/{production_id}/units/{unit_id}/narration"; return 201, d


def narration_synthesize_payload(production_id: str, unit_id: str, body: dict):
    try:
        mock_mode = _track9_mock_mode(body)
    except ValueError as exc:
        return 400, {"error": "INVALID_REQUEST", "message": str(exc)}
    _, err=_track9_check_unit(production_id, unit_id)
    if err:
        return err
    try:
        from app.production.storage import load_narration_artifact
        art=load_narration_artifact(production_id, unit_id, root=PRODUCTIONS_DIR)
    except FileNotFoundError:
        return 404, {"error": "NARRATION_NOT_FOUND", "production_id": production_id, "unit_id": unit_id}
    except Exception as exc:
        return 500, {"error": "LOAD_FAILED", "message": f"{exc.__class__.__name__}: {_browser_safe_diagnostic(exc)}"}
    pn=body.get("provider_name")
    if pn is not None:
        if not isinstance(pn, str) or not pn.strip():
            return 400, {"error": "INVALID_REQUEST", "message": "provider_name must be text"}
        pn=pn.strip().lower()
    voice=body.get("voice")
    if voice is not None:
        if not isinstance(voice, str) or not voice.strip():
            return 400, {"error": "INVALID_REQUEST", "message": "voice must be text"}
        voice=voice.strip()
    fmt=body.get("format")
    if fmt is not None:
        if not isinstance(fmt, str) or not fmt.strip():
            return 400, {"error": "INVALID_REQUEST", "message": "format must be mp3 or wav"}
        fmt=fmt.strip().lower().lstrip(".")
        if fmt not in ("mp3", "wav"):
            return 400, {"error": "INVALID_REQUEST", "message": "format must be mp3 or wav"}
    else:
        fmt="mp3"
    if mock_mode:
        pn="mock"
    try:
        from app.production.narration import synthesize_narration_audio
        meta=synthesize_narration_audio(art, root=PRODUCTIONS_DIR, provider_name=pn, voice=voice, output_format=fmt, mock_mode=mock_mode)
    except Exception as exc:
        from app.voice.exceptions import VoiceProviderError
        if isinstance(exc, VoiceProviderError):
            return 503, {"error": "TTS_PROVIDER_UNAVAILABLE", "message": _browser_safe_diagnostic(exc) or str(exc)}
        if isinstance(exc, ValueError) and "validation" in str(exc).lower():
            return 500, {"error": "AUDIO_VALIDATION_FAILED", "message": _browser_safe_diagnostic(exc) or str(exc)}
        return 500, {"error": "SYNTHESIS_FAILED", "message": f"{exc.__class__.__name__}: {_browser_safe_diagnostic(exc)}"}
    try:
        from app.production.storage import load_narration_artifact as _l2
        upd=_l2(production_id, unit_id, root=PRODUCTIONS_DIR)
        payload=upd.model_dump()
    except Exception:
        payload=art.model_dump()
    payload["audio"]=meta.model_dump(); payload["media_url"]=f"/api/productions/{production_id}/units/{unit_id}/narration"; return 200, payload


def _summary_video_status(production_id, unit_id, *, bound=None, inspect_binding=True):
    """Read-only promotion evidence; no media generation, upload or repair."""
    from app.production.storage import compute_content_hash
    from app.production.summary_video import load_narrated_summary, summary_video_output_path

    data = {
        "production_id": production_id, "unit_id": unit_id, "rendered": False,
        "promoted": False, "promotion_required": False, "promotion_error": None,
        "bound_project_id": None, "handoff_kind": None, "media_matches_promoted": None,
        "promote_url": f"/api/productions/{production_id}/units/{unit_id}/summary-video/promote",
    }
    if inspect_binding:
        try:
            bound = _production_executor(production_id).discover_bindings().get(unit_id)
        except Exception as exc:
            data.update({"promotion_error": "SUMMARY_HANDOFF_INVALID", "message": _browser_safe_diagnostic(exc)})
    # A canonical binding survives removal/rebuilding of mutable Track 9
    # sidecars. Absence of a preview must never erase the accepted lineage.
    if bound:
        data["bound_project_id"] = bound.directory_id
        handoff = bound.manifest.source_summary_handoff
        if handoff:
            data.update({"promoted": True, "handoff_kind": handoff.handoff_kind})
    try:
        summary = load_narrated_summary(production_id, unit_id, root=PRODUCTIONS_DIR)
        media = summary_video_output_path(production_id, unit_id, root=PRODUCTIONS_DIR)
        media.resolve().relative_to(PRODUCTIONS_DIR.resolve())
        data["rendered"] = (media.is_file() and media.stat().st_size > 0
                            and (summary.production_id, summary.unit_id) == (production_id, unit_id))
        if data["rendered"] and bound:
            if data["promoted"]:
                data["media_matches_promoted"] = compute_content_hash(media) == bound.manifest.source_summary_handoff.file_fingerprint
            else:
                data["promotion_error"] = "BOUND_PROJECT_CONFLICT"
        data["promotion_required"] = data["rendered"] and bound is None and data["promotion_error"] is None
    except FileNotFoundError:
        pass
    except Exception as exc:
        data.update({"promotion_error": "SUMMARY_HANDOFF_INVALID", "message": _browser_safe_diagnostic(exc)})
    return data


def summary_video_promote_payload(production_id: str, unit_id: str, body=None):
    try:
        _track9_mock_mode(body if body is not None else {})
    except ValueError as exc:
        return 400, {"error": "INVALID_REQUEST", "message": str(exc)}
    if body is not None and set(body) - {"mock_mode"}:
        return 400, {"error": "INVALID_REQUEST", "message": "Summary promotion accepts only mock_mode; never project retargeting or upload options."}
    _, err = _track9_check_unit(production_id, unit_id)
    if err:
        return err
    if not _publish_lock.acquire(blocking=False):
        return 409, {"error": "PUBLISH_ALREADY_RUNNING", "message": "A publishing operation is active. Inspect its durable outcome before promoting another master."}
    try:
        from app.production.summary_handoff import SummaryHandoffError, promote_narrated_summary_to_project

        # Serialize against web execution admission: a standard executor must
        # not initialize a competing binding while promotion accepts a master.
        with _production_runs_lock:
            if (_production_runs.get(production_id) or {}).get("running"):
                return 409, {"error": "EXECUTION_ALREADY_RUNNING", "message": "Wait for the active production execution before summary promotion."}
            try:
                result = promote_narrated_summary_to_project(
                    production_id, unit_id, root=PRODUCTIONS_DIR, projects_root=PROJECTS_DIR,
                )
            except SummaryHandoffError as exc:
                return exc.status, _sanitize_publishing_payload({
                    "error": exc.code, "production_id": production_id, "unit_id": unit_id,
                    "message": _browser_safe_diagnostic(exc),
                })
            except FileNotFoundError as exc:
                return 404, {"error": "SUMMARY_HANDOFF_ARTIFACT_NOT_FOUND", "message": _browser_safe_diagnostic(exc)}
            except ValueError as exc:
                return 422, {"error": "SUMMARY_HANDOFF_INVALID", "message": _browser_safe_diagnostic(exc)}
            except Exception as exc:
                return 500, {"error": "SUMMARY_PROMOTION_FAILED", "message": _browser_safe_diagnostic(exc)}
        return 201 if result.created else 200, _redact_secrets({
            "production_id": production_id, "unit_id": unit_id,
            "project_id": result.manifest.project_id, "promoted": True,
            "bound_project": _manifest_payload(result.manifest, result.project_dir.name),
            "summary_video": _summary_video_status(production_id, unit_id),
        })
    finally:
        _publish_lock.release()


def summary_video_build_payload(production_id: str, unit_id: str, body: dict):
    try:
        mock_mode = _track9_mock_mode(body)
    except ValueError as exc:
        return 400, {"error": "INVALID_REQUEST", "message": str(exc)}
    if not isinstance(body, dict) or not isinstance(body.get("promote_to_publishable", False), bool):
        return 400, {"error": "INVALID_REQUEST", "message": "promote_to_publishable must be a JSON boolean (default false)."}
    _, err=_track9_check_unit(production_id, unit_id)
    if err:
        return err
    try:
        from app.production.storage import load_narration_artifact
        narr=load_narration_artifact(production_id, unit_id, root=PRODUCTIONS_DIR)
    except FileNotFoundError:
        return 404, {"error": "NARRATION_NOT_FOUND", "production_id": production_id, "unit_id": unit_id, "message": "Build narration first."}
    except Exception as exc:
        return 500, {"error": "LOAD_FAILED", "message": f"{exc.__class__.__name__}: {_browser_safe_diagnostic(exc)}"}
    try:
        from app.production.storage import load_narration_audio as _la
        narr_audio=_la(production_id, unit_id, root=PRODUCTIONS_DIR)
    except FileNotFoundError:
        if not getattr(narr, "audio_artifact_path", None):
            return 404, {"error": "NARRATION_AUDIO_NOT_FOUND", "production_id": production_id, "unit_id": unit_id, "message": "Synthesize narration audio first."}
        narr_audio=None
    try:
        from app.production.summary_video import assemble_narrated_summary
        sv=assemble_narrated_summary(production_id, unit_id, root=PRODUCTIONS_DIR, narration_artifact=narr, narration_audio=narr_audio, mock_mode=mock_mode)
    except FileNotFoundError as exc:
        return 404, {"error": "SOURCE_NOT_FOUND", "message": f"{exc.__class__.__name__}: {_browser_safe_diagnostic(exc)}"}
    except ValueError as exc:
        return 400, {"error": "INVALID_GROUNDING", "message": _browser_safe_diagnostic(exc) or str(exc)}
    except RuntimeError as exc:
        msg=_browser_safe_diagnostic(exc) or str(exc)
        if "ffmpeg" in msg.lower() or "ffprobe" in msg.lower():
            return 503, {"error": "FFMPEG_UNAVAILABLE", "message": msg}
        return 500, {"error": "SUMMARY_ASSEMBLY_FAILED", "message": msg}
    except Exception as exc:
        return 500, {"error": "SUMMARY_ASSEMBLY_FAILED", "message": f"{exc.__class__.__name__}: {_browser_safe_diagnostic(exc)}"}
    data = sv.model_dump()
    data["media_url"] = f"/api/productions/{production_id}/units/{unit_id}/summary-video/media"
    if body.get("promote_to_publishable", False):
        status, promotion = summary_video_promote_payload(production_id, unit_id)
        if status not in {200, 201}:
            return status, {**promotion, "summary_video": {**data, **_summary_video_status(production_id, unit_id)}}
        data["bound_project"] = promotion["bound_project"]
    data.update(_summary_video_status(production_id, unit_id))
    return 201, data


def summary_video_get_payload(production_id: str, unit_id: str):
    _, err=_track9_check_unit(production_id, unit_id)
    if err:
        return err
    try:
        from app.production.summary_video import load_narrated_summary
        sv=load_narrated_summary(production_id, unit_id, root=PRODUCTIONS_DIR)
    except FileNotFoundError:
        return 404, {"error": "SUMMARY_VIDEO_NOT_FOUND", "production_id": production_id, "unit_id": unit_id}
    except Exception as exc:
        return 500, {"error": "LOAD_FAILED", "message": f"{exc.__class__.__name__}: {_browser_safe_diagnostic(exc)}"}
    data = sv.model_dump()
    data["media_url"] = f"/api/productions/{production_id}/units/{unit_id}/summary-video/media"
    data.update(_summary_video_status(production_id, unit_id))
    return 200, data


def _serve_summary_video_media(handler, production_id: str, unit_id: str):
    _, err=_track9_check_unit(production_id, unit_id)
    if err:
        s,p=err; return handler._json(s,p)
    try:
        from app.production.summary_video import summary_video_output_path, load_narrated_summary
        load_narrated_summary(production_id, unit_id, root=PRODUCTIONS_DIR)
        media=summary_video_output_path(production_id, unit_id, root=PRODUCTIONS_DIR)
    except FileNotFoundError:
        return handler._json(404, {"error": "SUMMARY_VIDEO_NOT_FOUND", "production_id": production_id, "unit_id": unit_id})
    except Exception as exc:
        return handler._json(500, {"error": "LOAD_FAILED", "message": f"{exc.__class__.__name__}: {_browser_safe_diagnostic(exc)}"})
    if not media.is_file():
        return handler._json(404, {"error": "SUMMARY_VIDEO_MEDIA_NOT_FOUND"})
    size=media.stat().st_size; rh=handler.headers.get("Range",""); s,e=0,size-1
    if rh:
        if not rh.startswith("bytes="):
            return handler._json(416, {"error": "INVALID_RANGE"})
        v=rh[6:].split(",",1)[0].strip(); parts=v.split("-",1)
        try:
            if len(parts)!=2 or (not parts[0] and not parts[1]): raise ValueError
            if parts[0]: s=int(parts[0]); e=int(parts[1]) if parts[1] else size-1
            else: sl=int(parts[1]); s=max(0,size-sl); e=size-1
            if s<0 or s>=size or e<s: raise ValueError
            e=min(e,size-1)
        except Exception:
            handler.send_response(416); handler.send_header("Content-Range", f"bytes */{size}"); handler.end_headers(); return
    ln=e-s+1
    handler.send_response(206 if rh else 200)
    handler.send_header("Content-Type", MIME.get(media.suffix.lower(), "video/mp4"))
    handler.send_header("Content-Length", str(ln))
    handler.send_header("Accept-Ranges", "bytes")
    if rh: handler.send_header("Content-Range", f"bytes {s}-{e}/{size}")
    handler.end_headers()
    with media.open("rb") as fh:
        fh.seek(s); rem=ln
        while rem>0:
            ch=fh.read(min(1024*256, rem))
            if not ch: break
            handler.wfile.write(ch); rem-=len(ch)


class AVMHandler(BaseHTTPRequestHandler):
    server_version = "AVMWeb/1.0"

    # -- helpers ----------------------------------------------------------
    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quiet console
        logger.debug("%s %s", self.address_string(), fmt % args)

    def _request_json_body(self) -> dict:
        """Best-effort JSON object body ({} when absent or malformed).

        Batch 1 hardening: refuse unbounded reads.  Creation and publish
        payloads are tiny; anything beyond 1 MB is rejected rather than
        read into memory.
        """
        max_body_bytes = 1_000_000
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > max_body_bytes:
                return {}
            raw = self.rfile.read(length) if length > 0 else b""
            if not raw:
                return {}
            body = json.loads(raw.decode("utf-8"))
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}

    def _query_param(self, name: str):
        values = parse_qs(urlparse(self.path).query).get(name) or []
        return values[0] if values else None

    # -- Phase 11 production routes (thin Command Center surface) ---------
    def _productions_get(self, path: str):
        match = re.match(r"^/api/productions/?$", path)
        if match:
            status, payload = productions_index_payload()
            return self._json(status, payload)
        match = re.match(r"^/api/productions/([^/]+)/progress/?$", path)
        if match:
            status, payload = production_progress_payload(match.group(1))
            return self._json(status, payload)
        match = re.match(r"^/api/productions/([^/]+)/units/([^/]+)/reconciliation/?$", path)
        if match:
            status, payload = unit_reconciliation_payload(
                match.group(1), match.group(2)
            )
            return self._json(status, payload)
        match = re.match(r"^/api/productions/([^/]+)/units/([^/]+)/plan/?$", path)
        if match:
            # G5: explicit, unit-level creative plan read. Plan bodies are
            # only ever read when THIS route is requested — never in the
            # production list/detail/progress paths (Phase 2B performance).
            status, payload = unit_plan_payload(match.group(1), match.group(2))
            return self._json(status, payload)
        # Operator-selected source segments (Track 8 V1).
        mseg = re.match(r"^/api/productions/([^/]+)/source/([^/]+)/segments/?$", path)
        if mseg:
            from app.production.segments import load_segments
            status, payload = load_segments(mseg.group(1), mseg.group(2), root=PRODUCTIONS_DIR)
            return self._json(status, payload)
        # Transcript GET (Track 8 V1)
        mtr = re.match(r"^/api/productions/([^/]+)/source/([^/]+)/transcript/?$", path)
        if mtr:
            from app.transcription.transcribe import load_transcript
            try:
                tr = load_transcript(mtr.group(1), mtr.group(2), root=PRODUCTIONS_DIR)
                return self._json(200, tr.model_dump())
            except FileNotFoundError:
                return self._json(404, {"error": "TRANSCRIPT_NOT_FOUND"})
            except Exception as e:
                return self._json(500, {"error": "LOAD_FAILED", "message": f"{e.__class__.__name__}"})
        # Track 9: summary-video metadata GET (must be before generic unit match)
        m = re.match(r"^/api/productions/([^/]+)/units/([^/]+)/summary-video/?$", path)
        if m:
            status, payload = summary_video_get_payload(m.group(1), m.group(2))
            return self._json(status, payload)
        m = re.match(r"^/api/productions/([^/]+)/units/([^/]+)/summary-video/media/?$", path)
        if m:
            return _serve_summary_video_media(self, m.group(1), m.group(2))
        # Track 9: narration GET (must be before generic unit match)
        m = re.match(r"^/api/productions/([^/]+)/units/([^/]+)/narration/?$", path)
        if m:
            status, payload = narration_get_payload(m.group(1), m.group(2))
            return self._json(status, payload)
        match = re.match(r"^/api/productions/([^/]+)/units/([^/]+)/audio/?$", path)
        if match:
            # AUDIO PREVIEW: an explicit, unit-scoped, read-only evidence read.
            # Like the plan and reconciliation reads it is never touched by the
            # list/progress paths, so no media inspection happens on a poll.
            status, payload = unit_audio_payload(match.group(1), match.group(2))
            return self._json(status, payload)
        match = re.match(r"^/api/productions/([^/]+)/units/([^/]+)/?$", path)
        if match:
            status, payload = unit_detail_payload(match.group(1), match.group(2))
            return self._json(status, payload)
        match = re.match(r"^/api/productions/([^/]+)/units/?$", path)
        if match:
            status, payload = production_units_payload(match.group(1))
            return self._json(status, payload)
        match = re.match(r"^/api/productions/([^/]+)/?$", path)
        if match:
            status, payload = production_detail_payload(match.group(1))
            return self._json(status, payload)
        return self._json(404, {"detail": "not found"})

    def _productions_post(self, path: str):
        upload = re.match(r"^/api/productions/([^/]+)/source/upload/?$", path)
        if upload:
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/octet-stream":
                return self._json(415, {"error": "INVALID_REQUEST", "message": "Source uploads require application/octet-stream."})
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                return self._json(400, {"error": "INVALID_REQUEST", "message": "Invalid upload Content-Length."})
            from app.production.source import register_uploaded_source

            timeout = self.connection.gettimeout()
            try:
                self.connection.settimeout(120)
                status, payload = register_uploaded_source(
                    upload.group(1), unquote(self.headers.get("X-AVM-Filename", "")),
                    self.rfile, length, root=PRODUCTIONS_DIR,
                )
            finally:
                self.connection.settimeout(timeout)
            return self._json(status, _redact_secrets(payload))
        m = re.match(r"^/api/productions/([^/]+)/source/?$", path)
        if m:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > 64 * 1024:
                return self._json(413, {"error": "PAYLOAD_TOO_LARGE"})
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                return self._json(400, {"error": "INVALID_JSON"})
            if not isinstance(body, dict):
                return self._json(400, {"error": "INVALID_REQUEST", "message": "Source intake requires a JSON object."})
            from app.production.source import register_source
            status, payload = register_source(m.group(1), body.get("source_path", ""), root=PRODUCTIONS_DIR,
                                              mock_mode=body.get("mock_mode", False))
            return self._json(status, payload)
        # Track 8 source segment save (operator-selected ranges only).
        mseg = re.match(r"^/api/productions/([^/]+)/source/([^/]+)/segments/?$", path)
        if mseg:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > 64 * 1024:
                return self._json(413, {"error": "PAYLOAD_TOO_LARGE"})
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                return self._json(400, {"error": "INVALID_JSON"})
            from app.production.segments import save_segments
            status, payload = save_segments(mseg.group(1), mseg.group(2), body, root=PRODUCTIONS_DIR)
            return self._json(status, payload)
        # Track 8 V1 transcript
        m2 = re.match(r"^/api/productions/([^/]+)/source/([^/]+)/transcript/?$", path)
        transcribe = re.match(r"^/api/productions/([^/]+)/transcribe/?$", path)
        if m2 or transcribe:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > 64 * 1024:
                return self._json(413, {"error": "PAYLOAD_TOO_LARGE"})
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}") if raw.strip() else {}
            except Exception:
                return self._json(400, {"error": "INVALID_JSON"})
            status, payload = transcribe_payload((m2 or transcribe).group(1), body, m2.group(2) if m2 else None)
            return self._json(status, payload)
        # Track 9: narration + summary-video POST routes
        m = re.match(r"^/api/productions/([^/]+)/units/([^/]+)/narration/synthesize/?$", path)
        if m:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > 64 * 1024:
                return self._json(413, {"error": "PAYLOAD_TOO_LARGE"})
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                return self._json(400, {"error": "INVALID_JSON"})
            status, payload = narration_synthesize_payload(m.group(1), m.group(2), body)
            return self._json(status, payload)
        m = re.match(r"^/api/productions/([^/]+)/units/([^/]+)/narration/?$", path)
        if m:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > 64 * 1024:
                return self._json(413, {"error": "PAYLOAD_TOO_LARGE"})
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                return self._json(400, {"error": "INVALID_JSON"})
            status, payload = narration_build_payload(m.group(1), m.group(2), body)
            return self._json(status, payload)
        m = re.match(r"^/api/productions/([^/]+)/units/([^/]+)/summary-video/promote/?$", path)
        if m:
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                return self._json(400, {"error": "INVALID_REQUEST", "message": "Invalid Content-Length."})
            if length < 0 or length > 64 * 1024:
                return self._json(413, {"error": "PAYLOAD_TOO_LARGE"})
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8") if length else "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._json(400, {"error": "INVALID_JSON"})
            if not isinstance(body, dict):
                return self._json(400, {"error": "INVALID_REQUEST", "message": "Summary promotion requires an empty JSON object."})
            status, payload = summary_video_promote_payload(
                m.group(1), m.group(2), body,
            )
            return self._json(status, payload)
        m = re.match(r"^/api/productions/([^/]+)/units/([^/]+)/summary-video/?$", path)
        if m:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > 64 * 1024:
                return self._json(413, {"error": "PAYLOAD_TOO_LARGE"})
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                return self._json(400, {"error": "INVALID_JSON"})
            status, payload = summary_video_build_payload(m.group(1), m.group(2), body)
            return self._json(status, payload)
        if re.match(r"^/api/productions/?$", path):
            return self._creation_post()
        match = re.match(r"^/api/productions/([^/]+)/(execute|resume)/?$", path)
        if match:
            status, payload = production_command_payload(
                match.group(1),
                self._query_param("unit_id"),
                match.group(2),
                self._request_json_body(),
            )
            return self._json(status, payload)
        match = re.match(r"^/api/productions/([^/]+)/units/([^/]+)/publish/?$", path)
        if match:
            status, payload = unit_publish_payload(
                match.group(1), match.group(2), self._request_json_body()
            )
            return self._json(status, payload)
        return self._json(404, {"detail": "not found"})

    def _creation_post(self):
        status, payload = create_production_payload(self._request_json_body())
        return self._json(status, payload)

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/health":
                return self._json(200, {"ok": True, "service": "avm-web"})
            if path == "/api/connections":
                return self._json(200, connections_payload())
            if path == "/api/capabilities":
                # Phase 2C: read-only local configuration readiness. Exact
                # path only — it never addresses a production or project.
                return self._json(200, capabilities_payload())
            if path == "/api/projects":
                return self._json(200, projects_index())
            match = re.match(r"^/api/projects/([^/]+)/publish/tiktok/status$", path)
            if match:
                status, payload = tiktok_status_payload(match.group(1))
                return self._json(status, payload)
            match = re.match(r"^/api/projects/([^/]+)/publish/youtube/status$", path)
            if match:
                status, payload = youtube_status_payload(match.group(1))
                return self._json(status, payload)
            match = re.match(r"^/api/projects/([^/]+)/media$", path)
            if match:
                return self._serve_media(match.group(1))
            match = re.match(r"^/api/projects/([^/]+)/scenes/([^/]+)/media$", path)
            if match:
                return self._serve_scene_media(match.group(1), match.group(2))
            match = re.match(r"^/api/projects/([^/]+)/assets/([^/]+)/media$", path)
            if match:
                return self._serve_asset_media(match.group(1), match.group(2))
            match = re.match(r"^/api/projects/([^/]+)/assets/([^/]+)/verify$", path)
            if match:
                status, payload = asset_verify_payload(match.group(1), match.group(2))
                return self._json(status, payload)
            match = re.match(r"^/api/projects/([^/]+)/assets$", path)
            if match:
                status, payload = project_assets_payload(match.group(1))
                return self._json(status, payload)
            match = re.match(r"^/api/projects/([^/]+)/?$", path)
            if match:
                detail = project_detail(match.group(1))
                if detail and detail.get("binding_error") == "PROJECT_BINDING_INVALID":
                    return self._json(400, {
                        "error": "PROJECT_BINDING_INVALID",
                        "message": (
                            "Project binding evidence is malformed or does not "
                            "match the requested project identity. No relationship "
                            "was inferred."
                        ),
                    })
                if detail:
                    return self._json(200, detail)
                return self._json(404, {"detail": "not found"})
            if path.startswith("/api/productions"):
                return self._productions_get(path)
            return self._static(path)
        except Exception as exc:
            if _is_client_disconnect(exc):
                # Normal browser disconnect during response write (polling
                # navigation/close). Logged cleanly; never a 500 (the socket
                # is gone) and never a misleading "GET failed" traceback.
                logger.info(
                    "GET %s: client disconnected before the response "
                    "completed (%s)", path, type(exc).__name__,
                )
                return
            logger.exception("GET %s failed", path)
            try:
                return self._json(500, {"detail": "internal error"})
            except Exception:  # noqa: BLE001 - response could not be delivered
                logger.info(
                    "GET %s: failure response could not be delivered "
                    "(client gone)", path,
                )

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if re.match(r"^/api/projects/[^/]+/scenes/[^/]+/media$", path):
                return self._json(405, {
                    "error": "METHOD_NOT_ALLOWED",
                    "message": "Scene media delivery is GET-only.",
                })
            if re.match(r"^/api/projects/[^/]+/assets/[^/]+/(media|verify)$", path):
                return self._json(405, {
                    "error": "METHOD_NOT_ALLOWED",
                    "message": "Asset surfaces are GET-only.",
                })
            if re.match(r"^/api/productions/[^/]+/units/[^/]+/audio/?$", path):
                # The audio surface is evidence, not an action: it can never
                # generate TTS, extract audio, or execute a production.
                return self._json(405, {
                    "error": "METHOD_NOT_ALLOWED",
                    "message": "Audio evidence is a GET-only, read-only "
                               "surface; it never generates or executes "
                               "anything.",
                })
            if path == "/api/capabilities":
                return self._json(405, {
                    "error": "METHOD_NOT_ALLOWED",
                    "message": "Capability readiness is a GET-only, "
                               "read-only configuration surface.",
                })
            match = re.match(r"^/api/platforms/(youtube|tiktok)/connect$", path)
            if match:
                status, payload = _start_connect(match.group(1))
                return self._json(status, payload)
            if re.match(r"^/api/projects/[^/]+/publish/(youtube|tiktok)/status$", path):
                # Drain the bounded body before HTTP/1.0 closes the socket.
                # Unread POST bytes can reset Windows loopback connections and
                # hide the truthful 405 response. This never invokes publishing.
                self._request_json_body()
                return self._json(405, {"error": "METHOD_NOT_ALLOWED", "message": "Publication status is GET-only."})
            match = re.match(r"^/api/projects/([^/]+)/publish/youtube$", path)
            if match:
                body = self._request_json_body()
                status, payload = youtube_publish_now(match.group(1), privacy_status=body.get("privacy_status"))
                return self._json(status, payload)
            match = re.match(r"^/api/projects/([^/]+)/publish/tiktok$", path)
            if match:
                # Explicit, deliberate publishing request only.
                body = self._request_json_body()
                status, payload = tiktok_publish_now(
                    match.group(1), privacy_level=body.get("privacy_level")
                )
                return self._json(status, payload)
            if re.match(r"^/api/productions/[^/]+/units/[^/]+/summary-video/media/?$", path):
                return self._json(405, {"error": "METHOD_NOT_ALLOWED", "message": "Summary video media is GET-only."})
            if path.startswith("/api/productions"):
                return self._productions_post(path)
            return self._json(404, {"detail": "not found"})
        except Exception as exc:
            # F-02 (audit): unexpected POST failures now return the truthful
            # JSON 500 envelope instead of dropping the connection. Known
            # validation errors are handled inside the payload functions and
            # never reach this branch; nothing is retried automatically.
            if _is_client_disconnect(exc):
                # Normal browser disconnect during response write. Logged
                # cleanly; the command outcome is unaffected because every
                # durable state change is persisted BEFORE the response.
                logger.info(
                    "POST %s: client disconnected before the response "
                    "completed (%s)", path, type(exc).__name__,
                )
                return
            logger.exception("POST %s failed", path)
            try:
                return self._json(500, {
                    "error": "INTERNAL_ERROR",
                    "message": (
                        "unexpected server error: "
                        f"{type(exc).__name__}: {_browser_safe_diagnostic(exc)}"
                    ),
                    "guidance": (
                        "The command was not retried. Check the engine log for "
                        "the full traceback before retrying manually."
                    ),
                })
            except Exception:  # noqa: BLE001 - response could not be delivered
                logger.info(
                    "POST %s: failure response could not be delivered "
                    "(client gone)", path,
                )

    # -- media (range-aware enough for <video> seeking) --------------------
    def _serve_media(self, project_id: str):
        media = media_path_for(project_id)
        if not media:
            return self._json(404, {"detail": "media unavailable"})
        size = media.stat().st_size
        range_header = self.headers.get("Range", "")
        start, end = 0, size - 1
        if range_header.startswith("bytes="):
            parts = range_header[6:].split("-")
            try:
                start = int(parts[0]) if parts[0] else 0
                if parts[1]:
                    end = min(int(parts[1]), size - 1)
            except ValueError:
                start, end = 0, size - 1
        length = end - start + 1
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", MIME[".mp4"])
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(media, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _serve_scene_media(self, project_id: str, scene_id: str):
        """Serve only the authoritative, root-confined media for one scene."""
        media, error = _scene_media_path(project_id, scene_id)
        if media is None:
            status = 404 if error in {
                "PROJECT_NOT_FOUND", "SCENE_NOT_FOUND", "SCENE_MEDIA_NOT_GENERATED",
                "SCENE_MEDIA_MISSING"
            } else 400
            return self._json(status, {
                "error": error or "SCENE_MEDIA_UNAVAILABLE",
                "message": "Scene media is unavailable; no file was served.",
            })
        size = media.stat().st_size
        range_header = self.headers.get("Range", "")
        start, end = 0, size - 1
        if range_header:
            if not range_header.startswith("bytes="):
                return self._json(416, {"error": "INVALID_RANGE", "message": "Unsupported byte range."})
            value = range_header[6:].split(",", 1)[0].strip()
            parts = value.split("-", 1)
            try:
                if len(parts) != 2 or (not parts[0] and not parts[1]):
                    raise ValueError
                if parts[0]:
                    start = int(parts[0])
                    end = int(parts[1]) if parts[1] else size - 1
                else:
                    suffix_length = int(parts[1])
                    if suffix_length <= 0:
                        raise ValueError
                    start = max(0, size - suffix_length)
                    end = size - 1
                if start < 0 or start >= size or end < start:
                    raise ValueError
                end = min(end, size - 1)
            except (TypeError, ValueError):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", MIME[media.suffix.lower()])
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with media.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _serve_asset_media(self, project_id: str, scene_id: str):
        """Serve only the root-confined, manifest-recorded asset for one scene.
        Range handling mirrors scene-media delivery; SHA-256 re-verification is
        the explicit /verify surface, never per-byte-range work here."""
        media, error = asset_media_path_for(project_id, scene_id)
        if media is None:
            return self._json(404, {
                "error": error or "ASSET_MEDIA_UNAVAILABLE",
                "message": "Asset media is unavailable; no file was served.",
            })
        size = media.stat().st_size
        range_header = self.headers.get("Range", "")
        start, end = 0, size - 1
        if range_header:
            if not range_header.startswith("bytes="):
                return self._json(416, {"error": "INVALID_RANGE", "message": "Unsupported byte range."})
            value = range_header[6:].split(",", 1)[0].strip()
            parts = value.split("-", 1)
            try:
                if len(parts) != 2 or (not parts[0] and not parts[1]):
                    raise ValueError
                if parts[0]:
                    start = int(parts[0])
                    end = int(parts[1]) if parts[1] else size - 1
                else:
                    suffix_length = int(parts[1])
                    if suffix_length <= 0:
                        raise ValueError
                    start = max(0, size - suffix_length)
                    end = size - 1
                if start < 0 or start >= size or end < start:
                    raise ValueError
                end = min(end, size - 1)
            except (TypeError, ValueError):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", MIME[media.suffix.lower()])
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with media.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # -- static files -------------------------------------------------------
    def _static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        candidate = (WEBAPP_DIR / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(WEBAPP_DIR.resolve())
        except (ValueError, OSError):
            return self._json(403, {"detail": "forbidden"})
        if not candidate.is_file():
            candidate = WEBAPP_DIR / "index.html"  # SPA fallback
        suffix = candidate.suffix.lower()
        body = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    from app.logging_setup import configure_logging

    configure_logging()
    logger.info(
        "server.startup runtime=command_center logging_configured=true"
    )
    port = DEFAULT_PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), AVMHandler)
    url = f"http://127.0.0.1:{port}"
    print("=" * 60)
    print("AUTO Video Maker — Web Experience")
    print(f"  UI + local API : {url}")
    print("  Data           : real manifests when present, labelled demo otherwise")
    print("  Publishing     : via existing produce.py flows (never automatic)")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())