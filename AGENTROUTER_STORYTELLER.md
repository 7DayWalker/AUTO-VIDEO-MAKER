# AgentRouter cinema-storyteller recaps

## Routes and identities

Core registers `gpt-6.1-sol` (**GPT-6.1 Sol**) and `gpt-6-astra` (**GPT-6 Astra**)
through the text-only `agentrouter` provider. Explicit canonical selection stays
locked. A gateway catalogue entry and successful calls verify route readiness,
not independent attestation of upstream model weights or remaining quota.

`AGENTROUTER_API_KEY` stays in the ignored local environment. Set
`AGENTROUTER_BASE_URL=http://127.0.0.1:8318/v1`. Never put credentials in URLs,
browser state, source files, logs, or version control. K3 uses Core's registry
projection for the AI Brain and Summary selectors; there is no frontend model list.

Summary Pro uses a declared two-stage workflow:

1. Core extracts timestamped keyframes and asks the separately configured Gemini
   vision adapter (default `gemini-3.5-flash-lite`, configurable via
   `AVM_RECAP_VISION_MODEL`) for visual action notes in batches of
   four images by default, with two concurrent observation requests. Set
   `AVM_RECAP_VISION_BATCH_SIZE` from 1 to 32 to trade payload size against
   request quota. The actual observer is recorded, never silently relabelled. Every note
   must cite a supplied frame ID and its exact timestamp. Successful batches are
   checkpointed independently, so overload does not discard the other batches.
   Missing, duplicated or
   invented observations are rejected.
2. Sol/Astra receives **only text**: validated visual notes, the full chapter's
   Whisper dialogue, prior continuity and citation IDs. The chapter is divided
   into four disjoint timestamp-bounded text batches (fewer for short budgets),
   processed with bounded concurrency and joined in chronological order; every
   overlapping dialogue segment remains supplied. Images never enter an
   AgentRouter request. The adapter rejects binary/content-part/base64 input
   before HTTP, and never logs raw provider errors or credentials.
3. Narration links evidence-backed character decisions, dialogue revelations,
   motivations and consequences, rather than camera descriptions. Forbidden
   caption phrasing triggers bounded repair. Prompt examples are not story facts.

Artifacts record observer identity, storyteller provider, frame and text-call
counts, and `visual_notes_then_text_storyteller`. Validated chapters and pending
visual notes can resume interruptions without treating partial output as final.
Reasoning effort `none` and a 300-second per-text-request timeout are configurable
with `AGENTROUTER_REASONING_EFFORT` and `AGENTROUTER_REQUEST_TIMEOUT`. Two-stage
planning has a bounded 14-minute operation budget; K3 allows 15 minutes and never
automatically retries writes. This is processing time, not final media duration.

## Private proxy

The operator-owned `C:\Users\Paul Ella\proxy.js` translates Chat Completions to
Responses requests. `C:\Projects\AUTO-VIDEO-MAKER\tools\configure_agentrouter_proxy.py`
preserves route/auth/tool handling and adds a media guard, output-budget/JSON-mode/
reasoning-effort forwarding, and truthful `length` status for incomplete output.

The helper recognizes explicit anchors, is idempotent, keeps a private backup,
syntax-checks with Node, and prints **boolean checks only**. No private proxy
contents are copied into the repository. Restart the proxy after applying it;
the helper deliberately does not terminate processes itself.
The private proxy was observed reverting during the session; re-check/reapply
the helper if an external process restores it. Core's own text-only adapter
remains the mandatory independent media guard.

## Speech and source audio

- Extended: **45–54 beats, 1,400–1,680 words**, target 540 seconds; measured
  acceptance is 480–600 seconds and **155–180 WPM**, aiming near 170 WPM.
- TTS defaults to `en-US-AndrewMultilingualNeural`, `+15%`; override via
  `AVM_RECAP_TTS_VOICE` / `AVM_RECAP_TTS_RATE` or synthesis API `voice` / `rate`.
- One continuous, whitespace-normalized request; conservative silence tightening
  retains short conversational breaths. No per-sentence silence padding.
- Sparse scripts cannot be stretched into slow recaps. Pitch-preserving tempo
  correction must satisfy runtime and WPM together; extreme correction is refused.
- Source audio is mixed at `0.10` beneath narrator `1.0`, without `amix`
  normalization. `AVM_RECAP_BACKGROUND_VOLUME` allows 0–0.30. Missing streams
  fall back to narration-only, reflected in metadata and K3 badges.

## Verification

```powershell
Set-Location -LiteralPath 'C:\Projects\AUTO-VIDEO-MAKER'
python 'C:\Projects\AUTO-VIDEO-MAKER\tools\run_hermetic_pytest.py' tests -q
python 'C:\Projects\AUTO-VIDEO-MAKER\tools\verify_extended_summary.py' prd_20261026_the_last_dragon_queen_part_i_ai_short_fi_719 --asset-id src-010 --model gpt-6.1-sol --stage all

Set-Location -LiteralPath 'C:\Users\Paul Ella\AppData\Local\Cline\avm-studio-pro'
$tests = @(Get-ChildItem -LiteralPath 'C:\Users\Paul Ella\AppData\Local\Cline\avm-studio-pro\tests' -Filter '*.mjs' | ForEach-Object FullName)
node --test @tests
npm run build
```

The smoke command backs up accepted artifacts and sends each write once. After
a timeout, inspect artifacts/checkpoints before manually retrying. Use
`--stage synthesize`, `--stage video`, or `--stage measure` to resume without
regenerating the script. No promotion or social upload is performed.

## Live verification status — 2026-11-05

Both AgentRouter models completed text/JSON readiness requests through the
loopback proxy. Sol generated a validated first chapter from real Gemini notes
and the film's Whisper transcript. The full 8–10-minute re-render **did not
complete**: Gemini Flash Lite repeatedly returned 503/connection timeouts; the
alternative Gemini 3 Flash Preview subsequently reported daily free-tier request
quota exhaustion (20 requests, retry delay approximately 20 hours).

The existing 53-beat / 1,172-word Gemini nine-minute recap was not replaced or
relabelled as Sol output. A partial checkpoint is resumable when a configured
Gemini observer is available again. The preview-observer attempt is separately
backed up; do not blindly retry generation during quota exhaustion.

An **isolated first-chapter preview**, outside the canonical production, verified
real Edge TTS and FFmpeg assembly without new LLM requests:

| Metric | First chapter only |
| --- | --- |
| Storyteller | `gpt-6.1-sol` |
| Beats / words | 10 / 298 |
| Voice / synthesis rate | Andrew Multilingual / `+15%` |
| Audio / video duration | 105.163208 seconds / 105.163208 seconds |
| Measured WPM | 170.0214 |
| Source-audio mix | `0.10` beneath narrator `1.0` |
| Sample regression gain | narrator 0.9997; source 0.0902 |

The isolated preview fully decoded with FFmpeg, exit code 0 and no decode
errors. Hash comparisons confirmed the original full narration JSON, audio
JSON/MP3 and summary JSON/MP4 remained byte-for-byte unchanged.

The first-cut signal-confidence test was inconclusive (0.219 residual-explanation
fraction against a 0.25 threshold); it is not reported as a passed confidence
test. Independent hermetic frequency tests verify both source-audio and silent
fallback paths in each assembly branch. This preview is **not** the requested
full recap and its metrics must not be presented as full-film verification.

Preview file:
`C:\Projects\AUTO-VIDEO-MAKER\temp\sol_chapter_preview\productions\prd_20261105_sol_chapter_preview_001\units\unit-001\plan\narrated_summary.mp4`

## Final automated checks

On 2026-11-05, the final Core hermetic suite completed with **2,207 passed,
1 skipped**, two existing Pydantic deprecation warnings, and **zero external
network attempts**. The final run was performed without concurrent live
generation. K3 completed **173 tests**, and its Vite production build passed
with the existing bundle-size warning. K3 was pushed to `origin/main` as
`fa43f402b8a8aea33023e1e6ba53252503681e59`.

The local full-suite evidence is retained at
`C:\Projects\AUTO-VIDEO-MAKER\temp\agentrouter_ship_suite.log`; the separate
live/partial-result evidence is at
`C:\Projects\AUTO-VIDEO-MAKER\temp\agentrouter_completion_evidence.json`.
Passing automated tests does not mean the provider-blocked full Sol render
completed.