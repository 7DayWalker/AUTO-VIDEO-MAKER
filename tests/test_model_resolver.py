"""
AVM Step 3 -- Model-bound Resolver tests (offline, deterministic).
"""

from __future__ import annotations

import importlib
import importlib.util
import sys

import pytest

from app.content.providers.base import BaseLLMProvider, LLMResponse
from app.content.exceptions import LLMRateLimitError, LLMAuthenticationError


class FakeSuccessProvider(BaseLLMProvider):
    def __init__(self, name_val="fake-a", model_val="fake-wire-a"):
        self._name = name_val
        self._model = model_val
    @property
    def name(self): return self._name
    @property
    def model(self): return self._model
    def generate(self, prompt, system_prompt=None, response_schema=None, temperature=0.7, max_tokens=4096):
        return LLMResponse(content='{"ok": true}', provider=self._name, model=self._model)
    def is_available(self): return True


class FakeUnavailableProvider(BaseLLMProvider):
    def __init__(self, name_val="fake-unavail", model_val="wire"):
        self._name = name_val
        self._model = model_val
    @property
    def name(self): return self._name
    @property
    def model(self): return self._model
    def generate(self, *a, **kw): return LLMResponse(content="{}", provider=self._name, model=self._model)
    def is_available(self): return False


TEST_MODEL_ID = "test-canonical-model-resolver"
FAKE_PROVIDERS = ("test-provider-a", "test-provider-b", "test-provider-c")
FAKE_WIRE = ("wire-a", "wire-b", "wire-c")


@pytest.fixture(autouse=True)
def _register_test_model_and_providers():
    import app.content.provider_registry as preg
    from app.content.model_registry import CanonicalModel, ProviderBinding, MODEL_REGISTRY

    factories = {
        "test-provider-a": lambda mid=None: FakeSuccessProvider("test-provider-a", mid or "wire-a"),
        "test-provider-b": lambda mid=None: FakeSuccessProvider("test-provider-b", mid or "wire-b"),
        "test-provider-c": lambda mid=None: FakeSuccessProvider("test-provider-c", mid or "wire-c"),
    }
    for n, f in factories.items():
        if preg.has_provider(n):
            preg._REGISTRY.pop(n, None)
        preg.register_provider(n, f)

    bindings = tuple(ProviderBinding(provider=n, provider_model_id=w) for n, w in zip(FAKE_PROVIDERS, FAKE_WIRE))
    model = CanonicalModel(id=TEST_MODEL_ID, display_name="Test Model", family="test", bindings=bindings, default_binding=FAKE_PROVIDERS[0])
    MODEL_REGISTRY[TEST_MODEL_ID] = model
    yield
    MODEL_REGISTRY.pop(TEST_MODEL_ID, None)
    for n in factories:
        preg._REGISTRY.pop(n, None)


def test_basic_resolution_single_binding():
    from app.content.model_resolver import resolve_model
    r = resolve_model(TEST_MODEL_ID)
    assert r.canonical_model_id == TEST_MODEL_ID
    assert r.provider == "test-provider-a"
    assert r.provider_model_id == "wire-a"
    assert isinstance(r.provider_instance, BaseLLMProvider)


def test_canonical_identity_and_wire_id_distinct():
    from app.content.model_resolver import resolve_model
    r = resolve_model(TEST_MODEL_ID)
    assert r.canonical_model_id == TEST_MODEL_ID
    assert r.provider_model_id != r.canonical_model_id


def test_provider_registry_integration_not_direct():
    from app.content.model_resolver import resolve_model
    r = resolve_model(TEST_MODEL_ID)
    assert r.provider_instance.name == "test-provider-a"


def test_unknown_model_raises():
    from app.content.model_resolver import resolve_model, UnknownCanonicalModelError
    with pytest.raises(UnknownCanonicalModelError):
        resolve_model("does-not-exist-xyz-unknown")


def test_no_bindings_raises():
    from app.content.model_resolver import resolve_model, NoProviderBindingsError
    from app.content.model_registry import CanonicalModel, MODEL_REGISTRY
    mid = "test-no-bindings"
    MODEL_REGISTRY[mid] = CanonicalModel(id=mid, display_name="Empty", bindings=())
    try:
        with pytest.raises(NoProviderBindingsError):
            resolve_model(mid)
    finally:
        MODEL_REGISTRY.pop(mid, None)


def test_codecraft_binding_now_resolves():
    """Step 4: qwen-3.8-flash -> codecraft is now resolvable (was ProviderNotRegisteredError in Step 3)."""
    from app.content.model_resolver import resolve_model
    r = resolve_model("qwen-3.8-flash")
    assert r.canonical_model_id == "qwen-3.8-flash"
    assert r.provider == "codecraft"
    assert r.provider_model_id == "qwen3.8-27b"


def test_deterministic_binding_order():
    from app.content.model_registry import get_model
    m = get_model(TEST_MODEL_ID)
    assert tuple(b.provider for b in m.bindings) == FAKE_PROVIDERS


def test_same_model_fallback_eligible():
    import app.content.provider_registry as preg
    from app.content.model_resolver import resolve_model
    preg._REGISTRY["test-provider-a"] = lambda mid=None: FakeUnavailableProvider("test-provider-a", mid or "wire-a")
    preg._REGISTRY["test-provider-b"] = lambda mid=None: FakeSuccessProvider("test-provider-b", mid or "wire-b")
    try:
        r = resolve_model(TEST_MODEL_ID, try_next_on_failure=True)
        assert r.provider == "test-provider-b"
        assert r.canonical_model_id == TEST_MODEL_ID
        assert any(a["provider"] == "test-provider-a" for a in r.attempted)
    finally:
        preg._REGISTRY["test-provider-a"] = lambda mid=None: FakeSuccessProvider("test-provider-a", mid or "wire-a")
        preg._REGISTRY["test-provider-b"] = lambda mid=None: FakeSuccessProvider("test-provider-b", mid or "wire-b")


def test_no_inter_model_fallback():
    from app.content.model_resolver import resolve_model, AllExactModelProvidersFailedError
    from app.content.model_registry import CanonicalModel, ProviderBinding, MODEL_REGISTRY
    import app.content.provider_registry as preg

    other_id = "test-other-model-not-used"
    MODEL_REGISTRY[other_id] = CanonicalModel(
        id=other_id, display_name="Other", bindings=(ProviderBinding(provider="test-provider-c", provider_model_id="other-wire"),)
    )
    orig = {n: preg._REGISTRY[n] for n in FAKE_PROVIDERS}
    for n in FAKE_PROVIDERS:
        preg._REGISTRY[n] = lambda mid=None, _n=n: FakeUnavailableProvider(_n, mid or "wire")
    try:
        with pytest.raises(AllExactModelProvidersFailedError) as ei:
            resolve_model(TEST_MODEL_ID, try_next_on_failure=True)
        assert all(a["provider"] in FAKE_PROVIDERS for a in ei.value.attempted)
    finally:
        for n, f in orig.items():
            preg._REGISTRY[n] = f
        MODEL_REGISTRY.pop(other_id, None)


def test_non_eligible_failure_does_not_fallback():
    import app.content.provider_registry as preg
    from app.content.model_resolver import resolve_model, AllExactModelProvidersFailedError

    def _bad(mid=None):
        raise LLMAuthenticationError("test-provider-a")
    orig = preg._REGISTRY["test-provider-a"]
    preg._REGISTRY["test-provider-a"] = _bad
    try:
        with pytest.raises(AllExactModelProvidersFailedError):
            resolve_model(TEST_MODEL_ID, try_next_on_failure=True)
    finally:
        preg._REGISTRY["test-provider-a"] = orig


def test_all_providers_fail_reports_attempted():
    import app.content.provider_registry as preg
    from app.content.model_resolver import resolve_model, AllExactModelProvidersFailedError
    orig = {n: preg._REGISTRY[n] for n in FAKE_PROVIDERS}
    for n in FAKE_PROVIDERS:
        preg._REGISTRY[n] = lambda mid=None, _n=n: FakeUnavailableProvider(_n, mid or "wire")
    try:
        with pytest.raises(AllExactModelProvidersFailedError) as ei:
            resolve_model(TEST_MODEL_ID, try_next_on_failure=True)
        assert ei.value.canonical_model_id == TEST_MODEL_ID
        assert len(ei.value.attempted) == 3
    finally:
        for n, w in zip(FAKE_PROVIDERS, FAKE_WIRE):
            preg._REGISTRY[n] = (lambda mid=None, _w=w, _n=n: FakeSuccessProvider(_n, mid or _w))


def test_import_no_network_no_credentials(monkeypatch):
    monkeypatch.delenv("ALIBABA_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("CODECRAFT_API_KEY", raising=False)
    # Execute a fresh copy of the module source under an ALIAS name instead of
    # sys.modules.pop() + re-import: replacing the session module object leaks
    # a second identity, so later tests that patch/except
    # app.content.model_resolver.<name> would act on a different object.
    reference = importlib.import_module("app.content.model_resolver")
    spec = importlib.util.spec_from_file_location(
        "avm_fresh_model_resolver", reference.__file__
    )
    mod = importlib.util.module_from_spec(spec)
    alias = "avm_fresh_model_resolver"
    sys.modules[alias] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        if sys.modules.get(alias) is mod:
            del sys.modules[alias]
    assert hasattr(mod, "resolve_model")
    import pathlib
    text = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    top = text.split("def resolve_model")[0]
    assert "requests." not in top


def test_unsupported_mode_rejected():
    from app.content.model_resolver import resolve_model
    with pytest.raises(ValueError, match="unsupported"):
        resolve_model(TEST_MODEL_ID, mode="auto")
    with pytest.raises(ValueError, match="unsupported"):
        resolve_model(TEST_MODEL_ID, mode="avm_auto")


def test_empty_id_rejected():
    from app.content.model_resolver import resolve_model
    with pytest.raises(ValueError):
        resolve_model("")
    with pytest.raises(ValueError):
        resolve_model("   ")


def test_existing_provider_first_behavior_unchanged():
    import pathlib
    text = pathlib.Path("C:/Projects/AUTO-VIDEO-MAKER/app/content/providers/__init__.py").read_text(encoding="utf-8")
    assert "model_resolver" not in text
    assert "CanonicalModel" not in text


def test_existing_fallback_unchanged():
    import pathlib
    text = pathlib.Path("C:/Projects/AUTO-VIDEO-MAKER/app/content/providers/fallback.py").read_text(encoding="utf-8")
    assert "model_resolver" not in text


def test_model_registry_separation():
    import pathlib
    t = pathlib.Path("C:/Projects/AUTO-VIDEO-MAKER/app/content/model_registry.py").read_text(encoding="utf-8")
    assert "model_resolver" not in t.split("__all__")[0]
