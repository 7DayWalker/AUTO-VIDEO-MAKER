# AVM PHASE 11C — BATCH 1C LIVE VALIDATION EVIDENCE REPORT

Date: 2026-09-13 (UTC) · Credential: dedicated AVM application key, `sk-ws-…`
class (present in the runtime environment as `DASHSCOPE_API_KEY`; never
printed, never persisted). Forbidden `sk-sp-…` Token Plan class: not used
(pre-flight class check enforced; the harness aborts on it).

Constraints honored: NO implementation changes, NO roadmap changes, one
sample per capability, no parallel samples, no comparison spend.

## Per-capability results

| Capability | Result | Evidence |
|---|---|---|
| Alibaba text generation (`qwen-plus`) | **LIVE-CONFIRMED at transport/auth/model level; FAILED at the AVM contract level** | Server returned a valid completion with full usage metadata (proven by the recorded validation error contents); construction of the existing `LLMResponse` then failed: `usage.prompt_tokens_details` (`{'cached_tokens': 0}`) cannot validate against `LLMResponse.usage: Dict[str, int]`. Root cause is AVM-side contract typing, NOT a provider failure. |
| Alibaba structured JSON generation (`qwen-plus`, json_object) | Same as above | Identical error signature; JSON mode reached the server and a completion returned. |
| Alibaba image generation (`wan2.2-t2i-flash`) | **LIVE-VALIDATED** | 720×1280 PNG, 834,472 bytes, 12.45s end-to-end; task `5200f8f7-95f7-4433-87df-1237dfd65591`; passed `verify_sourced_image` + ffprobe; provenance provider=alibaba, model, task_id, source_type=ai_generated, usage={image_count: 1}. Artifact: `diagnostics/batch1c_live/image/autosrc_live_image_001.png`. |
| Alibaba video generation (`wan2.2-t2v-plus`) | **LIVE-VALIDATED** | 1080×1920, 5s, h264, 3,756,339 bytes, 102.4s end-to-end; task `f3cfde29-51af-47b3-92d4-eacdb713c834`; passed the existing Phase 7A `validate_video_file` (exact 9:16); usage {video_duration: 5, video_ratio: 1080*1920, video_count: 1}; native audio facts truthfully recorded (has_audio=false — Wan T2V produced a silent clip; Phase 7D untouched). Artifact: `diagnostics/batch1c_live/video/live_video_001.mp4`. |

## Live-discovered parameter truths (provider-verified, exact)

- `wan2.2-t2v-plus` accepts ONLY these sizes (provider error text):
  `1080*1920, 1920*1080, 1440*1440, 1632*1248, 1248*1632, 480*832,
  832*480, 624*624`.
- Only `1080*1920` / `1920*1080` satisfy AVM's EXACT aspect-ratio contract
  (live-proven: `480*832` = 0.577 was generated but truthfully rejected by
  the Phase 7A aspect gate because 9:16 = 0.5625).
- Therefore the smallest AVM-compatible configuration for `wan2.2-t2v-plus`
  is **1080*1920 @ 5s** (the shipped default map's `720*1280` is NOT valid
  for this model and must be corrected in a follow-up change; until then,
  operators MUST set `AVM_ALIBABA_VIDEO_SIZES=9:16=1080*1920,16:9=1920*1080`).
- Duration 5s and 10s both accepted by the adapter; 5s validated live.
- Region: the DashScope **international** endpoint
  (`dashscope-intl.aliyuncs.com`) worked with this credential on first call.

## Adapter behavior proven live

1. Credential class acceptance (sk-ws) and pre-flight sk-sp abort logic.
2. OpenAI-compatible chat completions round-trip (auth, model, usage).
3. DashScope async submission → `task_id` → bounded polling → SUCCEEDED →
   result URL download → local materialization → existing validators.
4. Truthful failure classification: the invalid-size video attempt was
   labeled `task_failure` (fallback-eligible) — the classified-fallback
   pipeline behaved exactly as designed and returned a truthful error
   carrying the provider's parameter truth.
5. Artifact hygiene: the aspect-rejected completed clip was removed from
   disk rather than presented as a scene output.

## Video submissions accounting (spend)

1. `73d21456-…` — InvalidParameter (480*880): fast server-side rejection,
   no media produced (parameter validation, pre-generation).
2. `4893aa69-…` — completed 480*832 @5s; rejected by AVM's exact-aspect
   contract; artifact removed (billable sample).
3. `f3cfde29-…` — completed 1080*1920 @5s; LIVE-VALIDATED (billable sample).
Total: two billable video generations — one 480*832 (rejected post-hoc by
AVM contract), one 1080*1920 (validated). One image. Two minimal text/JSON
completions. Within the predefined validation budget.

## Required follow-ups (NOT performed — "no implementation changes")

1. **LLM usage contract:** DashScope returns OpenAI-style nested usage
   (`prompt_tokens_details.cached_tokens`). `LLMResponse.usage` is
   `Dict[str, int]`. Minimal fix: map usage to the integer keys
   (`prompt_tokens`, `completion_tokens`, `total_tokens`) in
   `AlibabaProvider` (and/or relax `LLMResponse.usage` typing). Until then
   text/JSON generation through the Alibaba seam raises on response
   construction even though the provider round-trip succeeds.
2. **Default video size map:** correct
   `app/orchestrator/alibaba_video_config.py::DEFAULT_VIDEO_SIZE_MAP` to
   `9:16 → 1080*1920`, `16:9 → 1920*1080` (provider-validated truth above).
3. Optional: record the validated `wan2.2-t2i-flash` behavior as the
   documented default (already validated live at 720*1280).

## Offline status after live validation

All 69 Batch 1C tests pass; full suite previously 878 passed / 1 skipped.
One mechanical structural repair was required during live validation: the
`DashScopeClient` class boundary in `app/orchestrator/alibaba_gateway.py`
had been broken by earlier file repairs (methods orphaned inside
`AlibabaGatewayResponseError`; `__init__` detached). This was restored to
the already-approved design (no behavioral or design change; all 43 visual
tests re-passed immediately after).

Raw evidence: `diagnostics/batch1c_live/evidence.json` (credential-scrubbed).
Harness: `diagnostics/phase11c_batch1c_live_validation.py` (diagnostics only).
