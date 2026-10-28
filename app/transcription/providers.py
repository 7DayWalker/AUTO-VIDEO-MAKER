"""Providers for transcription.

Track 8 Hardening (11C): real local STT activation + truthfulness.

- MockProvider remains deterministic and hermetic for tests; requires
  explicit ``provider="mock"``.
- LocalWhisperProvider is the real production STT engine (openai-whisper).
- Provider selection is truthful: omitted/empty provider MUST NOT silently
  return mock.  The production default is ``local_whisper`` (override via
  ``AVM_TRANSCRIPTION_PROVIDER`` env var).
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Dict, List, Optional
class TranscriptionResult:
    def __init__(self, language: Optional[str], segments: List[Dict], provider: str, provider_version: Optional[str]):
        self.language = language
        self.segments = segments
        self.provider = provider
        self.provider_version = provider_version
class MockProvider:
    provider = "mock"
    provider_version = "mock-v1"
    def transcribe(self, media_path: Path, duration: float) -> TranscriptionResult:
        from app.production.sandbox import sandbox_duration

        # Metadata-only fast path. Never decode media, extract WAV, or load STT.
        duration = sandbox_duration(duration)
        n = 2 if duration >= 2 else 1
        segs = []
        for i in range(n):
            start = (duration / n) * i
            end = (duration / n) * (i + 1)
            if i < n - 1:
                end = round(end - 0.05, 3)
            segs.append({"text": f"mock segment {i+1}", "start": start, "end": end})
        return TranscriptionResult(language="en", segments=segs, provider=self.provider, provider_version=self.provider_version)
class LocalWhisperProvider:
    provider = "local_whisper"
    provider_version = "whisper-base"
    def transcribe(self, media_path: Path, duration: float) -> TranscriptionResult:
        try:
            import whisper  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"STT_ENGINE_UNAVAILABLE: openai-whisper not installed ({exc})")
        model_name = os.getenv("AVM_WHISPER_MODEL", "tiny")
        try:
            model = whisper.load_model(model_name)
        except Exception as exc:
            raise RuntimeError(f"STT_ENGINE_UNAVAILABLE: cannot load whisper model '{model_name}' ({exc})")
        # Whisper's audio loader shells out to `ffmpeg` on PATH.  On Windows AVM
        # resolves ffmpeg via FFMPEG_PATH / WinGet install; ensure it is on PATH
        # for the child process so whisper.load_audio can find it.
        import shutil as _shutil
        _ffmpeg_for_whisper = _shutil.which("ffmpeg")
        _resolved_ffmpeg = None
        if _ffmpeg_for_whisper is None:
            try:
                from app.assembly.ffmpeg import resolve_ffmpeg as _resolve_ffmpeg
                _resolved_ffmpeg = _resolve_ffmpeg()
            except Exception:
                _resolved_ffmpeg = None
            if _resolved_ffmpeg:
                import pathlib as _pl
                _dir = str(_pl.Path(_resolved_ffmpeg).parent)
                if _dir not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = _dir + os.pathsep + os.environ.get("PATH", "")
        try:
            result = model.transcribe(str(media_path), verbose=False)
        except FileNotFoundError as exc:
            # Typically ffmpeg not on PATH for whisper.audio.load_audio
            raise RuntimeError(f"TRANSCRIPTION_FAILED: ffmpeg not found on PATH for Whisper audio decode ({exc})")
        except Exception as exc:
            raise RuntimeError(f"TRANSCRIPTION_FAILED: {exc}")
        segs = []
        for s in result.get("segments", []) or []:
            text = (s.get("text") or "").strip()
            if not text:
                continue
            start = float(s.get("start", 0))
            end = float(s.get("end", 0))
            if not (0 <= start < end):
                continue
            segs.append({"text": text, "start": start, "end": end})
        if not segs and (result.get("text") or "").strip():
            segs = [{"text": result["text"].strip(), "start": 0.0, "end": float(duration)}]
        return TranscriptionResult(language=result.get("language"), segments=segs, provider=self.provider, provider_version=model_name)

# Truthful production default: local_whisper.  Mock requires explicit opt-in.
# Overridable via AVM_TRANSCRIPTION_PROVIDER for operator configuration.
_DEFAULT_PROVIDER_ENV = "AVM_TRANSCRIPTION_PROVIDER"
_DEFAULT_PROVIDER_FALLBACK = "local_whisper"

def _default_provider_name() -> str:
    raw = os.getenv(_DEFAULT_PROVIDER_ENV, _DEFAULT_PROVIDER_FALLBACK)
    raw = (raw or "").strip().lower()
    return raw if raw else _DEFAULT_PROVIDER_FALLBACK

def get_provider(name: Optional[str]):
    raw = (name or "").strip().lower()
    if not raw:
        # Omitted provider -> truthful production default (never silent mock)
        raw = _default_provider_name()
    if raw == "mock":
        return MockProvider()
    if raw in ("local_whisper", "whisper"):
        return LocalWhisperProvider()
    raise ValueError(f"unknown transcription provider '{name}'")
