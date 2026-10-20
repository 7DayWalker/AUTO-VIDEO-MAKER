"""Track 8 transcription A."""
import json
import threading
import urllib.request
from pathlib import Path
import pytest
from app.production.storage import load_production, transcript_path_for
from app.web import server as web
from app.production.source import register_source
from app.transcription.transcribe import transcribe_source, load_transcript
from app.production.models import SourceTranscript
from app.production.segments import load_segments, save_segments
from app.production.storage import segments_path_for
def _make_prod(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,p=web.create_production_payload({"mode":"single_video","creation_method":"local","title":"T8 trans test","brief":"need transcript","output_intent":"youtube_shorts","target_duration":60,"scene_count":6})
    assert s==201
    return p["production_id"]
def _synth_video(path: Path, width=320, height=240, seconds=2):
    from app.assembly.ffmpeg import run_ffmpeg
    path.parent.mkdir(parents=True, exist_ok=True)
    args=["-y","-f","lavfi","-i",f"color=c=red:s={width}x{height}:r=25:d={seconds}","-c:v","libx264","-pix_fmt","yuv420p","-t",str(seconds),str(path)]
    run_ffmpeg(args,"synth video")
def test_single_video_still_green(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,_=web.create_production_payload({"mode":"single_video","creation_method":"local","title":"Hello world","brief":"a short brief for single","output_intent":"youtube_shorts"})
    assert s==201
def test_unit_count_still_green(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,_=web.create_production_payload({"mode":"unit_count","creation_method":"local","title":"Batch","brief":"batch brief","output_intent":"youtube_shorts","unit_count":2})
    assert s==201
def test_episodic_still_green(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,_=web.create_production_payload({"mode":"unit_count","creation_method":"local","title":"Series S","brief":"series brief","output_intent":"youtube_shorts","unit_count":2,"unit_type":"episode","episodes":[{"title":"E1"},{"title":"E2"}]})
    assert s==201
def test_source_ingestion_still_green(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    s,_=register_source(pid,str(vid),root=tmp_path/"productions")
    assert s==201
def test_valid_transcription(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    s,_=register_source(pid,str(vid),root=tmp_path/"productions")
    assert s==201
    s2,pay=transcribe_source(pid,"src-001",root=tmp_path/"productions",provider="mock")
    assert s2==201
    assert pay["asset_id"]=="src-001"
    assert pay["provider"]=="mock"
    assert pay["segment_count"]>=1
def test_transcript_created(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid)
    register_source(pid,str(vid),root=tmp_path/"productions")
    transcribe_source(pid,"src-001",root=tmp_path/"productions",provider="mock")
    tr=load_transcript(pid,"src-001",root=tmp_path/"productions")
    assert isinstance(tr, SourceTranscript)
def test_asset_id_association(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid)
    register_source(pid,str(vid),root=tmp_path/"productions")
    _,pay=transcribe_source(pid,"src-001",root=tmp_path/"productions",provider="mock")
    assert pay["asset_id"]=="src-001"
    tr=load_transcript(pid,"src-001",root=tmp_path/"productions")
    assert tr.asset_id=="src-001"
def test_segments_persist(tmp_path, monkeypatch):
    from app.production.storage import segments_path_for
    pid=_make_prod(tmp_path, monkeypatch)
    root=tmp_path/"productions"
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    register_source(pid,str(vid),root=root)
    transcribe_source(pid,"src-001",root=root,provider="mock")
    tr=load_transcript(pid,"src-001",root=root)
    payload={"segments":[{"start_seconds":0,"end_seconds":1,"title":" Intro ","premise":"A setup", "transcript_segment_ids":[0]}]}
    status,saved=save_segments(pid,"src-001",payload,root=root)
    assert status==200
    assert saved["segments"][0]["segment_id"]=="seg-001"
    assert saved["segments"][0]["title"]=="Intro"
    assert segments_path_for(pid,"src-001",root=root).is_file()
    status,reloaded=load_segments(pid,"src-001",root=root)
    assert status==200 and reloaded["segments"]==saved["segments"]
    assert len(tr.segments)>=1


def test_segment_validation_rejects_out_of_bounds_and_transcript_refs(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    root=tmp_path/"productions"
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    register_source(pid,str(vid),root=root)
    transcribe_source(pid,"src-001",root=root,provider="mock")
    status,_=save_segments(pid,"src-001",{"segments":[{"start_seconds":1,"end_seconds":3}]},root=root)
    assert status==400
    status,_=save_segments(pid,"src-001",{"segments":[{"start_seconds":0,"end_seconds":1,"transcript_segment_ids":[999]}]},root=root)
    assert status==400


def test_segments_http_post_and_get(tmp_path, monkeypatch):
    from urllib.error import HTTPError
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    pid=_make_prod(tmp_path,monkeypatch)
    root=tmp_path/"productions"
    video=tmp_path/"src.mp4"
    _synth_video(video,seconds=2)
    register_source(pid,str(video),root=root)
    transcribe_source(pid,"src-001",root=root,provider="mock")
    server=web.ThreadingHTTPServer(("127.0.0.1",0),web.AVMHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    url=f"http://127.0.0.1:{server.server_address[1]}/api/productions/{pid}/source/src-001/segments"
    try:
        req=urllib.request.Request(url,data=json.dumps({"segments":[{"start_seconds":0,"end_seconds":1}]}).encode(),headers={"Content-Type":"application/json"},method="POST")
        with urllib.request.urlopen(req) as response:
            assert response.status==200
        with urllib.request.urlopen(url) as response:
            body=json.loads(response.read())
        assert body["segments"][0]["segment_id"]=="seg-001"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
def test_segment_text_and_timestamps(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=3)
    register_source(pid,str(vid),root=tmp_path/"productions")
    _,pay=transcribe_source(pid,"src-001",root=tmp_path/"productions",provider="mock")
    for seg in pay["segments"]:
        assert seg["text"].strip()
        assert 0 <= seg["start"] < seg["end"]
