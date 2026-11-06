"""Stage 1 quality-first observer registry, failover and truthful chapter evidence."""
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.content import capability_router as routing, provider_registry
from app.content.exceptions import LLMConnectionError, LLMRateLimitError
from app.content.model_registry import VISUAL_OBSERVER_PRIORITY, JOB_SOURCE_VIDEO_RECAP, get_model
from app.production.narration_chapters import build_chapter_plan
from app.production.narration_real import (
    admitted_visual_observer, observe_with_vision_failover, visual_observer_attribution,
)
from app.production.recap_budget import resolve_recap_budget
from tests.test_agentrouter_storyteller import Observer, frames
from tests.track9_narration_fakes import ChapterNarrationProvider, story_evidence


@pytest.fixture(autouse=True)
def isolated_observer_config(monkeypatch):
    for name in ("AVM_RECAP_VISION_MODEL", "AVM_RECAP_VISION_BATCH_SIZE", "GEMINI_API_KEY", "ALIBABA_API_KEY",
                 "ALIBABA_BASE_URL", "OPENROUTER_API_KEY", "AGENTROUTER_API_KEY", "AGENTROUTER_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    routing.clear_quota_blockers()
    yield
    routing.clear_quota_blockers()


def make_observer(provider, model, *, failure=None, calls=None):
    observer = Observer()
    observer.name, observer.model = provider, model
    observer.is_available = lambda: True
    generate = observer.generate_multimodal
    def inspect(*args, **kwargs):
        if calls is not None:
            calls.append((provider, model))
        if failure is not None:
            raise failure
        return generate(*args, **kwargs)
    observer.generate_multimodal = inspect
    return observer


def install_observers(monkeypatch, observers):
    routes = [SimpleNamespace(canonical_model_id=mid, provider=observer.name, provider_model_id=observer.model)
              for mid, observer in observers]
    monkeypatch.setattr(routing, "configured_visual_observer_routes", lambda *a: routes)
    lookup = {(observer.name, observer.model): observer for _, observer in observers}
    monkeypatch.setattr(provider_registry, "create_provider", lambda provider, model: lookup[(provider, model)])


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-6-astra"])
def test_registry_declares_full_flash_not_flash_lite_and_keeps_storyteller_text_only(model):
    canonical = get_model(model)
    assert canonical.visual_observer_model_id == "gemini-3-flash-preview"
    assert VISUAL_OBSERVER_PRIORITY == (
        "gemini-3-flash-preview", "gemini-2.5-flash", "qwen-vl-max", "qwen2.5-vl-72b-instruct", "gemini-3.5-flash-lite")
    assert canonical.bindings[0].provider == "agentrouter"
    assert canonical.bindings[0].supported_input_modalities == ("text",)
    assert canonical.bindings[0].config_keys == ("AGENTROUTER_API_KEY",)
    assert not canonical.capabilities.image_input


@pytest.mark.parametrize("override,first", [
    ("gemini-3.5-flash-lite", "gemini-3-flash-preview"), ("gemini-2.5-flash", "gemini-2.5-flash"),
    ("qwen2.5-vl-72b-instruct", "gemini-3-flash-preview"),
])
def test_existing_overrides_never_promote_lite_or_qwen_ahead_of_full_flash(monkeypatch, override, first):
    monkeypatch.setenv("AVM_RECAP_VISION_MODEL", override)
    priority = routing.visual_observer_priority_ids(get_model("gpt-6.1-sol"))
    assert priority[0] == first and priority[-1] == "gemini-3.5-flash-lite"
    assert len(priority) == len(set(priority)) == 5
    assert all(get_model(mid).capabilities.image_input for mid in priority)


def test_all_configured_observers_follow_the_declared_tiers(monkeypatch):
    for name in ("GEMINI_API_KEY", "ALIBABA_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(name, "synthetic-application-key")
    routes = routing.configured_visual_observer_routes()
    assert [route.canonical_model_id for route in routes] == list(VISUAL_OBSERVER_PRIORITY)
    assert [route.provider for route in routes] == ["gemini", "gemini", "alibaba", "openrouter", "gemini"]
    assert all(route.input_modality == "image" for route in routes)


def test_qwen_alone_makes_two_stage_pro_locally_eligible_without_gemini(monkeypatch):
    monkeypatch.setenv("AGENTROUTER_API_KEY", "synthetic-storyteller-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-observer-key")
    assert routing.configured_visual_observer(get_model("gpt-6.1-sol")).id == "qwen2.5-vl-72b-instruct"
    assert routing.assess_model_for_job("gpt-6.1-sol", JOB_SOURCE_VIDEO_RECAP).eligible


def test_runtime_attempts_full_flash_then_qwen_then_lite(monkeypatch):
    monkeypatch.setenv("AVM_RECAP_VISION_MODEL", "gemini-3.5-flash-lite")
    calls = []
    routes = [
        ("gemini-3-flash-preview", make_observer("gemini", "gemini-3-flash-preview", failure=LLMRateLimitError("gemini"), calls=calls)),
        ("gemini-2.5-flash", make_observer("gemini", "gemini-2.5-flash", failure=LLMRateLimitError("gemini"), calls=calls)),
        ("qwen-vl-max", make_observer("alibaba", "qwen-vl-max", failure=LLMRateLimitError("alibaba"), calls=calls)),
        ("qwen2.5-vl-72b-instruct", make_observer("openrouter", "qwen/qwen2.5-vl-72b-instruct", failure=LLMRateLimitError("openrouter"), calls=calls)),
        ("gemini-3.5-flash-lite", make_observer("gemini", "gemini-3.5-flash-lite", calls=calls)),
    ]
    install_observers(monkeypatch, routes)
    supplied = frames(0, 300, 0)[:4]
    notes = observe_with_vision_failover(routes[-1][1], supplied, [], start=0, end=300, continuity=[], deadline=time.monotonic() + 10)
    assert calls == [(observer.name, observer.model) for _, observer in routes]
    assert all(note["observer_model"] == "gemini-3.5-flash-lite" for note in notes)


def test_rate_limited_flagship_is_skipped_for_later_chapters(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    routing.record_quota_blocker("gemini", "gemini-3-flash-preview", "synthetic rate limit")
    assert routing.configured_visual_observer(get_model("gpt-6.1-sol")).id == "gemini-2.5-flash"
    assert "gemini-3-flash-preview" not in [route.canonical_model_id for route in routing.configured_visual_observer_routes()]


def test_qwen_wire_identity_is_projected_as_its_exact_canonical_model(monkeypatch):
    observer = make_observer("openrouter", "qwen/qwen2.5-vl-72b-instruct")
    install_observers(monkeypatch, [("qwen2.5-vl-72b-instruct", observer)])
    notes = observe_with_vision_failover(observer, frames(0, 300, 0)[:4], [], start=0, end=300,
                                        continuity=[], deadline=time.monotonic() + 10)
    attribution = visual_observer_attribution(notes)
    assert attribution["visual_observer_model_id"] == "qwen2.5-vl-72b-instruct"
    assert attribution["visual_observer_provider"] == "openrouter"
    assert all(note["observer_model"] == "qwen/qwen2.5-vl-72b-instruct" for note in notes)


def test_cross_observer_batch_reuse_retains_mixed_provenance(monkeypatch):
    monkeypatch.setattr("app.production.visual_observation.time.sleep", lambda _: None)
    supplied = frames(0, 300, 0)[:8]
    full = make_observer("gemini", "gemini-3-flash-preview")
    original = full.generate_multimodal
    def interrupted(*args, **kwargs):
        if kwargs["images"][0][0] == supplied[4][0]:
            raise LLMRateLimitError("gemini")
        return original(*args, **kwargs)
    full.generate_multimodal = interrupted
    qwen = make_observer("openrouter", "qwen/qwen2.5-vl-72b-instruct")
    install_observers(monkeypatch, [("gemini-3-flash-preview", full), ("qwen2.5-vl-72b-instruct", qwen)])
    notes = observe_with_vision_failover(full, supplied, [], start=0, end=300, continuity=[], deadline=time.monotonic() + 10)
    assert len(full.calls) == len(qwen.calls) == 1  # Four accepted frames are not reobserved.
    assert [note["observer_provider"] for note in notes] == ["gemini"] * 4 + ["openrouter"] * 4
    metadata = visual_observer_attribution(notes)
    assert metadata["visual_observer_model_id"] is None and metadata["visual_observer_provider"] is None
    assert metadata["visual_observer_models"] == ["gemini-3-flash-preview", "qwen2.5-vl-72b-instruct"]


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:8318/v1", "https://agentrouter.org/v1", "https://sub.agentrouter.org/v1"])
def test_no_vision_payload_can_be_sent_to_the_cline_proxy_or_agentrouter(monkeypatch, endpoint):
    observer = make_observer("openrouter", "qwen/qwen2.5-vl-72b-instruct")
    observer._base_url = endpoint
    post = Mock()
    monkeypatch.setattr("requests.post", post)
    assert not admitted_visual_observer(observer)
    with pytest.raises(ValueError, match="text gateways"):
        observe_with_vision_failover(observer, frames(0, 300, 0)[:4], [], start=0, end=300,
                                     continuity=[], deadline=time.monotonic() + 10)
    assert not observer.calls and not post.called


def test_deadline_failure_is_bounded_and_does_not_call_any_observer(monkeypatch):
    observer = make_observer("gemini", "gemini-3-flash-preview")
    install_observers(monkeypatch, [("gemini-3-flash-preview", observer)])
    with pytest.raises(LLMConnectionError, match="deadline"):
        observe_with_vision_failover(observer, frames(0, 300, 0)[:4], [], start=0, end=300,
                                     continuity=[], deadline=time.monotonic() - 1)
    assert not observer.calls


@pytest.mark.parametrize("notes", [[], [{"observer_model": None, "observer_provider": None}],
    [{"observer_model": "gemini-3-flash-preview", "observer_provider": "gemini"}, {}]])
def test_unknown_or_partial_provenance_never_inherits_current_observer_config(notes):
    assert visual_observer_attribution(notes)["visual_observer_model_id"] is None
    assert visual_observer_attribution(notes)["visual_observer_provider"] is None


def test_chapter_and_saved_cache_attribution_use_the_observer_that_succeeded(monkeypatch, tmp_path):
    asset, transcript = story_evidence()
    primary = make_observer("gemini", "gemini-3-flash-preview", failure=LLMRateLimitError("gemini"))
    qwen = make_observer("openrouter", "qwen/qwen2.5-vl-72b-instruct")
    install_observers(monkeypatch, [("gemini-3-flash-preview", primary), ("qwen2.5-vl-72b-instruct", qwen)])
    checkpoint = tmp_path / "chapters.partial.json"
    result = build_chapter_plan("prd_observer_identity", "unit-001", asset, transcript,
        budget=resolve_recap_budget(asset.duration), prov=ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol"),
        visual_observer=primary, frame_loader=frames, checkpoint_path=checkpoint)
    assert result.visual_observer_model_id == "qwen2.5-vl-72b-instruct"
    assert result.visual_observer_provider == "openrouter"
    assert all(chapter.visual_observer_model_id == "qwen2.5-vl-72b-instruct" for chapter in result.chapters)
    accepted_calls = len(qwen.calls)
    replacement = make_observer("gemini", "gemini-3.5-flash-lite", failure=AssertionError("Cached evidence must not call a replacement"))
    again = build_chapter_plan("prd_observer_identity", "unit-001", asset, transcript,
        budget=resolve_recap_budget(asset.duration), prov=ChapterNarrationProvider(name="agentrouter", model="gpt-6-astra"),
        visual_observer=replacement, frame_loader=frames, checkpoint_path=checkpoint)
    assert again.live_vision_calls == 0 and again.stage1_cache_hits == len(again.chapters)
    assert again.visual_observer_model_id == result.visual_observer_model_id and len(qwen.calls) == accepted_calls
    assert all(chapter.visual_observation_source == "cache" for chapter in again.chapters)
    assert "gemini-3.5-flash-lite" not in json.dumps([c.model_dump() for c in again.chapters])


def test_pro_auto_never_selects_lite_before_eligible_full_size_observers(monkeypatch):
    from app.production.narration import pro_auto_model_ids
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-observer-key")
    candidates = pro_auto_model_ids()
    assert candidates.index("gemini-3-flash-preview") < candidates.index("gemini-3.5-flash-lite")
    assert candidates.index("gemini-2.5-flash") < candidates.index("gemini-3.5-flash-lite")