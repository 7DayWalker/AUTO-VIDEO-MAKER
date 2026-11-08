"""A truncated Pro chapter never buys an unbounded third call."""
import json

import pytest

from app.content.exceptions import LLMOutputTruncatedError
from app.production.narration_chapters import _chapter_budget, chapter_windows
from app.production.narration_storyteller import write_storyteller_chapter
from app.production.recap_budget import resolve_recap_budget
from tests.track9_narration_fakes import ChapterNarrationProvider, story_evidence
from tests.test_agentrouter_storyteller import Observer, frames
from app.production.visual_observation import observe_chapter_frames


def test_truncation_saves_two_part_shape_for_next_explicit_resume():
    asset, transcript = story_evidence()
    start, end, rows = chapter_windows(asset.duration, transcript)[0]
    supplied = frames(start, end, 0)
    local = _chapter_budget(resolve_recap_budget(asset.duration), 0, 5)
    notes = observe_chapter_frames(Observer(), supplied, rows, start=start, end=end, continuity=[])
    payload = {"chapter_index": 1, "chapter_count": 5, "start_seconds": start, "end_seconds": end,
        "transcript_segments": rows, "visual_action_notes": notes,
        "frames": [{"id": i, "timestamp_seconds": ts} for i, (ts, _) in enumerate(supplied)],
        "prior_chapter_summaries": [], "previous_narration_tail": "", "preferred_word_count": 308}
    state = {}
    failing = ChapterNarrationProvider(error=LLMOutputTruncatedError())
    with pytest.raises(LLMOutputTruncatedError):
        write_storyteller_chapter(failing, "Grounded recap", payload, asset=asset, frames=supplied,
            local_budget=local, deadline=-1, call_state=state, request_id="first")
    assert state["calls"] == 2 and state["split"] is True and len(failing.calls) == 2
    good = ChapterNarrationProvider()
    result, count = write_storyteller_chapter(good, "Grounded recap", payload, asset=asset, frames=supplied,
        local_budget=local, deadline=-1, call_state=state, request_id="new-resume")
    assert count == len(good.calls) == 2 and result["beats"]