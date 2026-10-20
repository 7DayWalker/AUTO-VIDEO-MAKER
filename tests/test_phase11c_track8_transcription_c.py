"""Track 8 transcription C."""
import json
from pathlib import Path
import pytest
from app.production.storage import load_production, transcript_path_for
from app.web import server as web
from app.production.source import register_source
from app.transcription.transcribe import transcribe_source, load_transcript
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
def test_reload_survives(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid)
    register_source(pid,str(vid),root=tmp_path/"productions")
    transcribe_source(pid,"src-001",root=tmp_path/"productions",provider="mock")
    tr=load_transcript(pid,"src-001",root=tmp_path/"productions")
    tr2=load_transcript(pid,"src-001",root=tmp_path/"productions")
    assert tr.model_dump()==tr2.model_dump()
def test_existing_prod_without_transcript_still_loads(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    prod=load_production(pid,root=tmp_path/"productions")
    assert prod.source_assets is None or prod.source_assets==[]
def test_existing_source_without_transcript_still_loads(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid)
    register_source(pid,str(vid),root=tmp_path/"productions")
    prod=load_production(pid,root=tmp_path/"productions")
    assert len(prod.source_assets)==1
    assert not transcript_path_for(pid,"src-001",root=tmp_path/"productions").exists()
def test_null_byte_rejected(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    s,_=transcribe_source(pid,"src-001\x00",root=tmp_path/"productions",provider="mock")
    assert s==400
def test_missing_source_asset_rejected(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid)
    register_source(pid,str(vid),root=tmp_path/"productions")
    s,_=transcribe_source(pid,"src-002",root=tmp_path/"productions",provider="mock")
    assert s==404
def test_missing_source_file_rejected(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid)
    register_source(pid,str(vid),root=tmp_path/"productions")
    (tmp_path/"productions"/pid/"source"/"src-001.mp4").unlink()
    s,_=transcribe_source(pid,"src-001",root=tmp_path/"productions",provider="mock")
    assert s==404
def test_stt_unavailable_truthful(tmp_path, monkeypatch):
    # Real Whisper is now installed; simulate unavailability via monkeypatch
    import app.transcription.providers as prov
    orig = prov.LocalWhisperProvider.transcribe
    def fail(self, media_path, duration):
        raise RuntimeError("STT_ENGINE_UNAVAILABLE: simulated missing engine")
    monkeypatch.setattr(prov.LocalWhisperProvider, "transcribe", fail)
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid)
    register_source(pid,str(vid),root=tmp_path/"productions")
    s,payload=transcribe_source(pid,"src-001",root=tmp_path/"productions",provider="local_whisper")
    assert s==503
    assert payload["error"]=="STT_ENGINE_UNAVAILABLE"
    assert not transcript_path_for(pid,"src-001",root=tmp_path/"productions").exists()
