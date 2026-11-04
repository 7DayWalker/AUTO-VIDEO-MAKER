# Extended chronological recaps

## Length policy

Core owns the length budget. `extended` is **40–54 beats, 1,150–1,500
spoken words, target 540 seconds, accepted audio range 480–600 seconds**.
`deep` is **28–40 beats, 750–1,150 words, target 390 seconds, accepted
audio range 300–480 seconds**. Auto selects deep for 15–25 minute sources
and extended for longer sources. Short sources and explicit sandbox runs
remain source-proportionate; explicit seconds override presets.

For a source of at least 15 minutes, deep/extended or a budget of at least
28 beats / 700 words uses `narration_chapters.py`. Approximately six-minute
chapters receive **every overlapping transcript segment with its original
ID**, prior chapter continuity notes, and the preceding narration tail.
Dense transcripts subdivide rather than truncate. Each completion writes
final beats, not intermediate notes for a movie-sized final rewrite.

Chapter-local word counts may vary modestly. Remaining capacity is reserved
so the final aggregate still strictly meets both word limits. Beat counts,
two/three-sentence narration, opening/middle/ending coverage, source bounds,
and chronological ordering are validated. No copied transcript/mock fallback
is admitted on real execution.

## Real Summary Pro

Real long-form Pro extracts timestamped JPEGs with FFmpeg approximately every
12 seconds, capped at 32 frames per chapter (155 frames for the verified film).
Each chapter sends **frames and transcript together** to the resolved vision
adapter. Every Pro beat cites inspected frame IDs; silent events can cite
frames without inventing dialogue. Disjoint evidence moments remain disjoint
source references, not a long clip spanning uncited material.

Frame bytes are loaded and encoded only inside Python provider adapters.
Timestamp/path tuples are handled explicitly; unreadable/missing frames fail
before HTTP. No image bytes, base64, credentials or request headers are logged.
Gemini uses bounded media/thinking configuration; all adapters reject output
truncation. Chapter JSON starts with 4K output tokens and retries truncation
at 8K. Fast overloads use bounded backoff; full timeouts move to the next
eligible Auto route. Explicit canonical selection never changes models.

`narration_chapters.partial.json` checkpoints only validated chapters and is
not a playable/complete narration artifact. Reuse requires matching source,
transcript, budget, model, chapter windows and frame timestamps, followed by
revalidation. It is removed on successful completion. The old narration is
not replaced by failed or partial generation.

`gemini-3.5-flash-lite` is an additional, independently identified canonical
vision model verified against Google's model catalogue and real timestamped
image requests on 2026-11-04. It is not an alias for Gemini 3 Flash Preview.
The older configured Gemini routes experienced overload/unavailability during
verification; Alibaba/OpenRouter routes reported model/quota failures.

## Audio and assembly

Measured TTS audio is authoritative. If a long preset falls outside its
duration range, bounded FFmpeg `atempo` adjusts speaking pace without changing
pitch, adding silence, or clipping narration. Metadata records original
duration and the tempo factor. Extreme correction is rejected.

Word-proportional beat allocation on a global cut grid creates about 90–110
moving shots for 8–10 minutes, each at most 5.5 seconds. The planner prefers
frame-grounded ranges long enough for a shot instead of turning every brief
dialogue citation into a tiny cut. Extraction is bounded-parallel; identical
source encodes are concatenated and muxed once to the narration's duration.
No frozen last-frame padding is used. An audio/video difference over 0.25
seconds is rejected for the multi-chapter path.

K3 exposes Extended, a native inline player, measured duration/beat/word/chapter/
cut badges, and a read-only **Load saved recap** action. Media stays behind the
same-origin `/api` proxy; the fingerprint invalidates stale browser media.
Generation, synthesis and assembly requests have 300,000ms deadlines and are
never automatically retried by the browser.

## Verified live output — The Last Dragon Queen

Production: `prd_20261026_the_last_dragon_queen_part_i_ai_short_fi_719`

Unit: `unit-001` · explicit source: `src-010` · `mock_mode=false`

| Metric | Measured result |
| --- | --- |
| summary mode / method | `pro` / `frame_sequence` |
| canonical model | `gemini-3.5-flash-lite` |
| chapters | 5 |
| grounded beats | 53 |
| spoken words | 1,172 |
| inspected frames | 155 |
| multimodal attempts, including validated checkpoint | 8 |
| original TTS duration | 473.088 seconds |
| tempo factor | 0.8760888888888889 |
| paced narration duration | 539.974708 seconds |
| MP4 container duration | 539.974708 seconds |
| video stream duration | 539.966667 seconds |
| moving cuts | 99 |
| maximum planned shot | 5.45429 seconds |
| output | H.264 / AAC, 854×480, 131,202,268 bytes |

All 53 beats cite both visual and transcript evidence. Chapter beat counts
are 9, 11, 11, 11, 11; word counts are 231, 219, 200, 265, 257. Every
chapter has 31 frames. Final HTTP narration: 133.562s (including revalidated
checkpoint); synthesis: 58.968s; assembly: 58.875s. HTTP Range reads returned
206 through both Core and K3's proxy. No promotion or social upload was sent.
Headless installed Chrome also loaded the saved recap inside SummaryStudio,
reported 539.974708 seconds, advanced playback, displayed all five metric
badges, and reported zero page errors. FFmpeg decoded the entire nine-minute
output with exit code 0 and no decode errors.

Sampling establishes timestamped grounding, not a guarantee that every
transient action between frames was observed. Operator review of the final
recap remains appropriate before publishing.

## Repeatable checks

Final verification on 2026-11-04: **Core 2,153 passed, 1 skipped**, zero
external network attempts under `run_hermetic_pytest.py`; **K3 171 passed**;
Vite production build passed. Existing Pydantic deprecation warnings and
Vite's >500kB bundle-size warning remain non-fatal. K3 commit:
`c0863f3cea4c46190d8ef00b34d1f0a1b414005f`.

From `C:\Projects\AUTO-VIDEO-MAKER`:

```powershell
python tools/run_hermetic_pytest.py tests -q
python tools/verify_extended_summary.py prd_20261026_the_last_dragon_queen_part_i_ai_short_fi_719 --asset-id src-010 --stage measure
```

The smoke tool defaults to real Pro Auto, backs up existing outputs before
narration generation, sends each command once, and probes the resulting
media. `--model` explicitly locks a canonical model; `--stage` can resume
synthesis/assembly without regenerating narration. Never blindly retry a
timed-out write—inspect artifacts first.

K3 on Windows PowerShell (Node 20 does not expand `tests/*.mjs` itself):

```powershell
$tests = @(Get-ChildItem tests -Filter '*.mjs' | ForEach-Object FullName)
node --test @tests
npm run build
```

Sandbox media regressions use a five-second per-command wall-clock ceiling,
matching the existing bounded sandbox FFmpeg timeout, rather than a flaky
sub-second process-startup benchmark. Their independent guards still forbid
external networking, loading Whisper, and decoding the operator's full movie.