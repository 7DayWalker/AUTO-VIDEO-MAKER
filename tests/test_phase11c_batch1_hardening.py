"""Phase 11C Batch 1 FINAL HARDENING tests.

Covers the four manual-test findings without any live network calls:

A. ProductionPlan scene-duration contract — the schema keeps its strict
   [MIN_SCENE_DURATION, MAX_SCENE_DURATION] bound; the planner prompts state
   it; the LLM parse boundary applies duration-PRESERVING normalization
   (never blind clamping, never silent) and infeasible totals still fail
   truthfully.
B. AI planner — OpenRouter remains the configuration-selected provider; the
   planner factory never silently falls back to mock in real mode.
C. Visual routing — the routed source selects per-scene among AI image
   generation (OpenRouter Image API), quality stock (Pexels), and the free
   Wikimedia fallback; unconfigured children are skipped truthfully; every
   failure is reported with all reasons; provenance is per-scene truthful.
D. Image validation — downloaded/generated imagery must pass FFprobe and the
   Phase 6C intake gate; corrupt media is rejected, never fabricated around.
I. HTTP polling — client disconnects are classified cleanly, real errors are
   not masked.

All tests are offline: HTTP seams are injected; no provider is called; no
credits are consumed.
"""

import json

import pytest
from pydantic import ValidationError

from app.content.config import MAX_SCENE_DURATION, MIN_SCENE_DURATION
from app.content.generator import ContentGenerator, normalize_scene_durations
from app.content.models import ContentBrief, ProductionPlan, Scene
from app.content.prompts.scene_planner import (
    get_combined_generation_prompt,
    get_scene_planner_system_prompt,
    get_scene_planner_user_prompt,
)


def _brief(target_duration=30.0, number_of_scenes=4):
    return ContentBrief(
        topic="Test topic",
        niche="technology",
        target_audience="developers",
        tone="informative",
        target_duration=target_duration,
        platform="youtube_shorts",
        language="en",
        content_goal="education",
        number_of_scenes=number_of_scenes,
    )


def _scene_dicts(durations):
    return [
        {
            "id": f"scene_{i + 1:02d}",
            "sequence": i + 1,
            "narration": f"Narration {i + 1}",
            "visual_prompt": f"Visual prompt {i + 1}",
            "estimated_duration": durations[i],
            "purpose": "hook" if i == 0 else "fact",
        }
        for i in range(len(durations))
    ]


# ---------------------------------------------------------------------------
# A — ProductionPlan duration contract
# ---------------------------------------------------------------------------

def test_schema_still_rejects_scene_duration_above_max():
    """Hardening never weakened the schema: >10s scenes are still rejected."""
    with pytest.raises(ValidationError):
        Scene(
            id="scene_01", sequence=1, narration="n", visual_prompt="v",
            estimated_duration=MAX_SCENE_DURATION + 2,
        )


def test_normalize_noop_when_all_durations_valid():
    scenes = _scene_dicts([5, 6, 7])
    out, warnings = normalize_scene_durations(scenes)
    assert warnings == []
    assert [s["estimated_duration"] for s in out] == [5, 6, 7]


def test_normalize_redistributes_preserving_total():
    """The manual-test failure pattern: a model emits a 12s scene.

    Normalization must bring every scene inside [2, 10] while preserving the
    TOTAL duration exactly — the plan's contract with the brief — keep the
    relative pacing weights, and record a warning (never silent).
    """
    scenes = _scene_dicts([12, 6, 6, 6])  # total 30, feasible in [8, 40]
    out, warnings = normalize_scene_durations(scenes)
    durations = [s["estimated_duration"] for s in out]
    assert all(MIN_SCENE_DURATION <= d <= MAX_SCENE_DURATION for d in durations)
    assert abs(sum(durations) - 30.0) < 1e-6
    assert len(warnings) == 1
    assert "preserved" in warnings[0]
    # Over-long scene capped at the schema max; the others absorbed its
    # excess proportionally (equal up to 0.1s rounding, total kept exact).
    assert durations[0] == MAX_SCENE_DURATION
    assert max(durations[1:]) - min(durations[1:]) <= 0.1 + 1e-9
    assert all(abs(d - 20.0 / 3.0) <= 0.1 for d in durations[1:])
    assert all(d < durations[0] for d in durations[1:])


def test_normalize_boundary_total_forces_uniform_max():
    """When the total EQUALS n * max, the only feasible plan is uniform
    max-duration scenes; normalization reaches it with the total preserved."""
    scenes = _scene_dicts([12, 15, 8, 5])  # total 40 == 4 scenes * 10 max
    out, warnings = normalize_scene_durations(scenes)
    durations = [s["estimated_duration"] for s in out]
    assert durations == [MAX_SCENE_DURATION] * 4
    assert abs(sum(durations) - 40.0) < 1e-6
    assert len(warnings) == 1


def test_normalize_handles_under_minimum_scenes_too():
    scenes = _scene_dicts([1, 9, 9])  # total 19 feasible in [6, 30]
    out, warnings = normalize_scene_durations(scenes)
    durations = [s["estimated_duration"] for s in out]
    assert all(MIN_SCENE_DURATION <= d <= MAX_SCENE_DURATION for d in durations)
    assert abs(sum(durations) - 19.0) < 1e-6
    assert warnings


def test_normalize_noop_when_total_infeasible_fails_truthfully():
    """When the total cannot fit in [n*2, n*10], normalization must NOT
    distort the plan — the schema/validator report the truthful failure."""
    scenes = _scene_dicts([20, 20, 20])  # total 60 > 3 scenes * 10 max
    out, warnings = normalize_scene_durations(scenes)
    assert warnings == []
    assert [s["estimated_duration"] for s in out] == [20, 20, 20]
    with pytest.raises(ValidationError):
        ProductionPlan(
            project_id="p", title="t", hook="h", script="s",
            estimated_duration=60,
            scenes=[Scene(**scene) for scene in out],
        )


def test_normalize_ignores_missing_or_non_numeric_durations():
    scenes = _scene_dicts([5, 5])
    scenes[1]["estimated_duration"] = "twelve"
    out, warnings = normalize_scene_durations(scenes)
    assert warnings == []
    assert out[1]["estimated_duration"] == "twelve"


class _FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def generate_json(self, prompt, system_prompt=None, **kwargs):
        self.prompts.append(prompt)
        payload = self.payload

        class _Response:
            content = payload

        return _Response()


def _plan_json(durations, target_duration=None):
    scenes = _scene_dicts(durations)
    return json.dumps({
        "project_id": "20260905_hardening_001",
        "title": "Hardening",
        "hook": "hook",
        "script": "script",
        "estimated_duration": target_duration or sum(durations),
        "scenes": scenes,
        "metadata": {"platform": "youtube_shorts", "language": "en"},
    })


def test_generator_parse_normalizes_and_records_warning():
    """The full LLM parse boundary: 12-20s scene estimates become a valid
    plan with the total preserved, and the result carries the warning."""
    # 4 scenes cannot hold 61s (max 40): truthful schema failure, no distortion.
    provider = _FakeProvider(_plan_json([12, 15, 16, 18]))
    result = ContentGenerator(provider, max_retries=1, retry_delay=0).generate(
        _brief(target_duration=61.0, number_of_scenes=4)
    )
    assert not result.success
    assert result.attempts == 1

    # 4 scenes CAN hold 40s: normalization produces a valid, total-preserving
    # plan and the warnings surface in the result.
    provider = _FakeProvider(_plan_json([12, 14, 7, 7]))
    result = ContentGenerator(provider, max_retries=1, retry_delay=0).generate(
        _brief(target_duration=40.0, number_of_scenes=4)
    )
    assert result.success, result.error
    assert result.plan is not None
    durations = [s.estimated_duration for s in result.plan.scenes]
    assert all(MIN_SCENE_DURATION <= d <= MAX_SCENE_DURATION for d in durations)
    assert abs(sum(durations) - 40.0) < 1e-6
    assert any("preserved" in w for w in result.warnings)


def test_retry_prompt_carries_the_exact_rejection_reason():
    """Retries must feed the rejection reason back so the model can
    converge instead of repeating the same schema violation blindly."""
    provider = _FakeProvider("not json at all")
    generator = ContentGenerator(provider, max_retries=2, retry_delay=0)
    result = generator.generate(_brief())
    assert not result.success
    assert len(provider.prompts) == 2
    assert "REJECTED" not in provider.prompts[0]
    assert "REJECTED BY STRICT SCHEMA VALIDATION" in provider.prompts[1]
    assert f"{MAX_SCENE_DURATION:g}" in provider.prompts[1]


def test_prompts_state_the_hard_duration_contract_and_hint():
    brief = _brief(target_duration=45.0, number_of_scenes=4)
    system_prompt, user_prompt = get_combined_generation_prompt(brief)
    for prompt in (system_prompt, user_prompt):
        assert "hard schema limit" in prompt.lower()
        assert f"{MAX_SCENE_DURATION:g}" in prompt
        assert "ai_image" in prompt
        assert "stock" in prompt
    planner_system = get_scene_planner_system_prompt()
    assert f"{MAX_SCENE_DURATION:g}" in planner_system
    assert "visual_source" in planner_system
    planner_user = get_scene_planner_user_prompt(brief)
    assert f"{MAX_SCENE_DURATION:g}" in planner_user
    assert "additional scenes" in planner_user.lower()


# ---------------------------------------------------------------------------
# B — AI planner: configuration-driven OpenRouter, no silent mock fallback
# ---------------------------------------------------------------------------

def test_planner_factory_selects_openrouter_from_configuration(monkeypatch):
    import app.content.providers as providers_mod
    import app.content.providers.openrouter as openrouter_mod
    from app.content.planner import create_scene_planner
    from app.content.providers.mock import MockLLMProvider
    from app.content.providers.openrouter import OpenRouterProvider

    monkeypatch.setattr(providers_mod, "DEFAULT_LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(openrouter_mod, "OPENROUTER_API_KEY", "test-key")

    planner = create_scene_planner(mock_mode=False)
    assert isinstance(planner.provider, OpenRouterProvider)
    assert not isinstance(planner.provider, MockLLMProvider)


def test_planner_factory_fails_truthfully_when_provider_unavailable(monkeypatch):
    """No silent mock fallback: an unconfigured real provider must raise."""
    import app.content.providers as providers_mod
    import app.content.providers.openrouter as openrouter_mod
    from app.content.planner import create_scene_planner

    monkeypatch.setattr(providers_mod, "DEFAULT_LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(openrouter_mod, "OPENROUTER_API_KEY", "")

    with pytest.raises(RuntimeError, match="content provider unavailable"):
        create_scene_planner(mock_mode=False)


def test_planner_factory_mock_only_when_explicit():
    from app.content.planner import create_scene_planner
    from app.content.providers.mock import MockLLMProvider

    planner = create_scene_planner(mock_mode=True)
    assert isinstance(planner.provider, MockLLMProvider)


def test_scene_visual_source_hint_normalization():
    base = dict(
        id="scene_01", sequence=1, narration="n", visual_prompt="v",
        estimated_duration=5,
    )
    assert Scene(**base, visual_source="AI Image").visual_source == "ai_image"
    assert Scene(**base, visual_source="ai-generated").visual_source == "ai_image"
    assert Scene(**base, visual_source="stock_photo").visual_source == "stock"
    assert Scene(**base, visual_source="photography").visual_source == "stock"
    # Unknown values degrade to "no hint" — never reject the whole plan.
    assert Scene(**base, visual_source="banana").visual_source is None
    assert Scene(**base, visual_source="  ").visual_source is None
    assert Scene(**base).visual_source is None


# ---------------------------------------------------------------------------
# B2 — OpenRouter Image API contract (offline; HTTP seam monkeypatched)
# ---------------------------------------------------------------------------

def _png_bytes(tmp_path):
    from tests.test_asset_workflow import make_image

    path = tmp_path / "probe.png"
    make_image(path, color="teal", size="720x1280")
    return path.read_bytes()


class _FakeImageResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload


def test_generate_image_success_contract(tmp_path, monkeypatch):
    import base64

    import app.content.providers.openrouter as openrouter_mod
    from app.content.providers.openrouter import OpenRouterProvider

    png = _png_bytes(tmp_path)
    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        captured["timeout"] = timeout
        return _FakeImageResponse(200, {
            "created": 1760000000,
            "data": [{
                "b64_json": base64.b64encode(png).decode("ascii"),
                "media_type": "image/png",
            }],
            "usage": {"image_tokens": 4160, "cost": 0.039},
        })

    monkeypatch.setattr(openrouter_mod.requests, "post", _fake_post)

    provider = OpenRouterProvider(api_key="test-key")
    result = provider.generate_image(
        "robot hero shielding a kitten", model="test/image-model",
        aspect_ratio="9:16",
    )
    assert captured["url"].endswith("/images")
    assert captured["payload"] == {
        "model": "test/image-model",
        "prompt": "robot hero shielding a kitten",
        "n": 1,
        "aspect_ratio": "9:16",
    }
    assert captured["timeout"] >= openrouter_mod.IMAGE_REQUEST_TIMEOUT_FLOOR
    assert result.image_bytes == png
    assert result.media_type == "image/png"
    assert result.model == "test/image-model"
    assert result.usage["cost"] == 0.039


def test_generate_image_requires_configuration(monkeypatch):
    import app.content.providers.openrouter as openrouter_mod
    from app.content.exceptions import (
        LLMAuthenticationError,
        LLMResponseError,
    )
    from app.content.providers.openrouter import OpenRouterProvider

    monkeypatch.setattr(openrouter_mod, "OPENROUTER_API_KEY", "")
    with pytest.raises(LLMAuthenticationError):
        OpenRouterProvider().generate_image("p", model="test/image-model")

    with pytest.raises(LLMResponseError, match="AVM_IMAGE_MODEL"):
        OpenRouterProvider(api_key="test-key").generate_image("p", model="")


def test_generate_image_reports_http_errors_truthfully(monkeypatch):
    import app.content.providers.openrouter as openrouter_mod
    from app.content.exceptions import LLMResponseError
    from app.content.providers.openrouter import OpenRouterProvider

    monkeypatch.setattr(
        openrouter_mod.requests, "post",
        lambda *a, **k: _FakeImageResponse(
            400, {"error": {"message": "model is not image-capable"}},
            text="bad request",
        ),
    )
    with pytest.raises(LLMResponseError):
        OpenRouterProvider(api_key="test-key").generate_image(
            "p", model="chat-only/model"
        )


def test_generate_image_rejects_empty_payload(monkeypatch):
    import app.content.providers.openrouter as openrouter_mod
    from app.content.exceptions import LLMResponseError
    from app.content.providers.openrouter import OpenRouterProvider

    monkeypatch.setattr(
        openrouter_mod.requests, "post",
        lambda *a, **k: _FakeImageResponse(200, {"created": 1, "data": []}),
    )
    with pytest.raises(LLMResponseError, match="no image data"):
        OpenRouterProvider(api_key="test-key").generate_image(
            "p", model="test/image-model"
        )


# ---------------------------------------------------------------------------
# C — Visual sources: Pexels (stock) and OpenRouter (AI image), offline
# ---------------------------------------------------------------------------

from app.orchestrator.alibaba_image import AlibabaImageSource  # noqa: E402
from app.orchestrator.visual_sources import (  # noqa: E402
    DormantExternalImageSource,
    OpenRouterImageSource,
    PexelsImageSource,
    RoutedVisualSource,
    SourceAvailability,
    VisualSourceContext,
    VisualSourceResult,
    VisualSourceStatus,
    WikimediaCommonsImageSource,
    create_visual_source,
)


def _ctx(tmp_path, scene_id="scene_01", prompt="gecko face amber eyes pulsing",
         hint=None, aspect="9:16"):
    return VisualSourceContext(
        project_id="p1",
        scene_id=scene_id,
        sequence=1,
        visual_prompt=prompt,
        purpose="fact",
        visual_style="cinematic",
        aspect_ratio=aspect,
        source_hint=hint,
        output_dir=tmp_path,
    )


def _pexels_payload(photo_id=7762128, width=3648, height=5472,
                    alt_text="close up of a gecko face with amber eyes"):
    return {
        "page": 1,
        "per_page": 8,
        "photos": [{
            "id": photo_id,
            "width": width,
            "height": height,
            "alt": alt_text,
            "url": f"https://www.pexels.com/photo/big-waves-{photo_id}/",
            "photographer": "Jane Doe",
            "photographer_url": "https://www.pexels.com/@janedoe",
            "src": {
                "original": f"https://images.pexels.com/photos/{photo_id}/pexels-photo-{photo_id}.jpeg",
                "large2x": f"https://images.pexels.com/photos/{photo_id}/pexels-photo-{photo_id}.jpeg?auto=compress&cs=tinysrgb&h=650&w=940",
            },
        }],
    }


def test_pexels_source_success_records_full_provenance(tmp_path):
    png = _png_bytes(tmp_path)
    searches = []

    def _search(url, params, api_key):
        searches.append((url, dict(params), api_key))
        return _pexels_payload()

    def _download(url, destination):
        destination.write_bytes(png)

    source = PexelsImageSource(
        tmp_path, api_key="test-pexels-key",
        search_fn=_search, download_fn=_download,
    )
    assert source.availability() == SourceAvailability.AVAILABLE

    result = source.source(_ctx(tmp_path))
    assert result.status == VisualSourceStatus.SUCCESS, result.message
    assert result.provider_id == "pexels"
    assert result.artifact_path.is_file()

    # The first search carries the AI-derived terms AND the vertical
    # orientation filter; the API key is passed through the seam.
    url, params, api_key = searches[0]
    assert url == PexelsImageSource._API_URL
    assert api_key == "test-pexels-key"
    assert params["orientation"] == "portrait"
    assert "gecko" in params["query"]

    meta = result.metadata
    assert meta["kind"] == "stock"
    assert meta["library"] == "pexels"
    assert meta["photo_id"] == 7762128
    assert meta["license"].startswith("Pexels License")
    assert meta["creator"] == "Jane Doe"
    assert meta["landing_page"].startswith("https://www.pexels.com/")
    assert meta["source_url"]


def test_pexels_source_requires_key(monkeypatch, tmp_path):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    source = PexelsImageSource(tmp_path)
    assert source.availability() == SourceAvailability.REQUIRES_CONFIGURATION
    assert "PEXELS_API_KEY" in source.configuration_summary()
    result = source.source(_ctx(tmp_path))
    assert result.status == VisualSourceStatus.REQUIRES_CONFIGURATION


def test_pexels_source_fails_truthfully_when_nothing_usable(tmp_path):
    def _search(url, params, api_key):
        return {"photos": []}

    source = PexelsImageSource(
        tmp_path, api_key="test-pexels-key", search_fn=_search,
    )
    result = source.source(_ctx(tmp_path))
    assert result.status == VisualSourceStatus.FAILURE
    assert "no usable Pexels image" in result.message
    assert "AVM_IMAGE_MODEL" in result.message  # actionable next step


def test_pexels_rejects_technically_valid_but_unrelated_candidate(tmp_path):
    """The manual-test failure mode on the stock route: a technically valid
    photo whose own description shares nothing with the scene intent must
    NOT be accepted merely because its bytes are valid — the relaxation
    ladder must run and the source must fail truthfully."""
    png = _png_bytes(tmp_path)
    searches = []

    def _search(url, params, api_key):
        searches.append(dict(params))
        return _pexels_payload(alt_text="excavator digging at a construction site")

    def _download(url, destination):
        destination.write_bytes(png)  # valid image bytes — relevance, not
        # technical validity, is what must reject this candidate

    source = PexelsImageSource(
        tmp_path, api_key="test-pexels-key",
        search_fn=_search, download_fn=_download,
    )
    result = source.source(_ctx(tmp_path))

    assert result.status == VisualSourceStatus.FAILURE
    assert "no usable Pexels image" in result.message
    assert result.artifact_path is None
    # The full relaxation ladder ran; nothing was fabricated.
    assert len(searches) >= 2


def test_pexels_ranks_candidates_by_relevance(tmp_path):
    """Pexels' own relevance order is preserved, but the lightweight gate
    must prefer the candidate whose description actually matches the scene
    intent when the API's first hit does not."""
    png = _png_bytes(tmp_path)
    payload = _pexels_payload(photo_id=111)
    weaker = _pexels_payload(photo_id=222)
    weaker["photos"][0]["alt"] = "small gecko on a rock"
    payload["photos"] = weaker["photos"] + payload["photos"]

    source = PexelsImageSource(
        tmp_path, api_key="test-pexels-key",
        search_fn=lambda url, params, api_key: payload,
        download_fn=lambda url, dest: dest.write_bytes(png),
    )
    result = source.source(_ctx(tmp_path))

    assert result.status == VisualSourceStatus.SUCCESS, result.message
    # The higher-overlap candidate wins even though it comes second in the
    # API's own ordering.
    assert result.metadata["photo_id"] == 111
    assert result.metadata["relevance_score"] >= 4


def test_pexels_source_rejects_corrupt_download(tmp_path):
    def _search(url, params, api_key):
        return _pexels_payload()

    def _download(url, destination):
        destination.write_bytes(b"this is not an image")

    source = PexelsImageSource(
        tmp_path, api_key="test-pexels-key",
        search_fn=_search, download_fn=_download,
    )
    result = source.source(_ctx(tmp_path))
    assert result.status == VisualSourceStatus.FAILURE
    assert "media verification" in result.message


class _StubAvailableProvider:
    def is_available(self):
        return True


def test_openrouter_source_success_records_ai_provenance(tmp_path):
    from app.content.providers.openrouter import OpenRouterImageResult

    png = _png_bytes(tmp_path)
    calls = []

    def _generate(prompt, model=None, aspect_ratio=None):
        calls.append((prompt, model, aspect_ratio))
        return OpenRouterImageResult(
            image_bytes=png, media_type="image/png", model=model,
            usage={"cost": 0.04},
        )

    source = OpenRouterImageSource(
        tmp_path, model="test/image-model",
        provider=_StubAvailableProvider(), generate_fn=_generate,
    )
    assert source.availability() == SourceAvailability.AVAILABLE

    result = source.source(_ctx(tmp_path, prompt="robot GECO shielding kitten"))
    assert result.status == VisualSourceStatus.SUCCESS, result.message
    assert result.provider_id == "openrouter"
    prompt, model, aspect = calls[0]
    assert "GECO" in prompt
    assert model == "test/image-model"
    assert aspect == "9:16"

    meta = result.metadata
    assert meta["kind"] == "ai_generated"
    assert meta["provider"] == "openrouter"
    assert meta["model"] == "test/image-model"
    assert meta["usage"]["cost"] == 0.04


def test_openrouter_source_requires_model_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv("AVM_IMAGE_MODEL", raising=False)
    source = OpenRouterImageSource(tmp_path)
    assert source.availability() == SourceAvailability.REQUIRES_CONFIGURATION
    assert "AVM_IMAGE_MODEL" in source.configuration_summary()
    result = source.source(_ctx(tmp_path))
    assert result.status == VisualSourceStatus.REQUIRES_CONFIGURATION


def test_openrouter_source_never_fabricates_on_failure(tmp_path):
    def _generate(prompt, model=None, aspect_ratio=None):
        raise RuntimeError("image model unavailable (quota)")

    source = OpenRouterImageSource(
        tmp_path, model="test/image-model",
        provider=_StubAvailableProvider(), generate_fn=_generate,
    )
    result = source.source(_ctx(tmp_path))
    assert result.status == VisualSourceStatus.FAILURE
    assert "quota" in result.message
    # No artifact was fabricated.
    assert not list(tmp_path.glob("autosrc_*"))


def test_openrouter_source_rejects_invalid_generated_media(tmp_path):
    from app.content.providers.openrouter import OpenRouterImageResult

    def _generate(prompt, model=None, aspect_ratio=None):
        return OpenRouterImageResult(
            image_bytes=b"corrupt bytes", media_type="image/png", model=model,
        )

    source = OpenRouterImageSource(
        tmp_path, model="test/image-model",
        provider=_StubAvailableProvider(), generate_fn=_generate,
    )
    result = source.source(_ctx(tmp_path))
    assert result.status == VisualSourceStatus.FAILURE
    assert "media verification" in result.message
    # The corrupt artifact is removed — never left for a later stage to adopt.
    assert not list(tmp_path.glob("autosrc_*"))


# ---------------------------------------------------------------------------
# C2 — RoutedVisualSource: minimal per-scene routing, truthful fall-through
# ---------------------------------------------------------------------------

def _success_child_seams(tmp_path, png, *, calls):
    """Real child sources wired offline: openrouter succeeds, pexels succeeds."""
    from app.content.providers.openrouter import OpenRouterImageResult

    def _generate(prompt, model=None, aspect_ratio=None):
        calls.append("openrouter")
        return OpenRouterImageResult(
            image_bytes=png, media_type="image/png", model=model,
        )

    ai = OpenRouterImageSource(
        tmp_path, model="test/image-model",
        provider=_StubAvailableProvider(), generate_fn=_generate,
    )

    def _search(url, params, api_key):
        calls.append("pexels")
        return _pexels_payload()

    def _download(url, destination):
        destination.write_bytes(png)

    stock = PexelsImageSource(
        tmp_path, api_key="test-pexels-key",
        search_fn=_search, download_fn=_download,
    )
    return ai, stock


def test_routed_hint_ai_image_prefers_generation(tmp_path):
    png = _png_bytes(tmp_path)
    calls = []
    ai, stock = _success_child_seams(tmp_path, png, calls=calls)
    routed = RoutedVisualSource([stock, ai], name="auto")

    result = routed.source(_ctx(tmp_path, hint="ai_image"))
    assert result.status == VisualSourceStatus.SUCCESS, result.message
    assert result.provider_id == "openrouter"
    assert calls == ["openrouter"]  # stock never called
    assert result.metadata["routed_via"] == "auto"
    assert result.metadata["source_hint"] == "ai_image"
    assert result.metadata["kind"] == "ai_generated"


def test_routed_hint_stock_prefers_stock_family(tmp_path):
    png = _png_bytes(tmp_path)
    calls = []
    ai, stock = _success_child_seams(tmp_path, png, calls=calls)
    routed = RoutedVisualSource([ai, stock], name="auto")

    result = routed.source(_ctx(tmp_path, hint="stock"))
    assert result.status == VisualSourceStatus.SUCCESS, result.message
    assert result.provider_id == "pexels"
    assert calls == ["pexels"]  # AI generation never called (no cost)
    assert result.metadata["library"] == "pexels"


def test_routed_default_order_is_ai_then_stock(tmp_path):
    png = _png_bytes(tmp_path)
    calls = []
    ai, stock = _success_child_seams(tmp_path, png, calls=calls)
    routed = RoutedVisualSource([ai, stock], name="auto")

    # No hint: configured chain order — AI first (operator opt-in via model).
    order = [child.name for child in routed._ordered_children(_ctx(tmp_path))]
    assert order == ["openrouter", "pexels"]
    result = routed.source(_ctx(tmp_path))
    assert result.provider_id == "openrouter"


def test_routed_falls_through_chain_when_child_fails(tmp_path):
    png = _png_bytes(tmp_path)
    calls = []
    _ai, stock = _success_child_seams(tmp_path, png, calls=calls)

    def _failing_generate(prompt, model=None, aspect_ratio=None):
        calls.append("openrouter")
        raise RuntimeError("image model overloaded")

    ai = OpenRouterImageSource(
        tmp_path, model="test/image-model",
        provider=_StubAvailableProvider(), generate_fn=_failing_generate,
    )
    routed = RoutedVisualSource([ai, stock], name="auto")

    result = routed.source(_ctx(tmp_path))
    assert result.status == VisualSourceStatus.SUCCESS, result.message
    assert result.provider_id == "pexels"
    assert calls == ["openrouter", "pexels"]
    assert result.metadata["routed_via"] == "auto"


def test_routed_falls_through_when_stock_candidate_is_irrelevant(tmp_path):
    """Relevance rejection integrates with the existing router fall-through:
    a stock child that only finds technically-valid-but-unrelated imagery
    FAILS, and the next configured child (AI generation) still runs."""
    from app.content.providers.openrouter import OpenRouterImageResult

    png = _png_bytes(tmp_path)
    calls = []
    _ai, _stock = _success_child_seams(tmp_path, png, calls=calls)

    def _search(url, params, api_key):
        calls.append("pexels")
        return _pexels_payload(
            alt_text="excavator digging at a construction site"
        )

    def _download(url, destination):
        destination.write_bytes(png)

    stock = PexelsImageSource(
        tmp_path, api_key="test-pexels-key",
        search_fn=_search, download_fn=_download,
    )
    ai = OpenRouterImageSource(
        tmp_path, model="test/image-model",
        provider=_StubAvailableProvider(),
        generate_fn=lambda prompt, model=None, aspect_ratio=None: (
            calls.append("openrouter"),
            OpenRouterImageResult(
                image_bytes=png, media_type="image/png", model=model,
            ),
        )[1],
    )
    routed = RoutedVisualSource([stock, ai], name="auto")

    result = routed.source(_ctx(tmp_path))
    assert result.status == VisualSourceStatus.SUCCESS, result.message
    assert result.provider_id == "openrouter"
    # The stock child exhausts its relaxation ladder before falling through.
    assert calls.count("pexels") >= 1
    assert calls[-1] == "openrouter"
    assert result.metadata["kind"] == "ai_generated"


def test_routed_reports_every_reason_when_all_children_fail(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("AVM_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)

    ai = OpenRouterImageSource(tmp_path)      # unconfigured -> skipped
    stock = PexelsImageSource(tmp_path)       # unconfigured -> skipped

    commons = WikimediaCommonsImageSource(tmp_path)

    def _boom(url, params):
        raise RuntimeError("commons unreachable")

    commons._search_fn = _boom
    routed = RoutedVisualSource([ai, stock, commons], name="auto")

    result = routed.source(_ctx(tmp_path))
    assert result.status == VisualSourceStatus.FAILURE
    assert "every configured visual source failed" in result.message
    assert "openrouter: skipped" in result.message
    assert "pexels: skipped" in result.message
    assert "commons unreachable" in result.message
    # Nothing fabricated anywhere.
    assert not list(tmp_path.glob("autosrc_*"))


def test_factory_auto_builds_default_routing_chain(monkeypatch, tmp_path):
    monkeypatch.delenv("AVM_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)

    source = create_visual_source("auto", tmp_path)
    assert isinstance(source, RoutedVisualSource)
    # Batch 1C: Alibaba is the PRIMARY AI image source; OpenRouter remains
    # the fallback generation source, then stock (Pexels), then Wikimedia.
    assert [child.name for child in source.children] == [
        "alibaba", "openrouter", "pexels", "wikimedia",
    ]
    # Wikimedia (keyless) keeps the default chain available out of the box.
    assert source.availability() == SourceAvailability.AVAILABLE
    assert isinstance(source.children[0], AlibabaImageSource)
    assert isinstance(source.children[1], OpenRouterImageSource)
    assert isinstance(source.children[2], PexelsImageSource)
    assert isinstance(source.children[3], WikimediaCommonsImageSource)



def test_factory_explicit_chain_routes_only_configured_sources(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("AVM_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)

    source = create_visual_source("openrouter,pexels", tmp_path)
    assert isinstance(source, RoutedVisualSource)
    assert [child.name for child in source.children] == ["openrouter", "pexels"]
    # Neither child configured: truthful "requires configuration", never a call.
    assert source.availability() == SourceAvailability.REQUIRES_CONFIGURATION
    assert not source.is_available()


def test_factory_single_selectors_map_to_concrete_sources(tmp_path):
    assert create_visual_source("", tmp_path) is None
    assert isinstance(create_visual_source("pexels", tmp_path), PexelsImageSource)
    assert isinstance(
        create_visual_source("openrouter", tmp_path), OpenRouterImageSource
    )
    assert isinstance(
        create_visual_source("wikimedia", tmp_path), WikimediaCommonsImageSource
    )
    unknown = create_visual_source("future-video-provider", tmp_path)
    assert isinstance(unknown, DormantExternalImageSource)
    assert not unknown.is_available()


# ---------------------------------------------------------------------------
# D — Phase 6C intake gate: provenance preserved, invalid media rejected
# ---------------------------------------------------------------------------

def test_intake_preserves_visual_source_metadata(tmp_path):
    from types import SimpleNamespace

    import app.orchestrator.assets as asset_workflow
    from tests.test_asset_workflow import make_image

    img = tmp_path / "external" / "shot.png"
    make_image(img)
    scenes = [SimpleNamespace(id="scene_01")]
    manifest, errors = asset_workflow.intake_assets(
        tmp_path, scenes, {"scene_01": str(img)}, {"scene_01": "image_motion"},
        provenance={"scene_01": {
            "supplied_by": "auto_source",
            "source_provider": "pexels",
            "source_status": "success",
            "source_metadata": {
                "kind": "stock", "library": "pexels",
                "license": "Pexels License", "creator": "Jane Doe",
            },
        }},
    )
    assert errors == []
    entry = manifest["assets"]["scene_01"]
    assert entry["supplied_by"] == "auto_source"
    assert entry["source_provider"] == "pexels"
    assert entry["source_metadata"]["library"] == "pexels"
    assert entry["source_metadata"]["license"] == "Pexels License"
    assert entry["sha256"]
    # Project-owned stored copy exists.
    assert (tmp_path / entry["stored_as"]).is_file()


def test_intake_still_rejects_empty_image(tmp_path):
    from types import SimpleNamespace

    import app.orchestrator.assets as asset_workflow

    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    scenes = [SimpleNamespace(id="scene_01")]
    manifest, errors = asset_workflow.intake_assets(
        tmp_path, scenes, {"scene_01": str(empty)},
        {"scene_01": "image_motion"},
    )
    assert errors
    assert "empty" in errors[0]
    assert "scene_01" not in manifest["assets"]


def test_verify_sourced_image_rejects_garbage_with_image_extension(tmp_path):
    """ffprobe alone "succeeds" on garbage bytes with an image extension
    (image2 demuxer reports a 0x0 mjpeg frame with rc=0); the shared
    verifier must reject it so corrupt downloads never reach intake."""
    from app.orchestrator.visual_sources import verify_sourced_image
    from tests.test_asset_workflow import make_image

    garbage = tmp_path / "garbage.jpeg"
    garbage.write_bytes(b"this is not an image")
    with pytest.raises(ValueError, match="no decodable image dimensions"):
        verify_sourced_image(garbage)

    real = tmp_path / "real.png"
    make_image(real)
    verify_sourced_image(real)  # does not raise


# ---------------------------------------------------------------------------
# I — HTTP polling: client disconnects classified cleanly
# ---------------------------------------------------------------------------

def test_client_disconnect_classification():
    from app.web.server import _is_client_disconnect

    assert _is_client_disconnect(ConnectionAbortedError(10053, "aborted"))
    assert _is_client_disconnect(ConnectionResetError(10054, "reset"))
    assert _is_client_disconnect(BrokenPipeError(10054, "gone"))

    exc = OSError("software caused connection abort")
    exc.winerror = 10053
    assert _is_client_disconnect(exc)

    # Real defects must NOT be masked as disconnects.
    assert not _is_client_disconnect(ValueError("boom"))
    plain = OSError("disk on fire")
    plain.winerror = None
    assert not _is_client_disconnect(plain)


# ---------------------------------------------------------------------------
# C3 — Pipeline integration: TRUE per-scene routed provenance end-to-end
# ---------------------------------------------------------------------------

def test_pipeline_records_per_scene_routed_provenance(tmp_path, monkeypatch):
    """Routing means different scenes may come from different child sources;
    the asset manifest must record the CHILD that actually produced each
    artifact (not the selector identity), with its full metadata — then run
    the real intake -> preflight -> ImageMotion path."""
    import app.orchestrator.assets as asset_workflow
    import app.orchestrator.visual_sources as visual_sources_mod
    from app.orchestrator.visual_providers import IMAGE_MOTION_PROVIDER
    from tests.test_visual_sources import _missing_orchestrator, _orch_plan

    orch, _pid = _missing_orchestrator(tmp_path, monkeypatch, image_source="auto")
    plan, _sm = _orch_plan(tmp_path)
    png = _png_bytes(tmp_path)

    class _FakeRoutedSource:
        name = "auto"

        def is_available(self):
            return True

        def availability(self):
            return SourceAvailability.AVAILABLE

        def configuration_summary(self):
            return "fake routed source (offline test)"

        def source(self, ctx):
            ctx.output_dir.mkdir(parents=True, exist_ok=True)
            out = ctx.output_dir / f"autosrc_{ctx.scene_id}.png"
            out.write_bytes(png)
            if ctx.scene_id.endswith("01"):
                return VisualSourceResult.success(
                    ctx.scene_id, "pexels", out,
                    metadata={"kind": "stock", "library": "pexels",
                              "license": "Pexels License"},
                )
            return VisualSourceResult.success(
                ctx.scene_id, "openrouter", out,
                metadata={"kind": "ai_generated", "provider": "openrouter",
                          "model": "test/image-model"},
            )

    monkeypatch.setattr(
        visual_sources_mod, "create_visual_source",
        lambda selector, output_dir=None: _FakeRoutedSource(),
    )

    scoped = {s.id: IMAGE_MOTION_PROVIDER for s in plan.scenes}
    auto, notes, prov, results = orch._auto_source_missing_assets(
        plan=plan, pending=list(plan.scenes), scoped_providers=scoped,
        manifest=asset_workflow.load_asset_manifest(tmp_path),
    )
    assert notes == []
    assert prov == "auto"
    sids = sorted(auto)
    assert results[sids[0]].provider_id == "pexels"
    assert results[sids[1]].provider_id == "openrouter"

    # Full local scene stage: intake -> preflight -> ImageMotion MP4s.
    orch._execute_scenes_stage()
    amf = json.loads(
        (tmp_path / "assets" / "asset_manifest.json").read_text("utf-8")
    )
    entries = amf["assets"]
    first, second = entries[sids[0]], entries[sids[1]]
    assert first["supplied_by"] == "auto_source"
    assert first["source_provider"] == "pexels"
    assert first["source_metadata"]["library"] == "pexels"
    assert second["supplied_by"] == "auto_source"
    assert second["source_provider"] == "openrouter"
    assert second["source_metadata"]["kind"] == "ai_generated"
    assert second["source_metadata"]["model"] == "test/image-model"

