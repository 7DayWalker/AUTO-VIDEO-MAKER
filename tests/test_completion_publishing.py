"""Durable opt-in publishing uses real engines and synthetic platform boundaries."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.publishing import completion
from app.publishing.models import PublicationState
from app.web import server as web
from tests.test_track10_publishing_parity import (
    runtime, accepted_project, http, request, offline, gate, PID, UID, read_manifest, write_record,
)


def consent(platforms=("youtube",)):
    return {"enabled": True, "confirm": True, "targets": [
        {"platform": platform, "account_id": "fixture-account", "privacy": "unlisted" if platform == "youtube" else "SELF_ONLY"}
        for platform in platforms]}


def install_accounts(monkeypatch, runtime):
    monkeypatch.setattr(web, "connections_payload", lambda: {platform: {"status": "CONNECTED", "account_id": "fixture-account"} for platform in ("youtube", "tiktok")})
    monkeypatch.setattr(runtime.yt, "get_channel", lambda: {"id": "fixture-account"}, raising=False)
    monkeypatch.setattr(runtime.tk, "get_user", lambda: {"open_id": "fixture-account"}, raising=False)
    monkeypatch.setattr(web, "publication_readiness_payload", lambda project, platform, **kwargs: (
        200, {"platform": platform, "project_id": project, "can_publish": True, "connected": True, "account_id": "fixture-account"}))


def test_default_off_has_no_writes_or_platform_calls(accepted_project):
    path = completion.completion_path(PID, UID, root=web.PRODUCTIONS_DIR)
    assert completion.run_completion(web, PID, UID) is None
    assert completion.completion_status(PID, UID, root=web.PRODUCTIONS_DIR)["enabled"] is False
    assert not path.exists() and accepted_project.yt.calls == accepted_project.tk.calls == []


@pytest.mark.parametrize("body", [
    {"enabled": "true", "confirm": True}, {"enabled": True},
    {"enabled": True, "confirm": True, "mock_mode": True},
    {"enabled": True, "confirm": True, "targets": []},
    {"enabled": True, "confirm": True, "targets": [{"platform": "instagram", "account_id": "a", "privacy": "public"}]},
])
def test_invalid_or_sandbox_consent_never_dispatches(accepted_project, monkeypatch, body):
    install_accounts(monkeypatch, accepted_project)
    with pytest.raises(ValueError):
        completion.configure_completion(web, PID, UID, body)
    assert accepted_project.yt.calls == accepted_project.tk.calls == []


def test_both_platforms_dispatch_once_and_restarts_only_reconcile(accepted_project, monkeypatch):
    install_accounts(monkeypatch, accepted_project)
    configured = completion.configure_completion(web, PID, UID, consent(("youtube", "tiktok")))
    assert configured["intent"]["consent_revision"] == 1
    result = completion.run_completion(web, PID, UID)
    assert result["dispatch"]["state"] == "finished", result["dispatch"]["reason"]
    assert accepted_project.yt.calls.count("upload") == accepted_project.tk.calls.count("init") == 1
    again = completion.run_completion(web, PID, UID)
    assert again["dispatch"]["state"] == "finished"
    assert accepted_project.yt.calls.count("upload") == accepted_project.tk.calls.count("init") == 1
    assert len(again["dispatch"]["claims"]) == 2


def test_ambiguous_claim_without_identity_never_reuploads(accepted_project, monkeypatch):
    install_accounts(monkeypatch, accepted_project)
    completion.configure_completion(web, PID, UID, consent())
    path = completion.completion_path(PID, UID, root=web.PRODUCTIONS_DIR)
    record = completion._load(path)
    record.dispatch.claims["youtube"] = "interrupted-before-identity"
    completion._save(path, record)
    result = completion.run_completion(web, PID, UID)
    assert result["dispatch"]["outcomes"]["youtube"]["state"] == "UNKNOWN_RECONCILIATION_REQUIRED"
    assert "upload" not in accepted_project.yt.calls


def test_failed_refresh_leaves_finished_video_and_reconnect_reason(accepted_project, monkeypatch):
    install_accounts(monkeypatch, accepted_project)
    completion.configure_completion(web, PID, UID, consent())
    monkeypatch.setattr(web, "publication_readiness_payload", lambda *a, **kw: (200, {
        "can_publish": False, "reconnect_required": True, "reason_code": "INVALID_GRANT"}))
    before = (accepted_project.directory / "manifest.json").read_bytes()
    result = completion.run_completion(web, PID, UID)
    assert result["dispatch"]["state"] == "attention"
    assert result["dispatch"]["outcomes"]["youtube"]["reconnect_required"]
    assert (accepted_project.directory / "manifest.json").read_bytes() == before
    assert "upload" not in accepted_project.yt.calls


def test_account_change_and_generation_revision_change_block_uploads(accepted_project, monkeypatch):
    install_accounts(monkeypatch, accepted_project)
    completion.configure_completion(web, PID, UID, consent())
    monkeypatch.setattr(web, "publication_readiness_payload", lambda *a, **kw: (200, {"can_publish": True, "account_id": "different"}))
    result = completion.run_completion(web, PID, UID)
    assert result["dispatch"]["outcomes"]["youtube"]["error"] == "ACCOUNT_CHANGED"
    monkeypatch.setattr(completion, "generation_revision", lambda *args: "different-generation")
    assert completion.run_completion(web, PID, UID)["dispatch"]["state"] == "stale"
    assert "upload" not in accepted_project.yt.calls


def test_declining_auto_publish_is_durable_and_preserves_prior_claims(accepted_project, monkeypatch):
    install_accounts(monkeypatch, accepted_project)
    completion.configure_completion(web, PID, UID, consent())
    path = completion.completion_path(PID, UID, root=web.PRODUCTIONS_DIR)
    record = completion._load(path)
    record.dispatch.claims["youtube"] = "protected"
    completion._save(path, record)
    disabled = completion.configure_completion(web, PID, UID, {"enabled": False})
    assert not disabled["intent"]["enabled"] and disabled["dispatch"]["claims"]["youtube"] == "protected"
    assert completion.run_completion(web, PID, UID) is None


def test_passive_status_route_never_dispatches_authorized_work(accepted_project, monkeypatch):
    install_accounts(monkeypatch, accepted_project)
    completion.configure_completion(web, PID, UID, consent())
    monkeypatch.setattr(completion, "schedule_completion", Mock(side_effect=AssertionError("GET cannot dispatch")))
    status, payload = web.completion_publish_payload(PID, UID)
    assert status == 200 and payload["intent"]["enabled"]
    assert "upload" not in accepted_project.yt.calls


def test_existing_engine_failure_cannot_become_success_or_duplicate(accepted_project, monkeypatch):
    install_accounts(monkeypatch, accepted_project)
    completion.configure_completion(web, PID, UID, consent())
    accepted_project.yt.upload_error = RuntimeError("synthetic lost response")
    result = completion.run_completion(web, PID, UID)
    assert result["dispatch"]["state"] == "attention"
    completion.run_completion(web, PID, UID)
    assert accepted_project.yt.calls.count("upload") == 1
    assert read_manifest(accepted_project.directory)["publication"]["state"] == "UNKNOWN_RECONCILIATION_REQUIRED"


def test_startup_sweep_only_reads_previously_authorized_units(accepted_project, monkeypatch):
    install_accounts(monkeypatch, accepted_project)
    completion.configure_completion(web, PID, UID, consent())
    completion.recover_completions(web)
    assert accepted_project.yt.calls.count("upload") == 1
    completion.recover_completions(web)
    assert accepted_project.yt.calls.count("upload") == 1


def test_wrong_target_path_cannot_escape_production(tmp_path):
    with pytest.raises(ValueError):
        completion.completion_path("../../credentials", "u", root=tmp_path)


def test_busy_manual_operation_never_creates_an_ambiguous_dispatch_claim(accepted_project, monkeypatch):
    install_accounts(monkeypatch, accepted_project)
    completion.configure_completion(web, PID, UID, consent())
    web._publish_lock.acquire()
    try:
        result = completion.run_completion(web, PID, UID)
        assert result["dispatch"]["claims"] == {}
    finally:
        web._publish_lock.release()
    result = completion.run_completion(web, PID, UID)
    assert result["dispatch"]["state"] == "finished" and accepted_project.yt.calls.count("upload") == 1


def test_consent_changes_preserve_the_original_destinations_as_history(accepted_project, monkeypatch):
    install_accounts(monkeypatch, accepted_project)
    completion.configure_completion(web, PID, UID, consent())
    result = completion.configure_completion(web, PID, UID, {"enabled": False})
    assert result["consent_history"][0]["targets"][0]["account_id"] == "fixture-account"
    assert result["intent"]["consent_revision"] == 2


def test_explicit_reconnect_resumes_only_existing_authorized_attention(accepted_project, monkeypatch):
    install_accounts(monkeypatch, accepted_project)
    completion.configure_completion(web, PID, UID, consent())
    path = completion.completion_path(PID, UID, root=web.PRODUCTIONS_DIR)
    record = completion._load(path)
    record.dispatch.state = "attention"
    completion._save(path, record)
    schedule = Mock()
    monkeypatch.setattr(completion, "schedule_completion", schedule)
    completion.retry_authorized_completions_after_connect(web, "tiktok")
    assert not schedule.called
    completion.retry_authorized_completions_after_connect(web, "youtube")
    schedule.assert_called_once_with(web, PID, UID)


def test_executor_completion_callback_failure_cannot_invalidate_finished_generation(accepted_project):
    callback = Mock(side_effect=RuntimeError("Publishing service unavailable"))
    accepted_project.executor._on_unit_complete = callback
    accepted_project.executor.base_config.mock_mode = False
    result = accepted_project.executor.execute(unit_id=UID)
    assert next(unit for unit in result.units if unit.unit_id == UID).state.value == "COMPLETED"
    callback.assert_called_once_with(PID, UID)


def test_os_locked_consent_cannot_stack_dispatch_commands(accepted_project, monkeypatch):
    install_accounts(monkeypatch, accepted_project)
    completion.configure_completion(web, PID, UID, consent())
    path = completion.completion_path(PID, UID, root=web.PRODUCTIONS_DIR)
    with completion._locked(path):
        # A separate OS handle must not acquire the existing unit claim lock.
        with pytest.raises((BlockingIOError, OSError)):
            with completion._locked(path):
                raise AssertionError("overlapping OS lock acquired")


def test_malformed_optional_consent_does_not_break_assembly_completion(accepted_project):
    path = completion.completion_path(PID, UID, root=web.PRODUCTIONS_DIR)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"intent": "not valid consent"}')
    before = path.read_bytes()
    assert completion.schedule_completion(web, PID, UID) is False
    assert path.read_bytes() == before
    assert "upload" not in accepted_project.yt.calls and "init" not in accepted_project.tk.calls