"""Pure 30fps/48kHz beat clock. No media, providers or filesystem side effects."""
from __future__ import annotations

from app.repurpose.models import RepurposeError, rendered_duration_bounds

FPS = 30
SAMPLE_RATE = 48000
SAMPLES_PER_FRAME = SAMPLE_RATE // FPS
PIPELINE_VERSION = "beat-locked-v2.3"
TTS_CACHE_VERSION = "beat-locked-v2.2"
MAX_VIDEO_SLOWDOWN = 2.0


def narration_tempo(beats, samples, target_seconds):
    """A bounded speech adjustment, not silence padding or a script rewrite."""
    minimum, maximum = rendered_duration_bounds(target_seconds)
    original = sum(samples) / SAMPLE_RATE
    framed = sum((n + SAMPLES_PER_FRAME - 1) // SAMPLES_PER_FRAME for n in samples) / FPS
    speed = 1.0
    if framed < minimum:
        speed = max(.85, original / (minimum + .25))
    elif framed > maximum:
        speed = min(1.20, original / (maximum - .25))
    # Never slow a voice so far that a beat would require unrelated footage.
    required = max(n / SAMPLE_RATE / ((b.coverage_end_seconds - b.focal_source_seconds) * MAX_VIDEO_SLOWDOWN)
                   for b, n in zip(beats, samples))
    speed = max(speed, required + .002 if required > speed else speed)
    if not .85 <= speed <= 1.20:
        raise RepurposeError("NARRATION_PACING_OUT_OF_BOUNDS", "Complete speech cannot fit verified scene coverage with bounded pacing", 422)
    return speed


def resolve_timeline(beats, audio_samples, *, occupied=()):
    if len(beats) != len(audio_samples) or not 4 <= len(beats) <= 6:
        raise RepurposeError("INVALID_BEAT_TIMELINE", "Four to six measured beats are required", 422)
    result, frame, used = [], 0, list(occupied)
    if [b.order for b in beats] != list(range(1, len(beats) + 1)):
        raise RepurposeError("INVALID_BEAT_TIMELINE", "Measured beats must have consecutive story order", 422)
    for beat, samples in zip(beats, audio_samples):
        if type(samples) is not int or samples <= 0:
            raise RepurposeError("INVALID_BEAT_AUDIO", "Beat audio requires positive PCM sample counts", 422)
        count = (samples + SAMPLES_PER_FRAME - 1) // SAMPLES_PER_FRAME
        duration = count / FPS
        start = beat.focal_source_seconds
        source_duration = min(duration, beat.coverage_end_seconds - start)
        end = start + source_duration
        factor = duration / source_duration
        if factor > MAX_VIDEO_SLOWDOWN + 1e-8:
            raise RepurposeError("BEAT_FOOTAGE_TOO_SHORT", f"Beat {beat.order} needs {duration:.3f}s of verified footage after its action anchor; revise the script or coverage", 422)
        if any(min(end, b) - max(start, a) > .05 for a, b in used):
            raise RepurposeError("BEAT_FOOTAGE_OVERLAP", "Measured beats repeat reserved source footage", 422)
        result.append({"beat_order": beat.order, "evidence_id": beat.evidence_id,
            "start_seconds": start, "end_seconds": end, "output_start": frame / FPS,
            "output_duration": duration, "output_start_frame": frame, "frame_count": count,
            "audio_samples": samples, "padded_audio_samples": count * SAMPLES_PER_FRAME,
            "tail_padding_samples": count * SAMPLES_PER_FRAME - samples,
            "anchor_precision": beat.anchor_precision, "beat_id": beat.beat_id, "visual_anchor_id": beat.visual_anchor_id,
            "focal_source_pts": beat.focal_source_pts, "source_time_base": beat.source_time_base,
            "video_tempo_factor": factor})
        used.append((start, end))
        frame += count
    return result


def validate_execution(clip, cuts):
    """Recompute accepted timing from measured audio, not mutable media sidecars."""
    if not clip.beats or len(cuts) != len(clip.beats):
        raise ValueError("Beat execution count mismatch")
    expected = resolve_timeline(clip.beats, [c["audio_samples"] for c in cuts])
    for planned, actual in zip(expected, cuts):
        if any(actual.get(k) != v for k, v in planned.items()):
            raise ValueError("Beat execution clock/anchor changed")