import json, hashlib, pytest
from app.assembly.video import create_test_video
from app.assembly.ffmpeg import probe_media
from app.production.models import Production, ProductionCommand, ProductionUnit, SourceAsset, SourceTranscript, TranscriptSegment, NarrativeBeat, NarrationSourceRef, NarrationArtifact
from app.production.narration import build_grounded_narration_plan, validate_narration_artifact
from app.production.storage import save_new_production, production_dir, save_narration_artifact, load_narration_artifact
from app.production.summary_video import assemble_narrated_summary, summary_video_output_path
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
def test_long_form_chunking_spans_entire_source(tmp_path):
    _reg(tmp_path, _sv(tmp_path, PID+"_long", duration=120.0))
    from app.production.storage import load_production
    a=load_production(PID, root=tmp_path).source_assets[0]
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"Segment {i}", start=i*3.0, end=(i+1)*3.0) for i in range(50)], provider="mock", provider_version="1.0", created_at="2026-08-31T09:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    art=build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=20.0, mock_mode=True)
    all_ids=sorted(int(x) for x in art.transcript_refs)
    assert min(all_ids) < 10
    assert max(all_ids) > 40
    assert [b.order for b in art.beats] == sorted(b.order for b in art.beats)
def test_grounding_ids_validated(tmp_path):
    _reg(tmp_path, _sv(tmp_path, PID+"_ground", duration=60.0))
    from app.production.storage import load_production
    a=load_production(PID, root=tmp_path).source_assets[0]
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"Seg {i}", start=i*3.0, end=(i+1)*3.0) for i in range(20)], provider="mock", provider_version="1.0", created_at="2026-08-31T09:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    art=build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=10.0, mock_mode=True)
    for r in art.source_refs:
        assert r.asset_id=="src-001"
        assert 0 <= r.start_seconds < r.end_seconds <= float(a.duration)
        for tid in r.transcript_segment_ids:
            assert 0 <= tid < len(tr.segments)
class _FailingProvider:
    name="mock-fail"; model="mock"
    def is_available(self): return True
    def generate(self, *a, **kw): raise RuntimeError("LLM failure")
    def generate_json(self, *a, **kw): raise RuntimeError("LLM failure json")
def test_invalid_refs_rejected_and_failure_no_fallback(tmp_path):
    _reg(tmp_path, _sv(tmp_path, PID+"_fail", duration=60.0))
    from app.production.storage import load_production
    a=load_production(PID, root=tmp_path).source_assets[0]
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"Seg {i}", start=i*2.0, end=(i+1)*2.0) for i in range(20)], provider="mock", provider_version="1.0", created_at="2026-08-31T09:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    bad_ref=NarrationSourceRef(asset_id="src-001", start_seconds=0.0, end_seconds=5.0, transcript_segment_ids=[9999])
    bad_beat=NarrativeBeat(order=1, title="Bad", narration_text="Bad narration", source_refs=[bad_ref], transcript_refs=["9999"])
    bad_art=NarrationArtifact(narration_id=f"narr-{PID}-{UID}", production_id=PID, unit_id=UID, source_asset_id="src-001", target_duration_seconds=10.0, status="grounded", narration_text="Bad narration", beats=[bad_beat], source_refs=[bad_ref], transcript_refs=["9999"], generated_at="2026-08-31T09:00:00+00:00")
    with pytest.raises(ValueError):
        validate_narration_artifact(bad_art, source_asset=a, source_transcript=tr)
    prov=_FailingProvider()
    with pytest.raises(Exception):
        build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=10.0, llm_provider_override=prov)
