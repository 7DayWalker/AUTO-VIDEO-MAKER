import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen
from unittest.mock import Mock

import pytest

from app.orchestrator import reconciliation as oracle
from app.orchestrator.production_execution import ProductionExecutor
from app.production.storage import compute_content_hash, load_production
from app.repurpose.handoff import promote_short, validate_repurpose_handoff
from app.repurpose.models import RenderArtifact, RepurposeError
from app.repurpose.service import create_batch
from app.repurpose.storage import atomic_json, unit_dir
from app.web import server as web
from tests.repurpose_fixtures import PID, FakePlanner, request_fixture, source_fixture


@pytest.fixture
def short_project(tmp_path, monkeypatch):
    root, projects = tmp_path/"productions", tmp_path/"projects"
    evidence = source_fixture(root)
    job = create_batch(PID, request_fixture(evidence).model_dump(mode="json"), root=root, background=False, provider=FakePlanner())
    assert job["status"] == "planned", job
    uid = "unit-short-001"
    folder = unit_dir(PID, uid, root)
    folder.mkdir(parents=True)
    media = folder/"short.mp4"
    media.write_bytes(b"short-synthetic-decoding-is-mocked")
    cuts, offset = [], 0
    for cut in job["clips"][0]["cuts"]:
        cuts.append({**cut, "output_start": offset, "output_duration": 5})
        offset += 5
    artifact = RenderArtifact(production_id=PID, unit_id=uid, batch_id=job["batch_id"], plan_hash=job["plan_hash"],
        mode="narrated", framing="cinema_canvas", output_path=f"units/{uid}/repurpose/short.mp4",
        file_fingerprint=compute_content_hash(media), file_size=media.stat().st_size, duration_seconds=45,
        cuts=cuts, captions_precision="word", loudness={"integrated_lufs": -14, "true_peak_db": -1.5},
        validation={"valid": True, "decode_errors": 0, "captions_burned": True, "hook_banner_burned": True})
    atomic_json(folder/"render.json", artifact)
    (folder/"captions.ass").write_text("[Script Info]\nPlayResX: 1080\nPlayResY: 1920", encoding="utf-8")
    atomic_json(folder/"caption_timing.json", {"entries": [], "precision": "word"})
    info = SimpleNamespace(width=1080, height=1920, duration=45, has_video=True, has_audio=True,
        video_codec="h264", audio_codec="aac", file_size=media.stat().st_size, audio_sample_rate=48000,
        audio_channels=2, audio_duration=45, audio_stream_count=1, video_stream_count=1, sample_rate=48000, channels=2)
    monkeypatch.setattr(oracle, "check_ffprobe_available", lambda: True)
    monkeypatch.setattr(oracle, "probe_media", lambda path: info)
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", root)
    monkeypatch.setattr(web, "PROJECTS_DIR", projects)
    return SimpleNamespace(root=root, projects=projects, job=job, uid=uid, artifact=artifact, media=media)


def test_short_import_is_canonical_and_independent(short_project):
    fixture = short_project
    directory, manifest, created = promote_short(PID, fixture.uid, root=fixture.root, projects_root=fixture.projects)
    assert created and manifest.source_summary_handoff is None
    assert manifest.source_repurpose_handoff.handoff_kind == "repurpose_short"
    validate_repurpose_handoff(manifest, directory)
    report = oracle.reconcile_project(directory.name, directory, persist=False)
    assert report.complete, report.reasons
    assert report.voice["status"] == "NOT_REQUIRED" and report.scenes["status"] == "NOT_REQUIRED"
    report = ProductionExecutor(PID, productions_root=fixture.root, projects_root=fixture.projects).derive_progress()
    assert next(u for u in report.units if u.unit_id == fixture.uid).state.value == "COMPLETED"
    assert len(load_production(PID, root=fixture.root).units) == 4
    second = promote_short(PID, fixture.uid, root=fixture.root, projects_root=fixture.projects)
    assert not second[2] and second[0] == directory


@pytest.mark.parametrize("name", ["short.mp4", "captions.ass", "render.json", "repurpose_plan.json"])
def test_accepted_package_tampering_is_rejected(short_project, name):
    f = short_project
    directory, manifest, _ = promote_short(PID, f.uid, root=f.root, projects_root=f.projects)
    path = directory/manifest.source_repurpose_handoff.snapshot_dir/name
    path.write_bytes(path.read_bytes()+b"changed")
    with pytest.raises(ValueError):
        validate_repurpose_handoff(manifest, directory)
    assert not oracle.reconcile_project(directory.name, directory, persist=False).complete


def test_promote_requires_exact_fingerprints_and_never_publishes(short_project, monkeypatch):
    f = short_project
    monkeypatch.setattr(web, "unit_publish_payload", lambda *a: pytest.fail("no upload"))
    assert web.repurpose_promote_payload(PID, f.uid, {})[0] == 400
    status, payload = web.repurpose_promote_payload(PID, f.uid, {"confirm": True,
        "expected_plan_hash": f.artifact.plan_hash, "expected_file_fingerprint": f.artifact.file_fingerprint})
    assert status == 200 and payload["promoted"] and payload["repurpose_video"]["promoted"]


def test_media_get_head_and_suffix_ranges(short_project):
    f = short_project
    http = ThreadingHTTPServer(("127.0.0.1", 0), web.AVMHandler)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{http.server_port}/api/productions/{PID}/units/{f.uid}/repurpose-video/media"
    try:
        with urlopen(Request(url, headers={"Range": "bytes=0-4"})) as response:
            assert response.status == 206 and response.read() == f.media.read_bytes()[:5]
        with urlopen(Request(url, method="HEAD")) as response:
            assert response.status == 200 and response.read() == b""
            assert int(response.headers["Content-Length"]) == f.media.stat().st_size
        with urlopen(Request(url, headers={"Range": "bytes=-3"})) as response:
            assert response.status == 206 and response.read() == f.media.read_bytes()[-3:]
    finally:
        http.shutdown(); http.server_close(); worker.join()


@pytest.mark.parametrize("pid,uid", [("..", "unit-short-001"), (PID, "../../outside")])
def test_invalid_identity_never_creates_lock_files(tmp_path, pid, uid):
    before = list(tmp_path.rglob("*"))
    with pytest.raises(RepurposeError):
        promote_short(pid, uid, root=tmp_path, projects_root=tmp_path/"projects")
    assert list(tmp_path.rglob("*")) == before


def test_180_second_raw_master_passes_canonical_handoff(tmp_path,monkeypatch):
    root,projects=tmp_path/"productions",tmp_path/"projects"
    evidence=source_fixture(root)
    from tests.repurpose_fixtures import output_fixture
    output=output_fixture("raw_highlight")
    for clip in output["clips"]:
        clip["cuts"]=[{"start_seconds":i*30,"end_seconds":(i+1)*30,"transcript_segment_ids":[i*6]} for i in range(6)]
    job=create_batch(PID,request_fixture(evidence,target_seconds=180,mode="raw_highlight",allow_overlap=True).model_dump(mode="json"),
        root=root,background=False,provider=FakePlanner(output))
    assert job["status"]=="planned",job
    uid="unit-short-001";folder=unit_dir(PID,uid,root);folder.mkdir(parents=True)
    media=folder/"short.mp4";media.write_bytes(b"synthetic-180s-media-probe-is-mocked")
    cuts=[{**c,"output_start":i*30,"output_duration":30} for i,c in enumerate(job["clips"][0]["cuts"])]
    artifact=RenderArtifact(production_id=PID,unit_id=uid,batch_id=job["batch_id"],plan_hash=job["plan_hash"],mode="raw_highlight",
        framing="cinema_canvas",output_path=f"units/{uid}/repurpose/short.mp4",file_fingerprint=compute_content_hash(media),
        file_size=media.stat().st_size,duration_seconds=180,cuts=cuts,captions_precision="segment_estimated_phrases",
        loudness={"integrated_lufs":-14,"true_peak_db":-1.5},validation={"valid":True,"decode_errors":0,"captions_burned":True,"hook_banner_burned":True})
    atomic_json(folder/"render.json",artifact);(folder/"captions.ass").write_text("[Script Info]\nPlayResX: 1080\nPlayResY: 1920")
    atomic_json(folder/"caption_timing.json",{"entries":[],"precision":"segment_estimated_phrases"})
    info=SimpleNamespace(width=1080,height=1920,duration=180,has_video=True,has_audio=True,video_codec="h264",audio_codec="aac",
        file_size=media.stat().st_size,audio_sample_rate=48000,audio_channels=2,audio_duration=180,audio_stream_count=1,video_stream_count=1,sample_rate=48000,channels=2)
    monkeypatch.setattr(oracle,"check_ffprobe_available",lambda:True);monkeypatch.setattr(oracle,"probe_media",lambda path:info)
    directory,manifest,_=promote_short(PID,uid,root=root,projects_root=projects)
    validate_repurpose_handoff(manifest,directory)
    assert oracle.reconcile_project(directory.name,directory,persist=False).complete