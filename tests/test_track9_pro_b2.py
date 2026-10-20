"""Pro B2: hierarchical multimodal + grounding."""
import json, hashlib
from app.assembly.video import create_test_video
from app.assembly.ffmpeg import probe_media
from app.production.models import Production, ProductionCommand, ProductionUnit, SourceAsset, SourceTranscript, TranscriptSegment
from app.production.narration import validate_narration_artifact
from app.production.storage import save_new_production, production_dir
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
def test_pro_visual_augments_with_hierarchical_mock(tmp_path):
    _reg(tmp_path, _sv(tmp_path, PID+"_hier", duration=720.0, color="green"))
    from app.production.storage import load_production
    import json as _j
    a=load_production(PID, root=tmp_path).source_assets[0]
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"Segment {i} dialogue", start=i*15.0, end=(i+1)*15.0-2.0) for i in range(24)], provider="mock", provider_version="1.0", created_at="2026-08-31T09:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    from app.content.providers.base import LLMResponse
    class _HMM:
        name="hmm"; model="hmm-vl"
        def is_available(self): return True
        def supports_multimodal(self): return True
        def generate_multimodal(self, prompt, system_prompt=None, *, video=None, images=None, video_bytes=None, response_schema=None, **kw):
            if "Analyze window" in prompt:
                if "[0-" in prompt:
                    return LLMResponse(content=_j.dumps({"visual_summary":"Character enters room silently, discovers body, reacts with shock. Silent visual event."}), provider="hmm", model="hmm-vl")
                return LLMResponse(content=_j.dumps({"visual_summary":"Dialogue scene with gestures; characters talking."}), provider="hmm", model="hmm-vl")
            return LLMResponse(content=_j.dumps({"summary":"Condensed grounded narrative.","beats":[{"beat_id":"b1","title":"Silent discovery","narration_text":"The protagonist silently enters and discovers the body, reacting in shock.","importance":0.95,"source_refs":[{"asset_id":"src-001","start_s":5,"end_s":45,"transcript_segment_ids":[0]}]}, {"beat_id":"b2","title":"Aftermath","narration_text":"They pick up the weapon left behind and depart.","importance":0.9,"source_refs":[{"asset_id":"src-001","start_s":300,"end_s":360,"transcript_segment_ids":[12]}]}]}), provider="hmm", model="hmm-vl")
        def generate(self, prompt, system_prompt=None, **kw): return self.generate_multimodal(prompt, system_prompt, video=None, images=None)
    prov=_HMM()
    import app.production.narration as narr
    from app.content.model_resolver import ResolvedModelProvider
    orig_resolve=narr.resolve_model
    def _fake_resolve(cid, mode="exact", try_next_on_failure=True):
        return ResolvedModelProvider(canonical_model_id=cid, provider="hmm", provider_model_id="hmm-vl", provider_instance=prov, attempted=())
    narr.resolve_model=_fake_resolve
    try:
        art=narr.build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=180.0, canonical_model_id="summary-pro-gemini-2.0-flash", summary_mode="pro", root=tmp_path)
    finally:
        narr.resolve_model=orig_resolve
    assert len(art.beats)>=1
    assert "discover" in art.narration_text.lower() or "weapon" in art.narration_text.lower()
    validate_narration_artifact(art, source_asset=a, source_transcript=tr)
    total=sum(float(r.end_seconds)-float(r.start_seconds) for r in art.source_refs)
    assert total < float(a.duration) * 0.8
    assert len(art.beats) < 6
