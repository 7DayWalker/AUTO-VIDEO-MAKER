"""Cache-only, selective text polish of an accepted recap's hook and finale.

No image IO or vision provider exists in this tool. Already validated beats and
all Stage 1 observations survive. Partial text results resume without rewriting
the chapters. Use --narrative-end only with a locally verified story boundary.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def polish(production_id, unit_id, *, narrative_end=None):
    from app.content.model_resolver import resolve_model
    from app.production.models import SourceTranscript
    from app.production.narration import validate_narration_artifact
    from app.production.narration_chapters import _ChapterPlan, _chapter_beats, _write_checkpoint
    from app.production.narration_real import (
        _call_json, STORYTELLER_SYSTEM_PROMPT, cold_open_instruction, ending_instruction,
        resolve_ending_anchor, validate_true_ending, align_final_story_clip, validate_storyteller_language,
    )
    from app.production.recap_budget import validate_recap_depth
    from app.production.source import find_source_asset
    from app.production.storage import (
        load_narration_artifact, load_production, save_narration_artifact, unit_plan_dir,
    )
    from app.production.summary_video import narration_content_hash
    from app.production.visual_cache import load_frame_observations, visual_source_identity
    from app.content.exceptions import LLMModelUnavailableError, LLMOutputTruncatedError

    root = ROOT / "productions"
    artifact = load_narration_artifact(production_id, unit_id, root=root)
    if artifact.summary_understanding_model_id != "gpt-6.1-sol" or artifact.planning_strategy != "multi_chapter":
        raise ValueError("Selective closeout requires an existing multi-chapter gpt-6.1-sol recap")
    base, plan = root / production_id, unit_plan_dir(production_id, unit_id, root=root)
    asset = find_source_asset(load_production(production_id, root=root), artifact.source_asset_id,
                              root=root, stored_path=artifact.source_media_path)
    transcript = SourceTranscript.model_validate_json((base / artifact.source_transcript_path).read_text(encoding="utf-8"))
    validate_narration_artifact(artifact, source_asset=asset, source_transcript=transcript)
    if (artifact.ending_anchor is not None and artifact.cold_open_hook is not None
            and (narrative_end is None or abs(artifact.ending_anchor.narrative_end_seconds - narrative_end) < .01)):
        validate_true_ending(artifact.beats, artifact.ending_anchor, require_visual=True)
        print("Selective narration polish already validated; no text or vision calls.", flush=True)
        return artifact
    identity = hashlib.sha256(json.dumps({"version": 1, "source": visual_source_identity(asset, transcript),
        "script": narration_content_hash(artifact), "narrative_end": narrative_end}, sort_keys=True).encode()).hexdigest()
    checkpoint = plan / "narration_polish.partial.json"
    cached = {}
    if checkpoint.is_file():
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        if data.get("identity") == identity:
            cached = data.get("validated_text", {})
    import threading
    from types import SimpleNamespace
    lock = threading.Lock()
    calls = 0

    def evidence(chapter):
        frames = [(ts, "retained-observation-no-image-io") for ts in chapter.frame_timestamps]
        notes = load_frame_observations(plan / "frame_observations.json", asset, transcript, frames,
                                       start=chapter.start_seconds, end=chapter.end_seconds)
        if notes is None:
            raise ValueError("Selective polish requires all retained Stage 1 notes; no live vision fallback is allowed")
        return frames, notes

    opening_frames, opening_notes = evidence(artifact.chapters[0])
    ending_frames, ending_notes = evidence(artifact.chapters[-1])
    anchor = resolve_ending_anchor(asset, transcript, ending_notes, narrative_end_seconds=narrative_end,
        detection_method="operator_verified_narrative_boundary" if narrative_end is not None else None)
    final_orders = [beat.order for beat in artifact.beats[-3:]]
    untouched = [beat.model_dump() for beat in artifact.beats[1:-3]]
    first_end = min(ref.start_seconds for ref in artifact.beats[1].source_refs)
    ending_start = min(ref.start_seconds for ref in artifact.beats[-3].source_refs)

    def write(kind):
        nonlocal calls
        first = kind == "hook"
        start, end = (0, first_end) if first else (ending_start, anchor.narrative_end_seconds)
        frames, notes = (opening_frames, opening_notes) if first else (ending_frames, ending_notes)
        notes = [note for note in notes if note.get("citable", True) and not note.get("credits_only")
                 and start <= note["timestamp_seconds"] < end]
        rows = [{"id": i, "start_seconds": max(start, seg.start), "end_seconds": min(end, seg.end), "text": seg.text}
                for i, seg in enumerate(transcript.segments) if seg.start < end and seg.end > start]
        payload = {"start_seconds": start, "end_seconds": end, "visual_action_notes": notes,
            "transcript_segments": rows,
            "prior_continuity_not_citable": artifact.chapters[-1].summary if not first else "",
            "preceding_narration_not_citable": artifact.beats[-4].narration_text if not first else "",
            "next_narration_not_citable": artifact.beats[1].narration_text if first else "",
            "writing_requirement": cold_open_instruction() if first else ending_instruction(anchor),
            "hook_quality_requirement": ("Use a specific evidence-backed contrast or question to frame the "
                "central conflict/discovery, not a routine guard's instruction or a caption. "
                "Do not import story facts that are not in the supplied opening notes.") if first else "",
            "beat_count": 1 if first else 3,
            "word_range": [30, 48] if first else [85, 115],
            "closure_requirement": ("State the outcome supported by the supplied ending evidence. If this is a "
                "Part I/unresolved ending, explicitly preserve its cliffhanger in the LAST sentence. "
                "Never invent anyone's survival, death or victory. Do not narrate an end card as story.") if not first else ""}
        prompt = "Polish only this accepted recap passage from retained evidence:\n" + json.dumps(payload, ensure_ascii=False)
        prompt += "\nReturn JSON only, with summary and beats matching this schema:\n" + json.dumps(_ChapterPlan.model_json_schema())
        provider = resolve_model("gpt-6.1-sol", mode="exact", try_next_on_failure=True).provider_instance
        output_tokens = 4096
        for attempt in range(2):
            data = cached.get(kind)
            if data is None:
                with lock:
                    calls += 1
                print("Requesting text-only Sol polish:", kind, "attempt", attempt + 1, flush=True)
                try:
                    data = _call_json(provider, STORYTELLER_SYSTEM_PROMPT, prompt, max_tokens=output_tokens)
                except LLMOutputTruncatedError:
                    if attempt:
                        raise
                    output_tokens = 8192
                    print("Incomplete text passage; expanding same-model output budget:", kind, flush=True)
                    continue
                except LLMModelUnavailableError:
                    # A brief gateway 400/404 is not evidence loss. One bounded
                    # same-model retry; never fall back to a different model.
                    if attempt:
                        raise
                    print("Transient same-model route rejection:", kind, flush=True)
                    time.sleep(2)
                    continue
            try:
                parsed = _ChapterPlan.model_validate(data)
                expected = 1 if first else 3
                if len(parsed.beats) != expected:
                    raise ValueError("Selective passage has the wrong beat count")
                allowed_frames, allowed_rows = {n["frame_id"] for n in notes}, {r["id"] for r in rows}
                for item in parsed.beats:
                    for citation in item.source_refs:
                        if (not set(citation.frame_ids).issubset(allowed_frames)
                                or not set(citation.transcript_segment_ids).issubset(allowed_rows)):
                            raise ValueError("Selective passage cites evidence outside its supplied scene")
                _, beats = _chapter_beats(data, asset, rows, frames, start, end, visual=True,
                    offset=0 if first else final_orders[0] - 1, validate_coverage=False)
                count = len(" ".join(b.narration_text for b in beats).split())
                low, high = payload["word_range"]
                if not low <= count <= high:
                    raise ValueError(f"Selective passage requires {low}-{high} words; received {count}")
                validate_storyteller_language(SimpleNamespace(beats=beats))
                if not first:
                    align_final_story_clip(beats, anchor, require_visual=True)
                with lock:
                    cached[kind] = data
                    _write_checkpoint(checkpoint, {"identity": identity, "validated_text": cached})
                print("Validated retained-evidence passage:", kind, "words", count, flush=True)
                return beats
            except ValueError as exc:
                with lock:
                    cached.pop(kind, None)
                if attempt:
                    raise
                prompt += "\nRepair once: " + str(exc) + ". Keep the same supplied evidence and word range."
    with ThreadPoolExecutor(max_workers=2) as pool:
        hook, finale = list(pool.map(write, ("hook", "finale")))
    artifact.beats = hook + artifact.beats[1:-3] + finale
    if [beat.model_dump() for beat in artifact.beats[1:-3]] != untouched:
        raise ValueError("Selective polish must preserve all other accepted beats")
    artifact.narration_text = " ".join(beat.narration_text for beat in artifact.beats)
    artifact.source_refs = [ref for beat in artifact.beats for ref in beat.source_refs]
    artifact.transcript_refs = [str(i) for i in sorted({i for ref in artifact.source_refs for i in ref.transcript_segment_ids})]
    artifact.ending_anchor = anchor
    artifact.cold_open_hook = re.split(r"(?<=[.!?])\s+", hook[0].narration_text, maxsplit=1)[0]
    for chapter in artifact.chapters:
        chapter_beats = [beat for beat in artifact.beats if chapter.beat_start_order <= beat.order <= chapter.beat_end_order]
        chapter.word_count = sum(len(beat.narration_text.split()) for beat in chapter_beats)
    artifact.chapters[-1].summary = " ".join(beat.narration_text for beat in finale)[:600]
    artifact.revised_at = datetime.now().isoformat()
    artifact.audio_status = "pending"
    artifact.text_storyteller_calls += calls
    validate_true_ending(artifact.beats, anchor, require_visual=True)
    validate_recap_depth(artifact, artifact.narration_budget, transcript, asset.duration)
    validate_narration_artifact(artifact, source_asset=asset, source_transcript=transcript)
    save_narration_artifact(artifact, production_id=production_id, unit_id=unit_id, root=root,
                            source_asset=asset, source_transcript=transcript)
    report = {"new_text_calls": calls, "new_vision_calls": 0, "unchanged_beats": len(untouched),
              "cached_stage1_chapters": len(artifact.chapters), "cold_open_hook": artifact.cold_open_hook,
              "ending_anchor": anchor.model_dump(), "final_beats": [b.model_dump() for b in finale]}
    (plan / "narration_polish.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    checkpoint.unlink(missing_ok=True)
    print(json.dumps({k: v for k, v in report.items() if k != "final_beats"}), flush=True)
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("production_id")
    parser.add_argument("--unit-id", default="unit-001")
    parser.add_argument("--narrative-end", type=float)
    args = parser.parse_args()
    if not re.fullmatch(r"prd_[a-z0-9_]+", args.production_id) or not re.fullmatch(r"unit-\d{3}", args.unit_id):
        parser.error("Use canonical production/unit IDs")
    try:
        polish(args.production_id, args.unit_id, narrative_end=args.narrative_end)
    except Exception as exc:
        # Upstream exception bodies/tracebacks can contain requests. Print type
        # only; validated partial passages remain available for a safe retry.
        print(json.dumps({"polish_failed": type(exc).__name__, "action": "Inspect retained checkpoint; no vision calls made"}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())