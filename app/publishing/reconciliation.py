"""
AUTO Video Maker - Publishing Reconciliation & Safe Upload State Machine

Phase 7A. Sits strictly AFTER the Phase 2G PROJECT_COMPLETE acceptance gate.

Core guarantees:
- A project is never uploaded merely because the manifest claims completion;
  the existing evidence-based reconciliation must return PROJECT_COMPLETE and
  the final MP4 must revalidate immediately before preparation.
- The exact file being uploaded is bound to a SHA-256 fingerprint captured at
  preparation time; if the file changes afterwards, upload is refused until
  the project is revalidated/re-prepared.
- Durable state transitions are persisted BEFORE risky operations. In
  particular, the manifest records UPLOAD_SUBMITTED before the upload API call
  begins, so a crash mid-upload can never lead to an automatic second upload.
- If a YouTube video ID exists, resume verifies/reconciles that exact video
  instead of uploading again.
- If a submission may have occurred but no durable identity was persisted,
  the record enters UNKNOWN_RECONCILIATION_REQUIRED and no retry happens.
"""

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    DEFAULT_YOUTUBE_CATEGORY_ID,
    DuplicateUploadBlockedError,
    MetadataMismatchError,
    MetadataValidationError,
    PublicationRecord,
    PublicationState,
    PublicationVerdict,
    PublishingEligibilityError,
    PublishingError,
    UploadIdentityMissingError,
    YouTubeMetadata,
    utc_now_iso,
)
from app.publishing.youtube import (
    MAX_DESCRIPTION_LENGTH,
    MAX_TITLE_LENGTH,
    YouTubePublisher,
)

logger = logging.getLogger(__name__)

ORCHESTRATOR_OUTPUT_DIR = PROJECT_ROOT / "output" / "projects"

# States from which re-preparation (new fingerprint) is allowed.
_REPREPARABLE_STATES = {
    PublicationState.NOT_STARTED,
    PublicationState.PREPARED,
}

# Both platform writers share this lock. It protects a fresh manifest merge,
# not a second publication lifecycle; all state transitions remain below.
_PUBLICATION_MANIFEST_LOCK = threading.RLock()

# States meaning "a submission may already exist on YouTube".
_POST_SUBMISSION_STATES = {
    PublicationState.UPLOAD_SUBMITTED,
    PublicationState.IDENTITY_PERSISTED,
    PublicationState.PROCESSING,
}


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def project_dir_for(project_id: str, project_dir: Optional[Path] = None) -> Path:
    if project_dir is None:
        project_dir = ORCHESTRATOR_OUTPUT_DIR / project_id
    return Path(project_dir)


def load_manifest(project_id: str, project_dir: Optional[Path] = None) -> Tuple[Path, ProjectManifest]:
    directory = project_dir_for(project_id, project_dir)
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        raise PublishingError(f"Project manifest not found: {manifest_path}")
    with open(manifest_path, "r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    return manifest_path, ProjectManifest.from_dict(data)


def save_manifest(manifest_path: Path, manifest: ProjectManifest) -> None:
    manifest.updated_at = utc_now_iso()
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=manifest_path.parent,
            prefix=f".{manifest_path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(manifest.to_dict(), handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, manifest_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _persist_platform_record(manifest_path: Path, manifest: ProjectManifest,
                             field_name: str, record: Any) -> None:
    """Merge only the owning platform's record into the latest disk manifest.

    A stale YouTube snapshot must not erase a newly persisted TikTok record
    (or execution evidence), and vice versa. Atomic replacement prevents a
    browser read from observing a partially written publication manifest.
    """
    if field_name not in {"publication", "tiktok_publication"}:
        raise ValueError("unknown publication record field")
    with _PUBLICATION_MANIFEST_LOCK:
        _, latest = load_manifest(manifest.project_id, manifest_path.parent)
        if latest.project_id != manifest.project_id or record.project_id != latest.project_id:
            raise PublishingError("Publication record project identity mismatch")
        record.updated_at = utc_now_iso()
        setattr(latest, field_name, record)
        save_manifest(manifest_path, latest)
        # Keep the caller's snapshot in sync for subsequent in-memory reads.
        manifest.publication = latest.publication
        manifest.tiktok_publication = latest.tiktok_publication
        manifest.updated_at = latest.updated_at


def get_record(manifest: ProjectManifest) -> Optional[PublicationRecord]:
    record = getattr(manifest, "publication", None)
    if isinstance(record, PublicationRecord):
        return record
    return None


def ensure_record(manifest: ProjectManifest) -> PublicationRecord:
    record = get_record(manifest)
    if record is None:
        record = PublicationRecord(project_id=manifest.project_id)
        manifest.publication = record
    return record


def persist_record(manifest_path: Path, manifest: ProjectManifest, record: PublicationRecord) -> None:
    """Persist YouTube evidence without clobbering the other platform."""
    _persist_platform_record(manifest_path, manifest, "publication", record)


# ---------------------------------------------------------------------------
# Fingerprint & metadata
# ---------------------------------------------------------------------------

def compute_file_fingerprint(path: Path) -> Tuple[str, int]:
    """Deterministic SHA-256 fingerprint of a file; returns (hex digest, size)."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def build_youtube_metadata(
    manifest: ProjectManifest,
    project_dir: Path,
    privacy_status: Optional[str] = None,
) -> YouTubeMetadata:
    """
    Deterministically build and validate the canonical YouTube metadata for a
    project from already-validated project evidence.

    Sources (no content regeneration ever occurs):
    - title / description: production plan (title, script, call_to_action),
      falling back to the manifest topic — identical to the Phase 7A behavior.
    - tags: explicit ``metadata.tags`` from the production plan when present,
      then deterministic niche/content-goal values, then optional operator
      configuration via YOUTUBE_TAGS (comma-separated). Never invented.
    - category_id: YOUTUBE_CATEGORY_ID environment configuration with a
      documented default of 27 (Education); validated against a whitelist.
    - made_for_kids: explicit boolean via YOUTUBE_MADE_FOR_KIDS (default False).
    - default_language: production plan ``metadata.language`` when present.

    Raises MetadataValidationError when the assembled metadata violates the
    platform contract; nothing is uploaded in that case.
    """
    plan_path = _path(project_dir, manifest.content_plan_path)
    title = manifest.topic
    description_parts: List[str] = []
    tags: List[str] = []
    language: Optional[str] = None

    if plan_path and plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            title = plan.get("title") or title
            script = (plan.get("script") or "").strip()
            if script:
                description_parts.append(script)
            cta = (plan.get("call_to_action") or "").strip()
            if cta and cta not in script:
                description_parts.append(cta)

            plan_meta = plan.get("metadata") or {}
            raw_tags = plan_meta.get("tags")
            if isinstance(raw_tags, str):
                raw_tags = [raw_tags]
            if isinstance(raw_tags, list):
                for raw in raw_tags:
                    tag = str(raw).strip()
                    if tag and tag.lower() not in {t.lower() for t in tags}:
                        tags.append(tag)
            language = (str(plan_meta.get("language") or "").strip() or None)
            for key in ("niche", "content_goal"):
                value = str(plan_meta.get(key) or "").strip()
                if value and value.lower() not in {t.lower() for t in tags}:
                    tags.append(value)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not derive metadata from production plan: %s", exc)

    if not description_parts:
        description_parts.append(manifest.topic)

    # Operator-provided tags via environment (deterministic, documented).
    for raw in os.getenv("YOUTUBE_TAGS", "").split(","):
        tag = raw.strip()
        if tag and tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)

    made_for_kids_raw = os.getenv("YOUTUBE_MADE_FOR_KIDS", "false").strip().lower()

    try:
        return YouTubeMetadata(
            title=title.strip(),
            # Defensive slice only; validation below rejects over-limit values
            # raised by operators. Plan-derived descriptions were already
            # accepted at this length by Phase 7A uploads.
            description="\n\n".join(description_parts).strip()[:MAX_DESCRIPTION_LENGTH],
            tags=tags,
            category_id=os.getenv("YOUTUBE_CATEGORY_ID", DEFAULT_YOUTUBE_CATEGORY_ID).strip(),
            privacy_status=(privacy_status or YouTubePublisher.default_privacy_status()).lower(),
            made_for_kids=made_for_kids_raw == "true",
            default_language=language,
        )
    except Exception as exc:
        raise MetadataValidationError(f"YouTube metadata validation failed: {exc}") from exc


def derive_metadata(manifest: ProjectManifest, project_dir: Path) -> Dict[str, str]:
    """Backward-compatible flat view of the canonical metadata."""
    metadata = build_youtube_metadata(manifest, project_dir)
    return {
        "title": metadata.title[:MAX_TITLE_LENGTH],
        "description": metadata.description[:MAX_DESCRIPTION_LENGTH],
    }


# ---------------------------------------------------------------------------
# Eligibility gate + preparation
# ---------------------------------------------------------------------------

def prepare_publish(
    project_id: str,
    project_dir: Optional[Path] = None,
    privacy_status: Optional[str] = None,
) -> PublicationRecord:
    """
    Publishing eligibility gate. Validates the project end-to-end through the
    canonical reconciliation layer, revalidates the final artifact right now,
    fingerprints it, derives metadata, and persists PREPARED.

    Raises PublishingEligibilityError when the gate fails; raises
    DuplicateUploadBlockedError when the artifact changed after a submission
    may have started.
    """
    report = reconcile_project(project_id, project_dir_for(project_id, project_dir))
    if report.verdict != ProjectVerdict.PROJECT_COMPLETE:
        raise PublishingEligibilityError(
            f"Publishing refused: reconciliation verdict is {report.verdict.value} "
            f"({'; '.join(report.reasons) or 'no reasons recorded'})"
        )

    directory = project_dir_for(project_id, project_dir)
    manifest_path, manifest = load_manifest(project_id, directory)

    # Immediate final-artifact validation (fresh FFprobe evidence).
    evidence = _assembly_evidence(manifest, directory)
    if evidence.get("status") != ArtifactStatus.VALID.value:
        validation = evidence.get("validation", {})
        error = (
            validation.get("error")
            or "; ".join(validation.get("errors", []))
            or evidence.get("status")
        )
        raise PublishingEligibilityError(f"Publishing refused: final artifact invalid: {error}")

    video_path = Path(evidence["path"])
    fingerprint, size = compute_file_fingerprint(video_path)

    record = ensure_record(manifest)

    # File-mutation guard: the prepared/uploaded fingerprint must still match.
    if record.file_fingerprint and record.file_fingerprint != fingerprint:
        if record.state in _POST_SUBMISSION_STATES:
            record.state = PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
            record.error = (
                f"Final artifact changed after submission (prepared {record.file_fingerprint}, "
                f"now {fingerprint}); manual reconciliation required"
            )
            persist_record(manifest_path, manifest, record)
            raise DuplicateUploadBlockedError(record.error)
        if record.state not in _REPREPARABLE_STATES:
            raise DuplicateUploadBlockedError(
                f"Final artifact changed after preparation in state {record.state.value}; "
                "re-run prepare to revalidate"
            )
        logger.warning(
            "Final artifact changed since last preparation (%s -> %s); re-preparing",
            record.file_fingerprint,
            fingerprint,
        )

    # Canonical metadata contract: built once from validated project evidence,
    # validated against platform limits, and persisted durably. Validation
    # is read-only and preserves the existing invalid-metadata error contract
    # even when an existing publication independently forbids preparation.
    metadata_model = build_youtube_metadata(manifest, directory, privacy_status=privacy_status)
    if record.youtube_video_id or record.state not in _REPREPARABLE_STATES:
        raise DuplicateUploadBlockedError(
            f"Cannot prepare YouTube publication in state {record.state.value} "
            "or with an existing video identity; reconcile the existing "
            "publication explicitly. Preparation never clears submission evidence."
        )
    record.final_video_path = str(video_path)
    record.file_fingerprint = fingerprint
    record.file_size = size
    record.youtube_metadata = metadata_model
    record.title = metadata_model.title[:MAX_TITLE_LENGTH]
    record.description = metadata_model.description[:MAX_DESCRIPTION_LENGTH]
    record.privacy_status = metadata_model.privacy_status
    record.prepared_at = utc_now_iso()
    record.state = PublicationState.PREPARED
    record.error = None
    persist_record(manifest_path, manifest, record)

    logger.info(
        "Publishing prepared: project=%s fingerprint=%s size=%d",
        project_id,
        fingerprint,
        size,
    )
    return record


# ---------------------------------------------------------------------------
# Submission (with duplicate-upload protection)
# ---------------------------------------------------------------------------

def submit_upload(
    project_id: str,
    publisher: Any,
    project_dir: Optional[Path] = None,
) -> PublicationRecord:
    """
    Submit the prepared upload exactly once.

    Safety rules enforced here:
    - If a YouTube video ID is already persisted, NEVER upload again.
    - UPLOAD_SUBMITTED without a durable identity means a previous attempt may
      have reached YouTube: enter UNKNOWN_RECONCILIATION_REQUIRED, no retry.
    - The UPLOAD_SUBMITTED state is persisted BEFORE the API call begins.
    - Any upload exception becomes UNKNOWN_RECONCILIATION_REQUIRED because the
      outcome on YouTube's side cannot be proven locally.
    """
    manifest_path, manifest = load_manifest(project_id, project_dir)
    record = get_record(manifest)
    if record is None:
        raise PublishingError("No publication record; run prepare first")

    # Duplicate protection: identity already durable -> verify instead of upload.
    if record.youtube_video_id:
        logger.info(
            "YouTube video ID already persisted (%s); refusing to upload again",
            record.youtube_video_id,
        )
        return record

    if record.state in {
        PublicationState.UPLOAD_SUBMITTED,
        PublicationState.UNKNOWN_RECONCILIATION_REQUIRED,
    }:
        # A previous submission may have reached YouTube without a durable
        # identity being persisted. The only safe action is explicit manual
        # reconciliation; never a blind duplicate upload.
        record.state = PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
        record.error = (
            "Previous upload submission was interrupted before a YouTube video ID "
            "could be persisted; refusing blind duplicate upload"
        )
        persist_record(manifest_path, manifest, record)
        raise UploadIdentityMissingError(record.error)

    if record.state not in {PublicationState.PREPARED, PublicationState.FAILED}:
        raise PublishingError(
            f"Cannot submit upload from state {record.state.value}; expected PREPARED"
        )

    # The exact file must still match the validated fingerprint.
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
    record.upload_submitted_at = utc_now_iso()
    persist_record(manifest_path, manifest, record)

    try:
        meta = record.youtube_metadata
        video_id = publisher.upload(
            video_path=str(video_path),
            title=record.title or "",
            description=record.description or "",
            privacy_status=record.privacy_status,
            tags=list(meta.tags) if meta else None,
            category_id=meta.category_id if meta else None,
            made_for_kids=meta.made_for_kids if meta else False,
            default_language=meta.default_language if meta else None,
        )
    except Exception as exc:
        # Outcome unknown: the bytes may have reached YouTube. Never auto-retry.
        record.state = PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
        record.error = f"Upload failed with unknown outcome: {exc}"
        persist_record(manifest_path, manifest, record)
        raise

    # Persist the durable identity IMMEDIATELY after the response.
    record.youtube_video_id = video_id
    record.state = PublicationState.IDENTITY_PERSISTED
    record.upload_completed_at = utc_now_iso()
    record.error = None
    persist_record(manifest_path, manifest, record)
    logger.info("Upload submitted; YouTube video ID persisted: %s", video_id)
    return record


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

_UPLOAD_STATUS_STATE = {
    "processed": PublicationState.VERIFIED,
    "processing": PublicationState.PROCESSING,
    "uploaded": PublicationState.PROCESSING,
    "failed": PublicationState.FAILED,
    "rejected": PublicationState.FAILED,
}


def _normalize_text(value: Optional[str]) -> str:
    """Whitespace-normalized comparison text (YouTube may reflow whitespace)."""
    return " ".join((value or "").split())


def metadata_mismatches(
    expected: Optional[YouTubeMetadata],
    legacy_title: Optional[str],
    legacy_description: Optional[str],
    legacy_privacy: Optional[str],
    info: Optional[Dict[str, Any]],
) -> List[str]:
    """
    Compare authoritative YouTube API evidence against the expected metadata.

    Returns a list of human-readable mismatch descriptions; empty means the
    API confirms the expected metadata. Fields absent from the API response
    are skipped (they cannot be verified), never treated as mismatches.
    """
    if info is None:
        return ["video not retrievable"]
    snippet = info.get("snippet", {})
    status = info.get("status", {})
    mismatches: List[str] = []

    exp_title = expected.title if expected else legacy_title
    api_title = snippet.get("title")
    if exp_title and _normalize_text(api_title) != _normalize_text(exp_title):
        mismatches.append(f"title: expected {exp_title!r}, API returned {api_title!r}")

    exp_description = expected.description if expected else legacy_description
    api_description = snippet.get("description")
    if exp_description and _normalize_text(api_description) != _normalize_text(exp_description):
        mismatches.append("description differs from the persisted expectation")

    exp_privacy = (expected.privacy_status if expected else legacy_privacy or "").lower()
    api_privacy = (status.get("privacyStatus") or "").lower()
    if exp_privacy and api_privacy and api_privacy != exp_privacy:
        mismatches.append(
            f"privacyStatus: expected {exp_privacy!r}, API returned {api_privacy!r}"
        )

    if expected is not None:
        api_tags = [str(tag).lower() for tag in snippet.get("tags") or []]
        missing_tags = [tag for tag in expected.tags if tag.lower() not in api_tags]
        if missing_tags:
            mismatches.append(f"tags missing on YouTube: {missing_tags}")

        api_category = snippet.get("categoryId")
        if api_category and api_category != expected.category_id:
            mismatches.append(
                f"categoryId: expected {expected.category_id!r}, "
                f"API returned {api_category!r}"
            )

        api_language = snippet.get("defaultLanguage")
        if expected.default_language and api_language and api_language != expected.default_language:
            mismatches.append(
                f"defaultLanguage: expected {expected.default_language!r}, "
                f"API returned {api_language!r}"
            )

        api_made_for_kids = status.get("madeForKids")
        if api_made_for_kids is not None and bool(api_made_for_kids) != expected.made_for_kids:
            mismatches.append(
                f"madeForKids: expected {expected.made_for_kids!r}, "
                f"API returned {api_made_for_kids!r}"
            )

    return mismatches


def verify_upload(
    project_id: str,
    publisher: Any,
    project_dir: Optional[Path] = None,
    poll_attempts: int = 1,
    poll_interval_seconds: float = 0.0,
) -> PublicationRecord:
    """
    Verify the uploaded video through the YouTube API using the persisted ID.

    VERIFIED is only ever set from real API evidence (videos.list by exact ID),
    never merely because an upload call returned without raising.
    """
    manifest_path, manifest = load_manifest(project_id, project_dir)
    record = get_record(manifest)
    if record is None or not record.youtube_video_id:
        raise PublishingError("No persisted YouTube video ID to verify")

    info: Optional[Dict[str, Any]] = None
    for attempt in range(max(1, poll_attempts)):
        info = publisher.get_video(record.youtube_video_id)
        status = (info or {}).get("status", {}).get("uploadStatus")
        if info is not None and status == "processed":
            break
        if attempt < poll_attempts - 1 and poll_interval_seconds > 0:
            time.sleep(poll_interval_seconds)

    if info is None:
        record.state = PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
        record.error = (
            f"YouTube reports no video with persisted ID {record.youtube_video_id}; "
            "manual reconciliation required"
        )
        persist_record(manifest_path, manifest, record)
        raise PublishingError(record.error)

    api_status = info.get("status", {})
    snippet = info.get("snippet", {})
    upload_status = api_status.get("uploadStatus")

    # Phase 7B: authoritative metadata verification. The upload response
    # alone is never trusted; the retrieved video must confirm the expected
    # metadata before VERIFIED may be recorded.
    mismatches = metadata_mismatches(
        record.youtube_metadata,
        record.title,
        record.description,
        record.privacy_status,
        info,
    )
    record.verification_evidence = {
        "video_id": record.youtube_video_id,
        "uploadStatus": upload_status,
        "privacyStatus": api_status.get("privacyStatus"),
        "license": api_status.get("license"),
        "title": snippet.get("title"),
        "channelTitle": snippet.get("channelTitle"),
        "metadata_mismatches": mismatches,
        "checked_at": utc_now_iso(),
    }

    if upload_status == "processed" and mismatches:
        # Truthful outcome: the video exists but its metadata does not match.
        # Never mark VERIFIED; never retry the upload to fix metadata.
        record.state = PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
        record.error = (
            "Metadata verification mismatch after upload: " + "; ".join(mismatches)
        )
        persist_record(manifest_path, manifest, record)
        logger.error(
            "Metadata verification mismatch for %s: %s",
            record.youtube_video_id,
            "; ".join(mismatches),
        )
        raise MetadataMismatchError(record.error)

    new_state = _UPLOAD_STATUS_STATE.get(upload_status, PublicationState.PROCESSING)
    record.state = new_state
    if new_state == PublicationState.VERIFIED:
        record.verified_at = utc_now_iso()
        record.error = None
    elif new_state == PublicationState.FAILED:
        record.error = f"YouTube uploadStatus={upload_status}"
    persist_record(manifest_path, manifest, record)
    logger.info(
        "Verification result for %s: uploadStatus=%s state=%s",
        record.youtube_video_id,
        upload_status,
        record.state.value,
    )
    return record


# ---------------------------------------------------------------------------
# Publication reconciliation
# ---------------------------------------------------------------------------

@dataclass
class PublicationReport:
    project_id: str
    verdict: PublicationVerdict
    state: Optional[str] = None
    youtube_video_id: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    updated: bool = False

    def summary(self) -> str:
        return "\n".join(
            [
                f"Project ID: {self.project_id}",
                f"Publication verdict: {self.verdict.value}",
                f"Durable state: {self.state}",
                f"YouTube video ID: {self.youtube_video_id or 'none'}",
                f"Reasons: {'; '.join(self.reasons) if self.reasons else 'none'}",
            ]
        )


def reconcile_publication(
    project_id: str,
    publisher: Any = None,
    project_dir: Optional[Path] = None,
) -> PublicationReport:
    """
    Evidence-based publication reconciliation.

    Uses the persisted YouTube video ID plus live API evidence where a
    publisher is available. Claims are never trusted blindly: VERIFIED is only
    confirmed against the API when possible.
    """
    manifest_path, manifest = load_manifest(project_id, project_dir)
    record = get_record(manifest)
    reasons: List[str] = []

    if record is None or record.state == PublicationState.NOT_STARTED:
        return PublicationReport(
            project_id=project_id,
            verdict=PublicationVerdict.PUBLICATION_NOT_STARTED,
            state=(record.state.value if record else None),
            reasons=["no publication record"],
        )

    if record.state == PublicationState.FAILED:
        reasons.append(record.error or "publication previously failed")
        return PublicationReport(
            project_id=project_id,
            verdict=PublicationVerdict.PUBLICATION_FAILED,
            state=record.state.value,
            youtube_video_id=record.youtube_video_id,
            reasons=reasons,
        )

    if publisher is None and record.state == PublicationState.UNKNOWN_RECONCILIATION_REQUIRED:
        return PublicationReport(
            project_id=project_id,
            verdict=PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED,
            state=record.state.value, youtube_video_id=record.youtube_video_id,
            reasons=[record.error or "explicit reconciliation required; no blind upload"],
        )

    if record.youtube_video_id:
        if publisher is not None:
            info = publisher.get_video(record.youtube_video_id)
            if info is None:
                record.state = PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
                record.error = (
                    f"YouTube reports no video with persisted ID {record.youtube_video_id}"
                )
                persist_record(manifest_path, manifest, record)
                reasons.append(record.error)
                return PublicationReport(
                    project_id=project_id,
                    verdict=PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED,
                    state=record.state.value,
                    youtube_video_id=record.youtube_video_id,
                    reasons=reasons,
                    updated=True,
                )
            upload_status = info.get("status", {}).get("uploadStatus")
            new_state = _UPLOAD_STATUS_STATE.get(upload_status, PublicationState.PROCESSING)

            # Phase 7B: recover an interrupted metadata update by inspecting
            # the EXISTING video — never by uploading again.
            pending_mismatches: List[str] = []
            if record.pending_metadata is not None and new_state != PublicationState.FAILED:
                pending_mismatches = metadata_mismatches(
                    record.pending_metadata, None, None, None, info
                )
                if not pending_mismatches:
                    # The authoritative API confirms the pending metadata:
                    # commit it durably. The upload identity is untouched.
                    record.youtube_metadata = record.pending_metadata
                    record.title = record.pending_metadata.title
                    record.description = record.pending_metadata.description
                    record.privacy_status = record.pending_metadata.privacy_status
                    record.pending_metadata = None
                    record.metadata_verified_at = utc_now_iso()
                    record.error = None
                    reasons.append("pending metadata update confirmed by API and committed")

            mismatches = metadata_mismatches(
                record.youtube_metadata,
                record.title,
                record.description,
                record.privacy_status,
                info,
            )
            record.verification_evidence = {
                "video_id": record.youtube_video_id,
                "uploadStatus": upload_status,
                "privacyStatus": info.get("status", {}).get("privacyStatus"),
                "title": info.get("snippet", {}).get("title"),
                "channelTitle": info.get("snippet", {}).get("channelTitle"),
                "metadata_mismatches": mismatches,
                "checked_at": utc_now_iso(),
            }

            if record.pending_metadata is not None and new_state != PublicationState.FAILED:
                # Update submitted but the API does not confirm it yet:
                # truthful pending verdict; the pending metadata stays durable
                # so a later resume can reconcile again. No upload, no retry.
                if new_state == PublicationState.VERIFIED:
                    record.verified_at = record.verified_at or utc_now_iso()
                persist_record(manifest_path, manifest, record)
                reasons.append(
                    "metadata update pending: " + ("; ".join(pending_mismatches) or "not yet reflected")
                )
                return PublicationReport(
                    project_id=project_id,
                    verdict=PublicationVerdict.PUBLICATION_METADATA_UPDATE_PENDING,
                    state=record.state.value,
                    youtube_video_id=record.youtube_video_id,
                    reasons=reasons,
                    updated=True,
                )

            if new_state == PublicationState.VERIFIED and mismatches:
                # The video exists and is processed, but its live metadata
                # does not match the persisted expectation. Never mark
                # VERIFIED on mismatched evidence; never upload again.
                record.state = PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
                record.error = (
                    "Metadata verification mismatch: " + "; ".join(mismatches)
                )
                persist_record(manifest_path, manifest, record)
                reasons.append(record.error)
                return PublicationReport(
                    project_id=project_id,
                    verdict=PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED,
                    state=record.state.value,
                    youtube_video_id=record.youtube_video_id,
                    reasons=reasons,
                    updated=True,
                )

            record.state = new_state
            if new_state == PublicationState.VERIFIED:
                record.verified_at = utc_now_iso()
                record.error = None
            elif new_state == PublicationState.FAILED:
                record.error = f"YouTube uploadStatus={upload_status}"
            persist_record(manifest_path, manifest, record)
            if new_state == PublicationState.VERIFIED:
                verdict = PublicationVerdict.PUBLICATION_VERIFIED
            elif new_state == PublicationState.FAILED:
                verdict = PublicationVerdict.PUBLICATION_FAILED
            else:
                verdict = PublicationVerdict.PUBLICATION_PROCESSING
            reasons.append(f"API uploadStatus={upload_status}")
            return PublicationReport(
                project_id=project_id,
                verdict=verdict,
                state=new_state.value,
                youtube_video_id=record.youtube_video_id,
                reasons=reasons,
                updated=True,
            )
        # No publisher available: trust only durable VERIFIED evidence.
        if record.pending_metadata is not None:
            reasons.append(
                "metadata update submitted but not yet verified (offline); "
                "resume with a publisher to reconcile the existing video"
            )
            return PublicationReport(
                project_id=project_id,
                verdict=PublicationVerdict.PUBLICATION_METADATA_UPDATE_PENDING,
                state=record.state.value,
                youtube_video_id=record.youtube_video_id,
                reasons=reasons,
            )
        if record.state == PublicationState.VERIFIED:
            verdict = PublicationVerdict.PUBLICATION_VERIFIED
            reasons.append("durable VERIFIED evidence (offline)")
        else:
            verdict = PublicationVerdict.PUBLICATION_PROCESSING
            reasons.append(f"durable state {record.state.value} (offline)")
        return PublicationReport(
            project_id=project_id,
            verdict=verdict,
            state=record.state.value,
            youtube_video_id=record.youtube_video_id,
            reasons=reasons,
        )

    if record.state in _POST_SUBMISSION_STATES or record.state == PublicationState.UNKNOWN_RECONCILIATION_REQUIRED:
        reasons.append(
            "submission may have reached YouTube but no durable video ID exists; "
            "duplicate upload forbidden pending manual reconciliation"
        )
        if record.state != PublicationState.UNKNOWN_RECONCILIATION_REQUIRED:
            record.state = PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
            record.error = record.error or "post-submission state without durable identity"
            persist_record(manifest_path, manifest, record)
        return PublicationReport(
            project_id=project_id,
            verdict=PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED,
            state=record.state.value,
            reasons=reasons,
            updated=True,
        )

    # PREPARED (or anything else pre-submission): nothing has been uploaded.
    return PublicationReport(
        project_id=project_id,
        verdict=PublicationVerdict.PUBLICATION_NOT_STARTED,
        state=record.state.value,
        reasons=["prepared but not yet submitted"],
    )


# ---------------------------------------------------------------------------
# Operator entry point (used by produce.py --publish-youtube)
# ---------------------------------------------------------------------------

def run_publish_flow(project_id: str, confirm_channel: bool = True) -> int:
    """
    Full operator flow: eligibility gate -> prepare -> reconcile -> submit ->
    verify -> final duplicate-safe reconciliation check.

    Returns a process exit code. Every step persists durable state so any
    interruption can be safely resumed by running the same command again.
    """
    print()
    print("=" * 60)
    print("YOUTUBE PUBLISHING — ELIGIBILITY GATE")
    print("=" * 60)

    try:
        # Inspect durable evidence BEFORE preparation. Resuming an existing
        # identity must not reset VERIFIED/PROCESSING or erase an ambiguity.
        offline_report = reconcile_publication(project_id)
        if offline_report.verdict in {
            PublicationVerdict.PUBLICATION_FAILED,
            PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED,
        }:
            print(offline_report.summary())
            print("BLOCKED: inspect and explicitly reconcile; no upload attempted.")
            return 2
        _, manifest = load_manifest(project_id)
        record = get_record(manifest)
        already_submitted = bool(record and record.youtube_video_id)
        if not already_submitted:
            record = prepare_publish(project_id)
    except (PublishingEligibilityError, DuplicateUploadBlockedError, PublishingError) as exc:
        print(f"PUBLISHING REFUSED: {exc}")
        return 1

    if already_submitted:
        print("Existing publication identity; preparation skipped on resume.")
    else:
        print("Reconciliation verdict : PROJECT_COMPLETE")
    print(f"Final video            : {record.final_video_path}")
    print(f"SHA-256 fingerprint    : {record.file_fingerprint}")
    print(f"File size              : {record.file_size} bytes")
    print(f"Title                  : {record.title}")
    print(f"Tags                   : {list(record.youtube_metadata.tags) if record.youtube_metadata else []}")
    print(f"Category ID            : {record.youtube_metadata.category_id if record.youtube_metadata else 'n/a'}")
    print(f"Made for kids          : {record.youtube_metadata.made_for_kids if record.youtube_metadata else 'n/a'}")
    print(f"Privacy status         : {record.privacy_status}")
    print()

    publisher = YouTubePublisher.from_env()
    try:
        channel = publisher.get_channel()
    except Exception as exc:
        print(f"AUTHENTICATION FAILED: {exc}")
        return 1
    if channel is None:
        print("AUTHENTICATION FAILED: authenticated account has no YouTube channel")
        return 1
    print(f"Authenticated channel  : {channel.get('snippet', {}).get('title')}")
    print()

    # Pre-submission reconciliation: never upload if identity already exists.
    report = reconcile_publication(project_id, publisher=publisher)
    print(report.summary())
    print()
    if report.verdict == PublicationVerdict.PUBLICATION_VERIFIED:
        print("Already published and verified; no upload performed.")
        return 0
    if report.verdict == PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED:
        print("BLOCKED: unknown post-submission state requires manual reconciliation.")
        print("No duplicate upload will be attempted automatically.")
        return 2
    if report.verdict == PublicationVerdict.PUBLICATION_FAILED:
        print("BLOCKED: publication previously failed; inspect the manifest diagnostics.")
        return 2

    print("Submitting upload...")
    try:
        record = submit_upload(project_id, publisher)
    except UploadIdentityMissingError as exc:
        print(f"BLOCKED (safe): {exc}")
        return 2
    except Exception as exc:
        print(f"UPLOAD FAILED WITH UNKNOWN OUTCOME: {exc}")
        print("The project is in UNKNOWN_RECONCILIATION_REQUIRED; do NOT re-run blindly.")
        return 2

    print(f"YouTube video ID persisted: {record.youtube_video_id}")
    print()

    print("Verifying via YouTube API...")
    try:
        record = verify_upload(
            project_id,
            publisher,
            poll_attempts=10,
            poll_interval_seconds=15,
        )
    except Exception as exc:
        print(f"VERIFICATION ERROR: {exc}")
        print("Resume later with the same command; the video ID is already persisted.")
        return 2

    print(f"Verification state     : {record.state.value}")
    print(f"Evidence               : {json.dumps(record.verification_evidence)}")
    print()

    # Final duplicate-upload safety proof: reconciliation must find the
    # existing identity and perform no second upload.
    final_report = reconcile_publication(project_id, publisher=publisher)
    print(final_report.summary())
    if final_report.verdict == PublicationVerdict.PUBLICATION_VERIFIED:
        print()
        print("PUBLISHING COMPLETE — VERIFIED VIA YOUTUBE API")
        return 0
    print()
    print("Publishing not fully verified yet; resume the same command later.")
    return 2


# ---------------------------------------------------------------------------
# Metadata update for an EXISTING publication (Phase 7B)
# ---------------------------------------------------------------------------

def update_video_metadata(
    project_id: str,
    publisher: Any,
    metadata: Optional[YouTubeMetadata] = None,
    project_dir: Optional[Path] = None,
) -> PublicationRecord:
    """
    Update the metadata of an ALREADY-PUBLISHED video (explicit operation).

    Hard guarantees:
    - Requires a persisted YouTube video ID; this function can never create
      a new upload or duplicate a publication.
    - The pending metadata is persisted BEFORE the videos.update call, so an
      interruption is recoverable by reconciling the exact existing video.
    - After the update, the authoritative API must confirm the new metadata;
      otherwise the record keeps the pending state and a truthful error is
      raised. The upload identity is never replaced.
    """
    manifest_path, manifest = load_manifest(project_id, project_dir)
    record = get_record(manifest)
    if record is None or not record.youtube_video_id:
        raise PublishingError(
            "No persisted YouTube video ID for this project; metadata updates "
            "target an existing upload. Run the publish flow first."
        )

    target = metadata or record.youtube_metadata
    if target is None:
        raise PublishingError(
            "No canonical metadata available for this project; provide "
            "metadata explicitly to update"
        )
    # Revalidate whatever source was used; invalid metadata never reaches API.
    try:
        target = YouTubeMetadata.model_validate(target.model_dump())
    except Exception as exc:
        raise MetadataValidationError(f"YouTube metadata validation failed: {exc}") from exc

    # Persist the update intent BEFORE touching the network so an interrupted
    # update can be reconciled against the exact existing video on resume.
    record.pending_metadata = target
    record.metadata_update_submitted_at = utc_now_iso()
    persist_record(manifest_path, manifest, record)

    try:
        publisher.update_metadata(
            record.youtube_video_id,
            title=target.title,
            description=target.description,
            privacy_status=target.privacy_status,
            tags=list(target.tags),
            category_id=target.category_id,
            made_for_kids=target.made_for_kids,
            default_language=target.default_language,
        )
    except Exception as exc:
        # Outcome unknown: keep the pending metadata durable and raise. Resume
        # reconciliation inspects the existing video; no retry happens here.
        record.error = f"Metadata update failed with unknown outcome: {exc}"
        persist_record(manifest_path, manifest, record)
        raise

    info = publisher.get_video(record.youtube_video_id)
    mismatches = metadata_mismatches(target, None, None, None, info)
    if mismatches:
        record.error = (
            "Metadata update verification mismatch: " + "; ".join(mismatches)
        )
        persist_record(manifest_path, manifest, record)
        logger.error(
            "Metadata update verification mismatch for %s: %s",
            record.youtube_video_id,
            "; ".join(mismatches),
        )
        raise MetadataMismatchError(record.error)

    # Authoritative confirmation: commit the new metadata durably. The
    # YouTube identity remains exactly the same.
    record.youtube_metadata = target
    record.title = target.title
    record.description = target.description
    record.privacy_status = target.privacy_status
    record.pending_metadata = None
    record.metadata_verified_at = utc_now_iso()
    record.error = None
    record.verification_evidence = {
        "video_id": record.youtube_video_id,
        "uploadStatus": (info or {}).get("status", {}).get("uploadStatus"),
        "privacyStatus": (info or {}).get("status", {}).get("privacyStatus"),
        "title": (info or {}).get("snippet", {}).get("title"),
        "channelTitle": (info or {}).get("snippet", {}).get("channelTitle"),
        "metadata_mismatches": [],
        "checked_at": utc_now_iso(),
    }
    persist_record(manifest_path, manifest, record)
    logger.info(
        "Metadata updated and verified for %s (video %s); identity unchanged",
        project_id,
        record.youtube_video_id,
    )
    return record


def run_metadata_update_flow(project_id: str, project_dir: Optional[Path] = None) -> int:
    """
    Operator entry point (produce.py --update-youtube-metadata).

    Explicitly separate from --publish-youtube: it NEVER uploads. It updates
    the metadata of the already-persisted YouTube video and verifies the
    result through the authoritative API.
    """
    print()
    print("=" * 60)
    print("YOUTUBE METADATA UPDATE — EXISTING PUBLICATION ONLY")
    print("=" * 60)

    try:
        _manifest_path, manifest = load_manifest(project_id, project_dir)
        record = get_record(manifest)
    except PublishingError as exc:
        print(f"METADATA UPDATE REFUSED: {exc}")
        return 1

    if record is None or not record.youtube_video_id:
        print("METADATA UPDATE REFUSED: no persisted YouTube video ID.")
        print("Run 'produce.py --publish-youtube' first; this command never uploads.")
        return 1

    print(f"YouTube video ID       : {record.youtube_video_id}")
    print(f"Durable state          : {record.state.value}")
    current = record.youtube_metadata
    print(f"Current title          : {current.title if current else record.title}")
    print(f"Current tags           : {list(current.tags) if current else []}")
    print()

    publisher = YouTubePublisher.from_env()
    try:
        channel = publisher.get_channel()
    except Exception as exc:
        print(f"AUTHENTICATION FAILED: {exc}")
        return 1
    if channel is None:
        print("AUTHENTICATION FAILED: authenticated account has no YouTube channel")
        return 1
    print(f"Authenticated channel  : {channel.get('snippet', {}).get('title')}")
    print()

    try:
        record = update_video_metadata(project_id, publisher, project_dir=project_dir)
    except MetadataMismatchError as exc:
        print(f"BLOCKED (truthful): {exc}")
        print("The pending metadata stays durable; resume reconciliation inspects")
        print("the EXISTING video. No duplicate upload will ever be attempted.")
        return 2
    except Exception as exc:
        print(f"METADATA UPDATE FAILED WITH UNKNOWN OUTCOME: {exc}")
        print("Resume with the same command; the video identity is already persisted.")
        return 2

    print(f"Metadata verified      : title={record.title!r}")
    print(f"Evidence               : {json.dumps(record.verification_evidence)}")
    print()

    # Final safety proof: reconciliation must confirm the existing identity
    # and perform no upload and no second update.
    final_report = reconcile_publication(project_id, publisher=publisher)
    print(final_report.summary())
    if final_report.verdict == PublicationVerdict.PUBLICATION_VERIFIED:
        print()
        print("METADATA UPDATE COMPLETE — VERIFIED VIA YOUTUBE API")
        return 0
    print()
    print("Metadata update not fully verified yet; resume the same command later.")
    return 2
