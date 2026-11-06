# Movie recap closeout: broadcast voice, cold open and true ending

## Runtime contracts

- Real recap TTS is one continuous Andrew Multilingual request at `+15%` by
  default. Silence tightening and tempo correction precede a recap-only
  70 Hz high-pass/compressor and **measured two-pass -14 LUFS** master. The
  encoded file must pass **-14 LUFS +/-0.5 LU, true peak <= -1 dBTP**.
- The source bed defaults to **0.05**; gain above **0.055** is rejected. A
  **-6 dB, 1.5 kHz** pocket plus 80 Hz high-pass/10 kHz low-pass and speech-keyed
  ducking separate the narrator. Both assembly branches use `amix normalize=0`
  and post-mix loudness measurement; other production audio workflows are unchanged.
- New long chapter recaps reserve the last **2-3 beats** for **92%-100% of active
  story**. Missing closing citations are rejected, not fabricated. Stage 1 must
  include the final three minutes and a closing-scene sample. Explicit
  `credits_only` notes may exclude a non-narrative tail; absent dialogue alone
  never means credits. Unknown tails retain full source-duration coverage.
- Beat 1 receives an evidence-backed, one-sentence stakes/curiosity hook and
  flows immediately into its opening scene.
- The closing moving cut reaches the narrative boundary. Selecting only a
  beat's early citations, favoring a longer battle over a short ending citation,
  or cumulative frame rounding must not erase the ending. A persisted shot
  manifest records exact source and output intervals.
- Invalid chapter prose does not discard unrelated later validated checkpoints.
  Stage 1 observations remain independent of storyteller, TTS and mix settings.

## The Last Dragon Queen / Part I — verified render

Production: `prd_20261026_the_last_dragon_queen_part_i_ai_short_fi_719`,
`unit-001`, source `src-010`, canonical text storyteller `gpt-6.1-sol`.

Only Beat 1 and Beats 51-53 changed. **Beats 2-50, Chapters 2-4 and the entire
Stage 1 cache are unchanged**. All five chapters reuse retained evidence;
**zero new vision requests** were made. Chapter 5 already contained citable
observations throughout 1620-1794s. The old script cited the final scene, but
the old visual allocator's last actual shot stopped at **1785.329s**.

The source actually ends on a **Part I cliffhanger**, not a peaceful resolution.
Local scene-boundary detection plus offline Windows OCR identified a branded
end card starting at **1803.566667s**; the final **1.585941s** is non-story.
The new recap preserves that unresolved ending rather than inventing a victory.

### Measured encoded audio

| Signal | Before LUFS | After LUFS | Before RMS dBFS | After RMS dBFS | Before/after true peak dBTP |
| --- | ---: | ---: | ---: | ---: | ---: |
| Narrator MP3 | -22.09 | -14.27 | -22.25 | -14.39 | -3.83 / -1.32 |
| Final MP4 | -22.07 | -14.01 | -25.09 | -17.13 | -6.80 / -3.97 |

The mono narrator and stereo MP4 are measured separately; equal LUFS need not
have equal RMS/sample peaks. An independent EBU-R128 measurement confirmed
approximately -14.2 LUFS narrator and -14.0 LUFS final MP4.

Measured narrator-to-background RMS separation: **13.514 -> 32.477 dB**.
The before bed was reconstructed from the backed-up original cut allocation;
the after bed was measured from the actual EQ/sidechain-processed source stem.
This is signal-level separation, not just the nominal gain ratio. Nominal
bed gain changed from 0.10 (-20 dB) to 0.05 (-26.02 dB), with additional vocal
pocket/ducking. Narration is never halved by `amix`.

### Hook and closing beats

**Cold open:** “Dragons are supposedly extinct—so why is a heartbeat pulsing
beneath their massive bones?”

**Beat 51:** “Ash’s chains glow red-hot, sending the commanders into panic. As
he struggles against his restraints, they order everyone back, then demand
that the dragon be killed.”

Moving source cuts: **1712.145-1717.617461s**, **1734.000-1739.472461s**.
Output: **473.000-484.000s**.

**Beat 52:** “The soldiers retreat as Ash thrashes, but their commanders demand
they hold the line and fire. Arrows pierce his scales while his companion begs
him not to kill the troops.”

Moving source cuts: **1745.992-1751.464461s**, **1779.838-1785.310461s**.
Output: **484.000-495.000s**.

**Beat 53:** “Amid flames and burning wreckage, Ash raises his head as soldiers
stand frozen. Yet the orders to kill him and keep attacking continue. Part I
ends on an unresolved cliffhanger, with the battle still raging.”

Moving source cuts: **1791.120-1796.592461s**, **1800.600250-1803.566667s**.
Output: **495.000-503.466417s**. The final cut ends immediately before the end card.

### Media verification

**503.466417 seconds (8:23.466), 92 moving shots, 53 beats, 1,500 words**.
H.264/AAC, 854x480, 48 kHz stereo, 129,953,457 bytes. Narrator: Andrew
Multilingual, `+15%`, 178.761 measured WPM. Audio/video duration delta: 0.0s.

All six closing cuts were decoded and compared against the registered source
using local grayscale samples, with correlation **0.98516-0.99977**. A full
FFmpeg decode had **zero errors**. Source comparisons consume no vision quota.

Final MP4:
`C:\Projects\AUTO-VIDEO-MAKER\productions\prd_20261026_the_last_dragon_queen_part_i_ai_short_fi_719\units\unit-001\plan\narrated_summary.mp4`

Read-only report:
`C:\Projects\AUTO-VIDEO-MAKER\temp\final_polish_closeout_report.json`

Original reviewed artifacts are backed up under:
`C:\Projects\AUTO-VIDEO-MAKER\temp\summary_before_final_polish`

## Reproducible operator checks

```powershell
python 'C:\Projects\AUTO-VIDEO-MAKER\tools\verify_recap_closeout.py' prd_20261026_the_last_dragon_queen_part_i_ai_short_fi_719 --before-dir 'C:\Projects\AUTO-VIDEO-MAKER\temp\summary_before_final_polish' --report 'C:\Projects\AUTO-VIDEO-MAKER\temp\final_polish_closeout_report.json'
python 'C:\Projects\AUTO-VIDEO-MAKER\tools\run_hermetic_pytest.py' tests/test_track9_extended_chapters.py tests/test_track9_standard_real.py tests/test_track9_api_routes.py tests/test_recap_final_polish.py tests/test_recap_audio_pacing_mix.py tests/test_agentrouter_storyteller.py tests/test_track9_visual_observation_cache.py tests/test_track9_narration_synthesis.py -q
```

`C:\Projects\AUTO-VIDEO-MAKER\tools\polish_recap.py` is cache-only selective
writing: it instantiates no vision provider and never reads image files. It
checkpoints validated passages and skips a completed closeout. It does not
repeat synthesis or assembly. A manually supplied `--narrative-end` must be
grounded in an independently inspected source boundary; the tool never guesses
one from missing dialogue.

Do not run a heavy live render concurrently with the full hermetic media suite
on an 8 GB Windows workstation. The initial concurrent full run exhausted
Windows paging/process resources (15 failures, 2,226 passed, one skipped,
zero external attempts). The isolated 80-test compatibility retest passed all
cases, including the short real-TTS handoff previously affected by resource
pressure. No quota or provider connection failure is concealed as a successful
generation, and no social publishing/promotion was performed.

Final requested suites plus broadcast/ending/cache/verification regressions:
**332 passed, zero external network attempts**, two existing Pydantic warnings.
Evidence:
`C:\Projects\AUTO-VIDEO-MAKER\temp\final_polish_ship_final.stdout.log`.

The isolated full repository suite completed with **2,243 passed, one existing
skip, zero external network attempts**, and the two existing Pydantic warnings:
`C:\Projects\AUTO-VIDEO-MAKER\temp\final_polish_full_suite_isolated.stdout.log`.