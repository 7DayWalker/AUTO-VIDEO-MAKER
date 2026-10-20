import hashlib, json
from pathlib import Path
import pytest
from app.production.models import Production
from app.production.storage import load_production
from app.web import server as web
from app.production.source import register_source
def _make_prod(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,p=web.create_production_payload({"mode":"single_video","creation_method":"local","title":"Src test","brief":"need source ingestion","output_intent":"youtube_shorts","target_duration":60,"scene_count":6})
    assert s==201
    return p["production_id"]
def _synth_video(path: Path, width=320, height=240, seconds=2):
    from app.assembly.ffmpeg import run_ffmpeg
    path.parent.mkdir(parents=True, exist_ok=True)
    args=["-y","-f","lavfi","-i",f"color=c=red:s={width}x{height}:r=25:d={seconds}","-c:v","libx264","-pix_fmt","yuv420p","-t",str(seconds),str(path)]
    run_ffmpeg(args,"synth video")
def test_unsupported_extension_rejected(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    f=tmp_path/"note.txt"
    f.write_text("hello",encoding="utf-8")
    s,_=register_source(pid,str(f),root=tmp_path/"productions")
    assert s==400
def test_missing_file_rejected(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    s,_=register_source(pid,str(tmp_path/"nope.mp4"),root=tmp_path/"productions")
    assert s==400
def test_directory_rejected(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    d=tmp_path/"adir"
    d.mkdir()
    s,_=register_source(pid,str(d),root=tmp_path/"productions")
    assert s==400
def test_null_byte_rejected(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid)
    s,_=register_source(pid,str(vid)+"\x00",root=tmp_path/"productions")
    assert s==400
def test_non_video_rejected(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    fake=tmp_path/"fake.mp4"
    fake.write_bytes(b"not a video at all"*100)
    s,_=register_source(pid,str(fake),root=tmp_path/"productions")
    assert s==400
def test_duration_truthful(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=3)
    s,pay=register_source(pid,str(vid),root=tmp_path/"productions")
    assert s==201
    assert 2.5 <= pay["source_asset"]["duration"] <= 4.0
def test_dimensions_recorded(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,width=320,height=240,seconds=2)
    s,pay=register_source(pid,str(vid),root=tmp_path/"productions")
    assert s==201
    assert pay["source_asset"]["width"]==320
    assert pay["source_asset"]["height"]==240
def test_codec_recorded(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    s,pay=register_source(pid,str(vid),root=tmp_path/"productions")
    assert s==201
    assert pay["source_asset"]["video_codec"] is not None
