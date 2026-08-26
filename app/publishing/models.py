"""
AUTO Video Maker - Publishing Models

Durable publication state for post-production publishing (Phase 7A).

These models are intentionally isolated from the production pipeline stages
(content/voice/scenes/captions/assembly). A project manifest carries at most
one optional ``PublicationRecord`` so publishing state never overloads or
mutates production stage semantics.

No secrets are ever stored in a PublicationRecord. OAuth tokens live in the
local credentials directory and are referenced only by path configuration.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class PublicationState(str, Enum):
    """Durable publishing state machine.

    NOT_STARTED -> PREPARED -> UPLOAD_SUBMITTED -> IDENTITY_PERSISTED
                -> PROCESSING -> VERIFIED

    FAILED and UNKNOWN_RECONCILIATION_REQUIRED are terminal-until-operator
    states. UNKNOWN_RECONCILIATION_REQUIRED is entered whenever an upload may
    have reached YouTube but no durable identity can prove what happened; it
    must never be resolved by blindly uploading again.
    """

    NOT_STARTED = "NOT_STARTED"
    PREPARED = "PREPARED"
    UPLOAD_SUBMITTED = "UPLOAD_SUBMITTED"
    IDENTITY_PERSISTED = "IDENTITY_PERSISTED"
    PROCESSING = "PROCESSING"
    VERIFIED = "VERIFIED"
    # Step 5J: terminal success for explicitly configured Sandbox/unaudited
    # SELF_ONLY publications where TikTok reports PUBLISH_COMPLETE but never
    # issues a public post_id (proven live behavior; human-confirmed visibility).
    VERIFIED_SANDBOX_PRIVATE = "VERIFIED_SANDBOX_PRIVATE"
    FAILED = "FAILED"
    UNKNOWN_RECONCILIATION_REQUIRED = "UNKNOWN_RECONCILIATION_REQUIRED"


class PublicationVerdict(str, Enum):
    """Evidence-based publication verdicts returned by reconciliation."""

    PUBLICATION_NOT_STARTED = "PUBLICATION_NOT_STARTED"
    PUBLICATION_PROCESSING = "PUBLICATION_PROCESSING"
    PUBLICATION_VERIFIED = "PUBLICATION_VERIFIED"
    PUBLICATION_FAILED = "PUBLICATION_FAILED"
    PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED = "PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED"
    # Phase 7B: a metadata update was submitted for an existing video but the
    # authoritative API does not yet confirm the new metadata. Never resolved
    # by uploading again; resolved by reconciling the exact persisted video ID.
    PUBLICATION_METADATA_UPDATE_PENDING = "PUBLICATION_METADATA_UPDATE_PENDING"


# ---------------------------------------------------------------------------
# Canonical YouTube metadata contract (Phase 7B)
# ---------------------------------------------------------------------------

# Whitelisted YouTube category IDs. Category IDs are never invented at
# runtime; anything outside this set fails validation.
YOUTUBE_CATEGORY_IDS = {
    "1": "Film & Animation",
    "2": "Autos & Vehicles",
    "10": "Music",
    "15": "Pets & Animals",
    "17": "Sports",
    "19": "Travel & Events",
    "20": "Gaming",
    "22": "People & Blogs",
    "23": "Comedy",
    "24": "Entertainment",
    "25": "News & Politics",
    "26": "Howto & Style",
    "27": "Education",
    "28": "Science & Technology",
    "29": "Nonprofits & Activism",
}

# Conservative documented default: Education.
DEFAULT_YOUTUBE_CATEGORY_ID = "27"

VALID_PRIVACY_STATUSES = {"private", "unlisted", "public"}

# YouTube platform limits enforced by validation (fail fast; no silent
# truncation of operator-supplied metadata).
YOUTUBE_MAX_TITLE_LENGTH = 100
YOUTUBE_MAX_DESCRIPTION_LENGTH = 5000
YOUTUBE_MAX_TAG_LENGTH = 100
YOUTUBE_MAX_TOTAL_TAGS_LENGTH = 500  # YouTube's total-tags character budget


class YouTubeMetadata(BaseModel):
    """
    Canonical, deterministic YouTube metadata contract for one project.

    Phase 7B. The metadata is built once from validated project evidence
    (production plan + manifest + explicit configuration), validated against
    platform limits, persisted durably with the project, and reused verbatim
    during resume, verification, and metadata updates.

    This model is independent from the upload identity: changing metadata
    after a successful upload must never trigger another upload.
    """

    title: str
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    category_id: str = Field(default=DEFAULT_YOUTUBE_CATEGORY_ID)
    privacy_status: str = Field(default="unlisted")
    made_for_kids: bool = False
    default_language: Optional[str] = None

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        stripped = (value or "").strip()
        if not stripped:
            raise ValueError("title must be non-empty")
        if len(stripped) > YOUTUBE_MAX_TITLE_LENGTH:
            raise ValueError(
                f"title exceeds YouTube limit of {YOUTUBE_MAX_TITLE_LENGTH} "
                f"characters ({len(stripped)}); no silent truncation is performed"
            )
        return stripped

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        text = value or ""
        if len(text) > YOUTUBE_MAX_DESCRIPTION_LENGTH:
            raise ValueError(
                f"description exceeds YouTube limit of "
                f"{YOUTUBE_MAX_DESCRIPTION_LENGTH} characters ({len(text)}); "
                "no silent truncation is performed"
            )
        return text

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: List[str]) -> List[str]:
        cleaned: List[str] = []
        seen = set()
        for raw in value or []:
            tag = str(raw).strip()
            if not tag:
                raise ValueError("tags must not contain empty entries")
            if len(tag) > YOUTUBE_MAX_TAG_LENGTH:
                raise ValueError(
                    f"tag exceeds {YOUTUBE_MAX_TAG_LENGTH} characters: {tag!r}"
                )
            key = tag.lower()
            if key in seen:
                raise ValueError(f"duplicate tag (case-insensitive): {tag!r}")
            seen.add(key)
            cleaned.append(tag)
        total = sum(len(t) for t in cleaned) + max(0, len(cleaned) - 1)
        if total > YOUTUBE_MAX_TOTAL_TAGS_LENGTH:
            raise ValueError(
                f"total tags length {total} exceeds YouTube budget of "
                f"{YOUTUBE_MAX_TOTAL_TAGS_LENGTH} characters"
            )
        return cleaned

    @field_validator("category_id")
    @classmethod
    def _validate_category(cls, value: str) -> str:
        category = str(value or "").strip()
        if category not in YOUTUBE_CATEGORY_IDS:
            raise ValueError(
                f"unknown YouTube category ID {category!r}; must be one of "
                f"{sorted(YOUTUBE_CATEGORY_IDS)}"
            )
        return category

    @field_validator("privacy_status")
    @classmethod
    def _validate_privacy(cls, value: str) -> str:
        privacy = str(value or "").strip().lower()
        if privacy not in VALID_PRIVACY_STATUSES:
            raise ValueError(
                f"privacy_status must be one of {sorted(VALID_PRIVACY_STATUSES)}, "
                f"got {value!r}"
            )
        return privacy

    @field_validator("default_language")
    @classmethod
    def _validate_language(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        language = str(value).strip()
        return language or None


class PublicationRecord(BaseModel):
    """
    Durable per-project publication record persisted inside the manifest.

    Enough information is preserved to safely resume after any interruption:
    the exact file fingerprint that was validated/prepared, submission
    timestamps, and the YouTube video ID as soon as it becomes known.
    """

    # Identity
    project_id: str = Field(min_length=1)
    platform: str = Field(default="youtube")

    # State machine
    state: PublicationState = Field(default=PublicationState.NOT_STARTED)

    # Validated artifact binding
    final_video_path: Optional[str] = None
    file_fingerprint: Optional[str] = Field(
        default=None,
        description="SHA-256 hex digest of the validated final MP4",
    )
    file_size: Optional[int] = None

    # Timestamps
    prepared_at: Optional[str] = None
    upload_submitted_at: Optional[str] = None
    upload_completed_at: Optional[str] = None
    verified_at: Optional[str] = None
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    # YouTube identity (persisted immediately once known)
    youtube_video_id: Optional[str] = None

    # Metadata actually submitted to YouTube (legacy flat fields, kept in
    # sync with ``youtube_metadata`` for backward compatibility)
    title: Optional[str] = None
    description: Optional[str] = None
    privacy_status: str = Field(default="unlisted")

    # Canonical metadata contract (Phase 7B). Single source of truth for what
    # was submitted to / verified on YouTube; the flat fields above mirror it.
    youtube_metadata: Optional["YouTubeMetadata"] = None
    # A metadata update submitted for an existing video whose authoritative
    # verification has not completed yet. Never triggers an upload.
    pending_metadata: Optional["YouTubeMetadata"] = None
    metadata_update_submitted_at: Optional[str] = None
    metadata_verified_at: Optional[str] = None

    # Verification evidence from the YouTube API
    verification_evidence: Dict[str, Any] = Field(default_factory=dict)

    # Diagnostics
    error: Optional[str] = None


class PublishingError(Exception):
    """Base class for publishing failures."""


class PublishingEligibilityError(PublishingError):
    """Project failed the publishing eligibility gate; upload refused."""


class DuplicateUploadBlockedError(PublishingError):
    """
    An upload was refused because durable state cannot prove that submitting
    again would not create a duplicate YouTube video.
    """


class UploadIdentityMissingError(PublishingError):
    """
    A previous submission may have reached YouTube but no durable identity
    (YouTube video ID) was persisted. The safe state is explicit manual
    reconciliation, never an automatic retry.
    """


class MetadataValidationError(PublishingError):
    """YouTube metadata failed deterministic validation; upload refused."""


class MetadataMismatchError(PublishingError):
    """
    An upload or metadata update completed, but the authoritative YouTube API
    does not confirm the expected metadata. The publication must never be
    marked VERIFIED in this situation, and the only safe resolution is
    reconciliation against the exact persisted video ID — never a retry
    upload.
    """


# ---------------------------------------------------------------------------
# TikTok publishing contract (Phase 8A)
# ---------------------------------------------------------------------------
#
# Evidence base (official TikTok developer documentation, fetched from
# developers.tiktok.com during Phase 8A capability discovery):
# - OAuth: POST https://open.tiktokapis.com/v2/oauth/token/ ; access_token
#   valid 24h; refresh_token valid 365 days; PKCE for desktop/mobile apps.
# - Direct Post init: POST https://open.tiktokapis.com/v2/post/publish/video/init/
#   scope video.publish; source FILE_UPLOAD or PULL_FROM_URL; returns a
#   durable publish_id plus a short-lived chunked upload_url.
# - post_info supports: privacy_level (PUBLIC_TO_EVERYONE,
#   MUTUAL_FOLLOW_FRIENDS, FOLLOWER_OF_CREATOR, SELF_ONLY), title/caption
#   (hashtags inline), disable_duet, disable_comment, disable_stitch,
#   video_cover_timestamp_ms.
# - Status: POST /v2/post/publish/status/fetch/ with publish_id; asynchronous
#   processing states; final public post id only when complete.
# - Unaudited clients: all content restricted to private viewing mode until
#   TikTok audits and approves the app.

TIKTOK_PRIVACY_LEVELS = {
    "PUBLIC_TO_EVERYONE",
    "MUTUAL_FOLLOW_FRIENDS",
    "FOLLOWER_OF_CREATOR",
    "SELF_ONLY",
}

# Conservative default: SELF_ONLY is the only privacy level guaranteed to be
# accepted for unaudited apps (all unaudited content is private anyway).
DEFAULT_TIKTOK_PRIVACY_LEVEL = "SELF_ONLY"

# Official caption limit for the Content Posting API (title field).
TIKTOK_MAX_TITLE_LENGTH = 2200

# Chunked FILE_UPLOAD sizing used by the official examples.
TIKTOK_DEFAULT_CHUNK_SIZE = 10_000_000


class TikTokMetadata(BaseModel):
    """
    Canonical TikTok metadata contract (Phase 8A).

    Only fields actually supported by the official Content Posting API
    direct-post endpoint are modeled. Unsupported concepts (e.g., separate
    YouTube-style tags) are deliberately absent rather than silently
    discarded: hashtags are part of the caption text on TikTok.
    """

    title: str = Field(description="Video caption; hashtags are embedded inline")
    privacy_level: str = Field(default=DEFAULT_TIKTOK_PRIVACY_LEVEL)
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False
    video_cover_timestamp_ms: Optional[int] = None

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        stripped = (value or "").strip()
        if not stripped:
            raise ValueError("title must be non-empty")
        if len(stripped) > TIKTOK_MAX_TITLE_LENGTH:
            raise ValueError(
                f"title exceeds TikTok limit of {TIKTOK_MAX_TITLE_LENGTH} "
                f"characters ({len(stripped)}); no silent truncation"
            )
        return stripped

    @field_validator("privacy_level")
    @classmethod
    def _validate_privacy(cls, value: str) -> str:
        privacy = str(value or "").strip().upper()
        if privacy not in TIKTOK_PRIVACY_LEVELS:
            raise ValueError(
                f"privacy_level must be one of {sorted(TIKTOK_PRIVACY_LEVELS)}, "
                f"got {value!r}"
            )
        return privacy

    @field_validator("video_cover_timestamp_ms")
    @classmethod
    def _validate_cover_ts(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        if value < 0:
            raise ValueError("video_cover_timestamp_ms must be non-negative")
        return int(value)


class TikTokPublicationRecord(BaseModel):
    """
    Durable per-project TikTok publication record (Phase 8A).

    Mirrors the YouTube PublicationRecord safety semantics while modeling the
    actual TikTok API identities:
    - ``tiktok_publish_id``: durable submission identity returned by the
      direct-post init endpoint BEFORE bytes are uploaded. Persisted
      immediately once known so an interrupted upload can always be
      reconciled via the status endpoint instead of re-submitting.
    - ``tiktok_post_id``: the final public post identity, only available
      after TikTok finishes asynchronous processing (PUBLISH_COMPLETE).
    """

    # Identity
    project_id: str = Field(min_length=1)
    platform: str = Field(default="tiktok")

    # State machine (reuses the shared PublicationState values; they map
    # cleanly onto the real TikTok lifecycle)
    state: PublicationState = Field(default=PublicationState.NOT_STARTED)

    # Validated artifact binding
    final_video_path: Optional[str] = None
    file_fingerprint: Optional[str] = None
    file_size: Optional[int] = None

    # Timestamps
    prepared_at: Optional[str] = None
    submission_started_at: Optional[str] = None
    submitted_at: Optional[str] = None
    verified_at: Optional[str] = None
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    # TikTok identities (publish_id persisted immediately once known)
    tiktok_publish_id: Optional[str] = None
    tiktok_post_id: Optional[str] = None
    open_id: Optional[str] = None

    # Canonical metadata submitted to TikTok
    tiktok_metadata: Optional[TikTokMetadata] = None

    # Verification evidence from the TikTok API
    verification_evidence: Dict[str, Any] = Field(default_factory=dict)

    # Preserved forensic history of superseded publication attempts (Step 5I).
    # Append-only; never written by the normal prepare/submit/verify paths so
    # historical failure evidence survives fresh submissions.
    reconciliation_history: List[Dict[str, Any]] = Field(default_factory=list)

    # Diagnostics
    error: Optional[str] = None


class TikTokAuthError(PublishingError):
    """
    TikTok authentication failed truthfully (missing credentials, expired or
    rejected token, denied authorization). Never faked or bypassed.
    """


def utc_now_iso() -> str:
    return datetime.now().isoformat()