"""
TRACK 9 -- PROVIDER-NEUTRAL CANONICAL MODEL ROUTING

Freezes the contract:

    USER SELECTS MODEL (canonical identity)
    AVM SELECTS PROVIDER (internal execution detail)
    PROVIDER FALLBACK MAY CHANGE PROVIDER
    PROVIDER FALLBACK MUST NEVER CHANGE MODEL

No network, no real provider credentials required.
"""
from __future__ import annotations

import pytest

from app.content.model_registry import (
    CanonicalModel,
    MODEL_REGISTRY,
    ModelCapabilities,
    ProviderBinding,
    deprecated_aliases_for,
    get_model,
    has_model,
    has_model_including_alias,
    is_deprecated_alias,
    list_models,
    list_models_for_pro,
    list_pro_model_ids,
    model_drawer_projection,
)
from app.content.model_resolver import (
    AllExactModelProvidersFailedError,
    resolve_model,
)
from app.production.narration import (
    SUMMARY_PRO_MODEL,
    SUMMARY_STANDARD_MODEL,
    resolve_summary_model,
)

PROVIDER_NAMES = ("gemini", "alibaba", "openrouter", "codecraft", "mock")


class _StubProvider:
    """Minimal provider stub: identity + availability only."""

    def __init__(self, name_val, model_val, available):
        self._name, self._model, self._available = name_val, model_val, available

    @property
    def name(self):
        return self._name

    @property
    def model(self):
        return self._model

    def generate(self, *a, **kw):
        from app.content.providers.base import LLMResponse

        return LLMResponse(content="{}", provider=self._name, model=self._model)

    def is_available(self):
        return self._available

    def supports_multimodal(self):
        return True


# --- 1. Summary Pro has no hardcoded Gemini owner -------------------------

def test_1_summary_pro_has_no_hardcoded_gemini_owner():
    """Pro eligibility must come from capability, never from a Gemini constant."""
    import inspect
    import app.production.narration as narr

    src = inspect.getsource(narr.resolve_summary_model)
    # Strip comments/docstring prose: only EXECUTABLE code must be provider-free.
    code_lines = [
        line.split("#", 1)[0]
        for line in src.splitlines()
        if not line.strip().startswith("#")
    ]
    code = "\n".join(code_lines).lower()
    # The resolver must not name a provider or a gemini model id in executable code.
    assert "gemini" not in code
    assert "alibaba" not in code
    assert "openrouter" not in code
    assert "summary-pro-" not in code
    # Pro discovery is capability-driven
    assert "list_models_for_pro" in code or "is_multimodal_capable" in code
    # Removing gemini from the registry must NOT break Pro resolution.
    removed = MODEL_REGISTRY.pop("gemini-3-flash-preview")
    try:
        mid = resolve_summary_model("pro", selection_mode="auto")
        assert mid != "gemini-3-flash-preview"
        assert get_model(mid).capabilities.is_multimodal_capable()
    finally:
        MODEL_REGISTRY["gemini-3-flash-preview"] = removed


# --- 2. Eligible models discovered by capability --------------------------

def test_2_eligible_models_discovered_by_capability():
    eligible = list_models_for_pro()
    assert eligible, "at least one multimodal-capable canonical model required"
    for m in eligible:
        assert m.capabilities.is_multimodal_capable() is True
    # Text-only models are excluded by capability, not by name
    assert "qwen-3.8-flash" not in list_pro_model_ids()
    # A brand-new multimodal model (no 'summary' in the name) is discovered
    new_id = "test-neutral-capability-model"
    MODEL_REGISTRY[new_id] = CanonicalModel(
        id=new_id,
        display_name="Neutral Capability Model",
        family="test",
        capabilities=ModelCapabilities(
            text=True, json_=True, vision=True, video=True, multimodal=True
        ),
        bindings=(ProviderBinding(provider="mock", provider_model_id="wire-neutral"),),
        default_binding="mock",
    )
    try:
        assert new_id in list_pro_model_ids()
    finally:
        MODEL_REGISTRY.pop(new_id, None)


# --- 3. Canonical model identity is separate from provider identity -------

def test_3_canonical_identity_separate_from_provider_identity():
    for m in list_models():
        assert m.id not in PROVIDER_NAMES, "canonical id must never be a provider name"
        for b in m.bindings:
            assert m.id != b.provider
            assert b.provider != m.display_name


# --- 4. A canonical model may have multiple provider bindings -------------

def test_4_canonical_model_may_have_multiple_provider_bindings():
    """The SCHEMA supports multiple bindings per canonical model.

    TRACK 9 TASK A2 UPDATE: the registry no longer SHIPS a multi-binding model,
    because every previously-registered second binding was a cross-model
    violation and was removed:

      * gemini-3-flash-preview -> alibaba/qwen-vl-plus  (Alibaba != Google)
      * gpt-4o                 -> alibaba/qwen-vl-plus  (Alibaba != OpenAI)
      * qwen-vl-max            -> openrouter/qwen2.5-vl-72b-instruct
                                  (a DIFFERENT model; verified absent from
                                   OpenRouter's live model list)

    Per the task rule "one verified provider binding is better than several
    misleading bindings", no substitute binding was invented. This test now
    proves the SCHEMA still supports multi-binding models and that uniqueness
    is enforced, without requiring a fabricated binding to exist.
    """
    # Schema-level: a multi-binding canonical model is constructible and
    # enforces per-provider uniqueness.
    multi = CanonicalModel(
        id="test-neutral-multi-binding",
        display_name="Multi Binding",
        family="test",
        capabilities=ModelCapabilities(text=True, json_=True, vision=True, video=True, multimodal=True),
        bindings=(
            ProviderBinding(provider="mock", provider_model_id="wire-1"),
            ProviderBinding(provider="gemini", provider_model_id="wire-2"),
        ),
        default_binding="mock",
    )
    providers = [b.provider for b in multi.bindings]
    assert len(providers) == len(set(providers)), "bindings unique per provider"
    with pytest.raises(Exception):
        CanonicalModel(
            id="test-neutral-dupe-binding",
            display_name="Dupe",
            family="test",
            capabilities=ModelCapabilities(text=True),
            bindings=(
                ProviderBinding(provider="mock", provider_model_id="a"),
                ProviderBinding(provider="mock", provider_model_id="b"),
            ),
        )

    # Registry-level: every SHIPPED binding is unique per provider, and
    # qwen-vl-max now has exactly its ONE verified first-party route.
    for m in list_models():
        shipped = [b.provider for b in m.bindings]
        assert len(shipped) == len(set(shipped)), f"{m.id} has duplicate providers"
    qwen_vl = get_model("qwen-vl-max")
    assert {b.provider for b in qwen_vl.bindings} == {"alibaba"}
    assert "openrouter" not in {b.provider for b in qwen_vl.bindings}, (
        "qwen-vl-max must not route to OpenRouter's different Qwen2.5-VL model"
    )


# --- 5. Explicit model selection resolves the exact canonical model -------

def test_5_explicit_selection_resolves_exact_model():
    for mid in list_pro_model_ids():
        assert resolve_summary_model("pro", preferred_model_id=mid, selection_mode="explicit") == mid
        assert resolve_summary_model("pro", summary_understanding_model_id=mid, selection_mode="manual") == mid


# --- 6. Provider fallback stays within the selected canonical model -------

def test_6_provider_fallback_stays_within_selected_model():
    import app.content.provider_registry as preg

    model_id = "test-neutral-same-model-fallback"
    MODEL_REGISTRY[model_id] = CanonicalModel(
        id=model_id,
        display_name="Same Model Fallback",
        family="test",
        capabilities=get_model(SUMMARY_PRO_MODEL).capabilities,
        bindings=(
            ProviderBinding(provider="t-neutral-down", provider_model_id="wire-down"),
            ProviderBinding(provider="t-neutral-up", provider_model_id="wire-up"),
        ),
        default_binding="t-neutral-down",
    )
    preg.register_provider("t-neutral-down", lambda mid=None: _StubProvider("t-neutral-down", mid or "wire-down", False))
    preg.register_provider("t-neutral-up", lambda mid=None: _StubProvider("t-neutral-up", mid or "wire-up", True))
    try:
        resolved = resolve_model(model_id, mode="exact", try_next_on_failure=True)
        # SAME MODEL, DIFFERENT PROVIDER
        assert resolved.canonical_model_id == model_id
        assert resolved.provider == "t-neutral-up"
        assert resolved.provider_model_id == "wire-up"
        # The unavailable provider was genuinely attempted first
        assert [a["provider"] for a in resolved.attempted] == ["t-neutral-down"]
    finally:
        MODEL_REGISTRY.pop(model_id, None)
        preg._REGISTRY.pop("t-neutral-down", None)
        preg._REGISTRY.pop("t-neutral-up", None)


# --- 7. Cross-model fallback is forbidden ---------------------------------

def test_7_cross_model_fallback_forbidden():
    import app.content.provider_registry as preg

    dead_id = "test-neutral-cross-dead"
    alive_id = "test-neutral-cross-alive"
    caps = get_model(SUMMARY_PRO_MODEL).capabilities
    MODEL_REGISTRY[dead_id] = CanonicalModel(
        id=dead_id, display_name="Cross Dead", family="test", capabilities=caps,
        bindings=(ProviderBinding(provider="t-cross-dead", provider_model_id="wire-dead"),),
        default_binding="t-cross-dead",
    )
    MODEL_REGISTRY[alive_id] = CanonicalModel(
        id=alive_id, display_name="Cross Alive", family="test", capabilities=caps,
        bindings=(ProviderBinding(provider="t-cross-alive", provider_model_id="wire-alive"),),
        default_binding="t-cross-alive",
    )
    preg.register_provider("t-cross-dead", lambda mid=None: _StubProvider("t-cross-dead", mid or "wire-dead", False))
    preg.register_provider("t-cross-alive", lambda mid=None: _StubProvider("t-cross-alive", mid or "wire-alive", True))
    try:
        # Every provider of the SELECTED model failed -> fail truthfully.
        # It must NEVER silently resolve the other (healthy) canonical model.
        with pytest.raises(AllExactModelProvidersFailedError) as exc:
            resolve_model(dead_id, mode="exact", try_next_on_failure=True)
        assert exc.value.canonical_model_id == dead_id
        assert all(a["provider"] != "t-cross-alive" for a in exc.value.attempted)
    finally:
        MODEL_REGISTRY.pop(dead_id, None)
        MODEL_REGISTRY.pop(alive_id, None)
        preg._REGISTRY.pop("t-cross-dead", None)
        preg._REGISTRY.pop("t-cross-alive", None)


# --- 8/9. Model selection precedes provider routing ----------------------

def test_8_auto_selects_canonical_model_before_provider_routing():
    """AUTO must pick a model with zero provider knowledge."""
    import app.content.provider_registry as preg

    real_registry = dict(preg._REGISTRY)
    preg._REGISTRY.clear()
    try:
        # No providers registered at all -> model selection still succeeds.
        mid = resolve_summary_model("pro", selection_mode="auto")
        assert mid in list_pro_model_ids()
    finally:
        preg._REGISTRY.update(real_registry)


def test_9_provider_routing_occurs_after_canonical_model_selection():
    mid = resolve_summary_model("pro", preferred_model_id="qwen-vl-max", selection_mode="explicit")
    assert mid == "qwen-vl-max"
    resolved = resolve_model(mid, mode="exact", try_next_on_failure=False)
    # Provider is chosen from THIS model's bindings only
    assert resolved.canonical_model_id == "qwen-vl-max"
    assert resolved.provider in {b.provider for b in get_model(mid).bindings}


# --- 10. Gemini is not structurally privileged ----------------------------

def test_10_gemini_not_structurally_privileged():
    gem = get_model("gemini-3-flash-preview")
    others = [m for m in list_models_for_pro() if m.id != gem.id]
    assert others, "Pro must have non-Gemini eligible models"
    for m in others:
        assert m.capabilities.is_multimodal_capable() == gem.capabilities.is_multimodal_capable()
    providers_in_catalogue = {b.provider for m in list_models_for_pro() for b in m.bindings}
    assert "gemini" in providers_in_catalogue
    assert providers_in_catalogue - {"gemini"}, "non-Gemini providers must exist"
    # Explicitly selecting a non-Gemini model never yields a gemini binding
    resolved = resolve_model("qwen-vl-max", mode="exact", try_next_on_failure=False)
    assert resolved.provider != "gemini"



# --- 11/12. CodeCraft truthful capability --------------------------------

def test_11_codecraft_binding_recognized_where_genuinely_supported():
    """CodeCraft genuinely executes text-only qwen3.8-27b -- that binding is real."""
    from app.content.providers.codecraft import CodeCraftProvider

    brain = get_model("qwen-3.8-flash")
    cc = [b for b in brain.bindings if b.provider == "codecraft"]
    assert len(cc) == 1
    assert cc[0].provider_model_id == "qwen3.8-27b"
    provider = CodeCraftProvider(api_key="sk-test-not-used")
    assert provider.name == "codecraft"
    assert provider.model == "qwen3.8-27b"
    assert hasattr(provider, "generate")


def test_12_unsupported_codecraft_multimodal_capability_rejected_truthfully():
    """CodeCraft image capability is MODEL-SPECIFIC, and never fabricated.

    TRACK 9 TASK C UPDATE (live evidence, not inference):

    * CodeCraft image input IS REAL for specific models -- a frame from the real
      source film was described accurately. The old blanket "text-only" verdict
      was an ADAPTER limitation, now corrected.
    * The capability is NOT blanket-true: the registered text model
      'qwen3.8-27b' is declared text->text by CodeCraft's own /models metadata
      and is still refused truthfully.
    * Native video is NOT claimed.
    * CodeCraft is still bound to NO Pro (multimodal) canonical model, because
      per-model identity on that gateway is unverifiable.
    """
    from app.content.providers.codecraft import CodeCraftProvider

    # The registered CodeCraft text model stays text-only and is refused.
    provider = CodeCraftProvider(api_key="sk-test-not-used")
    assert provider.model == "qwen3.8-27b"
    assert provider.supports_multimodal() is False
    assert provider.vision_capability_source() is None
    with pytest.raises(Exception) as exc_info:
        provider.generate_multimodal("probe", video=None, images=None)
    assert "not image-capable" in str(exc_info.value)

    # A verified vision model DOES advertise image input, but never native video.
    vision = CodeCraftProvider(api_key="sk-test-not-used", model="gpt-5.6-sol")
    assert vision.supports_multimodal() is True
    assert vision.vision_capability_source() == "verified_real_request"
    assert vision.supports_native_video() is False

    # TRACK 9 CodeCraft integration UPDATE: CodeCraft may now back a canonical
    # model whose route is IMAGE/FRAME-BASED (claude-opus-5.5, verified live).
    # The enduring invariant is narrower and stricter: CodeCraft must never be
    # a binding for a canonical model claiming NATIVE VIDEO ingestion, and any
    # CodeCraft binding must carry non-verified identity with cited evidence.
    from app.content.model_registry import IdentityVerification

    codecraft_backed = [
        m for m in list_models_for_pro()
        if "codecraft" in {b.provider for b in m.bindings}
    ]
    for m in codecraft_backed:
        assert m.capabilities.native_video_input is False, (
            f"{m.id} must not claim native video on a CodeCraft route"
        )
        assert m.capabilities.image_input is True
        binding = next(b for b in m.bindings if b.provider == "codecraft")
        # Identity is declared, never independently attested.
        assert binding.identity_verification == IdentityVerification.DECLARED
        assert (binding.identity_evidence or "").strip()
        # The canonical id must not leak the provider name.
        assert "codecraft" not in m.id.lower()


# --- Deprecated aliases are compat-only, never active identities ----------

def test_deprecated_aliases_are_compat_only():
    for alias, true_id in (
        ("summary-pro-gemini-3-flash-preview", "gemini-3-flash-preview"),
        ("summary-pro-gemini-2.0-flash", "gemini-3-flash-preview"),
        ("summary-pro-qwen-vl-max", "qwen-vl-max"),
        ("summary-pro-gpt4o", "gpt-4o"),
        ("summary-standard-qwen-flash", "qwen-3.8-flash"),
    ):
        assert is_deprecated_alias(alias) is True
        assert has_model(alias) is False, "alias must not be an active identity"
        assert has_model_including_alias(alias) is True
        assert get_model(alias).id == true_id
        assert alias not in [m.id for m in list_models()]
    assert "summary-pro-gemini-3-flash-preview" in deprecated_aliases_for("gemini-3-flash-preview")


# --- Model drawer Core contract hides providers ---------------------------

def test_model_drawer_contract_hides_provider_identity():
    payload = model_drawer_projection()
    assert payload["capability"] == "multimodal_video_understanding"
    assert payload["models"], "drawer must expose at least one canonical model"
    serialized = repr(payload).lower()
    for provider in ("alibaba", "openrouter", "codecraft"):
        assert provider not in serialized, f"provider '{provider}' leaked into drawer contract"
    # TASK D: no API keys / config key names may leak either.
    for secret_key in ("api_key", "_api_key", "gemini_api_key", "config_keys"):
        assert secret_key not in serialized, f"'{secret_key}' leaked into drawer contract"
    for entry in payload["models"]:
        assert set(entry) == {
            "id", "display_name", "family", "capabilities", "context_tokens",
            "eligible_for_job", "video_understanding_method",
            "cost_estimate", "cost_basis",
        }
        assert "provider" not in entry
        assert "bindings" not in entry
        assert entry["capabilities"]["multimodal"] is True
        # Image input and native video are reported DISTINCTLY.
        assert "image_input" in entry["capabilities"]
        assert "native_video_input" in entry["capabilities"]
        assert entry["video_understanding_method"] in ("native_video", "frame_sequence")
        # No fabricated cost claim.
        assert entry["cost_estimate"] is None
    assert payload["cost_optimized"] is False
    assert payload["ranking_basis"] == "capability_rank"


# --- Standard behavior remains intact ------------------------------------

def test_standard_behavior_remains_intact():
    assert SUMMARY_STANDARD_MODEL == "qwen-3.8-flash"
    mid = resolve_summary_model("standard")
    assert get_model(mid).capabilities.text is True
    assert get_model(mid).capabilities.is_multimodal_capable() is False
    # Non-multimodal model is rejected for Pro, truthfully
    with pytest.raises(ValueError, match="not multimodal-capable"):
        resolve_summary_model("pro", preferred_model_id=SUMMARY_STANDARD_MODEL, selection_mode="explicit")


        return True
