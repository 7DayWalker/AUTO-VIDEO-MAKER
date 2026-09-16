"""Batch 1C follow-up — Generation Strategy UI & execution wiring.

Hermetic coverage for the user-selectable production-level generation
strategy:

- ``local``                  — default; the existing local path is preserved
                               byte-for-byte (incl. legacy productions);
- ``alibaba_image_local_video`` — the existing Alibaba image source feeds the
                               existing local assembly pipeline;
- ``alibaba_video``          — every scene routes to the existing Batch 1C
                               ``alibaba_video`` adapter (real motion video);
                               unavailability fails truthfully and NEVER
                               silently substitutes local Ken-Burns motion.

No network requests are made; persistence uses tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.content.models import (
    ContentBrief,
    ProductionMetadata,
    ProductionPlan,
    Scene,
)
from app.orchestrator.assets import (
    GENERATION_PROVIDERS,
    PROVIDER_ALIBABA_VIDEO,
    preflight_assets,
)
from app.orchestrator.models import OrchestratorConfig
from app.orchestrator.production_execution import (
    apply_generation_strategy,
)
from app.orchestrator.visual_providers import (
    create_visual_provider,
)
from app.orchestrator.visual_sources import create_visual_source
from app.production.models import (
    DEFAULT_GENERATION_STRATEGY,
    GENERATION_STRATEGIES,
    Production,
    ProductionCommand,
    ProductionUnit,
)
from app.production.storage import load_production, save_new_production
from app.web import server as web


CREATED = "2026-09-13T09:00:00+00:00"
PID = "prd_20260913_strategy_001"


def make_production(strategy: str | None = None) -> Production:
    values = dict(
        production_id=PID,
        title="Generation strategy production",
        command=ProductionCommand(
            text="Create one video.",
            workload_shape="single_video",
            intended_unit_count=1,
            issued_at=CREATED,
        ),
        units=[
            ProductionUnit(
                unit_id="unit-001",
                ordinal=1,
                unit_type="standalone_video",
                title="Unit 001",
                content_intent={"target_duration": 10, "number_of_scenes": 2},
            )
        ],
        created_at=CREATED,
    )
    if strategy is not None:
        values["generation_strategy"] = strategy
    return Production(**values)


def make_plan(*, scenes: int = 2) -> ProductionPlan:
    duration = 5
    return ProductionPlan(
        project_id="20260913_strategy_plan_001",
        title="Strategy plan",
        hook="Hook",
        script=" ".join(f"Narration {i}" for i in range(1, scenes + 1)),
        call_to_action=None,
        estimated_duration=scenes * duration,
        scenes=[
            Scene(
                id=f"scene_{i:02d}",
                sequence=i,
                narration=f"Narration {i}",
                visual_prompt=f"Visual {i}",
                estimated_duration=duration,
                purpose="hook" if i == 1 else "conclusion",
            )
            for i in range(1, scenes + 1)
        ],
        metadata=ProductionMetadata(
            platform="youtube_shorts",
            language="en",
            niche="education",
            target_audience="general audience",
            content_goal="education",
            style="documentary",
            requires_fact_check=True,
        ),
    )


def baseline_config() -> OrchestratorConfig:
    """The exact base configuration the web local path builds today."""
    return OrchestratorConfig(
        topic="Strategy production",
        mock_mode=False,
        visual_mode="local",
        visual_default_provider="image_motion",
        image_source="auto",
    )


def snapshot(config: OrchestratorConfig) -> dict:
    return {
        "visual_mode": config.visual_mode,
        "visual_default_provider": config.visual_default_provider,
        "visual_scene_routing": dict(config.visual_scene_routing),
        "image_source": config.image_source,
    }
# ---------------------------------------------------------------------------
# 1) Default strategy is local
# 2) Legacy production without generation_strategy resolves to local
# 3) Local strategy reaches the existing local path (no-op)
# ---------------------------------------------------------------------------


def test_default_strategy_is_local():
    assert DEFAULT_GENERATION_STRATEGY == "local"
    assert GENERATION_STRATEGIES == (
        "local",
        "alibaba_image_local_video",
        "alibaba_video",
    )
    assert make_production().generation_strategy == "local"


def test_legacy_production_without_field_resolves_to_local(tmp_path):
    root = tmp_path / "productions"
    save_new_production(make_production(), root=root)
    path = root / PID / "production.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("generation_strategy", None)  # simulate a pre-strategy artifact
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_production(PID, root=root)
    assert loaded.generation_strategy == "local"
    # And the strategy application treats it exactly as local (no-op).
    config = baseline_config()
    before = snapshot(config)
    apply_generation_strategy(loaded, config, make_plan())
    assert snapshot(config) == before


def test_unknown_strategy_fails_loudly_never_rerouted():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="generation_strategy"):
        make_production(strategy="comfyui")


def test_local_strategy_preserves_existing_local_path():
    production = make_production("local")
    config = baseline_config()
    before = snapshot(config)
    apply_generation_strategy(production, config, make_plan())
    assert snapshot(config) == before
# ---------------------------------------------------------------------------
# 4+5) Alibaba Image -> Local Video: Alibaba image path + local assembly
# 6) Alibaba Video selects the Alibaba video path for every scene
# ---------------------------------------------------------------------------


def test_alibaba_image_strategy_selects_alibaba_image_source():
    production = make_production("alibaba_image_local_video")
    config = baseline_config()
    apply_generation_strategy(production, config, make_plan())

    assert config.image_source == "alibaba"
    # The existing Alibaba image source is selected offline (no request).
    from app.orchestrator.alibaba_image import AlibabaImageSource

    source = create_visual_source("alibaba")
    assert isinstance(source, AlibabaImageSource)


def test_alibaba_image_strategy_keeps_local_assembly_pipeline():
    production = make_production("alibaba_image_local_video")
    config = baseline_config()
    apply_generation_strategy(production, config, make_plan())

    # Downstream assembly stays exactly on the existing local path:
    # image_motion scenes animated/finished by the local engine.
    assert config.visual_default_provider == "image_motion"
    assert config.mock_mode is False


def test_alibaba_video_strategy_routes_every_scene_to_alibaba_video():
    production = make_production("alibaba_video")
    plan = make_plan(scenes=3)
    config = baseline_config()
    apply_generation_strategy(production, config, plan)

    assert config.visual_mode == "hybrid"
    assert config.visual_default_provider == "alibaba_video"
    assert config.visual_scene_routing == {
        scene.id: "alibaba_video" for scene in plan.scenes
    }

    # Through the EXISTING durable routing resolution:
    from app.orchestrator.visual_providers import (
        build_hybrid_routing,
        resolve_scene_provider,
    )

    scene_config = config.to_scene_config_dict()
    for scene in plan.scenes:
        assert (
            resolve_scene_provider(
                scene.id, visual_mode="hybrid", scene_config=scene_config
            )
            == "alibaba_video"
        )
    routing = build_hybrid_routing(
        plan.scenes, scene_config=scene_config,
        cli_overrides=config.visual_scene_routing,
    )
    assert {item["provider"] for item in routing.values()} == {"alibaba_video"}
# ---------------------------------------------------------------------------
# 7) Alibaba Video never silently uses the local Ken-Burns pipeline
# 8) Unavailable Alibaba video fails truthfully (no silent substitution)
# ---------------------------------------------------------------------------


def test_alibaba_video_provider_is_motion_video_not_ken_burns(tmp_path):
    provider = create_visual_provider(
        "hybrid",
        scene_config={"alibaba_model": "wan2.2-t2v-plus"},
        scenes_output_dir=tmp_path,
        provider_name="alibaba_video",
    )
    from app.orchestrator.alibaba_video import (
        AlibabaVideoVisualProvider,
        FallbackVideoVisualProvider,
    )
    from app.orchestrator.visual_providers import ImageMotionVisualProvider

    assert isinstance(provider, FallbackVideoVisualProvider)
    assert isinstance(provider._primary, AlibabaVideoVisualProvider)
    assert not isinstance(provider, ImageMotionVisualProvider)
    assert provider.name == "alibaba_video"


def test_asset_gate_exempts_alibaba_video_scenes(tmp_path):
    """A prompt-driven video provider needs no source asset; the Phase 6C
    gate must not demand one (that would make the strategy unexecutable)."""
    assert PROVIDER_ALIBABA_VIDEO in GENERATION_PROVIDERS

    class _Scene:
        id = "scene_001"
        sequence = 1

    failures = preflight_assets(
        tmp_path,
        [_Scene()],
        {"scene_001": {"provider": "alibaba_video"}},
        {"assets": {}},
        legacy_resolver=lambda scene: pytest.fail(
            "alibaba_video scenes must never be legacy-asset-resolved"
        ),
    )
    assert failures == []


class _UnavailableFallback:
    name = "openrouter_video"

    def is_available(self):
        return False

    def generate_scene(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("OpenRouter fallback must never be invoked here")


def _strategy_request():
    from app.flow.scene_models import (
        FlowGenerationConfig,
        SceneGenerationRequest,
    )

    return SceneGenerationRequest(
        project_id="20260913_strategy_plan_001",
        scene_id="scene_001",
        sequence=1,
        visual_prompt="A lighthouse in a storm.",
        config=FlowGenerationConfig(
            model="wan2.2-t2v-plus",
            aspect_ratio="9:16",
            duration=5,
            count=1,
            agent_enabled=False,
        ),
    )


def test_unconfigured_alibaba_video_fails_truthfully(tmp_path, monkeypatch):
    # Hermetic isolation: this test exercises the UNCONFIGURED path; the host
    # environment may legitimately carry Alibaba credentials (Track 3 Inc 2).
    monkeypatch.delenv("ALIBABA_API_KEY", raising=False)

    provider = create_visual_provider(
        "hybrid",
        scene_config={},
        scenes_output_dir=tmp_path,
        provider_name="alibaba_video",
    )

    # Configuration-only availability is False without a credential.
    assert provider.is_available() is False

    result = provider.generate_scene(_strategy_request())
    assert result.success is False
    verification = result.verification
    assert verification["provider"] == "alibaba_video"
    assert "ALIBABA_API_KEY" in (result.error or "")
    # Non-eligible category: the classified fallback NEVER substitutes.
    from app.providers.fallback_policy import FailureCategory

    assert verification["failure_category"] == FailureCategory.CONFIGURATION
    assert verification["fallback_eligible"] is False


def test_alibaba_video_ineligible_failure_never_falls_back(tmp_path):
    from app.orchestrator.alibaba_video import (
        AlibabaVideoVisualProvider,
        FallbackVideoVisualProvider,
    )

    wrapper = FallbackVideoVisualProvider(
        tmp_path,
        primary=AlibabaVideoVisualProvider(tmp_path, api_key=""),
        fallback=_UnavailableFallback(),
        fallback_enabled=True,
    )
    result = wrapper.generate_scene(_strategy_request())
    assert result.success is False
    # The ineligible failure is returned verbatim: no fallback annotation,
    # no OpenRouter invocation (the fake fallback raises if ever called).
    assert result.verification["failure_category"] == "configuration"
    assert result.verification.get("fallback_used", False) is False
    assert result.verification.get("provider") == "alibaba_video"
# ---------------------------------------------------------------------------
# 9+10) Strategy persists and survives reload/re-read
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", GENERATION_STRATEGIES)
def test_strategy_persists_and_survives_reload(tmp_path, strategy):
    root = tmp_path / "productions"
    save_new_production(make_production(strategy), root=root)

    first = load_production(PID, root=root)
    assert first.generation_strategy == strategy
    # Re-read from disk (fresh parse, not a cached model).
    second = load_production(PID, root=root)
    assert second.generation_strategy == strategy
    # The durable artifact carries the field verbatim.
    raw = json.loads(
        (root / PID / "production.json").read_text(encoding="utf-8")
    )
    assert raw["generation_strategy"] == strategy


# ---------------------------------------------------------------------------
# Server truth: creation validation, persistence, detail exposure
# ---------------------------------------------------------------------------


def _creation_request(strategy=None):
    body = {
        "mode": "single_video",
        "creation_method": "local",
        "title": "Strategy server test",
        "brief": "A strategy wiring validation production.",
        "output_intent": "youtube_shorts",
        "target_duration": 30,
        "scene_count": 6,
        "tone": "clear and cinematic",
    }
    if strategy is not None:
        body["generation_strategy"] = strategy
    return body


def test_server_creation_defaults_to_local(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    status, payload = web.create_production_payload(_creation_request())
    assert status == 201
    assert payload["generation_strategy"] == "local"
    loaded = load_production(
        payload["production_id"], root=tmp_path / "productions"
    )
    assert loaded.generation_strategy == "local"


@pytest.mark.parametrize(
    "strategy", ["alibaba_image_local_video", "alibaba_video"]
)
def test_server_creation_persists_selected_strategy(
    tmp_path, monkeypatch, strategy
):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    status, payload = web.create_production_payload(
        _creation_request(strategy)
    )
    assert status == 201
    assert payload["generation_strategy"] == strategy
    loaded = load_production(
        payload["production_id"], root=tmp_path / "productions"
    )
    assert loaded.generation_strategy == strategy

    # Reopening/loading the production exposes the selected strategy.
    detail_status, detail = web.production_detail_payload(
        payload["production_id"]
    )
    assert detail_status == 200
    assert detail["generation_strategy"] == strategy


def test_server_creation_rejects_unknown_strategy(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    status, payload = web.create_production_payload(
        _creation_request("comfyui")
    )
    assert status == 400
    assert payload["error"] == "INVALID_CREATION_REQUEST"
    assert "generation_strategy" in payload["message"]
# ---------------------------------------------------------------------------
# Executor wiring: _config_for_unit applies the persisted strategy
# ---------------------------------------------------------------------------


def _make_artifacts(plan):
    from app.orchestrator.production_execution import UnitPlanArtifacts

    return UnitPlanArtifacts(
        plan=plan,
        brief=ContentBrief(topic="Strategy production"),
        plan_path=Path("plan.json"),
        content_hash="0" * 64,
        production_plan_version=1,
    )


def test_executor_config_for_unit_applies_strategy(tmp_path):
    from app.orchestrator.production_execution import ProductionExecutor

    production = make_production("alibaba_video")
    plan = make_plan()
    config = baseline_config()
    config.production_id = production.production_id
    config.unit_id = "unit-001"
    executor = ProductionExecutor(
        PID, productions_root=tmp_path, base_config=config
    )
    applied = executor._config_for_unit(
        production, production.units[0], _make_artifacts(plan), bound=None
    )
    assert applied.visual_mode == "hybrid"
    assert applied.visual_default_provider == "alibaba_video"
    assert applied.visual_scene_routing == {
        scene.id: "alibaba_video" for scene in plan.scenes
    }


def test_executor_strategy_is_inert_in_mock_mode(tmp_path):
    """The explicit offline/mock contract stays fully hermetic."""
    from app.orchestrator.production_execution import ProductionExecutor

    production = make_production("alibaba_video")
    plan = make_plan()
    config = baseline_config()
    config.mock_mode = True
    config.image_source = "fixture"
    config.production_id = production.production_id
    config.unit_id = "unit-001"
    executor = ProductionExecutor(
        PID, productions_root=tmp_path, base_config=config
    )
    applied = executor._config_for_unit(
        production, production.units[0], _make_artifacts(plan), bound=None
    )
    assert applied.image_source == "fixture"
    assert applied.visual_default_provider == "image_motion"


# ---------------------------------------------------------------------------
# UI source contract (repo convention: static verification, no JS framework)
# ---------------------------------------------------------------------------


def test_create_view_generation_strategy_contract():
    root = Path(web.__file__).resolve().parents[2]
    source = (
        root / "webapp" / "assets" / "js" / "views" / "create.js"
    ).read_text(encoding="utf-8")

    # The control renders with the exact three strategy values.
    assert 'id="f-strategy"' in source
    assert "'local', 'Local (Recommended)'" in source
    assert "Production path" in source  # Track 3 Inc 2: user-facing label
    assert "Uses paid API" in source  # cost-class truth on the path selector
    assert "alibaba_image_local_video" in source
    assert "Alibaba Image → Local Video" in source
    assert "'alibaba_video', 'Alibaba Video'" in source
    assert "Alibaba generates actual motion-video scenes." in source
    # Local is the default selection.
    assert 'strategy: "local"' in source
    # The selection is persisted with the creation payload.
    assert "generation_strategy: d.strategy" in source
    # Track 3 Increment 5: creator-first hierarchy with progressive disclosure.
    # The brief leads; destination and length are primary; title/path/scenes
    # are natively collapsible advanced controls; cost truth stays visible.
    assert "What are you making?" in source
    assert "<details" in source and "advanced-panel" in source
    assert 'id="f-brief"' in source
    assert 'id="f-title"' in source
    assert 'id="f-duration"' in source
    assert source.index('id="f-brief"') < source.index('id="f-duration"')
    assert source.index('id="f-duration"') < source.index("<details")
    assert source.index("<details") < source.index('id="f-title"')
    assert "Free · local tools" in source
    assert "Uses paid API" in source


def test_production_detail_displays_persisted_strategy():
    root = Path(web.__file__).resolve().parents[2]
    source = (
        root / "webapp" / "assets" / "js" / "views" / "production-detail.js"
    ).read_text(encoding="utf-8")
    assert "p.generation_strategy" in source
    # Batch 1G: the legacy generation strategy is compatibility information —
    # it must never read as the actual provider, and the first-class Route and
    # Production MODE are rendered as separate, distinct facts.
    assert "Legacy strategy (compatibility)" in source
    assert "Generation strategy" not in source
    assert "<dt>Route</dt>" in source
    assert "Not specified (legacy)" in source
    assert "<dt>Mode</dt>" in source
    assert "<dt>Workload</dt>" not in source