"""
AUTO Video Maker - TikTok Content Posting API Publisher (Phase 8A)

Thin, testable wrapper around the OFFICIAL TikTok APIs only:

- OAuth 2.0 authorization-code flow with PKCE for desktop apps
  (https://www.tiktok.com/v2/auth/authorize/ + /v2/oauth/token/)
- Creator capability query (/v2/post/publish/creator_info/query/)
- Direct Post init for local files (FILE_UPLOAD) via
  /v2/post/publish/video/init/  -> durable publish_id + upload_url
- Chunked video upload to the returned upload_url
- Post status fetch (/v2/post/publish/status/fetch/) for asynchronous
  processing states and the final public post identity

No browser automation, scraping, cookie access, or private APIs are used or
permitted anywhere in this module.

Secrets are never hardcoded or logged: client key/secret come from
environment configuration; tokens are persisted only in the git-ignored
credentials/ directory.
"""

import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urlparse

import requests

from app.config import CREDENTIALS_DIR
from app.publishing.models import TikTokAuthError

logger = logging.getLogger(__name__)

# Official endpoints (developers.tiktok.com)
TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
TIKTOK_CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
TIKTOK_DIRECT_POST_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TIKTOK_STATUS_FETCH_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

# Step 5F diagnostic (explicitly opt-in): when this environment variable is set
# to a truthy value ("1"/"true"/"yes"/"on"), Direct Post initialization declares
# a SINGLE chunk whose size equals the exact video file size instead of the
# normal 10 MB multi-chunk plan. Default behavior is unchanged when unset.
TIKTOK_SINGLE_CHUNK_INIT_ENV = "TIKTOK_SINGLE_CHUNK_INIT"


def _resolve_chunk_params(size: int, chunk_size: int) -> tuple:
    """Return the effective (chunk_size, total_chunk_count) for a video.

    TikTok FILE_UPLOAD contract (media transfer guide; verified against the
    'invalid_params' field reports that Phase 11C Batch 1 hardening fixes):

    - ``total_chunk_count`` = ``floor(video_size / chunk_size)``, minimum 1;
    - the FINAL chunk absorbs the remainder bytes (it may exceed
      ``chunk_size``, up to 128 MB) — there is no extra short chunk;
    - when the plan is a single chunk, ``chunk_size`` MUST equal
      ``video_size`` (declaring a 10 MB chunk for a smaller video is what
      produced "The chunk size is invalid");
    - chunks are otherwise 5-64 MB; the 10 MB default satisfies this.

    Diagnostic single-chunk mode (env-gated, Step 5F) is unchanged: one
    declared chunk covering the entire file.
    """
    if os.environ.get(TIKTOK_SINGLE_CHUNK_INIT_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return size, 1
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if size <= 0:
        raise ValueError("video file is empty; nothing can be uploaded")
    total_chunks = max(1, size // chunk_size)
    if total_chunks == 1:
        # Single-chunk plan: the declared chunk size IS the video size.
        return size, 1
    return chunk_size, total_chunks

# Scopes required by this publishing path (official scope names).
TIKTOK_SCOPES = ["user.info.basic", "video.publish"]

DEFAULT_TIKTOK_TOKEN_FILE = "tiktok_token.json"
# Must exactly match the loopback redirect registered in the TikTok developer
# portal for "Auto Video Maker Publisher". TIKTOK_REDIRECT_URI (.env) overrides.
DEFAULT_TIKTOK_REDIRECT_URI = "http://localhost:3455/callback/"

# Official rate limit: each user access_token is limited to 6 init requests
# per minute. We stay well below it by design (one submission per project).
INIT_RATE_LIMIT_PER_MINUTE = 6


class TikTokPublisher:
    """
    TikTok Content Posting API publisher.

    A fully-injected instance may be provided for testing; otherwise HTTP is
    performed against the official endpoints after OAuth authorization.
    """

    def __init__(
        self,
        client_key: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        token_file: Optional[str] = None,
        session: Optional[Any] = None,
    ):
        self.client_key = client_key
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri or DEFAULT_TIKTOK_REDIRECT_URI
        self.token_file = token_file
        self._session = session if session is not None else requests.Session()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "TikTokPublisher":
        """Build a publisher from environment configuration (.env)."""
        return cls(
            client_key=os.getenv("TIKTOK_CLIENT_KEY"),
            client_secret=os.getenv("TIKTOK_CLIENT_SECRET"),
            redirect_uri=os.getenv("TIKTOK_REDIRECT_URI", DEFAULT_TIKTOK_REDIRECT_URI),
            token_file=os.getenv("TIKTOK_TOKEN_FILE", DEFAULT_TIKTOK_TOKEN_FILE),
        )

    @property
    def token_path(self) -> Path:
        raw = Path(self.token_file or DEFAULT_TIKTOK_TOKEN_FILE)
        if not raw.is_absolute():
            raw = CREDENTIALS_DIR / raw
        return raw

    def _require_credentials(self) -> None:
        if not self.client_key or not self.client_secret:
            raise TikTokAuthError(
                "TikTok credentials missing: set TIKTOK_CLIENT_KEY and "
                "TIKTOK_CLIENT_SECRET in .env (create a developer app at "
                "https://developers.tiktok.com first)."
            )

    # ------------------------------------------------------------------
    # Token persistence (never log token values)
    # ------------------------------------------------------------------

    def _load_token(self) -> Optional[Dict[str, Any]]:
        path = self.token_path
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not load persisted TikTok token (%s)", exc)
            return None
        if not data.get("access_token"):
            return None
        return data

    def _save_token(self, payload: Dict[str, Any]) -> None:
        path = self.token_path
        path.parent.mkdir(parents=True, exist_ok=True)
        record = dict(payload)
        record["saved_at"] = time.time()
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        logger.info("Persisted TikTok OAuth token to %s", path)

    @staticmethod
    def _token_expired(token: Dict[str, Any]) -> bool:
        expires_at = token.get("expires_at")
        if not isinstance(expires_at, (int, float)):
            return True
        # Refresh 5 minutes early to avoid mid-operation expiry.
        return time.time() >= float(expires_at) - 300

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    @staticmethod
    def _code_challenge(verifier: str) -> str:
        """
        TikTok-documented PKCE S256 variant for desktop Login Kit:
        the challenge is the HEX encoding of SHA256(code_verifier)
        (Login Kit desktop docs; differs from RFC-7636 base64url).
        """
        return hashlib.sha256(verifier.encode()).hexdigest()

    @staticmethod
    def _safe_exchange_diagnostics(payload: Any) -> str:
        """
        Format TikTok's SAFE diagnostic fields from a failed token-exchange
        response (error / error_description / log_id). These fields never
        contain credentials; tokens are only present in successful responses,
        which never reach this path.
        """
        if not isinstance(payload, dict):
            return ""
        parts = [
            f"{key}={payload[key]}"
            for key in ("error", "error_description", "log_id")
            if payload.get(key)
        ]
        return f" ({'; '.join(parts)})" if parts else ""

    def authenticate(self) -> Dict[str, Any]:
        """
        Return a valid access-token record, refreshing when possible and
        otherwise running the official PKCE authorization flow (opens the
        user's browser for consent). Raises TikTokAuthError truthfully on any
        failure — never fakes success.
        """
        self._require_credentials()

        token = self._load_token()
        if token and not self._token_expired(token):
            return token

        if token and token.get("refresh_token"):
            refreshed = self._refresh_token(token["refresh_token"])
            if refreshed is not None:
                return refreshed

        return self._run_authorization_flow()

    def _refresh_token(self, refresh_token: str) -> Optional[Dict[str, Any]]:
        try:
            response = self._session.post(
                TIKTOK_TOKEN_URL,
                data={
                    "client_key": self.client_key,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
        except Exception as exc:
            logger.warning("TikTok token refresh request failed: %s", exc)
            return None
        if response.status_code != 200:
            logger.warning(
                "TikTok token refresh rejected (HTTP %s); re-authorization required",
                response.status_code,
            )
            return None
        payload = response.json()
        if "access_token" not in payload:
            logger.warning("TikTok token refresh returned no access_token")
            return None
        payload["expires_at"] = time.time() + int(payload.get("expires_in", 86400))
        payload.setdefault("refresh_token", refresh_token)
        self._save_token(payload)
        return payload

    def _run_authorization_flow(self) -> Dict[str, Any]:
        """Official authorization-code + PKCE flow via a loopback redirect."""
        import threading
        import webbrowser
        from http.server import BaseHTTPRequestHandler, HTTPServer

        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        # TikTok-documented S256 variant: hex encoding of SHA256(verifier)
        # (NOT the RFC-7636 base64url form; see Login Kit desktop docs).
        challenge = self._code_challenge(verifier)

        auth_params = {
            "client_key": self.client_key,
            "scope": ",".join(TIKTOK_SCOPES),
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        auth_url = f"{TIKTOK_AUTH_URL}?{urlencode(auth_params)}"

        parsed_redirect = urlparse(self.redirect_uri)
        port = parsed_redirect.port or 80
        captured: Dict[str, str] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - stdlib API
                from urllib.parse import parse_qs, urlparse as _urlparse

                query = parse_qs(_urlparse(self.path).query)
                captured.update({k: v[0] for k, v in query.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h3>TikTok authorization received."
                    b"</h3>You can close this window.</body></html>"
                )

            def log_message(self, *args):  # silence default logging
                pass

        server = HTTPServer((parsed_redirect.hostname or "localhost", port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            print("Opening browser for TikTok authorization:")
            print(auth_url)
            webbrowser.open(auth_url)
            deadline = time.time() + 300
            while "code" not in captured and "error" not in captured:
                if time.time() > deadline:
                    raise TikTokAuthError(
                        "TikTok authorization timed out waiting for the "
                        "loopback redirect"
                    )
                time.sleep(0.2)
        finally:
            server.shutdown()
            server.server_close()

        if captured.get("state") != state:
            raise TikTokAuthError("TikTok authorization state mismatch (possible CSRF)")
        if "error" in captured:
            raise TikTokAuthError(
                f"TikTok authorization failed: {captured.get('error')}: "
                f"{captured.get('error_description', '')}"
            )
        code = captured.get("code")
        if not code:
            raise TikTokAuthError("TikTok authorization returned no code")

        response = self._session.post(
            TIKTOK_TOKEN_URL,
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if response.status_code != 200:
            try:
                payload = response.json()
            except Exception:
                payload = None
            raise TikTokAuthError(
                f"TikTok token exchange failed (HTTP {response.status_code})"
                + self._safe_exchange_diagnostics(payload)
            )
        payload = response.json()
        if "access_token" not in payload:
            raise TikTokAuthError(
                f"TikTok token exchange returned no access_token: "
                f"{self._safe_exchange_diagnostics(payload) or payload.get('error', 'unknown error')}"
            )
        payload["expires_at"] = time.time() + int(payload.get("expires_in", 86400))
        self._save_token(payload)
        return payload

    def validate_stored_token(self) -> Optional[Dict[str, Any]]:
        """
        Return a valid access-token record using ONLY persisted storage and
        the refresh grant — never launches the interactive authorization
        flow. Returns None when no usable stored credential exists.

        Used by connection-status tooling so a status check can never open a
        browser or otherwise surprise the operator.
        """
        token = self._load_token()
        if not token:
            return None
        if self._token_expired(token):
            refresh_token = token.get("refresh_token")
            if not refresh_token:
                return None
            refreshed = self._refresh_token(refresh_token)
            if refreshed is None:
                return None
            token = refreshed
        return token

    def _auth_headers(self) -> Dict[str, str]:
        token = self.authenticate()
        return {"Authorization": f"Bearer {token['access_token']}"}

    # ------------------------------------------------------------------
    # Account identity & creator capabilities
    # ------------------------------------------------------------------

    def get_user(self) -> Optional[Dict[str, Any]]:
        """Identify the authorized TikTok account (official user info API)."""
        fields = "open_id,union_id,avatar_url,display_name"
        response = self._session.get(
            TIKTOK_USER_INFO_URL,
            params={"fields": fields},
            headers=self._auth_headers(),
            timeout=30,
        )
        if response.status_code != 200:
            raise TikTokAuthError(
                f"TikTok user info failed (HTTP {response.status_code}): "
                f"{response.text[:200]}"
            )
        data = response.json().get("data", {}).get("user")
        return data or None

    def get_creator_info(self) -> Optional[Dict[str, Any]]:
        """Query creator capabilities (privacy options, max duration)."""
        response = self._session.post(
            TIKTOK_CREATOR_INFO_URL,
            json={},
            headers={**self._auth_headers(), "Content-Type": "application/json; charset=UTF-8"},
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"TikTok creator_info query failed (HTTP {response.status_code}): "
                f"{response.text[:200]}"
            )
        body = response.json()
        if body.get("error", {}).get("code") not in (None, "", "ok"):
            raise RuntimeError(
                f"TikTok creator_info query error: {body['error'].get('code')}: "
                f"{body['error'].get('message', '')}"
            )
        return body.get("data")

    # ------------------------------------------------------------------
    # Direct Post (FILE_UPLOAD)
    # ------------------------------------------------------------------

    def init_direct_post(
        self,
        video_path: str,
        title: str,
        privacy_level: str,
        disable_comment: bool = False,
        disable_duet: bool = False,
        disable_stitch: bool = False,
        video_cover_timestamp_ms: Optional[int] = None,
        chunk_size: int = 10_000_000,
    ) -> Dict[str, Any]:
        """
        Initialize a direct-post submission for a LOCAL file (FILE_UPLOAD).

        Returns {"publish_id": ..., "upload_url": ...}. The publish_id is a
        durable submission identity that must be persisted immediately so an
        interrupted upload can be reconciled instead of re-submitted.
        """
        size = os.path.getsize(video_path)
        chunk_size, total_chunks = _resolve_chunk_params(size, chunk_size)

        post_info: Dict[str, Any] = {
            "title": title,
            "privacy_level": privacy_level,
            "disable_duet": bool(disable_duet),
            "disable_comment": bool(disable_comment),
            "disable_stitch": bool(disable_stitch),
        }
        if video_cover_timestamp_ms is not None:
            post_info["video_cover_timestamp_ms"] = int(video_cover_timestamp_ms)

        body = {
            "post_info": post_info,
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunks,
            },
        }
        response = self._session.post(
            TIKTOK_DIRECT_POST_INIT_URL,
            json=body,
            headers={
                **self._auth_headers(),
                "Content-Type": "application/json; charset=UTF-8",
            },
            timeout=60,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"TikTok direct-post init failed (HTTP {response.status_code}): "
                f"{response.text[:300]}"
            )
        payload = response.json()
        error = payload.get("error", {})
        if error.get("code") not in (None, "", "ok"):
            raise RuntimeError(
                f"TikTok direct-post init error: {error.get('code')}: "
                f"{error.get('message', '')}"
            )
        data = payload.get("data", {})
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")
        if not publish_id or not upload_url:
            raise RuntimeError(
                f"TikTok direct-post init returned incomplete data: "
                f"publish_id={'present' if publish_id else 'missing'}, "
                f"upload_url={'present' if upload_url else 'missing'}"
            )
        return {
            "publish_id": publish_id,
            "upload_url": upload_url,
            "chunk_size": chunk_size,
        }

    def upload_video_chunks(
        self,
        upload_url: str,
        video_path: str,
        chunk_size: Optional[int] = None,
    ) -> None:
        """Upload the local file to the short-lived upload_url in chunks.

        Sends EXACTLY ``total_chunk_count`` PUT requests — the same plan
        declared at init (including the opt-in single-chunk diagnostic
        mode). The FINAL chunk absorbs the remainder bytes, matching
        TikTok's last-chunk-absorbs-remainder model; sending an extra short
        chunk (the old ceil behavior) desynchronizes the upload from the
        declaration. Coverage is verified truthfully at the end.
        """
        size = os.path.getsize(video_path)
        chunk_size, total_chunks = _resolve_chunk_params(
            size, chunk_size if chunk_size else 10_000_000
        )
        offset = 0
        with open(video_path, "rb") as handle:
            for index in range(total_chunks):
                if index == total_chunks - 1:
                    chunk = handle.read()  # final chunk absorbs the remainder
                else:
                    chunk = handle.read(chunk_size)
                if not chunk:
                    raise RuntimeError(
                        f"TikTok chunk upload incomplete: file shrank or read "
                        f"failed at chunk {index + 1}/{total_chunks} "
                        f"(offset {offset}, declared size {size})"
                    )
                end = offset + len(chunk) - 1
                headers = {
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{size}",
                }
                response = self._session.put(upload_url, data=chunk, headers=headers, timeout=300)
                if response.status_code not in (200, 201, 206):
                    raise RuntimeError(
                        f"TikTok chunk upload failed at offset {offset} "
                        f"(HTTP {response.status_code}): {response.text[:200]}"
                    )
                offset += len(chunk)
        if offset != size:
            raise RuntimeError(
                f"TikTok chunk upload incomplete: uploaded {offset} of "
                f"{size} bytes across {total_chunks} chunks"
            )

    def submit_direct_post(self, video_path: str, metadata: Any) -> str:
        """
        Full direct-post submission: init + chunked upload.

        Returns the durable publish_id. Callers must persist it immediately;
        any exception after init succeeded leaves a reconcilable publish_id
        behind rather than an unknown outcome.
        """
        result = self.init_direct_post(
            video_path=video_path,
            title=metadata.title,
            privacy_level=metadata.privacy_level,
            disable_comment=metadata.disable_comment,
            disable_duet=metadata.disable_duet,
            disable_stitch=metadata.disable_stitch,
            video_cover_timestamp_ms=metadata.video_cover_timestamp_ms,
        )
        # Persist-the-id responsibility belongs to the caller between these
        # two calls; submit_direct_post performs both back-to-back for
        # operator convenience.
        self.upload_video_chunks(result["upload_url"], video_path)
        return result["publish_id"]

    # ------------------------------------------------------------------
    # Status verification
    # ------------------------------------------------------------------

    def fetch_status(self, publish_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch the authoritative posting status for a durable publish_id.

        Returns the raw ``data`` object (status plus, when complete, the
        final public post id), or None if TikTok reports no such publish_id.
        """
        response = self._session.post(
            TIKTOK_STATUS_FETCH_URL,
            json={"publish_id": publish_id},
            headers={
                **self._auth_headers(),
                "Content-Type": "application/json; charset=UTF-8",
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"TikTok status fetch failed (HTTP {response.status_code}): "
                f"{response.text[:200]}"
            )
        body = response.json()
        error = body.get("error", {})
        if error.get("code") not in (None, "", "ok"):
            # Unrecognized publish_id or other API-level failure.
            logger.warning(
                "TikTok status fetch error for %s: %s", publish_id, error.get("code")
            )
            return None
        return body.get("data")
