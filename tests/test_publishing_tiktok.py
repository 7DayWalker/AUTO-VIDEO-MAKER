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
# PKCE conformance — TikTok-documented hex-encoded SHA256 challenge
# (regression guard: must never revert to RFC base64url encoding)
# ---------------------------------------------------------------------------

def test_pkce_challenge_is_hex_sha256_of_verifier():
    import base64 as _b64
    import hashlib

    verifier = "Q1w2E3r4T5y6U7i8O9p0_a-b.c~d"
    expected = hashlib.sha256(verifier.encode()).hexdigest()
    actual = TikTokPublisher._code_challenge(verifier)
    assert actual == expected
    # Explicit regression guard: must NOT be the base64url form.
    b64form = _b64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    assert actual != b64form
    assert len(actual) == 64
    assert all(c in "0123456789abcdef" for c in actual)


def test_generated_verifier_satisfies_tiktok_constraints():
    """The verifier generation used by the flow must meet TikTok's documented
    charset ([A-Z]/[a-z]/[0-9]/-/./_/~) and 43-128 length bounds."""
    import re as _re
    from secrets import token_urlsafe

    for _ in range(5):
        verifier = token_urlsafe(48)  # same expression as the implementation
        assert 43 <= len(verifier) <= 128
        assert _re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier)


# ---------------------------------------------------------------------------
# Case G — Platform isolation guarantee (controlled fixture, fully offline)
#
# Original intent (previously backed by a COPY of a real production manifest,
# which made the test depend on mutable live data): prove that
#   1. the TikTok submission path refuses to run without a prepared record,
#      before any network-capable object is used;
#   2. the refused attempt creates no TikTok publication state; and
#   3. the existing YouTube publication remains byte-for-byte intact.
# That invariant now lives in an EXPLICITLY CONSTRUCTED fixture manifest so
# the test passes for the correct reason on every machine, independent of
# whatever state real production projects happen to be in.
# ---------------------------------------------------------------------------

ISOLATION_PROJECT_ID = "platform_isolation_fixture"


def _build_isolation_project(tmp_path: Path) -> Path:
    """Explicitly construct the pre-TikTok situation: YouTube VERIFIED, no
    TikTok publication record of any kind."""
    project_dir = tmp_path / ISOLATION_PROJECT_ID
    project_dir.mkdir(parents=True)
    manifest = {
        "project_id": ISOLATION_PROJECT_ID,
        "topic": "Platform isolation fixture",
        "publication": {
            "project_id": ISOLATION_PROJECT_ID,
            "state": "VERIFIED",
            "youtube_video_id": "fixture_yt_video_id",
        },
        # Intentionally NO "tiktok_publication" key.
    }
    (project_dir / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return project_dir


class _NeverCalled:
    """Network-capable stand-in that fails the test if ever invoked."""

    def init_direct_post(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("init_direct_post must never be called")

    def upload_video_chunks(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("upload_video_chunks must never be called")


# ---------------------------------------------------------------------------
# Step 5F — chunk-plan resolution & opt-in single-chunk diagnostic mode
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from app.publishing import tiktok as _tk_mod  # noqa: E402


class _ProbePublisher(TikTokPublisher):
    """Real TikTokPublisher with only the HTTP layer faked (offline)."""

    def __init__(self, session):
        self._session = session

    def _auth_headers(self):  # noqa: D102 - offline stub
        return {"Authorization": "Bearer offline-test"}


class _FakeSession:
    def __init__(self):
        self.init_bodies = []
        self.puts = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.init_bodies.append(json)
        response = SimpleNamespace(status_code=200)

        def _json():
            return {
                "data": {
                    "publish_id": "v_pub_file~v2-1.probe",
                    "upload_url": "https://open-upload.tiktokapis.com/probe",
                },
                "error": {"code": "ok", "message": ""},
            }

        response.json = _json
        response.text = ""
        return response

    def put(self, url, data=None, headers=None, timeout=None):
        self.puts.append(dict(headers))
        response = SimpleNamespace(status_code=200)
        response.text = ""
        return response


def _write_probe_video(tmp_path, size):
    path = tmp_path / "probe.mp4"
    path.write_bytes(b"\0" * size)
    return str(path)


def test_step5f_default_chunk_math_follows_tiktok_contract(monkeypatch):
    # Phase 11C Batch 1 hardening (Finding 3): TikTok FILE_UPLOAD requires
    # total_chunk_count = floor(video_size / chunk_size) (min 1), with the
    # FINAL chunk absorbing the remainder. The old ceil math declared one
    # chunk too many (e.g. 8 instead of 7 for the 75,699,693-byte field
    # failure) and desynchronized the upload from the declaration.
    monkeypatch.delenv(_tk_mod.TIKTOK_SINGLE_CHUNK_INIT_ENV, raising=False)
    # The exact real candidate video from Step 5E.
    assert _tk_mod._resolve_chunk_params(22_670_365, 10_000_000) == (10_000_000, 2)
    # The exact field-reported failure: ceil said 8, TikTok requires 7.
    assert _tk_mod._resolve_chunk_params(75_699_693, 10_485_760) == (10_485_760, 7)


def test_step5f_chunk_math_edge_cases(monkeypatch):
    monkeypatch.delenv(_tk_mod.TIKTOK_SINGLE_CHUNK_INIT_ENV, raising=False)
    resolve = _tk_mod._resolve_chunk_params
    # Smaller than one chunk: single-chunk plan MUST declare
    # chunk_size == video_size (declaring 10MB for a smaller video is what
    # produced TikTok's "The chunk size is invalid").
    assert resolve(5_000_000, 10_000_000) == (5_000_000, 1)
    assert resolve(20_000_000, 10_000_000) == (10_000_000, 2)    # exact multiple
    assert resolve(20_000_001, 10_000_000) == (10_000_000, 2)    # remainder rides the final chunk
    assert resolve(10_000_000, 10_000_000) == (10_000_000, 1)    # exactly one chunk


def test_step5f_chunk_math_rejects_empty_and_invalid_inputs(monkeypatch):
    monkeypatch.delenv(_tk_mod.TIKTOK_SINGLE_CHUNK_INIT_ENV, raising=False)
    import pytest as _pytest

    with _pytest.raises(ValueError):
        _tk_mod._resolve_chunk_params(0, 10_000_000)
    with _pytest.raises(ValueError):
        _tk_mod._resolve_chunk_params(1_000, 0)


def test_step5f_single_chunk_diagnostic_opt_in(monkeypatch):
    size = 22_670_365
    for value in ("1", "true", "yes", "on"):
        monkeypatch.setenv(_tk_mod.TIKTOK_SINGLE_CHUNK_INIT_ENV, value)
        assert _tk_mod._resolve_chunk_params(size, 10_000_000) == (size, 1)
    monkeypatch.delenv(_tk_mod.TIKTOK_SINGLE_CHUNK_INIT_ENV)
    assert _tk_mod._resolve_chunk_params(size, 10_000_000) == (10_000_000, 2)


def test_step5f_default_init_declares_multi_chunk(tmp_path, monkeypatch):
    monkeypatch.delenv(_tk_mod.TIKTOK_SINGLE_CHUNK_INIT_ENV, raising=False)
    video = _write_probe_video(tmp_path, 25_000_000)
    session = _FakeSession()
    publisher = _ProbePublisher(session)

    result = publisher.init_direct_post(
        video_path=video,
        title="probe",
        privacy_level="SELF_ONLY",
    )

    source_info = session.init_bodies[0]["source_info"]
    assert source_info == {
        "source": "FILE_UPLOAD",
        "video_size": 25_000_000,
        "chunk_size": 10_000_000,
        "total_chunk_count": 2,
    }
    assert result["chunk_size"] == 10_000_000


def test_small_video_init_declares_single_chunk_equal_to_video_size(
    tmp_path, monkeypatch
):
    """Regression for the manual-test failure: a sub-10MB video (the normal
    AVM short) must declare ONE chunk whose size equals the video size —
    never a 10MB chunk over a smaller file ("The chunk size is invalid")."""
    monkeypatch.delenv(_tk_mod.TIKTOK_SINGLE_CHUNK_INIT_ENV, raising=False)
    video = _write_probe_video(tmp_path, 7_500_000)
    session = _FakeSession()
    publisher = _ProbePublisher(session)

    result = publisher.init_direct_post(
        video_path=video,
        title="probe",
        privacy_level="SELF_ONLY",
    )

    source_info = session.init_bodies[0]["source_info"]
    assert source_info == {
        "source": "FILE_UPLOAD",
        "video_size": 7_500_000,
        "chunk_size": 7_500_000,
        "total_chunk_count": 1,
    }
    publisher.upload_video_chunks(result["upload_url"], video)
    assert len(session.puts) == 1
    assert session.puts[0]["Content-Range"] == "bytes 0-7499999/7500000"


def test_step5f_diagnostic_mode_declares_and_uploads_single_chunk(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(_tk_mod.TIKTOK_SINGLE_CHUNK_INIT_ENV, "1")
    video = _write_probe_video(tmp_path, 25_000_000)
    session = _FakeSession()
    publisher = _ProbePublisher(session)

    result = publisher.init_direct_post(
        video_path=video,
        title="probe",
        privacy_level="SELF_ONLY",
    )

    source_info = session.init_bodies[0]["source_info"]
    assert source_info["chunk_size"] == 25_000_000
    assert source_info["total_chunk_count"] == 1

    # Upload loop must agree with the declaration: exactly ONE chunk covering
    # the whole file.
    publisher.upload_video_chunks(result["upload_url"], video)
    assert len(session.puts) == 1
    assert session.puts[0]["Content-Range"] == "bytes 0-24999999/25000000"


def test_step5f_upload_loop_matches_declaration_in_normal_mode(
    tmp_path, monkeypatch
):
    monkeypatch.delenv(_tk_mod.TIKTOK_SINGLE_CHUNK_INIT_ENV, raising=False)
    video = _write_probe_video(tmp_path, 25_000_000)
    session = _FakeSession()
    publisher = _ProbePublisher(session)

    result = publisher.init_direct_post(
        video_path=video,
        title="probe",
        privacy_level="SELF_ONLY",
    )
    publisher.upload_video_chunks(result["upload_url"], video)

    # Exactly total_chunk_count PUTs; the FINAL chunk absorbs the remainder
    # (15,000,000 bytes here — within TikTok's 128MB final-chunk allowance).
    assert result["chunk_size"] == 10_000_000
    assert len(session.puts) == 2
    assert session.puts[0]["Content-Range"] == "bytes 0-9999999/25000000"
    assert session.puts[1]["Content-Range"] == "bytes 10000000-24999999/25000000"


# ---------------------------------------------------------------------------
# Step 5J — VERIFIED_SANDBOX_PRIVATE terminal state
# ---------------------------------------------------------------------------

def _complete_no_post():
    return {"status": "PUBLISH_COMPLETE", "post_id": None}


def test_step5j_a_publish_complete_with_post_id_still_verified(
    tmp_path, tiktok_gate, monkeypatch
):
    monkeypatch.delenv("TIKTOK_PUBLISH_ENVIRONMENT", raising=False)
    project_dir = build_project(tmp_path)
    tt.prepare_tiktok_publish(PROJECT_ID, project_dir)
    publisher = FakeTikTokPublisher(status_queue=[complete_status()])
    tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)
    verified = tt.verify_tiktok_publish(PROJECT_ID, publisher, project_dir)
    assert verified.state == PublicationState.VERIFIED
    assert verified.tiktok_post_id == POST_ID


def test_step5j_b_sandbox_private_terminal_state(tmp_path, tiktok_gate, monkeypatch):
    monkeypatch.setenv("TIKTOK_PUBLISH_ENVIRONMENT", "sandbox")
    project_dir = build_project(tmp_path)
    tt.prepare_tiktok_publish(PROJECT_ID, project_dir)  # metadata privacy=SELF_ONLY

    publisher = FakeTikTokPublisher(status_queue=[_complete_no_post()])
    tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)
    record = tt.verify_tiktok_publish(PROJECT_ID, publisher, project_dir)

    assert record.state == PublicationState.VERIFIED_SANDBOX_PRIVATE
    assert record.tiktok_publish_id == PUBLISH_ID
    assert record.verified_at is not None
    ev = record.verification_evidence
    assert ev["status"] == "PUBLISH_COMPLETE"
    assert ev["post_id"] is None
    assert ev["privacy_level"] == "SELF_ONLY"
    assert ev["classification"] == "sandbox_private_terminal"


def test_step5j_c_no_post_id_outside_sandbox_context_stays_unverified(
    tmp_path, tiktok_gate, monkeypatch
):
    # Production context: must NOT become VERIFIED_SANDBOX_PRIVATE.
    monkeypatch.delenv("TIKTOK_PUBLISH_ENVIRONMENT", raising=False)
    project_dir = build_project(tmp_path)
    tt.prepare_tiktok_publish(PROJECT_ID, project_dir)
    publisher = FakeTikTokPublisher(status_queue=[_complete_no_post()])
    tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)
    record = tt.verify_tiktok_publish(PROJECT_ID, publisher, project_dir)
    assert record.state == PublicationState.PROCESSING
    assert "no public post id" in (record.error or "")

    # Sandbox env but non-SELF_ONLY privacy: still not eligible.
    monkeypatch.setenv("TIKTOK_PUBLISH_ENVIRONMENT", "sandbox")
    project_dir2 = build_project(tmp_path)
    tt.prepare_tiktok_publish(PROJECT_ID, project_dir2, privacy_level="SELF_ONLY")
    manifest_path, manifest = tt.load_manifest(PROJECT_ID, project_dir2)
    rec = tt.get_tiktok_record(manifest)
    rec.tiktok_metadata.privacy_level = "MUTUAL_FOLLOW_FRIENDS"
    tt.persist_tiktok_record(manifest_path, manifest, rec)
    publisher2 = FakeTikTokPublisher(status_queue=[_complete_no_post()])
    tt.submit_tiktok_publish(PROJECT_ID, publisher2, project_dir2)
    record2 = tt.verify_tiktok_publish(PROJECT_ID, publisher2, project_dir2)
    assert record2.state != PublicationState.VERIFIED_SANDBOX_PRIVATE


def test_step5j_d_normal_verified_rule_unchanged_offline(tmp_path, tiktok_gate):
    project_dir = build_project(tmp_path)
    tt.prepare_tiktok_publish(PROJECT_ID, project_dir)
    mark_publish_id_persisted(project_dir)
    publisher = FakeTikTokPublisher(status_queue=[complete_status()])
    tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)
    verified = tt.verify_tiktok_publish(PROJECT_ID, publisher, project_dir)
    assert verified.state == PublicationState.VERIFIED
    report = tt.reconcile_tiktok_publication(
        PROJECT_ID, publisher=None, project_dir=project_dir
    )
    assert report.verdict == PublicationVerdict.PUBLICATION_VERIFIED


def test_step5j_e_f_terminal_state_and_duplicate_refusal(
    tmp_path, tiktok_gate, monkeypatch
):
    monkeypatch.setenv("TIKTOK_PUBLISH_ENVIRONMENT", "sandbox")
    project_dir = build_project(tmp_path)
    tt.prepare_tiktok_publish(PROJECT_ID, project_dir)

    # Canonical submit → verify reaches the new terminal state.
    publisher = FakeTikTokPublisher(status_queue=[_complete_no_post(), _complete_no_post()])
    submitted = tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)
    finalized = tt.verify_tiktok_publish(PROJECT_ID, publisher, project_dir)
    assert finalized.state == PublicationState.VERIFIED_SANDBOX_PRIVATE

    # Terminal: offline reconciliation reports verified.
    report = tt.reconcile_tiktok_publication(
        PROJECT_ID, publisher=None, project_dir=project_dir
    )
    assert report.verdict == PublicationVerdict.PUBLICATION_VERIFIED

    # Anti-duplicate guard: existing publish_id → idempotent refusal, never a
    # second submission.
    again = tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)
    assert again.tiktok_publish_id == submitted.tiktok_publish_id
    assert again.state == PublicationState.VERIFIED_SANDBOX_PRIVATE
    assert len(publisher.init_calls) == 1
    assert submitted.tiktok_publish_id == finalized.tiktok_publish_id


def test_step5j_g_reconciliation_history_preserved_through_finalization(
    tmp_path, tiktok_gate, monkeypatch
):
    monkeypatch.setenv("TIKTOK_PUBLISH_ENVIRONMENT", "sandbox")
    project_dir = build_project(tmp_path)
    tt.prepare_tiktok_publish(PROJECT_ID, project_dir)

    # Simulate the Step-5I preserved history on the live record.
    manifest_path, manifest = tt.load_manifest(PROJECT_ID, project_dir)
    rec = tt.get_tiktok_record(manifest)
    rec.reconciliation_history = [{
        "previous_state": "UNKNOWN_RECONCILIATION_REQUIRED",
        "classification": "direct_post_init_rejected",
        "publish_id_issued": False,
    }]
    tt.persist_tiktok_record(manifest_path, manifest, rec)

    publisher = FakeTikTokPublisher(status_queue=[_complete_no_post()])
    tt.submit_tiktok_publish(PROJECT_ID, publisher, project_dir)
    finalized = tt.verify_tiktok_publish(PROJECT_ID, publisher, project_dir)

    persisted = load_record(project_dir)
    assert finalized.state == PublicationState.VERIFIED_SANDBOX_PRIVATE
    assert len(persisted.reconciliation_history) == 1
    assert (
        persisted.reconciliation_history[0]["classification"]
        == "direct_post_init_rejected"
    )

    def fetch_status(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("fetch_status must never be called")


def test_case_g_platform_isolation_controlled_fixture(tmp_path):
    project_dir = _build_isolation_project(tmp_path)

    _, manifest = tt.load_manifest(ISOLATION_PROJECT_ID, project_dir)

    # The existing YouTube publication is intact.
    yt = manifest.publication
    assert yt is not None
    assert yt.youtube_video_id == "fixture_yt_video_id"
    assert yt.state == PublicationState.VERIFIED

    # No TikTok identity exists at all.
    assert tt.get_tiktok_record(manifest) is None

    # Submitting without preparation is refused before any network object
    # is used; nothing is created.
    with pytest.raises(PublishingError):
        tt.submit_tiktok_publish(
            ISOLATION_PROJECT_ID, _NeverCalled(), project_dir=project_dir
        )

    # Reconciliation reports NOT_STARTED for TikTok while YouTube stays VERIFIED.
    report = tt.reconcile_tiktok_publication(
        ISOLATION_PROJECT_ID, publisher=None, project_dir=project_dir
    )
    assert report.verdict == PublicationVerdict.PUBLICATION_NOT_STARTED

    # The refused attempt left zero traces: YouTube untouched, TikTok absent.
    _, reloaded = tt.load_manifest(ISOLATION_PROJECT_ID, project_dir)
    assert reloaded.publication.youtube_video_id == "fixture_yt_video_id"
    assert reloaded.publication.state == PublicationState.VERIFIED
    assert tt.get_tiktok_record(reloaded) is None


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