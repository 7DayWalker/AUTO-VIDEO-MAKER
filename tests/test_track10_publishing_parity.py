"""Track 10: hermetic manual publishing parity and durable-evidence safety.

Only localhost HTTP is allowed. Publishers, OAuth, and physical media probes
are fakes; binding validation and the acceptance oracle remain real in the
derived-unit integration tests. Every writable artifact belongs to tmp_path.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.content.models import ProductionMetadata, ProductionPlan, Scene
from app.orchestrator import reconciliation as oracle
from app.orchestrator.models import ProductionBinding
from app.orchestrator.production_execution import ProductionExecutor, UnitExecutionState
from app.production.models import (
    Production, ProductionCommand, ProductionUnit, SourceAsset, SourceSegment,
    derive_production_unit_from_segment,
)
from app.production.storage import compute_content_hash, save_new_production, unit_plan_dir
from app.publishing import reconciliation as pub
from app.publishing import tiktok_reconciliation as tt
from app.publishing.models import (
    DuplicateUploadBlockedError, PublicationRecord,
    PublicationState, PublicationVerdict, TikTokAuthError, TikTokMetadata,
    TikTokPublicationRecord, YouTubeMetadata,
)
from app.publishing.tiktok import TikTokPublisher
from app.publishing.youtube import YouTubePublisher
from app.web import server as web
from tests.test_publishing import PROJECT_ID, VIDEO_ID, build_project, gate  # noqa: F401


PID = "prd_20261024_track10_001"
UID = "unit-001"
STAMP = "2026-10-24T09:00:00+00:00"


def forbidden(*args, **kwargs):
    raise AssertionError("An external API, interactive OAuth, or forbidden submission was reached")


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """No real platform credentials, browser consent, or outbound connections."""
    for name, value in {
        "YOUTUBE_TOKEN_FILE": str(tmp_path / "missing-yt.json"),
        "TIKTOK_TOKEN_FILE": str(tmp_path / "missing-tk.json"),
        "YOUTUBE_PRIVACY_STATUS": "unlisted", "YOUTUBE_TAGS": "",
        "YOUTUBE_CATEGORY_ID": "27", "YOUTUBE_MADE_FOR_KIDS": "false",
        "TIKTOK_PRIVACY_LEVEL": "SELF_ONLY", "TIKTOK_PUBLISH_ENVIRONMENT": "production",
    }.items():
        monkeypatch.setenv(name, value)
    original = socket.socket.connect

    def connect(sock, address):
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "localhost", "::1"):
            forbidden()
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(webbrowser, "open", forbidden)


def read_manifest(directory):
    return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))


def write_record(directory, platform, state, *, identity=None, post_id=None, error=None):
    path, manifest = pub.load_manifest(PROJECT_ID, directory)
    if platform == "youtube":
        record = PublicationRecord(
            project_id=manifest.project_id, state=state, youtube_video_id=identity,
            title="Test Video Title", description="Full narration script.\n\nFollow for more",
            youtube_metadata=YouTubeMetadata(title="Test Video Title"), error=error,
        )
        pub.persist_record(path, manifest, record)
    else:
        record = TikTokPublicationRecord(
            project_id=manifest.project_id, state=state, tiktok_publish_id=identity,
            tiktok_post_id=post_id, tiktok_metadata=TikTokMetadata(title="Test Video Title"),
            error=error,
        )
        tt.persist_tiktok_record(path, manifest, record)
    return record


class FakeYouTube:
    def __init__(self, directory, connected=True):
        self.directory = directory
        self.connected = connected
        self.calls = []
        self.api_status = "processed"
        self.upload_error = None
        self.missing = False

    def validate_stored_credentials(self):
        return object() if self.connected else None

    def upload(self, **kwargs):
        self.calls.append("upload")
        assert read_manifest(self.directory)["publication"]["state"] == "UPLOAD_SUBMITTED"
        if self.upload_error:
            raise self.upload_error
        return "fixture-video-10"

    def get_video(self, video_id):
        self.calls.append("lookup")
        if self.missing:
            return None
        record = read_manifest(self.directory)["publication"]
        meta = record.get("youtube_metadata") or {}
        return {
            "id": video_id,
            "snippet": {
                "title": meta.get("title") or record.get("title"),
                "description": meta.get("description") or record.get("description"),
                "tags": meta.get("tags", []), "categoryId": meta.get("category_id", "27"),
                "defaultLanguage": meta.get("default_language"), "channelTitle": "Offline channel",
            },
            "status": {
                "uploadStatus": self.api_status,
                "privacyStatus": meta.get("privacy_status") or record.get("privacy_status"),
                "madeForKids": meta.get("made_for_kids", False),
            },
        }


class FakeTikTok:
    def __init__(self, directory, connected=True):
        self.directory = directory
        self.connected = connected
        self.calls = []
        self.status = {"status": "PUBLISH_COMPLETE", "post_id": "fixture-post-10"}

    def validate_stored_token(self):
        return {"access_token": "offline-token"} if self.connected else None

    def get_creator_info(self):
        self.calls.append("creator")
        return {"privacy_level_options": ["SELF_ONLY"]}

    def init_direct_post(self, **kwargs):
        self.calls.append("init")
        assert read_manifest(self.directory)["tiktok_publication"]["state"] == "UPLOAD_SUBMITTED"
        return {"publish_id": "fixture-publish-10", "upload_url": "https://offline.invalid/upload"}

    def upload_video_chunks(self, upload_url, video_path):
        self.calls.append("bytes")
        assert read_manifest(self.directory)["tiktok_publication"]["tiktok_publish_id"] == "fixture-publish-10"

    def fetch_status(self, publish_id):
        self.calls.append("status")
        return self.status


@pytest.fixture()
def runtime(monkeypatch, tmp_path):
    projects, productions = tmp_path / "projects", tmp_path / "productions"
    directory = build_project(projects)
    monkeypatch.setattr(web, "PROJECTS_DIR", projects)
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", productions)
    monkeypatch.setattr(web, "WEBAPP_DIR", tmp_path / "webapp")
    monkeypatch.setattr(pub, "ORCHESTRATOR_OUTPUT_DIR", projects)
    yt, tk = FakeYouTube(directory), FakeTikTok(directory)
    monkeypatch.setattr(web, "_new_youtube_publisher", lambda: yt)
    monkeypatch.setattr(web, "_new_publisher", lambda: tk)
    return SimpleNamespace(directory=directory, projects=projects, productions=productions, yt=yt, tk=tk)


@pytest.fixture()
def http(runtime):
    server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.AVMHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request(base, route, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        base + route, data=data, method="POST" if body is not None else "GET",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def project_route(platform, status=False):
    return f"/api/projects/project/publish/{platform}" + ("/status" if status else "")


def production(derived=False):
    unit = ProductionUnit(unit_id=UID, ordinal=1, unit_type="standalone_video", title="Unit")
    result = Production(
        production_id=PID, title="Track 10", units=[unit], created_at=STAMP,
        command=ProductionCommand(text="Track 10", workload_shape="single_video", issued_at=STAMP),
    )
    if derived:
        asset = SourceAsset(
            asset_id="src-001", original_filename="source.mp4", stored_path="source/src-001.mp4",
            sha256="a" * 64, duration=30, width=1080, height=1920, file_size=100,
            mime="video/mp4", ingested_at=STAMP,
        )
        original = unit.model_copy(update={"unit_id": "unit-000"})
        result = result.model_copy(update={"units": [original], "source_assets": [asset]})
        derived_unit = derive_production_unit_from_segment(
            result, asset, SourceSegment(segment_id="seg-001", asset_id="src-001", start_seconds=1, end_seconds=11),
            unit_id=UID,
        )
        result = result.model_copy(update={"units": [original, derived_unit]})
    return result


@pytest.fixture()
def accepted_project(runtime, monkeypatch):
    """Real plan/hash binding and acceptance; only FFprobe/media facts are faked."""
    prod = production(derived=True)
    save_new_production(prod, root=runtime.productions)
    plan = ProductionPlan(
        project_id=PROJECT_ID, title="Accepted source-derived output", hook="A real hook",
        script="This is the offline fixture narration.", estimated_duration=10,
        scenes=[Scene(
            id="scene_01", sequence=1, narration="This is the offline fixture narration.",
            visual_prompt="A source excerpt", estimated_duration=10, purpose="hook",
        )],
        metadata=ProductionMetadata(
            platform="youtube_shorts", language="en", niche="education",
            target_audience="general audience", content_goal="education", style="documentary",
        ),
    )
    plan_dir = unit_plan_dir(PID, UID, root=runtime.productions)
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_file = plan_dir / "production_plan.json"
    plan_file.write_text(plan.model_dump_json(), encoding="utf-8")
    content_hash = compute_content_hash(plan_file)
    (plan_dir / "plan_meta.json").write_text(json.dumps({
        "production_id": PID, "unit_id": UID, "production_plan_version": prod.plan_version,
        "unit_plan_version": 1, "plan_identity": plan.project_id, "content_hash": content_hash,
    }), encoding="utf-8")
    path, manifest = pub.load_manifest(PROJECT_ID, runtime.directory)
    # The plan belongs to the production's unit; project-owned content is the
    # accepted execution copy, as in the existing executor path.
    Path(manifest.content_plan_path).write_text(plan.model_dump_json(), encoding="utf-8")
    manifest.production_binding = ProductionBinding(
        production_id=PID, unit_id=UID, production_plan_version=prod.plan_version,
        unit_plan_identity=plan.project_id, unit_plan_version=1,
        unit_plan_content_hash=content_hash, project_id=runtime.directory.name, bound_at=STAMP,
    )
    manifest.assembly_config.update({"audio_policy": "none", "enable_captions": False})
    scene_file = runtime.directory / "scene_01.mp4"
    scene_file.write_bytes(b"offline-scene-media")
    manifest.scene_video_paths = [str(scene_file)]
    pub.save_manifest(path, manifest)
    monkeypatch.setattr(oracle, "validate_video_file", lambda path, ratio: {"valid": Path(path).is_file()})
    monkeypatch.setattr(oracle, "check_ffprobe_available", lambda: True)
    monkeypatch.setattr(oracle, "probe_media", lambda path: SimpleNamespace(
        has_video=True, has_audio=False, duration=10.0, width=1080, height=1920,
        video_codec="h264", audio_codec=None, file_size=Path(path).stat().st_size,
    ))
    executor = ProductionExecutor(PID, productions_root=runtime.productions, projects_root=runtime.projects)
    monkeypatch.setattr(web, "_production_executor", lambda pid: executor)
    runtime.executor = executor
    runtime.production = prod
    return runtime


@pytest.mark.parametrize("state", [
    PublicationState.UPLOAD_SUBMITTED, PublicationState.IDENTITY_PERSISTED,
    PublicationState.PROCESSING, PublicationState.VERIFIED,
    PublicationState.VERIFIED_SANDBOX_PRIVATE, PublicationState.UNKNOWN_RECONCILIATION_REQUIRED,
    PublicationState.FAILED,
])
def test_prepare_never_erases_post_submission_state(tmp_path, gate, state):
    directory = build_project(tmp_path)
    pub.prepare_publish(PROJECT_ID, directory)
    path, manifest = pub.load_manifest(PROJECT_ID, directory)
    manifest.publication.state = state
    manifest.publication.error = "Preserve the forensic diagnostic"
    pub.persist_record(path, manifest, manifest.publication)
    before = path.read_bytes()
    with pytest.raises(DuplicateUploadBlockedError):
        pub.prepare_publish(PROJECT_ID, directory)
    assert path.read_bytes() == before


@pytest.mark.parametrize("state", [PublicationState.NOT_STARTED, PublicationState.PREPARED])
def test_prepare_never_overwrites_existing_video_identity(tmp_path, gate, state):
    directory = build_project(tmp_path)
    write_record(directory, "youtube", state, identity=VIDEO_ID)
    before = (directory / "manifest.json").read_bytes()
    with pytest.raises(DuplicateUploadBlockedError):
        pub.prepare_publish(PROJECT_ID, directory)
    assert (directory / "manifest.json").read_bytes() == before


def test_failed_preparation_requires_explicit_operator_reconciliation(tmp_path, gate):
    directory = build_project(tmp_path)
    write_record(directory, "youtube", PublicationState.FAILED, error="Rejected")
    with pytest.raises(DuplicateUploadBlockedError):
        pub.prepare_publish(PROJECT_ID, directory)
    # Only an explicit, already-reconciled operator correction to a permitted
    # pre-submission state permits preparation. There is no force/retry flag.
    path, manifest = pub.load_manifest(PROJECT_ID, directory)
    manifest.publication.state = PublicationState.NOT_STARTED
    pub.persist_record(path, manifest, manifest.publication)
    assert pub.prepare_publish(PROJECT_ID, directory).state == PublicationState.PREPARED


@pytest.mark.parametrize("status", ["failed", "rejected"])
@pytest.mark.parametrize("pending", [False, True])
def test_youtube_live_failed_verdict_matches_persisted_state(runtime, status, pending):
    record = write_record(runtime.directory, "youtube", PublicationState.PROCESSING, identity=VIDEO_ID)
    if pending:
        path, manifest = pub.load_manifest(PROJECT_ID, runtime.directory)
        record.pending_metadata = YouTubeMetadata(title="Pending edit")
        pub.persist_record(path, manifest, record)
    runtime.yt.api_status = status
    report = pub.reconcile_publication(PROJECT_ID, runtime.yt, runtime.directory)
    assert report.verdict == PublicationVerdict.PUBLICATION_FAILED
    assert report.state == "FAILED"
    persisted = read_manifest(runtime.directory)["publication"]
    assert persisted["state"] == "FAILED"
    assert persisted["error"]
    if pending:
        assert persisted["pending_metadata"]["title"] == "Pending edit"


def test_tiktok_live_failed_verdict_matches_persisted_state(runtime):
    write_record(runtime.directory, "tiktok", PublicationState.PROCESSING, identity="fixture-init")
    runtime.tk.status = {"status": "FAILED"}
    report = tt.reconcile_tiktok_publication(PROJECT_ID, runtime.tk, runtime.directory)
    assert report.verdict == PublicationVerdict.PUBLICATION_FAILED
    assert report.state == "FAILED"
    assert read_manifest(runtime.directory)["tiktok_publication"]["state"] == "FAILED"


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_status_reloads_all_fields_after_reconciliation(runtime, http, platform):
    write_record(runtime.directory, platform, PublicationState.PROCESSING, identity="fixture-existing", error="Old diagnostic")
    status, body = request(http, project_route(platform, status=True))
    assert status == 200
    assert body["state"] == "VERIFIED"
    assert body["verdict"] == "PUBLICATION_VERIFIED"
    assert body["error"] is None
    assert body["verified_at"]
    assert body["verification_evidence"]["checked_at"]
    assert body["can_publish"] is False
    if platform == "tiktok":
        assert body["post_id"] == "fixture-post-10"


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_disconnected_status_is_passive_and_never_reconciles(runtime, http, monkeypatch, platform):
    write_record(runtime.directory, platform, PublicationState.PROCESSING, identity="fixture-existing")
    publisher = runtime.yt if platform == "youtube" else runtime.tk
    publisher.connected = False
    monkeypatch.setattr(web, "_youtube_reconcile" if platform == "youtube" else "_reconcile", forbidden)
    before = (runtime.directory / "manifest.json").read_bytes()
    status, body = request(http, project_route(platform, status=True))
    assert status == 200
    assert body["connected"] is False and body["can_publish"] is False
    assert body["state"] == "PROCESSING"
    assert (runtime.directory / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
@pytest.mark.parametrize("state", [
    None, "NOT_STARTED", "PREPARED", "FAILED", "UNKNOWN_RECONCILIATION_REQUIRED",
    "UPLOAD_SUBMITTED", "IDENTITY_PERSISTED", "PROCESSING", "VERIFIED", "VERIFIED_SANDBOX_PRIVATE",
])
def test_can_publish_uses_canonical_completion_and_pre_submission_policy(accepted_project, http, platform, state):
    if state is not None:
        write_record(accepted_project.directory, platform, PublicationState(state))
    status, body = request(http, project_route(platform, status=True))
    assert status == 200
    assert body["can_publish"] is (state in (None, "NOT_STARTED", "PREPARED"))


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_displayed_completion_is_not_canonical_publish_eligibility(runtime, http, platform):
    path, manifest = pub.load_manifest(PROJECT_ID, runtime.directory)
    manifest.completed_at = STAMP
    pub.save_manifest(path, manifest)
    assert web.project_detail("project")["stage_status"] == "complete"
    status, body = request(http, project_route(platform, status=True))
    assert status == 200 and body["can_publish"] is False


def test_youtube_http_happy_path_reuses_prepare_submit_verify_reconcile(accepted_project, http, monkeypatch):
    calls = []
    for name in ("_youtube_prepare", "_youtube_submit", "_youtube_verify", "_youtube_reconcile"):
        original = getattr(web, name)

        def wrapper(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(web, name, wrapper)
    status, body = request(http, project_route("youtube"), {"privacy_status": "private"})
    assert status == 200 and body["state"] == "VERIFIED"
    assert body["verdict"] == "PUBLICATION_VERIFIED"
    assert body["video_id"] == "fixture-video-10" and body["post_id"] == body["video_id"]
    assert body["privacy_status"] == "private"
    assert calls == ["_youtube_reconcile", "_youtube_prepare", "_youtube_submit", "_youtube_verify", "_youtube_reconcile"]
    assert accepted_project.yt.calls.count("upload") == 1
    assert accepted_project.yt.calls.count("lookup") == 2
    # Repeat is a read of the durable success, not another upload or preparation.
    status, body = request(http, project_route("youtube"), {})
    assert status == 200 and body["published_already"] is True
    assert accepted_project.yt.calls.count("upload") == 1


@pytest.mark.parametrize("state, identity, error", [
    ("UNKNOWN_RECONCILIATION_REQUIRED", None, "UNKNOWN_RECONCILIATION_REQUIRED"),
    ("UPLOAD_SUBMITTED", None, "UNKNOWN_RECONCILIATION_REQUIRED"),
    ("FAILED", None, "PREVIOUS_FAILURE"),
    ("PROCESSING", "fixture-existing", "EXISTING_PUBLICATION_ID"),
    ("IDENTITY_PERSISTED", "fixture-existing", "EXISTING_PUBLICATION_ID"),
    ("PREPARED", "fixture-existing", "EXISTING_PUBLICATION_ID"),
])
def test_youtube_post_refuses_existing_or_ambiguous_submission(runtime, http, monkeypatch, state, identity, error):
    write_record(runtime.directory, "youtube", PublicationState(state), identity=identity)
    monkeypatch.setattr(web, "_youtube_prepare", forbidden)
    monkeypatch.setattr(web, "_youtube_submit", forbidden)
    status, body = request(http, project_route("youtube"), {})
    assert status == 409 and body["error"] == error
    assert runtime.yt.calls == []


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_publish_disconnected_refuses_before_preparation_or_oauth(runtime, http, monkeypatch, platform):
    publisher = runtime.yt if platform == "youtube" else runtime.tk
    publisher.connected = False
    monkeypatch.setattr(web, "_youtube_prepare" if platform == "youtube" else "_prepare", forbidden)
    monkeypatch.setattr(publisher, "authenticate", forbidden, raising=False)
    status, body = request(http, project_route(platform), {})
    assert status == 409 and body["error"] == f"{platform.upper()}_NOT_CONNECTED"
    assert read_manifest(runtime.directory)["publication"] is None
    assert read_manifest(runtime.directory)["tiktok_publication"] is None


def test_youtube_post_rejects_invalid_metadata_without_upload(accepted_project, http):
    status, body = request(http, project_route("youtube"), {"privacy_status": "invented"})
    assert status == 422 and body["error"] == "METADATA_INVALID"
    assert accepted_project.yt.calls == []
    assert read_manifest(accepted_project.directory)["publication"] is None


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_publish_and_status_unknown_project_are_404(runtime, http, platform):
    for suffix, body in (("", {}), ("/status", None)):
        status, payload = request(http, f"/api/projects/missing/publish/{platform}{suffix}", body)
        assert status == 404
    assert runtime.yt.calls == [] and runtime.tk.calls == []


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_status_is_get_only(runtime, http, platform):
    status, body = request(http, project_route(platform, status=True), {})
    assert status == 405 and body["error"] == "METHOD_NOT_ALLOWED"


def test_youtube_single_flight_refuses_concurrent_commands(runtime, http):
    assert web._publish_lock.acquire(blocking=False)
    try:
        status, body = request(http, project_route("youtube"), {})
        assert status == 409 and body["error"] == "PUBLISH_ALREADY_RUNNING"
    finally:
        web._publish_lock.release()
    assert runtime.yt.calls == []


def test_status_never_reconciles_during_an_active_publish(runtime, http):
    write_record(runtime.directory, "youtube", PublicationState.PROCESSING, identity="existing")
    assert web._publish_lock.acquire(blocking=False)
    try:
        status, body = request(http, project_route("youtube", status=True))
    finally:
        web._publish_lock.release()
    assert status == 200 and body["state"] == "PROCESSING"
    assert runtime.yt.calls == []
    assert "stored evidence" in body["note"]


def test_youtube_publisher_noninteractive_expiry_never_opens_oauth(monkeypatch):
    publisher = YouTubePublisher(allow_interactive=False)
    monkeypatch.setattr(publisher, "validate_stored_credentials", lambda: None)
    with pytest.raises(PermissionError):
        publisher.authenticate()


def test_tiktok_publisher_noninteractive_expiry_never_opens_oauth(monkeypatch):
    publisher = TikTokPublisher(client_key="offline-key", client_secret="offline-secret", allow_interactive=False)
    monkeypatch.setattr(publisher, "validate_stored_token", lambda: None)
    monkeypatch.setattr(publisher, "_run_authorization_flow", forbidden)
    with pytest.raises(TikTokAuthError):
        publisher.authenticate()


def test_http_factories_disable_interactive_but_cli_defaults_are_preserved(monkeypatch):
    assert web._new_youtube_publisher().allow_interactive is False
    assert web._new_publisher().allow_interactive is False
    assert YouTubePublisher.from_env().allow_interactive is True
    assert TikTokPublisher.from_env().allow_interactive is True


def unit_route():
    return f"/api/productions/{PID}/units/{UID}/publish"


@pytest.mark.parametrize("platforms", [["youtube"], ["tiktok", "youtube"], ["youtube", "tiktok"]])
def test_unit_platform_parity_preserves_both_records_and_real_binding(accepted_project, http, platforms):
    initial = read_manifest(accepted_project.directory)
    report = accepted_project.executor.derive_progress()
    assert next(item for item in report.units if item.unit_id == UID).state == UnitExecutionState.COMPLETED
    status, body = request(http, unit_route(), {"confirm": True, "platforms": platforms})
    assert status == 200 and body["status"] == "published" and body["outcome"] == "verified"
    assert body["project_id"] == accepted_project.directory.name
    assert set(body["results"]) == set(platforms)
    persisted = read_manifest(accepted_project.directory)
    assert persisted["production_binding"] == initial["production_binding"]
    assert persisted["stages"] == initial["stages"]
    assert persisted["final_video_path"] == initial["final_video_path"]
    assert persisted["publication"]["state"] == "VERIFIED"
    if "tiktok" in platforms:
        assert persisted["tiktok_publication"]["state"] == "VERIFIED"
        assert persisted["tiktok_publication"]["tiktok_publish_id"] == "fixture-publish-10"
    assert accepted_project.production.units[-1].source_lineage is not None
    code, detail = request(http, f"/api/productions/{PID}/units/{UID}")
    assert code == 200
    records = {row["platform"]: row for row in detail["bound_project"]["publications"]}
    assert records["youtube"]["video_id"] == "fixture-video-10"
    assert records["youtube"]["verdict"] == "PUBLICATION_VERIFIED"
    assert records["youtube"]["verified_at"]
    if "tiktok" in platforms:
        assert records["tiktok"]["publish_id"] == "fixture-publish-10"
        assert records["tiktok"]["post_id"] == "fixture-post-10"


@pytest.mark.parametrize("state, status_label, outcome", [
    ("PROCESSING", "processing", "processing"),
    ("VERIFIED_SANDBOX_PRIVATE", "verified_sandbox_private", "verified_sandbox_private"),
    ("VERIFIED", "published", "verified"),
    ("UNKNOWN_RECONCILIATION_REQUIRED", "unknown_reconciliation_required", "unknown_reconciliation_required"),
    ("FAILED", "failed", "failed"),
])
def test_unit_status_comes_from_durable_state_not_http_200(accepted_project, http, monkeypatch, state, status_label, outcome):
    monkeypatch.setattr(web, "tiktok_publish_now", lambda project_id, privacy_level=None: (200, {"state": state}))
    status, body = request(http, unit_route(), {"confirm": True, "platforms": ["tiktok"]})
    assert status == 200 and body["status"] == status_label and body["outcome"] == outcome


def test_multiplatform_partial_failure_keeps_success_evidence(accepted_project, http):
    accepted_project.yt.connected = False
    status, body = request(http, unit_route(), {"confirm": True, "platforms": ["tiktok", "youtube"]})
    assert status == 409 and body["outcome"] == "failed"
    assert body["results"]["tiktok"]["outcome"] == "verified"
    persisted = read_manifest(accepted_project.directory)
    assert persisted["tiktok_publication"]["state"] == "VERIFIED"
    assert persisted["publication"] is None


def test_summary_sidecar_never_retargets_a_completed_project(accepted_project, http):
    from app.production.summary_video import summary_video_output_path

    summary = summary_video_output_path(PID, UID, root=accepted_project.productions)
    summary.write_bytes(b"a-different-summary-file")
    before = read_manifest(accepted_project.directory)["final_video_path"]
    status, body = request(http, unit_route(), {"confirm": True, "platforms": ["youtube"]})
    assert status == 200
    persisted = read_manifest(accepted_project.directory)
    assert persisted["publication"]["final_video_path"] == before
    assert persisted["final_video_path"] == before


def test_summary_only_unit_is_refused_without_binding_or_state_fabrication(runtime, http, monkeypatch):
    from app.production.summary_video import summary_video_output_path

    save_new_production(production(), root=runtime.productions)
    summary = summary_video_output_path(PID, UID, root=runtime.productions)
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_bytes(b"standalone-summary")
    executor = ProductionExecutor(PID, productions_root=runtime.productions, projects_root=runtime.projects)
    monkeypatch.setattr(web, "_production_executor", lambda pid: executor)
    before = (runtime.directory / "manifest.json").read_bytes()
    status, body = request(http, unit_route(), {"confirm": True, "platforms": ["youtube"]})
    assert status == 422 and body["error"] == "SUMMARY_HANDOFF_REQUIRED"
    assert body["inspection_url"].endswith("/summary-video")
    assert executor.derive_progress().units[0].state == UnitExecutionState.NOT_STARTED
    assert (runtime.directory / "manifest.json").read_bytes() == before
    assert runtime.yt.calls == [] and runtime.tk.calls == []


def test_uncompleted_bound_unit_is_cleanly_refused(accepted_project, http):
    Path(read_manifest(accepted_project.directory)["final_video_path"]).unlink()
    before = (accepted_project.directory / "manifest.json").read_bytes()
    status, body = request(http, unit_route(), {"confirm": True, "platforms": ["youtube"]})
    assert status == 400 and body["error"] == "UNIT_NOT_COMPLETED"
    assert body["state"] == "INCOMPLETE"
    assert (accepted_project.directory / "manifest.json").read_bytes() == before
    assert accepted_project.yt.calls == []


@pytest.mark.parametrize("body, error", [
    ({}, "CONFIRMATION_REQUIRED"),
    ({"confirm": "true"}, "CONFIRMATION_REQUIRED"),
    ({"confirm": True, "platforms": "youtube"}, "INVALID_PUBLISH_PLATFORMS"),
    ({"confirm": True, "platforms": []}, "INVALID_PUBLISH_PLATFORMS"),
    ({"confirm": True, "platforms": ["youtube", "youtube"]}, "INVALID_PUBLISH_PLATFORMS"),
    ({"confirm": True, "platforms": ["instagram"]}, "PUBLISH_PLATFORM_NOT_SUPPORTED"),
])
def test_unit_command_validation_precedes_any_submission(accepted_project, http, body, error):
    status, payload = request(http, unit_route(), body)
    assert status == 400 and payload["error"] == error
    assert accepted_project.yt.calls == [] and accepted_project.tk.calls == []


@pytest.mark.parametrize("first_platform", ["youtube", "tiktok"])
def test_stale_manifest_writer_preserves_other_platform_and_execution(runtime, first_platform):
    path, stale = pub.load_manifest(PROJECT_ID, runtime.directory)
    first = write_record(runtime.directory, first_platform, PublicationState.PROCESSING, identity="first-identity")
    _, latest = pub.load_manifest(PROJECT_ID, runtime.directory)
    latest.completed_at = STAMP
    pub.save_manifest(path, latest)
    if first_platform == "youtube":
        other = TikTokPublicationRecord(project_id=PROJECT_ID, state=PublicationState.PREPARED)
        tt.persist_tiktok_record(path, stale, other)
    else:
        other = PublicationRecord(project_id=PROJECT_ID, state=PublicationState.PREPARED)
        pub.persist_record(path, stale, other)
    _, reloaded = pub.load_manifest(PROJECT_ID, runtime.directory)
    persisted_first = reloaded.publication if first_platform == "youtube" else reloaded.tiktok_publication
    assert persisted_first.model_dump() == first.model_dump()
    assert reloaded.publication is not None and reloaded.tiktok_publication is not None
    assert reloaded.completed_at == STAMP
    assert stale.publication is not None and stale.tiktok_publication is not None
    assert not list(runtime.directory.glob(".manifest.json.*.tmp"))


def test_concurrent_cross_platform_persistence_merges_without_clobber(runtime):
    path, yt_snapshot = pub.load_manifest(PROJECT_ID, runtime.directory)
    _, tk_snapshot = pub.load_manifest(PROJECT_ID, runtime.directory)
    barrier = threading.Barrier(2)
    errors = []

    def save(platform):
        try:
            barrier.wait(timeout=3)
            if platform == "youtube":
                pub.persist_record(path, yt_snapshot, PublicationRecord(project_id=PROJECT_ID, youtube_video_id="yt-identity"))
            else:
                tt.persist_tiktok_record(path, tk_snapshot, TikTokPublicationRecord(project_id=PROJECT_ID, tiktok_publish_id="tk-identity"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(platform,)) for platform in ("youtube", "tiktok")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert not errors and all(not thread.is_alive() for thread in threads)
    persisted = read_manifest(runtime.directory)
    assert persisted["publication"]["youtube_video_id"] == "yt-identity"
    assert persisted["tiktok_publication"]["tiktok_publish_id"] == "tk-identity"


def test_atomic_publication_failure_leaves_original_manifest_and_cleans_temp(runtime, monkeypatch):
    path, manifest = pub.load_manifest(PROJECT_ID, runtime.directory)
    before = path.read_bytes()

    def interrupted_replace(*args):
        raise OSError("Offline atomic replacement failure")

    monkeypatch.setattr(pub.os, "replace", interrupted_replace)
    with pytest.raises(OSError):
        pub.persist_record(path, manifest, PublicationRecord(project_id=PROJECT_ID))
    assert path.read_bytes() == before
    assert not list(runtime.directory.glob(".manifest.json.*.tmp"))


def test_wrong_project_publication_is_refused_without_manifest_change(runtime):
    path, manifest = pub.load_manifest(PROJECT_ID, runtime.directory)
    before = path.read_bytes()
    with pytest.raises(pub.PublishingError):
        pub.persist_record(path, manifest, PublicationRecord(project_id="unrelated-project"))
    assert path.read_bytes() == before


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_durable_evidence_allowlist_and_string_diagnostics_are_sanitized(runtime, platform, monkeypatch):
    synthetic_secret = "offline-diagnostic-value-123456"
    monkeypatch.setenv("AUDIT_PROVIDER_API_KEY", synthetic_secret)
    error = f"request failed {synthetic_secret}; access_token=offline-access-987; Bearer offline-bearer-654"
    record = write_record(runtime.directory, platform, PublicationState.FAILED, error=error)
    record.verification_evidence = {
        "checked_at": STAMP, "reason": error, "raw_response": {"token": "never-expose-this"},
        "upload_url": "https://offline.invalid/signed", "client_secret": "never-expose-this",
    }
    path, manifest = pub.load_manifest(PROJECT_ID, runtime.directory)
    if platform == "youtube":
        pub.persist_record(path, manifest, record)
    else:
        tt.persist_tiktok_record(path, manifest, record)
    payload = web._project_publications_summary(read_manifest(runtime.directory))[0]
    text = json.dumps(payload)
    for value in (synthetic_secret, "offline-access-987", "offline-bearer-654", "never-expose-this", "https://offline.invalid"):
        assert value not in text
    assert payload["state"] == "FAILED" and payload["verdict"] == "PUBLICATION_FAILED"
    assert payload["verification_evidence"]["checked_at"] == STAMP
    assert "raw_response" not in payload["verification_evidence"]


def test_youtube_http_unknown_upload_error_returns_durable_safe_evidence(accepted_project, http, monkeypatch):
    synthetic_secret = "offline-upload-secret-123456"
    monkeypatch.setenv("AUDIT_PROVIDER_API_KEY", synthetic_secret)
    accepted_project.yt.upload_error = RuntimeError(f"Lost response: {synthetic_secret}; token=offline-token-111")
    status, body = request(http, project_route("youtube"), {})
    assert status == 502 and body["error"] == "SUBMISSION_FAILED"
    assert body["state"] == "UNKNOWN_RECONCILIATION_REQUIRED"
    assert body["verdict"] == "PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED"
    assert synthetic_secret not in json.dumps(body) and "offline-token-111" not in json.dumps(body)
    assert accepted_project.yt.calls.count("upload") == 1
    status, body = request(http, project_route("youtube"), {})
    assert status == 409 and body["error"] == "UNKNOWN_RECONCILIATION_REQUIRED"
    assert accepted_project.yt.calls.count("upload") == 1


def test_youtube_status_lookup_error_is_sanitized_and_preserves_identity(runtime, http, monkeypatch):
    write_record(runtime.directory, "youtube", PublicationState.PROCESSING, identity="existing-video")

    def lookup(video_id):
        raise RuntimeError("Unavailable: client_secret=offline-lookup-secret")

    monkeypatch.setattr(runtime.yt, "get_video", lookup)
    status, body = request(http, project_route("youtube", status=True))
    assert status == 200 and body["video_id"] == "existing-video"
    assert body["state"] == "PROCESSING" and body["can_publish"] is False
    assert "offline-lookup-secret" not in json.dumps(body)


def test_cli_resume_inspects_identity_without_repreparing(runtime, monkeypatch):
    write_record(runtime.directory, "youtube", PublicationState.VERIFIED, identity=VIDEO_ID)
    monkeypatch.setattr(pub, "prepare_publish", forbidden)
    monkeypatch.setattr(YouTubePublisher, "from_env", classmethod(lambda cls: runtime.yt))
    monkeypatch.setattr(runtime.yt, "get_channel", lambda: {"snippet": {"title": "Offline channel"}}, raising=False)
    assert pub.run_publish_flow("project") == 0
    assert runtime.yt.calls == ["lookup"]


def test_cli_ambiguous_state_is_blocked_before_factory_or_preparation(runtime, monkeypatch):
    write_record(runtime.directory, "youtube", PublicationState.UPLOAD_SUBMITTED)
    monkeypatch.setattr(pub, "prepare_publish", forbidden)
    monkeypatch.setattr(YouTubePublisher, "from_env", forbidden)
    assert pub.run_publish_flow("project") == 2
    assert read_manifest(runtime.directory)["publication"]["state"] == "UNKNOWN_RECONCILIATION_REQUIRED"


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_unknown_state_with_identity_is_not_downgraded_offline(runtime, http, platform):
    write_record(runtime.directory, platform, PublicationState.UNKNOWN_RECONCILIATION_REQUIRED, identity="existing-id")
    if platform == "youtube":
        report = pub.reconcile_publication(PROJECT_ID, project_dir=runtime.directory)
    else:
        report = tt.reconcile_tiktok_publication(PROJECT_ID, project_dir=runtime.directory)
    assert report.verdict == PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED
    status, body = request(http, project_route(platform), {})
    assert status == 409 and body["error"] == "UNKNOWN_RECONCILIATION_REQUIRED"
    assert runtime.yt.calls == [] and runtime.tk.calls == []


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_status_reload_failure_releases_single_flight_lock(runtime, monkeypatch, platform):
    record = write_record(runtime.directory, platform, PublicationState.PROCESSING, identity="existing-id")
    calls = []

    def current(project_id):
        calls.append(project_id)
        if len(calls) > 1:
            raise RuntimeError("Fixture reload interrupted")
        return record

    monkeypatch.setattr(web, "_current_youtube_record" if platform == "youtube" else "_current_record", current)
    handler = web.youtube_status_payload if platform == "youtube" else web.tiktok_status_payload
    with pytest.raises(RuntimeError, match="reload interrupted"):
        handler("project")
    assert web._publish_lock.acquire(blocking=False)
    web._publish_lock.release()


def test_unit_transport_failure_never_claims_verified_publication(accepted_project, http, monkeypatch):
    monkeypatch.setattr(web, "youtube_publish_now", lambda project_id, privacy_status=None: (
        409, {"state": "VERIFIED", "verdict": "PUBLICATION_VERIFIED", "error": "YOUTUBE_NOT_CONNECTED"},
    ))
    status, body = request(http, unit_route(), {"confirm": True, "platforms": ["youtube"]})
    assert status == 409 and body["status"] == "failed"
    assert body["results"]["youtube"]["result"]["state"] == "VERIFIED"


@pytest.mark.parametrize("api_status", ["processing", "failed"])
def test_youtube_post_returns_real_processing_or_failure_not_fabricated_success(accepted_project, http, api_status, monkeypatch):
    monkeypatch.setattr(pub.time, "sleep", lambda seconds: None)
    accepted_project.yt.api_status = api_status
    status, body = request(http, project_route("youtube"), {})
    assert status == 200
    assert body["state"] == ("PROCESSING" if api_status == "processing" else "FAILED")
    assert body["verdict"] == ("PUBLICATION_PROCESSING" if api_status == "processing" else "PUBLICATION_FAILED")
    assert accepted_project.yt.calls.count("upload") == 1


def test_tiktok_expiry_during_creator_check_fails_without_submission(accepted_project, http, monkeypatch):
    def expired():
        raise TikTokAuthError("Stored credentials expired")

    monkeypatch.setattr(accepted_project.tk, "get_creator_info", expired)
    status, body = request(http, project_route("tiktok"), {})
    assert status == 502 and body["error"] == "AUTH_FAILED"
    assert accepted_project.tk.calls == []
    assert read_manifest(accepted_project.directory)["tiktok_publication"]["state"] == "PREPARED"


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_legacy_project_alias_resolves_every_publishing_delegate_to_same_directory(accepted_project, http, platform):
    status, body = request(http, f"/api/projects/{PROJECT_ID}/publish/{platform}", {})
    assert status == 200 and body["state"] == "VERIFIED"
    persisted = read_manifest(accepted_project.directory)
    assert persisted["publication" if platform == "youtube" else "tiktok_publication"]["state"] == "VERIFIED"


def test_processing_diagnostic_never_becomes_unit_failure(accepted_project, http, monkeypatch):
    monkeypatch.setattr(web, "tiktok_publish_now", lambda project_id, privacy_level=None: (
        200, {"state": "PROCESSING", "verdict": "PUBLICATION_PROCESSING", "error": "No public post identity yet"},
    ))
    status, body = request(http, unit_route(), {"confirm": True, "platforms": ["tiktok"]})
    assert status == 200 and body["status"] == "processing"
    assert body["outcome"] == "processing"


def test_unquoted_authorization_diagnostic_never_leaks_bearer_value():
    text = web._sanitize_diagnostic_text("HTTP denied: Authorization: Bearer offline-header-token-1234")
    assert "offline-header-token-1234" not in text
    assert "secret-omitted" in text


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_status_disables_publish_eligibility_while_single_flight_is_busy(accepted_project, http, platform):
    assert web._publish_lock.acquire(blocking=False)
    try:
        status, body = request(http, project_route(platform, status=True))
    finally:
        web._publish_lock.release()
    assert status == 200 and body["connected"] is True and body["can_publish"] is False


@pytest.mark.parametrize("boundary", ["intent", "executor"])
def test_unit_early_errors_use_publishing_diagnostic_sanitizer(accepted_project, http, monkeypatch, boundary):
    def failure(*args):
        raise ValueError("Fixture failure: client_secret=offline-early-secret-9876")

    monkeypatch.setattr(web, "_production_or_none" if boundary == "intent" else "_derive_progress", failure)
    status, body = request(http, unit_route(), {"confirm": True, "platforms": ["youtube"]})
    assert status == 500
    assert "offline-early-secret-9876" not in json.dumps(body)
    assert accepted_project.yt.calls == []