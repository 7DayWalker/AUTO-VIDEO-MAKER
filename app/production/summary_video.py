"""Track 9 Phase 3 — grounded narrated summary video assembly."""
from __future__ import annotations

import hashlib, json, logging, os, shutil, tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.assembly.audio_contract import AudioPolicy
from app.assembly.ffmpeg import probe_media, resolve_ffmpeg, run_ffmpeg
from app.assembly.models import AssemblyProject, Scene, VideoResolution
from app.assembly.pipeline import assemble_project
from app.production.models import NarrationArtifact, NarrationAudioArtifact, SourceAsset, SourceTranscript
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
    content = artifact.model_dump(mode="json", include=fields)
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

def _extract_clip_range(source_path: Path, start: float, duration: float, output_path: Path) -> None:
    ffmpeg=resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg unavailable: cannot extract source clip")
    args=["-y","-ss",f"{start:.3f}","-i",str(source_path),"-t",f"{duration:.3f}",
        "-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p","-movflags","+faststart",
        "-c:a","aac","-ac","2","-ar","44100","-b:a","128k", str(output_path)]
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


def _build_intermediate_clips(ordered_refs: List[Tuple[int, object]], source_asset: SourceAsset, source_path: Path, temp_dir: Path, *, narration_duration=None) -> List[Path]:
    clips: List[Path]=[]
    durations = _visual_clip_durations(ordered_refs, narration_duration)
    for idx,((order, ref), dur) in enumerate(zip(ordered_refs, durations)):
        start=float(ref.start_seconds)
        if dur <= 0:
            raise ValueError(f"ref order {order} has non-positive duration")
        clip=temp_dir / f"clip_{idx:04d}_b{order:03d}.mp4"
        _extract_clip_range(source_path, start, dur, clip)
        info=probe_media(clip)
        if not info.has_video or info.duration is None or info.duration <= 0.05:
            raise RuntimeError(f"extracted clip {clip.name} has no valid video stream")
        clips.append(clip)
    return clips

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
    ordered_refs=_ordered_visual_refs(narration_artifact)
    if not ordered_refs: raise ValueError('narrated summary requires at least one grounded visual reference')
    temp_base=pathlib.Path(tempfile.mkdtemp(prefix=f'track9_{production_id}_{unit_id}_'))
    try:
        clips=_build_intermediate_clips(ordered_refs, source_asset, source_path, temp_base, narration_duration=narration_duration)
        try:
            clip_total=sum(float(probe_media(clip).duration or 0) for clip in clips)
        except Exception:
            clip_total=None
        if clip_total is not None and narration_duration > clip_total + 0.3:
            pad=float(narration_duration)-float(clip_total)+1.0
            if pad>0 and clips:
                last=clips[-1]
                padded=temp_base / 'clip_last_padded.mp4'
                _run_cmd=['-y','-i',str(last),'-vf',f"tpad=stop_mode=clone:stop_duration={pad:.3f}",'-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-t',f"{float(probe_media(last).duration or 0)+pad:.3f}",'-c:a','aac','-movflags','+faststart',str(padded)]
                from app.assembly.ffmpeg import run_ffmpeg as _rf
                _rf(_run_cmd, 'padding last clip to narration')
                pi=probe_media(padded)
                if pi.has_video and pi.duration and pi.duration>0:
                    clips[-1]=padded
        target_w=source_info.width if source_info.width else 1280
        target_h=source_info.height if source_info.height else 720
        aspect='16:9' if (target_w or 0) >= (target_h or 0) else '9:16'
        from app.assembly.models import AssemblyProject, Scene, VideoResolution
        from app.assembly.audio_contract import AudioPolicy
        from app.assembly.pipeline import assemble_project
        project=AssemblyProject(project_id=f'{production_id}__{unit_id}__narrated_summary', title=f'Narrated summary {production_id}/{unit_id}', aspect_ratio=aspect, resolution=VideoResolution(width=int(target_w), height=int(target_h)), scenes=[Scene(id=f'beat_{i:03d}', video=str(p)) for i,p in enumerate(clips,1)], voiceover=str(narration_audio_file), audio_policy=AudioPolicy.NARRATION_ONLY, native_audio_volume=0.0)
        result=assemble_project(project, base_dir=pathlib.Path.cwd())
        if not result.success: raise RuntimeError(f'assembly failed: {result.errors}')
        assembled_file=pathlib.Path(result.output_path) if result.output_path else None
        if not assembled_file or not assembled_file.is_file(): raise RuntimeError(f'assembled output not present: {assembled_file}')
        final_info=probe_media(assembled_file)
        if not final_info.has_video: raise ValueError(f'assembled file has no video stream: {assembled_file}')
        if not final_info.has_audio: raise ValueError(f'assembled file has no audio stream (narration missing): {assembled_file}')
        if not final_info.duration or final_info.duration <= 0.3: raise ValueError(f'assembled file has no measurable duration: {assembled_file}')
        final_dest=summary_video_output_path(production_id, unit_id, root=root_p)
        final_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(assembled_file, final_dest)
        dest_info=probe_media(final_dest)
        prod_dir=production_dir(production_id, root=root_p)
        try: rel_output=final_dest.relative_to(prod_dir).as_posix()
        except Exception: rel_output=final_dest.name
        try:
            rel_narration=narration_audio_file.relative_to(prod_dir).as_posix()
            if not _is_relative_path(rel_narration): rel_narration=narration_audio.audio_path
        except Exception: rel_narration=narration_audio.audio_path
        artifact=NarratedSummaryArtifact(production_id=production_id, unit_id=unit_id, source_asset_id=source_asset.asset_id, narration_id=narration_artifact.narration_id, narration_audio_path=rel_narration, source_refs_count=len(ordered_refs), output_path=rel_output, output_duration_seconds=float(dest_info.duration or final_info.duration), video_width=dest_info.width, video_height=dest_info.height, video_codec=dest_info.video_codec, audio_codec=dest_info.audio_codec, source_duration_seconds=float(source_asset.duration), target_duration_seconds=float(narration_artifact.target_duration_seconds), narration_duration_seconds=float(narration_duration), file_size=int(final_dest.stat().st_size), generated_at=datetime.now().isoformat(), validation={'has_video': dest_info.has_video, 'has_audio': dest_info.has_audio, 'valid': True})
        artifact.file_fingerprint = compute_content_hash(final_dest)
        artifact.narration_content_hash = narration_content_hash(narration_artifact)
        _atomic_write_json(summary_video_metadata_path(production_id, unit_id, root=root_p), artifact.model_dump())
        return artifact
    finally:
        try: shutil.rmtree(temp_base, ignore_errors=True)
        except Exception: pass
