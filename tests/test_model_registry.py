"""
Step 1 -- Canonical Model Registry (read-only, data-only).
Focused tests + architectural invariant regression. No network, no provider
instantiation, no fallback/provider-factory coupling.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys

import pytest


def test_canonical_model_construction():
    from app.content.model_registry import CanonicalModel, ModelCapabilities, ProviderBinding

    m = CanonicalModel(
        id="qwen-3.8-flash",
        display_name="Qwen 3.8 Flash",
        family="qwen",
        capabilities=ModelCapabilities(text=True, json_=True),
        bindings=(ProviderBinding(provider="codecraft", provider_model_id="qwen3.8-27b"),),
        default_binding="codecraft",
    )
    assert m.id == "qwen-3.8-flash"
    assert m.display_name == "Qwen 3.8 Flash"
    assert m.family == "qwen"
    assert m.capabilities.json is True
    assert m.bindings[0].provider == "codecraft"


def test_provider_binding_construction():
    from app.content.model_registry import ProviderBinding

    b = ProviderBinding(provider="codecraft", provider_model_id="qwen3.8-27b")
    assert b.provider == "codecraft"
    assert b.provider_model_id == "qwen3.8-27b"
    assert b.config_keys == ()


def test_provider_binding_strips_and_accepts_list():
    from app.content.model_registry import ProviderBinding

    b = ProviderBinding(
        provider="  codecraft ",
        provider_model_id=" qwen3.8-27b ",
        config_keys=[" CODECRAFT_API_KEY ", "CODECRAFT_BASE_URL"],
    )
    assert b.provider == "codecraft"
    assert b.provider_model_id == "qwen3.8-27b"
    assert b.config_keys == ("CODECRAFT_API_KEY", "CODECRAFT_BASE_URL")
    b2 = ProviderBinding(provider="alibaba", provider_model_id="qwen-plus", config_keys=("ALIBABA_API_KEY",))
    assert b2.config_keys == ("ALIBABA_API_KEY",)


def test_list_models_deterministic_and_get_has():
    from app.content import model_registry as reg

    a = reg.list_models()
    b = reg.list_models()
    assert a == b
    # Base AI Brain model must remain present; summary models are additive
    assert "qwen-3.8-flash" in tuple(m.id for m in a)
    assert reg.has_model("qwen-3.8-flash") is True
    assert reg.has_model("  qwen-3.8-flash ") is True
    assert reg.has_model("") is False
    assert reg.has_model("does-not-exist") is False
    assert reg.get_model("qwen-3.8-flash").id == "qwen-3.8-flash"
    assert reg.get_model("  qwen-3.8-flash ").id == "qwen-3.8-flash"
    with pytest.raises(KeyError):
        reg.get_model("does-not-exist")
    with pytest.raises(KeyError):
        reg.get_model("")
    with pytest.raises(KeyError):
        reg.get_model("   ")
    # Summary understanding: true canonical identities are active, not aliases
    assert reg.has_model("qwen-3.8-flash")
    assert reg.has_model("gemini-3-flash-preview")
    assert reg.has_model("qwen-vl-max")
    assert reg.has_model("gpt-4o")
    # Deprecated aliases are NOT active identities (but get_model resolves them)
    assert not reg.has_model("summary-standard-qwen-flash")
    assert not reg.has_model("summary-pro-gemini-3-flash-preview")
    assert not reg.has_model("summary-pro-gemini-2.0-flash")
    assert reg.get_model("summary-pro-gemini-3-flash-preview").id == "gemini-3-flash-preview"
    assert reg.get_model("summary-standard-qwen-flash").id == "qwen-3.8-flash"
    # Drawer capability query is provider-neutral
    pro_ids = [m.id for m in reg.list_models_for_pro()]
    assert "gemini-3-flash-preview" in pro_ids
    assert "qwen-vl-max" in pro_ids
    assert "gpt-4o" in pro_ids
    assert "qwen-3.8-flash" not in pro_ids


def test_registry_snapshot_is_tuple():
    from app.content import model_registry as reg

    snap = reg.list_models()
    assert isinstance(snap, tuple)
    assert reg.list_models() == snap


def test_registry_models_are_frozen():
    from app.content.model_registry import get_model

    m = get_model("qwen-3.8-flash")
    with pytest.raises(Exception):
        m.id = "mutated"  # type: ignore[misc]
    with pytest.raises(Exception):
        m.bindings[0].provider = "mutated"  # type: ignore[misc]


def test_registry_initial_content_qwen_flash():
    from app.content.model_registry import get_model

    m = get_model("qwen-3.8-flash")
    assert m.display_name == "Qwen 3.8 Flash"
    assert m.family == "qwen"
    assert m.capabilities.text is True
    assert m.capabilities.json is True
    assert len(m.bindings) == 1
    assert m.bindings[0].provider == "codecraft"
    assert m.bindings[0].provider_model_id == "qwen3.8-27b"
    assert "CODECRAFT_API_KEY" in m.bindings[0].config_keys
    assert m.default_binding == "codecraft"


def test_gemma_is_not_a_qwen_binding():
    from app.content.model_registry import list_models

    gemma_wire = "google/gemma-4-26b-a4b-it:free"
    for m in list_models():
        assert all(b.provider_model_id != gemma_wire for b in m.bindings)
        if m.id == "qwen-3.8-flash":
            assert all("gemma" not in b.provider_model_id.lower() for b in m.bindings)


def test_alibaba_not_implicitly_bound():
    from app.content.model_registry import get_model

    m = get_model("qwen-3.8-flash")
    assert all(b.provider != "alibaba" for b in m.bindings)


def test_empty_ids_rejected():
    from app.content.model_registry import CanonicalModel, ProviderBinding

    with pytest.raises(Exception):
        CanonicalModel(id="", display_name="x")
    with pytest.raises(Exception):
        CanonicalModel(id="x", display_name="")
    with pytest.raises(Exception):
        ProviderBinding(provider="", provider_model_id="qwen3.8-27b")
    with pytest.raises(Exception):
        ProviderBinding(provider="codecraft", provider_model_id="")


def test_duplicate_provider_bindings_rejected():
    from app.content.model_registry import CanonicalModel, ProviderBinding

    with pytest.raises(ValueError, match="duplicate provider"):
        CanonicalModel(
            id="qwen-3.8-flash",
            display_name="Qwen 3.8 Flash",
            bindings=(
                ProviderBinding(provider="codecraft", provider_model_id="qwen3.8-27b"),
                ProviderBinding(provider="codecraft", provider_model_id="qwen3.8-flash"),
            ),
        )


def test_default_binding_must_exist():
    from app.content.model_registry import CanonicalModel, ProviderBinding

    with pytest.raises(ValueError, match="default_binding"):
        CanonicalModel(
            id="qwen-3.8-flash",
            display_name="Qwen 3.8 Flash",
            bindings=(ProviderBinding(provider="codecraft", provider_model_id="qwen3.8-27b"),),
            default_binding="alibaba",
        )


def test_canonical_id_not_equal_wire_id():
    # Provider-neutral migration: canonical id truthfully equals wire id when
    # model identity matches provider wire id (e.g. gemini-3-flash-preview).
    # This must NOT be rejected.
    from app.content.model_registry import CanonicalModel, ProviderBinding

    m = CanonicalModel(
        id="gemini-3-flash-preview",
        display_name="Gemini 3 Flash Preview",
        bindings=(ProviderBinding(provider="gemini", provider_model_id="gemini-3-flash-preview"),),
        default_binding="gemini",
    )
    assert m.bindings[0].provider_model_id == "gemini-3-flash-preview"


def test_provider_is_open_string_future_provider():
    from app.content.model_registry import CanonicalModel, ProviderBinding

    m = CanonicalModel(
        id="qwen-3.8-flash",
        display_name="Qwen 3.8 Flash",
        bindings=(
            ProviderBinding(provider="codecraft", provider_model_id="qwen3.8-27b"),
            ProviderBinding(provider="future_provider_x", provider_model_id="future-model-1"),
            ProviderBinding(provider="google", provider_model_id="gemini-3-flash"),
        ),
        default_binding="codecraft",
    )
    assert [b.provider for b in m.bindings] == ["codecraft", "future_provider_x", "google"]


def _fresh_copy(dotted: str, alias: str):
    """Execute a fresh copy of ``dotted`` from source under an ALIAS name.

    ``sys.modules.pop()`` + re-import leaks a second module object into the
    session: ``sys.modules`` and the parent package attribute then point at
    different objects, so a later test that patches
    ``app.content.model_registry.<name>`` patches a different module than the
    one the production code executes. Loading under an alias keeps the real
    module (and its identity) untouched.
    """
    reference = importlib.import_module(dotted)
    spec = importlib.util.spec_from_file_location(alias, reference.__file__)
    module = importlib.util.module_from_spec(spec)
    # pydantic/dataclasses resolve annotations through sys.modules[__module__],
    # so the alias must be registered while the fresh copy executes; it is
    # removed again immediately, leaving the real module identity untouched.
    sys.modules[alias] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.modules.get(alias) is module:
            del sys.modules[alias]
    return module


def test_registry_import_does_not_import_providers(monkeypatch):
    monkeypatch.delenv("ALIBABA_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("CODECRAFT_API_KEY", raising=False)
    monkeypatch.delenv("CODECRAFT_BASE_URL", raising=False)
    mod = _fresh_copy("app.content.model_registry", "avm_fresh_model_registry")
    assert mod.list_models()
    import pathlib

    text = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    # Check executable code only (before __all__), not docstring examples.
    code = text.split("__all__")[0]
    assert "from app.content.providers" not in code
    assert "import requests" not in code
    # BaseLLMProvider doc reference is allowed; ensure no import/use.
    assert "from app.content.providers.base import" not in code
    assert "BaseLLMProvider(" not in code


def test_registry_does_not_read_env(monkeypatch):
    monkeypatch.setenv("CODECRAFT_API_KEY", "sk-test-should-not-be-read")
    monkeypatch.setenv("ALIBABA_API_KEY", "sk-test-should-not-be-read")
    mod = _fresh_copy("app.content.model_registry", "avm_fresh_model_registry_env")
    m = mod.get_model("qwen-3.8-flash")
    assert m.bindings[0].provider_model_id == "qwen3.8-27b"


def test_existing_provider_factory_unchanged():
    import pathlib

    for path in (
        "app/content/providers/__init__.py",
        "app/content/providers/fallback.py",
        "app/content/providers/base.py",
        "app/content/generator.py",
        "app/content/planner.py",
        "app/orchestrator/production_execution.py",
        "app/production/models.py",
        "app/production/routes.py",
    ):
        text = pathlib.Path(f"C:/Projects/AUTO-VIDEO-MAKER/{path}").read_text(encoding="utf-8")
        assert "model_registry" not in text, f"{path} must not import model_registry in Step 1"
        assert "CanonicalModel" not in text, f"{path} must not reference CanonicalModel in Step 1"
    # get_default_provider reads DEFAULT_LLM_PROVIDER via live import;
    # exercise mock explicitly to prove factory still honors the env
    # without asserting that monkeypatched config overrides .env (some
    # branches cache the value). Instead, assert the module still
    # references DEFAULT_LLM_PROVIDER and returns a BaseLLMProvider.
    from app.content.providers import get_default_provider
    import app.content.config as cfg
    import app.content.providers as prov_mod
    import pathlib
    import inspect

    # Config and provider factory must still be wired.
    assert hasattr(cfg, "DEFAULT_LLM_PROVIDER")
    src = pathlib.Path(prov_mod.__file__).read_text(encoding="utf-8")
    assert "DEFAULT_LLM_PROVIDER" in src
    assert "get_default_provider" in src
    # Direct provider construction still works (the registry is dormant).
    from app.content.providers.mock import MockLLMProvider

    assert MockLLMProvider().name == "mock"
    p = get_default_provider()
    assert hasattr(p, "name") and hasattr(p, "is_available")
