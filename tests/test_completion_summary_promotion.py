"""Summary completion promotes only explicitly authorized real evidence."""
from types import SimpleNamespace

from app.publishing import completion
from app.web import server as web
from tests.test_track9_summary_publish_handoff import runtime, rendered, offline, PID, UID
from tests.test_track10_publishing_parity import FakeYouTube, FakeTikTok


def _install(monkeypatch, rendered):
    monkeypatch.setattr(web, "connections_payload", lambda: {
        "youtube": {"status": "CONNECTED", "account_id": "fixture-account"}})
    # A rendered fixture's real media/acceptance remains unchanged; its fake
    # speech is marked real only for this offline completion orchestration test.
    monkeypatch.setattr(completion, "_source_is_real", lambda *a: True)
    def publisher():
        bound = rendered.executor.discover_bindings().get(UID)
        value = FakeYouTube(bound.project_dir)
        value.get_channel = lambda: {"id": "fixture-account"}
        return value
    monkeypatch.setattr(web, "_new_youtube_publisher", publisher)
    monkeypatch.setattr(web, "publication_readiness_payload", lambda project, platform, **kw: (
        200, {"can_publish": True, "account_id": "fixture-account"}))


def test_authorized_summary_completion_promotes_before_publish(rendered, monkeypatch):
    _install(monkeypatch, rendered)
    completion.configure_completion(web, PID, UID, {"enabled": True, "confirm": True, "promote_summary": True,
        "targets": [{"platform": "youtube", "account_id": "fixture-account", "privacy": "unlisted"}]})
    assert not rendered.executor.discover_bindings().get(UID)
    result = completion.run_completion(web, PID, UID)
    assert result["dispatch"]["state"] == "finished", result["dispatch"]["reason"]
    assert rendered.executor.discover_bindings().get(UID)
    assert result["dispatch"]["outcomes"]["youtube"]["video_id"]


def test_rendered_sandbox_cannot_autopromote_or_publish(rendered, monkeypatch):
    _install(monkeypatch, rendered)
    monkeypatch.setattr(completion, "_source_is_real", lambda *a: False)
    completion.configure_completion(web, PID, UID, {"enabled": True, "confirm": True, "promote_summary": True,
        "targets": [{"platform": "youtube", "account_id": "fixture-account", "privacy": "unlisted"}]})
    result = completion.run_completion(web, PID, UID)
    assert result["dispatch"]["state"] == "attention"
    assert not rendered.executor.discover_bindings().get(UID)


def test_rendered_summary_without_promotion_consent_stays_inspectable(rendered, monkeypatch):
    _install(monkeypatch, rendered)
    completion.configure_completion(web, PID, UID, {"enabled": True, "confirm": True,
        "targets": [{"platform": "youtube", "account_id": "fixture-account", "privacy": "unlisted"}]})
    result = completion.run_completion(web, PID, UID)
    assert result["dispatch"]["state"] == "waiting_completion"
    assert not rendered.executor.discover_bindings().get(UID)


def test_existing_binding_is_revalidated_before_authorized_completion_dispatch(rendered, monkeypatch):
    from tests.test_track9_summary_publish_handoff import promote
    _install(monkeypatch, rendered)
    promote(rendered)
    original = web.summary_video_promote_payload
    calls = []
    monkeypatch.setattr(web, "summary_video_promote_payload", lambda pid, uid: calls.append((pid, uid)) or original(pid, uid))
    completion.configure_completion(web, PID, UID, {"enabled": True, "confirm": True, "promote_summary": True,
        "targets": [{"platform": "youtube", "account_id": "fixture-account", "privacy": "unlisted"}]})
    result = completion.run_completion(web, PID, UID)
    assert result["dispatch"]["state"] == "finished"
    assert calls == [(PID, UID)]