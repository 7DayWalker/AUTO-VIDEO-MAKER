# Reusable Summary Pro visual evidence

Stage 1 now persists `frame_observations.json` in the unit's plan directory.
Its identity is source asset ID, source SHA-256, media path/duration, and transcript
hash. It deliberately excludes storyteller model, narration preset, and voice.
Exact source timestamps are remapped to each request's local frame IDs. Direct
observations take precedence over retained scene descriptions.

## Cache and failover order

1. Reuse validated frame observations from the unit cache/checkpoint.
2. Hydrate available timestamp-cited scene descriptions from this unit's accepted
   Pro narration or `visual_evidence_prior_pro.json` snapshot. This happens before
   network, so cached work does not consume vision quota.
3. For evidence not locally available, use the configured Gemini observer, then
   admitted image-capable Alibaba/OpenRouter routes on transient/quota failures.
   AgentRouter/port 8318 is never a vision route.
4. Recheck the local cache after a failed live observer, retaining successful
   individual observation batches. No copied transcript or invented caption is
   substituted for missing visual evidence.

Hydrated legacy notes are **scene-level descriptions**, not new descriptions of
every individual image. Only timestamps explicitly cited by the prior artifact
are citable. A previously inspected frame without retained text is marked
`unavailable`, `citable=false`; the storyteller is forbidden to infer its action.
The cache preserves original beat orders, cited frame timestamps, observer
identity, and prior artifact hash. Changed source/transcript identity prevents
reusing old observations.

The Last Dragon Queen's retained Pro artifact contains 155 inspected timestamps
in five chapters. It retains descriptions tied to 122 of those timestamps;
33 have no retained description. Chapter 1's existing Sol checkpoint provides
richer direct observations. The remaining four chapters can be grounded in the
retained, explicitly cited scenes plus their complete Whisper dialogue, without
any new Gemini request.

## Narration execution

The selected text storyteller remains exactly `gpt-6.1-sol` or `gpt-6-astra`.
Validated batches/checkpoints resume without repeating completed writing.
Transient text transport failures have bounded retries; output/citation/depth
repair remains separately bounded. `model_id` is now a supported narration API
alias, with the same explicit-selection lock as `summary_understanding_model_id`.

The narration artifact reports `stage1_cache_hits`,
`stage1_prior_pro_hydrated_chapters`, `live_vision_calls`, and per-chapter citable/
unavailable frame counts. Cached observations are evidence; they are never
reported as a new live vision request.

## Private proxy

`C:\Projects\AUTO-VIDEO-MAKER\tools\configure_agentrouter_proxy.py` updates
`C:\Users\Paul Ella\proxy.js` without printing its private contents. The guard
now blocks **actual attachment objects only**. Plain strings remain strings,
even when they contain `data:image`, `image_url`, `input_image`, or long code
examples. Tests execute the synthetic JavaScript bridge and verify this behavior.
Core's AgentRouter adapter still independently forbids binary/media inputs.

## Checks

```powershell
Set-Location -LiteralPath 'C:\Projects\AUTO-VIDEO-MAKER'
python 'C:\Projects\AUTO-VIDEO-MAKER\tools\run_hermetic_pytest.py' tests/test_track9_extended_chapters.py tests/test_track9_standard_real.py tests/test_track9_api_routes.py tests/test_track9_visual_observation_cache.py tests/test_agentrouter_proxy_config.py tests/test_agentrouter_storyteller.py tests/test_recap_audio_pacing_mix.py -q
```

After a timed-out POST, inspect the checkpoint/artifacts before manually
resuming. Synthesis/assembly can run independently after the narration is saved;
do not rewrite completed chapters just to repeat those media stages.

## Verified full live render

The cached route completed all five chapters of The Last Dragon Queen with
`gpt-6.1-sol`: **53 beats, 1,478 words, 521.637958 seconds, 170.003 WPM, 95 moving
shots**. Andrew Multilingual at `+15%` was used. All five chapters hit retained
Stage 1 evidence and **zero new vision requests** were made. Chapters 2–5 use
explicitly cited scene-level prior-Pro notes; Chapter 1 reused direct observations.

Both real narrator and the ducked cut-source track are mixed into the final AAC
soundtrack (`0.10` source gain, `1.0` narration, `amix normalize=0`). The full
H.264/AAC MP4 is 854×480 and 135,098,560 bytes. Full FFmpeg decode passed without
errors, and the narration-content fingerprint matches the assembled media.

Read-only measured evidence:
`C:\Projects\AUTO-VIDEO-MAKER\temp\extended_summary_live_metrics.json`

The signal-level mix verifier passed on the full recap: estimated narrator gain
**0.9996**, source gain **0.0912** (expected 0.10), with positive independent
source contribution above the confidence threshold. No silence-only surrogate
was counted as a movie audio bed.

Final hermetic verification: **309 passed**, zero external network attempts,
with two existing Pydantic deprecation warnings. The run includes the three
requested Track 9 suites plus cache, attachment-guard, routing and audio regressions.
Evidence: `C:\Projects\AUTO-VIDEO-MAKER\temp\sol_cached_ship_tests.log`.