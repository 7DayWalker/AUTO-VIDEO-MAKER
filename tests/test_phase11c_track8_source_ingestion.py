"""Track 8 V1 — Source ingestion & registration (hermetic, offline)."""
import hashlib, json
from pathlib import Path
import pytest
from app.production.models import Production
from app.production.storage import load_production
from app.web import server as web
from app.production.source import register_source
def _make_prod(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    s, p = web.create_production_payload({"mode":"single_video","creation_method":"local","title":"Src test","brief":"need source ingestion","output_intent":"youtube_shorts","target_duration":60,"scene_count":6})
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
    s,p=web.create_production_payload({"mode":"unit_count","creation_method":"local","title":"Batch","brief":"batch brief","output_intent":"youtube_shorts","unit_count":2})
    assert s==201
def test_episodic_still_green(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,p=web.create_production_payload({"mode":"unit_count","creation_method":"local","title":"Series S","brief":"series brief","output_intent":"youtube_shorts","unit_count":2,"unit_type":"episode","episodes":[{"title":"E1"},{"title":"E2"}]})
    assert s==201
def test_valid_local_source_accepted(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src_input.mp4"
    _synth_video(vid)
    status,payload=register_source(pid,str(vid),root=tmp_path/"productions")
    assert status==201
    assert payload["source_asset"]["asset_id"]=="src-001"
    prod=load_production(pid,root=tmp_path/"productions")
    assert prod.source_assets is not None and len(prod.source_assets)==1
