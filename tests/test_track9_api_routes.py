"""Track 9 HTTP /api surface -- hermetic mock and real paths, no live calls."""
from __future__ import annotations
import json, threading, urllib.request, urllib.error
import io
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
        assert s==201, payload2
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
        status, registered, _ = _post(base, f"/api/productions/{pid}/source", {"mock_mode": True})
        assert status == 201 and registered["sandbox_fixture"] is True, registered
        source = registered["source_asset"]
        assert source["source_kind"] == "sandbox_fixture" and source["original_filename"] == "sandbox-summary-fixture.mp4"
        media = tmp_path / "productions" / pid / source["stored_path"]
        assert media.is_file() and media.stat().st_size > 0
        assert load_production(pid, root=tmp_path / "productions").source_assets[0].source_kind == "sandbox_fixture"
        assert not list(media.parent.glob(".sandbox-*"))
        for route, body in [
            (f"/source/{source['asset_id']}/transcript", {"provider": "mock", "mock_mode": True}),
            ("/units/unit-001/narration", {"mock_mode": True, "asset_id": source["asset_id"]}),
            ("/units/unit-001/narration/synthesize", {"mock_mode": True}),
            ("/units/unit-001/summary-video", {}),
            ("/units/unit-001/summary-video/promote", {}),
        ]:
            status, payload, _ = _post(base, f"/api/productions/{pid}" + route, body)
            assert status in {200, 201}, (route, payload)
        assert executor.derive_progress().complete
        manifest = next(executor.projects_root.glob("*/manifest.json"))
        accepted = json.loads(manifest.read_text(encoding="utf-8"))
        assert accepted["source_summary_handoff"]
        assert accepted["publication"] is None and accepted["tiktok_publication"] is None
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
        assert status == (422 if failure in ("missing_configuration", "ineligible_model") else 400), payload
        assert payload["error"] == ("MODEL_NOT_ELIGIBLE" if status == 422 else "INVALID_REQUEST")
        assert not narration_artifact_path(PID, UID, root=tmp_path / "productions").exists()
        if failure in ("missing_configuration", "ineligible_model"):
            assert factory_calls == provider.calls == []
        else:
            assert provider.json_calls == len(provider.calls) == 1
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
