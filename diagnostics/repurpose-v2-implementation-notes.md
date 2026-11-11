# Repurpose Studio v2 rollout

## Implemented

- Four to six narrated story beats per short, each bound to a verified visual action ID, exact source PTS and retained coverage.
- Separate TTS/48kHz PCM measurement per beat. At 30fps, one frame is 1,600 samples.
- Tail-only frame padding; no whole-clip narration/cut proportional scaling in v2.
- Bounded scene-local slowdown (up to 2x), never neighboring-window substitution; complete speech pacing stays within 0.85–1.20x.
- Exact execution sidecar, frame-count/mux-origin acceptance checks and immutable promotion snapshot.
- Sol/Astra and low/medium/high reasoning, with 16,384/32,768/32,768 output caps.
- Production-wide saved-batch listing and latest-rendered auto-load.
- Direct 16GiB streaming upload, cached-source transcription and explicit visual preparation.
- Existing Summary UI/CSS and accepted v1 artifacts remain unchanged.

## Runtime activation

Core and the operator-owned Node proxy were restarted for live acceptance. Refresh K3.
The proxy script was upgraded without exposing or changing credentials. Its fingerprint is
verified before paid acceptance dispatch; the upgrader handles the older operator proxy as well.

## Operator flow

1. Upload a new movie, or select an existing production and source. Duplicate titles show date/ID.
2. New uploads are transcribed with the existing resumable Whisper pipeline. Cached sources are reused.
3. Original-audio highlights can plan from transcript evidence. For beat-locked narration, use
   the source player and cached notes to verify exact visible action starts and coverage ends.
4. Optional visual preparation generates sampled observations through admitted image observers.
   It incurs separate vision calls and does not certify action onset.
5. Save at least four non-overlapping anchors per requested distinct short (or four with shared
   footage enabled). Select planner model/reasoning and explicitly plan.
6. Render the saved v2 plan. Scene-local slowdown and measured speech pacing accommodate short
   coverage. Out-of-bounds cases fail explicitly; speech is never truncated or footage substituted.
7. Review and promote a new exact-unit master. Existing promoted units remain protected.

## Live acceptance and limitations

- Semantic action verification is operator-assisted. Sparse visual observations cannot prove
  exact action onset automatically. Browser playback support for MKV/codecs varies; Core upload
  validation and rendering remain authoritative.
- Output caps reduce truncation risk, never guarantee model completion. Incomplete JSON fails
  without automatic LLM retries. Cost labels are qualitative; currency pricing is not configured.
- Source lineages in v2 reserve verified coverage at planning time. The immutable execution.json
  pins the exact measured subset actually rendered, and canonical acceptance validates both.
- Batch rpb-3aedc87ee6141119b03fe46a rendered and promoted units 004–006. It used Sol/medium/32,768,
  one successful call (1,262 input / 12,904 output / 11,624 reasoning tokens). Two earlier calls
  failed and were not automatically retried: one malformed response, one low-cap truncation.
  Each later call was separately authorized. Missing TTS beats resumed from completed audio caches.
- Synthetic encoded audio markers measure a maximum offset of 0.729ms. Live decoded narrator
  envelope correlation and frame PTS measure 0.334ms / 0.167ms / 0.333ms maximum boundary deltas.
  These are mechanical beat-boundary measurements, not a claim of exact word/action semantic alignment
  throughout every sentence. Action presence is grounded in verified source frames.
- The 45s editorial targets yielded complete measured shorts of 31.733s, 35.533s and 31.733s,
  within the existing target bounds. No silent filler or invented narration was appended.
- Existing v1 batch artifacts and units 001–003 were fingerprinted and remain byte-identical.
- The requested rpb-efefbbd66e0c1acd3b0fb0f8 does not exist in the inspected storage. Near-match
  rpb-afefbbd66a0c1acd3b0fb0f6 remains intact: its response has 127/121-character rationales against
  a 120 limit and a two-anchor clip. Recovery cannot invent the missing third citation; use a new
  explicitly confirmed plan after source verification.

## Validation

Offline pytest covers legacy planner/render/handoff contracts, v2 beat clocks, mocked media jobs,
canonical execution snapshots, preparation resume, source GET/HEAD ranges, selected model/effort
forwarding and synthetic proxy settings. K3 Node tests cover upload boundaries, batch pagination,
source preference, settings and unchanged Summary hashes. The K3 production build is validated.

Final verification: full Core hermetic suite 2,730 passed / 1 skipped with zero external
network attempts; focused Repurpose/AgentRouter suite 191 passed; K3 suite 241 passed;
production frontend build succeeded. Existing Pydantic deprecation and Vite bundle-size
warnings are non-fatal. All three new canonical promotion packages validate.

The local per-beat table and output fingerprints are recorded in
diagnostics/dragon-v2-final-sync-report.json (intentionally excluded from Git with other
operator acceptance data). Media lives under the production's units/unit-short-004..006/
repurpose/short.mp4, and promoted packages under output/projects/short_*.