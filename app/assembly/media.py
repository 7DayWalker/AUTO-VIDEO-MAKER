"""Small provider-agnostic media inspection helpers."""

from pathlib import Path

from app.assembly.audio_contract import AudioMediaFacts
from app.assembly.ffmpeg import probe_media


def inspect_audio(path: Path, *, is_synthetic: bool = False) -> AudioMediaFacts:
    """Inspect the first audio stream in ``path`` using the shared FFprobe path."""
    info = probe_media(Path(path))
    return AudioMediaFacts(
        has_audio=info.has_audio,
        audio_codec=info.audio_codec if info.has_audio else None,
        audio_duration=info.audio_duration if info.has_audio else None,
        sample_rate=info.sample_rate if info.has_audio else None,
        channels=info.channels if info.has_audio else None,
        is_synthetic=is_synthetic if info.has_audio else False,
    )