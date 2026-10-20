"""Track 8 V1 transcription core."""
from __future__ import annotations
import json, os, re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from app.production.models import SourceTranscript, TranscriptSegment
from app.production.storage import load_production, transcript_path_for, source_artifact_path
from app.transcription.providers import get_provider
def _atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try: tmp.unlink()
        except OSError: pass
def transcribe_source(production_id: str, asset_id: str, *, root: Optional[Path]=None, provider: Optional[str]=None) -> Tuple[int, dict]:
    if not isinstance(production_id, str) or "\x00" in production_id or not production_id.strip():
        return 400, {"error":"INVALID_REQUEST","message":"invalid production_id"}
    if not isinstance(asset_id, str) or "\x00" in asset_id or not re.match(r"^src-\d{3}$", asset_id.strip()):
        return 400, {"error":"INVALID_SOURCE_ASSET","message":"asset_id must match src-NNN"}
    asset_id = asset_id.strip()
    if provider is not None and "\x00" in str(provider):
        return 400, {"error":"INVALID_REQUEST","message":"provider contains null byte"}
    try:
        prod = load_production(production_id, root=root)
    except FileNotFoundError:
        return 404, {"error":"PRODUCTION_NOT_FOUND","message":f"production '{production_id}' not found"}
    except Exception as e:
        return 500, {"error":"LOAD_FAILED","message":f"{e.__class__.__name__}: {e}"}
    asset = next((a for a in (prod.source_assets or []) if a.asset_id==asset_id), None)
    if asset is None:
        return 404, {"error":"SOURCE_NOT_FOUND","message":f"source asset '{asset_id}' not found in {production_id}"}
    if asset.duration is None or asset.duration <= 0:
        return 500, {"error":"INVALID_SOURCE_ASSET","message":"source duration missing"}
    media_path = source_artifact_path(production_id, asset.stored_path, root=root)
    if not media_path.is_file():
        return 404, {"error":"SOURCE_NOT_FOUND","message":f"source file missing: {asset.stored_path}"}
    try:
        prov = get_provider(provider)
    except ValueError as e:
        return 400, {"error":"INVALID_REQUEST","message":str(e)}
    try:
        result = prov.transcribe(media_path, float(asset.duration))
    except RuntimeError as e:
        msg=str(e)
        if "STT_ENGINE_UNAVAILABLE" in msg:
            return 503, {"error":"STT_ENGINE_UNAVAILABLE","message":msg}
        return 500, {"error":"TRANSCRIPTION_FAILED","message":msg}
    except Exception as e:
        return 500, {"error":"TRANSCRIPTION_FAILED","message":f"{e.__class__.__name__}: {e}"}
    duration=float(asset.duration)
    segments: List[TranscriptSegment]=[]
    for idx, raw in enumerate(result.segments):
        try:
            text=raw.get("text",""); start=float(raw.get("start",-1)); end=float(raw.get("end",-1))
        except Exception as e:
            return 500, {"error":"TRANSCRIPTION_FAILED","message":f"invalid segment {idx}: {e}"}
        if "\x00" in str(text):
            return 500, {"error":"TRANSCRIPTION_FAILED","message":f"segment {idx} contains null byte"}
        if not isinstance(text,str) or not text.strip():
            continue
        if not (0 <= start < end <= duration + 1e-6):
            return 500, {"error":"TRANSCRIPTION_FAILED","message":f"segment {idx} timestamps out of bounds: {start}..{end} vs duration {duration}"}
        if end>duration: end=duration
        try:
            seg=TranscriptSegment(text=text.strip(), start=float(start), end=float(end), words=raw.get("words"))
        except Exception as e:
            return 500, {"error":"TRANSCRIPTION_FAILED","message":f"segment validation failed {idx}: {e}"}
        segments.append(seg)
    for i in range(1,len(segments)):
        if segments[i].start < segments[i-1].start:
            return 500, {"error":"TRANSCRIPTION_FAILED","message":"segments not chronologically ordered"}
    rel=f"source/{asset_id}.transcript.json"
    transcript=SourceTranscript(asset_id=asset_id, language=(result.language.strip() if isinstance(result.language,str) and result.language.strip() else None), segments=segments, provider=result.provider, provider_version=result.provider_version, created_at=datetime.now().isoformat(), transcript_path=rel)
    tpath=transcript_path_for(production_id, asset_id, root=root)
    try:
        _atomic_write_json(tpath, transcript.model_dump())
    except Exception as e:
        return 500, {"error":"PERSIST_FAILED","message":f"{e.__class__.__name__}: {e}"}
    return 201, {"production_id":production_id,"asset_id":asset_id,"transcript_path":rel,"provider":transcript.provider,"provider_version":transcript.provider_version,"language":transcript.language,"segment_count":len(segments),"segments":[s.model_dump() for s in segments]}
def load_transcript(production_id: str, asset_id: str, *, root: Optional[Path]=None):
    from app.production.models import SourceTranscript as ST
    tpath=transcript_path_for(production_id, asset_id, root=root)
    if not tpath.is_file():
        raise FileNotFoundError(f"transcript not found: {tpath}")
    data=json.loads(tpath.read_text(encoding="utf-8"))
    return ST(**data)
