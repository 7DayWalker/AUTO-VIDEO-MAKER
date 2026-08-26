"""
TikTok web publish bridge tests (Step 3B) — fully offline.

Verifies the bridge is a THIN adapter over the existing publishing modules:
- explicit request + project validation
- connectivity gate before any publish call
- exact invocation of the existing prepare → creator-check → submit → verify
  sequence (all faked; real TikTok APIs are NEVER touched)
- duplicate/ambiguous-state refusal without retries
- repeatable read-only status endpoint
- double-request safety via the single-flight lock
"""

import json
import threading
import time
import urllib.request
from types import SimpleNamespace

import pytest

from app.publishing.models import PublicationState, PublicationVerdict
from app.web import server as web


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def make_record(state, privacy="SELF_ONLY", publish_id=None, post_id=None, error=None):
    return SimpleNamespace(
        state=state,
        tiktok_metadata=SimpleNamespace(privacy_level=privacy),
        tiktok_publish_id=publish_id,
        tiktok_post_id=post_id,
        error=error,
        verified_at=None,
    )


def make_report(verdict, state=None, reasons=None):
    return SimpleNamespace(verdict=verdict, state=state, reasons=list(reasons or []))


class FakePublisher:
    def __init__(self, connected=True, options=("SELF_ONLY",), calls=None):
        self._connected = connected
        self._options = list(options)
        self._calls = calls if calls is not None else []

    def validate_stored_token(self):
        return {"access_token": "fake"} if self._connected else None

    def get_creator_info(self):
        self._calls.append("creator")
        return {"privacy_level_options": self._options}


class MustNotCall:
    """Any attribute access pattern used for 'must never happen' fakes."""

    def __getattr__(self, name):
        def _forbidden(*args, **kwargs):  # pragma: no cover
            raise AssertionError(f"{name} must never be called")
        return _forbidden


@pytest.fixture()
def api(monkeypatch, tmp_path):
    """Isolated server + data dir; returns (base_url, tmp_path, calls)."""
    monkeypatch.setattr(web, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(web, "WEBAPP_DIR", tmp_path / "webapp")
    (tmp_path / "webapp").mkdir()

    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web.AVMHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {
        "base": f"http://127.0.0.1:{httpd.server_address[1]}",
        "tmp": tmp_path,
        "calls": [],
    }
    httpd.shutdown()
    httpd.server_close()


def write_manifest(tmp, project_id="proj_pub", tk_state=None, complete=True):
    directory = tmp / "projects" / project_id
    directory.mkdir(parents=True)
    data = {
        "project_id": project_id,
        "topic": "Publish bridge fixture",
        "created_at": "2026-08-25T09:00:00",
        "stages": {},
    }
    if complete:
        data["completed_at"] = "2026-08-25T10:00:00"
    if tk_state:
        data["tiktok_publication"] = {"project_id": project_id, "state": tk_state}
    (directory / "manifest.json").write_text(json.dumps(data), encoding="utf-8")


def get(url):
    with urllib.request.urlopen(url) as res:
        return res.status, json.loads(res.read().decode("utf-8"))


def post(url, payload=None):
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


# ---------------------------------------------------------------------------
# A / B — request validation
# ---------------------------------------------------------------------------

def test_a_publish_requires_existing_project(api, monkeypatch):
    def forbidden(*args, **kwargs):  # pragma: no cover
        raise AssertionError("publishing sequence must not run for unknown projects")

    monkeypatch.setattr(web, "_reconcile", forbidden)
    monkeypatch.setattr(web, "_prepare", forbidden)
    monkeypatch.setattr(web, "_submit", forbidden)
    monkeypatch.setattr(web, "_verify", forbidden)

    status, body = post(api["base"] + "/api/projects/does_not_exist/publish/tiktok")
    assert status == 404


def test_b_publish_refused_when_tiktok_not_connected(api, monkeypatch):
    write_manifest(api["tmp"])
    monkeypatch.setattr(web, "_new_publisher", lambda: FakePublisher(connected=False))

    def forbidden(*args, **kwargs):  # pragma: no cover
        raise AssertionError("no upload may be attempted while disconnected")

    monkeypatch.setattr(web, "_prepare", forbidden)
    monkeypatch.setattr(web, "_submit", forbidden)
    monkeypatch.setattr(web, "_verify", forbidden)

    status, body = post(api["base"] + "/api/projects/proj_pub/publish/tiktok")
    assert status == 409
    assert body["error"] == "TIKTOK_NOT_CONNECTED"
    assert body["state"] is None


# ---------------------------------------------------------------------------
# C — explicit publish invokes the EXISTING sequence in order
# ---------------------------------------------------------------------------

def test_c_publish_invokes_existing_prepare_submit_verify_sequence(api, monkeypatch):
    write_manifest(api["tmp"])
    calls = api["calls"]
    publisher = FakePublisher(connected=True, calls=calls)
    monkeypatch.setattr(web, "_new_publisher", lambda: publisher)

    def fake_reconcile(project_id, publisher=None):
        calls.append("reconcile")
        return make_report(PublicationVerdict.PUBLICATION_NOT_STARTED)

    def fake_prepare(project_id, privacy_level=None):
        calls.append("prepare")
        return make_record(PublicationState.PREPARED)

    def fake_submit(project_id, pub):
        calls.append("submit")
        return make_record(PublicationState.IDENTITY_PERSISTED, publish_id="pid_1")

    def fake_verify(project_id, pub):
        calls.append("verify")
        return make_record(PublicationState.VERIFIED, post_id="post_1")

    monkeypatch.setattr(web, "_reconcile", fake_reconcile)
    monkeypatch.setattr(web, "_prepare", fake_prepare)
    monkeypatch.setattr(web, "_submit", fake_submit)
    monkeypatch.setattr(web, "_verify", fake_verify)

    status, body = post(api["base"] + "/api/projects/proj_pub/publish/tiktok",
                        {"privacy_level": "SELF_ONLY"})
    assert status == 200
    assert body["state"] == "VERIFIED"
    assert body["post_id"] == "post_1"
    # The bridge must call the existing flow in the canonical order.
    assert calls == ["reconcile", "prepare", "creator", "submit", "verify"]


# ---------------------------------------------------------------------------
# D — verified identity already exists: NO second upload
# ---------------------------------------------------------------------------

def test_d_verified_identity_never_resubmits(api, monkeypatch):
    write_manifest(api["tmp"], tk_state="VERIFIED")
    monkeypatch.setattr(web, "_new_publisher", lambda: FakePublisher(connected=True))
    monkeypatch.setattr(
        web, "_reconcile",
        lambda project_id, publisher=None: make_report(
            PublicationVerdict.PUBLICATION_VERIFIED, state="VERIFIED")
    )

    def forbidden(*args, **kwargs):  # pragma: no cover
        raise AssertionError("duplicate submission attempted after VERIFIED")

    monkeypatch.setattr(web, "_submit", forbidden)
    monkeypatch.setattr(web, "_verify", forbidden)

    status, body = post(api["base"] + "/api/projects/proj_pub/publish/tiktok")
    assert status == 200
    assert body.get("published_already") is True


# ---------------------------------------------------------------------------
# E — ambiguous/unknown state: truthful refusal, NO automatic retry
# ---------------------------------------------------------------------------

def test_e_unknown_state_forbidden_from_retrying(api, monkeypatch):
    write_manifest(api["tmp"])
    monkeypatch.setattr(web, "_new_publisher", lambda: FakePublisher(connected=True))
    monkeypatch.setattr(
        web, "_reconcile",
        lambda project_id, publisher=None: make_report(
            PublicationVerdict.PUBLICATION_UNKNOWN_RECONCILIATION_REQUIRED,
            reasons=["submission may have reached TikTok"],
        )
    )

    def forbidden(*args, **kwargs):  # pragma: no cover
        raise AssertionError("blind retry attempted from UNKNOWN_RECONCILIATION_REQUIRED")

    monkeypatch.setattr(web, "_prepare", forbidden)
    monkeypatch.setattr(web, "_submit", forbidden)
    monkeypatch.setattr(web, "_verify", forbidden)

    status, body = post(api["base"] + "/api/projects/proj_pub/publish/tiktok")
    assert status == 409
    assert body["error"] == "UNKNOWN_RECONCILIATION_REQUIRED"


# ---------------------------------------------------------------------------
# F — status endpoint: repeatable, read-only, never uploads/OAuth
# ---------------------------------------------------------------------------

def test_f_status_endpoint_is_read_only_and_repeatable(api, monkeypatch):
    write_manifest(api["tmp"], tk_state="PREPARED")

    def forbidden(*args, **kwargs):  # pragma: no cover
        raise AssertionError("status checks must not reconcile/upload while offline")

    monkeypatch.setattr(web, "_reconcile", forbidden)
    monkeypatch.setattr(web, "_submit", forbidden)
    monkeypatch.setattr(web, "_prepare", forbidden)
    monkeypatch.setattr(web, "_new_publisher", lambda: FakePublisher(connected=False))

    url = api["base"] + "/api/projects/proj_pub/publish/tiktok/status"
    status1, body1 = get(url)
    status2, body2 = get(url)
    assert status1 == 200 and status2 == 200
    assert body1["state"] == "PREPARED"
    assert body2["state"] == "PREPARED"
    assert body1["connected"] is False and body2["connected"] is False
    assert body1["can_publish"] is False


# ---------------------------------------------------------------------------
# G — double-request safety (single-flight lock)
# ---------------------------------------------------------------------------

def test_g_concurrent_publish_requests_cannot_duplicate(api, monkeypatch):
    write_manifest(api["tmp"])
    submit_count = {"n": 0}
    count_lock = threading.Lock()

    monkeypatch.setattr(web, "_new_publisher", lambda: FakePublisher(connected=True))

    def fake_reconcile(project_id, publisher=None):
        return make_report(PublicationVerdict.PUBLICATION_NOT_STARTED)

    def slow_prepare(project_id, privacy_level=None):
        time.sleep(0.4)  # widen the race window
        return make_record(PublicationState.PREPARED)

    def counting_submit(project_id, pub):
        with count_lock:
            submit_count["n"] += 1
        return make_record(PublicationState.PROCESSING, publish_id="pid_x")

    def fast_verify(project_id, pub):
        return make_record(PublicationState.VERIFIED, post_id="p_x")

    monkeypatch.setattr(web, "_reconcile", fake_reconcile)
    monkeypatch.setattr(web, "_prepare", slow_prepare)
    monkeypatch.setattr(web, "_submit", counting_submit)
    monkeypatch.setattr(web, "_verify", fast_verify)

    results = []

    def fire():
        results.append(post(
            api["base"] + "/api/projects/proj_pub/publish/tiktok",
            {"privacy_level": "SELF_ONLY"},
        ))

    threads = [threading.Thread(target=fire) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one submission may ever reach the (faked) TikTok sequence.
    assert submit_count["n"] == 1
    ok = [b for s, b in results if s == 200]
    busy = [b for s, b in results if s == 409]
    assert len(ok) == 1
    assert len(busy) >= 1
    assert all(b.get("error") == "PUBLISH_ALREADY_RUNNING" for b in busy)


# ---------------------------------------------------------------------------
# I — the bridge reaches TikTok only through the injectable factory
# ---------------------------------------------------------------------------

def test_i_bridge_uses_injectable_publisher_factory_only():
    def forbidden():  # pragma: no cover
        raise AssertionError("real TikTokPublisher constructed during tests")
    original = web._new_publisher
    web._new_publisher = forbidden
    try:
        # Disconnected-check path consults only the injected object:
        fake = SimpleNamespace(validate_stored_token=lambda: None)
        assert web._tiktok_connected(fake) is False
        connected_fake = SimpleNamespace(validate_stored_token=lambda: {"access_token": "x"})
        assert web._tiktok_connected(connected_fake) is True
    finally:
        web._new_publisher = original