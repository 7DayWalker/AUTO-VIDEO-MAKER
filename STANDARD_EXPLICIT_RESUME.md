# Standard storyteller and explicit Resume (backend only)

## Root cause

`pending_story.request_state.calls` was loaded as a permanent per-chapter credit
limit. A failed request persisted `calls: 2`; a subsequent Generate/Resume POST
read it and failed before HTTP. A bounded retry policy had inadvertently become
a permanent project lockout.

Narration now creates a context-local request identity at the public build
boundary. New invocations reset unfinished counters and retain the previous
count as diagnostics. Nested calls and automatic retries retain the same identity
and cannot renew their allowance. Completed chapters, validated partial results,
source/transcript identity and per-unit single-flight remain protected. Legacy
checkpoints migrate automatically, with no manual JSON edits.

Because the frozen client sends identical POSTs for manual and transport-resume
commands, the server treats each new admitted POST as a new invocation. It cannot
infer a human click without a separate client-provided idempotency/intent key.
Within one invocation, Standard makes one request per chapter and at most one
repair or transport retry; there is no nested retry multiplication.
Pro follows the same two-request ceiling, including truncation. Old saved
recovery halves remain resumable, but no new three-call split is initiated.
Provider/model fallback cannot renew an already-spent chapter allowance.

## Shared storyteller engine

Long-form Standard now uses `write_storyteller_chapter`, the existing sequential
Pro text writer. Standard receives only the chapter's dialogue, with every global
segment ID and its clipped `[start-end]` timestamp, short continuity context and
one JSON output example. It receives no images, image descriptions, full JSON
schema, or repeated arithmetic/word-count instructions. Sol/Astra requests retain
16,384 output tokens, explicit low reasoning and independent 300-second sockets.
The already-correct provider/proxy transport settings were verified, not replaced.

### Accept useful paid output

Standard validates grounding and chronology, not exact editorial word/beat
counts. An approximately 180-word chapter requested at 250 words is accepted
without repair if it covers its dialogue window. Nominal budgets remain visible;
`narration_quality` records actual counts and deviation. Grossly empty/oversized
output still fails. A missing continuity summary can be derived from accepted
prose; common `text`/`narration` aliases and timestamp-only references normalize
to the same canonical representation. Unknown IDs, out-of-window timestamps,
wrong-source citations, visual citations on Standard, and skipped timeline
coverage are not waived. Pro's existing grounding/depth checks are unchanged.

Auto and Feature can reuse each other's chapter checkpoints only when all
effective budget values, source/transcript/model identity and windows match.
Genuinely different recap lengths or evidence never qualify.

The last 36 seconds of this movie have no transcribed dialogue. Standard's
closing anchor is therefore explicitly `last_transcribed_dialogue`, not a claim
of inspected credits or an invented silent resolution. Pro's frame-based ending
continues to use its own visual evidence. Accepted length variation also cannot
trigger an expensive script rewrite after TTS: if the runtime/cadence targets
cannot intersect, Standard keeps natural measured audio and records the variance
instead of padding, truncating or fabricating narration.

## Live project identification

At inspection, the backend did **not** contain three saved Standard chapters.
The 40% indicator meant source and transcript (two of five workflow steps):

- `prd_20261106_kung_fu_summary_standard_verification_134`: Standard Auto
  (effective Feature), **2 of 8 chapters saved**, Chapter 3 exhausted at two
  attempts. Its original source and 1,023-segment transcript are retained.
- `prd_20261107_chinese_dramathey_mocked_his_kung_fuunti_477`: Standard Feature,
  **0 of 8 chapters saved**, Chapter 1 exhausted; its separately transcribed
  813-segment dialogue is retained rather than substituted.
- `prd_20261106_they_mocked_his_kung_fu_until_he_kod_the_141`: completed Pro
  Blockbuster (10 chapters, 18m04.8s). Source, transcript, script, audio and
  video metadata are protected and were not regenerated.

The older Standard resume completed its remaining six chapters in exactly six
successful calls, retaining Chapters 1–2 unchanged: **8 chapters, 80 beats,
2,281 words**. The newer Feature project generated all eight chapters in eight
successful calls: **80 beats, 2,283 words**. Neither narration run needed a retry
or discarded a paid completion. Live media results are reported after TTS/
assembly verification; an Edge timeout on the newer project's second audio
chunk was resumed without rewriting narration or its completed audio chunk.

The resumed Standard project completed its MP4: **798.7 seconds (13m18.7s),
617,483,829 bytes, 1920×1080 H.264/AAC**. Its original two chapters are unchanged.
Output: `C:\Projects\AUTO-VIDEO-MAKER\productions\prd_20261106_kung_fu_summary_standard_verification_134\units\unit-001\plan\narrated_summary.mp4`.
During the second project's mastering the workstation exhausted physical memory
and a local FFmpeg pass hit its existing 600-second bound. Its final local-only
resume uses the supported process environment `FFMPEG_TIMEOUT=1800`; no .env or
credentials were edited. Chapter LLM read timeouts remain 300 seconds.
The newer Feature voiceover completed at **800.718 seconds**. A subsequent local
30-second ffprobe timeout prompted bounded optional resource settings:
`FFPROBE_TIMEOUT` (1–300 seconds, default 30) and `AVM_RECAP_RENDER_WORKERS`
(1–4, default 4). The final assembly-only retry uses 180 seconds and one worker;
it reuses the completed narration and mastered audio without new paid calls.
That assembly-only retry completed successfully: **800.8 seconds (13m20.8s),
607,752,643 bytes, 1920×1080 H.264/AAC**, with 8 chapters, 80 beats and 2,283 words.
Output: `C:\Projects\AUTO-VIDEO-MAKER\productions\prd_20261107_chinese_dramathey_mocked_his_kung_fuunti_477\units\unit-001\plan\narrated_summary.mp4`.
All eight script chapters are unchanged through the audio/assembly recovery.

## Verification and scope

The required three hermetic suites pass **280 tests with zero external network
attempts**. The **15 new explicit-resume/storyteller regressions** also pass,
with zero external network attempts. Additional tests cover new-POST resets, same-request/nested limits,
checkpoint preservation, compact wire prompts, 180-word and over-target chapters,
timestamp normalization, wrong-source rejection, the dialogue-ending anchor,
Auto/Feature equivalence and post-TTS length variance. All tests use synthetic
providers and the hermetic runner; live completion is a separate deliberate task.
The final idle reruns passed **280 required + 297 additional targeted tests**
(577 total, zero external attempts). The intermediate loaded-workstation run
had three wall-clock/loopback timing failures and is retained in diagnostics;
it is not counted as successful validation. The completed Standard MP4 passed
full audio/video decode with zero errors and measures **−14.01 LUFS / −1.50 dBTP**.
Both the completed Standard and unchanged Pro media endpoints support HTTP 206.
The newer Standard MP4 also passed a full decode with zero errors and measures
**−14.02 LUFS / −1.42 dBTP**. All three video URLs were switched, played and
sought near their endings in isolated Chrome desktop/mobile viewports; all six
checks passed with readyState 4, no media errors and zero POSTs. These browser
checks use a plain video element against Core only, never load or change K3.

No K3 frontend files are read, edited, built, committed or pushed for this change.
Only `C:\Projects\AUTO-VIDEO-MAKER` is modified. Private evidence backups and live
reports stay under its ignored `diagnostics/universal-summary-standard-resume-20261107/`.