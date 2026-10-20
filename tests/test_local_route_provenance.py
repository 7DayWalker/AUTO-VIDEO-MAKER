"""Local-route scene provenance + accounting — authoritative evidence tests.

Context: the frontend live validation (K3 Phase 3C) observed completed
local-route scenes with null ``actual_provider`` / ``actual_model`` /
provenance / accounting. These tests pin what the local route may and may not
claim, at the real generation seam.

Architectural rules under test (Batch 1F / 1H, Phase 2A P2-03):

* Provenance answers WHAT EXECUTED the delivered artifact and comes ONLY from
  the provider's own ``SceneGenerationResult.verification``.
* Configured provider/model values (routing map, ``scene_config``, the request
  model tag built by ``build_scene_request``) are selection evidence and are
  never promoted to actual.
* Planner identity stays plan evidence; audio identity stays audio identity.
* Cost is a real zero ONLY where the execution seam can establish it (the known
  non-billable local family); anything genuinely unknown stays unknown.
* Legacy records without a provenance block stay unknown — never backfilled.
"""

import json
from datetime import datetime

import pytest

from app.flow.scene_generator import (
    load_manifest as load_scene_manifest,
    save_manifest as save_scene_manifest,
)
from app.flow.scene_models import SceneGenerationResult, SceneManifest
from app.orchestrator.exceptions import StageFailedError
from app.orchestrator.scene_execution import (
    BILLING_STATUS_NON_BILLABLE,
    COST_SOURCE_NONE,
    build_scene_request,
    completion_checkpoint_update,
)
from app.orchestrator.visual_providers import (
    EXTERNAL_VIDEO_PROVIDER,
    IMAGE_MOTION_PROVIDER,
    LOCAL_ASSET_PROVIDER,
    create_visual_provider,
)
from app.web import server as web

from tests.test_asset_workflow import (
    PLAN_ID,
    build_orchestrator,
    load_plan_obj,
    make_clip,
    make_image,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _local_orchestrator(tmp_path, monkeypatch):
    """Hybrid orchestrator whose scenes are routed to a local provider."""
    monkeypatch.delenv("AVM_IMAGE_SOURCE", raising=False)
    monkeypatch.delenv("AVM_ALIBABA_VIDEO_MODEL", raising=False)
    orch, _plan = build_orchestrator(
        tmp_path, monkeypatch,
        mode="hybrid",
        routing={"scene_001": IMAGE_MOTION_PROVIDER},
        explicit_assets={"scene_001": "image"},
        sabotage_discovery=True,
    )
    _plan_path, plan = load_plan_obj(tmp_path)
    return orch, plan


def _run_local(orch, plan, only=("scene_001",), provider_by_scene=None):
    """Drive the real local boundary pass for the given scenes."""
    orch._execute_scenes_stage_local(
        plan=plan,
        started_at=datetime.now().isoformat(),
        only_scenes=frozenset(only),
        provider_by_scene=provider_by_scene or {
            sid: IMAGE_MOTION_PROVIDER for sid in only
        },
        mode="hybrid",
    )


def _checkpoint_from_disk(tmp_path, scene_id):
    """Read one scene checkpoint back off disk (a fresh process's view)."""
    manifest = json.loads(
        (tmp_path / "manifest.json").read_text(encoding="utf-8")
    )
    return (manifest.get("scene_checkpoints") or {}).get(scene_id) or {}


def _scene_entry(tmp_path, scene_id):
    return load_scene_manifest(
        tmp_path / "scenes" / PLAN_ID, PLAN_ID
    ).get_scene(scene_id)


class _CallbackThenFailProvider:
    """A local provider that reports completion through the checkpoint
    callback and THEN fails the generation.

    Not a contrivance: ``ImageMotionVisualProvider`` and
    ``LocalAssetVisualProvider`` emit exactly this ``status=completed``
    callback at the end of a successful render, and the boundary can still
    fail afterwards (artifact removed or corrupted, process killed). Evidence
    that was already captured must not be erased by a later failure.
    """

    name = IMAGE_MOTION_PROVIDER

    def __init__(self, artifact_path):
        self.artifact_path = artifact_path

    def wants_resume(self, checkpoint):
        return False

    def generate_scene(self, request, **kwargs):
        callback = kwargs.get("checkpoint_callback")
        if callback is not None:
            callback({
                "scene_id": request.scene_id,
                "provider": IMAGE_MOTION_PROVIDER,
                "status": "completed",
                "generation_cost": 0,
                "file_path": str(self.artifact_path),
                "validation": {"valid": True},
            })
        return SceneGenerationResult.failure(
            request.scene_id, request.sequence,
            "simulated post-callback local render failure",
        )

    def resume_scene(self, request, checkpoint, **kwargs):
        return self.generate_scene(request, **kwargs)


# ---------------------------------------------------------------------------
# 1/2 — A successful local render captures authoritative actual provenance
# ---------------------------------------------------------------------------

def test_local_success_persists_provider_reported_actual_provenance(
    tmp_path, monkeypatch
):
    orch, plan = _local_orchestrator(tmp_path, monkeypatch)
    _run_local(orch, plan)

    checkpoint = _checkpoint_from_disk(tmp_path, "scene_001")
    provenance = checkpoint["provenance"]
    # The executing local provider identified ITSELF in its result. That is
    # authoritative evidence of what performed the generation — not a label
    # invented for the frontend.
    assert provenance["provider"] == IMAGE_MOTION_PROVIDER
    assert provenance["source"] == "generated"
    assert checkpoint["provider"] == IMAGE_MOTION_PROVIDER
    assert checkpoint["status"] == "completed"


def test_local_manifest_and_checkpoint_carry_identical_evidence(
    tmp_path, monkeypatch
):
    orch, plan = _local_orchestrator(tmp_path, monkeypatch)
    _run_local(orch, plan)

    entry = _scene_entry(tmp_path, "scene_001")
    checkpoint = _checkpoint_from_disk(tmp_path, "scene_001")
    assert entry is not None and entry.status == "completed"
    # One vocabulary, no drift: the scene manifest and the operational
    # checkpoint report the same authoritative evidence.
    assert entry.provenance == checkpoint["provenance"]
    assert entry.accounting == checkpoint["accounting"]


# ---------------------------------------------------------------------------
# 3 — Configured values are never promoted to actual
# ---------------------------------------------------------------------------

def test_configured_model_is_never_promoted_to_actual_local_model(
    tmp_path, monkeypatch
):
    orch, plan = _local_orchestrator(tmp_path, monkeypatch)
    # Configuration evidence a naive implementation would happily echo as
    # "actual": the Flow model, the AI-video models, and the neutral request
    # model tag built for local providers.
    orch.manifest.scene_config["model"] = "Veo 3.1 - Lite"
    orch.manifest.scene_config["alibaba_model"] = "wan2.2-t2v-plus"
    orch.manifest.scene_config["openrouter_model"] = "google/veo-3.1"
    request = build_scene_request(
        project_id=PLAN_ID,
        scene=plan.scenes[0],
        provider_name=IMAGE_MOTION_PROVIDER,
        scene_config=orch.manifest.scene_config,
        aspect_ratio="9:16",
    )
    assert request.config.model == LOCAL_ASSET_PROVIDER  # a request tag, not a fact

    _run_local(orch, plan)

    blob = json.dumps(_checkpoint_from_disk(tmp_path, "scene_001")["provenance"])
    provenance = json.loads(blob)
    assert "model" not in provenance
    for configured in ("Veo 3.1 - Lite", "wan2.2-t2v-plus", "google/veo-3.1",
                       LOCAL_ASSET_PROVIDER):
        assert configured not in blob


def test_routing_map_is_only_a_last_resort_identity_fallback(tmp_path):
    """The routed/selected provider may supply ``provider`` but never model,
    request identity or fallback state."""
    result = SceneGenerationResult.success_result(
        scene_id="scene_001", sequence=1, file_path=tmp_path / "scene_001.mp4",
        file_size=1, actual_duration=4.0, width=1080, height=1920, codec="h264",
    )
    update = completion_checkpoint_update(
        result=result, validation={"valid": True},
        provider_name=IMAGE_MOTION_PROVIDER, sequence=1,
        include_provenance=False,
    )
    assert update["provenance"] == {
        "provider": IMAGE_MOTION_PROVIDER, "source": "generated",
    }


# ---------------------------------------------------------------------------
# 4 — Planner identity stays plan evidence
# ---------------------------------------------------------------------------

def test_planner_provider_never_enters_local_scene_provenance(tmp_path, monkeypatch):
    orch, plan = _local_orchestrator(tmp_path, monkeypatch)
    # The planning provider is configuration/plan evidence.
    orch.manifest.content_config = {
        "planner_provider": "openrouter", "planner_model": "some-llm-slug",
    }
    _run_local(orch, plan)

    checkpoint = _checkpoint_from_disk(tmp_path, "scene_001")
    blob = json.dumps({
        "provenance": checkpoint["provenance"],
        "accounting": checkpoint["accounting"],
        "provider": checkpoint.get("provider"),
    })
    assert "openrouter" not in blob
    assert "some-llm-slug" not in blob


# ---------------------------------------------------------------------------
# 5 — Local engine/provider semantics per zero-cost family
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider_name", [
    IMAGE_MOTION_PROVIDER, LOCAL_ASSET_PROVIDER, EXTERNAL_VIDEO_PROVIDER,
])
def test_each_local_family_reports_its_own_executing_identity(
    tmp_path, monkeypatch, provider_name
):
    """Each local provider reports itself; none inherits another's name.

    ``local_asset`` normalizing a user clip must not be recorded as
    ``image_motion``, and vice versa.
    """
    assets = tmp_path / "assets"
    out = tmp_path / "out"
    assets.mkdir()
    out.mkdir()
    make_image(assets / "scene_001.png")
    make_clip(assets / "scene_001.mp4")

    provider = create_visual_provider(
        "hybrid",
        scene_config={},
        scenes_output_dir=out,
        local_asset_dir=assets,
        provider_name=provider_name,
    )
    _orch, plan = _local_orchestrator(tmp_path, monkeypatch)
    request = build_scene_request(
        project_id=PLAN_ID, scene=plan.scenes[0], provider_name=provider_name,
        scene_config={}, aspect_ratio="9:16",
    )
    result = provider.generate_scene(request, require_confirmation=False)
    assert result.success, result.error
    # The result — not the routing map — names the executor.
    assert result.verification["provider"] == provider_name

    update = completion_checkpoint_update(
        result=result, validation={"valid": True},
        provider_name=provider_name, sequence=1, include_provenance=False,
    )
    assert update["provenance"]["provider"] == provider_name
    assert update["accounting"]["summary"]["billing_status"] == (
        BILLING_STATUS_NON_BILLABLE
    )


# ---------------------------------------------------------------------------
# 6/7 — Genuinely unavailable identity stays unknown
# ---------------------------------------------------------------------------

def test_local_request_identity_and_fallback_state_remain_unknown(
    tmp_path, monkeypatch
):
    orch, plan = _local_orchestrator(tmp_path, monkeypatch)
    _run_local(orch, plan)

    checkpoint = _checkpoint_from_disk(tmp_path, "scene_001")
    provenance = checkpoint["provenance"]
    # A local FFmpeg render has no remote request identity: never fabricated.
    assert "request_id" not in provenance
    assert "request_id_kind" not in provenance
    # An absent fallback state means UNKNOWN, not "no fallback happened".
    assert "fallback_used" not in provenance

    attempt = checkpoint["accounting"]["attempts"][0]
    assert attempt["request_id"] is None
    assert attempt["request_id_kind"] is None


def test_unit_detail_reports_local_unknowns_as_null_not_inferred(
    tmp_path, monkeypatch
):
    orch, plan = _local_orchestrator(tmp_path, monkeypatch)
    _run_local(orch, plan)

    rows = web._scene_checkpoint_rows(
        orch.manifest.scene_checkpoints, orch.manifest.project_id
    )
    row = [r for r in rows if r["scene_id"] == "scene_001"][0]
    assert row["actual_provider"] == IMAGE_MOTION_PROVIDER
    assert row["actual_model"] is None
    assert row["request_id"] is None
    assert row["fallback_used"] is None
    # A real provenance block exists, so the legacy label must NOT be applied.
    assert row["legacy_evidence"] is None


# ---------------------------------------------------------------------------
# 8 — Accounting: a known non-billable zero, not a defaulted zero
# ---------------------------------------------------------------------------

def test_local_cost_is_a_known_non_billable_zero(tmp_path, monkeypatch):
    """The local family has authoritative non-billable evidence, so 0 is a
    real fact rather than an unknown cost silently zeroed."""
    orch, plan = _local_orchestrator(tmp_path, monkeypatch)
    _run_local(orch, plan)

    accounting = _checkpoint_from_disk(tmp_path, "scene_001")["accounting"]
    summary = accounting["summary"]
    assert summary["billing_status"] == BILLING_STATUS_NON_BILLABLE
    assert summary["cost"] == 0
    assert summary["cost_source"] == COST_SOURCE_NONE
    assert summary["currency"] is None
    assert summary["partial"] is False
    assert summary["billable_attempt_count"] == 0
    assert summary["final_request_id"] is None


def test_billable_provider_without_cost_evidence_is_never_zeroed():
    """The counterpart that proves the local zero is a family fact: an
    unknown-cost remote operation keeps cost=None and is flagged partial."""
    from app.orchestrator.scene_execution import (
        BILLING_STATUS_BILLABLE,
        BILLING_STATUS_UNKNOWN,
        COST_SOURCE_NONE,
        summarize_accounting_attempts,
    )

    attempts = [{
        "provider": "some_future_remote_provider", "model": None,
        "request_id": None, "request_id_kind": None,
        "billing_status": BILLING_STATUS_BILLABLE, "usage": {}, "cost": None,
        "cost_source": COST_SOURCE_NONE, "currency": None, "source": "generated",
    }]
    summary = summarize_accounting_attempts(attempts)
    assert summary["billing_status"] == BILLING_STATUS_BILLABLE
    assert summary["cost"] is None, "an unknown cost is never silently zeroed"
    assert summary["partial"] is True
    assert summary["currency"] is None
    # The three-value billing vocabulary has no fourth "assumed free" state.
    assert BILLING_STATUS_UNKNOWN == "unknown"


# ---------------------------------------------------------------------------
# 12/14 — The in-flight local checkpoint write must not erase captured evidence
# ---------------------------------------------------------------------------

def test_inflight_local_checkpoint_preserves_captured_evidence(
    tmp_path, monkeypatch
):
    """Regression: a later local failure must not strand a completed scene
    with null provenance and null accounting.

    The local boundary pass writes the scene manifest twice per scene: once
    from the provider's in-flight checkpoint callback and once from the
    completion seam. ``SceneManifest.add_scene`` REPLACES the entry, so if the
    in-flight write does not carry the already-captured evidence forward, a
    scene that then fails is persisted as ``status=completed`` with an empty
    provenance block — and the skip-if-complete guard means the completion
    seam can never run for it again.

    That is precisely the reported state: a completed local-route scene with
    null actual provider / model / provenance / accounting, produced by
    current code rather than by history.
    """
    import app.orchestrator.visual_providers as vp_mod

    orch, plan = _local_orchestrator(tmp_path, monkeypatch)
    _run_local(orch, plan)

    before = _scene_entry(tmp_path, "scene_001")
    captured_provenance = dict(before.provenance)
    captured_accounting = dict(before.accounting)
    assert captured_provenance["provider"] == IMAGE_MOTION_PROVIDER
    assert captured_accounting["summary"]["cost"] == 0

    # Remove the artifact so the scene is no longer skip-if-complete; the
    # re-render then emits its completion callback and fails afterwards.
    artifact = tmp_path / "scenes" / PLAN_ID / "scene_001.mp4"
    artifact.unlink()
    stub = _CallbackThenFailProvider(artifact)
    monkeypatch.setattr(vp_mod, "create_visual_provider", lambda *a, **k: stub)

    with pytest.raises(StageFailedError):
        _run_local(orch, plan)

    after = _scene_entry(tmp_path, "scene_001")
    assert after is not None
    assert after.provenance == captured_provenance, (
        "in-flight local checkpoint write erased authoritative provenance"
    )
    assert after.accounting == captured_accounting, (
        "in-flight local checkpoint write erased authoritative accounting"
    )
    # The checkpoint on disk agrees with the manifest (no drift between the
    # two authoritative views of the same scene).
    checkpoint = _checkpoint_from_disk(tmp_path, "scene_001")
    assert checkpoint["provenance"] == captured_provenance
    assert checkpoint["accounting"] == captured_accounting


def test_inflight_local_checkpoint_never_fabricates_for_legacy_entry(
    tmp_path, monkeypatch
):
    """The same seam must NOT invent evidence for a legacy scene.

    A pre-Batch-1F local entry has no provenance block at all. Carrying
    recorded evidence forward must leave an unknown record unknown — never
    populated from the routing map or the provider callback.
    """
    scenes_dir = tmp_path / "scenes" / PLAN_ID
    scenes_dir.mkdir(parents=True, exist_ok=True)
    legacy = SceneManifest(project_id=PLAN_ID)
    legacy.add_scene(
        scene_id="scene_001", sequence=1, file="scene_001.mp4",
        status="completed", generation_cost=0,
        checkpoint_evidence={"provider": IMAGE_MOTION_PROVIDER},
    )
    legacy.scenes[0].provenance = {}
    legacy.scenes[0].accounting = {}
    save_scene_manifest(legacy, scenes_dir)

    orch, plan = _local_orchestrator(tmp_path, monkeypatch)
    # No prior completion evidence exists, so the failed re-render must not
    # create any.
    stub = _CallbackThenFailProvider(scenes_dir / "scene_001.mp4")
    monkeypatch.setattr(
        "app.orchestrator.visual_providers.create_visual_provider",
        lambda *a, **k: stub,
    )
    with pytest.raises(StageFailedError):
        _run_local(orch, plan)

    entry = _scene_entry(tmp_path, "scene_001")
    assert entry.provenance == {}, "legacy unknown must stay unknown"
    assert entry.accounting == {}, "legacy unknown must stay unknown"


# ---------------------------------------------------------------------------
# 12 — Legacy records keep their explicit legacy labelling
# ---------------------------------------------------------------------------

LEGACY_LOCAL_CHECKPOINT = {
    "scene_id": "scene_001",
    "provider": "image_motion",
    "status": "completed",
    "generation_cost": 0,
    "file_path": "scenes/legacy/scene_001.mp4",
    "validation": {"valid": True},
}


def test_legacy_local_checkpoint_stays_unknown_and_is_not_relabelled():
    """A pre-Batch-1F local record exposes no actual evidence and is NOT
    quietly upgraded: the flat provider field remains the declared/routed
    value, and ``provenance`` stays None."""
    rows = web._scene_checkpoint_rows({"scene_001": dict(LEGACY_LOCAL_CHECKPOINT)})
    row = rows[0]
    assert row["provenance"] is None
    assert row["actual_provider"] is None
    assert row["actual_model"] is None
    assert row["accounting"] is None
    assert row["cost"] is None, "a legacy generation_cost=0 is not a known zero"
    assert row["provider"] == "image_motion"
    # Nothing observable existed to build a labelled legacy block from, so no
    # legacy evidence is manufactured either.
    assert row["legacy_evidence"] is None


def test_legacy_remote_checkpoint_keeps_configured_model_labelled_separately():
    """Contrast: on a remote legacy record the flat ``model`` is CONFIGURED
    identity and is surfaced under its own name, never as actual_model."""
    checkpoint = {
        "scene_id": "scene_001", "provider": "alibaba_video",
        "status": "completed", "task_id": "task-legacy",
        "model": "wanx2.1-t2v-turbo", "remote_status": "SUCCEEDED",
        "generation_cost": 0,
    }
    row = web._scene_checkpoint_rows({"scene_001": checkpoint})[0]
    assert row["actual_model"] is None
    assert row["actual_provider"] is None
    legacy = row["legacy_evidence"]
    assert legacy is not None
    assert legacy["configured_model"] == "wanx2.1-t2v-turbo"
    assert "actual_model" not in legacy
    assert "cost" not in legacy


# ---------------------------------------------------------------------------
# 15 — The response vocabulary the frontend already consumes is unchanged
# ---------------------------------------------------------------------------

SCENE_ROW_VOCABULARY = {
    "scene_id", "status", "artifact_state", "provider", "job_id_present",
    "task_identity_present", "provenance", "legacy_evidence",
    "actual_provider", "actual_model", "request_id", "fallback_used",
    "accounting", "billing_status", "usage", "cost", "cost_source",
    "currency", "attempt_count", "generation_cost", "video_available",
    "media_url", "updated_at",
}


def test_scene_row_vocabulary_is_unchanged(tmp_path, monkeypatch):
    orch, plan = _local_orchestrator(tmp_path, monkeypatch)
    _run_local(orch, plan)
    rows = web._scene_checkpoint_rows(
        orch.manifest.scene_checkpoints, orch.manifest.project_id
    )
    assert set(rows[0]) == SCENE_ROW_VOCABULARY


def test_legacy_scene_row_vocabulary_matches_current_vocabulary():
    """No new contract: the same keys are returned for legacy and current
    records; only the values differ."""
    legacy = web._scene_checkpoint_rows({"scene_001": dict(LEGACY_LOCAL_CHECKPOINT)})[0]
    assert set(legacy) == SCENE_ROW_VOCABULARY


# ---------------------------------------------------------------------------
# 16 — Read operations never mutate durable production state
# ---------------------------------------------------------------------------

def test_scene_row_projection_does_not_mutate_manifest(tmp_path, monkeypatch):
    orch, plan = _local_orchestrator(tmp_path, monkeypatch)
    _run_local(orch, plan)

    manifest_path = tmp_path / "manifest.json"
    before = manifest_path.read_text(encoding="utf-8")
    for _ in range(3):
        web._scene_checkpoint_rows(orch.manifest.scene_checkpoints, PLAN_ID)
    assert manifest_path.read_text(encoding="utf-8") == before

    scene_manifest_path = tmp_path / "scenes" / PLAN_ID / "scene_manifest.json"
    scene_before = scene_manifest_path.read_text(encoding="utf-8")
    for _ in range(3):
        _scene_entry(tmp_path, "scene_001")
    assert scene_manifest_path.read_text(encoding="utf-8") == scene_before


# ---------------------------------------------------------------------------
# 17 — The local route never constructs or reaches a billable provider
# ---------------------------------------------------------------------------

def test_local_route_never_constructs_a_billable_provider(tmp_path, monkeypatch):
    import app.orchestrator.visual_providers as vp_mod

    def _forbidden(*args, **kwargs):
        raise AssertionError("billable provider constructed on the local route")

    monkeypatch.setattr(vp_mod, "GeminiOmniVisualProvider", _forbidden)
    monkeypatch.setattr(
        "app.orchestrator.alibaba_video.AlibabaVideoVisualProvider", _forbidden,
    )
    monkeypatch.setattr(
        "app.orchestrator.openrouter_video.OpenRouterVideoVisualProvider",
        _forbidden,
    )
    monkeypatch.setattr(vp_mod, "GoogleFlowVisualProvider", _forbidden)
    monkeypatch.setattr(vp_mod, "FlowSceneGenerator", _forbidden)

    orch, plan = _local_orchestrator(tmp_path, monkeypatch)
    _run_local(orch, plan)

    accounting = _checkpoint_from_disk(tmp_path, "scene_001")["accounting"]
    assert accounting["summary"]["billable_attempt_count"] == 0
    assert accounting["summary"]["billing_status"] == BILLING_STATUS_NON_BILLABLE


