"""Track 9 Pro C: target effect, schema, TTS/assembly, roles."""
import json, hashlib
from app.assembly.video import create_test_video
from app.assembly.ffmpeg import probe_media
from app.production.models import Production, ProductionCommand, ProductionUnit, SourceAsset, SourceTranscript, TranscriptSegment
from app.production.narration import build_grounded_narration_plan
from app.production.storage import save_new_production, production_dir, save_narration_artifact
from app.production.summary_video import assemble_narrated_summary, summary_video_output_path
PID="prd_20260831_pro_001"; UID="unit-001"
def _prod():
    return Production(production_id=PID, title="pro", command=ProductionCommand(text="pro test", workload_shape="single_video", explicit_constraints={}, creation_context={"source":"test"}, issued_at="2026-08-31T09:00:00+00:00"), units=[ProductionUnit(unit_id=UID, ordinal=1, unit_type="standalone_video", title="u1", premise="p", content_intent={"tone":"calm"})], created_at="2026-08-31T09:00:00+00:00")
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
def test_pro_target_duration_effect(tmp_path):
    _reg(tmp_path, _sv(tmp_path, PID+"_budget2", duration=120.0))
    from app.production.storage import load_production
    a=load_production(PID, root=tmp_path).source_assets[0]
    tr=_tr(40)
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    short=build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=10.0, mock_mode=True, summary_mode="standard", root=tmp_path)
    long=build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=60.0, mock_mode=True, summary_mode="standard", root=tmp_path)
    assert len(short.beats) < len(long.beats)
def test_narration_artifact_unchanged_schema(tmp_path):
    _reg(tmp_path, _sv(tmp_path, PID+"_schema", duration=40.0))
    from app.production.storage import load_production
    a=load_production(PID, root=tmp_path).source_assets[0]
    tr=_tr(16)
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    art=build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=12.0, mock_mode=True, summary_mode="standard", root=tmp_path)
    save_narration_artifact(art, production_id=PID, unit_id=UID, root=tmp_path, source_asset=a, source_transcript=tr)
    from app.production.storage import load_narration_artifact
    from app.production.narration import synthesize_narration_audio
    reloaded=load_narration_artifact(PID, UID, root=tmp_path)
    synth=synthesize_narration_audio(reloaded, root=tmp_path, provider_name="mock", voice="mock-voice-1", output_format="mp3")
    assert synth.duration_seconds>0
    res=assemble_narrated_summary(PID, UID, root=tmp_path)
    out=summary_video_output_path(PID, UID, root=tmp_path)
    assert out.is_file()
    from app.assembly.ffmpeg import probe_media
    info=probe_media(out)
    assert info.has_video and info.has_audio
def test_model_registry_roles_separate():
    from app.content.model_registry import list_models, has_model, get_model, list_models_for_pro
    ids=[m.id for m in list_models()]
    assert "qwen-3.8-flash" in ids
    assert "gemini-3-flash-preview" in ids
    assert "qwen-vl-max" in ids
    assert "gpt-4o" in ids
    # Deprecated aliases are NOT active identities
    assert not has_model("summary-pro-gemini-3-flash-preview")
    assert not has_model("summary-pro-gemini-2.0-flash")
    assert not has_model("summary-standard-qwen-flash")
    assert get_model("summary-pro-gemini-3-flash-preview").id == "gemini-3-flash-preview"
    # Pro capability query is provider-neutral (no prefix filter)
    pro_ids=[m.id for m in list_models_for_pro()]
    assert "gemini-3-flash-preview" in pro_ids
    assert "qwen-vl-max" in pro_ids
    assert "gpt-4o" in pro_ids
    assert "qwen-3.8-flash" not in pro_ids
