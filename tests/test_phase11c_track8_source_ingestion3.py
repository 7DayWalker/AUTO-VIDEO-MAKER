import hashlib, json
from pathlib import Path
import pytest
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
def test_sha256_recorded(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    s,pay=register_source(pid,str(vid),root=tmp_path/"productions")
    assert s==201
    assert len(pay["source_asset"]["sha256"])==64
def test_stored_artifact_exists_and_hash_matches(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    s,pay=register_source(pid,str(vid),root=tmp_path/"productions")
    assert s==201
    rel=pay["stored_path"]
    dst=tmp_path/"productions"/pid/rel
    assert dst.exists()
    h=hashlib.sha256(dst.read_bytes()).hexdigest()
    assert h==pay["source_asset"]["sha256"]
def test_serialization_survives(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    register_source(pid,str(vid),root=tmp_path/"productions")
    raw=json.loads((tmp_path/"productions"/pid/"production.json").read_text(encoding="utf-8"))
    assert "source_assets" in raw and len(raw["source_assets"])==1
def test_reload_survives(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    _,pay=register_source(pid,str(vid),root=tmp_path/"productions")
    prod=load_production(pid,root=tmp_path/"productions")
    assert prod.source_assets[0].sha256==pay["source_asset"]["sha256"]
    assert prod.source_assets[0].stored_path=="source/src-001.mp4"
def test_relative_paths_only(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    _,pay=register_source(pid,str(vid),root=tmp_path/"productions")
    assert not pay["stored_path"].startswith(("/","\\"))
    raw=json.loads((tmp_path/"productions"/pid/"production.json").read_text(encoding="utf-8"))
    assert raw["source_assets"][0]["stored_path"]=="source/src-001.mp4"
def test_existing_productions_without_source_still_load(tmp_path, monkeypatch):
    monkeypatch.setattr(web,"PRODUCTIONS_DIR",tmp_path/"productions")
    s,p=web.create_production_payload({"mode":"single_video","creation_method":"local","title":"Legacy","brief":"legacy brief","output_intent":"youtube_shorts"})
    assert s==201
    prod=load_production(p["production_id"],root=tmp_path/"productions")
    assert prod.source_assets is None
def test_repeated_registration_deterministic(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    s1,p1=register_source(pid,str(vid),root=tmp_path/"productions")
    assert s1==201 and p1["source_asset"]["asset_id"]=="src-001"
    s2,p2=register_source(pid,str(vid),root=tmp_path/"productions")
    assert s2==201 and p2["source_asset"]["asset_id"]=="src-002"
    prod=load_production(pid,root=tmp_path/"productions")
    assert len(prod.source_assets)==2
def test_ffprobe_unavailable_truthful(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    import app.assembly.ffmpeg as ff
    monkeypatch.setattr(ff,"resolve_ffprobe",lambda: None)
    s,payload=register_source(pid,str(vid),root=tmp_path/"productions")
    assert s==503
    assert payload["error"]=="FFPROBE_UNAVAILABLE"
def test_no_second_engine():
    import app.production.source as src
    assert not hasattr(src,"SourceExecutor")
    import app.web.server as srv
    assert not hasattr(srv,"SourceExecutor")
    assert not hasattr(srv,"RepurposingExecutor")
def test_no_transcription_code():
    # Updated for Track 8 transcription increment: transcription is now expected.
    import pathlib
    assert pathlib.Path("app/transcription").exists()
def test_no_segment_planner():
    # Track 8 Segment Analysis (item 3) is implemented: segments are operator-selected
    # ranges backed by transcript, validated and atomically persisted. There is no
    # AI ranking/hook scoring/Story Intelligence — selection remains manual.
    import pathlib
    assert pathlib.Path("app/production/segments.py").exists()
    from app.production import segments as segmod
    src = pathlib.Path(segmod.__file__).read_text(encoding="utf-8").lower()
    import ast
    tree = ast.parse(src)
    mods = {n.module.lower() if n.module else "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {n.names[0].name.lower() for n in ast.walk(tree) if isinstance(n, ast.Import)}
    for kw in ["openai", "anthropic", "llm", "rank"]:
        assert kw not in " ".join(mods)
def test_no_derived_units_created(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    prod_before=load_production(pid,root=tmp_path/"productions")
    n_before=len(prod_before.units)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    register_source(pid,str(vid),root=tmp_path/"productions")
    prod_after=load_production(pid,root=tmp_path/"productions")
    assert len(prod_after.units)==n_before
