"""Phase 11C Batch 1F - durable ACTUAL provider/model/task provenance.

Hermetic tests for the approved Batch 1F design: the actual provider that
delivered a scene artifact, the actual model, the generic request identity
(task_id/job_id mapping), and the classified fallback state must become
durable scene-manifest provenance. The selected/routed provider, the
Production Route and generation_strategy are never changed by any of this.

All tests are offline: DashScope/OpenRouter are represented by the existing
fakes, MP4 fixtures are produced locally with ffmpeg, and no billable API
call is possible. Provider credentials are scrubbed in the launcher.
"""

import json
from datetime import datetime

import pytest

import app.orchestrator.alibaba_video as alibaba_video_mod
import app.orchestrator.openrouter_video as openrouter_video_mod
import app.orchestrator.visual_providers as visual_providers_mod
from app.flow.scene_generator import (
    load_manifest as load_scene_manifest,
    save_manifest as save_scene_manifest,
    validate_video_file,
)
from app.flow.scene_models import SceneGenerationResult, SceneManifest
from app.orchestrator.alibaba_video import (
    ALIBABA_VIDEO_PROVIDER,
    AlibabaVideoVisualProvider,
    FallbackVideoVisualProvider,
)
from app.orchestrator.models import ProjectManifest
from app.orchestrator.openrouter_video import (
    OpenRouterVideoModel,
    OpenRouterVideoVisualProvider,
)
from app.orchestrator.scene_execution import (
    build_scene_provenance,
    completion_checkpoint_update,
    validate_scene_artifact,
)
from app.orchestrator.visual_providers import (
    IMAGE_MOTION_PROVIDER,
    OPENROUTER_VIDEO_PROVIDER,
)
from app.production.routes import resolve_effective_route
from app.providers.fallback_policy import FailureCategory

from tests.test_asset_workflow import PLAN_ID, build_orchestrator, load_plan_obj
from tests.test_openrouter_video import (
    FakeCatalog,
    FakeOpenRouterClient,
    model_payload,
)
from tests.test_phase11c_batch1c_visual import (
    FakeDashScope,
    FakeOpenRouterVideo,
    make_clip,
    make_request,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def alibaba_provider(tmp_path, client, **kwargs):
    kwargs.setdefault("poll_interval", 0.0)
    kwargs.setdefault("poll_max_attempts", 3)
    return AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client, **kwargs
    )


def complete_scene(
    result,
    *,
    provider_name,
    validation=None,
    include_provenance=False,
):
    """Run the single Batch 1F capture seam exactly like the boundary pass."""
    if validation is None:
        validation = validate_scene_artifact(
            result, aspect_ratio="9:16", validate_video_file=validate_video_file
        )
    return completion_checkpoint_update(
        result=result,
        validation=validation,
        provider_name=provider_name,
        sequence=result.sequence,
        include_provenance=include_provenance,
    )


class FakeOpenRouterVideoWithJob(FakeOpenRouterVideo):
    """FakeOpenRouterVideo plus OpenRouter's native job identity + checkpoint."""

    def generate_scene(self, request, **kwargs):
        result = super().generate_scene(request, **kwargs)
        if result.success:
            result.verification["job_id"] = "job-002"
            result.verification["polling_url"] = (
                "https://openrouter.test/api/v1/videos/job-002"
            )
            callback = kwargs.get("checkpoint_callback")
            if callback is not None:
                callback({
                    "scene_id": request.scene_id,
                    "provider": "openrouter_video",
                    "status": "completed",
                    "job_id": "job-002",
                    "polling_url": "https://openrouter.test/api/v1/videos/job-002",
                    "model": result.verification.get("model"),
                    "file_path": str(result.file_path),
                })
        return result


def merged_checkpoint(callbacks, completion) -> dict:
    """Replay the pipeline's checkpoint merge semantics (callbacks + completion)."""
    checkpoint: dict = {}
    for update in callbacks:
        checkpoint.update(update)
    checkpoint.update(completion)
    return checkpoint


def _hybrid_orchestrator(tmp_path, monkeypatch, routing):
    """Isolated hybrid orchestrator over the standard two-scene plan fixture."""
    monkeypatch.delenv("AVM_IMAGE_SOURCE", raising=False)
    monkeypatch.delenv("AVM_ALIBABA_VIDEO_MODEL", raising=False)
    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch,
        mode="hybrid",
        routing=dict(routing),
        sabotage_discovery=True,
    )
    plan_path, plan = load_plan_obj(tmp_path)
    return orch, plan

# ---------------------------------------------------------------------------
# A/B/C - Alibaba success: durable actual provider/model/task provenance
# ---------------------------------------------------------------------------

def test_alibaba_success_records_durable_actual_provenance(tmp_path):
    client = FakeDashScope(states=["RUNNING", "SUCCEEDED"])
    client._download = make_clip(tmp_path / "fixture.mp4").read_bytes()
    provider = alibaba_provider(tmp_path, client)

    callbacks = []
    result = provider.generate_scene(
        make_request(), checkpoint_callback=callbacks.append
    )
    assert result.success

    # Native remote identity arrives through the operational callbacks first.
    checkpoint = merged_checkpoint(
        callbacks,
        complete_scene(result, provider_name=ALIBABA_VIDEO_PROVIDER),
    )
    assert checkpoint["task_id"] == "task-001"
    assert checkpoint["model"] == "wan2.2-t2v-plus"
    assert checkpoint["provider"] == ALIBABA_VIDEO_PROVIDER

    provenance = checkpoint["provenance"]
    assert provenance["provider"] == ALIBABA_VIDEO_PROVIDER
    assert provenance["model"] == "wan2.2-t2v-plus"
    assert provenance["request_id"] == "task-001"
    assert provenance["request_id_kind"] == "task_id"
    assert provenance["fallback_used"] is False
    assert provenance["source"] == "generated"
    # No fallback happened, so no fallback story is invented.
    assert "primary_provider" not in provenance
    assert "fallback_reason" not in provenance


def test_build_scene_provenance_prefers_actual_over_selected(tmp_path):
    """Actual provider/model come from the provider result, never selection."""
    artifact = make_clip(tmp_path / "actual.mp4")
    result = SceneGenerationResult.success_result(
        "scene_001", 1, artifact, artifact.stat().st_size, 5.0, 144, 256, "h264",
    )
    result.verification.update({
        "provider": "openrouter_video",
        "model": "vendor/actual-video",
        "job_id": "job_9",
    })
    provenance = build_scene_provenance(
        result, selected_provider=ALIBABA_VIDEO_PROVIDER
    )
    assert provenance["provider"] == "openrouter_video"
    assert provenance["model"] == "vendor/actual-video"
    assert provenance["request_id"] == "job_9"
    assert provenance["request_id_kind"] == "job_id"


# ---------------------------------------------------------------------------
# D - OpenRouter success: actual catalog model + job identity
# ---------------------------------------------------------------------------

def test_openrouter_success_records_actual_catalog_model_and_job_id(tmp_path):
    artifact = make_clip(tmp_path / "or_artifact.mp4")
    client = FakeOpenRouterClient(video_bytes=artifact.read_bytes())
    provider = OpenRouterVideoVisualProvider(
        tmp_path / "scenes",
        api_key="test-key-not-real",
        http_client=client,
        catalog=FakeCatalog([
            OpenRouterVideoModel.from_dict(
                model_payload("vendor/actual-video", durations=(4,))
            )
        ]),
        poll_interval=0.0,
        poll_max_attempts=3,
    )
    result = provider.generate_scene(make_request(duration=4))
    assert result.success

    # The OpenRouter stage persists flat remote provenance
    # (include_provenance defaults to True there) plus the provenance block.
    update = complete_scene(
        result,
        provider_name=OPENROUTER_VIDEO_PROVIDER,
        include_provenance=True,
    )
    assert update["job_id"] == "job_001"
    assert update["model"] == "vendor/actual-video"

    provenance = update["provenance"]
    assert provenance["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert provenance["model"] == "vendor/actual-video"
    assert provenance["request_id"] == "job_001"
    assert provenance["request_id_kind"] == "job_id"
    assert provenance["source"] == "generated"
    # Standalone OpenRouter never reports a fallback state: unknown stays
    # unknown (no fabricated fallback_used=false).
    assert "fallback_used" not in provenance

# ---------------------------------------------------------------------------
# E - Classified fallback: durable actual provider + preserved attempts
# ---------------------------------------------------------------------------

def test_fallback_provenance_is_durable_and_attempt_history_preserved(tmp_path):
    artifact = make_clip(tmp_path / "fb_artifact.mp4")
    callbacks = []
    wrapper = FallbackVideoVisualProvider(
        tmp_path,
        primary=alibaba_provider(tmp_path, FakeDashScope(states=["FAILED"])),
        fallback=FakeOpenRouterVideoWithJob(succeed=True, artifact=artifact),
        fallback_enabled=True,
    )
    result = wrapper.generate_scene(
        make_request(), checkpoint_callback=callbacks.append
    )
    assert result.success

    completion = complete_scene(result, provider_name=ALIBABA_VIDEO_PROVIDER)
    checkpoint = merged_checkpoint(callbacks, completion)

    # Authoritative artifact provenance names the FALLBACK provider.
    provenance = checkpoint["provenance"]
    assert provenance["provider"] == "openrouter_video"
    assert provenance["model"] == "google/veo-3.1"
    assert provenance["request_id"] == "job-002"
    assert provenance["request_id_kind"] == "job_id"
    assert provenance["fallback_used"] is True
    assert provenance["primary_provider"] == ALIBABA_VIDEO_PROVIDER
    assert provenance["fallback_reason"] == FailureCategory.TASK_FAILURE
    assert provenance["source"] == "generated"

    # The operational checkpoint keeps BOTH native remote identities (the
    # attempted Alibaba task and the successful OpenRouter job) and the
    # emitting (actual) provider identity.
    assert checkpoint["task_id"] == "task-001"
    assert checkpoint["job_id"] == "job-002"
    assert checkpoint["provider"] == "openrouter_video"


# ---------------------------------------------------------------------------
# F - Route / strategy / routing immutability
# ---------------------------------------------------------------------------

def test_fallback_leaves_route_strategy_and_routing_untouched(tmp_path):
    artifact = make_clip(tmp_path / "route_artifact.mp4")
    wrapper = FallbackVideoVisualProvider(
        tmp_path,
        primary=alibaba_provider(tmp_path, FakeDashScope(states=["FAILED"])),
        fallback=FakeOpenRouterVideo(succeed=True, artifact=artifact),
        fallback_enabled=True,
    )
    # The wrapper still identifies as the PRIMARY routing identity.
    assert FallbackVideoVisualProvider.name == ALIBABA_VIDEO_PROVIDER

    routing = {
        "scene_001": {"provider": "alibaba_video",
                      "reason": "explicit CLI/config override"},
    }
    routing_snapshot = json.dumps(routing, sort_keys=True)

    result = wrapper.generate_scene(make_request())
    assert result.success

    # Selection evidence untouched by the fallback or by provenance capture.
    assert json.dumps(routing, sort_keys=True) == routing_snapshot
    update = complete_scene(result, provider_name=ALIBABA_VIDEO_PROVIDER)
    assert update["provenance"]["provider"] == "openrouter_video"

    # Route resolution is identical before and after provenance capture.
    before = resolve_effective_route(
        route="full_ai_video", generation_strategy="alibaba_video"
    )
    after = resolve_effective_route(
        route="full_ai_video", generation_strategy="alibaba_video"
    )
    assert before == after
    assert before.route_id == "full_ai_video"
    assert before.legacy_strategy == "alibaba_video"


# ---------------------------------------------------------------------------
# G - Configured (selection) vs actual provenance
# ---------------------------------------------------------------------------

def test_configured_provider_model_remain_distinct_from_actual(tmp_path):
    artifact = make_clip(tmp_path / "cfg_artifact.mp4")
    wrapper = FallbackVideoVisualProvider(
        tmp_path,
        primary=alibaba_provider(
            tmp_path, FakeDashScope(states=["FAILED"]),
            model="wan2.2-t2v-plus",
        ),
        fallback=FakeOpenRouterVideoWithJob(succeed=True, artifact=artifact),
        fallback_enabled=True,
    )
    result = wrapper.generate_scene(make_request())
    assert result.success
    update = complete_scene(result, provider_name=ALIBABA_VIDEO_PROVIDER)
    provenance = update["provenance"]

    # SELECTED provider (routing vocabulary) stays alibaba_video...
    assert ALIBABA_VIDEO_PROVIDER == "alibaba_video"
    # ...while ACTUAL provider/model come from the OpenRouter execution.
    assert provenance["provider"] == "openrouter_video"
    assert provenance["model"] == "google/veo-3.1"
    assert provenance["primary_provider"] == ALIBABA_VIDEO_PROVIDER

# ---------------------------------------------------------------------------
# H - Unknown provenance is never fabricated
# ---------------------------------------------------------------------------

def _success_result_with(verification, tmp_path):
    artifact = make_clip(tmp_path / "unknown_fixture.mp4")
    result = SceneGenerationResult.success_result(
        "scene_001", 1, artifact, artifact.stat().st_size, 5.0, 144, 256, "h264",
    )
    result.verification.update(verification)
    return result


def test_unknown_provenance_is_never_fabricated(tmp_path):
    # Zero-cost style result: verification carries nothing but (at most) the
    # provider identity. Provenance stays minimal and truthful.
    result = _success_result_with({}, tmp_path)
    update = completion_checkpoint_update(
        result=result,
        validation={"valid": True},
        provider_name=IMAGE_MOTION_PROVIDER,
        sequence=1,
        include_provenance=False,
    )
    assert update["provider"] == IMAGE_MOTION_PROVIDER
    assert update["provenance"] == {
        "provider": IMAGE_MOTION_PROVIDER,
        "source": "generated",
    }
    assert "model" not in update["provenance"]
    assert "request_id" not in update["provenance"]
    assert "fallback_used" not in update["provenance"]

    # Provider self-identifies but reports no model/task/fallback state:
    # nothing is invented, and fallback_used is NOT assumed false.
    result = _success_result_with({"provider": "openrouter_video"}, tmp_path)
    provenance = build_scene_provenance(
        result, selected_provider=ALIBABA_VIDEO_PROVIDER
    )
    assert provenance == {"provider": "openrouter_video", "source": "generated"}

    # An explicitly reported fallback_used=false IS preserved.
    result = _success_result_with(
        {"provider": "alibaba_video", "fallback_used": False}, tmp_path
    )
    provenance = build_scene_provenance(
        result, selected_provider=ALIBABA_VIDEO_PROVIDER
    )
    assert provenance["fallback_used"] is False


# ---------------------------------------------------------------------------
# I - Artifact reuse: no regeneration, no fabricated original identity
# ---------------------------------------------------------------------------

def test_reuse_records_reused_source_and_leaves_original_identity_unknown(tmp_path):
    make_clip(tmp_path / "scene_001.mp4", seconds=5)
    client = FakeDashScope(states=["SUCCEEDED"])
    provider = alibaba_provider(tmp_path, client)

    result = provider.generate_scene(make_request())
    assert result.success
    # The provider was never called: the valid local artifact was reused.
    assert client.submit_calls == []
    assert client.poll_calls == []

    update = complete_scene(result, provider_name=ALIBABA_VIDEO_PROVIDER)
    provenance = update["provenance"]
    assert provenance["source"] == "reused"
    assert provenance["provider"] == ALIBABA_VIDEO_PROVIDER
    assert provenance["fallback_used"] is False
    # The ORIGINAL task identity is unknown at reuse time: never inferred
    # from current configuration, never fabricated.
    assert "request_id" not in provenance
    assert "request_id_kind" not in provenance


# ---------------------------------------------------------------------------
# J - Legacy manifests without provenance keep loading and stay truthful
# ---------------------------------------------------------------------------

def test_legacy_scene_manifest_without_provenance_loads_and_stays_truthful(tmp_path):
    legacy = {
        "project_id": "legacy_proj",
        "scenes": [
            {
                "scene_id": "scene_001",
                "sequence": 1,
                "file": "scene_001.mp4",
                "status": "completed",
                "generation_cost": 0,
                "checkpoint_evidence": {"provider": "alibaba_video"},
                "file_path": str(tmp_path / "scene_001.mp4"),
                "validation": {"valid": True},
            }
        ],
        "created_at": "2026-09-01T00:00:00",
        "updated_at": "2026-09-01T00:00:00",
    }
    (tmp_path / "scene_manifest.json").write_text(
        json.dumps(legacy), encoding="utf-8"
    )

    manifest = load_scene_manifest(tmp_path, "legacy_proj")
    entry = manifest.get_scene("scene_001")
    assert entry is not None
    assert entry.status == "completed"
    assert entry.provenance == {}  # unknown stays unknown
    assert entry.checkpoint_evidence == {"provider": "alibaba_video"}

    save_scene_manifest(manifest, tmp_path)
    reloaded = json.loads(
        (tmp_path / "scene_manifest.json").read_text(encoding="utf-8")
    )
    saved_entry = reloaded["scenes"][0]
    assert saved_entry["provenance"] == {}
    assert saved_entry["checkpoint_evidence"] == {"provider": "alibaba_video"}

# ---------------------------------------------------------------------------
# K - Provenance survives scene-manifest and project-manifest round trips
# ---------------------------------------------------------------------------

EXPECTED_FALLBACK_PROVENANCE = {
    "provider": "openrouter_video",
    "model": "vendor/actual-video",
    "request_id": "job-002",
    "request_id_kind": "job_id",
    "fallback_used": True,
    "primary_provider": "alibaba_video",
    "fallback_reason": "rate_limit",
    "source": "generated",
}


def test_provenance_survives_scene_manifest_and_project_manifest_round_trip(tmp_path):
    manifest = SceneManifest(project_id="proj_x")
    manifest.add_scene(
        "scene_001", 1, "scene_001.mp4", "completed",
        provenance=dict(EXPECTED_FALLBACK_PROVENANCE),
    )
    save_scene_manifest(manifest, tmp_path)
    loaded = load_scene_manifest(tmp_path, "proj_x")
    assert loaded.get_scene("scene_001").provenance == EXPECTED_FALLBACK_PROVENANCE

    project_manifest = ProjectManifest(project_id="proj_x", topic="probe topic")
    project_manifest.scene_checkpoints["scene_001"] = {
        "provider": "openrouter_video",
        "task_id": "task-001",   # preserved Alibaba attempt identity
        "job_id": "job-002",     # successful OpenRouter job identity
        "provenance": dict(EXPECTED_FALLBACK_PROVENANCE),
    }
    restored = ProjectManifest.from_dict(project_manifest.to_dict())
    checkpoint = restored.scene_checkpoints["scene_001"]
    assert checkpoint["provenance"] == EXPECTED_FALLBACK_PROVENANCE
    assert checkpoint["task_id"] == "task-001"
    assert checkpoint["job_id"] == "job-002"


# ---------------------------------------------------------------------------
# L - Backend rows recognize Alibaba task identity and expose provenance
# ---------------------------------------------------------------------------

def test_backend_scene_rows_recognize_task_identity_and_expose_provenance():
    from app.web.server import _scene_checkpoint_rows

    rows = _scene_checkpoint_rows({
        "scene_001": {
            "provider": "alibaba_video",
            "status": "completed",
            "task_id": "task-001",
            "model": "wan2.2-t2v-plus",
            "provenance": {
                "provider": "alibaba_video",
                "model": "wan2.2-t2v-plus",
                "request_id": "task-001",
                "request_id_kind": "task_id",
                "fallback_used": False,
                "source": "generated",
            },
        },
        "scene_002": {
            "provider": "openrouter_video",
            "status": "completed",
            "job_id": "job_9",
        },
        "scene_003": {"provider": "image_motion", "status": "completed"},
    })
    by_id = {row["scene_id"]: row for row in rows}

    alibaba_row = by_id["scene_001"]
    # Legacy key unchanged (OpenRouter vocabulary only)...
    assert alibaba_row["job_id_present"] is False
    # ...while task identity is now kind-aware for the DashScope task_id.
    assert alibaba_row["task_identity_present"] is True
    assert alibaba_row["actual_provider"] == "alibaba_video"
    assert alibaba_row["actual_model"] == "wan2.2-t2v-plus"
    assert alibaba_row["request_id"] == "task-001"
    assert alibaba_row["fallback_used"] is False

    openrouter_row = by_id["scene_002"]
    assert openrouter_row["job_id_present"] is True
    assert openrouter_row["task_identity_present"] is True
    # No provenance recorded yet: unknown stays unknown (None, not fabricated).
    assert openrouter_row["provenance"] is None
    assert openrouter_row["actual_provider"] is None

    zero_cost_row = by_id["scene_003"]
    assert zero_cost_row["job_id_present"] is False
    assert zero_cost_row["task_identity_present"] is False

# ---------------------------------------------------------------------------
# Pipeline integration - the boundary pass persists durable provenance
# ---------------------------------------------------------------------------

def test_pipeline_boundary_pass_persists_actual_provenance_for_alibaba_success(
    tmp_path, monkeypatch
):
    fake_client = FakeDashScope(states=["RUNNING", "SUCCEEDED"])
    fake_client._download = make_clip(tmp_path / "pipe_fixture.mp4").read_bytes()
    real_alibaba = alibaba_video_mod.AlibabaVideoVisualProvider

    def primary_factory(output_dir, **kwargs):
        return real_alibaba(
            output_dir,
            api_key="sk-ws-abc123",
            http_client=fake_client,
            allowed_durations=[4],
            poll_interval=0.0,
            poll_max_attempts=3,
            **kwargs,
        )

    monkeypatch.setattr(
        alibaba_video_mod, "AlibabaVideoVisualProvider", primary_factory
    )

    orch, plan = _hybrid_orchestrator(
        tmp_path, monkeypatch, routing={"scene_001": "alibaba_video"}
    )
    orch._execute_scenes_stage_local(
        plan=plan,
        started_at=datetime.now().isoformat(),
        only_scenes=frozenset({"scene_001"}),
        provider_by_scene={"scene_001": "alibaba_video"},
        mode="hybrid",
    )

    checkpoint = orch.manifest.scene_checkpoints["scene_001"]
    provenance = checkpoint["provenance"]
    assert provenance["provider"] == ALIBABA_VIDEO_PROVIDER
    assert provenance["model"] == "wan2.2-t2v-plus"
    assert provenance["request_id"] == "task-001"
    assert provenance["request_id_kind"] == "task_id"
    assert provenance["fallback_used"] is False
    assert checkpoint["task_id"] == "task-001"

    scenes_dir = tmp_path / "scenes" / PLAN_ID
    entry = load_scene_manifest(scenes_dir, PLAN_ID).get_scene("scene_001")
    assert entry is not None and entry.status == "completed"
    assert entry.provenance == provenance

    # Selection evidence is untouched by actual-provenance capture.
    assert (
        orch.manifest.scene_config["visual_scene_routing"]
        == {"scene_001": "alibaba_video"}
    )
    stage = orch.manifest.stages["scenes"]
    assert stage.metadata["provider"] == ["alibaba_video"]

def test_pipeline_fallback_persists_actual_provider_and_preserves_attempts(
    tmp_path, monkeypatch
):
    artifact = make_clip(tmp_path / "pipe_fb_artifact.mp4")
    real_alibaba = alibaba_video_mod.AlibabaVideoVisualProvider

    def primary_factory(output_dir, **kwargs):
        return real_alibaba(
            output_dir,
            api_key="sk-ws-abc123",
            http_client=FakeDashScope(states=["FAILED"]),
            allowed_durations=[4],
            poll_interval=0.0,
            poll_max_attempts=3,
            **kwargs,
        )

    monkeypatch.setattr(
        alibaba_video_mod, "AlibabaVideoVisualProvider", primary_factory
    )
    # _get_fallback imports the OpenRouter provider lazily from its module.
    monkeypatch.setattr(
        openrouter_video_mod,
        "OpenRouterVideoVisualProvider",
        lambda output_dir, **kwargs: FakeOpenRouterVideoWithJob(
            succeed=True, artifact=artifact
        ),
    )

    orch, plan = _hybrid_orchestrator(
        tmp_path, monkeypatch, routing={"scene_001": "alibaba_video"}
    )
    orch._execute_scenes_stage_local(
        plan=plan,
        started_at=datetime.now().isoformat(),
        only_scenes=frozenset({"scene_001"}),
        provider_by_scene={"scene_001": "alibaba_video"},
        mode="hybrid",
    )

    checkpoint = orch.manifest.scene_checkpoints["scene_001"]
    provenance = checkpoint["provenance"]
    # The artifact's authoritative provenance names the fallback provider...
    assert provenance["provider"] == "openrouter_video"
    assert provenance["model"] == "google/veo-3.1"
    assert provenance["request_id"] == "job-002"
    assert provenance["request_id_kind"] == "job_id"
    assert provenance["fallback_used"] is True
    assert provenance["primary_provider"] == ALIBABA_VIDEO_PROVIDER
    assert provenance["fallback_reason"] == FailureCategory.TASK_FAILURE
    # ...while BOTH native remote identities survive in the operational
    # checkpoint (attempted Alibaba task + successful OpenRouter job) and the
    # emitting provider is no longer force-relabeled.
    assert checkpoint["task_id"] == "task-001"
    assert checkpoint["job_id"] == "job-002"
    assert checkpoint["provider"] == "openrouter_video"

    scenes_dir = tmp_path / "scenes" / PLAN_ID
    entry = load_scene_manifest(scenes_dir, PLAN_ID).get_scene("scene_001")
    assert entry is not None
    assert entry.provenance["provider"] == "openrouter_video"
    assert entry.provenance["fallback_used"] is True

    # Route/selection evidence: unchanged.
    assert (
        orch.manifest.scene_config["visual_scene_routing"]
        == {"scene_001": "alibaba_video"}
    )
    stage = orch.manifest.stages["scenes"]
    assert stage.metadata["provider"] == ["alibaba_video"]

def test_pipeline_openrouter_stage_persists_provenance(tmp_path, monkeypatch):
    artifact = make_clip(tmp_path / "pipe_or_artifact.mp4")
    fake_client = FakeOpenRouterClient(video_bytes=artifact.read_bytes())
    real_openrouter = visual_providers_mod.OpenRouterVideoVisualProvider

    def openrouter_factory(output_dir, **kwargs):
        return real_openrouter(
            output_dir,
            api_key="test-key-not-real",
            http_client=fake_client,
            catalog=FakeCatalog([
                OpenRouterVideoModel.from_dict(
                    model_payload("vendor/actual-video", durations=(4,))
                )
            ]),
            poll_interval=0.0,
            poll_max_attempts=3,
            **kwargs,
        )

    monkeypatch.setattr(
        visual_providers_mod, "OpenRouterVideoVisualProvider", openrouter_factory
    )

    orch, plan = _hybrid_orchestrator(
        tmp_path, monkeypatch, routing={"scene_001": "openrouter_video"}
    )
    orch._execute_scenes_stage_openrouter(
        plan=plan,
        started_at=datetime.now().isoformat(),
        only_scenes=frozenset({"scene_001"}),
    )

    checkpoint = orch.manifest.scene_checkpoints["scene_001"]
    provenance = checkpoint["provenance"]
    assert provenance["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert provenance["model"] == "vendor/actual-video"
    assert provenance["request_id"] == "job_001"
    assert provenance["request_id_kind"] == "job_id"
    assert checkpoint["job_id"] == "job_001"
    assert "fallback_used" not in provenance

    scenes_dir = tmp_path / "scenes" / PLAN_ID
    entry = load_scene_manifest(scenes_dir, PLAN_ID).get_scene("scene_001")
    assert entry is not None
    assert entry.provenance == provenance
    assert entry.checkpoint_evidence["job_id"] == "job_001"
    assert entry.checkpoint_evidence["model"] == "vendor/actual-video"