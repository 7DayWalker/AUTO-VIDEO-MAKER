"""
Tests for app.flow scene generation module.

All tests run WITHOUT consuming Google Flow credits.
Tests mock Flow interactions where necessary.
"""

import pytest
import json
from pathlib import Path
from pydantic import ValidationError

from app.flow.scene_config import (
    FLOW_SCENE_MODEL,
    FLOW_SCENE_ASPECT_RATIO,
    FLOW_SCENE_DURATION,
    FLOW_SCENE_COUNT,
    FLOW_SCENE_MAX_PER_RUN,
    FLOW_SCENE_CREDIT_LIMIT,
    FLOW_SCENE_TIMEOUT,
    get_scene_config,
)
from app.flow.scene_models import (
    FlowGenerationConfig,
    SceneGenerationRequest,
    SceneGenerationResult,
    SceneMetadata,
    SceneManifest,
    SceneManifestEntry,
    FlowUIState,
    FlowHealthResult,
)
from app.flow.scene_generator import (
    FlowSceneGenerator,
    FlowSceneError,
    FlowConnectionError,
    FlowAuthenticationError,
    FlowGenerationError,
    FlowCreditError,
    validate_video_file,
)


class _FakeLocator:
    """Small sync-Playwright-compatible locator for production-path tests."""
    def __init__(self, text=""):
        self.text = text
        self.clicks = 0
        self.fills = []

    def count(self):
        return 1

    def nth(self, index):
        return self

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    def wait_for(self, state=None, timeout=None):
        return None

    def is_visible(self, timeout=None):
        return True

    def is_editable(self, timeout=None):
        return True

    def get_attribute(self, name):
        return None

    def click(self):
        self.clicks += 1

    def fill(self, value):
        self.fills.append(value)
        self.text = value

    def inner_text(self):
        return self.text


class _FakePage:
    def __init__(self):
        self.url = "https://labs.google/fx/tools/flow/project/test"
        self.locators = {}
        self.create_clicked = 0

    def title(self):
        return "Fake Flow Project"

    def locator(self, selector):
        if selector not in self.locators:
            self.locators[selector] = _FakeLocator()
        return self.locators[selector]

    def wait_for_timeout(self, value):
        return None

    def screenshot(self, path):
        Path(path).write_bytes(b"fake screenshot")


class _HealthPage(_FakePage):
    def __init__(self, html, url="https://labs.google/fx/tools/flow/project/test"):
        super().__init__()
        self.html = html
        self.url = url
        self.reloads = 0

    def content(self):
        return self.html

    def reload(self, **kwargs):
        self.reloads += 1


def _healthy_html():
    return (
        '<button>Veo 3.1 - Lite</button><button>9:16</button>'
        '<span>4s</span><button>Create</button>'
        '<div role="textbox" contenteditable="true"></div>'
    )


def _configure_health_page(page, html=None):
    """Configure only selectors represented by the synthetic HTML."""
    page.html = html if html is not None else page.html

    def locator(selector):
        if selector == '[role="textbox"][contenteditable="true"]':
            return _FakeLocator()
        if selector == 'button:has-text("Create")':
            return _FakeLocator()
        return type("EmptyLocator", (), {
            "count": lambda self: 0,
            "first": property(lambda self: self),
        })()

    page.locator = locator


def _forensic_payload():
    path = Path("logs/flow_diagnostic/forensic_project_initial_data_20260821_092828.json")
    if not path.exists():
        pytest.skip("forensic Flow fixture is not present")
    return json.loads(path.read_text(encoding="utf-8"))["endpoint_json"]


def test_flow_health_classifies_healthy_page_without_clicking(tmp_path, monkeypatch):
    page = _HealthPage(_healthy_html())
    _configure_health_page(page)
    generator = FlowSceneGenerator(screenshots_dir=tmp_path)
    generator.page = page
    generator.browser = object()
    generator.playwright = object()
    monkeypatch.setattr("app.flow.scene_generator.cdp_endpoint_diagnostics", lambda: {"available": True})

    result = generator.inspect_flow_health(page.url)

    assert isinstance(result, FlowHealthResult)
    assert result.classification == "GENERATION_SURFACE_READY"
    assert result.healthy is True
    assert result.create_button["found"] is True
    assert not any(locator.clicks for locator in page.locators.values())


def test_flow_health_does_not_promote_unusable_page_text_to_blocking_error(tmp_path, monkeypatch):
    page = _HealthPage(
        "Application error: a client-side exception has occurred",
    )
    _configure_health_page(page)
    generator = FlowSceneGenerator(screenshots_dir=tmp_path)
    generator.page = page
    generator.browser = object()
    generator.playwright = object()
    monkeypatch.setattr("app.flow.scene_generator.cdp_endpoint_diagnostics", lambda: {"available": True})

    result = generator.inspect_flow_health(page.url)

    assert result.classification == "GENERATION_SURFACE_READY"
    assert result.blocking_error_elements == []


def test_loading_health_poll_rechecks_page(tmp_path, monkeypatch):
    page = _HealthPage("Loading...")
    def loading_locator(selector):
        return type("EmptyLocator", (), {
            "count": lambda self: 0,
            "first": property(lambda self: self),
        })()
    page.locator = loading_locator
    generator = FlowSceneGenerator(screenshots_dir=tmp_path)
    generator.page = page
    generator.browser = object()
    generator.playwright = object()
    monkeypatch.setattr("app.flow.scene_generator.cdp_endpoint_diagnostics", lambda: {"available": True})
    monkeypatch.setattr("app.flow.scene_generator.HEALTH_TIMEOUT", 1)
    monkeypatch.setattr("app.flow.scene_generator.HEALTH_POLL_INTERVAL", 0)

    calls = {"count": 0}
    original = generator.inspect_flow_health

    def eventually_healthy(expected_url=None):
        calls["count"] += 1
        if calls["count"] == 2:
            page.html = _healthy_html()
            _configure_health_page(page)
        return original(expected_url)

    generator.inspect_flow_health = eventually_healthy
    result = generator._poll_for_healthy(page.url)

    assert result.classification == "GENERATION_SURFACE_READY"
    assert calls["count"] >= 2


def test_unhealthy_health_recovery_is_bounded(tmp_path, monkeypatch):
    page = _HealthPage("Something went wrong")
    page.locator = lambda selector: type("EmptyLocator", (), {
        "count": lambda self: 0,
        "first": property(lambda self: self),
    })()
    generator = FlowSceneGenerator(screenshots_dir=tmp_path)
    generator.page = page
    generator.browser = object()
    generator.playwright = object()
    monkeypatch.setattr("app.flow.scene_generator.cdp_endpoint_diagnostics", lambda: {"available": True})
    monkeypatch.setattr("app.flow.scene_generator.MAX_REFRESH_ATTEMPTS", 1)
    monkeypatch.setattr("app.flow.scene_generator.MAX_RESTART_ATTEMPTS", 0)

    result = generator.ensure_flow_healthy(page.url)

    assert result.classification == "GENERATION_SURFACE_UNAVAILABLE"
    assert page.reloads == 1
    assert generator._refresh_attempts == 1


def _control_ready(kind="prompt"):
    return {
        "found": True, "attached": True, "visible": True,
        "editable": kind == "prompt", "enabled": kind == "create",
        "stable": True, "in_viewport": True,
        "pointer_interactable": True, "unobstructed": True,
    }


def _classify(error_elements=None, prompt=None, create=None):
    return FlowSceneGenerator._classify_health_evidence(
        expected_url="https://labs.google/flow/project/1",
        actual_url="https://labs.google/flow/project/1",
        cdp_available=True,
        browser_connected=True,
        loading=[],
        prompt_state=prompt or _control_ready("prompt"),
        create_state=create or _control_ready("create"),
        error_elements=error_elements or [],
        page_available=True,
    )


def test_health_hidden_serialized_and_aggregate_errors_are_ready():
    result = _classify([
        {"term": "network error", "context_classification": "BACKGROUND_ERROR"},
        {"term": "something went wrong", "context_classification": "STALE_ERROR_ELEMENT"},
    ])
    assert result["classification"] == "GENERATION_SURFACE_READY"
    assert result["blocking_error_elements"] == []
    assert result["detected_error_text"] == ["network error", "something went wrong"]


def test_health_blocking_overlay_is_fatal():
    result = _classify([{
        "term": "something went wrong",
        "context_classification": "BLOCKING_APPLICATION_ERROR",
        "intersects_controls": True,
        "modal_ancestor": {"role": "dialog"},
    }])
    assert result["classification"] == "BLOCKING_APPLICATION_ERROR"
    assert result["overlay_state"]["blocking"] is True


def test_health_prompt_unavailable():
    prompt = _control_ready("prompt")
    prompt["visible"] = False
    prompt["stable"] = False
    result = _classify(prompt=prompt)
    assert result["classification"] == "GENERATION_SURFACE_UNAVAILABLE"


def test_health_create_disabled():
    create = _control_ready("create")
    create["enabled"] = False
    create["stable"] = False
    result = _classify(create=create)
    assert result["classification"] == "GENERATION_SURFACE_UNAVAILABLE"


def test_health_non_blocking_visible_message_keeps_surface_ready():
    result = _classify([{
        "term": "something went wrong",
        "context_classification": "NON_BLOCKING_ERROR_MESSAGE",
        "intersects_controls": False,
    }])
    assert result["classification"] == "GENERATION_SURFACE_READY"
    assert result["error_classification"] == "NON_BLOCKING_ERROR_MESSAGE"


def test_health_stale_url_has_priority():
    result = FlowSceneGenerator._classify_health_evidence(
        expected_url="https://labs.google/flow/project/expected",
        actual_url="https://labs.google/flow/project/other",
        cdp_available=True, browser_connected=True, loading=[],
        prompt_state=_control_ready("prompt"), create_state=_control_ready("create"),
        error_elements=[], page_available=True,
    )
    assert result["classification"] == "STALE"


def test_forensic_project_payload_exposes_durable_media_identity():
    """The saved GET fixture supplies IDs and the scoped redirect URL."""
    snapshot = FlowSceneGenerator._parse_project_snapshot(_forensic_payload())
    assert snapshot["project_id"] == "4236a954-2c94-4942-b298-fe9ffc74b950"
    assert snapshot["media_ids"]
    first = snapshot["media"][0]
    assert first["workflow_id"]
    assert first["operation_name"] == first["media_id"]
    assert first["media_blob_size"] > 0
    assert first["redirect_url"].endswith(f"name={first['media_id']}")
    assert first["prompt_values"]


def test_correlator_excludes_baseline_and_requires_exact_prompt():
    """A visible old asset or unmatched new asset is never selected."""
    payload = _forensic_payload()
    snapshot = FlowSceneGenerator._parse_project_snapshot(payload)
    generator = FlowSceneGenerator()
    generator._baseline_snapshot = {"media_ids": [snapshot["media_ids"][0]]}
    prompt = snapshot["media"][1]["media_title"]
    generator._submission_context = {"prompt": prompt, "submitted_at": "2026-08-21T00:00:00Z"}

    result = generator._correlate_submission(snapshot)

    assert result["correlation"] == "exact_prompt"
    assert result["selected"]["media_id"] != snapshot["media_ids"][0]
    assert result["selected"]["media_id"] == snapshot["media_ids"][1]


def test_correlator_returns_unresolved_for_unrelated_new_asset():
    generator = FlowSceneGenerator()
    generator._baseline_snapshot = {"media_ids": []}
    generator._submission_context = {"prompt": "the exact requested asset"}
    snapshot = {"media": [{
        "media_id": "unrelated-media", "prompt_values": ["another prompt"],
        "media_blob_size": 999, "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
    }]}
    result = generator._correlate_submission(snapshot)
    assert result["selected"] is None
    assert result["correlation"] == "unresolved"


def test_global_failure_text_is_background_only(tmp_path):
    """Page text alone cannot terminate a submitted generation."""
    generator = FlowSceneGenerator(screenshots_dir=tmp_path)
    generator.page = _HealthPage("generation failed please try again")
    generator._baseline_snapshot = {"media_ids": []}
    generator._submission_context = {"prompt": "requested"}
    generator._capture_project_snapshot = lambda: {"media": [], "media_ids": []}
    evidence = generator._generation_evidence()
    assert evidence["authoritative_failure"] is False
    assert evidence["authoritative_success"] is False
    assert evidence["background"]["page_error_terms"]


def test_polling_state_outage_is_unknown_not_timeout(tmp_path, monkeypatch):
    """Repeated GET-only state loss is an explicit reconciliation outcome."""
    generator = FlowSceneGenerator(screenshots_dir=tmp_path)
    generator.page = _FakePage()
    generator._capture_project_snapshot = lambda: {
        "media": [], "media_ids": [], "error": "fixture GET unavailable"
    }
    monkeypatch.setattr("app.flow.scene_generator.FLOW_SCENE_TIMEOUT", 1)
    monkeypatch.setattr("app.flow.scene_generator.FLOW_SCENE_POLL_INTERVAL", 0)
    monkeypatch.setattr("app.flow.scene_generator.FLOW_SCENE_MAX_POLL_RECOVERY_ATTEMPTS", 1)
    monkeypatch.setattr("app.flow.scene_generator.take_screenshot", lambda *args, **kwargs: None)

    completed, error = generator._wait_for_completion()

    assert completed is False
    assert "UNKNOWN_RECONCILIATION_REQUIRED" in error
    assert "TIMEOUT" not in error


def test_submission_checkpoint_has_identity_and_prepared_is_not_submitted(tmp_path):
    """Prepared intent remains zero-credit until the mocked click returns."""
    generator = FlowSceneGenerator(screenshots_dir=tmp_path)
    generator.page = _FakePage()
    generator._submission_id = "attempt-123"
    generator._baseline_snapshot = {"project_id": "project", "media_ids": ["old-media"]}
    generator._assets_before_generation = ["old-media"]
    checkpoints = []
    assert generator._submit_generation(checkpoints.append) is True
    assert checkpoints[0]["state"] == "PREPARED"
    assert checkpoints[0]["submission_id"] == "attempt-123"
    assert checkpoints[1]["state"] == "SUBMITTED"
    assert checkpoints[1]["submission_id"] == "attempt-123"
    assert checkpoints[1]["assets_before"] == ["old-media"]


def test_production_prompt_path_reacquires_and_reads_back(tmp_path):
    """Verification and entry use fresh candidates and never submit."""
    page = _FakePage()
    generator = FlowSceneGenerator(screenshots_dir=tmp_path)
    generator.page = page
    generator.ui_state.connected = True

    # Simulate Flow replacing the editor between verification and entry by
    # returning a new locator object for the same selector each time.
    original_locator = page.locator
    calls = {"textbox": 0}

    def rerendering_locator(selector):
        if selector == '[role="textbox"][contenteditable="true"]':
            calls["textbox"] += 1
            locator = _FakeLocator()
            page.locators[f"{selector}:{calls['textbox']}"] = locator
            return locator
        return original_locator(selector)

    page.locator = rerendering_locator
    assert generator._verify_prompt_input() is True
    assert generator._enter_prompt("A verified prompt") is True
    assert calls["textbox"] >= 2
    assert generator._last_prompt_diagnostics["selected"]["stable"] is True
    assert not any(locator.clicks for locator in page.locators.values() if hasattr(locator, "clicks") and locator.clicks > 1)


def test_ui_only_verification_finds_create_without_clicking(tmp_path):
    """The UI-only generator path records Create discovery but never clicks."""
    page = _FakePage()
    generator = FlowSceneGenerator(screenshots_dir=tmp_path)
    generator.page = page
    generator.ui_state.connected = True
    generator.ui_state.prompt_ready = True
    generator.verify_settings = lambda: generator.ui_state
    generator._verify_prompt_input = lambda: True

    result = generator.verify_ui_only(
        SceneGenerationRequest(
            project_id="test",
            scene_id="scene_01",
            sequence=1,
            visual_prompt="A verified prompt",
        )
    )

    assert result.success is True
    assert result.status == "dry_run"
    assert result.verification["prompt_entered"] is True
    assert result.verification["prompt_read_back"] is True
    assert result.verification["create_button_found"] is True
    assert result.verification["create_clicked"] is False
    assert result.verification["credits_consumed"] == 0


class _SnapshotPage(_FakePage):
    def __init__(self, response, url="https://labs.google/fx/tools/flow/project/4236a954-2c94-4942-b298-fe9ffc74b950"):
        super().__init__()
        self.url = url
        self.response = response
        self.evaluate_calls = []

    def evaluate(self, script, *args):
        self.evaluate_calls.append((script, args))
        return self.response


def _snapshot_generator(page, hint=None):
    generator = FlowSceneGenerator()
    generator.page = page
    generator._expected_project_url = page.url
    generator._resume_asset_hint = hint
    generator._capture_dom_tiles = lambda: []
    return generator


def test_project_snapshot_get_retrieval_preserves_request_and_response_diagnostics():
    payload = {
        "result": {"data": {"json": {
            "projectId": "4236a954-2c94-4942-b298-fe9ffc74b950",
            "projectContents": {"workflows": [], "media": []},
        }}}
    }
    page = _SnapshotPage({
        "status": 200, "ok": True, "body": payload,
        "body_text": json.dumps(payload), "content_type": "application/json",
    })
    generator = _snapshot_generator(page)

    snapshot = generator._capture_project_snapshot()

    assert snapshot["endpoint_status"] == 200
    assert snapshot["project_id"] == "4236a954-2c94-4942-b298-fe9ffc74b950"
    assert snapshot["endpoint_diagnostics"]["method"] == "GET"
    assert snapshot["endpoint_diagnostics"]["project_id"] == snapshot["project_id"]
    assert snapshot["endpoint_diagnostics"]["response_body"] == payload
    assert page.evaluate_calls[0][1][0] == snapshot["endpoint"]


def test_project_snapshot_malformed_success_is_safe_and_not_authoritative():
    page = _SnapshotPage({
        "status": 200, "ok": True, "body": {"unexpected": "shape"},
        "body_text": '{"unexpected":"shape"}', "content_type": "application/json",
    })
    generator = _snapshot_generator(page)

    snapshot = generator._capture_project_snapshot()

    assert snapshot["media"] == []
    assert snapshot["media_ids"] == []
    assert snapshot["project_id"] is None
    assert "error" not in snapshot


def test_project_snapshot_http_400_keeps_useful_diagnostics():
    page = _SnapshotPage({
        "status": 400, "ok": False, "body": {"error": "invalid project"},
        "body_text": '{"error":"invalid project"}',
        "content_type": "application/json",
    })
    generator = _snapshot_generator(page)

    snapshot = generator._capture_project_snapshot()

    assert snapshot["endpoint_status"] == 400
    assert snapshot["endpoint_diagnostics"]["status"] == 400
    assert snapshot["endpoint_diagnostics"]["response_text"] == '{"error":"invalid project"}'
    assert "projectInitialData HTTP 400" in snapshot["error"]
    assert "invalid project" in snapshot["error"]
    assert snapshot["endpoint_diagnostics"]["project_id"] == "4236a954-2c94-4942-b298-fe9ffc74b950"


def test_project_snapshot_http_400_uses_only_exact_persisted_media_hint():
    media_id = "baf6af99-1110-485d-87f0-25210f101ec8"
    hint = {
        "media_id": media_id,
        "project_id": "4236a954-2c94-4942-b298-fe9ffc74b950",
        "workflow_id": "workflow-exact",
        "prompt_values": ["requested prompt"],
        "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
        "media_blob_size": 2243264,
        "redirect_url": f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={media_id}",
    }
    page = _SnapshotPage({
        "status": 400, "ok": False, "body": {"error": "temporarily unavailable"},
        "body_text": '{"error":"temporarily unavailable"}',
        "content_type": "application/json",
    })
    generator = _snapshot_generator(page, hint)
    generator._capture_dom_tiles = lambda: [{
        "index": 99, "src": hint["redirect_url"], "workflow_id": "unrelated-workflow"
    }]

    snapshot = generator._capture_project_snapshot()

    assert snapshot["source"] == "flow.dom_resume_hint"
    assert snapshot["media_ids"] == [media_id]
    assert snapshot["reconciliation"] == "exact_persisted_media_id_visible"
    assert snapshot["endpoint_diagnostics"]["response_text"] == '{"error":"temporarily unavailable"}'


def test_project_snapshot_http_400_without_exact_identity_remains_unresolved():
    page = _SnapshotPage({
        "status": 400, "ok": False, "body": {"error": "bad request"},
        "body_text": '{"error":"bad request"}', "content_type": "application/json",
    })
    generator = _snapshot_generator(page, {
        "media_id": "persisted-media",
        "project_id": "4236a954-2c94-4942-b298-fe9ffc74b950",
        "workflow_id": "persisted-workflow",
    })
    generator._capture_dom_tiles = lambda: [{
        "index": 0, "src": "https://labs.google/media/other-media",
        "workflow_id": "other-workflow"
    }]

    snapshot = generator._capture_project_snapshot()

    assert snapshot["media"] == []
    assert snapshot["media_ids"] == []
    assert "UNKNOWN" not in snapshot["error"]
    assert "HTTP 400" in snapshot["error"]


def test_resume_restores_exact_hint_and_never_enters_create_path(tmp_path, monkeypatch):
    page = _FakePage()
    generator = FlowSceneGenerator(output_dir=tmp_path, screenshots_dir=tmp_path)
    generator.page = page
    generator._expected_project_url = page.url
    called = {"wait": 0, "submit": 0}

    def no_generation(*args, **kwargs):
        called["submit"] += 1
        raise AssertionError("resume must not call submission")

    def unresolved(*args, **kwargs):
        called["wait"] += 1
        assert generator._resume_asset_hint["media_id"] == "exact-media"
        return False, "UNKNOWN_RECONCILIATION_REQUIRED: unavailable"

    generator._submit_generation = no_generation
    generator._wait_for_completion = unresolved
    request = SceneGenerationRequest(
        project_id="project",
        scene_id="scene_01",
        sequence=1,
        visual_prompt="requested prompt",
        flow_project_url=page.url,
    )
    checkpoint = {
        "status": "unknown_reconciliation_required",
        "flow_project_url": page.url,
        "prompt": "requested prompt",
        "checkpoint_evidence": {
            "submission": {
                "state": "SUBMITTED",
                "submission_id": "submission-1",
                "baseline_snapshot": {"media_ids": ["old-media"], "media": []},
                "assets_before": ["old-media"],
            },
            "polling": {"selected_asset": {
                "media_id": "exact-media",
                "project_id": "project",
                "prompt_values": ["requested prompt"],
            }},
        },
    }

    result = generator.resume_submitted_scene(request, checkpoint)

    assert result.success is False
    assert result.status == "unknown_reconciliation_required"
    assert called == {"wait": 1, "submit": 0}


# ===== Config Tests =====

class TestSceneConfig:
    """Tests for scene generation configuration."""
    
    def test_default_model(self):
        """Default model should be Veo 3.1 - Lite."""
        assert FLOW_SCENE_MODEL == "Veo 3.1 - Lite"
    
    def test_default_aspect_ratio(self):
        """Default aspect ratio should be 9:16."""
        assert FLOW_SCENE_ASPECT_RATIO == "9:16"
    
    def test_default_duration(self):
        """Default duration should be 4 seconds."""
        assert FLOW_SCENE_DURATION == 4
    
    def test_default_count(self):
        """Default count should be 1."""
        assert FLOW_SCENE_COUNT == 1
    
    def test_default_max_per_run(self):
        """Default max scenes per run should be 1."""
        assert FLOW_SCENE_MAX_PER_RUN == 1
    
    def test_default_credit_limit(self):
        """Default credit limit should be 10."""
        assert FLOW_SCENE_CREDIT_LIMIT == 10
    
    def test_default_timeout(self):
        """Default timeout should be 600 seconds."""
        assert FLOW_SCENE_TIMEOUT == 600
    
    def test_get_scene_config(self):
        """get_scene_config should return all settings."""
        config = get_scene_config()
        assert "model" in config
        assert "aspect_ratio" in config
        assert "duration" in config
        assert "count" in config
        assert "credit_limit" in config


# ===== FlowGenerationConfig Tests =====

class TestFlowGenerationConfig:
    """Tests for FlowGenerationConfig model."""
    
    def test_default_config(self):
        """Should create config with defaults."""
        config = FlowGenerationConfig()
        assert config.model == FLOW_SCENE_MODEL
        assert config.aspect_ratio == FLOW_SCENE_ASPECT_RATIO
        assert config.duration == FLOW_SCENE_DURATION
        assert config.count == FLOW_SCENE_COUNT
        assert config.agent_enabled is False
    
    def test_custom_config(self):
        """Should accept custom values."""
        config = FlowGenerationConfig(
            model="Custom Model",
            aspect_ratio="16:9",
            duration=8,
            count=2,
        )
        assert config.model == "Custom Model"
        assert config.aspect_ratio == "16:9"
        assert config.duration == 8
        assert config.count == 2
    
    def test_invalid_aspect_ratio(self):
        """Should reject invalid aspect ratio."""
        with pytest.raises(ValidationError):
            FlowGenerationConfig(aspect_ratio="4:3")
    
    def test_valid_aspect_ratios(self):
        """Should accept valid aspect ratios."""
        for ratio in ["9:16", "16:9", "1:1"]:
            config = FlowGenerationConfig(aspect_ratio=ratio)
            assert config.aspect_ratio == ratio
    
    def test_duration_bounds(self):
        """Should enforce duration bounds."""
        with pytest.raises(ValidationError):
            FlowGenerationConfig(duration=0)
        
        with pytest.raises(ValidationError):
            FlowGenerationConfig(duration=100)
    
    def test_count_bounds(self):
        """Should enforce count bounds."""
        with pytest.raises(ValidationError):
            FlowGenerationConfig(count=0)
        
        with pytest.raises(ValidationError):
            FlowGenerationConfig(count=10)


# ===== SceneGenerationRequest Tests =====

class TestSceneGenerationRequest:
    """Tests for SceneGenerationRequest model."""
    
    def test_valid_request(self):
        """Should create valid request."""
        request = SceneGenerationRequest(
            project_id="test_project",
            scene_id="scene_01",
            sequence=1,
            visual_prompt="A beautiful sunset over the ocean",
        )
        assert request.project_id == "test_project"
        assert request.scene_id == "scene_01"
        assert request.sequence == 1
    
    def test_empty_project_id_rejected(self):
        """Should reject empty project ID."""
        with pytest.raises(ValidationError):
            SceneGenerationRequest(
                project_id="",
                scene_id="scene_01",
                sequence=1,
                visual_prompt="Test prompt",
            )
    
    def test_empty_scene_id_rejected(self):
        """Should reject empty scene ID."""
        with pytest.raises(ValidationError):
            SceneGenerationRequest(
                project_id="test",
                scene_id="",
                sequence=1,
                visual_prompt="Test prompt",
            )
    
    def test_empty_prompt_rejected(self):
        """Should reject empty visual prompt."""
        with pytest.raises(ValidationError):
            SceneGenerationRequest(
                project_id="test",
                scene_id="scene_01",
                sequence=1,
                visual_prompt="",
            )
    
    def test_whitespace_prompt_rejected(self):
        """Should reject whitespace-only prompt."""
        with pytest.raises(ValidationError):
            SceneGenerationRequest(
                project_id="test",
                scene_id="scene_01",
                sequence=1,
                visual_prompt="   ",
            )
    
    def test_sequence_must_be_positive(self):
        """Should reject non-positive sequence."""
        with pytest.raises(ValidationError):
            SceneGenerationRequest(
                project_id="test",
                scene_id="scene_01",
                sequence=0,
                visual_prompt="Test prompt",
            )


# ===== SceneGenerationResult Tests =====

class TestSceneGenerationResult:
    """Tests for SceneGenerationResult model."""
    
    def test_success_result(self):
        """Should create success result."""
        result = SceneGenerationResult.success_result(
            scene_id="scene_01",
            sequence=1,
            file_path=Path("/test/scene_01.mp4"),
            file_size=1000000,
            actual_duration=4.1,
            width=1080,
            height=1920,
            codec="h264",
        )
        assert result.success is True
        assert result.status == "completed"
        assert result.scene_id == "scene_01"
    
    def test_failure_result(self):
        """Should create failure result."""
        result = SceneGenerationResult.failure(
            scene_id="scene_01",
            sequence=1,
            error="Test error",
        )
        assert result.success is False
        assert result.status == "failed"
        assert result.error == "Test error"
    
    def test_dry_run_result(self):
        """Should create dry-run result."""
        result = SceneGenerationResult.dry_run(
            scene_id="scene_01",
            sequence=1,
            generation_cost=10,
        )
        assert result.success is True
        assert result.status == "dry_run"
        assert result.generation_cost == 10
    
    def test_skipped_result(self):
        """Should create skipped result."""
        result = SceneGenerationResult.skipped(
            scene_id="scene_01",
            sequence=1,
            reason="Already exists",
        )
        assert result.success is True
        assert result.status == "skipped"


# ===== SceneMetadata Tests =====

class TestSceneMetadata:
    """Tests for SceneMetadata model."""
    
    def test_valid_metadata(self):
        """Should create valid metadata."""
        metadata = SceneMetadata(
            project_id="test_project",
            scene_id="scene_01",
            sequence=1,
            source_prompt="Test prompt",
            model="Veo 3.1 - Lite",
            aspect_ratio="9:16",
            duration_requested=4,
            output_count=1,
            file_path="/test/scene_01.mp4",
            file_size=1000000,
            actual_duration=4.1,
            width=1080,
            height=1920,
            codec="h264",
        )
        assert metadata.project_id == "test_project"
        assert metadata.scene_id == "scene_01"
    
    def test_to_dict(self):
        """Should convert to dictionary."""
        metadata = SceneMetadata(
            project_id="test",
            scene_id="scene_01",
            sequence=1,
            source_prompt="Test",
            model="Veo",
            aspect_ratio="9:16",
            duration_requested=4,
            output_count=1,
            file_path="/test.mp4",
            file_size=1000,
            actual_duration=4.0,
            width=1080,
            height=1920,
            codec="h264",
        )
        data = metadata.to_dict()
        assert "project_id" in data
        assert "scene_id" in data
        assert "source_prompt" in data


# ===== SceneManifest Tests =====

class TestSceneManifest:
    """Tests for SceneManifest model."""
    
    def test_create_manifest(self):
        """Should create empty manifest."""
        manifest = SceneManifest(project_id="test_project")
        assert manifest.project_id == "test_project"
        assert len(manifest.scenes) == 0
    
    def test_add_scene(self):
        """Should add scene to manifest."""
        manifest = SceneManifest(project_id="test")
        manifest.add_scene(
            scene_id="scene_01",
            sequence=1,
            file="scene_01.mp4",
            status="completed",
        )
        assert len(manifest.scenes) == 1
        assert manifest.scenes[0].scene_id == "scene_01"
    
    def test_add_scene_updates_existing(self):
        """Should update existing scene."""
        manifest = SceneManifest(project_id="test")
        manifest.add_scene("scene_01", 1, "scene_01.mp4", "pending")
        manifest.add_scene("scene_01", 1, "scene_01.mp4", "completed")
        
        assert len(manifest.scenes) == 1
        assert manifest.scenes[0].status == "completed"
    
    def test_scenes_sorted_by_sequence(self):
        """Should sort scenes by sequence."""
        manifest = SceneManifest(project_id="test")
        manifest.add_scene("scene_03", 3, "scene_03.mp4", "completed")
        manifest.add_scene("scene_01", 1, "scene_01.mp4", "completed")
        manifest.add_scene("scene_02", 2, "scene_02.mp4", "completed")
        
        assert manifest.scenes[0].sequence == 1
        assert manifest.scenes[1].sequence == 2
        assert manifest.scenes[2].sequence == 3
    
    def test_get_scene(self):
        """Should get scene by ID."""
        manifest = SceneManifest(project_id="test")
        manifest.add_scene("scene_01", 1, "scene_01.mp4", "completed")
        
        scene = manifest.get_scene("scene_01")
        assert scene is not None
        assert scene.scene_id == "scene_01"
        
        missing = manifest.get_scene("scene_99")
        assert missing is None
    
    def test_to_dict(self):
        """Should convert to dictionary."""
        manifest = SceneManifest(project_id="test")
        manifest.add_scene("scene_01", 1, "scene_01.mp4", "completed")
        
        data = manifest.to_dict()
        assert "project_id" in data
        assert "scenes" in data
        assert len(data["scenes"]) == 1


# ===== FlowUIState Tests =====

class TestFlowUIState:
    """Tests for FlowUIState model."""
    
    def test_default_state(self):
        """Should create default state."""
        state = FlowUIState()
        assert state.connected is False
        assert state.authenticated is False
        assert state.ready_for_generation is False
    
    def test_ready_for_generation(self):
        """Should be ready when all prerequisites met."""
        state = FlowUIState(
            connected=True,
            authenticated=True,
            on_project_page=True,
            agent_off=True,
            video_mode=True,
            model_verified=True,
            aspect_ratio_verified=True,
            duration_verified=True,
            count_verified=True,
            prompt_ready=True,
        )
        assert state.ready_for_generation is True
    
    def test_not_ready_if_missing_prerequisite(self):
        """Should not be ready if any prerequisite missing."""
        state = FlowUIState(
            connected=True,
            authenticated=True,
            on_project_page=True,
            agent_off=True,
            video_mode=True,
            model_verified=True,
            aspect_ratio_verified=True,
            duration_verified=True,
            count_verified=True,
            prompt_ready=False,  # Missing
        )
        assert state.ready_for_generation is False
    
    def test_get_missing_prerequisites(self):
        """Should list missing prerequisites."""
        state = FlowUIState(
            connected=True,
            authenticated=False,
            on_project_page=True,
        )
        missing = state.get_missing_prerequisites()
        assert "Google authentication" in missing
        assert "Chrome/CDP connection" not in missing


# ===== Exception Tests =====

class TestExceptions:
    """Tests for scene generation exceptions."""
    
    def test_flow_scene_error(self):
        """FlowSceneError should be raisable."""
        with pytest.raises(FlowSceneError):
            raise FlowSceneError("Test error")
    
    def test_flow_connection_error(self):
        """FlowConnectionError should be FlowSceneError."""
        error = FlowConnectionError("Connection failed")
        assert isinstance(error, FlowSceneError)
    
    def test_flow_authentication_error(self):
        """FlowAuthenticationError should be FlowSceneError."""
        error = FlowAuthenticationError("Auth required")
        assert isinstance(error, FlowSceneError)
    
    def test_flow_generation_error(self):
        """FlowGenerationError should be FlowSceneError."""
        error = FlowGenerationError("Generation failed")
        assert isinstance(error, FlowSceneError)
    
    def test_flow_credit_error(self):
        """FlowCreditError should be FlowSceneError."""
        error = FlowCreditError("Credit limit exceeded")
        assert isinstance(error, FlowSceneError)


# ===== Credit Safety Tests =====

class TestCreditSafety:
    """Tests for credit safety mechanisms."""
    
    def test_credit_limit_default(self):
        """Default credit limit should be reasonable."""
        assert FLOW_SCENE_CREDIT_LIMIT > 0
        assert FLOW_SCENE_CREDIT_LIMIT <= 50
    
    def test_max_scenes_default(self):
        """Default max scenes should be 1 for safety."""
        assert FLOW_SCENE_MAX_PER_RUN == 1
    
    def test_generation_cost_in_result(self):
        """Result should include generation cost."""
        result = SceneGenerationResult.dry_run(
            scene_id="scene_01",
            sequence=1,
            generation_cost=10,
        )
        assert result.generation_cost == 10


# ===== Dry Run Tests =====

class TestDryRun:
    """Tests for dry-run functionality."""
    
    def test_dry_run_result_status(self):
        """Dry run should have correct status."""
        result = SceneGenerationResult.dry_run(
            scene_id="scene_01",
            sequence=1,
        )
        assert result.status == "dry_run"
        assert result.success is True
    
    def test_dry_run_no_file_path(self):
        """Dry run should not have file path."""
        result = SceneGenerationResult.dry_run(
            scene_id="scene_01",
            sequence=1,
        )
        assert result.file_path is None


# ===== Scene Selection Tests =====

class TestSceneSelection:
    """Tests for scene selection logic."""
    
    def test_valid_scene_id(self):
        """Should accept valid scene ID format."""
        request = SceneGenerationRequest(
            project_id="test",
            scene_id="scene_01",
            sequence=1,
            visual_prompt="Test",
        )
        assert request.scene_id == "scene_01"
    
    def test_sequence_ordering(self):
        """Manifest should maintain sequence order."""
        manifest = SceneManifest(project_id="test")
        manifest.add_scene("scene_02", 2, "scene_02.mp4", "completed")
        manifest.add_scene("scene_01", 1, "scene_01.mp4", "completed")
        
        assert manifest.scenes[0].sequence < manifest.scenes[1].sequence


# ===== Video Validation Tests =====

class TestVideoValidation:
    """Tests for video validation."""
    
    def test_nonexistent_file(self):
        """Should report nonexistent file."""
        result = validate_video_file(Path("/nonexistent/file.mp4"))
        assert result["valid"] is False
        assert "not exist" in result["error"].lower()
    
    def test_validation_returns_dict(self):
        """Validation should return dictionary."""
        result = validate_video_file(Path("/test.mp4"))
        assert isinstance(result, dict)
        assert "valid" in result


# ===== Manifest Persistence Tests =====

class TestManifestPersistence:
    """Tests for manifest save/load."""
    
    def test_manifest_to_dict_structure(self):
        """Manifest dict should have correct structure."""
        manifest = SceneManifest(project_id="test")
        manifest.add_scene("scene_01", 1, "scene_01.mp4", "completed")
        
        data = manifest.to_dict()
        
        assert "project_id" in data
        assert "scenes" in data
        assert "created_at" in data
        assert "updated_at" in data
    
    def test_manifest_entry_structure(self):
        """Manifest entry should have correct fields."""
        entry = SceneManifestEntry(
            scene_id="scene_01",
            sequence=1,
            file="scene_01.mp4",
            status="completed",
        )
        assert entry.scene_id == "scene_01"
        assert entry.sequence == 1
        assert entry.file == "scene_01.mp4"
        assert entry.status == "completed"


class _FakeDownload:
    def save_as(self, path):
        Path(path).write_bytes(b"fake video")


class _FakeDownloadContext:
    def __init__(self, download):
        self.value = download

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _DownloadPage(_FakePage):
    def __init__(self, tile, menu_item=None):
        super().__init__()
        self.tile = tile
        self.menu_item = menu_item
        self.selected_selectors = []

    def locator(self, selector):
        self.selected_selectors.append(selector)
        if selector.startswith("[data-media-id="):
            return self.tile
        if selector.startswith("video[src*="):
            return _FakeLocator()
        if "Download" in selector and self.menu_item is not None:
            return self.menu_item
        return _FakeLocator()

    def expect_download(self, timeout=None):
        return _FakeDownloadContext(_FakeDownload())


def test_download_uses_only_correlated_tile(tmp_path):
    generator = FlowSceneGenerator(screenshots_dir=tmp_path)
    tile = _FakeLocator()
    download_button = _FakeLocator()
    tile.locator = lambda selector: download_button if "Download" in selector else _FakeLocator()
    generator.page = _DownloadPage(tile)
    generator._correlated_asset = {
        "media_id": "correlated-media",
        "redirect_url": "https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=correlated-media",
    }

    result = generator._download_new_asset(tmp_path / "correlated.mp4")

    assert result == tmp_path / "correlated.mp4"
    assert (tmp_path / "correlated.mp4").read_bytes() == b"fake video"
    assert generator._last_download_evidence["media_id"] == "correlated-media"
    assert generator._last_download_evidence["tile_strategy"] == '[data-media-id="correlated-media"]'
    assert download_button.clicks == 1


def test_download_uses_correlated_tile_overflow_menu(tmp_path):
    generator = FlowSceneGenerator(screenshots_dir=tmp_path)
    tile = _FakeLocator()
    menu_button = _FakeLocator()

    class _HiddenLocator(_FakeLocator):
        def is_visible(self, timeout=None):
            return False

    tile.locator = lambda selector: (
        menu_button if "more" in selector.lower() else _HiddenLocator()
    )
    menu_item = _FakeLocator()
    generator.page = _DownloadPage(tile, menu_item=menu_item)
    generator._correlated_asset = {"media_id": "correlated-media"}

    result = generator._download_new_asset(tmp_path / "overflow.mp4")

    assert result == tmp_path / "overflow.mp4"
    assert (tmp_path / "overflow.mp4").read_bytes() == b"fake video"
    assert generator._last_download_evidence["selector"].endswith(" -> Download")
    assert menu_button.clicks == 1
    assert menu_item.clicks == 1
