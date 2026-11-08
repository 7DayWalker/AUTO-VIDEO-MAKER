"""
AUTO Video Maker - Platform connection management (CLI support layer)

Small entry points that wrap the EXISTING OAuth implementations:

- ``run_connect_youtube``  reuses YouTubePublisher.authenticate()/get_channel()
- ``run_connect_tiktok``   reuses TikTokPublisher.authenticate()/get_user()
- ``run_connections_status`` validates stored credentials WITHOUT launching
  any interactive flow (publishers' validate_stored_* helpers)

Hard guarantees:
- These functions never prepare, submit, or verify a publication.
- They never touch project manifests or production artifacts.
- They never print tokens, client secrets, or credential file contents.
"""

import logging
import threading
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional

from app.publishing.tiktok import TikTokAuthError, TikTokPublisher
from app.publishing.youtube import YouTubePublisher
from app.content.exceptions import safe_provider_text

logger = logging.getLogger(__name__)
_CREDENTIAL_LOCKS = {"youtube": threading.RLock(), "tiktok": threading.RLock()}


def credential_lock(platform):
    """Serialize refresh/consent for one account; never lock both platforms."""
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            with _CREDENTIAL_LOCKS[platform]:
                return function(*args, **kwargs)
        return run
    return decorate


def _mask_account_id(value: Optional[str]) -> str:
    """Show only a short non-identifying suffix of a provider account id."""
    if not value:
        return "unknown"
    return "…" + value[-6:] if len(value) > 6 else value


# ---------------------------------------------------------------------------
# YouTube connect (reuses the proven installed-app OAuth flow)
# ---------------------------------------------------------------------------

@credential_lock("youtube")
def run_connect_youtube(publisher: Optional[YouTubePublisher] = None, *, force=False) -> int:
    """
    Connect/confirm the YouTube channel via the existing OAuth implementation.

    Opens the interactive consent flow ONLY when no valid/refreshable token is
    persisted (that decision belongs to ``authenticate``). Never uploads.
    """
    pub = publisher or YouTubePublisher.from_env()
    if force:
        pub.force_reauthorize = True
    print("=" * 60)
    print("AUTO Video Maker - Connect YouTube")
    print("=" * 60)
    try:
        pub.authenticate()  # existing flow; browser only when required
        channel = pub.get_channel()
    except Exception as exc:
        print(f"YOUTUBE CONNECTION FAILED (truthfully): {safe_provider_text(exc)}")
        print("Nothing was uploaded and no project state was changed.")
        return 3
    if not channel:
        print("YOUTUBE CONNECTION FAILED: channel identity unavailable.")
        return 3
    snippet = channel.get("snippet", {}) if isinstance(channel, dict) else {}
    title = snippet.get("title") or "(unnamed channel)"
    print(f"Connected YouTube channel : {title}")
    print()
    print("Connection confirmed. No video was uploaded.")
    return 0


# ---------------------------------------------------------------------------
# TikTok connect (reuses the official PKCE Login Kit flow)
# ---------------------------------------------------------------------------

@credential_lock("tiktok")
def run_connect_tiktok(publisher: Optional[TikTokPublisher] = None, *, force=False) -> int:
    """
    Connect/confirm the TikTok account via the existing official OAuth
    (authorization-code + PKCE) implementation. Never submits a post.
    """
    pub = publisher or TikTokPublisher.from_env()
    if force:
        pub.force_reauthorize = True
    print("=" * 60)
    print("AUTO Video Maker - Connect TikTok")
    print("=" * 60)
    try:
        # Existing flow: reuse valid token, refresh when possible, otherwise
        # open the browser for official Login Kit authorization.
        pub.authenticate()
        user = pub.get_user()
    except TikTokAuthError as exc:
        print(f"TIKTOK CONNECTION FAILED (truthfully): {safe_provider_text(exc)}")
        print("No connection state was faked and nothing was submitted.")
        return 3
    except Exception as exc:
        print(f"TIKTOK CONNECTION FAILED: {safe_provider_text(exc)}")
        return 3
    if not user:
        print("TIKTOK CONNECTION FAILED: authorized account identity unavailable.")
        return 3
    display_name = user.get("display_name")
    open_id = user.get("open_id")
    label = display_name or _mask_account_id(open_id)
    print(f"Connected TikTok account  : {label}")
    if display_name and open_id:
        print(f"Account id (masked)       : {_mask_account_id(open_id)}")
    print()
    print("Connection confirmed. No video was published.")
    return 0


# ---------------------------------------------------------------------------
# Connection status (read-only, never interactive, never publishes)
# ---------------------------------------------------------------------------

@credential_lock("youtube")
def _youtube_status(pub: Any) -> Dict[str, Any]:
    pub.allow_interactive = False
    token_file = getattr(pub, "token_file", None)
    if not token_file or not Path(token_file).exists():
        return {"status": "NOT CONNECTED"}
    try:
        creds = pub.validate_stored_credentials()
    except ImportError:
        return {
            "status": "ERROR",
            "reason_code": "DEPENDENCY_MISSING",
            "reconnect_required": True,
            "detail": (
                "Google API client libraries are not installed "
                "(run: pip install -r requirements.txt)"
            ),
        }
    if creds is None:
        return {
            "status": "ERROR",
            "reason_code": getattr(pub, "credential_reason", None) or "CREDENTIALS_UNUSABLE",
            "reconnect_required": True,
            "detail": (
                "stored token exists but is invalid/expired and could not be "
                "refreshed; reconnect with --connect-youtube"
            ),
        }
    try:
        channel = pub.get_channel()
    except Exception as exc:
        return {"status": "ERROR", "reason_code": "ACCOUNT_UNVERIFIED", "detail": f"channel lookup failed: {safe_provider_text(exc)}"}
    if not channel:
        return {"status": "ERROR", "detail": "channel identity unavailable"}
    title = (channel.get("snippet", {}) or {}).get("title") or "(unnamed channel)"
    return {"status": "CONNECTED", "account": title, "account_id": channel.get("id"), "reconnect_required": False}


@credential_lock("tiktok")
def _tiktok_status(pub: Any) -> Dict[str, Any]:
    pub.allow_interactive = False
    if not pub.token_path.exists():
        return {"status": "NOT CONNECTED"}
    token = pub.validate_stored_token()
    if token is None:
        return {
            "status": "ERROR",
            "reason_code": getattr(pub, "credential_reason", None) or "CREDENTIALS_UNUSABLE",
            "reconnect_required": True,
            "detail": (
                "stored token exists but is expired/unrefreshable; "
                "reconnect with --connect-tiktok"
            ),
        }
    try:
        user = pub.get_user()
    except Exception as exc:
        return {"status": "ERROR", "reason_code": "ACCOUNT_UNVERIFIED", "detail": f"user info failed: {safe_provider_text(exc)}"}
    if not user:
        return {"status": "ERROR", "detail": "account identity unavailable"}
    label = user.get("display_name") or _mask_account_id(user.get("open_id"))
    return {"status": "CONNECTED", "account": label, "account_id": user.get("open_id"), "reconnect_required": False}


def run_connections_status(
    youtube_publisher: Optional[Any] = None,
    tiktok_publisher: Optional[Any] = None,
) -> int:
    """
    Truthful, read-only summary of platform connections. Never opens a
    browser, uploads, or publishes. Secrets are never printed.
    """
    yt_pub = youtube_publisher or YouTubePublisher.from_env()
    tk_pub = tiktok_publisher or TikTokPublisher.from_env()

    print("=" * 60)
    print("PLATFORM CONNECTIONS")
    print("=" * 60)

    yt = _youtube_status(yt_pub)
    print()
    print("YouTube")
    print(f"  Status : {yt['status']}")
    if yt.get("account"):
        print(f"  Account: {yt['account']}")
    if yt.get("detail"):
        print(f"  Detail : {yt['detail']}")

    tk = _tiktok_status(tk_pub)
    print()
    print("TikTok")
    print(f"  Status : {tk['status']}")
    if tk.get("account"):
        print(f"  Account: {tk['account']}")
    if tk.get("detail"):
        print(f"  Detail : {tk['detail']}")

    print()
    return 0