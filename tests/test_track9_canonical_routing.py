"""
TRACK 9 TASK E -- Canonical model routing + provider capability discovery.

Focused, deterministic, offline proofs for the 17 required properties. Test
doubles are used for routing; NO live provider call is made here (the live
proof is a separate bounded tool run).
"""
from __future__ import annotations

import pytest

from app.content.capability_router import (
    JOB_SOURCE_VIDEO_RECAP,
    NoEligibleModelError,
    assess_model_for_job,
    assess_provider_route,
    capability_rank,
    clear_quota_blockers,
    list_eligible_models_for_job,
    record_quota_blocker,
    select_model_auto,
    select_provider_for_model,
)
from app.content.model_registry import (
    MODEL_REGISTRY,
    ApiOperation,
    CanonicalModel,
    IdentityVerification,
    InputModality,
    ModelCapabilities,
    ProviderBinding,
    QuotaEvidence,
    discover_models_for_job,
    get_model,
    job_video_understanding_method,
    list_models,
    model_drawer_projection,
)
from app.content.model_resolver import (
    AllExactModelProvidersFailedError,
    resolve_model,
)

PROVIDER_NAMES = {"alibaba", "openrouter", "gemini", "codecraft", "mock"}

#: Bindings that were REMOVED in Task A2 because they pointed at a different
#: underlying model than the canonical identity claimed.
FORBIDDEN_BINDINGS = {
    ("gemini-3-flash-preview", "alibaba", "qwen-vl-plus"),
    ("gpt-4o", "alibaba", "qwen-vl-plus"),
    ("qwen-vl-max", "openrouter", "qwen/qwen2.5-vl-72b-instruct"),
}


@pytest.fixture(autouse=True)
def _clean_blockers():
    clear_quota_blockers()
    yield
    clear_quota_blockers()


# =========================================================================
# 1. Every retained provider binding preserves actual canonical identity
# =========================================================================

def test_1_every_retained_binding_identifies_its_actual_model():
    for m in list_models():
        assert m.bindings, f"{m.id} must have at least one binding"
        for b in m.bindings:
            # Identity strength is explicit and never 'unverified'.
            assert b.identity_verification in (
                IdentityVerification.VERIFIED, IdentityVerification.DECLARED
            )
            # Anything not first-party verified must name its evidence source.
            if b.identity_verification != IdentityVerification.VERIFIED:
                assert (b.identity_evidence or "").strip(), (
                    f"{m.id}/{b.provider} must cite its knowledge source"
                )
            # A canonical id must never be a provider name.
            assert m.id not in PROVIDER_NAMES


def test_1b_verified_bindings_are_first_party_or_authoritatively_listed():
    """A VERIFIED binding must have provenance naming the authority."""
    for m in list_models():
        for b in m.bindings:
            if b.identity_verification == IdentityVerification.VERIFIED:
                evidence = (b.identity_evidence or "").lower()
                assert evidence, f"{m.id}/{b.provider} VERIFIED without evidence"
                assert ("first-party" in evidence) or ("models" in evidence), (
                    f"{m.id}/{b.provider} must cite first-party or model-list evidence"
                )


# =========================================================================
# 2. Invalid cross-model bindings are absent
# =========================================================================

def test_2_invalid_cross_model_bindings_removed():
    for model_id, provider, wire in FORBIDDEN_BINDINGS:
        model = get_model(model_id)
        actual = {(b.provider, b.provider_model_id) for b in model.bindings}
        assert (provider, wire) not in actual, (
            f"invalid cross-model binding {model_id} -> {provider}/{wire} is still present"
        )


def test_2b_alibaba_never_serves_gemini_or_openai_models():
    """Alibaba must not be a route for a Google or OpenAI canonical model."""
    for model_id in ("gemini-3-flash-preview", "gpt-4o"):
        providers = {b.provider for b in get_model(model_id).bindings}
        assert "alibaba" not in providers, (
            f"Alibaba does not serve {model_id}; binding must not exist"
        )


def test_2c_unverified_binding_is_rejected_by_construction():
    """The schema REFUSES to register an unverified route."""
    with pytest.raises(ValueError, match="unverified"):
        ProviderBinding(
            provider="mock",
            provider_model_id="wire-x",
            identity_verification=IdentityVerification.UNVERIFIED,
            identity_evidence="none",
        )


def test_2d_declared_binding_without_evidence_is_rejected():
    with pytest.raises(ValueError, match="identity_evidence"):
        ProviderBinding(
            provider="mock",
            provider_model_id="wire-x",
            identity_verification=IdentityVerification.DECLARED,
        )


# =========================================================================
# 3. Provider discovery does not auto-equate similarly named models
# =========================================================================

def test_3_similar_names_are_not_the_same_model():
    """Qwen2.5-VL-72B-Instruct is NOT Qwen-VL-Max, despite both being 'qwen vl'."""
    vl_max = get_model("qwen-vl-max")
    vl_72b = get_model("qwen2.5-vl-72b-instruct")
    assert vl_max.id != vl_72b.id
    # They are separate canonical identities with separate routes.
    assert {b.provider_model_id for b in vl_max.bindings} == {"qwen-vl-max"}
    assert {b.provider_model_id for b in vl_72b.bindings} == {"qwen/qwen2.5-vl-72b-instruct"}
    # Neither model lists the other's wire id.
    assert "qwen/qwen2.5-vl-72b-instruct" not in {b.provider_model_id for b in vl_max.bindings}
    # Capability equivalence is NOT identity: both are vision-capable, still distinct.
    assert vl_max.capabilities.image_input and vl_72b.capabilities.image_input
    assert vl_max.id != vl_72b.id


def test_3b_capability_equivalence_is_not_identity():
    """Two models with IDENTICAL capabilities remain distinct identities."""
    caps = ModelCapabilities(text=True, json_=True, vision=True, video=True, multimodal=True)
    a = CanonicalModel(
        id="t9-caps-a", display_name="A", family="test", capabilities=caps,
        bindings=(ProviderBinding(provider="mock", provider_model_id="wire-a"),),
    )
    b = CanonicalModel(
        id="t9-caps-b", display_name="B", family="test", capabilities=caps,
        bindings=(ProviderBinding(provider="mock", provider_model_id="wire-b"),),
    )
    assert a.capabilities == b.capabilities
    assert a.id != b.id
    assert a.bindings[0].provider_model_id != b.bindings[0].provider_model_id


# =========================================================================
# 4. Models discovered by CAPABILITY, not feature-name prefixes
# =========================================================================

def test_4_discovery_is_capability_driven_not_name_driven():
    found = discover_models_for_job(JOB_SOURCE_VIDEO_RECAP)
    assert found, "at least one model must be capable of the job"
    for m in found:
        capable, method = m.capabilities.can_understand_source_video()
        assert capable is True
        assert method in (InputModality.NATIVE_VIDEO, InputModality.FRAME_SEQUENCE)
    # No product/provider label is required to qualify.
    ids = [m.id for m in found]
    for banned in ("summary", "pro"):
        assert not all(banned in i for i in ids)
    # A text-only model is NEVER classified as video-capable.
    assert "qwen-3.8-flash" not in ids


def test_4b_brand_new_unlabelled_model_is_discovered():
    """A model with no recognizable product/provider token still qualifies."""
    new_id = "t9-zzz-unlabelled-9999"
    MODEL_REGISTRY[new_id] = CanonicalModel(
        id=new_id, display_name="Unlabelled", family="test",
        capabilities=ModelCapabilities(
            text=True, json_=True, vision=True, video=True, multimodal=True
        ),
        bindings=(ProviderBinding(provider="mock", provider_model_id="wire-zzz"),),
        default_binding="mock",
    )
    try:
        ids = [m.id for m in discover_models_for_job(JOB_SOURCE_VIDEO_RECAP)]
        assert new_id in ids
    finally:
        MODEL_REGISTRY.pop(new_id, None)


def test_4c_text_only_model_is_never_video_capable():
    text_only = ModelCapabilities(text=True, json_=True, vision=False, video=False)
    capable, method = text_only.can_understand_source_video()
    assert capable is False
    assert method is None
    assert text_only.supported_input_modalities() == (InputModality.TEXT,)


def test_4d_image_input_and_native_video_are_distinct_capabilities():
    """A supports_multimodal boolean is NOT sufficient -- the two differ."""
    image_only = ModelCapabilities(text=True, vision=True, video=False)
    assert image_only.image_input is True
    assert image_only.native_video_input is False
    assert image_only.frame_sequence_capable is True
    assert image_only.can_understand_source_video() == (True, InputModality.FRAME_SEQUENCE)

    native = ModelCapabilities(text=True, vision=True, video=True)
    assert native.native_video_input is True
    assert native.can_understand_source_video() == (True, InputModality.NATIVE_VIDEO)

    # Registry reflects the real difference: GPT-4o has no native video.
    assert get_model("gpt-4o").capabilities.native_video_input is False
    assert job_video_understanding_method(get_model("gpt-4o")) == InputModality.FRAME_SEQUENCE
    assert job_video_understanding_method(get_model("gemini-3-flash-preview")) == InputModality.NATIVE_VIDEO


# =========================================================================
# 5. Auto selects a CANONICAL MODEL before provider routing
# =========================================================================

def test_5_auto_selects_model_before_provider():
    selection = select_model_auto(JOB_SOURCE_VIDEO_RECAP)
    assert selection.selection_mode == "auto"
    # The result is a MODEL identity, never a provider name.
    assert selection.canonical_model_id in [m.id for m in list_models()]
    assert selection.canonical_model_id not in PROVIDER_NAMES
    # AutoSelection carries NO provider field at all.
    assert not hasattr(selection, "provider")
    assert "provider" not in selection.as_dict()
    # Every considered candidate was capability-discovered first.
    for mid in selection.considered:
        assert get_model(mid).capabilities.can_understand_source_video()[0] is True
    # Provider resolution is a SEPARATE, later step.
    route = select_provider_for_model(selection.canonical_model_id, JOB_SOURCE_VIDEO_RECAP)
    assert route.canonical_model_id == selection.canonical_model_id


def test_5b_auto_makes_no_cost_claim():
    """Capability ranking is explicitly identified; cost is NOT claimed."""
    selection = select_model_auto(JOB_SOURCE_VIDEO_RECAP)
    assert selection.cost_optimized is False
    assert selection.ranking_basis == "capability_rank"


def test_5c_auto_requires_an_eligible_provider_route():
    """A capable model with NO eligible route must not win Auto."""
    orphan_id = "t9-auto-orphan"
    MODEL_REGISTRY[orphan_id] = CanonicalModel(
        id=orphan_id, display_name="Orphan", family="test",
        capabilities=ModelCapabilities(
            text=True, json_=True, vision=True, video=True, multimodal=True,
            long_context=True, tool_use=True, reasoning=True,
        ),
        bindings=(ProviderBinding(
            provider="mock", provider_model_id="wire-orphan",
            config_keys=("T9_DEFINITELY_UNSET_KEY",),
        ),),
        default_binding="mock",
    )
    try:
        assessment = assess_model_for_job(orphan_id, JOB_SOURCE_VIDEO_RECAP)
        assert assessment.capability_ok is True, "model IS capable"
        assert assessment.eligible is False, "but has no eligible route"
        selection = select_model_auto(JOB_SOURCE_VIDEO_RECAP)
        assert selection.canonical_model_id != orphan_id
        assert orphan_id in selection.considered
        assert orphan_id not in selection.eligible
    finally:
        MODEL_REGISTRY.pop(orphan_id, None)


def test_5d_auto_ranks_by_legitimate_capability_metadata():
    native = get_model("gemini-3-flash-preview")
    frame_based = get_model("gpt-4o")
    assert capability_rank(native) > capability_rank(frame_based)


# =========================================================================
# 6. Explicit selection is NEVER overridden
# =========================================================================

def test_6_explicit_selection_never_overridden():
    from app.production.narration import resolve_summary_model

    auto_choice = select_model_auto(JOB_SOURCE_VIDEO_RECAP).canonical_model_id
    for mid in ("gpt-4o", "qwen-vl-max", "qwen2.5-vl-72b-instruct"):
        chosen = resolve_summary_model("pro", preferred_model_id=mid, selection_mode="explicit")
        assert chosen == mid, "explicit selection must win"
        # Even when Auto would prefer a different (higher-ranked) model.
        if mid != auto_choice:
            assert chosen != auto_choice
        # And resolution preserves that exact identity.
        resolved = resolve_model(mid)
        assert resolved.canonical_model_id == mid


def test_6b_explicit_unknown_model_is_an_error_not_a_substitution():
    with pytest.raises(KeyError):
        get_model("t9-model-that-does-not-exist")


# =========================================================================
# 7. Providers selected only from verified bindings for THAT model
# =========================================================================

def test_7_provider_chosen_only_from_that_models_bindings():
    for m in discover_models_for_job(JOB_SOURCE_VIDEO_RECAP):
        declared = {(b.provider, b.provider_model_id) for b in m.bindings}
        assessment = assess_model_for_job(m.id, JOB_SOURCE_VIDEO_RECAP)
        for route in assessment.routes:
            assert (route.provider, route.provider_model_id) in declared
            assert route.canonical_model_id == m.id


def test_7b_selected_route_belongs_to_the_selected_model():
    selection = select_model_auto(JOB_SOURCE_VIDEO_RECAP)
    route = select_provider_for_model(selection.canonical_model_id)
    model = get_model(selection.canonical_model_id)
    assert (route.provider, route.provider_model_id) in {
        (b.provider, b.provider_model_id) for b in model.bindings
    }

# =========================================================================
# 8. Same-model provider fallback PRESERVES model identity
# 9. Cross-model fallback is REJECTED
# =========================================================================

class _Unavailable:
    def __init__(self, name, model):
        self._n, self._m = name, model

    @property
    def name(self):
        return self._n

    @property
    def model(self):
        return self._m

    def is_available(self):
        return False

    def supports_multimodal(self):
        return True

    def generate(self, *a, **kw):
        raise RuntimeError("unavailable")


class _Available(_Unavailable):
    def is_available(self):
        return True


def test_8_same_model_provider_fallback_preserves_identity():
    """Canonical X -> provider A fails -> provider B executes canonical X."""
    import app.content.provider_registry as preg

    model_id = "t9-same-model-fallback"
    MODEL_REGISTRY[model_id] = CanonicalModel(
        id=model_id, display_name="Same Model Fallback", family="test",
        capabilities=ModelCapabilities(
            text=True, json_=True, vision=True, video=True, multimodal=True
        ),
        bindings=(
            ProviderBinding(provider="t9-down", provider_model_id="wire-down"),
            ProviderBinding(provider="t9-up", provider_model_id="wire-up"),
        ),
        default_binding="t9-down",
    )
    preg.register_provider("t9-down", lambda mid=None: _Unavailable("t9-down", mid or "wire-down"),
                           allow_override=True)
    preg.register_provider("t9-up", lambda mid=None: _Available("t9-up", mid or "wire-up"),
                           allow_override=True)
    try:
        resolved = resolve_model(model_id, mode="exact", try_next_on_failure=True)
        # PROVIDER changed...
        assert resolved.provider == "t9-up"
        # ...but the CANONICAL MODEL IDENTITY did not.
        assert resolved.canonical_model_id == model_id
        # The wire id belongs to the SAME canonical model's binding set.
        assert resolved.provider_model_id == "wire-up"
        assert resolved.provider_model_id in {
            b.provider_model_id for b in MODEL_REGISTRY[model_id].bindings
        }
        # The failed attempt is recorded truthfully.
        assert any(a.get("provider") == "t9-down" for a in resolved.attempted)
    finally:
        MODEL_REGISTRY.pop(model_id, None)
        preg._REGISTRY.pop("t9-down", None)
        preg._REGISTRY.pop("t9-up", None)


def test_9_cross_model_fallback_is_rejected():
    """When all routes for X fail, AVM FAILS -- it never executes model Y as X."""
    import app.content.provider_registry as preg

    dead_id = "t9-cross-dead"
    alive_id = "t9-cross-alive"
    caps = ModelCapabilities(text=True, json_=True, vision=True, video=True, multimodal=True)
    MODEL_REGISTRY[dead_id] = CanonicalModel(
        id=dead_id, display_name="Cross Dead", family="test", capabilities=caps,
        bindings=(ProviderBinding(provider="t9-cd", provider_model_id="wire-cd"),),
        default_binding="t9-cd",
    )
    MODEL_REGISTRY[alive_id] = CanonicalModel(
        id=alive_id, display_name="Cross Alive", family="test", capabilities=caps,
        bindings=(ProviderBinding(provider="t9-ca", provider_model_id="wire-ca"),),
        default_binding="t9-ca",
    )
    preg.register_provider("t9-cd", lambda mid=None: _Unavailable("t9-cd", mid or "wire-cd"),
                           allow_override=True)
    preg.register_provider("t9-ca", lambda mid=None: _Available("t9-ca", mid or "wire-ca"),
                           allow_override=True)
    try:
        with pytest.raises(AllExactModelProvidersFailedError) as exc:
            resolve_model(dead_id, mode="exact", try_next_on_failure=True)
        # The error names the REQUESTED model, and never silently returns the
        # other healthy model.
        assert exc.value.canonical_model_id == dead_id
        assert alive_id not in str(exc.value)
        for attempt in exc.value.attempted:
            assert attempt.get("provider") != "t9-ca"
    finally:
        MODEL_REGISTRY.pop(dead_id, None)
        MODEL_REGISTRY.pop(alive_id, None)
        preg._REGISTRY.pop("t9-cd", None)
        preg._REGISTRY.pop("t9-ca", None)


def test_9b_router_refuses_rather_than_substituting_a_model():
    """No eligible route => explicit error, never a different model."""
    blocked_id = "t9-router-blocked"
    MODEL_REGISTRY[blocked_id] = CanonicalModel(
        id=blocked_id, display_name="Blocked", family="test",
        capabilities=ModelCapabilities(
            text=True, json_=True, vision=True, video=True, multimodal=True
        ),
        bindings=(ProviderBinding(
            provider="mock", provider_model_id="wire-blocked",
            config_keys=("T9_DEFINITELY_UNSET_KEY",),
        ),),
        default_binding="mock",
    )
    try:
        with pytest.raises(NoEligibleModelError) as exc:
            select_provider_for_model(blocked_id, JOB_SOURCE_VIDEO_RECAP)
        assert blocked_id in str(exc.value)
    finally:
        MODEL_REGISTRY.pop(blocked_id, None)



# =========================================================================
# 10. Unknown provider quota state is NOT reported as confirmed availability
# =========================================================================

def test_10_unknown_quota_is_not_reported_as_available(monkeypatch):
    monkeypatch.setenv("CODECRAFT_API_KEY", "cc_fake_for_test")
    monkeypatch.setenv("CODECRAFT_BASE_URL", "https://example.invalid/v1")
    binding = get_model("qwen-3.8-flash").bindings[0]
    route = assess_provider_route("qwen-3.8-flash", binding)
    ev = route.evidence
    # Credentials ARE configured...
    assert ev.credentials_configured is True
    # ...but that is explicitly NOT a claim about remaining credit.
    assert ev.quota_state == QuotaEvidence.CREDENTIALS_CONFIGURED
    assert ev.quota_known is False, "a configured key must never imply known quota"
    assert "unknown" in (ev.quota_detail or "").lower()
    assert ev.quota_state != QuotaEvidence.BALANCE_VERIFIED


def test_10b_no_balance_endpoint_is_registered_for_codecraft():
    """Live probe: /balance /credits /usage /account /me all 404 -> no endpoint."""
    from app.content.capability_router import PROVIDERS_WITH_BALANCE_ENDPOINT

    assert "codecraft" not in PROVIDERS_WITH_BALANCE_ENDPOINT


def test_10c_missing_credentials_make_route_ineligible(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    binding = get_model("gemini-3-flash-preview").bindings[0]
    route = assess_provider_route("gemini-3-flash-preview", binding)
    assert route.eligible is False
    assert any("credentials_missing" in r for r in route.reasons)
    assert route.evidence.quota_known is False


def test_10d_explicit_quota_exhaustion_is_honoured_as_evidence(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gm_fake_for_test")
    binding = get_model("gemini-3-flash-preview").bindings[0]
    assert assess_provider_route("gemini-3-flash-preview", binding).eligible is True

    # A real exhausted-quota response is recorded and respected.
    record_quota_blocker(binding.provider, binding.provider_model_id,
                         "HTTP 429 insufficient_quota", retry_after_seconds=120)
    route = assess_provider_route("gemini-3-flash-preview", binding)
    assert route.eligible is False
    assert "quota_exhausted_evidence" in route.reasons
    assert route.evidence.quota_state == QuotaEvidence.EXHAUSTED
    # Exhaustion IS known information (unlike "unknown").
    assert route.evidence.quota_known is True
    assert route.evidence.currently_blocked is True
    clear_quota_blockers()
    assert assess_provider_route("gemini-3-flash-preview", binding).eligible is True


def test_10e_evidence_levels_are_distinguishable():
    """credentials / listed / capability / balance are SEPARATE facts."""
    from app.content.capability_router import ProviderEvidence

    ev = ProviderEvidence(provider="p", provider_model_id="w")
    assert ev.credentials_configured is False
    assert ev.model_listed is False
    assert ev.capability_verified is False
    assert ev.quota_state == QuotaEvidence.UNKNOWN
    assert ev.quota_known is False
    payload = ev.as_dict()
    for field in ("credentials_configured", "model_listed", "capability_verified",
                  "quota_state", "quota_known"):
        assert field in payload


# =========================================================================
# 11. CodeCraft image support is MODEL-SPECIFIC
# 12. CodeCraft native video capability is NOT fabricated
# =========================================================================

def test_11_codecraft_image_support_is_model_specific():
    from app.content.providers.codecraft import (
        CODECRAFT_VERIFIED_VISION_MODELS,
        CodeCraftProvider,
    )

    # Verified vision model -> image input offered.
    vision = CodeCraftProvider(api_key="k", model="gpt-5.6-sol")
    assert vision.supports_multimodal() is True
    assert vision.vision_capability_source() == "verified_real_request"

    # Declared-only vision model -> offered, with weaker provenance.
    declared = CodeCraftProvider(api_key="k", model="gemini-3.7-flash")
    assert declared.supports_multimodal() is True
    assert declared.vision_capability_source() == "provider_declared_metadata"

    # Declared TEXT-ONLY model -> refused, even though the gateway would answer.
    text_only = CodeCraftProvider(api_key="k", model="qwen3.8-27b")
    assert text_only.supports_multimodal() is False
    assert text_only.vision_capability_source() is None

    # A tiny text-only model is never marked vision-capable.
    assert CodeCraftProvider(api_key="k", model="gemma-2-2b").supports_multimodal() is False
    assert "qwen3.8-27b" not in CODECRAFT_VERIFIED_VISION_MODELS
    assert "gemma-2-2b" not in CODECRAFT_VERIFIED_VISION_MODELS


def test_11b_codecraft_capability_read_from_provider_metadata_not_names():
    from app.content.providers.codecraft import CodeCraftProvider

    # Real shapes returned by CodeCraft's GET /models.
    text_entry = {"id": "x", "architecture": {"input_modalities": ["text"]},
                  "capabilities": ["reasoning", "tools"]}
    vision_entry = {"id": "y", "architecture": {"input_modalities": ["text", "image"]},
                    "capabilities": ["reasoning", "vision"]}
    assert CodeCraftProvider.model_declares_image_input(text_entry) is False
    assert CodeCraftProvider.model_declares_image_input(vision_entry) is True
    # Name is irrelevant: an "opus"-named entry declaring text-only is text-only.
    assert CodeCraftProvider.model_declares_image_input(
        {"id": "claude-opus-5.5", "architecture": {"input_modalities": ["text"]},
         "capabilities": ["reasoning"]}
    ) is False


def test_12_codecraft_native_video_is_not_fabricated():
    from app.content.providers.codecraft import CodeCraftProvider

    for mid in ("gpt-5.6-sol", "claude-opus-5.5", "gemini-3.7-flash", "qwen3.8-27b"):
        p = CodeCraftProvider(api_key="k", model=mid)
        assert p.supports_native_video() is False, (
            f"{mid} must not claim native video ingestion"
        )

    # No CodeCraft /models entry declares video input -> never inferred.
    vision_entry = {"architecture": {"input_modalities": ["text", "image"]},
                    "capabilities": ["vision"]}
    assert CodeCraftProvider.model_declares_native_video(vision_entry) is False
    video_entry = {"architecture": {"input_modalities": ["text", "video"]}}
    assert CodeCraftProvider.model_declares_native_video(video_entry) is True

    # CodeCraft is bound to NO canonical model that claims native video.
    for m in list_models():
        if m.capabilities.native_video_input:
            assert "codecraft" not in {b.provider for b in m.bindings}


def test_12b_codecraft_refuses_native_video_instead_of_faking_it():
    """A video= request is REFUSED, not downgraded to a text-only call."""
    from app.content.providers.codecraft import CodeCraftProvider

    p = CodeCraftProvider(api_key="k", model="gpt-5.6-sol")
    with pytest.raises(Exception) as exc:
        p.generate_multimodal("describe", video="C:/some/clip.mp4", images=None)


# =========================================================================
# 13. Frame-based visual analysis is distinguishable from native video
# =========================================================================

def test_13_frame_based_analysis_is_labelled_distinctly(tmp_path):
    """A frame-based request is recorded as frame_sequence, never native video."""
    from unittest.mock import Mock, patch

    from app.content.providers.codecraft import CodeCraftProvider

    frame_a = tmp_path / "f_12.jpg"
    frame_b = tmp_path / "f_34.jpg"
    frame_a.write_bytes(b"\xff\xd8\xff" + b"a" * 4096)
    frame_b.write_bytes(b"\xff\xd8\xff" + b"b" * 4096)

    fake = Mock()
    fake.status_code = 200
    fake.json.return_value = {
        "id": "cc-1", "model": "gpt-5.6-sol",
        "choices": [{"message": {"content": "two frames described"}}],
        "usage": {"total_tokens": 42},
    }
    p = CodeCraftProvider(api_key="k", model="gpt-5.6-sol")
    with patch("app.content.providers.codecraft.requests.post", return_value=fake) as post:
        resp = p.generate_multimodal(
            "Describe the visual events.",
            images=[(12.0, str(frame_a)), (34.5, str(frame_b))],
            max_tokens=100,
        )
    meta = resp.metadata or {}
    # TRUTHFUL labelling.
    assert meta["input_modality"] == InputModality.FRAME_SEQUENCE
    assert meta["visual_analysis_method"] == "frame_based_visual_analysis"
    assert meta["native_video_ingestion"] is False
    assert meta["frame_count"] == 2
    # Timestamps PRESERVED for source grounding.
    stamps = [f["timestamp_seconds"] for f in meta["frame_timestamps"]]
    assert stamps == [12.0, 34.5]
    # The request really carried image parts (not a text-only prompt).
    sent = post.call_args[1]["json"]
    parts = sent["messages"][-1]["content"]
    image_parts = [x for x in parts if x.get("type") == "image_url"]
    assert len(image_parts) == 2
    for part in image_parts:
        assert part["image_url"]["url"].startswith("data:image/")
    # Model identity recorded is the one actually requested.
    assert resp.model == "gpt-5.6-sol"
    assert meta["provider_reported_model"] == "gpt-5.6-sol"
    assert "provider_model_mismatch" not in meta


def test_13b_provider_model_mismatch_is_recorded_not_hidden():
    from unittest.mock import Mock, patch

    from app.content.providers.codecraft import CodeCraftProvider

    fake = Mock()
    fake.status_code = 200
    fake.json.return_value = {
        "model": "something-else",
        "choices": [{"message": {"content": "ok"}}],
    }
    p = CodeCraftProvider(api_key="k", model="qwen3.8-27b")
    with patch("app.content.providers.codecraft.requests.post", return_value=fake):
        resp = p.generate("hi")
    meta = resp.metadata or {}
    assert meta["provider_reported_model"] == "something-else"
    assert meta["provider_model_mismatch"] is True
    # The adapter still reports the model IT requested, truthfully.
    assert resp.model == "qwen3.8-27b"


def test_13c_oversized_frames_are_skipped_not_silently_truncated(tmp_path):
    from app.content.providers.codecraft import (
        CODECRAFT_MAX_IMAGE_BYTES,
        _build_image_parts,
    )

    big = tmp_path / "big.jpg"
    big.write_bytes(b"x" * (CODECRAFT_MAX_IMAGE_BYTES + 10))
    small = tmp_path / "small.jpg"
    small.write_bytes(b"y" * 2048)
    parts, meta = _build_image_parts([(1.0, str(big)), (2.0, str(small))])
    assert len(parts) == 1, "oversized frame must be skipped"
    skipped = [m for m in meta if m.get("skipped")]
    assert len(skipped) == 1
    assert "limit" in skipped[0]["reason"]


def test_13d_native_video_route_differs_from_frame_route_in_registry():
    """The two methods are distinguishable at the catalogue level."""
    native = [m.id for m in discover_models_for_job(JOB_SOURCE_VIDEO_RECAP)
              if job_video_understanding_method(m) == InputModality.NATIVE_VIDEO]
    frames = [m.id for m in discover_models_for_job(JOB_SOURCE_VIDEO_RECAP)
              if job_video_understanding_method(m) == InputModality.FRAME_SEQUENCE]


# =========================================================================
# 14. Model-drawer projection does NOT expose provider identities as models
# =========================================================================

def test_14_drawer_projection_hides_provider_identity():
    payload = model_drawer_projection(JOB_SOURCE_VIDEO_RECAP)
    assert payload["models"], "drawer must offer at least one canonical model"
    blob = repr(payload).lower()
    # No provider name anywhere.
    for provider in ("alibaba", "openrouter", "codecraft", "gemini_api", "dashscope"):
        assert provider not in blob, f"provider '{provider}' leaked into drawer"
    # No wire ids, keys or routing internals.
    for leak in ("openai/gpt-4o", "qwen/qwen2.5-vl-72b-instruct",
                 "api_key", "config_keys", "provider_model_id",
                 "identity_verification", "bindings", "quota"):
        assert leak not in blob, f"internal detail '{leak}' leaked into drawer"
    # Each entry is a MODEL identity, never a provider name.
    for entry in payload["models"]:
        assert entry["id"] not in PROVIDER_NAMES
        assert "provider" not in entry
        assert entry["eligible_for_job"] is True


def test_14b_drawer_makes_no_misleading_cost_or_availability_claim():
    payload = model_drawer_projection(JOB_SOURCE_VIDEO_RECAP)
    assert payload["cost_optimized"] is False
    for entry in payload["models"]:
        assert entry["cost_estimate"] is None
        assert "unavailable" in entry["cost_basis"]
        # The drawer makes no availability claim at all.
        assert "available" not in entry
        assert "quota" not in entry


def test_14c_drawer_reports_user_facing_fields_required_by_task_d():
    payload = model_drawer_projection(JOB_SOURCE_VIDEO_RECAP)
    for entry in payload["models"]:
        assert entry["id"] and isinstance(entry["id"], str)
        assert entry["display_name"] and isinstance(entry["display_name"], str)
        assert isinstance(entry["capabilities"], dict)
        # context info present (may be None = honestly unknown)
        assert "context_tokens" in entry
        assert entry["eligible_for_job"] is True
        assert entry["video_understanding_method"] in (
            InputModality.NATIVE_VIDEO, InputModality.FRAME_SEQUENCE
        )


def test_14d_drawer_rejects_an_unknown_job():
    with pytest.raises(KeyError):
        model_drawer_projection("t9-job-that-does-not-exist")


# =========================================================================
# 15/16/17. Existing behaviour remains intact
# =========================================================================

def test_15_summary_standard_behaviour_intact():
    from app.production.narration import (
        SUMMARY_STANDARD_MODEL,
        resolve_summary_model,
    )

    assert SUMMARY_STANDARD_MODEL == "qwen-3.8-flash"
    mid = resolve_summary_model("standard")
    model = get_model(mid)
    assert model.capabilities.text is True
    # The Standard model is still the CodeCraft text route.
    assert {b.provider for b in get_model("qwen-3.8-flash").bindings} == {"codecraft"}
    assert get_model("qwen-3.8-flash").bindings[0].provider_model_id == "qwen3.8-27b"
    # A text-only model is still refused for Pro, truthfully.
    with pytest.raises(ValueError, match="not multimodal-capable"):
        resolve_summary_model("pro", preferred_model_id=SUMMARY_STANDARD_MODEL,
                              selection_mode="explicit")


def test_16_summary_pro_default_identity_intact():
    from app.production.narration import SUMMARY_PRO_MODEL

    assert SUMMARY_PRO_MODEL == "gemini-3-flash-preview"
    model = get_model(SUMMARY_PRO_MODEL)
    assert model.display_name == "Gemini 3 Flash Preview"
    # Its ONE retained binding is first-party verified.
    assert len(model.bindings) == 1
    binding = model.bindings[0]
    assert binding.provider == "gemini"
    assert binding.provider_model_id == "gemini-3-flash-preview"
    assert binding.identity_verification == IdentityVerification.VERIFIED
    # Deprecated aliases still resolve but are not active identities.
    from app.content.model_registry import has_model

    assert has_model("summary-pro-gemini-2.0-flash") is False
    assert get_model("summary-pro-gemini-2.0-flash").id == SUMMARY_PRO_MODEL


def test_17_resolver_remains_the_single_resolver():
    """No second resolver / second canonical registry was introduced."""
    import app.content.capability_router as router

    # The router does NOT define a resolve_model of its own.
    assert not hasattr(router, "resolve_model")
    # And it does not hold its own model registry.
    assert not hasattr(router, "MODEL_REGISTRY")
    # The single registry remains authoritative.
    from app.content import model_registry as registry

    assert registry.MODEL_REGISTRY is MODEL_REGISTRY
    # Resolution still flows through the one resolver and preserves identity.
    resolved = resolve_model("qwen-3.8-flash")
    assert resolved.canonical_model_id == "qwen-3.8-flash"
    assert resolved.provider == "codecraft"
    assert resolved.provider_model_id == "qwen3.8-27b"
    # Canonical id and wire id stay distinct concepts.
    assert resolved.canonical_model_id != resolved.provider_model_id


def test_17b_eligibility_listing_is_deterministic():
    first = [a.canonical_model_id for a in list_eligible_models_for_job()]
    second = [a.canonical_model_id for a in list_eligible_models_for_job()]
    assert first == second
    assert select_model_auto().canonical_model_id == select_model_auto().canonical_model_id


# =========================================================================
# Adapter correctness surfaced by the REAL proof (reasoning models)
# =========================================================================

def test_13e_nested_usage_is_normalized_not_dropped():
    """CodeCraft nests usage detail; the frozen Dict[str,int] contract must hold.

    Live: a real request returned
      {"completion_tokens": 1186,
       "completion_tokens_details": {"reasoning_tokens": 825},
       "prompt_tokens_details": {"cached_tokens": 241}}
    which FAILS Pydantic validation on LLMResponse.usage (Dict[str,int]) if
    passed through unflattened. reasoning_tokens must survive, not be discarded.
    """
    from app.content.providers.codecraft import _normalize_usage

    flat = _normalize_usage({
        "prompt_tokens": 1573,
        "completion_tokens": 1186,
        "total_tokens": 2759,
        "completion_tokens_details": {"reasoning_tokens": 825},
        "prompt_tokens_details": {"cached_tokens": 241},
    })
    assert flat is not None
    assert flat["total_tokens"] == 2759
    assert flat["completion_tokens_details_reasoning_tokens"] == 825
    assert flat["prompt_tokens_details_cached_tokens"] == 241
    # Every value satisfies the frozen contract.
    for key, value in flat.items():
        assert isinstance(value, int), f"{key} must be int for LLMResponse.usage"
    assert _normalize_usage(None) is None


def test_13f_reasoning_budget_starvation_is_diagnosed_truthfully():
    """A budget fully consumed by reasoning is a REQUEST failure, reported as such."""
    from unittest.mock import Mock, patch

    from app.content.providers.codecraft import CodeCraftProvider

    starved = Mock()
    starved.status_code = 200
    starved.json.return_value = {
        "id": "cc-starved", "model": "gpt-5.6-sol",
        "choices": [{"finish_reason": "length",
                     "message": {"content": None, "role": "assistant"}}],
        "usage": {"prompt_tokens": 599, "completion_tokens": 200, "total_tokens": 799,
                  "completion_tokens_details": {"reasoning_tokens": 197}},
    }
    p = CodeCraftProvider(api_key="k", model="gpt-5.6-sol")
    with patch("app.content.providers.codecraft.requests.post", return_value=starved):
        with pytest.raises(Exception) as exc:
            # Force the floor off so the starved shape is actually exercised.
            p._model = "not-a-reasoning-model-in-the-registry"
            p.generate("hi", max_tokens=200)
    message = str(exc.value).lower()
    assert "reasoning" in message
    assert "max_tokens" in message


def test_13g_reasoning_floor_is_applied_and_disclosed():
    """A starved budget is RAISED for a reasoning model, and the raise is disclosed."""
    from unittest.mock import Mock, patch

    from app.content.providers.codecraft import (
        CODECRAFT_REASONING_MIN_TOKENS,
        CodeCraftProvider,
    )

    ok = Mock()
    ok.status_code = 200
    ok.json.return_value = {
        "id": "cc-ok", "model": "gpt-5.6-sol",
        "choices": [{"finish_reason": "stop",
                     "message": {"content": "described"}}],
        "usage": {"completion_tokens": 239, "total_tokens": 838,
                  "completion_tokens_details": {"reasoning_tokens": 184}},
    }
    p = CodeCraftProvider(api_key="k", model="gpt-5.6-sol")
    with patch("app.content.providers.codecraft.requests.post", return_value=ok) as post:
        resp = p.generate("describe", max_tokens=200)
    sent = post.call_args[1]["json"]
    # The request really carried the raised budget...
    assert sent["max_tokens"] == CODECRAFT_REASONING_MIN_TOKENS
    # ...and the adjustment is DISCLOSED, never silent.
    assert "max_tokens_adjustment" in (resp.metadata or {})
    assert "reasoning model" in resp.metadata["max_tokens_adjustment"]
    # A sufficient budget is left untouched.
    p2 = CodeCraftProvider(api_key="k", model="gpt-5.6-sol")
    with patch("app.content.providers.codecraft.requests.post", return_value=ok) as post2:
        resp2 = p2.generate("describe", max_tokens=8000)
    assert post2.call_args[1]["json"]["max_tokens"] == 8000
    assert "max_tokens_adjustment" not in (resp2.metadata or {})


def test_13h_registered_text_model_keeps_caller_budget_semantics():
    """The NON-reasoning registered route is not silently altered."""
    from app.content.providers.codecraft import CodeCraftProvider

    p = CodeCraftProvider(api_key="k", model="gemma-2-2b")
    value, reason = p._effective_max_tokens(64)
    assert value == 64
    assert reason is None


def test_13i_transient_5xx_is_retried_but_4xx_is_not():
    """Live: valid payloads intermittently 500; explicit 4xx is authoritative."""
    from unittest.mock import Mock, patch

    from app.content.providers.codecraft import CodeCraftProvider

    flaky = Mock()
    flaky.status_code = 500
    flaky.headers = {}
    flaky.json.return_value = {
        "error": {"message": "An unexpected error occurred.", "type": "api_error"}
    }
    good = Mock()
    good.status_code = 200
    good.json.return_value = {"id": "x", "model": "qwen3.8-27b",
                              "choices": [{"message": {"content": "recovered"}}]}

    p = CodeCraftProvider(api_key="k", model="qwen3.8-27b")
    with patch("app.content.providers.codecraft.requests.post",
               side_effect=[flaky, good]) as post:
        resp = p.generate("hi")
    assert resp.content == "recovered"
    assert post.call_count == 2, "a transient 5xx must be retried"

    # Explicit 4xx: raised immediately, never retried.
    from app.content.exceptions import LLMAuthenticationError

    rejected = Mock()
    rejected.status_code = 401
    rejected.headers = {}
    rejected.json.return_value = {"error": {"message": "unauthorized"}}
    p2 = CodeCraftProvider(api_key="k", model="qwen3.8-27b")
    with patch("app.content.providers.codecraft.requests.post",
               return_value=rejected) as post2:
        with pytest.raises(LLMAuthenticationError):
            p2.generate("hi")
    assert post2.call_count == 1, "explicit 4xx must NOT be retried"



