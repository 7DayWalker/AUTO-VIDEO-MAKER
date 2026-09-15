"""Phase 11C Batch 1H - provider accounting foundation.

Hermetic tests for the generic, provider-agnostic accounting model:

- provenance (Batch 1F) answers WHO/WHAT EXECUTED; accounting answers WHAT
  WAS CONSUMED / BILLED — related, never conflated;
- zero is a real fact only for known non-billable execution (local family,
  reuse); billable operations without provider cost evidence stay UNKNOWN
  (never a fabricated 0);
- provider-reported monetary cost is captured as cost (with its currency),
  never as usage;
- fallback retains both attempts; reuse creates no billable attempt;
  repeated completion updates are idempotent.

No network requests and no billable provider calls: DashScope/OpenRouter are
fakes, MP4/PNG fixtures are produced locally, and provider credentials are
scrubbed in the launcher.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

import app.orchestrator.alibaba_video as alibaba_video_mod
from app.flow.scene_generator import (
    load_manifest as load_scene_manifest,
    save_manifest as save_scene_manifest,
    validate_video_file,
)
from app.flow.scene_models import SceneGenerationResult, SceneManifest
from app.orchestrator.alibaba_video import ALIBABA_VIDEO_PROVIDER
from app.orchestrator.openrouter_video import (
    OpenRouterVideoModel,
    OpenRouterVideoVisualProvider,
    _extract_usage,
)
from app.orchestrator.scene_execution import (
    BILLING_STATUS_BILLABLE,
    BILLING_STATUS_NON_BILLABLE,
    COST_SOURCE_NONE,
    COST_SOURCE_PROVIDER_REPORTED,
    build_operation_accounting,
    build_scene_accounting,
    completion_checkpoint_update,
    validate_scene_artifact,
)
from app.orchestrator.visual_providers import IMAGE_MOTION_PROVIDER, OPENROUTER_VIDEO_PROVIDER
from app.production.routes import PRIVATE_ONLY_ROUTES, PRODUCTION_ROUTES

from tests.test_asset_workflow import PLAN_ID
from tests.test_openrouter_video import (
    FakeCatalog,
    FakeOpenRouterClient,
    model_payload,
)
from tests.test_phase11c_batch1c_visual import (
    FakeDashScope,
    FakeImageClient,
    make_clip,
    make_image_ctx,
    make_image_source,
    make_png,
    make_request,
)
from tests.test_phase11c_batch1f_provenance import (
    FakeOpenRouterVideoWithJob,
    _hybrid_orchestrator,
    merged_checkpoint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def alibaba_video_provider(tmp_path, client, **kwargs):
    kwargs.setdefault("poll_interval", 0.0)
    kwargs.setdefault("poll_max_attempts", 3)
    from app.orchestrator.alibaba_video import AlibabaVideoVisualProvider

    return AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client, **kwargs
    )


def make_result(verification, tmp_path, *, generation_cost=None):
    artifact = make_clip(tmp_path / "acct_fixture.mp4")
    result = SceneGenerationResult.success_result(
        "scene_001", 1, artifact, artifact.stat().st_size, 5.0, 144, 256, "h264",
        generation_cost=generation_cost,
    )
    result.verification.update(verification)
    return result


def account_scene(result, *, provider_name, checkpoint=None):
    return completion_checkpoint_update(
        result=result,
        validation={"valid": True},
        provider_name=provider_name,
        sequence=1,
        include_provenance=False,
        checkpoint=checkpoint,
    )

# ---------------------------------------------------------------------------
# A / Q - known non-billable local execution resolves to a real zero
# ---------------------------------------------------------------------------

def test_known_non_billable_local_execution(tmp_path):
    result = make_result({"provider": IMAGE_MOTION_PROVIDER}, tmp_path)
    update = account_scene(result, provider_name=IMAGE_MOTION_PROVIDER)
    accounting = update["accounting"]

    attempt = accounting["attempts"][0]
    assert attempt["provider"] == IMAGE_MOTION_PROVIDER
    assert attempt["billing_status"] == BILLING_STATUS_NON_BILLABLE
    assert attempt["cost"] == 0
    assert attempt["cost_source"] == COST_SOURCE_NONE
    assert attempt["currency"] is None

    summary = accounting["summary"]
    assert summary["billing_status"] == BILLING_STATUS_NON_BILLABLE
    assert summary["cost"] == 0
    assert summary["partial"] is False


def test_all_non_billable_attempts_resolve_to_zero(tmp_path):
    """Q. Known non-billable execution can resolve to zero — truthfully."""
    attempts = [
        build_operation_accounting(
            provider="image_motion", billing_status=BILLING_STATUS_NON_BILLABLE,
            cost=0,
        ),
        build_operation_accounting(
            provider="local_asset", billing_status=BILLING_STATUS_NON_BILLABLE,
            cost=0,
        ),
    ]
    from app.orchestrator.scene_execution import summarize_accounting_attempts

    summary = summarize_accounting_attempts(attempts)
    assert summary["billing_status"] == BILLING_STATUS_NON_BILLABLE
    assert summary["cost"] == 0
    assert summary["partial"] is False


# ---------------------------------------------------------------------------
# B / D / P - Alibaba video: billable, provider usage, cost UNKNOWN (never 0)
# ---------------------------------------------------------------------------

def test_alibaba_successful_video_accounting(tmp_path):
    client = FakeDashScope(states=["RUNNING", "SUCCEEDED"])
    client._download = make_clip(tmp_path / "fixture.mp4").read_bytes()
    result = alibaba_video_provider(tmp_path, client).generate_scene(make_request())
    assert result.success

    update = account_scene(
        result, provider_name=ALIBABA_VIDEO_PROVIDER,
        checkpoint={"task_id": "task-001"},
    )
    accounting = update["accounting"]
    attempt = accounting["attempts"][-1]
    assert attempt["provider"] == ALIBABA_VIDEO_PROVIDER
    assert attempt["model"] == "wan2.2-t2v-plus"
    assert attempt["request_id"] == "task-001"
    assert attempt["request_id_kind"] == "task_id"
    assert attempt["billing_status"] == BILLING_STATUS_BILLABLE
    assert attempt["usage"] == {"video_duration": 5}
    assert attempt["cost"] is None
    assert attempt["cost_source"] == COST_SOURCE_NONE
    assert attempt["currency"] is None

    summary = accounting["summary"]
    assert summary["billing_status"] == BILLING_STATUS_BILLABLE
    assert summary["cost"] is None
    assert summary["partial"] is True
    # P: billable-unknown must NEVER become a numeric zero.
    assert summary["cost"] != 0


def test_alibaba_false_zero_generation_cost_is_gone(tmp_path, monkeypatch):
    """D. The boundary pass no longer records generation_cost=0 for Alibaba."""
    fake_client = FakeDashScope(states=["SUCCEEDED"])
    fake_client._download = make_clip(tmp_path / "pipe_fix.mp4").read_bytes()
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
    assert checkpoint["generation_cost"] is None
    accounting = checkpoint["accounting"]
    assert accounting["summary"]["billing_status"] == "billable"
    assert accounting["summary"]["cost"] is None

    entry = load_scene_manifest(
        tmp_path / "scenes" / PLAN_ID, PLAN_ID
    ).get_scene("scene_001")
    assert entry.generation_cost is None
    assert entry.accounting["attempts"][0]["billing_status"] == "billable"
    assert entry.accounting["attempts"][0]["usage"] == {"video_duration": 5}

# ---------------------------------------------------------------------------
# C - Alibaba image accounting (asset-source metadata)
# ---------------------------------------------------------------------------

def test_alibaba_successful_image_accounting(tmp_path):
    from tests.test_phase11c_batch1c_visual import make_png

    png = make_png(tmp_path / "img_src.png")
    client = FakeImageClient(
        states=["RUNNING", "SUCCEEDED"], download=png.read_bytes()
    )
    source = make_image_source(tmp_path, client)
    result = source.source(make_image_ctx(tmp_path))
    assert result.status.value == "success"

    accounting = result.metadata["accounting"]
    assert accounting["provider"] == "alibaba"
    assert accounting["model"] == "wan2.2-t2i-flash"
    assert accounting["request_id"] == "img-task-1"
    assert accounting["request_id_kind"] == "task_id"
    assert accounting["billing_status"] == BILLING_STATUS_BILLABLE
    assert accounting["usage"] == {"image_count": 1}
    assert accounting["cost"] is None
    assert accounting["cost_source"] == COST_SOURCE_NONE
    assert accounting["currency"] is None


# ---------------------------------------------------------------------------
# E - Flow generation_cost semantics remain untouched
# ---------------------------------------------------------------------------

def test_flow_generation_cost_semantics_are_not_repurposed(tmp_path):
    """E. generation_cost stays a Flow-credits field; accounting never reads
    it as monetary provider cost."""
    # A remote provider result carrying a legacy Flow-credits value must not
    # turn that credit count into monetary cost evidence.
    result = make_result(
        {"provider": OPENROUTER_VIDEO_PROVIDER, "job_id": "job_1"},
        tmp_path,
        generation_cost=7,
    )
    accounting = build_scene_accounting(
        result, selected_provider=OPENROUTER_VIDEO_PROVIDER
    )
    assert accounting["attempts"][-1]["cost"] is None
    assert accounting["summary"]["cost"] is None

    # The Flow credit accounting machinery remains in place, untouched.
    import app.orchestrator.pipeline as pipeline_mod

    source = Path(pipeline_mod.__file__).read_text(encoding="utf-8")
    assert "credits_accounted" in source
    assert "credits_accounted_at" in source
    assert "total_credits_used" in source


# ---------------------------------------------------------------------------
# F / G / H - OpenRouter: usage never money; provider-reported cost is cost
# ---------------------------------------------------------------------------

def test_openrouter_usage_is_never_monetary_cost():
    """F. A monetary ``cost`` payload is no longer smuggled into usage."""
    assert _extract_usage({"cost": {"total": 0.5}}) is None
    assert _extract_usage({"usage": {"seconds": 4}}) == {"seconds": 4}
    assert _extract_usage({}) is None


def test_openrouter_provider_reported_cost_captured_as_cost(tmp_path):
    """G. Provider-reported cost becomes accounting cost with its currency."""
    artifact = make_clip(tmp_path / "or_cost.mp4")
    client = FakeOpenRouterClient(video_bytes=artifact.read_bytes())
    client.statuses = [
        {"status": "processing"},
        {"status": "completed", "usage": {"seconds": 4}, "actual_cost": "0.05"},
    ]
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

    accounting = build_scene_accounting(
        result, selected_provider=OPENROUTER_VIDEO_PROVIDER
    )
    attempt = accounting["attempts"][-1]
    assert attempt["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert attempt["billing_status"] == BILLING_STATUS_BILLABLE
    assert attempt["usage"] == {"seconds": 4}
    assert attempt["cost"] == "0.05"
    assert attempt["cost_source"] == COST_SOURCE_PROVIDER_REPORTED
    assert attempt["currency"] == "USD"
    summary = accounting["summary"]
    assert summary["cost"] == "0.05"
    assert summary["currency"] == "USD"
    assert summary["partial"] is False


def test_openrouter_estimate_stays_distinct_from_actual_cost(tmp_path):
    """H. An estimate is never promoted to actual cost."""
    artifact = make_clip(tmp_path / "or_est.mp4")
    client = FakeOpenRouterClient(
        video_bytes=artifact.read_bytes(),
        submission={
            "id": "job_est",
            "status": "pending",
            "polling_url": "https://openrouter.test/api/v1/videos/job_est",
            "estimated_cost": "0.02",
        },
    )
    client.statuses = [
        {"status": "completed", "usage": {"seconds": 4}, "actual_cost": "0.03"},
    ]
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
    verification = result.verification
    assert verification["estimated_cost"] == "0.02"
    assert verification["actual_cost"] == "0.03"

    attempt = build_scene_accounting(
        result, selected_provider=OPENROUTER_VIDEO_PROVIDER
    )["attempts"][-1]
    assert attempt["cost"] == "0.03"          # actual, not the estimate
    assert attempt["estimated_cost"] == "0.02"  # preserved as an estimate

# ---------------------------------------------------------------------------
# I / J - fallback retains both attempts; final accounting matches provenance
# ---------------------------------------------------------------------------

def test_fallback_produces_two_accounting_attempts(tmp_path):
    artifact = make_clip(tmp_path / "acct_fb.mp4")
    callbacks = []
    from app.orchestrator.alibaba_video import FallbackVideoVisualProvider
    from tests.test_phase11c_batch1f_provenance import alibaba_provider

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

    checkpoint = merged_checkpoint(callbacks, {})
    completion = completion_checkpoint_update(
        result=result,
        validation={"valid": True},
        provider_name=ALIBABA_VIDEO_PROVIDER,
        sequence=1,
        include_provenance=False,
        checkpoint=checkpoint,
    )
    accounting = completion["accounting"]
    assert accounting["summary"]["attempt_count"] == 2

    primary, final = accounting["attempts"]
    # Attempt 1: the failed Alibaba primary — billable (task was accepted),
    # with its native DashScope task identity recovered from the checkpoint.
    assert primary["provider"] == ALIBABA_VIDEO_PROVIDER
    assert primary["billing_status"] == BILLING_STATUS_BILLABLE
    assert primary["request_id"] == "task-001"
    assert primary["request_id_kind"] == "task_id"
    assert primary["cost"] is None
    # Attempt 2: the successful OpenRouter fallback that delivered the artifact.
    assert final["provider"] == "openrouter_video"
    assert final["request_id"] == "job-002"
    assert final["request_id_kind"] == "job_id"
    assert final["attempt_number"] == 2

    # J: final accounting provider agrees with the authoritative provenance.
    assert (
        accounting["summary"]["final_provider"]
        == completion["provenance"]["provider"]
        == "openrouter_video"
    )


def test_fallback_with_pre_submission_failure_is_non_billable(tmp_path):
    """Alibaba failed BEFORE any task existed (rate limit): no consumption."""
    from app.orchestrator.alibaba_gateway import AlibabaGatewayRateLimitError
    from app.orchestrator.alibaba_video import FallbackVideoVisualProvider
    from tests.test_phase11c_batch1f_provenance import (
        FakeOpenRouterVideo,
        alibaba_provider,
    )

    artifact = make_clip(tmp_path / "acct_fb2.mp4")
    result = FallbackVideoVisualProvider(
        tmp_path,
        primary=alibaba_provider(
            tmp_path,
            FakeDashScope(submit_error=AlibabaGatewayRateLimitError("Throttling")),
        ),
        fallback=FakeOpenRouterVideo(succeed=True, artifact=artifact),
        fallback_enabled=True,
    ).generate_scene(make_request())
    assert result.success

    accounting = build_scene_accounting(
        result, selected_provider=ALIBABA_VIDEO_PROVIDER
    )
    primary, final = accounting["attempts"]
    assert primary["provider"] == ALIBABA_VIDEO_PROVIDER
    assert primary["billing_status"] == BILLING_STATUS_NON_BILLABLE
    assert final["provider"] == "openrouter_video"

# ---------------------------------------------------------------------------
# K / L - idempotency and attempt identity
# ---------------------------------------------------------------------------

def test_repeated_completion_updates_are_idempotent(tmp_path):
    """K. The same request accounting twice never duplicates or increments."""
    result = make_result(
        {
            "provider": ALIBABA_VIDEO_PROVIDER,
            "model": "wan2.2-t2v-plus",
            "task_id": "task-001",
            "usage": {"video_duration": 5},
            "fallback_used": False,
        },
        tmp_path,
    )
    first = build_scene_accounting(
        result, selected_provider=ALIBABA_VIDEO_PROVIDER,
        checkpoint={"task_id": "task-001"},
    )
    second = build_scene_accounting(
        result, selected_provider=ALIBABA_VIDEO_PROVIDER,
        checkpoint={"task_id": "task-001"},
    )
    assert first == second
    assert len(first["attempts"]) == 1

    checkpoint: dict = {}
    update = account_scene(
        result, provider_name=ALIBABA_VIDEO_PROVIDER, checkpoint=checkpoint
    )
    checkpoint.update(update)
    checkpoint.update(update)  # replayed completion merge
    assert len(checkpoint["accounting"]["attempts"]) == 1


def test_new_request_identity_creates_a_new_attempt(tmp_path):
    """L. A new provider request identity is a new attempt (never a phantom
    duplicate of the stale operational identity)."""
    result = make_result(
        {
            "provider": ALIBABA_VIDEO_PROVIDER,
            "model": "wan2.2-t2v-plus",
            "task_id": "task-new",
            "usage": {"video_duration": 5},
        },
        tmp_path,
    )
    accounting = build_scene_accounting(
        result,
        selected_provider=ALIBABA_VIDEO_PROVIDER,
        checkpoint={"task_id": "task-old"},
    )
    assert accounting["summary"]["attempt_count"] == 1
    assert accounting["attempts"][0]["request_id"] == "task-new"


# ---------------------------------------------------------------------------
# M - reuse is not a billable provider operation
# ---------------------------------------------------------------------------

def test_reuse_does_not_create_a_billable_attempt(tmp_path):
    make_clip(tmp_path / "scene_001.mp4", seconds=5)
    client = FakeDashScope(states=["SUCCEEDED"])
    result = alibaba_video_provider(tmp_path, client).generate_scene(make_request())
    assert result.success
    assert client.submit_calls == []  # reused; no provider operation

    accounting = build_scene_accounting(
        result, selected_provider=ALIBABA_VIDEO_PROVIDER
    )
    attempt = accounting["attempts"][0]
    assert attempt["source"] == "reused"
    assert attempt["billing_status"] == BILLING_STATUS_NON_BILLABLE
    assert attempt["cost"] == 0
    assert accounting["summary"]["billing_status"] == BILLING_STATUS_NON_BILLABLE
    assert accounting["summary"]["cost"] == 0

# ---------------------------------------------------------------------------
# N / O - legacy artifacts stay loadable and stay unknown
# ---------------------------------------------------------------------------

def test_legacy_manifest_without_accounting_loads(tmp_path):
    """N. Legacy scene manifests keep loading; no accounting is fabricated."""
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
                "validation": {"valid": True},
            }
        ],
        "created_at": "2026-09-01T00:00:00",
        "updated_at": "2026-09-01T00:00:00",
    }
    (tmp_path / "scene_manifest.json").write_text(
        json.dumps(legacy), encoding="utf-8"
    )
    entry = load_scene_manifest(tmp_path, "legacy_proj").get_scene("scene_001")
    assert entry is not None
    assert entry.accounting == {}
    assert entry.generation_cost == 0  # preserved verbatim, not reinterpreted

    save_scene_manifest(entry and load_scene_manifest(tmp_path, "legacy_proj"),
                        tmp_path)
    reloaded = json.loads(
        (tmp_path / "scene_manifest.json").read_text(encoding="utf-8")
    )
    assert reloaded["scenes"][0]["accounting"] == {}


def test_legacy_generation_cost_zero_is_not_known_provider_zero():
    """O. A legacy generation_cost=0 checkpoint is ambiguous — the accounting
    view stays unknown instead of claiming a known provider monetary zero."""
    from app.web.server import _scene_checkpoint_rows

    rows = _scene_checkpoint_rows({
        "scene_001": {
            "provider": "alibaba_video",
            "status": "completed",
            "generation_cost": 0,
        },
    })
    row = rows[0]
    assert row["accounting"] is None
    assert row["billing_status"] is None
    assert row["cost"] is None
    assert row["cost_source"] is None


# ---------------------------------------------------------------------------
# R - server/API exposes accounting additively
# ---------------------------------------------------------------------------

def test_server_rows_expose_accounting_additively():
    from app.web.server import _scene_checkpoint_rows

    accounting = {
        "attempts": [{
            "provider": "alibaba_video",
            "model": "wan2.2-t2v-plus",
            "request_id": "task-001",
            "request_id_kind": "task_id",
            "billing_status": "billable",
            "usage": {"video_duration": 5},
            "cost": None,
            "cost_source": "none",
            "currency": None,
            "source": "generated",
            "attempt_number": 1,
        }],
        "summary": {
            "attempt_count": 1,
            "billable_attempt_count": 1,
            "non_billable_attempt_count": 0,
            "unknown_billing_attempt_count": 0,
            "billing_status": "billable",
            "cost": None,
            "cost_source": "none",
            "currency": None,
            "partial": True,
            "final_provider": "alibaba_video",
            "final_request_id": "task-001",
            "final_request_id_kind": "task_id",
            "final_usage": {"video_duration": 5},
        },
    }
    rows = _scene_checkpoint_rows({
        "scene_001": {
            "provider": "alibaba_video",
            "status": "completed",
            "provenance": {
                "provider": "alibaba_video",
                "model": "wan2.2-t2v-plus",
                "request_id": "task-001",
                "request_id_kind": "task_id",
                "fallback_used": False,
                "source": "generated",
            },
            "accounting": accounting,
        },
    })
    row = rows[0]
    assert row["accounting"] == accounting
    assert row["billing_status"] == "billable"
    assert row["usage"] == {"video_duration": 5}
    assert row["cost"] is None
    assert row["cost_source"] == "none"
    assert row["currency"] is None
    assert row["attempt_count"] == 1
    # Provenance coexists additively with accounting (S).
    assert row["actual_provider"] == "alibaba_video"
    assert row["actual_model"] == "wan2.2-t2v-plus"


# ---------------------------------------------------------------------------
# S / T / U - boundary regressions
# ---------------------------------------------------------------------------

def test_provenance_and_accounting_coexist_at_the_seam(tmp_path):
    """S. The Batch 1F provenance block is unchanged alongside accounting."""
    result = make_result(
        {
            "provider": ALIBABA_VIDEO_PROVIDER,
            "model": "wan2.2-t2v-plus",
            "task_id": "task-001",
            "usage": {"video_duration": 5},
            "fallback_used": False,
        },
        tmp_path,
    )
    update = account_scene(result, provider_name=ALIBABA_VIDEO_PROVIDER)
    assert update["provenance"]["provider"] == ALIBABA_VIDEO_PROVIDER
    assert update["provenance"]["request_id"] == "task-001"
    assert update["provenance"]["fallback_used"] is False
    assert update["accounting"]["summary"]["billing_status"] == "billable"


def test_route_architecture_untouched():
    """T. The SaaS/private route boundary is unchanged by accounting."""
    assert PRODUCTION_ROUTES == ("local_ai_assisted", "full_ai_video")
    assert PRIVATE_ONLY_ROUTES == ("google_flow",)


def test_google_flow_execution_path_untouched():
    """U. Flow execution/credits were not modified by the accounting work."""
    import app.flow.scene_generator as scene_generator_mod
    import app.orchestrator.pipeline as pipeline_mod

    flow_source = Path(scene_generator_mod.__file__).read_text(encoding="utf-8")
    assert "build_scene_accounting" not in flow_source
    assert "build_operation_accounting" not in flow_source
    pipeline_source = Path(pipeline_mod.__file__).read_text(encoding="utf-8")
    assert "credits_accounted" in pipeline_source
    assert "total_credits_used" in pipeline_source
