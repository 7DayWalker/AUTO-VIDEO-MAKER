"""
AVM Step 8 -- MODEL-FIRST PRODUCTION PLANNER ACTIVATION (offline, deterministic).

Proves the single runtime seam ``ProductionExecutor._planner_for`` and nothing
more: an explicit canonical model resolves through the EXISTING resolver and
provider binding into the EXISTING planner, while an absent id keeps the
EXISTING provider-first factory unchanged.

Fixed precedence: mock mode wins, already-pinned plan artifacts win (the
resolver is never reached), a declared canonical id activates the model-bound
resolver strictly inside that canonical model, an absent id keeps legacy
behavior, and every resolver failure propagates truthfully instead of reverting
to ``DEFAULT_LLM_PROVIDER``.

No network and no credential: the CodeCraft adapter is only CONSTRUCTED
(offline init) where a binding must really resolve.
"""

from __future__ import annotations

import json

import pytest

from app.content.model_registry import get_model
from app.content.model_resolver import (
    AllExactModelProvidersFailedError,
    ResolvedModelProvider,
    UnknownCanonicalModelError,
)
from app.content.models import (
    ContentBrief,
    ContentGenerationResult,
    ProductionMetadata,
    ProductionPlan,
    Scene,
)
from app.content.planner import ScenePlanner, create_scene_planner
from app.content.providers.base import LLMResponse
from app.orchestrator.models import OrchestratorConfig
from app.orchestrator.production_execution import ProductionExecutor
from app.production.models import Production, ProductionCommand, ProductionUnit
from app.production.storage import save_new_production

CREATED = "2026-09-30T09:00:00+00:00"
CANONICAL_ID = "qwen-3.8-flash"

#: Real registry binding for the one canonical model that exists today. Tests
#: read it from the registry instead of hardcoding a provider, so a future
#: binding addition cannot silently make a test assert the wrong provider.
_CANONICAL = get_model(CANONICAL_ID)
BINDING = next(
    binding
    for binding in _CANONICAL.bindings
    if binding.provider == _CANONICAL.default_binding
)


def make_plan(identity: str, *, scenes: int = 2) -> ProductionPlan:
    duration = 5
    return ProductionPlan(
        project_id=identity,
        title="Step 8 plan",
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


def make_production(
    *,
    canonical_model_id: str | None = None,
    production_id: str = "prd_20260930_step8_001",
) -> Production:
    """Durable intent record -- exactly the shape the creation API persists."""
    constraints = {"mode": "single_video", "output_intent": "youtube_shorts"}
    if canonical_model_id is not None:
        constraints["canonical_model_id"] = canonical_model_id
    return Production(
        production_id=production_id,
        title="Step 8 production",
        command=ProductionCommand(
            text="Create a step 8 production.",
            workload_shape="single_video",
            intended_unit_count=1,
            explicit_constraints=constraints,
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


class SentinelPlanner:
    """Stands in for the planner the provider-first factory would build."""

    canonical_model_id = None

    def __init__(self):
        self.calls = []

    def plan(self, brief: ContentBrief) -> ContentGenerationResult:
        self.calls.append(brief)
        return ContentGenerationResult.from_plan(
            make_plan(f"plan_{brief.production_context['unit_id']}"),
            provider="fake",
            model="fake",
        )


def forbidden_factory():
    raise AssertionError("legacy provider-first planner factory must not be used")


def resolver_must_not_run(*_args, **_kwargs):
    raise AssertionError("model resolver must not be invoked here")


def default_provider_must_not_run(*_args, **_kwargs):
    raise AssertionError("DEFAULT_LLM_PROVIDER must not be reached")


def build_executor(
    tmp_path,
    production: Production,
    *,
    mock_mode: bool = False,
    planner_factory=SentinelPlanner,
    save: bool = True,
):
    if save:
        save_new_production(production, root=tmp_path / "productions")
    kwargs = {} if planner_factory is None else {"planner_factory": planner_factory}
    return ProductionExecutor(
        production.production_id,
        productions_root=tmp_path / "productions",
        projects_root=tmp_path / "projects",
        base_config=OrchestratorConfig(
            topic="Step 8 production",
            mock_mode=mock_mode,
            target_duration=10,
            number_of_scenes=2,
            visual_mode="hybrid",
        ),
        **kwargs,
    )


@pytest.fixture()
def codecraft_configured(monkeypatch):
    """Make the configured-binding check pass WITHOUT any real credential."""
    monkeypatch.setenv("CODECRAFT_API_KEY", "step8-offline-placeholder-key")
    monkeypatch.delenv("CODECRAFT_BASE_URL", raising=False)
    monkeypatch.delenv("CODECRAFT_TEXT_MODEL", raising=False)


@pytest.fixture()
def codecraft_unconfigured(monkeypatch):
    monkeypatch.delenv("CODECRAFT_API_KEY", raising=False)


@pytest.fixture()
def no_default_provider(monkeypatch):
    """Any revert to the provider-first default becomes a test failure."""
    monkeypatch.setattr(
        "app.content.providers.get_default_provider", default_provider_must_not_run
    )


# ---------------------------------------------------------------------------
# Test 1 -- no canonical model: legacy provider-first path, resolver dormant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("declared", [None, "", "   "])
def test_undeclared_canonical_model_keeps_legacy_factory(tmp_path, monkeypatch, declared):
    monkeypatch.setattr("app.content.model_resolver.resolve_model", resolver_must_not_run)
    executor = build_executor(tmp_path, make_production(canonical_model_id=declared))
    assert isinstance(executor._planner_for(executor.load()), SentinelPlanner)


def test_non_string_canonical_value_is_treated_as_undeclared(tmp_path, monkeypatch):
    monkeypatch.setattr("app.content.model_resolver.resolve_model", resolver_must_not_run)
    executor = build_executor(tmp_path, make_production())
    loaded = executor.load()
    loaded.command.explicit_constraints["canonical_model_id"] = 12345
    assert isinstance(executor._planner_for(loaded), SentinelPlanner)


# ---------------------------------------------------------------------------
# Test 2 -- explicit canonical model: resolver activates, legacy path bypassed
# ---------------------------------------------------------------------------


def test_declared_canonical_model_activates_model_first_planner(
    tmp_path, monkeypatch, codecraft_configured
):
    monkeypatch.setattr(
        "app.content.providers.get_default_provider", default_provider_must_not_run
    )
    executor = build_executor(
        tmp_path, make_production(canonical_model_id=CANONICAL_ID),
        planner_factory=forbidden_factory,
    )
    planner = executor._planner_for(executor.load())

    assert isinstance(planner, ScenePlanner)
    # Canonical identity survives to the AI Brain execution boundary.
    assert planner.canonical_model_id == CANONICAL_ID
    # The binding comes from the registry, never from a provider choice.
    assert planner.provider.name == BINDING.provider
    assert planner.provider.model == BINDING.provider_model_id
    # No intra-model fallback happened, so no fallback evidence is invented.
    assert planner.resolution_trace == ()


def test_canonical_identity_reaches_the_generator(tmp_path, codecraft_configured):
    executor = build_executor(
        tmp_path, make_production(canonical_model_id=CANONICAL_ID),
        planner_factory=forbidden_factory,
    )
    planner = executor._planner_for(executor.load())
    assert planner.generator.canonical_model_id == CANONICAL_ID
    assert planner.provider is planner.generator.provider


def test_padded_canonical_id_resolves_after_trim(tmp_path, codecraft_configured):
    executor = build_executor(
        tmp_path, make_production(canonical_model_id=f"  {CANONICAL_ID}  "),
        planner_factory=forbidden_factory,
    )
    assert executor._planner_for(executor.load()).canonical_model_id == CANONICAL_ID


# ---------------------------------------------------------------------------
# Test 3 -- mock mode always wins: no resolution, no real-provider attribution
# ---------------------------------------------------------------------------


def test_mock_mode_wins_over_declared_canonical_model(tmp_path, monkeypatch):
    monkeypatch.setattr("app.content.model_resolver.resolve_model", resolver_must_not_run)
    executor = build_executor(
        tmp_path,
        make_production(canonical_model_id=CANONICAL_ID),
        mock_mode=True,
    )
    assert isinstance(executor._planner_for(executor.load()), SentinelPlanner)


def test_mock_mode_with_default_factory_is_never_attributed_to_a_real_model(
    tmp_path, monkeypatch, codecraft_configured
):
    """Default factory + mock mode: mock planner, canonical identity UNKNOWN."""
    monkeypatch.setattr("app.content.model_resolver.resolve_model", resolver_must_not_run)
    executor = build_executor(
        tmp_path,
        make_production(canonical_model_id=CANONICAL_ID),
        mock_mode=True,
        planner_factory=None,
    )
    planner = executor._planner_for(executor.load())
    assert planner.provider.name == "mock"
    assert planner.canonical_model_id is None


def test_mock_execution_records_no_model_first_provenance(tmp_path, monkeypatch):
    """A mock plan is not model-first evidence: no canonical id is persisted."""
    monkeypatch.setattr("app.content.model_resolver.resolve_model", resolver_must_not_run)
    executor = build_executor(
        tmp_path,
        make_production(canonical_model_id=CANONICAL_ID),
        mock_mode=True,
    )
    loaded = executor.load()
    executor.resolve_plan(loaded, loaded.units[0])
    meta = json.loads(
        (executor._plan_directory("unit-001") / "plan_meta.json").read_text("utf-8")
    )
    assert meta["planner_provider"] == "fake"
    # Unknown canonical identity is recorded as ABSENCE (never defaulted to a
    # real model), exactly the established evidence convention.
    assert "canonical_model_id" not in meta["planner_provenance"]


# ---------------------------------------------------------------------------
# Test 4 -- unknown canonical model fails truthfully, no substitution
# ---------------------------------------------------------------------------


def test_unknown_canonical_model_raises_and_never_substitutes(
    tmp_path, monkeypatch, no_default_provider
):
    monkeypatch.setattr(
        "app.content.planner.create_scene_planner", default_provider_must_not_run
    )
    executor = build_executor(
        tmp_path,
        make_production(canonical_model_id="gpt-5-turbo-mini"),
        planner_factory=forbidden_factory,
    )
    with pytest.raises(UnknownCanonicalModelError):
        executor._planner_for(executor.load())


def test_unknown_canonical_model_fails_the_plan_truthfully(tmp_path, monkeypatch):
    executor = build_executor(
        tmp_path,
        make_production(canonical_model_id="not-a-registered-model"),
        planner_factory=forbidden_factory,
    )
    loaded = executor.load()
    with pytest.raises(UnknownCanonicalModelError):
        executor.resolve_plan(loaded, loaded.units[0])
    # No partial plan artifacts were written for a failed resolution.
    assert not list(executor._plan_directory("unit-001").glob("*.json"))


# ---------------------------------------------------------------------------
# Test 5 -- provider binding unavailable: resolver error propagates, no revert
# ---------------------------------------------------------------------------


def test_unavailable_binding_fails_truthfully_without_default_revert(
    tmp_path, monkeypatch, codecraft_unconfigured, no_default_provider
):
    monkeypatch.setattr(
        "app.content.planner.create_scene_planner", default_provider_must_not_run
    )
    executor = build_executor(
        tmp_path,
        make_production(canonical_model_id=CANONICAL_ID),
        planner_factory=forbidden_factory,
    )
    with pytest.raises(AllExactModelProvidersFailedError) as caught:
        executor._planner_for(executor.load())

    # Same-canonical-model scope is preserved: the failure names the declared
    # model and the binding that was attempted -- never a different model.
    assert caught.value.canonical_model_id == CANONICAL_ID
    attempted = {entry["provider"]: entry.get("category") for entry in caught.value.attempted}
    assert BINDING.provider in attempted
    assert attempted[BINDING.provider]


# ---------------------------------------------------------------------------
# Test 6 -- pinned / reusable plan artifacts outrank the resolver
# ---------------------------------------------------------------------------


def test_pinned_plan_is_reused_and_the_resolver_never_runs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.content.model_resolver.resolve_model", resolver_must_not_run)
    production = make_production(canonical_model_id=CANONICAL_ID)
    first = build_executor(tmp_path, production, mock_mode=True)
    loaded = first.load()
    pinned = first.resolve_plan(loaded, loaded.units[0])

    # The SAME declared intent, now on a non-mock executor with a forbidden
    # factory: recorded artifacts are still the authority, so neither the
    # factory nor the resolver may run.
    second = build_executor(
        tmp_path, production, mock_mode=False,
        planner_factory=forbidden_factory, save=False,
    )
    reused_prod = second.load()
    reused = second.resolve_plan(reused_prod, reused_prod.units[0])
    assert reused.content_hash == pinned.content_hash
    assert reused.plan_path == pinned.plan_path


# ---------------------------------------------------------------------------
# Test 7 -- default factory vs injected factory must not diverge
# ---------------------------------------------------------------------------


def test_default_and_injected_factories_do_not_diverge(tmp_path, codecraft_configured):
    # Legacy (no declared model): each executor keeps ITS OWN factory.
    legacy_default = build_executor(
        tmp_path,
        make_production(production_id="prd_20260930_step8_legacy_a_001"),
        mock_mode=True,
        planner_factory=None,
    )
    legacy_injected = build_executor(
        tmp_path,
        make_production(production_id="prd_20260930_step8_legacy_b_001"),
        mock_mode=True,
        planner_factory=SentinelPlanner,
    )
    assert legacy_default._planner_for(legacy_default.load()).provider.name == "mock"
    assert isinstance(legacy_injected._planner_for(legacy_injected.load()), SentinelPlanner)

    # Model-first (declared model): BOTH factories are bypassed identically.
    model_first_default = build_executor(
        tmp_path,
        make_production(
            production_id="prd_20260930_step8_first_a_001", canonical_model_id=CANONICAL_ID
        ),
        planner_factory=None,
    )
    model_first_injected = build_executor(
        tmp_path,
        make_production(
            production_id="prd_20260930_step8_first_b_001", canonical_model_id=CANONICAL_ID
        ),
        planner_factory=create_scene_planner,
    )
    assert (
        model_first_default._planner_for(model_first_default.load()).canonical_model_id
        == CANONICAL_ID
    )
    assert (
        model_first_injected._planner_for(model_first_injected.load()).canonical_model_id
        == CANONICAL_ID
    )


# ---------------------------------------------------------------------------
# Test 8 -- persisted planner evidence is allow-listed, never a secret sink
# ---------------------------------------------------------------------------


class HostileMetadataProvider:
    """A configured provider that ALSO reports secrets in its raw metadata."""

    name = BINDING.provider
    model = BINDING.provider_model_id

    def generate_json(self, **_kwargs) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(make_plan("plan_step8_evidence").model_dump()),
            provider=self.name,
            model=self.model,
            usage={"completion_tokens": 11, "prompt_tokens": 7, "total_tokens": 18},
            metadata={
                # Legitimate evidence keys.
                "request_id": "req_step8_offline_0001",
                "fallback_used": False,
                "primary_provider": self.name,
                "final_provider": self.name,
                # Hostile keys/values that must NEVER be persisted.
                "api_key": "sk-live-supersecret-value",
                "authorization": "Bearer sk-live-supersecret-value",
                "prompt": "full system prompt text that must not persist",
                "source_path": "C:/Users/secret/user/.env",
                "raw_response": {"headers": {"authorization": "Bearer x"}},
            },
        )


def test_persisted_planner_evidence_is_allow_listed(
    tmp_path, monkeypatch, no_default_provider
):
    resolved = ResolvedModelProvider(
        canonical_model_id=CANONICAL_ID,
        provider=BINDING.provider,
        provider_model_id=BINDING.provider_model_id,
        provider_instance=HostileMetadataProvider(),
        attempted=(),
    )
    monkeypatch.setattr(
        "app.content.model_resolver.resolve_model", lambda *a, **k: resolved
    )
    executor = build_executor(
        tmp_path,
        make_production(
            production_id="prd_20260930_step8_evidence_001", canonical_model_id=CANONICAL_ID
        ),
        planner_factory=forbidden_factory,
    )
    loaded = executor.load()
    executor.resolve_plan(loaded, loaded.units[0])

    meta_path = executor._plan_directory("unit-001") / "plan_meta.json"
    text = meta_path.read_text("utf-8")
    meta = json.loads(text)
    provenance = meta["planner_provenance"]

    # Execution evidence IS recorded: canonical intent + resolved identity.
    assert provenance["canonical_model_id"] == CANONICAL_ID
    assert provenance["provider"] == BINDING.provider
    assert provenance["model"] == BINDING.provider_model_id
    assert provenance["request_id"] == "req_step8_offline_0001"
    assert provenance["fallback_used"] is False
    assert provenance["source"] == "generated"
    # Accounting reuses the video engine's authority: the final attempt carries
    # the canonical identity, and cost stays UNKNOWN instead of being invented.
    attempt = meta["planner_accounting"]["attempts"][-1]
    assert attempt["canonical_model_id"] == CANONICAL_ID
    assert attempt["provider"] == BINDING.provider
    assert attempt["billing_status"] == "unknown"
    assert attempt["cost"] is None
    assert attempt["cost_source"] == "none"

    # Nothing secret, no prompt, no raw body, no path leaked into the artifact.
    for forbidden in (
        "sk-live-supersecret-value",
        "api_key",
        "authorization",
        "Bearer",
        "raw_response",
        "supersecret",
        "C:/Users/secret",
        "full system prompt",
    ):
        assert forbidden not in text



