"""Bounded audio decoding and durable Whisper windows for feature-length media."""
from __future__ import annotations

import hashlib
import json
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path

LONG_SOURCE_SECONDS = 20 * 60
AUDIO_CHUNK_SECONDS = 12 * 60


def transcript_chunks_path(media_path: Path) -> Path:
    # Per-source namespace: two movies must never overwrite each other's resume.
    return media_path.parent / (media_path.stem + ".transcription") / "transcript_chunks.json"


def _segments(raw, start, end):
    result = []
    for segment in raw or []:
        text = segment.get("text", "")
        left, right = float(segment.get("start", -1)), float(segment.get("end", -1))
        if not isinstance(text, str) or not text.strip():
            continue
        if ("\x00" in text or not math.isfinite(left) or not math.isfinite(right)
                or left < start or right > end + 1e-6 or right <= left
                or (result and left < result[-1]["start"])):
            raise ValueError("Transcription chunk timestamps/text are invalid")
        result.append({"text": text.strip(), "start": left, "end": min(end, right)})
    return result


def transcribe_in_chunks(media_path, duration, model_name, transcribe_window):
    """At most twelve minutes of PCM reaches Whisper; completed text is atomic.

    ``transcribe_window`` loads the model lazily. Fully cached media needs no
    Whisper import/model download or FFmpeg extraction. Empty (silent) windows
    are valid completed chunks, but never fabricate dialogue.
    """
    from app.assembly.ffmpeg import get_ffmpeg_runner
    from app.transcription.transcribe import _atomic_write_json

    path = Path(media_path)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Source duration must be finite and positive")
    stat = path.stat()
    identity = hashlib.sha256(json.dumps({"version": 1, "path": str(path.resolve()),
        "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "duration": duration,
        "model": model_name, "chunk_seconds": AUDIO_CHUNK_SECONDS}, sort_keys=True).encode()).hexdigest()
    checkpoint = transcript_chunks_path(path)
    saved = {}
    try:
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        if data.get("identity") == identity and isinstance(data.get("chunks"), dict):
            saved = data["chunks"]
    except (OSError, ValueError, AttributeError):
        pass
    count = math.ceil(duration / AUDIO_CHUNK_SECONDS)
    completed, segments, language = {}, [], None
    # Validate retained text first. Rewriting chunk 1 must not drop already
    # completed chunks 2..20 if a restart occurs during checkpoint inspection.
    for index in range(count):
        start, end = index * AUDIO_CHUNK_SECONDS, min(duration, (index + 1) * AUDIO_CHUNK_SECONDS)
        entry = saved.get(str(index))
        try:
            if not isinstance(entry, dict) or entry.get("start") != start or entry.get("end") != end:
                continue
            _segments(entry["segments"], start, end)
            completed[str(index)] = entry
        except (ValueError, KeyError, TypeError, AttributeError):
            pass

    def persist(active_chunk=None):
        _atomic_write_json(checkpoint, {"version": 1, "identity": identity,
            "chunk_seconds": AUDIO_CHUNK_SECONDS, "chunk_count": count, "chunks": completed,
            "active_chunk": active_chunk, "complete": len(completed) == count,
            "updated_at": datetime.now(timezone.utc).isoformat()})

    for index in range(count):
        start, end = index * AUDIO_CHUNK_SECONDS, min(duration, (index + 1) * AUDIO_CHUNK_SECONDS)
        entry = saved.get(str(index))
        try:
            if not isinstance(entry, dict) or entry.get("start") != start or entry.get("end") != end:
                raise ValueError("Mismatched chunk")
            chunk_segments = _segments(entry["segments"], start, end)
        except (ValueError, KeyError, TypeError, AttributeError):
            persist(index + 1)
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".audio-", dir=checkpoint.parent) as scratch:
                audio = Path(scratch) / "window.wav"
                get_ffmpeg_runner().run(["-y", "-ss", f"{start:.3f}", "-i", str(path), "-t", f"{end - start:.3f}",
                    "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)],
                    "extracting a bounded twelve-minute Whisper window", timeout=300)
                result = transcribe_window(audio, end - start)
            shifted = [{"text": row["text"], "start": start + row["start"],
                        "end": min(end, start + row["end"])} for row in result.segments]
            chunk_segments = _segments(shifted, start, end)
            entry = {"start": start, "end": end, "segments": chunk_segments, "language": result.language}
        completed[str(index)] = entry
        segments.extend(chunk_segments)
        language = language or entry.get("language")
        persist()
    return language, segments