"""Track 8 V1: source media ingestion local-first."""
from __future__ import annotations
import hashlib, shutil, os, re, tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from app.production.models import Production, SourceAsset
from app.production.storage import load_production, production_source_dir, production_source_meta
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
VIDEO_MIME_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".webm": "video/webm"}
MAX_SOURCE_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
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
def register_source(production_id: str, source_path: str = "", *, root: Optional[Path]=None, mock_mode: bool=False):
    """Canonical local intake with an explicitly requested synthetic fixture only.

    Missing real sources still fail. A provided bad path is never replaced by a
    fixture, even in sandbox mode. Fixtures pass the same media/storage gate.
    """
    if not isinstance(mock_mode, bool):
        return 400, {"error": "INVALID_REQUEST", "message": "mock_mode must be a JSON boolean"}
    if not isinstance(source_path, str):
        return 400, {"error": "INVALID_SOURCE", "message": "source_path must be text"}
    if source_path.strip() or not mock_mode:
        return _register_local_source(production_id, source_path, root=root)
    try:
        prod = load_production(production_id, root=root)
    except FileNotFoundError:
        return 404, {"error": "PRODUCTION_NOT_FOUND", "message": "Create or select a production first."}
    except Exception:
        return 500, {"error": "LOAD_FAILED", "message": "Production intent could not be loaded."}
    if prod.production_id != production_id:
        return 400, {"error": "INVALID_REQUEST", "message": "Production identity does not match its directory."}
    source_dir = production_source_dir(production_id, root=root)
    source_dir.mkdir(parents=True, exist_ok=True)
    try:
        from app.production.sandbox import create_sandbox_source

        with tempfile.TemporaryDirectory(prefix=".sandbox-", dir=source_dir) as scratch:
            fixture = Path(scratch) / "sandbox-summary-fixture.mp4"
            create_sandbox_source(fixture)
            status, payload = _register_local_source(production_id, str(fixture), root=root, source_kind="sandbox_fixture")
            if status == 201:
                payload.update({"mock_mode": True, "sandbox_fixture": True,
                                "message": "Synthetic sandbox source registered locally; no provider or platform call was made."})
            return status, payload
    except Exception:
        return 503, {"error": "SANDBOX_FIXTURE_UNAVAILABLE", "message": "Local FFmpeg/FFprobe is required to create the sandbox source fixture."}


def register_uploaded_source(production_id: str, filename: str, stream, length: int, *, root: Optional[Path]=None):
    """Bounded streaming adapter to the SAME source validation/storage authority.

    Client filenames are metadata only, never writable paths. Invalid, oversized
    or truncated uploads cannot create a SourceAsset or escape production storage.
    """
    if (not isinstance(filename, str) or not filename.strip() or len(filename) > 255
            or any(char in filename for char in ("/", "\\", "\x00"))
            or Path(filename).suffix.lower() not in VIDEO_EXTS):
        return 400, {"error": "INVALID_SOURCE", "message": "Provide a plain MP4, MOV, MKV or WebM filename."}
    if not isinstance(length, int) or isinstance(length, bool) or length <= 0:
        return 400, {"error": "INVALID_SOURCE", "message": "A non-empty upload with Content-Length is required."}
    if length > MAX_SOURCE_UPLOAD_BYTES:
        return 413, {"error": "PAYLOAD_TOO_LARGE", "message": "Source upload limit is 2 GiB. Use a Core-local file path for larger media."}
    try:
        prod = load_production(production_id, root=root)
    except FileNotFoundError:
        return 404, {"error": "PRODUCTION_NOT_FOUND", "message": "Create or select a production first."}
    except Exception:
        return 500, {"error": "LOAD_FAILED", "message": "Production intent could not be loaded."}
    if prod.production_id != production_id:
        return 400, {"error": "INVALID_REQUEST", "message": "Production identity does not match its directory."}
    source_dir = production_source_dir(production_id, root=root)
    source_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".upload-", dir=source_dir) as scratch:
        incoming = Path(scratch) / ("upload" + Path(filename).suffix.lower())
        remaining = length
        try:
            with incoming.open("wb") as handle:
                while remaining:
                    chunk = stream.read(min(remaining, 1024 * 1024))
                    if not chunk or len(chunk) > remaining:
                        raise ValueError("truncated source upload")
                    handle.write(chunk)
                    remaining -= len(chunk)
        except (OSError, ValueError):
            return 400, {"error": "INVALID_SOURCE", "message": "Source upload did not complete; no source was registered."}
        return _register_local_source(production_id, str(incoming), root=root,
                                      original_filename=filename.strip(), source_kind="browser_upload")


def _register_local_source(production_id: str, source_path: str, *, root: Optional[Path]=None,
                           original_filename: Optional[str]=None, source_kind: str="local_file"):
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
    # Allowed formats have fixed MIME types. Windows mimetypes initialization
    # scans the registry on the first request and can take multiple seconds.
    mime = VIDEO_MIME_TYPES[ext]
    asset = SourceAsset(asset_id=asset_id, original_filename=original_filename or p.name, stored_path=rel_path, sha256=sha, duration=float(info.duration), width=info.width, height=info.height, video_codec=info.video_codec, audio_codec=info.audio_codec, mime=mime, file_size=dst.stat().st_size, ingested_at=datetime.now().isoformat(), source_kind=source_kind)
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
