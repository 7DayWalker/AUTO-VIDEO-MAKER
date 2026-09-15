"""Phase 11C Track 1 — truthfulness hardening regressions (D3/D2/D7).

Hermetic tests for the three audited truthfulness defects:

- D3: the creation response and persisted intent must classify the selected
  production path's cost class truthfully — never claim ``local_free`` for a
  billable path;
- D2: the OpenRouter video fallback resolves its credential with a
  deterministic precedence, is truthfully unavailable without one, warns once
  when enabled-but-unusable, and never leaks secret values;
- D7: non-Flow executions must never inherit the Flow-era
  ``Veo 3.1 - Lite`` model identity through shared scene-configuration
  defaults; the Flow boundary keeps its own default resolution.

No network calls, no provider requests, no paid operations.
"""

import logging

import pytest

from app.orchestrator.alibaba_video import FallbackVideoVisualProvider
from app.orchestrator import openrouter_video as orv
from app.orchestrator.models import OrchestratorConfig
from app.orchestrator.pipeline import flow_scene_model_from_config
from app.production.routes import (
    COST_CLASS_BILLABLE,
    COST_CLASS_LOCAL_FREE,
    creation_cost_class,
)


# ---------------------------------------------------------------------------
# D3 — creation-time cost classification (pure helper)
# ---------------------------------------------------------------------------


class TestCreationCostClass:
    def test_local_strategy_is_local_free(self):
        assert (
            creation_cost_class(generation_strategy="local")
            == COST_CLASS_LOCAL_FREE
        )

    def test_alibaba_image_strategy_is_billable(self):
        assert (
            creation_cost_class(generation_strategy="alibaba_image_local_video")
            == COST_CLASS_BILLABLE
        )

    def test_alibaba_video_strategy_is_billable(self):
        assert (
            creation_cost_class(generation_strategy="alibaba_video")
            == COST_CLASS_BILLABLE
        )

    def test_explicit_full_ai_video_route_is_billable_even_with_local_strategy(self):
        assert (
            creation_cost_class(route="full_ai_video", generation_strategy="local")
            == COST_CLASS_BILLABLE
        )

    def test_explicit_local_route_with_local_strategy_is_local_free(self):
        assert (
            creation_cost_class(
                route="local_ai_assisted", generation_strategy="local"
            )
            == COST_CLASS_LOCAL_FREE
        )

    def test_explicit_local_route_with_billable_strategy_stays_billable(self):
        # The legacy overlay makes the Alibaba image source a media-strategy
        # decision BENEATH the local route — the path is still billable.
        assert (
            creation_cost_class(
                route="local_ai_assisted",
                generation_strategy="alibaba_image_local_video",
            )
            == COST_CLASS_BILLABLE
        )

    def test_unknown_inputs_never_claim_free(self):
        # Conservative: the creation response must never claim "free" for a
        # path it cannot identify.
        assert (
            creation_cost_class(route="nonsense", generation_strategy="nonsense")
            == COST_CLASS_BILLABLE
        )

    def test_missing_inputs_default_to_the_local_free_path(self):
        assert creation_cost_class() == COST_CLASS_LOCAL_FREE


# ---------------------------------------------------------------------------
# D3 — creation response and persisted intent (web seam)
# ---------------------------------------------------------------------------


def _creation_request(**overrides):
    request = {
        "mode": "single_video",
        "creation_method": "local",
        "title": "Track 1 truthfulness video",
        "brief": "A small deterministic production for cost-class verification.",
        "output_intent": "youtube_shorts",
        "target_duration": 30,
        "scene_count": 6,
        "tone": "clear and cinematic",
    }
    request.update(overrides)
    return request


@pytest.mark.parametrize(
    "strategy,expected",
    [
        (None, "local_free"),
        ("local", "local_free"),
        ("alibaba_image_local_video", "billable"),
        ("alibaba_video", "billable"),
    ],
)
def test_creation_response_and_intent_classify_cost_truthfully(
    tmp_path, monkeypatch, strategy, expected
):
    from app.production.storage import load_production
    from app.web import server as web

    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    payload = _creation_request()
    if strategy is not None:
        payload["generation_strategy"] = strategy

    status, response = web.create_production_payload(payload)
    assert status == 201
    assert response["cost_class"] == expected

    production = load_production(
        response["production_id"], root=tmp_path / "productions"
    )
    assert production.command.explicit_constraints["cost_class"] == expected


# ---------------------------------------------------------------------------
# D2 — OpenRouter video fallback credential resolution
# ---------------------------------------------------------------------------


class TestOpenRouterVideoCredentialResolution:
    def test_canonical_key_wins_over_shared_key(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AVM_OPENROUTER_API_KEY", "avm-key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "shared-key")
        provider = orv.OpenRouterVideoVisualProvider(tmp_path)
        assert provider._api_key == "avm-key"
        assert provider.is_available() is True

    def test_shared_key_is_accepted_as_compatibility_fallback(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.delenv("AVM_OPENROUTER_API_KEY", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "shared-key")
        provider = orv.OpenRouterVideoVisualProvider(tmp_path)
        assert provider._api_key == "shared-key"
        assert provider.is_available() is True

    def test_no_key_is_truthfully_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.delenv("AVM_OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        provider = orv.OpenRouterVideoVisualProvider(tmp_path)
        assert provider._api_key == ""
        assert provider.is_available() is False

    def test_missing_credential_error_names_both_variables(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.delenv("AVM_OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        provider = orv.OpenRouterVideoVisualProvider(tmp_path)
        with pytest.raises(orv.OpenRouterVideoConfigurationError) as excinfo:
            provider._get_client()
        message = str(excinfo.value)
        assert "AVM_OPENROUTER_API_KEY" in message
        assert "OPENROUTER_API_KEY" in message

    def test_base_url_precedence_is_deterministic(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AVM_OPENROUTER_BASE_URL", "https://avm.example/v1")
        monkeypatch.setenv("OPENROUTER_BASE_URL", "https://shared.example/v1")
        provider = orv.OpenRouterVideoVisualProvider(tmp_path)
        assert provider.base_url == "https://avm.example/v1"

        monkeypatch.delenv("AVM_OPENROUTER_BASE_URL")
        provider = orv.OpenRouterVideoVisualProvider(tmp_path)
        assert provider.base_url == "https://shared.example/v1"

        monkeypatch.delenv("OPENROUTER_BASE_URL")
        provider = orv.OpenRouterVideoVisualProvider(tmp_path)
        assert provider.base_url == orv.OPENROUTER_DEFAULT_BASE_URL

    def test_planning_facts_without_any_key_are_none(self, monkeypatch):
        monkeypatch.delenv("AVM_OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        assert orv.openrouter_video_planning_facts() is None


class TestFallbackUnavailableWarning:
    def test_enabled_fallback_without_credential_warns_exactly_once(
        self, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.delenv("AVM_OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with caplog.at_level(logging.WARNING):
            wrapper = FallbackVideoVisualProvider(tmp_path, fallback_enabled=True)
            fallback = wrapper._get_fallback()
            # A second resolution reuses the cached provider: still one warning.
            assert wrapper._get_fallback() is fallback

        assert fallback is not None
        assert fallback.is_available() is False
        warnings = [
            record
            for record in caplog.records
            if record.getMessage().startswith("video_fallback_unavailable")
        ]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "AVM_OPENROUTER_API_KEY" in message
        assert "OPENROUTER_API_KEY" in message

    def test_no_secret_values_ever_appear_in_the_warning(
        self, tmp_path, monkeypatch, caplog
    ):
        # Even with a credential-shaped value present elsewhere in the
        # environment, the warning carries variable NAMES only.
        monkeypatch.setenv("AVM_OPENROUTER_API_KEY", "")
        monkeypatch.setenv("AVM_ALIBABA_API_KEY", "sk-sentinel-DO-NOT-LOG")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with caplog.at_level(logging.WARNING):
            wrapper = FallbackVideoVisualProvider(tmp_path, fallback_enabled=True)
            wrapper._get_fallback()
        joined = "\n".join(record.getMessage() for record in caplog.records)
        assert "sk-sentinel-DO-NOT-LOG" not in joined
        assert "sk-" not in joined
        assert "AVM_OPENROUTER_API_KEY" in joined

    def test_configured_fallback_does_not_warn(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv("AVM_OPENROUTER_API_KEY", "configured-key")
        with caplog.at_level(logging.WARNING):
            wrapper = FallbackVideoVisualProvider(tmp_path, fallback_enabled=True)
            fallback = wrapper._get_fallback()
        assert fallback is not None
        assert fallback.is_available() is True
        assert not [
            record
            for record in caplog.records
            if record.getMessage().startswith("video_fallback_unavailable")
        ]

    def test_disabled_fallback_never_warns(self, tmp_path, monkeypatch, caplog):
        monkeypatch.delenv("AVM_OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with caplog.at_level(logging.WARNING):
            wrapper = FallbackVideoVisualProvider(tmp_path, fallback_enabled=False)
            assert wrapper._get_fallback() is None
        assert not [
            record
            for record in caplog.records
            if record.getMessage().startswith("video_fallback_unavailable")
        ]


# ---------------------------------------------------------------------------
# D7 — non-Flow scenes never inherit the Flow-era model identity
# ---------------------------------------------------------------------------


class TestSceneModelTruthfulness:
    def test_default_scene_config_never_claims_a_flow_model(self):
        config = OrchestratorConfig(topic="Truthfulness default config")
        assert config.scene_model is None
        scene_config = config.to_scene_config_dict()
        assert scene_config["model"] is None
        assert scene_config["model"] != "Veo 3.1 - Lite"

    def test_explicit_scene_model_is_still_persisted(self):
        # The Flow CLI path (--scene-model) keeps recording the explicit value.
        config = OrchestratorConfig(
            topic="Truthfulness explicit model", scene_model="Veo 3.1 - Lite"
        )
        assert config.to_scene_config_dict()["model"] == "Veo 3.1 - Lite"

    def test_flow_boundary_resolves_missing_model_to_the_flow_default(self):
        from app.flow.scene_models import FLOW_SCENE_MODEL

        assert flow_scene_model_from_config({}) == FLOW_SCENE_MODEL
        assert flow_scene_model_from_config({"model": None}) == FLOW_SCENE_MODEL
        assert flow_scene_model_from_config(None) == FLOW_SCENE_MODEL

    def test_flow_boundary_keeps_an_explicit_model(self):
        assert (
            flow_scene_model_from_config({"model": "Veo 3.1 - Fast"})
            == "Veo 3.1 - Fast"
        )

    def test_flow_route_scene_config_resolves_the_flow_default(self):
        from app.flow.scene_models import FLOW_SCENE_MODEL

        config = OrchestratorConfig(
            topic="Truthfulness flow config", visual_mode="google_flow"
        )
        scene_config = config.to_scene_config_dict()
        assert scene_config["visual_mode"] == "google_flow"
        assert flow_scene_model_from_config(scene_config) == FLOW_SCENE_MODEL
        assert flow_scene_model_from_config(scene_config) == "Veo 3.1 - Lite"
