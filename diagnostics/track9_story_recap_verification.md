# Duration-aware AI Summary story recaps

## Core contract

`POST /api/productions/{pid}/units/{uid}/narration` accepts
`target_length_preset` (`auto` by default, `short`, `story`, `deep`). Explicit
finite positive `target_duration_seconds` takes precedence and resolves to a
`custom` budget. The artifact persists `target_length_preset`,
`target_duration_seconds`, and `narration_budget` without exposing provider keys.

| Preset / source length | Beats | Total narration words | Nominal target |
| --- | --- | --- | --- |
| Auto, under 3 minutes | 4–6 | 100–150 | 45–60 seconds |
| Auto, 3–10 minutes | 8–12 | 180–270 | 100 seconds |
| Auto, 10–45 minutes | 14–22 | 320–500 | 180 seconds |
| Auto, over 45 minutes | 22–32 | 500–800 | 270 seconds |
| Short | 8–10 | 150–210 | 75 seconds |
| Story | 15–22 | 350–520 | 195 seconds |
| Deep | 25–36 | 650–1,000 | 390 seconds |

Very short sources (under 75 seconds) and explicit sandbox runs retain bounded,
source-proportionate behavior. A preset that exceeds a short source is scaled
down instead of inventing missing story. Deliberately brief explicit durations
(30 seconds or less) remain supported for existing callers. TTS duration is
measured, not inferred from these nominal budgets; speaking rate changes timing.

Real story output must satisfy beat and word floors/ceilings, 2–3 sentences per
beat (normally 20–30 total words; a 16-word minimum permits natural variation),
chronological evidence, all populated fifths of the source timeline, and actual
opening/ending evidence within the first/last 10 percent. The temporal sections
guide setup, inciting incident, rising conflict, climax and resolution; they are
coverage checks, not claims that every story event occurs at a fixed percentage.

One bounded repair/expansion request precedes existing route/model failover.
Malformed, underlength and poorly grounded results never overwrite valid saved
narration. Long transcripts retain global IDs through bounded local notes and
final expansion. Pro reuses the same length/coverage authority after its visual
analysis; no text-only call is labeled as visual understanding.

Assembly allocates measured audio time per beat using word proportions and
cuts moving footage only within that beat's cited ranges. Shots are bounded to
six seconds; scarce footage is reused as moving footage, never a frozen EOF
frame. This is word-proportional pacing, not forced word-level audio alignment.

## K3

The existing dark native select in the Summary tier/model card offers:

- Auto (Duration-Scaled)
- Shorts Recap (60–90s)
- Story Recap (2.5–4 min)
- Deep Movie Recap (5+ min)

K3 passes intent through its same-origin `/api` proxy. It neither calculates
Core's budgets nor picks providers. Changing length keeps registered source/STT
but invalidates generated narration/audio/video controls; in-flight changes are
locked. Responses can show the resolved Core beat/word budget.

## Live Last Dragon Queen verification

Production: `prd_20261026_the_last_dragon_queen_part_i_ai_short_fi_719`

Unit: `unit-001`. Current real upload: `src-007`, 114,538,669 bytes,
1,805.152608 seconds; completed real Whisper transcript: 373 segments.

After restarting Core, an explicit real Auto narration request returned:

| Action | HTTP | Verified result |
| --- | --- | --- |
| Build narration | 200 | **14 beats, 488 words**, canonical `gemini-3-flash-preview`, Auto failover used |
| Synthesize audio | 200 | **165.864 seconds**, real Edge TTS `en-US-GuyNeural`, MP3 |
| Assemble video | 201 | **166.489909 seconds (2m46s)**, 854×480 H.264/AAC, 20,602,075 bytes |

Evidence spans **0.0–1,795.0 seconds**. All five timeline sections are represented
(4 / 4 / 3 / 3 / 3 beats overlap each section; a beat can span a boundary).
The final MP4 uses **38 moving shots**, maximum **5.948 seconds**, no freeze padding.
Each beat's cited ranges receive airtime even if one range is much shorter.
Narration grounding, both full FFmpeg media decodes, persisted readback and HTTP
206 range playback passed. The Whisper transcript hash remained unchanged;
prior plan artifacts were backed up locally. Nothing was published/uploaded.

Sanitized runtime evidence (not committed):
`C:\Projects\AUTO-VIDEO-MAKER\temp\dragon_story_live_verification.json`.

## Regression commands

Final requested Core suite: **259 passed**, two existing Pydantic deprecation
warnings, **0 external network attempts**, 149.49 seconds.

K3: **168 Node tests passed**;
`npm run build` passed with the existing large-chunk advisory.
Additional Core routing, Pro windowing, narration and assembly checks: **108
passed**, zero external network attempts.

Headless browser inspection verified all four options, Auto default, Story
selection, matching dark computed colors, responsive fit at 760 px, and Core
health through K3's same-origin proxy (HTTP 200). No image files were read.

```powershell
python C:\Projects\AUTO-VIDEO-MAKER\tools\run_hermetic_pytest.py C:\Projects\AUTO-VIDEO-MAKER\tests\test_track9_standard_real.py C:\Projects\AUTO-VIDEO-MAKER\tests\test_track9_api_routes.py C:\Projects\AUTO-VIDEO-MAKER\tests\test_track9_summary_publish_handoff.py -q
```

On Windows Node 20, expand `tests/*.mjs` with PowerShell before `node --test`;
Node does not expand a literal wildcard. Run `npm run build` in
`C:\Users\Paul Ella\AppData\Local\Cline\avm-studio-pro`.