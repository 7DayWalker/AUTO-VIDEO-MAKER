"""
AVM Step 7 -- MODEL-FIRST API contract (offline, deterministic).

Proves the two additive backend seams a model-first UI needs, and nothing more:

1. ``GET /api/capabilities`` projects the CANONICAL MODEL REGISTRY so a client
   can render an arbitrary model list dynamically -- while never naming a
   provider, a provider wire model id, a config key or a credential, and never
   ranking or pricing a model.
2. ``POST /api/productions`` accepts an optional ``canonical_model_id`` as
   CREATION INTENT only, validated against the registry.

Runtime boundary (Step 3 stays dormant): creating a production with a canonical
model NEVER calls ``resolve_model()`` and never changes which planner factory
the executor uses, so no production execution path is activated here.
"""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from app.content import provider_registry
from app.content.model_registry import (
    CanonicalModel,
    ModelCapabilities,
    ProviderBinding,
)
from app.production.storage import load_production
from app.web import server as web

# --- two SYNTHETIC canonical models: the projection must be registry-driven ---
# Deliberately not the production registry entry, and deliberately two: a client
# that hardcodes one model, or that renders providers, cannot pass these tests.
MODEL_A = CanonicalModel(
    id="synthetic-alpha",
    display_name="Synthetic Alpha",
    family="alpha",
    capabilities=ModelCapabilities(text=True, reasoning=True),
    bindings=(ProviderBinding(provider="mock", provider_model_id="alpha-wire-1"),),
    default_binding="mock",
)
MODEL_B = CanonicalModel(
    id="synthetic-beta",
    display_name="Synthetic Beta",
    family="beta",
    capabilities=ModelCapabilities(vision=True, long_context=True, tool_use=True),
    # TWO bindings on two DIFFERENT providers (one binding per provider is the
    # registry invariant): the projection must count them without naming them.
    bindings=(
        ProviderBinding(provider="mock", provider_model_id="beta-wire-1"),
        ProviderBinding(provider="gemini", provider_model_id="beta-wire-2"),
    ),
    default_binding="mock",
)


@pytest.fixture()
def two_models(monkeypatch):
    """Serve a synthetic two-model registry without touching the real one."""
    monkeypatch.setattr(
        "app.content.model_registry.list_models", lambda: (MODEL_A, MODEL_B)
    )
    monkeypatch.setattr(
        "app.content.model_registry.has_model",
        lambda canonical_id: (canonical_id or "").strip() in {MODEL_A.id, MODEL_B.id},
    )
    return (MODEL_A, MODEL_B)


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


def _request(**overrides):
    body = {
        "mode": "single_video",
        "creation_method": "local",
        "title": "Model-first test video",
        "brief": "A small deterministic production for Step 7 validation.",
        "output_intent": "youtube_shorts",
        "target_duration": 30,
        "scene_count": 6,
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# A) Canonical model projection on the EXISTING capabilities endpoint
# ---------------------------------------------------------------------------


def test_model_projection_is_registry_driven_and_model_first(two_models):
    projection = web.capabilities_payload()["models"]

    assert projection["source"] == "app.content.model_registry"
    assert [m["id"] for m in projection["models"]] == [
        "synthetic-alpha", "synthetic-beta"
    ]
    # display_name is the user-facing vocabulary; family + generic capabilities
    # are carried for capability-driven (never provider-driven) rendering.
    alpha = projection["models"][0]
    assert alpha["display_name"] == "Synthetic Alpha"
    assert alpha["family"] == "alpha"
    assert alpha["capabilities"] == {
        "text": True, "json": True, "reasoning": True,
        "vision": False, "long_context": False, "tool_use": False,
    }
    beta = projection["models"][1]
    assert beta["display_name"] == "Synthetic Beta"
    assert beta["capabilities"]["vision"] is True
    assert beta["capabilities"]["tool_use"] is True
    assert beta["binding_count"] == 2
    # The 'mock' binding is always locally configured -> ready, expressed in the
    # EXISTING seam vocabulary (no new available/coming-soon words invented).
    assert alpha["configured"] is True and alpha["status"] == "ready"


def test_model_projection_never_names_a_provider_or_wire_model(two_models):
    projection = web.capabilities_payload()["models"]
    # Asserted on the DATA (the model entries). The human-readable `note`
    # explains the model-first rule in prose and is not a data field.
    blob = json.dumps(projection["models"]).lower()

    for forbidden in (
        "mock", "codecraft", "alibaba", "openrouter", "gemini",   # provider ids
        "alpha-wire-1", "beta-wire-1", "beta-wire-2",             # wire model ids
        "provider", "config_keys", "codecraft_api_key",
        "cost", "price", "recommended", "best", "rank",
    ):
        assert forbidden not in blob, f"{forbidden!r} leaked into the model projection"
    # Model-first: bindings are COUNTED, never described.
    assert "binding_count" in blob
    # The entry schema itself is fixed and provider-free.
    assert set(projection["models"][0]) == {
        "id", "display_name", "family", "capabilities",
        "binding_count", "configured", "status",
    }


def test_model_projection_exposes_no_credential_value(two_models, monkeypatch):
    monkeypatch.setenv("CODECRAFT_API_KEY", "sk-sentinel-step7-2c44")
    monkeypatch.setenv("CODECRAFT_BASE_URL", "https://sentinel.example/v1")

    blob = json.dumps(web.capabilities_payload())
    assert "sk-sentinel-step7-2c44" not in blob
    assert "sentinel.example" not in blob


def test_model_readiness_is_configuration_only_and_truthful(monkeypatch):
    """Unconfigured / unregistered bindings are never projected as ready."""
    unconfigured = CanonicalModel(
        id="synthetic-unconfigured",
        display_name="Synthetic Unconfigured",
        family="gamma",
        bindings=(
            ProviderBinding(provider="codecraft", provider_model_id="gamma-wire-1"),
        ),
        default_binding="codecraft",
    )
    unregistered = CanonicalModel(
        id="synthetic-unregistered",
        display_name="Synthetic Unregistered",
        family="delta",
        bindings=(
            ProviderBinding(
                provider="provider_not_registered_zz",
                provider_model_id="delta-wire-1",
            ),
        ),
        default_binding="provider_not_registered_zz",
    )
    monkeypatch.delenv("CODECRAFT_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.content.model_registry.list_models",
        lambda: (unconfigured, unregistered),
    )

    models = web.capabilities_payload()["models"]["models"]
    assert models[0]["configured"] is False
    assert models[0]["status"] == "not_configured"   # registered, key missing
    assert models[1]["configured"] is False
    assert models[1]["status"] == "not_configured"   # provider not registered
    assert models[0]["binding_count"] == 1


def test_model_probe_failure_is_unknown_not_unavailable(monkeypatch, two_models):
    def boom(name, provider_model_id=None):
        raise RuntimeError("synthetic probe failure")

    monkeypatch.setattr(provider_registry, "create_provider", boom)

    models = web.capabilities_payload()["models"]["models"]
    assert [m["status"] for m in models] == ["unknown", "unknown"]
    assert all(m["configured"] is False for m in models)
    # A failing probe never removes a model and never fabricates readiness.
    assert [m["id"] for m in models] == ["synthetic-alpha", "synthetic-beta"]


def test_registry_failure_degrades_to_an_empty_list(monkeypatch):
    def explode():
        raise RuntimeError("synthetic registry failure")

    monkeypatch.setattr("app.content.model_registry.list_models", explode)

    projection = web.capabilities_payload()["models"]
    assert projection["models"] == []
    assert projection["status"] == "unknown"
    # Nothing is fabricated to fill the gap.
    assert "qwen" not in json.dumps(projection).lower()


def test_existing_capability_surfaces_are_unchanged(cap_server, two_models):
    base, _ = cap_server
    status, payload = _get(base + "/api/capabilities")

    assert status == 200
    assert payload["sample"] is False
    assert set(payload["seams"]) == {
        "planner", "tts", "image_source", "alibaba_video", "openrouter_video",
    }
    assert set(payload["production_routes"]["routes"]) == {
        "local_ai_assisted", "full_ai_video",
    }
    # The additive projection sits beside the established ones.
    assert [m["id"] for m in payload["models"]["models"]] == [
        "synthetic-alpha", "synthetic-beta"
    ]
    assert payload["models"]["models"][0]["display_name"] == "Synthetic Alpha"


# ---------------------------------------------------------------------------
# B) canonical_model_id as CREATION INTENT (never as execution wiring)
# ---------------------------------------------------------------------------


def test_creation_records_a_registered_canonical_model_as_intent(
    tmp_path, monkeypatch, two_models
):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    status, payload = web.create_production_payload(
        _request(canonical_model_id="synthetic-alpha")
    )

    assert status == 201
    # Echoed exactly as persisted, and as an INTENT — no provider, no wire model.
    assert payload["canonical_model_id"] == "synthetic-alpha"
    assert "provider" not in payload

    production = load_production(
        payload["production_id"], root=tmp_path / "productions"
    )
    constraints = production.command.explicit_constraints
    assert constraints["canonical_model_id"] == "synthetic-alpha"
    # A wire/provider identity was never smuggled in beside the intent.
    assert "provider" not in constraints
    assert "provider_model_id" not in constraints
    assert "alpha-wire-1" not in json.dumps(constraints)


def test_creation_rejects_an_unknown_canonical_model(tmp_path, monkeypatch, two_models):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    status, payload = web.create_production_payload(
        _request(canonical_model_id="gpt-9-does-not-exist")
    )

    assert status == 400
    assert payload["error"] == "INVALID_CREATION_REQUEST"
    assert "canonical_model_id" in payload["message"]
    # Fails loudly BEFORE persisting anything.
    assert not (tmp_path / "productions").exists()


def test_creation_without_a_model_is_byte_for_byte_the_old_contract(
    tmp_path, monkeypatch, two_models
):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    status, payload = web.create_production_payload(_request())

    assert status == 201
    assert payload["canonical_model_id"] is None
    production = load_production(
        payload["production_id"], root=tmp_path / "productions"
    )
    # Not declared != defaulted: the key is absent, never auto-filled.
    assert "canonical_model_id" not in production.command.explicit_constraints


def test_client_supplied_provider_identity_is_never_a_creation_input(
    tmp_path, monkeypatch, two_models
):
    """A model-first client cannot choose a provider, even if it tries."""
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    status, payload = web.create_production_payload(
        _request(
            canonical_model_id="synthetic-beta",
            provider="mock",
            provider_model_id="beta-wire-2",
            planner_provider="mock",
            planner_model="beta-wire-2",
        )
    )

    assert status == 201
    raw = json.loads(
        (
            tmp_path / "productions" / payload["production_id"] / "production.json"
        ).read_text(encoding="utf-8")
    )
    constraints = raw["command"]["explicit_constraints"]
    assert constraints["canonical_model_id"] == "synthetic-beta"
    for forbidden in ("provider", "provider_model_id", "planner_provider", "planner_model"):
        assert forbidden not in constraints
    assert "beta-wire" not in json.dumps(constraints)


def test_creation_never_activates_the_model_bound_resolver(
    tmp_path, monkeypatch, two_models
):
    """Step 3 stays DORMANT: no production execution path is wired by Step 7."""
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    monkeypatch.setattr(web, "PROJECTS_DIR", tmp_path / "projects")

    resolver_calls = []
    monkeypatch.setattr(
        "app.content.model_resolver.resolve_model",
        lambda *a, **k: resolver_calls.append((a, k)),
    )
    planner_calls = []

    class _Stub:
        provider = None

    def _provider_first_factory(*, mock_mode=False, output_dir=None):
        planner_calls.append(mock_mode)
        return _Stub()

    def _forbidden(*args, **kwargs):
        raise AssertionError("the dormant resolver connector must not be used")

    monkeypatch.setattr(
        "app.content.planner.create_scene_planner", _provider_first_factory
    )
    monkeypatch.setattr(
        "app.content.planner.planner_from_resolved_model", _forbidden
    )

    status, payload = web.create_production_payload(
        _request(canonical_model_id="synthetic-alpha")
    )
    assert status == 201
    assert resolver_calls == []

    # The executor still plans through the EXISTING provider-first seam.
    executor = web._production_executor(payload["production_id"])
    assert isinstance(executor._planner_factory(), _Stub)
    assert planner_calls and all(mode is False for mode in planner_calls)
    assert resolver_calls == []


def test_model_intent_is_readable_through_the_existing_detail_record(
    tmp_path, monkeypatch, two_models
):
    """No new endpoint: the intent rides the already-exposed command record."""
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    monkeypatch.setattr(web, "PROJECTS_DIR", tmp_path / "projects")
    status, created = web.create_production_payload(
        _request(canonical_model_id="synthetic-alpha")
    )
    assert status == 201

    status, detail = web.production_detail_payload(created["production_id"])
    assert status == 200
    assert (
        detail["command"]["explicit_constraints"]["canonical_model_id"]
        == "synthetic-alpha"
    )
    # The list surface is unchanged (no model field was added there).
    status, listing = web.productions_index_payload()
    assert status == 200
    assert "canonical_model_id" not in json.dumps(listing)

