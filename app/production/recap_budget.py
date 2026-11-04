"""Pure duration/length policy shared by real Standard and Pro narration.

These are narration targets, not claims about measured TTS length. Explicit
seconds win over a preset; tiny sources must not be expanded into invented films.
"""
from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RECAP_PRESETS = ("auto", "short", "story", "deep", "extended")
STORY_SECTIONS = ("setup", "inciting_incident", "rising_conflict", "climax", "resolution")


class RecapBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    preset: Literal["auto", "short", "story", "deep", "extended", "custom"]
    target_duration_seconds: float = Field(gt=0)
    min_duration_seconds: float | None = Field(default=None, gt=0)
    max_duration_seconds: float | None = Field(default=None, gt=0)
    min_beats: int = Field(ge=1)
    max_beats: int = Field(ge=1, le=54)
    min_narration_words: int = Field(ge=0)
    max_narration_words: int = Field(ge=1)
    min_words_per_beat: int = Field(default=16, ge=0)
    require_story_coverage: bool = True

    @model_validator(mode="after")
    def _validate_budget(self):
        if (not math.isfinite(self.target_duration_seconds) or self.min_beats > self.max_beats
                or self.min_narration_words > self.max_narration_words):
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
                                       "min_narration_words": minimum * 20,
                                       "max_narration_words": 180})


def normalize_length_preset(value):
    if not isinstance(value, str) or value.strip().lower() not in RECAP_PRESETS:
        raise ValueError("target_length_preset must be auto, short, story, deep or extended")
    return value.strip().lower()


def resolve_recap_budget(source_duration, *, target_length_preset="auto", target_duration_seconds=None, mock_mode=False):
    preset = normalize_length_preset(target_length_preset)
    source = float(source_duration)
    if not math.isfinite(source) or source <= 0:
        raise ValueError("source duration must be finite and positive")
    if target_duration_seconds is not None:
        if isinstance(target_duration_seconds, bool):
            raise ValueError("target_duration_seconds must be a finite positive number")
        try:
            duration = float(target_duration_seconds)
        except (TypeError, ValueError):
            raise ValueError("target_duration_seconds must be a finite positive number") from None
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("target_duration_seconds must be a finite positive number")
        if duration > source * 2.5:
            raise ValueError("target_duration_seconds is implausible for the source asset duration")
        # Preserve deliberately brief custom recaps (including legacy callers).
        if duration <= 30 or mock_mode:
            return RecapBudget(preset="custom", target_duration_seconds=duration, min_beats=1,
                               max_beats=min(12, max(1, math.ceil(duration / 8))), min_narration_words=0,
                               max_narration_words=max(1, math.ceil(duration * 3)),
                               min_words_per_beat=0, require_story_coverage=False)
        words = min(1500, math.ceil(duration * 2.5))
        return RecapBudget(preset="custom", target_duration_seconds=duration,
                           min_beats=min(45, max(4, math.floor(words / 30))),
                           max_beats=min(54, max(6, math.ceil(words / 20))),
                           min_narration_words=math.floor(words * 0.85), max_narration_words=math.ceil(words * 1.15))

    # Explicit sandbox and sub-minute clips stay fast and source-proportionate.
    if mock_mode or source < 75:
        duration = min(30 if mock_mode else 45, source * 0.6)
        return RecapBudget(preset=preset, target_duration_seconds=duration, min_beats=1, max_beats=6,
                           min_narration_words=0, max_narration_words=max(1, math.ceil(duration * 3)),
                           min_words_per_beat=0, require_story_coverage=False)
    duration_bounds = (None, None)
    if preset == "short":
        values = (75, 8, 10, 150, 210)
    elif preset == "story":
        values = (195, 15, 22, 350, 520)
    elif preset == "deep":
        values = (390, 28, 40, 750, 1150)
        duration_bounds = (300, 480)
    elif preset == "extended":
        values = (540, 40, 54, 1150, 1500)
        duration_bounds = (480, 600)
    elif source < 180:
        values = (min(60, source * 0.6), 4, 6, 100, 150)
    elif source < 600:
        values = (100, 8, 12, 180, 270)
    elif source < 900:
        values = (180, 14, 22, 320, 500)
    elif source < 1500:
        values = (390, 28, 40, 750, 1150)
        duration_bounds = (300, 480)
    else:
        values = (540, 40, 54, 1150, 1500)
        duration_bounds = (480, 600)
    duration, min_beats, max_beats, min_words, max_words = values
    if duration > source * 0.6:
        # A requested long preset cannot manufacture missing source material.
        scale = source * 0.6 / duration
        duration = source * 0.6
        min_beats = max(4, math.floor(min_beats * scale))
        max_beats = max(min_beats, math.ceil(max_beats * scale))
        min_words = max(min_beats * 16, math.floor(min_words * scale))
        max_words = max(min_words, math.ceil(max_words * scale))
        duration_bounds = tuple(value * scale if value is not None else None for value in duration_bounds)
    return RecapBudget(preset=preset, target_duration_seconds=duration,
                       min_duration_seconds=duration_bounds[0], max_duration_seconds=duration_bounds[1],
                       min_beats=min_beats, max_beats=max_beats,
                       min_narration_words=min_words, max_narration_words=max_words)


def requires_chapter_planning(source_duration, budget):
    """Length is a coverage contract, not a request for one enormous completion."""
    return (source_duration >= 900 and budget.enforce_depth and
            (budget.preset in {"deep", "extended"} or budget.min_beats >= 28
             or budget.min_narration_words >= 700))


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
        if beat_words < budget.min_words_per_beat or not 2 <= len(sentences) <= 3:
            raise ValueError(f"Beat {beat.order} has {len(sentences)} sentences and {beat_words} words; each beat needs 2-3 sentences, normally 20-30 words, not one-line blurbs")
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