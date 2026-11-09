"""Atomic batch artifacts and process-safe, non-replaying job records."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from app.production.storage import production_dir, production_revision_lock
from app.repurpose.models import RepurposeError, RepurposePlan, RenderArtifact

_IO_LOCKS = {}
_IO_GUARD = threading.Lock()


def _io_lock(path):
    with _IO_GUARD:
        return _IO_LOCKS.setdefault(str(Path(path).resolve()), threading.RLock())


def content_hash(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def safe_production_dir(production_id, root=None):
    if not re.fullmatch(r"prd_\d{8}_[a-z0-9_]+_\d{3}", production_id or ""):
        raise RepurposeError("INVALID_PRODUCTION", "Invalid production identity", 400)
    return production_dir(production_id, root).resolve()


def batch_dir(production_id, batch_id, root=None):
    if not re.fullmatch(r"rpb-[a-f0-9]{24}", batch_id or ""):
        raise RepurposeError("INVALID_BATCH", "Invalid batch identity", 400)
    return safe_production_dir(production_id, root) / "repurpose" / "batches" / batch_id


def unit_dir(production_id, unit_id, root=None):
    if not re.fullmatch(r"unit-short-\d{3}", unit_id or ""):
        raise RepurposeError("INVALID_SHORT_UNIT", "Invalid short unit identity", 400)
    return safe_production_dir(production_id, root) / "units" / unit_id / "repurpose"


def atomic_json(path, value):
    with _io_lock(path):
        return _atomic_json(path, value)


def _atomic_json(path, value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers/virus scanners can briefly deny replacement. Only
        # this local filesystem operation is retried; NEVER a provider call.
        for attempt in range(8):
            try:
                os.replace(name, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(.025 * (attempt+1))
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path):
    with _io_lock(path):
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_job(production_id, batch_id, root=None):
    try:
        return read_json(batch_dir(production_id, batch_id, root) / "job.json")
    except FileNotFoundError:
        raise RepurposeError("BATCH_NOT_FOUND", "Repurpose batch not found", 404) from None


def save_job(production_id, batch_id, job, root=None):
    atomic_json(batch_dir(production_id, batch_id, root) / "job.json", job)


def load_plan(production_id, batch_id, root=None):
    path = batch_dir(production_id, batch_id, root) / "plan.json"
    plan = RepurposePlan.model_validate(read_json(path))
    if (plan.production_id, plan.batch_id) != (production_id, batch_id):
        raise RepurposeError("PLAN_IDENTITY_MISMATCH", "Stored plan ownership changed", 422)
    return plan


def load_unit_plan(production_id, unit_id, root=None):
    from app.production.storage import load_production

    unit_dir(production_id, unit_id, root)  # Validate identities BEFORE filesystem access.
    production = load_production(production_id, root=root)
    unit = next((u for u in production.units if u.unit_id == unit_id), None)
    if unit is None or unit.repurpose_plan_reference is None:
        raise RepurposeError("SHORT_NOT_FOUND", "Unit has no repurpose plan", 404)
    ref = unit.repurpose_plan_reference
    expected = f"repurpose/batches/{ref.batch_id}/plan.json"
    if ref.plan_path != expected:
        raise RepurposeError("PLAN_IDENTITY_MISMATCH", "Short plan path changed", 422)
    plan = load_plan(production_id, ref.batch_id, root)
    if content_hash(plan) != ref.plan_hash or plan.clips[ref.clip_index].unit_id != unit_id:
        raise RepurposeError("PLAN_HASH_MISMATCH", "Short plan fingerprint changed", 422)
    lineage = unit.source_lineage
    if lineage is None or lineage.asset_id != plan.request.source_asset_id or lineage.source_segment_ids != [
            c.source_segment_id for c in plan.clips[ref.clip_index].cuts]:
        raise RepurposeError("SHORT_LINEAGE_CHANGED", "Short source-cut lineage changed", 422)
    return production, unit, plan, plan.clips[ref.clip_index]


def load_render(production_id, unit_id, root=None):
    artifact = RenderArtifact.model_validate(read_json(unit_dir(production_id, unit_id, root) / "render.json"))
    if (artifact.production_id, artifact.unit_id) != (production_id, unit_id):
        raise RepurposeError("RENDER_IDENTITY_MISMATCH", "Short render ownership changed", 422)
    expected = f"units/{unit_id}/repurpose/short.mp4"
    if artifact.output_path != expected:
        raise RepurposeError("RENDER_PATH_MISMATCH", "Short media path changed", 422)
    return artifact