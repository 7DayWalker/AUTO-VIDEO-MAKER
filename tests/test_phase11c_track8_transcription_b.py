"""Track 8 transcription B."""
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
def test_timestamp_validation_negative_start():
    from app.production.models import TranscriptSegment
    with pytest.raises(Exception):
        TranscriptSegment(text="hi", start=-0.1, end=1.0)
def test_timestamp_validation_end_le_start():
    from app.production.models import TranscriptSegment
    with pytest.raises(Exception):
        TranscriptSegment(text="hi", start=1.0, end=1.0)
def test_chronological_order(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid,seconds=2)
    register_source(pid,str(vid),root=tmp_path/"productions")
    _,pay=transcribe_source(pid,"src-001",root=tmp_path/"productions",provider="mock")
    starts=[seg["start"] for seg in pay["segments"]]
    assert starts==sorted(starts)
def test_relative_path_persisted(tmp_path, monkeypatch):
    pid=_make_prod(tmp_path, monkeypatch)
    vid=tmp_path/"src.mp4"
    _synth_video(vid)
    register_source(pid,str(vid),root=tmp_path/"productions")
    _,pay=transcribe_source(pid,"src-001",root=tmp_path/"productions",provider="mock")
    assert pay["transcript_path"]=="source/src-001.transcript.json"
    tr=load_transcript(pid,"src-001",root=tmp_path/"productions")
    assert tr.transcript_path=="source/src-001.transcript.json"
def test_absolute_path_rejected_model():
    from app.production.models import SourceTranscript
    with pytest.raises(Exception):
        SourceTranscript(asset_id="src-001", language="en", segments=[], provider="mock", created_at="2020-01-01T00:00:00", transcript_path="/abs/path.json")
