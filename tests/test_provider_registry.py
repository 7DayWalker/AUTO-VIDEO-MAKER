"""
AVM Step 2 -- Provider Registry foundation.

Tests the provider-name -> factory registry is extensible, stores factories
(not instances), is deterministic, supports open provider names, and does NOT
perform model resolution / fallback / network / credential checks.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys

import pytest


def test_registry_lists_registered_providers():
    from app.content import provider_registry as reg

    providers = reg.list_providers()
    assert isinstance(providers, tuple)
    # Step 4: codecraft is now registered
    assert providers == ("alibaba", "openrouter", "gemini", "mock", "codecraft")


def test_existing_real_providers_registered():
    from app.content.provider_registry import has_provider, get_provider_factory

    for name in ("alibaba", "openrouter", "gemini", "mock"):
        assert has_provider(name) is True
        assert callable(get_provider_factory(name))


def test_provider_names_are_open_strings():
    from app.content.provider_registry import register_provider, has_provider, get_provider_factory
    from app.content.providers.base import BaseLLMProvider

    future_name = "future_provider_x_step2"
    import app.content.provider_registry as reg
    if has_provider(future_name):
        reg._REGISTRY.pop(future_name.lower(), None)

    def _fake_factory(model_id=None):
        from app.content.providers.mock import MockLLMProvider
        return MockLLMProvider(model_name=model_id or "future-model-1")

    register_provider(future_name, _fake_factory)
    assert has_provider(future_name) is True
    assert has_provider(future_name.upper()) is True
    assert has_provider(f"  {future_name}  ") is True
    inst = get_provider_factory(future_name)("my-wire-id")
    assert isinstance(inst, BaseLLMProvider)
    assert inst.model == "my-wire-id"
    reg._REGISTRY.pop(future_name.lower(), None)
    assert has_provider(future_name) is False


def test_duplicate_registration_rejected():
    from app.content.provider_registry import register_provider

    def _dup(model_id=None):
        from app.content.providers.mock import MockLLMProvider
        return MockLLMProvider()

    with pytest.raises(ValueError, match="already registered"):
        register_provider("mock", _dup)
    register_provider("mock", _dup, allow_override=True)
    import app.content.provider_registry as reg

    def _real_mock(model_id=None):
        from app.content.providers.mock import MockLLMProvider
        if model_id:
            return MockLLMProvider(model_name=model_id)
        return MockLLMProvider()

    reg._REGISTRY["mock"] = _real_mock
    assert reg.has_provider("mock")


def test_unknown_provider_lookup_fails_clearly():
    from app.content.provider_registry import get_provider_factory, create_provider

    with pytest.raises(KeyError, match="unknown provider"):
        get_provider_factory("does_not_exist_xyz")
    with pytest.raises(KeyError, match="unknown provider"):
        create_provider("does_not_exist_xyz", "some-model")
    with pytest.raises((KeyError, ValueError)):
        get_provider_factory("")
    with pytest.raises((KeyError, ValueError)):
        create_provider("", "x")


def test_registry_stores_factories_not_instances():
    import app.content.provider_registry as reg
    from app.content.providers.base import BaseLLMProvider

    for name in reg.list_providers():
        factory = reg.get_provider_factory(name)
        assert callable(factory)
        assert not isinstance(factory, BaseLLMProvider)


def test_import_does_not_make_network_calls_and_no_credentials_needed(monkeypatch):
    monkeypatch.delenv("ALIBABA_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("CODECRAFT_API_KEY", raising=False)
    # Execute fresh copies of both module sources under ALIAS names instead of
    # sys.modules.pop() + re-import. Re-importing under the real name leaks a
    # second module object (sys.modules and the parent package attribute then
    # diverge), which silently breaks every later test that patches or
    # excepts app.content.provider_registry / model_registry members.
    def _fresh_copy(dotted: str, alias: str):
        reference = importlib.import_module(dotted)
        spec = importlib.util.spec_from_file_location(alias, reference.__file__)
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        try:
            spec.loader.exec_module(module)
        finally:
            if sys.modules.get(alias) is module:
                del sys.modules[alias]
        return module

    _fresh_copy("app.content.model_registry", "avm_fresh_model_registry_for_preg")
    mod = _fresh_copy("app.content.provider_registry", "avm_fresh_provider_registry")
    assert mod.list_providers() == ("alibaba", "openrouter", "gemini", "mock", "codecraft")
    import pathlib
    text = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    assert "import requests" not in text
    assert "from app.content.model_registry" not in text
    assert "FallbackLLMProvider" not in text


def test_factory_lookup_deterministic():
    from app.content import provider_registry as reg

    a = reg.get_provider_factory("alibaba")
    b = reg.get_provider_factory("alibaba")
    assert a is b
    assert reg.get_provider_factory("ALIBABA") is a


def test_factory_invoked_with_model_id():
    from app.content.provider_registry import create_provider

    p_alibaba = create_provider("alibaba", "my-qwen-test-123")
    assert p_alibaba.name == "alibaba" and p_alibaba.model == "my-qwen-test-123"
    p_openrouter = create_provider("openrouter", "anthropic/claude-3.5-haiku")
    assert p_openrouter.name == "openrouter"
    p_gemini = create_provider("gemini", "gemini-2.0-flash")
    assert p_gemini.name == "gemini" and p_gemini.model == "gemini-2.0-flash"
    p_mock = create_provider("mock", "hello-model")
    assert p_mock.name == "mock" and p_mock.model == "hello-model"
    p_mock_default = create_provider("mock")
    assert p_mock_default.model == "mock-model"
    with pytest.raises(ValueError):
        create_provider("mock", "")
    with pytest.raises(ValueError):
        create_provider("mock", "   ")


def test_existing_provider_implementations_unchanged():
    import pathlib

    for provider_file, class_name in (
        ("app/content/providers/alibaba.py", "AlibabaProvider"),
        ("app/content/providers/openrouter.py", "OpenRouterProvider"),
        ("app/content/providers/gemini.py", "GeminiProvider"),
        ("app/content/providers/mock.py", "MockLLMProvider"),
    ):
        text = pathlib.Path(f"C:/Projects/AUTO-VIDEO-MAKER/{provider_file}").read_text(encoding="utf-8")
        assert f"class {class_name}" in text
        assert "provider_registry" not in text
        assert "model_registry" not in text


def test_get_default_provider_behavior_unchanged():
    import pathlib
    from app.content.providers import get_default_provider
    import app.content.config as cfg
    import app.content.providers as prov_mod

    src = pathlib.Path(prov_mod.__file__).read_text(encoding="utf-8")
    assert "DEFAULT_LLM_PROVIDER" in src
    assert "def get_default_provider" in src
    p = get_default_provider()
    assert hasattr(p, "name") and hasattr(p, "is_available")
    assert isinstance(p.is_available(), bool)


def test_existing_fallback_behavior_unchanged():
    import pathlib

    text = pathlib.Path("C:/Projects/AUTO-VIDEO-MAKER/app/content/providers/fallback.py").read_text(encoding="utf-8")
    assert "class FallbackLLMProvider" in text
    assert "provider_registry" not in text
    assert "CanonicalModel" not in text


def test_model_registry_remains_separate():
    import pathlib

    model_text = pathlib.Path("C:/Projects/AUTO-VIDEO-MAKER/app/content/model_registry.py").read_text(encoding="utf-8")
    provider_text = pathlib.Path("C:/Projects/AUTO-VIDEO-MAKER/app/content/provider_registry.py").read_text(encoding="utf-8")
    assert "provider_registry" not in model_text
    assert "get_provider_factory" not in model_text
    # Check executable code only — docstring may mention canonical concept
    provider_code = provider_text.split("__all__")[0]
    assert "from app.content.model_registry" not in provider_code
    assert "MODEL_REGISTRY" not in provider_code
    assert "CanonicalModel" not in provider_code


def test_no_canonical_model_resolution_inside_provider_registry():
    import pathlib
    text = pathlib.Path("C:/Projects/AUTO-VIDEO-MAKER/app/content/provider_registry.py").read_text(encoding="utf-8")
    assert "get_model" not in text
    assert "list_models" not in text
    assert "qwen-3.8-flash" not in text


def test_codecraft_registered():
    from app.content.provider_registry import has_provider, get_provider_factory, create_provider

    assert has_provider("codecraft") is True
    assert has_provider("CODECRAFT") is True
    # Factory creates correct provider with wire id
    p = create_provider("codecraft", "qwen3.8-27b")
    assert p.name == "codecraft" and p.model == "qwen3.8-27b"
    assert callable(get_provider_factory("codecraft"))


def test_provider_name_normalization():
    from app.content.provider_registry import has_provider, get_provider_factory

    assert has_provider("  ALIBABA ") is True
    assert has_provider("OpenRouter") is True
    assert has_provider("MOCK") is True
    assert get_provider_factory("  gemini ") is get_provider_factory("gemini")
