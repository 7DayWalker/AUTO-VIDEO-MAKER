"""Phase 2C targeted tests — GET /api/capabilities readiness surface.

The capability surface must be: additive, GET-only, read-only, deterministic,
network-free, subprocess-free, secret-free, independent of production state,
and honest that "ready" means LOCALLY CONFIGURED only. All configuration
states are forced via monkeypatch so the tests never depend on the developer's
real .env values.
"""

from __future__ import annotations

import http.client
import inspect
import json
import socket
import subprocess
import threading
import urllib.error
import urllib.request

import pytest

from app.web import server as web


@pytest.fixture()
def cap_server(monkeypatch, tmp_path):
    """Real localhost HTTP server with isolated (empty) state areas."""
    monkeypatch.setattr(web, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    monkeypatch.setattr(web, "WEBAPP_DIR", tmp_path / "webapp")
    (tmp_path / "webapp").mkdir()
    (tmp_path / "webapp" / "index.html").write_text("<html>avm</html>", encoding="utf-8")
    (tmp_path / "projects").mkdir()
    (tmp_path / "productions").mkdir()
    monkeypatch.setattr(web, "_production_runs", {})

    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web.AVMHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path
    httpd.shutdown()
    httpd.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _post(url):
    request = urllib.request.Request(url, data=b"{}", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            return error.code, json.loads(body)
        except json.JSONDecodeError:
            return error.code, {"raw": body}


def _fully_configured(monkeypatch):
    """Force every seam into a deterministic locally-configured state."""
    monkeypatch.setattr("app.content.providers.DEFAULT_LLM_PROVIDER", "gemini")
    monkeypatch.setattr(
        "app.content.providers.gemini.GEMINI_API_KEY", "gemini-test-credential"
    )
    monkeypatch.setattr(
        "app.content.providers.gemini.GEMINI_MODEL", "configured-gemini-model"
    )
    monkeypatch.setattr("app.voice.config.TTS_PROVIDER", "edge_tts")
    monkeypatch.setenv("ALIBABA_API_KEY", "alibaba-video-test-credential")
    monkeypatch.setenv("AVM_OPENROUTER_API_KEY", "openrouter-video-test-credential")
    monkeypatch.delenv("AVM_IMAGE_SOURCE", raising=False)
    monkeypatch.delenv("AVM_ALIBABA_VIDEO_MODEL", raising=False)
    # auto -> wikimedia child is keyless and always AVAILABLE.


# ---------------------------------------------------------------------------
# Contract shape
# ---------------------------------------------------------------------------


def test_capabilities_endpoint_exists_and_is_shaped(cap_server):
    base, _ = cap_server
    status, payload = _get(base + "/api/capabilities")
    assert status == 200
    assert payload["sample"] is False
    assert "configuration readiness" in payload["notice"].lower()
    assert "never 'provider available'" in payload["notice"]
    assert set(payload["seams"]) == {
        "planner", "tts", "image_source", "alibaba_video", "openrouter_video",
    }
    routes = payload["production_routes"]["routes"]
    # Only the SaaS product-route surface is projected; private google_flow
    # is never presented, and the block self-labels as NOT route resolution.
    assert set(routes) == {"local_ai_assisted", "full_ai_video"}
    assert "NOT route resolution" in payload["production_routes"]["note"]


def test_endpoint_is_get_only_and_takes_no_arguments(cap_server):
    base, _ = cap_server
    status, payload = _post(base + "/api/capabilities")
    assert status == 405
    assert payload["error"] == "METHOD_NOT_ALLOWED"
    # The surface is not parameterized by production/project identity.
    assert inspect.signature(web.capabilities_payload).parameters == {}


# ---------------------------------------------------------------------------
# Read-only / deterministic / network-free guarantees
# ---------------------------------------------------------------------------


def test_repeated_calls_are_deterministic_and_read_only(cap_server):
    base, tmp = cap_server
    sentinel = tmp / "productions" / "prd_sentinel" / "production.json"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text('{"untouched": true}', encoding="utf-8")
    project_file = tmp / "projects" / "proj_sentinel" / "manifest.json"
    project_file.parent.mkdir(parents=True)
    project_file.write_text('{"untouched": true}', encoding="utf-8")
    before = {path: path.read_bytes() for path in (sentinel, project_file)}

    first = _get(base + "/api/capabilities")[1]
    second = _get(base + "/api/capabilities")[1]
    third = _get(base + "/api/capabilities")[1]
    assert first == second == third, "configuration projection must be stable"
    for path in before:
        assert path.read_bytes() == before[path]
    assert web._production_runs == {}, "capabilities must not touch run state"


def test_projection_never_touches_network_or_subprocess(monkeypatch):
    """Every outbound primitive raises: the projection must still succeed."""
    def _blocked(*args, **kwargs):
        raise AssertionError("capability surface performed I/O")

    import requests

    monkeypatch.setattr(requests, "get", _blocked)
    monkeypatch.setattr(requests, "post", _blocked)
    monkeypatch.setattr(requests, "request", _blocked)
    monkeypatch.setattr(requests.Session, "request", _blocked)
    monkeypatch.setattr(http.client, "HTTPConnection", _blocked)
    monkeypatch.setattr(http.client, "HTTPSConnection", _blocked)
    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(subprocess, "run", _blocked)
    monkeypatch.setattr(subprocess, "Popen", _blocked)
    monkeypatch.setattr(urllib.request, "urlopen", _blocked)

    payload = web.capabilities_payload()
    assert set(payload["seams"]) == {
        "planner", "tts", "image_source", "alibaba_video", "openrouter_video",
    }


# ---------------------------------------------------------------------------
# Truthful configured / missing reporting
# ---------------------------------------------------------------------------


def test_configured_seams_are_reported(monkeypatch):
    _fully_configured(monkeypatch)
    seams = web.capabilities_payload()["seams"]

    assert seams["planner"] == {
        "provider": "gemini",
        "model": "configured-gemini-model",
        "configured": True,
        "status": "ready",
    }
    assert seams["tts"]["provider"] == "edge_tts"
    assert seams["tts"]["configured"] is True
    assert seams["tts"]["status"] == "ready"
    assert seams["alibaba_video"]["configured"] is True
    assert seams["alibaba_video"]["status"] == "ready"
    # Model identity is a non-secret configuration value, reported verbatim.
    assert seams["alibaba_video"]["model"] == "wan2.2-t2v-plus"
    assert seams["openrouter_video"]["configured"] is True
    assert seams["openrouter_video"]["status"] == "ready"
    assert seams["image_source"]["configured"] is True
    assert seams["image_source"]["status"] == "available"


def test_missing_configuration_is_reported(monkeypatch):
    _fully_configured(monkeypatch)
    monkeypatch.setattr("app.content.providers.gemini.GEMINI_API_KEY", "")
    monkeypatch.delenv("ALIBABA_API_KEY", raising=False)
    monkeypatch.delenv("AVM_OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("AVM_IMAGE_SOURCE", "pexels")
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    seams = web.capabilities_payload()["seams"]

    assert seams["planner"]["configured"] is False
    assert seams["planner"]["status"] == "not_configured"
    assert seams["alibaba_video"]["configured"] is False
    assert seams["alibaba_video"]["status"] == "not_configured"
    assert seams["openrouter_video"]["configured"] is False
    assert seams["openrouter_video"]["status"] == "not_configured"
    # The pexels selector without its key uses the EXISTING availability
    # vocabulary verbatim (visual_sources.SourceAvailability).
    assert seams["image_source"]["status"] == "requires_configuration"
    assert seams["image_source"]["configured"] is False


def test_openrouter_video_key_precedence_is_respected(monkeypatch):
    # Shared content-gateway key satisfies the video seam's documented
    # fallback (resolve_openrouter_video_api_key precedence), truthfully.
    monkeypatch.delenv("AVM_OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "shared-openrouter-credential")
    seams = web.capabilities_payload()["seams"]
    assert seams["openrouter_video"]["configured"] is True


def test_planner_mock_is_disclosed_as_offline_mode(monkeypatch):
    monkeypatch.setattr("app.content.providers.DEFAULT_LLM_PROVIDER", "mock")
    planner = web.capabilities_payload()["seams"]["planner"]
    assert planner["provider"] == "mock"
    assert planner["configured"] is True
    # Mock is explicit offline/test mode: disclosed, never production ready.
    assert planner["status"] == "offline_mode"


def test_planner_unknown_provider_is_honestly_unknown(monkeypatch):
    monkeypatch.setattr(
        "app.content.providers.DEFAULT_LLM_PROVIDER", "definitely-not-a-provider"
    )
    planner = web.capabilities_payload()["seams"]["planner"]
    assert planner["status"] == "unknown"
    assert planner["configured"] is False
    assert planner["provider"] == "definitely-not-a-provider"


def test_alibaba_forbidden_credential_class_is_never_ready(monkeypatch):
    """The existing provider predicate distinguishes key-present from
    key-usable; the projection reuses it verbatim (never its own guess)."""
    from app.content import providers as content_providers

    monkeypatch.setattr(content_providers, "DEFAULT_LLM_PROVIDER", "alibaba")
    monkeypatch.setenv("ALIBABA_API_KEY", "sk-sp-forbidden-token-plan-class")
    planner = web.capabilities_payload()["seams"]["planner"]
    assert planner["configured"] is False
    assert planner["status"] == "not_configured"


def test_tts_unsupported_provider_is_reported(monkeypatch):
    monkeypatch.setattr("app.voice.config.TTS_PROVIDER", "some-unknown-tts")
    tts = web.capabilities_payload()["seams"]["tts"]
    assert tts["configured"] is False
    assert tts["status"] == "unsupported"


# ---------------------------------------------------------------------------
# Secrets never cross the boundary
# ---------------------------------------------------------------------------


_SENTINELS = {
    "ALIBABA_API_KEY": "sentinel-alibaba-key-9f31",
    "AVM_OPENROUTER_API_KEY": "sentinel-or-video-key-7b12",
    "OPENROUTER_API_KEY": "sentinel-shared-or-key-4a77",
    "PEXELS_API_KEY": "sentinel-pexels-key-5d88",
    "AVM_IMAGE_MODEL": "sentinel-image-model-unused-value",  # non-secret slug, may echo
}


def test_credential_values_never_appear_in_the_response(monkeypatch):
    _fully_configured(monkeypatch)
    monkeypatch.setenv("ALIBABA_API_KEY", _SENTINELS["ALIBABA_API_KEY"])
    monkeypatch.setenv("AVM_OPENROUTER_API_KEY", _SENTINELS["AVM_OPENROUTER_API_KEY"])
    monkeypatch.setenv("OPENROUTER_API_KEY", _SENTINELS["OPENROUTER_API_KEY"])
    monkeypatch.setenv("PEXELS_API_KEY", _SENTINELS["PEXELS_API_KEY"])
    monkeypatch.setattr(
        "app.content.providers.gemini.GEMINI_API_KEY", "sentinel-gemini-key-2c44"
    )
    monkeypatch.setattr("app.voice.config.TTS_PROVIDER", "edge_tts")

    rendered = json.dumps(web.capabilities_payload())
    for name in (
        "ALIBABA_API_KEY", "AVM_OPENROUTER_API_KEY", "OPENROUTER_API_KEY",
        "PEXELS_API_KEY",
    ):
        assert _SENTINELS[name] not in rendered, f"{name} value leaked"
    assert "sentinel-gemini-key-2c44" not in rendered, "credential value leaked"


# ---------------------------------------------------------------------------
# Route readiness is a pure projection, never route resolution
# ---------------------------------------------------------------------------


def test_route_projection_combines_existing_facts(monkeypatch):
    _fully_configured(monkeypatch)
    routes = web.capabilities_payload()["production_routes"]["routes"]
    assert routes["local_ai_assisted"]["status"] == "ready"
    assert routes["full_ai_video"]["status"] == "ready"
    assert routes["full_ai_video"]["missing"] == []

    # Removing the Alibaba key makes ONLY full_ai_video not-ready — the
    # projection composes existing seam facts and re-implements no routing.
    monkeypatch.delenv("ALIBABA_API_KEY", raising=False)
    routes = web.capabilities_payload()["production_routes"]["routes"]
    assert routes["full_ai_video"]["status"] == "not_ready"
    assert routes["full_ai_video"]["missing"] == ["alibaba_video"]
    assert routes["local_ai_assisted"]["status"] == "ready"


def test_route_projection_discloses_offline_mode_honestly(monkeypatch):
    _fully_configured(monkeypatch)
    monkeypatch.setattr("app.content.providers.DEFAULT_LLM_PROVIDER", "mock")
    routes = web.capabilities_payload()["production_routes"]["routes"]
    # Mock planning is explicit test mode: never projected as production
    # readiness even though every credential fact still says "configured".
    assert routes["local_ai_assisted"]["status"] == "offline_mode"
    assert routes["full_ai_video"]["status"] == "offline_mode"
    assert routes["local_ai_assisted"]["missing"] == []


# ---------------------------------------------------------------------------
# Existing surfaces untouched
# ---------------------------------------------------------------------------


def test_health_and_production_surfaces_remain_unchanged(cap_server):
    base, _ = cap_server
    with urllib.request.urlopen(base + "/api/health", timeout=10) as response:
        assert json.loads(response.read().decode("utf-8")) == {
            "ok": True, "service": "avm-web",
        }
    status, productions = _get(base + "/api/productions")
    assert status == 200
    assert productions == []  # isolated empty areas behave exactly as before
    # The capability surface coexists without perturbing anything.
    cap_status, cap_payload = _get(base + "/api/capabilities")
    assert cap_status == 200 and cap_payload["sample"] is False
    status, productions_again = _get(base + "/api/productions")
    assert (status, productions_again) == (200, [])



