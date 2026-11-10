# Track 8 — Repurpose Studio

## Contracts

Current closeout (2026-11-10): evidence-only planning and 30–180-second targets
are implemented. The Dragon Queen acceptance below completed in one streamed
Sol call; historical failure sections are retained as incident history.

`app/repurpose/` is a cached-source execution surface, not a scene generator.
`ProductionUnit.repurpose_plan_reference` points at an immutable batch plan;
units retain source-segment lineage. Batch commits use the existing canonical
intent revision and a process-safe lock. Each short is accepted into its own
project through `source_repurpose_handoff` / `handoff_kind="repurpose_short"`.
There are no fabricated summary narration files or generated-scene checkpoints.

### Planner

* Only `gpt-6.1-sol`, text-only Responses API, `max_output_tokens=16384`, and
  `reasoning={"effort":"low"}` are permitted.
* Cached media/transcript/visual observations and grounded recap beats are read
  locally. No re-upload, STT, frame extraction or live visual observer is called.
* A durable dispatch marker precedes the sole provider invocation. Its response
  is saved before plan acceptance and unit creation. No LLM retries/repairs or
  endpoint/model fallback exist.
* Repeated request IDs inspect the existing batch. Explicit `/recover` may
  commit a retained valid response after interruption; it never calls an LLM.
* Four-hour evidence is reduced with whole-source timestamp coverage, including
  the ending. No claim of guaranteed virality or fresh vision is made.
* New model output contains only title/hook/narration/rationale/evidence IDs.
  Python derives disjoint grounded cuts, with up to 16 cuts on long targets.
  No timestamp arithmetic or word counting is requested from the LLM; legacy
  cut-bearing responses remain loadable. Completed plans are saved before TTS.

### Rendering

* `narrated`: complete Andrew Multilingual +15% TTS, retained WordBoundary timing,
  bounded tempo adjustment, 0.05 source bed + sidechain ducking.
* `raw_highlight`: source audio only, output-timeline-remapped transcript captions.
* `cinema_canvas` (default) and `center_crop`: 1080×1920 H.264/AAC, yuv420p,
  square pixels and faststart. Selected source windows only are decoded.
* ASS files use a controlled relative filename under per-render cwd on Windows.
  User/model text cannot inject ASS overrides or FFmpeg expressions.
* Existing segment-only transcripts receive honestly labelled estimated phrase
  timing; precise word karaoke requires retained word timestamps. Future STT can
  opt into `AVM_WHISPER_WORD_TIMESTAMPS=1` without modifying old caches.
* Acceptance checks encoded duration/resolution/audio, full decoding, −14 LUFS
  ±0.5 and true peak ≤−1 dBTP. TTS synthesis is separate from the one LLM call.
* Targets: 30, 45, 60, 90, 120, 150 or 180 seconds (continuous API bounds 30–180).
  Measured complete speech stays in target-relative bounds and never exceeds
  180 seconds. The target is editorial, not permission to truncate speech.

## API

* GET `/api/capabilities`: `repurpose` configuration-only readiness.
  Includes `min_target_seconds=30`, `max_target_seconds=180` and
  `target_duration_presets=[30,45,60,90,120,150,180]`.
* GET `/api/productions/{p}/repurpose/sources`: ready source/evidence revisions.
* POST `/api/productions/{p}/repurpose/batches`: confirmed idempotent planning.
* GET `/api/productions/{p}/repurpose/batches/{b}`: read-only job/clip inspection.
* POST `/api/productions/{p}/repurpose/batches/{b}/render`: exact saved-plan hash
  and selected units, never a replan.
* POST `/api/productions/{p}/repurpose/batches/{b}/recover`: explicit retained
  response/intent recovery with `{ "confirm": true }`.
* GET/HEAD `/api/productions/{p}/units/{u}/repurpose-video/media`: fingerprinted
  preview, including 206 single/suffix ranges and 416 invalid ranges.
* POST `/api/productions/{p}/units/{u}/repurpose-video/promote`: confirmed exact
  plan/master fingerprints; never uploads. Existing accepted media is protected.

Existing manual YouTube/TikTok readiness and publish endpoints remain the only
publication state owners. Instagram is an exported-file target, not a connected
publishing bridge. Pre-generation automatic short publication is not implemented.

## K3

Repurpose Studio follows AI Summary in the sidebar and has independently scoped
two-column styling. `SummaryStudio.jsx` and `summary-studio.css` are unchanged.
Only session identity is retained; readiness and media evidence are re-read.
Active jobs poll sequentially and cancel on navigation. Uncertain planning keeps
the original request ID for inspection, never a new automatic dispatch.
"Promote & Open Export" selects the exact short unit in the existing panel.

## Verification and known live blocker — 2026-11-09

The requested Dragon Queen production was submitted once as batch
`rpb-eb94b88c7d327b2cc67bcb26`. Its configured loopback bridge rejected the
Responses request with `LLMModelUnavailableError` (adapter handling for HTTP
400/404). Counters: LLM=1, upload=0, STT=0, vision=0. No response, short units,
rendered Dragon MP4s or promotions were produced. No alternate endpoint or
second request was attempted. Configuration readiness is not endpoint readiness.

Synthetic local checks separately passed raw center-crop, raw cinema-canvas,
and narrated-timing cinema-canvas rendering/promotion at 1080×1920, 30 seconds,
−14.05 / −14.05 / −13.95 LUFS and zero decode errors. Synthetic narration is
explicitly not a live Andrew TTS test or a Dragon Queen acceptance result.

Tools:

* `tools/verify_repurpose_acceptance.py --live`: explicit real acceptance with
  a stable request ID (re-running the failed batch cannot call the planner again).
* `tools/verify_repurpose_synthetic.py`: isolated local media/timing tests,
  zero LLM/STT/live TTS calls; outputs under temp only.
* `tools/verify_repurpose_ui.py`: read-only headless desktop/mobile inspection.
* `tools/verify_repurpose_fixture_ui.py`: happy-path previews/exact-unit Export
  using synthetic accepted media and fixture responses, no production writes.
* `tools/run_hermetic_pytest.py tests -q -p no:cacheprovider`: isolated providers,
  credentials and networking. K3 uses its existing node:test suite and Vite build.

Full Core hermetic run: **2,628 passed, 1 skipped**, zero external network
attempts. K3: **234 passed**, production build passed (existing bundle-size
warning). Headless desktop/mobile checks passed both the live blocked state and
synthetic preview/Export flow with no JS errors or real publishing writes.

## Surgical transport correction — 2026-11-09

`AgentRouterProvider(..., use_responses_api=True)` now selects the direct
`https://agentrouter.org/v1/responses` endpoint BEFORE dispatch when the configured
base URL is loopback. Default callers still use the supported local Chat bridge;
direct HTTPS configurations still use native Responses. Repurpose uses this
explicit constructor option rather than mutating `_api_style`. The configured
credential and existing public client header are reused without exposing them.
No endpoint probes, automatic fallback, retries, or model substitutions occur.

The user separately authorized one new Dragon Queen batch with request ID
`track8-dragon-direct-responses-v2`, batch `rpb-c72c41bb924ad93a24c745bf`.
The direct Responses dispatch was also rejected with `LLMModelUnavailableError`
(the adapter's HTTP 400/404 rejection family). Its persisted counters remain
LLM=1, upload=0, STT=0, vision=0. No response or plan was retained, and no short
units, rendered MP4s, or promotions were produced. The previous failed batch
was not rewritten, and no further live request was made. This corrects the
loopback path mismatch but does not establish current upstream availability.

Focused transport and Repurpose regressions passed **129 tests**, with zero
external network attempts. K3 source files were not modified by this correction.

## Working Summary transport and safe diagnostics — 2026-11-09

Repurpose now constructs `AgentRouterProvider(model="gpt-6.1-sol")` with no
transport override, then calls `generate_json` with the same temperature 0.35,
16,384-token output budget and low reasoning contract used by Summary. Configured
loopback uses `/v1/chat/completions`; direct HTTPS uses `/v1/responses`. The
loopback bridge owns upstream Responses translation. There are no endpoint
probes, automatic retries, repairs or model substitutions.

AgentRouter HTTP rejections now retain the shared Gemini-style sanitized
diagnostics (status, code, message and request ID), plus transport/style.
Parameter errors at 400/413/422 use `LLMInvalidRequestError`; 404 or explicitly
identified missing/unsupported models retain `LLMModelUnavailableError`.
Keys, bearer credentials, URLs and payload-shaped strings are redacted. Timeout
diagnostics explicitly report no HTTP status and uncertain upstream outcome.
Batch failures persist these safe diagnostics instead of discarding the reason.

The separately authorized Summary-bridge request `track8-dragon-summary-bridge-v3`
created batch `rpb-910728e2d3e7e87cb78068ad`. This ONE invocation selected the
configured loopback Chat bridge, but returned no complete response before the
300-second client limit. The batch is `uncertain` with LLM=1 dispatch,
upload=0, STT=0, vision=0. No response/plan/short units/media/promotions exist,
and no second request or fallback was issued. Retained-result inspection found
no recoverable response. The new attempt did not return an HTTP 400 to diagnose.
The later timeout-persistence improvement applies to subsequent requests; this
original uncertain batch is preserved without rewriting its history.

The final focused AgentRouter/Repurpose/Gemini-diagnostic regressions passed
**161 tests**, with zero external network attempts. K3 was untouched.

## Completed evidence-only Dragon Queen acceptance — 2026-11-10

The user-authorized request `track8-dragon-evidence-only-v6` produced batch
`rpb-9acb97d5a5a400ccbb011f62`. A single streamed `gpt-6.1-sol` completion returned
2,581 characters of JSON in **211.485 seconds**, with approximately 3,394 input
tokens. The model supplied scripts and ordered evidence IDs, not cut coordinates.
Python derived the source cuts and pinned the plan before rendering.

Counters: **LLM calls=1, successful calls=1, uploads=0, STT=0, vision=0**. No
automatic LLM repair/retry was performed. The third short's initial Edge TTS
attempt received no audio; a render-only recovery reused the same saved plan.

| Unit | Title | Cuts | Encoded duration | LUFS | dBTP | Accepted project |
|---|---|---:|---:|---:|---:|---|
| unit-short-001 | The Baby Breathes Fire | 6 | 35.566667s | -14.01 | -1.92 | short_e45235b6204392a2d7f08b9d |
| unit-short-002 | Ash Holds the Bridge | 6 | 37.072938s | -14.06 | -1.49 | short_f7eefde5477080519857107e |
| unit-short-003 | A Name Isn't Control | 8 | 38.933333s | -14.02 | -2.89 | short_cb6769bad0b447e21f6d10d0 |

Every accepted output is 1080×1920, H.264/AAC, has burned-in word-timed ASS
captions and a hook banner, and passed independent full decoding with zero
errors. Narration is Andrew Multilingual at +15%; source bed is 0.05 with ducking.
All units report COMPLETED with AVAILABLE canonical masters and matching
promotion fingerprints. No platform publication was initiated.

Real desktop/mobile K3 verification played all three shorts without errors,
checked the seven 30–180s target options, and verified exact `unit-short-003`
Export navigation with no real publishing writes. Frozen Summary Studio hashes
remain unchanged. Local verification reports are under `temp/` and excluded
from commits.

Final verification: **93 Repurpose tests**, **189 Repurpose + AgentRouter
tests**, and **94 existing source/summary/API compatibility tests** passed, all
with zero external network attempts. K3: **235 passed**, production build passed.
The 180-second raw publishing handoff is covered hermetically with media probing
mocked; the live media acceptance used the requested 45-second editorial target.