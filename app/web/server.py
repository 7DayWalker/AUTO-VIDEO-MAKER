"""
AUTO Video Maker — Local Web Experience Server (stdlib only)

Serves the premium web UI (webapp/) plus a THIN JSON API that adapts the
existing connection layer and reads real project manifests read-only.

Design rules:
- NO publishing/OAuth logic lives here. Connection endpoints call the Step-1
  connection module; project endpoints parse output/projects manifests.
- Binds to 127.0.0.1 only. Secrets are never returned or logged.
- Connect endpoints run the existing interactive OAuth flows in a background
  thread and respond 202 immediately; the UI polls /api/connections.

Run:
    python -m app.web.server            (default port from AVM_WEB_PORT, else 8787)
"""

import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from app.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

WEBAPP_DIR = PROJECT_ROOT / "webapp"
PROJECTS_DIR = PROJECT_ROOT / "output" / "projects"
DEFAULT_PORT = int(os.getenv("AVM_WEB_PORT", "8787"))

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json",
    ".mp4": "video/mp4",
}

# Tracks an in-flight interactive connect so repeated POSTs don't stack flows.
_connect_running = {"platform": None}


# ---------------------------------------------------------------------------
# Data adapters (thin!)
# ---------------------------------------------------------------------------

def connections_payload() -> dict:
    """Connection status via the Step-1 connection layer (non-interactive)."""
    from app.publishing.connections import _tiktok_status, _youtube_status
    from app.publishing.tiktok import TikTokPublisher
    from app.publishing.youtube import YouTubePublisher

    payload = {}
    try:
        yt = dict(_youtube_status(YouTubePublisher.from_env()))
    except Exception as exc:
        logger.warning("YouTube status failed: %s", exc)
        yt = {"status": "ERROR"}
    yt.pop("detail", None)
    yt["sample"] = False
    payload["youtube"] = yt

    try:
        tk = dict(_tiktok_status(TikTokPublisher.from_env()))
    except Exception as exc:
        logger.warning("TikTok status failed: %s", exc)
        tk = {"status": "ERROR"}
    tk.pop("detail", None)
    tk["sample"] = False
    payload["tiktok"] = tk

    payload["instagram"] = {"status": "COMING SOON", "sample": False}
    payload["facebook"] = {"status": "COMING SOON", "sample": False}
    return payload


def _load_manifest(project_dir: Path):
    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except Exception as exc:
        logger.warning("Could not parse %s: %s", manifest_path, exc)
        return None


def _publication_summary(manifest: dict) -> list:
    out = []
    yt = manifest.get("publication") or {}
    if yt.get("state"):
        out.append({
            "platform": "youtube", "state": yt.get("state"),
            "post_id": yt.get("youtube_video_id"), "sample": False,
        })
    tk = manifest.get("tiktok_publication") or {}
    if tk.get("state"):
        out.append({
            "platform": "tiktok", "state": tk.get("state"),
            "publish_id_present": bool(tk.get("tiktok_publish_id")),
            "post_id": tk.get("tiktok_post_id"), "sample": False,
        })
    return out


def projects_index() -> list:
    if not PROJECTS_DIR.exists():
        return []
    items = []
    for child in sorted(PROJECTS_DIR.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        manifest = _load_manifest(child)
        if not manifest:
            continue
        stages = manifest.get("stages") or {}
        stage_status = "running"
        if manifest.get("completed_at"):
            stage_status = "complete"
        elif any(s.get("status") == "FAILED" for s in stages.values()):
            stage_status = "failed"
        final_video = manifest.get("final_video_path")
        items.append({
            "project_id": manifest.get("project_id") or child.name,
            "topic": manifest.get("topic") or child.name,
            "created_at": manifest.get("created_at"),
            "current_stage": str(manifest.get("current_stage", "")),
            "stage_status": stage_status,
            "final_video_available": bool(final_video and Path(final_video).exists()),
            "publications": _publication_summary(manifest),
            "sample": False,
        })
    return items


def project_detail(project_id: str):
    directory = PROJECTS_DIR / project_id
    # Path-traversal guard: resolved dir must stay inside PROJECTS_DIR.
    try:
        directory.resolve().relative_to(PROJECTS_DIR.resolve())
    except (ValueError, OSError):
        return None
    manifest = _load_manifest(directory)
    if not manifest:
        return None
    wanted = manifest.get("project_id")
    for item in projects_index():
        if item["project_id"] == wanted:
            return item
    return None


def media_path_for(project_id: str):
    detail = project_detail(project_id)
    if not detail or not detail.get("final_video_available"):
        return None
    manifest = _load_manifest(PROJECTS_DIR / project_id) or {}
    raw = manifest.get("final_video_path")
    if not raw:
        return None
    path = Path(raw)
    try:
        path.resolve().relative_to(PROJECT_ROOT.resolve())
    except (ValueError, OSError):
        return None
    return path if path.exists() else None


def _start_connect(platform: str):
    """Run the existing Step-1 interactive connect flow in the background."""
    from app.publishing import connections as conn

    if platform not in ("youtube", "tiktok"):
        return 404, {"detail": "unknown platform"}
    if not _connect_running["platform"]:
        def runner():
            try:
                if platform == "youtube":
                    conn.run_connect_youtube()
                else:
                    conn.run_connect_tiktok()
            finally:
                _connect_running["platform"] = None

        _connect_running["platform"] = platform
        threading.Thread(target=runner, daemon=True).start()
    return 202, {
        "detail": "authorization flow started; a browser window will open on this machine",
        "platform": platform,
    }


# ---------------------------------------------------------------------------
# TikTok publish bridge — THIN adapter over the EXISTING state machine
#
# Every real operation delegates to app/publishing.tiktok_reconciliation
# (prepare_tiktok_publish / submit_tiktok_publish / verify_tiktok_publish /
# reconcile_tiktok_publication). The web layer adds ONLY:
#   - request validation (project exists, TikTok connected)
#   - a single-flight lock so double-clicks/tabs cannot stack submissions
#   - truthful JSON mapping of durable states and errors
# It never implements upload, duplicate-prevention, or reconciliation logic.
# ---------------------------------------------------------------------------

_publish_lock = threading.Lock()


def _new_publisher():
    """Factory indirection so tests can inject a fake publisher."""
    from app.publishing.tiktok import TikTokPublisher

    return TikTokPublisher.from_env()


def _prepare(project_id: str, privacy_level=None):
    from app.publishing.tiktok_reconciliation import prepare_tiktok_publish

    return prepare_tiktok_publish(project_id, privacy_level=privacy_level)


def _submit(project_id: str, publisher):
    from app.publishing.tiktok_reconciliation import submit_tiktok_publish

    return submit_tiktok_publish(project_id, publisher)


def _verify(project_id: str, publisher):
    from app.publishing.tiktok_reconciliation import verify_tiktok_publish

    return verify_tiktok_publish(
        project_id, publisher, poll_attempts=5, poll_interval_seconds=3.0
    )


def _reconcile(project_id: str, publisher=None):
    from app.publishing.tiktok_reconciliation import reconcile_tiktok_publication

    return reconcile_tiktok_publication(project_id, publisher=publisher)


def _current_record(project_id: str):
    """Read the durable TikTok record from THIS bridge's project directory
    (honours test isolation; read-only, never mutates anything)."""
    from app.orchestrator.models import ProjectManifest
    from app.publishing.tiktok_reconciliation import get_tiktok_record

    directory = PROJECTS_DIR / project_id
    try:
        directory.resolve().relative_to(PROJECTS_DIR.resolve())
    except (ValueError, OSError):
        return None
    data = _load_manifest(directory)
    if not data:
        return None
    try:
        manifest = ProjectManifest.from_dict(data)
    except Exception as exc:
        logger.warning("Could not parse manifest for %s: %s", project_id, exc)
        return None
    return get_tiktok_record(manifest)


def _tiktok_connected(publisher) -> bool:
    """Non-interactive connectivity check; NEVER opens a browser."""
    try:
        return publisher.validate_stored_token() is not None
    except Exception:
        return False


def _record_payload(record) -> dict:
    meta = getattr(record, "tiktok_metadata", None)
    return {
        "state": record.state.value if record else None,
        "publish_id": getattr(record, "tiktok_publish_id", None),
        "post_id": getattr(record, "tiktok_post_id", None),
        "privacy_level": meta.privacy_level if meta else None,
        "error": getattr(record, "error", None),
        "verified_at": getattr(record, "verified_at", None),
        "sample": False,
    }


def tiktok_status_payload(project_id: str):
    """
    Truthful, repeatable status. Reconciles through the existing engine only
    when a stored token is valid AND a durable publish_id exists in a
    non-terminal state (fetch_status is read-only). Otherwise returns the
    durable stored state untouched. Never uploads, never creates identities,
    never launches OAuth.
    """
    detail = project_detail(project_id)
    if detail is None:
        return 404, {"detail": "project not found"}

    record = _current_record(project_id)
    publisher = _new_publisher()
    connected = _tiktok_connected(publisher)

    state = record.state.value if record else None
    note = None
    if connected and record is not None and record.tiktok_publish_id \
            and state not in ("VERIFIED", "VERIFIED_SANDBOX_PRIVATE", "FAILED",
                              "UNKNOWN_RECONCILIATION_REQUIRED"):
        try:
            report = _reconcile(project_id, publisher=publisher)
            state = report.state or state
            note = "; ".join(report.reasons) or None
        except Exception as exc:
            logger.warning("status reconciliation failed truthfully: %s", exc)
            note = f"live reconciliation unavailable: {exc}"

    meta = record.tiktok_metadata if record else None
    return 200, {
        "project_id": project_id,
        "connected": connected,
        "state": state,
        "can_publish": bool(
            connected
            and detail.get("stage_status") == "complete"
            and state in (None, "NOT_STARTED", "PREPARED", "FAILED")
        ),
        "publish_id": record.tiktok_publish_id if record else None,
        "post_id": record.tiktok_post_id if record else None,
        "privacy_level": meta.privacy_level if meta else None,
        "error": record.error if record else None,
        "note": note,
        "sample": False,
    }


def tiktok_publish_now(project_id: str, privacy_level=None):
    """
    Explicitly requested publish. Runs the EXISTING sequence synchronously
    (acceptable for the local single-user demo): offline reconcile pre-check
    → prepare → creator capability check → submit → verify. All durability
    and duplicate protection belong to the existing modules; this wrapper
    refuses rather than retries.
    """
    if not _publish_lock.acquire(blocking=False):
        return 409, {
            "error": "PUBLISH_ALREADY_RUNNING",
            "detail": "another publishing operation is already in progress; "
                      "the durable state machine prevents duplicates either way",
        }
    try:
        detail = project_detail(project_id)
        if detail is None:
            return 404, {"detail": "project not found"}

        publisher = _new_publisher()
        if not _tiktok_connected(publisher):
            stored = _current_record(project_id)
            return 409, {
                "error": "TIKTOK_NOT_CONNECTED",
                "detail": "TikTok is not connected on this machine; connect via "
                          "Platforms first. Nothing was uploaded.",
                "state": stored.state.value if stored else None,
            }

        # Offline pre-check through authoritative reconciliation (no network).
        report = _reconcile(project_id, publisher=None)
        verdict = report.verdict.value
        if verdict == "PUBLICATION_VERIFIED":
            payload = _record_payload(_current_record(project_id))
            payload.update({
                "published_already": True,
                "detail": "Already published and verified; no second submission.",
            })
            return 200, payload
        if verdict == "PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED":
            return 409, {
                "error": "UNKNOWN_RECONCILIATION_REQUIRED",
                "detail": "A previous submission may have reached TikTok without "
                          "a durable identity. Blind retry is forbidden; manual "
                          "reconciliation required.",
                "reasons": report.reasons,
            }
        if verdict == "PUBLICATION_FAILED":
            return 409, {
                "error": "PREVIOUS_FAILURE",
                "detail": "A previous publication failed; inspect the manifest "
                          "before any new attempt.",
                "reasons": report.reasons,
            }

        # Existing eligibility gate + artifact fingerprint binding.
        record = _prepare(project_id, privacy_level=privacy_level)

        # Creator capability check — mirrors the existing operator flow.
        try:
            creator = publisher.get_creator_info() or {}
            options = creator.get("privacy_level_options") or []
            wanted = record.tiktok_metadata.privacy_level
            if options and wanted not in options:
                return 409, {
                    "error": "PRIVACY_LEVEL_NOT_PERMITTED",
                    "detail": f"Privacy level {wanted} is not permitted for this "
                              f"account {options}. Nothing was submitted.",
                    "allowed_privacy_levels": options,
                }
        except Exception as exc:
            logger.info("creator capability check skipped truthfully: %s", exc)

        # Existing submit (duplicate protection inside) then existing verify.
        record = _submit(project_id, publisher)
        record = _verify(project_id, publisher)

        payload = _record_payload(record)
        payload["detail"] = (
            "Published and verified from official platform APIs"
            if record.state.value == "VERIFIED"
            else f"Submission handled by the durable state machine; current state: {record.state.value}"
        )
        return 200, payload

    except Exception as exc:
        name = type(exc).__name__
        detail = str(exc)
        mapping = {
            "PublishingEligibilityError": (422, "NOT_ELIGIBLE"),
            "MetadataValidationError": (422, "METADATA_INVALID"),
            "DuplicateUploadBlockedError": (409, "DUPLICATE_BLOCKED"),
            "UploadIdentityMissingError": (409, "UNKNOWN_RECONCILIATION_REQUIRED"),
            "TikTokAuthError": (502, "AUTH_FAILED"),
        }
        if name in mapping:
            status, error = mapping[name]
            return status, {"error": error, "detail": detail}
        logger.exception("TikTok publish failed")
        return 502, {
            "error": "SUBMISSION_FAILED",
            "detail": detail,
            "note": "If an init succeeded before failing, its durable publish_id "
                    "is already persisted; do NOT retry blindly.",
        }
    finally:
        _publish_lock.release()


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

class AVMHandler(BaseHTTPRequestHandler):
    server_version = "AVMWeb/1.0"

    # -- helpers ----------------------------------------------------------
    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quiet console
        logger.debug("%s %s", self.address_string(), fmt % args)

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/health":
                return self._json(200, {"ok": True, "service": "avm-web"})
            if path == "/api/connections":
                return self._json(200, connections_payload())
            if path == "/api/projects":
                return self._json(200, projects_index())
            match = re.match(r"^/api/projects/([^/]+)/publish/tiktok/status$", path)
            if match:
                status, payload = tiktok_status_payload(match.group(1))
                return self._json(status, payload)
            match = re.match(r"^/api/projects/([^/]+)/media$", path)
            if match:
                return self._serve_media(match.group(1))
            match = re.match(r"^/api/projects/([^/]+)/?$", path)
            if match:
                detail = project_detail(match.group(1))
                if detail:
                    return self._json(200, detail)
                return self._json(404, {"detail": "not found"})
            return self._static(path)
        except Exception:
            logger.exception("GET %s failed", path)
            return self._json(500, {"detail": "internal error"})

    def do_POST(self):
        path = urlparse(self.path).path
        match = re.match(r"^/api/platforms/(youtube|tiktok)/connect$", path)
        if match:
            status, payload = _start_connect(match.group(1))
            return self._json(status, payload)
        match = re.match(r"^/api/projects/([^/]+)/publish/tiktok$", path)
        if match:
            # Explicit, deliberate publishing request only.
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                body = {}
            if not isinstance(body, dict):
                body = {}
            status, payload = tiktok_publish_now(
                match.group(1), privacy_level=body.get("privacy_level")
            )
            return self._json(status, payload)
        return self._json(404, {"detail": "not found"})

    # -- media (range-aware enough for <video> seeking) --------------------
    def _serve_media(self, project_id: str):
        media = media_path_for(project_id)
        if not media:
            return self._json(404, {"detail": "media unavailable"})
        size = media.stat().st_size
        range_header = self.headers.get("Range", "")
        start, end = 0, size - 1
        if range_header.startswith("bytes="):
            parts = range_header[6:].split("-")
            try:
                start = int(parts[0]) if parts[0] else 0
                if parts[1]:
                    end = min(int(parts[1]), size - 1)
            except ValueError:
                start, end = 0, size - 1
        length = end - start + 1
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", MIME[".mp4"])
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(media, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # -- static files -------------------------------------------------------
    def _static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        candidate = (WEBAPP_DIR / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(WEBAPP_DIR.resolve())
        except (ValueError, OSError):
            return self._json(403, {"detail": "forbidden"})
        if not candidate.is_file():
            candidate = WEBAPP_DIR / "index.html"  # SPA fallback
        suffix = candidate.suffix.lower()
        body = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    port = DEFAULT_PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), AVMHandler)
    url = f"http://127.0.0.1:{port}"
    print("=" * 60)
    print("AUTO Video Maker — Web Experience")
    print(f"  UI + local API : {url}")
    print("  Data           : real manifests when present, labelled demo otherwise")
    print("  Publishing     : via existing produce.py flows (never automatic)")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
