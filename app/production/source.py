"""Track 8 V1: source media ingestion local-first."""
from __future__ import annotations
import hashlib, json, shutil, os, re, tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from app.production.models import Production, SourceAsset
from app.production.storage import load_production, production_source_dir, production_source_meta, sandbox_source_dir, sandbox_source_meta
from app.transcription.diagnostics import media_error_reason
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
def _next_asset_id(production: Production, reserved=()) -> str:
    used = {a.asset_id for a in (production.source_assets or [])} | set(reserved)
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
def load_sandbox_source(production_id: str, *, root: Optional[Path]=None) -> SourceAsset:
    """Read isolated synthetic evidence; never mutate the real source list."""
    payload = json.loads(sandbox_source_meta(production_id, root).read_text(encoding="utf-8"))
    asset = SourceAsset.model_validate(payload["source_asset"])
    if (payload.get("production_id") != production_id or asset.source_kind != "sandbox_fixture"
            or asset.stored_path != f"source/sandbox/{asset.asset_id}.mp4"):
        raise ValueError("sandbox source sidecar identity/path mismatch")
    return asset


def is_sandbox_source(asset: SourceAsset) -> bool:
    # Legacy provenance is explicit; an ordinary short/silent operator video
    # must never be classified as synthetic from its duration/dimensions alone.
    return (asset.source_kind == "sandbox_fixture" or asset.stored_path.startswith("source/sandbox/")
            or (asset.source_kind is None and asset.original_filename == "sandbox-summary-fixture.mp4"))


def find_source_asset(production: Production, asset_id: str, *, root: Optional[Path]=None,
                      include_sandbox=False, stored_path=None):
    """Exact artifact identity; canonical acceptance must never fall back."""
    asset = next((item for item in production.source_assets or [] if item.asset_id == asset_id
                  and (stored_path is None or item.stored_path == stored_path)), None)
    if asset is not None or not include_sandbox:
        return asset
    try:
        sandbox = load_sandbox_source(production.production_id, root=root)
    except FileNotFoundError:
        return None
    return sandbox if sandbox.asset_id == asset_id and (stored_path is None or sandbox.stored_path == stored_path) else None


def _upgrade_legacy_sandbox_source(production: Production, *, root: Optional[Path]=None) -> SourceAsset:
    """Upgrade into isolated storage, never rewrite an old intent/media artifact."""
    try:
        existing = load_sandbox_source(production.production_id, root=root)
        from app.production.storage import source_artifact_path
        info = _probe_source(source_artifact_path(production.production_id, existing.stored_path, root=root))
        if info.has_video and info.has_audio and info.duration and info.duration > 0:
            return existing
    except FileNotFoundError:
        pass
    status, payload = register_source(production.production_id, root=root, mock_mode=True)
    if status != 201:
        raise RuntimeError(payload.get("message", "The legacy sandbox source could not be upgraded with audio."))
    return SourceAsset.model_validate(payload["source_asset"])


def select_source_asset(production: Production, asset_id=None, *, root: Optional[Path]=None, mock_mode=False):
    """Operator commands keep real/sandbox pointers independent.

    Only a stale known synthetic identity may fall back to the last real
    source. Unknown identities and genuine real-media failures never do.
    """
    real = [item for item in production.source_assets or [] if not is_sandbox_source(item)]
    if asset_id is not None:
        asset = find_source_asset(production, asset_id, root=root, include_sandbox=True)
        if asset is None:
            return None
        if mock_mode or not is_sandbox_source(asset):
            return asset
        if real:
            return real[-1]
        # An explicit fixture request still executes the real STT provider; it
        # never silently enables mock. Legacy video-only media is upgraded in a
        # sidecar first, not replaced in the production's registered source list.
        if asset.stored_path.startswith("source/sandbox/"):
            return asset
        return _upgrade_legacy_sandbox_source(production, root=root)
    if not mock_mode:
        if real:
            return real[-1]
        legacy = [item for item in production.source_assets or [] if is_sandbox_source(item)]
        return _upgrade_legacy_sandbox_source(production, root=root) if legacy else None
    try:
        return load_sandbox_source(production.production_id, root=root)
    except FileNotFoundError:
        return (production.source_assets or [None])[-1]


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
    source_dir = sandbox_source_dir(production_id, root=root)
    source_dir.mkdir(parents=True, exist_ok=True)
    try:
        from app.production.sandbox import create_sandbox_source

        with tempfile.TemporaryDirectory(prefix=".sandbox-", dir=source_dir) as scratch:
            fixture = Path(scratch) / "sandbox-summary-fixture.mp4"
            create_sandbox_source(fixture)
            info = _probe_source(fixture)
            if not info.has_video or not info.has_audio or not info.duration or info.duration <= 0:
                raise RuntimeError("sandbox fixture requires valid video and audio streams")
            try:
                previous = load_sandbox_source(production_id, root=root)
            except FileNotFoundError:
                previous = None
            used = {asset.asset_id for asset in prod.source_assets or []}
            asset_id = previous.asset_id if previous and previous.asset_id not in used else _next_asset_id(prod)
            destination = source_dir / f"{asset_id}.mp4"
            asset = SourceAsset(asset_id=asset_id, stored_path=f"source/sandbox/{asset_id}.mp4",
                original_filename="sandbox-summary-fixture.mp4", source_kind="sandbox_fixture", sha256=sha256_file(fixture),
                duration=info.duration, width=info.width, height=info.height, video_codec=info.video_codec,
                audio_codec=info.audio_codec, file_size=fixture.stat().st_size, mime="video/mp4", ingested_at=datetime.now().isoformat())
            os.replace(fixture, destination)
            _atomic_write_json(sandbox_source_meta(production_id, root), {"production_id": production_id, "source_asset": asset.model_dump()})
            return 201, {"production_id": production_id, "source_asset": asset.model_dump(), "stored_path": asset.stored_path,
                         "mock_mode": True, "sandbox_fixture": True,
                         "message": "Isolated synthetic sandbox source with audio. Real production sources are unchanged."}
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
        return 503, {"error":"FFPROBE_UNAVAILABLE","message":media_error_reason(e)}
    except Exception as e:
        return 400, {"error":"INVALID_SOURCE","message":f"source probing failed: {e.__class__.__name__}: {media_error_reason(e)}"}
    if not info.has_video or info.duration is None or info.duration <= 0:
        return 400, {"error":"INVALID_SOURCE","message":"source is not a valid video (no video stream or duration)"}
    try:
        prod = load_production(production_id, root=root)
    except FileNotFoundError:
        return 404, {"error":"PRODUCTION_NOT_FOUND","message":f"production '{production_id}' not found"}
    except Exception as e:
        return 500, {"error":"LOAD_FAILED","message":f"{e.__class__.__name__}: {e}"}
    try:
        reserved = [load_sandbox_source(production_id, root=root).asset_id]
    except FileNotFoundError:
        reserved = []
    asset_id = _next_asset_id(prod, reserved)
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
