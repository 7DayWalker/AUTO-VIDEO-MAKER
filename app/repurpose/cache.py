"""Read existing source evidence; never upload, sample frames or call Whisper."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.production.models import NarrationArtifact
from app.production.storage import load_production, source_artifact_path, unit_plan_dir
from app.production.visual_cache import CACHE_FILENAME, load_cached_visual_evidence, visual_source_identity
from app.repurpose.models import RepurposeError
from app.repurpose.storage import content_hash, read_json, safe_production_dir
from app.transcription.transcribe import load_transcript


@dataclass
class Evidence:
    production: object
    asset: object
    transcript: object
    source_unit_id: str | None
    visuals: list
    beats: list
    identity: dict
    revision: str
    media: object
    anchors: list = field(default_factory=list)


def load_evidence(production_id, asset_id, source_unit_id=None, *, root=None, include_prepared=False):
    directory = safe_production_dir(production_id, root)
    production = load_production(production_id, root=root)
    if production.production_id != production_id:
        raise RepurposeError("SOURCE_OWNERSHIP_INVALID", "Production directory identity changed", 422)
    asset = next((a for a in production.source_assets or [] if a.asset_id == asset_id), None)
    if asset is None:
        raise RepurposeError("SOURCE_NOT_FOUND", "Select a source owned by this production", 404)
    if asset.source_kind == "sandbox_fixture":
        raise RepurposeError("SANDBOX_SOURCE", "Repurpose requires a real source, not sandbox media", 422)
    if not 60 <= asset.duration <= 14400:
        raise RepurposeError("SOURCE_DURATION_UNSUPPORTED", "Source must be 1 minute to 4 hours", 422)
    media = source_artifact_path(production_id, asset.stored_path, root).resolve()
    try:
        media.relative_to(directory)
    except ValueError:
        raise RepurposeError("SOURCE_OWNERSHIP_INVALID", "Source must stay under its production", 422) from None
    if not media.is_file() or (asset.file_size is not None and media.stat().st_size != asset.file_size):
        raise RepurposeError("SOURCE_MEDIA_MISSING", "Cached source is missing or its size changed", 422)
    try:
        transcript = load_transcript(production_id, asset_id, root=root)
    except FileNotFoundError:
        raise RepurposeError("TRANSCRIPT_CACHE_REQUIRED", "Transcribe explicitly before cache-only repurposing", 422) from None
    if transcript.asset_id != asset_id or transcript.provider == "mock" or not transcript.segments:
        raise RepurposeError("TRANSCRIPT_CACHE_REQUIRED", "A non-empty real transcript is required", 422)
    if any(s.end > asset.duration + .1 for s in transcript.segments):
        raise RepurposeError("TRANSCRIPT_CACHE_INVALID", "Transcript extends beyond source duration", 422)
    import math
    for segment in transcript.segments:
        for word in segment.words or []:
            a, b = word.get("start"), word.get("end")
            if (isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, (int, float)) or not isinstance(b, (int, float))
                    or not math.isfinite(a) or not math.isfinite(b) or not 0 <= a < b <= asset.duration+.1
                    or not isinstance(word.get("word", word.get("text")), str)):
                raise RepurposeError("TRANSCRIPT_WORD_CACHE_INVALID", "Cached word timing is invalid", 422)
    units = [u for u in production.units if u.repurpose_plan_reference is None]
    if source_unit_id:
        units = [u for u in units if u.unit_id == source_unit_id]
        if not units:
            raise RepurposeError("EVIDENCE_UNIT_NOT_FOUND", "Select an evidence-owning source unit", 404)
    chosen, visuals, beats = source_unit_id, [], []
    for unit in units:
        plan = unit_plan_dir(production_id, unit.unit_id, root)
        try:
            cached = load_cached_visual_evidence(plan / CACHE_FILENAME, asset, transcript)
        except ValueError:
            if source_unit_id:
                raise RepurposeError("STALE_VISUAL_CACHE", "Visual cache does not match the selected source", 422) from None
            continue
        retained = []
        narration_path = plan / "narration.json"
        if narration_path.is_file():
            try:
                narration = NarrationArtifact.model_validate(read_json(narration_path))
                if narration.source_asset_id == asset_id and narration.source_media_path == asset.stored_path and narration.status in {"grounded", "approved"}:
                    for beat in narration.beats:
                        refs = [r.model_dump(mode="json") for r in beat.source_refs
                                if r.asset_id == asset_id and r.end_seconds <= asset.duration]
                        if refs:
                            retained.append({"order": beat.order, "title": beat.title,
                                "text": beat.narration_text, "source_refs": refs})
            except (ValueError, OSError):
                pass
        if cached or retained:
            chosen, visuals, beats = unit.unit_id, cached, retained
            break
    identity = visual_source_identity(asset, transcript)
    prepared = directory / "source" / f"{asset_id}.repurpose" / CACHE_FILENAME
    prepared_visuals = load_cached_visual_evidence(prepared, asset, transcript) if include_prepared and prepared.is_file() else []
    if prepared_visuals:
        visuals = list({round(n["timestamp_seconds"], 3): n for n in [*visuals, *prepared_visuals]}.values())
        visuals.sort(key=lambda n: n["timestamp_seconds"])
    anchor_path = directory / "source" / f"{asset_id}.repurpose" / "anchors.json"
    anchors = []
    if include_prepared and anchor_path.is_file():
        saved = read_json(anchor_path)
        if saved.get("identity") != identity:
            raise RepurposeError("STALE_ACTION_ANCHORS", "Verified action anchors belong to different source/transcript evidence", 422)
        from app.repurpose.preparation import ActionAnchor
        anchors = [ActionAnchor.model_validate(a).model_dump(mode="json") for a in saved.get("anchors", [])]
        if len({a["evidence_id"] for a in anchors}) != len(anchors) or any(a["end_seconds"] > asset.duration for a in anchors):
            raise RepurposeError("INVALID_ACTION_ANCHORS", "Saved action anchors have invalid coverage/identities", 422)
    revision = content_hash({"identity": identity, "source_unit_id": chosen, "visuals": visuals, "beats": beats})
    if anchors:
        revision = content_hash({"legacy_revision": revision, "anchors": anchors})
    return Evidence(production, asset, transcript, chosen, visuals, beats, identity, revision, media, anchors)


def sources_payload(production_id, *, root=None):
    safe_production_dir(production_id, root)
    production = load_production(production_id, root=root)
    sources = []
    for asset in production.source_assets or []:
        row = {"asset_id": asset.asset_id, "filename": asset.original_filename,
                "duration_seconds": asset.duration, "source_sha256": asset.sha256, "source_kind": asset.source_kind,
                "ready": False, "transcript_ready": False, "beat_locked_ready": False,
                "media_url": f"/api/productions/{production_id}/repurpose/sources/{asset.asset_id}/media"}
        try:
            evidence = load_evidence(production_id, asset.asset_id, root=root, include_prepared=True)
            row.update(ready=True, source_unit_id=evidence.source_unit_id, evidence_revision=evidence.revision,
                transcript_segments=len(evidence.transcript.segments), language=evidence.transcript.language,
                captions_precision="word" if all(s.words for s in evidence.transcript.segments) else "segment_estimated_phrases",
                visual_observations=len(evidence.visuals), recap_beats=len(evidence.beats), transcript_ready=True,
                visual_evidence_ready=bool(evidence.visuals), beat_locked_ready=len(evidence.anchors) >= 4,
                verified_actions=len(evidence.anchors), anchors=evidence.anchors,
                observations=[{"timestamp_seconds": n["timestamp_seconds"], "action": n["action"]} for n in evidence.visuals],
                readiness_reasons=[] if len(evidence.anchors) >= 4 else ["VERIFY_ACTION_ANCHORS"])
        except (RepurposeError, ValueError, OSError) as exc:
            row["error"] = getattr(exc, "code", "CACHE_INVALID")
        sources.append(row)
    return {"production_id": production_id, "sources": sources}