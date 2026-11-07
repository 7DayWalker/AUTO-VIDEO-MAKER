"""Track 9 Phase 3 — grounded narrated summary video assembly."""
from __future__ import annotations

import hashlib, json, logging, math, os, shutil, tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.assembly.audio_contract import AudioPolicy
from app.assembly.ffmpeg import probe_media, resolve_ffmpeg, run_ffmpeg
from app.assembly.models import AssemblyProject, Scene, VideoResolution
from app.assembly.pipeline import assemble_project
from app.production.models import NarrationArtifact, NarrationAudioArtifact, SourceAsset, SourceTranscript
from app.production.media_concat import write_concat_manifest
from app.production.storage import (
    load_narration_artifact, load_narration_audio, load_production,
    compute_content_hash, production_dir, unit_plan_dir,
)

logger = logging.getLogger(__name__)
SUMMARY_VIDEO_FILE = "narrated_summary.json"
SUMMARY_VIDEO_OUTPUT_BASENAME = "narrated_summary.mp4"

def _is_relative_path(value: str) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if value.startswith(("/", "\\", "~")):
        return False
    import re
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return False
    if ".." in [seg for seg in value.replace("\\", "/").split("/") if seg]:
        return False
    return True

class NarratedSummaryArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    production_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    source_asset_id: str = Field(pattern=r"^src-\d{3}$")
    narration_id: str = Field(pattern=r"^narr-[a-z0-9_\-]+$")
    narration_audio_path: str = Field(min_length=1)
    source_refs_count: int = Field(ge=0)
    output_path: str = Field(min_length=1)
    output_duration_seconds: float = Field(gt=0.0)
    video_width: Optional[int] = Field(default=None, ge=1)
    video_height: Optional[int] = Field(default=None, ge=1)
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    source_duration_seconds: float = Field(gt=0.0)
    target_duration_seconds: float = Field(gt=0.0)
    narration_duration_seconds: float = Field(gt=0.0)
    file_size: int = Field(ge=1)
    generated_at: str = Field(min_length=1)
    validation: Dict = Field(default_factory=dict)
    beat_count: Optional[int] = Field(default=None, ge=1)
    word_count: Optional[int] = Field(default=None, ge=1)
    chapter_count: Optional[int] = Field(default=None, ge=1)
    summary_mode: Optional[str] = None
    canonical_model: Optional[str] = None
    words_per_minute: Optional[float] = Field(default=None, gt=0)
    background_audio_mixed: bool = False
    background_audio_volume: float = Field(default=0.0, ge=0, le=1)
    audio_mix_method: Optional[str] = None
    audio_processing: Dict = Field(default_factory=dict)
    # Assembly-time fingerprints tie promotion to the bytes/script actually
    # assembled. Optional for existing, already signed-off Track 9 sidecars.
    file_fingerprint: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    narration_content_hash: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    @model_validator(mode="after")
    def _check(self) -> "NarratedSummaryArtifact":
        if not _is_relative_path(self.output_path):
            raise ValueError("output_path must be relative to the production root")
        if not _is_relative_path(self.narration_audio_path):
            raise ValueError("narration_audio_path must be relative to the production root")
        return self

def summary_video_metadata_path(production_id: str, unit_id: str, root: Optional[Path]=None) -> Path:
    return unit_plan_dir(production_id, unit_id, root=root) / SUMMARY_VIDEO_FILE

def summary_video_output_path(production_id: str, unit_id: str, root: Optional[Path]=None) -> Path:
    return unit_plan_dir(production_id, unit_id, root=root) / SUMMARY_VIDEO_OUTPUT_BASENAME

def load_narrated_summary(production_id: str, unit_id: str, root: Optional[Path]=None) -> NarratedSummaryArtifact:
    p = summary_video_metadata_path(production_id, unit_id, root=root)
    if not p.is_file():
        raise FileNotFoundError(f"narrated summary not found: {p}")
    return NarratedSummaryArtifact.model_validate(json.loads(p.read_text(encoding="utf-8")))


def narration_content_hash(artifact: NarrationArtifact) -> str:
    """Hash grounded content plus declared audio provenance, not timestamps.

    A newly selected/synthesized voice must not be attributed to an older MP4
    that still contains the previous voice. Reassembly establishes that link.
    """
    fields = {
        "production_id", "unit_id", "narration_id", "source_asset_id",
        "narration_text", "summary", "beats", "source_refs", "transcript_refs",
        "audio_artifact_path", "audio_duration_seconds", "audio_provider",
        "audio_voice", "audio_format", "audio_status",
    }
    if artifact.source_transcript_path is not None:
        fields.add("source_transcript_path")
    if artifact.source_media_path is not None:
        fields.add("source_media_path")
    if artifact.ending_anchor is not None:
        fields.add("ending_anchor")
    if artifact.cold_open_hook is not None:
        fields.add("cold_open_hook")
    content = artifact.model_dump(mode="json", include=fields)
    # Empty additive visual fields must not change accepted legacy fingerprints.
    for ref in content.get("source_refs", []):
        if not ref.get("visual_frame_timestamps"):
            ref.pop("visual_frame_timestamps", None)
    for beat in content.get("beats", []):
        for ref in beat.get("source_refs", []):
            if not ref.get("visual_frame_timestamps"):
                ref.pop("visual_frame_timestamps", None)
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Optional[str]=None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), prefix=path.name+".tmp-", delete=False) as fh:
            tmp=fh.name
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if tmp:
            try: os.unlink(tmp)
            except OSError: pass

def _ordered_visual_refs(artifact: NarrationArtifact) -> List[Tuple[int, object]]:
    out: List[Tuple[int, object]]=[]
    for beat in sorted(artifact.beats, key=lambda b: b.order):
        for ref in beat.source_refs:
            out.append((beat.order, ref))
    return out

def _resolve_source_path(source_asset: SourceAsset, root: Optional[Path], production_id: str) -> Path:
    rel=Path(source_asset.stored_path)
    base=production_dir(production_id, root=root)
    return base / rel

def validate_grounded_visual_refs(artifact: NarrationArtifact, source_asset: SourceAsset,
    source_transcript: Optional[SourceTranscript]=None, source_segments: Optional[List[object]]=None) -> None:
    if artifact.source_asset_id != source_asset.asset_id:
        raise ValueError("NarrationArtifact.source_asset_id does not match SourceAsset.asset_id")
    if source_asset.duration is None or source_asset.duration <= 0:
        raise ValueError("SourceAsset.duration must be positive")
    orders=[b.order for b in artifact.beats]
    if orders != sorted(orders):
        raise ValueError("NarrativeBeat.order must be deterministically sorted")
    if not artifact.beats:
        raise ValueError("narrated summary requires at least one beat")
    allowed_seg_ids=None
    if source_segments is not None:
        allowed_seg_ids={getattr(s,"segment_id",None) for s in source_segments if getattr(s,"segment_id",None)}
    transcript_len=len(source_transcript.segments) if source_transcript is not None else None
    for beat in artifact.beats:
        if not beat.source_refs:
            raise ValueError(f"beat {beat.order} has no grounded source refs")
        for ref in beat.source_refs:
            if ref.asset_id != source_asset.asset_id:
                raise ValueError(f"beat {beat.order} references unknown asset {ref.asset_id!r}")
            if not (0 <= ref.start_seconds < ref.end_seconds <= float(source_asset.duration)):
                raise ValueError(f"beat {beat.order} has invalid range [{ref.start_seconds}, {ref.end_seconds}) for source duration {source_asset.duration}")
            if ref.source_segment_id is not None and allowed_seg_ids is not None and ref.source_segment_id not in allowed_seg_ids:
                raise ValueError(f"beat {beat.order} references unknown source_segment_id {ref.source_segment_id!r}")
            if transcript_len is not None:
                for tid in (ref.transcript_segment_ids or []):
                    if not isinstance(tid, int) or not (0 <= tid < transcript_len):
                        raise ValueError(f"beat {beat.order} has invalid transcript ref {tid!r}")
        if source_transcript is not None:
            for tid_str in beat.transcript_refs:
                try: tid=int(tid_str)
                except Exception: raise ValueError(f"beat {beat.order} transcript_ref {tid_str!r} is not an int index")
                if not (0 <= tid < transcript_len):  # type: ignore[arg-type]
                    raise ValueError(f"beat {beat.order} transcript_ref {tid_str!r} outside transcript")

def _extract_clip_range(source_path: Path, start: float, duration: float, output_path: Path, *, source_has_audio=None,
                        frame_count=None, frame_rate=None) -> None:
    ffmpeg=resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg unavailable: cannot extract source clip")
    has_audio = bool(probe_media(source_path).has_audio) if source_has_audio is None else source_has_audio
    audio_args = (["-map", "0:a:0", "-af", "aresample=48000:async=1:first_pts=0,apad",
                   "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "128k"] if has_audio else ["-an"])
    timing = (["-t", f"{duration:.8f}", "-i", str(source_path), "-t", f"{frame_count / frame_rate:.8f}",
               "-r", f"{frame_rate:.8f}", "-frames:v", str(frame_count)] if frame_count is not None else
              ["-i", str(source_path), "-t", f"{duration:.6f}"])
    # Bound decoder threads too: output-only -threads does not limit input
    # decode queues, especially when four high-resolution cuts run together.
    args=["-y","-ss",f"{start:.6f}","-threads","2",*timing, "-map", "0:v:0",
        "-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p","-movflags","+faststart",
        *audio_args, "-threads", "2", str(output_path)]
    run_ffmpeg(args, f"extracting source clip [{start:.3f}, {start+duration:.3f})")

def _visual_clip_durations(ordered_refs, narration_duration=None):
    """Use subranges of cited evidence, not minutes of silence after the voice.

    Evidence ranges may be much longer than the spoken recap. Shorten each
    visual proportionally within its existing citation; never expand a source
    range or change the persisted grounding. Short footage is padded later.
    """
    durations = [float(ref.end_seconds) - float(ref.start_seconds) for _, ref in ordered_refs]
    total = sum(durations)
    if narration_duration is not None and total > narration_duration > 0:
        return [duration * narration_duration / total for duration in durations]
    return durations


def _quantized_clip_frames(durations, frame_rate):
    """Round cumulative boundaries, not 360 independent shot lengths upward.

    Every cut gets floor/ceil(source-range frames); total drift is <= half a
    frame, so preceding cuts cannot consume the final scene's narration slot.
    """
    cumulative, previous, counts = 0.0, 0, []
    for duration in durations:
        cumulative += duration
        boundary = round(cumulative * frame_rate)
        if boundary <= previous:
            raise ValueError("A moving recap shot must contain at least one source frame")
        counts.append(boundary - previous)
        previous = boundary
    return counts


def _paced_visual_shots(narration, narration_duration):
    """Budget moving shots per beat, cycling only within its cited source ranges.

    TTS remains the audio authority. Word-proportional beat timing avoids one
    long citation consuming the entire recap. Repeated moving footage is honest
    reuse of evidence, never a frozen last frame or an invented source moment.
    """
    if getattr(narration, "planning_strategy", None) == "multi_chapter":
        return _chapter_visual_shots(narration, narration_duration)
    weights = [max(1, len(beat.narration_text.split())) for beat in narration.beats]
    total_weight = sum(weights)
    shots = []
    max_shot = 5.5
    for beat_index, (beat, weight) in enumerate(zip(narration.beats, weights)):
        target = narration_duration * weight / total_weight
        ranges = sorted(beat.source_refs, key=lambda ref: ref.start_seconds)
        # Give every cited range airtime, even when one is much shorter than
        # another. Sampling a flat list proportionally can skip short citations.
        allocation = target / len(ranges)
        for ref in ranges:
            length = ref.end_seconds - ref.start_seconds
            count = max(1, math.ceil(allocation / min(max_shot, length)))
            duration = allocation / count
            travel = max(0.0, length - duration)
            for index in range(count):
                start = ref.start_seconds + travel * index / max(1, count - 1)
                if (beat_index == len(narration.beats) - 1 and ref == ranges[-1] and index == count - 1):
                    start = ref.end_seconds - duration
                end = min(ref.end_seconds, start + duration)
                shots.append((beat.order, ref.model_copy(update={"start_seconds": start, "end_seconds": end})))
    return shots


def _chapter_visual_shots(narration, duration):
    """A global cut grid avoids rounding every 11-second beat up to three cuts.

    About 90-110 cuts for 8-10 minutes, scaling past 350 for epics, with <=5.5s per moving shot.
    Beat boundaries are quantized to one cut; no long beat or source citation
    can leave an extended static-looking shot or an overlong silent tail.
    """
    beats = narration.beats
    weights = [max(1, len(beat.narration_text.split())) for beat in beats]
    count = max(len(beats), 90 if duration >= 480 else 1, math.ceil(duration / 5.5))
    per_shot = duration / count
    allocations, cumulative, assigned = [], 0, 0
    for index, weight in enumerate(weights):
        cumulative += weight
        boundary = min(count - (len(beats) - index - 1), max(assigned + 1, round(count * cumulative / sum(weights))))
        allocations.append(boundary - assigned)
        assigned = boundary
    shots = []
    for beat_index, (beat, take) in enumerate(zip(beats, allocations)):
        next_start = min(ref.start_seconds for ref in beats[beat_index + 1].source_refs) if beat_index + 1 < len(beats) else float('inf')
        ranges = sorted((ref.model_copy(update={"end_seconds": min(ref.end_seconds, next_start)})
                        for ref in beat.source_refs if ref.start_seconds < min(ref.end_seconds, next_start)),
                        key=lambda ref: ref.start_seconds)
        if not ranges:
            raise ValueError("Chronological moving shots require distinct source windows for each beat")
        latest_ending_range = max(ranges, key=lambda ref: ref.end_seconds)
        # A short dialogue citation is evidence, not an obligation to make a
        # one-second cut. Prefer the inspected visual moments that can support
        # a full moving shot; otherwise use the longest admitted source ranges.
        full = [ref for ref in ranges if ref.end_seconds - ref.start_seconds >= per_shot - 1e-6]
        visual = [ref for ref in full if ref.visual_frame_timestamps]
        if full:
            ranges = visual or full
        else:
            ranges = ([max(ranges, key=lambda ref: ref.end_seconds)] if beat_index == len(beats) - 1
                      else [max(ranges, key=lambda ref: ref.end_seconds - ref.start_seconds)])
        if beat_index == len(beats) - 1 and latest_ending_range not in ranges:
            # A short closing citation is still mandatory. Long earlier refs
            # must not win the full-shot preference and erase the true ending.
            ranges = sorted(ranges + [latest_ending_range], key=lambda ref: ref.start_seconds)
        for index in range(take):
            # Include BOTH ends of a beat's evidence. The old floor(i*N/take)
            # mapping silently skipped the final scene when N > take.
            def selected_range(i):
                if take > 1:
                    return round(i * (len(ranges) - 1) / (take - 1))
                return 0 if beat_index == 0 else len(ranges) - 1
            range_index = selected_range(index)
            ref = ranges[range_index]
            siblings = [i for i in range(take) if selected_range(i) == range_index]
            rank = siblings.index(index)
            length = ref.end_seconds - ref.start_seconds
            # Short citations get more short moving cuts, never fabricated range expansion.
            parts = max(1, math.ceil(per_shot / min(5.5, length)))
            shot_duration = per_shot / parts
            for part in range(parts):
                fraction = (rank * parts + part) / max(1, len(siblings) * parts - 1)
                start = ref.start_seconds + max(0, length - shot_duration) * fraction
                if beat_index == len(beats) - 1 and index == take - 1 and part == parts - 1:
                    start = ref.end_seconds - shot_duration
                shots.append((beat.order, ref.model_copy(update={"start_seconds": start,
                              "end_seconds": min(ref.end_seconds, start + shot_duration)})))
    return shots


RECAP_BED_EQ = "highpass=f=80,equalizer=f=1500:t=q:w=1.0:g=-6,lowpass=f=10000"
RECAP_DUCKING = "sidechaincompress=threshold=0.08:ratio=2:attack=12:release=180:knee=2:makeup=1"


def _concat_recap_cuts(clips, scratch):
    """All cuts use identical encoders; mux video AND source audio with O(1) argv."""
    manifest = write_concat_manifest(Path(scratch) / "recap_cuts.ffconcat", clips)
    output = Path(scratch) / "cut_source.mp4"
    run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", str(manifest), "-c", "copy", str(output)],
               "concatenating moving recap cuts and their source audio without enumerated inputs")
    return output


def _recap_mix_filter(duration, background_volume, background_mixed):
    voice = (f"[1:a:0]aresample=48000,asetpts=PTS-STARTPTS,"
             f"aformat=channel_layouts=stereo,atrim=duration={duration:.6f}[voice]")
    if not background_mixed:
        return voice + ";[voice]anull[mix]"
    return (voice + ";[voice]asplit=2[narrator][control];"
            f"[0:a:0]aresample=48000,asetpts=PTS-STARTPTS,{RECAP_BED_EQ},"
            f"volume={background_volume:.4f},apad,atrim=duration={duration:.6f}[bed];"
            f"[bed][control]{RECAP_DUCKING}[ducked];"
            "[narrator][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")


def _measure_mix_separation(carrier, voice_path, duration, background_volume, background_mixed, scratch):
    """Measure the REAL ducked source stem, not a gain-only audibility claim."""
    from app.production.synthesis import measure_audio_loudness
    if not background_mixed:
        return {"narrator_to_background_rms_db": None, "source_stem_rms_db": None}
    bed = Path(scratch) / 'ducked_source.wav'
    filters = _recap_mix_filter(duration, background_volume, True).replace(
        "[narrator][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]",
        "[narrator]anullsink;[ducked]anull[mix]")
    run_ffmpeg(['-y', '-i', str(carrier), '-i', str(voice_path), '-filter_complex', filters,
                '-map', '[mix]', '-vn', '-c:a', 'pcm_s24le', '-ar', '48000', '-ac', '2', str(bed)],
               'measuring the actual EQ-carved ducked movie bed')
    source_levels = measure_audio_loudness(bed)
    narrator_levels = measure_audio_loudness(voice_path, audio_filter='aformat=channel_layouts=stereo')
    voice_rms, bed_rms = narrator_levels['rms_db'], source_levels['rms_db']
    ratio = voice_rms - bed_rms if voice_rms is not None and bed_rms is not None else None
    return {'narrator_to_background_rms_db': round(ratio, 3) if ratio is not None else None,
            'source_stem_rms_db': bed_rms, 'narrator_stem_rms_db': voice_rms,
            'source_stem_lufs': source_levels['integrated_lufs'], 'measurement': 'decoded_actual_ducked_stems'}


def _master_summary_mix(input_path, output_path, *, broadcast):
    from app.production.synthesis import (
        measure_audio_loudness, measured_loudnorm_filter, validate_broadcast_loudness, correct_encoded_loudness,
    )

    before = measure_audio_loudness(input_path)
    # Legacy/mock sources still assemble truthfully, but no broadcast claim is
    # made for sandbox tones. Real TTS tracks always get an encoded loudness check.
    filters = measured_loudnorm_filter(before) if broadcast else "anull"
    run_ffmpeg(["-y", "-i", str(input_path), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                "-af", filters, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart", str(output_path)], "mastering the encoded recap mix without halving narration")
    after = measure_audio_loudness(output_path)
    if broadcast:
        after = correct_encoded_loudness(output_path, after)
        validate_broadcast_loudness(after)
    return {"target_lufs": -14.0 if broadcast else None, "before": before, "after": after,
            "normalization": "two_pass_loudnorm" if broadcast else "none", "amix_normalize": False,
            "source_eq": RECAP_BED_EQ, "speech_ducking": RECAP_DUCKING}


def _shot_manifest(ordered_refs, clips, narration_duration, *, clip_durations=None):
    """Measured cut timing, so source timestamps remain auditable after encoding."""
    timeline, manifest = 0.0, []
    for index, ((order, ref), clip) in enumerate(zip(ordered_refs, clips)):
        actual = float(clip_durations[clip] if clip_durations is not None else probe_media(clip).duration or 0)
        end = min(narration_duration, timeline + actual)
        if end > timeline:
            manifest.append({"shot_index": index + 1, "beat_order": order,
                "source_start_seconds": round(ref.start_seconds, 6), "source_end_seconds": round(ref.end_seconds, 6),
                "output_start_seconds": round(timeline, 6), "output_end_seconds": round(end, 6),
                "moving_footage_looped": actual > ref.end_seconds - ref.start_seconds + .15})
        timeline += actual
    return manifest


def validate_finale_shots(narration, shots):
    from app.production.narration_real import validate_true_ending
    anchor = narration.ending_anchor
    if anchor is None:
        return
    validate_true_ending(narration.beats, anchor, require_visual=narration.summary_mode == "pro")
    for beat in narration.beats[-anchor.required_final_beats:]:
        selected = [ref for order, ref in shots if order == beat.order]
        if (not selected or any(ref.start_seconds < anchor.finale_start_seconds - .001
                                or ref.end_seconds > anchor.narrative_end_seconds + .001 for ref in selected)):
            raise ValueError("Final beats require moving source clips from the true ending window")
    last = shots[-1][1]
    if last.end_seconds < anchor.narrative_end_seconds - .05 or last.end_seconds > anchor.narrative_end_seconds + .001:
        raise ValueError("Last moving shot must reach the latest cited ending scene and exclude credits")


def recap_render_workers(shot_count):
    """Allow serial rendering on memory-starved machines without a UI change."""
    try:
        workers = int(os.getenv("AVM_RECAP_RENDER_WORKERS", "4"))
    except (TypeError, ValueError):
        raise ValueError("AVM_RECAP_RENDER_WORKERS must be an integer from 1 to 4") from None
    if not 1 <= workers <= 4:
        raise ValueError("AVM_RECAP_RENDER_WORKERS must be an integer from 1 to 4")
    return workers if shot_count >= 40 else 1


def _build_intermediate_clips(ordered_refs: List[Tuple[int, object]], source_asset: SourceAsset, source_path: Path, temp_dir: Path, *, narration_duration=None) -> List[Path]:
    durations = _visual_clip_durations(ordered_refs, narration_duration)
    source_info = probe_media(source_path)
    source_has_audio = bool(source_info.has_audio)
    frame_rate = source_info.frame_rate or 30.0
    frame_counts = _quantized_clip_frames(durations, frame_rate) if len(ordered_refs) >= 80 else None
    def extract(item):
        idx, ((order, ref), dur) = item
        start=float(ref.start_seconds)
        if dur <= 0:
            raise ValueError(f"ref order {order} has non-positive duration")
        clip=temp_dir / f"clip_{idx:04d}_b{order:03d}.mp4"
        timing = {"frame_count": frame_counts[idx], "frame_rate": frame_rate} if frame_counts is not None else {}
        _extract_clip_range(source_path, start, dur, clip, source_has_audio=source_has_audio, **timing)
        info=probe_media(clip)
        if not info.has_video or info.duration is None or info.duration <= 0.05:
            raise RuntimeError(f"extracted clip {clip.name} has no valid video stream")
        return clip
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=recap_render_workers(len(ordered_refs))) as pool:
        return list(pool.map(extract, enumerate(zip(ordered_refs, durations))))

def _measure_narration_audio_duration(narration_audio: NarrationAudioArtifact, artifact: NarrationArtifact, production_id: str, unit_id: str, root: Optional[Path]) -> Tuple[float, Path]:
    candidate_rel=narration_audio.audio_path or artifact.audio_artifact_path  # type: ignore[attr-defined]
    if candidate_rel:
        candidate=(production_dir(production_id, root=root) / Path(candidate_rel))
        if candidate.is_file():
            info=probe_media(candidate)
            if info.has_audio and info.duration and info.duration > 0:
                return float(info.duration), candidate
        from app.voice.validator import validate_audio_file
        v=validate_audio_file(candidate)
        if not v.valid or not v.duration or v.duration <= 0:
            raise ValueError(f"narration audio validation failed: {v.errors}")
        return float(v.duration), candidate
    raise ValueError("narration audio artifact has no resolvable audio_path")


def _assemble_sandbox_summary(production_id, unit_id, root, narration, audio, source_asset):
    from app.production.narration import validate_narration_artifact
    from app.production.sandbox import create_sandbox_summary, sandbox_duration
    from app.transcription.transcribe import load_transcript

    transcript = load_transcript(production_id, source_asset.asset_id, root=root, mock_mode=True)
    validate_narration_artifact(narration, source_asset=source_asset, source_transcript=transcript)
    validate_grounded_visual_refs(narration, source_asset, transcript)
    if not _resolve_source_path(source_asset, root, production_id).is_file():
        raise FileNotFoundError("The registered sandbox source file is missing")
    if audio.provider != "mock" or audio.status != "completed":
        raise ValueError("Synthesize sandbox narration audio before sandbox assembly")
    audio_path = production_dir(production_id, root=root) / audio.audio_path
    audio_info = probe_media(audio_path)
    if not audio_info.has_audio or not audio_info.duration or audio_info.duration <= 0:
        raise ValueError("sandbox narration audio failed its media validation")
    duration = sandbox_duration(min(audio_info.duration, narration.target_duration_seconds))
    final = summary_video_output_path(production_id, unit_id, root=root)
    final.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".sandbox-summary-", dir=final.parent) as scratch:
        temporary = Path(scratch) / "narrated_summary.mp4"
        create_sandbox_summary(temporary, audio_path, duration)
        info = probe_media(temporary)
        if not info.has_video or not info.has_audio or not info.duration or info.duration <= 0:
            raise ValueError("sandbox summary failed its fresh video/audio validation")
        os.replace(temporary, final)
    artifact = NarratedSummaryArtifact(
        production_id=production_id, unit_id=unit_id, source_asset_id=source_asset.asset_id,
        narration_id=narration.narration_id, narration_audio_path=audio.audio_path,
        source_refs_count=len(_ordered_visual_refs(narration)), output_path=final.relative_to(production_dir(production_id, root=root)).as_posix(),
        output_duration_seconds=info.duration, video_width=info.width, video_height=info.height,
        video_codec=info.video_codec, audio_codec=info.audio_codec, source_duration_seconds=source_asset.duration,
        target_duration_seconds=narration.target_duration_seconds, narration_duration_seconds=audio_info.duration,
        file_size=final.stat().st_size, generated_at=datetime.now().isoformat(),
        validation={"has_video": True, "has_audio": True, "valid": True, "mock_mode": True, "synthetic_visuals": True},
        file_fingerprint=compute_content_hash(final), narration_content_hash=narration_content_hash(narration),
    )
    _atomic_write_json(summary_video_metadata_path(production_id, unit_id, root=root), artifact.model_dump())
    return artifact


def assemble_narrated_summary(production_id, unit_id, root=None, *, narration_artifact=None, narration_audio=None, source_asset_override=None, mock_mode=False):
    if not isinstance(mock_mode, bool):
        raise ValueError("mock_mode must be a boolean")
    from app.production.storage import load_production, production_dir
    from app.production.narration import validate_narration_artifact
    import json, shutil, tempfile, pathlib
    from datetime import datetime
    root_p=pathlib.Path(root) if root is not None else None
    if narration_artifact is None:
        from app.production.storage import load_narration_artifact as _lna
        narration_artifact=_lna(production_id, unit_id, root=root_p)
    if narration_audio is None:
        from app.production.storage import load_narration_audio as _lnau
        try:
            narration_audio=_lnau(production_id, unit_id, root=root_p)
        except FileNotFoundError:
            if narration_artifact.audio_artifact_path:
                from app.production.models import NarrationAudioArtifact as _NAA
                narration_audio=_NAA(narration_id=narration_artifact.narration_id, production_id=production_id, unit_id=unit_id, audio_path=narration_artifact.audio_artifact_path, provider=narration_artifact.audio_provider or 'unknown', voice=narration_artifact.audio_voice or 'unknown', format=(narration_artifact.audio_format or 'mp3').lower(), duration_seconds=float(narration_artifact.audio_duration_seconds or 0.0), file_size=1, status='completed', generated_at=datetime.now().isoformat())
            else: raise
    production=load_production(production_id, root=root_p)
    source_asset=source_asset_override
    if source_asset is None:
        from app.production.source import find_source_asset
        source_asset = find_source_asset(production, narration_artifact.source_asset_id, root=root_p,
            include_sandbox=True, stored_path=narration_artifact.source_media_path)
    if source_asset is None:
        raise FileNotFoundError(f'source asset {narration_artifact.source_asset_id!r} not present')
    if mock_mode:
        return _assemble_sandbox_summary(production_id, unit_id, root_p, narration_artifact, narration_audio, source_asset)
    transcription=None
    transcript_rel = narration_artifact.source_transcript_path or f'source/{source_asset.asset_id}.transcript.json'
    tp=production_dir(production_id, root=root_p) / transcript_rel
    if narration_artifact.source_transcript_path is not None and not tp.is_file():
        raise FileNotFoundError('The transcript used to ground this narration is missing; transcribe and rebuild narration first')
    if tp.is_file():
        try:
            from app.production.models import SourceTranscript
            transcription=SourceTranscript.model_validate(json.loads(tp.read_text(encoding='utf-8')))
        except Exception:
            if narration_artifact.source_transcript_path is not None:
                raise ValueError('The transcript used to ground this narration is invalid')
            transcription=None
    segments=None
    sp=production_dir(production_id, root=root_p) / f'source/{source_asset.asset_id}.segments.json'
    if sp.is_file():
        try:
            raw=json.loads(sp.read_text(encoding='utf-8'))
            from app.production.models import SourceSegment
            segments=[SourceSegment(**x) for x in (raw.get('segments') or [])]
        except Exception: segments=None
    validate_narration_artifact(narration_artifact, source_asset=source_asset, source_transcript=transcription)
    validate_grounded_visual_refs(narration_artifact, source_asset, transcription, segments)
    source_path=_resolve_source_path(source_asset, root_p, production_id)
    if not source_path.is_file(): raise FileNotFoundError(f'source media not found: {source_path}')
    from app.assembly.ffmpeg import probe_media
    source_info=probe_media(source_path)
    if not source_info.has_video or not source_info.duration or source_info.duration <= 0:
        raise ValueError(f'source {source_path} is not valid video')
    narration_duration, narration_audio_file=_measure_narration_audio_duration(narration_audio, narration_artifact, production_id, unit_id, root_p)
    try:
        background_volume = float(os.getenv("AVM_RECAP_BACKGROUND_VOLUME", "0.05"))
    except (TypeError, ValueError):
        raise ValueError("Recap background volume must be between 0 and 0.055") from None
    if not math.isfinite(background_volume) or not 0 <= background_volume <= .055:
        raise ValueError("Recap background volume must be between 0 and 0.055 so movie audio cannot mask narration")
    source_refs = _ordered_visual_refs(narration_artifact)
    if not source_refs: raise ValueError('narrated summary requires at least one grounded visual reference')
    ordered_refs = _paced_visual_shots(narration_artifact, narration_duration)
    validate_finale_shots(narration_artifact, ordered_refs)
    temp_base=pathlib.Path(tempfile.mkdtemp(prefix=f'track9_{production_id}_{unit_id}_'))
    try:
        clips=_build_intermediate_clips(ordered_refs, source_asset, source_path, temp_base)
        # Reuse media facts across the bed check, rounding and shot sidecar.
        # Hundreds of redundant FFprobe process startups cost minutes on Windows.
        clip_info = {clip: probe_media(clip) for clip in clips}
        # A missing stream is a clean narration-only fallback, never an invalid
        # FFmpeg input. All cuts are made from this ONE registered source.
        background_mixed = (background_volume > 0 and bool(source_info.has_audio)
                            and all(clip_info[clip].has_audio for clip in clips))
        clip_total=sum(float(clip_info[clip].duration or 0) for clip in clips)
        if narration_duration > clip_total:
            # Encoder frame rounding can leave a small deficit. Repeat moving
            # cited footage at EOF, never hold a still frame or cut off speech.
            deficit = narration_duration - clip_total + 0.1
            last = clips[-1]
            padded = temp_base / 'clip_last_looped.mp4'
            last_duration = float(clip_info[last].duration or 0)
            run_ffmpeg(['-y', '-stream_loop', '-1', '-i', str(last), '-t', f'{last_duration + deficit:.3f}',
                        *(['-c:a', 'aac', '-ar', '48000', '-ac', '2'] if clip_info[last].has_audio else ['-an']),
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(padded)],
                       'matching moving summary footage to narration')
            clips[-1] = padded
            clip_info[padded] = probe_media(padded)
        if narration_artifact.ending_anchor is not None:
            preceding = sum(float(clip_info[clip].duration or 0) for clip in clips[:-1])
            remaining = narration_duration - preceding
            order, last_ref = ordered_refs[-1]
            original = max(narration_artifact.beats[-1].source_refs, key=lambda r: r.end_seconds)
            if not 0 < remaining <= original.end_seconds - original.start_seconds:
                raise ValueError('Cut-frame rounding cannot fit the complete true ending scene')
            last_ref = last_ref.model_copy(update={'start_seconds': last_ref.end_seconds - remaining})
            final_clip = temp_base / 'clip_true_ending.mp4'
            _extract_clip_range(source_path, last_ref.start_seconds, remaining, final_clip,
                                source_has_audio=background_mixed)
            clips[-1] = final_clip
            clip_info[final_clip] = probe_media(final_clip)
            ordered_refs[-1] = (order, last_ref)
            validate_finale_shots(narration_artifact, ordered_refs)
        target_w=source_info.width if source_info.width else 1280
        target_h=source_info.height if source_info.height else 720
        aspect='16:9' if (target_w or 0) >= (target_h or 0) else '9:16'
        cut_source = _concat_recap_cuts(clips, temp_base)
        # Also protect imported/legacy long plans without a multi_chapter flag.
        # Leave generous headroom for Windows executable/path quoting overhead.
        concat_mux = (narration_artifact.planning_strategy == "multi_chapter" or len(clips) >= 80
                      or sum(len(str(clip)) + 16 for clip in clips) >= 8000)
        if concat_mux:
            # All cuts came from the same registered source with identical encode
            # settings. Avoid re-normalizing/re-encoding 100+ clips a second time.
            assembled_file = temp_base / 'chapter_summary_unmastered.mp4'
            mix_args = ['-filter_complex', _recap_mix_filter(narration_duration, background_volume, background_mixed),
                        '-map', '[mix]']
            run_ffmpeg(['-y', '-i', str(cut_source), '-i', str(narration_audio_file),
                        '-map', '0:v:0', *mix_args, '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                        '-t', f'{narration_duration:.6f}', '-shortest', '-movflags', '+faststart', str(assembled_file)],
                       'muxing chronological recap cuts to the exact narration duration')
        else:
            # Keep the shared assembly engine unchanged for non-recap jobs.
            # Its output supplies the video carrier; remix original cut audio
            # below with the SAME recap-specific vocal pocket/ducking chain.
            from app.assembly.pipeline import assemble_project
            policy = AudioPolicy.NATIVE_PLUS_NARRATION if background_mixed else AudioPolicy.NARRATION_ONLY
            project=AssemblyProject(project_id=f'{production_id}__{unit_id}__narrated_summary', title=f'Narrated summary {production_id}/{unit_id}', aspect_ratio=aspect, resolution=VideoResolution(width=int(target_w), height=int(target_h)), scenes=[Scene(id=f'beat_{i:03d}', video=str(p)) for i,p in enumerate(clips,1)], voiceover=str(narration_audio_file), audio_policy=policy, native_audio_volume=background_volume if background_mixed else 0.0)
            result=assemble_project(project, base_dir=pathlib.Path.cwd())
            if not result.success: raise RuntimeError(f'assembly failed: {result.errors}')
            carrier=pathlib.Path(result.output_path) if result.output_path else None
            if not carrier or not carrier.is_file(): raise RuntimeError('summary video carrier was not assembled')
            assembled_file = temp_base / 'summary_unmastered.mp4'
            run_ffmpeg(['-y', '-i', str(cut_source), '-i', str(narration_audio_file),
                        '-i', str(carrier), '-filter_complex', _recap_mix_filter(narration_duration, background_volume, background_mixed),
                        '-map', '2:v:0', '-map', '[mix]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                        '-t', f'{narration_duration:.6f}', '-movflags', '+faststart', str(assembled_file)],
                       'applying the recap vocal pocket and speech ducking to standard cuts')
        if not assembled_file or not assembled_file.is_file(): raise RuntimeError(f'assembled output not present: {assembled_file}')
        mastered = temp_base / 'summary_broadcast.mp4'
        broadcast = bool(narration_audio.audio_processing) or narration_audio.provider == 'edge_tts'
        separation = _measure_mix_separation(cut_source, narration_audio_file, narration_duration,
            background_volume, background_mixed, temp_base) if broadcast else {}
        audio_processing = _master_summary_mix(assembled_file, mastered, broadcast=broadcast)
        audio_processing.update(separation)
        assembled_file = mastered
        final_info=probe_media(assembled_file)
        if not final_info.has_video: raise ValueError(f'assembled file has no video stream: {assembled_file}')
        if not final_info.has_audio: raise ValueError(f'assembled file has no audio stream (narration missing): {assembled_file}')
        if not final_info.duration or final_info.duration <= 0.3: raise ValueError(f'assembled file has no measurable duration: {assembled_file}')
        if concat_mux and abs(final_info.duration - narration_duration) > .25:
            raise ValueError('Summary video does not closely match the measured narration duration')
        final_dest=summary_video_output_path(production_id, unit_id, root=root_p)
        final_dest.parent.mkdir(parents=True, exist_ok=True)
        # Readers keep the previous playable master until the entire replacement
        # is on disk. A disconnect/interrupted copy cannot expose a partial MP4.
        from app.production.source import _atomic_copy
        _atomic_copy(assembled_file, final_dest)
        dest_info=probe_media(final_dest)
        prod_dir=production_dir(production_id, root=root_p)
        try: rel_output=final_dest.relative_to(prod_dir).as_posix()
        except Exception: rel_output=final_dest.name
        try:
            rel_narration=narration_audio_file.relative_to(prod_dir).as_posix()
            if not _is_relative_path(rel_narration): rel_narration=narration_audio.audio_path
        except Exception: rel_narration=narration_audio.audio_path
        artifact=NarratedSummaryArtifact(production_id=production_id, unit_id=unit_id, source_asset_id=source_asset.asset_id, narration_id=narration_artifact.narration_id, narration_audio_path=rel_narration, source_refs_count=len(source_refs), output_path=rel_output, output_duration_seconds=float(dest_info.duration or final_info.duration), video_width=dest_info.width, video_height=dest_info.height, video_codec=dest_info.video_codec, audio_codec=dest_info.audio_codec, source_duration_seconds=float(source_asset.duration), target_duration_seconds=float(narration_artifact.target_duration_seconds), narration_duration_seconds=float(narration_duration), file_size=int(final_dest.stat().st_size), generated_at=datetime.now().isoformat(), validation={'has_video': dest_info.has_video, 'has_audio': dest_info.has_audio, 'valid': True, 'visual_shots': len(ordered_refs), 'max_shot_seconds': max(ref.end_seconds - ref.start_seconds for _, ref in ordered_refs), 'freeze_padding': False})
        artifact.file_fingerprint = compute_content_hash(final_dest)
        artifact.beat_count = len(narration_artifact.beats)
        artifact.word_count = len(narration_artifact.narration_text.split())
        artifact.chapter_count = narration_artifact.chapter_count
        artifact.summary_mode = narration_artifact.summary_mode
        artifact.canonical_model = narration_artifact.summary_understanding_model_id
        artifact.words_per_minute = artifact.word_count * 60 / narration_duration
        artifact.background_audio_mixed = background_mixed
        artifact.background_audio_volume = background_volume if background_mixed else 0.0
        artifact.audio_mix_method = "amix_ducked_source" if background_mixed else "narration_only"
        artifact.audio_processing = audio_processing
        artifact.validation.update(source_has_audio=bool(source_info.has_audio), background_audio_mixed=background_mixed,
                                   background_audio_volume=artifact.background_audio_volume, narrator_volume=1.0,
                                   audio_mix_method=artifact.audio_mix_method)
        artifact.validation['audio_video_delta_seconds'] = round(artifact.output_duration_seconds - narration_duration, 6)
        artifact.validation['video_assembly_method'] = 'concat_demuxer_stream_copy' if concat_mux else 'shared_assembly_carrier'
        artifact.validation['concat_manifest_entries'] = len(clips)
        artifact.validation['shot_manifest'] = _shot_manifest(ordered_refs, clips, narration_duration,
            clip_durations={clip: float(clip_info[clip].duration or 0) for clip in clips})
        artifact.validation['ending_anchor'] = narration_artifact.ending_anchor.model_dump() if narration_artifact.ending_anchor else None
        artifact.validation['true_ending_shots_validated'] = narration_artifact.ending_anchor is not None
        artifact.narration_content_hash = narration_content_hash(narration_artifact)
        _atomic_write_json(summary_video_metadata_path(production_id, unit_id, root=root_p), artifact.model_dump())
        return artifact
    finally:
        try: shutil.rmtree(temp_base, ignore_errors=True)
        except Exception: pass
