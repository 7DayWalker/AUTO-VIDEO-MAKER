"""Phase 8 — provider-agnostic scene execution boundary tests.

All tests are hermetic: no OpenRouter, Gemini, Google Flow, or paid video
generation is ever performed. Providers are fakes at the execution boundary;
real MP4 fixtures are produced locally with ffmpeg (same mechanism the
existing routing/provider suites use).
"""

import subprocess
from pathlib import Path

import pytest

from app.flow.scene_generator import validate_video_file
from app.flow.scene_models import SceneGenerationResult
from app.orchestrator.exceptions import ValidationError
from app.orchestrator.scene_execution import (
    build_scene_request,
    completion_checkpoint_update,
    invoke_scene_generation,
    provider_wants_resume,
    remote_job_identity,
    validate_scene_artifact,
)
from app.orchestrator.visual_providers import (
    GOOGLE_FLOW_PROVIDER,
    GoogleFlowVisualProvider,
    IMAGE_MOTION_PROVIDER,
    LOCAL_ASSET_PROVIDER,
    OPENROUTER_VIDEO_PROVIDER,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_clip(path: Path, seconds=1, size="180x320"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"color=c=blue:s={size}:d={seconds}",
         "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", str(path)],
        capture_output=True, check=True, timeout=120,
    )


class _Scene:
    def __init__(self, scene_id, sequence, duration=4):
        self.id = scene_id
        self.sequence = sequence
        self.estimated_duration = duration
        self.visual_prompt = "a test scene"


def make_request(scene_id="scene_001", sequence=1, duration=4):
    return build_scene_request(
        project_id="proj",
        scene=_Scene(scene_id, sequence, duration),
        provider_name=LOCAL_ASSET_PROVIDER,
        aspect_ratio="9:16",
    )

class _RecordingProvider:
    """Hermetic provider double exercising the existing provider contract.

    Records every boundary dispatch decision (generate vs resume) and either
    writes a real local MP4 fixture, an invalid artifact, or fails truthfully.
    """

    name = "fake_provider"

    def __init__(self, output_dir, *, fail=False, invalid=False,
                 verification=None, audio_facts=None):
        self.output_dir = Path(output_dir)
        self.calls = []
        self._fail = fail
        self._invalid = invalid
        self._verification = dict(verification or {})
        self._audio_facts = audio_facts

    def generate_scene(self, request, *, require_confirmation=False,
                       checkpoint_callback=None, asset_override=None, **_):
        self.calls.append({
            "kind": "generate",
            "scene_id": request.scene_id,
            "require_confirmation": require_confirmation,
            "asset_override": asset_override,
        })
        if checkpoint_callback:
            checkpoint_callback({
                "scene_id": request.scene_id, "status": "generating",
            })
        return self._finish(request)

    def resume_scene(self, request, checkpoint, *, checkpoint_callback=None, **_):
        self.calls.append({
            "kind": "resume",
            "scene_id": request.scene_id,
            "resumed_job": (checkpoint or {}).get("job_id"),
        })
        return self._finish(request)

    def _finish(self, request) -> SceneGenerationResult:
        if self._fail:
            result = SceneGenerationResult.failure(
                request.scene_id, request.sequence, "simulated provider failure"
            )
            result.verification.update(self._verification)
            return result
        path = self.output_dir / f"{request.scene_id}.mp4"
        if self._invalid:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"this is not a real mp4 container")
        else:
            make_clip(path)
        result = SceneGenerationResult.success_result(
            scene_id=request.scene_id,
            sequence=request.sequence,
            file_path=path,
            file_size=path.stat().st_size,
            actual_duration=1.0,
            width=180,
            height=320,
            codec="h264",
            audio_facts=self._audio_facts,
        )
        result.verification.update(self._verification)
        return result


# ---------------------------------------------------------------------------
# Dispatch: resume-vs-generate (never re-submit a submitted remote job)
# ---------------------------------------------------------------------------

def test_generate_dispatch_without_checkpoint(tmp_path):
    provider = _RecordingProvider(tmp_path)
    result = invoke_scene_generation(
        request=make_request(), provider=provider, checkpoint=None,
    )
    assert result.success
    assert provider.calls == [{
        "kind": "generate", "scene_id": "scene_001",
        "require_confirmation": False, "asset_override": None,
    }]


def test_remote_job_checkpoint_dispatches_to_resume_never_generates(tmp_path):
    provider = _RecordingProvider(
        tmp_path,
        verification={"job_id": "job_001", "polling_url": "https://remote.test/j1"},
    )
    checkpoint = {
        "provider": OPENROUTER_VIDEO_PROVIDER,
        "status": "generation_submitted",
        "job_id": "job_001",
        "polling_url": "https://remote.test/j1",
    }
    result = invoke_scene_generation(
        request=make_request(), provider=provider, checkpoint=checkpoint,
    )
    assert result.success
    # The submitted remote job was resumed; submit was never called again.
    assert [call["kind"] for call in provider.calls] == ["resume"]
    assert provider.calls[0]["resumed_job"] == "job_001"


def test_remote_job_identity_requires_both_job_and_url(tmp_path):
    provider = _RecordingProvider(tmp_path)
    partial = {"job_id": "job_001"}
    invoke_scene_generation(request=make_request(), provider=provider, checkpoint=partial)
    assert [call["kind"] for call in provider.calls] == ["generate"]
    assert remote_job_identity(partial) is None
    assert remote_job_identity({"job_id": "j", "polling_url": "u"}) == {
        "job_id": "j", "polling_url": "u",
    }
    assert remote_job_identity(None) is None
    assert remote_job_identity({}) is None

def test_provider_wants_resume_hook_overrides_default(tmp_path):
    class _HookedProvider(_RecordingProvider):
        def wants_resume(self, checkpoint):
            # Provider-owned rule: status-based resumability (Flow-style).
            return (checkpoint or {}).get("status") == "generation_submitted"

    provider = _HookedProvider(tmp_path)
    status_only = {"provider": "google_flow", "status": "generation_submitted"}
    assert provider_wants_resume(provider, status_only) is True
    assert provider_wants_resume(provider, {"status": "failed"}) is False
    assert provider_wants_resume(provider, None) is False
    result = invoke_scene_generation(
        request=make_request(), provider=provider, checkpoint=status_only,
    )
    assert result.success
    assert [call["kind"] for call in provider.calls] == ["resume"]


def test_invoke_forwards_require_confirmation_and_dry_run(tmp_path):
    provider = _RecordingProvider(tmp_path)
    invoke_scene_generation(
        request=make_request(), provider=provider,
        require_confirmation=True, dry_run=False,
    )
    assert provider.calls[0]["require_confirmation"] is True


def test_invoke_forwards_asset_override(tmp_path):
    provider = _RecordingProvider(tmp_path)
    override = Path("/assets/source/scene_001.mp4")
    invoke_scene_generation(
        request=make_request(), provider=provider, asset_override=override,
    )
    assert provider.calls[0]["asset_override"] == override


# ---------------------------------------------------------------------------
# Validation: Phase 7A output contract is authoritative
# ---------------------------------------------------------------------------

def test_successful_execution_passes_phase7a_validation(tmp_path):
    provider = _RecordingProvider(tmp_path)
    result = invoke_scene_generation(
        request=make_request(), provider=provider, checkpoint=None,
    )
    validation = validate_scene_artifact(
        result, aspect_ratio="9:16", validate_video_file=validate_video_file,
    )
    assert validation is not None
    assert validation["valid"] is True


def test_provider_failure_is_truthful_no_validation(tmp_path):
    provider = _RecordingProvider(tmp_path, fail=True)
    result = invoke_scene_generation(
        request=make_request(), provider=provider, checkpoint=None,
    )
    assert not result.success
    assert "simulated provider failure" in result.error
    # A failure result is returned truthfully; the boundary never raises a
    # validation error for it and never substitutes another provider.
    assert validate_scene_artifact(
        result, aspect_ratio="9:16", validate_video_file=validate_video_file,
    ) is None


def test_invalid_artifact_raises_validation_error(tmp_path):
    provider = _RecordingProvider(tmp_path, invalid=True)
    result = invoke_scene_generation(
        request=make_request(), provider=provider, checkpoint=None,
    )
    assert result.success  # provider SAID success...
    with pytest.raises(ValidationError) as excinfo:
        validate_scene_artifact(
            result, aspect_ratio="9:16", validate_video_file=validate_video_file,
        )
    assert "scene_001 validation failed" in str(excinfo.value)
    assert excinfo.value.path.endswith("scene_001.mp4")

# ---------------------------------------------------------------------------
# Completion checkpoint: canonical shape, provenance, no secrets
# ---------------------------------------------------------------------------

def test_completion_checkpoint_shape_zero_cost_family(tmp_path):
    provider = _RecordingProvider(tmp_path)
    result = invoke_scene_generation(request=make_request(), provider=provider)
    validation = validate_scene_artifact(
        result, aspect_ratio="9:16", validate_video_file=validate_video_file,
    )
    update = completion_checkpoint_update(
        result=result,
        validation=validation,
        provider_name=IMAGE_MOTION_PROVIDER,
        sequence=1,
        extra_fields={"generation_cost": 0, "source_asset": "assets/x.png"},
        include_provenance=False,
    )
    assert update["provider"] == IMAGE_MOTION_PROVIDER
    assert update["status"] == "completed"
    assert update["generation_cost"] == 0
    assert update["source_asset"] == "assets/x.png"
    assert update["file_path"].endswith("scene_001.mp4")
    assert update["validation"]["valid"] is True
    assert "updated_at" in update
    # No remote-job provenance leaked into the zero-cost checkpoint.
    for key in ("model", "access", "job_id", "polling_url"):
        assert key not in update


def test_completion_checkpoint_merges_remote_provenance(tmp_path):
    provider = _RecordingProvider(
        tmp_path,
        verification={
            "provider": OPENROUTER_VIDEO_PROVIDER,
            "job_id": "job_9",
            "polling_url": "https://remote.test/j9",
            "model": "vendor/video",
            "access": "free",
            "estimated_cost": 0,
            # Secrets must never be carried into checkpoints; only the known
            # provenance keys are merged.
            "authorization": "Bearer super-secret-token",
        },
    )
    result = invoke_scene_generation(request=make_request(), provider=provider)
    validation = validate_scene_artifact(
        result, aspect_ratio="9:16", validate_video_file=validate_video_file,
    )
    update = completion_checkpoint_update(
        result=result,
        validation=validation,
        provider_name=OPENROUTER_VIDEO_PROVIDER,
        sequence=1,
    )
    assert update["job_id"] == "job_9"
    assert update["model"] == "vendor/video"
    assert update["access"] == "free"
    assert update["estimated_cost"] == 0
    # Secret safety: unknown verification keys are NOT persisted.
    assert "authorization" not in update
    assert "super-secret-token" not in str(update)

def test_full_boundary_lifecycle_persists_checkpoint_and_scene_path(tmp_path):
    """Simulate the pipeline lifecycle: invoke -> validate -> persist."""
    provider = _RecordingProvider(
        tmp_path, verification={"job_id": "job_1", "polling_url": "u"},
    )
    checkpoints = {}

    def persist_checkpoint(update):
        current = checkpoints.get(update["scene_id"], {})
        current.update(update)
        checkpoints[update["scene_id"]] = current

    request = make_request()
    result = invoke_scene_generation(
        request=request, provider=provider,
        checkpoint=checkpoints.get("scene_001"),
        checkpoint_callback=persist_checkpoint,
    )
    assert result.success
    validation = validate_scene_artifact(
        result, aspect_ratio="9:16", validate_video_file=validate_video_file,
    )
    scene_paths = [str(result.file_path)]
    checkpoints["scene_001"] = {
        **checkpoints.get("scene_001", {}),
        **completion_checkpoint_update(
            result=result,
            validation=validation,
            provider_name=provider.name,
            sequence=request.sequence,
        ),
    }

    assert "scene_001.mp4" in {Path(p).name for p in scene_paths}
    ck = checkpoints["scene_001"]
    assert ck["provider"] == "fake_provider"  # provider identity preserved
    assert ck["status"] == "completed"
    assert ck["job_id"] == "job_1"
    assert ck["validation"]["valid"] is True


# ---------------------------------------------------------------------------
# Provider parity through the boundary (hermetic fakes, no live services)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider_name", [
    IMAGE_MOTION_PROVIDER,
    LOCAL_ASSET_PROVIDER,
    OPENROUTER_VIDEO_PROVIDER,
])
def test_provider_parity_through_boundary(tmp_path, provider_name):
    provider = _RecordingProvider(
        tmp_path, verification={"provider": provider_name},
    )
    result = invoke_scene_generation(
        request=make_request(), provider=provider, checkpoint=None,
    )
    validation = validate_scene_artifact(
        result, aspect_ratio="9:16", validate_video_file=validate_video_file,
    )
    assert result.success and validation["valid"]
    assert result.verification["provider"] == provider_name
    assert [call["kind"] for call in provider.calls] == ["generate"]


# ---------------------------------------------------------------------------
# Request construction
# ---------------------------------------------------------------------------

def test_build_scene_request_model_selection():
    openrouter_request = build_scene_request(
        project_id="proj", scene=_Scene("scene_002", 2),
        provider_name=OPENROUTER_VIDEO_PROVIDER,
        scene_config={"openrouter_model": "vendor/video"},
        aspect_ratio="9:16",
    )
    assert openrouter_request.config.model == "vendor/video"
    fallback = build_scene_request(
        project_id="proj", scene=_Scene("scene_002", 2),
        provider_name=OPENROUTER_VIDEO_PROVIDER,
        scene_config={},
        aspect_ratio="9:16",
    )
    assert fallback.config.model == "openrouter_video"
    asset_request = build_scene_request(
        project_id="proj", scene=_Scene("scene_002", 2),
        provider_name=LOCAL_ASSET_PROVIDER,
        scene_config={"openrouter_model": "vendor/video"},
        aspect_ratio="9:16",
    )
    assert asset_request.config.model == "local_asset"
    # Duration handling is shared and deterministic: a missing/zero duration
    # falls back to 4; a negative duration is clamped to the 1-second minimum.
    fallback_duration = build_scene_request(
        project_id="proj", scene=_Scene("scene_003", 3, duration=0),
        provider_name=LOCAL_ASSET_PROVIDER,
        aspect_ratio="9:16",
    )
    assert fallback_duration.config.duration == 4
    clamped = build_scene_request(
        project_id="proj", scene=_Scene("scene_003", 3, duration=-3),
        provider_name=LOCAL_ASSET_PROVIDER,
        aspect_ratio="9:16",
    )
    assert clamped.config.duration == 1

# ---------------------------------------------------------------------------
# Google Flow isolation: provider-owned resume decision at the boundary
# ---------------------------------------------------------------------------

def _flow_provider() -> GoogleFlowVisualProvider:
    """Instance without constructor side effects (hook reads checkpoints only)."""
    return GoogleFlowVisualProvider.__new__(GoogleFlowVisualProvider)


@pytest.mark.parametrize("checkpoint,expected", [
    ({"status": "generation_submitted"}, True),
    ({"status": "generating"}, True),
    ({"status": "generated"}, True),
    ({"status": "downloaded"}, True),
    ({"status": "timeout"}, True),
    ({"status": "unknown_reconciliation_required"}, True),
    ({"status": "prepared"}, False),
    ({"status": "failed"}, False),
    ({}, False),
    (None, False),
    # Submitted evidence forces resumability for unknown statuses...
    ({"status": "pending", "checkpoint_evidence": {"submission": {"state": "SUBMITTED"}}}, True),
    # ...but never for failed/prepared (no accidental Create re-click).
    ({"status": "prepared", "checkpoint_evidence": {"submission": {"state": "SUBMITTED"}}}, False),
    ({"status": "failed", "checkpoint_evidence": {"submission": {"state": "SUBMITTED"}}}, False),
])
def test_google_flow_wants_resume_states(checkpoint, expected):
    assert GoogleFlowVisualProvider.wants_resume(_flow_provider(), checkpoint) is expected


def test_image_motion_remains_the_default_provider():
    # A checkpoint with a remote job identity does NOT let Flow become the
    # generic default; Flow's decision comes only from its own hook, and the
    # zero-cost default remains ImageMotion (Phase 8 principle 5).
    from app.orchestrator.visual_providers import DEFAULT_HYBRID_PROVIDER
    assert DEFAULT_HYBRID_PROVIDER == IMAGE_MOTION_PROVIDER
    assert GOOGLE_FLOW_PROVIDER != DEFAULT_HYBRID_PROVIDER
