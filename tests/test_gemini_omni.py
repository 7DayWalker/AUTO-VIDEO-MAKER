"""Phase 7C — Gemini Omni visual provider tests.

Hermetic: the Gemini client boundary is mocked; no real API calls, no API
key required, no network access, no credits. Fixtures are deterministic
local FFmpeg MP4s.
"""

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.flow.scene_generator import validate_video_file
from app.flow.scene_models import FlowGenerationConfig, SceneGenerationRequest
from app.orchestrator.visual_providers import (
    GEMINI_OMNI_DEFAULT_MODEL,
    GEMINI_OMNI_PROVIDER,
    GeminiOmniVisualProvider,
    ZERO_COST_PROVIDERS,
    VALID_PROVIDERS,
    create_visual_provider,
    normalize_provider,
)

from tests.test_asset_workflow import build_orchestrator, make_clip

PROJECT_ID = "20260103_assets_probe_001"


# ---------------------------------------------------------------------------
# Fake Gemini client boundary
# ---------------------------------------------------------------------------

def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


class _FakeInteractions:
    """Records create submissions; scripted get() for async polling."""

    def __init__(self, create_fn=None, get_fn=None):
        self._create_fn = create_fn
        self._get_fn = get_fn
        self.create_calls = []
        self.get_calls = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if self._create_fn is not None:
            return self._create_fn(**kwargs)
        raise AssertionError("unexpected create submission")

    def get(self, id=None, **kwargs):
        self.get_calls.append(id)
        if self._get_fn is not None:
            return self._get_fn(id)
        raise AssertionError("unexpected interaction poll")


class _FakeFiles:
    """Scripted file-state lifecycle: states pop in order per get()."""

    def __init__(self, states=(), download_bytes=b"", get_error=None):
        self._states = list(states)
        self._download_bytes = download_bytes
        self._get_error = get_error
        self.download_calls = []

    def get(self, name=None, **kwargs):
        if self._get_error is not None:
            raise self._get_error
        state = self._states.pop(0) if self._states else "FAILED"
        return _ns(state=state, error=None, name=name, uri=name)

    def download(self, file=None, **kwargs):
        self.download_calls.append(file)
        return self._download_bytes


class _FakeClient:
    def __init__(self, interactions=None, files=None):
        self.interactions = interactions
        self.files = files


def _inline_interaction(mp4_bytes, interaction_id="int_001", model=None):
    """A completed interaction with inline (base64) video data — real shape."""
    return _ns(
        id=interaction_id,
        status="completed",
        model=model,
        errors=None,
        output_video=_ns(
            data=base64.b64encode(mp4_bytes).decode("ascii"),
            mime_type="video/mp4",
            uri=None,
            type="video",
        ),
    )


def _uri_interaction(uri, interaction_id="int_001", status="completed"):
    return _ns(
        id=interaction_id,
        status=status,
        model=None,
        errors=None,
        output_video=_ns(
            data=None, mime_type="video/mp4", uri=uri, type="video"
        ),
    )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fixture_mp4(tmp_path: Path) -> bytes:
    path = tmp_path / "_fixture.mp4"
    make_clip(path, seconds=1)
    return path.read_bytes()


def _make_provider(tmp_path, fake_client, **kwargs):
    kwargs.setdefault("api_key", "test-key-not-real")
    kwargs.setdefault("poll_interval", 0.0)
    kwargs.setdefault("poll_max_attempts", 3)
    provider = GeminiOmniVisualProvider(tmp_path / "scenes" / PROJECT_ID, **kwargs)
    provider._sleep = lambda seconds: None  # no real sleeping in tests
    provider._get_client = lambda: fake_client
    return provider


def _make_request(scene_id="scene_001", sequence=1, duration=2):
    return SceneGenerationRequest(
        project_id=PROJECT_ID,
        scene_id=scene_id,
        sequence=sequence,
        visual_prompt="A cinematic visual for the Gemini Omni probe.",
        config=FlowGenerationConfig(
            model="local_asset", aspect_ratio="9:16", duration=duration,
            count=1, agent_enabled=False,
        ),
    )


# ---------------------------------------------------------------------------
# Test 1 — provider registration
# ---------------------------------------------------------------------------

def test_gemini_omni_is_valid_distinct_cost_bearing_provider():
    assert normalize_provider("gemini_omni") == "gemini_omni"
    assert GEMINI_OMNI_PROVIDER in VALID_PROVIDERS
    # Cost-bearing: deliberately NOT in the zero-cost set (no auto-sourcing).
    assert GEMINI_OMNI_PROVIDER not in ZERO_COST_PROVIDERS

    provider = create_visual_provider(
        "hybrid",
        scene_config={},
        scenes_output_dir=Path("."),
        provider_name="gemini_omni",
    )
    assert isinstance(provider, GeminiOmniVisualProvider)
    assert provider.name == "gemini_omni"
    assert provider.requires_browser is False
    assert provider.model == GEMINI_OMNI_DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Test 2 — missing configuration: no API call, truthful unavailability
# ---------------------------------------------------------------------------

def test_missing_configuration_makes_no_call_and_fails_truthfully(tmp_path, monkeypatch):
    monkeypatch.delenv("AVM_GEMINI_API_KEY", raising=False)

    create_calls = []

    class _ForbiddenInteractions:
        def create(self, **kwargs):
            create_calls.append(kwargs)
            raise AssertionError("network generation must not occur")

    provider = GeminiOmniVisualProvider(tmp_path / "out")
    assert provider.is_available() is False

    # Even with a live-looking client injected, the config gate fires first.
    provider._get_client = lambda: _FakeClient(_ForbiddenInteractions())
    result = provider.generate_scene(_make_request())

    assert result.success is False
    assert "not configured" in result.error
    assert "AVM_GEMINI_API_KEY" in result.error
    assert result.verification["provider"] == "gemini_omni"
    assert create_calls == []

    # SDK-unavailable surface is also truthful unavailability.
    def _raise_no_sdk():
        raise RuntimeError("the official google-genai SDK is not installed")

    provider2 = GeminiOmniVisualProvider(tmp_path / "out2", api_key="some-key")
    provider2._get_client = _raise_no_sdk
    assert provider2.is_available() is False
    result2 = provider2.generate_scene(_make_request())
    assert result2.success is False
    assert "unavailable" in result2.error


# ---------------------------------------------------------------------------
# Test 3 — successful inline video generation
# ---------------------------------------------------------------------------

def test_successful_inline_generation_satisfies_scene_contract(tmp_path):
    mp4_bytes = _make_fixture_mp4(tmp_path)
    interactions = _FakeInteractions(
        create_fn=lambda **kw: _inline_interaction(mp4_bytes, model=kw.get("model"))
    )
    provider = _make_provider(tmp_path, _FakeClient(interactions))

    result = provider.generate_scene(_make_request("scene_001", 1, duration=2))

    assert result.success is True
    assert result.status == "completed"

    # The submission: configured model, scene prompt, mapped aspect ratio.
    assert len(interactions.create_calls) == 1
    call = interactions.create_calls[0]
    assert call["model"] == GEMINI_OMNI_DEFAULT_MODEL
    assert call["input"] == "A cinematic visual for the Gemini Omni probe."
    assert call["response_format"]["type"] == "video"
    assert call["response_format"]["aspect_ratio"] == "9:16"

    # Phase 7A output contract.
    output = Path(tmp_path) / "scenes" / PROJECT_ID / "scene_001.mp4"
    assert output.is_file()
    assert result.file_path == output
    validation = validate_video_file(output, "9:16")
    assert validation.get("valid"), validation.get("error")
    assert validation["duration"] > 0
    assert validation["width"] * 16 == validation["height"] * 9

    # Truthful verification/checkpoint metadata (no secrets, no fake cost).
    assert result.verification["provider"] == "gemini_omni"
    assert result.verification["model"] == GEMINI_OMNI_DEFAULT_MODEL
    assert result.verification["interaction_id"] == "int_001"
    assert result.verification["output_delivery"] == "inline"
    assert result.verification["classification"] == "cost_bearing_text_to_video"
    assert result.generation_cost is None
    assert not (output.parent / "scene_001.mp4.part").exists()


# ---------------------------------------------------------------------------
# Test 4 — URI-delivered output: create → poll → ACTIVE → download
# ---------------------------------------------------------------------------

def test_uri_delivered_output_polls_to_active_then_downloads(tmp_path):
    mp4_bytes = _make_fixture_mp4(tmp_path)
    interactions = _FakeInteractions(
        create_fn=lambda **kw: _uri_interaction("https://files.example/vid1")
    )
    files = _FakeFiles(states=["PROCESSING", "PROCESSING", "ACTIVE"],
                       download_bytes=mp4_bytes)
    sleeps = []
    provider = _make_provider(tmp_path, _FakeClient(interactions, files))
    provider._sleep = lambda seconds: sleeps.append(seconds)

    result = provider.generate_scene(_make_request())

    assert result.success is True
    assert result.verification["output_delivery"] == "uri"
    assert len(files.download_calls) == 1
    assert files.download_calls[0] == "https://files.example/vid1"
    output = Path(tmp_path) / "scenes" / PROJECT_ID / "scene_001.mp4"
    assert validate_video_file(output, "9:16").get("valid")
    # No real sleeps occurred (injected no-op) but polling did happen.
    assert len(sleeps) == 2


# ---------------------------------------------------------------------------
# Test 5 — URI delivery reaching FAILED is a truthful failure
# ---------------------------------------------------------------------------

def test_uri_file_state_failed_is_truthful_failure(tmp_path):
    interactions = _FakeInteractions(
        create_fn=lambda **kw: _uri_interaction("https://files.example/vid2")
    )
    files = _FakeFiles(states=["PROCESSING", "FAILED"])
    provider = _make_provider(tmp_path, _FakeClient(interactions, files))

    result = provider.generate_scene(_make_request())

    assert result.success is False
    assert "FAILED" in result.error
    assert result.verification["provider"] == "gemini_omni"
    assert result.verification["interaction_id"] == "int_001"
    # No output materialized, no fake success, no fallback.
    output = Path(tmp_path) / "scenes" / PROJECT_ID / "scene_001.mp4"
    assert not output.exists()


# ---------------------------------------------------------------------------
# Test 6 — successful-looking response without video output fails
# ---------------------------------------------------------------------------

def test_response_without_video_output_fails_after_polling(tmp_path):
    interactions = _FakeInteractions(
        create_fn=lambda **kw: _ns(
            id="int_003", status="processing", model=None, errors=None,
            output_video=None,
        ),
        get_fn=lambda id: _ns(
            id=id, status="processing", model=None, errors=None,
            output_video=None,
        ),
    )
    provider = _make_provider(tmp_path, _FakeClient(interactions), poll_max_attempts=2)

    result = provider.generate_scene(_make_request())

    assert result.success is False
    assert "no video output" in result.error
    # Exactly ONE billable submission; only non-billable interaction polls after.
    assert len(interactions.create_calls) == 1
    assert len(interactions.get_calls) == 2
    assert not (Path(tmp_path) / "scenes" / PROJECT_ID / "scene_001.mp4").exists()


# ---------------------------------------------------------------------------
# Test 7 — invalid generated video fails validation (no false success)
# ---------------------------------------------------------------------------

def test_invalid_generated_video_fails_validation(tmp_path):
    interactions = _FakeInteractions(
        create_fn=lambda **kw: _inline_interaction(b"\x00" * 2048)
    )
    provider = _make_provider(tmp_path, _FakeClient(interactions))

    result = provider.generate_scene(_make_request())

    assert result.success is False
    assert "failed scene validation" in result.error
    assert result.verification["provider"] == "gemini_omni"
    # Nothing placed at the scene output location.
    assert not (Path(tmp_path) / "scenes" / PROJECT_ID / "scene_001.mp4").exists()
    assert not (Path(tmp_path) / "scenes" / PROJECT_ID / "scene_001.mp4.part").exists()


# ---------------------------------------------------------------------------
# Test 8 — pipeline-level integration through hybrid routing
# ---------------------------------------------------------------------------

def test_pipeline_routes_gemini_scene_to_valid_output(tmp_path, monkeypatch):
    import app.orchestrator.visual_providers as vp_module
    from app.orchestrator.visual_providers import GeminiOmniVisualProvider as _Cls

    mp4_bytes = _make_fixture_mp4(tmp_path)
    interactions = _FakeInteractions(
        create_fn=lambda **kw: _inline_interaction(mp4_bytes, model=kw.get("model"))
    )
    monkeypatch.setenv("AVM_GEMINI_API_KEY", "fake-test-key")
    monkeypatch.setattr(_Cls, "_get_client", lambda self: _FakeClient(interactions))
    monkeypatch.setattr(_Cls, "_sleep", staticmethod(lambda seconds: None))

    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch,
        mode="hybrid",
        routing={"scene_001": "gemini_omni", "scene_002": "image_motion"},
        explicit_assets={"scene_002": "image"},
    )
    orch._execute_scenes_stage()

    # The billable submission went out with the scene's intent.
    assert len(interactions.create_calls) == 1
    assert interactions.create_calls[0]["input"] == "Visual 1."

    output = Path(tmp_path) / "scenes" / PROJECT_ID / "scene_001.mp4"
    assert output.is_file()
    assert validate_video_file(output, "9:16").get("valid")

    checkpoint = orch.manifest.scene_checkpoints["scene_001"]
    assert checkpoint["provider"] == "gemini_omni"
    assert checkpoint["status"] == "completed"

    # scene_video_paths → provider-agnostic assembly (Phase 7A).
    assert str(output) in orch.manifest.scene_video_paths

    raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert raw["scene_checkpoints"]["scene_001"]["provider"] == "gemini_omni"


# ---------------------------------------------------------------------------
# Test 9 — completed valid scene reused; missing output = NEW generation
# ---------------------------------------------------------------------------

def test_completed_scene_reused_and_regeneration_is_new_submission(tmp_path, monkeypatch):
    import app.orchestrator.visual_providers as vp_module
    from app.orchestrator.visual_providers import GeminiOmniVisualProvider as _Cls

    mp4_bytes = _make_fixture_mp4(tmp_path)
    interactions = _FakeInteractions(
        create_fn=lambda **kw: _inline_interaction(
            mp4_bytes, interaction_id=f"int_{len(interactions.create_calls):03d}",
        )
    )
    monkeypatch.setenv("AVM_GEMINI_API_KEY", "fake-test-key")
    monkeypatch.setattr(_Cls, "_get_client", lambda self: _FakeClient(interactions))
    monkeypatch.setattr(_Cls, "_sleep", staticmethod(lambda seconds: None))

    orch, _pid = build_orchestrator(
        tmp_path, monkeypatch,
        mode="hybrid",
        routing={"scene_001": "gemini_omni", "scene_002": "image_motion"},
        explicit_assets={"scene_002": "image"},
    )
    orch._execute_scenes_stage()
    assert len(interactions.create_calls) == 1
    output = Path(tmp_path) / "scenes" / PROJECT_ID / "scene_001.mp4"
    first_mtime = output.stat().st_mtime_ns

    # Resume pass: completed + valid → reuse, submission count unchanged.
    orch._execute_scenes_stage()
    assert len(interactions.create_calls) == 1
    assert output.stat().st_mtime_ns == first_mtime

    # Missing output → regeneration through the normal generation path.
    # This is truthfully a NEW billable submission, not a remote resume.
    output.unlink()
    orch._execute_scenes_stage()
    assert len(interactions.create_calls) == 2
    assert output.is_file()
    assert orch.manifest.scene_checkpoints["scene_001"]["interaction_id"] == "int_002"


# ---------------------------------------------------------------------------
# Test 10 — polling/download failure never resubmits a billable generation
# ---------------------------------------------------------------------------

def test_polling_failure_never_resubmits_generation(tmp_path):
    interactions = _FakeInteractions(
        create_fn=lambda **kw: _uri_interaction("https://files.example/vid3")
    )
    files = _FakeFiles(states=["PROCESSING", "PROCESSING", "PROCESSING"])
    provider = _make_provider(tmp_path, _FakeClient(interactions, files), poll_max_attempts=2)

    result = provider.generate_scene(_make_request())

    assert result.success is False
    assert "did not become ACTIVE" in result.error
    # Exactly one billable submission despite the retrieval failure.
    assert len(interactions.create_calls) == 1
    assert not (Path(tmp_path) / "scenes" / PROJECT_ID / "scene_001.mp4").exists()

