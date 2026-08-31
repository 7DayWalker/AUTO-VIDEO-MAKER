# PHASE 9 — DURABLE PRODUCTION PLAN

Track: PERSONAL AVM MATURITY (visual/production roadmap; not the publishing track,
whose "Phase 8A" is a separate, already-completed milestone).

FINAL SPECIFICATION — all CTO architectural decisions recorded as finalized
(see Amendment / Decision Record, Section 35).

## 1. Status

Specification / Design Gate — FROZEN PENDING FORMAL ACCEPTANCE.

This document is the authoritative Phase 9 architectural contract. Writing or freezing
this document does **NOT** authorize implementation. Phase 9 implementation may begin
only after: (1) this specification is reviewed; (2) it is explicitly accepted by the
repository owner; (3) a separate implementation task is issued. Until then: no
production code, no tests, no scaffolding, no dependency changes, no configuration
changes, no database, no UI.

This document supersedes all earlier pre-freeze Phase 9 proposals (Section 35).

## 2. Baseline

- HEAD: `6a6f6fe` ("docs: establish AVM master roadmap")
- Phase 8: FORMALLY ACCEPTED (ancestry `3f70255` → `c10132d` → `fd51a4c`)
- Working tree: CLEAN
- Test baseline: `python -m pytest -q tests` → 585 passed, 2 warnings
- Authoritative roadmap: `AVM_MASTER_ROADMAP.md`
  (Phase 9 = Durable Production Plan §4.6; Phase 10 = Plan-Backed Execution §4.7)

## 3. Objective

Establish a durable, versioned, inspectable, secret-free, provider-neutral production
intent artifact — the **Production** — representing what AVM intends to produce, from a
single standalone video to a large multi-unit creative workload, as plain data.

    PHASE 9:  "What do we intend to produce?"
    PHASE 10: "How do we execute that intent?"

Phase 9 does NOT change what AVM executes.

## 4. Problem Statement

Repository evidence (Phase 9 discovery, verified at HEAD `6a6f6fe`):

- AVM produces **one video per project**; `ProductionPlan`
  (app/content/models.py:176–239) is a single-video artifact (`number_of_scenes`
  bounded 2–20, content/models.py:52–57; `target_duration` 10–180 s, models.py:325).
- There is **no durable representation of a production workload, unit identity, the
  originating production command, or shared creative context**. Nothing can durably
  represent "Create a 60-episode animated series about X" or "Create 10 TikTok videos
  about forex psychology."
- Production intent is **fragmented across three artifacts** with no authority model:
  `manifest.topic` (models.py:163), `manifest.content_config` (pipeline.py:221 —
  written, never read), `content/<pid>/production_plan.json` (planner.py:89–93).
- `content_brief.json` is persisted (planner.py:96–100) with a loader
  (planner.py:128–148) that has **zero callers**; the content stage rebuilds the brief
  from CLI config each run (pipeline.py:357).
- Execution state (`ProjectManifest.scene_checkpoints`, `stages`, publication records —
  models.py:154–204) mixes plan-derived identity (scene_id/sequence) with execution
  evidence (status, job_id, provider, cost) in the same dictionaries (completion shape:
  scene_execution.py:150–188).
- Downstream stages bind to the per-video plan by path convention (`content_plan_path`;
  reloaded at pipeline.py:459–462, 660, 2086, 2182).

## 5. Architectural Principles

1. **ONE AVM CORE.** The plan artifact is personal/public-neutral data — never a
   personal-only design a future SaaS control plane must rebuild.
2. **INTENT ≠ EXECUTION.** The durable plan describes what should exist; execution
   state describes what happened. **Execution evidence must remain outside the durable
   intent layer.**
3. **AVM plans productions, not merely episodes.** A Production may contain one
   Production Unit or many. **Episode is optional metadata on a unit — never the
   architectural primitive.**
4. **Shared creative continuity belongs to the optional Production Bible**; unit-
   specific creative intent belongs to the Production Unit and its detailed plan.
5. **Additive, not invasive.** Phase 9 layers over the proven Phase 1–8 engine.
   Existing loaders (pipeline.py:459–462, 660, 2086, 2182), manifests, checkpoints,
   routing, and provider behavior remain unchanged.
6. **Provider neutrality.** Provider intent is neutral names only; no transport,
   client, session, or runtime state ever enters the plan.
7. **Google Flow remains first-class:** representable as routing intent exactly like
   every other provider; no demotion, redesign, defaulting, or automatic fallback.
8. **No execution in Phase 9.** The artifact may describe intended execution
   requirements; it must never trigger them.
9. **Durable human-readable JSON.** No database, no framework, no new runtime
   dependency (Phase 8 §18 non-goals; roadmap Principle 11).
10. **Filesystem-independent identity.** Identities are stable identifiers; directory
    names derive from them; no absolute/local path is part of any identity.
11. **Secret-free.** No credentials, tokens, or `.env`-derived values in any plan
    artifact.
12. **Phase discipline.** Specification only; one phase at a time.

## 6. Target Architecture

```
                    AVM
                     │
                     ▼
                 PRODUCTION
                     │
          ┌──────────┼──────────┐
          │          │          │
       COMMAND     BIBLE      UNITS
          │          │          ▼
          │          │   Per-unit ProductionPlan
          │          │          │
          └──────────┴──────────┘
                     │
                     ▼
              EXISTING AVM CORE
                     │
                     ▼
               PHASE 10+
                     │
          ┌──────────┴──────────┐
          │                     │
   PERSONAL CONTROL       FUTURE PUBLIC
       PLANE               CONTROL PLANE
          │                     │
          └──────────┬──────────┘
                     ▼
               SAME AVM CORE
```

Two strictly separated layers:

```
INTENT LAYER (defined by Phase 9)

    Production Command (why)  →  Production  →  optional Production Bible
                              →  Production Units (neutral primitive)
                                     └── per-unit ProductionPlan references

EXECUTION LAYER (existing Phase 1–8 system; unchanged by Phase 9)

    ProjectManifest · StageResult · scene_checkpoints · SceneManifest
    · generated artifacts · publication records · reconciliation state
```

## 7. Domain Model (conceptual entities; no code in Phase 9)

- **Production Command** — the durable, historical instruction that originated the
  Production (Section 8).
- **Production** — the durable workload-level intent artifact (Section 9).
- **Production Bible** — OPTIONAL production-level shared creative context
  (Section 10).
- **Production Unit** — the neutral primitive: intent for ONE video output
  (Section 11).
- **Episodic metadata** — OPTIONAL semantic metadata on a unit (Section 12).
- **Plan Reference** — a unit's link to its per-unit detailed plan (Section 13).
- **Plan Version / Schema Version** — intent-revision history vs structure
  compatibility (Sections 20, 29).

## 8. Production Command

The durable historical representation of the instruction that originated the
Production. Captured **once** at production creation; **never silently rewritten**.

May contain: original command/request text; requested workload shape; intended unit
count; production intent; explicit constraints; explicitly requested platform/output;
explicitly requested duration; explicitly requested provider preference; creation
context; creation timestamp; source/origin identity where appropriate.

**CTO DECISION (A9) — MINIMAL COMMAND CAPTURE:** preserve the original command plus
structured fields that were explicitly supplied. Do NOT invent a sophisticated command
language or parser. The Command is provenance — NOT a command parser, natural-language
execution grammar, scheduler, automation rule, execution instruction, retry policy, or
fallback policy. Phase 12 owns automation and recurring command execution.

## 9. Production

The durable workload-level intent artifact. It owns:

- stable `production_id`
- production name/title; production-level creative intent
- command provenance
- intended workload scope; intended unit count
- optional Production Bible
- Production Units
- current `plan_version`
- deterministic ordering semantics
- plan metadata; creation/revision provenance

It MUST NOT own: execution status; generated files; provider jobs; polling state;
actual costs; execution checkpoints; retry state; fallback state; publication execution
state; provider objects; browser sessions; API clients.

**A Production is intent, not execution.**

**Scope-agnostic by construction** — no separate architecture exists for single video,
batch, series, or episodic production:

| Operator statement | Representation |
|---|---|
| "Create one 30-second TikTok explaining three reasons gold is rising." | Production + Command + 1 Unit (`standalone_video`), no Bible |
| "Create 10 unrelated TikTok videos about forex psychology." | Production + Command + 10 Units (`batch_item`), optional Bible |
| "Create a 60-episode animated mecha series." | Production + Command + Bible + 60 Units (`episode` + episodic metadata) |

All three use the **same schema**.

## 10. Production Bible — OPTIONAL first-class component

An OPTIONAL Production-level creative-context artifact preserving shared creative
context referenceable by multiple Production Units.

- A Production **without** a Bible is completely valid; a simple standalone video does
  not need one. A complex series may use one extensively.
- The Bible is NOT: execution state; provider configuration; a checkpoint; a manifest
  replacement; a database; a UI; an execution engine; continuity enforcement logic.
- Phase 9 only makes the shared context **representable**; continuity enforcement
  belongs to future phases.

**CTO DECISION (A5) — canonical named sections, extensible:** the Bible supports
canonical conceptual sections — `characters`, `cast`, `world`, `locations`,
`visual_language`, `narrative`, `recurring_elements`, `brand_context` — which are
canonical, **not rigid limits**; future sections may be added without redesigning the
Production architecture. The exact internal typed schemas are intentionally NOT fully
designed in Phase 9 (no character intelligence, continuity checking, voice matching,
visual-consistency enforcement, or contradiction detection — future intelligence
capabilities).

Conceptual structure (illustrative):

```
Production Bible
    characters/          kai · general_zarek
    cast/                kai_voice · zarek_voice
    world/               mecha_empire
    locations/           iron_city · royal_palace
    visual_language/     cinematic_anime
    narrative/           canon_rules
    recurring_elements/  red_mecha_crown
    brand_context/       presentation_rules
```

## 11. Production Unit — the neutral primitive

**CRITICAL RULE: the Production Unit is the neutral primitive. NOT the episode.**

A Production Unit represents the intent necessary to produce **ONE video output**.

**CTO DECISION (A6) — open, extensible unit-type vocabulary.** Recommended initial
set: `standalone_video`, `episode`, `chapter`, `campaign_asset`, `batch_item`. Future
types may be added without redesigning the Production architecture.

A unit may contain intent: stable `unit_id`; title; description; premise; content
intent; narrative intent; duration target; target platform/output intent; visual
intent; narration/voice intent; content requirements; optional provider/routing intent;
optional relationships/dependencies; optional episodic metadata; optional Production
Bible references; reference to the unit's detailed ProductionPlan.

A Production Unit contains **NO execution state**. Never place inside a unit: job_id;
polling_url; checkpoint status; provider execution result; actual cost; usage evidence;
validation result; generated artifact path; execution status; retry counter; Flow
browser state; Flow session state; OpenRouter remote-job state; provider SDK objects;
credentials.

## 12. Episodic Metadata

Episode is NOT the architectural primitive; it is optional metadata on a unit:

- season number; episode number; episode title; continuity relationships; ordering
  information.

Both are valid units:

```
type = standalone_video
type = episode, episode_number = 17, season_number = 1
```

The execution architecture does not change. Episode semantics must never force AVM
into an episode-first model.

## 13. Plan References

A unit plan reference conceptually contains:

- relative location/reference from the Production root (never an absolute personal
  filesystem path as identity)
- referenced plan identity
- referenced plan version
- content hash at reference time
- resolution status

**CTO DECISION (A4) — per-unit ProductionPlan generation is LAZY and belongs to Phase
10.** Phase 9 must NOT invoke an LLM provider to generate scene-level plans. A unit may
initially contain an unresolved plan reference (`status = unresolved`). Later, Phase 10
may create the actual ProductionPlan and bind execution to it; Phase 9 must not execute
that process.

Content-hash mismatch is a **DATA VALIDATION** problem, not an execution problem.

The existing `ProductionPlan` remains the detailed per-video scene-level intent
artifact. Do NOT redesign its consumers (voice, scenes, captions, assembly). The
relationship is:

```
Production → Production Unit → existing per-video ProductionPlan
```

The Production Unit does not absorb or duplicate the ProductionPlan. Detailed
scene-level narrative belongs in ProductionPlan; workload-level intent belongs in
Production; shared creative context belongs in the Bible; execution state belongs in
the execution layer.

Bible references: units may cite Bible elements by stable identifiers without copying
content (e.g., `unit_017.bible_refs: characters/kai, locations/iron_city,
recurring_elements/red_mecha_crown`). Shared creative context belongs in the Bible;
unit-specific intent belongs in the Unit / per-unit plan; execution state belongs in
the execution layer.

## 14. Intent vs Execution

**Hard invariant.** The Production intent layer (Production, Command, Bible, Units)
must never contain: execution checkpoints; StageResult state; generated file paths;
validation results; actual costs; actual usage; provider execution results; remote job
state; retry state; fallback state; execution progress; publication execution state.

The Bible must also remain intent/context only. Never put runtime observations into the
Bible (prohibited example: "Character render failed in Episode 4.") — that is
execution/evidence state and belongs elsewhere.

The intent layer is a strictly separate artifact family from `ProjectManifest` /
`scene_checkpoints` / `SceneManifest`. Phase 10 must bind execution *against* an
immutable plan version — never *into* it.

## 15. Architectural Decision

**SELECTED: OPTION 2 — PARALLEL PRODUCTION ARTIFACT FAMILY** (Command + optional Bible
+ Production Units layered over the existing per-video `ProductionPlan`).

| Option | Verdict |
|---|---|
| 1. Evolve `ProductionPlan` into the workload model | **Rejected.** Would force Bible context, unit typing, and workload semantics into a model already consumed by voice/scenes/captions/assembly (pipeline.py:459–462, 660, 2086, 2182) — unnecessarily destabilizes the established pipeline. |
| 2. Parallel Production artifact family | **SELECTED** |
| 3. Make `ProjectManifest` the plan | **Rejected.** The manifest is execution-authoritative; merging intent re-creates the conflation Phase 9 removes. |
| 4. Database-backed plan store | **Rejected.** Phase 8 §18 non-goals; roadmap Principle 11. Revisit only at Productization, on evidence. |

Rationale: zero disruption to the proven engine; the primitive fits the model; scope-
agnostic by construction; clean Phase 9/10 seam; best ONE-ACM-CORE trajectory; legacy
coexistence.

## 16. Provider / Routing Intent

Provider intent may be represented using provider names only: `image_motion`,
`local_asset`, `external_video`, `gemini_omni`, `openrouter_video`, `google_flow`
(and unset/`default` = orchestration's existing routing precedence).

Must NOT be embedded: provider SDK objects; API clients; job IDs; polling URLs;
browser sessions; Flow project state; OpenRouter remote-job state; transport
configuration; credentials.

Provider intent is not provider execution. Phase 9 must not perform provider selection
based on evidence (Phase 14 owns provider intelligence/ranking). Phase 9 must not
introduce fallback or retry (Phase 15 owns recovery/fallback).

## 17. Google Flow Preservation

Google Flow remains **first-class**. Phase 9 must preserve the ability to express
`provider intent = google_flow`. Flow remains subject to its existing execution rules:
explicit routing; no default hybrid selection; credit firewall; existing lifecycle;
existing resume semantics; no automatic fallback.

Do not remove, deprecate, replace, or redesign Google Flow. Do not place Flow
browser/session state into the Production artifact.

## 18. Identity

**CTO DECISION (A7) — human-readable collision-safe production IDs:**

- `production_id` format: `prd_YYYYMMDD_<slug>_<###>` — unique, stable forever,
  filesystem-safe, independent of absolute paths and execution state, stable across
  plan revisions. Implementation must allocate the suffix **collision-safely**. Do not
  derive identity dynamically from content at read time.

- `unit_id`: unique within a Production; stable forever; independent of ordering and
  execution; preserved across revisions. **Ordering must NOT be encoded into unit
  identity.**

## 19. Ordering

Every Production Unit has explicit deterministic ordering (an ordinal field).
Enumeration produces a total deterministic order; **no ties**. Episode numbers are
metadata and do not replace ordinal ordering. Reordering a unit changes ordering
metadata; it does NOT change unit identity.

## 20. Versioning

- Production identified by `production_id`.
- Unit identified by `production_id + unit_id`.
- Plan version identified by `production_id + plan_version`; starts at **1**, increments
  monotonically.
- Intent revisions create NEW plan versions; **never mutate a superseded version**;
  historical versions remain immutable.
- Conceptual storage:

```
productions/
    <production_id>/
        production.json
        versions/
            v001/production.json
            v002/production.json
```

The exact implementation layout may be adjusted only if the same durability and
immutability guarantees remain intact. Phase 10 must eventually bind execution to an
exact plan version; execution must never rewrite the intent version it executes.

## 21. Source of Truth

For Phase 9 Productions:

| Concern | Authority |
|---|---|
| Workload scope / production identity / unit identity / ordering | `production.json` |
| Shared creative context | Production Bible |
| Unit-specific scene-level intent | existing per-unit ProductionPlan |
| Original production instruction | Production Command |
| Execution state | ProjectManifest / checkpoints / SceneManifest |

Existing legacy fields — `manifest.topic`, `manifest.content_config`,
`content_brief.json` — must NOT be deleted in Phase 9; they remain recorded legacy
inputs for backward compatibility. **There must not be a fourth independent source of
truth.**

## 22. Storage

**CTO DECISION (A8) — dedicated intent data area.** Create a new durable top-level
directory:

```
productions/        durable intent
projects/           execution artifacts/state (existing; unchanged)
```

Do NOT mix Production intent with project execution artifacts. The Production artifact
must be relocatable; identity must not depend on a personal absolute filesystem path.
Runtime/generated project artifacts remain under the existing project architecture.
Production artifacts are data, not provider/runtime state. If the repository ignores
runtime/generated data, the implementation should extend `.gitignore` treatment
consistently (implementation-task item). Do not introduce a database.

## 23. Backward Compatibility

Existing Phase 1–8 projects must remain loadable and resumable. No migration is
required for existing projects. Existing `manifest.json`, `production_plan.json`,
`content_brief.json`, scene manifests, checkpoints, routing, assembly configuration,
and publication records must continue to work exactly as before. A legacy project may
simply have no Production artifact. **Legacy-project adoption/import is NOT part of
Phase 9** (may be considered later based on evidence).

## 24. Phase 7 / Phase 8 Contract Preservation

Phase 9 builds ABOVE the existing engine. Do not redesign:

- **PHASE 7A:** `validate_video_file` authoritative; validation before
  checkpoint/promotion; scene output contract unchanged; assembly receives scene paths.
- **PHASE 7D:** audio policy assembly-owned (`narration_only`, `native_only`,
  `native_plus_narration`); providers report facts only; final AAC contract unchanged.
- **PHASE 7E:** dynamic discovery; free-first; paid disabled by default; explicit paid
  authorization; no automatic free→paid fallback; capability preflight;
  checkpoint-before-polling; resume without duplicate POST; terminal handling; atomic
  `.part`; truthful provenance; secret safety.
- **PHASE 8:** provider-agnostic execution boundary; provider-owned mechanics;
  orchestration-owned routing; persisted routing authority; no automatic provider
  substitution; Flow isolation; assembly independence; checkpoint semantics; secret
  safety.

Phase 8's explicit deferral remains intact: **cross-provider retry/fallback → Phase 15**
(PHASE_8_PLANNING_EXECUTION_ARCHITECTURE.md §22). Phase 9 must introduce NONE of these
behaviors.

## 25. Personal / Public Compatibility

The Phase 9 artifact must be suitable for future consumption by the personal control
plane, a future web/SaaS control plane, a future mobile control plane, and a future
API/developer control plane — but Phase 9 must NOT implement any of those surfaces.

The artifact must therefore be: plain JSON; self-contained; versioned;
provider-neutral; secret-free; path-independent in identity; process-independent;
inspectable; portable; capable of representing one unit or many units. This is the data
contract that helps ensure ONE AVM CORE. Do NOT design multi-tenancy, authentication,
quotas, billing, APIs, cloud execution, or SaaS infrastructure in Phase 9.

## 26. Phase 9 / Phase 10 Boundary

**PHASE 9 OWNS:** durable Production intent; Production identity; Command provenance;
workload representation; Production Units; optional Production Bible; ordering;
relationships as data; plan references; plan versioning; serialization; persistence;
validation; inspection; reconstruction after process termination; immutability
discipline.

**PHASE 10 OWNS:** making the Production plan authoritative for execution; determining
what remains; binding units to execution; creating/using per-unit ProductionPlans;
execution scheduling; connecting units to projects; checkpoint linkage; production-
level resume; execution progress; completion against plan units.

Phase 9 must implement NONE of the Phase 10 responsibilities.

## 27. Future-Phase Boundaries

- **Phase 11:** command center; operator UI; plan visualization; workload inspection.
- **Phase 12:** scheduling; recurring production; unattended automation; command-driven
  autonomous continuation.
- **Phase 13:** QC; metrics; quality gates; production health; scale behavior.
- **Phase 14:** provider intelligence; evidence-driven selection; provider/model
  ranking.
- **Phase 15:** recovery; retry; fallback; cross-provider recovery policy.
- **Phase 16+:** full personal AVM autonomy.
- **Productization:** Public AVM SaaS; web app; mobile app; API; multi-tenancy;
  authentication; quotas; billing; cloud infrastructure.

Phase 9 must not implement any of these.

## 28. Security

Production artifacts must never contain: API keys; access tokens; passwords; bearer
tokens; cookies; provider credentials; `.env`-derived secrets; provider SDK secrets.

The Production Command may record the **fact** that a constraint or authorization
exists as declared intent — never the authorization material. Validation should reject
secret-shaped fields where practical. The Production Bible must never be used to
smuggle credentials or connection data.

## 29. Schema Evolution

Use an explicit `schema_version` from day one. Schema evolution must be controlled:
additive changes increment the appropriate version; unknown major schema versions fail
loudly rather than being silently interpreted.

`schema_version` (structure compatibility) and `plan_version` (intent revision
history) are **separate concepts** — do not confuse them.

## 30. Test Strategy

When Phase 9 is eventually implemented, tests must be **hermetic** (no real provider
generation). Required categories:

1. Production serialization/deserialization round-trip.
2. Command persistence.
3. Single-video production with one `standalone_video` unit and NO Bible.
4. Multi-unit batch production.
5. 60-unit episodic production.
6. Optional Bible.
7. Bible canonical sections.
8. Bible element references.
9. Stable production identity.
10. Stable unit identity.
11. Deterministic ordering.
12. Reordering without identity change.
13. Plan version creation.
14. Immutable historical versions.
15. Schema version handling.
16. Intent/execution separation.
17. Checkpoint exclusion.
18. Provider runtime exclusion.
19. OpenRouter job-state exclusion.
20. Flow session-state exclusion.
21. Secret exclusion.
22. Provider-neutral intent.
23. Google Flow representability.
24. Relative/path-independent references.
25. Plan-reference hash integrity.
26. Process termination/reload.
27. Relocation/path independence.
28. Legacy Phase 1–8 compatibility.
29. Malformed artifact rejection.
30. No execution/provider calls.
31. No database.
32. No live generation.

## 31. Acceptance Criteria

Phase 9 is accepted only if ALL are satisfied:

1. A Production Command is durably representable.
2. A Production can represent one video.
3. A Production can represent many units.
4. Production Unit is the neutral workload primitive.
5. Episode is optional metadata, never the architectural primitive.
6. Standalone video requires no special architecture.
7. Production Bible is optional.
8. Bible supports stable references.
9. Bible remains extensible without speculative giant schemas.
10. 60-unit episodic workload is representable.
11. Unrelated batch workload is representable.
12. Stable production identity exists.
13. Stable unit identity exists.
14. Ordering is deterministic.
15. Reordering does not change identity.
16. `plan_version` is explicit.
17. Historical plan versions are immutable.
18. `schema_version` is explicit.
19. Intent and execution are strictly separated.
20. Checkpoints cannot become plan state.
21. Generated artifacts cannot become plan state.
22. Actual costs cannot become plan state.
23. Provider runtime state cannot become plan state.
24. OpenRouter runtime state cannot become plan state.
25. Flow browser/session state cannot become plan state.
26. Credentials cannot enter the Production artifact.
27. Provider intent uses neutral names.
28. Google Flow remains first-class.
29. Existing ProductionPlan remains compatible.
30. Existing Phase 1–8 projects remain loadable/resumable.
31. Existing execution pipeline behavior is unchanged.
32. No execution binding exists in Phase 9.
33. Per-unit ProductionPlan generation remains a Phase 10 responsibility.
34. No retry/fallback exists.
35. No provider substitution exists.
36. No database is introduced.
37. No UI is introduced.
38. No SaaS infrastructure is introduced.
39. Production identity is independent of personal filesystem paths.
40. Production artifact is JSON-serializable and inspectable.
41. Production artifact survives process termination/reload.
42. Production artifact can be consumed by a future Personal control plane.
43. Production artifact can be consumed by a future Public/SaaS control plane.
44. No second AVM production engine is created.

## 32. Danger Zones

Explicitly prohibited:

- turning episode into the architectural primitive
- requiring a Bible for simple videos
- creating separate single/batch/series architectures
- making ProjectManifest the plan
- creating a fourth intent source
- duplicating ProductionPlan content unnecessarily
- putting execution state into Production (or into the Bible)
- putting provider implementation details, Flow runtime state, or OpenRouter job state
  into Production
- putting secrets into Production
- creating a database; creating UI; creating SaaS infrastructure
- implementing scheduling; automation; retry; fallback; provider ranking; model ranking
- invoking providers; generating per-unit scene plans in Phase 9
- modifying Phase 7A/7D/7E/8 behavior; redesigning Google Flow, OpenRouter, or Gemini
- creating a personal-only identity model
- silently widening scope

## 33. Design Gate / Implementation Authorization

**WRITING OR FREEZING THIS DOCUMENT DOES NOT AUTHORIZE IMPLEMENTATION.**

Phase 9 implementation requires a separate implementation task. Until such
authorization exists: no production code; no tests; no scaffolding; no dependency
changes; no configuration changes; no database; no UI.

When separately authorized, Phase 9 implementation may implement: Pydantic/domain
models for Production intent; serialization/deserialization; persistence; versioning;
immutable snapshots; validation; referential integrity; artifact loading; artifact
inspection at data level; tests for all acceptance criteria; a minimal creation surface
if explicitly included in the implementation task.

It must NOT implement: execution; provider invocation; LLM generation; polling;
scheduling; automation; retries; fallback; publishing; UI; database; SaaS
infrastructure; billing; cost enforcement; provider/model ranking; OpenRouter/Flow/
Gemini redesign; audio redesign; Phase 7/8 behavior changes; legacy migration;
continuity enforcement; intelligent character/cast reasoning.

## 34. References

- `AVM_MASTER_ROADMAP.md` (§4.6, §4.7, §8, §10)
- `PHASE_8_PLANNING_EXECUTION_ARCHITECTURE.md` (incl. §22 deferred items)
- `PHASE_7D_AUDIO_CONTRACT.md`, `PHASE_7E_OPENROUTER_VIDEO.md`,
  `scene-output-contract.md`
- Phase 9 discovery/architectural analysis and Amendments A1–A9 (repository session
  record)
- Source evidence: app/content/models.py, app/content/planner.py,
  app/orchestrator/models.py, app/orchestrator/pipeline.py,
  app/orchestrator/scene_execution.py, app/flow/scene_models.py,
  app/publishing/models.py, app/orchestrator/reconciliation.py, produce.py

## 35. Amendment / Decision Record

This specification **supersedes** all earlier pre-freeze Phase 9 proposals (the
pre-amendment proposal and Amendment A1 draft are superseded in full by this rewrite).

Finalized architectural decisions (no longer open questions):

| ID | Decision |
|---|---|
| **A1** | **Production Unit replaces episode as the neutral workload primitive.** Episode is optional metadata (§11, §12). |
| **A2** | **Production Bible introduced as an optional first-class Production component** (§10). |
| **A3** | **Single-video, batch, and episodic productions use the same Production abstraction** — no special architectures (§9). |
| **A4** | **Per-unit ProductionPlan generation is deferred to Phase 10** (lazy; Phase 9 invokes no LLM provider) (§13). |
| **A5** | **Bible receives canonical named sections while remaining extensible** — no speculative typed schemas in Phase 9 (§10). |
| **A6** | **Initial unit-type vocabulary is open/extensible** — recommended initial set: `standalone_video`, `episode`, `chapter`, `campaign_asset`, `batch_item` (§11). |
| **A7** | **Human-readable collision-safe production IDs adopted**: `prd_YYYYMMDD_<slug>_<###>` (§18). |
| **A8** | **Dedicated `productions/` intent storage adopted**, separate from `projects/` (§22). |
| **A9** | **Minimal durable command capture adopted** — original text + explicitly supplied structured intent; no command grammar/parser (§8). |

Amendments to this document require an explicit, documented update. This document is
strategic/architectural specification; it changes no runtime behavior.






