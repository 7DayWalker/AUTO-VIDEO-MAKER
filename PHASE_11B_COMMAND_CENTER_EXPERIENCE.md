# PHASE 11B — COMMAND CENTER EXPERIENCE

## 1. Document Status

**Specification state:** SPECIFICATION DRAFTED / AWAITING EXPLICIT FREEZE APPROVAL.

**Phase:** 11B — Command Center Experience.

**Track:** Personal AVM Maturity.

**Implementation status:** NOT IMPLEMENTED.

**Implementation authorization:** NOT GRANTED BY THIS DOCUMENT.

This document freezes the intended Phase 11B experience boundary for review. It is an
architectural and product specification, not an implementation task. Creating this file
does not authorize production-code changes, test changes, frontend changes, backend
changes, dependency changes, commits, or pushes.

This specification is subordinate to the authoritative roadmap and to the frozen Phase 9,
Phase 10, and Phase 11 contracts. It supersedes no earlier specification.

### 1.1 Authoritative inputs

This specification is based on:

- `AVM_MASTER_ROADMAP.md`
- `PHASE_11_PERSONAL_COMMAND_CENTER.md`
- the Phase 11B Discovery / Architectural Analysis report
- the actual repository implementation inspected at specification time

### 1.2 Current repository baseline

At specification time:

- Phase 11 is accepted and checkpointed at Git commit
  `b50503c0a1612cdcf24de8a1c7e1e2e44da5f79a`.
- Phase 11B is inserted between Phase 11 and Phase 12 in the authoritative roadmap.
- Phase 12 remains **Personal Automation**.
- `productions/` is currently empty in the inspected working tree.
- `output/projects/` contains existing execution-project artifacts.
- The current working tree contains a pre-existing, intentionally unstaged modification to
  `AVM_MASTER_ROADMAP.md`; this specification does not modify that file.
- The Phase 11 specification contains specification-time baseline language such as
  “FROZEN PENDING FORMAL ACCEPTANCE” and “Phase 11 status at specification time: NOT
  STARTED.” Those statements are historical context from its authoring baseline. Current
  Phase 11 status is established by the roadmap, accepted implementation, and checkpoint
  above.

For **current Phase 11 status**, the precedence order is explicit:

1. The accepted `AVM_MASTER_ROADMAP.md`.
2. The accepted Phase 11 implementation in the repository.
3. The current accepted checkpoint
   `b50503c0a1612cdcf24de8a1c7e1e2e44da5f79a`.

Those sources supersede historical specification-time metadata in
`PHASE_11_PERSONAL_COMMAND_CENTER.md`. The architectural decisions and contract in the
Phase 11 specification remain authoritative unless they are explicitly superseded by an
accepted roadmap or implementation decision.

### 1.3 Evidence limitations

The repository contains no dedicated screenshot set for this discovery. The UX assessment
uses the actual source, API contracts, tests, current disk state, and the supplied live
observation that the application displayed:

```text
Engine connected · live data
No productions yet.
```

The final implementation must continue to distinguish those two truthful facts rather than
collapse them into one generic status.

### 1.4 Discovery traceability notes

- **F-05 — Static UI-source verification compromise:** The repository intentionally uses
  dependency-free static source-contract checks because no browser/JavaScript test
  infrastructure exists and Phase 11 forbids frontend framework/build-tool expansion.
  Phase 11B preserves that approach for structural guarantees and supplements it with
  manual browser, accessibility, responsive, media-safety, and execution-semantics checks.
- **F-06:** F-06 meaning was not recoverable from currently tracked repository material
  during discovery/audit. No meaning is invented or assigned by Phase 11B.

---

## 2. Phase Objective

Phase 11B is a **presentation-first refinement of the existing localhost-only Phase 11
Personal Command Center**, using existing Phase 9 intent, Phase 10 execution/binding
evidence, existing project manifests, existing artifacts, existing reconciliation, and
existing publishing state to create a coherent personal production-control experience.

The objective is not to make a generic dashboard prettier. The objective is to make the
operator understand the AVM production lifecycle and safely act on it:

```text
Production Intent
        ↓
Production
        ↓
Production Units
        ↓
Execution State
        ↓
Bound Project
        ↓
Media / Artifacts / Evidence
        ↓
Publishing
```

The finished experience shall make these questions answerable without reconstructing the
answer from unrelated screens:

1. What am I producing?
2. What is happening now?
3. What needs my attention?
4. What has completed?
5. What is blocked?
6. What failed?
7. What can safely happen next?
8. Which project is bound to this unit?
9. What media actually exists?
10. What artifacts actually exist?
11. What provider evidence exists?
12. What publishing state exists?

Phase 11B improves observation and control presentation. It does not change the meaning
of any domain object or execution operation.

---

## 3. Relationship to the Roadmap

The authoritative sequence is:

```text
Phase 9   — Durable Production Plan       COMPLETE
Phase 10  — Plan-Backed Execution         COMPLETE
Phase 11  — Personal Command Center       COMPLETE
Phase 11B — Command Center Experience     CURRENT / THIS SPECIFICATION
Phase 12  — Personal Automation           FUTURE
Phase 13  — Quality and Scale              FUTURE
Phase 14  — Provider Intelligence          FUTURE
Phase 15  — Recovery / Retry / Fallback    FUTURE
Phase 16+ — Ongoing content operations     FUTURE
```

Phase 11B is a bounded lettered insertion on the personal-maturity track. It is not a new
product architecture, not a replacement for Phase 11, and not a reuse of the historical
publishing-track “8A” collision space.

Phase 11B exists before Phase 12 specifically to polish the operator experience before
automation and scheduling are introduced.

---

## 4. Relationship to Phase 9, Phase 10, Phase 11, and Phase 12

### 4.1 Phase 9 — Durable Production Plan

Phase 9 owns production intent:

- production identity and command provenance;
- production units;
- unit intent;
- dependencies;
- creative/bible context;
- schema and plan versioning;
- revision semantics.

Phase 11B may display those values. It shall not edit, revise, reorder, or reinterpret
them.

### 4.2 Phase 10 — Plan-Backed Execution

Phase 10 owns execution through `ProductionExecutor` and the existing AVM pipeline.

Phase 10 owns:

- execution composition;
- binding discovery;
- six-state progress derivation;
- dependency enforcement;
- remote-job protection;
- resume behavior;
- execution manifests and checkpoints;
- reconciliation semantics.

Phase 11B may present those facts and invoke already-authorized explicit operations through
the existing API. It shall not reproduce or modify those semantics.

### 4.3 Phase 11 — Personal Command Center

Phase 11 delivered the functional observation/control surface:

- production list and production detail;
- unit detail;
- six-state progress presentation;
- explicit Execute/Resume commands;
- confirmation dialogs;
- read-only reconciliation;
- project and artifact inspection;
- per-unit publishing delegation;
- localhost-only vanilla SPA and thin Python API extension.

Phase 11B refines how those capabilities are understood and used. It does not authorize
new Phase 11 domain capabilities.

### 4.4 Phase 12 — Personal Automation

Phase 12 owns future automation concerns:

- scheduling;
- unattended execution;
- automatic continuation;
- automation/watchers;
- automated publishing;
- future recovery automation.

Phase 11B shall not pull those concerns forward.

---

## 5. Architectural Authorities

Phase 11B preserves the Phase 11 “three authorities, no fourth” model.

### 5.1 Intent authority

**Authority:** Phase 9 production intent.

**Location:** `productions/` and its authoritative production artifacts.

**Meaning:** What AVM intends to produce.

The UI is read-only over intent. Phase 11B shall not mutate `production.json`, unit
definitions, dependencies, plan versions, or content intent.

### 5.2 Execution authority

**Authority:** Phase 10 `ProductionExecutor` and the existing execution pipeline.

**Location:** `output/projects/<project_id>/manifest.json`, checkpoints, and existing
execution artifacts.

**Meaning:** What execution has actually done and what evidence exists.

The Command Center shall not invoke providers directly, create projects independently, or
modify execution manifests.

### 5.3 State authority

**Authority:** `ProductionExecutor.derive_progress()`.

**Meaning:** The authoritative classification of unit state.

Required conceptual pipeline:

```text
ProductionExecutor.derive_progress()
        ↓
existing API transport
        ↓
Command Center presentation
```

The frontend may format, group, filter, and explain the returned state. It shall not
independently classify execution state or maintain a competing state machine.

### 5.4 Binding authority

**Authority:** `ProjectManifest.production_binding`.

A production unit is bound to a project only when the loadable execution manifest contains
an authoritative valid binding referencing that production and unit.

The following are not binding authority by themselves:

- directory names;
- project-directory presence;
- plan directories;
- `plan_meta.json`;
- artifact paths;
- topic similarity;
- IDs that merely look related.

### 5.5 Artifact authority

**Authority:** Existing project-manifest, checkpoint, stage, and artifact structures.

Phase 11B may present actual artifact evidence. It shall not claim an artifact is available
merely because a path string is recorded.

### 5.6 Publishing authority

**Authority:** Existing publishing modules and durable publication records.

Phase 11B may present publication state and invoke already-authorized per-unit publishing
delegation. It shall not create a new publishing state machine or production-level
publishing authority.

### 5.7 Derived surface rule

The Command Center remains a derived observation/control surface:

```text
Derived Command Center View
  = inspect intent
  + inspect execution evidence
  + inspect authoritative binding
  + reconcile existing artifacts when explicitly requested
  + present existing publication state
```

No derived UI view may become a durable authority.

---

## 6. Non-Negotiable Architectural Boundaries

Phase 11B shall remain:

- localhost-only;
- dependency-light;
- vanilla-JavaScript compatible;
- presentation-first;
- read-only wherever possible;
- delegated to existing authorities for state-changing operations.

Phase 11B shall not introduce:

1. A second execution engine.
2. A second orchestrator.
3. A second state machine.
4. A second production authority.
5. A second binding authority.
6. A durable UI state authority.
7. A database.
8. Authentication or tenancy.
9. Public or LAN/network exposure.
10. A scheduler.
11. Automation, watchers, queues, or daemons.
12. Unattended execution or automatic continuation.
13. Automatic retry, fallback, or provider substitution.
14. Provider optimization, ranking, or recommendation.
15. Quality scoring or quality gates.
16. Production, unit, or dependency editing.
17. Batch execution or batch publishing.
18. New publishing orchestration or a new publishing state machine.
19. Media transcoding, regeneration, repair, or a new media-processing engine.
20. A new asset-generation engine.
21. Frontend framework migration.
22. Frontend build-system migration.
23. A separate mobile application.

Any proposal requiring classifications D, E, F, or G in Section 26 is rejected from this
phase unless a separate roadmap/specification amendment is approved first.

---

## 7. Six-State Vocabulary and Presentation Groups

The authoritative states remain exactly:

```text
NOT_STARTED
BLOCKED
IN_FLIGHT
INCOMPLETE
COMPLETED
FAILED
```

They shall not be renamed, replaced, or supplemented by a competing domain taxonomy.

Phase 11B may introduce presentation groups such as:

| Presentation group | Included authoritative states | Meaning |
|---|---|---|
| Needs attention | `FAILED`, `BLOCKED` | Operator should inspect a reason or dependency. |
| Executing | `IN_FLIGHT` | Existing execution evidence indicates in-flight work. |
| Resumable | `INCOMPLETE` | Existing semantics indicate an explicit resume may be appropriate. |
| Ready to begin | `NOT_STARTED` | No execution binding/evidence has started the unit. |
| Completed | `COMPLETED` | Existing execution evidence says the unit is complete. |

These are filters, visual groups, and explanatory labels only. They are not persisted
states, not API state values, and not authority over `derive_progress()`.

---

## 8. Operator Experience Model

The operator journey is frozen as:

```text
OVERVIEW
   ↓
PRODUCTIONS
   ↓
PRODUCTION DETAIL
   ↓
UNIT DETAIL
   ↓
MEDIA / ARTIFACTS / EVIDENCE
   ↓
PUBLISHING
```

At every level, the interface shall make the following explicit:

- **Where am I?**
- **What am I looking at?**
- **What happened?**
- **What is happening?**
- **What needs attention?**
- **What can I do?**
- **What will happen if I do it?**

The operator should not need to infer the production hierarchy from IDs, paths, or
navigation order.

### 8.1 Experience principle

The Command Center should feel like a personal AI-video production control room:

- operational rather than decorative;
- media-aware where media exists;
- evidence-based rather than speculative;
- dense enough for production work but not an undifferentiated admin table;
- explicit about safe actions and prohibited actions.

---

## 9. Information Architecture

The conceptual navigation is frozen as:

```text
Workspace
    Overview
    Productions
    Projects / Outputs
    Create Video

Distribution
    Publishing Activity
    Platforms

System
    Settings
```

No additional top-level destinations are required by Phase 11B.

### 9.1 Overview

The attention-oriented landing surface. It answers:

> What deserves my attention right now?

It should summarize existing production, project, and platform evidence without duplicating
the full Productions workspace.

### 9.2 Productions

The primary production-control workspace. It contains the production list, production
detail, unit detail, execution controls, dependency visibility, evidence, and links to
media and publishing.

### 9.3 Projects / Outputs

The execution-project and generated-output inspection surface. It explains that a Project
is an execution output and is separate from a Production intent. Where a valid
`production_binding` exists, it links back to Production → Unit.

### 9.4 Create Video

The existing command-generation workflow. It prepares the current AVM engine command. It
does not become a new browser-based production-authoring system and does not introduce
production intent mutation.

### 9.5 Publishing Activity

The existing publication history/activity surface sourced from durable publication records.
It does not become a durable execution-event system or general activity ledger.

### 9.6 Platforms

The existing connection and platform-capability surface. Connection status is distinct from
whether a given platform is publishable through a particular Command Center action.

### 9.7 Settings

The existing local configuration and safety-information surface. Secrets remain server-side
and are never displayed.

---

## 10. Overview Experience

Overview is an attention-first summary, not a second Productions index.

Recommended priority order:

1. Failed units or productions requiring attention.
2. Blocked work and its blocking dependencies.
3. Active/in-flight work.
4. Incomplete/resumable work.
5. Recently completed productions.
6. Recent outputs with actual media evidence.
7. Platform and connection summary.

The implementation may use existing API payloads and small read-only additions only where
required. It shall not invent activity or execution history that is not present.

### 10.1 Independent status dimensions

The Overview must distinguish these dimensions:

| Dimension | Allowed presentation values | Meaning |
|---|---|---|
| Engine | Connected; Offline; Connection check failed | Whether the local web/engine bridge responds. |
| Production data | Live available; Live empty; Unavailable; Intent malformed | State of the Phase 9 production data source. |
| Project data | Live available; No projects; Demo samples; Manifest unavailable | State of the project/output data source. |
| Platform | Connected; Not connected; Coming soon; Connection error | State/capability of a platform connector. |

“Engine connected” shall never imply “productions exist.”

### 10.2 Connected plus empty production data

When the engine is connected and `productions/` contains no Phase 9 production records,
the interface shall communicate all of the following:

- the engine is connected;
- the production data source is live/real;
- there are currently no production records;
- the Command Center does not create productions;
- the user must use the existing AVM production workflow to create/issue production intent.

This is a valid empty state, not an offline state and not a demo state.

---

## 11. Production List

The production list is the primary operational index for durable productions.

Each production card/row shall communicate, using authoritative data where available:

- title and identity;
- production ID;
- unit count;
- six-state distribution;
- completed/total progress;
- attention indicators;
- current explicit execution/run indication where safely available;
- last relevant authoritative timestamp where available, with the source labelled as
  `revised_at` for production intent or `updated_at`/checkpoint timestamp for execution
  evidence; do not invent a cross-source “last activity” value;
- a clear route to Production Detail.

The production list shall not imply that a progress value is durable state. Progress is
freshly derived through the Phase 10 executor.

### 11.1 Ephemeral run metadata

The current server has an in-memory `_production_runs` registry for presentation of an
active explicit command. If this is shown in the production list, it must be labeled and
treated as:

- ephemeral process/session information;
- not durable execution state;
- not a new authority;
- not a replacement for manifest/checkpoint evidence;
- never persisted by the Command Center.

If the existing API cannot safely provide this value, any support shall be a small additive
read-only API change and shall not add persistence.

### 11.2 Actual current implementation note

The current production list in `webapp/assets/js/views/productions.js` renders production
cards, counts, a unit-completion bar, and Execute/Resume production actions. It does not
currently provide the full attention-oriented hierarchy specified here. That is an
experience refinement target, not evidence that the production authority is missing.

---

## 12. Production Detail

Production Detail is organized around the following frozen sections, in this priority
order:

1. Production identity.
2. Operational attention.
3. Progress and state summary.
4. Primary execution controls.
5. Production units.
6. Dependencies and blocking.
7. Bound projects.
8. Supporting evidence.

The visual hierarchy shall prioritize operational state over secondary metadata.

The page shall make clear:

- what the production is;
- how many units exist;
- how units are distributed among the six states;
- whether anything needs attention;
- whether an explicit execution command is active;
- what action is safe for each unit;
- which units are bound to projects.

### 12.1 Unit presentation groups

The detail view may support these filters/groups:

- All;
- Needs attention;
- Executing;
- Resumable;
- Completed;
- Blocked;
- Not started.

They are presentation filters only and must not be sent as alternate domain states.

### 12.2 Actual current implementation note

The current `production-detail.js` already renders command provenance, six-state counts,
unit rows, dependencies, bound-project links, production controls, explicit confirmation,
and read-only polling. It is functionally aligned with Phase 11 but visually dense and
does not yet provide the full attention/evidence hierarchy frozen here.

The list-level active-run indication is required only when the existing response or an
approved Class C read-only extension supplies the ephemeral value. Production Detail must
still provide the authoritative inspection surface when list-level run data is absent.

---

## 13. Execute versus Resume Presentation

This is a high-priority safety requirement.

The visible action must communicate the existing Phase 10 semantic operation. The server
and executor remain authoritative about whether an operation is allowed.

| Authoritative state | Preferred presentation | Phase 11B rule |
|---|---|---|
| `NOT_STARTED` | **Primary:** Execute. **Secondary:** Inspect. | Begin execution. Do not imply that a project or execution binding already exists. |
| `INCOMPLETE` | **Primary:** Resume. **Secondary:** Inspect. | Continue existing incomplete execution. Do not present a fresh “new execution” implication or label the primary action Execute. |
| `IN_FLIGHT` | **Primary:** Inspect current state. **Secondary:** Resume Unit, shown as an explicitly labelled continuation action when no active command is already registered; disable it with an “Execution already in progress” explanation when the current run metadata says this production is active. | The current Phase 10 executor accepts re-entry for in-flight work and protects remote jobs from resubmission. Resume means explicit re-entry through the existing executor, never a new remote submission. The UI must never imply duplicate execution. |
| `COMPLETED` | **Primary:** View media. **Secondary:** Publish where currently supported. **Additional:** Inspect. | Do not offer Execute or Resume and do not imply re-execution. |
| `BLOCKED` | **Primary:** View blockers. **Secondary:** Inspect. | No dependency bypass, force action, or execution promise. |
| `FAILED` | **Primary:** View failure / existing recovery guidance. **Secondary:** Inspect. | Do not invent retry, skip, fallback, force execution, or automatic recovery. |

Phase 11B shall not add:

- force execute;
- force resume;
- retry;
- cancel;
- skip;
- regenerate;
- provider override.

If the current API returns an action response that differs from the display expectation,
the implementation must follow the API/executor semantics and report the discrepancy. It
must not create a frontend-only interpretation.

### 13.1 Authoritative action and stale-render rule

The matrix above is a presentation contract, not execution authority. The API/server is
the final authority at the instant an action is requested. Between page render and click,
the state may have changed because another explicit command, an existing engine process,
or an external operator action changed the on-disk evidence. Therefore:

1. The client must send only an already-authorized operation and must not infer permission
   from stale markup alone.
2. The server must re-derive/validate current state before starting or rejecting the
   command, using the existing Phase 10 authority.
3. A stale or rejected action must render the returned truthful error/state and must not
   retry automatically or silently substitute another action.
4. A disabled or hidden button is presentation only; it never grants or denies authority
   independently of the server.
5. `IN_FLIGHT` Resume means explicit re-entry through `ProductionExecutor.execute()`
   with existing remote-job protection. It is visible as a secondary action only when
   no active command is known; it is disabled while the current production run is active.
   It never means submit another remote job.

---

## 14. Execution Experience

The existing explicit command semantics remain frozen:

- the operator initiates the command;
- the UI requests confirmation;
- the API delegates to `ProductionExecutor`;
- the server may run the command in a background thread;
- the UI may poll read-only progress;
- polling never starts, resumes, retries, or continues execution;
- no automatic continuation exists;
- no automatic retry exists;
- no cancellation is introduced in Phase 11B.

### 14.1 Confirmation requirements

Confirmation remains mandatory for:

- Execute Production;
- Resume Production;
- Execute Unit;
- Resume Unit;
- Publish Unit.

The confirmation dialog shall show:

- operation;
- target;
- scope;
- unit count where applicable;
- first action where known;
- explicit Confirm;
- explicit Cancel.

Cancel, backdrop close, Escape close, or dialog dismissal shall produce no state-changing
request.

### 14.2 Post-command presentation

After command acceptance, the UI should communicate:

- command accepted;
- target and operation;
- current known execution state;
- current known ephemeral run state where available;
- last refresh/derivation time;
- that the view is derived from local disk-backed authorities;
- any current error;
- what the Command Center did not do, where that distinction matters.

### 14.3 Polling

Polling remains observation-only. It may refresh displayed progress and stop when the
existing run indication is no longer active. It shall not:

- send a POST;
- trigger `ProductionExecutor`;
- retry a failed request;
- submit a remote job;
- mutate a manifest;
- persist derived state.

---

## 15. Unit Detail

Unit Detail shall visually connect the lifecycle rather than present isolated metadata
tables.

### 15.1 Unit header

The header shall show:

- production title and route context;
- unit title;
- ordinal;
- unit ID;
- authoritative six-state status;
- next safe action or reason no action is available.

### 15.2 Intent

Display existing intent values where present:

- content intent;
- unit type;
- episode metadata;
- provider intent;
- dependencies;
- Bible references;
- plan identity;
- plan version;
- content hash.

Intent shall be visibly distinguished from execution evidence.

### 15.3 Execution

Display:

- derived six-state state;
- bound project ID, only from `production_binding`;
- bound plan version;
- current stage and stage progression;
- failure information;
- blocking-unit explanation.

### 15.4 Evidence and reconciliation

Display, where existing data supports it:

- scene checkpoints;
- scene status;
- actual provider evidence;
- job/status evidence that is safe to expose;
- recorded generation cost;
- timestamps;
- on-demand read-only reconciliation;
- completion verdict and reasons.

### 15.5 Actual current implementation note

The current `unit-detail.js` already renders intent, execution state, plan evidence,
bound-project stage/checkpoint data, artifact paths, publication chips, failure details,
reconciliation, and explicit controls. The main Phase 11B change is the experience
hierarchy: intent → bound project → execution → media/evidence → publishing.

---

## 16. Media Experience

Media is a first-class experience element when existing valid media is available.

### 16.1 Existing media evidence

The current project-manifest adapter can expose existing values including:

- scene video paths;
- voice audio path;
- captions path;
- final video path;
- content and scene manifest paths;
- stage and checkpoint information.

The current server provides project-level final-media delivery through:

```text
GET /api/projects/<project_id>/media
```

The current project-detail view can render a final video when the engine is live and a
valid final video exists. There is no frozen requirement for a new scene-media delivery
engine.

### 16.2 Completed-unit media presentation

Where existing valid media is available, a completed unit should provide:

- final-video preview;
- unit identity;
- media availability state;
- audio/caption indicators based on actual artifact evidence;
- publishing readiness information;
- a clear relationship to the bound project.

### 16.3 Scene storyboard/timeline

Where existing scene video paths and checkpoint evidence are safely available, the UI may
present:

```text
Scene 1 → Scene 2 → Scene 3 → …
```

Each scene may show:

- scene order and identifier;
- preview where safely supported;
- checkpoint status;
- provider evidence;
- recorded cost where present;
- timestamp;
- missing or failed state.

The scene-media behavior is explicit:

| Evidence condition | Required presentation |
|---|---|
| Safe existing scene media can be delivered from an allowlisted project-root file | Show a preview affordance for that scene. |
| Scene metadata/checkpoint exists but no safe scene-media delivery is available | Show a metadata-only scene card; do not render a broken or invented preview. |
| Scene media path/evidence is recorded but the validated file is missing | Show `RECORDED BUT MISSING`. |
| Authoritative scene/stage evidence reports failure | Show `FAILED` and the available failure reason. |
| No scene evidence has been produced and the scene is applicable | Show `NOT GENERATED`, not an invented placeholder artifact. |

Scene metadata must remain useful without media delivery. A storyboard/timeline is therefore
not contingent on introducing a new media server, and missing preview support must not block
the rest of the evidence presentation.

### 16.4 Conditional scene-media delivery contract

The existing project-level final-media route does not provide scene-media delivery. If an
actual scene preview is required and cannot use an existing safe route, a narrowly scoped
Class C route may be proposed:

```text
GET /api/projects/<project_id>/scenes/<scene_id>/media
```

The route is conditional and is not approved merely by this specification. If approved,
its contract is:

- `GET` only; no mutation, processing, transcoding, thumbnail generation, or regeneration;
- resolve the scene file only from the authoritative manifest/checkpoint scene path;
- require the resolved file to remain inside the approved `output/projects/<project_id>/`
  root;
- reject traversal, absolute-path escape, symlink escape, unknown project identity, and
  arbitrary query-selected files;
- return only an allowlisted supported media content type, such as `video/mp4`, with a
  safe content length;
- on success, return the media bytes with the validated allowlisted `Content-Type` and no
  filesystem path in the response body; on failure, return the existing JSON error
  envelope with a stable error code and no raw path;
- return `404` for absent/unrecorded scene media and a truthful non-success response for
  invalid, missing, or failed evidence;
- never return a remote URL, credential, token, environment value, or absolute filesystem
  path;
- have tests for valid media, missing media, traversal, root escape, unsupported type,
  malformed evidence, and read-only behavior.

If this route is not approved or cannot safely deliver a scene file, the UI must use the
metadata-only behavior above. A new media-processing system is never an alternative.

### 16.5 Prohibited media work

Phase 11B shall not add:

- transcoding;
- thumbnail-generation infrastructure;
- media repair;
- scene regeneration;
- content transformation;
- a new asset-generation service;
- a new media-processing engine.

---

## 17. Artifact Experience

Artifact presentation shall distinguish these states:

```text
AVAILABLE
RECORDED BUT MISSING
NOT GENERATED
FAILED
NOT APPLICABLE
```

The UI must not claim an artifact is available merely because a manifest contains a path
string.

### 17.1 Artifact-state derivation

Artifact state is a presentation classification derived from existing authoritative
evidence. It is not a new durable state and is never written back to disk. Apply the first
applicable rule in this order:

1. **FAILED:** authoritative stage/checkpoint evidence reports that the artifact’s
   producing operation failed. A stale file does not override this failure state for the
   current operation.
2. **RECORDED BUT MISSING:** an authoritative artifact path/evidence record exists, but
   the path cannot be resolved to an existing regular file inside the approved project
   root.
3. **AVAILABLE:** an authoritative path resolves to an existing, non-empty, safely
   addressable regular file inside the approved project root. For media, the file type is
   allowlisted and the server’s existing safe delivery path can serve it. This does not
   claim that a media decoder has been run unless the repository has such evidence.
4. **NOT APPLICABLE:** the artifact type is structurally irrelevant to the current
   project/unit configuration, and that irrelevance is established by existing manifest,
   configuration, or stage-contract evidence.
5. **NOT GENERATED:** no artifact path/evidence exists, the artifact is applicable, and
   the applicable producing stage has not produced it or has not yet reached it.

The precedence and edge cases are:

| Case | Required state/behavior |
|---|---|
| Stage failed and a stale artifact file remains | `FAILED`; do not present the stale file as the current successful output. |
| Path recorded but file is missing | `RECORDED BUT MISSING`. |
| No path and producing stage has not been reached | `NOT GENERATED`. |
| Artifact structurally irrelevant | `NOT APPLICABLE`, only when existing contract evidence proves irrelevance **and no earlier rule (failure evidence, recorded path, or valid in-root file) applies**. The numbered precedence in §17.1 always governs. |
| File exists outside the allowed project root | `RECORDED BUT MISSING` with a safe invalid-path reason; never `AVAILABLE`, and never serve it. |
| File exists but is empty, malformed, or disallowed | Never `AVAILABLE`; use `RECORDED BUT MISSING` for a recorded unusable file or `FAILED` when authoritative failure evidence applies. |
| Final video exists but stage metadata is incomplete | It may be shown as `AVAILABLE` only as an existing safely addressable artifact; do not claim the pipeline is complete or that the file is final by metadata inference. |

The `NOT APPLICABLE` rule is evaluated before `NOT GENERATED`: an artifact known to be
irrelevant must not be described as merely not yet produced. A file outside the allowed
root or with an unsafe path is never served, even if the raw manifest value is present.

The following are separate facts and must not be equated:

```text
path recorded
≠ file exists
≠ file is safely addressable
≠ file is playable
≠ file is final
```

Where the current repository cannot prove decoder-level playability, the UI shall say
`Available for preview` only after safe file/type checks and shall allow the browser to
report playback failure. It shall not claim that playability was independently validated.

When the relevant data and safe action exist, read-only artifact cards may provide:

- artifact type;
- availability;
- preview;
- open;
- download;
- relevant existing metadata.

### 17.2 Safe artifact-path representation

The browser receives only one of the following representations:

- a project-relative display path, normalized with forward slashes and relative to the
  approved project root; or
- an allowlisted same-origin media URL generated by the server for a validated artifact;
  or
- a metadata-only record when no safe delivery is available.

The browser never receives an unnecessary absolute filesystem path, an arbitrary file
selector, or an unsafe remote URL. The server may retain the original internal path long
enough to validate and serve the artifact, but it must not expose that internal value as a
general-purpose browser field.

Missing/invalid paths are represented as an explicit artifact state plus a safe reason,
not as a link. Path validation must occur before any file is opened or served.

Any future artifact/media delivery support must:

- validate paths;
- remain inside allowed project roots;
- avoid absolute-path leakage;
- avoid arbitrary-file access;
- avoid secrets and credentials;
- avoid unsafe arbitrary remote URLs;
- remain read-only;
- not process or regenerate media.

The current implementation exposes manifest-derived paths in bound-unit payloads and can
encounter `polling_url` inside raw checkpoint data. Before any Phase 11B UI consumes those
fields, the transport must conform to this section: paths are normalized to the safe
representation above and `polling_url` is omitted or reduced to safe status evidence.

---

## 18. Provider and Model Presentation

Provider intent and actual provider evidence must remain separate.

### 18.1 Required presentation distinction

```text
Provider intent:
  <requested routing intent from production/unit intent>

Actual provider evidence:
  <provider observed in execution/checkpoint evidence>

Recorded generation cost:
  <value when present>

Cost unavailable:
  <when no trustworthy value exists>
```

The UI may show provider evidence per scene and summarize observed providers if the summary
is derived from actual checkpoint data.

### 18.2 Prohibited provider presentation

Do not display or introduce:

- API keys;
- tokens;
- credentials;
- environment values;
- provider secrets;
- provider recommendations;
- provider ranking;
- cost optimization recommendations;
- provider substitution;
- fallback controls;
- retry controls.

Unavailable cost shall not be rendered as zero. A total may only be shown when its
coverage and meaning are clear; incomplete evidence must be labeled as incomplete or
unavailable.

### 18.3 Polling URL rule

The current server-side scene checkpoint adapter can encounter a `polling_url` value.
Phase 11B shall treat remote polling URLs as server-side transport data by default:

- do not expose them in the UI;
- do not expose them through a new API response by convenience;
- expose only a safe status/job representation when the approved UI requirement needs it;
- require explicit security review before any URL is made operator-visible.

This rule does not change Phase 10 remote-job protection or polling behavior.

---

## 19. Publishing Experience

Publishing remains separate from execution:

```text
Completed unit
    ↓
Final media evidence
    ↓
Destination availability
    ↓
Existing publication state
    ↓
Existing explicit publish action
```

The interface shall distinguish:

- platform connected;
- platform supported by this Command Center action;
- already published;
- not published;
- publication processing/submission state where already recorded;
- publication failure;
- manual reconciliation required;
- unsupported surface.

The current production-level unit adapter delegates to the existing publishing flow and
supports TikTok through the existing bridge. A connected platform is not automatically a
platform supported by every Command Center action. The UI must communicate that
distinction, including the current limitation that YouTube is not added as a new
production-level web orchestration path by Phase 11B.

Publishing must remain:

- per-unit;
- operator initiated;
- confirmation gated;
- project-bound;
- owned by existing publishing modules and records.

Phase 11B shall not introduce:

- batch publishing;
- automatic publishing;
- scheduling;
- new publishing adapters;
- production-level publication state;
- a new publishing state machine;
- YouTube orchestration not already supported by the current surface.

---

## 20. Projects / Outputs

The domain distinction is frozen:

```text
Production = durable intent and workload
Project    = execution project/output bound to a production unit
```

Projects/Outputs shall make the relationship visible when, and only when,
`ProjectManifest.production_binding` provides authoritative binding.

Where a valid binding exists, Project Detail should provide navigation:

```text
Project
   ↕
Production → Unit
```

Where no valid binding exists, the UI shall say that the project is unbound or legacy. It
shall not guess.

### 20.1 Conditional binding-summary API contract

The current `GET /api/projects/<project_id>` response does not expose the complete
authoritative binding needed for a Project → Production → Unit backlink. If the backlink
cannot be supplied from an existing safe payload, the following narrowly scoped Class C
extension is permitted:

```text
GET /api/projects/<project_id>
```

The response may add exactly one optional field:

```json
{
  "production_binding": null
}
```

or, when the loadable project manifest contains a valid binding whose
`production_binding.project_id` matches the requested project identity:

```json
{
  "production_binding": {
    "production_id": "...",
    "unit_id": "...",
    "production_plan_version": 1,
    "unit_plan_identity": "...",
    "unit_plan_version": 1,
    "unit_plan_content_hash": "<sha256>",
    "project_id": "<requested-project-id>",
    "bound_at": "<timestamp>"
  }
}
```

The exact source is `ProjectManifest.production_binding`; no other field may populate
this object. The response contract is:

- `null` for an unbound project or a legacy manifest without a binding;
- `null` for a project whose binding is absent; an unbound or legacy project is not an
  error and is not adopted;
- a structured non-success response (never a guessed binding) for a binding that is
  orphaned, malformed, or does not match the requested project identity. The response
  must use the existing API error envelope and a stable error code such as
  `PROJECT_BINDING_INVALID`;
- never inferred from directory names, plan artifacts, `plan_meta.json`, artifact paths,
  topic, or similar IDs;
- read-only and non-persistent;
- free of absolute paths, credentials, tokens, polling URLs, and environment values.

Required tests for this conditional extension are:

- valid bound manifest returns the exact safe binding fields;
- legacy/unbound manifest returns `null` and is not adopted;
- mismatched/corrupt binding returns the structured invalid-binding error and is not
  silently associated;
- path traversal/project-ID confusion cannot expose another project’s binding;
- no mutation occurs and no new relationship store is created.

This is a permitted Class C contract, not an implementation authorization. If the existing
API is extended, the extension must remain limited to this safe binding summary.

The current repository does not contain a dedicated `webapp/assets/js/views/projects.js`;
the Projects and Activity views are currently defined together in
`webapp/assets/js/views/activity.js`. Phase 11B may reorganize presentation/modules if
needed, but this is not permission to change the domain model or introduce a framework.

---

## 21. Live, Empty, Demo, Offline, and Malformed Data States

The data-state model is frozen as follows.

### 21.1 Engine state

```text
Connected
Offline
Connection check failed
```

### 21.2 Production data state

```text
Live production data available
Live production data empty
Production data unavailable
Production intent malformed
```

### 21.3 Project data state

```text
Live project data available
No projects
Demo project samples
Project manifest unavailable
```

### 21.4 Platform state

```text
Connected
Not connected
Coming soon
Connection error
```

### 21.5 Demo data rule

Demo data must remain explicitly labeled. It must never be mixed with real production,
project, connection, execution, or publication state.

The current `demo-data.js` provides sample projects, activity, and connections only when
the local engine bridge is unavailable. Production data is not represented by demo
production records. The UI shall preserve that distinction.

---

## 22. Loading and Error States

### 22.1 Loading

Loading shall communicate observation/loading, not execution.

Permitted presentation includes:

- page-level loading state;
- section loading state;
- skeleton rows/cards;
- “Refreshing production state…”;
- semantic status announcements.

The implementation should use `aria-busy`, `role="status"`, and `aria-live` where
appropriate. A refresh shall not look like a command submission.

### 22.2 Error presentation

Important errors shall not exist only as transient toasts. The UI may retain toasts as a
secondary notification, but important failures require persistent inline context:

```text
WHAT FAILED
WHY IT FAILED
WHAT CAN I DO NEXT?
WHAT DID AVM NOT DO?
```

Existing structured backend errors shall be preserved, including truthful distinctions for:

- production not found;
- malformed production intent;
- executor failure;
- missing confirmation;
- blocked dependency;
- already-running execution;
- failed-unit recovery restriction;
- unsupported publishing;
- missing or invalid unit.

The UI shall not invent explanations or imply recovery that does not exist.

Generic render failures should provide route/data context and an appropriate refresh/retry
affordance without hiding the underlying truthful error.

---

## 23. Accessibility

Phase 11B minimum accessibility requirements are:

- semantic headings;
- semantic navigation;
- keyboard-accessible controls;
- visible focus indicators;
- accessible state labels;
- state communication independent of color;
- accessible confirmation dialogs;
- `role="dialog"` where appropriate;
- `aria-modal="true"` for modal dialogs;
- an explicit labelled-dialog relationship;
- keyboard Escape behavior;
- focus management into dialogs;
- focus return after dialog close;
- meaningful loading/status announcements;
- clear button/link distinction;
- adequate click/touch targets;
- no essential hover-only action.

Confirmation dialogs receive special attention because they guard state-changing operations.
Cancel and dismissal must be keyboard accessible and side-effect free.

No frontend framework is required or authorized merely to satisfy these requirements.

---

## 24. Responsive Experience

The experience is desktop-first with responsive fallback:

1. Desktop.
2. Laptop.
3. Tablet.
4. Mobile fallback.

### 24.1 Desktop

Provide the full production workspace, summary panels, unit/evidence presentation, and
clear persistent navigation.

### 24.2 Laptop

Preserve the information hierarchy while reducing secondary metadata and avoiding
unnecessary horizontal density.

### 24.3 Tablet

Stack panels, use compact unit presentation, and preserve state/action visibility.

### 24.4 Mobile fallback

This is not a separate mobile application. On narrow screens prioritize:

- production/unit identity;
- state;
- attention reason;
- primary safe action;
- media preview.

Do not force a dense desktop table into an unusable narrow viewport. Horizontal scrolling
may remain for evidence tables where it is the least misleading option.

---

## 25. Visual Design Direction

The implementation shall use the existing AVM visual identity as its foundation:

- dark surfaces;
- restrained blue accent;
- existing status colors;
- existing typography tokens;
- existing cards, tables, badges, and spacing primitives.

The target is a professional personal AI-video production control room, not visual
similarity to another product.

### 25.1 Priority hierarchy

Visual hierarchy shall prioritize:

1. Production identity.
2. Attention/state.
3. Progress.
4. Primary safe action.
5. Media.
6. Evidence.
7. Secondary metadata.

### 25.2 Design characteristics

Favor:

- strong hierarchy;
- restrained density;
- meaningful whitespace;
- clear typography;
- strong state indicators;
- media-first presentation where media exists;
- clear action hierarchy;
- consistent cards/panels;
- concise metadata;
- obvious navigation location;
- minimal decorative noise.

Do not introduce a large unrelated design system or optimize for copying Topview or any
other external product.

---

## 26. API Boundary

### 26.1 Existing API is the default

The existing thin API is the default transport. Phase 11B does not require a wholesale
backend rewrite or a new service layer.

Existing relevant routes include:

```text
GET  /api/health
GET  /api/connections
GET  /api/projects
GET  /api/projects/<project_id>
GET  /api/projects/<project_id>/media
GET  /api/projects/<project_id>/publish/tiktok/status

GET  /api/productions
GET  /api/productions/<production_id>
GET  /api/productions/<production_id>/units
GET  /api/productions/<production_id>/progress
GET  /api/productions/<production_id>/units/<unit_id>
GET  /api/productions/<production_id>/units/<unit_id>/reconciliation

POST /api/productions/<production_id>/execute
POST /api/productions/<production_id>/resume
POST /api/productions/<production_id>/execute?unit_id=<unit_id>
POST /api/productions/<production_id>/resume?unit_id=<unit_id>
POST /api/productions/<production_id>/units/<unit_id>/publish
```

### 26.2 No mandatory API addition at freeze

**Frozen decision:** No API addition is mandatory for Phase 11B at specification freeze.

The existing API already exposes the core values required for the P0/P1 experience,
including production intent, unit intent, derived state, blocking units, binding-derived
project information, plan evidence, stage data, scene checkpoint rows, artifact paths,
failure details, reconciliation, publication records, and ephemeral run information in
the relevant detail payloads.

This does not mean that every P0/P1 requirement is implementable without transport review.
Specific Class C API additions may become implementation-required when a P0/P1 requirement
cannot be satisfied safely from the existing API. Such an addition is allowed only after
the implementation proves, in order, that:

1. The existing API is insufficient for the requirement.
2. The addition is read-only, except for an already-authorized Phase 11 command.
3. The addition derives only from an existing authority.
4. The addition introduces no persistence or new state value.
5. The addition creates no new authority.
6. The addition has a defined filesystem, secret, and remote-transport security boundary.
7. The addition has exact payload and automated-test requirements.

### 26.3 Conditional additive support

If implementation proves that a presentation requirement cannot be met safely from the
existing API, only these conditional read-only additions may be proposed in a separate
implementation review:

| Candidate support | Classification | Conditions |
|---|---|---|
| Safe delivery of an already-existing scene artifact | C | Allowlisted project-root path; no processing; no arbitrary URL/file access. |
| Artifact availability metadata | C | Derived from existing paths/files; never durable UI state. |
| Valid Production → Unit → Project summary | C | Derived only from `production_binding`; no inference. |
| Safe ephemeral run summary | C | Clearly ephemeral; never persisted; no new state authority. |

No candidate endpoint is approved for implementation by this document. A future proposal
must specify its exact route, payload, path validation, redaction, and tests before work
begins.

### 26.4 API invariants

Any approved additive support must:

- preserve existing authority;
- remain read-only unless it is an already-authorized Phase 11 command;
- avoid new persistence;
- avoid new state values;
- validate filesystem boundaries;
- avoid absolute-path leakage;
- avoid secrets;
- avoid unsafe remote URL exposure;
- avoid direct provider invocation;
- avoid manifest or intent mutation.

No Class C candidate is pre-approved merely because it makes frontend work easier. The
implementation must provide the proof and exact contract before adding it.

---

## 27. Cross-Cutting Mandatory Phase 11B Requirements

The following requirements apply across P0, P1, and P2. They are mandatory implementation
quality and safety requirements, not optional polish and not additional feature scope:

- accessibility;
- loading states;
- error states;
- responsive behavior;
- media and filesystem path security;
- secret and transport-data safety;
- preservation of the Phase 9/10/11 regression suite;
- no-new-authority discipline;
- truthful live/empty/demo/offline and malformed-data handling;
- explicit Execute/Resume and confirmation safety.

Any P0, P1, or P2 implementation is incomplete if it satisfies the visual feature while
violating one of these cross-cutting requirements.

---

## 28. P0 / P1 / P2 Scope

The implementation scope is frozen to the following categories. Expansion requires a
roadmap/specification amendment.

### 28.1 P0 — Required

- **P0-1:** Production-centric hierarchy.
- **P0-2:** Six-state attention clarity.
- **P0-3:** Correct Execute versus Resume presentation.
- **P0-4:** Live/empty/demo/offline clarity.
- **P0-5:** Production → Unit → Project relationship.
- **P0-6:** Confirmation and active-run visibility.

### 28.2 P1 — Required after P0

- **P1-1:** Production-detail reorganization.
- **P1-2:** Rich unit-detail workspace.
- **P1-3:** Final-video preview using existing valid media.
- **P1-4:** Scene storyboard/timeline using existing evidence.
- **P1-5:** Project/output relationship presentation.
- **P1-6:** Provider intent versus actual provider evidence.
- **P1-7:** Persistent error/recovery presentation.
- **P1-8:** Modal accessibility hardening.

### 28.3 P2 — Polish

- **P2-1:** Filtering/grouping by authoritative state.
- **P2-2:** Typography, spacing, and action hierarchy refinement.
- **P2-3:** Artifact cards.
- **P2-4:** Platform/publishing summaries.
- **P2-5:** Responsive refinements.
- **P2-6:** Route active-state correction.

### 28.4 P3 — Not part of the implementation scope

P3 is reserved for future/optional ideas and is not approved Phase 11B implementation
scope. Examples include durable execution history, broad cost analytics, quality
intelligence, automation, provider optimization, and recovery policy.

---

## 29. A/B/C/D/E/F/G Classification

### 29.1 Classification definitions

| Class | Definition | Phase 11B disposition |
|---|---|---|
| A | Pure presentation | Permitted. |
| B | Existing API/data presentation | Permitted. |
| C | Small additive read-only API support | Conditionally permitted after exact review. |
| D | Domain-model change | Rejected from Phase 11B. |
| E | Execution-engine change | Rejected from Phase 11B. |
| F | Future-phase feature | Rejected from Phase 11B; carry forward. |
| G | Out-of-scope/architectural expansion | Rejected from Phase 11B. |

### 29.2 Major feature classification

| Feature | Class | Frozen boundary |
|---|---:|---|
| Production-centric layout | A | Reorganize existing screens only. |
| Six-state emphasis and grouping | A/B | Use executor-returned states; no second taxonomy. |
| Execute/Resume wording | A/B | Follow API/executor semantics. |
| Live/empty/demo/offline messaging | A/B | Separate engine, data, project, and platform dimensions. |
| Production → Unit → Project links | B/C | Use authoritative binding only. |
| Existing progress rendering | B | Freshly derived through Phase 10. |
| Existing stage/checkpoint rendering | B | Present actual evidence only. |
| Final-video preview | B/C | Use existing valid media and safe delivery. |
| Scene storyboard/timeline | A/B/C | Presentation over existing scene evidence. |
| Artifact availability cards | A/B/C | Verify paths/files; no processing. |
| Provider intent/evidence distinction | A/B | No routing or recommendation. |
| Recorded-cost presentation | A/B | Unknown is not zero. |
| Publishing-state presentation | A/B | Existing records and adapters only. |
| Modal accessibility | A | No framework required. |
| Responsive layout refinement | A | Desktop-first; no separate mobile app. |
| Persistent execution error presentation | A/B | Use existing structured errors. |
| Safe artifact/media endpoint, if proven necessary | C | Exact endpoint requires separate approval. |
| Production or unit editing | D | Out of scope. |
| Binding mutation or inference | D | Out of scope. |
| New executor/state machine | E | Out of scope. |
| Scheduling/automation | F | Phase 12. |
| Quality scoring | F | Phase 13. |
| Provider intelligence | F | Phase 14. |
| Retry/fallback/recovery policy | F | Phase 15. |
| Database/history authority | G | Out of scope. |
| Auth/tenancy/public service | G | Out of scope. |
| Media processing/regeneration | G | Out of scope. |
| Frontend framework migration | G | Out of scope. |

---

## 30. Future-Phase Carry-Forwards

### 30.1 Phase 12 — Personal Automation

Carry forward:

- scheduling;
- unattended execution;
- automatic continuation;
- automation/watchers/daemons;
- automated publishing;
- future cancellation/stop semantics if separately designed;
- future automation-oriented recovery.

### 30.2 Phase 13 — Quality and Scale

Carry forward:

- quality scoring;
- quality gates;
- content evaluation;
- quality-driven operator or automation decisions;
- scale-specific workload operations where new infrastructure is required.

### 30.3 Phase 14 — Provider Intelligence

Carry forward:

- provider ranking;
- provider optimization;
- performance comparison;
- cost/speed policy;
- routing recommendations;
- provider selection intelligence.

### 30.4 Phase 15 — Recovery / Retry / Fallback

Carry forward:

- retry policy;
- fallback;
- substitution;
- recovery orchestration;
- automated failure policy;
- quota/error recovery.

### 30.5 Phase 16+ and later public surfaces

Carry forward:

- durable operational history/event infrastructure;
- ongoing content operations;
- public control plane;
- authentication and authorization;
- tenancy and multi-user concerns;
- scale-oriented service architecture.

---

## 31. Acceptance Criteria

The following criteria are concrete and testable. They describe the future implementation,
not work performed by creating this document.

### 31.1 Architecture

- **AC-01:** No second execution engine exists; all execution commands continue to flow
  through the existing Phase 10 `ProductionExecutor`.
- **AC-02:** `ProductionExecutor` remains the sole production-level execution authority.
- **AC-03:** `ProductionExecutor.derive_progress()` remains the sole state-classification
  authority.
- **AC-04:** `ProjectManifest.production_binding` remains the sole authoritative
  production-unit-to-project binding.
- **AC-05:** No durable UI-derived state or progress authority is introduced.
- **AC-06:** No database, authentication system, tenancy model, or public/network exposure
  is introduced.
- **AC-07:** No scheduler, watcher, queue, daemon, automation, or unattended execution is
  introduced.

### 31.2 Production experience

- **AC-08:** In the primary production viewport before opening a unit, the production
  title/ID, plan version, unit count, and production-level state summary are all present
  as readable text or labelled values; none requires inspecting a secondary dialog.
- **AC-09:** In that same production viewport, counts for each of the six authoritative
  states are available without opening every unit; a zero count is represented explicitly
  or by an unambiguous zero-state treatment.
- **AC-10:** `FAILED` and `BLOCKED` each have visible state text, a non-color indicator
  (such as an icon, label, or structural treatment), and a reason or direct route to the
  relevant failure/blocker explanation.
- **AC-11:** When authoritative binding exists, the rendered experience contains named,
  navigable references in the direction `Production → Unit → Bound Project`; when it does
  not exist, it renders an explicit unbound/legacy result and no inferred project link.
- **AC-12:** Production Detail exposes dependencies and identifies blocking units without
  duplicating Phase 10 enforcement.

### 31.3 Execution

- **AC-13:** Execute versus Resume presentation matches the frozen Section 13 action
  matrix for all six states, including the explicit `IN_FLIGHT` inspection-first rule;
  any stale action rejected by the server is rendered as the returned truthful state/error
  without an automatic alternate command.
- **AC-14:** Page load, refresh, reconciliation, and polling never automatically execute,
  resume, retry, or continue production.
- **AC-15:** Confirmation remains mandatory for Execute Production, Resume Production,
  Execute Unit, Resume Unit, and Publish Unit.
- **AC-16:** Failed-unit behavior remains truthful and does not invent retry, skip,
  fallback, or force-execution semantics.
- **AC-17:** `IN_FLIGHT` presentation never implies duplicate submission or promises that a
  remote job will be resubmitted.

### 31.4 Data states

- **AC-18:** Connected engine plus empty production data is visibly distinct from offline
  engine state.
- **AC-19:** Demo project/activity/connection data is explicitly labeled and never presented
  as real production or publication data.
- **AC-20:** Malformed or unavailable production data is distinguishable from a valid empty
  production data source.

### 31.5 Unit experience

- **AC-21:** Unit Detail renders separately labelled Intent and Execution sections, and
  includes explicit cross-references from the unit header to the bound project (when
  bound), media/evidence, and publishing sections in the frozen lifecycle order.
- **AC-22:** A bound project is shown only when authoritative `production_binding` data
  validates the relationship.
- **AC-23:** Plan artifacts and plan metadata are presented as evidence, never as binding
  authority.

### 31.6 Media and artifacts

- **AC-24:** A completed unit can expose an existing final video where a valid, safely
  delivered final-media artifact exists.
- **AC-25:** Missing or unverified media is not presented as available merely because a
  path string exists.
- **AC-26:** A scene storyboard/timeline uses existing scene order, checkpoint, provider,
  path, cost, and timestamp evidence only.
- **AC-27:** No media-processing, thumbnail-generation, transcoding, regeneration, or
  repair engine is introduced.

### 31.7 Provider and cost

- **AC-28:** Provider intent and actual provider evidence remain visibly separate.
- **AC-29:** Unavailable generation cost is not displayed or aggregated as zero.
- **AC-30:** Credentials, tokens, keys, environment secrets, and unsafe transport data are
  not exposed; remote `polling_url` values remain hidden unless separately approved safe.

### 31.8 Publishing

- **AC-31:** Publishing remains explicit, per-unit, project-bound, and confirmation-gated.
- **AC-32:** A connected platform is distinguished from a platform supported by the
  particular Command Center publishing action.
- **AC-33:** No batch publishing, automatic publishing, scheduling, or new publication
  state machine is introduced.

### 31.9 Accessibility

- **AC-34:** Confirmation dialogs are keyboard accessible, dismissible without side
  effects, and usable without a pointer.
- **AC-35:** Confirmation dialogs have correct dialog semantics, `aria-modal`, and an
  accessible labelled relationship.
- **AC-36:** Dialog focus is placed appropriately and returned to the triggering control
  after close; Escape behavior is defined and tested.
- **AC-37:** State is understandable without relying solely on color.

### 31.10 Responsive behavior

- **AC-38:** At the supported desktop viewport, the production workspace renders, in
  order, production identity, attention/state, progress, primary safe action, units, and
  supporting evidence; the primary controls remain keyboard reachable without horizontal
  scrolling of the main action region.
- **AC-39:** At the supported tablet viewport, panels may stack, but production/unit
  identity, authoritative state text, attention reason, and the applicable safe action
  remain visible in the primary flow; no required value is available only through hover.
- **AC-40:** At the supported mobile-fallback viewport, the first unit/production view
  presents identity, state, attention reason, primary safe action, and media (when
  available) in that priority order or as explicitly labelled sequential sections; no
  separate mobile architecture is introduced.

### 31.11 Quality and dependency discipline

- **AC-41:** No frontend framework migration occurs.
- **AC-42:** No unnecessary dependency or frontend build system is introduced.

### 31.12 Safety and persistence

- **AC-43:** Any filesystem/media delivery support is path-safe and restricted to approved
  project roots, with traversal, symlink escape, arbitrary-file selection, and unsupported
  content-type cases rejected.
- **AC-44:** Absolute filesystem paths are not unnecessarily returned or displayed.
- **AC-45:** Remote polling URLs are not exposed unless their safety and usefulness are
  explicitly reviewed and approved.
- **AC-46:** No durable execution history, event store, progress cache, or other new
  authority is introduced.

---

## 32. Danger Zones

The following are explicit Phase 11B danger zones:

1. **Second state machine:** Reclassifying six-state progress in JavaScript or the web
   adapter instead of presenting executor output.
2. **Execute/Resume semantic drift:** Showing Execute where the operator needs Resume or
   implying that In Flight means safe resubmission.
3. **Empty live data mistaken for offline:** Treating a connected engine with zero
   production records as a disconnected or demo state.
4. **Binding inference:** Inferring Production → Unit → Project relationships from paths,
   IDs, directories, or topic similarity.
5. **False media availability:** Treating a recorded path as proof that a playable,
   complete, correctly bound artifact exists.
6. **Secret or transport-data exposure:** Leaking credentials, provider secrets, or unsafe
   remote polling URLs through a richer evidence view.
7. **Incomplete cost aggregation:** Displaying unknown cost as zero or implying complete
   accounting from partial provider evidence.
8. **Durable execution-history creep:** Creating a new database, event log, progress cache,
   or UI-owned history authority to make the interface appear more operational.
9. **Publishing-scope expansion:** Adding batch, automatic, scheduled, or new-platform
   orchestration under the guise of presentation polish.
10. **Frontend overengineering:** Introducing a framework, build system, or replacement SPA
    architecture when the existing vanilla implementation is sufficient.
11. **Hidden domain mutation:** Adding edit, adoption, binding, dependency, or plan-change
    behavior through an apparently presentational control.
12. **API/data-contract mismatch:** Treating a required experience as presentation-only
    when authoritative data is unavailable through the existing safe API. Examples include
    project backlink binding data, scene-media delivery, artifact availability, list-level
    active-run information, and transport-sensitive fields. Class C support requires proof
    that the existing API is insufficient and that the addition satisfies Section 26.
13. **Phase 12 leakage:** Introducing scheduling, unattended execution, automatic
    continuation, watchers, or automation.
14. **Phase 14 leakage:** Introducing provider ranking, optimization, recommendations, or
    routing changes.
15. **Phase 15 leakage:** Introducing retry, fallback, substitution, or automated recovery.

Any implementation encountering one of these conditions must stop and obtain an explicit
scope decision rather than silently expanding Phase 11B.

---

## 33. Testing Strategy

Testing begins only after this specification is reviewed/accepted and a separate
implementation task is issued.

### 33.1 Regression requirements

1. The existing Phase 9 regression suite must remain green.
2. The existing Phase 10 regression suite must remain green.
3. The existing Phase 11 tests in `tests/test_phase11_command_center.py` must remain
   green without weakening assertions.
4. No Phase 11 authority, command semantics, confirmation requirement, or six-state
   vocabulary may be removed to satisfy a visual test.

### 33.2 Backend/API tests

If no API is added, no new backend endpoint test is required solely for Phase 11B
presentation. If a P0/P1 requirement cannot be satisfied safely from the existing API,
the implementation must first record the insufficiency and obtain approval for the
specific Class C addition described in Section 26. If a conditional additive API is
approved, tests must cover:

- exact JSON shape;
- read-only behavior;
- authority/source-of-truth derivation;
- proof that the existing API was insufficient;
- path traversal rejection;
- allowed-root enforcement;
- absence of absolute-path leakage where not needed;
- secret redaction;
- remote URL handling;
- malformed/missing artifact behavior;
- no persistence of derived UI state.

For the conditional project binding summary, tests must cover the exact response field,
valid binding, `null` unbound/legacy behavior, and structured invalid-binding behavior.
For conditional scene-media delivery, tests must cover the exact route, media response
content type, missing/invalid evidence, traversal/root escape, and the absence of raw
paths or remote URLs.

### 33.3 Static source-contract tests

Because the current frontend has no JavaScript test infrastructure and Phase 11 forbids
framework/build-tool expansion, static source-contract tests remain an acceptable fallback
for structural guarantees, including:

- state-action wording;
- confirmation gating;
- escaped dynamic values;
- no direct provider invocation;
- no polling-triggered POST;
- modal accessibility attributes;
- demo/live labeling;
- hidden polling URL handling.

Static tests do not replace manual browser acceptance.

### 33.4 Manual UI acceptance checklist

The implementation review must manually verify at minimum:

- overview attention ordering;
- connected plus empty production state;
- demo versus live data labeling;
- production list scanning;
- production detail hierarchy;
- all six states;
- dependencies and blockers;
- Execute/Resume wording;
- active-run presentation;
- confirmation behavior and cancel side effects;
- failed-unit guidance;
- unit lifecycle hierarchy;
- bound-project navigation;
- media availability truthfulness;
- final video preview;
- scene storyboard/timeline where evidence exists;
- provider intent versus actual provider;
- cost unavailable behavior;
- publishing capability distinction;
- malformed and unavailable data states.

The manual checklist must record the viewport used for the desktop, tablet, and mobile
checks, and must verify the required priority order rather than relying on an unqualified
judgment that the page “looks coherent.”

### 33.5 Accessibility verification

Verify with keyboard-only operation and browser accessibility inspection:

- navigation;
- route focus;
- headings and landmarks;
- dialogs;
- focus trapping/return;
- Escape close;
- live/status announcements;
- state labels without color;
- button/link semantics;
- target sizes and contrast.

### 33.6 Responsive verification

Verify at representative desktop, laptop, tablet, and narrow mobile widths. Confirm that
identity, state, attention reason, safe action, and media remain understandable.

### 33.7 Media/artifact safety verification

Verify:

- valid existing final media previews;
- missing-path handling;
- path traversal rejection;
- no arbitrary file access;
- no absolute path leakage;
- scene evidence is not fabricated;
- no media processing is triggered by viewing.

### 33.8 Execution semantics verification

Verify:

- no command on page load;
- no command on refresh/polling;
- confirmation on every state-changing operation;
- no failed-unit retry fiction;
- no duplicate in-flight submission;
- no completed-unit re-execution;
- delegation remains through `ProductionExecutor`.

---

## 34. Implementation Boundary

**THIS DOCUMENT AUTHORIZES IMPLEMENTATION PLANNING ONLY.**

The specification itself does not authorize implementation during this task.

No code change, test change, UI change, backend change, dependency change, configuration
change, database change, or media-processing change is authorized merely because this file
exists.

Implementation may use only the accepted A/B/C scope from this document. Any D/E/F/G work
requires a separate written scope decision and, where applicable, a roadmap/specification
amendment before implementation.

---

## 35. Design Gate

Phase 11B implementation may begin **only after all of the following have occurred**:

1. This specification has been reviewed.
2. Phase 11B scope has been explicitly accepted.
3. The architectural authorities and no-fourth-authority rule have been accepted.
4. The information architecture and operator journey have been accepted.
5. The Execute/Resume and confirmation boundaries have been accepted.
6. Any conditional API additions have been specified exactly and accepted.
7. The acceptance criteria have been accepted.
8. The danger zones have been acknowledged.
9. No unresolved domain-authority conflict remains.
10. A separate Act-mode implementation task is issued.

The existence of this specification file, or its placement in the repository, is not an
implementation authorization.

---

## 36. Amendment Log

This document is the first Phase 11B specification record. Future changes require an
explicit documented amendment and review.

- **2026-09-02 — Post-audit remediation applied; specification remains awaiting
  independent re-audit and explicit freeze approval.**
  - Added the authoritative Execute/Resume presentation matrix and stale-render rule.
  - Defined the conditional project-detail binding-summary contract, including valid,
    unbound/legacy, malformed, mismatched, security, and test behavior.
  - Defined default-hidden `polling_url` transport handling and safe artifact-path rules.
  - Defined artifact-state precedence and conditional scene-media delivery behavior.
  - Added cross-cutting mandatory accessibility, loading, error, responsive, security,
    regression, and no-new-authority requirements without expanding P0/P1/P2 feature scope.
  - Tightened observable acceptance evidence for production identity, state treatment,
    relationships, lifecycle layout, and responsive priority.
  - Added explicit F-05/F-06 discovery traceability and the API/data-contract mismatch
    danger zone.
  - No implementation, application/source-code change, test change, dependency change,
    commit, or push is included or authorized by this remediation.

- **2026-09-02 — Phase 11B specification drafted / awaiting freeze approval.**
  - Created `PHASE_11B_COMMAND_CENTER_EXPERIENCE.md` as the proposed frozen engineering
    contract for Phase 11B — Command Center Experience.
  - Defined Phase 11B as a presentation-first refinement of the accepted Phase 11
    Personal Command Center.
  - Preserved Phase 9 intent authority, Phase 10 execution and progress authority,
    `ProjectManifest.production_binding` authority, existing artifact authority, and
    existing publishing authority.
  - Preserved the exact Phase 10 six-state vocabulary and explicit Execute/Resume,
    confirmation, polling, publishing, localhost, and no-new-durable-authority rules.
  - Defined the production-centric operator journey, information architecture, live/empty/
    demo/offline model, production/unit/media/artifact/provider/publishing presentation,
    accessibility, responsive behavior, P0/P1/P2 scope, classification rules, acceptance
    criteria, danger zones, and testing strategy.
  - Recorded that no mandatory API addition is approved at specification freeze; any
    required safe media/artifact/binding/run support remains conditional Class C work.
  - No implementation, source-code change, test change, dependency change, commit, or
    push is included or authorized by this entry.

---

**END OF PHASE 11B SPECIFICATION**