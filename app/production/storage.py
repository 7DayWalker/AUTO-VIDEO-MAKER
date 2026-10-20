"""
AUTO Video Maker - Phase 9: Durable Production intent storage.

Layout (PHASE_9 spec §22, decision A8):

    productions/
        <production_id>/
            production.json          current intent (schema_version + plan_version)
            versions/
                v<NNN>/production.json   immutable historical snapshots
            units/
                <unit_id>/plan/      Phase 10 binding target (empty in Phase 9)

Guarantees:

- ``productions/`` is durable INTENT; it is fully separate from the execution
  artifacts/state (``output/projects/``, manifests, checkpoints).
- All writes are ATOMIC (temporary file + ``os.replace``), following the
  repository's proven pattern (app/orchestrator/assets.py:129-135).
- Version snapshots are IMMUTABLE: an existing ``versions/v<NNN>/`` file is
  never silently overwritten; saving a revision snapshots the prior current
  intent first, then atomically writes the new current intent.
- Identity never depends on absolute filesystem paths; a production directory
  is relocatable without invalidating its identity.
- Malformed artifacts and unsupported schema versions FAIL LOUDLY.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from pydantic import ValidationError

from app.production.models import (
    Production,
    SourceAsset,
    SourceSegment,
    derive_production_unit_from_segment as _derive_unit_from_segment,
)
from app.production.routes import assert_saas_production_route
from app.production.validation import assert_valid_production

_PRODUCTION_FILE = "production.json"
_VERSIONS_DIR = "versions"
_UNITS_DIR = "units"
_PLAN_DIR = "plan"
_SOURCE_DIR = "source"
_SOURCE_META = "source.json"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _default_root() -> Path:
    """Import the configured intent data area lazily (avoids import cycles)."""
    from app.config import PRODUCTIONS_DIR

    return Path(PRODUCTIONS_DIR)


def productions_root(root: Optional[Path] = None) -> Path:
    return Path(root) if root is not None else _default_root()


def production_dir(production_id: str, root: Optional[Path] = None) -> Path:
    return productions_root(root) / production_id


def current_path(production_id: str, root: Optional[Path] = None) -> Path:
    return production_dir(production_id, root) / _PRODUCTION_FILE


def version_path(
    production_id: str, version: int, root: Optional[Path] = None
) -> Path:
    return (
        production_dir(production_id, root)
        / _VERSIONS_DIR
        / f"v{version:03d}"
        / _PRODUCTION_FILE
    )


def unit_plan_dir(
    production_id: str, unit_id: str, root: Optional[Path] = None
) -> Path:
    return production_dir(production_id, root) / _UNITS_DIR / unit_id / _PLAN_DIR


def narration_artifact_path(
    production_id: str, unit_id: str, root: Optional[Path] = None
) -> Path:
    return unit_plan_dir(production_id, unit_id, root) / "narration.json"


def narration_audio_metadata_path(
    production_id: str, unit_id: str, root: Optional[Path] = None
) -> Path:
    return unit_plan_dir(production_id, unit_id, root) / "narration_audio.json"


def narration_audio_path(
    production_id: str,
    unit_id: str,
    *,
    root: Optional[Path] = None,
    output_format: str = "mp3",
) -> Path:
    ext = output_format.lower().lstrip(".")
    if ext not in {"mp3", "wav"}:
        raise ValueError("narration audio output format must be mp3 or wav")
    return unit_plan_dir(production_id, unit_id, root) / f"narration_audio.{ext}"


def save_narration_artifact(
    artifact: "NarrationArtifact",
    *,
    production_id: str,
    unit_id: str,
    root: Optional[Path] = None,
    source_asset: Optional["SourceAsset"] = None,
    source_transcript: Optional["SourceTranscript"] = None,
) -> Path:
    """Persist a Track 9 narration artifact under the canonical unit plan directory."""
    from app.production.narration import validate_narration_artifact

    validate_narration_artifact(
        artifact,
        source_asset=source_asset,
        source_transcript=source_transcript,
    )
    path = narration_artifact_path(production_id, unit_id, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(artifact.model_dump(), indent=2, ensure_ascii=False))
    return path


def save_narration_audio(
    artifact: "NarrationAudioArtifact",
    *,
    production_id: str,
    unit_id: str,
    root: Optional[Path] = None,
) -> Path:
    """Persist synthesized narration audio metadata under the canonical unit plan directory."""
    from app.production.models import NarrationAudioArtifact

    if not isinstance(artifact, NarrationAudioArtifact):
        raise TypeError("artifact must be a NarrationAudioArtifact")
    path = narration_audio_metadata_path(production_id, unit_id, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(artifact.model_dump(), indent=2, ensure_ascii=False))
    return path


def load_narration_artifact(
    production_id: str, unit_id: str, *, root: Optional[Path] = None
) -> "NarrationArtifact":
    """Load a persisted Track 9 narration artifact."""
    from app.production.models import NarrationArtifact

    path = narration_artifact_path(production_id, unit_id, root=root)
    if not path.is_file():
        raise FileNotFoundError(f"narration artifact not found: {path}")
    return NarrationArtifact.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_narration_audio(
    production_id: str, unit_id: str, *, root: Optional[Path] = None
) -> "NarrationAudioArtifact":
    """Load persisted synthesized narration audio metadata."""
    from app.production.models import NarrationAudioArtifact

    path = narration_audio_metadata_path(production_id, unit_id, root=root)
    if not path.is_file():
        raise FileNotFoundError(f"narration audio metadata not found: {path}")
    return NarrationAudioArtifact.model_validate(json.loads(path.read_text(encoding="utf-8")))


def production_source_dir(production_id: str, root: Optional[Path] = None) -> Path:
    return production_dir(production_id, root) / _SOURCE_DIR


def production_source_meta(production_id: str, root: Optional[Path] = None) -> Path:
    return production_source_dir(production_id, root) / _SOURCE_META


def transcript_path_for(production_id: str, asset_id: str, root: Optional[Path] = None) -> Path:
    return production_source_dir(production_id, root) / f"{asset_id}.transcript.json"


def segments_path_for(production_id: str, asset_id: str, root: Optional[Path] = None) -> Path:
    return production_source_dir(production_id, root) / f"{asset_id}.segments.json"


def source_artifact_path(production_id: str, stored_path: str, root: Optional[Path] = None) -> Path:
    return production_dir(production_id, root) / stored_path.replace("\\", "/").lstrip("/")


def production_exists(production_id: str, root: Optional[Path] = None) -> bool:
    return current_path(production_id, root).exists()


def list_productions(root: Optional[Path] = None) -> List[str]:
    """Return the sorted production identities present in the intent area."""
    base = productions_root(root)
    if not base.is_dir():
        return []
    return sorted(
        entry.name
        for entry in base.iterdir()
        if entry.is_dir() and (entry / _PRODUCTION_FILE).is_file()
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize(production: Production) -> str:
    """Serialize to human-readable JSON (repository Pydantic v2 conventions)."""
    return json.dumps(
        production.model_dump(by_alias=True), indent=2, ensure_ascii=False
    )


def _atomic_write(path: Path, text: str) -> None:
    """Atomic write: temporary file + ``os.replace`` (same-volume atomicity)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)  # atomic on the same volume


def compute_content_hash(path: Path) -> str:
    """SHA-256 of an artifact (same convention as app/orchestrator/assets.py)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def _load_artifact(path: Path) -> Production:
    """Read + parse + structurally validate one artifact. FAIL LOUDLY."""
    if not path.is_file():
        raise FileNotFoundError(f"production artifact not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed production artifact {path}: {exc}") from exc
    try:
        return Production.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"invalid production artifact {path}: {exc}") from exc


def load_production(production_id: str, *, root: Optional[Path] = None) -> Production:
    """Load the CURRENT intent for a production, fully validated.

    Fails loudly on missing/malformed artifacts, unsupported schema versions,
    execution-state/secret contamination, and broken resolved plan references.
    """
    production = _load_artifact(current_path(production_id, root))
    assert_valid_production(
        production, production_dir=production_dir(production_id, root)
    )
    return production


def load_version_snapshot(
    production_id: str, version: int, *, root: Optional[Path] = None
) -> Production:
    """Load one immutable historical intent snapshot (fully validated)."""
    production = _load_artifact(version_path(production_id, version, root))
    if production.plan_version != version:
        raise ValueError(
            f"snapshot v{version:03d} carries plan_version "
            f"{production.plan_version} (snapshot integrity violation)"
        )
    assert_valid_production(
        production, production_dir=production_dir(production_id, root)
    )
    return production


def list_versions(production_id: str, *, root: Optional[Path] = None) -> List[int]:
    """Return the sorted immutable snapshot versions present on disk."""
    base = production_dir(production_id, root) / _VERSIONS_DIR
    if not base.is_dir():
        return []
    return sorted(
        int(entry.name[1:])
        for entry in base.iterdir()
        if entry.is_dir()
        and entry.name.startswith("v")
        and entry.name[1:].isdigit()
        and (entry / _PRODUCTION_FILE).is_file()
    )


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


def _write_unit_plan_dirs(production: Production, root: Optional[Path]) -> None:
    """Create per-unit Phase 10 binding directories (kept EMPTY in Phase 9)."""
    for unit in production.units:
        unit_plan_dir(production.production_id, unit.unit_id, root).mkdir(
            parents=True, exist_ok=True
        )


def derive_production_unit_from_segment(
    production_id: str,
    source_asset: SourceAsset,
    source_segment: SourceSegment,
    *,
    root: Optional[Path] = None,
    unit_id: Optional[str] = None,
    title: Optional[str] = None,
    premise: Optional[str] = None,
    unit_type: str = "standalone_video",
    ordinal: Optional[int] = None,
    content_intent: Optional[dict] = None,
    provider_intent: Optional[str] = None,
    save: bool = True,
) -> Production:
    """Load a production, derive one canonical unit from a selected source segment,
    persist it through the canonical save/revision flow, and return the updated
    production object.

    Repeated derivation of the same production + source_asset + source_segment is
    idempotent and reuses the existing derived unit instead of creating a duplicate.
    """
    production = load_production(production_id, root=root)
    existing = next(
        (
            unit
            for unit in production.units
            if unit.source_lineage is not None
            and unit.source_lineage.asset_id == source_asset.asset_id
            and source_segment.segment_id in set(unit.source_lineage.source_segment_ids)
        ),
        None,
    )
    if existing is not None:
        return production

    derived = _derive_unit_from_segment(
        production,
        source_asset,
        source_segment,
        unit_id=unit_id,
        title=title,
        premise=premise,
        unit_type=unit_type,
        ordinal=ordinal,
        content_intent=content_intent,
        provider_intent=provider_intent,
    )
    revised_units = list(production.units) + [derived]
    lineage_entries = list(production.source_lineage or [])
    if derived.source_lineage is not None:
        lineage_entries = [lineage for lineage in lineage_entries if lineage.unit_id != derived.unit_id]
        lineage_entries.append(derived.source_lineage)

    revised = production.model_copy(
        update={
            "plan_version": production.plan_version + 1,
            "revised_at": datetime.now().isoformat(),
            "units": revised_units,
            "source_lineage": lineage_entries,
        }
    )
    if save:
        save_revision(revised, root=root)
    return revised


def save_new_production(
    production: Production, *, root: Optional[Path] = None
) -> Path:
    """Persist a NEW production intent artifact (plan_version must be 1).

    Validates before persistence, atomically writes the current intent, and
    REFUSES to overwrite an existing identity. Immutable snapshots of
    superseded versions are written by ``save_revision``.

    SaaS/private boundary: a NEW production can only select a SaaS product
    route (``assert_saas_production_route``) — ``google_flow`` is a
    private-only capability and is never a new SaaS route. Existing
    artifacts (including legacy private ones) keep loading, and
    ``save_revision`` remains ungated so legacy private productions stay
    revisable.
    """
    if production.plan_version != 1:
        raise ValueError(
            "a new production must have plan_version == 1 "
            f"(got {production.plan_version})"
        )
    assert_saas_production_route(production.route)
    assert_valid_production(production)
    target = current_path(production.production_id, root)
    if target.exists():
        raise FileExistsError(
            f"production '{production.production_id}' already exists: {target}; "
            "identities are never overwritten"
        )
    text = _serialize(production)
    _write_unit_plan_dirs(production, root)
    _atomic_write(target, text)
    return target


def save_revision(revised: Production, *, root: Optional[Path] = None) -> Path:
    """Persist a NEW plan_version of an existing production.

    Guarantees:

    - versions increment monotonically by exactly one (no gaps, no reuse);
    - the superseded current intent is snapshotted into ``versions/v<NNN>/``
      BEFORE the new current intent is written;
    - existing snapshots are never overwritten (immutability);
    - created_at provenance and unit identities are preserved across revisions.
    """
    current = load_production(revised.production_id, root=root)
    if revised.plan_version != current.plan_version + 1:
        raise ValueError(
            "plan_version must increment monotonically by one: current is "
            f"{current.plan_version}, revision declares {revised.plan_version}"
        )
    if revised.created_at != current.created_at:
        raise ValueError("created_at provenance cannot change across revisions")
    removed = sorted(
        {unit.unit_id for unit in current.units}
        - {unit.unit_id for unit in revised.units}
    )
    if removed:
        raise ValueError(
            "unit identities are stable across revisions; revision removes: "
            f"{removed}"
        )
    assert_valid_production(revised)
    snapshot = version_path(revised.production_id, current.plan_version, root)
    if snapshot.exists():
        raise FileExistsError(
            f"immutable snapshot already exists and is never overwritten: {snapshot}"
        )
    _atomic_write(snapshot, _serialize(current))
    _write_unit_plan_dirs(revised, root)
    target = current_path(revised.production_id, root)
    _atomic_write(target, _serialize(revised))
    return target
