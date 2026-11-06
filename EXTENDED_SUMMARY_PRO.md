# Extended chronological recaps

## Length policy

Core owns the length budget in `app/production/recap_budget.py`. Targets are
planning contracts, not claims about measured TTS duration.

| Preset | Target runtime (preferred seconds) | Beats | Spoken words | Chapter policy |
| --- | --- | --- | --- | --- |
| `auto` | Duration-scaled: see source brackets below | Scaled | Scaled | Scaled |
| `short` | 60–90s (75) | 8–10 | 150–210 | 1 |
| `story` | 3–4m (195) | 15–22 | 350–520 | 1 |
| `deep` | 5–7m (390) | 28–40 | 750–1,150 | 3–6 |
| `extended` | 8–10m (540) | 45–54 | 1,400–1,680 | 4–6 |
| `feature` | 12–15m (810) | 65–80 | 2,050–2,550 | 7–8 |
| `blockbuster` | 18–22m (1,200) | 95–120 | 3,050–3,750 | 10–12 |
| `epic` | 25–32m (1,800) | 135–170 | 4,250–5,400 | 14–16 |
| `custom` | Positive minutes/seconds, at most 35m / 2,100s | Duration-scaled, at most 220 | 170 WPM reference, cadence-compatible range | 1–16, duration-scaled |

| Auto source duration | Beats | Words | Chapter policy | Runtime |
| --- | --- | --- | --- | --- |
| <180s | 4–6 | 100–150 | 1 | 45–60s |
| 180–<600s | 8–12 | 180–270 | 1 | 60–90s |
| 600–<1,500s | 16–26 | 450–750 | 1–3 | 3–4.5m |
| 1,500–<3,300s | 45–54 | 1,400–1,680 | 4–6 | 8–10m |
| 3,300–<6,300s | 65–80 | 2,050–2,550 | 7–8 | 12–15m |
| 6,300–10,200s inclusive | 95–120 | 3,050–3,750 | 10–12 | 18–22m |
| >10,200s | 135–170 | 4,250–5,400 | 14–16 | 25–32m |

Sources below 75 seconds retain the brief fixture-safe policy: at most
45 seconds or 60% of source, whichever is shorter; sandbox remains at most
30 seconds. Real presets and custom targets cap both target and upper runtime
at **75% of source duration**, proportionally scaling beat/word/chapter detail
when necessary. Only brief, explicitly timed tiny fixtures retain the legacy
test exemption. `target_duration_seconds` wins over `target_minutes`, which
wins over the preset; invalid/nonfinite/over-35-minute requests fail before
provider work. `custom` requires an explicit target.

Custom examples: `{ "target_length_preset": "custom", "target_minutes": 30 }`
or `{ "target_duration_seconds": 1200 }`. Custom runtime bounds are +/-5%,
clamped to 2,100s and the source ceiling; word bounds must also intersect
155–180 WPM. Requested duration and source-ratio limiting are recorded separately.

For a source of at least 15 minutes, deep/extended or a budget of at least
28 beats / 700 words uses `narration_chapters.py`. Approximately six-minute
chapters, clamped to the chosen recap's chapter range, receive **every overlapping transcript segment with its original
ID**, prior chapter continuity notes, and the preceding narration tail.
Dense transcripts subdivide rather than truncate. Each completion writes
final beats, not intermediate notes for a movie-sized final rewrite.

Chapter-local word counts may vary modestly. Remaining capacity is reserved
so the final aggregate still strictly meets both word limits. Beat counts,
two-to-four naturally varied sentences, opening/middle/ending coverage, source bounds,
and chronological ordering are validated. No copied transcript/mock fallback
is admitted on real execution.

## Real Summary Pro

Real long-form Pro extracts timestamped JPEGs with FFmpeg approximately every
12 seconds, capped at 32 frames per chapter (155 frames for the historical verified film).
Wider feature/epic chapters also retain a near-ending sample so the closing
scene is not lost beyond the 15-second final-evidence tolerance.
Direct vision routes send **frames and transcript together** to the resolved
adapter. AgentRouter's Sol/Astra routes instead use a separate Gemini observer,
then send **pure-text visual action notes and Whisper dialogue** to the selected
storyteller. Neither Sol nor Astra is advertised as accepting images or native
video. Every Pro beat cites inspected frame IDs; silent events can cite
frames without inventing dialogue. Disjoint evidence moments remain disjoint
source references, not a long clip spanning uncited material.

Frame bytes are loaded and encoded only inside Python provider adapters.
Timestamp/path tuples are handled explicitly; unreadable/missing frames fail
before HTTP. No image bytes, base64, credentials or request headers are logged.
Gemini uses bounded media/thinking configuration; all adapters reject output
truncation. Chapter JSON starts with 4K output tokens and retries at 8K;
the text-only storyteller divides chapters into bounded 2–3-beat requests,
validates each batch, then validates the aggregate chapter without a lossy rewrite.
Fast overloads use bounded backoff; full timeouts move to the next
eligible Auto route. Explicit canonical selection never changes models.

`narration_chapters.partial.json` checkpoints validated chapters and pending
validated visual notes (before the separate storyteller request), and is
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

Measured TTS audio is authoritative. Recaps default to Andrew Multilingual at
configurable `+15%`. Scripts up to 1,800 words stay continuous; longer scripts
use chapter/beat/sentence-aligned batches of **at most 450 words / 6,000 UTF-8
bytes**, with a 90-second per-request deadline and a 480-second synthesis
operation budget. A matching hash checkpoint retains validated chunks after
interruption; script/provider/voice/rate/pitch/volume changes invalidate reuse.
Chunk bytes and decoded PCM are revalidated on resume. Raw provider segments
are decoded to 48kHz stereo 24-bit PCM, then joined with an FFmpeg concat file
before whole-script silence tightening, pacing and **two-pass -14 LUFS**
mastering. No per-beat silence padding or per-chunk loudness normalization is
added. Metadata records chunk boundaries, duration and resumed chunk counts.
Bounded FFmpeg `atempo` is admitted only when **both** the preset
runtime and 155–180 WPM can be met. Sparse scripts are rejected rather than
stretched into slow nine-minute speech. Metadata records original duration,
removed silence, synthesis rate, measured WPM and tempo factor.

Source cuts retain original audio at default `0.05` gain beneath the `1.0`
narrator, with a **-6dB pocket at 1.5kHz**, speech sidechain ducking and
`amix normalize=0`. `AVM_RECAP_BACKGROUND_VOLUME` accepts 0–0.055 (0 disables the bed).
Missing audio falls back to narration-only, explicitly recorded in metadata.

Word-proportional beat allocation on a global cut grid creates about 90–110
moving shots for 8–10 minutes, each at most 5.5 seconds. The planner prefers
frame-grounded ranges long enough for a shot instead of turning every brief
dialogue citation into a tiny cut. Extraction is bounded-parallel; identical
source encodes are concatenated with **`-f concat -safe 0`** and stream-copied
before the two-input narrator/source-bed mix. All multi-chapter plans, and
legacy/imported plans with 80+ cuts or long input paths, bypass the shared
enumerated-input assembly path. The command line stays constant-size for
120–400 cuts; the shot list and original cut audio live in the concat manifest.
Repeated FFprobe results are reused across mixing and the measured shot manifest.
Cumulative frame boundaries are quantized for 80+ shots: total timing drift
stays within half a frame instead of rounding every cut upward and consuming
the true ending's narration slot. Frame-count extraction remains bounded to
the cited source range.
No frozen last-frame padding is used. An audio/video difference over 0.25
seconds is rejected for the multi-chapter path.

K3 exposes all eight presets plus custom minutes, a native inline player, measured duration/beat/word/chapter/
cut badges, and a read-only **Load saved recap** action. Media stays behind the
same-origin `/api` proxy; the fingerprint invalidates stale browser media.
Chapter narration and the Vite proxy allow 1,800,000ms; synthesis/assembly
allow 600,000ms and are never automatically retried by the browser. Health,
progress and read budgets remain independent and fail-fast.

## 2026-11-06 long-form safety boundary

This expansion is validated with pure policy tests, hermetic chapter/HTTP
contracts, fake TTS adapters, and small local synthetic audio/video fixtures.
The verified **The Last Dragon Queen 8m23s master was not re-rendered or
overwritten**. Its SHA-256 and production/output file metadata were recorded
before implementation and checked afterward. Historical real-render metrics
below remain historical, not evidence of a new 15/20/30-minute render.
Flag-only `run_hermetic_pytest.py` invocations explicitly default to `tests/`,
not the repository root's manual live smoke scripts. External transport and
dotenv remain blocked throughout the suite.

Final expansion verification: **Core 2,448 passed, 1 skipped** (720.77 seconds),
**0 external network attempts**; **K3 185 passed**, production build passed.
The large-shot command checks stayed at **1,154 quoted characters** through
400 shots, and real tiny 360-cut concatenation passed with and without audio.
All **1,036 existing production/output files** retained their size and UTC
modification time; the protected master's SHA-256 remained
`ec54786d0e0f40d4d3666ebce34afcc05a0e5414d05bb989fa6e266e03e4a928`.
K3 expansion commit: `9b481af12fc66b5dc2ede72598a7eae44d1fa153`.

## Previous baseline — The Last Dragon Queen (before storyteller upgrade)

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

### Completed Sol follow-up

On 2026-11-05, the same production/unit/source completed with `gpt-6.1-sol` using
the reusable visual-observation cache: **5 chapters, 53 beats, 1,478 words,
521.637958 seconds (8:42), 170.003 WPM, 95 moving cuts**. Andrew Multilingual was
synthesized at `+15%`; real movie audio was mixed at `0.10` beneath narrator `1.0`.
All five chapters reused visual evidence and made zero new vision calls.
Full MP4 decode passed with no errors; the script/media fingerprint matched.
The earlier Gemini metrics above remain explicitly the historical baseline.

Baseline verification on 2026-11-04: **Core 2,153 passed, 1 skipped**, zero
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