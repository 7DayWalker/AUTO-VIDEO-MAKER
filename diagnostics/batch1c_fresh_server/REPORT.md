# BATCH 1C FRESH-SERVER LIVE VERIFICATION — REPORT

Date: 2026-09-14 · Mode: investigation only, ZERO production-code changes
Server: PID 22924, started 2026-09-14T15:46:47+01:00 (`python -m app.web.server`, port 8787),
launched with `ALIBABA_API_KEY` propagated from Windows User scope (`sk-ws-` class, 116 chars;
secret never printed; not `sk-sp-`). Stale pre-test server (PID 13204) terminated first.
Health: `{"ok": true, "service": "avm-web"}`. Only one Python process ran the tests.

## Executive result

- TEST A (local): **GREEN** — proven end-to-end on the fresh process.
- TEST B (alibaba_image_local_video): **GREEN** — genuinely hybrid (Alibaba image + local motion).
- TEST C (alibaba_video): **GREEN on attempt 2**; attempt 1 failed pre-submission (see defect D1).
- The earlier stale-server incident hypothesis is CONFIRMED as plausible: a fresh process routes
  exactly per the persisted `generation_strategy`; no Local/Hybrid substitution occurred.

## Tests (all: 1 unit, 2 scenes, target 10 s, planner alibaba/qwen-plus, TTS edge_tts)

### TEST A — `prd_20260914_freshserver_test_a_local_hybrid_200`
- selected/persisted: `local` / `local`; CC label "Local / Hybrid (default)".
- execution: hybrid mode, `visual_default_provider=image_motion`, `visual_source=auto`
  (scene_provider_routing both image_motion). Auto chain first child = AlibabaImageSource →
  2 billable images (wan2.2-t2i-flash 720*1280; tasks ac4cd62f…, 6d7cb927…; asset_manifest
  `source_provider=alibaba`, `routed_via=auto`). ImageMotion rendered 5 s/7 s 1080x1920 h264
  (+aac narration per scene). Assembly FFmpeg → final 12.033 s, 2,898,152 bytes, h264 + aac.
- artifact: `output/projects/20260914_freshserver_verification_test_751/final/…_final.mp4`
- classification: **GREEN**. Note: creation response said `cost_class: local_free` while
  execution made 2 billable Alibaba image generations (see D3).

### TEST B — `prd_20260914_freshserver_test_b_alibaba_image_local_v_914`
- selected/persisted: `alibaba_image_local_video`; CC label "Alibaba Image → Local Video".
- execution: `visual_source=alibaba` (explicit; asset metadata has NO `routed_via`), images
  wan2.2-t2i-flash (tasks 938f30ff…, e4840ecd…), ImageMotion 6 s/5 s, assembly FFmpeg →
  final 11.033 s, 2,332,693 bytes, h264 + aac.
- artifact: `output/projects/20260914_freshserver_verification_test_273/final/…_final.mp4`
- classification: **GREEN** (distinguished from Full AI Video by execution evidence, not
  visuals: image-only DashScope t2i tasks + image_motion checkpoints).

### TEST C — `alibaba_video`
- Attempt 1 `prd_20260914_freshserver_test_c_alibaba_full_ai_video_201`: plan drew 4.0 s +
  6.0 s scenes → adapter refused PRE-SUBMISSION ("requested duration 4s is outside the
  model's supported durations [5, 10] (no provider default was substituted)", category
  capability, fallback-eligible). Fallback attempted; OpenRouter video fallback UNAVAILABLE
  (D2) → truthful Alibaba-only failure. **Zero video spend.** Project …test_486.
- Attempt 2 `prd_20260914_freshserver_test_c2_alibaba_full_ai_vide_297`: plan drew 5.0/5.0 →
  full path executed. scene_config: hybrid, `visual_default_provider=alibaba_video`, routing
  both scenes alibaba_video ("explicit CLI/config override"). DashScope t2v tasks
  35f651dd-3811-4620-814d-c6c030399ff6 and 00c8df4a-0c30-41c6-a141-424e7bce066a, model
  wan2.2-t2v-plus, usage {video_duration:5, video_ratio:1080*1920, video_count:1}, clips
  silent (has_audio=false, truthfully recorded). Scene MP4s = downloaded Alibaba artifacts
  (byte sizes match checkpoints; 5.000 s each; NO assets/, NO temp/auto_source →
  ImageMotion never ran). Assembly FFmpeg added narration → final 10.033 s, 3,185,512
  bytes, 1080x1920 h264 + aac.
- artifact: `output/projects/20260914_freshserver_verification_test_881/final/…_final.mp4`

## Provenance matrix

| Test | User Selection | Persisted | Actual Provider | Actual Model | Media | Video Engine | Assembly | Final Artifact | Command Center | Result |
|---|---|---|---|---|---|---|---|---|---|---|
| A | local | local | alibaba (images, via auto chain) | wan2.2-t2i-flash | 2 AI images (ac4cd62f, 6d7cb927) | image_motion | FFmpeg | 12.033s 1080x1920 h264+aac | "Local / Hybrid (default)" + image_motion rows | GREEN |
| B | alibaba_image_local_video | alibaba_image_local_video | alibaba (images, explicit) | wan2.2-t2i-flash | 2 AI images (938f30ff, e4840ecd) | image_motion | FFmpeg | 11.033s 1080x1920 h264+aac | "Alibaba Image → Local Video" + image_motion rows | GREEN |
| C1 | alibaba_video | alibaba_video | none (pre-submission refusal) | wan2.2-t2v-plus (never submitted) | none | none (guard fired) | none | none (truthful FAILED) | FAILED state shown | guard correct; route unproven |
| C2 | alibaba_video | alibaba_video | alibaba (video) | wan2.2-t2v-plus | 2 t2v clips (35f651dd, 00c8df4a; silent) | alibaba_video (ImageMotion NOT used) | FFmpeg | 10.033s 1080x1920 h264+aac | "Alibaba Video" + alibaba_video rows | GREEN |

## Mismatches / defects (documented, NOT fixed)

- D1 Planner→adapter duration contract mismatch: planner emits arbitrary in-schema scene
  durations (schema 2–10 s) but wan2.2-t2v-plus accepts only 5/10 s → alibaba_video
  productions fail intermittently at plan-draw time (C1 failed, C2 passed). No normalization
  on either side.
- D2 OpenRouter VIDEO fallback reads `AVM_OPENROUTER_API_KEY` (openrouter_video.py:38) but
  .env defines `OPENROUTER_API_KEY` → video fallback permanently "not available" in this
  deployment; classified-eligible Alibaba failures degrade to Alibaba-only failures.
- D3 Creation response always says `cost_class: local_free` even for billable strategies.
- D4 Scene manifest records `generation_cost: 0` for billable alibaba_video scenes
  (hardcoded in the local boundary pass, pipeline.py:2041).
- D5 Unit Detail `job_id_present=false` for Alibaba tasks (UI key is Flow/OpenRouter-specific;
  DashScope `task_id` is persisted but not surfaced); image-source provenance (Alibaba images
  under Local/Hybrid) exists only in asset manifests, never displayed.
- D6 No INFO-level execution logs captured anywhere (no logging handler configured; only
  WARNING+ reaches stderr) — submission/polling evidence lives only in durable checkpoints.
- D7 `scene_config.model` still says "Veo 3.1 - Lite" (legacy Flow-era default) — cosmetic.

## Billable calls made (spend discipline kept)

- 4 Alibaba images (wan2.2-t2i-flash): 2 (Test A, via auto chain) + 2 (Test B).
- 2 Alibaba videos (wan2.2-t2v-plus, 5 s, 1080*1920): Test C2 only. C1 submitted nothing.
- 4 qwen-plus planning calls (one per production; text, negligible). No OpenRouter calls.
- No retries for prettiness; C2 was the minimum necessary re-attempt after a pre-billable
  refusal (the guard guarantees no video spend on a non-5/10 plan draw).

## Raw evidence

`test_*_creation.json`, `test_*_production_detail.json`, `test_*_unit_detail.json`,
`test_*_project_payload.json`, `server_stdout.log`, `server_stderr.log` (this directory);
production intents under `productions/`, execution manifests under `output/projects/`.

- classification: **GREEN** (attempt 2), with defects D1/D2/D4 documented.

