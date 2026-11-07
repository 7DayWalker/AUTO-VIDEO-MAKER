"""Universal movie regressions: no provider/network/model download is needed."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.content.exceptions import LLMConnectionError, LLMOutputTruncatedError, LLMModelUnavailableError, LLMQuotaError
from app.production.narration_chapters import build_chapter_plan, chapter_windows, _chapter_budget
from app.production.recap_budget import resolve_recap_budget
from tests.track9_narration_fakes import ChapterNarrationProvider, story_evidence
from tests.test_agentrouter_storyteller import Observer, frames
from tests.test_track9_extended_chapters import frame_loader


@pytest.mark.parametrize("source", [60, 1200, 3600, 7200, 14400])
@pytest.mark.parametrize("visual", [False, True])
def test_independent_16k_chapter_calls_survive_expired_global_deadline(tmp_path, source, visual):
    asset, transcript = story_evidence(duration=source, count=720)
    class Timed(ChapterNarrationProvider):
        _timeout = 1
        def generate(self, *args, **kwargs):
            assert self._timeout == 300
            return super().generate(*args, **kwargs)
    provider = Timed()
    budget = resolve_recap_budget(source, target_length_preset="extended" if source == 1200 else "auto")
    checkpoint = tmp_path / "narration_chapters.partial.json"
    artifact = build_chapter_plan("p", "u", asset, transcript, budget=budget, prov=provider,
        context_tokens=128000, frame_loader=frame_loader if visual else None, deadline=-100,
        checkpoint_path=checkpoint)
    assert len(provider.calls) == artifact.chapter_count
    assert all(call["max_tokens"] == 16384 for call in provider.calls)
    assert provider._timeout == 1, "per-call copies never mutate a shared adapter"
    assert json.loads(checkpoint.read_text())["complete"]


def test_protected_nine_chapters_are_never_regenerated(tmp_path):
    asset, transcript = story_evidence(duration=3742, count=720)
    budget = resolve_recap_budget(asset.duration, target_length_preset="blockbuster")
    path = tmp_path / "narration_chapters.partial.json"
    class Interrupted(ChapterNarrationProvider):
        def generate(self, prompt, *args, **kwargs):
            evidence = json.loads(prompt.split("evidence:\n")[1].split("\nRequired JSON schema:")[0])
            if evidence["chapter_index"] == 10:
                raise ValueError("interrupted before final chapter")
            return super().generate(prompt, *args, **kwargs)
    with pytest.raises(ValueError, match="interrupted"):
        build_chapter_plan("p", "u", asset, transcript, budget=budget, prov=Interrupted(),
                           context_tokens=128000, checkpoint_path=path)
    before = json.loads(path.read_text())["chapters"]
    assert len(before) == 10 - 1
    provider = ChapterNarrationProvider()
    result = build_chapter_plan("p", "u", asset, transcript, budget=budget, prov=provider,
        context_tokens=128000, checkpoint_path=path, minimum_cached_chapters=9)
    assert result.chapter_count == 10 and len(provider.calls) == 1
    assert provider.chapters[0]["chapter_index"] == 10
    assert json.loads(path.read_text())["chapters"][:9] == before
    with pytest.raises(ValueError, match="Protected chapter checkpoint"):
        build_chapter_plan("p", "u", asset, transcript, budget=budget, prov=ChapterNarrationProvider(model="other"),
            context_tokens=128000, checkpoint_path=path, minimum_cached_chapters=9)


def test_paid_retry_claims_survive_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr("app.production.narration_chapters.time.sleep", lambda seconds: None)
    asset, transcript = story_evidence()
    path = tmp_path / "chapters.json"
    provider = ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol", error=LLMConnectionError("agentrouter", "connection failed"))
    with pytest.raises(LLMConnectionError):
        build_chapter_plan("p", "u", asset, transcript, budget=resolve_recap_budget(asset.duration), prov=provider, checkpoint_path=path)
    assert len(provider.calls) == 2
    fresh = ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol")
    with pytest.raises(RuntimeError, match="credit guard"):
        build_chapter_plan("p", "u", asset, transcript, budget=resolve_recap_budget(asset.duration), prov=fresh, checkpoint_path=path)
    assert not fresh.calls


def test_whole_pro_chapter_only_splits_after_truncation_and_never_retries_halves():
    from app.production.narration_storyteller import write_storyteller_chapter
    asset, transcript = story_evidence()
    start, end, rows = chapter_windows(asset.duration, transcript)[0]
    supplied = frames(start, end, 0)
    budget = _chapter_budget(resolve_recap_budget(asset.duration), 0, 5)
    payload = {"chapter_index": 1, "start_seconds": start, "end_seconds": end,
        "transcript_segments": rows, "frames": [{"id": i, "timestamp_seconds": ts} for i, (ts, _) in enumerate(supplied)],
        "visual_action_notes": [{"frame_id": i, "timestamp_seconds": ts, "action": "A companion escapes."} for i, (ts, _) in enumerate(supplied)],
        "prior_chapter_summaries": [], "previous_narration_tail": "", "preferred_word_count": 308}
    class Truncated(ChapterNarrationProvider):
        def generate(self, *args, **kwargs):
            response = super().generate(*args, **kwargs)
            if len(self.calls) == 1:
                raise LLMOutputTruncatedError()
            return response
    provider = Truncated(name="agentrouter", model="gpt-6.1-sol")
    cache, state = {}, {}
    result, calls = write_storyteller_chapter(provider, "Grounded recap", payload, asset=asset, frames=supplied,
        local_budget=budget, deadline=-10, cached_batches=cache, call_state=state,
        on_batch=lambda key, value: cache.update({key: value}))
    assert calls == len(provider.calls) == 3
    assert state["split"] and set(cache) == {"split:0", "split:1"}
    assert all(call["max_tokens"] == 16384 for call in provider.calls)
    assert len(result["beats"]) == budget.max_beats
    again, _ = write_storyteller_chapter(provider, "Grounded recap", payload, asset=asset, frames=supplied,
        local_budget=budget, deadline=-10, cached_batches=cache, call_state=state)
    assert again == result and len(provider.calls) == 3


@pytest.mark.parametrize("failure", [LLMModelUnavailableError(), LLMQuotaError("gemini"), LLMConnectionError("gemini", "socket failed")])
def test_dead_vision_routes_are_quarantined_for_subsequent_chapters(monkeypatch, failure):
    from app.production.narration_real import observe_with_vision_failover
    from app.content import capability_router, provider_registry
    bad, good = Observer(), Observer()
    bad.model, good.model = "gemini-3.6-pro", "gemini-3.5-flash-lite"
    bad.generate_multimodal = Mock(side_effect=failure)
    routes = [SimpleNamespace(canonical_model_id=p.model, provider=p.name, provider_model_id=p.model) for p in (bad, good)]
    monkeypatch.setattr(capability_router, "configured_visual_observer_routes", lambda: routes)
    monkeypatch.setattr(provider_registry, "create_provider", lambda name, model: bad if model == bad.model else good)
    good.is_available = lambda: True
    for _ in range(2):
        result = observe_with_vision_failover(bad, frames(0, 100, 0)[:4], [], start=0, end=100, continuity=[], deadline=-1)
        assert len(result) == 4
    assert bad.generate_multimodal.call_count == 1 and len(good.calls) == 2


def test_four_hour_whisper_uses_twenty_bounded_windows_and_resumes(tmp_path, monkeypatch):
    from app.transcription.chunked import transcribe_in_chunks, transcript_chunks_path
    from app.transcription.providers import TranscriptionResult
    import app.assembly.ffmpeg as ffmpeg
    media = tmp_path / "source.mp4"
    media.write_bytes(b"fixture metadata only, never decoded")
    commands, called = [], []
    def extract(args, description, timeout):
        assert args.index("-ss") < args.index("-i") and timeout == 300
        assert float(args[args.index("-t") + 1]) <= 720
        commands.append(args)
        Path(args[-1]).write_bytes(b"fixture audio")
    monkeypatch.setattr(ffmpeg, "get_ffmpeg_runner", lambda: SimpleNamespace(run=extract))
    def transcribe(path, duration):
        called.append(duration)
        if len(called) == 4:
            raise RuntimeError("interrupted window")
        return TranscriptionResult("en", [{"text": "Grounded dialogue", "start": 0, "end": duration}], "local_whisper", "tiny")
    with pytest.raises(RuntimeError, match="interrupted"):
        transcribe_in_chunks(media, 14400, "tiny", transcribe)
    checkpoint = transcript_chunks_path(media)
    assert len(json.loads(checkpoint.read_text())["chunks"]) == 3
    language, segments = transcribe_in_chunks(media, 14400, "tiny", transcribe)
    assert language == "en" and len(segments) == 20 and len(called) == 21
    assert segments[3]["start"] == 2160 and segments[-1]["end"] == 14400
    before = len(commands)
    assert transcribe_in_chunks(media, 14400, "tiny", lambda *a: pytest.fail("completed chunks were retranscribed")) == (language, segments)
    assert len(commands) == before
    assert json.loads(checkpoint.read_text())["complete"]


def test_multigig_upload_reads_one_megabyte_chunks_without_copy(tmp_path, monkeypatch):
    from app.production import source
    from tests.test_track9_api_routes import _make_prod, PID
    _make_prod(tmp_path)
    total = 4 * 1024 ** 3
    seen = []
    class Stream:
        remaining = total
        def read(self, size):
            seen.append(size)
            assert 0 < size <= 1024 ** 2
            self.remaining -= size
            return b"x" * size
    class Sink(io.BytesIO):
        def write(self, chunk): return len(chunk)  # No multi-GB test artifact.
        def __enter__(self): return self
        def __exit__(self, *a): pass
    original = Path.open
    monkeypatch.setattr(Path, "open", lambda p, mode="r", *a, **kw: Sink() if mode == "wb" else original(p, mode, *a, **kw))
    monkeypatch.setattr(source.shutil, "disk_usage", lambda p: SimpleNamespace(free=10 * total))
    monkeypatch.setattr(source, "_register_local_source", lambda *a, **kw: (201, {"sha": kw["uploaded_sha256"]}))
    status, result = source.register_uploaded_source(PID, "movie.mp4", Stream(), total, root=tmp_path / "productions")
    assert status == 201 and len(result["sha"]) == 64 and sum(seen) == total


@pytest.mark.parametrize("error", [ConnectionResetError, BrokenPipeError])
def test_range_seek_disconnect_never_raises_or_writes_another_response(tmp_path, monkeypatch, error):
    from app.web import server
    from app.production import summary_video
    media = tmp_path / "recap.mp4"
    media.write_bytes(b"0123456789")
    monkeypatch.setattr(server, "_track9_check_unit", lambda *a: (object(), None))
    monkeypatch.setattr(summary_video, "load_narrated_summary", lambda *a, **kw: object())
    monkeypatch.setattr(summary_video, "summary_video_output_path", lambda *a, **kw: media)
    codes, headers = [], {}
    handler = SimpleNamespace(headers={"Range": "bytes=2-5"}, send_response=codes.append,
        send_header=headers.__setitem__, end_headers=lambda: None,
        wfile=SimpleNamespace(write=Mock(side_effect=error())), _json=Mock(side_effect=AssertionError("no JSON after 206")))
    server._serve_summary_video_media(handler, "p", "u")
    assert codes == [206] and headers["Content-Range"] == "bytes 2-5/10" and headers["Content-Length"] == "4"


def test_changing_observer_priority_never_invalidates_completed_paid_chapters(tmp_path):
    asset, transcript = story_evidence()
    path = tmp_path / "chapters.json"
    original = Observer()
    build_chapter_plan("p", "u", asset, transcript, budget=resolve_recap_budget(asset.duration),
        prov=ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol"), visual_observer=original,
        frame_loader=frames, checkpoint_path=path)
    next_observer = Observer()
    next_observer.model = "gemini-3.6-pro"
    provider = ChapterNarrationProvider(name="agentrouter", model="gpt-6.1-sol")
    result = build_chapter_plan("p", "u", asset, transcript, budget=resolve_recap_budget(asset.duration),
        prov=provider, visual_observer=next_observer, frame_loader=frames, checkpoint_path=path,
        minimum_cached_chapters=5)
    assert not provider.calls and not next_observer.calls
    assert result.visual_observer_model_id == original.model


def test_missing_whisper_chunk_does_not_drop_later_valid_checkpoints(tmp_path, monkeypatch):
    from app.transcription.chunked import transcribe_in_chunks, transcript_chunks_path
    from app.transcription.providers import TranscriptionResult
    from app.assembly import ffmpeg
    media = tmp_path / "source.mp4"
    media.write_bytes(b"source")
    monkeypatch.setattr(ffmpeg, "get_ffmpeg_runner", lambda: SimpleNamespace(run=lambda *a, **kw: None))
    transcribe = lambda p, duration: TranscriptionResult("en", [{"start": 0, "end": duration, "text": "dialogue"}], "local_whisper", "tiny")
    transcribe_in_chunks(media, 2160, "tiny", transcribe)
    checkpoint = transcript_chunks_path(media)
    saved = json.loads(checkpoint.read_text())
    del saved["chunks"]["1"]
    checkpoint.write_text(json.dumps(saved))
    def fail(*a):
        raise RuntimeError("restart before missing window")
    with pytest.raises(RuntimeError):
        transcribe_in_chunks(media, 2160, "tiny", fail)
    assert set(json.loads(checkpoint.read_text())["chunks"]) == {"0", "2"}


def test_all_recap_ffmpeg_seeks_are_input_side_even_in_hour_four(tmp_path, monkeypatch):
    from app.production import narration_pro, summary_video
    from app.assembly import ffmpeg
    media = tmp_path / "source.mp4"
    media.write_bytes(b"not decoded")
    commands = []
    def execute(args, *a, **kw):
        commands.append(args)
        Path(args[-1]).write_bytes(b"x" * 2048)
    monkeypatch.setattr(ffmpeg, "get_ffmpeg_runner", lambda: SimpleNamespace(run=execute))
    monkeypatch.setattr(summary_video, "run_ffmpeg", execute)
    monkeypatch.setattr(summary_video, "resolve_ffmpeg", lambda: "ffmpeg")
    assert narration_pro._extract_window_clip(media, 13000, 13005, tmp_path, 0)
    assert narration_pro._extract_window_frames(media, 13000, 14000, tmp_path, 0, frame_count=4)
    summary_video._extract_clip_range(media, 13000, 5, tmp_path / "cut.mp4", source_has_audio=True)
    assert len(commands) == 6
    assert all(args.index("-ss") < args.index("-i") for args in commands)


def test_interrupted_atomic_media_copy_preserves_the_previous_master(tmp_path, monkeypatch):
    from app.production import source
    incoming, master = tmp_path / "new.mp4", tmp_path / "accepted.mp4"
    incoming.write_bytes(b"new complete content")
    master.write_bytes(b"old accepted content")
    def interrupted(src, dst):
        Path(dst).write_bytes(b"partial")
        raise OSError("synthetic disk interruption")
    monkeypatch.setattr(source.shutil, "copyfile", interrupted)
    with pytest.raises(OSError, match="interruption"):
        source._atomic_copy(incoming, master)
    assert master.read_bytes() == b"old accepted content"
    assert not list(tmp_path.glob("*.tmp-*"))


def test_oserror_after_paid_chapter_never_cross_model_regenerates(monkeypatch, tmp_path):
    from app.production.narration import build_grounded_narration_plan
    from tests.test_track9_extended_chapters import _install_pro_pool, PID, UID
    class BrokenAfterFirst(ChapterNarrationProvider):
        def generate(self, prompt, *args, **kwargs):
            evidence = json.loads(prompt.split("evidence:\n")[1].split("\nRequired JSON schema:")[0])
            if evidence["chapter_index"] == 2:
                raise ConnectionError("synthetic interrupted paid route")
            return super().generate(prompt, *args, **kwargs)
    failed, backup = BrokenAfterFirst(), ChapterNarrationProvider()
    _install_pro_pool(monkeypatch, [failed, backup])
    monkeypatch.setattr("app.production.narration_chapters.time.sleep", lambda *a: None)
    monkeypatch.setattr("app.production.narration_pro._extract_window_frames",
                        lambda src, start, end, directory, index, **kw: frame_loader(start, end, index))
    asset, transcript = story_evidence()
    source = tmp_path / PID / asset.stored_path
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fixture source")
    with pytest.raises(ConnectionError, match="paid route"):
        build_grounded_narration_plan(PID, UID, asset, transcript, summary_mode="pro", mock_mode=False,
                                       target_length_preset="extended", root=tmp_path)
    assert not backup.calls
    checkpoint = tmp_path / PID / "units" / UID / "plan/narration_chapters.partial.json"
    assert len(json.loads(checkpoint.read_text())["chapters"]) == 1