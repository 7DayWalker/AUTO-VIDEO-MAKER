"""Pro hierarchical multimodal understanding (bounded windows, bounded visual evidence)."""
from __future__ import annotations
import json, math, re, shutil, tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from app.production.models import NarrativeBeat, NarrationArtifact, NarrationSourceRef, SourceAsset, SourceTranscript

def _src_arg(source_asset: SourceAsset, production_id: str, root: Optional[Path]=None) -> Path:
    from app.production.storage import production_dir
    rel = Path(source_asset.stored_path)
    base = production_dir(production_id, root=root)
    p = base / rel
    if p.is_file():
        return p
    cand = Path(source_asset.stored_path)
    if cand.is_file():
        return cand
    return p

def _seg_in(tr: SourceTranscript, ws: float, we: float):
    return [s for s in tr.segments if s.start < we and s.end > ws]

def _extract_window_clip(src: Path, ws: float, we: float, out_dir: Path, window_idx: int) -> Optional[Path]:
    if not src.is_file():
        return None
    duration = max(0.1, float(we) - float(ws))
    out = out_dir / f"window_{window_idx:03d}_{ws:.1f}_{we:.1f}.mp4"
    from app.assembly.ffmpeg import get_ffmpeg_runner
    runner = get_ffmpeg_runner()
    try:
        runner.run(["-y","-ss",f"{ws:.3f}","-t",f"{duration:.3f}","-i",str(src),"-c","copy","-movflags","+faststart",str(out)], f"Pro window clip {window_idx} copy")
        if out.is_file() and out.stat().st_size > 1024:
            return out
    except Exception:
        pass
    try:
        runner.run(["-y","-ss",f"{ws:.3f}","-t",f"{duration:.3f}","-i",str(src),"-c:v","libx264","-preset","ultrafast","-crf","23","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-movflags","+faststart",str(out)], f"Pro window clip {window_idx} re-encode")
        if out.is_file() and out.stat().st_size > 1024:
            return out
    except Exception:
        pass
    if out.is_file():
        try:
            out.unlink()
        except Exception:
            pass
    return None

def _probe_clip_duration(p: Path) -> Optional[float]:
    try:
        from app.assembly.ffmpeg import probe_media
        info = probe_media(p)
        return float(info.duration) if info.duration else None
    except Exception:
        return None

# --- TRACK 9: frame-based visual analysis seam -------------------------------
# Some canonical models (e.g. claude-opus-5.5 via CodeCraft, gpt-4o) verify
# IMAGE input but have NO established native-video ingestion. For those, the
# SAME bounded window is represented as timestamped stills instead of an MP4.
# This is an extra representation of the EXISTING window -- not a second
# pipeline: window boundaries, transcript pairing and evidence recording below
# are untouched.

#: Frames sampled per bounded window for frame-based analysis.
PRO_FRAMES_PER_WINDOW = 4
#: Hard ceiling so payloads stay inside real provider limits.
PRO_FRAME_WIDTH = 640


def _provider_uses_native_video(prov) -> bool:
    """Whether this provider can ingest the window CLIP itself.

    Providers advertise an explicit ``supports_native_video()`` when they have
    something truthful to say. Absent that, the historical assumption (native
    video) is preserved so existing Gemini/Alibaba behaviour is unchanged.
    """
    probe = getattr(prov, "supports_native_video", None)
    if callable(probe):
        try:
            return bool(probe())
        except Exception:
            return True
    return True


def _extract_window_frames(src: Path, ws: float, we: float, out_dir: Path, window_idx: int):
    """Extract TIMESTAMPED frames spanning the bounded window [ws, we].

    Returns ``[(timestamp_seconds, path), ...]``. Timestamps are real source
    offsets, so grounding survives into the execution record.
    """
    if not src.is_file():
        return []
    span = max(0.0, float(we) - float(ws))
    count = max(1, int(PRO_FRAMES_PER_WINDOW))
    if span <= 0:
        stamps = [float(ws)]
    else:
        # Interior sampling: avoids the exact boundary frames, which are often
        # black/transitional.
        step = span / (count + 1)
        stamps = [float(ws) + step * (k + 1) for k in range(count)]

    from app.assembly.ffmpeg import get_ffmpeg_runner
    runner = get_ffmpeg_runner()
    frames = []
    for k, ts in enumerate(stamps):
        out = out_dir / f"window_{window_idx:03d}_frame_{k:02d}_{ts:.1f}.jpg"
        try:
            runner.run(
                ["-y", "-ss", f"{ts:.3f}", "-i", str(src), "-frames:v", "1",
                 "-vf", f"scale={PRO_FRAME_WIDTH}:-2", "-q:v", "5", str(out)],
                f"Pro window {window_idx} frame {k} @ {ts:.1f}s",
            )
        except Exception:
            continue
        if out.is_file() and out.stat().st_size > 1024:
            frames.append((round(ts, 2), str(out)))
    return frames


def build_pro_multimodal_plan(production_id: str, unit_id: str, source_asset: SourceAsset, source_transcript: SourceTranscript, target_duration_seconds: float, *, summary_model_id: str, prov, root=None, recap_budget=None) -> NarrationArtifact:
    dur = float(source_asset.duration)
    n = max(1, min(math.ceil(dur/120.0), 64))
    step = dur / n
    window_summaries = []
    visual_all = []
    src_video = _src_arg(source_asset, production_id, root=root)
    tmp_root = Path(tempfile.mkdtemp(prefix="pro_window_"))
    multimodal_calls = 0
    # TRACK 9: decide the TRUTHFUL visual route ONCE, from provider capability.
    # native video -> window MP4 (existing behaviour, unchanged)
    # frame-based  -> timestamped stills from the SAME bounded window
    use_native_video = _provider_uses_native_video(prov)
    visual_method = "native_video_ingestion" if use_native_video else "frame_based_visual_analysis"
    try:
        for i in range(n):
            ws = i*step; we = min((i+1)*step, dur)
            segs = _seg_in(source_transcript, ws, we)
            ttext = "\n".join(f"[{s.start:.1f}-{s.end:.1f}] {s.text}" for s in segs) if segs else "[silence]"
            prompt = f"Analyze window [{ws:.0f}-{we:.0f}s] multimodally. Video is PRIMARY. Transcript:\n{ttext}\nList key visual events within [{ws:.0f},{we:.0f}s]."
            clip_path = None; clip_duration = None
            window_frames = []

            if not use_native_video:
                # FRAME-BASED route: same bounded window, represented as stills.
                window_frames = _extract_window_frames(src_video, ws, we, tmp_root, i)
                if window_frames:
                    stamp_list = ", ".join(f"{ts:.1f}s" for ts, _ in window_frames)
                    prompt = (
                        f"Analyze window [{ws:.0f}-{we:.0f}s] using the provided frames, "
                        f"sampled in chronological order at {stamp_list}. The FRAMES are "
                        f"PRIMARY visual evidence. Transcript:\n{ttext}\n"
                        f"List key visual events within [{ws:.0f},{we:.0f}s]."
                    )
                    resp = prov.generate_multimodal(
                        prompt,
                        system_prompt="You are performing frame-based visual analysis of a bounded video window.",
                        video=None, images=window_frames,
                    )
                else:
                    # No frames could be extracted -> fail truthfully rather than
                    # pretending a text-only call performed visual understanding.
                    raise ValueError(
                        f"frame-based visual analysis selected for window [{ws:.0f}-{we:.0f}s] "
                        f"but no frames could be extracted from the source"
                    )
            else:
                if src_video.is_file():
                    clip_path = _extract_window_clip(src_video, ws, we, tmp_root, i)
                    if clip_path and clip_path.is_file():
                        clip_duration = _probe_clip_duration(clip_path)
                video_arg = str(clip_path) if clip_path and clip_path.is_file() else None
                video_bytes = None
                if clip_path and clip_path.is_file():
                    try:
                        if clip_path.stat().st_size < 18*1024*1024:
                            video_bytes = clip_path.read_bytes()
                    except: video_bytes = None
                if video_arg is None and src_video.is_file():
                    resp = prov.generate_multimodal(prompt, system_prompt="You are multimodal video understanding.", video=None, images=None)
                else:
                    try:
                        kw={}
                        if video_bytes is not None: kw["video_bytes"]=video_bytes
                        resp = prov.generate_multimodal(prompt, system_prompt="You are multimodal video understanding.", video=video_arg, images=None, **kw)
                    except TypeError:
                        resp = prov.generate_multimodal(prompt, system_prompt="You are multimodal video understanding.", video=video_arg, images=None)
            multimodal_calls += 1
            vs = resp.content if hasattr(resp,"content") else str(resp)
            vs_text = vs
            try:
                j=json.loads(vs); vs_text=j.get("visual_summary") or j.get("summary") or vs
            except: pass
            vs_text_s=str(vs_text)[:900]
            visual_all.append(f"Window w{i} [{ws:.0f}-{we:.0f}s]: {vs_text_s}")
            summary_entry = {"window_id":f"w{i}","start_s":round(ws,2),"end_s":round(we,2),"clip_duration":clip_duration,"visual_evidence":vs_text_s,"has_speech":len(segs)>0}
            # TRUTHFUL method label on every window record.
            summary_entry["visual_method"] = visual_method
            if not use_native_video:
                summary_entry["frame_timestamps"] = [ts for ts, _ in window_frames]
                summary_entry["frame_count"] = len(window_frames)
            window_summaries.append(summary_entry)

        if recap_budget is not None and recap_budget.enforce_depth:
            from app.production.narration_real import ai_grounded_plan
            from app.content.model_registry import get_model
            artifact = ai_grounded_plan(production_id, unit_id, source_asset, source_transcript,
                target_duration_seconds=target_duration_seconds, prov=prov, recap_budget=recap_budget,
                output_attempts=2, visual_evidence=window_summaries,
                context_tokens=get_model(summary_model_id).capabilities.context_tokens)
            artifact.summary_understanding_model_id = summary_model_id
            object.__setattr__(artifact, "_pro_window_summaries", window_summaries)
            object.__setattr__(artifact, "_pro_multimodal_calls", multimodal_calls)
            object.__setattr__(artifact, "_pro_visual_method", visual_method)
            return artifact

        full_text="\n".join(s.text for s in source_transcript.segments)
        visual_combined="\n".join(visual_all)
        sys_prompt="You are grounded narrative planner. Output strictly as JSON."
        plan_prompt=f"Source duration: {dur:.1f}s, Target: {float(target_duration_seconds):.1f}s\nTranscript:\n{full_text[:9000]}\n\nVisual evidence:\n{visual_combined[:9000]}\n\nWindow evidence:\n{json.dumps(window_summaries, indent=2)[:14000]}\n\nTask: select important events, fit ~{float(target_duration_seconds):.0f}s. Output JSON: {{\"summary\": \"...\", \"beats\": [{{\"beat_id\":\"b1\",\"title\":\"...\",\"narration_text\":\"...\",\"source_refs\":[{{\"asset_id\":\"src-001\",\"start_s\":0,\"end_s\":10,\"transcript_segment_ids\":[0]}}]}}]}}"
        try:
            raw_resp=prov.generate(prompt=plan_prompt, system_prompt=sys_prompt)
            raw=raw_resp.content if hasattr(raw_resp,"content") else str(raw_resp)
            try:
                json.loads(raw)
            except:
                raw2=prov.generate(prompt=plan_prompt+"\n\nRespond with valid JSON only.", system_prompt=sys_prompt+" Respond with valid JSON only.")
                raw2s=raw2.content if hasattr(raw2,"content") else str(raw2)
                json.loads(raw2s); raw=raw2s
        except Exception:
            raw_resp2=prov.generate_multimodal(plan_prompt, system_prompt=sys_prompt, video=None, images=None)
            raw=raw_resp2.content if hasattr(raw_resp2,"content") else str(raw_resp2)
            multimodal_calls+=1
        s=raw.strip(); fence=chr(96)*3
        if s.startswith(fence):
            s=re.sub(r"^```(?:json)?\s*","",s,flags=re.IGNORECASE); s=re.sub(r"\s*```\s*$","",s); s=s.strip()
        a=s.find("{"); b=s.rfind("}")
        if a==-1 or b<=a:
            raise ValueError(f"Model output no JSON: {raw[:600]!r}")
        data=json.loads(s[a:b+1])
        beats_raw=data.get("beats")
        if not isinstance(beats_raw, list) or not beats_raw:
            raise ValueError("Model output must contain non-empty beats array")
        beats=[]; source_refs=[]; transcript_refs=[]; narration_parts=[]
        for idx,b in enumerate(beats_raw):
            bid=str(b.get("beat_id",f"b{idx+1}")); title=str(b.get("title",bid)); text=str(b.get("narration_text") or "").strip()
            if not text:
                raise ValueError(f"Beat {bid} empty narration")
            refs_raw=b.get("source_refs") or []
            if not refs_raw:
                raise ValueError(f"Beat {bid} has no source_refs")
            beat_refs=[]
            for r in refs_raw:
                asset_id=str(r.get("asset_id",source_asset.asset_id)); start_s=float(r.get("start_s",0)); end_s=float(r.get("end_s",start_s+5))
                tids=r.get("transcript_segment_ids") or r.get("transcript_ids") or []; tids_int=[int(x) for x in tids] if isinstance(tids,(list,tuple)) else []
                beat_refs.append(NarrationSourceRef(asset_id=asset_id, start_seconds=start_s, end_seconds=end_s, transcript_segment_ids=tids_int))
            beat=NarrativeBeat(order=idx+1, title=title or f"Beat {idx+1}", narration_text=text, summary=text[:180], source_refs=beat_refs, transcript_refs=[str(x) for rr in beat_refs for x in rr.transcript_segment_ids])
            beats.append(beat); source_refs.extend(beat_refs); transcript_refs.extend(beat.transcript_refs); narration_parts.append(text)
        artifact=NarrationArtifact(narration_id=f"narr-{production_id}-{unit_id}", production_id=production_id, unit_id=unit_id, source_asset_id=source_asset.asset_id, target_duration_seconds=float(target_duration_seconds), status="grounded", narration_text=" ".join(narration_parts), summary=str(data.get("summary") or "Condensed grounded narrative from multimodal understanding."), beats=beats, source_refs=source_refs, transcript_refs=sorted(set(transcript_refs), key=lambda x: int(x)) if transcript_refs else [], generated_at=datetime.now().isoformat())
        from app.production.narration import validate_narration_artifact
        validate_narration_artifact(artifact, source_asset=source_asset, source_transcript=source_transcript)
        try:
            object.__setattr__(artifact, "_pro_window_summaries", window_summaries)
            object.__setattr__(artifact, "_pro_multimodal_calls", multimodal_calls)
            # TRACK 9: truthful execution-method label for the whole run.
            object.__setattr__(artifact, "_pro_visual_method", visual_method)
        except: pass
        return artifact
    finally:
        try: shutil.rmtree(tmp_root, ignore_errors=True)
        except: pass
