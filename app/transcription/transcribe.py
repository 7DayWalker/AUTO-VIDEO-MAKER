"""Track 8 V1 transcription core."""
from __future__ import annotations
import json, os, re, tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from app.production.models import SourceTranscript, TranscriptSegment
from app.production.storage import load_production, transcript_path_for, sandbox_transcript_path_for, source_artifact_path
from app.transcription.providers import MockProvider, get_provider
def _atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=path.name + ".tmp-", delete=False) as fh:
            tmp = Path(fh.name)
            json.dump(obj, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        try:
            if tmp is not None: tmp.unlink()
        except OSError: pass
def transcribe_source(production_id: str, asset_id: str, *, root: Optional[Path]=None, provider: Optional[str]=None,
                     mock_mode: Optional[bool]=None) -> Tuple[int, dict]:
    # None preserves the legacy direct provider="mock" sidecar contract. HTTP
    # always supplies a boolean and isolates mock output from real STT work.
    if mock_mode is not None and not isinstance(mock_mode, bool):
        return 400, {"error":"INVALID_REQUEST","message":"mock_mode must be a JSON boolean"}
    if not isinstance(production_id, str) or "\x00" in production_id or not production_id.strip():
        return 400, {"error":"INVALID_REQUEST","message":"invalid production_id"}
    if not isinstance(asset_id, str) or "\x00" in asset_id or not re.match(r"^src-\d{3}$", asset_id.strip()):
        return 400, {"error":"INVALID_SOURCE_ASSET","message":"asset_id must match src-NNN"}
    asset_id = asset_id.strip()
    if provider is not None and (not isinstance(provider, str) or "\x00" in provider):
        return 400, {"error":"INVALID_REQUEST","message":"provider must be text without null bytes"}
    sandbox = mock_mode is True or (mock_mode is not None and (provider or "").strip().lower() == "mock")
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
        # Explicit sandbox intent wins over configured/previous real providers.
        prov = MockProvider() if sandbox else get_provider(provider)
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
    tpath = (sandbox_transcript_path_for if sandbox else transcript_path_for)(production_id, asset_id, root=root)
    rel=f"source/{tpath.name}"
    transcript=SourceTranscript(asset_id=asset_id, language=(result.language.strip() if isinstance(result.language,str) and result.language.strip() else None), segments=segments, provider=result.provider, provider_version=result.provider_version, created_at=datetime.now().isoformat(), transcript_path=rel)
    try:
        _atomic_write_json(tpath, transcript.model_dump())
    except Exception as e:
        return 500, {"error":"PERSIST_FAILED","message":f"{e.__class__.__name__}: {e}"}
    return 201, {"production_id":production_id,"asset_id":asset_id,"transcript_path":rel,"provider":transcript.provider,"provider_version":transcript.provider_version,"language":transcript.language,"segment_count":len(segments),"segments":[s.model_dump() for s in segments],"mock_mode":transcript.provider == "mock"}
def load_transcript(production_id: str, asset_id: str, *, root: Optional[Path]=None, mock_mode: bool=False):
    from app.production.models import SourceTranscript as ST
    tpath=transcript_path_for(production_id, asset_id, root=root)
    if mock_mode:
        sandbox_path = sandbox_transcript_path_for(production_id, asset_id, root=root)
        if sandbox_path.is_file():
            tpath = sandbox_path
    if not tpath.is_file():
        raise FileNotFoundError(f"transcript not found: {tpath}")
    data=json.loads(tpath.read_text(encoding="utf-8"))
    if mock_mode and data.get("provider") != "mock":
        raise FileNotFoundError("Transcribe the source in sandbox mode first; real transcripts are not sandbox evidence.")
    return ST(**data)
