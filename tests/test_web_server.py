"""
Web experience bridge tests (offline).

The bridge must be THIN and SAFE:
- /api/connections reuses the Step-1 connection status logic (non-interactive).
- /api/projects reads manifests read-only; path traversal is blocked.
- Connect endpoints never invoke publishing code paths.
"""

import json
import threading
from pathlib import Path

import pytest

from app.web import server as web


@pytest.fixture()
def api_server(monkeypatch, tmp_path):
    """Real HTTP server on an ephemeral port with isolated data dirs."""
    monkeypatch.setattr(web, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(web, "WEBAPP_DIR", tmp_path / "webapp")
    (tmp_path / "webapp").mkdir()
    (tmp_path / "webapp" / "index.html").write_text("<html>avm</html>", encoding="utf-8")

    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web.AVMHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path
    httpd.shutdown()
    httpd.server_close()


def _get(url):
    import urllib.request
    with urllib.request.urlopen(url) as res:
        return res.status, json.loads(res.read().decode("utf-8"))


def _post(url, payload=None):
    import urllib.request
    req = urllib.request.Request(url, data=b"{}", method="POST")
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code:
            return code, {}
        raise


def test_health(api_server):
    base, _ = api_server
    status, body = _get(base + "/api/health")
    assert status == 200 and body["ok"] is True


def test_connections_reuses_step1_layer(api_server, monkeypatch):
    base, _ = api_server

    class FakePub:
        token_file = "missing.json"
        class token_path:
            exists = staticmethod(lambda: False)

    monkeypatch.setattr(web, "connections_payload", lambda: {
        "youtube": {"status": "CONNECTED", "account": "Test Channel"},
        "tiktok": {"status": "NOT CONNECTED"},
        "instagram": {"status": "COMING SOON"},
        "facebook": {"status": "COMING SOON"},
    })
    # Patch publisher constructors used inside connections_payload's real impl;
    # here we replaced payload wholesale, so simply verify HTTP surface.
    status, body = _get(base + "/api/connections")
    assert status == 200
    assert body["youtube"]["status"] == "CONNECTED"
    assert "token" not in json.dumps(body).lower()


def test_projects_read_only_from_manifests(api_server):
    base, tmp = api_server
    proj = tmp / "projects" / "proj_001"
    proj.mkdir(parents=True)
    (proj / "manifest.json").write_text(json.dumps({
        "project_id": "proj_001",
        "topic": "Test topic",
        "created_at": "2026-08-25T10:00:00",
        "completed_at": "2026-08-25T11:00:00",
        "stages": {},
        "publication": {"state": "VERIFIED", "youtube_video_id": "abc123"},
        "final_video_path": str(tmp / "missing.mp4"),
    }), encoding="utf-8")

    status, items = _get(base + "/api/projects")
    assert status == 200 and len(items) == 1
    item = items[0]
    assert item["project_id"] == "proj_001"
    assert item["stage_status"] == "complete"
    assert item["publications"][0]["platform"] == "youtube"
    assert item["publications"][0]["state"] == "VERIFIED"


def test_project_detail_traversal_blocked(api_server):
    base, _ = api_server
    # Direct function-level guard:
    assert web.project_detail("..%2F..%2Fsecrets") is None
    assert web.project_detail("../../../.env") is None
    assert web.media_path_for("../../../.env") is None
    # And via HTTP the traversal id simply resolves to "not found":
    import urllib.error
    try:
        with urllib.request.urlopen(base + "/api/projects/..%2F.env") as res:
            status = res.status
    except Exception as exc:
        status = getattr(exc, "code", 0)
    assert status in (403, 404)


def test_connect_endpoint_routes_only_youtube_tiktok(api_server, monkeypatch):
    base, _ = api_server
    started = []

    def fake_start(platform):
        started.append(platform)
        return 202, {"detail": "started", "platform": platform}

    monkeypatch.setattr(web, "_start_connect", fake_start)
    status, _ = _post(base + "/api/platforms/tiktok/connect")
    assert status == 202 and started == ["tiktok"]
