"""
AUTO Video Maker - Publishing Package (Phase 7A/7B/8A)

Platform publishing foundation sitting strictly after the PROJECT_COMPLETE
acceptance gate. See reconciliation.py (YouTube) and tiktok_reconciliation.py
(TikTok) for the safe publication state machines.

Only ``app.publishing.models`` is imported eagerly: ``app.orchestrator.models``
depends on it, so importing the heavier reconciliation/publisher modules here
would create a circular import at package initialization time. Those are
exposed lazily below.
"""

from app.publishing.models import (
    DEFAULT_TIKTOK_PRIVACY_LEVEL,
    DEFAULT_YOUTUBE_CATEGORY_ID,
    DuplicateUploadBlockedError,
    MetadataMismatchError,
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
    YouTubeMetadata,
)

__all__ = [
    "DEFAULT_TIKTOK_PRIVACY_LEVEL",
    "DEFAULT_YOUTUBE_CATEGORY_ID",
    "DuplicateUploadBlockedError",
    "MetadataMismatchError",
    "MetadataValidationError",
    "PublicationRecord",
    "PublicationReport",
    "PublicationState",
    "PublicationVerdict",
    "PublishingEligibilityError",
    "PublishingError",
    "TikTokAuthError",
    "TikTokMetadata",
    "TikTokPublicationRecord",
    "UploadIdentityMissingError",
    "YouTubeMetadata",
    "build_tiktok_metadata",
    "build_youtube_metadata",
    "compute_file_fingerprint",
    "metadata_mismatches",
    "prepare_publish",
    "prepare_tiktok_publish",
    "reconcile_publication",
    "reconcile_tiktok_publication",
    "run_metadata_update_flow",
    "run_tiktok_publish_flow",
    "submit_tiktok_publish",
    "submit_upload",
    "update_video_metadata",
    "verify_tiktok_publish",
    "verify_upload",
]


def __getattr__(name):
    # Lazy exports that pull in the platform state machines / publishers only
    # when actually used.
    if name in {
        "PublicationReport",
        "build_youtube_metadata",
        "compute_file_fingerprint",
        "metadata_mismatches",
        "prepare_publish",
        "reconcile_publication",
        "run_metadata_update_flow",
        "submit_upload",
        "update_video_metadata",
        "verify_upload",
    }:
        from app.publishing import reconciliation as _reconciliation

        return getattr(_reconciliation, name)
    if name in {
        "build_tiktok_metadata",
        "prepare_tiktok_publish",
        "reconcile_tiktok_publication",
        "run_tiktok_publish_flow",
        "submit_tiktok_publish",
        "verify_tiktok_publish",
    }:
        from app.publishing import tiktok_reconciliation as _tiktok

        return getattr(_tiktok, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")