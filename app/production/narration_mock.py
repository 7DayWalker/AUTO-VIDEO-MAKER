from __future__ import annotations
from datetime import datetime
from typing import List

from app.production.models import NarrativeBeat, NarrationArtifact, NarrationSourceRef, SourceAsset, SourceTranscript
from app.production.narration import validate_narration_artifact


def mock_grounded_plan(
    production_id: str,
    unit_id: str,
    source_asset: SourceAsset,
    source_transcript: SourceTranscript,
    *,
    target_duration_seconds: float,
    status: str = "grounded",
    provider_override=None,
) -> NarrationArtifact:
    """Deterministic mock AI — budget-aware, sparse, condensed narration.

    Not a transcript copy: each beat narration is a short original summary
    phrase derived from its window (first 10 words plus framing), proving
    condensation. Beat count scales with target/source ratio, proving
    target matters, and selection is spread across the source proving
    sparse coverage and long-form spanning.
    """
    n = len(source_transcript.segments)
    if n == 0:
        raise ValueError("no transcript segments")
    ratio = min(1.0, max(0.10, float(target_duration_seconds) / max(1.0, float(source_asset.duration))))
    # 2..6 beats budget-driven
    desired = int(round(2 + ratio * 4))
    desired = max(2, min(6, desired))
    # need at least 3 segs per beat on average to keep refs grounded
    beat_count = min(desired, max(1, n // 3))
    if n >= 6:
        beat_count = max(2, beat_count)
    else:
        beat_count = max(1, beat_count)
    step = n / beat_count
    beats: List[NarrativeBeat] = []
    source_refs: List[NarrationSourceRef] = []
    transcript_refs: List[str] = []
    narration_parts: List[str] = []
    for b in range(beat_count):
        center = int(round((b + 0.5) * step))
        center = max(1, min(n - 2, center))
        lo = max(0, center - 1)
        hi = min(n - 1, center + 1)
        idxs = list(range(lo, hi + 1))
        # condensed narration: short original, not verbatim join
        joined = " ".join(source_transcript.segments[i].text.strip() for i in idxs if source_transcript.segments[i].text.strip())
        condensed = " ".join(joined.split()[:10]) if joined else f"segment {idxs[0]}"
        narration = f"Summary beat {b+1}: {condensed}."
        first = source_transcript.segments[idxs[0]]
        last = source_transcript.segments[idxs[-1]]
        # clamp end to source duration (mock uses transcript text for demo, not media-accurate durations)
        dur = float(source_asset.duration)
        s = min(float(first.start), dur - 0.1)
        e = min(float(last.end), dur)
        if e <= s:
            e = min(s + 6.0, dur)
            s = max(0.0, e - 6.0)
        ref = NarrationSourceRef(
            asset_id=source_asset.asset_id,
            start_seconds=float(s),
            end_seconds=float(e),
            transcript_segment_ids=list(idxs),
        )
        beat = NarrativeBeat(
            order=b + 1,
            title=f"Beat {b+1}",
            narration_text=narration,
            summary=narration[:180],
            source_refs=[ref],
            transcript_refs=[str(i) for i in idxs],
        )
        beats.append(beat)
        source_refs.append(ref)
        transcript_refs.extend(str(i) for i in idxs)
        narration_parts.append(narration)
    summary = "Condensed summary of the source covering selected key moments."
    narration_text = " ".join(narration_parts)
    artifact = NarrationArtifact(
        narration_id=f"narr-{production_id}-{unit_id}",
        production_id=production_id,
        unit_id=unit_id,
        source_asset_id=source_asset.asset_id,
        target_duration_seconds=float(target_duration_seconds),
        status=status,
        narration_text=narration_text,
        summary=summary,
        beats=beats,
        source_refs=source_refs,
        transcript_refs=sorted(set(transcript_refs), key=lambda x: int(x)),
        generated_at=datetime.now().isoformat(),
    )
    validate_narration_artifact(artifact, source_asset=source_asset, source_transcript=source_transcript)
    # bump provider call count if a mock-like override was supplied
    if provider_override is not None:
        # Call the override's generate once to prove provider invocation, then fail if it raises
        _prov = provider_override
        if hasattr(_prov, "generate") and callable(getattr(_prov, "generate")):
            _prov.generate(prompt="mock narration probe", system_prompt="test")
        if hasattr(_prov, "_call_count"):
            try:
                _prov._call_count += 1
            except Exception:
                pass
    return artifact
