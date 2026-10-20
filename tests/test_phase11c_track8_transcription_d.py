"""Track 8 transcription D."""
from pathlib import Path
import pytest
from app.production.storage import load_production, transcript_path_for
from app.web import server as web
from app.production.source import register_source
from app.transcription.transcribe import transcribe_source
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
def test_stt_failure_no_false_completion(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid)
    register_source(pid,str(vid),root=tmp_path/"productions")
    import app.transcription.providers as prov
    def fail(self, media_path, duration):
        raise RuntimeError("TRANSCRIPTION_FAILED: boom")
    monkeypatch.setattr(prov.MockProvider,"transcribe", fail)
    s,_=transcribe_source(pid,"src-001",root=tmp_path/"productions",provider="mock")
    assert s==500
    assert not transcript_path_for(pid,"src-001",root=tmp_path/"productions").exists()
def test_mock_deterministic(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    register_source(pid,str(vid),root=tmp_path/"productions")
    s1,p1=transcribe_source(pid,"src-001",root=tmp_path/"productions",provider="mock")
    s2,p2=transcribe_source(pid,"src-001",root=tmp_path/"productions",provider="mock")
    assert s1==201 and s2==201
    assert p1["segments"]==p2["segments"]
def test_timestamp_end_beyond_duration_rejected(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    register_source(pid,str(vid),root=tmp_path/"productions")
    import app.transcription.providers as prov
    def bad(self, media_path, duration):
        from app.transcription.providers import TranscriptionResult
        return TranscriptionResult(language="en", segments=[{"text":"bad","start":0,"end": duration+10}], provider="mock", provider_version="mock-v1")
    monkeypatch.setattr(prov.MockProvider,"transcribe", bad)
    s,_=transcribe_source(pid,"src-001",root=tmp_path/"productions",provider="mock")
    assert s==500
def test_segment_service_is_available():
    from app.production import segments
    assert callable(segments.save_segments)
    assert callable(segments.load_segments)
def test_no_derived_units(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    prod_before=load_production(pid,root=tmp_path/"productions")
    n=len(prod_before.units)
    vid=tmp_path/"src.mp4"
    _synth_video(vid)
    register_source(pid,str(vid),root=tmp_path/"productions")
    transcribe_source(pid,"src-001",root=tmp_path/"productions",provider="mock")
    prod_after=load_production(pid,root=tmp_path/"productions")
    assert len(prod_after.units)==n
def test_no_execution_engine():
    import app.transcription.transcribe as tr
    assert not hasattr(tr,"TranscriptionExecutor")
    assert not hasattr(tr,"RepurposingExecutor")
