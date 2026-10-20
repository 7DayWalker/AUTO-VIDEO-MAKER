"""
AVM Step 5 -- Fallback validation (synthetic model, offline).
"""

from __future__ import annotations

import pytest

from app.content.providers.base import BaseLLMProvider, LLMResponse
from app.content.exceptions import LLMAuthenticationError

class FakeSuccess(BaseLLMProvider):
    def __init__(self, n, m):
        self._n = n; self._m = m
    @property
    def name(self): return self._n
    @property
    def model(self): return self._m
    def generate(self, *a, **kw): return LLMResponse(content="{}", provider=self._n, model=self._m)
    def is_available(self): return True

class FakeUnavailable(BaseLLMProvider):
    def __init__(self, n, m):
        self._n = n; self._m = m
    @property
    def name(self): return self._n
    @property
    def model(self): return self._m
    def generate(self, *a, **kw): return LLMResponse(content="{}", provider=self._n, model=self._m)
    def is_available(self): return False

TEST_SYN_ID = "synthetic-qwen-fallback-test"
FAKE_PROVIDERS = ("syn-fallback-a", "syn-fallback-b", "syn-fallback-c")
FAKE_WIRE = ("syn-wire-a", "syn-wire-b", "syn-wire-c")

@pytest.fixture(autouse=True)
def _syn_model():
    import app.content.provider_registry as preg
    from app.content.model_registry import CanonicalModel, ProviderBinding, MODEL_REGISTRY
    factories = {
        "syn-fallback-a": lambda mid=None: FakeSuccess("syn-fallback-a", mid or "syn-wire-a"),
        "syn-fallback-b": lambda mid=None: FakeSuccess("syn-fallback-b", mid or "syn-wire-b"),
        "syn-fallback-c": lambda mid=None: FakeSuccess("syn-fallback-c", mid or "syn-wire-c"),
    }
    saved = {}
    for n, f in factories.items():
        if preg.has_provider(n):
            saved[n] = preg._REGISTRY[n]
            preg._REGISTRY.pop(n, None)
        preg.register_provider(n, f)
    bindings = tuple(ProviderBinding(provider=n, provider_model_id=w) for n, w in zip(FAKE_PROVIDERS, FAKE_WIRE))
    model = CanonicalModel(id=TEST_SYN_ID, display_name="Synthetic Qwen Fallback", family="test", bindings=bindings)
    MODEL_REGISTRY[TEST_SYN_ID] = model
    yield
    MODEL_REGISTRY.pop(TEST_SYN_ID, None)
    for n in list(factories):
        preg._REGISTRY.pop(n, None)
        if n in saved:
            preg._REGISTRY[n] = saved[n]

def test_exact_mode_single_provider_no_cross():
    from app.content.model_resolver import resolve_model
    r = resolve_model(TEST_SYN_ID)
    assert r.canonical_model_id == TEST_SYN_ID
    assert r.provider == "syn-fallback-a"

def test_eligible_unavailable_advances():
    import app.content.provider_registry as preg
    from app.content.model_resolver import resolve_model
    preg._REGISTRY["syn-fallback-a"] = lambda mid=None: FakeUnavailable("syn-fallback-a", mid or FAKE_WIRE[0])
    r = resolve_model(TEST_SYN_ID, try_next_on_failure=True)
    assert r.provider == "syn-fallback-b"

def test_eligible_quota_advances():
    import app.content.provider_registry as preg
    from app.content.model_resolver import resolve_model
    from app.content.providers.codecraft import CodeCraftQuotaError
    def _quota(mid=None):
        raise CodeCraftQuotaError("syn-fallback-a")
    saved = preg._REGISTRY["syn-fallback-a"]
    preg._REGISTRY["syn-fallback-a"] = _quota
    try:
        r = resolve_model(TEST_SYN_ID, try_next_on_failure=True)
        assert r.provider == "syn-fallback-b"
    finally:
        preg._REGISTRY["syn-fallback-a"] = saved

def test_non_eligible_auth_stops():
    import app.content.provider_registry as preg
    from app.content.model_resolver import resolve_model, AllExactModelProvidersFailedError
    def _auth(mid=None):
        raise LLMAuthenticationError("syn-fallback-a")
    saved = preg._REGISTRY["syn-fallback-a"]
    preg._REGISTRY["syn-fallback-a"] = _auth
    try:
        with pytest.raises(AllExactModelProvidersFailedError):
            resolve_model(TEST_SYN_ID, try_next_on_failure=True)
    finally:
        preg._REGISTRY["syn-fallback-a"] = saved

def test_non_eligible_invalid_request_stops():
    import app.content.provider_registry as preg
    from app.content.model_resolver import resolve_model, AllExactModelProvidersFailedError
    from app.content.providers.codecraft import CodeCraftInvalidRequestError
    def _inv(mid=None):
        raise CodeCraftInvalidRequestError("bad request")
    saved = preg._REGISTRY["syn-fallback-a"]
    preg._REGISTRY["syn-fallback-a"] = _inv
    try:
        with pytest.raises(AllExactModelProvidersFailedError) as ei:
            resolve_model(TEST_SYN_ID, try_next_on_failure=True)
        assert len(ei.value.attempted) == 1
    finally:
        preg._REGISTRY["syn-fallback-a"] = saved

def test_eligible_malformed_advances():
    # RESPONSE_MALFORMED is fallback-eligible per fallback_policy.FALLBACK_ELIGIBLE_CATEGORIES
    import app.content.provider_registry as preg
    from app.content.model_resolver import resolve_model
    from app.content.providers.codecraft import CodeCraftResponseError
    def _mal(mid=None):
        raise CodeCraftResponseError("malformed")
    saved = preg._REGISTRY["syn-fallback-a"]
    preg._REGISTRY["syn-fallback-a"] = _mal
    try:
        r = resolve_model(TEST_SYN_ID, try_next_on_failure=True)
        assert r.provider == "syn-fallback-b"
    finally:
        preg._REGISTRY["syn-fallback-a"] = saved

def test_non_eligible_configuration_stops():
    import app.content.provider_registry as preg
    from app.content.model_resolver import resolve_model, AllExactModelProvidersFailedError
    from app.content.exceptions import LLMProviderError
    from app.providers.fallback_policy import CategoryMixin, FailureCategory
    class _ConfigErr(LLMProviderError, CategoryMixin):
        category = FailureCategory.CONFIGURATION
    def _cfg(mid=None):
        raise _ConfigErr("bad config")
    saved = preg._REGISTRY["syn-fallback-a"]
    preg._REGISTRY["syn-fallback-a"] = _cfg
    try:
        with pytest.raises(AllExactModelProvidersFailedError) as ei:
            resolve_model(TEST_SYN_ID, try_next_on_failure=True)
        assert len(ei.value.attempted) == 1
    finally:
        preg._REGISTRY["syn-fallback-a"] = saved

def test_deterministic_order():
    from app.content.model_registry import get_model
    assert tuple(b.provider for b in get_model(TEST_SYN_ID).bindings) == FAKE_PROVIDERS

def test_successful_second_provider():
    import app.content.provider_registry as preg
    from app.content.model_resolver import resolve_model
    preg._REGISTRY["syn-fallback-a"] = lambda mid=None: FakeUnavailable("syn-fallback-a", mid or FAKE_WIRE[0])
    r = resolve_model(TEST_SYN_ID, try_next_on_failure=True)
    assert r.provider == "syn-fallback-b" and r.provider_model_id == "syn-wire-b"

def test_successful_third_provider():
    import app.content.provider_registry as preg
    from app.content.model_resolver import resolve_model
    preg._REGISTRY["syn-fallback-a"] = lambda mid=None: FakeUnavailable("syn-fallback-a", mid or FAKE_WIRE[0])
    preg._REGISTRY["syn-fallback-b"] = lambda mid=None: FakeUnavailable("syn-fallback-b", mid or FAKE_WIRE[1])
    r = resolve_model(TEST_SYN_ID, try_next_on_failure=True)
    assert r.provider == "syn-fallback-c"

def test_all_providers_exhausted():
    import app.content.provider_registry as preg
    from app.content.model_resolver import resolve_model, AllExactModelProvidersFailedError
    for n in FAKE_PROVIDERS:
        preg._REGISTRY[n] = lambda mid=None, _n=n: FakeUnavailable(_n, mid or "wire")
    with pytest.raises(AllExactModelProvidersFailedError) as ei:
        resolve_model(TEST_SYN_ID, try_next_on_failure=True)
    assert ei.value.canonical_model_id == TEST_SYN_ID
    assert len(ei.value.attempted) == 3

def test_attempt_trace():
    import app.content.provider_registry as preg
    from app.content.model_resolver import resolve_model
    preg._REGISTRY["syn-fallback-a"] = lambda mid=None: FakeUnavailable("syn-fallback-a", mid or FAKE_WIRE[0])
    r = resolve_model(TEST_SYN_ID, try_next_on_failure=True)
    assert len(r.attempted) == 1 and r.attempted[0]["provider"] == "syn-fallback-a"

def test_canonical_identity_preserved():
    import app.content.provider_registry as preg
    from app.content.model_resolver import resolve_model
    preg._REGISTRY["syn-fallback-a"] = lambda mid=None: FakeUnavailable("syn-fallback-a", mid or FAKE_WIRE[0])
    r = resolve_model(TEST_SYN_ID, try_next_on_failure=True)
    assert r.canonical_model_id == TEST_SYN_ID

def test_current_qwen_has_only_codecraft():
    from app.content.model_registry import get_model
    m = get_model("qwen-3.8-flash")
    assert len(m.bindings) == 1 and m.bindings[0].provider == "codecraft"

def test_current_qwen_exhaustion_no_other_model():
    from app.content.model_resolver import resolve_model, AllExactModelProvidersFailedError
    from app.content.model_registry import CanonicalModel, ProviderBinding, MODEL_REGISTRY
    other = CanonicalModel(id="step5-other-model", display_name="Other", family="other", bindings=(ProviderBinding(provider="syn-fallback-a", provider_model_id="other-wire"),))
    MODEL_REGISTRY[other.id] = other
    import app.content.provider_registry as preg
    real_cf = preg._REGISTRY.get("codecraft")
    class _Unavail:
        def __init__(self, *a, **kw): self._m = "qwen3.8-27b"
        @property
        def name(self): return "codecraft"
        @property
        def model(self): return self._m
        def is_available(self): return False
    preg._REGISTRY["codecraft"] = lambda mid=None: _Unavail()
    try:
        with pytest.raises(AllExactModelProvidersFailedError) as ei:
            resolve_model("qwen-3.8-flash", try_next_on_failure=True)
        assert all(a["provider"] == "codecraft" for a in ei.value.attempted)
    finally:
        if real_cf:
            preg._REGISTRY["codecraft"] = real_cf
        MODEL_REGISTRY.pop(other.id, None)

def test_existing_global_fallback_unchanged():
    import pathlib
    text = pathlib.Path("C:/Projects/AUTO-VIDEO-MAKER/app/content/providers/fallback.py").read_text(encoding="utf-8")
    assert "model_resolver" not in text
    assert "CanonicalModel" not in text
    assert "Alibaba" in text and "OpenRouter" in text

def test_no_real_network_required():
    from app.content.model_resolver import resolve_model
    r = resolve_model(TEST_SYN_ID)
    assert r.provider == "syn-fallback-a"
