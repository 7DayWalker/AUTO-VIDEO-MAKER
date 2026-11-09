"""Local synthetic render/handoff checks, never an LLM, Whisper or live TTS call.

Outputs are test fixtures in temp/, NOT the requested Dragon Queen acceptance.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from app.assembly.ffmpeg import FFmpegRunner, get_ffmpeg_runner, resolve_ffmpeg, resolve_ffprobe, run_ffmpeg, probe_media
    from app.production.models import Production, ProductionCommand, ProductionUnit, SourceAsset, SourceTranscript, TranscriptSegment
    from app.production.storage import compute_content_hash, commit_repurpose_batch, production_source_dir, save_new_production
    from app.repurpose.cache import load_evidence
    from app.repurpose.handoff import promote_short
    from app.repurpose.models import BatchRequest, PlannerOutput, RepurposePlan
    from app.repurpose.planner import validate_output
    from app.repurpose.renderer import render_short
    from app.repurpose.storage import atomic_json, batch_dir, content_hash, unit_dir
    from app.voice.providers.edge_tts import EdgeTTSProvider

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path(__file__).resolve().parents[1]/"temp/track8_synthetic/report.json")
    args = parser.parse_args()
    root = args.report.parent/str(time.time_ns())
    productions, projects = root/"productions", root/"projects"
    pid = "prd_20261109_repurpose_synthetic_001"
    source = production_source_dir(pid, productions)
    source.mkdir(parents=True)
    runner = get_ffmpeg_runner()
    runner.ffmpeg_path, runner.ffprobe_path = resolve_ffmpeg(), resolve_ffprobe()
    runner._ffmpeg_available = runner._ffprobe_available = None
    media = source/"src-001.mp4"
    run_ffmpeg(["-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000", "-t", "130", "-c:v", "libx264",
        "-preset", "ultrafast", "-crf", "25", "-threads", "2", "-c:a", "aac", "-ac", "2", str(media)],
        "generating hermetic synthetic source", timeout=240)
    asset = SourceAsset(asset_id="src-001", original_filename="synthetic.mp4", stored_path="source/src-001.mp4",
        sha256=compute_content_hash(media), duration=130, file_size=media.stat().st_size, ingested_at="2026-11-09", source_kind="local_file")
    production = Production(production_id=pid, title="Synthetic repurpose verification", command=ProductionCommand(
        text="Synthetic local renderer verification, not live acceptance", workload_shape="single_video", issued_at="2026-11-09"),
        units=[ProductionUnit(unit_id="unit-001", ordinal=1, unit_type="standalone_video", title="Synthetic source")],
        source_assets=[asset], created_at="2026-11-09")
    save_new_production(production, root=productions)
    transcript = SourceTranscript(asset_id="src-001", provider="synthetic_fixture", language="en", created_at="2026-11-09",
        transcript_path="source/src-001.transcript.json", segments=[TranscriptSegment(text=f"A synthetic scene demonstrates caption timing number {i}.",
            start=i*5, end=i*5+5) for i in range(26)])
    atomic_json(source/"src-001.transcript.json", transcript)
    evidence = load_evidence(pid, "src-001", root=productions)
    rows = []
    for index, (mode, framing) in enumerate((("raw_highlight", "center_crop"), ("raw_highlight", "cinema_canvas"), ("narrated", "cinema_canvas"))):
        request = BatchRequest(request_id=f"synthetic-{index:02d}-fixture", confirm=True, source_asset_id="src-001",
            evidence_revision=evidence.revision, mode=mode, count=3, target_seconds=30, framing=framing)
        output = PlannerOutput.model_validate({"clips": [{"title": f"Synthetic moment {n+1}", "hook": "A synthetic hook — renderer verification only",
            "rationale": "Synthetic source-timeline fixture, not a model-selected viral clip.",
            "narration_text": " ".join(["Synthetic timing verifies the complete narrator caption and sound chain."]*10) if mode == "narrated" else None,
            "cuts": [{"start_seconds": n*40+j*5, "end_seconds": n*40+j*5+5, "transcript_segment_ids": [n*8+j]} for j in range(6)]} for n in range(3)]})
        validate_output(output, evidence, request)
        plan = RepurposePlan(production_id=pid, batch_id="rpb-"+str(index+1)*24, request=request,
            source_sha256=asset.sha256, transcript_sha256=evidence.identity["transcript_sha256"], clips=output.clips)
        plan = commit_repurpose_batch(plan, root=productions)
        uid = plan.clips[0].unit_id
        async def synthetic_tts(self, text, output_path, **kwargs):
            run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=600:sample_rate=48000", "-t", "32",
                "-c:a", "libmp3lame", "-b:a", "192k", str(output_path)], "generating synthetic narrator timing fixture", timeout=60)
            atomic_json(output_path.with_suffix(".mp3.timing.json"), {"boundaries": [{"text": w, "start": .3+k*.27,
                "end": .3+k*.27+.21, "kind": "WordBoundary"} for k,w in enumerate(text.split())]})
        with patch.object(EdgeTTSProvider, "generate", synthetic_tts):
            artifact = render_short(pid, uid, root=productions)
        directory, manifest, _ = promote_short(pid, uid, root=productions, projects_root=projects)
        rows.append({"mode": mode, "framing": framing, "unit_id": uid, "project_id": directory.name,
            "duration": artifact.duration_seconds, "resolution": [artifact.width, artifact.height], "loudness": artifact.loudness,
            "decode_errors": artifact.validation["decode_errors"], "captions_precision": artifact.captions_precision})
        print("synthetic case passed", mode, framing, flush=True)
    report = {"ok": True, "synthetic_only": True, "root": str(root), "llm_calls": 0, "stt_calls": 0, "live_tts_calls": 0, "cases": rows}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()