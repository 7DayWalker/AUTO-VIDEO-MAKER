"""Track 8 hardening: provider truthfulness regression.

- Production/default provider must NOT silently be mock
- Explicit mock still works
- Missing whisper engine -> 503 truthful
- No fallback to mock from whisper failures
- Omitted provider truthfully selects local_whisper (or configured default)
"""
from pathlib import Path
import pytest
from app.web import server as web
from app.production.source import register_source
from app.transcription.transcribe import transcribe_source
from app.transcription.providers import get_provider, MockProvider, LocalWhisperProvider
from app.production.storage import transcript_path_for

def _make_prod(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    s, p = web.create_production_payload({"mode":"single_video","creation_method":"local","title":"Truth test","brief":"truth","output_intent":"youtube_shorts","target_duration":60,"scene_count":6})
    assert s == 201
    return p["production_id"]

def _synth_video(path: Path, seconds=2):
    from app.assembly.ffmpeg import run_ffmpeg
    path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-y","-f","lavfi","-i",f"color=c=red:s=320x240:r=25:d={seconds}","-c:v","libx264","-pix_fmt","yuv420p","-t",str(seconds),str(path)], "synth video")

def test_omitted_provider_is_not_mock(tmp_path, monkeypatch):
    # Omitted provider must be local_whisper (truthful), not silent mock
    assert get_provider(None).provider == "local_whisper"
    assert get_provider("").provider == "local_whisper"
    assert get_provider("   ").provider == "local_whisper"

def test_explicit_mock_still_works(tmp_path, monkeypatch):
    assert get_provider("mock").provider == "mock"

def test_env_override_default_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("AVM_TRANSCRIPTION_PROVIDER", "mock")
    # When env overrides default, omitted provider becomes mock explicitly via config
    p = get_provider(None)
    assert p.provider == "mock"
    monkeypatch.delenv("AVM_TRANSCRIPTION_PROVIDER", raising=False)
    assert get_provider(None).provider == "local_whisper"

def test_production_default_is_local_whisper_not_mock(tmp_path, monkeypatch):
    # transcribe_source with omitted provider should attempt local_whisper, not mock
    # Simulate whisper unavailable so we can assert 503, not mock success
    import app.transcription.providers as prov
    def fail(self, media_path, duration):
        raise RuntimeError("STT_ENGINE_UNAVAILABLE: simulated")
    monkeypatch.setattr(prov.LocalWhisperProvider, "transcribe", fail)
    pid = _make_prod(tmp_path, monkeypatch)
    vid = tmp_path / "src.mp4"
    _synth_video(vid)
    s, _ = register_source(pid, str(vid), root=tmp_path / "productions")
    assert s == 201
    # provider=None -> production default (local_whisper) -> 503
    code, payload = transcribe_source(pid, "src-001", root=tmp_path / "productions", provider=None)
    assert code == 503
    assert payload["error"] == "STT_ENGINE_UNAVAILABLE"
    assert not transcript_path_for(pid, "src-001", root=tmp_path / "productions").exists()

def test_explicit_mock_provider_produces_mock(tmp_path, monkeypatch):
    pid = _make_prod(tmp_path, monkeypatch)
    vid = tmp_path / "src.mp4"
    _synth_video(vid)
    s, _ = register_source(pid, str(vid), root=tmp_path / "productions")
    assert s == 201
    code, payload = transcribe_source(pid, "src-001", root=tmp_path / "productions", provider="mock")
    assert code == 201
    assert payload["provider"] == "mock"

def test_whisper_uses_configured_model(tmp_path, monkeypatch):
    import app.transcription.providers as prov
    captured = {}
    real_load = None
    try:
        import whisper  # type: ignore
        real_load = whisper.load_model
    except Exception:
        pytest.skip("whisper not installed")
    def fake_load(name):
        captured["model"] = name
        raise RuntimeError("STT_ENGINE_UNAVAILABLE: fake load for test")
    monkeypatch.setattr("whisper.load_model", fake_load)
    monkeypatch.setenv("AVM_WHISPER_MODEL", "tiny")
    p = prov.LocalWhisperProvider()
    try:
        p.transcribe(Path("/tmp/fake.mp4"), 2.0)
    except RuntimeError as e:
        assert "STT_ENGINE_UNAVAILABLE" in str(e)
    assert captured.get("model") == "tiny"
    # custom model
    monkeypatch.setenv("AVM_WHISPER_MODEL", "base")
    try:
        p.transcribe(Path("/tmp/fake.mp4"), 2.0)
    except RuntimeError:
        pass
    assert captured.get("model") == "base"

def test_no_fallback_to_mock_on_whisper_failure(tmp_path, monkeypatch):
    import app.transcription.providers as prov
    def fail(self, media_path, duration):
        raise RuntimeError("STT_ENGINE_UNAVAILABLE: boom")
    monkeypatch.setattr(prov.LocalWhisperProvider, "transcribe", fail)
    pid = _make_prod(tmp_path, monkeypatch)
    vid = tmp_path / "src.mp4"
    _synth_video(vid)
    s, _ = register_source(pid, str(vid), root=tmp_path / "productions")
    assert s == 201
    code, payload = transcribe_source(pid, "src-001", root=tmp_path / "productions", provider="local_whisper")
    assert code == 503
    assert payload["error"] == "STT_ENGINE_UNAVAILABLE"
    # Must not have created transcript
    assert not transcript_path_for(pid, "src-001", root=tmp_path / "productions").exists()
