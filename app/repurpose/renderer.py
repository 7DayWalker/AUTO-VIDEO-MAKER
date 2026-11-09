"""Bounded source cuts, one final vertical encode and measured audio acceptance."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from app.assembly.ffmpeg import get_ffmpeg_runner, probe_media, run_ffmpeg
from app.production.storage import compute_content_hash, production_dir, production_revision_lock
from app.production.synthesis import BROADCAST_VOCAL_CHAIN, measured_loudnorm_filter, measure_audio_loudness
from app.repurpose.cache import load_evidence
from app.repurpose.captions import build_ass, group_words, narration_words, transcript_captions
from app.repurpose.models import RenderArtifact, RepurposeError
from app.repurpose.storage import atomic_json, content_hash, load_render, load_unit_plan, read_json, unit_dir


def framing_filter(mode):
    if mode == "center_crop":
        return ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase:force_divisible_by=2,"
                "crop=1080:1920,setsar=1,ass=filename='captions.ass'[v]")
    if mode != "cinema_canvas":
        raise ValueError("Unknown framing preset")
    return (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
        "gblur=sigma=14,eq=brightness=-0.16:saturation=0.65,scale=1080:1920[canvas];"
        "[fg]scale=1080:1280:force_original_aspect_ratio=decrease:force_divisible_by=2[front];"
        "[canvas][front]overlay=x=(W-w)/2:y=(H-h)/2,setsar=1,ass=filename='captions.ass'[v]"
    )


def render_short(production_id, unit_id, *, root=None):
    # Serialization protects a unit from two batches/processes replacing it.
    unit_dir(production_id, unit_id, root)
    with production_revision_lock(unit_id, production_dir(production_id, root)/"units"):
        return _render_short(production_id, unit_id, root=root)


def _render_short(production_id, unit_id, *, root=None):
    production, unit, plan, clip = load_unit_plan(production_id, unit_id, root)
    directory = unit_dir(production_id, unit_id, root)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        previous = load_render(production_id, unit_id, root)
        media = directory / "short.mp4"
        if previous.plan_hash == content_hash(plan) and media.is_file() and compute_content_hash(media) == previous.file_fingerprint:
            return previous
    except FileNotFoundError:
        pass
    if (directory / "promotion.json").is_file():
        raise RepurposeError("ACCEPTED_MASTER_PROTECTED", "Use a new unit for changed accepted media")
    evidence = load_evidence(production_id, plan.request.source_asset_id, plan.request.source_unit_id, root=root)
    if evidence.revision != plan.request.evidence_revision or evidence.asset.sha256 != plan.source_sha256:
        raise RepurposeError("STALE_EVIDENCE", "Source evidence changed after planning")
    if compute_content_hash(evidence.media) != plan.source_sha256:
        raise RepurposeError("SOURCE_FINGERPRINT_CHANGED", "Cached source bytes changed")
    source_info = probe_media(evidence.media)
    if not source_info.has_video or (plan.request.mode == "raw_highlight" and not source_info.has_audio):
        raise RepurposeError("SOURCE_STREAM_REQUIRED", "Source requires video and raw highlights require original audio", 422)
    duration = sum(c.end_seconds-c.start_seconds for c in clip.cuts)
    entries, precision, speed = [], "segment_estimated_phrases", 1.0
    narration = directory / "narration.mp3"
    if plan.request.mode == "narrated":
        from app.voice.providers.edge_tts import EdgeTTSProvider

        marker = narration.with_suffix(".mp3.capture-timing")
        timing = narration.with_suffix(".mp3.timing.json")
        if not narration.is_file() or not timing.is_file():
            timing.unlink(missing_ok=True)
            marker.touch()
            try:
                asyncio.run(EdgeTTSProvider().generate(clip.narration_text, narration,
                    voice="en-US-AndrewMultilingualNeural", rate="+15%"))
            finally:
                marker.unlink(missing_ok=True)
        original_duration = probe_media(narration).duration
        speed = max(.85, min(1.20, original_duration / plan.request.target_seconds))
        duration = original_duration / speed
        if not 30 <= duration <= 60:
            raise RepurposeError("NARRATION_DURATION_OUT_OF_BOUNDS", "Complete narration falls outside 30–60s; no speech was truncated", 422)
        words = narration_words(read_json(timing)["boundaries"], speed=speed, duration=duration)
        if not words:
            raise RepurposeError("NARRATION_TIMING_MISSING", "TTS did not retain word boundaries", 422)
        entries = [{"start": g[0]["start"], "end": g[-1]["end"],
                    "text": " ".join(w["text"] for w in g), "words": g} for g in group_words(words)]
        precision = "word"
    total_source = sum(c.end_seconds-c.start_seconds for c in clip.cuts)
    output_cuts, offset = [], 0.0
    for cut in clip.cuts:
        output_duration = (cut.end_seconds-cut.start_seconds) * duration / total_source
        output_cuts.append({**cut.model_dump(mode="json"), "output_start": offset, "output_duration": output_duration})
        offset += output_duration
    if plan.request.mode == "raw_highlight":
        entries, precision = transcript_captions(evidence.transcript, output_cuts)
    if not entries:
        raise RepurposeError("CAPTIONS_UNAVAILABLE", "Selected clips have no caption timing", 422)
    with tempfile.TemporaryDirectory(prefix=".render-", dir=directory) as temporary:
        scratch = Path(temporary)
        ass = build_ass(clip.hook, entries, duration)
        (scratch / "captions.ass").write_text(ass, encoding="utf-8")
        concat_lines = []
        for index, cut in enumerate(output_cuts):
            name = f"cut{index:02d}.mkv"
            factor = cut["output_duration"]/(cut["end_seconds"]-cut["start_seconds"])
            args = ["-hide_banner", "-loglevel", "error", "-y", "-threads", "2", "-ss", f"{cut['start_seconds']:.6f}",
                "-t", f"{cut['end_seconds']-cut['start_seconds']:.6f}", "-i", str(evidence.media)]
            if not source_info.has_audio:
                args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            args += ["-map", "0:v:0", "-map", "0:a:0" if source_info.has_audio else "1:a:0",
                "-vf", f"scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,setpts=(PTS-STARTPTS)*{factor:.9f},fps=30",
                "-af", f"asetpts=PTS-STARTPTS,atempo={1/factor:.9f},apad,atrim=duration={cut['output_duration']:.6f}",
                "-t", f"{cut['output_duration']:.6f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", "-threads", "2", name]
            run_ffmpeg(args, "rendering a bounded source cut", cwd=scratch, timeout=300)
            concat_lines.append(f"file '{name}'")
        (scratch / "cuts.txt").write_text("\n".join(concat_lines), encoding="ascii")
        edit = scratch / "edit.mkv"
        run_ffmpeg(["-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "1", "-i", "cuts.txt",
            "-c", "copy", str(edit)], "joining selected source cuts", cwd=scratch, timeout=300)
        mix = scratch / "mix.wav"
        if plan.request.mode == "narrated":
            voice = scratch / "voice.wav"
            run_ffmpeg(["-y", "-i", str(narration), "-af", f"atempo={speed:.9f},{BROADCAST_VOCAL_CHAIN}",
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(voice)], "preparing complete short narration", timeout=180)
            normalized = scratch / "voice-master.wav"
            run_ffmpeg(["-y", "-i", str(voice), "-af", measured_loudnorm_filter(measure_audio_loudness(voice)),
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(normalized)], "mastering Andrew short narration", timeout=180)
            graph = ("[1:a]asplit=2[narrator][sc];[0:a]volume=0.05,highpass=f=100,lowpass=f=8000[bed];"
                "[bed][sc]sidechaincompress=threshold=0.08:ratio=2:attack=12:release=180:knee=2:makeup=1[duck];"
                "[narrator][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
            run_ffmpeg(["-y", "-i", str(edit), "-i", str(normalized), "-filter_complex", graph, "-map", "[mix]",
                "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(mix)], "mixing narration with 0.05 ducked movie audio", timeout=180)
        else:
            run_ffmpeg(["-y", "-i", str(edit), "-vn", "-c:a", "pcm_s16le", str(mix)], "preparing original highlight audio", timeout=180)
        levels = measure_audio_loudness(mix)
        output = scratch / "short.mp4"
        run_ffmpeg(["-hide_banner", "-y", "-threads", "2", "-i", str(edit), "-i", str(mix),
            "-filter_complex_threads", "2", "-filter_complex", framing_filter(plan.request.framing), "-map", "[v]", "-map", "1:a:0",
            "-af", measured_loudnorm_filter(levels), "-t", f"{duration:.6f}", "-r", "30", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-threads", "2", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(output)],
            "encoding captioned 1080x1920 short", cwd=scratch, timeout=600)
        final_levels = measure_audio_loudness(output)
        delta = -14 - (final_levels.get("integrated_lufs") or -99)
        if .5 < abs(delta) <= 1 and final_levels.get("true_peak_db") is not None:
            corrected = scratch / "corrected.mp4"
            run_ffmpeg(["-y", "-i", str(output), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                "-af", f"volume={delta:.5f}dB,alimiter=limit=0.84:level=0", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", str(corrected)], "correcting encoded short loudness", timeout=180)
            os.replace(corrected, output)
            final_levels = measure_audio_loudness(output)
        if (final_levels.get("integrated_lufs") is None or abs(final_levels["integrated_lufs"]+14) > .5
                or final_levels.get("true_peak_db") is None or final_levels["true_peak_db"] > -1):
            raise RepurposeError("SHORT_AUDIO_VALIDATION_FAILED", "Encoded audio did not meet −14 LUFS ±0.5 / ≤−1 dBTP", 422)
        info = probe_media(output)
        if not info.has_video or not info.has_audio or (info.width, info.height) != (1080, 1920) or not 30 <= info.duration <= 60.1:
            raise RepurposeError("SHORT_MEDIA_VALIDATION_FAILED", "Encoded short failed resolution, audio or duration validation", 422)
        decoded = run_ffmpeg(["-v", "error", "-xerror", "-threads", "2", "-i", str(output), "-map", "0:v:0", "-map", "0:a:0",
            "-f", "null", "-"], "verifying complete short decoding", timeout=180)
        if decoded.stderr.strip():
            raise RepurposeError("SHORT_DECODE_FAILED", "Encoded short reported decode errors", 422)
        artifact = RenderArtifact(production_id=production_id, unit_id=unit_id, batch_id=plan.batch_id,
            plan_hash=content_hash(plan), mode=plan.request.mode, framing=plan.request.framing,
            output_path=f"units/{unit_id}/repurpose/short.mp4", file_fingerprint=compute_content_hash(output),
            file_size=output.stat().st_size, duration_seconds=info.duration, cuts=output_cuts,
            captions_precision=precision, loudness=final_levels,
            validation={"valid": True, "decode_errors": 0, "captions_burned": True, "hook_banner_burned": True,
                "video_codec": info.video_codec, "audio_codec": info.audio_codec, "voice": "en-US-AndrewMultilingualNeural" if narration.is_file() else None,
                "voice_rate": "+15%" if plan.request.mode == "narrated" else None, "tempo_factor": speed,
                "background_volume": .05 if plan.request.mode == "narrated" else 1.0,
                "audio_mix_method": "narration_ducked_movie" if plan.request.mode == "narrated" else "original_audio",
                "source_sha256": plan.source_sha256})
        os.replace(output, directory / "short.mp4")
        (directory / "captions.ass").write_text(ass, encoding="utf-8")
        atomic_json(directory / "caption_timing.json", {"precision": precision, "entries": entries, "duration": duration})
        atomic_json(directory / "render.json", artifact)
        return artifact