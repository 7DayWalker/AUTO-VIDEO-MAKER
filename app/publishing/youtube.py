"""
AUTO Video Maker - YouTube Data API v3 Publisher

Thin, testable wrapper around the official YouTube Data API for:
- OAuth 2.0 user-channel authorization (installed-app flow)
- Resumable video upload
- Video lookup by ID (verification / reconciliation)
- Channel identification

Google API libraries are imported lazily inside methods so the module (and
everything that imports it) remains importable in offline test environments.

Secrets are never hardcoded: client secrets and the persisted OAuth token are
located via environment configuration (see ``from_env``). Tokens are stored
only in the git-ignored ``credentials/`` directory.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import CREDENTIALS_DIR

logger = logging.getLogger(__name__)

# upload scope: videos.insert
# readonly scope: videos.list / channels.list used for verification
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

DEFAULT_CLIENT_SECRETS_FILE = "youtube_client_secret.json"
DEFAULT_TOKEN_FILE = "youtube_token.json"

# Phase 7B: the conservative default is ``unlisted``. The environment
# variable YOUTUBE_PRIVACY_STATUS (see .env.example) overrides this; nothing
# is ever published publicly unless explicitly configured.
DEFAULT_PRIVACY_STATUS = "unlisted"

# YouTube hard limits
MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 5000


class YouTubePublisher:
    """
    YouTube Data API publisher.

    A pre-built ``youtube_client`` may be injected for testing; otherwise the
    client is constructed after OAuth authorization.
    """

    def __init__(
        self,
        client_secrets_file: Optional[str] = None,
        token_file: Optional[str] = None,
        youtube_client: Any = None,
        *,
        allow_interactive: bool = True,
    ):
        self.client_secrets_file = client_secrets_file
        self.token_file = token_file
        self._youtube = youtube_client
        self.allow_interactive = allow_interactive

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, *, allow_interactive: bool = True) -> "YouTubePublisher":
        """Build a publisher from environment configuration.

        Required environment variables (see .env.example):
            YOUTUBE_CLIENT_SECRETS_FILE  path to the OAuth client secret JSON
            YOUTUBE_TOKEN_FILE           path where the authorized token is persisted
            YOUTUBE_PRIVACY_STATUS       default privacy for uploads (public/unlisted/private)
        """
        secrets = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", DEFAULT_CLIENT_SECRETS_FILE)
        token = os.getenv("YOUTUBE_TOKEN_FILE", DEFAULT_TOKEN_FILE)
        secrets_path = Path(secrets)
        if not secrets_path.is_absolute():
            secrets_path = CREDENTIALS_DIR / secrets_path
        token_path = Path(token)
        if not token_path.is_absolute():
            token_path = CREDENTIALS_DIR / token_path
        return cls(
            client_secrets_file=str(secrets_path),
            token_file=str(token_path),
            allow_interactive=allow_interactive,
        )

    @staticmethod
    def default_privacy_status() -> str:
        return os.getenv("YOUTUBE_PRIVACY_STATUS", DEFAULT_PRIVACY_STATUS).lower()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> Any:
        """Authorize via OAuth 2.0 and return the YouTube API client.

        Reuses a persisted token when present and valid; otherwise runs the
        installed-app flow, which may open an interactive browser window on
        first use. The refreshed token is saved to ``token_file``.
        """
        if self._youtube is not None:
            return self._youtube

        if not self.allow_interactive:
            # HTTP publish/status may refresh stored credentials, but must
            # never fall through to consent if they expire during a request.
            creds = self.validate_stored_credentials()
            if creds is None:
                raise PermissionError(
                    "No usable stored YouTube credentials; reconnect through "
                    "the explicit platform connection command."
                )
            from googleapiclient.discovery import build

            self._youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
            return self._youtube

        if not self.client_secrets_file or not Path(self.client_secrets_file).exists():
            raise FileNotFoundError(
                "YouTube OAuth client secrets file not found: "
                f"{self.client_secrets_file}. Set YOUTUBE_CLIENT_SECRETS_FILE "
                "to the path of the client secret JSON downloaded from Google Cloud Console."
            )

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds: Optional[Credentials] = None
        token_path = Path(self.token_file)
        if token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)
            except Exception as exc:
                logger.warning("Could not load persisted YouTube token (%s); re-authorizing", exc)
                creds = None

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                logger.warning("YouTube token refresh failed (%s); re-authorizing", exc)
                creds = None

        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                self.client_secrets_file, YOUTUBE_SCOPES
            )
            creds = flow.run_local_server(port=0, prompt="consent")
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")
            logger.info("Persisted YouTube OAuth token to %s", token_path)

        self._youtube = build("youtube", "v3", credentials=creds)
        return self._youtube

    def validate_stored_credentials(self) -> Optional[Any]:
        """
        Load and refresh persisted credentials WITHOUT launching the
        interactive installed-app flow. Returns valid Google credentials or
        None when no usable stored credential exists.

        Used by connection-status tooling so checking status can never open a
        browser; publishing still uses ``authenticate`` unchanged.
        """
        if not self.token_file or not Path(self.token_file).exists():
            return None
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        try:
            creds = Credentials.from_authorized_user_file(
                str(self.token_file), YOUTUBE_SCOPES
            )
        except Exception as exc:
            logger.warning("Could not load persisted YouTube token (%s)", exc)
            return None
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                Path(self.token_file).write_text(creds.to_json(), encoding="utf-8")
                logger.info("Refreshed persisted YouTube OAuth token in place")
            except Exception as exc:
                logger.warning("YouTube token refresh failed (%s)", exc)
                return None
        if not creds.valid:
            return None
        return creds

    # ------------------------------------------------------------------
    # API operations
    # ------------------------------------------------------------------

    def get_channel(self) -> Optional[Dict[str, Any]]:
        """Identify the authenticated channel (channels.list mine=True)."""
        youtube = self.authenticate()
        response = youtube.channels().list(part="snippet,contentDetails", mine=True).execute()
        items = response.get("items", [])
        return items[0] if items else None

    def upload(
        self,
        video_path: str,
        title: str,
        description: str,
        privacy_status: str = DEFAULT_PRIVACY_STATUS,
        tags: Optional[list] = None,
        category_id: Optional[str] = None,
        made_for_kids: bool = False,
        default_language: Optional[str] = None,
    ) -> str:
        """Upload a video file (resumable) and return the YouTube video ID.

        Raises on failure. Callers must treat any exception after the request
        has begun as an unknown-outcome submission (the file may have reached
        YouTube) and must not blindly retry.

        Metadata arguments are defensive slices only; callers are expected to
        pass already-validated :class:`~app.publishing.models.YouTubeMetadata`
        values (validation itself never truncates silently).
        """
        youtube = self.authenticate()

        from googleapiclient.http import MediaFileUpload

        snippet: Dict[str, Any] = {
            "title": title[:MAX_TITLE_LENGTH],
            "description": description[:MAX_DESCRIPTION_LENGTH],
            # Documented default: Education (27).
            "categoryId": category_id or "27",
        }
        if tags:
            snippet["tags"] = [str(tag) for tag in tags]
        if default_language:
            snippet["defaultLanguage"] = default_language
        body = {
            "snippet": snippet,
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": bool(made_for_kids),
            },
        }
        media = MediaFileUpload(
            video_path,
            mimetype="video/mp4",
            chunksize=1024 * 1024,
            resumable=True,
        )
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response: Optional[Dict[str, Any]] = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(
                    "YouTube upload progress: %.1f%%",
                    status.progress() * 100,
                )
        video_id = response.get("id")
        if not video_id:
            raise RuntimeError(f"YouTube upload response missing video id: {response}")
        return video_id

    def update_metadata(
        self,
        video_id: str,
        *,
        title: str,
        description: str,
        privacy_status: str,
        tags: Optional[list] = None,
        category_id: Optional[str] = None,
        made_for_kids: bool = False,
        default_language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update metadata of an EXISTING YouTube video (videos.update).

        This is a distinct operation from ``upload`` and must only ever be
        called with an already-persisted YouTube video ID. It can never create
        a new video or duplicate a publication.
        """
        youtube = self.authenticate()

        snippet: Dict[str, Any] = {
            "title": title[:MAX_TITLE_LENGTH],
            "description": description[:MAX_DESCRIPTION_LENGTH],
            # videos.update requires categoryId whenever snippet is sent.
            "categoryId": category_id or "27",
        }
        if tags:
            snippet["tags"] = [str(tag) for tag in tags]
        if default_language:
            snippet["defaultLanguage"] = default_language
        body = {
            "id": video_id,
            "snippet": snippet,
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": bool(made_for_kids),
            },
        }
        response = (
            youtube.videos()
            .update(part="snippet,status", body=body)
            .execute()
        )
        if response.get("id") != video_id:
            raise RuntimeError(
                f"YouTube metadata update returned unexpected id "
                f"{response.get('id')!r}; expected {video_id!r}"
            )
        return response

    def get_video(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a video by exact ID (videos.list). Returns None if absent."""
        youtube = self.authenticate()
        response = youtube.videos().list(
            part="snippet,status,processingDetails",
            id=video_id,
        ).execute()
        items = response.get("items", [])
        return items[0] if items else None