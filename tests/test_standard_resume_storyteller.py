"""Backend-only explicit resume, compact Standard and paid-output retention."""
import json
from types import SimpleNamespace

import pytest

from app.content.exceptions import LLMConnectionError
from app.production.narration_chapters import build_chapter_plan, normalize_standard_chapter, _chapter_beats
from app.production.narration_real import NarrationModelOutputError, narration_request
from app.production.recap_budget import resolve_recap_budget
from tests.track9_narration_fakes import ChapterNarrationProvider, story_evidence, chapter_prompt_evidence


def _compact_build(tmp_path, provider, *, asset=None, transcript=None):
    default_asset, default_transcript = story_evidence(duration=3742, count=720)
    asset, transcript = asset or default_asset, transcript or default_transcript
    return build_chapter_plan("prd_resume_fixture", "unit-001", asset, transcript,
        budget=resolve_recap_budget(asset.duration, target_length_preset="feature"), prov=provider,
        context_tokens=128000, checkpoint_path=tmp_path / "narration_chapters.partial.json")


def test_new_post_resets_only_unfinished_chapter_and_never_repeats_saved_work(tmp_path, monkeypatch):
    from app.web import server
    from tests.test_track9_api_routes import _server, _make_prod, _post, PID, UID
    base, httpd, _ = _server(monkeypatch, tmp_path)
    _make_prod(tmp_path)
    monkeypatch.setattr("app.production.narration_storyteller.time.sleep", lambda _: None)
    class Interrupted(ChapterNarrationProvider):
        attempts = 0
        def generate(self, prompt, *args, **kwargs):
            if chapter_prompt_evidence(prompt)["chapter_index"] == 3:
                self.attempts += 1
                raise LLMConnectionError(self.name, "fixture timeout")
            return super().generate(prompt, *args, **kwargs)
    first = Interrupted(name="agentrouter", model="gpt-6.1-sol")
    second = ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol")
    providers = iter([first, second])

    @server._summary_single_flight("narrating")
    @narration_request
    def build(pid, uid, body):
        try:
            artifact = _compact_build(tmp_path, next(providers))
            return 201, {"chapter_count": artifact.chapter_count}
        except LLMConnectionError:
            return 503, {"error": "fixture interrupted"}
    monkeypatch.setattr(server, "narration_build_payload", build)
    path = tmp_path / "narration_chapters.partial.json"
    try:
        route = f"/api/productions/{PID}/units/{UID}/narration"
        assert _post(base, route)[0] == 503
        saved = json.loads(path.read_text())
        assert len(saved["chapters"]) == 2 and first.attempts == 2
        assert saved["pending_story"]["request_state"]["calls"] == 2
        status, result, _ = _post(base, route)
        assert status == 201 and result["chapter_count"] == 8
        assert [row["chapter_index"] for row in second.chapters] == list(range(3, 9))
        assert json.loads(path.read_text())["chapters"][:2] == saved["chapters"]
    finally:
        httpd.shutdown(); httpd.server_close()


def test_nested_retry_in_same_request_cannot_reset_attempt_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("app.production.narration_storyteller.time.sleep", lambda _: None)
    failing = ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol",
        error=LLMConnectionError("agentrouter", "fixture socket failure"))
    good = ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol")
    @narration_request
    def nested():
        _compact_build(tmp_path, good)
    @narration_request
    def invocation():
        with pytest.raises(LLMConnectionError):
            _compact_build(tmp_path, failing)
        with pytest.raises(RuntimeError, match="request.*retry limit"):
            nested()
    invocation()
    assert len(failing.calls) == 2 and not good.calls
    assert _compact_build(tmp_path, good).chapter_count == 8


@pytest.mark.parametrize("words,beats", [(180, 8), (335, 11)])
def test_compact_standard_accepts_editorial_variance_without_spending_a_retry(tmp_path, words, beats):
    def vary(data):
        original = data["beats"]
        # Retain chronology and chapter opening/ending while varying beat count.
        selected = [original[round(i * (len(original) - 1) / (beats - 1))] for i in range(beats)]
        if beats > len(original):
            # Add a distinct dialogue moment next to the first cited moment.
            selected = json.loads(json.dumps(original))
            extra = json.loads(json.dumps(selected[0]))
            extra["source_refs"][0]["transcript_segment_ids"][0] += 1
            selected.insert(1, extra)
        text = ("The traveler faces a difficult choice while pursuing the truth. "
                "Their decision changes the journey and forces the companions to face the danger together "
                "before finding another way to protect the people waiting for them at home.")
        for i, beat in enumerate(selected):
            amount = words * (i + 1) // len(selected) - words * i // len(selected)
            beat["narration_text"] = " ".join(text.split()[:amount]).rstrip(".") + "."
        data["beats"] = selected
    provider = ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol", fault=vary, always_fail=True)
    artifact = _compact_build(tmp_path, provider)
    assert len(provider.calls) == artifact.chapter_count == 8
    assert len(artifact.narration_text.split()) == words * 8
    assert artifact.narration_quality["outside_nominal_target"]
    assert all(call["max_tokens"] == 16384 and call["reasoning"] == {"effort": "low"} for call in provider.calls)
    assert all('"$defs"' not in call["prompt"] and '"transcript_segments"' not in call["prompt"]
               and "DIALOGUE (ID [start-end]" in call["prompt"] for call in provider.calls)
    assert {row["id"] for chapter in provider.chapters for row in chapter["transcript_segments"]} == set(range(720))


def test_standard_normalizes_timestamp_refs_and_large_citation_lists_without_dropping_ids():
    asset, transcript = story_evidence(duration=200, count=40)
    rows = [{"id": i, "start_seconds": row.start, "end_seconds": row.end, "text": row.text}
            for i, row in enumerate(transcript.segments)]
    data = {"beats": [{"text": "A connected, grounded paragraph.", "source_refs": {"start": 0, "end": 200}}]}
    normalized = normalize_standard_chapter(data, rows, asset.asset_id)
    assert sum(len(ref["transcript_segment_ids"]) for ref in normalized["beats"][0]["source_refs"]) == 40
    plan, beats = _chapter_beats(normalized, asset, rows, [], 0, 200, visual=False, offset=0)
    assert {tid for ref in beats[0].source_refs for tid in ref.transcript_segment_ids} == set(range(40))


@pytest.mark.parametrize("ref", [
    {"transcript_segment_ids": [999]}, {"transcript_segment_ids": [True]},
    {"start_seconds": 0, "end_seconds": 99999}, {"frame_ids": [0]},
    {"transcript_segment_ids": [0], "asset_id": "src-999"},
    {"transcript_segment_ids": [0], "start_seconds": 95, "end_seconds": 100},
])
def test_forgiving_word_targets_never_accept_ungrounded_evidence(ref):
    rows = [{"id": 0, "start_seconds": 0, "end_seconds": 5, "text": "Dialogue"},
            {"id": 1, "start_seconds": 95, "end_seconds": 100, "text": "Ending"}]
    with pytest.raises(NarrationModelOutputError):
        normalize_standard_chapter({"beats": [{"narration_text": "Story", "source_refs": [ref]}]}, rows, "src-001")


def test_standard_silent_tail_ends_at_last_dialogue_without_inventing_visual_evidence(tmp_path):
    asset, transcript = story_evidence(duration=3742, count=720)
    transcript.segments = [row for row in transcript.segments if row.end <= 3707]
    artifact = _compact_build(tmp_path, ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol"),
                              asset=asset, transcript=transcript)
    assert artifact.ending_anchor.detection_method == "last_transcribed_dialogue"
    assert artifact.ending_anchor.narrative_end_seconds == transcript.segments[-1].end
    assert artifact.visual_frame_count == artifact.live_vision_calls == artifact.multimodal_calls == 0


def test_accepted_light_standard_script_does_not_fail_again_during_audio_pacing(tmp_path):
    from app.production.narration import _pace_long_narration
    artifact = SimpleNamespace(planning_strategy="multi_chapter", summary_mode="standard",
        narration_budget=resolve_recap_budget(3742, target_length_preset="feature"),
        narration_text="grounded " * 1450, narration_quality={"policy": "dialogue_grounding_first_v1"})
    measured = SimpleNamespace(valid=True, duration=540)
    assert _pace_long_narration(tmp_path / "not-read.mp3", artifact, measured) == (measured, 540, 1.0)
    assert artifact.narration_quality["runtime_target_unattainable_at_natural_cadence"]


def test_equivalent_auto_and_feature_labels_keep_completed_chapters(tmp_path):
    asset, transcript = story_evidence(duration=3742, count=720)
    path = tmp_path / "narration_chapters.partial.json"
    first = ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol")
    build_chapter_plan("p", "u", asset, transcript, budget=resolve_recap_budget(asset.duration),
        prov=first, checkpoint_path=path, context_tokens=128000)
    original = json.loads(path.read_text())["chapters"]
    resumed = ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol")
    result = build_chapter_plan("p", "u", asset, transcript,
        budget=resolve_recap_budget(asset.duration, target_length_preset="feature"),
        prov=resumed, checkpoint_path=path, context_tokens=128000, minimum_cached_chapters=8)
    assert result.target_length_preset == "feature" and not resumed.calls
    assert json.loads(path.read_text())["chapters"] == original
    with pytest.raises(ValueError, match="Protected chapter checkpoint"):
        build_chapter_plan("p", "u", asset, transcript,
            budget=resolve_recap_budget(asset.duration, target_length_preset="blockbuster"),
            prov=resumed, checkpoint_path=path, context_tokens=128000, minimum_cached_chapters=8)


def test_pro_resume_reuses_visual_evidence_and_renews_only_new_request_allowance(tmp_path, monkeypatch):
    from tests.test_agentrouter_storyteller import Observer, frames
    asset, transcript = story_evidence()
    path = tmp_path / "pro.partial.json"
    observer = Observer()
    failing = ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol",
        error=LLMConnectionError("agentrouter", "fixture provider outage"))
    monkeypatch.setattr("app.production.narration_storyteller.time.sleep", lambda _: None)
    with pytest.raises(LLMConnectionError):
        build_chapter_plan("p", "u", asset, transcript, budget=resolve_recap_budget(asset.duration),
            prov=failing, visual_observer=observer, frame_loader=frames, checkpoint_path=path, context_tokens=128000)
    assert len(failing.calls) == 2
    observed = len(observer.calls)
    recovered = ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol")
    result = build_chapter_plan("p", "u", asset, transcript, budget=resolve_recap_budget(asset.duration),
        prov=recovered, visual_observer=observer, frame_loader=frames, checkpoint_path=path, context_tokens=128000)
    assert len(recovered.calls) == result.chapter_count == 5
    assert len(observer.calls) == observed * 5  # First chapter observation wasn't billed again.