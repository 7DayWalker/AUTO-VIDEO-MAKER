"""Phase 7E - hermetic OpenRouter video gateway tests.

No OpenRouter requests are made here.  The HTTP boundary, model catalog, remote
job lifecycle, and download are all represented by small fakes.  MP4 fixtures
are generated locally with FFmpeg so the existing Phase 7A and Phase 7D
contracts are exercised end to end.
"""

import json
import subprocess
from pathlib import Path

import pytest

from app.flow.scene_models import FlowGenerationConfig, SceneGenerationRequest
from app.orchestrator.models import OrchestratorConfig
from app.orchestrator.openrouter_video import (
    OPENROUTER_FREE,
    OPENROUTER_PAID,
    OPENROUTER_VIDEO_PROVIDER,
    OpenRouterHTTPClient,
    OpenRouterVideoCapabilityError,
    OpenRouterVideoConfigurationError,
    OpenRouterVideoModel,
    OpenRouterVideoVisualProvider,
    OpenRouterVideoCatalog,
    build_video_request,
    capability_errors,
    classify_access,
    compatible_models,
    parse_video_model_catalog,
    select_video_model,
)
from app.orchestrator.visual_providers import (
    VALID_PROVIDERS,
    create_visual_provider,
    normalize_provider,
)


PROJECT_ID = "20260829_openrouter_probe_001"


def make_clip(path: Path, *, seconds: float = 1.0, size: str = "180x320", with_audio: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    from app.assembly.ffmpeg import resolve_ffmpeg

    args = [
        resolve_ffmpeg() or "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=blue:s={size}:d={seconds}:r=30",
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


def make_request(*, aspect_ratio="9:16", duration=4) -> SceneGenerationRequest:
    return SceneGenerationRequest(
        project_id=PROJECT_ID,
        scene_id="scene_001",
        sequence=1,
        visual_prompt="A cinematic test scene with a slow camera push-in.",
        config=FlowGenerationConfig(
            model="internal-planning-model",
            aspect_ratio=aspect_ratio,
            duration=duration,
            count=1,
            agent_enabled=False,
        ),
    )


def model_payload(
    model_id="free/video",
    *,
    price="0",
    durations=(4,),
    resolutions=("720p",),
    ratios=("9:16",),
    sizes=None,
    audio=True,
    seed=True,
    frame_images=("first_frame",),
    passthrough=(),
):
    return {
        "id": model_id,
        "canonical_slug": model_id,
        "name": model_id.title(),
        "description": "test model",
        "created": 1787990000,
        "supported_resolutions": list(resolutions) if resolutions is not None else None,
        "supported_aspect_ratios": list(ratios) if ratios is not None else None,
        "supported_sizes": list(sizes) if sizes is not None else None,
        "supported_durations": list(durations) if durations is not None else None,
        "supported_frame_images": list(frame_images) if frame_images is not None else None,
        "generate_audio": audio,
        "seed": seed,
        "pricing_skus": {"duration_seconds": price} if price is not None else {},
        "allowed_passthrough_parameters": list(passthrough),
    }


class FakeCatalog:
    def __init__(self, models):
        self._models = list(models)
        self.calls = []

    def models(self, *, refresh=False):
        self.calls.append(refresh)
        return list(self._models)


class FakeOpenRouterClient:
    def __init__(self, video_bytes=b"", statuses=None, submission=None, error=None):
        self.video_bytes = video_bytes
        self.statuses = list(statuses or [{"status": "completed"}])
        self.submission = submission or {
            "id": "job_001",
            "status": "pending",
            "polling_url": "https://openrouter.test/api/v1/videos/job_001",
        }
        self.error = error
        self.submit_calls = []
        self.poll_calls = []
        self.download_calls = []

    def submit_video(self, body):
        if self.error:
            raise self.error
        self.submit_calls.append(dict(body))
        return dict(self.submission)

    def get_generation(self, polling_url):
        self.poll_calls.append(polling_url)
        if not self.statuses:
            return {"status": "processing"}
        return dict(self.statuses.pop(0))

    def get_video_content(self, video_id, content_url=None):
        self.download_calls.append((video_id, content_url))
        return self.video_bytes


class FakeResponse:
    def __init__(self, *, payload=None, content=b"", ok=True, status_code=200, headers=None, text=""):
        self._payload = payload or {}
        self.content = content
        self.ok = ok
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "application/json"}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.get_calls = []
        self.post_calls = []
        self.get_response = FakeResponse(payload={"data": []})
        self.post_response = FakeResponse(payload={"id": "job"})

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_response

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_response


def provider(tmp_path, catalog, client, **kwargs):
    kwargs.setdefault("poll_max_attempts", 3)
    return OpenRouterVideoVisualProvider(
        tmp_path / "scenes",
        api_key="test-key-not-real",
        catalog=FakeCatalog(catalog),
        http_client=client,
        poll_interval=0,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Registration, catalog, cache, access classification, and selection
# ---------------------------------------------------------------------------


def test_provider_registration_and_factory_are_generic():
    assert normalize_provider("OPENROUTER_VIDEO") == OPENROUTER_VIDEO_PROVIDER
    assert OPENROUTER_VIDEO_PROVIDER in VALID_PROVIDERS
    instance = create_visual_provider(
        "hybrid",
        scene_config={"openrouter_model": "vendor/future-video"},
        scenes_output_dir=Path("scenes"),
        provider_name=OPENROUTER_VIDEO_PROVIDER,
    )
    assert isinstance(instance, OpenRouterVideoVisualProvider)
    assert instance.model == "vendor/future-video"
    assert instance.requires_browser is False


def test_provider_is_unavailable_without_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("AVM_OPENROUTER_API_KEY", raising=False)
    instance = OpenRouterVideoVisualProvider(tmp_path / "scenes")
    assert instance.is_available() is False
    result = instance.generate_scene(make_request())
    assert result.success is False
    assert "AVM_OPENROUTER_API_KEY" in result.error


def test_catalog_parsing_preserves_capabilities_and_filters_non_video_entries():
    payload = {
        "data": [
            model_payload("vendor/video", price="0.20", durations=(4, 8)),
            {"id": "vendor/text", "architecture": {"output_modalities": ["text"]}},
            {"name": "invalid"},
        ]
    }
    models = parse_video_model_catalog(payload)
    assert [model.model_id for model in models] == ["vendor/video", "vendor/text"]
    assert models[0].supported_durations == (4, 8)
    assert models[0].supported_resolutions == ("720p",)
    assert models[0].allowed_passthrough_parameters == ()
    assert [model.model_id for model in compatible_models(models, make_request())] == ["vendor/video"]


def test_video_only_filter_excludes_general_catalog_models():
    models = parse_video_model_catalog(
        {"data": [model_payload("vendor/video"), {"id": "vendor/text", "output_modalities": ["text"]}]}
    )
    assert [model.model_id for model in models if model.video_capability] == ["vendor/video"]


@pytest.mark.parametrize(
    ("pricing", "model_id", "expected"),
    [(None, "vendor/unknown", OPENROUTER_PAID), ({"a": "0"}, "vendor/free", OPENROUTER_FREE), ({"a": "0", "b": "0.1"}, "vendor/mixed", OPENROUTER_PAID), ({}, "vendor/empty", OPENROUTER_PAID), ({}, "vendor/empty:free", OPENROUTER_FREE)],
)
def test_access_classification_is_pricing_driven(pricing, model_id, expected):
    assert classify_access(pricing, model_id) == expected


def test_free_first_selection_is_deterministic_and_never_selects_paid_automatically():
    request = make_request()
    models = [
        OpenRouterVideoModel.from_dict(model_payload("vendor/z-free", price="0")),
        OpenRouterVideoModel.from_dict(model_payload("vendor/a-free", price="0")),
        OpenRouterVideoModel.from_dict(model_payload("vendor/a-paid", price="0.20")),
    ]
    assert select_video_model(models, request).model_id == "vendor/a-free"
    with pytest.raises(OpenRouterVideoConfigurationError, match="paid"):
        select_video_model(
            [OpenRouterVideoModel.from_dict(model_payload("vendor/a-paid", price="0.20"))],
            request,
            allow_paid=True,
        )


def test_explicit_paid_model_requires_authorization_but_can_be_selected():
    request = make_request()
    paid = OpenRouterVideoModel.from_dict(model_payload("vendor/paid", price="0.20"))
    with pytest.raises(OpenRouterVideoConfigurationError, match="paid"):
        select_video_model([paid], request, explicit_model=paid.model_id)
    assert select_video_model([paid], request, explicit_model=paid.model_id, allow_paid=True) == paid


def test_no_silent_free_to_paid_fallback_when_free_is_incompatible():
    request = make_request(duration=8)
    free = OpenRouterVideoModel.from_dict(model_payload("vendor/free", price="0", durations=(4,)))
    paid = OpenRouterVideoModel.from_dict(model_payload("vendor/paid", price="0.20", durations=(8,)))
    with pytest.raises(OpenRouterVideoConfigurationError, match="paid"):
        select_video_model([free, paid], request)


def test_catalog_cache_refreshes_stale_metadata_without_becoming_source_of_truth(tmp_path):
    cache_path = tmp_path / "models.json"
    first = OpenRouterVideoModel.from_dict(model_payload("vendor/first", price="0"))
    second = OpenRouterVideoModel.from_dict(model_payload("vendor/second", price="0.20"))
    calls = []

    class Client:
        def list_video_models(self):
            calls.append(1)
            return {"data": [second.to_dict()]}

    clock = [100.0]
    catalog = OpenRouterVideoCatalog(Client(), cache_path=cache_path, ttl_seconds=10, clock=lambda: clock[0])
    catalog._write_cache([first])
    assert catalog.models()[0].model_id == "vendor/first"
    assert calls == []
    clock[0] = 111.0
    assert catalog.models()[0].model_id == "vendor/second"
    assert calls == [1]
    assert not cache_path.with_name("models.json.part").exists()


# ---------------------------------------------------------------------------
# Capability validation and request translation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scene_request", "model", "needle"),
    [
        (make_request(aspect_ratio="16:9"), model_payload(ratios=("9:16",)), "aspect ratio"),
        (make_request(duration=8), model_payload(durations=(4,)), "duration"),
        (make_request(), model_payload(resolutions=("1080p",)), "resolution"),
    ],
)
def test_capability_mismatch_is_reported_before_submission(scene_request, model, needle):
    parsed = OpenRouterVideoModel.from_dict(model)
    errors = capability_errors(parsed, scene_request)
    assert any(needle in error for error in errors)
    fake_client = FakeOpenRouterClient()
    result = provider(Path("."), [parsed], fake_client, model=parsed.model_id).generate_scene(scene_request)
    assert result.success is False
    assert needle in result.error
    assert fake_client.submit_calls == []


def test_audio_capability_is_required_before_submission(tmp_path):
    request = make_request()
    no_audio = OpenRouterVideoModel.from_dict(model_payload("vendor/no-audio", audio=False))
    result = provider(tmp_path, [no_audio], FakeOpenRouterClient(), model=no_audio.model_id, generate_audio=True).generate_scene(request)
    assert result.success is False
    assert "native audio" in result.error


def test_request_translation_uses_api_fields_and_not_internal_planning_fields():
    request = make_request(duration=4)
    model = OpenRouterVideoModel.from_dict(model_payload("vendor/future", price="0", passthrough=("quality",)))
    body = build_video_request(
        model,
        request,
        resolution="720p",
        generate_audio=True,
        seed=7,
        passthrough={"quality": "high"},
    )
    assert body == {
        "model": "vendor/future",
        "prompt": request.visual_prompt,
        "duration": 4,
        "resolution": "720p",
        "aspect_ratio": "9:16",
        "generate_audio": True,
        "seed": 7,
        "quality": "high",
    }
    assert "agent_enabled" not in body
    assert "count" not in body
    assert "internal-planning-model" not in json.dumps(body)


def test_request_translation_rejects_unsupported_passthrough():
    model = OpenRouterVideoModel.from_dict(model_payload("vendor/video", price="0"))
    with pytest.raises(OpenRouterVideoCapabilityError, match="passthrough"):
        build_video_request(model, make_request(), passthrough={"not_allowed": True})


def test_reference_frame_request_is_capability_checked():
    model = OpenRouterVideoModel.from_dict(model_payload("vendor/video", price="0", frame_images=("first_frame",)))
    body = build_video_request(
        model,
        make_request(),
        frame_images=[
            {"type": "image_url", "image_url": {"url": "https://example.test/frame.jpg"}, "frame_type": "first_frame"}
        ],
    )
    assert body["frame_images"][0]["frame_type"] == "first_frame"


# ---------------------------------------------------------------------------
# HTTP boundary
# ---------------------------------------------------------------------------


def test_http_client_uses_documented_paths_auth_and_json_body():
    session = FakeSession()
    session.get_response = FakeResponse(payload={"data": [{"id": "vendor/video"}]})
    session.post_response = FakeResponse(payload={"id": "job", "polling_url": "https://router.test/poll"})
    client = OpenRouterHTTPClient(api_key="test-key-not-real", base_url="https://router.test/api/v1", session=session)
    assert client.list_video_models()["data"][0]["id"] == "vendor/video"
    client.submit_video({"model": "vendor/video", "prompt": "test"})
    assert session.get_calls[0][0] == "https://router.test/api/v1/videos/models"
    assert session.post_calls[0][0] == "https://router.test/api/v1/videos"
    assert session.post_calls[0][1]["json"]["model"] == "vendor/video"
    assert session.post_calls[0][1]["headers"]["Authorization"] == "Bearer test-key-not-real"


def test_http_client_rejects_api_failure_and_bad_media_type():
    session = FakeSession()
    session.get_response = FakeResponse(ok=False, status_code=401, payload={"error": {"message": "unauthorized"}})
    client = OpenRouterHTTPClient(api_key="test-key-not-real", session=session)
    with pytest.raises(Exception, match="unauthorized"):
        client.list_video_models()

    session.get_response = FakeResponse(content=b"not-video", headers={"Content-Type": "text/plain"})
    with pytest.raises(Exception, match="media type"):
        client.get_video_content("job_001")


# ---------------------------------------------------------------------------
# Async lifecycle, output contract, audio facts, provenance, and idempotency
# ---------------------------------------------------------------------------


def test_async_generation_downloads_atomically_validates_and_reports_audio(tmp_path):
    fixture = make_clip(tmp_path / "fixture.mp4", with_audio=True)
    fake_client = FakeOpenRouterClient(
        video_bytes=fixture.read_bytes(),
        statuses=[{"status": "processing"}, {"status": "completed", "usage": {"seconds": 4}, "actual_cost": "0.00"}],
    )
    model = OpenRouterVideoModel.from_dict(model_payload("vendor/free-video", price="0"))
    checkpoints = []
    result = provider(tmp_path, [model], fake_client, model=model.model_id).generate_scene(
        make_request(), checkpoint_callback=checkpoints.append
    )
    output = tmp_path / "scenes" / "scene_001.mp4"
    assert result.success is True
    assert result.file_path == output
    assert output.is_file()
    assert not output.with_name(output.name + ".part").exists()
    assert result.audio_facts is not None
    assert result.audio_facts.has_audio is True
    assert result.audio_facts.is_synthetic is False
    # Phase 7D audio facts must fully flow from the returned media (fixture is
    # AAC / 44.1 kHz / stereo), not just the presence flags.
    assert result.audio_facts.audio_codec == "aac"
    assert result.audio_facts.sample_rate == 44100
    assert result.audio_facts.channels == 2
    assert result.audio_facts.audio_duration is not None
    assert 0.4 <= result.audio_facts.audio_duration <= 1.6
    assert result.verification["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert result.verification["model"] == model.model_id
    assert result.verification["access_route"] == OPENROUTER_FREE
    assert result.verification["actual_cost"] == "0.00"
    assert result.verification["usage"] == {"seconds": 4}
    assert any(item.get("status") == "generation_submitted" for item in checkpoints)
    assert checkpoints[-1]["file_path"] == str(output)
    assert len(fake_client.submit_calls) == 1
    assert len(fake_client.poll_calls) == 2
    assert len(fake_client.download_calls) == 1


def test_polling_timeout_preserves_remote_identity_and_never_resubmits(tmp_path):
    fake_client = FakeOpenRouterClient(
        statuses=[{"status": "processing"}, {"status": "processing"}],
    )
    model = OpenRouterVideoModel.from_dict(model_payload("vendor/free-video", price="0"))
    checkpoints = []
    result = provider(tmp_path, [model], fake_client, model=model.model_id, poll_max_attempts=2).generate_scene(
        make_request(), checkpoint_callback=checkpoints.append
    )
    assert result.success is False
    assert result.status == "failed"
    assert "not resubmitted" in result.error
    timeout = [item for item in checkpoints if item.get("status") == "timeout"][-1]
    assert timeout["job_id"] == "job_001"
    assert len(fake_client.submit_calls) == 1


def test_expired_status_is_an_unrecoverable_terminal_failure(tmp_path):
    """A remote ``expired`` job must be classified explicitly as unrecoverable,
    never as a generic polling timeout, and never resubmitted."""
    model = OpenRouterVideoModel.from_dict(model_payload("vendor/free-video", price="0"))
    fake_client = FakeOpenRouterClient(
        statuses=[{"status": "expired", "error": {"message": "generation window elapsed"}}],
    )
    checkpoints = []
    result = provider(tmp_path, [model], fake_client, model=model.model_id).generate_scene(
        make_request(), checkpoint_callback=checkpoints.append
    )
    assert result.success is False
    assert result.status == "failed"
    assert "expired" in result.error
    assert "not resubmitted" in result.error
    assert "polling timed out" not in result.error  # distinct from a timeout
    assert result.verification["provider"] == OPENROUTER_VIDEO_PROVIDER
    assert result.verification["model"] == model.model_id
    assert result.verification["access"] == OPENROUTER_FREE
    terminal = [item for item in checkpoints if item.get("remote_status") == "expired"][-1]
    assert terminal["job_id"] == "job_001"
    assert terminal["model"] == model.model_id
    assert len(fake_client.submit_calls) == 1  # one submission, never a second


def test_resume_of_expired_job_does_not_poll_or_resubmit(tmp_path):
    """A checkpoint already marked terminal (expired) must be failed truthfully
    without any new submission AND without even re-polling the dead job."""
    model = OpenRouterVideoModel.from_dict(model_payload("vendor/free-video", price="0"))
    fake_client = FakeOpenRouterClient()
    checkpoints = []
    result = provider(tmp_path, [model], fake_client, model=model.model_id).resume_scene(
        make_request(),
        {
            "provider": OPENROUTER_VIDEO_PROVIDER,
            "status": "failed",
            "remote_status": "expired",
            "error": "generation window elapsed",
            "job_id": "job_001",
            "polling_url": "https://openrouter.test/api/v1/videos/job_001",
            "model": model.model_id,
            "access": OPENROUTER_FREE,
        },
        checkpoint_callback=checkpoints.append,
    )
    assert result.success is False
    assert "expired" in result.error
    assert "not resubmitted" in result.error
    assert result.verification["job_id"] == "job_001"
    assert fake_client.submit_calls == []
    assert fake_client.poll_calls == []  # terminal failure is not re-polled
    assert fake_client.download_calls == []


def test_remote_job_resume_polls_existing_job_without_submit(tmp_path):
    fixture = make_clip(tmp_path / "fixture.mp4", with_audio=False)
    fake_client = FakeOpenRouterClient(
        video_bytes=fixture.read_bytes(),
        statuses=[{"status": "completed"}],
    )
    model = OpenRouterVideoModel.from_dict(model_payload("vendor/free-video", price="0", audio=False))
    checkpoints = []
    result = provider(tmp_path, [model], fake_client, model=model.model_id).resume_scene(
        make_request(),
        {
            "provider": OPENROUTER_VIDEO_PROVIDER,
            "status": "timeout",
            "job_id": "job_001",
            "polling_url": "https://openrouter.test/api/v1/videos/job_001",
            "model": model.model_id,
            "access": OPENROUTER_FREE,
        },
        checkpoint_callback=checkpoints.append,
    )
    assert result.success is True
    assert len(fake_client.submit_calls) == 0
    assert len(fake_client.poll_calls) == 1
    assert checkpoints[-1]["job_id"] == "job_001"


def test_completed_valid_local_scene_is_reused_without_catalog_or_provider_call(tmp_path):
    output_dir = tmp_path / "scenes"
    output = make_clip(output_dir / "scene_001.mp4")
    client = FakeOpenRouterClient(error=AssertionError("provider invoked for reusable output"))
    instance = provider(tmp_path, [], client, model="vendor/video")
    result = instance.generate_scene(make_request())
    assert result.success is True
    assert result.file_path == output
    assert result.verification["reuse"] == "completed_valid_local_scene"
    assert client.submit_calls == []


@pytest.mark.parametrize("size", ["320x180", "180x320"])
def test_invalid_output_aspect_is_rejected_and_part_removed(tmp_path, size):
    fixture = make_clip(tmp_path / "fixture.mp4", size=size, with_audio=False)
    model = OpenRouterVideoModel.from_dict(model_payload("vendor/free-video", price="0", audio=False))
    fake_client = FakeOpenRouterClient(video_bytes=fixture.read_bytes())
    result = provider(tmp_path, [model], fake_client, model=model.model_id).generate_scene(make_request())
    if size == "320x180":
        assert result.success is False
        output = tmp_path / "scenes" / "scene_001.mp4"
        assert not output.exists()
        assert not output.with_name(output.name + ".part").exists()
    else:
        assert result.success is True


def test_provider_failure_is_truthful_and_does_not_retry_submission(tmp_path):
    model = OpenRouterVideoModel.from_dict(model_payload("vendor/free-video", price="0"))
    fake_client = FakeOpenRouterClient(statuses=[{"status": "failed", "error": {"message": "policy rejected"}}])
    result = provider(tmp_path, [model], fake_client, model=model.model_id).generate_scene(make_request())
    assert result.success is False
    assert "policy rejected" in result.error
    assert len(fake_client.submit_calls) == 1


def test_paid_provider_never_submits_when_paid_disabled(tmp_path):
    model = OpenRouterVideoModel.from_dict(model_payload("vendor/paid-video", price="0.20"))
    fake_client = FakeOpenRouterClient()
    result = provider(tmp_path, [model], fake_client, model=model.model_id, allow_paid=False).generate_scene(make_request())
    assert result.success is False
    assert "paid" in result.error
    assert fake_client.submit_calls == []


def test_orchestrator_config_persists_openrouter_controls_without_key():
    config = OrchestratorConfig(
        topic="OpenRouter configuration test",
        flow_project_url="",
        visual_mode="hybrid",
        visual_default_provider=OPENROUTER_VIDEO_PROVIDER,
        openrouter_model="vendor/video",
        openrouter_resolution="720p",
        openrouter_allow_paid=True,
        openrouter_allow_paid_fallback=True,
        openrouter_generate_audio=True,
    )
    scene_config = config.to_scene_config_dict()
    assert scene_config["openrouter_model"] == "vendor/video"
    assert scene_config["openrouter_allow_paid"] is True
    assert scene_config["openrouter_allow_paid_fallback"] is True
    assert scene_config["openrouter_generate_audio"] is True
    assert "api_key" not in json.dumps(scene_config).lower()
