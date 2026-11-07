# Universal source-length summary hardening

## Supported workflow and honest limits

Both real Summary modes retain the existing source-grounded validation, recap
budgets, canonical-model routing and explicit publishing boundary. The pipeline
is hardened for one-minute through four-hour sources; it cannot guarantee the
availability of external AI/TTS providers, a working codec, or sufficient disk.
No failure is converted to synthetic narration. Browser movie intake uses a raw
File body, not multipart parsing or a base64 JSON envelope.

- **Ingest:** up to 16 GiB, one-MiB reads to disk with streaming SHA-256, a free
  disk-space check, then a same-volume rename instead of another movie copy.
  Vite pipes requests/responses with backpressure and no overall proxy timeout.
  Core has a 300-second *idle* upload timeout. Explicit Content-Length is
  required; chunked transfer encoding and multipart are rejected, not buffered.
- **Transcription:** sources longer than 20 minutes use twelve-minute 16-kHz
  mono PCM windows. Per-source `source/src-NNN.transcription/transcript_chunks.json`
  persists validated global timestamps after every window. A source path/stat,
  duration, model and window-size fingerprint isolates stale work. Empty silent
  windows are retained without invented words. Existing complete transcripts
  are reused by the studio. Neither Whisper nor Torch is loaded for sandbox.
- **Seeking:** frame extraction, source clips and Whisper windows use input-side
  `-ss`, before `-i`; decode and encode threads are bounded for parallel frame/
  cut work. Seek speed still depends on codec/keyframes and disk speed.
- **Narration:** 16,384 output tokens from the first request. AgentRouter chapter
  requests explicitly force `reasoning: {effort: "low"}` even if an old environment
  setting asks for high effort. The private proxy preserves the nested Responses
  object and converts Chat's equivalent field without dropping token budgets.
- **Credit guard:** one sequential whole-chapter call. One repair/transport retry
  is the ceiling. A proven first-call truncation in Pro may instead use one
  recovery round of two sequential halves (at most three requests total); those
  halves are not retried. Claims are saved *before* dispatch, so reconnecting or
  restarting cannot reset the allowance. This intentionally fails closed if a
  provider bills work but loses its response; exactly-once billing cannot be
  promised without upstream idempotency support.
- **Durability:** Standard and Pro persist every completed chapter; completed
  checkpoints remain after the final artifact save. Source/transcript/budget/
  storyteller identity is checked, with stable observer-independent identity
  for new checkpoints. Changed observer priority never relabels prior evidence.
  `minimum_cached_chapters` refuses regeneration of protected completed chapters.
- **Timeouts:** every chapter request receives a fresh 300-second socket budget;
  no movie-wide deadline is subtracted. TTS has independent 240-second chunk
  budgets and retained validated PCM files; the old 29-minute global TTS limit
  no longer starves later chunks. Scripts above 450 words are chunked (including
  eight-minute recaps, not just feature-length scripts). Edge's transient empty
  audio response receives one bounded retry, never an unbounded retry loop.
- **Observer fallback:** first capability/quota/network/auth/timeout failure
  quarantines that route for the process lifetime. Sequential bounded observer
  batches prevent a queue of already-failed routes from burning more requests.
- **Playback:** bounded HTTP 206 range reads, strict invalid-range handling and
  quiet browser disconnects, including BaseHTTPRequestHandler's final flush.
  Final media replacement is atomic; interrupted copies leave the prior master
  intact rather than exposing an incomplete MP4.

## K3 recovery contract

The operator's one-click run owns a single-flight client lock. Core independently
locks recap writes per unit and transcription per source. The studio polls both
production detail and recap progress every three seconds during active work.
Transport failures enter a quiet reconnect state: it reads matching artifacts,
waits while Core remains active, and resubmits only after two confirmed idle
reads. It never retries provider refusals, validation errors or credit guards.
Unmounting aborts recovery and cannot start a following stage. Publishing is
never part of automatic recovery.

Health reads remain fail-fast. Client narration/TTS calls retain their 30-minute
transport deadline; losing that socket is not cancellation of Core's durable
work. Upload/transcription use separate four-hour client budgets.

## Verification

Run Core through `tools/run_hermetic_pytest.py` (dotenv/provider configuration
removed, runtime storage isolated, external networking blocked). The universal
regressions simulate a four-GiB upload and all twenty windows of a four-hour
source without allocating either in memory. They exercise crash/resume, retained
credit claims, chapter protection, observer quarantine, 16K/low requests, input
seeks and browser byte-range disconnects. They are not a real four-hour live
provider/Whisper benchmark.

The private `C:\Users\Paul Ella\proxy.js` is deliberately outside Git. Its
idempotent installer and synthetic JavaScript contract tests are versioned in
Core; credentials and live media/checkpoint backups are not.

Live counts-only reports and screenshots for this run stay under the ignored
`diagnostics/universal-summary-20261106/` directory. The earlier eight-chapter
Kung Fu MP4/audio and the nine-chapter checkpoint were backed up there before
any live command. Final live results are recorded separately from hermetic tests.

## Live verification on 2026-11-06

- The protected Kung Fu resume reused Chapters 1–9 unchanged (108 beats, 2,851
  words) and wrote only Chapter 10 in **one 16K/low-effort call**, taking 166.281s.
  The validated Blockbuster script contains 10 chapters, 120 beats and 3,175 words.
- The first Edge TTS run stopped after six completed chunks with an upstream
  `NoAudioReceived`. An audio-only resume reused those six PCM checkpoints and
  completed all ten. Mastered narrator: **1,084.740625s (18m04.7s), −14.27 LUFS,
  −1.73 dBTP**. The overnight process interruption stopped assembly, not the
  completed script/audio; on 2026-11-07 an assembly-only resume reused both.
  Assembly returned HTTP 201 with **1,084.8s, 871,802,949 bytes**, H.264/AAC.
  Final SHA-256: `ecf2b29d45da7bfd9cd87c43c15fdfafa91767a69df3d1bd9efd8b17e43711eb`.
  Final mix: **−14.01 LUFS, −1.44 dBTP**, 198 moving cuts, 1920×1080.
  Complete FFmpeg decode: exit 0, zero error lines. Desktop and mobile headless
  Chrome played the finished file without page/media errors. Core and K3 both
  returned 206 for beginning and suffix byte ranges. No promotion or publishing
  was requested.
- The separate Standard verification production
  `prd_20261106_kung_fu_summary_standard_verification_134` reused the real source
  and Whisper sidecar without touching Pro. Chapters 1–2 were saved; Chapter 2
  needed its one output repair, and Chapter 3 exhausted its socket retry. It
  stopped with an AgentRouter timeout after 1,370.39s. No guard reset, re-billing
  of saved chapters or fabricated successful full-movie Standard result occurred.
  Thus the transport/credit/resume fixes are verified, but an unconditional
  live Standard success claim is **not** supported by this run.

## Release checks and limitations

- Requested Core suites plus universal regressions: **305 passed**, zero
  external network attempts (the three requested suites alone: 280 passed).
- Earlier expanded full Core suite: **2,484 passed, 1 skipped**, zero external
  network attempts. Later full release rerun under extreme workstation memory
  pressure: **2,464 passed, 1 skipped, 4 failures, 19 setup errors**. Those errors
  were local FFmpeg five-second and loopback/sandbox timing limits, not external
  network calls; that rerun is not represented as green. Final changed-code
  focused checks passed separately.
- K3: **209 passed**, production build passed (existing bundle warning only).
  Commit `3b1816b90e987de88b219fe1a61bf5dd68821865` pushed to `origin/main`.
- Browser: 400px configuration / adjacent 16:9 monitor, CTA within the desktop
  viewport, correct violet-indigo gradient, no duplicate IDs, no overflow,
  functioning collapsed legacy controls, and completed-video playback.
- The real source was 1h02m22s. Four-hour windowing/ingest was verified with
  hermetic fixtures, not a real four-hour live paid end-to-end benchmark.