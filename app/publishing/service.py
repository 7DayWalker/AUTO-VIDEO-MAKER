"""Shared publishing orchestration for manual commands and completion dispatch.

The context injects path/binding and presentation adapters; the existing
YouTube/TikTok engines alone own preparation, upload identities and verification.
No browser, retry loop or independent publication state machine lives here.
"""
from __future__ import annotations

import logging

from app.publishing.models import TikTokAuthError

logger = logging.getLogger(__name__)


def publish_project(context, project_id, platform, *, privacy=None, expected_account_id=None,
                    expected_fingerprint=None, expected_metadata=None, on_submission=None):
    if platform not in ("youtube", "tiktok"):
        return 400, {"error": "PUBLISH_PLATFORM_NOT_SUPPORTED"}
    if not context._publish_lock.acquire(blocking=False):
        return 409, {"error": "PUBLISH_ALREADY_RUNNING", "detail": "Another publishing operation is active."}
    from app.publishing.connections import _CREDENTIAL_LOCKS
    credential_lock = _CREDENTIAL_LOCKS[platform]
    if not credential_lock.acquire(blocking=False):
        context._publish_lock.release()
        return 409, {"error": "CONNECTION_IN_PROGRESS", "detail": "Complete the explicit account connection before publishing."}
    youtube = platform == "youtube"
    current = context._current_youtube_record if youtube else context._current_record
    payload_for = lambda record: context._publication_payload(record, platform)
    try:
        if context.project_detail(project_id) is None:
            return 404, {"detail": "project not found"}
        publisher = context._new_youtube_publisher() if youtube else context._new_publisher()
        connected = context._youtube_connected(publisher) if youtube else context._tiktok_connected(publisher)
        if not connected:
            payload = payload_for(current(project_id))
            payload.update(error="YOUTUBE_NOT_CONNECTED" if youtube else "TIKTOK_NOT_CONNECTED",
                reconnect_required=True, reason_code=getattr(publisher, "credential_reason", None) or "REAUTHORIZE_REQUIRED",
                detail="Reconnect explicitly from Export. Nothing was uploaded.")
            return 409, payload
        if expected_account_id is not None:
            account = publisher.get_channel() if youtube else publisher.get_user()
            actual = (account or {}).get("id" if youtube else "open_id")
            if actual != expected_account_id:
                return 409, {"error": "ACCOUNT_CHANGED", "detail": "Connected account differs from consent; authorize the new destination explicitly."}
        reconcile = context._youtube_reconcile if youtube else context._reconcile
        report = reconcile(project_id, publisher=None)
        stored = current(project_id)
        payload = payload_for(stored)
        payload["verdict"] = context._enum_value(report.verdict)
        if payload["verdict"] == "PUBLICATION_VERIFIED":
            payload.update(published_already=True, detail="Already verified; no second upload.")
            return 200, payload
        if payload["verdict"] in ("PUBLICATION_FAILED", "PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED"):
            payload.update(error="PREVIOUS_FAILURE" if payload["verdict"] == "PUBLICATION_FAILED" else "UNKNOWN_RECONCILIATION_REQUIRED",
                detail="Inspect and explicitly reconcile the previous publication; blind retry is forbidden.", reasons=report.reasons)
            return 409, context._sanitize_publishing_payload(payload)
        if payload.get("video_id") or payload.get("publish_id") or payload.get("post_id"):
            payload.update(error="EXISTING_PUBLICATION_ID", detail="A durable identity exists; inspect status, do not submit again.")
            return 409, payload
        prepare = context._youtube_prepare if youtube else context._prepare
        record = prepare(project_id, **({"privacy_status": privacy} if youtube else {"privacy_level": privacy}))
        if expected_fingerprint is not None and record.file_fingerprint != expected_fingerprint:
            return 409, {"error": "MASTER_CHANGED", "detail": "Master changed after consent dispatch; no upload was sent."}
        if expected_metadata is not None:
            import hashlib
            metadata = record.youtube_metadata if youtube else record.tiktok_metadata
            digest = hashlib.sha256(metadata.model_dump_json().encode()).hexdigest()
            if digest != expected_metadata:
                return 409, {"error": "METADATA_CHANGED", "detail": "Publishing metadata changed after dispatch preparation; review and authorize again."}
        if not youtube:
            # Preserve the established manual preflight contract; durable
            # completion additionally requires strict readiness before claiming.
            try:
                creator = publisher.get_creator_info() or {}
                options = creator.get("privacy_level_options") or []
                if not isinstance(options, list) or not options:
                    return 422, {"error": "CREATOR_CAPABILITIES_UNAVAILABLE",
                                 "detail": "TikTok privacy capabilities could not be verified. Nothing was submitted."}
                if record.tiktok_metadata.privacy_level not in options:
                    return 409, {"error": "PRIVACY_LEVEL_NOT_PERMITTED", "allowed_privacy_levels": options,
                        "detail": "Selected privacy is not permitted for this account. Nothing was submitted."}
                maximum = creator.get("max_video_post_duration_sec")
                if isinstance(maximum, (int, float)):
                    from app.assembly.ffmpeg import probe_media
                    if probe_media(context.media_path_for(project_id)).duration > maximum:
                        return 409, {"error": "VIDEO_DURATION_NOT_PERMITTED", "detail": "Master exceeds the creator's permitted duration."}
            except TikTokAuthError:
                raise
            except Exception as exc:
                return 422, {"error": "CREATOR_CAPABILITIES_UNAVAILABLE",
                             "detail": context._sanitize_diagnostic_text(exc)}
        if on_submission is not None:
            on_submission()
        if youtube:
            context._youtube_submit(project_id, publisher)
            record = context._youtube_verify(project_id, publisher)
            final = context._youtube_reconcile(project_id, publisher=publisher)
            record = current(project_id) or record
            payload = payload_for(record)
            payload["verdict"] = context._enum_value(final.verdict)
        else:
            context._submit(project_id, publisher)
            record = context._verify(project_id, publisher)
            payload = payload_for(record)
        payload["detail"] = "Handled by the existing publication engine; inspect its durable state and verdict."
        return 200, context._sanitize_publishing_payload(payload)
    except Exception as exc:
        logger.warning("Publishing failed: platform=%s diagnostic=%s", platform, context._sanitize_diagnostic_text(exc))
        return context._publishing_exception_payload(project_id, platform, exc)
    finally:
        credential_lock.release()
        context._publish_lock.release()