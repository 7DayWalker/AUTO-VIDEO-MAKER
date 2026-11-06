# Movie recap startup repair and creator workflow

## Root cause and runtime proof

The helper `chapter_planning_timeout_seconds` was already present on disk in
`app/production/recap_budget.py` after commit `25ade11`. The old Core processes
started before that deployment. A module cached earlier by the live Python
process therefore lacked the new helper when narration was imported later.
Two pre-deployment `app.web.server` process trees were found and stopped;
leaving one behind produced inconsistent replies during the first restart.

The fix is a coherent restart, **not** reloading Pydantic model modules while
requests use them. The budget contract is now explicitly exported, its imports
are module-level, and server startup imports/checks Standard, Pro and chapter
planning before binding the port. `/api/health` remains unchanged.

Read-only runtime proof in the actual serving process:

```text
GET http://127.0.0.1:8787/api/summary/runtime
```

This returns only the process ID, contract name, import-identity booleans and
feature-film timeout values. No credentials, environment, transcript or paths
are returned. Final live verification used Core process **29608**.

## Preserved Kung Fu source and completed recap

Dedicated title: **They Mocked His Kung Fu — Until He KO'd the Giant!**

Production: `prd_20261106_they_mocked_his_kung_fu_until_he_kod_the_141`

Original bee production:
`prd_20261105_i_want_to_create_single_video_about_a_be_119`

- Exact uploaded source: `src-001`, 520,520,220 bytes (496.4 MiB),
  3,742.174331 seconds (1h02m22s), 1920×1080.
- Source SHA-256:
  `9083dab3eabd101ed5b21aa11ec052d35db29029d58d9b1d789cdb3e3516f888`.
- Completed Chinese Whisper transcript: `local_whisper`, `tiny`, 1,023
  segments. Its original byte-for-byte sidecar was copied without retranscribing.
- The dedicated project contains independent verified copies, not writable
  hardlinks. The original bee project still has its valid source and transcript.
- The Dragon Queen 8m23s recap and its existing accepted handoff file were not
  rebuilt, promoted, deleted or overwritten; original hashes and modification
  times were checked before and after.

Live **Summary Pro / Auto / GPT-6.1 Sol** narration returned HTTP 201 with
**8 chapters, 80 grounded beats, 2,203 words**, and an 810-second target. The
finished voiceover is **737.547438 seconds**; the final MP4 is **737.6 seconds
(12m17.6s)**, 582,200,562 bytes. Assembly returned HTTP 201.
Read-only validation confirmed 256 inspected source frames, 135 moving cuts,
179.2 spoken words/minute, one audio stream, matching script/media fingerprints
and zero final MP4 decode errors.

Open the finished recap through the local Core media endpoint:

```text
http://127.0.0.1:8787/api/productions/prd_20261106_they_mocked_his_kung_fu_until_he_kod_the_141/units/unit-001/summary-video/media
```

The completed recap was **not promoted or published**. Export remains a separate
deliberate action.

## Long-form voiceover repair

The first two voiceover attempts exposed an independent issue: the 90-second
per-chunk timeout cancelled valid Edge speech. A live 281-word chapter completed
in about 113 seconds. Chunk timeout is now 240 seconds; synthesis is bounded at
1,740 seconds, below K3's 1,800-second request/proxy deadline. Validated chunks
remain resumable, and the successful narration is never rewritten to retry TTS.

## Resume and duplicate-command protection

- Production detail includes registered source metadata without reading media
  or transcript contents.
- `GET /api/productions/{production}/units/{unit}/summary-progress` reports
  active work and chapter-checkpoint counts, never story text.
- Narration, voiceover and assembly have a shared per-unit single-flight lock.
  A refresh/second tab gets HTTP 409 rather than another writing command.
- K3 polls only active work, verifies source/unit/style/length/model before
  reusing saved narration, and requires read-only inspection after ambiguous
  transport failures.

## Reusable verification tools

```text
python tools/prepare_summary_resume.py --production-id <origin> --asset-id <source> --title <movie-title>
python tools/verify_summary_resume_live.py --production-id <dedicated> --asset-id <source> --mode pro --model-id gpt-6.1-sol --full --report <new-json-path>
python tools/verify_summary_resume_live.py --production-id <dedicated> --asset-id <source> --mode pro --model-id gpt-6.1-sol --resume --full --report <new-json-path>
python tools/verify_summary_studio_browser.py --production-id <dedicated> --report-dir <local-report-directory>
```

Live tools require deliberate execution. Reports contain safe counts/identities,
not provider payloads. Browser screenshots are written without reading binary
media as text. `--sandbox` adds a separate isolated synthetic autopilot check.

Final hermetic verification covered the requested API/chapter suites plus
budget, source-preservation, long-form synthesis, Standard narration, visual
cache and web readiness regressions: **409 passing tests, zero external network
attempts**. Headless desktop/mobile checks and one isolated sandbox autopilot
completed successfully. The finished recap also played in headless Chrome.