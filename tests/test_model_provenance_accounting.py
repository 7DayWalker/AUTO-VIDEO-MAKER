"""
AVM Step 6 -- Provenance + Accounting hardening (offline, deterministic).

Proves the model-centric AI Brain execution is truthfully traceable through the
EXISTING AVM provenance/accounting authority, with the four identities kept
distinct:

    canonical_model_id : qwen-3.8-flash   (user-facing identity)
    provider           : codecraft        (internal execution detail)
    model              : qwen3.8-27b      (provider wire id)
    request_id         : provider-supplied, else ABSENT (never invented)

No network and no billable call: the CodeCraft transport is a deterministic
double and no live probe is attempted. No historical production record is read,
backfilled or rewritten -- every filesystem assertion runs inside ``tmp_path``.
"""

from __future__ import annotations

import json

import pytest

from app.content.execution_evidence import (
    SOURCE_GENERATED,
    normalize_llm_verification,
)
from app.content.models import ContentBrief, ContentGenerationResult
from app.content.providers.base import BaseLLMProvider, LLMResponse
from app.content.providers.mock import MOCK_PRODUCTION_PLAN
from app.orchestrator import planner_evidence, scene_execution
from app.orchestrator.planner_evidence import (
    CANONICAL_MODEL_ID_KEY,
    REQUEST_ID_KIND_LLM_RESPONSE,
    build_planner_accounting,
    build_planner_provenance,
)

CANONICAL_QWEN = "qwen-3.8-flash"
CODECRAFT = "codecraft"
CODECRAFT_WIRE = "qwen3.8-27b"
CODECRAFT_REQUEST_ID = "cc_req_abc123"
CODECRAFT_USAGE = {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33}

BRIEF_TOPIC = "5 strange facts about the ocean"

#: The established provenance vocabulary (owned by the video engine's
#: ``build_scene_provenance``). Step 6 may ONLY add ``canonical_model_id`` and
#: ``resolution_trace``; anything else would be a competing vocabulary.
ESTABLISHED_PROVENANCE_KEYS = {
    "provider",
    "model",
    "request_id",
    "request_id_kind",
    "fallback_used",
    "primary_provider",
    "fallback_reason",
    "source",
}
ADDITIVE_PROVENANCE_KEYS = {"canonical_model_id", "resolution_trace"}


def make_brief() -> ContentBrief:
    return ContentBrief(topic=BRIEF_TOPIC)


def project(result: ContentGenerationResult) -> tuple:
    """Project one AI Brain result through the persistence-seam builders."""
    kwargs = dict(
        canonical_model_id=result.canonical_model_id,
        provider=result.provider,
        model=result.model,
        verification=result.verification,
    )
    return build_planner_provenance(**kwargs), build_planner_accounting(**kwargs)


class StubLLM(BaseLLMProvider):
    """Deterministic offline provider double returning the mock plan."""

    def __init__(self, name, model, *, usage=None, request_id=None, extra_metadata=None):
        self._name = name
        self._model = model
        self._usage = usage
        metadata = {}
        if request_id:
            metadata["request_id"] = request_id
        metadata.update(extra_metadata or {})
        self._metadata = metadata or None

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    def generate(self, prompt, system_prompt=None, response_schema=None,
                 temperature=0.7, max_tokens=4096, **kwargs) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(MOCK_PRODUCTION_PLAN),
            provider=self._name,
            model=self._model,
            usage=self._usage,
            metadata=self._metadata,
        )

    def is_available(self) -> bool:
        return True


class StubUnavailable(BaseLLMProvider):
    """A registered binding that is locally unavailable (fallback-eligible)."""

    def __init__(self, name, model):
        self._name = name
        self._model = model

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    def generate(self, *args, **kwargs) -> LLMResponse:  # pragma: no cover
        raise AssertionError("an unavailable provider must never be called")

    def is_available(self) -> bool:
        return False


@pytest.fixture
def codecraft_offline(monkeypatch):
    """The REAL CodeCraft adapter with a deterministic offline transport.

    No ``CODECRAFT_API_KEY`` is required for exact resolution and no HTTP
    request is ever made: ``_post`` is replaced, so this exercises the real
    response normalization (``raw["id"]`` -> ``request_id``, ``usage``
    pass-through) without a live or billable probe.
    """
    from app.content.providers import codecraft as cc

    # A configured-but-unused credential: the transport below is replaced, so
    # this key is never sent anywhere and no HTTP request is made.
    monkeypatch.setenv("CODECRAFT_API_KEY", "sk-offline-test-not-used")
    payload = {
        "id": CODECRAFT_REQUEST_ID,
        "choices": [{"message": {"content": json.dumps(MOCK_PRODUCTION_PLAN)}}],
        "usage": dict(CODECRAFT_USAGE),
    }
    monkeypatch.setattr(
        cc.CodeCraftProvider, "_post", lambda self, p: json.loads(json.dumps(payload))
    )
    return payload


@pytest.fixture
def codecraft_no_evidence(monkeypatch):
    """Real CodeCraft adapter returning NEITHER a request id NOR usage."""
    from app.content.providers import codecraft as cc

    monkeypatch.setenv("CODECRAFT_API_KEY", "sk-offline-test-not-used")
    payload = {
        "choices": [{"message": {"content": json.dumps(MOCK_PRODUCTION_PLAN)}}],
    }
    monkeypatch.setattr(
        cc.CodeCraftProvider, "_post", lambda self, p: json.loads(json.dumps(payload))
    )
    return payload


# ---------------------------------------------------------------------------
# Step 21 - ONE authority: no second provenance/accounting system
# ---------------------------------------------------------------------------


def test_accounting_reuses_the_existing_single_authority():
    """The AI Brain uses the video engine's OWN accounting functions."""
    assert (
        planner_evidence.build_operation_accounting
        is scene_execution.build_operation_accounting
    )
    assert (
        planner_evidence.summarize_accounting_attempts
        is scene_execution.summarize_accounting_attempts
    )


def test_status_vocabulary_is_identical_not_parallel():
    assert (
        planner_evidence.BILLING_STATUS_UNKNOWN
        == scene_execution.BILLING_STATUS_UNKNOWN
        == "unknown"
    )
    assert (
        planner_evidence.COST_SOURCE_NONE == scene_execution.COST_SOURCE_NONE == "none"
    )
    assert SOURCE_GENERATED == "generated"


def test_provenance_vocabulary_is_established_plus_one_additive_concept():
    produced = set(
        build_planner_provenance(
            canonical_model_id=CANONICAL_QWEN,
            provider=CODECRAFT,
            model=CODECRAFT_WIRE,
            verification={"request_id": CODECRAFT_REQUEST_ID},
        )
    )
    assert produced <= ESTABLISHED_PROVENANCE_KEYS | ADDITIVE_PROVENANCE_KEYS
    # ``canonical_model_id`` is the ONLY additive concept on this path.
    assert produced & ADDITIVE_PROVENANCE_KEYS == {CANONICAL_MODEL_ID_KEY}


def test_source_keeps_its_established_meaning():
    """``source`` is not overloaded with canonical model identity."""
    provenance = build_planner_provenance(
        canonical_model_id=CANONICAL_QWEN, provider=CODECRAFT, model=CODECRAFT_WIRE
    )
    assert provenance["source"] == "generated"
    assert CANONICAL_QWEN not in provenance["source"]


# ---------------------------------------------------------------------------
# A / B / C / D / F / G - successful CodeCraft execution
# ---------------------------------------------------------------------------


def run_resolved_qwen():
    """Resolve qwen-3.8-flash and run the AI Brain offline end-to-end."""
    from app.content.model_resolver import resolve_model
    from app.content.planner import planner_from_resolved_model

    resolved = resolve_model(CANONICAL_QWEN)
    assert resolved.provider == CODECRAFT
    assert resolved.provider_model_id == CODECRAFT_WIRE
    assert resolved.attempted == ()

    planner = planner_from_resolved_model(resolved)
    result = planner.plan(make_brief())
    assert result.success is True, result.error
    return resolved, result


def test_canonical_identity_survives_resolution(codecraft_offline):
    """A: the user-facing model identity reaches execution evidence."""
    _resolved, result = run_resolved_qwen()
    assert result.canonical_model_id == CANONICAL_QWEN
    provenance, _accounting = project(result)
    assert provenance[CANONICAL_MODEL_ID_KEY] == CANONICAL_QWEN


def test_provider_and_provider_model_stay_distinct(codecraft_offline):
    """B + C: provider and wire model are never collapsed into each other."""
    _resolved, result = run_resolved_qwen()
    # Existing flat fields keep their ESTABLISHED meaning (wire identity).
    assert result.provider == CODECRAFT
    assert result.model == CODECRAFT_WIRE

    provenance, accounting = project(result)
    assert provenance["provider"] == CODECRAFT
    assert provenance["model"] == CODECRAFT_WIRE
    assert provenance[CANONICAL_MODEL_ID_KEY] == CANONICAL_QWEN
    # Three distinct identities, none equal to another.
    assert len({
        provenance[CANONICAL_MODEL_ID_KEY],
        provenance["provider"],
        provenance["model"],
    }) == 3

    attempt = accounting["attempts"][-1]
    assert attempt["provider"] == CODECRAFT
    assert attempt["model"] == CODECRAFT_WIRE
    assert attempt[CANONICAL_MODEL_ID_KEY] == CANONICAL_QWEN


def test_successful_codecraft_records_no_fallback(codecraft_offline):
    """D: single real binding -> fallback_used is a KNOWN false."""
    _resolved, result = run_resolved_qwen()
    provenance, accounting = project(result)
    assert provenance["fallback_used"] is False
    assert "primary_provider" not in provenance
    assert "fallback_reason" not in provenance
    assert "resolution_trace" not in provenance
    assert accounting["summary"]["attempt_count"] == 1
    assert len(accounting["attempts"]) == 1


def test_request_id_survives_normalization(codecraft_offline):
    """F: the provider's own request id reaches the evidence verbatim."""
    _resolved, result = run_resolved_qwen()
    assert result.verification["request_id"] == CODECRAFT_REQUEST_ID
    provenance, accounting = project(result)
    assert provenance["request_id"] == CODECRAFT_REQUEST_ID
    assert provenance["request_id_kind"] == REQUEST_ID_KIND_LLM_RESPONSE
    assert accounting["summary"]["final_request_id"] == CODECRAFT_REQUEST_ID


def test_usage_survives_normalization(codecraft_offline):
    """G: provider-reported usage is passed through, never recomputed."""
    _resolved, result = run_resolved_qwen()
    assert result.verification["usage"] == CODECRAFT_USAGE
    provenance, accounting = project(result)
    assert accounting["attempts"][-1]["usage"] == CODECRAFT_USAGE
    assert accounting["summary"]["final_usage"] == CODECRAFT_USAGE
    assert "usage" not in provenance  # provenance is not accounting


# ---------------------------------------------------------------------------
# H / I / J - UNKNOWN stays UNKNOWN (never zero, never invented)
# ---------------------------------------------------------------------------


def test_missing_usage_stays_unknown_not_zero(codecraft_no_evidence):
    """H: absent usage is never reinterpreted as zero usage."""
    _resolved, result = run_resolved_qwen()
    assert "usage" not in result.verification

    _provenance, accounting = project(result)
    attempt = accounting["attempts"][-1]
    assert attempt["usage"] == {}
    assert not attempt["usage"]
    assert "total_tokens" not in attempt["usage"]
    assert accounting["summary"]["final_usage"] == {}
    # Absent usage must not become an absent-cost zero either.
    assert attempt["cost"] is None


def test_missing_request_id_is_absent_never_invented(codecraft_no_evidence):
    """I: no provider request id -> the key is absent, never a local UUID."""
    _resolved, result = run_resolved_qwen()
    assert "request_id" not in result.verification

    provenance, accounting = project(result)
    assert "request_id" not in provenance
    assert "request_id_kind" not in provenance
    assert accounting["attempts"][-1]["request_id"] is None
    assert accounting["summary"]["final_request_id"] is None


def test_missing_codecraft_cost_is_never_zero(codecraft_offline):
    """J: no authoritative CodeCraft cost -> UNKNOWN, never 0 / never free."""
    _resolved, result = run_resolved_qwen()
    _provenance, accounting = project(result)
    attempt = accounting["attempts"][-1]

    assert attempt["cost"] is None
    assert attempt["cost"] != 0
    assert attempt["cost_source"] == "none"
    assert attempt["currency"] is None
    # "No cost data" is NOT "free": the billing state stays unknown and is
    # never the known-non-billable class.
    assert attempt["billing_status"] == "unknown"
    assert attempt["billing_status"] != "non_billable"
    assert attempt["billing_status"] != "billable"

    summary = accounting["summary"]
    assert summary["cost"] is None
    assert summary["billing_status"] == "unknown"
    assert summary["cost_source"] == "none"
    assert summary["currency"] is None
    assert summary["partial"] is False


# ---------------------------------------------------------------------------
# E / N - PROVIDER fallback WITHIN ONE canonical model
# ---------------------------------------------------------------------------

SYN_ID = "synthetic-qwen-provenance-test"
SYN_A = "syn-prov-a"
SYN_B = "syn-prov-b"
SYN_WIRE_A = "syn-wire-a"
SYN_WIRE_B = "syn-wire-b"
SYN_REQUEST_ID = "syn_req_9"


@pytest.fixture
def syn_two_binding_model():
    """Synthetic canonical model: binding A (unavailable) then B (succeeds).

    Registration and registry mutations are fully reverted, so no other test
    observes this model and no real binding is disturbed.
    """
    import app.content.provider_registry as preg
    from app.content.model_registry import (
        MODEL_REGISTRY,
        CanonicalModel,
        ProviderBinding,
    )

    factories = {
        SYN_A: lambda mid=None: StubUnavailable(SYN_A, mid or SYN_WIRE_A),
        SYN_B: lambda mid=None: StubLLM(
            SYN_B,
            mid or SYN_WIRE_B,
            usage={"total_tokens": 5},
            request_id=SYN_REQUEST_ID,
        ),
    }
    saved = {}
    for name, factory in factories.items():
        if preg.has_provider(name):
            saved[name] = preg._REGISTRY[name]
            preg._REGISTRY.pop(name, None)
        preg.register_provider(name, factory)

    model = CanonicalModel(
        id=SYN_ID,
        display_name="Synthetic Qwen Provenance",
        family="test",
        bindings=(
            ProviderBinding(provider=SYN_A, provider_model_id=SYN_WIRE_A),
            ProviderBinding(provider=SYN_B, provider_model_id=SYN_WIRE_B),
        ),
    )
    MODEL_REGISTRY[SYN_ID] = model
    try:
        yield model
    finally:
        MODEL_REGISTRY.pop(SYN_ID, None)
        for name in factories:
            preg._REGISTRY.pop(name, None)
            if name in saved:
                preg._REGISTRY[name] = saved[name]


def run_resolved_synthetic():
    """Resolve the synthetic model with intra-model provider fallback."""
    from app.content.model_resolver import resolve_model
    from app.content.planner import planner_from_resolved_model

    resolved = resolve_model(SYN_ID, try_next_on_failure=True)
    result = planner_from_resolved_model(resolved).plan(make_brief())
    assert result.success is True, result.error
    return resolved, result


def test_final_successful_provider_is_authoritative(syn_two_binding_model):
    """N: provider_a fails, provider_b succeeds -> provider is provider_b."""
    resolved, result = run_resolved_synthetic()
    assert resolved.provider == SYN_B
    assert result.provider == SYN_B

    provenance, accounting = project(result)
    assert provenance["provider"] == SYN_B
    assert provenance["provider"] != SYN_A
    assert accounting["summary"]["final_provider"] == SYN_B
    assert accounting["attempts"][-1]["provider"] == SYN_B


def test_same_model_fallback_preserves_primary_and_canonical(syn_two_binding_model):
    """E: primary provider preserved, canonical model unchanged by fallback."""
    _resolved, result = run_resolved_synthetic()
    provenance, accounting = project(result)

    assert provenance[CANONICAL_MODEL_ID_KEY] == SYN_ID
    assert provenance["primary_provider"] == SYN_A
    assert provenance["provider"] == SYN_B
    assert provenance["fallback_used"] is True
    assert provenance["fallback_reason"] == "provider_unavailable"
    assert provenance["model"] == SYN_WIRE_B

    # PROVIDER fallback within ONE canonical model: the failed binding is
    # evidenced and no second model identity appears.
    assert [t["provider"] for t in provenance["resolution_trace"]] == [SYN_A]
    assert provenance["resolution_trace"][0]["provider_model_id"] == SYN_WIRE_A

    assert len(accounting["attempts"]) == 2
    primary, final = accounting["attempts"]
    assert primary["provider"] == SYN_A
    assert final["provider"] == SYN_B
    assert primary[CANONICAL_MODEL_ID_KEY] == SYN_ID
    assert final[CANONICAL_MODEL_ID_KEY] == SYN_ID
    assert primary["attempt_number"] == 1
    assert final["attempt_number"] == 2
    assert accounting["summary"]["attempt_count"] == 2


def test_failed_attempt_gets_no_fabricated_identity(syn_two_binding_model):
    """Step 13: no request id / usage / cost invented for a failed attempt."""
    _resolved, result = run_resolved_synthetic()
    _provenance, accounting = project(result)
    primary = accounting["attempts"][0]

    assert primary["request_id"] is None
    assert primary["request_id_kind"] is None
    assert primary["usage"] == {}
    assert primary["cost"] is None
    assert primary["cost_source"] == "none"
    assert primary["billing_status"] == "unknown"
    # The failed binding's wire model was never executed, so it stays unknown.
    assert primary["model"] is None
    # The successful attempt keeps its own real evidence.
    assert accounting["attempts"][-1]["request_id"] == SYN_REQUEST_ID


# ---------------------------------------------------------------------------
# K / L / M / O - no backfill, no fabricated identity, no cross-model leakage
# ---------------------------------------------------------------------------


def test_legacy_records_are_never_backfilled_or_inferred(tmp_path):
    """K: historical evidence stays exactly as written; unknown beats inferred."""
    legacy_meta = {
        "production_id": "prd_legacy",
        "unit_id": "unit-001",
        "production_plan_version": 1,
        "unit_plan_version": 1,
        "plan_identity": "legacy_plan",
        "content_hash": "a" * 64,
        "planner_provider": "openrouter",
        "planner_model": "some/wire-model",
        "planner_warnings": [],
    }
    path = tmp_path / "plan_meta.json"
    path.write_text(json.dumps(legacy_meta), encoding="utf-8")
    before = path.read_bytes()

    meta = json.loads(path.read_text(encoding="utf-8"))
    # No canonical identity existed then, so none is invented now.
    assert meta.get("planner_provenance") is None
    assert meta.get("planner_accounting") is None
    assert CANONICAL_MODEL_ID_KEY not in json.dumps(meta)
    assert path.read_bytes() == before

    # A legacy in-memory result keeps the exact pre-Step-6 shape.
    result = ContentGenerationResult.from_plan(
        plan=None, provider="openrouter", model="some/wire-model"
    )
    assert result.canonical_model_id is None
    assert result.verification == {}

    provenance, accounting = project(result)
    assert CANONICAL_MODEL_ID_KEY not in provenance
    # The provider wire model is NOT promoted to a canonical identity.
    assert provenance["model"] == "some/wire-model"
    assert CANONICAL_MODEL_ID_KEY not in accounting["attempts"][-1] or (
        accounting["attempts"][-1][CANONICAL_MODEL_ID_KEY] is None
    )


def test_local_provider_gets_no_fabricated_model_identity():
    """L: local execution keeps its established local semantics."""
    response = LLMResponse(content="{}", provider="image_motion", model="local_asset")
    verification = normalize_llm_verification(response)
    result = ContentGenerationResult.from_plan(
        plan=None, provider="image_motion", model="local_asset",
        verification=verification,
    )
    provenance, accounting = project(result)

    assert CANONICAL_MODEL_ID_KEY not in provenance
    assert provenance["provider"] == "image_motion"
    assert provenance["model"] == "local_asset"
    blob = json.dumps({"p": provenance, "a": accounting}).lower()
    assert "qwen" not in blob
    assert "codecraft" not in blob


def test_mock_execution_is_never_attributed_to_codecraft_or_qwen():
    """M: mock stays mock -- no CodeCraft/Qwen identity is attached."""
    from app.content.planner import create_scene_planner

    result = create_scene_planner(mock_mode=True).plan(make_brief())
    assert result.success is True
    assert result.provider == "mock"
    assert result.canonical_model_id is None

    provenance, accounting = project(result)
    assert CANONICAL_MODEL_ID_KEY not in provenance
    assert provenance["provider"] == "mock"
    assert provenance["model"] == "mock-model"
    assert provenance["fallback_used"] is False

    blob = json.dumps({"p": provenance, "a": accounting}).lower()
    assert "qwen" not in blob
    assert "codecraft" not in blob
    assert CANONICAL_QWEN not in blob


def test_cross_model_identity_cannot_leak(syn_two_binding_model):
    """O: another canonical model never appears in a Qwen execution's evidence."""
    qwen_provenance = build_planner_provenance(
        canonical_model_id=CANONICAL_QWEN, provider=CODECRAFT, model=CODECRAFT_WIRE
    )
    assert SYN_ID not in json.dumps(qwen_provenance)

    _resolved, result = run_resolved_synthetic()
    syn_provenance, syn_accounting = project(result)
    blob = json.dumps({"p": syn_provenance, "a": syn_accounting})
    assert CANONICAL_QWEN not in blob
    assert CODECRAFT not in blob
    assert CODECRAFT_WIRE not in blob


def test_unknown_canonical_model_is_never_substituted():
    """Exact-model only: an unregistered model fails instead of degrading."""
    from app.content.model_resolver import (
        UnknownCanonicalModelError,
        resolve_model,
    )

    with pytest.raises(UnknownCanonicalModelError):
        resolve_model("google/gemma-4-26b-a4b-it:free")


# ---------------------------------------------------------------------------
# Step 18 / 19 - persistence seam and the existing API surface
# ---------------------------------------------------------------------------


def test_plan_meta_records_model_first_evidence(tmp_path):
    """The persistence seam writes additive evidence beside the legacy keys."""
    from app.orchestrator.models import OrchestratorConfig
    from app.orchestrator.production_execution import ProductionExecutor
    from app.production.storage import save_new_production, unit_plan_dir
    from tests.test_plan_content_api import PID, UNIT_ID, make_plan, make_production

    productions = tmp_path / "productions"
    projects = tmp_path / "projects"
    save_new_production(make_production(), root=productions)

    class ResolverPlanner:
        """Stands in for a planner built by ``planner_from_resolved_model``."""

        def plan(self, brief):
            return ContentGenerationResult.from_plan(
                make_plan(),
                provider=CODECRAFT,
                model=CODECRAFT_WIRE,
                canonical_model_id=CANONICAL_QWEN,
                verification={
                    "provider": CODECRAFT,
                    "model": CODECRAFT_WIRE,
                    "request_id": CODECRAFT_REQUEST_ID,
                    "usage": dict(CODECRAFT_USAGE),
                },
            )

    base = OrchestratorConfig(
        topic="Step 6 provenance",
        mock_mode=True,
        visual_mode="local",
        visual_default_provider="image_motion",
        image_source="auto",
    )
    executor = ProductionExecutor(
        PID,
        productions_root=productions,
        projects_root=projects,
        base_config=base,
        planner_factory=lambda: ResolverPlanner(),
    )
    production = executor.load()
    executor.resolve_plan(production, production.units[0])

    meta_path = unit_plan_dir(PID, UNIT_ID, root=productions) / "plan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # Existing flat keys keep their established meaning (never renamed).
    assert meta["planner_provider"] == CODECRAFT
    assert meta["planner_model"] == CODECRAFT_WIRE

    provenance = meta["planner_provenance"]
    assert provenance[CANONICAL_MODEL_ID_KEY] == CANONICAL_QWEN
    assert provenance["provider"] == CODECRAFT
    assert provenance["model"] == CODECRAFT_WIRE
    assert provenance["request_id"] == CODECRAFT_REQUEST_ID
    assert provenance["fallback_used"] is False
    assert provenance["source"] == "generated"

    summary = meta["planner_accounting"]["summary"]
    assert summary["final_usage"] == CODECRAFT_USAGE
    assert summary["final_provider"] == CODECRAFT
    assert summary["cost"] is None
    assert summary["billing_status"] == "unknown"

    serialized = meta_path.read_text(encoding="utf-8")
    assert "sk-offline-test-not-used" not in serialized
    assert "CODECRAFT_API_KEY" not in serialized


def test_step7_plan_evidence_exposes_model_evidence_without_weakening_the_allow_list(
    tmp_path, monkeypatch
):
    """Step 7: the model-first UI reads Step-6 evidence through the SAME projection.

    The allow-list philosophy is preserved, not weakened:

    1. the original seven plan-metadata keys stay intact (spelling + meaning);
    2. the additive Step-6 model evidence IS exposed (canonical model identity
       first, provider / provider wire model as secondary execution evidence);
    3. creative plan content stays out of the projection;
    4. provider credentials stay out;
    5. internal/absolute paths stay out.
    """
    from app.production.storage import unit_plan_dir
    from app.web import server as web
    from tests.test_plan_content_api import PID, UNIT_ID

    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    plan_dir = unit_plan_dir(PID, UNIT_ID, root=tmp_path / "productions")
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan_meta.json").write_text(
        json.dumps(
            {
                # (1) the original seven allow-listed metadata keys
                "production_plan_version": 1,
                "unit_plan_version": 1,
                "plan_identity": "safe-plan",
                "content_hash": "a" * 64,
                "planner_provider": CODECRAFT,
                "planner_model": CODECRAFT_WIRE,
                "planner_warnings": [],
                # (2) the Step-6 model-centric evidence block
                "planner_provenance": {
                    CANONICAL_MODEL_ID_KEY: CANONICAL_QWEN,
                    "provider": CODECRAFT,
                    "model": CODECRAFT_WIRE,
                    "request_id": CODECRAFT_REQUEST_ID,
                    "request_id_kind": REQUEST_ID_KIND_LLM_RESPONSE,
                    "fallback_used": False,
                    "source": SOURCE_GENERATED,
                },
                "planner_accounting": {
                    "attempts": [{"provider": CODECRAFT, "cost": None}],
                    "summary": {"cost": None, "billing_status": "unknown"},
                },
                # (3/4/5) things that must NEVER reach a browser
                "prompt": "SECRET-PROMPT-BODY",
                "raw_response": {"choices": [{"message": {"content": "SCRIPT-LEAK"}}]},
                "api_key": "sk-live-sentinel-9f3c1a",
                "source_path": "C:/private/avm/plan.json",
                "polling_url": "https://remote.example/private",
                "generated_at": "2026-09-29T10:00:00",
            }
        ),
        encoding="utf-8",
    )
    # A creative plan body sitting beside the metadata must never be served here.
    (plan_dir / "production_plan.json").write_text(
        json.dumps({"project_id": "safe-plan", "script": "CREATIVE-SCRIPT-LEAK"}),
        encoding="utf-8",
    )

    payload = web._plan_evidence(PID, UNIT_ID)
    meta = payload["meta"]

    # (1) existing contract preserved verbatim
    assert meta["production_plan_version"] == 1
    assert meta["unit_plan_version"] == 1
    assert meta["plan_identity"] == "safe-plan"
    assert meta["content_hash"] == "a" * 64
    assert meta["planner_provider"] == CODECRAFT
    assert meta["planner_model"] == CODECRAFT_WIRE
    assert meta["planner_warnings"] == []

    # (2) additive model evidence, canonical identity first
    provenance = meta["planner_provenance"]
    assert provenance[CANONICAL_MODEL_ID_KEY] == CANONICAL_QWEN
    assert provenance["provider"] == CODECRAFT
    assert provenance["model"] == CODECRAFT_WIRE
    assert provenance["request_id"] == CODECRAFT_REQUEST_ID
    assert provenance["request_id_kind"] == REQUEST_ID_KIND_LLM_RESPONSE
    assert provenance["fallback_used"] is False
    assert provenance["source"] == SOURCE_GENERATED

    # The allow-list grew by EXACTLY one key and stayed strict.
    assert set(meta) <= set(web._PLAN_META_EVIDENCE_KEYS) | {"planner_provenance"}
    assert "planner_accounting" not in meta
    assert set(provenance) <= set(web._PLANNER_PROVENANCE_KEYS)

    blob = json.dumps(payload)
    assert "CREATIVE-SCRIPT-LEAK" not in blob      # (3)
    assert "SECRET-PROMPT-BODY" not in blob        # (3)
    assert "SCRIPT-LEAK" not in blob               # (3)
    assert "sk-live-sentinel-9f3c1a" not in blob   # (4)
    assert "C:/private" not in blob                # (5)
    assert "remote.example" not in blob            # (5)

def test_step7_plan_evidence_never_invents_a_model_or_provider(tmp_path, monkeypatch):
    """Legacy / mock / hostile records: absent model evidence stays ABSENT."""
    from app.production.storage import unit_plan_dir
    from app.web import server as web
    from tests.test_plan_content_api import PID, UNIT_ID

    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    plan_dir = unit_plan_dir(PID, UNIT_ID, root=tmp_path / "productions")
    plan_dir.mkdir(parents=True)

    # (a) pre-Step-6 record: flat planner identity only, no provenance block.
    (plan_dir / "plan_meta.json").write_text(
        json.dumps(
            {
                "production_plan_version": 1,
                "unit_plan_version": 1,
                "plan_identity": "legacy-plan",
                "content_hash": "b" * 64,
                "planner_provider": "openrouter",
                "planner_model": "legacy/wire-model",
            }
        ),
        encoding="utf-8",
    )
    meta = web._plan_evidence(PID, UNIT_ID)["meta"]
    assert "planner_provenance" not in meta
    blob = json.dumps(meta)
    # (6/7/8) nothing invented: no canonical model, request id or fallback fact
    assert CANONICAL_QWEN not in blob
    assert "canonical_model_id" not in blob
    assert "request_id" not in blob
    assert "fallback_used" not in blob
    # the legacy flat fields keep their established meaning
    assert meta["planner_provider"] == "openrouter"
    assert meta["planner_model"] == "legacy/wire-model"

    # (b) mock/local planning: a provenance block WITHOUT a canonical model.
    (plan_dir / "plan_meta.json").write_text(
        json.dumps(
            {
                "plan_identity": "mock-plan",
                "planner_provider": "mock",
                "planner_provenance": {
                    "provider": "mock",
                    "fallback_used": False,
                    "source": SOURCE_GENERATED,
                },
            }
        ),
        encoding="utf-8",
    )
    meta = web._plan_evidence(PID, UNIT_ID)["meta"]
    assert CANONICAL_MODEL_ID_KEY not in meta["planner_provenance"]
    assert "model" not in meta["planner_provenance"]
    assert meta["planner_provenance"]["provider"] == "mock"

    # (c) unknown/hostile keys inside the provenance block stay server-side.
    (plan_dir / "plan_meta.json").write_text(
        json.dumps(
            {
                "planner_provenance": {
                    CANONICAL_MODEL_ID_KEY: CANONICAL_QWEN,
                    "prompt": "SECRET-PROMPT-BODY",
                    "authorization": "Bearer sk-live-sentinel-9f3c1a",
                    "base_url": "https://remote.example/v1",
                    "nested": {"path": "C:/private/avm"},
                }
            }
        ),
        encoding="utf-8",
    )
    payload = web._plan_evidence(PID, UNIT_ID)
    assert payload["meta"]["planner_provenance"] == {
        CANONICAL_MODEL_ID_KEY: CANONICAL_QWEN
    }
    blob = json.dumps(payload)
    assert "SECRET-PROMPT-BODY" not in blob
    assert "sk-live-sentinel-9f3c1a" not in blob
    assert "remote.example" not in blob
    assert "C:/private" not in blob

