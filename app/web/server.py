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
from urllib.parse import parse_qs, urlparse

from app.config import PROJECT_ROOT

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
    ".json": "application/json",
    ".mp4": "video/mp4",
}

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


def _publication_summary(manifest: dict) -> list:
    out = []
    yt = manifest.get("publication") or {}
    if yt.get("state"):
        out.append({
            "platform": "youtube", "state": yt.get("state"),
            "post_id": yt.get("youtube_video_id"), "sample": False,
        })
    tk = manifest.get("tiktok_publication") or {}
    if tk.get("state"):
        out.append({
            "platform": "tiktok", "state": tk.get("state"),
            "publish_id_present": bool(tk.get("tiktok_publish_id")),
            "post_id": tk.get("tiktok_post_id"), "sample": False,
        })
    return out


def projects_index() -> list:
    if not PROJECTS_DIR.exists():
        return []
    items = []
    for child in sorted(PROJECTS_DIR.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        manifest = _load_manifest(child)
        if not manifest:
            continue
        stages = manifest.get("stages") or {}
        stage_status = "running"
        if manifest.get("completed_at"):
            stage_status = "complete"
        elif any(s.get("status") == "FAILED" for s in stages.values()):
            stage_status = "failed"
        final_video = manifest.get("final_video_path")
        items.append({
            "project_id": manifest.get("project_id") or child.name,
            "topic": manifest.get("topic") or child.name,
            "created_at": manifest.get("created_at"),
            "current_stage": str(manifest.get("current_stage", "")),
            "stage_status": stage_status,
            "final_video_available": bool(final_video and Path(final_video).exists()),
            "publications": _publication_summary(manifest),
            "sample": False,
        })
    return items


def project_detail(project_id: str):
    directory = PROJECTS_DIR / project_id
    # Path-traversal guard: resolved dir must stay inside PROJECTS_DIR.
    try:
        directory.resolve().relative_to(PROJECTS_DIR.resolve())
    except (ValueError, OSError):
        return None
    manifest = _load_manifest(directory)
    if not manifest:
        return None
    wanted = manifest.get("project_id")
    for item in projects_index():
        if item["project_id"] == wanted:
            return item
    return None


def media_path_for(project_id: str):
    detail = project_detail(project_id)
    if not detail or not detail.get("final_video_available"):
        return None
    manifest = _load_manifest(PROJECTS_DIR / project_id) or {}
    raw = manifest.get("final_video_path")
    if not raw:
        return None
    path = Path(raw)
    try:
        path.resolve().relative_to(PROJECT_ROOT.resolve())
    except (ValueError, OSError):
        return None
    return path if path.exists() else None


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
# TikTok publish bridge — THIN adapter over the EXISTING state machine
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

    return TikTokPublisher.from_env()


def _prepare(project_id: str, privacy_level=None):
    from app.publishing.tiktok_reconciliation import prepare_tiktok_publish

    return prepare_tiktok_publish(project_id, privacy_level=privacy_level)


def _submit(project_id: str, publisher):
    from app.publishing.tiktok_reconciliation import submit_tiktok_publish

    return submit_tiktok_publish(project_id, publisher)


def _verify(project_id: str, publisher):
    from app.publishing.tiktok_reconciliation import verify_tiktok_publish

    return verify_tiktok_publish(
        project_id, publisher, poll_attempts=5, poll_interval_seconds=3.0
    )


def _reconcile(project_id: str, publisher=None):
    from app.publishing.tiktok_reconciliation import reconcile_tiktok_publication

    return reconcile_tiktok_publication(project_id, publisher=publisher)


def _current_record(project_id: str):
    """Read the durable TikTok record from THIS bridge's project directory
    (honours test isolation; read-only, never mutates anything)."""
    from app.orchestrator.models import ProjectManifest
    from app.publishing.tiktok_reconciliation import get_tiktok_record

    directory = PROJECTS_DIR / project_id
    try:
        directory.resolve().relative_to(PROJECTS_DIR.resolve())
    except (ValueError, OSError):
        return None
    data = _load_manifest(directory)
    if not data:
        return None
    try:
        manifest = ProjectManifest.from_dict(data)
    except Exception as exc:
        logger.warning("Could not parse manifest for %s: %s", project_id, exc)
        return None
    return get_tiktok_record(manifest)


def _tiktok_connected(publisher) -> bool:
    """Non-interactive connectivity check; NEVER opens a browser."""
    try:
        return publisher.validate_stored_token() is not None
    except Exception:
        return False


def _record_payload(record) -> dict:
    meta = getattr(record, "tiktok_metadata", None)
    return {
        "state": record.state.value if record else None,
        "publish_id": getattr(record, "tiktok_publish_id", None),
        "post_id": getattr(record, "tiktok_post_id", None),
        "privacy_level": meta.privacy_level if meta else None,
        "error": getattr(record, "error", None),
        "verified_at": getattr(record, "verified_at", None),
        "sample": False,
    }


def tiktok_status_payload(project_id: str):
    """
    Truthful, repeatable status. Reconciles through the existing engine only
    when a stored token is valid AND a durable publish_id exists in a
    non-terminal state (fetch_status is read-only). Otherwise returns the
    durable stored state untouched. Never uploads, never creates identities,
    never launches OAuth.
    """
    detail = project_detail(project_id)
    if detail is None:
        return 404, {"detail": "project not found"}

    record = _current_record(project_id)
    publisher = _new_publisher()
    connected = _tiktok_connected(publisher)

    state = record.state.value if record else None
    note = None
    if connected and record is not None and record.tiktok_publish_id \
            and state not in ("VERIFIED", "VERIFIED_SANDBOX_PRIVATE", "FAILED",
                              "UNKNOWN_RECONCILIATION_REQUIRED"):
        try:
            report = _reconcile(project_id, publisher=publisher)
            state = report.state or state
            note = "; ".join(report.reasons) or None
        except Exception as exc:
            logger.warning("status reconciliation failed truthfully: %s", exc)
            note = f"live reconciliation unavailable: {exc}"

    meta = record.tiktok_metadata if record else None
    return 200, {
        "project_id": project_id,
        "connected": connected,
        "state": state,
        "can_publish": bool(
            connected
            and detail.get("stage_status") == "complete"
            and state in (None, "NOT_STARTED", "PREPARED", "FAILED")
        ),
        "publish_id": record.tiktok_publish_id if record else None,
        "post_id": record.tiktok_post_id if record else None,
        "privacy_level": meta.privacy_level if meta else None,
        "error": record.error if record else None,
        "note": note,
        "sample": False,
    }


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
            return 409, {
                "error": "TIKTOK_NOT_CONNECTED",
                "detail": "TikTok is not connected on this machine; connect via "
                          "Platforms first. Nothing was uploaded.",
                "state": stored.state.value if stored else None,
            }

        # Offline pre-check through authoritative reconciliation (no network).
        report = _reconcile(project_id, publisher=None)
        verdict = report.verdict.value
        if verdict == "PUBLICATION_VERIFIED":
            payload = _record_payload(_current_record(project_id))
            payload.update({
                "published_already": True,
                "detail": "Already published and verified; no second submission.",
            })
            return 200, payload
        if verdict == "PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED":
            return 409, {
                "error": "UNKNOWN_RECONCILIATION_REQUIRED",
                "detail": "A previous submission may have reached TikTok without "
                          "a durable identity. Blind retry is forbidden; manual "
                          "reconciliation required.",
                "reasons": report.reasons,
            }
        if verdict == "PUBLICATION_FAILED":
            return 409, {
                "error": "PREVIOUS_FAILURE",
                "detail": "A previous publication failed; inspect the manifest "
                          "before any new attempt.",
                "reasons": report.reasons,
            }

        # Existing eligibility gate + artifact fingerprint binding.
        record = _prepare(project_id, privacy_level=privacy_level)

        # Creator capability check — mirrors the existing operator flow.
        try:
            creator = publisher.get_creator_info() or {}
            options = creator.get("privacy_level_options") or []
            wanted = record.tiktok_metadata.privacy_level
            if options and wanted not in options:
                return 409, {
                    "error": "PRIVACY_LEVEL_NOT_PERMITTED",
                    "detail": f"Privacy level {wanted} is not permitted for this "
                              f"account {options}. Nothing was submitted.",
                    "allowed_privacy_levels": options,
                }
        except Exception as exc:
            logger.info("creator capability check skipped truthfully: %s", exc)

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
        name = type(exc).__name__
        detail = str(exc)
        mapping = {
            "PublishingEligibilityError": (422, "NOT_ELIGIBLE"),
            "MetadataValidationError": (422, "METADATA_INVALID"),
            "DuplicateUploadBlockedError": (409, "DUPLICATE_BLOCKED"),
            "UploadIdentityMissingError": (409, "UNKNOWN_RECONCILIATION_REQUIRED"),
            "TikTokAuthError": (502, "AUTH_FAILED"),
        }
        if name in mapping:
            status, error = mapping[name]
            return status, {"error": error, "detail": detail}
        logger.exception("TikTok publish failed")
        return 502, {
            "error": "SUBMISSION_FAILED",
            "detail": detail,
            "note": "If an init succeeded before failing, its durable publish_id "
                    "is already persisted; do NOT retry blindly.",
        }
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
            f"{type(exc).__name__}: {exc}"
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

    return ProductionExecutor(
        production_id,
        productions_root=PRODUCTIONS_DIR,
        projects_root=PROJECTS_DIR,
    )


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
        "message": f"ProductionExecutor raised {type(exc).__name__}: {exc}. "
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
    }


def _state_counts(report) -> dict:
    """Counts keyed by the Phase 10 six-state vocabulary, verbatim."""
    from app.orchestrator.production_execution import UnitExecutionState

    counts = report.counts()
    return {state.value: counts[state] for state in UnitExecutionState}


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
            evidence["meta"] = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            evidence["meta_error"] = f"malformed plan metadata: {exc}"
    return evidence


def _manifest_payload(manifest) -> dict:
    """Read-only, secret-free snapshot of a bound project manifest."""
    data = manifest.model_dump()
    stages = {}
    for name, result in manifest.stages.items():
        status = getattr(result.status, "value", str(result.status))
        stages[name.upper()] = {
            "status": str(status).upper(),
            "error": result.error,
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "duration_seconds": result.duration_seconds,
        }
    # Config snapshots and the Flow project URL are deliberately omitted:
    # they are runtime transport detail, never operator inspection surface.
    return {
        "project_id": manifest.project_id,
        "topic": manifest.topic,
        "created_at": manifest.created_at,
        "updated_at": manifest.updated_at,
        "completed_at": manifest.completed_at,
        "current_stage": str(getattr(manifest.current_stage, "value", manifest.current_stage)),
        "dry_run": manifest.dry_run,
        "mock_mode": manifest.mock_mode,
        "stages": stages,
        "scene_checkpoints": data.get("scene_checkpoints") or {},
        "production_binding": data.get("production_binding"),
        "publications": _publication_summary(data),
        "artifacts": {
            "content_plan_path": manifest.content_plan_path,
            "voice_audio_path": manifest.voice_audio_path,
            "voice_timing_path": manifest.voice_timing_path,
            "scene_manifest_path": manifest.scene_manifest_path,
            "scene_video_paths": list(manifest.scene_video_paths or []),
            "captions_path": manifest.captions_path,
            "final_video_path": manifest.final_video_path,
        },
    }


def _failure_details(manifest) -> list:
    """Failing stages with truthful error text (empty when healthy)."""
    failing = []
    for name, result in manifest.stages.items():
        status = getattr(result.status, "value", str(result.status))
        if str(status).upper() == "FAILED":
            failing.append({
                "stage": name.upper(),
                "error": result.error,
                "completed_at": result.completed_at,
            })
    return failing


def _scene_checkpoint_rows(scene_checkpoints: dict) -> list:
    """Table-friendly rows; the raw checkpoint dict stays available untouched."""
    rows = []
    for scene_id, checkpoint in sorted(scene_checkpoints.items()):
        if not isinstance(checkpoint, dict):
            continue
        evidence = checkpoint.get("checkpoint_evidence") or {}
        rows.append({
            "scene_id": scene_id,
            "status": checkpoint.get("status"),
            "provider": checkpoint.get("provider") or evidence.get("provider"),
            "job_id": checkpoint.get("job_id"),
            "polling_url": checkpoint.get("polling_url"),
            "generation_cost": checkpoint.get("generation_cost"),
            "video_path": checkpoint.get("video_path") or evidence.get("video_path"),
            "updated_at": checkpoint.get("updated_at") or checkpoint.get("timestamp"),
        })
    return rows


# ---------------------------------------------------------------------------
# Phase 11 — read-only inspection payloads
# ---------------------------------------------------------------------------


def productions_index_payload():
    """Production overview: intent + freshly derived aggregate progress."""
    from app.production.storage import list_productions

    items = []
    for production_id in list_productions(root=PRODUCTIONS_DIR):
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
            "created_at": production.created_at,
            "revised_at": production.revised_at,
            "sample": False,
        }
        try:
            report = _derive_progress(production_id)
            item["counts"] = _state_counts(report)
            item["complete"] = report.complete
        except Exception as exc:
            logger.warning(
                "progress derivation failed truthfully for %s: %s", production_id, exc
            )
            item["counts"] = None
            item["error"] = f"progress derivation failed: {type(exc).__name__}: {exc}"
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
        "command": production.command.model_dump(),
        "plan_version": production.plan_version,
        "created_at": production.created_at,
        "revised_at": production.revised_at,
        "unit_count": len(production.units),
        "counts": _state_counts(report),
        "complete": report.complete,
        "units": units,
        "run": dict(_production_runs.get(production.production_id) or {"running": False}),
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
        report = executor.derive_progress()
        bindings = executor.discover_bindings(production)
    except Exception as exc:
        return 500, _executor_error(exc)
    progress_by_id = {
        item.unit_id: _unit_progress_dict(item) for item in report.units
    }
    bound = bindings.get(unit_id)
    manifest_payload = _manifest_payload(bound.manifest) if bound else None
    payload = {
        "production_id": production_id,
        "production_title": production.title,
        "plan_version": production.plan_version,
        "unit_intent": _unit_intent(unit),
        "execution": progress_by_id.get(unit_id),
        "blocking_units": _blocking_units(unit, progress_by_id),
        "plan_evidence": _plan_evidence(production_id, unit_id),
        "bound_project": manifest_payload,
        "scene_checkpoint_rows": (
            _scene_checkpoint_rows(manifest_payload["scene_checkpoints"])
            if manifest_payload else []
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
        "reconciliation": report.to_dict(),
        "persisted": False,
        "sample": False,
    }
    return 200, _redact_secrets(payload)


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
            "started_at": datetime.now().isoformat(),
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
                    "error": f"{type(exc).__name__}: {exc}",
                })

    threading.Thread(target=worker, daemon=True).start()
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


def unit_publish_payload(production_id: str, unit_id: str, body: dict):
    """Publish Unit — thin adapter over the EXISTING publishing bridge."""
    try:
        production = _production_or_none(production_id)
    except ValueError as exc:
        return 500, _intent_malformed(production_id, exc)
    if production is None:
        return 404, _production_not_found(production_id)
    if not body.get("confirm"):
        return 400, _confirmation_required(
            f"POST /api/productions/{production_id}/units/{unit_id}/publish"
        )
    try:
        report = _derive_progress(production_id)
    except Exception as exc:
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
        return 400, {
            "error": "UNIT_NOT_BOUND",
            "production_id": production_id,
            "unit_id": unit_id,
            "message": "Unit is COMPLETED but has no bound execution project.",
        }

    platforms = body.get("platforms") or ["tiktok"]
    unsupported = [item for item in platforms if item != "tiktok"]
    if unsupported:
        return 400, {
            "error": "PUBLISH_PLATFORM_NOT_SUPPORTED",
            "production_id": production_id,
            "unit_id": unit_id,
            "platforms": platforms,
            "message": (
                "No existing web publishing adapter for "
                f"{', '.join(unsupported)}; Phase 11 only delegates to the "
                "existing publishing flows. YouTube publishing remains a "
                "produce.py flow in this phase."
            ),
        }

    # Delegate to the EXISTING Phase 7A/8A web publishing bridge (same code
    # path as POST /api/projects/<id>/publish/tiktok). All durability,
    # duplicate protection, and truthful errors belong to that bridge.
    status, result = tiktok_publish_now(
        project_id, privacy_level=body.get("privacy_level")
    )
    payload = {
        "status": "published" if status == 200 else "failed",
        "production_id": production_id,
        "unit_id": unit_id,
        "project_id": project_id,
        "platforms": platforms,
        "result": result,
        "message": (
            "Delegated to the existing publishing flow; the durable "
            "publication state machine owns all outcomes."
        ),
    }
    return status, _redact_secrets(payload)


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

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
        """Best-effort JSON object body ({} when absent or malformed)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
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

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/health":
                return self._json(200, {"ok": True, "service": "avm-web"})
            if path == "/api/connections":
                return self._json(200, connections_payload())
            if path == "/api/projects":
                return self._json(200, projects_index())
            match = re.match(r"^/api/projects/([^/]+)/publish/tiktok/status$", path)
            if match:
                status, payload = tiktok_status_payload(match.group(1))
                return self._json(status, payload)
            match = re.match(r"^/api/projects/([^/]+)/media$", path)
            if match:
                return self._serve_media(match.group(1))
            match = re.match(r"^/api/projects/([^/]+)/?$", path)
            if match:
                detail = project_detail(match.group(1))
                if detail:
                    return self._json(200, detail)
                return self._json(404, {"detail": "not found"})
            if path.startswith("/api/productions"):
                return self._productions_get(path)
            return self._static(path)
        except Exception:
            logger.exception("GET %s failed", path)
            return self._json(500, {"detail": "internal error"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            match = re.match(r"^/api/platforms/(youtube|tiktok)/connect$", path)
            if match:
                status, payload = _start_connect(match.group(1))
                return self._json(status, payload)
            match = re.match(r"^/api/projects/([^/]+)/publish/tiktok$", path)
            if match:
                # Explicit, deliberate publishing request only.
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length > 0 else b"{}"
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                status, payload = tiktok_publish_now(
                    match.group(1), privacy_level=body.get("privacy_level")
                )
                return self._json(status, payload)
            if path.startswith("/api/productions"):
                return self._productions_post(path)
            return self._json(404, {"detail": "not found"})
        except Exception as exc:
            # F-02 (audit): unexpected POST failures now return the truthful
            # JSON 500 envelope instead of dropping the connection. Known
            # validation errors are handled inside the payload functions and
            # never reach this branch; nothing is retried automatically.
            logger.exception("POST %s failed", path)
            return self._json(500, {
                "error": "INTERNAL_ERROR",
                "message": f"unexpected server error: {type(exc).__name__}: {exc}",
                "guidance": (
                    "The command was not retried. Check the engine log for "
                    "the full traceback before retrying manually."
                ),
            })

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
