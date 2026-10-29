"""Track 9 HTTP /api surface -- hermetic mock and real paths, no live calls."""
from __future__ import annotations
import json, threading, urllib.request, urllib.error
import io
import time
from pathlib import Path
import pytest
from app.assembly.video import create_test_video
from app.production.models import Production, ProductionCommand, ProductionUnit
from app.production.storage import save_new_production
from app.web import server as web
PID="prd_20260831_track9_api_001"
UID="unit-001"
AID="src-001"
def _server(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "PROJECTS_DIR", tmp_path/"projects")
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path/"productions")
    monkeypatch.setattr(web, "WEBAPP_DIR", tmp_path/"webapp")
    (tmp_path/"webapp").mkdir(); (tmp_path/"webapp"/"index.html").write_text("<html>avm</html>", encoding="utf-8")
    (tmp_path/"projects").mkdir(); (tmp_path/"productions").mkdir()
    httpd=web.ThreadingHTTPServer(("127.0.0.1",0), web.AVMHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}", httpd, tmp_path
def _get(base, path):
    try:
        with urllib.request.urlopen(base+path, timeout=10) as r:
            return r.status, json.loads(r.read().decode()), dict(r.headers)
    except urllib.error.HTTPError as e:
        b=e.read().decode()
        try: j=json.loads(b)
        except Exception: j={"raw": b}
        return e.code, j, dict(e.headers)
def _post(base, path, body=None):
    data=json.dumps(body or {}).encode()
    req=urllib.request.Request(base+path, data=data, method="POST", headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode()), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw=e.read().decode()
        try: j=json.loads(raw)
        except Exception: j={"raw": raw}
        return e.code, j, dict(e.headers)
def _get_bytes(base, path, headers=None):
    req=urllib.request.Request(base+path, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
def _make_prod(tmp_path):
    prod=Production(production_id=PID, title="Track9 API e2e", command=ProductionCommand(text="track9 api e2e", workload_shape="single_video", explicit_constraints={}, creation_context={"source":"test"}, issued_at="2026-08-31T09:00:00+00:00"), units=[ProductionUnit(unit_id=UID, ordinal=1, unit_type="standalone_video", title="u1", premise="p", content_intent={"tone":"calm"})], created_at="2026-08-31T09:00:00+00:00")
    save_new_production(prod, root=tmp_path/"productions")
    return prod
def _ingest(tmp_path, duration=40.0):
    tmp_vid=tmp_path/"_src_tmp.mp4"
    create_test_video(tmp_vid, duration=duration, width=640, height=360, fps=24, color="blue")
    from app.production.source import register_source
    status,payload=register_source(PID, str(tmp_vid), root=tmp_path/"productions")
    assert status==201, payload
    return payload["source_asset"]
def _transcribe(tmp_path):
    from app.transcription.transcribe import transcribe_source
    status,payload=transcribe_source(PID, AID, root=tmp_path/"productions", provider="mock")
    assert status in (200,201), payload
    # Enrich transcript to 12 segments so mock_grounded_plan can produce 2+ beats
    try:
        import json
        from app.production.storage import transcript_path_for, load_production
        from app.production.models import SourceTranscript, TranscriptSegment
        prod=load_production(PID, root=tmp_path/"productions")
        sa=next(a for a in (prod.source_assets or []) if a.asset_id==AID)
        dur=float(sa.duration)
        n=12
        segs=[TranscriptSegment(text=f"Mock enriched segment {i+1} about the source content topic example", start=(dur/n)*i, end=(dur/n)*(i+1)-0.01).model_dump() for i in range(n)]
        # Reload existing transcript path and overwrite with enriched one
        tp=transcript_path_for(PID, AID, root=tmp_path/"productions")
        data=json.loads(tp.read_text(encoding="utf-8"))
        data["segments"]=segs
        tp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"enrich failed: {e}")
    return payload
def test_capabilities_ai_summary_shape_and_no_leak(monkeypatch, tmp_path):
    base,httpd,_=_server(monkeypatch, tmp_path)
    try:
        status,payload,_=_get(base, "/api/capabilities")
        assert status==200
        assert "ai_summary" in payload
        ai=payload["ai_summary"]
        assert "modes" in ai and "understanding_models" in ai and "status" in ai
        assert "standard" in ai["modes"] and "pro" in ai["modes"]
        assert ai["modes"]["standard"]["default_model_id"]=="qwen-3.8-flash"
        assert ai["modes"]["pro"]["default_model_id"]=="gemini-3-flash-preview"
        assert ai["modes"]["standard"]["understanding_method"]=="transcript_audio"
        assert ai["modes"]["pro"]["understanding_method"]=="frame_sequence"
        ums=ai["understanding_models"]
        assert len(ums)>=2
        for m in ums:
            assert "id" in m and "display_name" in m and "family" in m
            assert "capabilities" in m and "eligible_modes" in m and "configured" in m and "status" in m
            assert "provider" not in json.dumps(m).lower()
            assert "wire" not in json.dumps(m).lower()
            for mode in m["eligible_modes"]:
                assert mode in ("standard","pro")
            assert m["status"] in ("ready","not_configured","unknown")
            assert isinstance(m["configured"], bool)
        std_only=[x for x in ums if x["id"]=="qwen-3.8-flash"]
        assert std_only and std_only[0]["eligible_modes"]==["standard"]
        pro_eligible=[x for x in ums if "pro" in x["eligible_modes"]]
        assert len(pro_eligible)>=2
        raw=json.dumps(ai).lower()
        for forbidden in ("codecraft","alibaba","openrouter","sk-","api_key","balance","quota"):
            assert forbidden not in raw, f"leaked {forbidden}"
        assert "seams" in payload and "models" in payload and "production_routes" in payload
        assert payload["sample"] is False
    finally: httpd.shutdown()

def test_capabilities_synthetic_registry_drives_ai_summary(monkeypatch, tmp_path):
    from app.content.model_registry import CanonicalModel, ModelCapabilities, ProviderBinding
    ma=CanonicalModel(id="synthetic-alpha", display_name="Synthetic Alpha", family="alpha", capabilities=ModelCapabilities(text=True, reasoning=True), bindings=(ProviderBinding(provider="mock", provider_model_id="alpha-wire"),), default_binding="mock")
    mb=CanonicalModel(id="synthetic-beta", display_name="Synthetic Beta", family="beta", capabilities=ModelCapabilities(text=True, vision=True, long_context=True, tool_use=True, multimodal=True, video=True), bindings=(ProviderBinding(provider="mock", provider_model_id="beta-wire"),), default_binding="mock")
    monkeypatch.setattr("app.content.model_registry.list_models", lambda: (ma, mb))
    base,httpd,_=_server(monkeypatch, tmp_path)
    try:
        status,payload,_=_get(base, "/api/capabilities")
        assert status==200
        ids={x["id"] for x in payload["ai_summary"]["understanding_models"]}
        assert ids=={"synthetic-alpha","synthetic-beta"}
        by={x["id"]: x for x in payload["ai_summary"]["understanding_models"]}
        assert by["synthetic-alpha"]["eligible_modes"]==["standard"]
        assert set(by["synthetic-beta"]["eligible_modes"])=={"standard","pro"}
    finally: httpd.shutdown()
def test_e2e_narration_standard_pro_synthesize_summary_and_media(monkeypatch, tmp_path):
    base,httpd,_=_server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path); _ingest(tmp_path, duration=12.0); _transcribe(tmp_path)
        s,payload,_=_post(base, f"/api/productions/{PID}/units/{UID}/narration", {"summary_mode":"standard","mock_mode": True,"target_duration_seconds":6})
        assert s==201, payload
        assert payload["production_id"]==PID and payload["unit_id"]==UID
        assert payload["target_duration_seconds"]==6.0
        assert len(payload["beats"])>=1
        s,j,_=_get(base, f"/api/productions/{PID}/units/{UID}/narration")
        assert s==200, j
        assert j["narration_id"]==payload["narration_id"]
        s,payload2,_=_post(base, f"/api/productions/{PID}/units/{UID}/narration", {"summary_mode":"pro","selection_mode":"explicit","preferred_model_id":"gemini-3-flash-preview","mock_mode": True,"target_duration_seconds":8})
        assert s==200, payload2
        s,payload3,_=_post(base, f"/api/productions/{PID}/units/{UID}/narration/synthesize", {"mock_mode": True,"format":"mp3"})
        assert s==200, payload3
        assert "audio" in payload3
        assert payload3["audio"]["provider"]=="mock"
        assert payload3["audio"]["status"]=="completed"
        s,payload4,_=_post(base, f"/api/productions/{PID}/units/{UID}/summary-video", {})
        assert s==201, payload4
        assert payload4["production_id"]==PID and payload4["unit_id"]==UID
        assert "output_path" in payload4 and "media_url" in payload4
        assert payload4["media_url"]==f"/api/productions/{PID}/units/{UID}/summary-video/media"
        assert payload4["file_size"]>0
        s,j2,_=_get(base, f"/api/productions/{PID}/units/{UID}/summary-video")
        assert s==200, j2
        assert j2["narration_id"]==payload3["narration_id"]
        status,data,headers=_get_bytes(base, f"/api/productions/{PID}/units/{UID}/summary-video/media")
        assert status==200, (status, data[:200])
        assert len(data)>1000
        ctype=(headers.get("Content-Type") or headers.get("content-type") or "").lower()
        assert "video/mp4" in ctype or "mp4" in ctype or "video" in ctype
        status,data2,headers2=_get_bytes(base, f"/api/productions/{PID}/units/{UID}/summary-video/media", headers={"Range":"bytes=0-99"})
        assert status==206, (status, data2[:200])
        assert len(data2)==100
        assert "bytes 0-99" in (headers2.get("Content-Range") or headers2.get("content-range") or "")
    finally: httpd.shutdown()
def test_invalid_model_returns_404_or_422_no_substitution(monkeypatch, tmp_path):
    base,httpd,_=_server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path); _ingest(tmp_path, duration=10.0); _transcribe(tmp_path)
        s,j,_=_post(base, f"/api/productions/{PID}/units/{UID}/narration", {"summary_mode":"standard","preferred_model_id":"does-not-exist-xyz","mock_mode": True})
        assert s in (400,404), j
        assert j.get("error") in ("MODEL_NOT_FOUND","INVALID_REQUEST")
        s,j,_=_post(base, f"/api/productions/{PID}/units/{UID}/narration", {"summary_mode":"pro","preferred_model_id":"qwen-3.8-flash","mock_mode": True})
        assert s==422, j
        assert j.get("error")=="MODEL_NOT_ELIGIBLE"
        s,j,_=_post(base, f"/api/productions/{PID}/units/{UID}/narration", {"summary_mode":"pro","selection_mode":"explicit","mock_mode": True})
        assert s==400, j
        assert j.get("error")=="INVALID_REQUEST"
        s,j,_=_post(base, f"/api/productions/{PID}/units/{UID}/narration", {"summary_mode":"turbo","mock_mode": True})
        assert s==400, j
    finally: httpd.shutdown()

def test_missing_source_or_transcript_returns_404(monkeypatch, tmp_path):
    base,httpd,_=_server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path)
        s,j,_=_post(base, f"/api/productions/{PID}/units/{UID}/narration", {"mock_mode": True})
        assert s==404, j
        assert j.get("error")=="SOURCE_NOT_FOUND"
        _ingest(tmp_path, duration=10.0)
        s,j,_=_post(base, f"/api/productions/{PID}/units/{UID}/narration", {"mock_mode": True})
        assert s==404, j
        assert j.get("error")=="TRANSCRIPT_NOT_FOUND"
        s,j,_=_get(base, f"/api/productions/{PID}/units/{UID}/narration")
        assert s==404, j
        assert j.get("error")=="NARRATION_NOT_FOUND"
        s,j,_=_post(base, f"/api/productions/{PID}/units/{UID}/narration/synthesize", {"mock_mode": True})
        assert s==404, j
        s,j,_=_post(base, f"/api/productions/{PID}/units/{UID}/summary-video", {})
        assert s==404, j
        assert j.get("error")=="NARRATION_NOT_FOUND"
    finally: httpd.shutdown()
def test_summary_requires_audio(monkeypatch, tmp_path):
    base,httpd,_=_server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path); _ingest(tmp_path, duration=10.0); _transcribe(tmp_path)
        s,j,_=_post(base, f"/api/productions/{PID}/units/{UID}/narration", {"mock_mode": True})
        assert s==201, j
        s,j,_=_post(base, f"/api/productions/{PID}/units/{UID}/summary-video", {})
        assert s==404, j
        assert j.get("error")=="NARRATION_AUDIO_NOT_FOUND"
        s,j,_=_get(base, f"/api/productions/{PID}/units/{UID}/summary-video")
        assert s==404, j
        assert j.get("error")=="SUMMARY_VIDEO_NOT_FOUND"
        s,_,_=_get(base, f"/api/productions/{PID}/units/{UID}/summary-video/media")
        assert s==404
    finally: httpd.shutdown()
def test_summary_mode_pro_auto_selects_without_explicit_id(monkeypatch, tmp_path):
    base,httpd,_=_server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path); _ingest(tmp_path, duration=10.0); _transcribe(tmp_path)
        s,j,_=_post(base, f"/api/productions/{PID}/units/{UID}/narration", {"summary_mode":"pro","mock_mode": True})
        assert s==201, j
        assert len(j["beats"])>=1
    finally: httpd.shutdown()
def test_narration_synthesize_invalid_format(monkeypatch, tmp_path):
    base,httpd,_=_server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path); _ingest(tmp_path, duration=10.0); _transcribe(tmp_path)
        s,j,_=_post(base, f"/api/productions/{PID}/units/{UID}/narration", {"mock_mode": True})
        assert s==201, j
        s,j,_=_post(base, f"/api/productions/{PID}/units/{UID}/narration/synthesize", {"format":"flac"})
        assert s==400, j
        assert j.get("error")=="INVALID_REQUEST"
    finally: httpd.shutdown()


def _standard_real_source(tmp_path):
    """Persist canonical source/transcript evidence without FFmpeg or STT calls."""
    from app.production.storage import save_revision, transcript_path_for
    from tests.track9_narration_fakes import standard_evidence

    asset, transcript = standard_evidence()
    production = _make_prod(tmp_path)
    save_revision(production.model_copy(update={
        "source_assets": [asset], "plan_version": production.plan_version + 1,
        "revised_at": "2026-10-24T09:00:00+00:00",
    }), root=tmp_path / "productions")
    path = transcript_path_for(PID, AID, root=tmp_path / "productions")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(transcript.model_dump_json(), encoding="utf-8")
    return asset, transcript


@pytest.mark.parametrize("model_id", [None, "qwen-3.8-flash", "gemini-3-flash-preview"])
def test_standard_real_http_uses_resolved_adapter_and_persists(monkeypatch, tmp_path, model_id):
    from app.production.models import NarrationArtifact
    from app.production.narration import validate_narration_artifact
    from app.production.storage import load_narration_artifact
    from tests.track9_narration_fakes import install_standard_provider, standard_response

    def forbidden(*args, **kwargs):
        raise AssertionError("mock/Pro planner must not run for real Standard HTTP requests")

    monkeypatch.setattr("app.production.narration_mock.mock_grounded_plan", forbidden)
    monkeypatch.setattr("app.production.narration_pro.build_pro_multimodal_plan", forbidden)
    provider, factory_calls, binding = install_standard_provider(monkeypatch, model_id or "qwen-3.8-flash")
    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        asset, transcript = _standard_real_source(tmp_path)
        body = {"summary_mode": "standard", "mock_mode": False, "target_duration_seconds": 30}
        if model_id is not None:
            body.update(selection_mode="explicit", summary_understanding_model_id=model_id)
        status, payload, _ = _post(base, f"/api/productions/{PID}/units/{UID}/narration", body)
        assert status == 201, payload
        assert factory_calls == [binding.provider_model_id]
        assert provider.json_calls == len(provider.calls) == 1
        media_url = payload.pop("media_url")
        artifact = NarrationArtifact.model_validate(payload)
        assert artifact.summary == standard_response()["summary"]
        assert artifact.transcript_refs == ["1", "9"]
        assert [beat.order for beat in artifact.beats] == [1, 2]
        assert [ref.transcript_segment_ids for ref in artifact.source_refs] == [[1], [9]]
        assert artifact.narration_text == " ".join(beat["narration_text"] for beat in standard_response()["beats"])
        assert artifact.status == "grounded" and artifact.source_asset_id == AID
        validate_narration_artifact(artifact, source_asset=asset, source_transcript=transcript)
        assert load_narration_artifact(PID, UID, root=tmp_path / "productions").model_dump() == payload
        status, reloaded, _ = _get(base, media_url)
        assert status == 200 and reloaded == payload
        for key in ("provider", "provider_model_id", "wire_id", "api_key"):
            assert key not in payload
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_empty_real_source_intake_does_not_create_fixture(monkeypatch, tmp_path):
    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path)
        for body in ({}, {"mock_mode": False}, {"mock_mode": "true"}):
            status, payload, _ = _post(base, f"/api/productions/{PID}/source", body)
            assert status == 400, payload
        from app.production.storage import load_production

        assert not load_production(PID, root=tmp_path / "productions").source_assets
    finally:
        httpd.shutdown(); httpd.server_close()


def test_sandbox_source_registration_is_explicit_canonical_and_zero_typing(monkeypatch, tmp_path):
    from app.orchestrator.production_execution import ProductionExecutor
    from app.production.storage import load_production
    import mimetypes

    monkeypatch.setattr(mimetypes, "init", lambda *args, **kwargs: pytest.fail("Sandbox intake must not scan the workstation MIME registry."))

    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        status, creation, _ = _post(base, "/api/productions", {
            "mode": "single_video", "creation_method": "local", "title": "AI Summary Recap",
            "brief": "Prepare a source-grounded narrated AI Summary recap.", "output_intent": "youtube_shorts",
        })
        assert status == 201, creation
        pid = creation["production_id"]
        executor = ProductionExecutor(pid, productions_root=tmp_path / "productions", projects_root=tmp_path / "projects")
        monkeypatch.setattr(web, "_production_executor", lambda id: executor)
        # Measure per-command work, not first-use executable availability checks
        # (which start extra Windows processes and vary with cold antivirus I/O).
        from app.assembly.ffmpeg import get_ffmpeg_runner
        runner = get_ffmpeg_runner()
        runner.ensure_ffmpeg(); runner.ensure_ffprobe()
        started = time.perf_counter()
        status, registered, _ = _post(base, f"/api/productions/{pid}/source", {"mock_mode": True})
        timings = {"source": time.perf_counter() - started}
        assert timings["source"] < 1.0, timings
        assert status == 201 and registered["sandbox_fixture"] is True, registered
        source = registered["source_asset"]
        assert source["source_kind"] == "sandbox_fixture" and source["original_filename"] == "sandbox-summary-fixture.mp4"
        media = tmp_path / "productions" / pid / source["stored_path"]
        assert media.is_file() and media.stat().st_size > 0
        assert source["duration"] <= 4.0
        assert not load_production(pid, root=tmp_path / "productions").source_assets
        from app.production.source import load_sandbox_source
        from app.assembly.ffmpeg import probe_media
        assert load_sandbox_source(pid, root=tmp_path / "productions").asset_id == source["asset_id"]
        assert source["stored_path"].startswith("source/sandbox/")
        info = probe_media(media)
        assert info.has_video and info.has_audio and info.audio_codec == "aac"
        assert info.sample_rate == 16000
        assert not list(media.parent.glob(".sandbox-*"))
        from app.production.storage import compute_content_hash
        assert compute_content_hash(media) == source["sha256"]
        for route, body in [
            (f"/source/{source['asset_id']}/transcript", {"provider": "mock", "mock_mode": True}),
            ("/units/unit-001/narration", {"mock_mode": True, "asset_id": source["asset_id"]}),
            ("/units/unit-001/narration/synthesize", {"mock_mode": True}),
            ("/units/unit-001/summary-video", {"mock_mode": True}),
            ("/units/unit-001/summary-video/promote", {"mock_mode": True}),
        ]:
            started = time.perf_counter()
            status, payload, _ = _post(base, f"/api/productions/{pid}" + route, body)
            timings[route] = time.perf_counter() - started
            assert status in {200, 201}, (route, payload)
            assert timings[route] < 1.0, timings
        print("Sandbox step timings: " + json.dumps(timings, sort_keys=True))
        assert executor.derive_progress().complete
        manifest = next(executor.projects_root.glob("*/manifest.json"))
        accepted = json.loads(manifest.read_text(encoding="utf-8"))
        assert accepted["source_summary_handoff"]
        assert accepted["assembly_config"]["summary_mock_mode"] is True
        assert accepted["publication"] is None and accepted["tiktok_publication"] is None
    finally:
        httpd.shutdown(); httpd.server_close()


def _long_source_with_real_transcript(tmp_path):
    from app.production.models import SourceAsset, SourceTranscript, TranscriptSegment
    from app.production.storage import save_revision, transcript_path_for

    production = _make_prod(tmp_path)
    media = tmp_path / "productions" / PID / "source" / "src-001.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"Long source must never be opened or decoded in sandbox tests.")
    source = SourceAsset(asset_id=AID, original_filename="30-minute-movie.mp4", stored_path="source/src-001.mp4",
                         sha256="a" * 64, duration=1800.0, width=3840, height=2160,
                         ingested_at="2026-10-26T00:00:00", source_kind="local_file")
    save_revision(production.model_copy(update={"plan_version": production.plan_version + 1, "source_assets": [source]}),
                  root=tmp_path / "productions")
    transcript = SourceTranscript(asset_id=AID, provider="local_whisper", provider_version="real-fixture",
                                  language="en", created_at="2026-10-26T00:00:00",
                                  transcript_path="source/src-001.transcript.json",
                                  segments=[TranscriptSegment(text="Existing real transcript must be preserved.", start=0, end=1800)])
    canonical = transcript_path_for(PID, AID, root=tmp_path / "productions")
    canonical.write_text(transcript.model_dump_json(), encoding="utf-8")
    return source, canonical, canonical.read_text(encoding="utf-8")


@pytest.mark.parametrize("body", [{"mock_mode": True}, {"provider": "mock"}, {"mock_mode": True, "provider": "local_whisper"}])
@pytest.mark.parametrize("route", ["/transcribe", f"/source/{AID}/transcript"])
def test_mock_transcription_is_instant_for_long_source_and_preserves_real_transcript(monkeypatch, tmp_path, body, route):
    from app.transcription.providers import LocalWhisperProvider
    from app.transcription.transcribe import load_transcript
    import app.assembly.ffmpeg as ffmpeg

    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _, canonical, before = _long_source_with_real_transcript(tmp_path)
        def forbidden(*args, **kwargs):
            pytest.fail("Sandbox transcription must not load Whisper, run FFmpeg, or probe the full movie.")
        monkeypatch.setattr(LocalWhisperProvider, "transcribe", forbidden)
        monkeypatch.setattr(ffmpeg, "run_ffmpeg", forbidden)
        monkeypatch.setattr(ffmpeg, "probe_media", forbidden)
        started = time.perf_counter()
        status, payload, _ = _post(base, f"/api/productions/{PID}" + route, body)
        assert status == 201, payload
        assert time.perf_counter() - started < 1.0
        assert payload["provider"] == "mock" and payload["mock_mode"] is True
        assert max(segment["end"] for segment in payload["segments"]) <= 4.0
        assert canonical.read_text(encoding="utf-8") == before
        assert load_transcript(PID, AID, root=tmp_path / "productions").provider == "local_whisper"
        assert load_transcript(PID, AID, root=tmp_path / "productions", mock_mode=True).provider == "mock"
        assert payload["transcript_path"] != "source/src-001.transcript.json"
    finally:
        httpd.shutdown(); httpd.server_close()


def test_mock_transcription_does_not_wait_for_an_active_real_transcriber(monkeypatch, tmp_path):
    from app.transcription.providers import LocalWhisperProvider, TranscriptionResult
    from app.transcription.transcribe import transcribe_source

    _, canonical, before = _long_source_with_real_transcript(tmp_path)
    entered, release = threading.Event(), threading.Event()
    result = []
    def slow_real(self, media_path, duration):
        entered.set()
        assert release.wait(5), "test did not release its real transcription fixture"
        return TranscriptionResult("en", [{"text": "Finished real transcript", "start": 0, "end": duration}], "local_whisper", "real-fixture")
    monkeypatch.setattr(LocalWhisperProvider, "transcribe", slow_real)
    worker = threading.Thread(target=lambda: result.append(transcribe_source(PID, AID, root=tmp_path / "productions", provider="local_whisper")))
    worker.start()
    try:
        assert entered.wait(2)
        started = time.perf_counter()
        status, payload = transcribe_source(PID, AID, root=tmp_path / "productions", mock_mode=True)
        assert time.perf_counter() - started < 0.2
        assert status == 201 and payload["provider"] == "mock"
        assert worker.is_alive() and not release.is_set()
        assert canonical.read_text(encoding="utf-8") == before
    finally:
        release.set(); worker.join(timeout=5)
    assert not worker.is_alive() and result[0][0] == 201
    assert "Finished real transcript" in canonical.read_text(encoding="utf-8")


def test_real_transcription_remains_real_after_a_sandbox_transcript_exists(monkeypatch, tmp_path):
    from app.transcription.providers import LocalWhisperProvider, TranscriptionResult
    from app.transcription.transcribe import load_transcript

    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _long_source_with_real_transcript(tmp_path)
        status, _, _ = _post(base, f"/api/productions/{PID}/transcribe", {"mock_mode": True})
        assert status == 201
        calls = []
        def real(self, media_path, duration):
            calls.append((media_path.name, duration))
            return TranscriptionResult("en", [{"text": "New real transcript", "start": 0, "end": duration}], "local_whisper", "fixture")
        monkeypatch.setattr(LocalWhisperProvider, "transcribe", real)
        status, payload, _ = _post(base, f"/api/productions/{PID}/transcribe", {})
        assert status == 201 and payload["provider"] == "local_whisper" and payload["mock_mode"] is False
        assert calls == [("src-001.mp4", 1800.0)]
        assert load_transcript(PID, AID, root=tmp_path / "productions").segments[0].text == "New real transcript"
        assert load_transcript(PID, AID, root=tmp_path / "productions", mock_mode=True).provider == "mock"
    finally:
        httpd.shutdown(); httpd.server_close()


def test_failed_sandbox_media_generation_never_registers_a_false_source(monkeypatch, tmp_path):
    from app.production import sandbox
    from app.production.storage import load_production

    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path)
        def fail(output_path):
            raise RuntimeError("ffmpeg sandbox fixture failure")
        monkeypatch.setattr(sandbox, "create_sandbox_source", fail)
        status, payload, _ = _post(base, f"/api/productions/{PID}/source", {"mock_mode": True})
        assert status == 503 and payload["error"] == "SANDBOX_FIXTURE_UNAVAILABLE"
        assert not load_production(PID, root=tmp_path / "productions").source_assets
        assert not list((tmp_path / "productions" / PID / "source").glob(".sandbox-*"))
    finally:
        httpd.shutdown(); httpd.server_close()


@pytest.mark.parametrize("summary_mode", ["standard", "pro"])
def test_sandbox_long_source_pipeline_never_decodes_movie_and_passes_canonical_promotion(monkeypatch, tmp_path, summary_mode):
    from app.orchestrator.production_execution import ProductionExecutor
    from app.transcription.providers import LocalWhisperProvider
    from app.assembly import pipeline
    from app.production import summary_video
    from app.production import narration

    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _, canonical, before = _long_source_with_real_transcript(tmp_path)
        executor = ProductionExecutor(PID, productions_root=tmp_path / "productions", projects_root=tmp_path / "projects")
        monkeypatch.setattr(web, "_production_executor", lambda pid: executor)
        def forbidden(*args, **kwargs):
            pytest.fail("Sandbox must not call Whisper, decode source clips, or enter the full real assembly pipeline.")
        monkeypatch.setattr(LocalWhisperProvider, "transcribe", forbidden)
        monkeypatch.setattr(pipeline, "assemble_project", forbidden)
        monkeypatch.setattr(summary_video, "_build_intermediate_clips", forbidden)
        monkeypatch.setattr(narration, "get_provider", forbidden)
        timings = {}
        for route, body in [
            ("/transcribe", {"mock_mode": True}),
            (f"/units/{UID}/narration", {"mock_mode": True, "asset_id": AID, "summary_mode": summary_mode}),
            (f"/units/{UID}/narration/synthesize", {"mock_mode": True}),
            (f"/units/{UID}/summary-video", {"mock_mode": True}),
            (f"/units/{UID}/summary-video/promote", {"mock_mode": True}),
        ]:
            started = time.perf_counter()
            status, payload, _ = _post(base, f"/api/productions/{PID}" + route, body)
            timings[route] = time.perf_counter() - started
            assert status in {200, 201}, (route, payload)
            assert timings[route] < 1.0, timings
            if route.endswith("/summary-video"):
                assert payload["validation"]["mock_mode"] is True
                assert payload["validation"]["synthetic_visuals"] is True
                assert payload["output_duration_seconds"] <= 4.2
            if route.endswith("/narration"):
                assert payload["source_transcript_path"] == f"source/{AID}.sandbox.transcript.json"
            if route.endswith("/narration/synthesize"):
                assert payload["audio"]["provider"] == "mock"
                assert payload["audio"]["voice"] == "sandbox-tone"
        assert executor.derive_progress().complete
        assert canonical.read_text(encoding="utf-8") == before
        print("Long-source sandbox timings: " + json.dumps(timings, sort_keys=True))
    finally:
        httpd.shutdown(); httpd.server_close()


@pytest.mark.parametrize("route", ["/source", "/transcribe", f"/source/{AID}/transcript",
                                  f"/units/{UID}/narration", f"/units/{UID}/narration/synthesize",
                                  f"/units/{UID}/summary-video", f"/units/{UID}/summary-video/promote"])
@pytest.mark.parametrize("value", ["true", 1])
def test_mock_flags_require_json_booleans_before_any_provider_or_media_work(monkeypatch, tmp_path, route, value):
    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path)
        status, payload, _ = _post(base, f"/api/productions/{PID}" + route, {"mock_mode": value})
        assert status == 400 and payload["error"] == "INVALID_REQUEST", payload
    finally:
        httpd.shutdown(); httpd.server_close()


def test_sandbox_does_not_substitute_a_bad_explicit_source_path(monkeypatch, tmp_path):
    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path)
        status, _, _ = _post(base, f"/api/productions/{PID}/source", {"mock_mode": True, "source_path": str(tmp_path / "missing.mp4")})
        assert status == 400
        status, _, _ = _post(base, "/api/productions/unknown/source", {"mock_mode": True})
        assert status == 404
        assert not (tmp_path / "productions" / "unknown").exists()
    finally:
        httpd.shutdown(); httpd.server_close()


def test_sandbox_source_sidecar_never_changes_real_intent_media_or_source_pointer(monkeypatch, tmp_path):
    from app.production.storage import compute_content_hash, current_path, load_production, production_source_meta
    from app.production.source import load_sandbox_source
    from app.transcription.providers import LocalWhisperProvider, TranscriptionResult

    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path); real = _ingest(tmp_path, duration=2)
        root = tmp_path / "productions"
        media = root / PID / real["stored_path"]
        paths = [media, current_path(PID, root), production_source_meta(PID, root)]
        before = {path: compute_content_hash(path) for path in paths}
        status, fixture, _ = _post(base, f"/api/productions/{PID}/source", {"mock_mode": True})
        assert status == 201 and fixture["sandbox_fixture"] is True, fixture
        assert fixture["source_asset"]["asset_id"] != real["asset_id"]
        assert fixture["source_asset"]["stored_path"].startswith("source/sandbox/")
        assert {path: compute_content_hash(path) for path in paths} == before
        assert [source.asset_id for source in load_production(PID, root=root).source_assets] == [AID]
        assert load_sandbox_source(PID, root=root).asset_id == fixture["source_asset"]["asset_id"]
        calls = []
        def real_transcribe(self, path, duration):
            calls.append(path)
            return TranscriptionResult("en", [{"text": "Real source", "start": 0, "end": duration}], "local_whisper", "fixture")
        monkeypatch.setattr(LocalWhisperProvider, "transcribe", real_transcribe)
        status, result, _ = _post(base, f"/api/productions/{PID}/transcribe", {"mock_mode": False})
        assert status == 201 and result["asset_id"] == AID and result["provider"] == "local_whisper"
        assert calls == [media]
        assert {path: compute_content_hash(path) for path in paths} == before
    finally:
        httpd.shutdown(); httpd.server_close()


def test_real_registration_reserves_sandbox_identity_without_replacing_either_media(monkeypatch, tmp_path):
    from app.assembly.ffmpeg import probe_media
    from app.production.source import load_sandbox_source
    from app.production.storage import compute_content_hash, load_production

    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path)
        status, fixture, _ = _post(base, f"/api/productions/{PID}/source", {"mock_mode": True})
        assert status == 201, fixture
        sandbox = load_sandbox_source(PID, root=tmp_path / "productions")
        sandbox_media = tmp_path / "productions" / PID / sandbox.stored_path
        before = compute_content_hash(sandbox_media)
        real = _ingest(tmp_path, duration=2)
        assert real["asset_id"] != sandbox.asset_id
        assert compute_content_hash(sandbox_media) == before
        assert probe_media(sandbox_media).has_audio
        assert [source.asset_id for source in load_production(PID, root=tmp_path / "productions").source_assets] == [real["asset_id"]]
    finally:
        httpd.shutdown(); httpd.server_close()


@pytest.mark.parametrize("route", ["/transcribe", "/source/src-002/transcript"])
def test_real_transcription_skips_legacy_video_only_sandbox_without_touching_it(monkeypatch, tmp_path, route):
    from app.assembly.ffmpeg import run_ffmpeg, probe_media
    from app.production.models import SourceAsset
    from app.production.storage import compute_content_hash, load_production, save_revision
    from app.transcription.providers import LocalWhisperProvider, TranscriptionResult

    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path); real = _ingest(tmp_path, duration=2)
        root = tmp_path / "productions"
        legacy = root / PID / "source" / "src-002.mp4"
        run_ffmpeg(["-y", "-f", "lavfi", "-i", "color=c=purple:s=320x180:r=12:d=4",
                    "-an", "-c:v", "libx264", "-threads", "1", "-preset", "ultrafast", str(legacy)], "legacy video-only fixture")
        production = load_production(PID, root=root)
        asset = SourceAsset(asset_id="src-002", stored_path="source/src-002.mp4", original_filename="sandbox-summary-fixture.mp4",
                            source_kind="sandbox_fixture", duration=4, width=320, height=180, video_codec="h264",
                            sha256=compute_content_hash(legacy), file_size=legacy.stat().st_size, ingested_at=production.created_at)
        save_revision(production.model_copy(update={"plan_version": production.plan_version + 1,
                                                   "source_assets": [*production.source_assets, asset]}), root=root)
        before = compute_content_hash(legacy)
        assert not probe_media(legacy).has_audio
        calls = []
        def real_transcribe(self, path, duration):
            calls.append(path.name)
            return TranscriptionResult("en", [{"text": "Last real source", "start": 0, "end": duration}], "local_whisper", "fixture")
        monkeypatch.setattr(LocalWhisperProvider, "transcribe", real_transcribe)
        status, payload, _ = _post(base, f"/api/productions/{PID}" + route, {"mock_mode": False})
        assert status == 201 and payload["asset_id"] == real["asset_id"], payload
        assert payload["source_asset"]["source_kind"] == "local_file"
        if "src-002" in route:
            assert payload["requested_asset_id"] == "src-002" and payload["source_fallback"] is True
        assert calls == ["src-001.mp4"]
        assert compute_content_hash(legacy) == before
        assert not (legacy.parent / "src-002.transcript.json").exists()
    finally:
        httpd.shutdown(); httpd.server_close()


FFMPEG_BANNER = (
    "ffmpeg version 8.0-full_build Copyright (c) FFmpeg developers\n"
    "  built with gcc 15.1.0\n"
    "  configuration: --enable-gpl --enable-version3 --enable-libx264 --enable-libmp3lame\n"
    "  libavutil      60.  8.100 / 60.  8.100\n"
    "  libavcodec     62. 11.100 / 62. 11.100\n"
    "Input #0, mov,mp4, from 'C:/private/source.mp4':\n"
    "  Metadata:\n    encoder : Lavf62.3.100\n"
    "  Duration: 00:00:04.00, start: 0.000000\n"
    "  Stream #0:0: Video: h264, yuv420p, 320x180\n"
)


@pytest.mark.parametrize("reason", ["Output file does not contain any stream", "Error opening input files: Permission denied"])
@pytest.mark.parametrize("route", ["/transcribe", f"/source/{AID}/transcript"])
def test_transcription_ffmpeg_errors_are_short_and_banner_free(monkeypatch, tmp_path, reason, route):
    from app.transcription.providers import LocalWhisperProvider
    from app.production.storage import transcript_path_for

    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path); _ingest(tmp_path, duration=1)
        def failure(*args, **kwargs):
            raise RuntimeError("TRANSCRIPTION_FAILED: Failed to load audio:\n" + FFMPEG_BANNER + reason + "\nConversion failed!\n")
        monkeypatch.setattr(LocalWhisperProvider, "transcribe", failure)
        status, payload, _ = _post(base, f"/api/productions/{PID}" + route, {})
        assert status in {422, 500}, payload
        assert not any(noise in payload["message"] for noise in ["ffmpeg version", "configuration:", "--enable-", "libavcodec", "C:/private", "Stream #"])
        assert len(payload["message"]) < 400 and "\n" not in payload["message"]
        if "does not contain" in reason:
            assert "no audio track" in payload["message"] and "Mock / sandbox" in payload["message"]
        else:
            assert "Permission denied" in payload["message"]
        assert not transcript_path_for(PID, AID, root=tmp_path / "productions").exists()
    finally:
        httpd.shutdown(); httpd.server_close()


def test_video_without_audio_fails_before_loading_whisper_and_does_not_enable_mock(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    from app.assembly.ffmpeg import run_ffmpeg
    from app.production.storage import transcript_path_for

    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path)
        video = tmp_path / "silent-real.mp4"
        run_ffmpeg(["-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=12:d=1", "-an",
                    "-c:v", "libx264", "-threads", "1", "-preset", "ultrafast", str(video)], "silent real source")
        status, _, _ = _post(base, f"/api/productions/{PID}/source", {"source_path": str(video)})
        assert status == 201
        monkeypatch.setitem(sys.modules, "whisper", SimpleNamespace(load_model=lambda *args: pytest.fail("Whisper must not load for audio-less media")))
        status, payload, _ = _post(base, f"/api/productions/{PID}/transcribe", {})
        assert status == 422 and payload["error"] == "SOURCE_AUDIO_MISSING", payload
        assert "Source video has no audio track" in payload["message"]
        assert "configuration:" not in payload["message"]
        assert not transcript_path_for(PID, AID, root=tmp_path / "productions").exists()
        assert not list((tmp_path / "productions" / PID / "source").glob("*.sandbox.transcript.json"))
    finally:
        httpd.shutdown(); httpd.server_close()


def test_browser_diagnostic_discards_ffmpeg_build_banner_but_keeps_terminal_reason():
    message = web._browser_safe_diagnostic(FFMPEG_BANNER + "Error opening input file C:/private/source.mp4: Permission denied\nConversion failed!")
    assert "Permission denied" in message
    assert not any(noise in message for noise in ["ffmpeg version", "configuration:", "--enable-", "libavcodec", "C:/private", "Stream #"])


def test_ffmpeg_exception_uses_full_stderr_instead_of_its_truncated_banner_preview():
    from app.assembly.exceptions import FFmpegExecutionError
    from app.transcription.diagnostics import media_error_reason

    exception = FFmpegExecutionError("ffmpeg command", 1, FFMPEG_BANNER * 3 + "Error opening output file: Permission denied\nConversion failed!")
    assert "Permission denied" not in str(exception), "fixture must exercise the truncated preview"
    for message in (media_error_reason(exception), web._browser_safe_diagnostic(exception)):
        assert "Permission denied" in message
        assert len(message) < 400
        assert not any(noise in message for noise in ["ffmpeg version", "configuration:", "--enable-", "libavcodec", "Stream #"])


def test_legacy_fixture_without_real_fallback_is_upgraded_with_audio_only_in_sandbox_sidecar(monkeypatch, tmp_path):
    from app.assembly.ffmpeg import probe_media, run_ffmpeg
    from app.production.models import SourceAsset
    from app.production.storage import compute_content_hash, current_path, load_production, save_revision
    from app.transcription.providers import LocalWhisperProvider, TranscriptionResult

    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        production = _make_prod(tmp_path)
        root = tmp_path / "productions"
        legacy = root / PID / "source" / "src-001.mp4"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        run_ffmpeg(["-y", "-f", "lavfi", "-i", "color=c=purple:s=320x180:r=12:d=4", "-an",
                    "-c:v", "libx264", "-threads", "1", "-preset", "ultrafast", str(legacy)], "legacy video-only source")
        asset = SourceAsset(asset_id=AID, stored_path="source/src-001.mp4", source_kind="sandbox_fixture",
                            original_filename="sandbox-summary-fixture.mp4", duration=4, width=320, height=180,
                            video_codec="h264", sha256=compute_content_hash(legacy), ingested_at=production.created_at)
        save_revision(production.model_copy(update={"plan_version": 2, "source_assets": [asset]}), root=root)
        intent = current_path(PID, root)
        before = {path: compute_content_hash(path) for path in (intent, legacy)}
        calls = []
        def real_transcribe(self, path, duration):
            info = probe_media(path)
            assert info.has_video and info.has_audio and info.audio_codec == "aac"
            assert path.parent.name == "sandbox"
            calls.append(path)
            return TranscriptionResult("en", [{"text": "Real STT fixture response", "start": 0, "end": duration}], "local_whisper", "fixture")
        monkeypatch.setattr(LocalWhisperProvider, "transcribe", real_transcribe)
        for _ in range(2):
            status, payload, _ = _post(base, f"/api/productions/{PID}/transcribe", {"mock_mode": False})
            assert status == 201 and payload["provider"] == "local_whisper" and payload["mock_mode"] is False, payload
            assert payload["source_asset"]["stored_path"].startswith("source/sandbox/")
        assert len(calls) == 2 and calls[0] == calls[1]
        assert {path: compute_content_hash(path) for path in before} == before
        assert not probe_media(legacy).has_audio
        assert [source.asset_id for source in load_production(PID, root=root).source_assets] == [AID]
    finally:
        httpd.shutdown(); httpd.server_close()


def test_isolated_sandbox_full_pipeline_preserves_real_source_and_transcript_through_promotion(monkeypatch, tmp_path):
    from app.orchestrator.production_execution import ProductionExecutor
    from app.production.storage import compute_content_hash, current_path, load_production
    from app.assembly.ffmpeg import probe_media

    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        real, transcript, _ = _long_source_with_real_transcript(tmp_path)
        root = tmp_path / "productions"
        media = root / PID / real.stored_path
        protected = {path: compute_content_hash(path) for path in (media, transcript, current_path(PID, root))}
        executor = ProductionExecutor(PID, productions_root=root, projects_root=tmp_path / "projects")
        monkeypatch.setattr(web, "_production_executor", lambda pid: executor)
        status, payload, _ = _post(base, f"/api/productions/{PID}/source", {"mock_mode": True})
        assert status == 201, payload
        sandbox = payload["source_asset"]
        assert sandbox["asset_id"] != real.asset_id and sandbox["stored_path"].startswith("source/sandbox/")
        assert probe_media(root / PID / sandbox["stored_path"]).has_audio
        for route, body in [
            ("/transcribe", {"mock_mode": True}),
            (f"/units/{UID}/narration", {"mock_mode": True}),
            (f"/units/{UID}/narration/synthesize", {"mock_mode": True}),
            (f"/units/{UID}/summary-video", {"mock_mode": True}),
            (f"/units/{UID}/summary-video/promote", {"mock_mode": True}),
        ]:
            status, payload, _ = _post(base, f"/api/productions/{PID}" + route, body)
            assert status in {200, 201}, (route, payload)
            if route.endswith("/transcribe"):
                assert payload["asset_id"] == sandbox["asset_id"]
            if route.endswith("/narration"):
                assert payload["source_asset_id"] == sandbox["asset_id"]
                assert payload["source_media_path"] == sandbox["stored_path"]
            assert {path: compute_content_hash(path) for path in protected} == protected
        assert executor.derive_progress().complete
        assert [source.asset_id for source in load_production(PID, root=root).source_assets] == [real.asset_id]
        binding = executor.discover_bindings()[UID]
        assert binding.manifest.source_summary_handoff.source_asset_id == sandbox["asset_id"]
    finally:
        httpd.shutdown(); httpd.server_close()


@pytest.mark.parametrize("message", ["FFmpeg command failed: ffmpeg version 8.0-full_build", "TRANSCRIPTION_FAILED: configuration: --enable-gpl --enable-libx264"])
def test_even_truncated_or_single_line_ffmpeg_banners_are_not_browser_errors(message):
    result = web._browser_safe_diagnostic(message)
    assert result and "Media processing failed" in result
    assert not any(noise in result for noise in ("ffmpeg version", "configuration:", "--enable-"))


def test_browser_source_upload_reuses_intake_without_client_path(monkeypatch, tmp_path):
    from app.production.storage import compute_content_hash, load_production

    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _make_prod(tmp_path)
        video = tmp_path / "picked.mp4"
        create_test_video(video, duration=2, width=320, height=180, fps=24)
        request = urllib.request.Request(base + f"/api/productions/{PID}/source/upload", data=video.read_bytes(),
                                         method="POST", headers={"Content-Type": "application/octet-stream", "X-AVM-Filename": "My%20source.mp4"})
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 201
            payload = json.loads(response.read())
        asset = payload["source_asset"]
        assert asset["original_filename"] == "My source.mp4" and asset["source_kind"] == "browser_upload"
        stored = tmp_path / "productions" / PID / asset["stored_path"]
        assert compute_content_hash(stored) == compute_content_hash(video)
        assert not list(stored.parent.glob(".upload-*"))
        assert load_production(PID, root=tmp_path / "productions").source_assets[0].original_filename == "My source.mp4"
    finally:
        httpd.shutdown(); httpd.server_close()


@pytest.mark.parametrize("filename,body,length", [
    ("../escape.mp4", b"video", 5), ("C:\\fakepath\\source.mp4", b"video", 5),
    ("source.txt", b"video", 5), ("source.mp4", b"", 0),
    ("source.mp4", b"short", 100), ("source.mp4", b"", 2 * 1024 * 1024 * 1024 + 1),
])
def test_upload_limits_names_and_truncation_never_register_invalid_source(monkeypatch, tmp_path, filename, body, length):
    from app.production.source import register_uploaded_source
    from app.production.storage import load_production

    _make_prod(tmp_path)
    status, _ = register_uploaded_source(PID, filename, io.BytesIO(body), length, root=tmp_path / "productions")
    assert status in {400, 413}
    assert not load_production(PID, root=tmp_path / "productions").source_assets
    assert not list((tmp_path / "productions" / PID).rglob(".upload-*"))


@pytest.mark.parametrize("failure", ["bad_grounding", "bad_json", "missing_configuration", "ineligible_model"])
def test_standard_real_http_rejects_invalid_plan_without_persistence(monkeypatch, tmp_path, failure):
    from app.content.model_registry import CanonicalModel, MODEL_REGISTRY, ModelCapabilities
    from app.production.storage import narration_artifact_path
    from tests.track9_narration_fakes import install_standard_provider, standard_response

    data = standard_response()
    if failure == "bad_grounding":
        data["beats"][0]["source_refs"][0]["transcript_segment_ids"] = [999]
    provider, factory_calls, binding = install_standard_provider(
        monkeypatch, data=data, **({"raw": "malformed model output"} if failure == "bad_json" else {}),
    )
    # This test exhausts ONE configured route; other canonical CodeCraft models
    # share its credential names but are not implemented by this exact-wire fake.
    monkeypatch.setattr("app.content.model_registry.list_models", lambda: (MODEL_REGISTRY["qwen-3.8-flash"],))
    if failure == "missing_configuration":
        for key in binding.config_keys:
            monkeypatch.delenv(key, raising=False)
    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _standard_real_source(tmp_path)
        body = {"summary_mode": "standard", "mock_mode": False}
        if failure == "ineligible_model":
            model = CanonicalModel(id="standard-no-json", display_name="No JSON", family="fixture", capabilities=ModelCapabilities(json_=False))
            monkeypatch.setitem(MODEL_REGISTRY, model.id, model)
            body.update(selection_mode="explicit", summary_understanding_model_id=model.id)
        status, payload, _ = _post(base, f"/api/productions/{PID}/units/{UID}/narration", body)
        assert status == (422 if failure in ("missing_configuration", "ineligible_model") else 503), payload
        assert payload["error"] == ("MODEL_NOT_ELIGIBLE" if status == 422 else "NARRATION_PROVIDER_TEMPORARILY_UNAVAILABLE")
        assert not narration_artifact_path(PID, UID, root=tmp_path / "productions").exists()
        if failure in ("missing_configuration", "ineligible_model"):
            assert factory_calls == provider.calls == []
        else:
            assert provider.json_calls == len(provider.calls) == 2
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_capabilities_standard_requires_json_without_provider_leakage(monkeypatch, tmp_path):
    from app.content.model_registry import CanonicalModel, ModelCapabilities

    model = CanonicalModel(id="text-without-json", display_name="Text without JSON", family="fixture", capabilities=ModelCapabilities(json_=False))
    monkeypatch.setattr("app.content.model_registry.list_models", lambda: (model,))
    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        status, payload, _ = _get(base, "/api/capabilities")
        assert status == 200
        assert payload["ai_summary"]["understanding_models"] == []
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_standard_real_http_retains_validated_source_segment_ids(monkeypatch, tmp_path):
    from app.production.storage import load_narration_artifact
    from tests.track9_narration_fakes import install_standard_provider, standard_response

    data = standard_response()
    for index, beat in enumerate(data["beats"], 1):
        beat["source_refs"][0]["source_segment_id"] = f"seg-{index:03d}"
    provider, _, _ = install_standard_provider(monkeypatch, data=data)
    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _standard_real_source(tmp_path)
        segment_path = f"/api/productions/{PID}/source/{AID}/segments"
        status, payload, _ = _post(base, segment_path, {"segments": [
            {"start_seconds": 10, "end_seconds": 20, "transcript_segment_ids": [1]},
            {"start_seconds": 90, "end_seconds": 100, "transcript_segment_ids": [9]},
        ]})
        assert status == 200, payload
        status, payload, _ = _post(base, f"/api/productions/{PID}/units/{UID}/narration", {
            "summary_mode": "standard", "mock_mode": False, "target_duration_seconds": 30,
        })
        assert status == 201, payload
        persisted = load_narration_artifact(PID, UID, root=tmp_path / "productions")
        assert [beat.source_segment_ids for beat in persisted.beats] == [["seg-001"], ["seg-002"]]
        assert [ref.source_segment_id for ref in persisted.source_refs] == ["seg-001", "seg-002"]
        evidence = json.loads(provider.calls[0]["prompt"].split("evidence:\n", 1)[1].split("\nRequired JSON schema:", 1)[0])
        assert [segment["segment_id"] for segment in evidence["source_segments"]] == ["seg-001", "seg-002"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_standard_real_http_auto_recovers_from_partial_json_and_regeneration_returns_200(monkeypatch, tmp_path):
    from tests.test_track9_standard_real import install_auto_pool
    from app.production.storage import load_narration_artifact
    candidates, providers = install_auto_pool(monkeypatch)
    providers[0].raw = '{"summary":"unfinished","beats":['
    base, httpd, _ = _server(monkeypatch, tmp_path)
    try:
        _standard_real_source(tmp_path)
        route = f"/api/productions/{PID}/units/{UID}/narration"
        body = {"summary_mode": "standard", "mock_mode": False}
        status, payload, _ = _post(base, route, body)
        assert status == 201, payload
        assert payload["summary_understanding_model_id"] == candidates[1].id
        assert payload["summary_selection_mode"] == "auto" and payload["summary_failover_used"] is True
        assert [len(provider.calls) for provider in providers] == [2, 1]
        status, payload, _ = _post(base, route, body)
        assert status == 200, payload
        saved = load_narration_artifact(PID, UID, root=tmp_path / "productions")
        assert saved.narration_text == payload["narration_text"]
        assert saved.summary_understanding_model_id == candidates[1].id
        snapshot = saved.model_dump()
        providers[1].raw = "unparseable private-source content"
        status, payload, _ = _post(base, route, body)
        assert status == 503, payload
        assert "private-source" not in json.dumps(payload)
        assert load_narration_artifact(PID, UID, root=tmp_path / "productions").model_dump() == snapshot
    finally:
        httpd.shutdown(); httpd.server_close()
