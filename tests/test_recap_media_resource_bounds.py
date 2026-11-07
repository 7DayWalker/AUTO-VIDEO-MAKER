"""Resource knobs stay bounded and never weaken media validation."""
import subprocess
from types import SimpleNamespace

import pytest

from app.assembly.ffmpeg import FFmpegRunner
from app.assembly.exceptions import FFmpegExecutionError
from app.production.summary_video import recap_render_workers


@pytest.mark.parametrize("timeout", [1, 30, 180, 300])
def test_probe_honors_independent_bounded_socketless_timeout(tmp_path, monkeypatch, timeout):
    runner = FFmpegRunner(probe_timeout=timeout)
    monkeypatch.setattr(runner, "ensure_ffprobe", lambda: None)
    path = tmp_path / "fixture.txt"
    path.write_text("Not media; subprocess is replaced by synthetic metadata")
    calls = []
    def probe(command, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stdout='{"format":{"duration":"2"},"streams":[]}', stderr="")
    monkeypatch.setattr("app.assembly.ffmpeg.subprocess.run", probe)
    assert runner.probe(path).duration == 2
    assert calls == [{"capture_output": True, "text": True, "timeout": timeout}]


@pytest.mark.parametrize("timeout", [0, -1, 301, True, 2.5])
def test_probe_timeout_rejects_invalid_unbounded_values(timeout):
    with pytest.raises(ValueError, match="FFPROBE_TIMEOUT"):
        FFmpegRunner(probe_timeout=timeout)


def test_probe_timeout_error_omits_command_payload(tmp_path, monkeypatch):
    runner = FFmpegRunner(probe_timeout=180)
    monkeypatch.setattr(runner, "ensure_ffprobe", lambda: None)
    path = tmp_path / "fixture.txt"
    path.write_text("Synthetic probe fixture")
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired(["private-provider-fixture"], 180)
    monkeypatch.setattr("app.assembly.ffmpeg.subprocess.run", fail)
    with pytest.raises(FFmpegExecutionError, match="180 seconds") as caught:
        runner.probe(path)
    assert "private-provider-fixture" not in str(caught.value)


@pytest.mark.parametrize("value", ["1", "2", "4"])
def test_recap_workers_can_be_reduced_without_touching_video_contract(monkeypatch, value):
    monkeypatch.setenv("AVM_RECAP_RENDER_WORKERS", value)
    assert recap_render_workers(300) == int(value)
    assert recap_render_workers(3) == 1


@pytest.mark.parametrize("value", ["0", "5", "1000", "none"])
def test_recap_workers_reject_unsafe_values(monkeypatch, value):
    monkeypatch.setenv("AVM_RECAP_RENDER_WORKERS", value)
    with pytest.raises(ValueError, match="AVM_RECAP_RENDER_WORKERS"):
        recap_render_workers(300)