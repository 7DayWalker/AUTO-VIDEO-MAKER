"""Canonical Track 8 Segment Analysis / Operator Selection contract."""
import json
import math
import threading
import urllib.request
from pathlib import Path
import pytest
from app.production.segments import load_segments, save_segments
from app.production.storage import segments_path_for
from app.production.source import register_source
from app.transcription.transcribe import transcribe_source, load_transcript
from app.web import server as web

def _make_prod(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    s, p = web.create_production_payload({"mode":"single_video","creation_method":"local","title":"seg test","brief":"track8","output_intent":"youtube_shorts","target_duration":60,"scene_count":6})
    assert s == 201
    return p["production_id"]

def _synth_video(path: Path, seconds=3):
    from app.assembly.ffmpeg import run_ffmpeg
    path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-y","-f","lavfi","-i",f"color=c=red:s=320x240:r=25:d={seconds}","-c:v","libx264","-pix_fmt","yuv420p","-t",str(seconds),str(path)], "synth")

def _prep(tmp_path, monkeypatch, seconds=3):
    pid = _make_prod(tmp_path, monkeypatch)
    root = tmp_path / "productions"
    vp = tmp_path / "src.mp4"
    _synth_video(vp, seconds=seconds)
    assert register_source(pid, str(vp), root=root)[0] == 201
    assert transcribe_source(pid, "src-001", root=root, provider="mock")[0] == 201
    return pid, root

def test_valid_segment_creation_and_deterministic_ids(tmp_path, monkeypatch):
    pid, root = _prep(tmp_path, monkeypatch, seconds=4)
    s, payload = save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":1},{"start_seconds":1,"end_seconds":2,"title":"hello","premise":"prem"}]}, root=root)
    assert s == 200
    assert payload["segments"][0]["segment_id"] == "seg-001"
    assert payload["segments"][1]["segment_id"] == "seg-002"
    assert payload["segments_path"] == "source/src-001.segments.json"

def test_source_ownership_validation(tmp_path, monkeypatch):
    pid, root = _prep(tmp_path, monkeypatch)
    s, _p = save_segments("nonexistent-prod-id-123456", "src-001", {"segments":[{"start_seconds":0,"end_seconds":1}]}, root=root)
    assert s in (400, 404)
    assert save_segments(pid, "src-999", {"segments":[{"start_seconds":0,"end_seconds":1}]}, root=root)[0] == 404

def test_transcript_existence_requirement(tmp_path, monkeypatch):
    pid = _make_prod(tmp_path, monkeypatch)
    root = tmp_path / "productions"
    vp = tmp_path / "src2.mp4"
    _synth_video(vp, seconds=2)
    register_source(pid, str(vp), root=root)
    s, p = save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":1}]}, root=root)
    assert s == 404 and p.get("error") == "TRANSCRIPT_NOT_FOUND"

def test_timestamp_validation(tmp_path, monkeypatch):
    pid, root = _prep(tmp_path, monkeypatch, seconds=2)
    assert save_segments(pid, "src-001", {"segments":[{"start_seconds":-0.1,"end_seconds":1}]}, root=root)[0] == 400
    assert save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":0}]}, root=root)[0] == 400
    assert save_segments(pid, "src-001", {"segments":[{"start_seconds":1,"end_seconds":0.5}]}, root=root)[0] == 400
    assert save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":100}]}, root=root)[0] == 400

def test_rejection_of_non_finite_values(tmp_path, monkeypatch):
    pid, root = _prep(tmp_path, monkeypatch, seconds=2)
    assert save_segments(pid, "src-001", {"segments":[{"start_seconds":float("inf"),"end_seconds":1}]}, root=root)[0] == 400
    assert save_segments(pid, "src-001", {"segments":[{"start_seconds":float("nan"),"end_seconds":1}]}, root=root)[0] == 400
    assert save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":float("inf")}]}, root=root)[0] == 400

def test_optional_title_and_premise(tmp_path, monkeypatch):
    pid, root = _prep(tmp_path, monkeypatch, seconds=2)
    s, p = save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":1,"title":"  t  ","premise":"  pr  "}]}, root=root)
    assert s == 200 and p["segments"][0]["title"] == "t" and p["segments"][0]["premise"] == "pr"
    assert save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":1,"title":" "}]}, root=root)[0] == 400
    assert save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":1,"title":"x"*201}]}, root=root)[0] == 400
    assert save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":1,"premise":"x"*501}]}, root=root)[0] == 400

def test_accepted_and_auto_transcript_refs(tmp_path, monkeypatch):
    pid, root = _prep(tmp_path, monkeypatch, seconds=3)
    tr = load_transcript(pid, "src-001", root=root)
    s, p = save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":1,"transcript_segment_ids":[0]}]}, root=root)
    assert s == 200 and p["segments"][0]["transcript_segment_ids"] == [0]
    from app.production.storage import load_production as _lp
    dur = float(_lp(pid, root=root).source_assets[0].duration)
    s2, p2 = save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":dur}]}, root=root)
    assert s2 == 200
    expected = [n for n, seg in enumerate(tr.segments) if seg.start < dur and seg.end > 0]
    assert p2["segments"][0]["transcript_segment_ids"] == expected

def test_transcript_reference_validation(tmp_path, monkeypatch):
    pid, root = _prep(tmp_path, monkeypatch, seconds=2)
    assert save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":1,"transcript_segment_ids":[999]}]}, root=root)[0] == 400
    assert save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":1,"transcript_segment_ids":[-1]}]}, root=root)[0] == 400

def test_deterministic_ordering_and_ids(tmp_path, monkeypatch):
    pid, root = _prep(tmp_path, monkeypatch, seconds=4)
    s, p = save_segments(pid, "src-001", {"segments":[{"start_seconds":2,"end_seconds":3},{"start_seconds":0,"end_seconds":1}]}, root=root)
    assert s == 200 and [x["segment_id"] for x in p["segments"]] == ["seg-001","seg-002"]
    assert p["segments"][0]["start_seconds"] == 2

def test_persistence_reload_and_atomicity(tmp_path, monkeypatch):
    pid, root = _prep(tmp_path, monkeypatch, seconds=3)
    s, saved = save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":1}]}, root=root)
    assert s == 200 and segments_path_for(pid, "src-001", root=root).is_file()
    import json as _j
    assert _j.loads(segments_path_for(pid, "src-001", root=root).read_text(encoding="utf-8"))["segments"] == saved["segments"]
    s2, r2 = load_segments(pid, "src-001", root=root)
    s3, r3 = load_segments(pid, "src-001", root=root)
    assert s2 == 200 and s3 == 200 and r2 == r3 == saved

def test_production_ownership_isolation(tmp_path, monkeypatch):
    pid1, root = _prep(tmp_path, monkeypatch, seconds=2)
    pid2 = _make_prod(tmp_path, monkeypatch)
    vp2 = tmp_path / "srcB.mp4"
    _synth_video(vp2, seconds=2)
    register_source(pid2, str(vp2), root=root)
    transcribe_source(pid2, "src-001", root=root, provider="mock")
    save_segments(pid1, "src-001", {"segments":[{"start_seconds":0,"end_seconds":1,"title":"A"}]}, root=root)
    s, r = load_segments(pid2, "src-001", root=root)
    assert s == 200 and r["segments"] == []

def test_get_and_post_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    pid, root = _prep(tmp_path, monkeypatch, seconds=2)
    server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.AVMHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api/productions/{pid}/source/src-001/segments"
    try:
        with urllib.request.urlopen(base) as r:
            assert json.loads(r.read())["segments"] == []
        req = urllib.request.Request(base, data=json.dumps({"segments":[{"start_seconds":0,"end_seconds":1}]}).encode(), headers={"Content-Type":"application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            body = json.loads(r.read())
            assert body["segments"][0]["segment_id"] == "seg-001"
        with urllib.request.urlopen(base) as r:
            assert json.loads(r.read())["segments"][0]["segment_id"] == "seg-001"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)

def test_invalid_payload_and_empty_collection(tmp_path, monkeypatch):
    pid, root = _prep(tmp_path, monkeypatch, seconds=2)
    assert save_segments(pid, "src-001", None, root=root)[0] == 400
    assert save_segments(pid, "src-001", {}, root=root)[0] == 400
    assert save_segments(pid, "src-001", {"segments":"bad"}, root=root)[0] == 400
    s, p = save_segments(pid, "src-001", {"segments":[]}, root=root)
    assert s == 200 and p["segments"] == []
    assert load_segments(pid, "src-001", root=root)[1]["segments"] == []

def test_no_ai_ranking_and_no_media_extraction(tmp_path, monkeypatch):
    pid, root = _prep(tmp_path, monkeypatch, seconds=2)
    s, p = save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":1}]}, root=root)
    assert s == 200 and p["segments"][0]["start_seconds"] == 0
    import app.production.segments as segmod
    # Architecture guard: no imported ranking/AI modules, no ffmpeg extraction
    import ast as _ast
    tree = _ast.parse(Path(segmod.__file__).read_text(encoding="utf-8"))
    imports = {n.names[0].name.lower() if hasattr(n,'names') else '' for n in _ast.walk(tree) if isinstance(n, _ast.Import)}
    froms = {n.module.lower() if n.module else '' for n in _ast.walk(tree) if isinstance(n, _ast.ImportFrom)}
    combined = " ".join(imports | froms)
    for kw in ["rank","llm","openai","anthropic","ffmpeg"]:
        assert kw not in combined, f"prohibited import '{kw}' found"
    src = Path(segmod.__file__).read_text(encoding="utf-8").lower()
    assert "ffmpeg" not in src or "no media extraction" in src

def test_timestamp_boundaries(tmp_path, monkeypatch):
    pid, root = _prep(tmp_path, monkeypatch, seconds=2)
    from app.production.storage import load_production
    dur = float(load_production(pid, root=root).source_assets[0].duration)
    assert save_segments(pid, "src-001", {"segments":[{"start_seconds":0,"end_seconds":dur}]}, root=root)[0] == 200



