import json, pytest
from pathlib import Path
from app.assembly.ffmpeg import probe_media
from app.assembly.video import create_test_video
from app.production.models import Production, ProductionCommand, ProductionUnit, SourceAsset, TranscriptSegment, SourceTranscript
from app.production.narration import build_grounded_narration_plan, synthesize_narration_audio
from app.production.storage import save_new_production, production_dir
from app.production.summary_video import validate_grounded_visual_refs, assemble_narrated_summary, summary_video_output_path, load_narrated_summary

PID="prd_20260831_phase3_001"
UID="unit-001"
def _prod():
    return Production(production_id=PID, title="phase3", command=ProductionCommand(text="phase3 summary", workload_shape="single_video", explicit_constraints={}, creation_context={"source":"test"}, issued_at="2026-08-31T09:00:00+00:00"), units=[ProductionUnit(unit_id=UID, ordinal=1, unit_type="standalone_video", title="u1", premise="p", content_intent={"tone":"calm"})], created_at="2026-08-31T09:00:00+00:00")
def _source_video(root, pid, duration=12.0):
    d=root / pid / "source"; d.mkdir(parents=True, exist_ok=True); p=d/"src-001.mp4"; create_test_video(p, duration=duration, width=640, height=360, fps=24, color="blue"); return p
def _reg(tmp_root, source_path):
    import shutil, hashlib
    from app.production.storage import load_production, save_revision, production_dir as pd
    from app.assembly.ffmpeg import probe_media as pm
    save_new_production(_prod(), root=tmp_root)
    info=pm(source_path); h=hashlib.sha256(source_path.read_bytes()).hexdigest()
    rel="source/src-001.mp4"; dst=pd(PID, root=tmp_root)/rel; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source_path, dst)
    from app.production.models import SourceAsset
    asset=SourceAsset(asset_id="src-001", original_filename="src-001.mp4", stored_path=rel, sha256=h, duration=float(info.duration), width=info.width, height=info.height, video_codec=info.video_codec, audio_codec=info.audio_codec, mime="video/mp4", file_size=dst.stat().st_size, ingested_at="2026-08-31T09:00:00+00:00")
    prod2=load_production(PID, root=tmp_root); revised=prod2.model_copy(update={"plan_version":prod2.plan_version+1,"revised_at":"2026-08-31T10:00:00+00:00","source_assets":[asset]})
    save_revision(revised, root=tmp_root); return asset

def test_visual_ref_resolution_and_ordering(tmp_path):
    _reg(tmp_path, _source_video(tmp_path, PID+"_ord", duration=30.0))
    from app.production.storage import load_production
    a=load_production(PID, root=tmp_path).source_assets[0]
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"seg {i}", start=i*2.0, end=(i+1)*2.0) for i in range(15)], provider="mock", provider_version="1.0", created_at="2026-08-31T09:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").parent.mkdir(parents=True, exist_ok=True)
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    art=build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=8.0, mock_mode=True)
    assert len(art.beats)>=2
    assert [b.order for b in art.beats]==sorted(b.order for b in art.beats)

def test_invalid_source_range_rejected_no_clamp(tmp_path):
    _reg(tmp_path, _source_video(tmp_path, PID+"_bad", duration=10.0))
    from app.production.storage import load_production
    a=load_production(PID, root=tmp_path).source_assets[0]
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text="hello", start=0.0, end=5.0), TranscriptSegment(text="world", start=5.0, end=10.0)], provider="mock", provider_version="1.0", created_at="2026-08-31T09:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    art=build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=4.0, mock_mode=True)
    bad=art.model_copy(update={"beats":[art.beats[0].model_copy(update={"source_refs":[art.beats[0].source_refs[0].model_copy(update={"start_seconds":-1.0})]})]})
    with pytest.raises(ValueError): validate_grounded_visual_refs(bad, a, tr)
    bad2=art.model_copy(update={"beats":[art.beats[0].model_copy(update={"source_refs":[art.beats[0].source_refs[0].model_copy(update={"end_seconds":999.0})]})]})
    with pytest.raises(ValueError): validate_grounded_visual_refs(bad2, a, tr)
