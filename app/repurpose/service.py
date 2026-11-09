"""Durable one-call jobs. Reads never trigger work, including after a restart."""
from __future__ import annotations

import hashlib
import threading
import logging
from concurrent.futures import ThreadPoolExecutor

from app.production.storage import commit_repurpose_batch, production_revision_lock
from app.repurpose.cache import load_evidence, sources_payload
from app.repurpose.models import BatchRequest, RenderRequest, RepurposeError, timestamp
from app.repurpose.planner import plan_once, planner_provider
from app.repurpose.storage import atomic_json, batch_dir, content_hash, load_job, load_plan, load_render, save_job, unit_dir

_WORKERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="repurpose")
_ACTIVE = set()
_GUARD = threading.Lock()
logger = logging.getLogger(__name__)


def readiness():
    configured = False
    try:
        planner_provider()
        configured = True
    except (ValueError, RuntimeError):
        pass
    return {"implemented": True, "status": "ready" if configured else "not_configured", "configured": configured,
        "configuration_only": True,
        "model_id": "gpt-6.1-sol", "api_style": "responses", "max_output_tokens": 16384,
        "reasoning": {"effort": "low"}, "modes": ["narrated", "raw_highlight"],
        "framing_presets": ["cinema_canvas", "center_crop"], "count_bounds": [3, 10],
        "duration_bounds": [30, 60], "source_duration_bounds": [60, 14400],
        "default_framing": "cinema_canvas", "cache_only": True, "auto_publish": False}


def _key(p, b, root):
    return str(batch_dir(p, b, root))


def batch_status(production_id, batch_id, *, root=None):
    job = load_job(production_id, batch_id, root)
    with _GUARD:
        active = _key(production_id, batch_id, root) in _ACTIVE
    # A persisted active marker is not proof that its original worker survived.
    if job["status"] in {"planning", "rendering"} and not active:
        job = {**job, "status": "uncertain" if job["status"] == "planning" else "interrupted",
            "error": {"code": "WORKER_INTERRUPTED", "message": "Inspect saved artifacts. Planning is never resubmitted automatically."}}
    path = batch_dir(production_id, batch_id, root) / "plan.json"
    if path.is_file():
        plan = load_plan(production_id, batch_id, root)
        clips = []
        for clip in plan.clips:
            row = clip.model_dump(mode="json")
            row.update(mode=plan.request.mode, framing=plan.request.framing,
                status=job.get("units", {}).get(clip.unit_id, {}).get("status", "planned"),
                error=job.get("units", {}).get(clip.unit_id, {}).get("error"))
            try:
                artifact = load_render(production_id, clip.unit_id, root)
                row["render"] = artifact.model_dump(mode="json")
                row["media_url"] = f"/api/productions/{production_id}/units/{clip.unit_id}/repurpose-video/media"
                from app.repurpose.storage import read_json
                promotion = unit_dir(production_id, clip.unit_id, root) / "promotion.json"
                if promotion.is_file():
                    row["project_id"] = read_json(promotion).get("project_id")
            except FileNotFoundError:
                pass
            clips.append(row)
        job = {**job, "plan_hash": content_hash(plan), "clips": clips}
    return job


def create_batch(production_id, body, *, root=None, background=True, provider=None):
    request = BatchRequest.model_validate(body)
    batch_id = "rpb-" + hashlib.sha256(f"{production_id}\0{request.request_id}".encode()).hexdigest()[:24]
    directory = batch_dir(production_id, batch_id, root)
    with production_revision_lock(production_id, root):
        if (directory / "job.json").is_file():
            job = load_job(production_id, batch_id, root)
            if job["request_hash"] != content_hash(request):
                raise RepurposeError("REQUEST_ID_CONFLICT", "Request ID already belongs to different settings")
            return batch_status(production_id, batch_id, root=root)
        evidence = load_evidence(production_id, request.source_asset_id, request.source_unit_id, root=root)
        if evidence.revision != request.evidence_revision:
            raise RepurposeError("STALE_EVIDENCE", "Reload the source evidence before planning")
        if not request.allow_overlap and evidence.asset.duration < request.count * request.target_seconds:
            raise RepurposeError("INSUFFICIENT_DISTINCT_MOMENTS", "Source is too short for distinct requested clips", 422)
        chosen_provider = provider or planner_provider()
        job = {"production_id": production_id, "batch_id": batch_id, "request_id": request.request_id,
            "request_hash": content_hash(request), "request": request.model_dump(mode="json"), "status": "planning",
            "created_at": timestamp(), "updated_at": timestamp(), "llm_calls": 0,
            "uploads": 0, "stt_calls": 0, "vision_calls": 0, "units": {}, "render_requests": {}}
        save_job(production_id, batch_id, job, root)
        with _GUARD:
            _ACTIVE.add(_key(production_id, batch_id, root))
    def work():
        try:
            def dispatch():
                job["llm_calls"] = 1  # Marker precedes the ONLY provider dispatch.
                save_job(production_id, batch_id, job, root)
            def received(response):
                atomic_json(directory / "planner_response.json", {"model": response.model, "content": response.content})
            plan = plan_once(evidence, request, batch_id, provider=chosen_provider, on_response=received, on_dispatch=dispatch)
            atomic_json(directory / "candidate.json", plan)
            plan = commit_repurpose_batch(plan, root=root)
            job.update(status="planned", plan_hash=content_hash(plan), updated_at=timestamp(),
                units={c.unit_id: {"status": "planned"} for c in plan.clips})
        except Exception as exc:
            logger.warning("Repurpose planning stopped: batch=%s failure=%s (no retry)", batch_id, type(exc).__name__)
            uncertain = getattr(exc, "category", None) == "timeout" or type(exc).__name__ == "LLMConnectionError"
            job.update(status="uncertain" if uncertain else "failed", updated_at=timestamp(),
                error={"code": getattr(exc, "code", "PLANNING_FAILED"),
                       "message": "One-call planning failed; inspect the saved response. No request was retried.", "type": type(exc).__name__})
        finally:
            save_job(production_id, batch_id, job, root)
            with _GUARD:
                _ACTIVE.discard(_key(production_id, batch_id, root))
    if background:
        _WORKERS.submit(work)
        return {**job, "status_url": f"/api/productions/{production_id}/repurpose/batches/{batch_id}"}
    work()
    return batch_status(production_id, batch_id, root=root)


def render_batch(production_id, batch_id, body, *, root=None, background=True, renderer=None):
    request = RenderRequest.model_validate(body)
    with production_revision_lock(production_id, root):
        job = load_job(production_id, batch_id, root)
        plan = load_plan(production_id, batch_id, root)
        if content_hash(plan) != request.expected_plan_hash:
            raise RepurposeError("STALE_PLAN", "Render request does not match the accepted plan")
        if not set(request.unit_ids) <= {c.unit_id for c in plan.clips}:
            raise RepurposeError("UNIT_NOT_IN_BATCH", "Selected unit does not belong to this batch", 400)
        previous = job.get("render_requests", {}).get(request.request_id)
        if previous:
            if previous != content_hash(request):
                raise RepurposeError("REQUEST_ID_CONFLICT", "Render request ID settings changed")
            return batch_status(production_id, batch_id, root=root)
        with _GUARD:
            key = _key(production_id, batch_id, root)
            if key in _ACTIVE:
                raise RepurposeError("BATCH_BUSY", "Batch already has an active worker")
            _ACTIVE.add(key)
        job.setdefault("render_requests", {})[request.request_id] = content_hash(request)
        job.update(status="rendering", updated_at=timestamp())
        save_job(production_id, batch_id, job, root)
    def work():
        from app.repurpose.renderer import render_short

        failed = False
        try:
            for unit_id in request.unit_ids:
                job["units"][unit_id] = {"status": "rendering"}
                save_job(production_id, batch_id, job, root)
                try:
                    (renderer or render_short)(production_id, unit_id, root=root)
                    job["units"][unit_id] = {"status": "rendered"}
                except Exception as exc:
                    logger.exception("Repurpose render failed: unit=%s", unit_id)
                    failed = True
                    job["units"][unit_id] = {"status": "failed", "error": {"code": getattr(exc, "code", "RENDER_FAILED"),
                        "message": str(exc)[:400] if isinstance(exc, RepurposeError) else "Render failed; inspect Core logs. The saved plan is reusable.", "type": type(exc).__name__}}
                save_job(production_id, batch_id, job, root)
            job.update(status="render_failed" if failed else "rendered", updated_at=timestamp())
        finally:
            save_job(production_id, batch_id, job, root)
            with _GUARD:
                _ACTIVE.discard(_key(production_id, batch_id, root))
    if background:
        _WORKERS.submit(work)
        return {"production_id": production_id, "batch_id": batch_id, "status": "rendering"}
    work()
    return batch_status(production_id, batch_id, root=root)


def recover_batch(production_id, batch_id, *, root=None):
    """Explicit recovery of retained response/intent. NEVER creates a provider."""
    from types import SimpleNamespace
    from app.repurpose.planner import plan_from_response
    from app.repurpose.storage import read_json

    with production_revision_lock(production_id, root):
        with _GUARD:
            if _key(production_id, batch_id, root) in _ACTIVE:
                raise RepurposeError("BATCH_BUSY", "An active batch cannot be recovered")
        job = load_job(production_id, batch_id, root)
        request = BatchRequest.model_validate(job["request"])
        directory = batch_dir(production_id, batch_id, root)
        if (directory / "plan.json").is_file():
            plan = load_plan(production_id, batch_id, root)
        else:
            response_path = directory / "planner_response.json"
            if not response_path.is_file():
                raise RepurposeError("PLANNER_OUTCOME_UNCERTAIN", "No retained planner response; no resubmission is permitted")
            evidence = load_evidence(production_id, request.source_asset_id, request.source_unit_id, root=root)
            if evidence.revision != request.evidence_revision:
                raise RepurposeError("STALE_EVIDENCE", "Recovery evidence changed")
            plan = plan_from_response(SimpleNamespace(**read_json(response_path)), evidence, request, batch_id)
        plan = commit_repurpose_batch(plan, root=root)
        job.update(status="planned", plan_hash=content_hash(plan), updated_at=timestamp())
        job.pop("error", None)
        job["units"] = {c.unit_id: job.get("units", {}).get(c.unit_id, {"status": "planned"}) for c in plan.clips}
        save_job(production_id, batch_id, job, root)
        return batch_status(production_id, batch_id, root=root)