import json, hashlib, pytest
from app.assembly.video import create_test_video
from app.assembly.ffmpeg import probe_media
from app.production.models import Production, ProductionCommand, ProductionUnit, SourceAsset, SourceTranscript, TranscriptSegment
from app.production.narration import build_grounded_narration_plan, validate_narration_artifact
from app.production.storage import save_new_production, production_dir, save_narration_artifact, load_narration_artifact
PID="prd_20260831_phase3b_001"; UID="unit-001"
def _prod():
    return Production(production_id=PID, title="phase3b", command=ProductionCommand(text="phase3b ai", workload_shape="single_video", explicit_constraints={}, creation_context={"source":"test"}, issued_at="2026-08-31T09:00:00+00:00"), units=[ProductionUnit(unit_id=UID, ordinal=1, unit_type="standalone_video", title="u1", premise="p", content_intent={"tone":"calm"})], created_at="2026-08-31T09:00:00+00:00")
def _sv(root, pid, duration=30.0, color="blue"):
    d=root/pid/"source"; d.mkdir(parents=True, exist_ok=True); p=d/"src-001.mp4"; create_test_video(p, duration=duration, width=640, height=360, fps=24, color=color); return p
def _reg(tmp_root, source_path):
    import shutil
    from app.production.storage import load_production, save_revision, production_dir as pd
    save_new_production(_prod(), root=tmp_root)
    info=probe_media(source_path); h=hashlib.sha256(source_path.read_bytes()).hexdigest()
    rel="source/src-001.mp4"; dst=pd(PID, root=tmp_root)/rel; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source_path, dst)
    asset=SourceAsset(asset_id="src-001", original_filename="src-001.mp4", stored_path=rel, sha256=h, duration=float(info.duration), width=info.width, height=info.height, video_codec=info.video_codec, audio_codec=info.audio_codec, mime="video/mp4", file_size=dst.stat().st_size, ingested_at="2026-08-31T09:00:00+00:00")
    prod2=load_production(PID, root=tmp_root); revised=prod2.model_copy(update={"plan_version":prod2.plan_version+1,"revised_at":"2026-08-31T10:00:00+00:00","source_assets":[asset]})
    save_revision(revised, root=tmp_root); return asset
def _tr(n=40):
    return SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"Segment content number {i} with informative text about topic {i%5}", start=i*3.0, end=(i+1)*3.0) for i in range(n)], provider="mock", provider_version="1.0", created_at="2026-08-31T09:00:00+00:00", transcript_path="source/src-001.transcript.json")
def test_mock_condensed_not_transcript_copy(tmp_path):
    _reg(tmp_path, _sv(tmp_path, PID+"_cond", duration=90.0))
    from app.production.storage import load_production
    a=load_production(PID, root=tmp_path).source_assets[0]
    tr=_tr(20)
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    art=build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=15.0, mock_mode=True)
    raw_join=" ".join(s.text for s in tr.segments)
    assert art.narration_text.strip() != raw_join.strip()
    assert all("Summary beat" in b.narration_text for b in art.beats)
    validate_narration_artifact(art, source_asset=a, source_transcript=tr)
    save_narration_artifact(art, production_id=PID, unit_id=UID, root=tmp_path, source_asset=a, source_transcript=tr)
    reloaded=load_narration_artifact(PID, UID, root=tmp_path)
    assert reloaded.narration_text==art.narration_text
def test_target_duration_affects_retained_content(tmp_path):
    _reg(tmp_path, _sv(tmp_path, PID+"_budget", duration=120.0))
    from app.production.storage import load_production
    a=load_production(PID, root=tmp_path).source_assets[0]
    tr=_tr(40)
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    short=build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=10.0, mock_mode=True)
    long=build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=60.0, mock_mode=True)
    assert len(short.beats) < len(long.beats)
    assert len(short.source_refs) < len(long.source_refs)
    assert len(short.narration_text.split()) < len(long.narration_text.split())
def test_sparse_selection_not_every_window(tmp_path):
    _reg(tmp_path, _sv(tmp_path, PID+"_sparse", duration=90.0))
    from app.production.storage import load_production
    a=load_production(PID, root=tmp_path).source_assets[0]
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"Long segment {i} filler content", start=i*3.0, end=(i+1)*3.0) for i in range(30)], provider="mock", provider_version="1.0", created_at="2026-08-31T09:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    art=build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=12.0, mock_mode=True)
    assert len(art.beats) < 4
    selected_dur=sum(float(r.end_seconds)-float(r.start_seconds) for r in art.source_refs)
    assert selected_dur < float(a.duration) * 0.6
    assert len(art.transcript_refs) < len(tr.segments)
