"""
Phase 7B focused tests: canonical YouTube metadata contract, deterministic
metadata sourcing/validation/persistence, explicit metadata-update path
separate from upload, authoritative metadata verification, interrupted-update
reconciliation, and existing-production-video safety.

All tests are offline: YouTube API interactions go through controllable fake
publishers. No real API calls occur. The production-project safety test is
read-only against a temporary COPY of the real manifest and can never trigger
an upload or modify the live video.
"""

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.publishing.models import (
    MetadataMismatchError,
    MetadataValidationError,
    PublicationState,
    PublicationVerdict,
    PublishingError,
    YouTubeMetadata,
)
from app.publishing import reconciliation as pub
from tests.test_publishing import (
    PROJECT_ID,
    VIDEO_ID,
    FakeYouTubePublisher,
    _processed_video,
    build_project,
    gate,  # noqa: F401  (pytest fixture)
    load_record,
)

# Plan-derived description used across fixtures: script + blank line + CTA.
# Built with chr(10) so no escape sequences appear in source literals.
PLAN_DESCRIPTION = "Full narration script." + chr(10) * 2 + "Follow for more"


# ---------------------------------------------------------------------------
# Case A — New publication: valid artifact + valid metadata = exactly one upload
# ---------------------------------------------------------------------------

def test_case_a_new_publication_uploads_exactly_once_with_full_metadata(tmp_path, gate):
    project_dir = build_project(tmp_path)
    pub.prepare_publish(PROJECT_ID, project_dir)

    publisher = FakeYouTubePublisher(existing=_processed_video(VIDEO_ID))
    record = pub.submit_upload(PROJECT_ID, publisher, project_dir)
    verified = pub.verify_upload(PROJECT_ID, publisher, project_dir)

    # Exactly one upload submission carrying the full validated metadata.
    assert len(publisher.upload_calls) == 1
    call = publisher.upload_calls[0]
    assert call["title"] == "Test Video Title"
    assert call["description"] == PLAN_DESCRIPTION
    assert call["privacy_status"] == "unlisted"
    assert call["category_id"] == "27"
    assert call["made_for_kids"] is False

    assert record.youtube_video_id == VIDEO_ID
    assert verified.state == PublicationState.VERIFIED
    # Canonical metadata durably persisted alongside the identity.
    persisted = load_record(project_dir)
    assert persisted.youtube_metadata is not None
    assert persisted.youtube_metadata.title == "Test Video Title"
    assert persisted.youtube_metadata.privacy_status == "unlisted"


# ---------------------------------------------------------------------------
# Case B — Resume after verified upload: no upload
# ---------------------------------------------------------------------------

def test_case_b_resume_after_verified_upload_never_uploads(tmp_path, gate):
    project_dir = build_project(tmp_path)
    pub.prepare_publish(PROJECT_ID, project_dir)
    publisher = FakeYouTubePublisher(existing=_processed_video(VIDEO_ID))
    pub.submit_upload(PROJECT_ID, publisher, project_dir)
    pub.verify_upload(PROJECT_ID, publisher, project_dir)

    # Resume: submit_upload must refuse to upload because identity exists.
    result = pub.submit_upload(PROJECT_ID, publisher, project_dir)
    assert len(publisher.upload_calls) == 1, "no second upload may occur"

    report = pub.reconcile_publication(
        PROJECT_ID, publisher=publisher, project_dir=project_dir
    )
    assert report.verdict == PublicationVerdict.PUBLICATION_VERIFIED
    assert result.youtube_video_id == VIDEO_ID


# ---------------------------------------------------------------------------
# Case C — Explicit metadata change updates the EXISTING video, never uploads
# ---------------------------------------------------------------------------

def test_case_c_metadata_update_targets_existing_video_without_upload(tmp_path, gate):
    project_dir = build_project(tmp_path)
    pub.prepare_publish(PROJECT_ID, project_dir)
    publisher = FakeYouTubePublisher(existing=_processed_video(VIDEO_ID))
    pub.submit_upload(PROJECT_ID, publisher, project_dir)
    pub.verify_upload(PROJECT_ID, publisher, project_dir)

    updated = YouTubeMetadata(
        title="Updated Test Video Title",
        description="Updated description.",
        tags=["education", "testing"],
        category_id="27",
        privacy_status="unlisted",
        made_for_kids=False,
    )
    # The API now reflects the new metadata.
    publisher.existing = {
        "id": VIDEO_ID,
        "snippet": {
            "title": "Updated Test Video Title",
            "description": "Updated description.",
            "tags": ["education", "testing"],
            "categoryId": "27",
            "channelTitle": "Test Channel",
        },
        "status": {
            "uploadStatus": "processed",
            "privacyStatus": "unlisted",
            "license": "youtube",
            "madeForKids": False,
        },
    }

    record = pub.update_video_metadata(PROJECT_ID, publisher, metadata=updated, project_dir=project_dir)

    # The update targeted the exact persisted video ID...
    assert len(publisher.update_metadata_calls) == 1
    assert publisher.update_metadata_calls[0]["video_id"] == VIDEO_ID
    assert publisher.update_metadata_calls[0]["title"] == "Updated Test Video Title"
    # ...and NO new upload ever happened.
    assert len(publisher.upload_calls) == 1  # only the original Case A upload

    # Identity immutable; metadata committed durably.
    assert record.youtube_video_id == VIDEO_ID
    assert record.title == "Updated Test Video Title"
    assert record.pending_metadata is None
    assert record.metadata_verified_at is not None
    persisted = load_record(project_dir)
    assert persisted.youtube_video_id == VIDEO_ID
    assert persisted.youtube_metadata.tags == ["education", "testing"]

    report = pub.reconcile_publication(
        PROJECT_ID, publisher=publisher, project_dir=project_dir
    )
    assert report.verdict == PublicationVerdict.PUBLICATION_VERIFIED


# ---------------------------------------------------------------------------
# Case D — Invalid metadata: nothing is uploaded or sent to the API
# ---------------------------------------------------------------------------

def test_case_d_invalid_metadata_is_rejected_before_any_api_call(tmp_path, gate):
    # Model-level validation failures.
    with pytest.raises(ValidationError):
        YouTubeMetadata(title="   ")
    with pytest.raises(ValidationError):
        YouTubeMetadata(title="x" * 101)
    with pytest.raises(ValidationError):
        YouTubeMetadata(title="ok", tags=["", "blank entry"])
    with pytest.raises(ValidationError):
        YouTubeMetadata(title="ok", tags=["a", "A"])  # duplicate, case-insensitive
    with pytest.raises(ValidationError):
        YouTubeMetadata(title="ok", category_id="999")
    with pytest.raises(ValidationError):
        YouTubeMetadata(title="ok", privacy_status="secret")

    # Update path: invalid metadata never reaches the publisher.
    project_dir = build_project(tmp_path)
    pub.prepare_publish(PROJECT_ID, project_dir)
    publisher = FakeYouTubePublisher(existing=_processed_video(VIDEO_ID))
    pub.submit_upload(PROJECT_ID, publisher, project_dir)
    pub.verify_upload(PROJECT_ID, publisher, project_dir)

    with pytest.raises(MetadataValidationError):
        pub.update_video_metadata(
            PROJECT_ID,
            publisher,
            metadata={"title": "", "description": "x"},
            project_dir=project_dir,
        )
    assert publisher.update_metadata_calls == []
    assert len(publisher.upload_calls) == 1

    # Preparation path: an over-limit plan title aborts preparation.
    plan_path = Path(project_dir / "content" / PROJECT_ID / "production_plan.json")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["title"] = "T" * 150
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    with pytest.raises(MetadataValidationError):
        pub.prepare_publish(PROJECT_ID, project_dir)
    # Still exactly the one legitimate upload from earlier in this test;
    # the invalid metadata caused no additional upload.
    assert len(publisher.upload_calls) == 1


# ---------------------------------------------------------------------------
# Case E — Interrupted metadata update: resume reconciles the existing video
# ---------------------------------------------------------------------------

def _mark_update_submitted(project_dir: Path) -> None:
    """Simulate a crash right after the update intent was persisted."""
    manifest_path, manifest = pub.load_manifest(PROJECT_ID, project_dir)
    record = pub.get_record(manifest)
    record.pending_metadata = YouTubeMetadata(
        title="Resumed Test Video Title",
        description=PLAN_DESCRIPTION,
        tags=["resumed"],
        category_id="27",
        privacy_status="unlisted",
        made_for_kids=False,
    )
    record.metadata_update_submitted_at = "2026-08-22T00:00:00"
    pub.persist_record(manifest_path, manifest, record)


def _video_with(title, description, tags=None):
    return {
        "id": VIDEO_ID,
        "snippet": {
            "title": title,
            "description": description,
            "tags": list(tags or []),
            "categoryId": "27",
            "channelTitle": "Test Channel",
        },
        "status": {
            "uploadStatus": "processed",
            "privacyStatus": "unlisted",
            "license": "youtube",
            "madeForKids": False,
        },
    }


def test_case_e_interrupted_update_reconciles_existing_video_and_commits(tmp_path, gate):
    project_dir = build_project(tmp_path)
    pub.prepare_publish(PROJECT_ID, project_dir)
    publisher = FakeYouTubePublisher(existing=_processed_video(VIDEO_ID))
    pub.submit_upload(PROJECT_ID, publisher, project_dir)
    pub.verify_upload(PROJECT_ID, publisher, project_dir)

    _mark_update_submitted(project_dir)

    # The update actually reached YouTube before the crash: the API now
    # reports the pending metadata.
    publisher.existing = _video_with(
        "Resumed Test Video Title",
        PLAN_DESCRIPTION,
        tags=["resumed"],
    )

    report = pub.reconcile_publication(
        PROJECT_ID, publisher=publisher, project_dir=project_dir
    )

    # Recovery inspects the existing video: no upload, no second update.
    assert publisher.upload_calls == [] or len(publisher.upload_calls) == 1
    assert len([c for c in publisher.upload_calls]) == 1  # original upload only
    assert publisher.update_metadata_calls == []

    assert report.verdict == PublicationVerdict.PUBLICATION_VERIFIED
    persisted = load_record(project_dir)
    assert persisted.pending_metadata is None
    assert persisted.youtube_metadata.title == "Resumed Test Video Title"
    assert persisted.youtube_video_id == VIDEO_ID


def test_case_e_interrupted_update_not_yet_reflected_reports_pending(tmp_path, gate):
    project_dir = build_project(tmp_path)
    pub.prepare_publish(PROJECT_ID, project_dir)
    publisher = FakeYouTubePublisher(existing=_processed_video(VIDEO_ID))
    pub.submit_upload(PROJECT_ID, publisher, project_dir)
    pub.verify_upload(PROJECT_ID, publisher, project_dir)

    _mark_update_submitted(project_dir)

    # The API still shows the OLD metadata: truthful pending verdict.
    report = pub.reconcile_publication(
        PROJECT_ID, publisher=publisher, project_dir=project_dir
    )

    assert report.verdict == PublicationVerdict.PUBLICATION_METADATA_UPDATE_PENDING
    assert publisher.update_metadata_calls == []
    assert len(publisher.upload_calls) == 1
    persisted = load_record(project_dir)
    assert persisted.pending_metadata is not None
    assert persisted.state == PublicationState.VERIFIED  # base publication intact


# ---------------------------------------------------------------------------
# Case F — Metadata verification mismatch: never falsely VERIFIED, no retry
# ---------------------------------------------------------------------------

def test_case_f_update_mismatch_is_truthful_and_keeps_pending(tmp_path, gate):
    project_dir = build_project(tmp_path)
    pub.prepare_publish(PROJECT_ID, project_dir)
    publisher = FakeYouTubePublisher(existing=_processed_video(VIDEO_ID))
    pub.submit_upload(PROJECT_ID, publisher, project_dir)
    pub.verify_upload(PROJECT_ID, publisher, project_dir)

    updated = YouTubeMetadata(
        title="Mismatched Update Title",
        description=PLAN_DESCRIPTION,
        category_id="27",
        privacy_status="unlisted",
    )
    # videos.update "succeeds" but the authoritative lookup still shows the
    # OLD title: the mismatch must be reported truthfully.
    publisher.existing = _processed_video(VIDEO_ID)

    with pytest.raises(MetadataMismatchError):
        pub.update_video_metadata(
            PROJECT_ID, publisher, metadata=updated, project_dir=project_dir
        )

    # No retry, no upload; pending stays durable for reconciliation.
    assert len(publisher.update_metadata_calls) == 1
    assert len(publisher.upload_calls) == 1
    persisted = load_record(project_dir)
    assert persisted.pending_metadata is not None
    assert persisted.error
    # The committed metadata was NOT replaced by unverified values.
    assert persisted.youtube_metadata.title == "Test Video Title"


def test_case_f_post_upload_metadata_mismatch_blocks_verified(tmp_path, gate):
    project_dir = build_project(tmp_path)
    pub.prepare_publish(PROJECT_ID, project_dir)

    # The upload "succeeds" but the retrieved video has a different title.
    wrong_title = _processed_video(VIDEO_ID)
    wrong_title["snippet"]["title"] = "Somebody Else Changed The Title"
    publisher = FakeYouTubePublisher(existing=wrong_title)

    pub.submit_upload(PROJECT_ID, publisher, project_dir)
    with pytest.raises(MetadataMismatchError):
        pub.verify_upload(PROJECT_ID, publisher, project_dir)

    persisted = load_record(project_dir)
    assert persisted.state == PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
    assert "mismatch" in (persisted.error or "").lower()
    assert len(publisher.upload_calls) == 1

    # Reconciliation agrees: not VERIFIED, and it never uploads again.
    report = pub.reconcile_publication(
        PROJECT_ID, publisher=publisher, project_dir=project_dir
    )
    assert report.verdict == PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED
    assert len(publisher.upload_calls) == 1


# ---------------------------------------------------------------------------
# Case G — Existing production video safety (read-only, offline)
# ---------------------------------------------------------------------------

PRODUCTION_DIR_NAME = "20260821_why_some_plants_sleep_at_night_570"
PRODUCTION_MANIFEST = (
    Path(__file__).resolve().parent.parent
    / "output" / "projects" / PRODUCTION_DIR_NAME / "manifest.json"
)
PRODUCTION_VIDEO_ID = "HTyI9r7hdVw"


@pytest.mark.skipif(
    not PRODUCTION_MANIFEST.exists(),
    reason="real production manifest not present on this machine",
)
def test_case_g_production_video_is_protected_from_duplicate_upload(tmp_path):
    # Work on a temporary COPY of the real manifest: the real project files
    # are never modified by this test.
    project_dir = tmp_path / PRODUCTION_DIR_NAME
    project_dir.mkdir(parents=True)
    shutil.copy2(PRODUCTION_MANIFEST, project_dir / "manifest.json")

    _, manifest = pub.load_manifest(PRODUCTION_DIR_NAME, project_dir)
    record = pub.get_record(manifest)
    assert record is not None
    assert record.youtube_video_id == PRODUCTION_VIDEO_ID
    assert record.state == PublicationState.VERIFIED

    # A publisher whose API always answers authoritatively for the REAL video.
    def authoritative(_video_id):
        return {
            "id": PRODUCTION_VIDEO_ID,
            "snippet": {
                "title": record.title,
                "description": record.description,
                "channelTitle": "Paul Ella",
            },
            "status": {
                "uploadStatus": "processed",
                "privacyStatus": record.privacy_status,
                "license": "youtube",
            },
        }

    class ReadOnlyPublisher:
        upload_calls = []
        update_metadata_calls = []

        def upload(self, *args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("upload must never be called for a published project")

        def update_metadata(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("update must never be called in this test")

        def get_video(self, video_id):
            return authoritative(video_id)

    publisher = ReadOnlyPublisher()

    # Resume of the publish command must be a no-op for the upload path.
    result = pub.submit_upload(PRODUCTION_DIR_NAME, publisher, project_dir=project_dir)
    assert result.youtube_video_id == PRODUCTION_VIDEO_ID

    # Reconciliation confirms the existing publication without any upload.
    report = pub.reconcile_publication(
        PRODUCTION_DIR_NAME, publisher=publisher, project_dir=project_dir
    )
    assert report.verdict == PublicationVerdict.PUBLICATION_VERIFIED
    assert report.youtube_video_id == PRODUCTION_VIDEO_ID


# ---------------------------------------------------------------------------
# Canonical metadata model unit checks
# ---------------------------------------------------------------------------

def test_metadata_model_defaults_are_conservative():
    meta = YouTubeMetadata(title="Solo Title")
    assert meta.privacy_status == "unlisted"
    assert meta.category_id == "27"
    assert meta.made_for_kids is False
    assert meta.tags == []
    assert meta.default_language is None


def test_metadata_serialization_round_trip():
    meta = YouTubeMetadata(
        title="Round Trip",
        description="desc",
        tags=["a", "b"],
        category_id="28",
        privacy_status="private",
        made_for_kids=True,
        default_language="en",
    )
    restored = YouTubeMetadata.model_validate(meta.model_dump())
    assert restored == meta


def test_build_metadata_sources_tags_from_plan_and_env(monkeypatch, tmp_path, gate):
    project_dir = build_project(tmp_path)
    plan_path = Path(project_dir / "content" / PROJECT_ID / "production_plan.json")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["metadata"] = {
        "language": "en",
        "niche": "educational",
        "content_goal": "education",
        "tags": ["plants", "nature"],
    }
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setenv("YOUTUBE_TAGS", "Nature Facts, plants")  # dedupe ci

    _, manifest = pub.load_manifest(PROJECT_ID, project_dir)
    meta = pub.build_youtube_metadata(manifest, project_dir)

    lowered = [t.lower() for t in meta.tags]
    assert "plants" in lowered
    assert "nature" in lowered
    assert "nature facts" in lowered
    assert lowered.count("plants") == 1
    assert meta.default_language == "en"
    assert meta.category_id == "27"
    assert meta.privacy_status == "unlisted"