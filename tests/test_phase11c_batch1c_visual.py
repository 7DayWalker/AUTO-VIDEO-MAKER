"""Phase 11C Batch 1C - Alibaba image/video providers + classified fallback.

Hermetic tests for the visual seams: DashScope request translation, async
task lifecycle, safety guards (explicit duration/size, no provider-default
reliance), artifact validation, provenance, native audio facts, and the
single authorized Alibaba -> OpenRouter fallback with its failure
classification. MP4/PNG fixtures are generated locally; no network requests
are made.
"""

import subprocess
from pathlib import Path

import pytest

from app.assembly.ffmpeg import resolve_ffmpeg
from app.flow.scene_models import (
    FlowGenerationConfig,
    SceneGenerationRequest,
    SceneGenerationResult,
)
from app.orchestrator.alibaba_gateway import (
    AlibabaGatewayAuthError,
    AlibabaGatewayConfigurationError,
    AlibabaGatewayInvalidRequestError,
    AlibabaGatewayModerationError,
    AlibabaGatewayNetworkError,
    AlibabaGatewayRateLimitError,
    AlibabaGatewayResponseError,
    AlibabaGatewayServerError,
)
from app.orchestrator.alibaba_video import (
    ALIBABA_VIDEO_PROVIDER,
    AlibabaVideoVisualProvider,
    FallbackVideoVisualProvider,
    parse_durations,
    parse_size_map,
)
from app.orchestrator.visual_providers import (
    VALID_PROVIDERS,
    create_visual_provider,
    normalize_provider,
)
from app.providers.fallback_policy import FailureCategory


PROJECT_ID = "20260912_alibaba_probe_001"


def make_clip(path: Path, *, seconds: float = 5.0, size: str = "144x256",
              with_audio: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        resolve_ffmpeg() or "ffmpeg", "-y",
        "-f", "lavfi", "-i",
        f"color=c=blue:s={size}:d={seconds}:r=30",
    ]
    if with_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if with_audio:
        args += ["-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest"]
    else:
        args += ["-an"]
    args.append(str(path))
    subprocess.run(args, capture_output=True, check=True, timeout=120)
    return path


def make_png(path: Path, *, size: str = "144x256") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [resolve_ffmpeg() or "ffmpeg", "-y", "-f", "lavfi", "-i",
         f"color=c=red:s={size}:d=1", "-frames:v", "1", str(path)],
        capture_output=True, check=True, timeout=120,
    )
    return path


def make_request(*, aspect_ratio="9:16", duration=5, scene_id="scene_001"):
    return SceneGenerationRequest(
        project_id=PROJECT_ID,
        scene_id=scene_id,
        sequence=1,
        visual_prompt="A cinematic mecha walking through neon rain.",
        config=FlowGenerationConfig(
            model="wan2.2-t2v-plus",
            aspect_ratio=aspect_ratio,
            duration=duration,
            count=1,
            agent_enabled=False,
        ),
    )


class FakeDashScope:
    """Fake DashScope boundary: submit / poll / download with scripted states."""

    def __init__(self, *, states=None, download=None, submit_error=None):
        self._states = list(states or [])
        self._download = download
        self.submit_error = submit_error
        self.submit_calls = []
        self.poll_calls = []
        self.download_calls = []

    def submit_task(self, path, body, operation="x"):
        self.submit_calls.append((path, body))
        if self.submit_error is not None:
            raise self.submit_error
        return {"output": {"task_id": "task-001", "task_status": "PENDING"}}

    def poll_task(self, task_id, operation="x"):
        self.poll_calls.append(task_id)
        if not self._states:
            return {"output": {"task_id": task_id, "task_status": "RUNNING"}}
        state = self._states.pop(0)
        if isinstance(state, Exception):
            raise state
        output = {"task_id": task_id, "task_status": state}
        if state == "SUCCEEDED":
            output["video_url"] = "https://media.test/generated.mp4"
            return {"output": output, "usage": {"video_duration": 5}}
        return {"output": output}

    def download_bytes(self, url, operation="x", **kwargs):
        self.download_calls.append(url)
        if isinstance(self._download, Exception):
            raise self._download
        return self._download


# ---------------------------------------------------------------------------
# Provider registry / factory integration
# ---------------------------------------------------------------------------


def test_alibaba_video_provider_is_registered_and_normalizeable():
    assert ALIBABA_VIDEO_PROVIDER in VALID_PROVIDERS
    assert normalize_provider("alibaba_video") == ALIBABA_VIDEO_PROVIDER


def test_factory_constructs_the_fallback_wrapper_from_persisted_routing(tmp_path):
    provider = create_visual_provider(
        "hybrid",
        scene_config={"alibaba_model": "wan2.2-t2v-plus"},
        scenes_output_dir=tmp_path,
        provider_name=ALIBABA_VIDEO_PROVIDER,
    )
    assert isinstance(provider, FallbackVideoVisualProvider)
    assert provider._primary.model == "wan2.2-t2v-plus"
    # Construction never resolves the fallback or touches the network.
    assert provider._fallback is None


# ---------------------------------------------------------------------------
# Request translation + safety guards
# ---------------------------------------------------------------------------


def test_parse_size_map_and_durations_helpers():
    assert parse_size_map("9:16=720*1280,16:9=1280*720") == {
        "9:16": "720*1280", "16:9": "1280*720",
    }
    assert parse_durations("5,10") == [5, 10]


def test_translation_uses_explicit_model_size_duration_and_never_defaults(tmp_path):
    client = FakeDashScope(states=["SUCCEEDED"])
    provider = AlibabaVideoVisualProvider(
        tmp_path, model="wan2.2-t2v-plus", api_key="sk-ws-abc123",
        http_client=client, poll_interval=0.0, poll_max_attempts=1,
    )
    client._download = make_clip(tmp_path / "fixture.mp4").read_bytes()
    provider.output_dir = tmp_path
    result = provider.generate_scene(make_request(duration=5))

    assert result.success
    path, body = client.submit_calls[0]
    assert path.endswith("/api/v1/services/aigc/video-generation/video-synthesis")
    assert body["model"] == "wan2.2-t2v-plus"
    # Every important parameter is EXPLICIT — no provider-default reliance.
    # Batch 1C live validation: wan2.2-t2v-plus's smallest AVM-compatible
    # 9:16 size is 1080*1920 (provider-supported AND exact-ratio).
    assert body["parameters"]["size"] == "1080*1920"
    assert body["parameters"]["duration"] == 5
    assert "prompt_extend" in body["parameters"]
    assert body["input"]["prompt"] == make_request().visual_prompt


def test_translation_wide_aspect_maps_to_landscape_size(tmp_path):
    client = FakeDashScope()
    client._download = b"x"
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
    )
    body = provider._translate_request(make_request(aspect_ratio="16:9"))
    assert body["parameters"]["size"] == "1920*1080"


# ---------------------------------------------------------------------------
# Lifecycle: submit -> poll -> download -> validate -> provenance
# ---------------------------------------------------------------------------


def test_successful_generation_downloads_validates_and_records_provenance(
    tmp_path,
):
    client = FakeDashScope(states=["RUNNING", "SUCCEEDED"])
    client._download = make_clip(tmp_path / "clip_src.mp4").read_bytes()
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
        poll_interval=0.0,
    )
    result = provider.generate_scene(make_request())

    assert result.success
    assert len(client.submit_calls) == 1
    assert len(client.poll_calls) == 2
    assert len(client.download_calls) == 1
    verification = result.verification
    assert verification["provider"] == ALIBABA_VIDEO_PROVIDER
    assert verification["model"] == "wan2.2-t2v-plus"
    assert verification["task_id"] == "task-001"
    assert verification["fallback_used"] is False
    assert verification["request"]["size"] == "1080*1920"
    assert verification["request"]["duration"] == 5
    assert verification["request"]["aspect_ratio"] == "9:16"
    assert result.file_path is not None and result.file_path.is_file()


def test_native_audio_facts_are_observed_from_the_artifact(tmp_path):
    client = FakeDashScope(states=["SUCCEEDED"])
    client._download = make_clip(tmp_path / "clip_audio.mp4", with_audio=True).read_bytes()
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
        poll_interval=0.0,
    )
    result = provider.generate_scene(make_request())
    assert result.success
    assert result.audio_facts is not None
    assert result.audio_facts.has_audio is True
    assert result.audio_facts.has_native_audio is True
    # Phase 7D untouched: the facts ride on the result, assembly owns policy.
    assert result.verification["audio_facts"]["has_audio"] is True


def test_silent_video_reports_truthful_no_audio_facts(tmp_path):
    client = FakeDashScope(states=["SUCCEEDED"])
    client._download = make_clip(tmp_path / "clip_silent.mp4", with_audio=False).read_bytes()
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
        poll_interval=0.0,
    )
    result = provider.generate_scene(make_request())
    assert result.success
    assert result.audio_facts is not None
    assert result.audio_facts.has_audio is False
    assert result.audio_facts.has_native_audio is False




# ---------------------------------------------------------------------------
# Safety guards: invalid requests are rejected BEFORE any billable call
# ---------------------------------------------------------------------------


def test_model_unsupported_duration_is_an_eligible_capability_failure(tmp_path):
    client = FakeDashScope()
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
    )
    result = provider.generate_scene(make_request(duration=7))

    assert not result.success
    assert len(client.submit_calls) == 0, "no billable request may be made"
    assert result.verification["failure_category"] == (
        FailureCategory.CAPABILITY
    )
    assert result.verification["fallback_eligible"] is True
    assert "no provider default was substituted" in (result.error or "")


def test_configured_duration_bounds_are_respected(tmp_path):
    client = FakeDashScope()
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
        allowed_durations=[5, 7, 10],
    )
    body = provider._translate_request(make_request(duration=7))
    assert body["parameters"]["duration"] == 7


def test_unsupported_aspect_ratio_fails_before_submission(tmp_path):
    client = FakeDashScope()
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
    )
    result = provider.generate_scene(make_request(aspect_ratio="1:1"))
    assert not result.success
    assert len(client.submit_calls) == 0
    assert result.verification["failure_category"] == (
        FailureCategory.CAPABILITY
    )


def test_avm_domain_violation_bypassing_schema_is_non_eligible(tmp_path):
    client = FakeDashScope()
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
    )
    config = FlowGenerationConfig.model_construct(
        model="wan2.2-t2v-plus", aspect_ratio="9:16", duration=90,
        count=1, agent_enabled=False,
    )
    request = SceneGenerationRequest.model_construct(
        project_id=PROJECT_ID, scene_id="scene_001", sequence=1,
        visual_prompt="mecha", config=config, flow_project_url=None,
    )
    result = provider.generate_scene(request)
    assert not result.success
    assert len(client.submit_calls) == 0
    assert result.verification["failure_category"] == (
        FailureCategory.INVALID_REQUEST
    )
    assert result.verification["fallback_eligible"] is False


# ---------------------------------------------------------------------------
# Lifecycle failures: classified, truthful, never resubmitted
# ---------------------------------------------------------------------------


def test_submit_rate_limit_is_classified_fallback_eligible(tmp_path):
    client = FakeDashScope(submit_error=AlibabaGatewayRateLimitError("429"))
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
    )
    result = provider.generate_scene(make_request())
    assert not result.success
    assert result.verification["failure_category"] == (
        FailureCategory.RATE_LIMIT
    )
    assert result.verification["fallback_eligible"] is True


def test_submit_auth_failure_is_not_fallback_eligible(tmp_path):
    client = FakeDashScope(submit_error=AlibabaGatewayAuthError("401"))
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
    )
    result = provider.generate_scene(make_request())
    assert not result.success
    assert result.verification["failure_category"] == (
        FailureCategory.AUTHENTICATION
    )
    assert result.verification["fallback_eligible"] is False


def test_malformed_submit_response_is_eligible(tmp_path):
    client = FakeDashScope(
        submit_error=AlibabaGatewayResponseError("no task_id")
    )
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
    )
    result = provider.generate_scene(make_request())
    assert not result.success
    assert result.verification["failure_category"] == (
        FailureCategory.RESPONSE_MALFORMED
    )


def test_polling_timeout_is_bounded_and_never_resubmits(tmp_path):
    client = FakeDashScope(states=["RUNNING"])
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
        poll_interval=0.0, poll_max_attempts=3,
    )
    result = provider.generate_scene(make_request())

    assert not result.success
    assert result.status == "timeout"
    assert len(client.submit_calls) == 1, "exactly one submission, ever"
    assert len(client.poll_calls) == 3
    assert result.verification["failure_category"] == (
        FailureCategory.POLL_TIMEOUT
    )
    assert result.verification["fallback_eligible"] is False
    assert result.verification["task_id"] == "task-001"


def test_remote_task_failure_is_eligible_and_carries_task_identity(tmp_path):
    client = FakeDashScope(states=["FAILED"])
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
        poll_interval=0.0, poll_max_attempts=1,
    )
    result = provider.generate_scene(make_request())
    assert not result.success
    assert result.verification["failure_category"] == (
        FailureCategory.TASK_FAILURE
    )
    assert result.verification["fallback_eligible"] is True
    assert result.verification["task_id"] == "task-001"
    assert result.verification["remote_status"] == "FAILED"


def test_download_failure_is_classified_network_error(tmp_path):
    client = FakeDashScope(states=["SUCCEEDED"])
    client._download = AlibabaGatewayNetworkError("connection reset")
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
        poll_interval=0.0, poll_max_attempts=1,
    )
    result = provider.generate_scene(make_request())
    assert not result.success
    assert result.verification["failure_category"] == (
        FailureCategory.NETWORK
    )
    assert result.verification["fallback_eligible"] is True
    # A partial artifact must never be presented as a scene output.
    assert not (tmp_path / "scene_001.mp4").exists()


def test_invalid_downloaded_media_fails_validation_truthfully(tmp_path):
    client = FakeDashScope(states=["SUCCEEDED"])
    client._download = b"this is not an mp4 file"
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
        poll_interval=0.0, poll_max_attempts=1,
    )
    result = provider.generate_scene(make_request())
    assert not result.success
    assert result.verification["failure_category"] == (
        FailureCategory.ARTIFACT_VALIDATION
    )
    assert result.verification["fallback_eligible"] is False
    assert not (tmp_path / "scene_001.mp4").exists()


def test_missing_credential_fails_before_any_request(tmp_path):
    client = FakeDashScope()
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="", http_client=client,
    )
    result = provider.generate_scene(make_request())
    assert not result.success
    assert result.verification["failure_category"] == (
        FailureCategory.CONFIGURATION
    )
    assert result.verification["fallback_eligible"] is False
    assert client.submit_calls == []



# ---------------------------------------------------------------------------
# Resume / checkpoint semantics (never resubmit a submitted remote task)
# ---------------------------------------------------------------------------


def test_wants_resume_requires_an_alibaba_task_identity(tmp_path):
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=FakeDashScope(),
    )
    assert provider.wants_resume({
        "provider": ALIBABA_VIDEO_PROVIDER,
        "task_id": "task-9", "status": "polling",
    }) is True
    assert provider.wants_resume({
        "provider": ALIBABA_VIDEO_PROVIDER,
        "task_id": "task-9", "status": "completed",
    }) is False
    assert provider.wants_resume({"status": "polling"}) is False
    assert provider.wants_resume(None) is False


def test_resume_polls_an_in_flight_task_without_resubmission(tmp_path):
    client = FakeDashScope(states=["SUCCEEDED"])
    client._download = make_clip(tmp_path / "resume.mp4").read_bytes()
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
        poll_interval=0.0,
    )
    result = provider.resume_scene(
        make_request(),
        {"provider": ALIBABA_VIDEO_PROVIDER, "task_id": "task-001",
         "status": "generation_submitted"},
    )
    assert result.success
    assert client.submit_calls == [], "resume must NEVER resubmit"
    assert len(client.poll_calls) == 1


def test_resume_of_a_terminal_failure_never_resubmits(tmp_path):
    client = FakeDashScope(states=["SUCCEEDED"])
    client._download = make_clip(tmp_path / "never.mp4").read_bytes()
    provider = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client,
        poll_interval=0.0,
    )
    result = provider.resume_scene(
        make_request(),
        {"provider": ALIBABA_VIDEO_PROVIDER, "task_id": "task-9",
         "status": "failed", "remote_status": "FAILED", "error": "boom"},
    )
    assert not result.success
    assert client.submit_calls == [] and client.poll_calls == []
    assert "not resubmitted" in (result.error or "")


# ---------------------------------------------------------------------------
# Classified video fallback: Alibaba PRIMARY -> OpenRouter
# ---------------------------------------------------------------------------


class FakeOpenRouterVideo:
    """OpenRouter stand-in implementing the existing provider protocol."""

    name = "openrouter_video"
    requires_browser = False
    calls = 0

    def __init__(self, *, succeed=True, artifact=None):
        self.succeed = succeed
        self._artifact = artifact

    def is_available(self):
        return True

    def generate_scene(self, request, **kwargs):
        type(self).calls += 1
        if self.succeed:
            result = SceneGenerationResult.success_result(
                request.scene_id, request.sequence, self._artifact,
                self._artifact.stat().st_size, 5.0, 144, 256, "h264",
                generation_cost=None, audio_facts=None,
            )
            result.verification.update(
                {"provider": "openrouter_video", "model": "google/veo-3.1"}
            )
            return result
        result = SceneGenerationResult.failure(
            request.scene_id, request.sequence, "OpenRouter also failed"
        )
        result.verification.update({"provider": "openrouter_video"})
        return result

    def resume_scene(self, request, checkpoint, checkpoint_callback=None,
                     **kwargs):
        return SceneGenerationResult.failure(
            request.scene_id, request.sequence, "resume not scripted"
        )

    def wants_resume(self, checkpoint):
        return bool((checkpoint or {}).get("provider") == "openrouter_video")


def build_wrapper(tmp_path, *, primary_client, openrouter, artifact):
    primary = AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=primary_client,
        poll_interval=0.0, poll_max_attempts=1,
    )
    return FallbackVideoVisualProvider(
        tmp_path, primary=primary, fallback=openrouter, fallback_enabled=True,
    )


def test_fallback_5xx_and_task_failure_and_network_are_eligible(tmp_path):
    artifact = make_clip(tmp_path / "or_artifact2.mp4")
    cases = [
        AlibabaGatewayServerError("HTTP 502"),
        AlibabaGatewayNetworkError("dns failure"),
    ]
    for error in cases:
        FakeOpenRouterVideo.calls = 0
        openrouter = FakeOpenRouterVideo(succeed=True, artifact=artifact)
        wrapper = build_wrapper(
            tmp_path,
            primary_client=FakeDashScope(submit_error=error),
            openrouter=openrouter, artifact=artifact,
        )
        result = wrapper.generate_scene(make_request())
        assert result.success, error
        assert openrouter.calls == 1
        assert result.verification["fallback_used"] is True
        assert result.verification["final_provider"] == "openrouter_video"


def test_fallback_remote_task_failure_is_eligible(tmp_path):
    artifact = make_clip(tmp_path / "or_artifact3.mp4")
    FakeOpenRouterVideo.calls = 0
    openrouter = FakeOpenRouterVideo(succeed=True, artifact=artifact)
    wrapper = build_wrapper(
        tmp_path,
        primary_client=FakeDashScope(states=["FAILED"]),
        openrouter=openrouter, artifact=artifact,
    )
    result = wrapper.generate_scene(make_request())
    assert result.success
    assert openrouter.calls == 1
    assert result.verification["fallback_reason"] == (
        FailureCategory.TASK_FAILURE
    )


def test_non_eligible_failures_never_invoke_openrouter(tmp_path):
    artifact = make_clip(tmp_path / "or_never.mp4")
    cases = [
        (FakeDashScope(submit_error=AlibabaGatewayAuthError("401")),
         FailureCategory.AUTHENTICATION),
        (FakeDashScope(
            submit_error=AlibabaGatewayInvalidRequestError("bad param")),
         FailureCategory.INVALID_REQUEST),
        (FakeDashScope(
            submit_error=AlibabaGatewayModerationError("policy")),
         FailureCategory.CONTENT_MODERATION),
        (FakeDashScope(
            submit_error=AlibabaGatewayConfigurationError("no key")),
         FailureCategory.CONFIGURATION),
    ]
    for primary_client, expected_category in cases:
        FakeOpenRouterVideo.calls = 0
        openrouter = FakeOpenRouterVideo(succeed=True, artifact=artifact)
        wrapper = build_wrapper(
            tmp_path, primary_client=primary_client,
            openrouter=openrouter, artifact=artifact,
        )
        result = wrapper.generate_scene(make_request())
        assert not result.success
        assert openrouter.calls == 0, expected_category
        assert result.verification["fallback_eligible"] is False
        assert result.verification["failure_category"] == expected_category


def test_invalid_avm_requests_never_fall_back(tmp_path):
    artifact = make_clip(tmp_path / "or_invalid.mp4")
    FakeOpenRouterVideo.calls = 0
    openrouter = FakeOpenRouterVideo(succeed=True, artifact=artifact)
    wrapper = build_wrapper(
        tmp_path,
        primary_client=FakeDashScope(),  # would succeed if it were called
        openrouter=openrouter, artifact=artifact,
    )
    # A DOMAIN-invalid duration (bypassing the schema) must never reach
    # OpenRouter.
    config = FlowGenerationConfig.model_construct(
        model="wan2.2-t2v-plus", aspect_ratio="9:16", duration=90,
        count=1, agent_enabled=False,
    )
    request = SceneGenerationRequest.model_construct(
        project_id=PROJECT_ID, scene_id="scene_001", sequence=1,
        visual_prompt="mecha", config=config, flow_project_url=None,
    )
    result = wrapper.generate_scene(request)
    assert not result.success
    assert openrouter.calls == 0


def test_alibaba_success_never_invokes_openrouter(tmp_path):
    artifact = make_clip(tmp_path / "al_success.mp4")
    FakeOpenRouterVideo.calls = 0
    openrouter = FakeOpenRouterVideo(succeed=True, artifact=artifact)
    primary_client = FakeDashScope(states=["SUCCEEDED"])
    primary_client._download = artifact.read_bytes()
    wrapper = build_wrapper(
        tmp_path, primary_client=primary_client,
        openrouter=openrouter, artifact=artifact,
    )
    result = wrapper.generate_scene(make_request())
    assert result.success
    assert openrouter.calls == 0
    assert result.verification["fallback_used"] is False
    assert result.verification["provider"] == ALIBABA_VIDEO_PROVIDER


def test_fallback_disabled_or_unavailable_fails_truthfully(tmp_path):
    artifact = make_clip(tmp_path / "or_disabled.mp4")
    FakeOpenRouterVideo.calls = 0
    openrouter = FakeOpenRouterVideo(succeed=True, artifact=artifact)
    wrapper = build_wrapper(
        tmp_path,
        primary_client=FakeDashScope(
            submit_error=AlibabaGatewayRateLimitError("Throttling")
        ),
        openrouter=openrouter, artifact=artifact,
    )
    wrapper._fallback_enabled = False
    result = wrapper.generate_scene(make_request())
    assert not result.success
    assert openrouter.calls == 0

    # unavailable fallback provider
    class UnavailableOR(FakeOpenRouterVideo):
        def is_available(self):
            return False

    FakeOpenRouterVideo.calls = 0
    wrapper2 = build_wrapper(
        tmp_path,
        primary_client=FakeDashScope(
            submit_error=AlibabaGatewayRateLimitError("Throttling")
        ),
        openrouter=UnavailableOR(succeed=True, artifact=artifact),
        artifact=artifact,
    )
    result2 = wrapper2.generate_scene(make_request())
    assert not result2.success


def test_wrapper_availability_requires_only_the_primary(tmp_path):
    artifact = make_clip(tmp_path / "avail.mp4")
    wrapper = build_wrapper(
        tmp_path,
        primary_client=FakeDashScope(),
        openrouter=FakeOpenRouterVideo(succeed=True, artifact=artifact),
        artifact=artifact,
    )
    assert wrapper.is_available() is True
    wrapper._primary._api_key = ""
    assert wrapper.is_available() is False


def test_wrapper_resume_delegation_by_checkpoint_identity(tmp_path):
    artifact = make_clip(tmp_path / "delegate.mp4")
    openrouter = FakeOpenRouterVideo(succeed=True, artifact=artifact)
    primary_client = FakeDashScope(states=["SUCCEEDED"])
    primary_client._download = artifact.read_bytes()
    wrapper = build_wrapper(
        tmp_path, primary_client=primary_client,
        openrouter=openrouter, artifact=artifact,
    )
    # Alibaba checkpoint -> primary resume (polls, never submits).
    assert wrapper.wants_resume({
        "provider": ALIBABA_VIDEO_PROVIDER, "task_id": "t",
        "status": "polling",
    }) is True
    result = wrapper.resume_scene(
        make_request(),
        {"provider": ALIBABA_VIDEO_PROVIDER, "task_id": "task-001",
         "status": "generation_submitted"},
    )
    assert result.success
    assert primary_client.submit_calls == []

    # OpenRouter checkpoint -> delegated to the fallback provider's own
    # wants_resume hook (its job identity lives there).
    or_checkpoint = {
        "provider": "openrouter_video", "job_id": "j", "polling_url": "u",
        "status": "generation_submitted",
    }
    assert wrapper.wants_resume(or_checkpoint) is True


def test_openrouter_fallback_failure_returns_truthful_failure(tmp_path):
    artifact = make_clip(tmp_path / "or_fail.mp4")
    FakeOpenRouterVideo.calls = 0
    openrouter = FakeOpenRouterVideo(succeed=False, artifact=artifact)
    wrapper = build_wrapper(
        tmp_path,
        primary_client=FakeDashScope(
            submit_error=AlibabaGatewayRateLimitError("Throttling")
        ),
        openrouter=openrouter, artifact=artifact,
    )
    result = wrapper.generate_scene(make_request())
    assert not result.success
    assert openrouter.calls == 1
    verification = result.verification
    assert verification["fallback_used"] is True
    assert verification["fallback_reason"] == FailureCategory.RATE_LIMIT
    assert verification["final_provider"] == "openrouter_video"
    assert verification["provider"] == "openrouter_video"


def test_fallback_rate_limit_invokes_openrouter_once_and_annotates(tmp_path):
    artifact = make_clip(tmp_path / "or_artifact.mp4")
    FakeOpenRouterVideo.calls = 0
    primary_client = FakeDashScope(
        submit_error=AlibabaGatewayRateLimitError("Throttling")
    )
    openrouter = FakeOpenRouterVideo(succeed=True, artifact=artifact)
    wrapper = build_wrapper(tmp_path, primary_client=primary_client,
                            openrouter=openrouter, artifact=artifact)

    result = wrapper.generate_scene(make_request())

    assert result.success
    # one classified fallback invocation — never parallel, never a retry loop
    assert openrouter.calls == 1
    verification = result.verification
    assert verification["primary_provider"] == ALIBABA_VIDEO_PROVIDER
    assert verification["final_provider"] == "openrouter_video"
    assert verification["fallback_used"] is True
    assert verification["fallback_reason"] == FailureCategory.RATE_LIMIT
    assert verification["provider"] == "openrouter_video"
    assert any("alibaba_fallback_reason=rate_limit" in w
               for w in result.warnings)



# ---------------------------------------------------------------------------
# Alibaba image source (Phase 6F seam, Phase 6C intake upstream)
# ---------------------------------------------------------------------------


class FakeImageClient:
    """Fake DashScope image boundary with scripted poll states + PNG bytes."""

    def __init__(self, *, states=None, download=None, submit_error=None):
        self._states = list(states or [])
        self._download = download
        self.submit_error = submit_error
        self.submit_calls = []
        self.poll_calls = []

    def submit_task(self, path, body, operation="x"):
        self.submit_calls.append((path, body))
        if self.submit_error is not None:
            raise self.submit_error
        return {"output": {"task_id": "img-task-1", "task_status": "PENDING"}}

    def poll_task(self, task_id, operation="x"):
        self.poll_calls.append(task_id)
        if not self._states:
            return {"output": {"task_id": task_id, "task_status": "RUNNING"}}
        state = self._states.pop(0)
        if state == "SUCCEEDED":
            return {
                "output": {
                    "task_id": task_id, "task_status": "SUCCEEDED",
                    "results": [{"url": "https://media.test/img.png"}],
                },
                "usage": {"image_count": 1},
            }
        return {"output": {"task_id": task_id, "task_status": state}}

    def download_bytes(self, url, operation="x", **kwargs):
        if isinstance(self._download, Exception):
            raise self._download
        return self._download


def make_image_ctx(tmp_path, *, aspect_ratio="9:16"):
    from app.orchestrator.visual_sources import VisualSourceContext

    return VisualSourceContext(
        project_id=PROJECT_ID,
        scene_id="scene_img",
        sequence=1,
        visual_prompt="A mecha in neon rain",
        aspect_ratio=aspect_ratio,
        output_dir=tmp_path,
    )


def make_image_source(tmp_path, client, *, model="wan2.2-t2i-flash"):
    from app.orchestrator.alibaba_image import AlibabaImageSource

    return AlibabaImageSource(
        tmp_path, api_key="sk-ws-abc123", model=model,
        http_client=client, poll_interval=0.0, poll_max_attempts=3,
    )


def test_image_request_translation_is_explicit(tmp_path):
    client = FakeImageClient(
        states=["SUCCEEDED"],
        download=make_png(tmp_path / "src.png").read_bytes(),
    )
    source = make_image_source(tmp_path, client)
    result = source.source(make_image_ctx(tmp_path))

    assert result.status.value == "success"
    path, body = client.submit_calls[0]
    assert path.endswith("/api/v1/services/aigc/text2image/image-synthesis")
    assert body["model"] == "wan2.2-t2i-flash"
    assert body["parameters"]["size"] == "720*1280"
    assert body["parameters"]["n"] == 1
    assert "mecha" in body["input"]["prompt"]


def test_image_success_materializes_locally_with_provenance(tmp_path):
    png = make_png(tmp_path / "img_src.png")
    client = FakeImageClient(states=["RUNNING", "SUCCEEDED"], download=png.read_bytes())
    source = make_image_source(tmp_path, client)
    result = source.source(make_image_ctx(tmp_path))

    assert result.status.value == "success"
    assert result.provider_id == "alibaba"
    assert result.artifact_path is not None and result.artifact_path.is_file()
    metadata = result.metadata
    assert metadata["kind"] == "ai_generated"
    assert metadata["source_type"] == "ai_generated"
    assert metadata["provider"] == "alibaba"
    assert metadata["model"] == "wan2.2-t2i-flash"
    assert metadata["task_id"] == "img-task-1"


def test_image_failure_states_are_classified(tmp_path):
    png = make_png(tmp_path / "img_unused.png")
    cases = [
        # (client, expected category, expected eligibility)
        (FakeImageClient(submit_error=AlibabaGatewayRateLimitError("429")),
         FailureCategory.RATE_LIMIT, True),
        (FakeImageClient(submit_error=AlibabaGatewayAuthError("401")),
         FailureCategory.AUTHENTICATION, False),
        (FakeImageClient(states=["FAILED"]),
         FailureCategory.TASK_FAILURE, True),
    ]
    for client, category, eligible in cases:
        source = make_image_source(tmp_path, client)
        result = source.source(make_image_ctx(tmp_path))
        assert result.status.value == "failure", category
        assert result.metadata["failure_category"] == category
        assert result.metadata["fallback_eligible"] is eligible


# ---------------------------------------------------------------------------
# Visual routing: classified chain stop + default auto chain ordering
# ---------------------------------------------------------------------------


def test_routing_stops_after_non_eligible_generation_failure(tmp_path):
    from app.orchestrator.visual_sources import (
        RoutedVisualSource,
        create_visual_source,
    )

    client = FakeImageClient(submit_error=AlibabaGatewayModerationError("x"))
    alibaba = make_image_source(tmp_path, client)
    client._download = b""

    class CountingOpenRouter:
        name = "openrouter"
        calls = 0

        def availability(self):
            from app.orchestrator.visual_sources import SourceAvailability
            return SourceAvailability.AVAILABLE

        def configuration_summary(self):
            return "counting openrouter"

        def source(self, ctx):
            type(self).calls += 1
            raise AssertionError("must never be called")

    routed = RoutedVisualSource(
        [alibaba, CountingOpenRouter()], name="routed", output_dir=tmp_path,
    )
    result = routed.source(make_image_ctx(tmp_path))

    assert result.status.value == "failure"
    assert CountingOpenRouter.calls == 0, (
        "a non-eligible failure must stop the chain — never re-send the "
        "same request to OpenRouter"
    )
    assert "NOT fallback-eligible" in (result.message or "")


def test_routing_falls_through_on_eligible_failure(tmp_path):
    from app.orchestrator.visual_sources import (
        RoutedVisualSource,
        VisualSourceResult,
        VisualSourceStatus,
    )

    client = FakeImageClient(submit_error=AlibabaGatewayRateLimitError("429"))
    alibaba = make_image_source(tmp_path, client)
    client._download = b""

    class StubStock:
        name = "pexels"
        calls = 0

        def availability(self):
            from app.orchestrator.visual_sources import SourceAvailability
            return SourceAvailability.AVAILABLE

        def configuration_summary(self):
            return "stub stock"

        def source(self, ctx):
            type(self).calls += 1
            return VisualSourceResult.success(
                ctx.scene_id, self.name, make_png(tmp_path / "stock.png"),
                metadata={"kind": "stock"},
            )

    stock = StubStock()
    routed = RoutedVisualSource(
        [alibaba, stock], name="routed", output_dir=tmp_path,
    )
    result = routed.source(make_image_ctx(tmp_path))
    assert result.status.value == "success"
    assert stock.calls == 1, "eligible failures keep the chain fall-through"
    assert result.provider_id == "pexels"


def test_default_auto_chain_starts_with_alibaba_then_openrouter(tmp_path):
    from app.orchestrator.alibaba_image import AlibabaImageSource
    from app.orchestrator.visual_sources import (
        OpenRouterImageSource,
        PexelsImageSource,
        RoutedVisualSource,
        WikimediaCommonsImageSource,
        create_visual_source,
    )

    source = create_visual_source("auto", tmp_path)
    assert isinstance(source, RoutedVisualSource)
    kinds = [type(child).__name__ for child in source.children]
    assert kinds == [
        "AlibabaImageSource",
        "OpenRouterImageSource",
        "PexelsImageSource",
        "WikimediaCommonsImageSource",
    ]
    assert isinstance(source.children[0], AlibabaImageSource)


def test_explicit_alibaba_selector_maps_to_the_image_source(tmp_path):
    from app.orchestrator.alibaba_image import AlibabaImageSource
    from app.orchestrator.visual_sources import create_visual_source

    source = create_visual_source("alibaba", tmp_path)
    assert isinstance(source, AlibabaImageSource)


def test_image_poll_timeout_is_bounded_and_reports_in_flight_task(tmp_path):
    client = FakeImageClient(states=["RUNNING"])
    source = make_image_source(tmp_path, client)
    result = source.source(make_image_ctx(tmp_path))
    assert result.status.value == "failure"
    assert result.metadata["failure_category"] == (
        FailureCategory.POLL_TIMEOUT
    )
    assert result.metadata["task_id"] == "img-task-1"


def test_image_malformed_success_response_is_eligible_failure(tmp_path):
    client = FakeImageClient(states=["SUCCEEDED"], download=b"")
    # SUCCEEDED but empty download -> network/response classification
    source = make_image_source(tmp_path, client)
    result = source.source(make_image_ctx(tmp_path))
    assert result.status.value == "failure"


def test_image_unconfigured_reports_requires_configuration(tmp_path):
    from app.orchestrator.alibaba_image import AlibabaImageSource
    from app.orchestrator.visual_sources import SourceAvailability

    source = AlibabaImageSource(tmp_path, api_key="")
    assert source.availability() == SourceAvailability.REQUIRES_CONFIGURATION


