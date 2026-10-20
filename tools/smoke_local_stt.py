#!/usr/bin/env python
"""Manual smoke: REAL local Whisper STT against REAL media. Not part of pytest."""
import argparse, json, os, sys, tempfile
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
def synth_tts_video(path: Path, text: str, seconds=5.0):
    import asyncio, edge_tts
    from app.assembly.ffmpeg import run_ffmpeg
    wav = path.with_suffix(".wav")
    async def _s():
        await edge_tts.Communicate(text=text, voice="en-US-AriaNeural").save(str(wav))
    asyncio.run(_s())
    assert wav.exists() and wav.stat().st_size > 1000
    tmp = path.with_suffix(".h264.mp4")
    run_ffmpeg(["-y","-f","lavfi","-i",f"color=c=blue:s=320x240:r=15:d={seconds}","-c:v","libx264","-pix_fmt","yuv420p","-t",str(seconds),str(tmp)], "color")
    run_ffmpeg(["-y","-i",str(tmp),"-i",str(wav),"-c:v","copy","-c:a","aac","-shortest",str(path)], "mux")
    for p in [wav, tmp]:
        try: p.unlink()
        except: pass
    return path
def main():
    import argparse, json, os, sys, tempfile, time
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=None)
    ap.add_argument("--model", default=os.getenv("AVM_WHISPER_MODEL","tiny"))
    args = ap.parse_args()
    os.environ["AVM_WHISPER_MODEL"] = args.model
    from app.web import server as web
    from app.production.source import register_source
    from app.transcription.transcribe import transcribe_source, load_transcript
    from app.production.storage import transcript_path_for
    from app.assembly.ffmpeg import resolve_ffmpeg, probe_media
    ff = resolve_ffmpeg()
    print(f"FFmpeg: {ff} exists={Path(ff).exists() if ff else False}")
    import whisper, torch
    print(f"whisper {whisper.__file__} torch {torch.__version__} cuda={torch.cuda.is_available()}")
    if args.source:
        use_src = Path(args.source)
        assert use_src.is_file()
        tmp_root = Path(tempfile.mkdtemp(prefix="avm_smoke_"))
        prod_root = tmp_root / "productions"
        orig = web.PRODUCTIONS_DIR
        web.PRODUCTIONS_DIR = prod_root
        try:
            s, payload = web.create_production_payload({"mode":"single_video","creation_method":"local","title":"Smoke","brief":"smoke","output_intent":"youtube_shorts","target_duration":30,"scene_count":3})
            assert s==201
            pid = payload["production_id"]
        finally:
            web.PRODUCTIONS_DIR = orig
    else:
        from app.config import PRODUCTIONS_DIR
        cands = list(Path(PRODUCTIONS_DIR).rglob("source/*.mp4")) if Path(PRODUCTIONS_DIR).exists() else []
        tmp_root = Path(tempfile.mkdtemp(prefix="avm_smoke_"))
        prod_root = tmp_root / "productions"
        orig = web.PRODUCTIONS_DIR
        web.PRODUCTIONS_DIR = prod_root
        try:
            s, payload = web.create_production_payload({"mode":"single_video","creation_method":"local","title":"Smoke","brief":"smoke","output_intent":"youtube_shorts","target_duration":30,"scene_count":3})
            assert s==201
            pid = payload["production_id"]
        finally:
            web.PRODUCTIONS_DIR = orig
        if cands:
            cands.sort(key=lambda p: p.stat().st_size)
            reuse = cands[0]
            print(f"Reusing {reuse}")
            status, reg = register_source(pid, str(reuse), root=prod_root)
            assert status==201
            use_src = prod_root / pid / reg["stored_path"]
        else:
            synth_tmp = tmp_root / "smoke_source.mp4"
            text = "Hello from AVM. This is a real local whisper smoke test."
            print(f"Synthesizing TTS: {text!r}")
            synth_tts_video(synth_tmp, text, seconds=5)
            status, reg = register_source(pid, str(synth_tmp), root=prod_root)
            assert status==201
            use_src = prod_root / pid / reg["stored_path"]
            print(f"Fixture -> {use_src}")
    # register if --source branch not yet registered
    if args.source:
        status, reg = register_source(pid, str(use_src), root=prod_root)
        if status != 201:
            # already registered? use_src is original path still need to register
            status, reg = register_source(pid, str(Path(args.source)), root=prod_root)
        assert status==201, reg
        use_src = prod_root / pid / reg["stored_path"]
    print(f"Production {pid} root={root if (root:=prod_root) else ''}")
    print(f"Source {use_src} size={use_src.stat().st_size}")
    info = probe_media(use_src)
    print(f"duration={info.duration} has_audio={info.has_audio} codec={info.audio_codec}")
    t0 = time.time()
    code, payload = transcribe_source(pid, "src-001", root=prod_root, provider="local_whisper")
    dt = time.time()-t0
    print(f"transcribe -> {code} in {dt:.1f}s")
    print(json.dumps(payload, indent=2, ensure_ascii=False)[:4000])
    if code != 201:
        sys.exit(1)
    tpath = transcript_path_for(pid, "src-001", root=prod_root)
    print(f"Persisted {tpath} exists={tpath.exists()}")
    tr = load_transcript(pid, "src-001", root=prod_root)
    print(f"Reloaded {len(tr.segments)} segs provider={tr.provider} model={tr.provider_version} lang={tr.language}")
    for i, s in enumerate(tr.segments[:6]):
        print(f"  [{i}] {s.start:.2f}->{s.end:.2f} {s.text!r}")
    assert all(0 <= s.start < s.end <= info.duration+1e-6 for s in tr.segments)
    assert all(s.text.strip() for s in tr.segments)
    assert tr.provider == "local_whisper"
    assert len(tr.segments) >= 1
    print("SMOKE PASS: real local STT verified")
    print(f"Artifacts under {prod_root}")
if __name__ == "__main__":
    main()
