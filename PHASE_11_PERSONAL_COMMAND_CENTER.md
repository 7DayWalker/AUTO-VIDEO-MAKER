# PHASE 11 — PERSONAL COMMAND CENTER

Track: PERSONAL AVM MATURITY (visual/production roadmap; not the publishing track, whose "Phase 8A" is a separate, already-completed milestone).

FINAL SPECIFICATION — all architectural decisions recorded as finalized in the Amendment / Decision Record (Section 39).

## 1. Status

Specification / Design Gate — FROZEN PENDING FORMAL ACCEPTANCE.

This document is the authoritative Phase 11 architectural contract. Writing or freezing this document does **NOT** authorize implementation. Phase 11 implementation may begin only after: (1) this specification is reviewed; (2) it is explicitly accepted by the repository owner; and (3) a separate implementation task is issued. Until then: no production code, no tests, no scaffolding, no dependency changes, no configuration changes, no frontend tooling changes, no database, no migrations.

This document extends the frozen Phase 9 (`PHASE_9_DURABLE_PRODUCTION_PLAN.md`) and Phase 10 (`PHASE_10_PLAN_BACKED_EXECUTION.md`) contracts into the personal control / operator-inspection domain. It supersedes no frozen phase specification and grants no permission to modify Phase 1–10 behavior except through the additive, explicitly scoped mechanisms frozen here.

**Derivation:** This specification is derived from Phase 11 Discovery / Architectural Analysis (performed separately). Where discovery provides context, it is treated as evidence. Where this specification makes explicit decisions, those decisions override discovery recommendations unless a contradiction is impossible to resolve.

**Amendment record:** 2026-09-01 — architectural hardening amendment applied (pre-implementation audit). Amendments A–F and clarified sections integrated into primary specification (see Section 39 for amendment record). All decisions A1–A20 remain intact and unrenumbered.

---

## 2. Baseline

- **HEAD at specification time:** `eb31a68191c5ee76846c52fe9833c7c6b55c7881`
- **Last commit:** "Phase 10: implement plan-backed execution"
- **Phase 10:** COMPLETE — implementation `2dffee1`, specification freeze `ac0d648`, independent acceptance audit accepted
- **Phase 9:** COMPLETE — implementation `2dffee1`, specification freeze `ac0d648`
- **Phases 1–8:** COMPLETE
- **Working tree:** CLEAN
- **Authoritative roadmap:** `AVM_MASTER_ROADMAP.md` (Phase 11 = Personal Command Center, §4.8; autonomy Level 3→4 transition)
- **Phase 11 status at specification time:** NOT STARTED

---

## 3. Strategic Architectural Principle

### ONE AVM CORE WITH MULTIPLE CONTROL/PRODUCT SURFACES

**From `AVM_MASTER_ROADMAP.md` §1.1 (frozen):**

> **The long-term AVM architecture is ONE AVM CORE with multiple control/product surfaces — not separate personal and public production engines.**

The architecture is:

```
                         AVM CORE
                    (Phases 9–10 + Base)
                             │
              ┌──────────────┴──────────────┐
              │                             │
       PERSONAL CONTROL              PUBLIC CONTROL
         (Phase 11)                    (Future)
              │                             │
       Command Center              SaaS/Web UI
       Operator Dashboard          Customer UX
       Advanced/Deep Controls      Simplified Controls
              │                             │
              └──────────────┬──────────────┘
                             │
                     SAME AVM CORE
                    (One Execution Engine)
```

**Phase 11 Architectural Role:**

Phase 11 is the **Personal Control Plane** over the shared AVM Core.

It is **NOT**:
- A second execution engine
- A second state machine for production execution
- A second source of truth
- A replacement for Phase 10
- An automation system (that is Phase 12)
- A quality-intelligence layer (that is Phase 13)
- A provider-optimization layer (that is Phase 14)
- A recovery/fallback layer (that is Phase 15)

It is:
- An observation surface for the operator
- A control interface for human-initiated execution
- A dashboard for understanding production state
- A thin adapter over existing AVM Core capabilities

**Implications:**

1. All execution flows through the existing Phase 10 `ProductionExecutor`.
2. All durable state remains in the three authorities: INTENT (Phase 9), EXECUTION (Phase 10), BINDING (Phase 10).
3. The Command Center derives operator views from these authorities; it does not become a fourth authority.
4. The same AVM Core will later be controlled by a future public control surface.
5. Code written for Phase 11 must not leak personal-only assumptions into the Core.

---

## 4. Phase Objective

### Statement

Give the operator a proper, coherent command center for:

1. **Inspecting** what is currently executing
2. **Understanding** production progress and state
3. **Controlling** execution through explicit human commands
4. **Intervening** in production through legitimate Phase 10 operations

Not:

- Automatically continuing work (Phase 12)
- Ranking providers (Phase 14)
- Recovering from failures via fallback (Phase 15)
- Making quality decisions (Phase 13)
- Replacing operator judgment with automation

### Autonomy Level

```
LEVEL 2 (ACHIEVED):   AVM executes a defined workflow
LEVEL 3 (PHASE 10):   AVM executes a durable production plan ✅ COMPLETE
LEVEL 4 (PHASE 12):   AVM continues autonomously (unattended) ← NOT Phase 11
```

Phase 11 remains at LEVEL 3 (durable plan execution) but adds a **personal control interface** for the operator to direct, observe, and intervene in that execution.

---

## 5. Problem Statement

From Phase 10 discovery / repository evidence:

- Phase 10 executor (`ProductionExecutor`) is stateless and re-entrant; it can execute productions and derive progress on demand.
- Existing web layer (`app/web/server.py`) is thin and read-only, providing project inspection + publishing integration.
- **Existing gap:** No operator interface for production-level inspection, control, or understanding of multi-unit executions.
- **Example need:** To operate a 60-episode production, an operator must today invoke `produce.py --production <id>` via CLI and watch stdout, with no production-level dashboard, no unit detail, no failure diagnosis surface, no dependency visualization.

Phase 11 solves this by providing a **Personal Command Center** — a web-based dashboard and API that sits cleanly above the Phase 10 executor and provides deep operator visibility + control.

---

## 6. Phase 9 Contract (Authoritative Reference)

From `PHASE_9_DURABLE_PRODUCTION_PLAN.md`:

- **Production** is durable intent: what AVM intends to produce.
- **ProductionUnit** is the neutral workload primitive (not episode, though episode is optional metadata).
- **ProductionBible** is optional shared creative context.
- **Dependencies** are authoritative prerequisites; cycles are forbidden.
- **Schema versioning** (`schema_version`) is separate from **plan versioning** (`plan_version`).
- **No execution state enters the intent layer.** Intent is immutable (though revisable via `plan_version` bumps).

**Phase 11 must NOT:**
- Mutate `production.json` except through explicit, tracked revision mechanisms.
- Silently adopt legacy projects into productions.
- Invent new production-level fields or modify the Production model.

**Phase 11 may:**
- Load and inspect Production artifacts.
- Display production metadata.
- Enforce production intent constraints (e.g., dependencies).

---

## 7. Phase 10 Contract (Authoritative Reference)

From `PHASE_10_PLAN_BACKED_EXECUTION.md`:

- **ProductionExecutor** is the sole production-level execution composer.
- **Six-state vocabulary** (NOT_STARTED, BLOCKED, IN_FLIGHT, INCOMPLETE, COMPLETED, FAILED) is derived from execution evidence; never stored as durable state.
- **Re-invocation is the resume mechanism.** `executor.execute()` re-derives progress and continues eligible units.
- **Binding** connects a unit to an execution project. Binding discovery prevents duplicate execution.
- **Remote job protection:** in-flight OpenRouter/Flow jobs are never resubmitted on resume.
- **Reconciliation** is read-only by default. Repairs are only persisted on operator command.
- **No durable progress cache exists.** Progress is computed on demand from binding + manifest + reconciliation.
- **Version mismatch reporting:** a unit COMPLETED under an older `production_plan_version` is reported as informational; it does not change state.

**Phase 11 must NOT:**
- Bypass `ProductionExecutor` to invoke providers directly.
- Create a second execution state machine.
- Duplicate Phase 10 progress derivation; instead, invoke or reuse existing derivation.
- Silently repair manifests or mutate execution state.

**Phase 11 may:**
- Display the six-state vocabulary.
- Invoke `executor.execute()` / `executor.resume()` when operator commands do so.
- Read `executor.derive_progress()` to display production progress.
- Display binding information and plan pinning.
- Invoke reconciliation read-only.

---

## 8. Architectural Principles (Non-Negotiable)

### 8.1 One Execution Engine

All production execution flows through `ProductionExecutor` (Phase 10).

The Command Center never:
- Instantiates `ProductionOrchestrator` directly
- Invokes providers (Flow, OpenRouter, Gemini, etc.) directly
- Creates execution projects independently
- Mutates project manifests
- Makes scheduling decisions

### 8.2 Three Authorities, No Fourth (AMENDMENT A)

**Authority 1: INTENT**
- Location: `productions/` directory
- Content: Production, units, dependencies, Bible
- Mutability: Immutable (bumped via `plan_version`)
- Owner: Phase 9

**Authority 2: EXECUTION**
- Location: `output/projects/` directory + manifests + checkpoints
- Content: ProjectManifest, scene states, reconciliation evidence
- Mutability: Updated during execution; repairs via explicit operator action
- Owner: Phase 10 executor + existing pipeline

**Authority 3: BINDING**
- Location: Authoritative binding is persisted in `ProjectManifest.production_binding` (execution-side)
- Supporting evidence: Per-unit plan artifacts in `productions/<production_id>/units/<unit_id>/plan/` directory
- Content: Plan identity, version, content hash, unit/production references
- Mutability: Binding in manifest is immutable once persisted; plan artifacts are immutable once bound
- Owner: Phase 10 executor (on first unit execution)

**AMENDMENT A (Binding Authority) — FROZEN:**

The distinction between **binding evidence** and **binding authority** is critical:

- **Plan Artifacts** (`productions/<id>/units/<uid>/plan/`): These contain `production_plan.json`, `content_brief.json`, `plan_meta.json`. They are supporting evidence of what plan was used, but they are **NOT the authoritative unit→project binding.**
- **Execution Binding** (`output/projects/<project_id>/manifest.json`): The authoritative unit→project binding is persisted in `ProjectManifest.production_binding` on the execution side. This binding asserts which project is executing which unit.

**A binding exists if and only if:**
1. A loadable `ProjectManifest` exists in an execution project directory
2. That manifest contains a `production_binding` field
3. That binding references the production and unit
4. The binding has not been corrupted or orphaned

**A binding DOES NOT exist merely because:**
- Plan artifact directories exist
- `plan_meta.json` exists
- A project directory exists without a manifest
- A manifest exists without a production_binding field

**Binding Discovery** (from Phase 10 `ProductionExecutor.discover_bindings()`):
- Scans execution project directories for `ProjectManifest` files
- Validates manifest can be loaded (fails loudly if corrupted)
- Checks manifest.production_binding references the target production and a known unit
- Never guesses, adopts, or silently re-binds projects
- Never scans plan directories to infer bindings

**The Command Center must:**
- Respect this authority hierarchy
- Never infer bindings from plan artifacts alone
- Display binding information from the manifest source only
- Report binding mismatches loudly

**The Command Center must NOT:**
- Create or modify production_binding entries
- Invent bindings from directory presence
- Assume plan_meta.json establishes a binding
- Adopt unbound projects into productions

### 8.3 Derived State, Never Stored

The Command Center displays:

```
Derived View = INSPECT(Authority 1, 2, 3) + RECONCILIATION(Authority 2)
```

This view is computed on demand. Examples:

```
production_progress = executor.derive_progress()  // read-only, no cache

unit_state = (
    INSPECT(unit_binding) +
    INSPECT(project_manifest) +
    RECONCILE(project) +
    INTERPRET_via_six_state_vocabulary
)
```

Never persist this derived view.

### 8.4 Operator Initiation

All Phase 11 execution commands are **explicit operator actions.**

```
Operator clicks [Execute] (or API POST /execute)
  ↓
Command Center invokes executor.execute()
  ↓
Phase 10 executor runs
  ↓
Operator may poll for progress (optional presentation UX)
```

This is different from:

```
❌ Timer fires → automatic execution (Phase 12)
❌ Watcher detects state change → automatic action (Phase 12)
❌ Failed unit → automatic retry (Phase 12/15)
❌ Quota exhausted → automatic fallback (Phase 15)
```

### 8.5 Secrets Never Exposed

The Command Center never returns:
- API keys
- Credentials
- Tokens
- Authentication headers
- Environment variables containing secrets

If an operation requires a secret (e.g., Flow project URL), it remains in the server environment; the UI receives only a safe representation (or an indication that the secret is configured).

---

## 9. Command Center Role & Boundaries

### 9.1 What the Command Center IS

A web-based **observation and control surface** for the operator:

1. **Observation:** Production progress, unit states, dependencies, plan information, execution evidence, failure details
2. **Control:** Execute production, resume production, execute specific unit, resume specific unit
3. **Integration:** Wraps existing Phase 7A publishing, reconciliation, project inspection

### 9.2 What the Command Center IS NOT

- **Not an execution engine:** Phase 10 `ProductionExecutor` is the engine
- **Not a planner:** Phase 9 plans; Phase 10 binds
- **Not a scheduler:** That is Phase 12
- **Not a QC engine:** That is Phase 13
- **Not a provider optimizer:** That is Phase 14
- **Not a fallback/recovery engine:** That is Phase 15
- **Not a production editor:** Intent editing is future work (not Phase 11)
- **Not a database:** No new durable state authority

### 9.3 Core Interaction Model

```
Operator (Browser)
    │
    v
Phase 11 API (thin adapter)
    │
    ├─→ INSPECT (Authority 1, 2, 3) → return read-only views
    │
    └─→ EXECUTE (operator command)
           ↓
        ProductionExecutor.execute()
           ↓
        ProductionOrchestrator.run()
           ↓
        Existing AVM Core Engine
```

---

## 10. Existing Web Architecture Assessment

### 10.1 Current State (Verified from Repository)

**Backend (`app/web/server.py`):**
- Python stdlib `ThreadingHTTPServer` (no frameworks)
- Binds to `127.0.0.1` only (localhost)
- Thin JSON API over project manifests (read-only)
- Existing endpoints: `/api/projects`, `/api/connections`, `/api/publish`, `/media`, static SPA
- Design rules: NO publishing/OAuth logic here; connection endpoints run background threads; UI polls for status

**Frontend (`webapp/`):**
- Vanilla JavaScript SPA (no framework)
- `index.html` entry point
- Views: `dashboard.js`, `projects.js`, `project-detail.js`, `create.js`, `platforms.js`, `activity.js`, `settings.js`
- CSS: `base.css`, `ui.css`
- Components: `components.js`, `api.js`, `icons.js`, `demo-data.js`

**Existing Patterns (Reusable):**
- ✅ Manifest read-only pattern (→ extend to production loading)
- ✅ Reconciliation invocation (→ extend to progress derivation)
- ✅ Project listing (→ extend to production listing)
- ✅ SPA view routing (→ add production/unit views)
- ✅ Background thread for async operations (→ suitable for long-running execution)
- ✅ Polling pattern for status (→ suitable for execution progress)

### 10.2 Suitability for Phase 11

The existing web architecture is **suitable as a foundation** for Phase 11. No wholesale rewrite is needed.

**Why:**
- Existing patterns are proven and maintainable
- Stdlib HTTP server is lightweight
- Vanilla JS keeps dependencies minimal
- Localhost-only binding is appropriate for personal operator tool
- Thin API adapter pattern is reusable

**Constraints for Phase 11:**
- No new heavyweight frameworks required
- No new database required
- No new external dependencies required
- Extend existing patterns; don't replace them

---

## 11. Frontend Technology Decision (FROZEN)

### Decision A1: Vanilla JavaScript SPA Extension

**The Phase 11 frontend must extend the existing vanilla JavaScript SPA.**

Rationale:
- ✅ Existing infrastructure proven
- ✅ Minimal dependencies
- ✅ No build pipeline required
- ✅ Hermetic, auditable frontend
- ✅ Compatible with future public surface (no personal-only framework assumptions)
- ✅ Complexity of Phase 11 UI is modest (production dashboard, unit table, unit detail, project inspect)

**Scope boundaries:**
- Add new views: `production-list.js`, `production-detail.js`, `unit-detail.js`
- Extend existing `api.js` with production API calls
- Extend CSS for new layouts
- No new JavaScript frameworks (React, Vue, etc.)
- No new build pipeline (webpack, Vite, etc.)
- No npm dependencies unless exceptional justification

**If complexity reaches critical threshold (evidence-based):**
- A future phase may revisit this decision and introduce a framework
- But Phase 11 itself must NOT introduce one

---

## 12. Backend / Web Server Architecture Decision (FROZEN)

### Decision A2: Thin API Extension to app/web/server.py

**The Phase 11 backend must extend `app/web/server.py` with new routes.**

Rationale:
- ✅ Existing thin-adapter pattern proven
- ✅ Reuses existing project loading, manifest parsing, reconciliation invocation
- ✅ No new service layer abstractions required
- ✅ Single HTTP server keeps complexity low
- ✅ Localhost-only binding already established

**Architecture:**

```python
# app/web/server.py (extended)

class AVMHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # ... existing routes ...
        if path.startswith("/api/productions"):
            return self._handle_productions_api(path)
        if path.startswith("/api/projects"):
            return self._handle_projects_api(path)  # existing
        # ...
    
    def do_POST(self):
        # ... existing routes ...
        if path.startswith("/api/productions"):
            return self._handle_productions_post(path)
        # ...

def _handle_productions_api(self, path):
    # GET /api/productions
    # GET /api/productions/<id>
    # GET /api/productions/<id>/progress
    # GET /api/productions/<id>/units
    # GET /api/productions/<id>/units/<uid>
    ...

def _handle_productions_post(self, path):
    # POST /api/productions/<id>/execute
    # POST /api/productions/<id>/resume
    ...
```

**No separate service layer, no database, no ORM.**

---

## 13. Source-of-Truth Model (Frozen)

### Decision A3: Three Authorities + Derived View (No Fourth)

**The Command Center operates over three authoritative sources:**

```
Derived View = INSPECT(Authority 1, 2, 3) + RECONCILIATION(Authority 2)
```

**Authorities:**

1. **INTENT Authority:** `productions/production.json` — what AVM intends to produce
2. **EXECUTION Authority:** `output/projects/<project_id>/manifest.json` — current execution state, scene checkpoints, reconciliation
3. **BINDING Authority:** `ProjectManifest.production_binding` — which project is executing which unit

**Derived View (Command Center):**

```
derived_production_view = {
    production: load from Authority 1,
    units: for each unit {
        intent: from Authority 1,
        binding: from Authority 2 (via ProjectManifest.production_binding),
        execution_state: derive from Authority 2 + reconciliation,
        progress: six_state_vocabulary(execution_state)
    }
}
```

The derived view is **computed on demand, never persisted as durable state.**

---

## 14. Production Overview / Dashboard

### Capabilities

The Command Center shall provide a production overview showing:

- **Production list:** All productions in `productions/` directory
- **Production identity:** `production_id`, `title`
- **Command provenance:** Original command text, workload shape, intended unit count
- **Plan information:** Current `plan_version`, `created_at`, `revised_at`
- **Aggregate progress:** counts of units by six-state vocabulary (NOT_STARTED, BLOCKED, IN_FLIGHT, INCOMPLETE, COMPLETED, FAILED)
- **Quick actions:** [Execute], [Resume], [View Detail]

### Non-Capabilities

- ❌ Production creation (Phase 11 does not author productions)
- ❌ Production editing (future work)
- ❌ Bulk production operations (too risky; out of scope)

---

## 15. Production Detail View

### Capabilities

When an operator loads a specific production, the Command Center shall display:

**Production Metadata:**
- `production_id`, `title`, `plan_version`
- Original command text
- Created/revised timestamps
- Total unit count

**Unit Table:**
- Column: ordinal
- Column: unit_id
- Column: title
- Column: dependencies (human-readable list or count)
- Column: execution_state (six-state badge: ✓ COMPLETED, ⧖ IN_FLIGHT, etc.)
- Column: bound project_id (if bound)
- Column: quick actions

**Progress Summary:**
- Counts: 0 COMPLETED, 5 IN_FLIGHT, 10 INCOMPLETE, 2 BLOCKED, 1 FAILED, 42 NOT_STARTED
- Visual progress bar (optional)

**Quick Actions:**
- [Execute Production]
- [Resume Production]
- Unit row click → Unit Detail view

### Non-Capabilities

- ❌ Production editing
- ❌ Unit reordering
- ❌ Dependency editing
- ❌ Bulk unit operations

---

## 16. Unit Inspection & Detail View

### Capabilities

When an operator clicks a unit, the Command Center shall display:

**Unit Intent:**
- `unit_id`, `ordinal`, `title`, `description`, `premise`
- `unit_type` (episode, standalone_video, etc.)
- Episode metadata (season, episode_number, episode_title) if applicable
- Content intent (topic, duration, platform, etc.)
- Bible references (list of `element_id` references)
- Provider intent (image_motion, google_flow, openrouter_video, etc.)
- Dependencies (list of unit_ids that must complete first)

**Execution State:**
- Current state (one of six-state vocabulary)
- Bound project `project_id` (if started)
- Plan version when bound (`bound_plan_version`)
- Plan hash for version pinning (`content_hash`)
- Last update timestamp

**Bound Project Information (if bound):**
- Manifest snapshot: current stage, stage statuses
- Scene checkpoint table:
  - Scene ID
  - Provider used
  - Status (COMPLETED, FAILED, RUNNING, PENDING)
  - Cost (if available)
  - Timestamp
- Scene video paths (read-only display)
- Voice audio path
- Captions path
- Final video path (if completed)
- Publication status (YouTube, TikTok if present)

**Reconciliation Information (if available):**
- On-demand reconciliation report (read-only)
- Artifact statuses (content, voice, scenes, captions, assembly)
- Completion verdict

**Error / Failure Details (if failed or incomplete):**
- Failing stage (CONTENT, VOICE, SCENES, CAPTIONS, ASSEMBLY)
- Error message
- Provider information (if provider-related)
- Timestamp
- Actionable next steps (e.g., "Check provider status", "Review scene artifacts")

**Quick Actions:**
- [Execute Unit] (if NOT_STARTED or after dependencies complete)
- [Resume Unit] (if INCOMPLETE, IN_FLIGHT, or FAILED — reuses Phase 10 resume semantics)
- [View Production Plan] (if plan exists)
- [View Project Manifest] (if bound)
- [Run Reconciliation] (on-demand, read-only report)
- [View/Download Artifacts] (scene videos, final video, etc.)

### Non-Capabilities

- ❌ Unit editing
- ❌ Dependency modification
- ❌ Provider selection from UI (that is Phase 14)
- ❌ Automatic retry policy (that is Phase 12)
- ❌ Quality scoring (that is Phase 13)

---

## 17. Plan Inspection

### Capabilities

The Command Center may display:

- **Plan existence:** whether per-unit plan artifacts exist
- **Plan identity:** `plan_identity` from `plan_meta.json`
- **Plan version:** `production_plan_version` and `unit_plan_version`
- **Content hash:** `content_hash` for version pinning verification
- **Plan structure:** per-unit ProductionPlan (scenes, durations, content)
- **Plan loading:** ability to load and display the actual ProductionPlan JSON

### Non-Capabilities

- ❌ Plan generation (that is Phase 10 responsibility)
- ❌ Plan mutation (intent is immutable)
- ❌ Plan version selection (version pinning is automatic)

---

## 18. Progress Derivation & Six-State Vocabulary (AMENDMENT D)

### Decision A4: Reuse Phase 10 Progress Derivation (STRENGTHENED)

**AMENDMENT D (Progress Authority) — FROZEN:**

The Command Center shall **invoke `ProductionExecutor.derive_progress()`** to obtain unit states.

It shall **NOT**:
- Re-implement the six-state logic
- Maintain a separate state machine
- Cache progress (always re-derive on demand)
- Independently compute state classifications

**The hierarchy is absolute:**

1. **Phase 10 ProductionExecutor.derive_progress()** = Authoritative state classification
2. **Phase 11 API** = Presentation/transport adapter only
3. **Phase 11 UI** = Rendering only

If implementation discovers that the Phase 11 specification's explanatory table differs from actual Phase 10 behavior, implementation must follow Phase 10 behavior and report the discrepancy rather than silently creating a second interpretation.

The API should preferably expose the executor-derived state directly without transformation.

### Six-State Vocabulary (From Phase 10, Frozen)

| State | Meaning | Display | Actionability |
|---|---|---|---|
| **NOT_STARTED** | Unit has not yet been executed; all dependencies complete or unit has no dependencies | ◐ White badge | [Execute Unit] available |
| **BLOCKED** | Unit cannot start because at least one dependency is not COMPLETED | ⊗ Gray badge | Show blocking unit list; user must complete dependencies first |
| **IN_FLIGHT** | Execution is actively running (project manifest shows stage=RUNNING) or a remote job is in-flight (OpenRouter polling, Flow project) | ⧖ Blue badge | Wait or check remote job status; no [Retry] on in-flight jobs; [Resume Unit] respects Phase 10 remote-job protection |
| **INCOMPLETE** | Execution started but did not complete; project stage is not COMPLETE; reconciliation verdict is PROJECT_INCOMPLETE | ⊘ Yellow badge | [Resume Unit] or [Execute Production] (continue) available |
| **COMPLETED** | Reconciliation verdict = PROJECT_COMPLETE | ✓ Green badge | Final video playback; [Publish Unit] available (Phase 7A) |
| **FAILED** | Reconciliation verdict = PROJECT_FAILED or a stage status = FAILED | ✗ Red badge | Display failure details; [Resume Unit] respects Phase 10 semantics; diagnostic options |

**Important Note on Terminology:**

The six-state table uses presentation descriptions only. The actual state classification is whatever Phase 10's `ProductionExecutor.derive_progress()` returns. The table is for UI documentation purposes and must not become a second interpretation engine.

### Progress Display

Production-level aggregation:

```
Production Totals:
  ✓ COMPLETED: 42
  ⧖ IN_FLIGHT: 1
  ⊘ INCOMPLETE: 10
  ✗ FAILED: 2
  ⊗ BLOCKED: 2
  ◐ NOT_STARTED: 3
  ─────────────
    Total: 60
```

Progress bar (optional):

```
[████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░] 30/60 (50%)
```

---

## 19. Dependency Representation & Enforcement

### Decision A5: Display Dependencies, Trust Phase 10 Enforcement

The Command Center shall:

**Display:**
- Unit dependencies (list of unit_ids that must complete first)
- Blocking units (for BLOCKED units, show which dependencies are not COMPLETED)
- Dependency graph (optional: text representation, not required in MVP)

**Enforce:**
- Never bypass dependency checks (Phase 10 enforces these)
- If operator clicks "Execute Unit", Phase 10 will reject if dependencies not met
- Display warning: "Unit is BLOCKED by X, Y. Complete those first."

**NOT:**
- Allow operator to manually skip dependencies
- Create a UI-level dependency editor
- Reorder units (ordinal is immutable)

### Topological Ordering

Phase 10 spec §17 (frozen):

> DEPENDENCIES ARE AUTHORITATIVE PREREQUISITES. ORDINAL IS THE DETERMINISTIC ORDERING/TIE-BREAKING MECHANISM AMONG CURRENTLY ELIGIBLE UNITS.

The Command Center displays this correctly: a unit with ordinal=2 may remain BLOCKED if it depends on ordinal=5, until ordinal=5 completes. The UI respects this; no special handling needed.

---

## 20. Project Inspection & Scene Details

### Capabilities

The Command Center may display information about a bound project:

**Project Metadata:**
- `project_id` (execution project directory identity)
- `topic`
- `created_at` / `updated_at`
- Production binding (if bound)

**Stage Progress:**
- Current stage (CONTENT, VOICE, SCENES, CAPTIONS, ASSEMBLY, COMPLETE)
- Per-stage status and timing
- Stage errors (if any)

**Scene Checkpoints:**
- Table of scenes
- Scene ID
- Provider used (image_motion, google_flow, openrouter_video, gemini_omni, etc.)
- Status (COMPLETED, FAILED, RUNNING, PENDING)
- Cost (if available)
- Timestamp
- Video path

**Artifacts:**
- Content plan path
- Voice audio path
- Scene video paths
- Captions path
- Final video path (for completed projects)
- Download / view links (safe, relative paths)

**Publishing State:**
- YouTube publication record (if published)
- TikTok publication record (if published)
- Unpublished status (if not yet published)

**Reconciliation:**
- On-demand reconciliation report (read-only)
- Artifact validation results
- Completion verdict

### Non-Capabilities

- ❌ Project editing
- ❌ Manifest mutation
- ❌ Stage re-execution (use [Resume Unit] instead)
- ❌ Scene regeneration outside of unit execution

---

## 21. Execution Commands

### Decision A6: Only Explicit Operator-Initiated Commands

The Command Center exposes only these execution commands:

#### 21.1 Execute Production

```
POST /api/productions/<production_id>/execute

Request:
{
  "confirm": true
}

Response: 202 Accepted | 200 OK
{
  "status": "executing" or "complete",
  "production_id": "prd_20260901_series_001",
  "message": "..."
}
```

**Semantics:**
- Operator explicitly clicks [Execute Production]
- Command Center invokes `ProductionExecutor(production_id).execute()`
- Executor finds first eligible unit and executes it
- After unit completes, executor derives progress and continues to next eligible unit
- Loop until all units are COMPLETED or a FAILED/unrecoverable state blocks continuation

**Safeguards:**
- Confirmation dialog before execution
- Display: "Execute production XYZ (60 units total)?"
- Show first unit to be executed

#### 21.2 Resume Production

```
POST /api/productions/<production_id>/resume

Response: 202 Accepted | 200 OK
{
  "status": "executing" or "complete",
  "production_id": "prd_20260901_series_001",
  "message": "..."
}
```

**Semantics:**
- Same as Execute, but only continues eligible units; does NOT restart COMPLETED units
- Re-derives progress from disk (authorities)
- Skips any COMPLETED units
- Continues INCOMPLETE, IN_FLIGHT, or FAILED units (operator decides via explicit command)

#### 21.3 Execute Specific Unit

```
POST /api/productions/<production_id>/execute?unit_id=unit_5

Response: 202 Accepted | 200 OK | 400 Bad Request (if dependencies not met)
{
  "status": "...",
  "unit_id": "unit_5",
  "message": "..."
}
```

**Semantics:**
- Execute only the specified unit (if eligible)
- Phase 10 enforces dependencies; if not met, responds with error

#### 21.4 Resume Specific Unit

```
POST /api/productions/<production_id>/resume?unit_id=unit_5

Response: 202 Accepted | 200 OK
{
  "status": "...",
  "unit_id": "unit_5",
  "message": "..."
}
```

**Semantics:**
- Resume only the specified unit
- Useful for continuing a FAILED or INCOMPLETE unit

### Explicitly NOT Included (Out of Scope)

❌ **Skip Unit:** Operator cannot skip BLOCKED or FAILED units. Phase 12 automation may handle this later.

❌ **Cancel Execution:** Long-running execution cannot be easily canceled mid-unit. This is a Phase 12 concern.

❌ **Change Provider & Retry:** Provider selection is Phase 14. Retry uses the existing provider routing.

❌ **Force Provider:** Operator cannot override provider intent without editing production (future work).

❌ **Fallback/Automatic Retry on Failure:** That is Phase 15 recovery logic.

❌ **Publish All:** Publishing automation is Phase 12.

---

## 22. Confirmation Model (AMENDMENT F)

### Decision A7: Explicit Confirmations Required for State-Changing Operations

**AMENDMENT F (Confirmation Requirements) — FROZEN:**

Confirmation is **REQUIRED** before:
- Execute Production
- Resume Production
- Execute Unit
- Resume Unit
- Publish Unit

Confirmation is **NOT** required for:
- Read-only inspection (production list, unit detail, project manifest)
- Reconciliation report (read-only)
- Artifact viewing/downloading

**Confirmation dialog shows:**
- Operation description ("Execute production XYZ?", "Resume unit unit_5?")
- Estimated scope (unit count, duration if available)
- First action to be taken (first unit to execute)
- [Confirm] / [Cancel] buttons

**Implementation pattern:**

```javascript
// In Phase 11 API:
if (!request.get("confirm")) {
    return 400 Bad Request {
        error: "Confirmation required",
        message: "Confirm this operation before proceeding"
    }
}
// Proceed with execution
```

---

## 23. Publishing Adapter (AMENDMENT E)

### Decision A8: Thin Publishing Adapter Over Phase 7A

**AMENDMENT E (Publishing Adapter) — FROZEN:**

Phase 11 does NOT implement production-level publishing state or orchestration.

Phase 11 MAY provide a thin adapter that:

1. **Discovers** which units are COMPLETED and publishable
2. **Invokes** existing Phase 7A publishing on a per-project basis
3. **Reports** publishing status (from existing project manifest records)
4. **Never** implements batch publishing, scheduling, or automatic publishing

**Publishing flow:**

```
Unit is COMPLETED
  ↓
[Publish Unit] button available in UI
  ↓
Operator clicks [Publish Unit] for unit_5
  ↓
Command Center retrieves bound project_id
  ↓
Command Center invokes existing /api/publish endpoint (Phase 7A)
  ↓
Phase 7A handles publishing (YouTube, TikTok, etc.)
  ↓
Command Center displays result
```

**Publishing API:**

```
POST /api/productions/<production_id>/units/<unit_id>/publish

Request:
{
  "confirm": true,
  "platforms": ["youtube", "tiktok"]  // or empty → use project defaults
}

Response: 202 Accepted | 200 OK | 400 Bad Request (if not COMPLETED)
{
  "status": "publishing" | "published",
  "unit_id": "unit_5",
  "project_id": "...",
  "platforms": {...},
  "message": "..."
}
```

**Non-Capabilities:**

❌ Production-level publishing state (not stored)
❌ Batch publishing
❌ Publishing scheduling
❌ Automatic publishing on completion

---

## 24. Error Handling & Truthful Feedback

### Decision A9: Errors Are Truthful and Actionable

**Principle:**

The Command Center and API shall report errors truthfully. When an operation cannot proceed, the error message shall:

1. **State what failed** (e.g., "Unit unit_5 cannot execute: dependency unit_2 is not COMPLETED")
2. **Explain why** (e.g., "unit_2 has failed; resolve before continuing")
3. **Suggest action** (e.g., "Resume unit_2 or skip it in a future phase")

**Examples:**

```
400 Bad Request
{
  "error": "BLOCKED_DEPENDENCY",
  "unit_id": "unit_5",
  "blocking_units": ["unit_2", "unit_3"],
  "message": "Cannot execute unit_5: dependencies unit_2 and unit_3 are not COMPLETED. Complete them first or wait for Phase 12 automation."
}

500 Internal Server Error
{
  "error": "EXECUTOR_ERROR",
  "message": "ProductionExecutor raised exception: [details]. Check logs for full trace."
}

404 Not Found
{
  "error": "PRODUCTION_NOT_FOUND",
  "production_id": "prd_unknown",
  "message": "Production not found in productions/ directory. Verify the production_id and try again."
}
```

**Non-Capabilities:**

❌ Hiding errors from operator
❌ Silently retrying on transient failures
❌ Returning sanitized error messages that hide root cause
❌ Automatically falling back to another provider or approach

---

## 25. Legacy Project Compatibility

### Decision A10: No Automatic Adoption; Explicit Binding Only

**Principle:**

Phase 11 does NOT automatically adopt unbound legacy projects (projects created by Phase 1-8 that predate Phase 9/10 production model).

**Legacy project** = project in `output/projects/<project_id>/` created before Phase 10, without a `ProjectManifest.production_binding` field.

**Behavior:**

1. Legacy projects are **visible** via the existing project inspection API (Phase 7A)
2. Legacy projects are **NOT displayed** under production units
3. Legacy projects **cannot be resumed** as part of production execution
4. Adopting a legacy project into a production requires **explicit, manual action** (future Phase 11 enhancement or Phase 12 feature)

**Rationale:**

- Legacy projects may have incompatible manifest format or missing fields
- Automatic adoption risks corrupting execution semantics
- Operator must explicitly decide how to integrate legacy work
- Clean separation of concerns: legacy vs. production-managed projects

---

## 26. Retry Semantics (AMENDMENT B & C)

### Decision A11: Resume, Not Automatic Retry (STRENGTHENED)

**AMENDMENT B (Execution Lifetime) — FROZEN:**
**AMENDMENT C (Resume vs. Retry) — FROZEN:**

The Command Center does NOT implement automatic retry policy.

**Explicit Resume Terminology:**

Use **"Resume Unit"** and **"Resume Production"**, not "Retry".

```
Resume = re-invoke ProductionExecutor on an existing unit binding
        = respect Phase 10's remote-job protection (in-flight jobs not resubmitted)
        = compute progress and continue from where it left off

Retry = automatic re-execution policy (Phase 12 / Phase 15)
      = usually implies changing provider or approach on failure
      = NOT in Phase 11 scope
```

**Resume Behavior:**

1. Operator explicitly clicks [Resume Unit]
2. Phase 11 API invokes `ProductionExecutor.execute(production_id, unit_id=<unit_id>)`
3. Executor checks binding for the unit
4. Executor re-derives progress
5. If unit is INCOMPLETE: continue from where it left off
6. If unit is IN_FLIGHT: check remote job status (OpenRouter polling, Flow project); if complete, continue; if in-flight, wait
7. If unit is FAILED: report failure; continue only if operator's explicit action (via Resume)
8. If unit is COMPLETED: do not re-execute

**No "force retry" option without editing production intent (future work).**

---

## 27. Asynchronous Execution Lifetime (AMENDMENT B)

### Decision A12: Operator-Command-Owned Async Execution

**AMENDMENT B (Execution Lifetime) — FROZEN:**

**Execution Initiation:**

```
Operator clicks [Execute Production]
  ↓
UI sends POST /api/productions/<id>/execute with confirm=true
  ↓
Server receives request
  ↓
Server spawns background thread: executor.execute(production_id)
  ↓
Server responds: 202 Accepted | 200 OK (depending on sync/async completion speed)
  ↓
Client may begin polling: GET /api/productions/<id>/progress
```

**Long-Running Execution:**

If a single unit takes hours (e.g., OpenRouter video generation, Flow multi-scene project):

1. Server thread runs executor
2. Executor blocks until unit completes
3. Executor derives progress and continues to next unit
4. During this time, client polls progress endpoint
5. Progress endpoint returns current state (read-only view of three authorities)
6. When all units complete or error blocks continuation, executor thread exits
7. Next poll detects completion; UI updates

**No Polling Required (Alternative):**

If implementation chooses synchronous response:

```
POST /api/productions/<id>/execute
  → server executes synchronously (may take hours)
  → returns 200 OK with final status
```

This is allowed but not recommended for UX. Polling is preferred for operator visibility.

**Not Phase 12 Automation:**

This is NOT:
- ❌ A daemon/scheduler deciding to run on a timer
- ❌ A watcher service monitoring filesystem changes
- ❌ An automatic continuation system
- ❌ A queue-based execution model

This IS:
- ✅ A human operator initiating an execution command
- ✅ A background thread (ThreadingHTTPServer built-in) managing the execution
- ✅ Optional polling for progress visibility
- ✅ One execution per operator command

---

## 28. Secrets & Credential Handling

### Decision A13: Secrets Remain Server-Side

The Command Center API shall never expose:

- Provider API keys (OpenRouter, Flow, Gemini, etc.)
- Credentials (usernames, passwords)
- OAuth tokens
- Bearer tokens
- Environment variable contents

**Safe Representations:**

Instead, the API may return:

```json
{
  "providers": {
    "openrouter": {
      "configured": true,
      "last_used": "2026-09-01T14:32:00Z"
    },
    "google_flow": {
      "configured": true,
      "project_id": "flow-proj-123"  // safe, non-secret
    }
  }
}
```

**Server-Side Secrets:**

Secrets in `app/web/server.py` environment:

```python
import os

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")  # Secret, never exposed
FLOW_PROJECT_ID = os.getenv("FLOW_PROJECT_ID")        # Semi-secret
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")    # Secret, never exposed
```

When Phase 11 needs to invoke a provider or service, it uses these environment secrets internally. UI receives only "configured: true".

---

## 29. Personal/Public Boundary

### Decision A14: Localhost-Only, No Network Exposure

**Principle:**

Phase 11 Command Center is a **personal operator tool** designed for a single human at a single machine.

**Binding:**

- Backend binds to `127.0.0.1:8080` only (existing pattern from Phase 7A)
- Frontend is served from same server (SPA)
- No network interfaces exposed to LAN, Internet, or other machines
- No API key / authentication system required (trusted environment)

**Why:**

- Operator is on the same machine as AVM
- No multi-tenant concerns
- No public API contract required
- No rate limiting or auth overhead
- Security by network isolation

**For Future Public Surface (Phase 13+):**

- A separate, hardened API endpoint (possibly on different port)
- Authentication, authorization, rate limiting
- Careful credential handling
- Separate security audit

Phase 11 code must not leak personal-only assumptions (hardcoded localhost, no auth paths) into the Core or future public API.

---

## 30. Scope (FROZEN)

### What Phase 11 Delivers

1. ✅ **Production-level observation:** Inspect all productions and their unit states
2. ✅ **Progress derivation:** Display execution progress using Phase 10 vocabulary
3. ✅ **Unit detail inspection:** View unit intent, execution state, bound project, artifacts
4. ✅ **Explicit execution commands:** Execute / Resume production, Execute / Resume unit
5. ✅ **Reconciliation reporting:** On-demand, read-only project artifact validation
6. ✅ **Thin publishing adapter:** Invoke Phase 7A publishing from unit detail view
7. ✅ **Dependency visualization:** Display unit dependencies and blocking states
8. ✅ **Production dashboard:** Aggregate progress, quick-action buttons, production list
9. ✅ **Error reporting:** Truthful, actionable error messages
10. ✅ **Vanilla SPA + thin Python backend:** Extend existing `webapp/` and `app/web/server.py`

### What Phase 11 Does NOT Deliver

- ❌ Production creation or editing
- ❌ Production plan generation
- ❌ Provider selection / routing
- ❌ Automatic retries or fallback logic
- ❌ Quality intelligence (scoring, ranking)
- ❌ Scheduling or automation daemon
- ❌ Database for durable state (all state from disk authorities)
- ❌ Legacy project adoption
- ❌ Bulk operations (publish all, execute all patterns requiring schema changes)
- ❌ New secrets/OAuth integration
- ❌ Public API / multi-user support
- ❌ WebSocket or real-time push (polling is sufficient)

---

## 31. Non-Goals

### Phase 11 Is Not:

**Autonomy Layer (Phase 12):**
- Phase 11 does not implement automatic continuation, scheduling, or daemon behavior
- Operator initiates every action

**Quality Assurance (Phase 13):**
- Phase 11 does not score, rank, or compare video quality
- It displays execution state; QC is separate

**Provider Optimization (Phase 14):**
- Phase 11 does not select providers or route work based on cost/speed
- Provider selection remains in production intent

**Fallback / Recovery (Phase 15):**
- Phase 11 does not implement fallback logic, provider swaps, or intelligent retry
- It displays failure; recovery is operator-driven

**Production Editor (Future):**
- Phase 11 does not support editing productions, units, or dependencies
- Intent editing is out of scope

**Public Control Plane (Phase 13+):**
- Phase 11 is personal-only
- Public control plane will be a separate, hardened service

---

## 32. Technology Decision Record (FROZEN)

### A1: Vanilla JavaScript SPA Extension
- **Decision:** No new frameworks; extend existing SPA
- **Rationale:** Proven, minimal dependencies, auditable
- **Locked In:** Yes

### A2: Thin API Extension to app/web/server.py
- **Decision:** No new service layer; extend existing Python HTTP server
- **Rationale:** Reuses patterns, keeps complexity low
- **Locked In:** Yes

### A3: Three Authorities + Derived View (No Fourth)
- **Decision:** INTENT, EXECUTION, BINDING authorities only; derived views computed on demand
- **Rationale:** Respects Phase 9/10 contracts; no new state authority
- **Locked In:** Yes

### A4: Reuse Phase 10 derive_progress()
- **Decision:** Invoke existing progress derivation; never reimplement six-state logic
- **Rationale:** Single source of truth; respect Phase 10 authority
- **Locked In:** Yes (AMENDMENT D)

### A5: Display Dependencies, Trust Phase 10 Enforcement
- **Decision:** UI shows dependencies; Phase 10 enforces them
- **Rationale:** Clean separation; no duplicate validation in UI
- **Locked In:** Yes

### A6: Only Explicit Operator-Initiated Commands
- **Decision:** Execute / Resume production/unit only; no automation
- **Rationale:** Phase 11 is personal control, not autonomy
- **Locked In:** Yes

### A7: Explicit Confirmations Required
- **Decision:** Confirm before Execute/Resume/Publish; not required for read-only
- **Rationale:** Prevent accidental operations on long-running production
- **Locked In:** Yes (AMENDMENT F)

### A8: Thin Publishing Adapter Over Phase 7A
- **Decision:** Discover COMPLETED units and invoke existing /api/publish
- **Rationale:** No new publishing state; reuse existing publishing
- **Locked In:** Yes (AMENDMENT E)

### A9: Errors Are Truthful and Actionable
- **Decision:** Error messages explain what failed and suggest next steps
- **Rationale:** Operator-facing tool; debugging requires transparency
- **Locked In:** Yes

### A10: No Automatic Adoption of Legacy Projects
- **Decision:** Legacy projects visible but not in production unit list; no automatic binding
- **Rationale:** Safety; manual adoption prevents data corruption
- **Locked In:** Yes

### A11: Resume, Not Automatic Retry (STRENGTHENED)
- **Decision:** Use "Resume Unit" terminology; respect Phase 10 remote-job protection; no auto-retry policy
- **Rationale:** Phase 11 is not Phase 12; phase separation clarity
- **Rationale:** Phase 10 handles job status correctly; do not duplicate
- **Locked In:** Yes (AMENDMENT B & C)

### A12: Operator-Command-Owned Async Execution
- **Decision:** POST /execute spawns background thread; 202 Accepted / 200 OK; optional polling
- **Rationale:** Long-running units require async; polling provides visibility
- **Locked In:** Yes (AMENDMENT B)

### A13: Secrets Remain Server-Side
- **Decision:** Environment secrets never exposed in API; safe representations only
- **Rationale:** Credential security; trusted environment
- **Locked In:** Yes

### A14: Localhost-Only, No Network Exposure
- **Decision:** Bind to 127.0.0.1 only; no auth required; personal tool
- **Rationale:** Personal tool is single-operator, single-machine
- **Locked In:** Yes

### A15: Binding Authority is execution-side manifest (AMENDMENT A)
- **Decision:** ProjectManifest.production_binding is authoritative; plan artifacts are evidence only
- **Rationale:** Prevents silent binding adoption; clear execution authority
- **Locked In:** Yes (AMENDMENT A)

### A16: Progress Derivation (AMENDMENT D Strengthened)
- **Decision:** ProductionExecutor.derive_progress() is sole authority; no second state machine
- **Rationale:** One engine, one authority; reimplement prevents correctness
- **Locked In:** Yes (AMENDMENT D)

### A17: Production-Level Publishing (AMENDMENT E)
- **Decision:** No production publishing state; thin adapter only
- **Rationale:** Reuse Phase 7A; prevent double-publishing, schedule bugs
- **Locked In:** Yes (AMENDMENT E)

### A18: Confirmation Model (AMENDMENT F)
- **Decision:** Confirm before Execute/Resume/Publish; not for read-only
- **Rationale:** Prevent accidental operations
- **Locked In:** Yes (AMENDMENT F)

### A19: No Database
- **Decision:** All state from disk authorities (productions/, output/projects/)
- **Rationale:** Hermetic, auditable, respects Phase 9/10 contracts
- **Locked In:** Yes

### A20: Legacy Compatibility (from Phase 9/10 contracts)
- **Decision:** Respect all Phase 9/10 semantics; extend, do not replace
- **Rationale:** One AVM Core; Phase 11 is thin adapter only
- **Locked In:** Yes

---

## 33. Persistence & Authority Model (FROZEN)

### No New Durable State Authority

Phase 11 introduces **ZERO** new durable state authorities.

All state is read-only with respect to Phase 11:

```
Phase 11 READ-ONLY VIEWS:

productions/production.json          ← Phase 9 (INTENT Authority)
productions/production.json          ← Phase 10 updates on revision (if allowed)
output/projects/<id>/manifest.json   ← Phase 10 (EXECUTION Authority)
output/projects/<id>/checkpoints/    ← Phase 10 checkpoints
productions/<id>/units/<uid>/plan/   ← Phase 10 (bindings + plans)
```

Phase 11 MAY **INVOKE** but **NEVER PERSISTS:**

```
executor.execute()     → re-derives progress from disk
executor.derive_progress()  → reads authorities, returns computed state
executor.reconcile()   → reports on-demand, never stored
```

### On-Demand Derivation (Golden Path)

```python
# Phase 11 API handler for GET /api/productions/<id>/progress

def get_production_progress(production_id):
    # Load intent (Authority 1)
    production = load_production(production_id)
    
    # Load execution state + bindings (Authorities 2, 3)
    executor = ProductionExecutor(production_id)
    
    # Derive progress (no cache)
    progress = executor.derive_progress()
    
    # Return to UI (never persisted)
    return {
        "production_id": production_id,
        "production": production,
        "progress": progress,
        "derived_at": now()
    }

# Each call to this endpoint re-derives from disk; no progress cache.
```

### Why No Database?

From Phase 9/10 contracts (frozen):

1. **Hermetic Execution:** All evidence is on disk; execution is reproducible
2. **Operator Auditability:** Operator can inspect any part of any production by reading files
3. **No Schema Migrations:** No upgrade burden; no data corruption risk
4. **Simplicity:** Single source of truth per authority
5. **Phase 9/10 Contracts:** Explicitly forbid hidden state in databases

Phase 11 respects this contract absolutely.

---

## 34. Test Strategy (FROZEN)

### Phase 11 Implementation Must Include

**Unit Tests (Python backend):**
- Production loading from disk
- Production.units enumeration
- Binding discovery (manifest-based only, not plan-based)
- Progress derivation invocation (mock executor.derive_progress())
- API endpoint responses (JSON shape, HTTP codes)
- Error handling (dependency blocking, missing production, etc.)
- Confirmation requirement validation
- Legacy project filtering

**Integration Tests (Backend + Executor):**
- Execute production with mock units (small, fast)
- Resume production with mock units
- Progress polling behavior
- Unit binding discovery
- Phase 10 executor contract compliance (do not corrupt bindings, respect remote-job protection)

**UI Tests (Vanilla JS):**
- Production list rendering
- Production detail rendering (unit table, progress summary)
- Unit detail rendering (intent, execution state, project info)
- Confirmation dialogs
- API error handling (display error messages truthfully)
- Polling logic (detect completion, update UI)

**No Database Tests:**
- Zero tests that assume or create a database

**No New Framework Tests:**
- Tests use existing test patterns from codebase

### Phase 11 Implementation Must NOT Include

- ❌ Performance benchmarks (not required; personal tool)
- ❌ Load testing (single operator; not multi-user)
- ❌ WebSocket tests (polling only)
- ❌ OAuth/auth tests (localhost trusted environment)
- ❌ Database migration tests (no database)
- ❌ Multi-instance tests (single server)

### Test Execution

Tests run via existing test command (e.g., `pytest app/`, `npm test`). Phase 11 does not introduce new test infrastructure.

---

## 35. Acceptance Criteria (50 Functional + Architectural)

### A. Production Inspection (5)

1. **PROD-001:** Command Center loads all productions from `productions/` directory and displays them in a list with id, title, unit count, current plan version
2. **PROD-002:** Clicking a production in the list navigates to production detail view
3. **PROD-003:** Production detail view displays all units in a table with: ordinal, id, title, dependencies (count or list), current state badge
4. **PROD-004:** Production detail view displays aggregate progress: counts of units by six-state vocabulary
5. **PROD-005:** Production detail view provides quick-action buttons: [Execute Production], [Resume Production], [View Unit Detail]

### B. Unit Inspection (6)

6. **UNIT-001:** Clicking a unit row navigates to unit detail view
7. **UNIT-002:** Unit detail displays unit intent: id, ordinal, title, description, unit_type, episode metadata (if episode), provider intent, dependencies
8. **UNIT-003:** Unit detail displays execution state: current six-state badge, bound project_id, bound plan version, content_hash
9. **UNIT-004:** Unit detail displays bound project information: manifest snapshot, stage progress, scene checkpoint table, artifacts (paths)
10. **UNIT-005:** Unit detail displays on-demand reconciliation report (read-only)
11. **UNIT-006:** Unit detail displays error/failure details if state is FAILED or INCOMPLETE (stage, error message, provider info, timestamp)

### C. Progress Derivation (4)

12. **PROG-001:** GET /api/productions/<id>/progress invokes executor.derive_progress() and returns six-state counts
13. **PROG-002:** Progress derivation is re-computed on every API call (no cache)
14. **PROG-003:** Six-state vocabulary is exactly what Phase 10 executor returns (NOT_STARTED, BLOCKED, IN_FLIGHT, INCOMPLETE, COMPLETED, FAILED)
15. **PROG-004:** Progress view never persists derived state to disk or database

### D. Binding Authority (5)

16. **BIND-001:** Binding discovery scans execution project manifests for production_binding field (not plan directories)
17. **BIND-002:** A unit is considered bound if and only if ProjectManifest.production_binding references the production and unit
18. **BIND-003:** Plan artifacts in productions/<id>/units/<uid>/plan/ are displayed as supporting evidence but never as binding authority
19. **BIND-004:** Plan directory presence does NOT establish a binding
20. **BIND-005:** Unbound units display "Not Started" or "No Project" state; bound units display bound project_id

### E. Execution Commands (8)

21. **EXEC-001:** POST /api/productions/<id>/execute with confirm=true spawns background execution and returns 202 Accepted or 200 OK
22. **EXEC-002:** POST /api/productions/<id>/execute without confirm returns 400 Bad Request with "Confirmation required" error
23. **EXEC-003:** Execute Production invokes executor.execute(production_id), which begins execution of first eligible unit
24. **EXEC-004:** Execute Production loops, continuing to next eligible unit after each completes
25. **EXEC-005:** Execute Production stops when all units are COMPLETED or a FAILED unit blocks continuation
26. **EXEC-006:** POST /api/productions/<id>/resume with confirm=true skips COMPLETED units and continues INCOMPLETE/IN_FLIGHT/FAILED units
27. **EXEC-007:** POST /api/productions/<id>/execute?unit_id=<uid> executes only the specified unit (if eligible)
28. **EXEC-008:** POST /api/productions/<id>/resume?unit_id=<uid> resumes only the specified unit

### F. Dependency Enforcement (3)

29. **DEP-001:** Units with unsatisfied dependencies display as BLOCKED with list of blocking units
30. **DEP-002:** Attempting to execute a BLOCKED unit returns 400 Bad Request with blocking unit list
31. **DEP-003:** Phase 10 executor enforces dependencies; Command Center displays them, never duplicates enforcement logic

### G. Confirmation Model (3)

32. **CONF-001:** UI displays confirmation dialog before Execute Production, Resume Production, Execute Unit, Resume Unit
33. **CONF-002:** Confirmation dialog shows operation description, scope (unit count), and first action
34. **CONF-003:** Confirmation is NOT required for read-only inspection, reconciliation, or artifact viewing

### H. Publishing Adapter (2)

35. **PUB-001:** POST /api/productions/<id>/units/<uid>/publish with confirm=true invokes existing /api/publish for that project
36. **PUB-002:** Publish Unit requires unit to be in COMPLETED state; returns 400 Bad Request if not

### I. Error Handling (4)

37. **ERR-001:** All error responses include error code, message, and actionable suggestion
38. **ERR-002:** Dependency blocking error shows blocking unit list
39. **ERR-003:** Executor errors are reported truthfully with exception details
40. **ERR-004:** API returns 404 for missing production, 400 for invalid unit, 500 for executor exceptions

### J. Legacy Compatibility (2)

41. **LEG-001:** Unbound legacy projects are visible via /api/projects but not in production unit lists
42. **LEG-002:** Unbound legacy projects cannot be resumed as part of production execution

### K. Asynchronous Execution (2)

43. **ASYNC-001:** Long-running unit execution does not block HTTP server; client may poll /api/productions/<id>/progress
44. **ASYNC-002:** Polling returns 200 OK with current progress; no 202 Accepted on polling (only on initial POST /execute)

### L. Secrets & Security (2)

45. **SEC-001:** API never returns provider API keys, tokens, credentials, or environment variables
46. **SEC-002:** Safe representations (e.g., "configured: true", "project_id: flow-123") are returned instead of secrets

### M. Schema & Compatibility (2)

47. **COMPAT-001:** Phase 11 API does not modify production.json, unit definitions, or binding manifests
48. **COMPAT-002:** Phase 11 API respects all Phase 9/10 semantics (no silent adoptions, no manifest rewrites)

### N. Architectural (5)

49. **ARCH-001:** All execution flows through ProductionExecutor (Phase 10); Command Center never invokes providers directly
50. **ARCH-002:** No new durable state authority; all state from disk (productions/, output/projects/)
51. **ARCH-003:** Three authorities only (INTENT, EXECUTION, BINDING); no fourth authority
52. **ARCH-004:** Vanilla JavaScript SPA + thin Python API adapter; no new frameworks or service layers
53. **ARCH-005:** Localhost-only binding (127.0.0.1); no network exposure or auth system

---

## 36. Danger Zones (14 Explicitly Prohibited Actions)

These actions are **FORBIDDEN** in Phase 11 implementation:

1. ❌ **Modifying production.json intent:** Phase 11 is read-only on production intent; editing is future work
2. ❌ **Creating a second execution engine:** All execution must flow through Phase 10 ProductionExecutor
3. ❌ **Implementing automatic retry logic:** Retry / fallback is Phase 12/15 responsibility
4. ❌ **Creating a durable state database:** All state must remain on disk (productions/, output/projects/)
5. ❌ **Silently adopting legacy projects:** Manual binding only; no automatic adoption
6. ❌ **Re-implementing six-state progress logic:** Always invoke Phase 10 derive_progress(); never duplicate
7. ❌ **Bypassing dependency enforcement:** Phase 10 enforces; Phase 11 displays
8. ❌ **Exposing secrets in API responses:** No API keys, tokens, credentials; safe representations only
9. ❌ **Creating new service layers or microservices:** Thin Python adapter only
10. ❌ **Introducing heavyweight frameworks:** No React, Vue, Node.js cluster, etc.
11. ❌ **Implementing batch/bulk operations:** Operations are per-unit or per-production, one at a time
12. ❌ **Creating network-exposed endpoints:** Localhost-only binding (127.0.0.1)
13. ❌ **Implementing scheduling or automation daemons:** Operator initiates all actions
14. ❌ **Modifying ProjectManifest.production_binding outside of Phase 10:** Binding is Phase 10 authority

---

## 37. Design Gate & Implementation Readiness

### Specification Status: FROZEN

This specification is complete and ready for review.

**No changes to this specification are permitted without explicit written approval from the repository owner.**

### Before Implementation Can Begin:

1. ✅ **Specification Review:** Repository owner reviews this frozen specification
2. ✅ **Explicit Acceptance:** Repository owner confirms specification is acceptable
3. ✅ **Separate Implementation Authorization:** A distinct task is issued authorizing Phase 11 implementation
4. ⏳ **Implementation Phase:** Only after (1), (2), and (3) above, Phase 11 implementation begins

### During Implementation:

- Implementation must adhere to all decisions A1–A20 (frozen)
- All six hardening amendments (A–F) must be respected
- All 50 acceptance criteria must be validated
- All 14 danger zones must be avoided
- Tests must pass before implementation is considered complete
- Code review must verify contract compliance

### Implementation is NOT Authorized by This Specification

**This document freezes the architecture and requirements. It does NOT grant permission to implement. Implementation requires a separate, explicit authorization task.**

---

## 38. References

### Frozen Specifications (Precedent)

- **Phase 9:** `PHASE_9_DURABLE_PRODUCTION_PLAN.md` — Production intent model, schema versioning, dependency model
- **Phase 10:** `PHASE_10_PLAN_BACKED_EXECUTION.md` — ProductionExecutor, six-state vocabulary, binding discovery, reconciliation
- **Phase 7A:** Existing publishing integration (implicit reference; formal spec not reviewed here)
- **AVM Master Roadmap:** `AVM_MASTER_ROADMAP.md` §1.1 (One AVM Core principle), §4.8 (Phase 11 = Personal Command Center)

### Repository Structure (Implicit Contracts)

- `productions/` — Production intent authority (Phase 9)
- `output/projects/` — Execution authority (Phase 10)
- `app/web/server.py` — Existing thin API server (Phase 7A+)
- `webapp/` — Existing vanilla JavaScript SPA (Phase 7A+)

### Code References (Existing)

- `app/orchestrator/production_execution.py` — ProductionExecutor class (import for execution)
- `app/web/server.py` — BaseHTTPRequestHandler (extend with new routes)
- `webapp/components.js` — Existing UI components (reuse patterns)
- `webapp/api.js` — Existing API client (extend with production routes)

---

## 39. Amendment & Decision Record (Frozen)

### Original Decisions: A1–A20

All 20 original architectural decisions (A1 through A20) are listed in Section 32 (Technology Decision Record) and remain **LOCKED IN**. No modifications to A1–A20 are permitted.

### Six Hardening Amendments (Pre-Implementation Audit)

#### AMENDMENT A: Binding Authority Clarity (2026-09-01)

**Issue:** Distinction between binding evidence (plan artifacts) and binding authority (manifest) was implicit; needed explicit clarification to prevent silent adoption bugs.

**Resolution:** Frozen explicit rule (Section 8.2):
- **Binding Authority** = `ProjectManifest.production_binding` (execution-side manifest field)
- **Binding Evidence** = `productions/<id>/units/<uid>/plan/` artifacts (supporting documentation, not binding)
- Binding exists if and only if manifest.production_binding references the production and unit
- Binding discovery scans manifests, never infers from directory presence
- Decision A15 (locked in Section 32)

**Implementation Impact:** Binding discovery code must validate this strictly; tests must verify no silent adoption.

#### AMENDMENT B: Execution Lifetime (2026-09-01)

**Issue:** Async execution model needed clarification; avoid conflating operator-initiated command execution with Phase 12 automation/scheduling.

**Resolution:** Frozen explicit rule (Sections 27, 26):
- Operator clicks [Execute] → background thread starts → returns 202/200 → client polls (optional)
- This is operator-command-owned async, NOT daemon/scheduler/watcher/automation
- Synchronous response (blocking HTTP) is allowed alternative but not preferred
- Polling is optional presentation pattern; Phase 11 does not require real-time push
- Decision A12 (locked in Section 32)

**Implementation Impact:** Threading model must be clear in code; async pattern must not leak "automatic" assumptions.

#### AMENDMENT C: Resume vs. Retry Terminology (2026-09-01)

**Issue:** "Retry" implies automatic retry policy; "Resume" clarifies explicit operator action.

**Resolution:** Frozen terminology rule (Section 26):
- Always use "Resume Unit" / "Resume Production", never "Retry"
- Resume = re-invoke executor on existing binding, respecting Phase 10 remote-job protection
- Retry = automatic retry policy (Phase 12/15 feature, not Phase 11)
- Decision A11 (locked in Section 32)

**Implementation Impact:** UI labels, API documentation, error messages must use "Resume" only.

#### AMENDMENT D: Progress Derivation Authority (STRENGTHENED) (2026-09-01)

**Issue:** Risk of re-implementing six-state logic; need absolute hierarchy to prevent second state machine.

**Resolution:** Frozen absolute rule (Section 18):
- Phase 10 `ProductionExecutor.derive_progress()` is sole authority
- Phase 11 invokes it; never reimplements state classification
- If specification table (Section 18) differs from actual Phase 10 behavior, follow Phase 10
- No progress caching; always re-derive on-demand
- Decision A4 (locked in Section 32), Decision A16 (locked in Section 32)

**Implementation Impact:** No independent progress state machine; import and invoke executor.derive_progress() directly.

#### AMENDMENT E: Production-Level Publishing (2026-09-01)

**Issue:** Publishing state machines and batch operations are Phase 12+ features; Phase 11 should be thin adapter only.

**Resolution:** Frozen explicit rule (Section 23):
- No production-level publishing state stored
- Phase 11 discovers COMPLETED units and invokes existing Phase 7A `/api/publish`
- No batch publishing, scheduling, or automatic publishing
- Publishing is per-unit, on-demand, operator-initiated
- Decision A8 (locked in Section 32), Decision A17 (locked in Section 32)

**Implementation Impact:** Publishing adapter is thin bridge to existing Phase 7A; no new state machine.

#### AMENDMENT F: Confirmation Model (2026-09-01)

**Issue:** Long-running operations (Execute Production on 60 units) risk accidental initiation; need explicit safeguard.

**Resolution:** Frozen confirmation rule (Section 22):
- Confirmation REQUIRED before: Execute Production, Resume Production, Execute Unit, Resume Unit, Publish Unit
- Confirmation NOT required for: read-only inspection, reconciliation, artifact viewing
- Confirmation dialog shows operation description, scope (unit count), first action
- Decision A7 (locked in Section 32), Decision A18 (locked in Section 32)

**Implementation Impact:** All state-changing endpoints must check `confirm` parameter; UI must show confirmation dialog.

### Amendment Application

These six amendments (A–F) are **integrated into the primary specification** (Sections 8.2, 18, 22, 23, 26, 27) and do not introduce new sections. They represent pre-implementation hardening of frozen decisions A1–A20.

**No additional amendments are permitted without explicit written approval.**

---

## 40. Closing Statement

### What This Specification Provides

This document is the complete, frozen architectural contract for Phase 11 — Personal Command Center. It contains:

- **Clear Problem:** Operator needs production-level observation and control (§5)
- **Explicit Principles:** Three authorities, one engine, derived views, operator initiation (§8)
- **Architectural Decisions:** A1–A20, all locked in (§32)
- **Hardening Amendments:** A–F, integrating pre-implementation insights (§39)
- **API Contracts:** Exact endpoints, request/response shapes, HTTP codes (§21–23)
- **UI Capabilities:** What users see and can do (§14–20)
- **Authority Model:** INTENT, EXECUTION, BINDING; no fourth authority (§13, §33)
- **50 Acceptance Criteria:** Measurable completion definition (§35)
- **14 Danger Zones:** Explicitly forbidden actions (§36)

### What This Specification Does NOT Provide

❌ Implementation code
❌ Test code
❌ Frontend markup or styling
❌ Database schema or migrations
❌ Dependency list or package.json changes
❌ Configuration files or deployment scripts
❌ Authorization to implement

### Implementation Authorization

**Implementation of Phase 11 is NOT PERMITTED based on this specification alone.**

Implementation may begin only after:

1. Repository owner reviews this frozen specification
2. Repository owner explicitly accepts this specification
3. A separate implementation authorization task is issued
4. Both (2) and (3) are completed

Until then: **No production code, no tests, no scaffolding, no changes.**

### Specification Integrity

This specification is **FROZEN**. Any changes require explicit written approval from the repository owner.

All decisions A1–A20 are locked. All amendments A–F are integrated and locked. The 50 acceptance criteria are final. The 14 danger zones are absolute.

---

END OF SPECIFICATION
