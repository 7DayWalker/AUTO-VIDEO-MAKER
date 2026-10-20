"""Pro B1: multimodal provider receives video."""
import json, hashlib
from app.assembly.video import create_test_video
from app.assembly.ffmpeg import probe_media
from app.production.models import Production, ProductionCommand, ProductionUnit, SourceAsset, SourceTranscript, TranscriptSegment
from app.production.narration import build_grounded_narration_plan
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
def _tr(n=16):
    return SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"Segment content number {i} with informative text about topic {i%5}", start=i*3.0, end=(i+1)*3.0) for i in range(n)], provider="mock", provider_version="1.0", created_at="2026-08-31T09:00:00+00:00", transcript_path="source/src-001.transcript.json")
def test_pro_multimodal_provider_receives_video(tmp_path):
    _reg(tmp_path, _sv(tmp_path, PID+"_mm", duration=60.0))
    from app.production.storage import load_production
    a=load_production(PID, root=tmp_path).source_assets[0]
    tr=_tr(16)
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    calls=[]
    class _MM:
        name="mm"; model="mm-vl"
        def is_available(self): return True
        def supports_multimodal(self): return True
        def generate_multimodal(self, prompt, system_prompt=None, *, video=None, images=None, video_bytes=None, response_schema=None, **kw):
            calls.append({"video": video, "has_video": video is not None, "prompt_len": len(prompt)})
            from app.content.providers.base import LLMResponse
            return LLMResponse(content='probe', provider='mm', model='mm-vl')
        def generate(self, prompt, system_prompt=None, **kw):
            calls.append({"video": None, "has_video": False}); return None
    art=build_grounded_narration_plan(PID, UID, a, tr, target_duration_seconds=10.0, canonical_model_id="summary-pro-gemini-2.0-flash", summary_mode="pro", llm_provider_override=_MM(), root=tmp_path)
    assert any(c.get("has_video") for c in calls)
    assert any(c.get("video") and "src-001.mp4" in str(c["video"]) for c in calls)
