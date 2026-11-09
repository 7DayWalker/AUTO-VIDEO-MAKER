from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.production.models import SourceTranscript, TranscriptSegment
from app.repurpose.captions import build_ass, escape_ass, narration_words, transcript_captions
from app.repurpose.renderer import framing_filter
from app.transcription.chunked import _segments


def transcript(segments):
    return SourceTranscript(asset_id="src-001", provider="local_whisper", created_at="fixture", transcript_path="source/src-001.transcript.json", segments=segments)


def test_raw_caption_cuts_remap_and_keep_segment_precision():
    source = transcript([TranscriptSegment(text="One two three four five six seven eight", start=10, end=18)])
    rows, precision = transcript_captions(source, [{"start_seconds": 10, "end_seconds": 18, "output_start": 3, "output_duration": 4}])
    assert precision == "segment_estimated_phrases"
    assert rows[0]["start"] == 3 and rows[-1]["end"] == 7
    assert all(row["words"] is None for row in rows)


def test_word_captions_follow_disjoint_cut_timeline():
    source = transcript([TranscriptSegment(text="Hello world", start=20, end=22,
        words=[{"word": "Hello", "start": 20, "end": 21}, {"word": "world", "start": 21, "end": 22}])])
    rows, precision = transcript_captions(source, [{"start_seconds": 20, "end_seconds": 22, "output_start": 12, "output_duration": 2}])
    assert precision == "word" and rows[0]["start"] == 12 and rows[0]["end"] == 14


def test_narration_timing_transforms_tempo_not_movie_time():
    words = narration_words([{"text": "Hello", "start": 2, "end": 4, "kind": "WordBoundary"}], speed=2, duration=10)
    assert words == [{"text": "Hello", "start": 1, "end": 2}]


def test_ass_safety_hook_and_explicit_vertical_resolution():
    malicious = r"{\pos(0,0)}Hello\Nworld"
    assert "{" not in escape_ass(malicious) and "\\" not in escape_ass(malicious)
    script = build_ass(malicious, [{"text": "原始字幕", "start": 0, "end": 2, "words": None}], 45)
    assert "PlayResX: 1080" in script and "PlayResY: 1920" in script
    assert "Style: Hook" in script and "Style: Caption" in script and "原始字幕" in script


@pytest.mark.parametrize("mode", ["center_crop", "cinema_canvas"])
def test_filter_uses_safe_relative_ass_filename(mode):
    graph = framing_filter(mode)
    assert "ass=filename='captions.ass'" in graph and "C:" not in graph and "1080:1920" in graph
    assert ("gblur" in graph) == (mode == "cinema_canvas")


def test_ffmpeg_cwd_is_optional_and_not_shell_interpolated(monkeypatch, tmp_path):
    from app.assembly.ffmpeg import FFmpegRunner
    runner = FFmpegRunner()
    runner.ffmpeg_path = "fixture-ffmpeg.exe"
    monkeypatch.setattr(runner, "ensure_ffmpeg", lambda: None)
    execute = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("app.assembly.ffmpeg.subprocess.run", execute)
    runner.run(["-vf", "ass=filename='captions.ass'"], cwd=tmp_path)
    assert execute.call_args.kwargs["cwd"] == str(tmp_path)
    assert isinstance(execute.call_args.args[0], list) and "shell" not in execute.call_args.kwargs


def test_chunked_transcription_preserves_shifted_word_times():
    rows = _segments([{"text": "Word", "start": 720, "end": 722,
        "words": [{"word": "Word", "start": 720.2, "end": 721.5}]}], 720, 1440)
    assert rows[0]["words"][0]["start"] == 720.2
    with pytest.raises(ValueError):
        _segments([{"text": "Bad", "start": 720, "end": 722, "words": [{"word": "Bad", "start": 700, "end": 721}]}], 720, 1440)