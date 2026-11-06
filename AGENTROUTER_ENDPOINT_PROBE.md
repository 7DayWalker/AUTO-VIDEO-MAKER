# AgentRouter capability verification — 2026-11-06

Bounded live requests used the existing ignored `AGENTROUTER_API_KEY`, HTTPS,
and the same public client User-Agent as the operator's private proxy. No key,
raw error/response body, audio bytes, image bytes or private proxy content is
included in this report. No production media was changed or uploaded.

## Catalog

| GET endpoint | HTTP | Verified catalog IDs |
| --- | --- | --- |
| `/v1beta/models` | 404 | No catalog on this route |
| `/v1/models` | 200 | `gpt-5.6-sol`, `gpt-6-astra`, `gpt-6.1-sol` |

The catalog contained no Claude, GPT-4o, audio or image models and had no next-page
token. A catalog entry alone does not establish quota, model weight identity or
execution success.

## Live POST endpoint/model results

| Endpoint | Models | HTTP | Result |
| --- | --- | --- | --- |
| `/v1/messages` | `claude-opus-5.5`, `claude-3-5-sonnet`, `claude-3-7-sonnet` | 503 each | Upstream failure; no successful text |
| `/v1/chat/completions` (direct) | Same three Claude IDs | 503 each | Upstream failure |
| `/v1/chat/completions` (direct) | `gpt-4o`, `gpt-6.1-sol`, `gpt-6-astra` | 400 each | Unsupported route/model operation |
| `/v1/responses` | Same three Claude IDs; `gpt-4o` | 503 each | Upstream failure; not admitted |
| `/v1/responses` | `gpt-6.1-sol`, `gpt-6-astra` | 200 each | Non-empty text; matching returned model; follow-up JSON also passed |
| `/v1/responses` | `gpt-5.6-sol` | 200 | Follow-up JSON `{ "ok": true }` verified with matching returned model |
| `/v1/chat/completions` (existing local proxy) | `gpt-6.1-sol` | 200 | Non-empty matching text; proxy unchanged |
| `/v1/audio/speech` | `tts-1`, `tts-1-hd`, `gpt-4o-mini-tts` | 503 each | No valid audio bytes |
| `/v1/audio/transcriptions` | `whisper-1` | 503 | No valid transcription envelope |
| `/v1/audio/translations` | `whisper-1` | 503 | No valid translation envelope |
| `/v1/images/generations` | `dall-e-3`, `gpt-image-1`, `flux` | 503 each | No valid image bytes |

Audio requests used a generated one-second 16 kHz mono WAV tone, not operator
audio. Image requests asked for one plain square. Text output budgets were small;
requests used bounded timeouts and concurrency, not a load test.

## Enabled vs deliberately unchanged

- **Enabled:** Core's existing Sol/Astra adapter uses native Responses for direct
  HTTPS and preserves Chat Completions for loopback proxy configuration. It
  validates completion status, returned model identity, refusal/truncation and
  known integer usage counters. JSON mode and instructions are translated into
  the Responses envelope. Media is rejected before HTTP. Redirects are disabled.
- **Stage 1:** full-size Gemini Flash routes first, then the configured Alibaba/
  OpenRouter Qwen vision routes, then Flash-Lite. Cached batches keep the observer
  that actually produced them; mixed evidence is reported as mixed/unknown,
  never as the initially configured model. Explicit rate/quota failures become
  short-lived route blockers. Auto never prefers Flash-Lite over eligible
  full-size models merely because their capability flags tie.
- **Not enabled:** AgentRouter Claude/GPT-4o, cloud TTS, cloud Whisper transcription/
  translation and image generation. GPT-4o keeps its pre-existing OpenRouter
  binding; CodeCraft's Claude quarantine remains intact. Recap speech stays
  `edge_tts` with `en-US-AndrewMultilingualNeural` at `+15%`; STT stays local Whisper.
- **Discovery only:** `gpt-5.6-sol` passed the small direct JSON probe but was not
  added as a new canonical selector in this Sol/Astra-focused upgrade.

`GET /api/capabilities` remains a read-only, local configuration-readiness
projection. "Ready" is not an uptime or remaining-quota guarantee. It now includes
the observer priority for two-stage models; runtime chapter artifacts are the
authority for the observer that succeeded. There is no speculative Messages,
direct Chat, audio or image readiness based on key presence.

The sanitized private per-request diagnostics are retained locally at
`C:\Projects\AUTO-VIDEO-MAKER\temp\agentrouter_endpoint_probe.json` (ignored by Git).

## Restarted Core capability snapshot

`GET http://127.0.0.1:8787/api/capabilities` returned **HTTP 200** after starting
the updated Core. The API's canonical model projection contains these 13 models:

| Canonical model | Configuration readiness | Summary modes |
| --- | --- | --- |
| `qwen-3.8-flash` | Not configured (quarantined CodeCraft) | Standard |
| `gemini-3-flash-preview` | Ready | Standard, Pro |
| `gemini-2.5-flash` | Ready | Standard, Pro |
| `gemini-3.6-flash` | Ready | Standard, Pro |
| `gemini-3.5-flash-lite` | Ready (still fallback in Stage 1) | Standard, Pro |
| `qwen-vl-max` | Ready | Standard, Pro |
| `gpt-4o` | Ready (existing OpenRouter, NOT AgentRouter) | Standard, Pro |
| `qwen2.5-vl-72b-instruct` | Ready | Standard, Pro |
| `qwen-plus` | Ready | Standard |
| `gemma-4-26b-a4b-it` | Ready | Standard, Pro |
| `claude-opus-5.5` | Not configured (quarantined CodeCraft) | Standard, Pro |
| `gpt-6.1-sol` | Ready | Standard, Pro (two-stage) |
| `gpt-6-astra` | Ready | Standard, Pro (two-stage) |

Both two-stage models project `visual_observer_model_id=gemini-3-flash-preview`
and the five-model priority chain despite the workstation's old Flash-Lite
override. These are configured-route facts, not fabricated live-vision results.
Freshly accepted/cached chapter artifacts determine actual observer attribution.

The updated Python adapter itself also completed direct JSON calls for Sol and
Astra with **HTTP 200**, matching returned model IDs and `image_payloads_sent=false`.

## Automated verification

- Requested Core hermetic suites (`test_track9_canonical_routing.py`,
  `test_track9_standard_real.py`, `test_track9_extended_chapters.py`, and the added
  `test_track11a_model_registry.py`): **230 passed**, **0 external attempts**.
- Focused Responses/observer/cache/storyteller regression run: **112 passed**,
  **0 external attempts**.
- Full Core hermetic suite: **2,308 passed, 1 skipped**, **0 external attempts**
  (693.87 seconds). Two existing Pydantic deprecation warnings remain.
- K3: **178 passed**, no failures/skips; `npm run build` exit **0**. Vite's existing
  large-chunk advisory remains nonfatal.
- The operator-owned `C:\Users\Paul Ella\proxy.js` remained byte-for-byte unchanged;
  its local text route still passed. No helper installation or proxy restart was
  performed.
- A final scan of the 23 task files found **zero configured-secret matches**.

K3 commit pushed to `origin/main`:
[`e97905cd89c548719132ad12a17b7e6f4bfc8a4a`](https://github.com/7DayWalker/avm-studio-pro/commit/e97905cd89c548719132ad12a17b7e6f4bfc8a4a).

Local verification logs are ignored runtime artifacts:
`C:\Projects\AUTO-VIDEO-MAKER\temp\stage1_full_hermetic.log`,
`C:\Projects\AUTO-VIDEO-MAKER\temp\stage1_k3_tests.log`, and
`C:\Projects\AUTO-VIDEO-MAKER\temp\stage1_k3_build.log`.