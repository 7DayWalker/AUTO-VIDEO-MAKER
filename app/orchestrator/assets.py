"""Step 6C — project-scoped asset workflow (intake, fingerprinting,
manifest, preflight).

This module owns the durable answer to: "Which exact asset belongs to this
scene?" Provider selection remains exclusively in
``manifest.scene_config.scene_provider_routing`` (Phase 6B) — the two concerns
are intentionally never merged here.

Authoritative-source rule: after successful intake, the project-owned copy
(``assets/source/<scene_id>.<ext>``) is the ONLY source for that scene. The
original CLI path is intake provenance and is never consulted again.
"""

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS

ASSET_MANIFEST_VERSION = 1

# Provider identifiers mirror app/orchestrator/visual_providers.py constants.
# They are intentionally NOT imported: importing that module would drag the
# Flow/CDP dependency chain into every assets-only consumer. The authoritative
# provider decision ALWAYS lives in scene_config.scene_provider_routing; these
# literals only tag descriptive provenance and media-type expectations.
PROVIDER_GOOGLE_FLOW = "google_flow"
PROVIDER_LOCAL_ASSET = "local_asset"
PROVIDER_IMAGE_MOTION = "image_motion"
# Phase 7B: intake route identity for an externally obtained video clip.
PROVIDER_EXTERNAL_VIDEO = "external_video"
# Phase 7C: cost-bearing text-to-video generation; no local asset is required.
PROVIDER_GEMINI_OMNI = "gemini_omni"
# Phase 7E: generic OpenRouter video generation; no local asset is required.
PROVIDER_OPENROUTER_VIDEO = "openrouter_video"
# Batch 1C: Alibaba (DashScope Wan t2v) motion-video generation; like the
# other generation providers it produces the scene video directly from the
# prompt and requires no local source asset (its resolve_asset is None by
# contract). Exempting it keeps the Phase 6C asset gate truthful for the
# user-selected alibaba_video generation strategy instead of demanding an
# image from a prompt-driven video provider.
PROVIDER_ALIBABA_VIDEO = "alibaba_video"

# Media types each zero-cost provider can legitimately consume.
REQUIRED_MEDIA_TYPES = {
    PROVIDER_IMAGE_MOTION: {"image"},
    PROVIDER_LOCAL_ASSET: {"image", "video"},
    PROVIDER_EXTERNAL_VIDEO: {"video"},
}

# Providers that own generation and require no local asset (asset intake and
# preflight do not apply to them).
GENERATION_PROVIDERS = {
    PROVIDER_GOOGLE_FLOW,
    PROVIDER_GEMINI_OMNI,
    PROVIDER_OPENROUTER_VIDEO,
    PROVIDER_ALIBABA_VIDEO,
}


# ---------------------------------------------------------------------------
# Parsing / normalization
# ---------------------------------------------------------------------------

def parse_asset_mappings(items: List[str]) -> Dict[str, str]:
    """Parse repeatable ``SCENE_ID=PATH`` strings into a mapping.

    Raises ValueError on malformed entries or conflicting duplicates for the
    same scene ID. Identical duplicates are tolerated.
    """
    mappings: Dict[str, str] = {}
    for item in items:
        text = (item or "").strip()
        if "=" not in text:
            raise ValueError(
                f"Malformed asset mapping '{item}'. Expected SCENE_ID=PATH."
            )
        scene_id, _, raw_path = text.partition("=")
        scene_id = scene_id.strip()
        raw_path = raw_path.strip().strip('"')
        if not scene_id or not raw_path:
            raise ValueError(
                f"Malformed asset mapping '{item}'. "
                "Both SCENE_ID and PATH are required."
            )
        path = str(Path(raw_path))  # normalizes separators on Windows
        existing = mappings.get(scene_id)
        if existing is not None and existing != path:
            raise ValueError(
                f"Conflicting asset mappings for '{scene_id}': "
                f"'{existing}' vs '{raw_path}'."
            )
        mappings[scene_id] = path
    return mappings


def media_type_for(path: Path) -> Optional[str]:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Manifest persistence (atomic replace)
# ---------------------------------------------------------------------------

def asset_manifest_path(project_dir: Path) -> Path:
    return Path(project_dir) / "assets" / "asset_manifest.json"


def load_asset_manifest(project_dir: Path) -> Dict[str, Any]:
    path = asset_manifest_path(project_dir)
    if not path.exists():
        return {"version": ASSET_MANIFEST_VERSION, "assets": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("version", ASSET_MANIFEST_VERSION)
    data.setdefault("assets", {})
    return data


def save_asset_manifest(project_dir: Path, data: Dict[str, Any]) -> Path:
    path = asset_manifest_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic on same volume
    return path


def resolve_authoritative_source(
    project_dir: Path,
    scene_id: str,
    asset_manifest: Dict[str, Any],
) -> Optional[Path]:
    """Project-owned stored copy for a mapped scene, or None."""
    entry = asset_manifest.get("assets", {}).get(scene_id)
    if not entry:
        return None
    return Path(project_dir) / entry["stored_as"]


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------

def intake_assets(
    project_dir: Path,
    plan_scenes: List[Any],
    mappings: Dict[str, str],
    provider_by_scene: Dict[str, str],
    provenance: Optional[Dict[str, Dict]] = None,
):
    """Validate + copy mapped assets into the project; update the manifest.

    Returns ``(asset_manifest, errors)``. ``errors`` is non-empty when ANY
    mapping failed; successful entries are still recorded so the user sees a
    complete picture, but the caller must treat non-empty errors as fatal for
    the stage (preflight will refuse to pass).

    Rules:
    - Scene IDs must exist in the production plan.
    - Scenes routed to google_flow must not be mapped (no asset required).
    - Only supported extensions are accepted (videos ffprobe-checked for a
      video stream at intake; images checked non-empty, fully probed by the
      provider during generation).
    - Original and stored-copy SHA-256 must match.
    """
    from app.assembly.ffmpeg import probe_media

    manifest = load_asset_manifest(project_dir)
    valid_ids = {scene.id for scene in plan_scenes}
    errors: List[str] = []
    source_dir = Path(project_dir) / "assets" / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    for scene_id in sorted(mappings):
        raw_path = Path(mappings[scene_id])
        if scene_id not in valid_ids:
            errors.append(
                f"{scene_id}: unknown scene ID "
                f"(valid: {', '.join(sorted(valid_ids))})"
            )
            continue
        provider = provider_by_scene.get(scene_id)
        if provider in GENERATION_PROVIDERS:
            errors.append(
                f"{scene_id}: routed to {provider}; a local asset is not "
                "required and cannot be mapped."
            )
            continue
        if not raw_path.is_file():
            errors.append(f"{scene_id}: source not found: {raw_path}")
            continue
        media_type = media_type_for(raw_path)
        if media_type is None:
            errors.append(
                f"{scene_id}: unsupported extension '{raw_path.suffix}' "
                f"(supported: {', '.join(sorted(SUPPORTED_EXTS))})"
            )
            continue

        original_hash = sha256_file(raw_path)

        if media_type == "video":
            try:
                info = probe_media(raw_path)
                if not info.has_video:
                    errors.append(
                        f"{scene_id}: video asset has no video stream"
                    )
                    continue
            except Exception as exc:  # noqa: BLE001 - truthful validation
                errors.append(f"{scene_id}: invalid video asset: {exc}")
                continue
        else:
            if raw_path.stat().st_size == 0:
                errors.append(f"{scene_id}: image asset is empty")
                continue

        stored_name = f"{scene_id}{raw_path.suffix.lower()}"
        stored_path = source_dir / stored_name
        shutil.copyfile(raw_path, stored_path)
        stored_hash = sha256_file(stored_path)
        if stored_hash != original_hash:
            stored_path.unlink(missing_ok=True)
            errors.append(
                f"{scene_id}: intake integrity failure — copied asset hash "
                "does not match the original"
            )
            continue

        entry = {
            "source_name": raw_path.name,
            "stored_as": f"assets/source/{stored_name}",
            "media_type": media_type,
            "provider": provider_by_scene.get(scene_id),
            "sha256": stored_hash,
            "bytes": stored_path.stat().st_size,
        }
        prov = (provenance or {}).get(scene_id) or {}
        # Phase 11C Batch 1 hardening: "source_metadata" carries the visual
        # source's rich provenance (kind, license, photographer, landing
        # page, AI model, usage/cost) — additive, secret-free, and never a
        # routing authority.
        for key in (
            "supplied_by", "source_provider", "source_status", "source_metadata",
        ):
            if prov.get(key):
                entry[key] = prov[key]
        manifest["assets"][scene_id] = entry

    save_asset_manifest(project_dir, manifest)
    return manifest, errors


# ---------------------------------------------------------------------------
# Verification / preflight
# ---------------------------------------------------------------------------

def verify_stored_asset(project_dir: Path, entry: Dict[str, Any]) -> Optional[str]:
    """Return a failure reason for a stored project asset, or None if OK."""
    stored_rel = entry.get("stored_as")
    expected_hash = entry.get("sha256")
    if not stored_rel or not expected_hash:
        return "asset manifest entry is incomplete"
    stored_abs = Path(project_dir) / stored_rel
    if not stored_abs.is_file():
        return f"project asset missing: {stored_rel}"
    actual = sha256_file(stored_abs)
    if actual != expected_hash:
        return (
            f"SHA-256 integrity mismatch for {stored_rel} "
            f"(expected {str(expected_hash)[:12]}…, got {actual[:12]}…)"
        )
    return None


def preflight_assets(
    project_dir: Path,
    plan_scenes: List[Any],
    routing: Dict[str, Dict[str, Any]],
    asset_manifest: Dict[str, Any],
    legacy_resolver=None,
    missing_message=None,
) -> List[str]:
    """Validate every ZERO-COST routed scene. Flow scenes are exempt.

    ``legacy_resolver``: optional callable(scene) used for scenes with no
    asset-manifest entry (legacy filename discovery). It may return:

    - a ``Path``     -> discovered, scene passes;
    - ``None``       -> generic failure text is synthesized;
    - a ``str``      -> an already-formatted truthful failure reason
      (the pipeline injects provider-identical wording here).

    ``missing_message``: optional callable(provider_name, scene_id) -> str,
    used when ``None`` is returned by the resolver and no per-scene message
    was supplied.

    Reports ALL problems in one pass. Never suggests or triggers Flow.
    Media-type expectations are enforced (image_motion requires images;
    local_asset accepts images or videos), and every stored fingerprint is
    re-verified against the manifest.
    """
    failures: List[str] = []
    assets = asset_manifest.get("assets", {})
    for scene in sorted(plan_scenes, key=lambda s: s.sequence):
        routed = routing.get(scene.id, {}).get("provider")
        if routed is None or routed in GENERATION_PROVIDERS:
            continue  # generation providers need no local asset; unrouted scenes aren't ours
        entry = assets.get(scene.id)
        if entry is not None:
            reason = verify_stored_asset(project_dir, entry)
            if not reason:
                required = REQUIRED_MEDIA_TYPES.get(routed)
                media = str(entry.get("media_type") or "")
                if required is not None and media not in required:
                    reason = (
                        f"asset media type mismatch: routed to '{routed}' "
                        f"but manifest records media_type='{media}'"
                    )
            if reason:
                failures.append(f"{scene.id}: {reason}")
            continue
        outcome = legacy_resolver(scene) if legacy_resolver else None
        if isinstance(outcome, Path):
            continue  # legacy discovery succeeded
        if isinstance(outcome, str) and outcome.strip():
            failures.append(outcome)
            continue
        if missing_message is not None:
            failures.append(missing_message(str(routed), scene.id))
        else:
            failures.append(
                f"{scene.id}: no asset mapped and no file discovered via "
                "naming convention"
            )
    return failures