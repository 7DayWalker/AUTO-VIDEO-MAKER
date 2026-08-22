"""
Phase 8A focused tests: TikTok publishing foundation based on the OFFICIAL
Content Posting API lifecycle.

All tests are offline: TikTok API interactions go through controllable fake
publishers/sessions. No real API calls occur. The production-project safety
test works only on a temporary COPY of the real manifest and can never trigger
a submission or modify any live publication.
"""

import json
import shutil
from pathlib import Path

import pytest

from app.publishing.models import (
    DEFAULT_TIKTOK_PRIVACY_LEVEL,
    MetadataValidationError,
    PublicationState,
    PublicationVerdict,
    PublishingError,
    TikTokAuthError,
    TikTokMetadata,
)
from app.publishing import tiktok_reconciliation as tt
from app.publishing.tiktok import TikTokPublisher
from tests.test_publishing import (
    PROJECT_ID,
    build_project,
    gate,  # noqa: F401  (pytest fixture)
)

PUBLISH_ID = "v_pub_file~v2-1.test123456789"
POST_ID = "7301234567890123456"


# ---------------------------------------------------------------------------
# Fakes & helpers
# ---------------------------------------------------------------------------

class FakeTikTokPublisher:
    """Offline stand-in for TikTokPublisher with controllable behavior."""

    def __init__(
        self,
        publish_id=PUBLISH_ID,
        fail_init=None,
        fail_upload=None,
        status_queue=None,
    ):
        self.publish_id = publish_id
        self.fail_init = fail_init
        self.fail_upload = fail_upload
        self.status_queue = list(status_queue or [])
        self.init_calls = []
        self.upload_calls = []
        self.status_calls = []

    def init_direct_post(self, video_path, title, privacy_level, **kwargs):
        self.init_calls.append(
            {
                "video_path": video_path,
                "title": title,
                "privacy_level": privacy_level,
                **kwargs,
            }
        )
        if self.fail_init is not None:
            raise self.fail_init
        return {"publish_id": self.publish_id, "upload_url": "https://upload.example/video"}

    def upload_video_chunks(self, upload_url, video_path):
        self.upload_calls.append((upload_url, video_path))
        if self.fail_upload is not None:
            raise self.fail_upload

    def fetch_status(self, publish_id):
        self.status_calls.append(publish_id)
        if not self.status_queue:
            return None
        return self.status_queue.pop(0)


def complete_status(post_id=POST_ID):
    return {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": post_id}


def processing_status():
    return {"status": "PUBLISH_PROCESSING"}


# ---------------------------------------------------------------------------
# Gate fixture for the TikTok module (patches ITS call sites)
# ---------------------------------------------------------------------------

@pytest.fixture()
def tiktok_gate(monkeypatch):
    """Patch the two validation call sites used by the TikTok eligibility gate."""
    from app.orchestrator.reconciliation import ArtifactStatus, ProjectVerdict
    from tests.test_publishing import make_report

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

    monkeypatch.setattr(tt, "reconcile_project", fake_reconcile)
    monkeypatch.setattr(tt, "_assembly_evidence", fake_assembly)
    fixture = type("TikTokGateFixture", (), {})()
    fixture.set_verdict = set_verdict
    fixture.set_assembly = set_assembly
    return fixture


def load_record(project_dir: Path):
    data = json.loads((project_dir / "manifest.json").read_text(encoding="utf-8"))
    assert data.get("tiktok_publication"), "tiktok_publication record missing"
    from app.publishing.models import TikTokPublicationRecord

    return TikTokPublicationRecord.model_validate(data["tiktok_publication"])


def mark_submission_started(project_dir: Path) -> None:
    """Simulate a crash after UPLOAD_SUBMITTED was persisted, before init."""
    manifest_path, manifest = tt.load_manifest(PROJECT_ID, project_dir)
    record = tt.get_tiktok_record(manifest)
    record.state = PublicationState.UPLOAD_SUBMITTED
    record.submission_started_at = "2026-08-22T00:00:00"
    tt.persist_tiktok_record(manifest_path, manifest, record)


def mark_publish_id_persisted(project_dir: Path) -> None:
    """Simulate a crash right after init returned the durable publish_id."""
    manifest_path, manifest = tt.load_manifest(PROJECT_ID, project_dir)
    record = tt.get_tiktok_record(manifest)
    record.tiktok_publish_id = PUBLISH_ID
    record.state = PublicationState.IDENTITY_PERSISTED
    tt.persist_tiktok_record(manifest_path, manifest, record)


# ---------------------------------------------------------------------------
# Case A — Fresh publication preparation
# ---------------------------------------------------------------------------

def test_case_a_fresh_preparation_is_ready_for_official_submission(tmp_path, tiktok_gate):
    project_dir = build_project(tmp_path)

    record = tt.prepare_tiktok_publish(PROJECT_ID, project_dir)

    assert record.state == PublicationState.PREPARED
    assert record.tiktok_publish_id is None
    assert record.tiktok_metadata is not None
    # Base fixture plan has no metadata block: caption is the plan title
    # alone. Hashtag derivation is covered by its dedicated test below.
    assert record.tiktok_metadata.title == "Test Video Title"
    assert record.tiktok_metadata.privacy_level == DEFAULT_TIKTOK_PRIVACY_LEVEL

    # Durable in the manifest; nothing was submitted to any network.
    persisted = load_record(project_dir)
    assert persisted.state == PublicationState.PREPARED
    assert persisted.tiktok_metadata.title.startswith("Test Video Title")


# ---------------------------------------------------------------------------
# Case B — Existing publication identity: no duplicate submission
# ---------------------------------------------------------------------------

def test_case_b_existing_identity_never_submits_again(tmp_path, tiktok_gate):
    project_dir = build_project(tmp_path)
    tt.prepare_tiktok_publish(PROJECT_ID, project_dir)

    # Two authoritative answers: one for verification, one for the final
    # duplicate-safe reconciliation check.
    publisher = FakeTikTokPublisher(
        status_queue=[complete_status(), complete_status()]
    )
    record = tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)
    verified = tt.verify_tiktok_publish(PROJECT_ID, publisher, project_dir)

    assert len(publisher.init_calls) == 1
    assert record.tiktok_publish_id == PUBLISH_ID
    assert verified.state == PublicationState.VERIFIED
    assert verified.tiktok_post_id == POST_ID

    # Resume: must refuse to submit again.
    result = tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)
    assert len(publisher.init_calls) == 1, "no duplicate submission may occur"
    assert result.tiktok_publish_id == PUBLISH_ID

    report = tt.reconcile_tiktok_publication(
        PROJECT_ID, publisher=publisher, project_dir=project_dir
    )
    assert report.verdict == PublicationVerdict.PUBLICATION_VERIFIED


def test_case_b_submission_carries_validated_metadata(tmp_path, tiktok_gate):
    project_dir = build_project(tmp_path)
    tt.prepare_tiktok_publish(PROJECT_ID, project_dir)

    publisher = FakeTikTokPublisher(status_queue=[processing_status()])
    tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)

    call = publisher.init_calls[0]
    assert call["title"].startswith("Test Video Title")
    assert call["privacy_level"] == "SELF_ONLY"
    assert call["disable_comment"] is False
    assert call["disable_duet"] is False
    assert call["disable_stitch"] is False


# ---------------------------------------------------------------------------
# Case C — Invalid metadata: no network submission
# ---------------------------------------------------------------------------

def test_case_c_invalid_metadata_blocks_before_any_network_call(tmp_path, tiktok_gate):
    project_dir = build_project(tmp_path)

    with pytest.raises(MetadataValidationError):
        tt.prepare_tiktok_publish(
            PROJECT_ID, project_dir, privacy_level="DEFINITELY_NOT_VALID"
        )

    data = json.loads((project_dir / "manifest.json").read_text(encoding="utf-8"))
    assert data.get("tiktok_publication") is None

    publisher = FakeTikTokPublisher()
    with pytest.raises(PublishingError):
        tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)
    assert publisher.init_calls == []


def test_case_c_model_validation_rejects_bad_values():
    with pytest.raises(Exception):
        TikTokMetadata(title="   ")
    with pytest.raises(Exception):
        TikTokMetadata(title="x" * 2201)
    with pytest.raises(Exception):
        TikTokMetadata(title="ok", privacy_level="friends only")


# ---------------------------------------------------------------------------
# Case D — Interrupted submission: resume reconciles, never blind-resubmits
# ---------------------------------------------------------------------------

def test_case_d_interrupted_before_init_requires_reconciliation(tmp_path, tiktok_gate):
    project_dir = build_project(tmp_path)
    tt.prepare_tiktok_publish(PROJECT_ID, project_dir)
    mark_submission_started(project_dir)

    publisher = FakeTikTokPublisher()
    with pytest.raises(Exception) as excinfo:
        tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)

    assert "refusing blind duplicate" in str(excinfo.value).lower()
    assert publisher.init_calls == []
    persisted = load_record(project_dir)
    assert persisted.state == PublicationState.UNKNOWN_RECONCILIATION_REQUIRED

    report = tt.reconcile_tiktok_publication(
        PROJECT_ID, publisher=publisher, project_dir=project_dir
    )
    assert report.verdict == PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED


def test_case_d_interrupted_after_publish_id_resumes_via_status(tmp_path, tiktok_gate):
    project_dir = build_project(tmp_path)
    tt.prepare_tiktok_publish(PROJECT_ID, project_dir)
    mark_publish_id_persisted(project_dir)

    # The bytes actually finished uploading before the crash; TikTok has
    # already completed processing.
    publisher = FakeTikTokPublisher(status_queue=[complete_status()])

    result = tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)
    assert publisher.init_calls == [], "resume must NOT re-run init"
    assert result.tiktok_publish_id == PUBLISH_ID

    verified = tt.verify_tiktok_publish(PROJECT_ID, publisher, project_dir)
    assert verified.state == PublicationState.VERIFIED
    assert verified.tiktok_post_id == POST_ID


def test_case_d_chunk_upload_failure_keeps_durable_identity(tmp_path, tiktok_gate):
    project_dir = build_project(tmp_path)
    tt.prepare_tiktok_publish(PROJECT_ID, project_dir)

    publisher = FakeTikTokPublisher(fail_upload=RuntimeError("connection reset mid-upload"))
    with pytest.raises(RuntimeError):
        tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)

    # The publish_id was persisted BEFORE the upload began, so the outcome is
    # reconcilable rather than unknown.
    persisted = load_record(project_dir)
    assert persisted.tiktok_publish_id == PUBLISH_ID
    assert persisted.state == PublicationState.IDENTITY_PERSISTED
    assert len(publisher.init_calls) == 1

    # Resume must not re-submit; reconciliation uses the official status API.
    again = tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)
    assert len(publisher.init_calls) == 1
    assert again.tiktok_publish_id == PUBLISH_ID


# ---------------------------------------------------------------------------
# Case E — Ambiguous API result: requires reconciliation, no auto-repost
# ---------------------------------------------------------------------------

def test_case_e_ambiguous_status_result_never_auto_resubmits(tmp_path, tiktok_gate):
    project_dir = build_project(tmp_path)
    tt.prepare_tiktok_publish(PROJECT_ID, project_dir)

    # Init succeeds locally but the status endpoint later reports nothing.
    publisher = FakeTikTokPublisher(status_queue=[None])
    tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)

    with pytest.raises(PublishingError):
        tt.verify_tiktok_publish(PROJECT_ID, publisher, project_dir)

    persisted = load_record(project_dir)
    assert persisted.state == PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
    assert len(publisher.init_calls) == 1

    report = tt.reconcile_tiktok_publication(
        PROJECT_ID, publisher=publisher, project_dir=project_dir
    )
    assert report.verdict == PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED
    assert len(publisher.init_calls) == 1


def test_case_e_processing_state_polls_within_bounds_and_stays_truthful(tmp_path, tiktok_gate):
    project_dir = build_project(tmp_path)
    tt.prepare_tiktok_publish(PROJECT_ID, project_dir)

    # TikTok keeps reporting PROCESSING on every bounded poll.
    publisher = FakeTikTokPublisher(
        status_queue=[processing_status(), processing_status(), processing_status()]
    )
    tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)

    verified = tt.verify_tiktok_publish(
        PROJECT_ID, publisher, project_dir, poll_attempts=3, poll_interval_seconds=0
    )
    assert verified.state == PublicationState.PROCESSING
    assert verified.tiktok_post_id is None
    assert len(publisher.status_calls) == 3  # bounded polling, no infinite loop


# ---------------------------------------------------------------------------
# Case F — Expired or invalid credentials: truthful failure, no fake success
# ---------------------------------------------------------------------------

class FakeSession401:
    def get(self, *args, **kwargs):
        class R:
            status_code = 401
            text = '{"error":{"code":"access_token_invalid"}}'

        return R()


def test_case_f_missing_credentials_fail_truthfully(monkeypatch):
    monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False)
    monkeypatch.delenv("TIKTOK_CLIENT_SECRET", raising=False)
    publisher = TikTokPublisher.from_env()
    with pytest.raises(TikTokAuthError):
        publisher.authenticate()


def test_case_f_invalid_token_fails_truthfully(monkeypatch, tmp_path):
    import time as _time

    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "test-key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("TIKTOK_TOKEN_FILE", str(tmp_path / "tk.json"))
    # A token that still looks valid locally but is rejected by the API.
    (tmp_path / "tk.json").write_text(
        json.dumps(
            {
                "access_token": "rejected-by-tiktok",
                "expires_at": _time.time() + 3600,
            }
        ),
        encoding="utf-8",
    )

    publisher = TikTokPublisher(client_key="test-key", client_secret="test-secret",
                                token_file=str(tmp_path / "tk.json"),
                                session=FakeSession401())
    with pytest.raises(TikTokAuthError):
        publisher.get_user()


def test_case_f_expiry_detection():
    import time as _time

    fresh = {"access_token": "a", "expires_at": _time.time() + 3600}
    stale = {"access_token": "a", "expires_at": _time.time() - 10}
    assert TikTokPublisher._token_expired(fresh) is False
    assert TikTokPublisher._token_expired(stale) is True
    assert TikTokPublisher._token_expired({}) is True


# ---------------------------------------------------------------------------
# Case G — Existing production project safety (read-only, offline)
# ---------------------------------------------------------------------------

PRODUCTION_DIR_NAME = "20260821_why_some_plants_sleep_at_night_570"
PRODUCTION_MANIFEST = (
    Path(__file__).resolve().parent.parent
    / "output" / "projects" / PRODUCTION_DIR_NAME / "manifest.json"
)


@pytest.mark.skipif(
    not PRODUCTION_MANIFEST.exists(),
    reason="real production manifest not present on this machine",
)
def test_case_g_production_project_platform_isolation(tmp_path):
    # Work on a temporary COPY of the real manifest; real files are untouched.
    project_dir = tmp_path / PRODUCTION_DIR_NAME
    project_dir.mkdir(parents=True)
    shutil.copy2(PRODUCTION_MANIFEST, project_dir / "manifest.json")

    _, manifest = tt.load_manifest(PRODUCTION_DIR_NAME, project_dir)

    # The existing YouTube publication must be fully intact.
    yt = manifest.publication
    assert yt is not None
    assert yt.youtube_video_id == "HTyI9r7hdVw"
    assert yt.state == PublicationState.VERIFIED

    # No TikTok identity exists yet for this project.
    tk = tt.get_tiktok_record(manifest)
    assert tk is None or tk.tiktok_publish_id is None

    # Submitting without preparation is refused; nothing is created.
    class NeverCalled:
        def init_direct_post(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("must never be called")

        def upload_video_chunks(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("must never be called")

    with pytest.raises(PublishingError):
        tt.submit_tiktok_publish(PRODUCTION_DIR_NAME, NeverCalled(), project_dir=project_dir)

    # Reconciliation reports NOT_STARTED for TikTok while YouTube stays VERIFIED.
    report = tt.reconcile_tiktok_publication(
        PRODUCTION_DIR_NAME, publisher=None, project_dir=project_dir
    )
    assert report.verdict == PublicationVerdict.PUBLICATION_NOT_STARTED

    _, reloaded = tt.load_manifest(PRODUCTION_DIR_NAME, project_dir)
    assert reloaded.publication.youtube_video_id == "HTyI9r7hdVw"
    assert reloaded.publication.state == PublicationState.VERIFIED


# ---------------------------------------------------------------------------
# Canonical metadata model unit checks
# ---------------------------------------------------------------------------

def test_tiktok_metadata_defaults_are_conservative():
    meta = TikTokMetadata(title="Solo Caption")
    assert meta.privacy_level == "SELF_ONLY"
    assert meta.disable_comment is False
    assert meta.disable_duet is False
    assert meta.disable_stitch is False
    assert meta.video_cover_timestamp_ms is None


def test_build_tiktok_metadata_derives_hashtags_from_plan(monkeypatch, tmp_path):
    project_dir = build_project(tmp_path)
    plan_path = Path(project_dir / "content" / PROJECT_ID / "production_plan.json")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["metadata"] = {
        "language": "en",
        "niche": "educational",
        "content_goal": "education",
        "tags": ["plants", "nature facts"],
    }
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    _, manifest = tt.load_manifest(PROJECT_ID, project_dir)
    meta = tt.build_tiktok_metadata(manifest, project_dir)

    lowered = meta.title.lower()
    assert lowered.startswith("test video title")
    assert "#plants" in lowered
    assert "#naturefacts" in lowered
    assert "#educational" in lowered
    assert meta.privacy_level == "SELF_ONLY"