"""Pure duration/length policy shared by real Standard and Pro narration.

These are narration targets, not claims about measured TTS length. Explicit
seconds win over a preset; tiny sources must not be expanded into invented films.
"""
from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RECAP_PRESETS = ("auto", "short", "story", "deep", "extended", "feature", "blockbuster", "epic", "custom")
RecapLengthPreset = Literal["auto", "short", "story", "deep", "extended", "feature", "blockbuster", "epic", "custom"]
MAX_RECAP_SECONDS = 2100
MAX_RECAP_BEATS = 220
SOURCE_RATIO_CEILING = 0.75
# Target seconds, beat range, word range, spoken-runtime range, chapter range.
PRESET_BUDGETS = {
    "short": (75, (8, 10), (150, 210), (60, 90), (1, 1)),
    "story": (195, (15, 22), (350, 520), (180, 240), (1, 1)),
    "deep": (390, (28, 40), (750, 1150), (300, 420), (3, 6)),
    "extended": (540, (45, 54), (1400, 1680), (480, 600), (4, 6)),
    "feature": (810, (65, 80), (2050, 2550), (720, 900), (7, 8)),
    "blockbuster": (1200, (95, 120), (3050, 3750), (1080, 1320), (10, 12)),
    "epic": (1800, (135, 170), (4250, 5400), (1500, 1920), (14, 16)),
}
STORY_SECTIONS = ("setup", "inciting_incident", "rising_conflict", "climax", "resolution")

# This module is deliberately dependency-light. Import its public contract at
# module load, not halfway through a live movie build after a code deployment.
__all__ = [
    "RECAP_PRESETS", "RecapLengthPreset", "MAX_RECAP_SECONDS", "MAX_RECAP_BEATS",
    "SOURCE_RATIO_CEILING", "PRESET_BUDGETS", "STORY_SECTIONS", "RecapBudget",
    "normalize_length_preset", "normalize_duration_override", "resolve_recap_budget",
    "requires_chapter_planning", "chapter_planning_timeout_seconds",
    "timeline_sections", "validate_recap_depth",
]


class RecapBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    preset: RecapLengthPreset
    target_duration_seconds: float = Field(gt=0, le=MAX_RECAP_SECONDS)
    requested_duration_seconds: float | None = Field(default=None, gt=0, le=MAX_RECAP_SECONDS)
    source_ratio_limited: bool = False
    min_duration_seconds: float | None = Field(default=None, gt=0)
    max_duration_seconds: float | None = Field(default=None, gt=0)
    min_beats: int = Field(ge=1)
    max_beats: int = Field(ge=1, le=MAX_RECAP_BEATS)
    min_chapters: int = Field(default=1, ge=1, le=40)
    max_chapters: int = Field(default=12, ge=1, le=40)
    min_narration_words: int = Field(ge=0)
    max_narration_words: int = Field(ge=1)
    min_words_per_beat: int = Field(default=16, ge=0)
    require_story_coverage: bool = True

    @model_validator(mode="after")
    def _validate_budget(self):
        if (not math.isfinite(self.target_duration_seconds) or self.min_beats > self.max_beats
                or self.min_narration_words > self.max_narration_words
                or self.min_chapters > self.max_chapters):
            raise ValueError("Recap duration and beat/word ranges must be finite and ordered")
        if (self.min_duration_seconds is None) != (self.max_duration_seconds is None):
            raise ValueError("Recap duration bounds must be supplied together")
        if self.min_duration_seconds is not None and not (
            math.isfinite(self.max_duration_seconds)
            and self.min_duration_seconds <= self.target_duration_seconds <= self.max_duration_seconds
        ):
            raise ValueError("Recap target must lie inside its finite duration bounds")
        return self

    @property
    def enforce_depth(self):
        return self.min_narration_words > 0

    def window_budget(self, segment_count):
        """Intermediate notes retain detail but don't repeat the full film budget."""
        if not self.enforce_depth:
            return self
        minimum = min(3, segment_count)
        return self.model_copy(update={"min_beats": minimum, "max_beats": 6,
                                       "min_chapters": 1, "max_chapters": 1,
                                       "min_narration_words": minimum * 20,
                                       "max_narration_words": 180})


def normalize_length_preset(value):
    if not isinstance(value, str) or value.strip().lower() not in RECAP_PRESETS:
        raise ValueError("target_length_preset must be " + ", ".join(RECAP_PRESETS))
    return value.strip().lower()


def normalize_duration_override(target_duration_seconds=None, target_minutes=None):
    """Validate both units; explicit seconds take precedence over minutes."""
    values = []
    for name, raw, multiplier in (("target_duration_seconds", target_duration_seconds, 1),
                                  ("target_minutes", target_minutes, 60)):
        if raw is None:
            values.append(None)
            continue
        try:
            if isinstance(raw, bool):
                raise ValueError
            value = float(raw) * multiplier
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{name} must be a finite positive number") from None
        if not math.isfinite(value) or not 0 < value <= MAX_RECAP_SECONDS:
            raise ValueError(f"{name} must be positive and at most 35 minutes (2100 seconds)")
        values.append(value)
    return values[0] if values[0] is not None else values[1]


def _custom_chapters(duration):
    for threshold, chapters in ((1500, (14, 16)), (1080, (10, 12)), (720, (7, 8)),
                                (480, (4, 6)), (300, (3, 6))):
        if duration >= threshold:
            return chapters
    return (1, 1)


def _brief_budget(preset, duration, *, requested=None, limited=False):
    return RecapBudget(preset=preset, target_duration_seconds=duration,
                       requested_duration_seconds=requested, source_ratio_limited=limited,
                       min_beats=1, max_beats=min(12, max(1, math.ceil(duration / 8))),
                       min_narration_words=0, max_narration_words=max(1, math.ceil(duration * 3)),
                       min_words_per_beat=0, min_chapters=1, max_chapters=1,
                       require_story_coverage=False)


def resolve_recap_budget(source_duration, *, target_length_preset="auto", target_duration_seconds=None,
                         target_minutes=None, mock_mode=False):
    preset = normalize_length_preset(target_length_preset)
    if isinstance(source_duration, bool):
        raise ValueError("source duration must be finite and positive")
    source = float(source_duration)
    if not math.isfinite(source) or source <= 0:
        raise ValueError("source duration must be finite and positive")
    requested = normalize_duration_override(target_duration_seconds, target_minutes)
    if preset == "custom" and requested is None:
        raise ValueError("Custom recap requires target_minutes or target_duration_seconds")
    # Sandbox requests never synthesize a movie-sized preview. Tiny, explicitly
    # timed fixtures retain the legacy brief-test exemption, not production policy.
    if mock_mode:
        return _brief_budget("custom" if requested is not None else preset,
                             min(requested or 30, 30, source * .6), requested=requested)
    if requested is not None:
        if source < 75 and requested > source * 2.5:
            raise ValueError("target_duration_seconds is implausible for the source asset duration")
        ceiling = source * SOURCE_RATIO_CEILING
        duration = requested if source < 75 and requested <= 30 else min(requested, ceiling)
        if duration <= 30:
            return _brief_budget("custom", duration, requested=requested, limited=duration < requested)
        words = math.ceil(duration * 170 / 60)
        chapters = _custom_chapters(duration)
        minimum_seconds = duration * .95
        maximum_seconds = min(MAX_RECAP_SECONDS, ceiling, duration * 1.05)
        # Every accepted custom word count must intersect the natural 155-180
        # WPM pacing range; a broad +/-15% range admitted impossible scripts.
        minimum_words = math.ceil(max(words * .90, minimum_seconds * 155 / 60))
        maximum_words = math.floor(min(words * 1.10, maximum_seconds * 180 / 60))
        return RecapBudget(preset="custom", target_duration_seconds=duration,
                           requested_duration_seconds=requested, source_ratio_limited=duration < requested,
                           min_duration_seconds=minimum_seconds, max_duration_seconds=maximum_seconds,
                           min_beats=min(MAX_RECAP_BEATS, max(4, math.floor(words / 32))),
                           max_beats=min(MAX_RECAP_BEATS, max(6, math.ceil(words / 26))),
                           min_chapters=chapters[0], max_chapters=chapters[1],
                           min_narration_words=minimum_words, max_narration_words=maximum_words)
    if source < 75:
        return _brief_budget(preset, min(45, source * .6))
    if preset != "auto":
        values = PRESET_BUDGETS[preset]
    elif source < 180:
        values = (60, (4, 6), (100, 150), (45, 60), (1, 1))
    elif source < 600:
        values = (90, (8, 12), (180, 270), (60, 90), (1, 1))
    elif source < 1500:
        values = (210, (16, 26), (450, 750), (180, 270), (1, 3))
    elif source < 3300:
        values = PRESET_BUDGETS["extended"]
    elif source < 6300:
        values = PRESET_BUDGETS["feature"]
    elif source <= 10200:
        values = PRESET_BUDGETS["blockbuster"]
    else:
        values = PRESET_BUDGETS["epic"]
    duration, (min_beats, max_beats), (min_words, max_words), duration_bounds, chapters = values
    ceiling = source * SOURCE_RATIO_CEILING
    limited = duration > ceiling
    if limited:
        # A requested long preset cannot manufacture missing source material.
        scale = ceiling / duration
        duration = ceiling
        min_beats = max(4, math.floor(min_beats * scale))
        max_beats = max(min_beats, math.ceil(max_beats * scale))
        min_words = max(min_beats * 16, math.floor(min_words * scale))
        max_words = max(min_words, math.ceil(max_words * scale))
        duration_bounds = tuple(value * scale for value in duration_bounds)
        chapters = (max(1, math.floor(chapters[0] * scale)), max(1, math.ceil(chapters[1] * scale)))
    return RecapBudget(preset=preset, target_duration_seconds=duration,
                       source_ratio_limited=limited,
                       min_duration_seconds=min(duration, duration_bounds[0]),
                       max_duration_seconds=min(ceiling, duration_bounds[1]),
                       min_beats=min_beats, max_beats=max_beats,
                       min_chapters=chapters[0], max_chapters=chapters[1],
                       min_narration_words=min_words, max_narration_words=max_words)


def requires_chapter_planning(source_duration, budget):
    """Length is a coverage contract, not a request for one enormous completion."""
    return (source_duration >= 900 and budget.enforce_depth and
            (budget.preset in {"deep", "extended", "feature", "blockbuster", "epic"} or budget.min_beats >= 28
             or budget.min_narration_words >= 700))


def chapter_planning_timeout_seconds(budget: RecapBudget | None, *, composite: bool = False) -> int:
    """Independent socket budget for EVERY chapter call, never a job deadline.

    Keep the public signature for callers/runtime diagnostics. A sixteen-chapter
    movie may legitimately run for hours; previous chapters cannot consume the
    next chapter's time. Durability and single-flight, not a global stopwatch,
    bound recovery work.
    """
    return 300


def timeline_sections(transcript, source_duration, allowed_ids=None):
    """Five temporal bands of actual supplied evidence, not invented events."""
    allowed = sorted(allowed_ids if allowed_ids is not None else range(len(transcript.segments)))
    sections = []
    for index, label in enumerate(STORY_SECTIONS):
        ids = [tid for tid in allowed if min(4, int(transcript.segments[tid].start / source_duration * 5)) == index]
        if ids:
            sections.append({"section": label, "start_seconds": round(source_duration * index / 5, 3),
                             "end_seconds": round(source_duration * (index + 1) / 5, 3),
                             "transcript_segment_ids": ids})
    return sections


def validate_recap_depth(artifact, budget, transcript, source_duration, allowed_ids=None):
    """Return fixed, safe repair diagnostics; never include generated/source text."""
    count = len(artifact.beats)
    words = len(artifact.narration_text.split())
    if not budget.min_beats <= count <= budget.max_beats:
        raise ValueError(f"Recap requires {budget.min_beats}-{budget.max_beats} beats; received {count}")
    if not budget.min_narration_words <= words <= budget.max_narration_words:
        raise ValueError(f"Recap requires {budget.min_narration_words}-{budget.max_narration_words} words; received {words}")
    if not budget.enforce_depth:
        return
    for beat in artifact.beats:
        sentences = [part for part in re.split(r"[.!?。！？]+(?:[\"'’”]*\s+|$)", beat.narration_text.strip()) if part.strip()]
        beat_words = len(beat.narration_text.split())
        if beat_words < budget.min_words_per_beat or not 2 <= len(sentences) <= 4:
            raise ValueError(f"Beat {beat.order} has {len(sentences)} sentences and {beat_words} words; each beat needs 2-4 varied sentences, normally 26-36 words for extended recaps, not one-line blurbs")
    if not budget.require_story_coverage:
        return
    beat_ids = [set(index for ref in beat.source_refs for index in ref.transcript_segment_ids) for beat in artifact.beats]
    starts = [min(ref.start_seconds for ref in beat.source_refs) for beat in artifact.beats]
    if starts != sorted(starts):
        raise ValueError("Recap beats must tell the story chronologically from opening to ending")
    for section in timeline_sections(transcript, source_duration, allowed_ids):
        ids = set(section["transcript_segment_ids"])
        minimum = 2 if budget.min_beats >= 14 and len(ids) >= 2 else 1
        if sum(bool(ids & refs) or any(
            ref.visual_frame_timestamps and ref.start_seconds < section["end_seconds"]
            and ref.end_seconds > section["start_seconds"] for ref in beat.source_refs
        ) for beat, refs in zip(artifact.beats, beat_ids)) < minimum:
            raise ValueError(f"Recap needs at least {minimum} beats from timeline section {section['section']}")
    available = sorted(allowed_ids if allowed_ids is not None else range(len(transcript.segments)))
    first = min(transcript.segments[index].start for index in available)
    last = max(transcript.segments[index].end for index in available)
    if (min(ref.start_seconds for ref in artifact.source_refs) > first + (last - first) * 0.1
            or max(ref.end_seconds for ref in artifact.source_refs) < last - (last - first) * 0.1):
        raise ValueError("Recap must include opening and ending evidence within the first and last 10 percent of the supplied timeline")