# Real Standard narration: The Last Dragon Queen

Verified locally on 2026-10-30 (workstation local date), without mock providers,
image inspection, changes to source media/transcript, or publishing.

## Production

- Production: `prd_20261026_the_last_dragon_queen_part_i_ai_short_fi_719`
- Unit: `unit-001`; selected real source: `src-006`
- Source: 114,538,669 bytes; 1,805.152608 seconds; 854 x 480
- Transcript: `local_whisper`, 365 segments, 6,698 text characters

## Reproduced root cause

The original `narration_real._call_json` invoked `GeminiProvider.generate_json`,
which invoked `generate` and the real `generateContent` HTTP endpoint with native
JSON mode. It did **not** call a scene-script stub. The exact wire model
`gemini-3-flash-preview` was accepted.

The original 34,644-character prompt and 4,096-token shared reasoning/output
budget produced `finishReason=MAX_TOKENS`: 16,113 prompt tokens, only 151 visible
completion tokens, 20,192 total tokens. The 506-character JSON response stopped
mid-reference array. The adapter ignored the truncation finish reason; strict
JSON decoding raised `Narration model must return a valid JSON object`. Auto's
transient-only policy excluded that output error, so configured alternatives
were never tried. Failed builds left the earlier sandbox narration untouched.

## Fixes

- Native JSON completion remains the provider contract. Gemini JSON requests
  reserve visible output and constrain thinking; all three adapters reject
  truncated output. Gemini joins non-thought text parts and keeps API keys out
  of transport URLs.
- Complete JSON objects/beat arrays are extracted through fences and prose.
  Equivalent text, ID and timestamp fields normalize against actual evidence.
  Unknown IDs, invalid ranges and incomplete outer JSON remain failures.
- Compact transcript IDs/text retain all source dialogue; oversized inputs
  use byte/token-bounded windows and validated hierarchical merging. IDs stay
  global; timestamps can be restored deterministically instead of generated.
- One bounded output-repair attempt per window, then route/model failover.
  Auto handles provider/format/grounding/access/quota errors. Explicit selection
  stays within its canonical model. Local bugs and moderation are not hidden.
- Actual alternative models have their own canonical identities. No Gemini
  preview is relabeled as a different model. Catalogue presence does not imply
  account entitlement: direct Gemini 2.5 Flash was listed but rejected at runtime.
- `edge-tts` 7.2.8 returned WebSocket HTTP 403. Upgrading the existing dependency
  to 7.3.1 restored real speech through the current Microsoft client protocol.
  TTS error diagnostics omit transport URLs/tokens.
- Summary visuals use bounded subranges of cited evidence to match measured
  voice duration, rather than rendering long evidence spans with trailing silence.
- Replacing existing narration returns HTTP 200; first creation remains 201.

## Live HTTP verification

Core was restarted on `http://127.0.0.1:8787`. All requests below used the
production and unit above; `mock_mode` was false.

| Action | Result | Verified artifact |
| --- | --- | --- |
| `POST .../narration` with `{"summary_mode":"standard","mock_mode":false}` | **200**, 17.80 s | 4 beats; 74 words; 7 source refs; 13 valid transcript IDs; canonical `gemini-3.6-flash`; Auto failover used |
| `POST .../narration/synthesize` | **200**, 8.94 s | Real `edge_tts`, `en-US-GuyNeural`; MP3, 26.952 s, 161,712 bytes |
| `POST .../summary-video` | **201**, 8.34 s | H.264 + AAC, 854 x 480; 27.056576 s, 2,407,682 bytes |
| Summary media range GET | **206** | Requested first 1,024 bytes returned |
| Persisted narration and summary GET | **200** each | Saved artifacts reload successfully |

Both audio and video passed fresh probing and full FFmpeg decoding. Every
narration reference was checked for source identity, valid transcript index,
timestamp bounds and temporal overlap. The transcript SHA-256 stayed unchanged.
Prior plan artifacts were backed up locally before replacement.

Raw operator data, response snippets and generated media are not committed.
The detailed local report is
`C:\Projects\AUTO-VIDEO-MAKER\temp\dragon_standard_live_verification.json`.

## Reproducible hermetic checks

Final requested suite: **216 passed**, 2 existing Pydantic deprecation warnings,
147.07 seconds; **0 external network attempts**. Additional provider/registry,
canonical routing, voice and assembly regression checks: **213 passed**,
0 external network attempts. `pip check` reports no broken requirements.

```powershell
python C:\Projects\AUTO-VIDEO-MAKER\tools\run_hermetic_pytest.py C:\Projects\AUTO-VIDEO-MAKER\tests\test_track9_standard_real.py C:\Projects\AUTO-VIDEO-MAKER\tests\test_track9_api_routes.py C:\Projects\AUTO-VIDEO-MAKER\tests\test_track9_summary_publish_handoff.py -q
```

Tests replace external boundaries and isolate all runtime paths/credentials.
They cover the actual provider HTTP payloads, complete/truncated JSON, schema
normalization, bounded multilingual/long-ASR windowing, valid references,
Auto/explicit failover, failure non-persistence, TTS redaction, media timing,
and the canonical publish handoff. Live success is point-in-time verification,
not a guarantee against future provider outages, rate limits or credit exhaustion.