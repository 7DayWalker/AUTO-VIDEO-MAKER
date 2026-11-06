"""Reusable source-bound visual evidence, independent of narration model/budget.

Legacy Pro beats are retained SCENE observations, not fresh per-frame captions.
Only explicitly cited timestamps get those descriptions; other inspected frames
remain unavailable/non-citable. No frame bytes or private configuration enter
this JSON cache.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from app.production.models import NarrationArtifact
from app.production.visual_observation import _ActionNote

CACHE_FILENAME = "frame_observations.json"
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_LOCK = threading.Lock()


def _lock(path):
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(str(Path(path).resolve()), threading.Lock())


def visual_source_identity(asset, transcript):
    transcript_hash = hashlib.sha256(transcript.model_dump_json().encode("utf-8")).hexdigest()
    return {"asset_id": asset.asset_id, "source_sha256": asset.sha256,
            "source_media_path": asset.stored_path, "source_duration_s": asset.duration,
            "transcript_sha256": transcript_hash}


def _read(path, identity):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("version") == 1 and data.get("source_identity") == identity and isinstance(data.get("chapters"), list):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {"version": 1, "source_identity": identity, "chapters": []}


def cache_source_conflicts(path, asset, transcript):
    """Do not re-hydrate old scene descriptions after source/dialogue changes."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data.get("version") == 1 and data.get("source_identity") != visual_source_identity(asset, transcript)
    except (OSError, ValueError, AttributeError):
        return False


def _atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
        json.dump(data, file, ensure_ascii=False, indent=2)
    try:
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(.1 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def save_frame_observations(path, asset, transcript, frames, notes, *, start, end, provenance=None):
    """Store validated complete or partial observations; prefer direct evidence."""
    identity = visual_source_identity(asset, transcript)
    allowed = {round(float(ts), 3) for ts, _ in frames}
    parsed = [_ActionNote.model_validate(note) for note in notes]
    if any(round(note.timestamp_seconds, 3) not in allowed for note in parsed):
        raise ValueError("Cached visual observations must match supplied source timestamps")
    with _lock(path):
        data = _read(path, identity)
        chapter = next((c for c in data["chapters"] if c.get("start_seconds") == start and c.get("end_seconds") == end), None)
        if chapter is None:
            chapter = {"start_seconds": start, "end_seconds": end, "frame_timestamps": sorted(allowed),
                       "frame_observations": [], "provenance": provenance or {}}
            data["chapters"].append(chapter)
        stored = {round(n["timestamp_seconds"], 3): n for n in chapter["frame_observations"]}
        priority = {"unavailable": 0, "prior_pro_scene": 1, "live_frame": 2}
        for note in parsed:
            stamp = round(note.timestamp_seconds, 3)
            existing = stored.get(stamp)
            if existing is None or priority[note.evidence_kind] >= priority[existing.get("evidence_kind", "live_frame")]:
                stored[stamp] = note.model_dump()
        chapter["frame_observations"] = [stored[stamp] for stamp in sorted(stored)]
        if provenance:
            chapter["provenance"] = provenance
        data["updated_at"] = datetime.now().isoformat()
        _atomic_json(path, data)


def load_frame_observations(path, asset, transcript, frames, *, start, end):
    """Map source timestamps to current local frame IDs, never invent captions."""
    with _lock(path):
        data = _read(path, visual_source_identity(asset, transcript))
    stored = {}
    for chapter in data["chapters"]:
        if not isinstance(chapter, dict):
            continue
        for raw in chapter.get("frame_observations", []):
            try:
                note = _ActionNote.model_validate(raw)
            except ValueError:
                continue
            if start <= note.timestamp_seconds <= end:
                stamp = round(note.timestamp_seconds, 3)
                previous = stored.get(stamp)
                if previous is None or note.evidence_kind == "live_frame":
                    stored[stamp] = note
    result = []
    for index, (stamp, _) in enumerate(frames):
        note = stored.get(round(float(stamp), 3))
        if note is None:
            return None
        result.append(note.model_copy(update={"frame_id": index, "timestamp_seconds": float(stamp)}).model_dump())
    cited = [n["timestamp_seconds"] for n in result if n["citable"]]
    if not cited or min(cited) > start + (end - start) * .15 or max(cited) < end - (end - start) * .15:
        return None
    if any(right - left > (end - start) * .4 for left, right in zip(cited, cited[1:])):
        return None
    return result


def hydrate_prior_pro(path, prior_path, asset, transcript, frames, *, start, end, production_id, unit_id):
    """Migrate verified, timestamp-cited Pro scene descriptions without a model call."""
    if cache_source_conflicts(path, asset, transcript):
        return False
    try:
        text = Path(prior_path).read_text(encoding="utf-8-sig")
        prior = NarrationArtifact.model_validate_json(text)
    except (OSError, ValueError):
        return False
    if ((prior.production_id, prior.unit_id, prior.source_asset_id) != (production_id, unit_id, asset.asset_id)
            or prior.summary_mode != "pro" or prior.understanding_method != "frame_sequence"
            or prior.source_media_path != asset.stored_path
            or prior.source_transcript_path != transcript.transcript_path
            or prior.multimodal_calls < 1):
        return False
    from app.production.narration import validate_narration_artifact
    try:
        validate_narration_artifact(prior, source_asset=asset, source_transcript=transcript)
    except ValueError:
        return False
    inspected = {round(ts, 3) for chapter in prior.chapters for ts in chapter.frame_timestamps}
    notes = []
    for index, (stamp, _) in enumerate(frames):
        stamp = round(float(stamp), 3)
        if stamp not in inspected:
            return False
        beats = [beat for beat in prior.beats if any(stamp in {round(t, 3) for t in ref.visual_frame_timestamps}
                                                    for ref in beat.source_refs)]
        if beats:
            description = " ".join(dict.fromkeys(beat.narration_text for beat in beats))
            if len(description) > 1700:
                return False
            chapter = next((c for c in prior.chapters if stamp in {round(t, 3) for t in c.frame_timestamps}), None)
            observer_model = getattr(chapter, "visual_observer_model_id", None) or prior.visual_observer_model_id
            if not observer_model and prior.visual_understanding_strategy != "visual_notes_then_text_storyteller":
                # A legacy direct-Pro model can be the observer. A text-only
                # storyteller can NEVER stand in for missing observer metadata.
                from app.content.model_registry import get_model
                try:
                    if get_model(prior.summary_understanding_model_id).capabilities.image_input:
                        observer_model = prior.summary_understanding_model_id
                except KeyError:
                    pass
            note = {"frame_id": index, "timestamp_seconds": stamp,
                "action": "Retained scene-level observation; not an individual-frame caption: " + description,
                "evidence_kind": "prior_pro_scene", "citable": True,
                "observer_model": observer_model,
                "observer_provider": getattr(chapter, "visual_observer_provider", None) or prior.visual_observer_provider,
                "source_beat_orders": [beat.order for beat in beats],
                "evidence_frame_timestamps": sorted({ts for beat in beats for ref in beat.source_refs
                                                     for ts in ref.visual_frame_timestamps})}
        else:
            note = {"frame_id": index, "timestamp_seconds": stamp,
                    "action": "Previously inspected frame has no retained visual description. Do not infer its action or cite it.",
                    "evidence_kind": "unavailable", "citable": False}
        notes.append(note)
    save_frame_observations(path, asset, transcript, frames, notes, start=start, end=end,
        provenance={"kind": "prior_pro_scene_hydration", "artifact_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "artifact_name": Path(prior_path).name, "observer_model": prior.summary_understanding_model_id,
                    "generated_at": prior.generated_at})
    return True