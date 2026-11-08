"""Durable opt-in completion publishing, separate from generation and OAuth.

No GET or browser completion callback dispatches work. Explicit consent and real
completion commands own admission. Claims survive restarts; an ambiguous claim
can only reconcile an existing identity, never purchase another upload.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
import math

from app.content.exceptions import safe_provider_text
from app.production.storage import load_production, unit_plan_dir, _atomic_write
from app.publishing.models import (
    CompletionDispatch, CompletionPublication, CompletionPublishIntent, CompletionPublishTarget, utc_now_iso,
)

logger = logging.getLogger(__name__)
_LOCKS = {}
_LOCKS_LOCK = threading.Lock()
_RUNNING = set()
_RUNNING_LOCK = threading.Lock()


def completion_path(production_id, unit_id, *, root=None):
    if not all(isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value) for value in (production_id, unit_id)):
        raise ValueError("Invalid production/unit identity")
    return unit_plan_dir(production_id, unit_id, root=root).parent / "publishing" / "completion.json"


@contextmanager
def _locked(path):
    """A process lock plus an OS file lock serialize consent/dispatch on Windows."""
    with _LOCKS_LOCK:
        lock = _LOCKS.setdefault(str(path.resolve()), threading.RLock())
    if not lock.acquire(blocking=False):
        raise BlockingIOError("Completion publishing is active; inspect before changing consent")
    handle = None
    acquired = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.with_suffix(".lock").open("a+b")
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        yield
    finally:
        if handle is not None:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        lock.release()


def _load(path):
    return CompletionPublication.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None


def _save(path, record):
    record.dispatch.updated_at = utc_now_iso()
    for attempt in range(5):
        try:
            _atomic_write(path, record.model_dump_json(indent=2))
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(.1 * (attempt + 1))


def completion_status(production_id, unit_id, *, root=None):
    """Strictly passive, including when an authorized dispatch is pending."""
    record = _load(completion_path(production_id, unit_id, root=root))
    return record.model_dump() if record else {"enabled": False, "state": "disabled", "production_id": production_id, "unit_id": unit_id}


def generation_revision(production, unit_id, recipe):
    unit = next(unit for unit in production.units if unit.unit_id == unit_id)
    evidence = {"plan_version": production.plan_version, "unit": unit.model_dump(exclude={"plan_reference"}),
        "sources": [{"asset_id": asset.asset_id, "sha256": asset.sha256, "duration": asset.duration} for asset in production.source_assets],
        "recipe": recipe,
        "metadata_config": {name: os.getenv(name, "") for name in ("YOUTUBE_TAGS", "YOUTUBE_CATEGORY_ID", "YOUTUBE_MADE_FOR_KIDS")}}
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _recipe(body):
    recipe = body.get("recipe") or {}
    allowed = {"source_asset_id", "summary_mode", "model_id", "target_length_preset", "target_minutes"}
    if not isinstance(recipe, dict) or set(recipe) - allowed:
        raise ValueError("Unsupported completion generation recipe")
    if any((value is not None and not isinstance(value, (str, int, float))) or isinstance(value, bool) for value in recipe.values()):
        raise ValueError("Invalid completion recipe values")
    if recipe and (not isinstance(recipe.get("source_asset_id"), str) or not re.fullmatch(r"src-\d{3}", recipe["source_asset_id"])
                   or recipe.get("summary_mode") not in {"standard", "pro"} or not recipe.get("model_id")):
        raise ValueError("Summary consent requires source, mode and canonical model identity")
    if "target_minutes" in recipe and (not isinstance(recipe["target_minutes"], (int, float))
        or not math.isfinite(recipe["target_minutes"]) or not 0 < recipe["target_minutes"] <= 35):
        raise ValueError("Summary target minutes must be finite and at most 35")
    return recipe


def configure_completion(context, production_id, unit_id, body):
    if not isinstance(body, dict) or set(body) - {"enabled", "confirm", "targets", "promote_summary", "recipe", "mock_mode"}:
        raise ValueError("Completion consent accepts only explicit destinations and generation settings")
    if type(body.get("enabled")) is not bool:
        raise ValueError("enabled must be an explicit JSON boolean")
    if body["enabled"] and (body.get("confirm") is not True or body.get("mock_mode") is True):
        raise ValueError("Real Auto-Publish requires explicit consent; sandbox cannot publish")
    if type(body.get("promote_summary", False)) is not bool:
        raise ValueError("promote_summary must be an explicit boolean")
    production = load_production(production_id, root=context.PRODUCTIONS_DIR)
    if not any(unit.unit_id == unit_id for unit in production.units):
        raise ValueError("Unit not found")
    path = completion_path(production_id, unit_id, root=context.PRODUCTIONS_DIR)
    with _locked(path):
        previous = _load(path)
        if previous and previous.dispatch.claims and body["enabled"]:
            raise ValueError("A prior dispatch claim is protected. Reconcile it in Export; use a new unit for a new upload")
        recipe = _recipe(body) if body["enabled"] else previous.intent.recipe if previous else {}
        targets = [CompletionPublishTarget.model_validate(target) for target in body.get("targets", [])] if body["enabled"] else []
        if body["enabled"]:
            accounts = context.connections_payload()
            for target in targets:
                account = accounts.get(target.platform) or {}
                if account.get("status") != "CONNECTED" or account.get("account_id") != target.account_id:
                    raise ValueError("Reconnect required: consent must bind the currently verified account")
            if recipe and not any(asset.asset_id == recipe["source_asset_id"] for asset in production.source_assets):
                raise ValueError("Summary consent source is not registered to this production")
        intent = CompletionPublishIntent(enabled=body["enabled"], production_id=production_id, unit_id=unit_id,
            generation_revision=generation_revision(production, unit_id, recipe), recipe=recipe,
            consent_revision=previous.intent.consent_revision + 1 if previous else 1,
            consent_at=utc_now_iso(), targets=targets, promote_summary=body.get("promote_summary", False))
        dispatch = previous.dispatch if previous and previous.dispatch.claims else CompletionDispatch()
        dispatch.state = "armed" if intent.enabled else "disabled"
        history = list(previous.consent_history) + [previous.intent.model_dump()] if previous else []
        record = CompletionPublication(intent=intent, dispatch=dispatch, consent_history=history)
        _save(path, record)
    return record.model_dump()


def _recipe_matches(context, intent):
    if not intent.recipe:
        return True
    from app.production.storage import load_narration_artifact
    try:
        narration = load_narration_artifact(intent.production_id, intent.unit_id, root=context.PRODUCTIONS_DIR)
    except FileNotFoundError:
        return False
    actual = {"source_asset_id": narration.source_asset_id, "summary_mode": narration.summary_mode,
        "model_id": narration.summary_understanding_model_id, "target_length_preset": narration.target_length_preset}
    if narration.status not in {"grounded", "approved"}:
        return False
    for key, value in intent.recipe.items():
        if value is None:
            continue
        if key == "target_minutes":
            budget = narration.narration_budget
            if budget is None or abs((budget.requested_duration_seconds or budget.target_duration_seconds) - value * 60) > .01:
                return False
        elif actual[key] != value:
            return False
    return True


def _metadata_hash(context, project_id, target):
    from app.orchestrator.models import ProjectManifest
    from app.publishing.reconciliation import build_youtube_metadata
    from app.publishing.tiktok_reconciliation import build_tiktok_metadata
    directory = context._publishing_project_directory(project_id)
    manifest = ProjectManifest.from_dict(context._load_manifest(directory))
    metadata = (build_youtube_metadata(manifest, directory, privacy_status=target.privacy) if target.platform == "youtube"
                else build_tiktok_metadata(manifest, directory, privacy_level=target.privacy))
    return hashlib.sha256(metadata.model_dump_json().encode()).hexdigest()


def _reconcile_claim(context, project_id, platform, expected_account_id):
    current = context._current_youtube_record if platform == "youtube" else context._current_record
    stored = current(project_id)
    identity = getattr(stored, "youtube_video_id" if platform == "youtube" else "tiktok_publish_id", None)
    if not identity:
        return {"state": "UNKNOWN_RECONCILIATION_REQUIRED", "error": "INSPECTION_REQUIRED",
                "detail": "A durable dispatch claim exists without a platform identity. No upload was retried."}
    if context._publish_lock.acquire(blocking=False):
        try:
            publisher = context._new_youtube_publisher() if platform == "youtube" else context._new_publisher()
            connected = context._youtube_connected(publisher) if platform == "youtube" else context._tiktok_connected(publisher)
            if connected:
                account = publisher.get_channel() if platform == "youtube" else publisher.get_user()
                if (account or {}).get("id" if platform == "youtube" else "open_id") != expected_account_id:
                    payload = context._publication_payload(current(project_id), platform)
                    payload.update(error="ACCOUNT_CHANGED", reconnect_required=True, note="Reconcile with the originally authorized account.")
                    return context._sanitize_publishing_payload(payload)
                reconcile = context._youtube_reconcile if platform == "youtube" else context._reconcile
                reconcile(project_id, publisher=publisher)
            else:
                payload = context._publication_payload(current(project_id), platform)
                payload.update(connected=False, reconnect_required=True, note="Reconnect required to verify the existing platform identity.")
                return context._sanitize_publishing_payload(payload)
        except Exception as exc:
            payload = context._publication_payload(current(project_id), platform)
            payload.update(reconnect_required=True, note=safe_provider_text(exc))
            return context._sanitize_publishing_payload(payload)
        finally:
            context._publish_lock.release()
    return context._sanitize_publishing_payload(context._publication_payload(current(project_id), platform))


def _source_is_real(context, intent):
    from app.production.storage import load_narration_artifact
    try:
        narration = load_narration_artifact(intent.production_id, intent.unit_id, root=context.PRODUCTIONS_DIR)
        return narration.status in {"grounded", "approved"} and narration.audio_provider != "mock"
    except FileNotFoundError:
        return not intent.promote_summary


def run_completion(context, production_id, unit_id):
    path = completion_path(production_id, unit_id, root=context.PRODUCTIONS_DIR)
    if not path.is_file():
        return None  # Default off: no new file or provider call.
    try:
        with _locked(path):
            record = _load(path)
            if not record or not record.intent.enabled:
                return None
            intent, dispatch = record.intent, record.dispatch
            production = load_production(production_id, root=context.PRODUCTIONS_DIR)
            if generation_revision(production, unit_id, intent.recipe) != intent.generation_revision:
                dispatch.state, dispatch.reason = "stale", "Generation settings changed. Authorize Auto-Publish again for this revision."
                _save(path, record)
                return record.model_dump()
            executor = context._production_executor(production_id)
            bound = executor.discover_bindings(production).get(unit_id)
            if intent.promote_summary and not dispatch.claims and context._unit_has_summary(production_id, unit_id):
                if not _source_is_real(context, intent) or not _recipe_matches(context, intent):
                    raise ValueError("Summary recipe or real narration differs from consent; no promotion/upload was sent")
                status, result = context.summary_video_promote_payload(production_id, unit_id)
                if status not in (200, 201):
                    raise ValueError(result.get("message") or "Summary promotion refused")
                bound = executor.discover_bindings(production).get(unit_id)
            if bound is None or not context._publishing_project_complete(bound.directory_id):
                dispatch.state, dispatch.reason = "waiting_completion", "Waiting for a canonical completed master."
                _save(path, record)
                return record.model_dump()
            if bound.manifest.mock_mode or not _source_is_real(context, intent) or (intent.recipe and not _recipe_matches(context, intent)):
                raise ValueError("Sandbox or changed summary recipe cannot publish under this consent")
            project_id = bound.directory_id
            from app.publishing.reconciliation import compute_file_fingerprint
            media = Path(bound.manifest.final_video_path or "")
            if not media.is_absolute():
                media = context._publishing_project_directory(project_id) / media
            media = media.resolve()
            media.relative_to(context._publishing_project_directory(project_id).resolve())
            fingerprint, _ = compute_file_fingerprint(media)
            if dispatch.project_id and (dispatch.project_id != project_id or dispatch.file_fingerprint != fingerprint):
                dispatch.state, dispatch.reason = "stale", "Accepted master changed; prior publication claims remain protected."
                _save(path, record)
                return record.model_dump()
            dispatch.project_id, dispatch.file_fingerprint = project_id, fingerprint
            for target in intent.targets:
                platform = target.platform
                if platform in dispatch.claims:
                    dispatch.outcomes[platform] = _reconcile_claim(context, project_id, platform, target.account_id)
                    _save(path, record)
                    continue
                status, readiness = context.publication_readiness_payload(project_id, platform, privacy=target.privacy)
                if status != 200 or readiness.get("can_publish") is not True:
                    dispatch.outcomes[platform] = {"state": "BLOCKED", "reconnect_required": readiness.get("reconnect_required", False),
                        "error": readiness.get("reason_code") or "READINESS_REQUIRED", "detail": readiness.get("note") or "Readiness failed; reconnect or inspect Export."}
                    _save(path, record)
                    continue
                if readiness.get("account_id") != target.account_id:
                    dispatch.outcomes[platform] = {"state": "BLOCKED", "error": "ACCOUNT_CHANGED", "detail": "Account differs from consent. Reauthorize explicitly."}
                    _save(path, record)
                    continue
                digest = _metadata_hash(context, project_id, target)
                if platform in dispatch.metadata_fingerprints and dispatch.metadata_fingerprints[platform] != digest:
                    raise ValueError("Metadata changed after dispatch preparation; authorize again")
                dispatch.metadata_fingerprints[platform] = digest
                dispatch.state = "dispatching"
                _save(path, record)
                def claim_submission():
                    # The shared service invokes this only after lock/preflight
                    # admission, but BEFORE the existing upload engine starts.
                    dispatch.claims[platform] = utc_now_iso()
                    dispatch.claim_consents[platform] = intent.consent_revision
                    _save(path, record)
                from app.publishing.service import publish_project
                status, outcome = publish_project(context, project_id, platform, privacy=target.privacy,
                    expected_account_id=target.account_id, expected_fingerprint=fingerprint, expected_metadata=digest,
                    on_submission=claim_submission)
                dispatch.outcomes[platform] = context._sanitize_publishing_payload({**outcome, "http_status": status})
                _save(path, record)
            success = all((dispatch.outcomes.get(target.platform) or {}).get("state") in {"VERIFIED", "VERIFIED_SANDBOX_PRIVATE"}
                          and not (dispatch.outcomes[target.platform].get("error") or dispatch.outcomes[target.platform].get("reconnect_required"))
                          for target in intent.targets)
            dispatch.state = "finished" if success else "attention"
            dispatch.reason = None if success else "Production is complete. Inspect publishing outcomes; reconnect/reconcile explicitly where required."
            _save(path, record)
            return record.model_dump()
    except BlockingIOError:
        return None
    except Exception as exc:
        logger.warning("Completion publishing paused: production=%s unit=%s type=%s", production_id, unit_id, type(exc).__name__)
        try:
            with _locked(path):
                record = _load(path)
                if record:
                    record.dispatch.state, record.dispatch.reason = "attention", safe_provider_text(exc)
                    _save(path, record)
                    return record.model_dump()
        except (OSError, ValueError):
            pass
        return None


def schedule_completion(context, production_id, unit_id):
    """Called by explicit mutation/completion paths only, never inspection GETs."""
    path = completion_path(production_id, unit_id, root=context.PRODUCTIONS_DIR)
    if not path.is_file():
        return False
    try:
        record = _load(path)
    except (OSError, ValueError):
        logger.warning("Malformed completion consent skipped: production=%s unit=%s", production_id, unit_id)
        return False
    if not record or not record.intent.enabled:
        return False
    key = str(path.resolve())
    with _RUNNING_LOCK:
        if key in _RUNNING:
            return False
        _RUNNING.add(key)
    def worker():
        try:
            run_completion(context, production_id, unit_id)
        finally:
            with _RUNNING_LOCK:
                _RUNNING.discard(key)
    try:
        threading.Thread(target=worker, daemon=True).start()
    except RuntimeError:
        with _RUNNING_LOCK:
            _RUNNING.discard(key)
        logger.warning("Completion dispatch deferred until recovery: production=%s unit=%s", production_id, unit_id)
        return False
    return True


def retry_authorized_completions_after_connect(context, platform):
    """Explicit reconnect unblocks only existing, still-current consent."""
    for path in Path(context.PRODUCTIONS_DIR).glob("*/units/*/publishing/completion.json"):
        try:
            record = _load(path)
            if record and record.intent.enabled and record.dispatch.state == "attention" and any(
                target.platform == platform for target in record.intent.targets):
                schedule_completion(context, record.intent.production_id, record.intent.unit_id)
        except (OSError, ValueError):
            logger.warning("Invalid existing publishing consent skipped after reconnect")


def recover_completions(context):
    """One bounded startup sweep of previously authorized units, no scheduler."""
    root = Path(context.PRODUCTIONS_DIR)
    for path in root.glob("*/units/*/publishing/completion.json"):
        try:
            record = _load(path)
            if record and record.intent.enabled and record.dispatch.state not in {"finished", "stale", "disabled"}:
                run_completion(context, record.intent.production_id, record.intent.unit_id)
        except (OSError, ValueError):
            logger.warning("Invalid completion consent skipped during startup recovery")