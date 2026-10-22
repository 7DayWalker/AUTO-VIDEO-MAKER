"""Track 9 HTTP /api surface -- hermetic (mock mode, no live calls)."""
from __future__ import annotations
import json, threading, urllib.request, urllib.error
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
