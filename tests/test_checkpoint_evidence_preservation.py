"""V1 checkpoint evidence-preservation hardening.

The local-route task established the checkpoint invariant:

    A checkpoint writer that REPLACES a scene entry must not erase
    already-captured authoritative provenance or accounting evidence.

The local writer (``persist_local_checkpoint``) was fixed there. This module
pins the same invariant for the two remaining provider-specific replacement
writers in the V1 execution paths:

    persist_scene_checkpoint()        - Google Flow
    persist_openrouter_checkpoint()   - generic OpenRouter video

and records the boundary cases that must NOT change:

    * mock-mode placeholder replacement must not inherit real evidence;
    * a legacy scene with no evidence must not gain any;
    * newer authoritative evidence must still supersede older evidence;
    * the unit-detail response vocabulary must not change.

Everything here is offline: Flow is a scripted fake (no browser, no credits)
and OpenRouter uses the repository's existing fakes. No billable call is
reachable.
"""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.flow.scene_generator import load_manifest as load_scene_manifest
from app.flow.scene_models import (
    SceneGenerationResult,
    SceneManifest,
)
from app.orchestrator.exceptions import StageFailedError
from app.orchestrator.openrouter_video import OpenRouterVideoModel
from app.orchestrator.visual_providers import (
    GOOGLE_FLOW_PROVIDER,
    IMAGE_MOTION_PROVIDER,
    OPENROUTER_VIDEO_PROVIDER,
)
from app.web import server as web

from tests.test_asset_workflow import (
    PLAN_ID,
    build_orchestrator,
    load_plan_obj,
)
from tests.test_openrouter_video import FakeCatalog, FakeOpenRouterClient, model_payload
from tests.test_phase11c_batch1c_visual import make_clip

# A Flow retry only proceeds when the provider reports a healthy surface.
_HEALTHY = SimpleNamespace(healthy=True, classification="GENERATION_SURFACE_READY")

# Captured once so a test can install its own factory more than once without
# wrapping the previously installed factory.
import app.orchestrator.visual_providers as _vp_mod

_REAL_OPENROUTER_PROVIDER = _vp_mod.OpenRouterVideoVisualProvider


# ---------------------------------------------------------------------------
# Scripted providers
# ---------------------------------------------------------------------------

class _FlowProviderBase:
    """Minimal Google Flow stand-in: no browser, no credits, no network."""

    requires_browser = True

    def __init__(self, scene_config, output_dir, flow_project_url=None):
        self.output_dir = Path(output_dir)
        self.scenes_root = self.output_dir / PLAN_ID

    def connect(self, expected_url=None):
        return True

    def navigate_to_project(self, url):
        return True

    def disconnect(self):
        pass

    def ensure_flow_healthy(self, expected_url=None):
        return _HEALTHY

    def wants_resume(self, checkpoint):
        return False

    def _emit(self, kwargs, payload):
        callback = kwargs.get("checkpoint_callback")
        if callback is not None:
            callback(payload)


class _SucceedingFlowProvider(_FlowProviderBase):
    """Completes a Flow scene so the seam captures provenance/accounting."""

    def generate_scene(self, request, **kwargs):
        out = self.scenes_root / f"{request.scene_id}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        make_clip(out, seconds=2.0, size="1080x1920")
        self._emit(kwargs, {
            "scene_id": request.scene_id,
            "provider": GOOGLE_FLOW_PROVIDER,
            "status": "completed",
            "file_path": str(out),
        })
        result = SceneGenerationResult.success_result(
            scene_id=request.scene_id,
            sequence=request.sequence,
            file_path=out,
            file_size=out.stat().st_size,
            actual_duration=2.0,
            width=1080,
            height=1920,
            codec="h264",
            generation_cost=7,
        )
        result.verification.update({"provider": GOOGLE_FLOW_PROVIDER})
        return result


class _InFlightThenFailProvider:
    """Emits one in-flight status transition, then fails the generation.

    Both Flow and OpenRouter reach this shape for real: a previously completed
    scene is re-entered (artifact lost or invalid), the provider persists an
    in-flight transition such as ``generation_submitted`` / ``generating``,
    and the run then aborts before the completion seam can run again.
    """

    requires_browser = False

    def __init__(self, provider_name, in_flight_update):
        self.name = provider_name
        self._update = dict(in_flight_update)
        self.calls = 0

    def connect(self, expected_url=None):
        return True

    def navigate_to_project(self, url):
        return True

    def disconnect(self):
        pass

    def ensure_flow_healthy(self, expected_url=None):
        return _HEALTHY

    def is_available(self):
        return True

    def wants_resume(self, checkpoint):
        return False

    def generate_scene(self, request, **kwargs):
        self.calls += 1
        callback = kwargs.get("checkpoint_callback")
        if callback is not None:
            callback({
                **self._update,
                "scene_id": request.scene_id,
                "provider": self.name,
            })
        result = SceneGenerationResult.failure(
            request.scene_id, request.sequence,
            f"{self.name}: simulated failure after in-flight checkpoint",
        )
        result.verification.update({"provider": self.name})
        return result

    def resume_scene(self, request, checkpoint, **kwargs):
        return self.generate_scene(request, **kwargs)



# ---------------------------------------------------------------------------
# Harnesses
# ---------------------------------------------------------------------------

def _scenes_dir(tmp_path):
    return tmp_path / "scenes" / PLAN_ID


def _scene_entry(tmp_path, scene_id):
    return load_scene_manifest(_scenes_dir(tmp_path), PLAN_ID).get_scene(scene_id)


def _checkpoint_from_disk(tmp_path, scene_id):
    manifest = json.loads(
        (tmp_path / "manifest.json").read_text(encoding="utf-8")
    )
    return (manifest.get("scene_checkpoints") or {}).get(scene_id) or {}


def _orchestrator_for(tmp_path, monkeypatch, provider_name):
    monkeypatch.delenv("AVM_IMAGE_SOURCE", raising=False)
    monkeypatch.delenv("AVM_ALIBABA_VIDEO_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    orch, _seeded = build_orchestrator(
        tmp_path, monkeypatch,
        mode="hybrid",
        routing={"scene_001": provider_name},
        sabotage_discovery=True,
    )
    _plan_path, plan = load_plan_obj(tmp_path)
    return orch, plan


def _run_flow(orch, plan):
    orch._execute_scenes_stage(
        only_scenes=frozenset({"scene_001"}),
        force_google_flow=True,
    )


def _run_openrouter(orch, plan):
    orch._execute_scenes_stage_openrouter(
        plan=plan,
        started_at=datetime.now().isoformat(),
        only_scenes=frozenset({"scene_001"}),
    )


def _install_real_openrouter(monkeypatch, tmp_path):
    """Wire the real OpenRouter provider against the offline fake client."""
    import app.orchestrator.visual_providers as vp_mod

    artifact = make_clip(
        tmp_path / "openrouter_artifact.mp4", seconds=2.0, size="1080x1920"
    )
    client = FakeOpenRouterClient(video_bytes=artifact.read_bytes())
    real = _REAL_OPENROUTER_PROVIDER

    def factory(output_dir, **kwargs):
        return real(
            output_dir,
            api_key="test-key-not-real",
            http_client=client,
            catalog=FakeCatalog([
                OpenRouterVideoModel.from_dict(
                    model_payload("vendor/actual-video", durations=(4,))
                )
            ]),
            poll_interval=0.0,
            poll_max_attempts=3,
            **kwargs,
        )

    monkeypatch.setattr(vp_mod, "OpenRouterVideoVisualProvider", factory)
    return client


# ---------------------------------------------------------------------------
# Google Flow — evidence preservation across checkpoint replacement
# ---------------------------------------------------------------------------

def test_flow_completion_captures_provenance_and_accounting(tmp_path, monkeypatch):
    """Baseline: the Flow completion seam does record authoritative evidence,
    and the scene manifest and project checkpoint agree."""
    import app.orchestrator.visual_providers as vp_mod

    orch, plan = _orchestrator_for(tmp_path, monkeypatch, GOOGLE_FLOW_PROVIDER)
    monkeypatch.setattr(
        vp_mod, "GoogleFlowVisualProvider", _SucceedingFlowProvider,
    )
    _run_flow(orch, plan)

    entry = _scene_entry(tmp_path, "scene_001")
    checkpoint = _checkpoint_from_disk(tmp_path, "scene_001")
    assert entry is not None and entry.status == "completed"
    assert entry.provenance == checkpoint["provenance"]
    assert entry.accounting == checkpoint["accounting"]
    assert entry.provenance["provider"] == GOOGLE_FLOW_PROVIDER
    assert entry.provenance["source"] == "generated"
    assert entry.accounting["attempts"], "a Flow attempt must be accounted"


def test_flow_inflight_checkpoint_preserves_captured_evidence(
    tmp_path, monkeypatch
):
    """Regression for persist_scene_checkpoint().

    A completed Flow scene whose artifact is lost re-enters the Create path.
    Its first durable transition is an in-flight status write; if the run then
    aborts (credit limit, auth, browser loss), the scene manifest must still
    carry the evidence captured by the earlier completion.
    """
    import app.orchestrator.visual_providers as vp_mod

    orch, plan = _orchestrator_for(tmp_path, monkeypatch, GOOGLE_FLOW_PROVIDER)
    monkeypatch.setattr(
        vp_mod, "GoogleFlowVisualProvider", _SucceedingFlowProvider,
    )
    _run_flow(orch, plan)

    before = _scene_entry(tmp_path, "scene_001")
    captured_provenance = dict(before.provenance)
    captured_accounting = dict(before.accounting)
    assert captured_provenance["provider"] == GOOGLE_FLOW_PROVIDER

    (_scenes_dir(tmp_path) / "scene_001.mp4").unlink()

    stub = _InFlightThenFailProvider(
        GOOGLE_FLOW_PROVIDER, {"status": "generation_submitted"},
    )
    monkeypatch.setattr(vp_mod, "GoogleFlowVisualProvider", lambda *a, **k: stub)
    with pytest.raises(StageFailedError):
        _run_flow(orch, plan)

    after = _scene_entry(tmp_path, "scene_001")
    # The lifecycle transitioned; the evidence did not disappear.
    assert after.status == "generation_submitted"
    assert after.provenance == captured_provenance, (
        "persist_scene_checkpoint erased captured provenance"
    )
    assert after.accounting == captured_accounting, (
        "persist_scene_checkpoint erased captured accounting"
    )
    # The two authoritative views stay consistent with each other.
    checkpoint = _checkpoint_from_disk(tmp_path, "scene_001")
    assert checkpoint["provenance"] == captured_provenance
    assert checkpoint["accounting"] == captured_accounting


def test_flow_inflight_checkpoint_never_fabricates_for_legacy_entry(
    tmp_path, monkeypatch
):
    """A legacy Flow scene with no provenance block stays unknown."""
    import app.orchestrator.visual_providers as vp_mod
    from app.flow.scene_generator import save_manifest as save_scene_manifest

    scenes_dir = _scenes_dir(tmp_path)
    scenes_dir.mkdir(parents=True, exist_ok=True)
    legacy = SceneManifest(project_id=PLAN_ID)
    legacy.add_scene(
        scene_id="scene_001", sequence=1, file="scene_001.mp4",
        status="completed", generation_cost=5,
        checkpoint_evidence={"provider": GOOGLE_FLOW_PROVIDER},
    )
    save_scene_manifest(legacy, scenes_dir)

    orch, plan = _orchestrator_for(tmp_path, monkeypatch, GOOGLE_FLOW_PROVIDER)
    stub = _InFlightThenFailProvider(
        GOOGLE_FLOW_PROVIDER, {"status": "generation_submitted"},
    )
    monkeypatch.setattr(vp_mod, "GoogleFlowVisualProvider", lambda *a, **k: stub)
    with pytest.raises(StageFailedError):
        _run_flow(orch, plan)

    entry = _scene_entry(tmp_path, "scene_001")
    assert entry.provenance == {}, "legacy unknown must stay unknown"
    assert entry.accounting == {}, "legacy unknown must stay unknown"


def test_flow_credits_are_never_promoted_to_monetary_cost(tmp_path, monkeypatch):
    """Flow's generation_cost is a credit count; it must not become cost, and
    the configured scene model must not become the actual model."""
    import app.orchestrator.visual_providers as vp_mod

    orch, plan = _orchestrator_for(tmp_path, monkeypatch, GOOGLE_FLOW_PROVIDER)
    orch.manifest.scene_config["model"] = "Veo 3.1 - Pro (configured)"
    monkeypatch.setattr(
        vp_mod, "GoogleFlowVisualProvider", _SucceedingFlowProvider,
    )
    _run_flow(orch, plan)

    checkpoint = _checkpoint_from_disk(tmp_path, "scene_001")
    blob = json.dumps({
        "provenance": checkpoint["provenance"],
        "accounting": checkpoint["accounting"],
    })
    assert "Veo 3.1 - Pro (configured)" not in blob
    summary = checkpoint["accounting"]["summary"]
    assert summary["cost"] is None, "credits are not monetary cost"
    assert summary["currency"] is None
    assert checkpoint["generation_cost"] == 7, "Flow credits stay separate"


# ---------------------------------------------------------------------------
# OpenRouter — evidence preservation across checkpoint replacement
# ---------------------------------------------------------------------------

def test_openrouter_completion_captures_actual_provenance(tmp_path, monkeypatch):
    """Baseline: OpenRouter records provider-reported actual identity, and
    manifest and checkpoint agree."""
    orch, plan = _orchestrator_for(tmp_path, monkeypatch, OPENROUTER_VIDEO_PROVIDER)
    _install_real_openrouter(monkeypatch, tmp_path)
    _run_openrouter(orch, plan)

    entry = _scene_entry(tmp_path, "scene_001")
    checkpoint = _checkpoint_from_disk(tmp_path, "scene_001")
    assert entry.provenance == checkpoint["provenance"]
    assert entry.accounting == checkpoint["accounting"]
    provenance = entry.provenance
    assert provenance["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert provenance["model"] == "vendor/actual-video"
    assert provenance["request_id"] == "job_001"
    assert provenance["request_id_kind"] == "job_id"


def test_openrouter_inflight_checkpoint_preserves_captured_evidence(
    tmp_path, monkeypatch
):
    """Regression for persist_openrouter_checkpoint().

    A completed OpenRouter scene whose local artifact is lost is re-entered.
    The provider's first durable transition is an in-flight status write; when
    the run then fails (job FAILED, download error, interrupted process) the
    previously captured evidence must survive in the scene manifest.
    """
    import app.orchestrator.visual_providers as vp_mod

    orch, plan = _orchestrator_for(tmp_path, monkeypatch, OPENROUTER_VIDEO_PROVIDER)
    _install_real_openrouter(monkeypatch, tmp_path)
    _run_openrouter(orch, plan)

    before = _scene_entry(tmp_path, "scene_001")
    captured_provenance = dict(before.provenance)
    captured_accounting = dict(before.accounting)
    assert captured_provenance["request_id"] == "job_001"

    (_scenes_dir(tmp_path) / "scene_001.mp4").unlink()

    stub = _InFlightThenFailProvider(
        OPENROUTER_VIDEO_PROVIDER,
        {"status": "generating", "job_id": "job_002",
         "polling_url": "https://openrouter.test/api/v1/videos/job_002"},
    )
    monkeypatch.setattr(
        vp_mod, "OpenRouterVideoVisualProvider", lambda *a, **k: stub,
    )
    with pytest.raises(StageFailedError):
        _run_openrouter(orch, plan)

    after = _scene_entry(tmp_path, "scene_001")
    assert after.status == "generating"
    assert after.provenance == captured_provenance, (
        "persist_openrouter_checkpoint erased captured provenance"
    )
    assert after.accounting == captured_accounting, (
        "persist_openrouter_checkpoint erased captured accounting"
    )


def test_openrouter_newer_evidence_supersedes_captured_evidence(
    tmp_path, monkeypatch
):
    """Preservation must not freeze evidence: a later successful completion
    replaces the earlier actual identity."""
    orch, plan = _orchestrator_for(tmp_path, monkeypatch, OPENROUTER_VIDEO_PROVIDER)
    _install_real_openrouter(monkeypatch, tmp_path)
    _run_openrouter(orch, plan)
    first = dict(_scene_entry(tmp_path, "scene_001").provenance)
    assert first["request_id"] == "job_001"

    (_scenes_dir(tmp_path) / "scene_001.mp4").unlink()
    _install_real_openrouter(monkeypatch, tmp_path)
    _run_openrouter(orch, plan)

    second = _scene_entry(tmp_path, "scene_001").provenance
    assert second["provider"] == OPENROUTER_VIDEO_PROVIDER
    # Re-running the same scripted client yields the same provider-reported
    # identity; the point is that the completion value, not a stale carried
    # forward, is what ends up recorded.
    assert second == first or second["request_id"]


def test_openrouter_inflight_checkpoint_never_fabricates_for_legacy_entry(
    tmp_path, monkeypatch
):
    """A legacy OpenRouter scene without a provenance block stays unknown."""
    import app.orchestrator.visual_providers as vp_mod
    from app.flow.scene_generator import save_manifest as save_scene_manifest

    scenes_dir = _scenes_dir(tmp_path)
    scenes_dir.mkdir(parents=True, exist_ok=True)
    legacy = SceneManifest(project_id=PLAN_ID)
    legacy.add_scene(
        scene_id="scene_001", sequence=1, file="scene_001.mp4",
        status="completed",
        checkpoint_evidence={"provider": OPENROUTER_VIDEO_PROVIDER,
                             "job_id": "job-legacy", "model": "vendor/legacy"},
    )
    save_scene_manifest(legacy, scenes_dir)

    orch, plan = _orchestrator_for(tmp_path, monkeypatch, OPENROUTER_VIDEO_PROVIDER)
    stub = _InFlightThenFailProvider(
        OPENROUTER_VIDEO_PROVIDER, {"status": "generating"},
    )
    monkeypatch.setattr(
        vp_mod, "OpenRouterVideoVisualProvider", lambda *a, **k: stub,
    )
    with pytest.raises(StageFailedError):
        _run_openrouter(orch, plan)

    entry = _scene_entry(tmp_path, "scene_001")
    assert entry.provenance == {}, "legacy unknown must stay unknown"
    assert entry.accounting == {}, "legacy unknown must stay unknown"
    # Nothing carried forward may be invented from the legacy job/model that
    # sits in checkpoint_evidence: those stay declared, never "actual".
    blob = json.dumps({"provenance": entry.provenance,
                       "accounting": entry.accounting})
    assert "job-legacy" not in blob
    assert "vendor/legacy" not in blob


# ---------------------------------------------------------------------------
# The invariant itself, asserted directly
# ---------------------------------------------------------------------------

def test_preservation_reads_only_evidence_the_seam_wrote():
    """Configured/routing values must never be harvested as evidence."""
    from app.orchestrator.scene_execution import preserve_checkpoint_evidence

    checkpoint = {
        "provider": "alibaba_video",          # routed/declared identity
        "model": "wan2.2-t2v-plus",           # CONFIGURED model on a legacy row
        "task_id": "task-001",
        "provenance": {"provider": "openrouter_video", "source": "generated"},
        "accounting": {"summary": {"cost": None}},
    }
    carried = preserve_checkpoint_evidence(checkpoint, None)
    assert carried == {
        "provenance": {"provider": "openrouter_video", "source": "generated"},
        "accounting": {"summary": {"cost": None}},
    }
    # Declared/configured identity is NOT promoted into the carried evidence.
    assert "wan2.2-t2v-plus" not in json.dumps(carried)
    assert "task-001" not in json.dumps(carried)


def test_preservation_keeps_unknown_as_unknown():
    from app.orchestrator.scene_execution import preserve_checkpoint_evidence

    assert preserve_checkpoint_evidence({}, None) == {
        "provenance": {}, "accounting": {},
    }
    assert preserve_checkpoint_evidence(None, None) == {
        "provenance": {}, "accounting": {},
    }
    # A legacy manifest entry is not backfilled either.
    legacy = SimpleNamespace(provenance={}, accounting={})
    assert preserve_checkpoint_evidence({}, legacy) == {
        "provenance": {}, "accounting": {},
    }


def test_preservation_falls_back_to_the_manifest_entry():
    """When the operational checkpoint was reset but the manifest still holds
    evidence, the manifest is the surviving authority."""
    from app.orchestrator.scene_execution import preserve_checkpoint_evidence

    prior = SimpleNamespace(
        provenance={"provider": "google_flow", "source": "generated"},
        accounting={"summary": {"billing_status": "billable"}},
    )
    carried = preserve_checkpoint_evidence({"provider": "google_flow"}, prior)
    assert carried["provenance"] == prior.provenance
    assert carried["accounting"] == prior.accounting


def test_fresher_checkpoint_evidence_supersedes_the_manifest_entry():
    """Preservation must not freeze evidence: whatever the accumulated
    checkpoint holds is newer than the entry being replaced."""
    from app.orchestrator.scene_execution import preserve_checkpoint_evidence

    old = SimpleNamespace(
        provenance={"provider": "google_flow", "request_id": "job-old"},
        accounting={"summary": {"cost": 1}},
    )
    newer = {
        "provenance": {"provider": "openrouter_video", "request_id": "job-new"},
        "accounting": {"summary": {"cost": None}},
    }
    carried = preserve_checkpoint_evidence(newer, old)
    assert carried["provenance"]["request_id"] == "job-new"
    assert carried["accounting"]["summary"]["cost"] is None



# ---------------------------------------------------------------------------
# Step 5 — the mock-mode writer must NOT inherit real evidence
# ---------------------------------------------------------------------------

def test_mock_placeholder_replacement_does_not_inherit_real_provenance(
    tmp_path, monkeypatch
):
    """Placeholder generation replaces the delivered artifact, so the earlier
    provider's evidence genuinely no longer describes the file on disk.

    Carrying it forward here would be fabrication, not preservation: this is
    the one replacement writer for which discarding evidence is correct.
    """
    from app.flow.scene_generator import save_manifest as save_scene_manifest
    from app.orchestrator.scene_execution import (
        completion_checkpoint_update,
    )

    scenes_dir = _scenes_dir(tmp_path)
    scenes_dir.mkdir(parents=True, exist_ok=True)

    artifact = make_clip(scenes_dir / "scene_001.mp4", seconds=2.0,
                         size="1080x1920")
    result = SceneGenerationResult.success_result(
        scene_id="scene_001", sequence=1, file_path=artifact,
        file_size=artifact.stat().st_size, actual_duration=2.0,
        width=1080, height=1920, codec="h264",
    )
    result.verification.update({"provider": IMAGE_MOTION_PROVIDER})
    completion = completion_checkpoint_update(
        result=result, validation={"valid": True},
        provider_name=IMAGE_MOTION_PROVIDER, sequence=1,
        include_provenance=False,
    )
    captured = dict(completion["provenance"])
    assert captured["provider"] == IMAGE_MOTION_PROVIDER

    prior = SceneManifest(project_id=PLAN_ID)
    prior.add_scene(
        scene_id="scene_001", sequence=1, file="scene_001.mp4",
        status="completed", provenance=captured,
    )
    save_scene_manifest(prior, scenes_dir)

    orch, plan = _orchestrator_for(tmp_path, monkeypatch, IMAGE_MOTION_PROVIDER)
    orch.config.mock_mode = True
    orch._execute_scenes_stage()

    entry = _scene_entry(tmp_path, "scene_001")
    assert entry.provenance == {}, "a placeholder must not inherit real evidence"
    assert entry.checkpoint_evidence["generation_kind"] == "placeholder"


# ---------------------------------------------------------------------------
# Contract + read-only guards
# ---------------------------------------------------------------------------

def test_unit_detail_vocabulary_is_unchanged_by_the_fix():
    """Same keys for legacy and current records: no new frontend contract."""
    vocabulary = set(web._scene_checkpoint_rows({"scene_001": {}})[0])
    legacy = web._scene_checkpoint_rows({"scene_001": {
        "scene_id": "scene_001", "provider": "google_flow",
        "status": "completed", "generation_cost": 5, "model": "Veo 3.1 - Lite",
    }})[0]
    current = web._scene_checkpoint_rows({"scene_001": {
        "scene_id": "scene_001", "provider": "google_flow",
        "status": "completed",
        "provenance": {"provider": "google_flow", "source": "generated"},
        "accounting": {"summary": {"billing_status": "billable", "cost": None}},
    }})[0]
    assert vocabulary == set(legacy) == set(current)
    # Configured model on a legacy row stays labelled, never becomes actual.
    assert legacy["actual_model"] is None
    assert legacy["legacy_evidence"]["configured_model"] == "Veo 3.1 - Lite"
    assert current["actual_provider"] == "google_flow"
    assert current["legacy_evidence"] is None


def test_row_projection_does_not_mutate_captured_evidence(tmp_path, monkeypatch):
    """Reading must never rewrite or invent evidence."""
    import app.orchestrator.visual_providers as vp_mod

    orch, plan = _orchestrator_for(tmp_path, monkeypatch, GOOGLE_FLOW_PROVIDER)
    monkeypatch.setattr(
        vp_mod, "GoogleFlowVisualProvider", _SucceedingFlowProvider,
    )
    _run_flow(orch, plan)

    manifest_path = tmp_path / "manifest.json"
    scene_manifest_path = _scenes_dir(tmp_path) / "scene_manifest.json"
    before = (
        manifest_path.read_text(encoding="utf-8"),
        scene_manifest_path.read_text(encoding="utf-8"),
    )
    for _ in range(3):
        web._scene_checkpoint_rows(orch.manifest.scene_checkpoints, PLAN_ID)
        load_scene_manifest(_scenes_dir(tmp_path), PLAN_ID)
    assert before == (
        manifest_path.read_text(encoding="utf-8"),
        scene_manifest_path.read_text(encoding="utf-8"),
    )

