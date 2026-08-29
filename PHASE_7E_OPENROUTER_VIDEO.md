# Phase 7E — Generic OpenRouter Video Gateway

Phase 7E adds AVM's generic, multi-model OpenRouter video generation provider. This document
describes what was actually implemented, the safety guarantees, and the exact provider/provenance
contract. It intentionally does not describe Phase 8 architecture.

## 1. Provider boundary

- Provider identity: `openrouter_video`
- Implementation: `app/orchestrator/openrouter_video.py`
- `requires_browser = False` (API-backed).
- The provider is **not** a Seedance, Wan, or Veo integration. It is a gateway to every model
  advertised through the current OpenRouter video API (`GET /api/v1/videos/models`).
- Model selection is fully generic and configurable (`AVM_OPENROUTER_MODEL` /
  `--openrouter-model`). When no model is explicitly selected, `select_video_model()` chooses a
  deterministic compatible model, preferring free models over paid models.

## 2. Model discovery and capability information

- The provider reads the current OpenRouter video catalog from `GET {base}/videos/models`.
- `parse_video_model_catalog()` maps the documented catalog payload into
  `OpenRouterVideoModel` objects carrying:
  - `model_id`, display name, provider/vendor
  - video capability flag
  - supported aspect ratios, resolutions, durations
  - audio/generate-audio capability
  - pricing SKUs (`starts_at`, `amount`, `unit`) and free/paid classification
- `classify_access()` derives `free`/`paid` from pricing data. No hardcoded model lists such as
  `["seedance", "wan", "veo"]` are used as architectural boundaries.
- Catalog results are cached at a configurable path (`AVM_OPENROUTER_MODEL_CACHE`) with a TTL
  (`AVM_OPENROUTER_MODEL_CACHE_TTL`, default 900 s). Fresh discovery is always possible; the cache
  is never the source of truth for pricing or availability.

## 3. Free-first policy and paid safety

- Access classification: `free` | `paid` (derived from live pricing).
- Free-first selection rules:
  1. Explicit user/model choice (if supplied and compatible).
  2. Compatible free models, in deterministic catalog order.
  3. Compatible paid model **only** when explicitly authorized.
- `AVM_OPENROUTER_ALLOW_PAID` / `--openrouter-allow-paid` authorizes paid generation for the
  selected model. Default: **disabled**.
- `AVM_OPENROUTER_ALLOW_PAID_FALLBACK` / `--openrouter-allow-paid-fallback` is persisted as an
  opt-in marker only. Phase 7E never automatically switches to a paid model and never automatically
  resubmits a failed free generation as paid.
- There is **no silent free → paid fallback** for quota exhaustion, timeouts, model failure, or
  errors. A failing free attempt surfaces a truthful failure and an optional paid alternative.

## 4. Capability-aware request validation

`capability_errors()` compares the scene request against the selected model's capabilities before
the billable boundary:

- video capability
- aspect ratio (9:16, 16:9, 1:1, 4:3, etc.)
- duration (model-supported duration list / range)
- resolution when advertised
- audio/generate-audio when requested

Incompatible requests fail before any network submission with a truthful explanation.

## 5. Request translation and asynchronous lifecycle

- `build_video_request()` translates AVM's normalized scene request into the current OpenRouter
  video request shape. AVM's internal planning `duration` is **not** leaked into the API when the
  model does not support an explicit duration parameter; model-supported values are derived from
  the capability catalog.
- Lifecycle: `POST {base}/videos` → returned `id`/`polling_url` → bounded polling
  (`AVM_OPENROUTER_DEFAULT_POLL_INTERVAL`, max attempts) → `COMPLETED` state → video URL download.
- Polling failures never resubmit the job.

## 6. Idempotency and billable safety

- Once a remote job is accepted, the `id` + `polling_url` are persisted in the scene checkpoint.
- On resume, `has_remote_job` causes the pipeline to call `resume_scene()` which polls the persisted
  remote job and retrieves the output — it never calls `POST /videos` again.
- If the remote job cannot be recovered, the provider fails honestly and requires a deliberate new
  generation. No automatic duplicate billable submission is possible.
## 7. Download safety and the Phase 7A output contract

- The completed video URL is downloaded to `<output>.part`, verified non-empty, then atomically
  renamed to `scenes/<project_id>/<scene_id>.mp4`.
- The final scene file passes the existing `validate_video_file(path, aspect_ratio)` contract:
  exists, non-empty, video stream, positive duration, valid dimensions, exact aspect ratio.
- Failed/stale `.part` files are removed.

## 8. Audio integration (Phase 7D)

- The provider inspects the downloaded media and populates
  `SceneGenerationResult.audio_facts` (provider-agnostic `AudioMediaFacts`: `has_audio`,
  `audio_codec`, `audio_duration`, `sample_rate`, `channels`, `is_synthetic`).
- Native model audio is **not** mixed with narration inside the provider. The Phase 7D assembly
  policies (`narration_only`, `native_only`, `native_plus_narration`) remain authoritative.

## 9. Provenance

`SceneGenerationResult.verification` records truthfully:

- `provider = openrouter_video`
- `model` (the actual OpenRouter model ID)
- `access` (`free` | `paid`)
- `job_id`, `polling_url`
- `estimated_cost`, `actual_cost`, `usage` when returned by the API

The provider is never represented as `local_asset`, `external_video`, `google_flow`, or
`image_motion`. API keys are never persisted, printed, or committed.

## 10. Configuration

Environment variables (all optional; empty/absent means unavailable):

| Variable | Meaning | Default |
| --- | --- | --- |
| `AVM_OPENROUTER_API_KEY` | API key; absent -> provider unavailable | - |
| `AVM_OPENROUTER_BASE_URL` | API base URL | `https://openrouter.ai/api/v1` |
| `AVM_OPENROUTER_MODEL` | Video model ID | discovery/selection |
| `AVM_OPENROUTER_RESOLUTION` | Requested resolution | model default |
| `AVM_OPENROUTER_ALLOW_PAID` | Authorize paid generation | `false` |
| `AVM_OPENROUTER_ALLOW_PAID_FALLBACK` | Opt-in marker only | `false` |
| `AVM_OPENROUTER_GENERATE_AUDIO` | Request audio when supported | `false` |
| `AVM_OPENROUTER_MODEL_CACHE` | Catalog cache path | default temp path |
| `AVM_OPENROUTER_MODEL_CACHE_TTL` | Cache TTL seconds | `900` |

CLI flags: `--openrouter-model`, `--openrouter-resolution`, `--openrouter-allow-paid`,
`--openrouter-allow-paid-fallback`, `--openrouter-generate-audio`.
`--visual-default-provider openrouter_video` and `--route-scene scene_id=openrouter_video` are
supported in hybrid mode. OpenRouter selection is persisted per project and is authoritative on
resume (API keys remain environment-only).

## 11. Google Flow and ImageMotion remain private/local

- Google Flow remains the private personal route. It is not exposed as an OpenRouter model, is not
  part of the OpenRouter catalog, and is never selected by any OpenRouter logic.
- ImageMotion remains the local zero-cost route; OpenRouter is never mandatory.

## 12. Current API findings and limitations

- The implementation was written against the OpenRouter public OpenAPI document and the
  `/api/v1/videos/models` catalog shape retrieved during Phase 7E. Any discrepancy between this
  document and the live API should be resolved in favor of the live API.
- Whether any currently listed OpenRouter video model is genuinely free is determined at runtime
  from the live catalog; no permanent free-model list is hardcoded.
- No real video generation was performed during Phase 7E implementation; all tests are hermetic
  against mocked HTTP, catalog, polling, and download boundaries.

## 13. Deliberately deferred

- Full web UI model selector
- Automatic model ranking/scoring
- Automatic paid fallback execution
- Non-OpenRouter video providers
- Phase 8 planning/execution architecture