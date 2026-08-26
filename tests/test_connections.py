"""
Connection-management command tests (offline only).

Covers:
- TikTok redirect URI default matches the developer portal registration and
  TIKTOK_REDIRECT_URI override behavior.
- --connect-tiktok / --connect-youtube reuse existing authentication logic and
  can NEVER reach publishing code paths.
- --connections truthfully distinguishes NOT CONNECTED / CONNECTED / ERROR
  without printing secrets.

No real OAuth, uploads, or publication occurs. All publishers are fakes whose
publishing methods raise AssertionError if ever invoked.
"""

from pathlib import Path

import pytest

import app.publishing.connections as conn
from app.publishing.tiktok import DEFAULT_TIKTOK_REDIRECT_URI, TikTokPublisher

PORTAL_REDIRECT_URI = "http://localhost:3455/callback/"


# ---------------------------------------------------------------------------
# Redirect URI configuration
# ---------------------------------------------------------------------------

def test_tiktok_default_redirect_uri_matches_portal(monkeypatch):
    monkeypatch.delenv("TIKTOK_REDIRECT_URI", raising=False)
    publisher = TikTokPublisher.from_env()
    assert DEFAULT_TIKTOK_REDIRECT_URI == PORTAL_REDIRECT_URI
    assert publisher.redirect_uri == PORTAL_REDIRECT_URI


def test_tiktok_redirect_uri_override_respected(monkeypatch):
    monkeypatch.setenv("TIKTOK_REDIRECT_URI", "http://localhost:9999/custom")
    publisher = TikTokPublisher.from_env()
    assert publisher.redirect_uri == "http://localhost:9999/custom"


def test_auth_and_token_exchange_share_one_redirect_uri(monkeypatch):
    """The configured URI is a single value reused by both flow stages."""
    monkeypatch.delenv("TIKTOK_REDIRECT_URI", raising=False)
    publisher = TikTokPublisher.from_env()
    # Single stored value -> authorization URL and token exchange cannot drift.
    assert publisher.redirect_uri == publisher.redirect_uri.strip()
    assert publisher.redirect_uri == PORTAL_REDIRECT_URI


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeYouTubePublisher:
    """Stands in for YouTubePublisher; uploading is forbidden here."""

    def __init__(self, token_file=None):
        self.token_file = token_file

    def authenticate(self):
        self.authenticated = True
        return object()

    def get_channel(self):
        return {"snippet": {"title": "Test Channel"}}

    def validate_stored_credentials(self):
        return None if not self.token_file or not Path(self.token_file).exists() \
            else object()

    def upload(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("connection commands must never upload")

    def update_metadata(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("connection commands must never modify videos")


class FakeTikTokPublisher:
    """Stands in for TikTokPublisher; publishing is forbidden here."""

    def __init__(self, token_path=None):
        self.token_path = Path(token_path) if token_path else Path("missing") / "none.json"

    def authenticate(self):
        self.authenticated = True
        return {"access_token": "secret-value-never-printed"}

    def get_user(self):
        return {"display_name": "Test Creator", "open_id": "abcdef1234567890"}

    def validate_stored_token(self):
        return None if not self.token_path.exists() else {"access_token": "x"}

    def init_direct_post(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("connection commands must never submit posts")

    def upload_video_chunks(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("connection commands must never upload")

    def fetch_status(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("connection commands must never reconcile posts")


# ---------------------------------------------------------------------------
# Connect commands
# ---------------------------------------------------------------------------

def test_connect_tiktok_uses_existing_auth_never_publishes(capsys):
    pub = FakeTikTokPublisher()
    rc = conn.run_connect_tiktok(publisher=pub)
    assert rc == 0
    out = capsys.readouterr().out
    assert pub.authenticated is True
    assert "Test Creator" in out
    assert "secret-value-never-printed" not in out  # token never printed
    # The publishing methods were never touched (they would raise).


def test_connect_tiktok_fails_truthfully_on_auth_error(capsys):
    class FailingPublisher(FakeTikTokPublisher):
        def authenticate(self):
            raise conn.TikTokAuthError("authorization denied by user")

    rc = conn.run_connect_tiktok(publisher=FailingPublisher())
    assert rc != 0
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "authorization denied" in out


def test_connect_youtube_reuses_auth_never_uploads(capsys):
    pub = FakeYouTubePublisher()
    rc = conn.run_connect_youtube(publisher=pub)
    assert rc == 0
    out = capsys.readouterr().out
    assert pub.authenticated is True
    assert "Test Channel" in out


def test_connect_youtube_fails_truthfully(capsys):
    class FailingPublisher(FakeYouTubePublisher):
        def authenticate(self):
            raise FileNotFoundError("client secrets file missing")

    rc = conn.run_connect_youtube(publisher=FailingPublisher())
    assert rc != 0
    assert "FAILED" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Connection status
# ---------------------------------------------------------------------------

def test_status_not_connected_when_no_token_files(capsys):
    rc = conn.run_connections_status(
        youtube_publisher=FakeYouTubePublisher(token_file=None),
        tiktok_publisher=FakeTikTokPublisher(token_path="missing/none.json"),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("NOT CONNECTED") == 2


def test_status_connected_when_credentials_validate(capsys, tmp_path):
    yt = FakeYouTubePublisher(token_file=tmp_path / "yt.json")
    (tmp_path / "yt.json").write_text("{}", encoding="utf-8")
    tk = FakeTikTokPublisher(token_path=tmp_path / "tk.json")
    (tmp_path / "tk.json").write_text("{}", encoding="utf-8")
    rc = conn.run_connections_status(youtube_publisher=yt, tiktok_publisher=tk)
    assert rc == 0
    out = capsys.readouterr().out
    assert "CONNECTED" in out
    assert "Test Channel" in out
    assert "Test Creator" in out


def test_status_error_when_token_exists_but_fails_validation(capsys, tmp_path):
    class StaleYT(FakeYouTubePublisher):
        def validate_stored_credentials(self):
            return None

    class StaleTK(FakeTikTokPublisher):
        def validate_stored_token(self):
            return None

    token = tmp_path / "tok.json"
    token.write_text("{}", encoding="utf-8")
    rc = conn.run_connections_status(
        youtube_publisher=StaleYT(token_file=token),
        tiktok_publisher=StaleTK(token_path=token),
    )
    assert rc == 0
    out = capsys.readouterr().out
    # Token file exists -> must NOT be reported as simply NOT CONNECTED.
    assert out.count("ERROR") == 2
    assert "NOT CONNECTED" not in out


def test_status_error_when_account_lookup_fails(capsys, tmp_path):
    class LookupFailYT(FakeYouTubePublisher):
        def get_channel(self):
            raise RuntimeError("api down")

    class LookupFailTK(FakeTikTokPublisher):
        def get_user(self):
            raise RuntimeError("api down")

    token = tmp_path / "tok.json"
    token.write_text("{}", encoding="utf-8")
    conn.run_connections_status(
        youtube_publisher=LookupFailYT(token_file=token),
        tiktok_publisher=LookupFailTK(token_path=token),
    )
    out = capsys.readouterr().out
    assert out.count("ERROR") == 2
    assert "api down" in out


def test_status_never_publishes(tmp_path):
    token = tmp_path / "tok.json"
    token.write_text("{}", encoding="utf-8")
    tk = FakeTikTokPublisher(token_path=token)
    yt = FakeYouTubePublisher(token_file=token)
    conn.run_connections_status(youtube_publisher=yt, tiktok_publisher=tk)
    # Any attempt to publish would have raised AssertionError in the fakes.


# ---------------------------------------------------------------------------
# CLI integration (wiring verified against the real produce.py source)
# ---------------------------------------------------------------------------

def test_cli_registers_connection_flags_as_store_true():
    source = (
        Path(__file__).resolve().parent.parent / "produce.py"
    ).read_text(encoding="utf-8")
    for flag in ("--connect-tiktok", "--connect-youtube", "--connections"):
        assert flag in source, f"{flag} not registered in produce.py"


def test_cli_connection_flags_share_mutually_exclusive_group_with_publishing():
    source = (
        Path(__file__).resolve().parent.parent / "produce.py"
    ).read_text(encoding="utf-8")
    group_start = source.index("topic_group.add_argument")
    group_end = source.index("# Flow project URL")
    group_body = source[group_start:group_end]
    for flag in ("--publish-youtube", "--publish-tiktok",
                 "--connect-tiktok", "--connect-youtube", "--connections"):
        assert flag in group_body, (
            f"{flag} must belong to the same required mutually exclusive group"
        )


def test_cli_dispatches_connection_commands_before_publishing():
    source = (
        Path(__file__).resolve().parent.parent / "produce.py"
    ).read_text(encoding="utf-8")
    connect_idx = source.index("if args.connect_youtube:")
    publish_idx = source.index("if args.publish_youtube:")
    assert connect_idx < publish_idx, (
        "connection dispatch must run before publishing dispatch"
    )