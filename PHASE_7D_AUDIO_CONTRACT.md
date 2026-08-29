# Phase 7D Audio Contract

## Boundary

Visual providers produce scene media. They may return a scene MP4 with an
optional audio stream, but they do not decide whether that stream competes with
AVM narration. Assembly inspects the media, records facts, resolves the final
audio policy, and owns the FFmpeg mapping.

Audio facts describe the first audio stream that FFprobe can observe:

- `has_audio`
- `audio_codec`
- `audio_duration`
- `sample_rate`
- `channels`

Facts are not semantic classification. AVM does not infer dialogue, ambience,
SFX, or audio quality. AVM-created silence may be marked synthetic so required
topology silence is not treated as meaningful native scene audio.

Audio remains optional for a valid scene. `validate_video_file()` continues to
require a valid video stream, positive duration, and valid dimensions/aspect;
it does not require audio.

## Supported Policies

The assembly policy is explicit and is persisted in the project manifest's
`assembly_config.audio_policy` snapshot:

- `narration_only` — use AVM narration when present; suppress native scene
  audio. If narration is absent, native scene audio may be used.
- `native_only` — use native scene audio when present and suppress narration.
  If native audio is unavailable, deterministically fall back to narration.
- `native_plus_narration` — mix native scene audio and AVM narration when both
  exist.

Background music remains the existing additive assembly capability. All
selected inputs use explicit volume and duration handling; advanced ducking,
sidechain compression, and mastering are deferred.

The default is `narration_only`. A manifest created before Phase 7D has no
policy field and therefore keeps the historical behavior. Resume uses the
persisted policy rather than silently applying a later default.

## Output Contract

Scene normalization explicitly maps one video stream and one AAC stereo stream.
Scenes without source audio receive deterministic topology silence for concat;
that silence is not automatically considered native content. Final assembly
explicitly maps the selected video and filtered audio output. A standard output
with selected audio contains exactly one AAC audio stream, never multiple
uncontrolled audio streams. Native and mixed audio are trimmed to the final
video duration; narration is padded where the existing behavior requires it.

## Deliberately Deferred

This phase does not implement semantic audio detection, dialogue/narration
intelligence, ducking, sidechain processing, mastering, new video providers,
OpenRouter TTS, a timeline editor, or Phase 8 planning/execution architecture.