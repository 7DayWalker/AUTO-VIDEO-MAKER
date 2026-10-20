"""Validated operator-selected source segments; no media extraction."""
from __future__ import annotations
import json, math, os, re, tempfile
from pathlib import Path
from typing import Optional
from app.production.models import SourceSegment, SourceTranscript
from app.production.storage import load_production, segments_path_for, transcript_path_for


def _identity(pid, aid):
    if not isinstance(pid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", pid.strip()):
        return None, None, (400, {"error": "INVALID_REQUEST"})
    if not isinstance(aid, str) or not re.fullmatch(r"src-\d{3}", aid.strip()):
        return None, None, (400, {"error": "INVALID_SOURCE_ASSET"})
    return pid.strip(), aid.strip(), None


def _context(pid, aid, root):
    try:
        production = load_production(pid, root=root)
    except FileNotFoundError:
        return None, None, (404, {"error": "PRODUCTION_NOT_FOUND"})
    except Exception as exc:
        return None, None, (500, {"error": "LOAD_FAILED", "message": type(exc).__name__})
    asset = next((a for a in (production.source_assets or []) if a.asset_id == aid), None)
    if asset is None:
        return None, None, (404, {"error": "SOURCE_NOT_FOUND"})
    if asset.duration is None or not math.isfinite(float(asset.duration)) or asset.duration <= 0:
        return None, None, (500, {"error": "INVALID_SOURCE_ASSET"})
    path = transcript_path_for(pid, aid, root=root)
    if not path.is_file():
        return None, None, (404, {"error": "TRANSCRIPT_NOT_FOUND", "message": "transcribe first"})
    try:
        transcript = SourceTranscript(**json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        return None, None, (500, {"error": "TRANSCRIPT_INVALID", "message": type(exc).__name__})
    if transcript.asset_id != aid:
        return None, None, (500, {"error": "TRANSCRIPT_INVALID", "message": "asset mismatch"})
    return asset, transcript, None


def save_segments(production_id, asset_id, body, *, root: Optional[Path] = None):
    pid, aid, error = _identity(production_id, asset_id)
    if error: return error
    if not isinstance(body, dict) or not isinstance(body.get("segments"), list):
        return 400, {"error": "INVALID_REQUEST", "message": "segments must be a list"}
    items = body["segments"]
    forbidden = {"path", "absolute_path", "stored_path", "transcript_path", "segments_path", "asset_path"}
    if any(isinstance(x, dict) and forbidden.intersection(x) for x in items):
        return 400, {"error": "INVALID_SEGMENT", "message": "filesystem paths are not accepted"}
    asset, transcript, error = _context(pid, aid, root)
    if error: return error
    allowed = {"start_seconds", "end_seconds", "title", "premise", "transcript_segment_ids"}
    output = []
    for i, raw in enumerate(items):
        if not isinstance(raw, dict) or set(raw) - allowed:
            return 400, {"error": "INVALID_SEGMENT", "message": f"invalid segment {i} fields"}
        values = [raw.get(k) for k in ("start_seconds", "end_seconds")]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) for v in values):
            return 400, {"error": "INVALID_SEGMENT", "message": f"segment {i} timestamps must be finite numbers"}
        start, end = map(float, values)
        if not 0 <= start < end <= float(asset.duration):
            return 400, {"error": "INVALID_SEGMENT", "message": f"segment {i} range exceeds source duration or is empty"}
        title, premise = raw.get("title"), raw.get("premise")
        for name, value, limit in (("title", title, 200), ("premise", premise, 500)):
            size = len(value.strip()) if name == "title" and isinstance(value, str) else len(value) if isinstance(value, str) else 0
            if value is not None and (not isinstance(value, str) or "\x00" in value or not value.strip() or size > limit):
                return 400, {"error": "INVALID_SEGMENT", "message": f"invalid segment {i} {name}"}
        title = title.strip() if title is not None else None
        premise = premise.strip() if premise is not None else None
        refs = raw.get("transcript_segment_ids")
        if refs is None:
            refs = [n for n, seg in enumerate(transcript.segments) if seg.start < end and seg.end > start]
        elif not isinstance(refs, list) or any(isinstance(r, bool) or not isinstance(r, int) or r < 0 or r >= len(transcript.segments) for r in refs):
            return 400, {"error": "INVALID_SEGMENT", "message": f"unknown transcript reference on segment {i}"}
        try:
            segment = SourceSegment(segment_id=f"seg-{i + 1:03d}", asset_id=aid, start_seconds=start, end_seconds=end, title=title, premise=premise, transcript_segment_ids=refs)
        except Exception as exc:
            return 400, {"error": "INVALID_SEGMENT", "message": str(exc)}
        output.append(segment.model_dump())
    relative = f"source/{aid}.segments.json"
    payload = {"production_id": pid, "asset_id": aid, "segments_path": relative, "segments": output}
    path = segments_path_for(pid, aid, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                prefix=path.name + ".tmp-", delete=False) as temp:
            temp_name = temp.name
            json.dump(payload, temp, indent=2, ensure_ascii=False)
        os.replace(temp_name, path)
    except Exception as exc:
        return 500, {"error": "PERSIST_FAILED", "message": type(exc).__name__}
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
    return 200, payload


def load_segments(production_id, asset_id, *, root=None):
    pid, aid, error = _identity(production_id, asset_id)
    if error: return error
    asset, transcript, error = _context(pid, aid, root)
    if error: return error
    relative = f"source/{aid}.segments.json"
    path = segments_path_for(pid, aid, root=root)
    if not path.is_file():
        return 200, {"production_id": pid, "asset_id": aid, "segments_path": relative, "segments": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("production_id") != pid or data.get("asset_id") != aid or data.get("segments_path") != relative or not isinstance(data.get("segments"), list):
            raise ValueError("invalid sidecar identity or shape")
        output = []
        for i, item in enumerate(data["segments"]):
            segment = SourceSegment(**item)
            if segment.segment_id != f"seg-{i + 1:03d}" or segment.asset_id != aid or segment.end_seconds > float(asset.duration):
                raise ValueError("invalid segment identity or duration")
            if any(ref >= len(transcript.segments) for ref in segment.transcript_segment_ids):
                raise ValueError("unknown transcript reference")
            output.append(segment.model_dump())
        return 200, {"production_id": pid, "asset_id": aid, "segments_path": relative, "segments": output}
    except Exception as exc:
        return 500, {"error": "SEGMENTS_INVALID", "message": type(exc).__name__}
