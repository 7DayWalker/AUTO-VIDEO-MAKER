"""Hermetic Track 9 -> canonical Track 10 handoff, real binding/acceptance.

Local FFmpeg media exercises the real assembly and acceptance paths. Provider
fixtures and fake publishers replace every external boundary; no OAuth or
external networking is allowed. All persisted state belongs to tmp_path.
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

from app.assembly.video import create_test_video
from app.assembly.ffmpeg import run_ffmpeg
from app.orchestrator import reconciliation as oracle
from app.orchestrator.models import ProductionBinding
from app.orchestrator.production_execution import ProductionExecutor, UnitExecutionState
from app.production.models import Production, ProductionCommand, ProductionUnit
from app.production.storage import (
    compute_content_hash, load_narration_artifact, save_narration_artifact, save_new_production, unit_plan_dir,
)
from app.production.summary_video import summary_video_metadata_path, summary_video_output_path
from app.publishing import reconciliation as pub
from app.publishing import tiktok_reconciliation as tt
from app.publishing.models import PublicationRecord, PublicationState, PublishingEligibilityError, TikTokPublicationRecord
from app.web import server as web
from tests.test_track10_publishing_parity import FakeTikTok, FakeYouTube, read_manifest


PID = "prd_20261024_summary_handoff_001"
UID = "unit-001"
STAMP = "2026-10-24T09:00:00+00:00"
UNIT_ROUTE = f"/api/productions/{PID}/units/{UID}"


def request(base, route, body=None):
    req = urllib.request.Request(
        base + route, data=json.dumps(body).encode() if body is not None else None,
        method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    original = socket.socket.connect

    def connect(sock, address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "localhost", "::1"}:
            raise AssertionError("external networking forbidden in summary handoff tests")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: pytest.fail("OAuth/browser consent is forbidden"))
    for name, value in {
        "YOUTUBE_TOKEN_FILE": str(tmp_path / "missing-youtube.json"),
        "TIKTOK_TOKEN_FILE": str(tmp_path / "missing-tiktok.json"),
        "YOUTUBE_TAGS": "", "YOUTUBE_CATEGORY_ID": "27", "YOUTUBE_MADE_FOR_KIDS": "false",
        "YOUTUBE_PRIVACY_STATUS": "unlisted", "TIKTOK_PRIVACY_LEVEL": "SELF_ONLY",
        "TIKTOK_PUBLISH_ENVIRONMENT": "production",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.fixture()
def runtime(monkeypatch, tmp_path):
    projects, productions = tmp_path / "projects", tmp_path / "productions"
    projects.mkdir(); productions.mkdir()
    monkeypatch.setattr(web, "PROJECTS_DIR", projects)
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", productions)
    production = Production(
        production_id=PID, title="Grounded source summary", created_at=STAMP,
        command=ProductionCommand(text="Create a grounded recap", workload_shape="single_video", issued_at=STAMP),
        units=[ProductionUnit(unit_id=UID, ordinal=1, unit_type="standalone_video",
                              title="  A grounded source recap  ", content_intent={"hook": "An observed discovery", "niche": "education"})],
    )
    save_new_production(production, root=productions)
    executor = ProductionExecutor(PID, productions_root=productions, projects_root=projects)
    monkeypatch.setattr(web, "_production_executor", lambda pid: executor)
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web.AVMHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    value = SimpleNamespace(
        projects=projects, productions=productions, executor=executor,
        base=f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path=tmp_path,
    )
    yield value
    httpd.shutdown(); httpd.server_close(); thread.join(timeout=2)


def assemble(runtime, *, promote=False, real=False):
    source = runtime.tmp_path / "local-source.mp4"
    create_test_video(source, duration=12, width=320, height=180, fps=24)
    status, body = request(runtime.base, f"/api/productions/{PID}/source", {"source_path": str(source)})
    assert status == 201, body
    status, body = request(runtime.base, f"/api/productions/{PID}/source/src-001/transcript", {} if real else {"provider": "mock", "mock_mode": True})
    assert status in {200, 201}, body
    status, body = request(runtime.base, UNIT_ROUTE + "/narration", {"summary_mode": "standard", "mock_mode": not real, "target_duration_seconds": 6})
    assert status == 201, body
    status, body = request(runtime.base, UNIT_ROUTE + "/narration/synthesize", {"mock_mode": not real, "format": "mp3"})
    assert status == 200, body
    status, body = request(runtime.base, UNIT_ROUTE + "/summary-video", {"promote_to_publishable": promote})
    assert status == 201, body
    return body


@pytest.fixture()
def rendered(runtime):
    runtime.summary = assemble(runtime)
    return runtime


def promote(runtime):
    status, body = request(runtime.base, UNIT_ROUTE + "/summary-video/promote", {})
    assert status in {200, 201}, body
    return status, body, runtime.projects / body["project_id"]


@pytest.mark.parametrize("tiktok_environment", ["production", "sandbox"])
def test_e2e_assemble_promote_publish_both_platforms(runtime, monkeypatch, tiktok_environment):
    from tests.track9_narration_fakes import install_standard_provider, standard_evidence, standard_response

    _, transcript = standard_evidence(duration=12)
    transcriber = SimpleNamespace(transcribe=lambda path, duration: SimpleNamespace(
        segments=[segment.model_dump() for segment in transcript.segments],
        language="en", provider="fixture-stt", provider_version="offline",
    ))
    monkeypatch.setattr("app.transcription.transcribe.get_provider", lambda provider=None: transcriber)
    provider, _, _ = install_standard_provider(monkeypatch, data=standard_response(duration=12))

    class LocalVoice:
        name = "fixture-tts"

        async def generate(self, *, output_path, **kwargs):
            run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                        "-c:a", "libmp3lame", str(output_path)], "hermetic fixture narration")

    monkeypatch.setattr("app.production.narration._resolve_narration_provider", lambda name=None: LocalVoice())
    summary = assemble(runtime, real=True)
    assert len(provider.calls) == 1 and provider.json_calls == 1
    assert summary["rendered"] is True and summary["promoted"] is False
    status, promoted, directory = promote(runtime)
    assert status == 201 and promoted["summary_video"]["promotion_required"] is False
    manifest = read_manifest(directory)
    binding = manifest["production_binding"]
    assert binding["production_id"] == PID and binding["unit_id"] == UID
    assert binding["execution_dir"] == directory.name and binding["handoff_kind"] == "narrated_summary"
    plan_path = unit_plan_dir(PID, UID, root=runtime.productions) / "production_plan.json"
    assert binding["unit_plan_content_hash"] == compute_content_hash(plan_path)
    assert manifest["source_summary_handoff"]["file_fingerprint"] == compute_content_hash(Path(manifest["final_video_path"]))
    assert Path(manifest["final_video_path"]).is_relative_to(directory)
    assert manifest["current_stage"] == "complete"
    assert manifest["stages"]["voice"]["status"] == "skipped"
    assert manifest["stages"]["scenes"]["status"] == "skipped"
    assert manifest["scene_checkpoints"] == {} and manifest["voice_timing_path"] is None
    assert runtime.executor.derive_progress().units[0].state == UnitExecutionState.COMPLETED
    assert ProductionExecutor.derive_progress_many([PID], productions_root=runtime.productions,
                                                  projects_root=runtime.projects)[PID].complete
    assert oracle.reconcile_project(directory.name, directory, persist=False).verdict == oracle.ProjectVerdict.PROJECT_COMPLETE
    status, detail = request(runtime.base, UNIT_ROUTE)
    assert status == 200 and detail["summary_video"]["promoted"] is True
    assert detail["bound_project"]["artifacts"]["final_video"]["available"] is True
    assert detail["bound_project"]["artifacts"]["voice_timing"]["state"] == "NOT APPLICABLE"
    status, metadata = request(runtime.base, UNIT_ROUTE + "/summary-video")
    assert status == 200 and metadata["promoted"] is True

    yt, tk = FakeYouTube(directory), FakeTikTok(directory)
    if tiktok_environment == "sandbox":
        monkeypatch.setenv("TIKTOK_PUBLISH_ENVIRONMENT", "sandbox")
        tk.status = {"status": "PUBLISH_COMPLETE"}
    monkeypatch.setattr(web, "_new_youtube_publisher", lambda: yt)
    monkeypatch.setattr(web, "_new_publisher", lambda: tk)
    status, body = request(runtime.base, UNIT_ROUTE + "/publish", {"confirm": True, "platforms": ["youtube", "tiktok"]})
    assert status == 200, body
    persisted = read_manifest(directory)
    assert persisted["publication"]["state"] == "VERIFIED"
    assert persisted["tiktok_publication"]["state"] == ("VERIFIED_SANDBOX_PRIVATE" if tiktok_environment == "sandbox" else "VERIFIED")
    narration = load_narration_artifact(PID, UID, root=runtime.productions)
    assert narration.audio_provider == "fixture-tts"
    assert persisted["publication"]["title"] == "A grounded source recap"
    assert persisted["publication"]["description"] == narration.narration_text.strip()
    assert persisted["tiktok_publication"]["tiktok_metadata"]["title"].startswith("A grounded source recap")
    assert yt.calls.count("upload") == 1 and tk.calls.count("init") == 1
    before = (directory / "manifest.json").read_bytes()
    assert promote(runtime)[0] == 200
    assert (directory / "manifest.json").read_bytes() == before


def test_optional_build_flag_promotes_without_publishing(runtime, monkeypatch):
    monkeypatch.setattr(web, "_new_youtube_publisher", lambda: pytest.fail("promotion must not initialize publishers"))
    monkeypatch.setattr(web, "_new_publisher", lambda: pytest.fail("promotion must not initialize publishers"))
    summary = assemble(runtime, promote=True)
    assert summary["promoted"] is True and summary["bound_project"]["source_summary_handoff"]
    manifest = read_manifest(runtime.projects / summary["bound_project_id"])
    assert manifest["publication"] is None and manifest["tiktok_publication"] is None


def test_unpromoted_summary_returns_actionable_422(rendered, monkeypatch):
    monkeypatch.setattr(web, "_new_youtube_publisher", lambda: pytest.fail("unbound summary cannot upload"))
    monkeypatch.setattr(web, "_new_publisher", lambda: pytest.fail("unbound summary cannot upload"))
    status, body = request(rendered.base, UNIT_ROUTE + "/publish", {"confirm": True, "platforms": ["youtube", "tiktok"]})
    assert status == 422 and body["error"] == "SUMMARY_HANDOFF_REQUIRED"
    assert body["promote_url"] == UNIT_ROUTE + "/summary-video/promote"
    assert "Promote" in body["message"]
    assert rendered.executor.derive_progress().units[0].state == UnitExecutionState.NOT_STARTED
    assert list(rendered.projects.iterdir()) == []


def test_standard_bound_project_is_never_clobbered(rendered):
    _, _, directory = promote(rendered)
    path, manifest = pub.load_manifest(directory.name, directory)
    manifest.source_summary_handoff = None
    manifest.production_binding = ProductionBinding.model_validate({
        **manifest.production_binding.model_dump(), "handoff_kind": None, "execution_dir": None,
    })
    scene = directory / "standard-scene.mp4"
    scene.write_bytes(b"existing-standard-scene")
    manifest.scene_video_paths = [str(scene)]
    manifest.publication = PublicationRecord(project_id=directory.name, state=PublicationState.VERIFIED, youtube_video_id="protected-standard-upload")
    pub.save_manifest(path, manifest)
    before = path.read_bytes()
    status, body = request(rendered.base, UNIT_ROUTE + "/summary-video/promote", {})
    assert status == 409 and body["error"] == "BOUND_PROJECT_CONFLICT"
    assert path.read_bytes() == before and scene.read_bytes() == b"existing-standard-scene"


PROTECTED = [PublicationState.UPLOAD_SUBMITTED, PublicationState.IDENTITY_PERSISTED,
             PublicationState.PROCESSING, PublicationState.VERIFIED, PublicationState.VERIFIED_SANDBOX_PRIVATE,
             PublicationState.UNKNOWN_RECONCILIATION_REQUIRED, PublicationState.FAILED]


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
@pytest.mark.parametrize("state", PROTECTED)
def test_repromotion_protects_all_publication_states(rendered, platform, state):
    _, _, directory = promote(rendered)
    path, manifest = pub.load_manifest(directory.name, directory)
    fingerprint = manifest.source_summary_handoff.file_fingerprint
    if platform == "youtube":
        manifest.publication = PublicationRecord(project_id=directory.name, state=state, file_fingerprint=fingerprint,
                                                  youtube_video_id="protected-yt-id", error="retained diagnostic")
    else:
        manifest.tiktok_publication = TikTokPublicationRecord(project_id=directory.name, state=state, file_fingerprint=fingerprint,
                                                               tiktok_publish_id="protected-tk-id", error="retained diagnostic")
    pub.save_manifest(path, manifest)
    before = path.read_bytes()
    media_before = Path(manifest.final_video_path).read_bytes()
    assert promote(rendered)[0] == 200
    assert path.read_bytes() == before
    # Sidecar mutation must not mutate the project-owned accepted master.
    summary_video_output_path(PID, UID, root=rendered.productions).write_bytes(b"different-summary-bytes")
    status, body = request(rendered.base, UNIT_ROUTE + "/summary-video/promote", {})
    assert status == 409 and body["error"] == "SUMMARY_PUBLICATION_PROTECTED"
    assert path.read_bytes() == before
    assert Path(manifest.final_video_path).read_bytes() == media_before
    assert oracle.reconcile_project(directory.name, directory, persist=False).complete


@pytest.mark.parametrize("field", ["media", "narration", "plan", "binding"])
def test_canonical_oracle_revalidates_import_evidence(rendered, field):
    _, _, directory = promote(rendered)
    path, manifest = pub.load_manifest(directory.name, directory)
    if field == "media":
        Path(manifest.final_video_path).write_bytes(b"corrupt-accepted-media")
    elif field == "narration":
        (directory / manifest.source_summary_handoff.snapshot_dir / "narration.json").write_text("{}", encoding="utf-8")
    elif field == "plan":
        Path(manifest.content_plan_path).write_text("{}", encoding="utf-8")
    else:
        manifest.production_binding = None
        pub.save_manifest(path, manifest)
    assert oracle.reconcile_project(directory.name, directory, persist=False).complete is False
    with pytest.raises(PublishingEligibilityError):
        pub.prepare_publish(directory.name, directory)
    with pytest.raises(PublishingEligibilityError):
        tt.prepare_tiktok_publish(directory.name, directory)
    if field != "binding":
        with pytest.raises(ValueError, match="cannot be regenerated by the scene engine"):
            rendered.executor.execute(unit_id=UID)


@pytest.mark.parametrize("body", [{"promote_to_publishable": "true"}, {"promote_to_publishable": 1}, []])
def test_build_rejects_non_boolean_promotion_before_assembly(runtime, body):
    status, payload = request(runtime.base, UNIT_ROUTE + "/summary-video", body)
    assert status == 400 and payload["error"] == "INVALID_REQUEST"
    assert list(runtime.projects.iterdir()) == []


def test_promote_route_is_post_only_and_refuses_retargeting(runtime):
    assert request(runtime.base, UNIT_ROUTE + "/summary-video/promote")[0] == 404
    status, body = request(runtime.base, UNIT_ROUTE + "/summary-video/promote", {"project_id": "clobber"})
    assert status == 400 and body["error"] == "INVALID_REQUEST"


def test_direct_helper_promotion_uses_the_same_canonical_binding(rendered):
    from app.production.summary_handoff import promote_narrated_summary_to_project

    result = promote_narrated_summary_to_project(
        PID, UID, root=rendered.productions, projects_root=rendered.projects,
    )
    assert result.created
    assert oracle.reconcile_project(result.project_dir.name, result.project_dir).complete
    assert rendered.executor.discover_bindings()[UID].directory_id == result.project_dir.name


@pytest.mark.parametrize("state", [PublicationState.NOT_STARTED, PublicationState.PREPARED])
def test_unpublished_summary_update_preserves_preparation_records(rendered, state):
    _, _, directory = promote(rendered)
    path, manifest = pub.load_manifest(directory.name, directory)
    old_media = Path(manifest.final_video_path)
    fingerprint = manifest.source_summary_handoff.file_fingerprint
    manifest.publication = PublicationRecord(project_id=directory.name, state=state, file_fingerprint=fingerprint,
                                              error="retained preparation diagnostic")
    manifest.tiktok_publication = TikTokPublicationRecord(project_id=directory.name, state=state, file_fingerprint=fingerprint)
    pub.save_manifest(path, manifest)
    before = read_manifest(directory)
    summary = summary_video_output_path(PID, UID, root=rendered.productions)
    replacement = rendered.tmp_path / "new-master.mp4"
    run_ffmpeg(["-y", "-i", str(summary), "-map", "0", "-c", "copy",
                "-metadata", "comment=new unpublished summary version", str(replacement)], "unpublished summary replacement")
    summary.write_bytes(replacement.read_bytes())
    metadata_path = summary_video_metadata_path(PID, UID, root=rendered.productions)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.update(file_fingerprint=compute_content_hash(summary), file_size=summary.stat().st_size)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    assert metadata["file_fingerprint"] != fingerprint
    status, payload = request(rendered.base, UNIT_ROUTE + "/summary-video/promote", {})
    assert status == 200 and payload["project_id"] == directory.name
    updated = read_manifest(directory)
    assert updated["publication"] == before["publication"]
    assert updated["tiktok_publication"] == before["tiktok_publication"]
    assert updated["production_binding"]["unit_plan_version"] == before["production_binding"]["unit_plan_version"] + 1
    assert old_media.is_file() and compute_content_hash(old_media) == fingerprint
    assert oracle.reconcile_project(directory.name, directory).complete
    # Existing preparation engines, not promotion, own refreshed fingerprints.
    assert pub.prepare_publish(directory.name, directory).file_fingerprint == metadata["file_fingerprint"]
    assert tt.prepare_tiktok_publish(directory.name, directory).file_fingerprint == metadata["file_fingerprint"]


def test_changed_narration_is_protected_after_publication(rendered):
    _, _, directory = promote(rendered)
    path, manifest = pub.load_manifest(directory.name, directory)
    manifest.publication = PublicationRecord(project_id=directory.name, state=PublicationState.UPLOAD_SUBMITTED)
    pub.save_manifest(path, manifest)
    before = path.read_bytes()
    narration = load_narration_artifact(PID, UID, root=rendered.productions)
    narration.narration_text += " Changed grounded script."
    save_narration_artifact(narration, production_id=PID, unit_id=UID, root=rendered.productions)
    status, payload = request(rendered.base, UNIT_ROUTE + "/summary-video/promote", {})
    assert status == 409 and payload["error"] == "SUMMARY_PUBLICATION_PROTECTED"
    assert path.read_bytes() == before


def test_removed_summary_sidecars_do_not_erase_or_retarget_the_canonical_project(rendered, monkeypatch):
    _, _, directory = promote(rendered)
    before = read_manifest(directory)
    summary_video_output_path(PID, UID, root=rendered.productions).unlink()
    summary_video_metadata_path(PID, UID, root=rendered.productions).unlink()
    status, detail = request(rendered.base, UNIT_ROUTE)
    assert status == 200 and detail["summary_video"]["promoted"] is True
    assert detail["summary_video"]["rendered"] is False
    assert detail["summary_video"]["bound_project_id"] == directory.name
    assert detail["execution"]["state"] == "COMPLETED"
    publisher = FakeYouTube(directory)
    monkeypatch.setattr(web, "_new_youtube_publisher", lambda: publisher)
    status, body = request(rendered.base, UNIT_ROUTE + "/publish", {"confirm": True, "platforms": ["youtube"]})
    assert status == 200, body
    assert read_manifest(directory)["publication"]["final_video_path"] == before["final_video_path"]


@pytest.mark.parametrize("bound", [False, True])
def test_persistence_failure_rolls_back_new_and_existing_handoffs(rendered, monkeypatch, bound):
    if bound:
        _, _, directory = promote(rendered)
        manifest_before = (directory / "manifest.json").read_bytes()
        narration = load_narration_artifact(PID, UID, root=rendered.productions)
        narration.summary = "New source summary metadata before any publication."
        save_narration_artifact(narration, production_id=PID, unit_id=UID, root=rendered.productions)
        from app.production.summary_video import narration_content_hash

        metadata_path = summary_video_metadata_path(PID, UID, root=rendered.productions)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["narration_content_hash"] = narration_content_hash(narration)
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    plan_dir = unit_plan_dir(PID, UID, root=rendered.productions)
    plan_before = {name: (plan_dir / name).read_bytes() if (plan_dir / name).exists() else None
                   for name in ("production_plan.json", "content_brief.json", "plan_meta.json")}

    def fail(*args, **kwargs):
        raise OSError("fixture manifest persistence failure")

    monkeypatch.setattr(pub, "save_manifest", fail)
    status, payload = request(rendered.base, UNIT_ROUTE + "/summary-video/promote", {})
    assert status == 500 and payload["error"] == "SUMMARY_PROMOTION_FAILED"
    for name, data in plan_before.items():
        path = plan_dir / name
        assert (path.read_bytes() if path.exists() else None) == data
    if bound:
        assert (directory / "manifest.json").read_bytes() == manifest_before
        assert oracle.reconcile_project(directory.name, directory).complete
    else:
        assert list(rendered.projects.iterdir()) == []


@pytest.mark.parametrize("body", [[], None, "not-an-object"])
def test_promotion_refuses_non_object_json_without_creating_binding(runtime, body):
    req = urllib.request.Request(runtime.base + UNIT_ROUTE + "/summary-video/promote", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(req, timeout=10)
    assert error.value.code == 400
    assert list(runtime.projects.iterdir()) == []


@pytest.mark.parametrize("malformed", [b"{", b"\xff"])
def test_promotion_refuses_invalid_json(runtime, malformed):
    req = urllib.request.Request(runtime.base + UNIT_ROUTE + "/summary-video/promote", data=malformed,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(req, timeout=10)
    assert error.value.code == 400
    assert list(runtime.projects.iterdir()) == []


@pytest.mark.parametrize("artifact", ["missing_media", "empty_media", "invalid_media", "missing_narration", "wrong_identity"])
def test_promotion_requires_matching_grounded_artifacts_and_fresh_media(rendered, artifact):
    summary_path = summary_video_output_path(PID, UID, root=rendered.productions)
    if artifact == "missing_media":
        summary_path.unlink()
    elif artifact == "empty_media":
        summary_path.write_bytes(b"")
    elif artifact == "invalid_media":
        summary_path.write_bytes(b"not-a-real-mp4")
    elif artifact == "missing_narration":
        (summary_path.parent / "narration.json").unlink()
    else:
        metadata_path = summary_video_metadata_path(PID, UID, root=rendered.productions)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["unit_id"] = "unit-999"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    status, _ = request(rendered.base, UNIT_ROUTE + "/summary-video/promote", {})
    assert status in {404, 422}
    assert list(rendered.projects.iterdir()) == []


def test_legacy_summary_without_new_hashes_can_be_explicitly_promoted(rendered):
    metadata_path = summary_video_metadata_path(PID, UID, root=rendered.productions)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.pop("file_fingerprint", None); metadata.pop("narration_content_hash", None)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    _, payload, directory = promote(rendered)
    assert payload["promoted"] is True
    assert oracle.reconcile_project(directory.name, directory).complete


def test_active_execution_or_publication_refuses_promotion(rendered):
    with web._production_runs_lock:
        web._production_runs[PID] = {"running": True}
    try:
        status, payload = request(rendered.base, UNIT_ROUTE + "/summary-video/promote", {})
        assert status == 409 and payload["error"] == "EXECUTION_ALREADY_RUNNING"
    finally:
        with web._production_runs_lock:
            web._production_runs.pop(PID, None)
    web._publish_lock.acquire()
    try:
        status, payload = request(rendered.base, UNIT_ROUTE + "/summary-video/promote", {})
        assert status == 409 and payload["error"] == "PUBLISH_ALREADY_RUNNING"
    finally:
        web._publish_lock.release()
    assert list(rendered.projects.iterdir()) == []


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_status_post_rejection_delivers_405_with_unread_body_without_publication(runtime, platform, monkeypatch):
    monkeypatch.setattr(web, "_new_youtube_publisher", lambda: pytest.fail("GET-only rejection must never initialize a publisher"))
    monkeypatch.setattr(web, "_new_publisher", lambda: pytest.fail("GET-only rejection must never initialize a publisher"))
    # More than one TCP packet: ensures body draining is required rather than
    # accidentally passing because a tiny JSON body arrived with the headers.
    status, payload = request(runtime.base, f"/api/projects/unbound/publish/{platform}/status", {"ignored": "x" * 64000})
    assert status == 405 and payload["error"] == "METHOD_NOT_ALLOWED"
    assert list(runtime.projects.iterdir()) == []