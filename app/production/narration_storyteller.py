"""Bounded parallel TEXT batches for slow reasoning-model chapter writers.

Visual inspection is already complete. Each batch receives a disjoint portion
of the chapter's timestamped notes and every overlapping dialogue segment. No
final whole-chapter rewrite loses detail; validated beats are joined in order.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import copy
import json
import logging
import time

from app.content.exceptions import LLMConnectionError, LLMOutputTruncatedError
from app.content.providers.agentrouter import require_text_only
from app.production.narration_real import (
    NarrationModelOutputError, _call_json, validate_storyteller_language, standard_transient_failure_category,
)
from app.production.recap_budget import validate_recap_depth

logger = logging.getLogger(__name__)


def write_storyteller_chapter(prov, system, payload, *, asset, frames, local_budget, deadline,
                              cached_batches=None, on_batch=None, context_tokens=None):
    from app.production.narration_chapters import _ChapterPlan, _chapter_beats, _artifact, _share

    count = max(1, min(4, local_budget.min_beats // 2))
    start, end = payload["start_seconds"], payload["end_seconds"]
    # Identity/plot continuity is read-only context for every parallel batch;
    # only the local evidence IDs below are citable. This avoids each batch
    # rediscovering characters or retelling another batch's action.
    continuity_map = [{"timestamp_seconds": note["timestamp_seconds"], "action": note["action"]}
                      for note in payload["visual_action_notes"]]

    def write(index):
        # Each worker owns its request timeout; do not race on a shared adapter.
        provider = copy(prov) if hasattr(prov, "_timeout") else prov
        left = start + (end - start) * index / count
        right = start + (end - start) * (index + 1) / count
        rows = [{**row, "start_seconds": max(left, row["start_seconds"]),
                 "end_seconds": min(right, row["end_seconds"])} for row in payload["transcript_segments"]
                if row["start_seconds"] < right and row["end_seconds"] > left]
        notes = [note for note in payload["visual_action_notes"] if note.get("citable", True)
                 and left <= note["timestamp_seconds"] < right]
        allowed_frames = {note["frame_id"] for note in notes}
        allowed_transcript = {row["id"] for row in rows}
        budget = local_budget.model_copy(update={
            "target_duration_seconds": local_budget.target_duration_seconds / count,
            "min_beats": max(1, _share(local_budget.min_beats, index, count)),
            "max_beats": max(1, _share(local_budget.max_beats, index, count)),
            "min_narration_words": _share(local_budget.min_narration_words, index, count),
            "max_narration_words": _share(local_budget.max_narration_words, index, count),
            "require_story_coverage": False,
        })
        preferred_words = _share(payload["preferred_word_count"], index, count)
        evidence = {
            "chapter_index": payload["chapter_index"], "batch_index": index + 1,
            "start_seconds": left, "end_seconds": right,
            "preferred_beat_count": budget.max_beats, "preferred_word_count": preferred_words,
            "transcript_segments": rows, "visual_action_notes": notes,
            "frames": [frame for frame in payload["frames"] if frame["id"] in allowed_frames],
            "prior_chapter_summaries": payload["prior_chapter_summaries"],
            "chapter_continuity_context_not_citable": continuity_map,
            "previous_narration_tail": payload["previous_narration_tail"] if index == 0 else "",
            **({"repair_instructions": payload["repair_instructions"]} if payload.get("repair_instructions") else {}),
        }
        prompt = ("Write a short consecutive movie-recap passage from this evidence:\n"
                  + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
                  + "\nRequired JSON schema:\n" + json.dumps(_ChapterPlan.model_json_schema(), separators=(",", ":"))
                  + f"\nWrite {budget.min_beats}-{budget.max_beats} beats, about {preferred_words} total spoken words "
                  + f"(allowed {budget.min_narration_words}-{budget.max_narration_words}). "
                  + "Each beat has two or three connected sentences. Narrate decisions, stakes and consequences; "
                  + "cite only this batch's evidence IDs. Do not recap other batches or announce the chapter. "
                  + "Use the chapter continuity context only to keep roles and causal transitions consistent, not as new citable evidence. "
                  + "Retained Pro notes describe a SCENE tied to its cited frames, not everything in each single image. "
                  + "Do not invent action for unavailable frames. "
                  + "Draft directly, without trying to hit exact per-sentence word counts.")
        calls = 0
        cached = (cached_batches or {}).get(str(index))
        if not isinstance(cached, dict) or not isinstance(cached.get("plan"), dict):
            cached = None
        output_failures = 0
        for attempt in range(4):
            if time.monotonic() >= deadline:
                raise LLMConnectionError(prov.name, "Storyteller batch deadline exhausted")
            require_text_only(prompt)
            max_tokens = 8192 if output_failures else 4096
            if len((system + prompt).encode("utf-8")) + max_tokens + 256 > (context_tokens or 32768):
                raise NarrationModelOutputError("Storyteller batch exceeds its bounded context budget")
            if hasattr(provider, "_timeout"):
                provider._timeout = min(provider._timeout, max(.1, deadline - time.monotonic()))
            output_received = False
            try:
                if cached is not None:
                    data = cached["plan"]
                    calls = max(1, int(cached.get("calls", 1)))
                    cached = None
                else:
                    calls += 1
                    data = _call_json(provider, system, prompt, max_tokens=max_tokens)
                output_received = True
                plan = _ChapterPlan.model_validate(data)
                for beat in plan.beats:
                    for citation in beat.source_refs:
                        if (not set(citation.frame_ids).issubset(allowed_frames)
                                or not set(citation.transcript_segment_ids).issubset(allowed_transcript)):
                            raise NarrationModelOutputError("Storyteller batch cites evidence outside its supplied window")
                # Coverage is validated again on the assembled chapter. Two-beat
                # batches cannot satisfy a chapter's 40%-gap rule individually.
                _, beats = _chapter_beats(data, asset, rows, frames, left, right, visual=True, offset=0, validate_coverage=False)
                artifact = _artifact("prd_storyteller_validation", "unit-001", asset, beats,
                                     budget.target_duration_seconds, plan.summary, "grounded")
                try:
                    validate_recap_depth(artifact, budget, None, asset.duration)
                except ValueError as exc:
                    # These fixed diagnostics contain counts/constraints only,
                    # allowing a repair to correct its actual depth mismatch.
                    raise NarrationModelOutputError(str(exc)) from None
                validate_storyteller_language(artifact)
                if on_batch is not None:
                    on_batch(index, {"plan": plan.model_dump(), "calls": calls})
                return plan, calls
            except (ValueError, LLMOutputTruncatedError) as exc:
                if not output_received and isinstance(exc, ValueError) and not isinstance(exc, NarrationModelOutputError):
                    raise  # Local/provider misuse is not repaired as generated text.
                output_failures += 1
                if output_failures >= 2 or attempt == 3:
                    if isinstance(exc, NarrationModelOutputError):
                        raise
                    raise NarrationModelOutputError("Storyteller batch failed its output/word/citation validation") from None
                # No rejected draft is appended: keep retries compact and never
                # turn a small request back into a chapter-sized reasoning task.
                diagnostic = str(exc) if isinstance(exc, NarrationModelOutputError) else "Invalid batch shape or word/sentence budget"
                prompt += "\nRewrite this batch once: " + diagnostic + ". Keep the same evidence and word range."
            except Exception as exc:
                from app.providers.fallback_policy import FailureCategory
                category = standard_transient_failure_category(exc)
                # Adapter diagnostics here are fixed status/type facts, never
                # upstream bodies, prompts, headers or credential-bearing URLs.
                logger.warning("Storyteller batch transport failed: chapter=%d batch=%d attempt=%d type=%s category=%s",
                               payload["chapter_index"], index + 1, attempt + 1, type(exc).__name__, category)
                if (attempt == 3 or time.monotonic() >= deadline or category not in {
                    FailureCategory.NETWORK, FailureCategory.TIMEOUT, FailureCategory.PROVIDER_5XX,
                    FailureCategory.PROVIDER_UNAVAILABLE,
                }):
                    raise
                time.sleep(min(2 ** attempt, max(0, deadline - time.monotonic())))
        raise NarrationModelOutputError("Storyteller batch did not complete")

    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(write, range(count)))
    return {"summary": " ".join(plan.summary for plan, _ in results)[:600],
            "beats": [beat.model_dump() for plan, _ in results for beat in plan.beats]}, sum(calls for _, calls in results)