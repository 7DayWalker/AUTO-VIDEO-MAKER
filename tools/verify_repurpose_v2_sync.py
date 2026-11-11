"""Real synthetic frame/audio acceptance, zero LLM/STT/vision/TTS networking."""
from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from app.assembly.ffmpeg import run_ffmpeg, get_ffmpeg_runner, resolve_ffmpeg, resolve_ffprobe
    from app.production.models import Production, ProductionCommand, ProductionUnit, SourceAsset, SourceTranscript, TranscriptSegment
    from app.production.storage import production_source_dir, save_new_production, commit_repurpose_batch, compute_content_hash
    from app.repurpose.models import BatchRequest, RepurposePlan, ClipIntent, StoryBeat, SourceCut
    from app.repurpose.renderer import render_short
    from app.repurpose.storage import atomic_json, content_hash
    from app.repurpose.cache import load_evidence
    from app.repurpose.preparation import save_anchors
    from app.repurpose.timeline import resolve_timeline, SAMPLE_RATE, PIPELINE_VERSION
    runner = get_ffmpeg_runner()
    runner.ffmpeg_path, runner.ffprobe_path = resolve_ffmpeg(), resolve_ffprobe()
    runner._ffmpeg_available = runner._ffprobe_available = None
    report_path = ROOT / "diagnostics/repurpose-v2-synthetic-sync.json"
    with tempfile.TemporaryDirectory(prefix="avm-v2-sync-") as scratch:
        root = Path(scratch); pid = "prd_20261110_v2_sync_fixture_001"
        source = production_source_dir(pid, root); source.mkdir(parents=True)
        media = source / "src-001.mp4"
        colors = ("red", "green", "blue", "yellow")
        args = ["-y"]
        for color in colors:
            args += ["-f", "lavfi", "-i", f"color=c={color}:s=640x360:r=30:d=20"]
        graph = ";".join(f"[{i}:v]drawbox=x=20:y=20:w=80:h=80:color=white:t=fill:enable='lt(t,0.10)'[v{i}]" for i in range(4))
        graph += ";[v0][v1][v2][v3]concat=n=4:v=1:a=0[out]"
        run_ffmpeg([*args, "-filter_complex", graph, "-map", "[out]", "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", str(media)],
            "creating timestamp-coded synthetic source", timeout=180)
        asset = SourceAsset(asset_id="src-001", stored_path="source/src-001.mp4", original_filename="synthetic.mp4",
            sha256=compute_content_hash(media), duration=80, file_size=media.stat().st_size, source_kind="local_file", ingested_at="2026-11-10")
        save_new_production(Production(production_id=pid, title="Synthetic v2 sync", command=ProductionCommand(text="Offline fixture",
            workload_shape="single_video", issued_at="2026-11-10"), units=[ProductionUnit(unit_id="unit-001", ordinal=1,
            unit_type="standalone_video", title="Fixture")], source_assets=[asset], created_at="2026-11-10"), root=root)
        atomic_json(source / "src-001.transcript.json", SourceTranscript(asset_id="src-001", provider="synthetic_fixture",
            language="en", created_at="2026-11-10", transcript_path="source/src-001.transcript.json",
            segments=[TranscriptSegment(text="Synthetic timing fixture.", start=i * 20, end=i * 20 + 15) for i in range(4)]))
        evidence = load_evidence(pid, "src-001", root=root)
        save_anchors(pid, "src-001", {"confirm": True, "evidence_revision": evidence.revision,
            "anchors": [{"action": f"{c} scene is visible", "start_seconds": i * 20, "end_seconds": i * 20 + 15} for i, c in enumerate(colors)]}, root=root)
        evidence = load_evidence(pid, "src-001", root=root, include_prepared=True)
        samples = [480001, 432123, 528017, 480500]
        beats = [StoryBeat(order=i + 1, spoken_text=f"This is the {color} scene.", evidence_id=evidence.anchors[i]["evidence_id"],
            focal_source_seconds=i * 20, coverage_end_seconds=i * 20 + 15) for i, color in enumerate(colors)]
        clip = ClipIntent(title="Synthetic clock", hook=beats[0].spoken_text, rationale="Frame boundary verification.", beats=beats,
            narration_text=" ".join(b.spoken_text for b in beats), cuts=[SourceCut(start_seconds=b.focal_source_seconds,
                end_seconds=b.coverage_end_seconds, transcript_segment_ids=[i]) for i, b in enumerate(beats)])
        request = BatchRequest(request_id="synthetic-v2-clock", confirm=True, source_asset_id="src-001", evidence_revision=evidence.revision,
            story_version=2, allow_overlap=True, target_seconds=45)
        plan = commit_repurpose_batch(RepurposePlan(schema_version=2, production_id=pid, batch_id="rpb-" + "d" * 24,
            request=request, source_sha256=asset.sha256, transcript_sha256=evidence.identity["transcript_sha256"],
            clips=[clip.model_copy(deep=True) for _ in range(3)]), root=root)
        def synthetic_audio(clip, directory, **kwargs):
            cuts = resolve_timeline(clip.beats, samples); audio = directory / "narration.wav"
            with wave.open(str(audio), "wb") as stream:
                stream.setnchannels(2); stream.setsampwidth(2); stream.setframerate(SAMPLE_RATE)
                for count, cut in zip(samples, cuts):
                    pcm = bytearray()
                    for n in range(count):
                        amplitude = .35 if n < 4800 else .09
                        value = round(32767 * amplitude * math.sin(2 * math.pi * 700 * n / SAMPLE_RATE))
                        pcm.extend(struct.pack("<hh", value, value))
                    stream.writeframes(pcm); stream.writeframes(b"\0" * cut["tail_padding_samples"] * 4)
            entries = [{"start": c["output_start"], "end": c["output_start"] + 1, "text": b.spoken_text, "words": None}
                for b, c in zip(clip.beats, cuts)]
            return audio, cuts, entries
        with patch("app.repurpose.renderer._beat_audio", synthetic_audio):
            artifact = render_short(pid, plan.clips[0].unit_id, root=root)
        output = root / pid / artifact.output_path
        decoded = root / "decoded.wav"
        run_ffmpeg(["-y", "-i", str(output), "-vn", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1", str(decoded)],
            "measuring encoded synthetic beat markers", timeout=180)
        with wave.open(str(decoded), "rb") as stream: pcm = stream.readframes(stream.getnframes())
        values = struct.unpack("<" + "h" * (len(pcm) // 2), pcm)
        rows = []
        for cut in artifact.cuts:
            expected = cut["output_start_frame"] * 1600
            left = max(0, expected - 1600); right = min(len(values), expected + 3200)
            onset = next((n for n in range(left, right - 48) if sum(v * v for v in values[n:n + 48]) / 48 > 7000**2), None)
            if onset is None: raise RuntimeError("Synthetic audio marker not detected")
            delta = (onset - expected) / 48
            if abs(delta) > 1000 / 30: raise RuntimeError("Synthetic frame/audio marker exceeds one frame")
            rows.append({"beat": cut["beat_order"], "output_start_frame": cut["output_start_frame"], "frame_count": cut["frame_count"],
                "expected_audio_sample": expected, "measured_marker_sample": onset, "delta_ms": delta})
        report = {"ok": True, "pipeline_version": PIPELINE_VERSION, "llm_calls": 0, "stt_calls": 0, "vision_calls": 0,
            "live_tts_calls": 0, "duration_seconds": artifact.duration_seconds, "beats": rows,
            "maximum_boundary_delta_ms": max(abs(r["delta_ms"]) for r in rows)}
        atomic_json(report_path, report)
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()