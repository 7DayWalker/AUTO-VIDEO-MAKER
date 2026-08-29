# PHASE 8 — PLANNING / EXECUTION ARCHITECTURE

Track: VISUAL PRODUCTION / ORCHESTRATION (not the publishing track, which uses "Phase 8A").

## 1. Status

Specification / Design Gate — FROZEN.

This document is the authoritative contract for Phase 8. Implementation sessions must
conform to it and must not silently widen scope. Deviations require an explicit,
documented amendment to this file.

This task (the creation of this document) performed zero implementation: no code was
refactored, no tests changed, no live services contacted.

## 2. Baseline

- HEAD: `8403574` ("Phase 7E pre-live hardening: honor visual-default-provider, …")
- Working tree at specification time: CLEAN
- Safe test suite: `python -m pytest -q tests` → 554 passed, 2 warnings
- Phase 7A–7E visual architecture: implemented and protected
- Phase 7D audio contract: complete (`PHASE_7D_AUDIO_CONTRACT.md`)
- Phase 7E OpenRouter gateway: complete (`PHASE_7E_OPENROUTER_VIDEO.md`)
- Phase 7E live acceptance: blocked by current OpenRouter service state
  (27 video models discovered, 0 free models). No paid generation may be introduced
  merely to complete that acceptance. This is a Phase 7E acceptance concern, NOT a
  Phase 8 work item.

## 3. Problem Statement

`app/orchestrator/pipeline.py` (≈106 KB) contains provider-specific scene execution
paths — local execution, OpenRouter execution, and hybrid execution (which itself
contains Flow execution). These paths duplicate substantial shared responsibilities:

- provider invocation
- output validation
- checkpoint creation / updates
- scene path persistence (`scene_video_paths`)
- failure persistence
- provider provenance handling
- resume state transitions

Each new visual provider or provider behavior change increases coupling between the
central orchestration pipeline and individual provider mechanics. Existing monoliths:

| Module | Size (approx.) |
|---|---|
| `app/orchestrator/pipeline.py` | 106 KB |
| `app/flow/scene_generator.py` | 110 KB |
| `app/orchestrator/openrouter_video.py` | 50 KB |
| `app/orchestrator/visual_providers.py` | 48 KB |

Left unaddressed, ordinary scene execution requires a growing provider-specific
branch in the central pipeline, and shared responsibilities drift apart between
branches.

## 4. Objective

Create a provider-agnostic scene execution boundary that separates:

1. orchestration and state management, from
2. provider-specific scene generation/execution.

The architecture must allow future visual providers to be added without continually
adding provider-specific execution branches to the central production pipeline.

This is an **extraction/refactoring phase, NOT a rewrite**. Existing behavior must be
preserved. The Phase 7A–7E contracts are inputs, not targets for redesign.

## 5. Architectural Principles (non-negotiable)

1. **Phase 7A output contract.** All visual providers ultimately produce the existing
   validated scene MP4 contract. Assembly remains provider-agnostic and receives
   scene paths, never provider implementations. The scene output contract is not
   redesigned.
2. **Phase 7D audio contract.** Audio policy remains owned by assembly. The three
   policies remain authoritative: `narration_only`, `native_only`,
   `native_plus_narration`. Providers may report `AudioMediaFacts`; they must NOT mix
   narration and must NOT decide final project audio policy. Final output contains
   exactly one deterministic AAC audio stream.
3. **Phase 7E OpenRouter contract preserved as-is:** dynamic model discovery;
   live pricing-derived free/paid classification; free-first selection; paid disabled
   by default; explicit paid authorization; no automatic free→paid fallback;
   capability preflight; async job lifecycle; checkpoint-before-polling; resume
   without duplicate submission; terminal expired-job handling; atomic `.part`
   artifact handling; truthful provenance; secret safety. Phase 8 must NOT redesign
   OpenRouter.
4. **Google Flow isolation.** Flow remains a distinct provider. OpenRouter failures
   are never routed into Flow; Flow is never routed through OpenRouter; Flow is never
   the default provider. Explicit per-scene routing and the credit firewall remain.
5. **ImageMotion preservation.** `image_motion` remains the default zero-cost
   provider when no explicit alternative is selected. OpenRouter must not become the
   default. The zero-cost path must not be removed or weakened.
6. **Resume authority.** Persisted project configuration remains authoritative during
   resume. New Phase 8 state must not silently override persisted configuration.
7. **No automatic billable fallback.** No Phase 8 feature may introduce
   free→paid, providerA→providerB, or failed-generation→automatic-alternate
   generation behavior. Any such behavior belongs to an explicitly defined future
   phase.
8. **No live generation during specification or implementation test runs.** Zero
   OpenRouter, Gemini, or Google Flow video generations; zero paid generations.

## 6. Existing Architecture (as observed at baseline)

- Providers (all under `app/orchestrator/visual_providers.py` unless noted):
  `image_motion`, `local_asset`, `external_video`, `gemini_omni`,
  `openrouter_video` (`app/orchestrator/openrouter_video.py`),
  `google_flow` (`app/flow/scene_generator.py`).
  `ZERO_COST_PROVIDERS = {local_asset, image_motion, external_video}`;
  `DEFAULT_HYBRID_PROVIDER = image_motion`.
- Provider protocol (already duck-typed across modules):
  `generate_scene(request, *, dry_run, require_confirmation, checkpoint_callback)`
  and `resume_scene(request, checkpoint, *, checkpoint_callback)`, returning
  `SceneGenerationResult` (`app/flow/scene_models.py`). Providers emit checkpoint
  updates through `checkpoint_callback`.
- Pipeline entry: `Pipeline._execute_scenes_stage`, with provider-specific paths
  `_execute_scenes_stage_local`, `_execute_scenes_stage_openrouter`, and
  `_execute_scenes_stage_hybrid` (Flow executes inside hybrid via its own
  `generate_scene`/`resume_scene`).
- Routing: `build_hybrid_routing` / `resolve_provider` in `visual_providers.py`.
  Resolution order: explicit persisted `scene_config.scene_provider_routing[scene_id]`
  → configured `visual_default_provider` (persisted per project / CLI) →
  `image_motion` default. `visual_default_provider` can never be `google_flow`.
- Checkpoints: per-scene checkpoint dictionaries persisted in the project manifest
  (`scene_checkpoints`); routing persisted as `scene_config.scene_provider_routing`
  before generation. Resume reads persisted state; OpenRouter resume polls the
  existing remote job and never re-POSTs; Flow resume uses its own submitted-job
  mechanism; completed valid local artifacts are reused without provider invocation.
- Validation: `validate_video_file` (FFprobe-based, aspect-ratio aware) gates what
  counts as a produced scene.
- Assembly (`app/assembly/`) consumes validated scene paths and owns audio policy.

## 7. Target Architecture (behavioral, not class-prescriptive)

The exact implementation mechanism (module names, whether a small protocol class, a
plain function dispatch table, or the existing duck-typed provider protocol plus a
shared executor function) must be determined during implementation after inspecting
the current code. This specification defines required behavior only:

1. **Uniform scene execution boundary.** Orchestration requests "execute scene
   generation" through one provider-agnostic boundary without knowing whether the
   provider is `image_motion`, `local_asset`, `external_video`, `gemini_omni`,
   `openrouter_video`, or `google_flow`. Provider-specific transport and generation
   logic stays inside provider boundaries. No speculative class hierarchies.
2. **Shared responsibilities centralized once:** scene execution result handling,
   output validation, checkpoint updates, scene path persistence, failure
   persistence, provider provenance handling, and resume state transitions are
   implemented in exactly one place consumed by all providers — not duplicated
   across provider branches.
3. **Provider adapters keep their own:** API/browser/local generation mechanism,
   provider-specific request construction, polling, retrieval, media facts, and
   provenance details. They return the existing scene-generation contract
   (`SceneGenerationResult` + checkpoint callbacks).
4. **Routing stays an orchestration concern** and keeps the existing precedence
   (Section 11). Global routing decisions never move into providers.
5. **Resume stays provider-specific inside the provider**, invoked through the same
   uniform boundary. The central pipeline must not need `if provider == X: resume
   differently` branches for ordinary execution.
6. **Extraction is incremental:** each step is small, behavior-preserving, covered
   by tests before moving on. No wholesale rewrite of `pipeline.py`, no unnecessary
   rewriting of provider modules.

## 8. Scene Execution Contract

The uniform boundary accepts a scene request (existing `SceneGenerationRequest`
shape) plus routing information and returns the existing `SceneGenerationResult`
plus persisted artifacts. Regardless of provider:

- On success: a validated scene MP4 exists on disk at a persisted path; the manifest
  records the path and provider provenance; checkpoints reflect completion.
- On validation failure: the artifact is NOT promoted to a scene path; the failure is
  persisted truthfully with the provider that produced it.
- On provider failure: the failure is persisted truthfully with enough evidence
  (provider, scene, reason, provider-specific identifiers such as remote job id) to
  resume or deliberately retry.
- The boundary is synchronous from orchestration's perspective; async providers
  (OpenRouter, Flow) keep their async lifecycle inside the provider.
- Audio facts: providers may report `AudioMediaFacts`; final audio policy remains
  assembly-owned (Section 13).

## 9. Provider Boundary

Providers remain responsible for:

- their own generation mechanism (local render, browser automation, HTTP API);
- provider-specific request construction;
- provider-specific polling / job lifecycle;
- provider-specific retrieval and artifact handling (e.g., OpenRouter atomic
  `.part` handling; Flow snapshot verification);
- provider-specific media facts and provenance details;
- provider-specific resume behavior (Section 12).

Providers must NOT: make routing decisions, change audio policy, fall back to other
providers, mix narration, or emit secrets.

## 10. Orchestration Boundary

Orchestration remains responsible for:

- stage sequencing and stage failure handling;
- routing resolution and persistence (Section 11);
- invoking the uniform scene execution boundary;
- receiving results and persisting outcomes (paths, failures, provenance,
  checkpoints) exactly once, in one shared code path;
- resume state transitions between stages.

After Phase 8, ordinary scene execution must not require a growing
provider-specific branch in the central pipeline. Dispatching to a provider by
name (a lookup, not a behavioral branch) is acceptable; duplicated
validate/checkpoint/persist logic per provider is not.

## 11. Routing Contract

Preserved exactly:

```
explicit scene route (scene_config.scene_provider_routing[scene_id])
  > persisted routing / configured visual_default_provider
    > visual default (image_motion when unset)
      > image_motion fallback default
```

- `google_flow` can never be a default; it requires explicit per-scene routing.
- OpenRouter is never implicitly the default.
- Routing is resolved and persisted before generation; persisted configuration
  remains authoritative during resume.
- No routing logic moves into providers.

## 12. Checkpoint / Resume Contract

- The current manifest/checkpoint contract is preserved. Existing fields are not
  removed merely because execution is refactored.
- Any new Phase 8 state must be explicit, serializable (manifest JSON), safe, and
  resume-aware. It must not silently override persisted configuration.
- Provider-specific resume behavior is preserved:
  - OpenRouter remote job: resume by polling the existing job; **never POST again**;
  - Flow submitted scene: use the existing Flow resume mechanism;
  - completed valid local artifact: reuse without provider invocation.
- Resume paths are reached through the uniform boundary; provider-specific branches
  for resume must not be reintroduced into the central pipeline.
- Provider identity recorded at generation time is preserved through resume.

## 13. Audio Contract

- Audio policy remains assembly-owned; the three policies `narration_only`,
  `native_only`, `native_plus_narration` remain authoritative.
- Providers may report `AudioMediaFacts`; providers must not mix narration or decide
  final project audio policy.
- Final output contains exactly one deterministic AAC audio stream.
- No Phase 8 change may alter the Phase 7D contract. Existing audio contract tests
  must remain green.

## 14. Assembly Contract

- Assembly is unchanged in architectural responsibility. It consumes validated scene
  paths and owns audio policy.
- No provider implementation, provider module, or provider object enters assembly.
- `app/assembly/` code is not a Phase 8 edit target except where a strictly additive
  type/import change is forced by extraction, with tests.

## 15. Failure / No-Fallback Contract

- Provider failure remains truthful and attributable to the failing provider.
- A failed provider must NOT silently become another provider. No automatic
  free→paid, provider→provider, or failed→alternate-generation behavior exists in
  Phase 8.
- Failure persistence must contain sufficient evidence to resume or deliberately
  retry (provider identity, scene id, reason, provider-specific identifiers).
- Existing zero-cost failure guarantees (e.g., missing-asset failures never construct
  or fall back to Flow) remain intact.

## 16. OpenRouter Safety Contract

Phase 8 must not weaken or redesign Phase 7E. Specifically preserved: dynamic model
discovery; live pricing-derived free/paid classification; free-first selection; paid
disabled by default; explicit paid authorization; no automatic free→paid fallback;
capability preflight; async job lifecycle; checkpoint-before-polling; resume without
duplicate submission; terminal expired-job handling; atomic `.part` artifact
handling; truthful provenance; secret safety. Paid generation remains disabled by
default after Phase 8.

## 17. Google Flow Isolation

- Flow remains a distinct provider with explicit routing and its credit firewall.
- OpenRouter failures are never routed into Flow; Flow is never routed through
  OpenRouter; Flow is never the default provider.
- Flow's internal mechanism (including `app/flow/scene_generator.py`) is NOT
  redesigned in Phase 8. Flow participates in the uniform boundary via its existing
  `generate_scene`/`resume_scene` protocol; where practical, tests exercise Flow
  through a fake at the boundary rather than the real generator.

## 18. Non-Goals

Phase 8 explicitly excludes:

- new visual providers; new video models
- OpenRouter model UI; automatic model ranking
- automatic paid fallback; cost ceiling implementation; billing/account management
- Gemini redesign; Google Flow redesign; ImageMotion redesign
- audio policy redesign; TTS provider redesign
- TikTok publishing changes; YouTube publishing changes; web dashboard redesign
- database introduction; new framework introduction
- replacing the existing pipeline wholesale; rewriting provider modules
  unnecessarily; speculative abstractions for future features
- completing Phase 7E live acceptance via paid generation

## 19. Test Strategy

- **Regression suite (authoritative):** `python -m pytest -q tests` — all 554+
  existing tests remain green. Bare repository-root `pytest` must NOT be used
  (root-level live scripts such as `gemini_test.py`, `flow_test.py` may be
  collected).
- **New execution-boundary unit tests** verify: successful provider execution;
  provider failure; validation failure; checkpoint persistence; provider
  provenance; scene path persistence.
- **Provider parity tests** verify representative execution for at minimum:
  `image_motion`, `local_asset`, `openrouter_video` via fake provider, and Flow via
  a fake provider boundary where practical. No live services are invoked.
- **Routing tests** verify: explicit route wins; persisted route wins over default;
  configured visual default works; ImageMotion remains fallback default; Flow cannot
  become an unintended default.
- **Resume tests** verify: completed local scene reused; remote OpenRouter job
  resumed without POST; failed scene remains failed; provider identity preserved;
  checkpoint structure survives the refactor.
- **Audio regression:** existing audio contract tests remain green; not rewritten
  unless implementation requires additive coverage.
- **End-to-end mock run:** a mock production run still reaches `PROJECT_COMPLETE`
  where currently expected.

## 20. Acceptance Criteria

Phase 8 is PASS only if ALL of the following are true:

1. Existing 554+ safe tests remain green (`python -m pytest -q tests`).
2. No existing Phase 7 contract is weakened.
3. Provider-specific execution logic is reduced or cleanly isolated rather than
   duplicated.
4. Central orchestration no longer needs a growing provider-specific execution
   branch for ordinary scene execution.
5. Existing providers continue producing the same scene output contract.
6. Existing routing precedence remains unchanged.
7. Existing checkpoint structure remains compatible.
8. Resume behavior remains correct.
9. OpenRouter remote jobs are never resubmitted during resume.
10. Paid generation remains disabled by default.
11. No automatic free→paid or provider→provider fallback is introduced.
12. Google Flow remains isolated.
13. Audio policy remains assembly-owned.
14. Assembly remains provider-agnostic.
15. Mock/local end-to-end production remains functional.
16. No secrets enter manifests, checkpoints, logs, or source.
17. No new runtime dependency is introduced without explicit architectural
    justification.
18. `git diff --check` passes.
19. Repository working tree is clean at completion.

## 21. Implementation Guardrails

A future implementation session must:

1. inspect existing code first;
2. identify the smallest clean extraction boundary;
3. implement incrementally, one behavior-preserving step at a time;
4. run focused tests after each step;
5. run the full safe suite (`python -m pytest -q tests`) before committing;
6. inspect the diff;
7. commit only after tests pass.

Additional guardrails:

- No speculative framework, dependency, or class hierarchy.
- Do not run bare repository-root `pytest`.
- Known Pydantic deprecation warnings are pre-existing and out of scope.
- If a factual README correction directly supports Phase 8 clarity, it may be
  included as a small documentation task — not a major workstream. (Current README
  contains stale statements about production features.)

## 22. Deferred Items

- Phase 7E live acceptance completion (blocked by OpenRouter service state: 27 video
  models, 0 free; must not be resolved by introducing paid generation).
- Any cost-ceiling / budget / billing management.
- Any cross-provider retry or fallback policy (must be an explicit future phase).
- Any publishing-track work ("Phase 8A" terminology belongs to the publishing track
  and must not be used for this phase).
- Pydantic deprecation warning cleanup.
- Any new visual provider onboarding (should become trivial after Phase 8, but is
  not part of Phase 8).

## 23. Phase Completion Definition

Phase 8 is COMPLETE when:

1. All acceptance criteria in Section 20 are verified against actual test runs and
   the final diff.
2. The uniform scene execution boundary exists and all six providers
   (`image_motion`, `local_asset`, `external_video`, `gemini_omni`,
   `openrouter_video`, `google_flow`) execute through it.
3. Shared execution responsibilities exist in exactly one shared code path.
4. `python -m pytest -q tests` is green (554+ tests, plus new Phase 8 tests).
5. The working tree is clean and all Phase 8 changes are committed.
6. This document's status section is updated only if behavior deviated (with an
   explicit amendment note); otherwise it stands as frozen.
