"""Explicit visual preparation and operator-verified action coverage.

Observation is never treated as exact action onset. GETs never dispatch work.
"""
from __future__ import annotations

import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import Field, StrictInt, StrictBool, model_validator

from app.repurpose.models import Contract, RepurposeError, timestamp
from app.repurpose.storage import atomic_json, content_hash, read_json, safe_production_dir
from app.production.storage import production_revision_lock

_WORKERS = ThreadPoolExecutor(max_workers=1, thread_name_prefix="repurpose-prep")
_ACTIVE = set()
_GUARD = threading.Lock()


class PreparationRequest(Contract):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,100}$")
    confirm: StrictBool
    evidence_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    frame_count: StrictInt = Field(default=60, ge=12, le=120)

    @model_validator(mode="after")
    def confirmed(self):
        if "confirm" not in self.model_fields_set or self.confirm is not True:
            raise ValueError("Explicit preparation confirmation is required")
        return self


class ActionAnchor(Contract):
    evidence_id: str = Field(pattern=r"^a-[a-f0-9]{16}$")
    action: str = Field(min_length=3, max_length=900)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    focal_source_pts: int | None = None
    source_time_base: str | None = Field(default=None, pattern=r"^\d+/\d+$")
    anchor_precision: Literal["operator_verified_action", "frame_verified_action"] = "operator_verified_action"
    verification_frame_timestamps: list[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def bounds(self):
        if self.end_seconds <= self.start_seconds or "\x00" in self.action:
            raise ValueError("Verified action coverage must be positive and text safe")
        return self


class AnchorInput(Contract):
    action: str = Field(min_length=3, max_length=900)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)


class AnchorRequest(Contract):
    confirm: StrictBool
    evidence_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    anchors: list[AnchorInput] = Field(min_length=1, max_length=120)


def _directory(production_id, asset_id, root):
    import re
    if not re.fullmatch(r"src-\d{3}", asset_id):
        raise RepurposeError("INVALID_SOURCE", "Invalid source identity", 400)
    return safe_production_dir(production_id, root) / "source" / f"{asset_id}.repurpose"


def save_anchors(production_id, asset_id, body, *, root=None):
    from app.repurpose.cache import load_evidence
    request = AnchorRequest.model_validate(body)
    if body.get("confirm") is not True:
        raise RepurposeError("CONFIRMATION_REQUIRED", "Confirm that these timestamps show the stated actions", 400)
    with production_revision_lock(production_id, root):
        evidence = load_evidence(production_id, asset_id, root=root, include_prepared=True)
        if evidence.revision != request.evidence_revision:
            raise RepurposeError("STALE_EVIDENCE", "Reload evidence before verifying action anchors")
        anchors = []
        for row in request.anchors:
            if row.end_seconds > evidence.asset.duration:
                raise RepurposeError("INVALID_ACTION_ANCHOR", "Action coverage extends beyond source", 422)
            identity = content_hash({"source": evidence.identity, **row.model_dump(mode="json")})
            anchors.append(ActionAnchor(evidence_id="a-" + identity[:16], **row.model_dump()).model_dump(mode="json"))
        if len({a["evidence_id"] for a in anchors}) != len(anchors):
            raise RepurposeError("INVALID_ACTION_ANCHOR", "Duplicate verified anchors", 422)
        anchors.sort(key=lambda a: (a["start_seconds"], a["evidence_id"]))
        atomic_json(_directory(production_id, asset_id, root) / "anchors.json", {"identity": evidence.identity, "anchors": anchors,
            "precision": "operator_verified_action", "updated_at": timestamp()})
        updated = load_evidence(production_id, asset_id, root=root, include_prepared=True)
        return {"production_id": production_id, "asset_id": asset_id, "anchors": anchors, "evidence_revision": updated.revision}


def preparation_status(production_id, asset_id, *, root=None):
    path = _directory(production_id, asset_id, root) / "preparation.json"
    try:
        job = read_json(path)
    except FileNotFoundError:
        return {"production_id": production_id, "asset_id": asset_id, "status": "not_started"}
    if (job.get("production_id"), job.get("asset_id")) != (production_id, asset_id):
        raise RepurposeError("PREPARATION_IDENTITY_MISMATCH", "Preparation ownership changed", 422)
    with _GUARD:
        active = str(path) in _ACTIVE
    return {**job, "status": "interrupted" if job["status"] == "preparing" and not active else job["status"]}


def prepare_source(production_id, asset_id, body, *, root=None, background=True):
    from app.repurpose.cache import load_evidence
    from app.content.capability_router import configured_visual_observer_routes
    request = PreparationRequest.model_validate(body)
    if body.get("confirm") is not True:
        raise RepurposeError("CONFIRMATION_REQUIRED", "Confirm visual-observer preparation", 400)
    path = _directory(production_id, asset_id, root) / "preparation.json"
    with production_revision_lock(production_id, root):
        if path.is_file():
            old = read_json(path)
            if old.get("request_id") == request.request_id:
                if old.get("request_hash") != content_hash(request):
                    raise RepurposeError("REQUEST_ID_CONFLICT", "Preparation request settings changed")
                return preparation_status(production_id, asset_id, root=root)
        with _GUARD:
            if str(path) in _ACTIVE:
                raise RepurposeError("PREPARATION_BUSY", "Source preparation is already running")
        evidence = load_evidence(production_id, asset_id, root=root, include_prepared=True)
        if evidence.revision != request.evidence_revision:
            raise RepurposeError("STALE_EVIDENCE", "Reload source evidence before preparation")
        routes = configured_visual_observer_routes()
        if not routes:
            raise RepurposeError("VISUAL_OBSERVER_NOT_CONFIGURED", "Configure an admitted image observer before preparation", 503)
        job = {"production_id": production_id, "asset_id": asset_id, "request_id": request.request_id,
            "request_hash": content_hash(request), "status": "preparing", "vision_calls": 0, "observations": 0,
            "frame_count": request.frame_count, "created_at": timestamp(), "updated_at": timestamp()}
        atomic_json(path, job)
        with _GUARD:
            _ACTIVE.add(str(path))

    def work():
        from app.content.provider_registry import create_provider
        from app.production.narration_pro import _extract_window_frames
        from app.production.visual_cache import CACHE_FILENAME, save_frame_observations, load_cached_visual_evidence
        from app.production.visual_observation import observe_chapter_frames
        try:
            route = routes[0]
            provider = create_provider(route.provider, route.provider_model_id)
            step = evidence.asset.duration / (request.frame_count + 1)
            stamps = [round(step * (i + 1), 3) for i in range(request.frame_count)]
            cache = path.parent / CACHE_FILENAME
            retained = {round(n["timestamp_seconds"], 3) for n in load_cached_visual_evidence(cache, evidence.asset, evidence.transcript)}
            with tempfile.TemporaryDirectory(prefix=".frames-", dir=path.parent) as scratch:
                from pathlib import Path
                for index in range(0, len(stamps), 12):
                    selected = stamps[index:index + 12]
                    if all(ts in retained for ts in selected):
                        job["observations"] += len(selected)
                        atomic_json(path, job)
                        continue
                    left = max(0, selected[0] - step / 2)
                    right = min(evidence.asset.duration, selected[-1] + step / 2)
                    frames = _extract_window_frames(evidence.media, left, right, Path(scratch), index, timestamps=selected)
                    if len(frames) != len(selected):
                        raise RepurposeError("PREPARATION_FRAME_MISSING", "Not all requested source frames could be decoded", 422)
                    rows = [{"text": s.text, "start_seconds": s.start, "end_seconds": s.end} for s in evidence.transcript.segments
                            if s.start < right and s.end > left]
                    def dispatch():
                        job["vision_calls"] += 1
                        atomic_json(path, job)
                    # One dispatch per batch. No inherited observer repair/failover loop.
                    notes = observe_chapter_frames(provider, frames, rows, start=left, end=right, continuity="",
                        on_request=dispatch, deadline=time.monotonic() + 120, _single_batch=True)
                    save_frame_observations(cache, evidence.asset, evidence.transcript, frames, notes, start=left, end=right)
                    job["observations"] += len(notes)
                    atomic_json(path, job)
            job["status"] = "ready_for_verification"
        except Exception as exc:
            job.update(status="failed", error={"code": getattr(exc, "code", "PREPARATION_FAILED"),
                "message": str(exc)[:300] if isinstance(exc, RepurposeError) else "Visual preparation stopped. Inspect saved progress; no request was automatically retried."})
        finally:
            job["updated_at"] = timestamp()
            atomic_json(path, job)
            with _GUARD:
                _ACTIVE.discard(str(path))
    if background:
        _WORKERS.submit(work)
    else:
        work()
    return preparation_status(production_id, asset_id, root=root)