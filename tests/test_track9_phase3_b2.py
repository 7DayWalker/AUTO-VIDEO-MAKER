import json, pytest
from app.assembly.ffmpeg import probe_media
from app.assembly.video import create_test_video
from app.production.models import Production, ProductionCommand, ProductionUnit, TranscriptSegment, SourceTranscript
from app.production.narration import build_grounded_narration_plan, synthesize_narration_audio
from app.production.storage import save_new_production, production_dir, load_narration_artifact
from app.production.summary_video import assemble_narrated_summary, summary_video_output_path
PID="prd_20260831_phase3_001"
UID="unit-001"
def _prod():
    return Production(production_id=PID, title="phase3", command=ProductionCommand(text="phase3", workload_shape="single_video", explicit_constraints={}, creation_context={"source":"test"}, issued_at="2026-08-31T09:00:00+00:00"), units=[ProductionUnit(unit_id=UID, ordinal=1, unit_type="standalone_video", title="u1", premise="p", content_intent={"tone":"calm"})], created_at="2026-08-31T09:00:00+00:00")
def _sv(root, pid, duration=12.0):
    d=root/pid/"source"; d.mkdir(parents=True, exist_ok=True); p=d/"src-001.mp4"; create_test_video(p, duration=duration, width=640, height=360, fps=24, color="blue"); return p
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
def test_ffmpeg_multiple_refs(tmp_path):
    _reg(tmp_path, _sv(tmp_path, PID+"_auth", duration=30.0))
    from app.production.storage import load_production
    a=load_production(PID, root=tmp_path).source_assets[0]
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"line {i}", start=i*1.5, end=(i+1)*1.5) for i in range(20)], provider="mock", provider_version="1.0", created_at="2026-08-31T09:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    art=build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=8.0, mock_mode=True)
    ref=art.beats[0].source_refs[0]; mid=(ref.start_seconds+ref.end_seconds)/2
    r1=ref.model_copy(update={"end_seconds":mid}); r2=ref.model_copy(update={"start_seconds":mid})
    beat0=art.beats[0].model_copy(update={"source_refs":[r1,r2]})
    art2=art.model_copy(update={"beats":[beat0]+art.beats[1:]}); art=art2.model_copy(update={"source_refs":[r for b in art2.beats for r in b.source_refs]})
    from app.production.storage import save_narration_artifact
    save_narration_artifact(art, production_id=PID, unit_id=UID, root=tmp_path, source_asset=a, source_transcript=tr)
    synthesize_narration_audio(load_narration_artifact(PID, UID, root=tmp_path), root=tmp_path, provider_name="mock", voice="mock-voice-1", output_format="mp3")
    result=assemble_narrated_summary(PID, UID, root=tmp_path)
    assert result.source_refs_count>=2
    out=summary_video_output_path(PID, UID, root=tmp_path)
    assert out.is_file() and out.stat().st_size>0
    info=probe_media(out)
    assert info.has_video and info.has_audio and info.duration and info.duration>0
def test_timing_and_intent(tmp_path):
    _reg(tmp_path, _sv(tmp_path, PID+"_timing", duration=30.0))
    from app.production.storage import load_production
    a=load_production(PID, root=tmp_path).source_assets[0]
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"line {i} hello", start=i*1.0, end=(i+1)*1.0) for i in range(20)], provider="mock", provider_version="1.0", created_at="2026-08-31T09:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    art=build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=8.0, mock_mode=True)
    from app.production.storage import save_narration_artifact
    save_narration_artifact(art, production_id=PID, unit_id=UID, root=tmp_path, source_asset=a, source_transcript=tr)
    meta=synthesize_narration_audio(load_narration_artifact(PID, UID, root=tmp_path), root=tmp_path, provider_name="mock", voice="mock-voice-1", output_format="mp3")
    result=assemble_narrated_summary(PID, UID, root=tmp_path)
    assert result.narration_duration_seconds==pytest.approx(meta.duration_seconds, rel=0.05)
    assert result.output_duration_seconds>0
    assert result.validation["has_audio"] is True
