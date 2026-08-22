"""
AUTO Video Maker - TikTok Publishing Reconciliation & Safe Submission (Phase 8A)

Sits strictly AFTER the Phase 2G PROJECT_COMPLETE acceptance gate, mirroring
the proven YouTube publishing safety principles while modeling the ACTUAL
TikTok Content Posting API lifecycle:

- The final MP4 is revalidated and fingerprint-bound before preparation.
- SUBMISSION intent is persisted BEFORE the irreversible network operation.
- The direct-post init endpoint returns a DURABLE publish_id BEFORE bytes are
  uploaded; that identity is persisted IMMEDIATELY so an interrupted upload
  can always be reconciled via the official status endpoint instead of
  re-submitting.
- If a submission may have started but no publish_id was persisted, the
  record enters UNKNOWN_RECONCILIATION_REQUIRED; blind resubmission is
  forbidden.
- TikTok processing is asynchronous: VERIFIED requires authoritative status
  evidence (PUBLISH_COMPLETE + public post id), never a mere HTTP success.

State mapping onto the shared PublicationState values:
  NOT_STARTED -> PREPARED -> UPLOAD_SUBMITTED (init about to run)
             -> IDENTITY_PERSISTED (publish_id durable) -> PROCESSING
             -> VERIFIED (PUBLISH_COMPLETE + post id)
"""

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import PROJECT_ROOT
from app.orchestrator.models import ProjectManifest
from app.orchestrator.reconciliation import (
    ArtifactStatus,
    ProjectVerdict,
    _assembly_evidence,
    _path,
    reconcile_project,
)
from app.publishing.models import (
    DEFAULT_TIKTOK_PRIVACY_LEVEL,
    DuplicateUploadBlockedError,
    MetadataValidationError,
    PublicationRecord,
    PublicationState,
    PublicationVerdict,
    PublishingEligibilityError,
    PublishingError,
    TikTokAuthError,
    TikTokMetadata,
    TikTokPublicationRecord,
    UploadIdentityMissingError,
    utc_now_iso,
)
from app.publishing.reconciliation import (
    compute_file_fingerprint,
    project_dir_for,
)
from app.publishing.tiktok import TikTokPublisher

logger = logging.getLogger(__name__)

ORCHESTRATOR_OUTPUT_DIR = PROJECT_ROOT / "output" / "projects"

# States meaning "a submission may already exist on TikTok".
_POST_SUBMISSION_STATES = {
    PublicationState.UPLOAD_SUBMITTED,
    PublicationState.IDENTITY_PERSISTED,
    PublicationState.PROCESSING,
}

# Official TikTok status values observed in the Content Posting API.
_STATUS_STATE = {
    "PUBLISH_COMPLETE": PublicationState.VERIFIED,
    "SEND_TO_INBOX": PublicationState.PROCESSING,
    "PUBLISH_PROCESSING": PublicationState.PROCESSING,
    "FAILED": PublicationState.FAILED,
}


# ---------------------------------------------------------------------------
# Persistence helpers (platform-specific record inside the same manifest)
# ---------------------------------------------------------------------------

def load_manifest(project_id: str, project_dir: Optional[Path] = None):
    directory = project_dir_for(project_id, project_dir)
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        raise PublishingError(f"Project manifest not found: {manifest_path}")
    with open(manifest_path, "r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    return manifest_path, ProjectManifest.from_dict(data)


def get_tiktok_record(manifest: ProjectManifest) -> Optional[TikTokPublicationRecord]:
    record = getattr(manifest, "tiktok_publication", None)
    return record if isinstance(record, TikTokPublicationRecord) else None


def ensure_tiktok_record(manifest: ProjectManifest) -> TikTokPublicationRecord:
    record = get_tiktok_record(manifest)
    if record is None:
        record = TikTokPublicationRecord(project_id=manifest.project_id)
        manifest.tiktok_publication = record
    return record


def persist_tiktok_record(
    manifest_path: Path, manifest: ProjectManifest, record: TikTokPublicationRecord
) -> None:
    record.updated_at = utc_now_iso()
    manifest.tiktok_publication = record
    manifest.updated_at = utc_now_iso()
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Metadata sourcing & validation
# ---------------------------------------------------------------------------

def build_tiktok_metadata(
    manifest: ProjectManifest,
    project_dir: Path,
    privacy_level: Optional[str] = None,
) -> TikTokMetadata:
    """
    Deterministically build the canonical TikTok metadata from already-
    validated project evidence. No content regeneration ever occurs.

    - Caption: production plan title (+ hashtags derived deterministically
      from plan tags/niche when present). Hashtags are part of the caption on
      TikTok — there is no separate tags field in the official API.
    - Privacy: TIKTOK_PRIVACY_LEVEL env with conservative SELF_ONLY default;
      unaudited apps are restricted to private posts by TikTok regardless.
    """
    plan_path = _path(project_dir, manifest.content_plan_path)
    title = manifest.topic
    hashtags: List[str] = []

    if plan_path and plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            title = plan.get("title") or title
            plan_meta = plan.get("metadata") or {}
            raw_tags = plan_meta.get("tags")
            if isinstance(raw_tags, str):
                raw_tags = [raw_tags]
            candidates: List[str] = []
            if isinstance(raw_tags, list):
                candidates.extend(str(t) for t in raw_tags)
            for key in ("niche", "content_goal"):
                value = str(plan_meta.get(key) or "").strip()
                if value:
                    candidates.append(value)
            for candidate in candidates:
                slug = "".join(ch if ch.isalnum() else "" for ch in candidate).lower()
                if slug and f"#{slug}" not in {h.lower() for h in hashtags}:
                    hashtags.append(f"#{slug}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not derive TikTok metadata from plan: %s", exc)

    caption_parts = [title.strip()]
    if hashtags:
        caption_parts.append(" ".join(hashtags))
    caption = " ".join(caption_parts).strip()

    try:
        return TikTokMetadata(
            title=caption,
            privacy_level=(
                privacy_level
                or os.getenv("TIKTOK_PRIVACY_LEVEL", DEFAULT_TIKTOK_PRIVACY_LEVEL)
            ).strip().upper(),
        )
    except Exception as exc:
        raise MetadataValidationError(f"TikTok metadata validation failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Eligibility gate + preparation
# ---------------------------------------------------------------------------

def prepare_tiktok_publish(
    project_id: str,
    project_dir: Optional[Path] = None,
    privacy_level: Optional[str] = None,
) -> TikTokPublicationRecord:
    """
    TikTok eligibility gate: canonical reconciliation must return
    PROJECT_COMPLETE, the final MP4 must revalidate right now, and valid
    metadata must be built — all before anything is submitted.
    """
    report = reconcile_project(project_id, project_dir_for(project_id, project_dir))
    if report.verdict != ProjectVerdict.PROJECT_COMPLETE:
        raise PublishingEligibilityError(
            f"TikTok publishing refused: reconciliation verdict is "
            f"{report.verdict.value} ({'; '.join(report.reasons) or 'no reasons'})"
        )

    directory = project_dir_for(project_id, project_dir)
    manifest_path, manifest = load_manifest(project_id, directory)

    evidence = _assembly_evidence(manifest, directory)
    if evidence.get("status") != ArtifactStatus.VALID.value:
        validation = evidence.get("validation", {})
        error = (
            validation.get("error")
            or "; ".join(validation.get("errors", []))
            or evidence.get("status")
        )
        raise PublishingEligibilityError(
            f"TikTok publishing refused: final artifact invalid: {error}"
        )

    video_path = Path(evidence["path"])
    fingerprint, size = compute_file_fingerprint(video_path)

    record = ensure_tiktok_record(manifest)

    # File-mutation guard: identical semantics to the YouTube path.
    if record.file_fingerprint and record.file_fingerprint != fingerprint:
        if record.state in _POST_SUBMISSION_STATES:
            record.state = PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
            record.error = (
                f"Final artifact changed after submission (prepared "
                f"{record.file_fingerprint}, now {fingerprint}); manual "
                "reconciliation required"
            )
            persist_tiktok_record(manifest_path, manifest, record)
            raise DuplicateUploadBlockedError(record.error)

    metadata = build_tiktok_metadata(manifest, directory, privacy_level=privacy_level)
    record.final_video_path = str(video_path)
    record.file_fingerprint = fingerprint
    record.file_size = size
    record.tiktok_metadata = metadata
    record.prepared_at = utc_now_iso()
    if record.state == PublicationState.NOT_STARTED:
        record.state = PublicationState.PREPARED
    record.error = None
    persist_tiktok_record(manifest_path, manifest, record)

    logger.info(
        "TikTok publishing prepared: project=%s fingerprint=%s size=%d",
        project_id,
        fingerprint,
        size,
    )
    return record


# ---------------------------------------------------------------------------
# Submission (duplicate-post protection)
# ---------------------------------------------------------------------------

def submit_tiktok_publish(
    project_id: str,
    publisher: Any,
    project_dir: Optional[Path] = None,
) -> TikTokPublicationRecord:
    """
    Submit the prepared TikTok direct post exactly once.

    Safety rules enforced here:
    - If a tiktok_publish_id is already persisted, NEVER submit again; resume
      reconciles via the official status endpoint instead.
    - UPLOAD_SUBMITTED without a durable publish_id means a previous init may
      have reached TikTok: enter UNKNOWN_RECONCILIATION_REQUIRED, no retry.
    - UPLOAD_SUBMITTED is persisted BEFORE the init call begins.
    - The publish_id returned by init is persisted IMMEDIATELY (before any
      byte is uploaded), because it is a durable submission identity.
    """
    manifest_path, manifest = load_manifest(project_id, project_dir)
    record = get_tiktok_record(manifest)
    if record is None:
        raise PublishingError("No TikTok publication record; run prepare first")

    # Duplicate protection: submission identity already durable.
    if record.tiktok_publish_id:
        logger.info(
            "TikTok publish_id already persisted (%s); refusing to submit again",
            record.tiktok_publish_id,
        )
        return record

    if record.state in {
        PublicationState.UPLOAD_SUBMITTED,
        PublicationState.UNKNOWN_RECONCILIATION_REQUIRED,
    }:
        record.state = PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
        record.error = (
            "Previous TikTok submission was interrupted before a durable "
            "publish_id could be persisted; refusing blind duplicate submission"
        )
        persist_tiktok_record(manifest_path, manifest, record)
        raise UploadIdentityMissingError(record.error)

    if record.state not in {PublicationState.PREPARED, PublicationState.FAILED}:
        raise PublishingError(
            f"Cannot submit TikTok post from state {record.state.value}; "
            "expected PREPARED"
        )

    video_path = Path(record.final_video_path or "")
    if not video_path.exists():
        raise PublishingEligibilityError(f"Prepared final video missing: {video_path}")
    fingerprint, _size = compute_file_fingerprint(video_path)
    if fingerprint != record.file_fingerprint:
        raise PublishingEligibilityError(
            f"Final artifact changed after preparation "
            f"(prepared {record.file_fingerprint}, now {fingerprint}); re-run prepare"
        )

    # Persist the submission intent BEFORE touching the network.
    record.state = PublicationState.UPLOAD_SUBMITTED
    record.submission_started_at = utc_now_iso()
    persist_tiktok_record(manifest_path, manifest, record)

    try:
        result = publisher.init_direct_post(
            video_path=str(video_path),
            title=record.tiktok_metadata.title,
            privacy_level=record.tiktok_metadata.privacy_level,
            disable_comment=record.tiktok_metadata.disable_comment,
            disable_duet=record.tiktok_metadata.disable_duet,
            disable_stitch=record.tiktok_metadata.disable_stitch,
            video_cover_timestamp_ms=record.tiktok_metadata.video_cover_timestamp_ms,
        )
    except Exception as exc:
        # Outcome unknown: an init request may have reached TikTok even if it
        # errored locally. Never auto-retry.
        record.state = PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
        record.error = f"TikTok init failed with unknown outcome: {exc}"
        persist_tiktok_record(manifest_path, manifest, record)
        raise

    # Persist the durable submission identity IMMEDIATELY (before uploading).
    record.tiktok_publish_id = result["publish_id"]
    record.state = PublicationState.IDENTITY_PERSISTED
    persist_tiktok_record(manifest_path, manifest, record)
    logger.info("TikTok publish_id persisted: %s", result["publish_id"])

    try:
        publisher.upload_video_chunks(result["upload_url"], str(video_path))
    except Exception as exc:
        # Bytes may be partially uploaded; the publish_id is durable, so
        # resume reconciles via the status endpoint instead of re-submitting.
        record.error = f"TikTok chunk upload interrupted: {exc}"
        persist_tiktok_record(manifest_path, manifest, record)
        raise

    record.state = PublicationState.PROCESSING
    record.submitted_at = utc_now_iso()
    record.error = None
    persist_tiktok_record(manifest_path, manifest, record)
    return record


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_tiktok_publish(
    project_id: str,
    publisher: Any,
    project_dir: Optional[Path] = None,
    poll_attempts: int = 1,
    poll_interval_seconds: float = 0.0,
) -> TikTokPublicationRecord:
    """
    Verify via the official status endpoint using the durable publish_id.

    VERIFIED requires PUBLISH_COMPLETE plus the final public post id. Unknown
    statuses are kept truthfully as PROCESSING with raw evidence attached.
    """
    manifest_path, manifest = load_manifest(project_id, project_dir)
    record = get_tiktok_record(manifest)
    if record is None or not record.tiktok_publish_id:
        raise PublishingError("No persisted TikTok publish_id to verify")

    info: Optional[Dict[str, Any]] = None
    for attempt in range(max(1, poll_attempts)):
        info = publisher.fetch_status(record.tiktok_publish_id)
        status = (info or {}).get("status")
        if info is not None and status == "PUBLISH_COMPLETE":
            break
        if attempt < poll_attempts - 1 and poll_interval_seconds > 0:
            time.sleep(poll_interval_seconds)

    if info is None:
        record.state = PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
        record.error = (
            f"TikTok reports no status for persisted publish_id "
            f"{record.tiktok_publish_id}; manual reconciliation required"
        )
        persist_tiktok_record(manifest_path, manifest, record)
        raise PublishingError(record.error)

    status = info.get("status")
    # TikTok's field name for the final post id (documented spelling).
    post_id = info.get("publicaly_available_post_id") or info.get("post_id")
    record.verification_evidence = {
        "publish_id": record.tiktok_publish_id,
        "status": status,
        "post_id": post_id,
        "checked_at": utc_now_iso(),
    }

    new_state = _STATUS_STATE.get(status, PublicationState.PROCESSING)
    if new_state == PublicationState.VERIFIED and not post_id:
        # Complete but no retrievable post identity: do not claim full
        # verification without an identity.
        new_state = PublicationState.PROCESSING
        record.error = (
            "TikTok reports PUBLISH_COMPLETE but no public post id was returned"
        )
    elif new_state == PublicationState.FAILED:
        record.error = f"TikTok posting status={status}"

    record.state = new_state
    if post_id:
        record.tiktok_post_id = post_id
    if new_state == PublicationState.VERIFIED:
        record.verified_at = utc_now_iso()
        record.error = None
    persist_tiktok_record(manifest_path, manifest, record)
    logger.info(
        "TikTok verification for %s: status=%s state=%s",
        record.tiktok_publish_id,
        status,
        record.state.value,
    )
    return record


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

@dataclass
class TikTokPublicationReport:
    project_id: str
    verdict: PublicationVerdict
    state: Optional[str] = None
    tiktok_publish_id: Optional[str] = None
    tiktok_post_id: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    updated: bool = False

    def summary(self) -> str:
        return chr(10).join(
            [
                f"Project ID: {self.project_id}",
                f"TikTok publication verdict: {self.verdict.value}",
                f"Durable state: {self.state}",
                f"TikTok publish_id: {self.tiktok_publish_id or 'none'}",
                f"TikTok post_id: {self.tiktok_post_id or 'none'}",
                f"Reasons: {'; '.join(self.reasons) if self.reasons else 'none'}",
            ]
        )


def reconcile_tiktok_publication(
    project_id: str,
    publisher: Any = None,
    project_dir: Optional[Path] = None,
) -> TikTokPublicationReport:
    """Evidence-based TikTok publication reconciliation."""
    manifest_path, manifest = load_manifest(project_id, project_dir)
    record = get_tiktok_record(manifest)
    reasons: List[str] = []

    if record is None or record.state == PublicationState.NOT_STARTED:
        return TikTokPublicationReport(
            project_id=project_id,
            verdict=PublicationVerdict.PUBLICATION_NOT_STARTED,
            state=(record.state.value if record else None),
            reasons=["no TikTok publication record"],
        )

    if record.state == PublicationState.FAILED:
        reasons.append(record.error or "TikTok publication previously failed")
        return TikTokPublicationReport(
            project_id=project_id,
            verdict=PublicationVerdict.PUBLICATION_FAILED,
            state=record.state.value,
            tiktok_publish_id=record.tiktok_publish_id,
            tiktok_post_id=record.tiktok_post_id,
            reasons=reasons,
        )

    if record.tiktok_publish_id:
        if publisher is not None:
            info = publisher.fetch_status(record.tiktok_publish_id)
            if info is None:
                record.state = PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
                record.error = (
                    f"TikTok reports no status for persisted publish_id "
                    f"{record.tiktok_publish_id}"
                )
                persist_tiktok_record(manifest_path, manifest, record)
                reasons.append(record.error)
                return TikTokPublicationReport(
                    project_id=project_id,
                    verdict=PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED,
                    state=record.state.value,
                    tiktok_publish_id=record.tiktok_publish_id,
                    tiktok_post_id=record.tiktok_post_id,
                    reasons=reasons,
                    updated=True,
                )
            status = info.get("status")
            post_id = info.get("publicaly_available_post_id") or info.get("post_id")
            new_state = _STATUS_STATE.get(status, PublicationState.PROCESSING)
            if new_state == PublicationState.VERIFIED and not post_id:
                new_state = PublicationState.PROCESSING
            record.verification_evidence = {
                "publish_id": record.tiktok_publish_id,
                "status": status,
                "post_id": post_id,
                "checked_at": utc_now_iso(),
            }
            record.state = new_state
            if post_id:
                record.tiktok_post_id = post_id
            if new_state == PublicationState.VERIFIED:
                record.verified_at = utc_now_iso()
                record.error = None
            elif new_state == PublicationState.FAILED:
                record.error = f"TikTok posting status={status}"
            persist_tiktok_record(manifest_path, manifest, record)
            verdict = (
                PublicationVerdict.PUBLICATION_VERIFIED
                if new_state == PublicationState.VERIFIED
                else PublicationVerdict.PUBLICATION_PROCESSING
            )
            reasons.append(f"API status={status}")
            return TikTokPublicationReport(
                project_id=project_id,
                verdict=verdict,
                state=new_state.value,
                tiktok_publish_id=record.tiktok_publish_id,
                tiktok_post_id=record.tiktok_post_id,
                reasons=reasons,
                updated=True,
            )
        # Offline: trust only durable terminal evidence.
        if record.state == PublicationState.VERIFIED and record.tiktok_post_id:
            verdict = PublicationVerdict.PUBLICATION_VERIFIED
            reasons.append("durable VERIFIED evidence with post id (offline)")
        else:
            verdict = PublicationVerdict.PUBLICATION_PROCESSING
            reasons.append(f"durable state {record.state.value} (offline)")
        return TikTokPublicationReport(
            project_id=project_id,
            verdict=verdict,
            state=record.state.value,
            tiktok_publish_id=record.tiktok_publish_id,
            tiktok_post_id=record.tiktok_post_id,
            reasons=reasons,
        )

    if record.state in _POST_SUBMISSION_STATES or record.state == PublicationState.UNKNOWN_RECONCILIATION_REQUIRED:
        reasons.append(
            "submission may have reached TikTok but no durable publish_id "
            "exists; duplicate submission forbidden pending manual reconciliation"
        )
        if record.state != PublicationState.UNKNOWN_RECONCILIATION_REQUIRED:
            record.state = PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
            record.error = record.error or "post-submission state without durable identity"
            persist_tiktok_record(manifest_path, manifest, record)
        return TikTokPublicationReport(
            project_id=project_id,
            verdict=PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED,
            state=record.state.value,
            reasons=reasons,
            updated=True,
        )

    return TikTokPublicationReport(
        project_id=project_id,
        verdict=PublicationVerdict.PUBLICATION_NOT_STARTED,
        state=record.state.value,
        reasons=["prepared but not yet submitted"],
    )


# ---------------------------------------------------------------------------
# Operator entry point (produce.py --publish-tiktok)
# ---------------------------------------------------------------------------

def run_tiktok_publish_flow(project_id: str, project_dir: Optional[Path] = None) -> int:
    """
    Full operator flow: eligibility gate -> prepare -> authenticate ->
    reconcile -> one controlled submission -> bounded verification ->
    final duplicate-safe reconciliation check.

    Returns a process exit code. Every step persists durable state so any
    interruption can be safely resumed by re-running the same command.
    """
    print()
    print("=" * 60)
    print("TIKTOK PUBLISHING — ELIGIBILITY GATE")
    print("=" * 60)

    try:
        record = prepare_tiktok_publish(project_id, project_dir=project_dir)
    except (PublishingEligibilityError, DuplicateUploadBlockedError, PublishingError) as exc:
        print(f"PUBLISHING REFUSED: {exc}")
        return 1

    meta = record.tiktok_metadata
    print("Reconciliation verdict : PROJECT_COMPLETE")
    print(f"Final video            : {record.final_video_path}")
    print(f"SHA-256 fingerprint    : {record.file_fingerprint}")
    print(f"File size              : {record.file_size} bytes")
    print(f"Caption                : {meta.title if meta else 'n/a'}")
    print(f"Privacy level          : {meta.privacy_level if meta else 'n/a'}")
    print()

    publisher = TikTokPublisher.from_env()
    try:
        user = publisher.get_user()
    except TikTokAuthError as exc:
        print(f"AUTHENTICATION REQUIRED/BLOCKED: {exc}")
        print()
        print("TikTok publishing cannot proceed until you complete the official")
        print("developer setup and authorization. Nothing was submitted.")
        return 3
    except Exception as exc:
        print(f"AUTHENTICATION FAILED: {exc}")
        return 3
    if user is None:
        print("AUTHENTICATION FAILED: authorized account identity unavailable")
        return 3
    display_name = user.get("display_name") or user.get("open_id", "")
    print(f"Authenticated account  : {display_name}")

    # Capability check against the real creator profile where available.
    try:
        creator = publisher.get_creator_info()
        options = (creator or {}).get("privacy_level_options") or []
        wanted = meta.privacy_level if meta else ""
        if options and wanted and wanted not in options:
            print(
                f"PRIVACY LEVEL NOT PERMITTED: {wanted} not in creator options "
                f"{options}; nothing was submitted."
            )
            return 1
        max_duration = (creator or {}).get("max_video_post_duration_sec")
        if max_duration:
            print(f"Creator max post duration : {max_duration}s")
    except Exception as exc:
        logger.info("creator_info query skipped/failed: %s", exc)
    print()

    report = reconcile_tiktok_publication(
        project_id, publisher=publisher, project_dir=project_dir
    )
    print(report.summary())
    print()
    if report.verdict == PublicationVerdict.PUBLICATION_VERIFIED:
        print("Already published and verified on TikTok; no submission performed.")
        return 0
    if report.verdict == PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED:
        print("BLOCKED: unknown post-submission state requires manual reconciliation.")
        print("No duplicate submission will be attempted automatically.")
        return 2
    if report.verdict == PublicationVerdict.PUBLICATION_FAILED:
        print("BLOCKED: TikTok publication previously failed; inspect diagnostics.")
        return 2

    print("Submitting TikTok direct post...")
    try:
        record = submit_tiktok_publish(
            project_id, publisher, project_dir=project_dir
        )
    except UploadIdentityMissingError as exc:
        print(f"BLOCKED (safe): {exc}")
        return 2
    except Exception as exc:
        print(f"SUBMISSION FAILED WITH UNKNOWN OUTCOME: {exc}")
        print("The durable publish_id (if returned) is persisted; do NOT re-run blindly.")
        return 2

    print(f"TikTok publish_id persisted: {record.tiktok_publish_id}")
    print()

    print("Verifying via TikTok status API (bounded polling)...")
    try:
        record = verify_tiktok_publish(
            project_id,
            publisher,
            project_dir=project_dir,
            poll_attempts=6,
            poll_interval_seconds=10,
        )
    except Exception as exc:
        print(f"VERIFICATION ERROR: {exc}")
        print("Resume later with the same command; the publish_id is already persisted.")
        return 2

    print(f"Verification state     : {record.state.value}")
    print(f"Evidence               : {json.dumps(record.verification_evidence)}")
    print()

    final_report = reconcile_tiktok_publication(
        project_id, publisher=publisher, project_dir=project_dir
    )
    print(final_report.summary())
    if final_report.verdict == PublicationVerdict.PUBLICATION_VERIFIED:
        print()
        print("TIKTOK PUBLISHING COMPLETE — VERIFIED VIA OFFICIAL API")
        return 0
    print()
    print("TikTok processing not complete yet; resume the same command later.")
    return 2