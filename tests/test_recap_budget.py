"""Pure long-form duration policy: no filesystem, credentials or providers."""
import math

import pytest
from pydantic import ValidationError

from app.production.recap_budget import (
    MAX_RECAP_SECONDS, PRESET_BUDGETS, RECAP_PRESETS, RecapBudget,
    normalize_duration_override, normalize_length_preset, resolve_recap_budget,
    requires_chapter_planning, chapter_planning_timeout_seconds,
)


@pytest.mark.parametrize("preset", PRESET_BUDGETS)
def test_all_presets_have_complete_runtime_beat_word_and_chapter_contracts(preset):
    target, beats, words, runtime, chapters = PRESET_BUDGETS[preset]
    budget = resolve_recap_budget(14400, target_length_preset=preset)
    assert budget.preset == preset
    assert budget.target_duration_seconds == target
    assert (budget.min_beats, budget.max_beats) == beats
    assert (budget.min_narration_words, budget.max_narration_words) == words
    assert (budget.min_duration_seconds, budget.max_duration_seconds) == runtime
    assert (budget.min_chapters, budget.max_chapters) == chapters
    assert not budget.source_ratio_limited
    assert RecapBudget.model_validate_json(budget.model_dump_json()) == budget


@pytest.mark.parametrize("source,beats,words,runtime,chapters", [
    (120, (4, 6), (100, 150), (45, 60), (1, 1)),
    (179.99, (4, 6), (100, 150), (45, 60), (1, 1)),
    (180, (8, 12), (180, 270), (60, 90), (1, 1)),
    (599.99, (8, 12), (180, 270), (60, 90), (1, 1)),
    (600, (16, 26), (450, 750), (180, 270), (1, 3)),
    (1499.99, (16, 26), (450, 750), (180, 270), (1, 3)),
    (1500, (45, 54), (1400, 1680), (480, 600), (4, 6)),
    (1800, (45, 54), (1400, 1680), (480, 600), (4, 6)),
    (3299.99, (45, 54), (1400, 1680), (480, 600), (4, 6)),
    (3300, (65, 80), (2050, 2550), (720, 900), (7, 8)),
    (5400, (65, 80), (2050, 2550), (720, 900), (7, 8)),
    (6299.99, (65, 80), (2050, 2550), (720, 900), (7, 8)),
    (6300, (95, 120), (3050, 3750), (1080, 1320), (10, 12)),
    (9000, (95, 120), (3050, 3750), (1080, 1320), (10, 12)),
    (10200, (95, 120), (3050, 3750), (1080, 1320), (10, 12)),
    (10200.01, (135, 170), (4250, 5400), (1500, 1920), (14, 16)),
    (14400, (135, 170), (4250, 5400), (1500, 1920), (14, 16)),
])
def test_auto_movie_boundaries(source, beats, words, runtime, chapters):
    budget = resolve_recap_budget(source)
    assert (budget.min_beats, budget.max_beats) == beats
    assert (budget.min_narration_words, budget.max_narration_words) == words
    assert (budget.min_duration_seconds, budget.max_duration_seconds) == runtime
    assert (budget.min_chapters, budget.max_chapters) == chapters


@pytest.mark.parametrize("minutes", [0.5, 1, 7.25, 12, 15, 20, 25, 30, 32, 35])
def test_custom_minutes_and_seconds_are_equivalent_and_scale_past_old_caps(minutes):
    seconds = minutes * 60
    budget = resolve_recap_budget(14400, target_length_preset="custom", target_minutes=minutes)
    assert budget == resolve_recap_budget(14400, target_duration_seconds=seconds)
    assert budget.target_duration_seconds == seconds
    assert budget.requested_duration_seconds == seconds
    assert budget.max_duration_seconds is None or budget.max_duration_seconds <= MAX_RECAP_SECONDS
    if minutes >= 15:
        assert budget.min_narration_words > 2000 and budget.max_beats > 54
        assert requires_chapter_planning(14400, budget)
    if minutes == 35:
        assert budget.max_narration_words >= 5900 and budget.max_beats > 200
    if minutes > .5:
        # Neither accepted extreme may create an impossible runtime/cadence.
        for words in (budget.min_narration_words, budget.max_narration_words):
            assert max(budget.min_duration_seconds, words * 60 / 180) <= min(budget.max_duration_seconds, words * 60 / 155)


@pytest.mark.parametrize("preset", ["feature", "blockbuster", "epic", "auto"])
@pytest.mark.parametrize("source", [75, 120, 600, 1200, 1800])
def test_long_preset_scales_every_range_without_exceeding_source_ceiling(preset, source):
    budget = resolve_recap_budget(source, target_length_preset=preset)
    assert budget.target_duration_seconds <= source * .75
    assert budget.max_duration_seconds <= source * .75
    assert budget.min_duration_seconds <= budget.target_duration_seconds <= budget.max_duration_seconds
    assert budget.min_beats <= budget.max_beats and budget.min_chapters <= budget.max_chapters
    if preset == "epic" and source == 1800:
        assert budget.source_ratio_limited
        assert budget.target_duration_seconds == 1350
        assert budget.min_narration_words == math.floor(4250 * .75)


def test_custom_source_ceiling_and_tiny_fixture_exemption_are_explicit():
    budget = resolve_recap_budget(1200, target_minutes=30)
    assert budget.target_duration_seconds == budget.max_duration_seconds == 900
    assert budget.requested_duration_seconds == 1800 and budget.source_ratio_limited
    assert resolve_recap_budget(8, target_duration_seconds=10).target_duration_seconds == 10
    assert resolve_recap_budget(12).target_duration_seconds == pytest.approx(7.2)
    for preset in RECAP_PRESETS:
        budget = resolve_recap_budget(14400, target_length_preset=preset,
                                      target_minutes=35 if preset == "custom" else None, mock_mode=True)
        assert budget.target_duration_seconds <= 30 and not budget.enforce_depth


@pytest.mark.parametrize("value", [True, False, 0, -1, "NaN", math.inf, "invalid", 2100.01])
def test_invalid_seconds_fail_even_before_sandbox(value):
    with pytest.raises(ValueError):
        resolve_recap_budget(14400, target_duration_seconds=value, mock_mode=True)


@pytest.mark.parametrize("value", [True, 0, -1, math.nan, math.inf, "bad", 35.01])
def test_invalid_minutes_fail_without_providers(value):
    with pytest.raises(ValueError):
        normalize_duration_override(target_minutes=value)


def test_seconds_precedence_custom_requires_target_and_chapter_model_limits():
    assert normalize_duration_override(900, 30) == 900
    assert normalize_length_preset(" EPIC ") == "epic"
    with pytest.raises(ValueError, match="requires"):
        resolve_recap_budget(5400, target_length_preset="custom")
    budget = resolve_recap_budget(14400, target_length_preset="epic")
    with pytest.raises(ValidationError):
        RecapBudget.model_validate({**budget.model_dump(), "max_beats": 221})
    with pytest.raises(ValidationError):
        RecapBudget.model_validate({**budget.model_dump(), "min_chapters": 17})
    assert chapter_planning_timeout_seconds(budget) == 300
    assert chapter_planning_timeout_seconds(budget, composite=True) == 300


def test_timeout_helper_is_an_explicit_shared_module_level_contract():
    from app.production import recap_budget, narration, narration_chapters, narration_real, narration_pro
    from app.web import server

    assert "chapter_planning_timeout_seconds" in recap_budget.__all__
    for module in (narration, narration_chapters, server):
        assert module.chapter_planning_timeout_seconds is chapter_planning_timeout_seconds
    for module in (narration_real, narration_pro):
        assert module.requires_chapter_planning is requires_chapter_planning
    assert chapter_planning_timeout_seconds(None) == 300
    assert chapter_planning_timeout_seconds(None, composite=True) == 300
    movie = resolve_recap_budget(3742.174331)
    assert movie.target_duration_seconds == 810
    assert chapter_planning_timeout_seconds(movie) == 300
    assert chapter_planning_timeout_seconds(movie, composite=True) == 300