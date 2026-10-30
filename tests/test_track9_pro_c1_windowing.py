"""Pro C1: bounded windowing."""
import json, hashlib
import pytest
from app.assembly.video import create_test_video
from app.assembly.ffmpeg import probe_media, get_ffmpeg_runner
from app.production.models import Production, ProductionCommand, ProductionUnit, SourceAsset, SourceTranscript, TranscriptSegment
from app.production.storage import save_new_production, production_dir
from app.production.narration import build_grounded_narration_plan, validate_narration_artifact

PID="prd_20261007_pro_c1_001"; UID="unit-001"
def _prod():
    return Production(production_id=PID, title="pro_c1", command=ProductionCommand(text="pro_c1", workload_shape="single_video", explicit_constraints={}, creation_context={"source":"test"}, issued_at="2026-10-07T00:00:00+00:00"), units=[ProductionUnit(unit_id=UID, ordinal=1, unit_type="standalone_video", title="u1", premise="p", content_intent={"tone":"calm"})], created_at="2026-10-07T00:00:00+00:00")
def _reg(tmp_root, src):
    import shutil
    save_new_production(_prod(), root=tmp_root)
    info=probe_media(src); h=hashlib.sha256(src.read_bytes()).hexdigest()
    rel="source/src-001.mp4"; dst=production_dir(PID, root=tmp_root)/rel; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
    asset=SourceAsset(asset_id="src-001", original_filename="src-001.mp4", stored_path=rel, sha256=h, duration=float(info.duration), width=info.width, height=info.height, video_codec=info.video_codec, audio_codec=info.audio_codec or "aac", mime="video/mp4", file_size=dst.stat().st_size, ingested_at="2026-10-07T00:00:00+00:00")
    from app.production.storage import load_production, save_revision
    prod2=load_production(PID, root=tmp_root); revised=prod2.model_copy(update={"plan_version":prod2.plan_version+1,"revised_at":"2026-10-07T01:00:00+00:00","source_assets":[asset]})
    save_revision(revised, root=tmp_root); return asset
def _make_long_source(tmp_root, duration_per_seg=60.0, n_segs=6):
    d=tmp_root / "_src_build"; d.mkdir(parents=True, exist_ok=True)
    colors=['red','green','blue','yellow','magenta','cyan']*2
    segs=[]
    for i in range(n_segs):
        p=d / f"seg_{i:02d}.mp4"
        if not p.is_file():
            create_test_video(p, duration=duration_per_seg, width=320, height=180, fps=24, color=colors[i % len(colors)])
        segs.append(p)
    concat=d / "concat.txt"
    with open(concat, "w", encoding="utf-8") as f:
        for p in segs:
            esc=p.as_posix().replace("'", "'\\''"); f.write(f"file '{esc}'\n")
    merged=d / "merged.mp4"
    runner=get_ffmpeg_runner()
    if not merged.is_file():
        runner.run(["-y","-f","concat","-safe","0","-i",str(concat),"-c","copy","-movflags","+faststart",str(merged)], "concat long source")
    return merged
def _story_window_plan(transcript):
    """A 60-second plan now obeys the shared depth/coverage contract too."""
    from tests.track9_narration_fakes import story_response
    data = story_response(count=6, segment_count=len(transcript.segments))
    for beat in data["beats"]:
        ref = beat["source_refs"][0]
        segment = transcript.segments[ref["transcript_segment_ids"][0]]
        ref.update(start_seconds=segment.start + 1, end_seconds=segment.end - 1)
    return json.dumps(data)


def _story_window_text(index):
    return (f"Source event {index} reveals a challenge that changes the companion's plans. "
            "They choose a difficult path and confront the consequences while protecting the people who depend on them.")


def test_each_window_receives_only_its_bounded_visual_evidence(tmp_path):
    merged=_make_long_source(tmp_path, duration_per_seg=60.0, n_segs=6)
    asset=_reg(tmp_path, merged)
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=_story_window_text(i), start=i*60.0, end=(i+1)*60.0) for i in range(6)], provider="mock", provider_version="1.0", created_at="2026-10-07T00:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    from app.content.providers.base import LLMResponse
    seen=[]
    class Rec:
        name="rec"; model="rec-vl"
        def is_available(self): return True
        def supports_multimodal(self): return True
        def generate(self, prompt, system_prompt=None, **kw):
            return LLMResponse(content=_story_window_plan(tr), provider="rec", model="rec-vl")
        def generate_multimodal(self, prompt, system_prompt=None, *, video=None, images=None, video_bytes=None, **kw):
            import pathlib as _pl
            seen.append({"video":video})
            clip_dur=None
            if video and _pl.Path(video).is_file():
                try: clip_dur=probe_media(_pl.Path(video)).duration
                except: clip_dur=None
            seen[-1]["clip_duration"]=clip_dur
            return LLMResponse(content=json.dumps({"visual_summary":"red"}), provider="rec", model="rec-vl")
    prov=Rec()
    import app.production.narration as narr
    from app.content.model_resolver import ResolvedModelProvider
    orig=narr.resolve_model
    narr.resolve_model=lambda cid, mode="exact", try_next_on_failure=True: ResolvedModelProvider(canonical_model_id=cid, provider="rec", provider_model_id="rec-vl", provider_instance=prov, attempted=())
    try:
        art=narr.build_grounded_narration_plan(PID, UID, asset, tr, target_duration_seconds=60.0, canonical_model_id="summary-pro-gemini-2.0-flash", summary_mode="pro", root=tmp_path)
    finally:
        narr.resolve_model=orig
    assert 3 <= len(seen) <= 4
    for e in seen:
        assert e.get("clip_duration") is not None and 80 < e["clip_duration"] < 135
    ws=getattr(art, "_pro_window_summaries", None)
    assert ws is not None and 3 <= len(ws) <= 4

def test_full_source_not_repeated(tmp_path):
    merged=_make_long_source(tmp_path, duration_per_seg=120.0, n_segs=6)
    asset=_reg(tmp_path, merged)
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=_story_window_text(i), start=i*60.0, end=(i+1)*60.0) for i in range(12)], provider="mock", provider_version="1.0", created_at="2026-10-07T00:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    from app.content.providers.base import LLMResponse
    seen=[]
    class Rec2:
        name="rec2"; model="rec2-vl"
        def is_available(self): return True
        def supports_multimodal(self): return True
        def generate(self, prompt, system_prompt=None, **kw):
            return LLMResponse(content=_story_window_plan(tr), provider="rec2", model="rec2-vl")
        def generate_multimodal(self, prompt, system_prompt=None, *, video=None, images=None, video_bytes=None, **kw):
            import pathlib as _pl
            seen.append({"video":video})
            if video and _pl.Path(video).is_file():
                try: dur=probe_media(_pl.Path(video)).duration
                except: dur=None
                seen[-1]["dur"]=dur
            return LLMResponse(content=json.dumps({"visual_summary":"vis"}), provider="rec2", model="rec2-vl")
    prov=Rec2()
    import app.production.narration as narr
    from app.content.model_resolver import ResolvedModelProvider
    orig=narr.resolve_model
    narr.resolve_model=lambda cid, mode="exact", try_next_on_failure=True: ResolvedModelProvider(canonical_model_id=cid, provider="rec2", provider_model_id="rec2-vl", provider_instance=prov, attempted=())
    try:
        art=narr.build_grounded_narration_plan(PID, UID, asset, tr, target_duration_seconds=60.0, canonical_model_id="summary-pro-gemini-2.0-flash", summary_mode="pro", root=tmp_path)
    finally:
        narr.resolve_model=orig
    assert 6 <= len(seen) <= 7
    for e in seen:
        assert e.get("dur") is not None and 95 < e["dur"] < 135

def test_standard_remains_transcript_only(tmp_path):
    merged=_make_long_source(tmp_path, duration_per_seg=10.0, n_segs=4)
    asset=_reg(tmp_path, merged)
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"hello {i}", start=i*5.0, end=(i+1)*5.0) for i in range(8)], provider="mock", provider_version="1.0", created_at="2026-10-07T00:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    art=build_grounded_narration_plan(PID, UID, asset, tr, target_duration_seconds=10.0, mock_mode=True, summary_mode="standard", root=tmp_path)
    validate_narration_artifact(art, source_asset=asset, source_transcript=tr)
    assert not hasattr(art, "_pro_window_summaries")

def test_pro_requires_multimodal_and_no_silent_downgrade(tmp_path):
    merged=_make_long_source(tmp_path, duration_per_seg=10.0, n_segs=4)
    asset=_reg(tmp_path, merged)
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"x {i}", start=i*5.0, end=(i+1)*5.0) for i in range(8)], provider="mock", provider_version="1.0", created_at="2026-10-07T00:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    import pytest as _pt
    with _pt.raises(Exception):
        build_grounded_narration_plan(PID, UID, asset, tr, target_duration_seconds=10.0, canonical_model_id="summary-standard-qwen-flash", summary_mode="pro", mock_mode=True, root=tmp_path)
    class Bad:
        name="bad"; model="bad"
        def is_available(self): return True
        def supports_multimodal(self): return False
        def generate(self, *a, **kw): raise RuntimeError("no")
        def generate_multimodal(self, *a, **kw): raise NotImplementedError("no mm")
    with _pt.raises(Exception):
        build_grounded_narration_plan(PID, UID, asset, tr, target_duration_seconds=10.0, canonical_model_id="summary-pro-gemini-2.0-flash", summary_mode="pro", llm_provider_override=Bad(), root=tmp_path)

def test_provider_neutrality_dynamic_resolve(monkeypatch):
    from app.production.narration import resolve_summary_model
    from app.content.model_resolver import resolve_model
    import app.content.provider_registry as provider_registry
    mid=resolve_summary_model("pro")
    from app.content.model_registry import get_model
    binding = get_model(mid).bindings[0]
    for key in binding.config_keys:
        monkeypatch.setenv(key, "https://example.invalid/v1" if key.endswith("BASE_URL") else "synthetic-routing-key")
    # Resolve the real canonical binding through a hermetic adapter, not a
    # workstation-configured live provider (including import-time key caches).
    class StubProvider:
        name = binding.provider
        model = binding.provider_model_id
        def is_available(self): return True
        def generate(self, *args, **kwargs): raise AssertionError("resolution must not generate")
    monkeypatch.setitem(provider_registry._REGISTRY, binding.provider, lambda wire=None: StubProvider())
    resolved=resolve_model(mid, mode="exact", try_next_on_failure=True)
    assert resolved.provider and resolved.provider_model_id
    from app.content.model_registry import get_model
    m=get_model(mid)
    assert m.capabilities.is_multimodal_capable()

def test_visual_evidence_can_contribute(tmp_path):
    merged=_make_long_source(tmp_path, duration_per_seg=60.0, n_segs=4)
    asset=_reg(tmp_path, merged)
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"dialogue {i}", start=i*30.0, end=(i+1)*30.0) for i in range(8)], provider="mock", provider_version="1.0", created_at="2026-10-07T00:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    from app.content.providers.base import LLMResponse
    class Vis:
        name="vis"; model="vis-vl"
        def is_available(self): return True
        def supports_multimodal(self): return True
        def generate_multimodal(self, prompt, system_prompt=None, *, video=None, images=None, video_bytes=None, **kw):
            if "Analyze window [0-" in prompt:
                return LLMResponse(content=json.dumps({"visual_summary":"Character silently discovers object, shock"}), provider="vis", model="vis-vl")
            return LLMResponse(content=json.dumps({"visual_summary":"talking scene"}), provider="vis", model="vis-vl")
        def generate(self, prompt, system_prompt=None, **kw):
            return LLMResponse(content=json.dumps({"summary":"condensed","beats":[{"beat_id":"b1","title":"Silent discovery","narration_text":"The protagonist silently discovers the object and reacts in shock.","source_refs":[{"asset_id":"src-001","start_s":5,"end_s":45,"transcript_segment_ids":[0]}]}]}), provider="vis", model="vis-vl")
    prov=Vis()
    import app.production.narration as narr
    from app.content.model_resolver import ResolvedModelProvider
    orig=narr.resolve_model
    narr.resolve_model=lambda cid, mode="exact", try_next_on_failure=True: ResolvedModelProvider(canonical_model_id=cid, provider="vis", provider_model_id="vis-vl", provider_instance=prov, attempted=())
    try:
        art=narr.build_grounded_narration_plan(PID, UID, asset, tr, target_duration_seconds=30.0, canonical_model_id="summary-pro-gemini-2.0-flash", summary_mode="pro", root=tmp_path)
    finally:
        narr.resolve_model=orig
    assert any("silent" in b.narration_text.lower() or "discover" in b.narration_text.lower() for b in art.beats)

def test_long_form_windows_cover_entire_duration(tmp_path):
    merged=_make_long_source(tmp_path, duration_per_seg=120.0, n_segs=5)
    asset=_reg(tmp_path, merged)
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"seg {i}", start=i*30.0, end=(i+1)*30.0) for i in range(20)], provider="mock", provider_version="1.0", created_at="2026-10-07T00:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    from app.content.providers.base import LLMResponse
    class R:
        name="r"; model="r-vl"
        def is_available(self): return True
        def supports_multimodal(self): return True
        def generate_multimodal(self, prompt, system_prompt=None, *, video=None, images=None, video_bytes=None, **kw):
            return LLMResponse(content=json.dumps({"visual_summary":"vis"}), provider="r", model="r-vl")
        def generate(self, prompt, system_prompt=None, **kw):
            return LLMResponse(content=json.dumps({"summary":"s","beats":[{"beat_id":"b1","title":"t","narration_text":"narr","source_refs":[{"asset_id":"src-001","start_s":0,"end_s":5,"transcript_segment_ids":[0]}]}]}), provider="r", model="r-vl")
    prov=R()
    import app.production.narration as narr
    from app.content.model_resolver import ResolvedModelProvider
    orig=narr.resolve_model
    narr.resolve_model=lambda cid, mode="exact", try_next_on_failure=True: ResolvedModelProvider(canonical_model_id=cid, provider="r", provider_model_id="r-vl", provider_instance=prov, attempted=())
    try:
        art=narr.build_grounded_narration_plan(PID, UID, asset, tr, target_duration_seconds=30.0, summary_mode="pro", root=tmp_path)
    finally:
        narr.resolve_model=orig
    ws=getattr(art, "_pro_window_summaries", [])
    assert 5 <= len(ws) <= 6
    assert ws[0]["start_s"] == 0
    assert abs(ws[-1]["end_s"] - 600.0) < 1.0
    for a,b in zip(ws, ws[1:]):
        assert abs(a["end_s"] - b["start_s"]) < 0.05

def test_grounding_and_phase2_3_compat(tmp_path):
    merged=_make_long_source(tmp_path, duration_per_seg=10.0, n_segs=2)
    asset=_reg(tmp_path, merged)
    tr=SourceTranscript(asset_id="src-001", language="en", segments=[TranscriptSegment(text=f"line {i}", start=i*2.0, end=(i+1)*2.0) for i in range(10)], provider="mock", provider_version="1.0", created_at="2026-10-07T00:00:00+00:00", transcript_path="source/src-001.transcript.json")
    (production_dir(PID, root=tmp_path)/"source/src-001.transcript.json").write_text(json.dumps(tr.model_dump()), encoding="utf-8")
    art=build_grounded_narration_plan(PID, UID, asset, tr, target_duration_seconds=6.0, mock_mode=True, summary_mode="standard", root=tmp_path)
    from app.production.narration import synthesize_narration_audio
    from app.production.summary_video import assemble_narrated_summary, summary_video_output_path
    from app.production.storage import save_narration_artifact, load_narration_artifact
    save_narration_artifact(art, production_id=PID, unit_id=UID, root=tmp_path, source_asset=asset, source_transcript=tr)
    synth=synthesize_narration_audio(load_narration_artifact(PID, UID, root=tmp_path), root=tmp_path, provider_name="mock", voice="mock-voice-1", output_format="mp3")
    assert synth.duration_seconds > 0
    res=assemble_narrated_summary(PID, UID, root=tmp_path)
    out=summary_video_output_path(PID, UID, root=tmp_path)
    assert out.is_file()
    info=probe_media(out)
    assert info.has_video and info.has_audio
