# Track 8 — Repurpose Studio

## Contracts

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

## API

* GET `/api/capabilities`: `repurpose` configuration-only readiness.
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