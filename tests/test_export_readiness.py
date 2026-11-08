"""Explicit OAuth and passive readiness: no uploads or production mutations."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.web import server as web
from app.publishing import connections
from app.publishing.youtube import YouTubePublisher
from tests.test_track10_publishing_parity import runtime, accepted_project, http, request, offline, gate


def test_connection_projection_preserves_reconnect_reason_and_never_interacts(monkeypatch):
    calls = []
    def factory(**kwargs):
        assert kwargs == {"allow_interactive": False}
        return SimpleNamespace(allow_interactive=False)
    monkeypatch.setattr("app.publishing.youtube.YouTubePublisher.from_env", factory)
    monkeypatch.setattr("app.publishing.tiktok.TikTokPublisher.from_env", factory)
    monkeypatch.setattr(connections, "_youtube_status", lambda pub: {
        "status": "ERROR", "reason_code": "INVALID_GRANT", "detail": "invalid_grant; reconnect explicitly",
    })
    monkeypatch.setattr(connections, "_tiktok_status", lambda pub: {"status": "CONNECTED", "account": "Fixture"})
    payload = web.connections_payload()
    assert payload["youtube"]["reconnect_required"] and payload["youtube"]["reason_code"] == "INVALID_GRANT"
    assert "invalid_grant" in payload["youtube"]["detail"]
    assert not payload["tiktok"]["reconnect_required"]


def test_reconnect_is_single_flight_truthful_and_tracks_terminal_result(monkeypatch):
    monkeypatch.setattr(web, "_connect_running", {"platform": None})
    monkeypatch.setattr(web, "_connect_operations", {})
    runners = []
    monkeypatch.setattr(web.threading, "Thread", lambda target, **kw: SimpleNamespace(start=lambda: runners.append(target)))
    connect = Mock(return_value=0)
    monkeypatch.setattr(connections, "run_connect_youtube", connect)
    status, first = web._start_connect("youtube")
    assert status == 202 and first["operation"]["state"] == "running"
    assert web._start_connect("youtube")[1]["operation"]["id"] == first["operation"]["id"]
    assert web._start_connect("tiktok")[0] == 409
    assert len(runners) == 1
    runners[0]()
    assert web._connect_operations["youtube"]["state"] == "completed"
    connect.assert_called_once_with(force=True)


def test_invalid_grant_is_safe_and_actionable(monkeypatch, tmp_path):
    from google.oauth2.credentials import Credentials
    path = tmp_path / "fixture.json"
    path.write_text("{}")
    credentials = SimpleNamespace(expired=True, refresh_token="synthetic", refresh=Mock(side_effect=RuntimeError("invalid_grant synthetic-private")))
    monkeypatch.setattr(Credentials, "from_authorized_user_file", lambda *args: credentials)
    publisher = YouTubePublisher(token_file=str(path), allow_interactive=False)
    assert publisher.validate_stored_credentials() is None
    assert publisher.credential_reason == "INVALID_GRANT"


def test_forced_tiktok_consent_is_used_once_not_again_for_account_lookup(monkeypatch):
    from app.publishing.tiktok import TikTokPublisher
    publisher = TikTokPublisher(client_key="fixture", client_secret="fixture")
    publisher.force_reauthorize = True
    flow = Mock(return_value={"access_token": "synthetic"})
    monkeypatch.setattr(publisher, "_run_authorization_flow", flow)
    monkeypatch.setattr(publisher, "_load_token", lambda: {"access_token": "synthetic", "expires_at": 10**12})
    publisher.authenticate()
    publisher.authenticate()
    assert flow.call_count == 1


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_readiness_inspection_never_reconciles_existing_identity(accepted_project, monkeypatch, platform):
    from tests.test_track10_publishing_parity import write_record
    from app.publishing.models import PublicationState
    write_record(accepted_project.directory, platform, PublicationState.PROCESSING, identity="existing")
    before = (accepted_project.directory / "manifest.json").read_bytes()
    monkeypatch.setattr(web, "_youtube_reconcile", Mock(side_effect=AssertionError("passive read must not reconcile")))
    monkeypatch.setattr(web, "_reconcile", Mock(side_effect=AssertionError("passive read must not reconcile")))
    status, payload = web.publication_readiness_payload("project", platform)
    assert status == 200 and payload["inspection_only"] and payload["can_publish"] is False
    assert (accepted_project.directory / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_readiness_checks_account_metadata_and_master_without_submission(accepted_project, monkeypatch, platform):
    account = {"status": "CONNECTED", "account": "Fixture", "account_id": "account-fixture"}
    monkeypatch.setattr(connections, "_youtube_status", lambda pub: account)
    monkeypatch.setattr(connections, "_tiktok_status", lambda pub: account)
    monkeypatch.setattr(web, "media_path_for", lambda pid: accepted_project.directory / "final.mp4")
    monkeypatch.setattr("app.assembly.ffmpeg.probe_media", lambda path: SimpleNamespace(duration=30))
    before = (accepted_project.directory / "manifest.json").read_bytes()
    status, payload = web.publication_readiness_payload("project", platform)
    assert status == 200 and payload["can_publish"] is True, payload
    assert payload["account_id"] == "account-fixture"
    assert (accepted_project.directory / "manifest.json").read_bytes() == before
    assert "upload" not in accepted_project.yt.calls and "init" not in accepted_project.tk.calls


def test_tiktok_unknown_creator_capabilities_fail_closed(accepted_project, monkeypatch):
    monkeypatch.setattr(connections, "_tiktok_status", lambda pub: {"status": "CONNECTED", "account_id": "fixture"})
    monkeypatch.setattr(accepted_project.tk, "get_creator_info", lambda: {})
    status, payload = web.publication_readiness_payload("project", "tiktok")
    assert status == 200 and payload["can_publish"] is False and payload["reason_code"] == "PREFLIGHT_UNAVAILABLE"
    assert "init" not in accepted_project.tk.calls


def test_tiktok_duration_limit_is_checked_before_upload(accepted_project, monkeypatch):
    monkeypatch.setattr(connections, "_tiktok_status", lambda pub: {"status": "CONNECTED", "account_id": "fixture"})
    monkeypatch.setattr(accepted_project.tk, "get_creator_info", lambda: {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 10})
    monkeypatch.setattr("app.assembly.ffmpeg.probe_media", lambda path: SimpleNamespace(duration=30))
    status, payload = web.publication_readiness_payload("project", "tiktok")
    assert status == 200 and not payload["can_publish"] and "duration" in payload["note"]


def test_readiness_and_connect_operation_are_get_only(http, accepted_project, monkeypatch):
    monkeypatch.setattr(web, "publication_readiness_payload", lambda pid, platform: (200, {"inspection_only": True, "project_id": pid, "platform": platform}))
    assert request(http, "/api/projects/project/publish/youtube/readiness")[1]["inspection_only"]
    assert request(http, "/api/platforms/youtube/connection-operation")[0] == 200


@pytest.mark.parametrize("creator", [{}, {"privacy_level_options": []}])
def test_manual_tiktok_unknown_capabilities_cannot_submit(accepted_project, monkeypatch, creator):
    monkeypatch.setattr(accepted_project.tk, "get_creator_info", lambda: creator)
    status, payload = web.tiktok_publish_now("project", privacy_level="SELF_ONLY")
    assert status == 422 and payload["error"] == "CREATOR_CAPABILITIES_UNAVAILABLE"
    assert "init" not in accepted_project.tk.calls


def test_manual_tiktok_creator_exception_is_not_permission_to_upload(accepted_project, monkeypatch):
    monkeypatch.setattr(accepted_project.tk, "get_creator_info", Mock(side_effect=RuntimeError("fixture creator query failure")))
    status, payload = web.tiktok_publish_now("project")
    assert status == 422 and payload["error"] == "CREATOR_CAPABILITIES_UNAVAILABLE"
    assert "init" not in accepted_project.tk.calls


def test_one_click_publish_checks_the_exact_readiness_master_and_account(accepted_project, monkeypatch):
    from tests.test_track10_publishing_parity import PID, UID
    status, payload = web.unit_publish_payload(PID, UID, {"confirm": True, "platforms": ["youtube"],
        "expected_project_id": "changed-binding", "expected_file_fingerprint": "a" * 64})
    assert status == 409 and payload["error"] == "PROJECT_BINDING_CHANGED"
    monkeypatch.setattr(accepted_project.yt, "get_channel", lambda: {"id": "fixture-account"}, raising=False)
    status, payload = web.unit_publish_payload(PID, UID, {"confirm": True, "platforms": ["youtube"],
        "expected_project_id": "project", "expected_account_id": "fixture-account", "expected_file_fingerprint": "a" * 64})
    assert status == 409 and payload["results"]["youtube"]["result"]["error"] == "MASTER_CHANGED"
    assert "upload" not in accepted_project.yt.calls


def test_readiness_fingerprints_accepted_master_without_mutation(accepted_project, monkeypatch):
    monkeypatch.setattr(connections, "_youtube_status", lambda pub: {"status": "CONNECTED", "account_id": "fixture"})
    before = (accepted_project.directory / "manifest.json").read_bytes()
    status, payload = web.publication_readiness_payload("project", "youtube")
    assert status == 200 and payload["file_fingerprint"] == web._publishing_master_fingerprint("project")
    assert len(payload["file_fingerprint"]) == 64
    assert before == (accepted_project.directory / "manifest.json").read_bytes()