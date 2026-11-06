"""Recap voice settings, resumable long-form TTS and broadcast mastering.

Short scripts stay continuous. Long scripts use bounded chapter/beat batches,
decoded to uniform PCM before concatenation; no artificial per-beat pauses.
The shared voice engine remains unchanged for other production workflows.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_RECAP_VOICE = "en-US-AndrewMultilingualNeural"
DEFAULT_RECAP_RATE = "+15%"
BROADCAST_LUFS = -14.0
BROADCAST_TRUE_PEAK_DB = -1.0
# Lossy MP3/AAC can overshoot a PCM ceiling. Leave 0.5 dB codec headroom.
NORMALIZATION_TRUE_PEAK_DB = -1.5
BROADCAST_VOCAL_CHAIN = (
    "highpass=f=70,"
    "acompressor=threshold=0.089:ratio=4:attack=3:release=100:knee=4:makeup=1"
)
BROADCAST_CHAIN_VERSION = "recap-broadcast-v1"
TTS_CHUNK_TRIGGER_WORDS = 1800
TTS_CHUNK_MAX_WORDS = 450
TTS_CHUNK_MAX_BYTES = 6000
TTS_REQUEST_TIMEOUT_SECONDS = 90
TTS_SYNTHESIS_TIMEOUT_SECONDS = 480


def recap_voice(override=None):
    return override or os.getenv("AVM_RECAP_TTS_VOICE") or DEFAULT_RECAP_VOICE


def recap_rate(override=None):
    value = override if override is not None else os.getenv("AVM_RECAP_TTS_RATE", DEFAULT_RECAP_RATE)
    if not isinstance(value, str) or not re.fullmatch(r"[+-]\d{1,2}%", value) or not -25 <= int(value[:-1]) <= 60:
        raise ValueError("Recap rate must be a signed percentage between -25% and +60%")
    return value


def spoken_recap_text(text):
    # Whitespace only: do not silently rewrite approved content or citations.
    return " ".join(text.split())


@dataclass(frozen=True)
class RecapTTSChunk:
    text: str
    chapter_index: int | None = None
    beat_start_order: int | None = None
    beat_end_order: int | None = None


def recap_tts_chunks(artifact):
    """Preserve approved prose exactly, batching at chapter/beat/sentence edges."""
    text = spoken_recap_text(artifact.narration_text)
    if not text:
        raise ValueError("Recap TTS requires non-empty spoken narration")
    if len(text.split()) <= TTS_CHUNK_TRIGGER_WORDS and len(text.encode("utf-8")) <= 24000:
        return [RecapTTSChunk(text)]
    beats = getattr(artifact, "beats", [])
    chapters = getattr(artifact, "chapters", [])
    if spoken_recap_text(" ".join(beat.narration_text for beat in beats)) == text:
        units = [(spoken_recap_text(beat.narration_text), next((chapter.chapter_index for chapter in chapters
                  if chapter.beat_start_order <= beat.order <= chapter.beat_end_order), None), beat.order)
                 for beat in beats]
    else:
        # Never substitute a stale beat draft for the approved full script.
        units = [(text, None, None)]
    chunks, pending = [], []
    chapter_index = beat_start = beat_end = None

    def flush():
        if pending:
            chunks.append(RecapTTSChunk(" ".join(pending), chapter_index, beat_start, beat_end))
            pending.clear()

    def would_overflow(part):
        return (len(pending) + len(part.split()) > TTS_CHUNK_MAX_WORDS
                or len((" ".join(pending) + " " + part).encode("utf-8")) > TTS_CHUNK_MAX_BYTES)

    for unit, chapter, order in units:
        if pending and (chapter != chapter_index or would_overflow(unit)):
            flush()
        for sentence in re.split(r"(?<=[.!?。！？])\s+", unit):
            if pending and would_overflow(sentence):
                flush()
            for word in sentence.split():
                if len(word.encode("utf-8")) > TTS_CHUNK_MAX_BYTES:
                    raise ValueError("A narration token exceeds the bounded TTS payload size")
                if pending and (chapter != chapter_index or len(pending) >= TTS_CHUNK_MAX_WORDS
                                or len((" ".join(pending) + " " + word).encode("utf-8")) > TTS_CHUNK_MAX_BYTES):
                    flush()
                if not pending:
                    chapter_index, beat_start = chapter, order
                beat_end = order
                pending.append(word)
            # Prefer a whole sentence/beat boundary over splitting the next one.
            if pending and (len(pending) >= TTS_CHUNK_MAX_WORDS - 45
                            or len(" ".join(pending).encode("utf-8")) >= TTS_CHUNK_MAX_BYTES - 600):
                flush()
    flush()
    if " ".join(chunk.text for chunk in chunks) != text:
        raise ValueError("Bounded TTS must preserve every approved narration word in order")
    return chunks


async def synthesize_recap_segments(artifact, provider, output_path, *, voice, rate, pitch, volume, cache_root=None):
    """Retain validated chunks on failure; never reuse another script or voice.

    Checkpoints contain hashes/timing only, never prompts or credentials. PCM
    decoding removes per-request encoder delay before the file-backed concat.
    The final loudness chain runs once, on the entire concatenated narration.
    """
    from app.assembly.ffmpeg import run_ffmpeg
    from app.production.media_concat import write_concat_manifest
    from app.production.storage import _atomic_write, compute_content_hash
    from app.voice.exceptions import VoiceProviderError
    from app.voice.validator import validate_audio_file

    chunks = recap_tts_chunks(artifact)
    metadata = {"method": "single_request" if len(chunks) == 1 else "chapter_beat_chunks",
                "chunk_count": len(chunks), "resumed_chunks": 0,
                "max_chunk_words": max(len(chunk.text.split()) for chunk in chunks),
                "max_chunk_utf8_bytes": max(len(chunk.text.encode("utf-8")) for chunk in chunks),
                "request_timeout_seconds": TTS_REQUEST_TIMEOUT_SECONDS}
    if len(chunks) == 1:
        try:
            await asyncio.wait_for(provider.generate(text=chunks[0].text, output_path=output_path,
                voice=voice, rate=rate, pitch=pitch, volume=volume), timeout=TTS_REQUEST_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise VoiceProviderError(provider.name, "Recap TTS request timed out; no completed audio was saved") from None
        return metadata
    identity = hashlib.sha256(json.dumps({"version": 1, "provider": provider.name,
        "voice": voice, "rate": rate, "pitch": pitch, "volume": volume, "format": output_path.suffix,
        "chunks": [hashlib.sha256(chunk.text.encode("utf-8")).hexdigest() for chunk in chunks]},
        sort_keys=True).encode()).hexdigest()
    cache = Path(cache_root or output_path.with_name(output_path.stem + ".tts_chunks")) / identity
    cache.mkdir(parents=True, exist_ok=True)
    checkpoint_path = cache / "checkpoint.json"
    completed = {}
    try:
        saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if saved.get("identity") == identity and isinstance(saved.get("chunks"), dict):
            completed = saved["chunks"]
    except (OSError, ValueError, AttributeError):
        pass
    deadline = time.monotonic() + TTS_SYNTHESIS_TIMEOUT_SECONDS
    paths, timings = [], []
    for index, chunk in enumerate(chunks, 1):
        path = cache / f"chunk_{index:04d}.wav"
        record = completed.get(str(index))
        resumed = False
        if isinstance(record, dict) and path.is_file() and record.get("sha256") == compute_content_hash(path):
            validation = validate_audio_file(path)
            resumed = bool(validation.valid and validation.codec == "pcm_s24le"
                           and validation.sample_rate == 48000 and validation.channels == 2)
        raw = cache / f"chunk_{index:04d}.partial.mp3"
        pcm = cache / f"chunk_{index:04d}.partial.wav"
        if not resumed:
            remaining = min(TTS_REQUEST_TIMEOUT_SECONDS, deadline - time.monotonic())
            if remaining <= 0:
                raise VoiceProviderError(provider.name, "Long recap TTS deadline reached; retry resumes validated chunks")
            try:
                await asyncio.wait_for(provider.generate(text=chunk.text, output_path=raw, voice=voice,
                    rate=rate, pitch=pitch, volume=volume), timeout=remaining)
                validation = validate_audio_file(raw)
                if not validation.valid:
                    raise ValueError(f"Recap TTS chunk {index} failed audio validation")
                run_ffmpeg(["-y", "-i", str(raw), "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(pcm)],
                           "decoding a bounded recap TTS segment without encoder-delay padding")
                validation = validate_audio_file(pcm)
                if not validation.valid:
                    raise ValueError(f"Recap TTS chunk {index} failed PCM validation")
                pcm.replace(path)
                completed[str(index)] = {"sha256": compute_content_hash(path), "duration_seconds": validation.duration}
                _atomic_write(checkpoint_path, json.dumps({"identity": identity, "chunks": completed}))
            except asyncio.TimeoutError:
                raise VoiceProviderError(provider.name, f"Recap TTS chunk {index} timed out; retry resumes validated chunks") from None
            finally:
                raw.unlink(missing_ok=True)
                pcm.unlink(missing_ok=True)
        else:
            metadata["resumed_chunks"] += 1
        paths.append(path)
        timings.append({"chunk_index": index, "chapter_index": chunk.chapter_index,
                        "beat_start_order": chunk.beat_start_order, "beat_end_order": chunk.beat_end_order,
                        "word_count": len(chunk.text.split()), "duration_seconds": validation.duration})
    manifest = write_concat_manifest(cache / "segments.ffconcat", paths)
    concatenated = cache / ("concatenated.partial" + output_path.suffix)
    codec = ["-c:a", "libmp3lame", "-b:a", "192k"] if output_path.suffix.lower() == ".mp3" else ["-c:a", "pcm_s24le"]
    try:
        run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", str(manifest), "-vn", "-ar", "48000", "-ac", "2",
                    *codec, str(concatenated)], "concatenating validated long-form TTS before broadcast normalization")
        joined = validate_audio_file(concatenated)
        expected = sum(item["duration_seconds"] for item in timings)
        if not joined.valid or abs(float(joined.duration or 0) - expected) > .25:
            raise ValueError("Concatenated recap TTS must contain every complete audio segment")
        concatenated.replace(output_path)
    finally:
        concatenated.unlink(missing_ok=True)
    metadata.update(concat_method="ffmpeg_concat_demuxer", intermediate_format="pcm_s24le_48000_stereo", chunks=timings)
    return metadata


def measure_audio_loudness(path, *, audio_filter=""):
    """Measure decoded input LUFS, true/sample peaks and RMS, never print PCM.

    astats precedes loudnorm: its RMS/peak numbers describe the INPUT, not
    loudnorm's trial output. Only finite numeric diagnostics are retained.
    """
    from app.assembly.ffmpeg import run_ffmpeg

    filters = ([audio_filter] if audio_filter else []) + [
        "astats=metadata=0:reset=0",
        f"loudnorm=I={BROADCAST_LUFS}:TP={NORMALIZATION_TRUE_PEAK_DB}:LRA=7:print_format=json",
    ]
    result = run_ffmpeg(["-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                         ",".join(filters), "-f", "null", "-"], "measuring recap audio loudness")
    blocks = re.findall(r'\{\s*"input_i".*?\}', result.stderr, re.S)
    if not blocks:
        raise ValueError("FFmpeg returned no recap loudness measurement")
    raw = json.loads(blocks[-1])

    def finite(value):
        number = float(value)
        return number if math.isfinite(number) else None

    def statistic(label):
        values = re.findall(re.escape(label) + r":\s*([^\s]+)", result.stderr)
        return finite(values[-1]) if values else None

    return {
        "integrated_lufs": finite(raw["input_i"]),
        "true_peak_db": finite(raw["input_tp"]),
        "loudness_range_lu": finite(raw["input_lra"]),
        "threshold_lufs": finite(raw["input_thresh"]),
        "target_offset_db": finite(raw["target_offset"]),
        "rms_db": statistic("RMS level dB"),
        "sample_peak_db": statistic("Peak level dB"),
    }


def measured_loudnorm_filter(levels):
    """Second pass must use measurements from the exact same processing chain."""
    keys = ("integrated_lufs", "true_peak_db", "loudness_range_lu", "threshold_lufs", "target_offset_db")
    if any(levels.get(key) is None or not math.isfinite(levels[key]) for key in keys):
        raise ValueError("Silent or non-finite audio cannot be mastered as broadcast narration")
    return (
        f"loudnorm=I={BROADCAST_LUFS}:TP={NORMALIZATION_TRUE_PEAK_DB}:LRA=7:"
        f"measured_I={levels['integrated_lufs']}:measured_TP={levels['true_peak_db']}:"
        f"measured_LRA={levels['loudness_range_lu']}:measured_thresh={levels['threshold_lufs']}:"
        f"offset={levels['target_offset_db']}:linear=true:print_format=json"
    )


def master_narration_audio(output_path, validation):
    """Compress, two-pass normalize and validate the actual encoded narrator.

    This is recap-only and runs AFTER silence tightening/tempo changes. Failure
    leaves the input intact; mock/sandbox audio is never claimed as a narrator.
    """
    from app.assembly.ffmpeg import run_ffmpeg
    from app.voice.validator import validate_audio_file

    output_path = Path(output_path)
    temporary = output_path.with_name(output_path.stem + ".broadcast" + output_path.suffix)
    before = measure_audio_loudness(output_path)
    compressed = measure_audio_loudness(output_path, audio_filter=BROADCAST_VOCAL_CHAIN)
    codec = (["-c:a", "libmp3lame", "-b:a", "192k"] if output_path.suffix.lower() == ".mp3"
             else ["-c:a", "pcm_s24le"])
    try:
        run_ffmpeg(["-y", "-i", str(output_path), "-vn", "-af",
                    BROADCAST_VOCAL_CHAIN + "," + measured_loudnorm_filter(compressed),
                    "-ar", "48000", "-ac", str(validation.channels or 1), *codec, str(temporary)],
                   "mastering recap narrator to -14 LUFS with codec peak headroom")
        measured = validate_audio_file(temporary)
        if not measured.valid or abs(float(measured.duration or 0) - float(validation.duration or 0)) > .15:
            raise ValueError("Broadcast processing must preserve the complete narration duration")
        after = measure_audio_loudness(temporary)
        after = correct_encoded_loudness(temporary, after)
        measured = validate_audio_file(temporary)
        if not measured.valid or abs(float(measured.duration or 0) - float(validation.duration or 0)) > .15:
            raise ValueError("Encoded loudness correction must preserve complete speech and duration")
        validate_broadcast_loudness(after)
        temporary.replace(output_path)
        return measured, {"chain_version": BROADCAST_CHAIN_VERSION, "target_lufs": BROADCAST_LUFS,
                          "true_peak_ceiling_db": BROADCAST_TRUE_PEAK_DB, "vocal_filter": BROADCAST_VOCAL_CHAIN,
                          "normalization": "two_pass_loudnorm", "before": before, "after": after}
    finally:
        temporary.unlink(missing_ok=True)


def correct_encoded_loudness(path, levels):
    """Bounded sub-dB correction for dynamic loudnorm/codec rounding, not AGC.

    A measured second pass can still land ~0.5 LU below target on peaky speech.
    Correct that measured deficit, with a look-ahead limiter and codec headroom.
    """
    from app.assembly.ffmpeg import run_ffmpeg
    path = Path(path)
    temporary = path.with_name(path.stem + ".level" + path.suffix)
    try:
        for _ in range(3):
            if levels["integrated_lufs"] is None or abs(BROADCAST_LUFS - levels["integrated_lufs"]) <= .15:
                break
            gain = max(-2.0, min(2.0, BROADCAST_LUFS - levels["integrated_lufs"]))
            limit = 10 ** (NORMALIZATION_TRUE_PEAK_DB / 20)
            codec = (["-c:a", "libmp3lame", "-b:a", "192k"] if path.suffix.lower() == ".mp3"
                     else ["-c:a", "aac", "-b:a", "192k"] if path.suffix.lower() == ".mp4"
                     else ["-c:a", "pcm_s24le"])
            maps = ["-map", "0:v:0", "-c:v", "copy"] if path.suffix.lower() == ".mp4" else ["-vn"]
            run_ffmpeg(["-y", "-i", str(path), *maps, "-map", "0:a:0", "-af",
                        f"volume={gain:.3f}dB,alimiter=limit={limit:.6f}:level=0:latency=1",
                        "-ar", "48000", *codec, str(temporary)], "correcting measured encoded recap loudness")
            levels = measure_audio_loudness(temporary)
            temporary.replace(path)
        return levels
    finally:
        temporary.unlink(missing_ok=True)


def validate_broadcast_loudness(levels):
    if (levels.get("integrated_lufs") is None or abs(levels["integrated_lufs"] - BROADCAST_LUFS) > .5
            or levels.get("true_peak_db") is None or levels["true_peak_db"] > BROADCAST_TRUE_PEAK_DB):
        raise ValueError("Encoded recap audio must measure -14 LUFS (+/-0.5 LU) and at most -1 dBTP")


def tighten_narration_silence(output_path, validation):
    """Keep short conversational pauses; reduce only long near-silent gaps.

    The -50 dB threshold protects quiet speech. A 0.55-second trigger preserves
    ordinary punctuation pauses; longer dead gaps retain a 0.16-second breath.
    Reverse trimming also removes excessive final silence without chopping speech.
    """
    from app.assembly.ffmpeg import run_ffmpeg
    from app.voice.validator import validate_audio_file

    original = float(validation.duration or 0)
    tightened = output_path.with_name(output_path.stem + ".tight" + output_path.suffix)
    trim = ("silenceremove=start_periods=1:start_duration=0.03:start_threshold=-50dB:start_silence=0.04:"
            "stop_periods=-1:stop_duration=0.55:stop_threshold=-50dB:stop_silence=0.16,"
            "areverse,silenceremove=start_periods=1:start_duration=0.03:start_threshold=-50dB:start_silence=0.04,areverse")
    try:
        run_ffmpeg(["-y", "-i", str(output_path), "-vn", "-af", trim, str(tightened)],
                   "tightening recap breaths without per-beat silence padding")
        measured = validate_audio_file(tightened)
        if not measured.valid or float(measured.duration or 0) < original * .65:
            raise ValueError("Tightened narration failed validation; speech must not be truncated")
        tightened.replace(output_path)
        return measured, max(0.0, original - float(measured.duration or 0))
    finally:
        tightened.unlink(missing_ok=True)