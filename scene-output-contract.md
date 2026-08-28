# Unified Scene Output Contract

## Purpose

This document defines the common downstream contract for a *completed scene*
in AUTO VIDEO MAKER. Every scene, regardless of how it was produced, becomes
interchangeable downstream when it resolves to a valid scene MP4 recorded
through the established manifest/checkpoint path.

This is a description of the architecture that already exists (Phases 6A–6F).
It introduces no new abstraction and no speculative future provider.

## Rendering Boundary

A `VisualProvider` implementation turns one `SceneGenerationRequest` into one
validated scene MP4. Implementations may differ internally — for example:

- `ImageMotionVisualProvider` renders a still image into a deterministic
  Ken Burns / pan MP4 (FFmpeg zoompan);
- `LocalAssetVisualProvider` normalizes a user-supplied image or video into a
  valid scene MP4 (FFmpeg scale/crop/fps);
- `GoogleFlowVisualProvider` obtains a generated clip via Google Flow.

These differences are confined to the provider. All providers converge on the
same output obligation: a `SceneGenerationResult` whose `file_path` points to
a valid MP4 satisfying the shared video validation contract.

## Required Output

A completed scene MP4 must:

- exist and be non-empty;
- be a real MP4 video (ffprobe finds a video stream);
- match the configured aspect ratio exactly (e.g. 9:16 → `width*16 == height*9`);
- have a positive duration and dimensions.

`validate_video_file(file_path, expected_aspect_ratio)` (in
`app/flow/scene_generator.py`) is the **authoritative validator**. Providers
run it on their own output, and the shared scene loop runs it again before a
scene is accepted.

The expected output location is:

    project_dir/scenes/<project_id>/<scene_id>.mp4

## Manifest and Checkpoint Integration

Completion is recorded through the established path:

- `scene_manifest.json` gains a `SceneManifestEntry` for the scene
  (`file`, `status`, `validation`, `checkpoint_evidence`);
- `manifest.scene_checkpoints[<scene_id>]` records the provider, output
  `file_path`, validation result, cost, and timestamp;
- `manifest.scene_video_paths` accumulates the ordered list of validated
  scene MP4 paths — this is the handoff to assembly.

## Provider Independence

Assembly consumes video paths, never provider identities. The assembly stage
builds its input directly from `manifest.scene_video_paths`:

```python
AssemblyScene(
    id=<scene_id>,
    video=<scene_mp4_path>,
)
```

There is no provider-specific branching in assembly. Downstream code cannot
tell — and must not need to tell — whether a scene MP4 came from ImageMotion
rendering, local asset normalization, Google Flow, or any future provider
that satisfies this contract.

## Resume

On resume, a scene whose manifest entry is completed/validated **and** whose
output file still exists **and** still passes `validate_video_file()` is
reused as-is; its provider is not invoked again. A scene whose output is
missing or fails validation does not qualify as reusable and is regenerated
through its routed provider. The project-owned asset copy (Phase 6C) remains
the authoritative source for any regeneration.
