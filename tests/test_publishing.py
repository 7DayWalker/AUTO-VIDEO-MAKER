"""
Phase 7A focused tests: publishing eligibility gate, safe upload state
machine, duplicate-upload protection, and publication reconciliation.

All tests are offline: YouTube API interactions go through a controllable
fake publisher, and media validation is exercised through the same call sites
used by production but backed by lightweight fakes. No real API calls occur.
"""

import hashlib
import json
from pathlib import Path

import pytest

from app.orchestrator.models import ProjectManifest
from app.orchestrator.reconciliation import (
    ArtifactStatus,
    ProjectVerdict,
    ReconciliationReport,
)
from app.publishing.models import (
    DuplicateUploadBlockedError,
    PublicationRecord,
    PublicationState,
    PublicationVerdict,
    PublishingEligibilityError,
    PublishingError,
    UploadIdentityMissingError,
)
from app.publishing import reconciliation as pub


PROJECT_ID = "20231024_test_project_001"
VIDEO_ID = "abc123def456"


# ---------------------------------------------------------------------------
# Fakes & fixtures
# ---------------------------------------------------------------------------

class FakeYouTubePublisher:
    """Offline stand-in for YouTubePublisher with controllable behavior."""

    def __init__(self, video_id=VIDEO_ID, existing=None, fail_with=None):
        self.video_id = video_id
        self.existing = existing  # dict returned by get_video
        self.fail_with = fail_with
        self.upload_calls = []
        self.update_metadata_calls = []
        self.get_video_calls = []

    def upload(
        self,
        video_path,
        title,
        description,
        privacy_status,
        tags=None,
        category_id=None,
        made_for_kids=False,
        default_language=None,
    ):
        self.upload_calls.append(
            {
                "video_path": video_path,
                "title": title,
                "description": description,
                "privacy_status": privacy_status,
                "tags": list(tags) if tags else None,
                "category_id": category_id,
                "made_for_kids": made_for_kids,
                "default_language": default_language,
            }
        )
        if self.fail_with is not None:
            raise self.fail_with
        return self.video_id

    def update_metadata(self, video_id, **kwargs):
        self.update_metadata_calls.append({"video_id": video_id, **kwargs})
        if self.fail_with is not None:
            raise self.fail_with
        return {"id": video_id}

    def get_video(self, video_id):
        self.get_video_calls.append(video_id)
        return self.existing


def make_report(verdict: ProjectVerdict) -> ReconciliationReport:
    return ReconciliationReport(
        project_id=PROJECT_ID,
        content={"status": ArtifactStatus.VALID.value},
        voice={"status": ArtifactStatus.VALID.value},
        scenes={"status": ArtifactStatus.VALID.value, "items": []},
        captions={"status": ArtifactStatus.NOT_REQUIRED.value},
        assembly={"status": ArtifactStatus.VALID.value},
        verdict=verdict,
        reasons=[] if verdict == ProjectVerdict.PROJECT_COMPLETE else ["incomplete"],
    )


def build_project(tmp_path: Path, final_bytes: bytes = b"final-mp4-bytes") -> Path:
    """Create a minimal completed-looking project tree with a real final MP4."""
    project_dir = tmp_path / "project"
    plan_dir = project_dir / "content" / PROJECT_ID
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "production_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "project_id": PROJECT_ID,
                "title": "Test Video Title",
                "script": "Full narration script.",
                "call_to_action": "Follow for more",
            }
        ),
        encoding="utf-8",
    )

    final_dir = project_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    final_path = final_dir / f"{PROJECT_ID}_final.mp4"
    final_path.write_bytes(final_bytes)

    manifest = ProjectManifest(project_id=PROJECT_ID, topic="Test Topic")
    manifest.content_plan_path = str(plan_path)
    manifest.final_video_path = str(final_path)
    manifest.assembly_config = {"resolution_width": 1080, "resolution_height": 1920}
    manifest.current_stage = manifest.current_stage.__class__("complete")
    (project_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict()), encoding="utf-8"
    )
    return project_dir


@pytest.fixture()
def gate(monkeypatch):
    """Patch the two validation call sites used by the eligibility gate."""
    state = {"verdict": ProjectVerdict.PROJECT_COMPLETE, "assembly": None}

    def set_verdict(verdict):
        state["verdict"] = verdict

    def set_assembly(evidence):
        state["assembly"] = evidence

    def fake_reconcile(project_id, project_dir=None, **kwargs):
        return make_report(state["verdict"])

    def fake_assembly(manifest, project_dir):
        if state["assembly"] is not None:
            return state["assembly"]
        path = Path(manifest.final_video_path)
        return {
            "status": ArtifactStatus.VALID.value,
            "path": str(path),
            "validation": {
                "valid": True,
                "path": str(path),
                "file_size": path.stat().st_size,
                "duration": 30.0,
                "width": 1080,
                "height": 1920,
                "has_video": True,
                "has_audio": True,
                "errors": [],
            },
        }

    monkeypatch.setattr(pub, "reconcile_project", fake_reconcile)
    monkeypatch.setattr(pub, "_assembly_evidence", fake_assembly)
    fixture = type("GateFixture", (), {})()
    fixture.set_verdict = set_verdict
    fixture.set_assembly = set_assembly
    return fixture


def load_record(project_dir: Path) -> PublicationRecord:
    data = json.loads((project_dir / "manifest.json").read_text(encoding="utf-8"))
    assert data.get("publication"), "publication record missing from manifest"
    return PublicationRecord.model_validate(data["publication"])


# ---------------------------------------------------------------------------
# Test A — Incomplete project rejection
# ---------------------------------------------------------------------------

def test_incomplete_project_cannot_be_published(tmp_path, gate):
    project_dir = build_project(tmp_path)
    gate.set_verdict(ProjectVerdict.PROJECT_INCOMPLETE)

    with pytest.raises(PublishingEligibilityError):
        pub.prepare_publish(PROJECT_ID, project_dir)

    # No publication record may be persisted for an ineligible project.
    data = json.loads((project_dir / "manifest.json").read_text(encoding="utf-8"))
    assert data.get("publication") is None


# ---------------------------------------------------------------------------
# Test B — Missing final artifact
# ---------------------------------------------------------------------------

def test_missing_final_artifact_rejected(tmp_path, gate):
    project_dir = build_project(tmp_path)
    gate.set_assembly({"status": ArtifactStatus.MISSING.value, "path": None})

    with pytest.raises(PublishingEligibilityError):
        pub.prepare_publish(PROJECT_ID, project_dir)


# ---------------------------------------------------------------------------
# Test C — Invalid final MP4
# ---------------------------------------------------------------------------

def test_invalid_final_mp4_rejected(tmp_path, gate):
    project_dir = build_project(tmp_path)
    gate.set_assembly(
        {
            "status": ArtifactStatus.INVALID.value,
            "path": str(project_dir / "final" / f"{PROJECT_ID}_final.mp4"),
            "validation": {"valid": False, "errors": ["no audio stream"]},
        }
    )

    with pytest.raises(PublishingEligibilityError):
        pub.prepare_publish(PROJECT_ID, project_dir)


# ---------------------------------------------------------------------------
# Test D — Valid project preparation with deterministic fingerprint
# ---------------------------------------------------------------------------

def test_valid_project_prepares_with_deterministic_fingerprint(tmp_path, gate):
    project_dir = build_project(tmp_path, final_bytes=b"deterministic-bytes")
    final_path = project_dir / "final" / f"{PROJECT_ID}_final.mp4"
    expected = hashlib.sha256(final_path.read_bytes()).hexdigest()

    record = pub.prepare_publish(PROJECT_ID, project_dir)

    assert record.state == PublicationState.PREPARED
    assert record.file_fingerprint == expected
    assert record.file_size == len(b"deterministic-bytes")
    assert record.title == "Test Video Title"
    assert record.youtube_video_id is None

    # Deterministic across repeated preparation.
    again = pub.prepare_publish(PROJECT_ID, project_dir)
    assert again.file_fingerprint == expected

    # Durable in the manifest.
    persisted = load_record(project_dir)
    assert persisted.state == PublicationState.PREPARED
    assert persisted.file_fingerprint == expected


# ---------------------------------------------------------------------------
# Test E — Successful upload persists identity before verification
# ---------------------------------------------------------------------------

def test_successful_upload_persists_identity_then_verifies(tmp_path, gate):
    project_dir = build_project(tmp_path)
    pub.prepare_publish(PROJECT_ID, project_dir)
    publisher = FakeYouTubePublisher(existing=_processed_video(VIDEO_ID))

    record = pub.submit_upload(PROJECT_ID, publisher, project_dir)

    assert len(publisher.upload_calls) == 1
    call = publisher.upload_calls[0]
    assert call["title"] == "Test Video Title"
    assert record.state == PublicationState.IDENTITY_PERSISTED
    assert record.youtube_video_id == VIDEO_ID

    # Identity durable on disk immediately after submission.
    persisted = load_record(project_dir)
    assert persisted.youtube_video_id == VIDEO_ID

    verified = pub.verify_upload(PROJECT_ID, publisher, project_dir)
    assert verified.state == PublicationState.VERIFIED
    assert verified.verified_at is not None
    assert verified.verification_evidence["uploadStatus"] == "processed"

    report = pub.reconcile_publication(PROJECT_ID, publisher=publisher, project_dir=project_dir)
    assert report.verdict == PublicationVerdict.PUBLICATION_VERIFIED


# ---------------------------------------------------------------------------
# Test F — Interruption after upload identity: resume never re-uploads
# ---------------------------------------------------------------------------

def test_resume_with_persisted_identity_does_not_upload_again(tmp_path, gate):
    project_dir = build_project(tmp_path)
    pub.prepare_publish(PROJECT_ID, project_dir)

    # Simulate a crash right after the ID was persisted.
    manifest_path, manifest = pub.load_manifest(PROJECT_ID, project_dir)
    record = pub.get_record(manifest)
    record.youtube_video_id = VIDEO_ID
    record.state = PublicationState.IDENTITY_PERSISTED
    pub.persist_record(manifest_path, manifest, record)

    publisher = FakeYouTubePublisher(existing=_processed_video(VIDEO_ID))
    result = pub.submit_upload(PROJECT_ID, publisher, project_dir)

    assert publisher.upload_calls == [], "upload must not be called again"
    assert result.youtube_video_id == VIDEO_ID

    verified = pub.verify_upload(PROJECT_ID, publisher, project_dir)
    assert verified.state == PublicationState.VERIFIED


# ---------------------------------------------------------------------------
# Test G — Unknown post-submission state: no blind duplicate upload
# ---------------------------------------------------------------------------

def test_unknown_post_submission_state_blocks_duplicate_upload(tmp_path, gate):
    project_dir = build_project(tmp_path)
    pub.prepare_publish(PROJECT_ID, project_dir)

    # Simulate a crash after UPLOAD_SUBMITTED was persisted but before any
    # YouTube identity could be recorded.
    manifest_path, manifest = pub.load_manifest(PROJECT_ID, project_dir)
    record = pub.get_record(manifest)
    record.state = PublicationState.UPLOAD_SUBMITTED
    record.upload_submitted_at = "2026-08-21T00:00:00"
    pub.persist_record(manifest_path, manifest, record)

    publisher = FakeYouTubePublisher()
    with pytest.raises(UploadIdentityMissingError):
        pub.submit_upload(PROJECT_ID, publisher, project_dir)

    assert publisher.upload_calls == [], "blind retry is forbidden"

    persisted = load_record(project_dir)
    assert persisted.state == PublicationState.UNKNOWN_RECONCILIATION_REQUIRED

    report = pub.reconcile_publication(PROJECT_ID, publisher=publisher, project_dir=project_dir)
    assert report.verdict == PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED


def test_upload_exception_enters_unknown_state_and_never_retries(tmp_path, gate):
    project_dir = build_project(tmp_path)
    pub.prepare_publish(PROJECT_ID, project_dir)

    publisher = FakeYouTubePublisher(fail_with=RuntimeError("connection reset mid-upload"))
    with pytest.raises(RuntimeError):
        pub.submit_upload(PROJECT_ID, publisher, project_dir)

    assert len(publisher.upload_calls) == 1
    persisted = load_record(project_dir)
    assert persisted.state == PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
    assert persisted.error

    # Resume attempt must refuse to upload again.
    with pytest.raises(UploadIdentityMissingError):
        pub.submit_upload(PROJECT_ID, publisher, project_dir)
    assert len(publisher.upload_calls) == 1


# ---------------------------------------------------------------------------
# Test H — File mutation after preparation
# ---------------------------------------------------------------------------

def test_file_mutation_after_preparation_rejects_upload_until_reprepared(tmp_path, gate):
    project_dir = build_project(tmp_path)
    final_path = project_dir / "final" / f"{PROJECT_ID}_final.mp4"
    original_fingerprint = pub.prepare_publish(PROJECT_ID, project_dir).file_fingerprint

    # Mutate the validated artifact after preparation.
    final_path.write_bytes(b"tampered-content")

    publisher = FakeYouTubePublisher()
    with pytest.raises(PublishingEligibilityError):
        pub.submit_upload(PROJECT_ID, publisher, project_dir)
    assert publisher.upload_calls == []

    # Re-preparation revalidates and refreshes the fingerprint...
    record = pub.prepare_publish(PROJECT_ID, project_dir)
    assert record.file_fingerprint != original_fingerprint
    assert record.file_fingerprint == hashlib.sha256(final_path.read_bytes()).hexdigest()

    # ...after which a single clean upload proceeds.
    publisher = FakeYouTubePublisher(existing=_processed_video(VIDEO_ID))
    submitted = pub.submit_upload(PROJECT_ID, publisher, project_dir)
    assert len(publisher.upload_calls) == 1
    assert submitted.youtube_video_id == VIDEO_ID


def test_file_mutation_after_submission_requires_manual_reconciliation(tmp_path, gate):
    project_dir = build_project(tmp_path)
    final_path = project_dir / "final" / f"{PROJECT_ID}_final.mp4"
    pub.prepare_publish(PROJECT_ID, project_dir)

    manifest_path, manifest = pub.load_manifest(PROJECT_ID, project_dir)
    record = pub.get_record(manifest)
    record.state = PublicationState.UPLOAD_SUBMITTED
    pub.persist_record(manifest_path, manifest, record)

    final_path.write_bytes(b"mutated-after-submission")

    with pytest.raises(DuplicateUploadBlockedError):
        pub.prepare_publish(PROJECT_ID, project_dir)

    persisted = load_record(project_dir)
    assert persisted.state == PublicationState.UNKNOWN_RECONCILIATION_REQUIRED


# ---------------------------------------------------------------------------
# Reconciliation outcome mapping
# ---------------------------------------------------------------------------

def test_reconcile_publication_outcomes(tmp_path, gate):
    project_dir = build_project(tmp_path)

    # No record at all.
    report = pub.reconcile_publication(PROJECT_ID, project_dir=project_dir)
    assert report.verdict == PublicationVerdict.PUBLICATION_NOT_STARTED

    # Prepared but not submitted.
    pub.prepare_publish(PROJECT_ID, project_dir)
    report = pub.reconcile_publication(PROJECT_ID, project_dir=project_dir)
    assert report.verdict == PublicationVerdict.PUBLICATION_NOT_STARTED

    # Persisted ID confirmed by the API as processed.
    manifest_path, manifest = pub.load_manifest(PROJECT_ID, project_dir)
    record = pub.get_record(manifest)
    record.youtube_video_id = VIDEO_ID
    record.state = PublicationState.IDENTITY_PERSISTED
    pub.persist_record(manifest_path, manifest, record)

    publisher = FakeYouTubePublisher(existing=_processed_video(VIDEO_ID))
    report = pub.reconcile_publication(PROJECT_ID, publisher=publisher, project_dir=project_dir)
    assert report.verdict == PublicationVerdict.PUBLICATION_VERIFIED

    # Persisted ID that the API reports as still processing.
    publisher = FakeYouTubePublisher(existing=_processing_video(VIDEO_ID))
    report = pub.reconcile_publication(PROJECT_ID, publisher=publisher, project_dir=project_dir)
    assert report.verdict == PublicationVerdict.PUBLICATION_PROCESSING

    # Persisted ID the API cannot find: explicit unknown state, no retry.
    publisher = FakeYouTubePublisher(existing=None)
    report = pub.reconcile_publication(PROJECT_ID, publisher=publisher, project_dir=project_dir)
    assert report.verdict == PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED
    persisted = load_record(project_dir)
    assert persisted.state == PublicationState.UNKNOWN_RECONCILIATION_REQUIRED


def test_manifest_round_trip_preserves_publication(tmp_path, gate):
    project_dir = build_project(tmp_path)
    pub.prepare_publish(PROJECT_ID, project_dir)
    _, manifest = pub.load_manifest(PROJECT_ID, project_dir)
    record = pub.get_record(manifest)
    assert isinstance(record, PublicationRecord)
    assert record.state == PublicationState.PREPARED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _processed_video(video_id: str, privacy_status: str = "unlisted") -> dict:
    # Echoes exactly what YouTube stores for the metadata submitted by the
    # Phase 7B pipeline (plan-derived title/description, Education category,
    # no tags, not made for kids).
    return {
        "id": video_id,
        "snippet": {
            "title": "Test Video Title",
            "description": "Full narration script.\n\nFollow for more",
            "categoryId": "27",
            "channelTitle": "Test Channel",
        },
        "status": {
            "uploadStatus": "processed",
            "privacyStatus": privacy_status,
            "license": "youtube",
            "madeForKids": False,
        },
    }


def _processing_video(video_id: str) -> dict:
    info = _processed_video(video_id)
    info["status"]["uploadStatus"] = "processing"
    return info