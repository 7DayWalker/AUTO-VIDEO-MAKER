"""Track 8 V1: source media ingestion local-first."""
from __future__ import annotations
import hashlib, mimetypes, shutil, os, re
from datetime import datetime
from pathlib import Path
from typing import Optional
from app.production.models import Production, SourceAsset
from app.production.storage import load_production, production_source_dir, production_source_meta
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()
def _probe_source(path: Path):
    from app.assembly.ffmpeg import probe_media, resolve_ffprobe
    if resolve_ffprobe() is None:
        raise RuntimeError("ffprobe unavailable: cannot validate source media")
    return probe_media(path)
def _next_asset_id(production: Production) -> str:
    used = {a.asset_id for a in (production.source_assets or [])}
    for i in range(1, 1000):
        cand = f"src-{i:03d}"
        if cand not in used:
            return cand
    raise RuntimeError("too many source assets")
def _atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + f".tmp-{os.getpid()}")
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except OSError:
        shutil.move(str(tmp), str(dst))
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
def _atomic_write_json(path: Path, obj: dict) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
def register_source(production_id: str, source_path: str, *, root: Optional[Path]=None):
    if not isinstance(source_path, str):
        return 400, {"error":"INVALID_SOURCE","message":"source_path must be text"}
    if "\x00" in source_path:
        return 400, {"error":"INVALID_SOURCE","message":"source_path contains an invalid character"}
    raw = source_path.strip()
    if not raw:
        return 400, {"error":"INVALID_SOURCE","message":"source_path is required"}
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        return 400, {"error":"INVALID_SOURCE","message":"URL ingestion is not supported in V1 (local file only)"}
    p = Path(raw)
    if not p.exists():
        return 400, {"error":"INVALID_SOURCE","message":"source file not found"}
    if not p.is_file():
        return 400, {"error":"INVALID_SOURCE","message":"source path is not a file"}
    ext = p.suffix.lower()
    if ext not in VIDEO_EXTS:
        return 400, {"error":"INVALID_SOURCE","message":"unsupported video extension"}
    try:
        info = _probe_source(p)
    except RuntimeError as e:
        return 503, {"error":"FFPROBE_UNAVAILABLE","message":str(e)}
    except Exception as e:
        return 400, {"error":"INVALID_SOURCE","message":f"source probing failed: {e.__class__.__name__}: {e}"}
    if not info.has_video or info.duration is None or info.duration <= 0:
        return 400, {"error":"INVALID_SOURCE","message":"source is not a valid video (no video stream or duration)"}
    try:
        prod = load_production(production_id, root=root)
    except FileNotFoundError:
        return 404, {"error":"PRODUCTION_NOT_FOUND","message":f"production '{production_id}' not found"}
    except Exception as e:
        return 500, {"error":"LOAD_FAILED","message":f"{e.__class__.__name__}: {e}"}
    asset_id = _next_asset_id(prod)
    rel_path = f"source/{asset_id}{ext}"
    from app.config import PRODUCTIONS_DIR
    base = Path(root) if root is not None else Path(PRODUCTIONS_DIR)
    dst = base / production_id / rel_path
    try:
        if p.resolve() == dst.resolve():
            sha = sha256_file(dst)
        else:
            _atomic_copy(p, dst); sha = sha256_file(dst)
    except Exception:
        _atomic_copy(p, dst); sha = sha256_file(dst)
    mime, _ = mimetypes.guess_type(p.name)
    asset = SourceAsset(asset_id=asset_id, original_filename=p.name, stored_path=rel_path, sha256=sha, duration=float(info.duration), width=info.width, height=info.height, video_codec=info.video_codec, audio_codec=info.audio_codec, mime=mime, file_size=dst.stat().st_size, ingested_at=datetime.now().isoformat())
    from app.production.storage import save_revision
    revised = prod.model_copy(update={"plan_version": prod.plan_version+1, "revised_at": datetime.now().isoformat(), "source_assets": list(prod.source_assets or [])+[asset]})
    try:
        save_revision(revised, root=root)
    except Exception as e:
        try:
            if dst.exists(): dst.unlink()
        except OSError:
            pass
        return 500, {"error":"PERSIST_FAILED","message":f"{e.__class__.__name__}: {e}"}
    try:
        meta_path = production_source_meta(production_id, root=root)
        _atomic_write_json(meta_path, {"production_id": production_id, "assets":[a.model_dump() for a in revised.source_assets]})
    except Exception:
        pass
    return 201, {"production_id": production_id, "source_asset": asset.model_dump(), "stored_path": rel_path}
