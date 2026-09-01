# PHASE 10 - PLAN-BACKED EXECUTION

Track: PERSONAL AVM MATURITY (visual/production roadmap; not the publishing track, whose "Phase 8A" is a separate, already-completed milestone).

FINAL SPECIFICATION - all architectural decisions are recorded as finalized in the Amendment / Decision Record (Section 38).

## 1. Status

Specification / Design Gate - FROZEN PENDING FORMAL ACCEPTANCE.

This document is the authoritative Phase 10 architectural contract. Writing or freezing this document does **NOT** authorize implementation. Phase 10 implementation may begin only after: (1) this specification is reviewed; (2) it is explicitly accepted by the repository owner; and (3) a separate implementation task is issued. Until then: no production code, no tests, no scaffolding, no dependency changes, no configuration changes, no database, no UI.

This document extends the frozen Phase 9 contract (`PHASE_9_DURABLE_PRODUCTION_PLAN.md`) into execution. It supersedes no frozen phase specification and grants no permission to modify Phase 1-9 behavior except through the additive, explicitly scoped mechanisms frozen here.

**Amendment record:** 2026-08-31 - architectural hardening amendment applied (pre-implementation audit). Decisions A27-A31 appended; Sections 8, 11, 12, 15, 17, 19, 30, 33, 34, 35, 36, 38 clarified. A1-A26 and all 38 sections remain intact and unrenumbered.

## 2. Baseline

- HEAD at specification time: `ac0d648` ("docs: freeze Phase 9 durable production plan specification")
- Phase 9: COMPLETE - implementation `2dffee1`, 60-unit stress coverage `a90330d`, specification freeze `ac0d648`
- Phase 8: FORMALLY ACCEPTED (ancestry `3f70255` -> `c10132d` -> `fd51a4c`)
- Phases 1-4, 5A-5J, 6A-6F, 7A-7E: COMPLETE
- Working tree: CLEAN
- Test baseline: `python -m pytest -q tests` -> 672 passed, 2 warnings (pre-existing Pydantic deprecations)
- Authoritative roadmap: `AVM_MASTER_ROADMAP.md` (Phase 10 = Plan-Backed Execution, Section 4.7; autonomy Level 3, Section 9)
- Phase 10 implementation status: NOT STARTED

## 3. Phase Objective

Make the durable Production plan the authoritative input to execution.

Phase 9 created the durable intent layer. Phase 10 establishes how that intent becomes executable through the existing AVM production engine, raising AVM from autonomy Level 2 to Level 3:

    LEVEL 2 (achieved):  AVM can execute a defined production workflow (single-project pipeline)
    LEVEL 3 (Phase 10):  AVM can execute a durable production plan

    PHASE 9:   "What do we intend to produce?"
    PHASE 10:  "Execute the plan."

AVM must be able to:

- know what remains to be produced
- know what has already been completed
- execute a durable Production
- resume production after interruption
- maintain production-level execution state (derived, never stored in intent)
- distinguish planned work from completed work
- support single-video, batch, and multi-episode workloads identically
- connect Production Units to existing AVM project executions
- execute against exact immutable plan versions

## 4. Problem Statement

Repository evidence (Phase 10 discovery, verified at HEAD `ac0d648`):

- The Phase 9 intent layer (`app/production/`) is durable, validated, versioned - and completely decoupled: no execution module imports it. Nothing can execute a Production.
- The existing engine is strictly single-project: `produce.py` -> `OrchestratorConfig` -> `ProductionOrchestrator.run()` executes one video per invocation (`app/orchestrator/pipeline.py:61-173`).
- Nothing execution-side references a production. `ProjectManifest` (`app/orchestrator/models.py:154-204`) carries no production/unit fields. After a crash, no durable artifact can answer: which production was running? which units have execution projects? which unit is recoverable? which units have not started?
- Per-unit `ProductionPlan` generation is deliberately deferred to Phase 10 (Phase 9 Section 13, decision A4). A `ProductionUnit`'s intent currently has no path to a `ContentBrief`/`ProductionPlan`.
- `provider_intent = google_flow` cannot become `visual_default_provider` (forbidden by `app/orchestrator/models.py:469-482`); Flow is reachable only through explicit per-scene routing, which requires scene IDs - which exist only after a plan exists.
- Existing per-project resume and reconciliation are complete (`app/orchestrator/pipeline.py:236-328`; `app/orchestrator/reconciliation.py:344-412`) but have no production-level composition: nothing can derive "what remains" across the units of a Production.

Phase 10 closes exactly these gaps - without building a second production engine, without changing provider contracts, and without contaminating intent with execution state.

## 5. Phase 9 Contract (inherited authority)

Phase 10 inherits and must preserve the frozen Phase 9 contract:

- **Schema** (`app/production/models.py`): `Production`, `ProductionCommand`, `ProductionBible`/`BibleElement`, `ProductionUnit`, `EpisodeMetadata`, `PlanReference`; `schema_name = "avm.production"`, `SUPPORTED_SCHEMA_VERSION = 1`; provider intent is name-level only (`PROVIDER_INTENT_NAMES`, `PROVIDER_INTENT_DEFAULT`).
- **Storage** (`app/production/storage.py`): `productions/<production_id>/production.json`; immutable `versions/v<NNN>/` snapshots; atomic writes (temporary file + `os.replace`); monotonic single-step revisions; unit identities never removed; `created_at` provenance immutable; `units/<unit_id>/plan/` pre-created as the declared Phase 10 binding target (empty in Phase 9).
- **Validation** (`app/production/validation.py`): schema compatibility, recursive execution-state-key and secret-key exclusion scans, resolved plan-reference hash integrity; loud failure on any violation.
- **Boundary ownership** (Phase 9 Section 26): Phase 10 owns making the plan authoritative for execution, per-unit plan generation, unit-to-project connection, what-remains derivation, production-level resume/progress, and execution ordering.
- **Invariants** (Phase 9 Sections 14, 20, 21, 22): intent/execution separation; execution binds against an immutable plan version, never into it; `ProjectManifest`/scene checkpoints remain the execution-state authority; `productions/` never holds execution state.

## 6. Architectural Principles (non-negotiable)

1. **ONE AVM PRODUCTION ENGINE.** The existing `ProductionOrchestrator` remains the only video production engine. Phase 10 adds a production-level orchestration/binding layer around it. No second engine.
2. **INTENT != EXECUTION.** The Production artifact is immutable during execution. Execution state lives exclusively in existing execution artifacts plus the additive execution-side binding defined here.
3. **ONE MECHANISM FOR ALL SHAPES.** Single-video, batch, and episodic Productions execute through one executor. Episode metadata is data, never architecture.
4. **DERIVED EXECUTION STATE.** "What remains" is always derived from durable evidence. It is never stored on units and never becomes a second source of truth.
5. **EXACT VERSION PINNING.** Every execution binds to an exact production plan version and exact per-unit plan bytes (content hash). Later revisions never rewrite what was executed.
6. **ORCHESTRATION-OWNED ROUTING.** Provider intent is translated into existing config inputs only. Routing precedence, persistence, and the provider factory remain exactly as in Phases 6E/8.
7. **PROVIDER CONTRACTS PRESERVED.** Phase 7A (scene output), 7D (audio), 7E (OpenRouter), and 8 (scene execution boundary) contracts are inputs, not targets.
8. **CRASH-SAFE RE-ENTRANCY.** Every executor invocation re-derives all state from durable artifacts. No process-local authority. Running the command again is the resume mechanism.
9. **LEGACY COMPATIBILITY.** Phase 1-8 projects load, reconcile, resume, and complete unchanged, without migration.
10. **PERSONAL/PUBLIC-NEUTRAL CORE.** The executor is AVM Core: identity-based, provider-neutral, secret-free, machine-independent, portable.
11. **NO SPECULATIVE INFRASTRUCTURE.** No database, queues, workers, schedulers, message bus, cloud services, or new provider abstraction.
12. **SMALLEST ADDITIVE MECHANISM.** Every change to existing modules is additive and backward compatible. No opportunistic refactoring.

## 7. Core Architecture

    DURABLE INTENT (Phase 9, unchanged)
    Production  (production.json + immutable versions/)
        |  read-only
        v
    PRODUCTION EXECUTOR  (NEW - execution layer)
        1. load + validate Production
        2. derive "what remains" (bindings x manifests x reconciliation)
        3. select next actionable unit (dependency-respecting order)
        4. lazy per-unit plan generation (existing ScenePlanner)
        5. translate unit intent -> OrchestratorConfig (incl. routing inputs)
        6. record execution binding (additive ProjectManifest fields)
        7. run existing ProductionOrchestrator   <-- THE ENGINE, UNCHANGED
        8. repeat / report; re-entrant at every step
        |
        v
    EXISTING PROJECT EXECUTION
    content -> voice -> scenes (routing -> provider boundary) -> captions -> assembly
        |
        v
    EXISTING RECONCILIATION (PROJECT_COMPLETE gate)
        |
        v
    EXISTING FINAL ARTIFACT (+ existing per-project publishing, unchanged)

The executor is an **orchestration/binding layer**, not an engine. It never generates media, never invokes visual providers, never constructs provider objects, and never touches publishing state machines.

**Module placement (frozen constraint):** the executor and the unit-to-brief adapter live in the execution layer (proposed: `app/orchestrator/production_execution.py`, with adapter helpers in the same module or a sibling). They MUST NOT live in `app/production/` - that package is intent-only by frozen Phase 9 declaration. `app/production/` may gain only additive read/write helpers that preserve its guarantees (atomicity, immutability, validation); its models, identity, and validation semantics remain untouched.

## 8. Production -> Unit -> Plan -> Project Model

### 8.1 Identity table (frozen)

| Identity | Meaning | Allocated by | Lives in | Mutable |
|---|---|---|---|---|
| `production_id` | durable workload/intent identity (`prd_YYYYMMDD_<slug>_<NNN>`) | Phase 9 identity (collision-safe, `app/production/identity.py`) | `productions/<id>/` | never |
| `unit_id` | one production unit's intent identity | Production author (Phase 9) | Production artifact | never (revisions may add units, never remove) |
| `project_id` | one actual execution project's identity | existing engine (`generate_project_id`, `app/content/models.py:283-303`) | `output/projects/<id>/` | never |

These identities are **never conflated**. `production_id` never replaces `project_id`; the execution directory structure is not reorganized around productions; legacy project identity is untouched.

### 8.2 Mapping rule (frozen)

**ONE PRODUCTION UNIT -> ONE EXISTING PROJECT EXECUTION.**

Each executable `ProductionUnit` that produces a video maps to exactly one existing AVM project execution. The relationship is established by the execution binding (Section 15) and is strictly 1:1:

- one unit may bind at most one project - a second binding for an already-bound unit is a loud failure;
- one project may bind at most one unit - a manifest carrying a binding for unit X never serves unit Y;
- once a unit has a bound project, its execution continues exclusively through that project (existing resume); the executor never creates a second project for a bound unit.

### 8.3 Known architectural fact: project-id replacement quirk (documented debt - do NOT fix)

Existing behavior (verified at HEAD `ac0d648`): `_initialize_project` allocates `project_id` A and creates `output/projects/A/` (`app/orchestrator/pipeline.py:211-234`). The CONTENT stage may then replace `manifest.project_id` with the plan's own identity B (`app/orchestrator/pipeline.py:397-401`; the plan identity is LLM-provided or freshly generated - `app/content/generator.py:216-218`). The physical project directory remains A; `--resume` resolves by directory identity A; content/scene artifacts nest under A.

Phase 10 does NOT modify this behavior. The **execution binding records the project DIRECTORY identity** (the initial project_id A) - the durable execution identity that resume accepts. Because the binding is written at project initialization and is immutable, it is unaffected by the later manifest identity replacement. Remediation of this quirk is explicitly deferred (decision A20).

### 8.4 Project directory identity is the execution binding target (frozen - decision A29)

**THE PROJECT DIRECTORY IDENTITY IS THE EXECUTION/BINDING TARGET FOR PHASE 10.** The four identity concepts are strictly distinguished:

| Concept | Definition | Role in Phase 10 |
|---|---|---|
| `production_id` | workload-level intent identity | names the Production; never a project identity |
| `unit_id` | stable workload-unit intent identity | names the unit; never a project identity |
| **project directory identity** | the directory name under the configured execution root (`output/projects/<name>/`) allocated by the engine at initialization | THE execution project identity; the value the binding records; the value `--resume` resolves |
| `manifest.project_id` | existing legacy manifest field | mutable by the existing engine (Section 8.3 quirk); preserved as-is; NOT the binding source |

**Phase 10 must never assume `manifest.project_id == project directory name`** - that equality is not guaranteed by the existing engine (Section 8.3).

The binding field `project_id` is a **captured execution identity assertion**: the directory name as allocated by the engine at initialization time, recorded once into the initial manifest, never re-derived and never rewritten. It is not read back from `manifest.project_id` and is not separately represented elsewhere.

Deterministic resolution rule (no absolute paths, no guessing):

    production + unit -> binding (manifest scan) -> binding.project_id (captured directory identity assertion)
                      -> output/projects/<binding.project_id>/  (resolved against the configured execution root)

Explicitly forbidden: using `production_id` as a `project_id`; replacing or repairing the project_id architecture or the legacy ID mismatch; scanning arbitrary project directories and guessing a binding (bindings exist only where a loadable manifest asserts them - Section 15.5); using absolute filesystem paths as durable identity; assuming `manifest.project_id` names the directory.

## 9. Production Executor

### 9.1 Entry point (minimal surface)

`produce.py` gains a production execution mode:

    python produce.py --production <production_id> [--unit <unit_id>] [existing engine flags]

- `--production <id>`: load, derive state, and execute/continue the Production. Re-invoking the same command is the resume/continuation mechanism.
- `--unit <unit_id>` (optional): restrict the invocation to one declared unit (operator control). The unit must belong to the Production; otherwise loud failure.
- All existing engine flags (`--mock`, `--dry-run`, visual configuration, Flow URL, OpenRouter flags) keep their existing meaning and feed the existing engine as today. Mock mode makes full production execution hermetic.

A read-only status report (derived progress) is printed on every invocation before execution begins. A separate status-only command is permitted but not required.

Default `produce.py` behavior (single video without `--production`) is unchanged.

### 9.2 Per-invocation algorithm (frozen)

1. **Load intent.** `load_production(production_id)` (validated; fails loudly). Defensive re-validation of dependency acyclicity (Phase 9 already rejects cycles at model level - `app/production/models.py:401-423`).
2. **Discover bindings.** Scan execution project manifests for bindings referencing this `production_id` (the manifest is the binding authority - Section 15).
3. **Derive unit states.** For each unit, derive its execution state (Section 16) from binding + project evidence + `reconcile_project` (read-only).
4. **Determine the actionable set.** NOT_STARTED (not blocked), IN_FLIGHT, and INCOMPLETE units whose declared dependencies are all COMPLETED.
5. **Execute in deterministic order** (Section 17). For each unit:
   a. ensure a per-unit plan artifact exists for the current production plan version (Section 12);
   b. build `OrchestratorConfig` from the adapter + translation rules (Sections 10, 21);
   c. NOT_STARTED unit: initialize a new project through the engine, binding recorded at initialization; IN_FLIGHT/INCOMPLETE unit: resume the bound project through the engine's existing resume path, validating binding consistency;
   d. run `ProductionOrchestrator.run()` to completion, truthful failure, or operator interruption.
6. **Stop conditions.** The invocation stops when: all units are COMPLETED (production complete); a unit fails truthfully (stop-on-first-failure - Section 9.4); or the operator interrupts (state is durable; re-invoke to continue).
7. **Report.** Print the derived production progress (Section 16) truthfully.

The executor holds no authoritative state in memory across invocations. Every step is re-derivable from durable artifacts.

### 9.3 Concurrency discipline (frozen)

One production is executed by one process at a time (the same discipline as existing single-project execution - enforced by `PipelineStage` RUNNING crash markers, not by locks). Running two executors against the same production concurrently is operator error; the engine's crash-marker semantics make the state recoverable, but Phase 10 adds no distributed locking, daemons, or schedulers.

### 9.4 Failure semantics (frozen)

- **Stop-on-first-failure.** A unit's truthful failure (plan generation failure, engine failure, terminal provider failure) stops the invocation with a truthful report. Independent units are NOT auto-continued within the same invocation.
- **Re-invocation continues.** A fresh invocation re-derives state: the failed unit is FAILED (never auto-retried); remaining actionable units proceed.
- **No retry/fallback/substitution policy is added.** Existing bounded engine behavior (for example, the content generator's existing LLM attempt loop, `app/content/generator.py:51+`) is preserved unchanged. A failed unit is retried only by an explicit operator decision (for example, resuming the bound project with existing `--resume` semantics, or issuing a deliberate new intent revision).

## 10. Unit -> ContentBrief Adapter

Phase 10 introduces a **pure adapter function**: `ProductionUnit intent (+ Production defaults) -> ContentBrief`. It is a mapping, not a planner.

### 10.1 Mapping table (frozen)

| ContentBrief field | Source | Notes |
|---|---|---|
| `topic` | `unit.title` (episode_title may refine it via planning context, Section 11) | `ContentBrief.topic` requires 3-500 chars (`app/content/models.py:34`) |
| `target_duration` | `unit.content_intent["target_duration"]` if present, else the command-level default recorded by the adapter, else the existing engine default | seconds |
| `number_of_scenes` | `unit.content_intent["number_of_scenes"]` if present, else engine default | must satisfy 2-20 (`app/content/models.py:52-57`) |
| `niche`, `target_audience`, `tone`, `style`, `platform`, `language`, `content_goal`, `call_to_action` | `unit.content_intent` recognized keys, falling back to operator/runtime defaults | existing semantics unchanged |
| `production_context` (NEW, additive) | assembled by the adapter from `unit` + Production (Section 11) | `Optional[Dict[str, Any]] = None` |

**Recognized `content_intent` keys (frozen set):** `target_duration`, `number_of_scenes`, `platform`, `language`, `tone`, `style`, `target_audience`, `niche`, `content_goal`, `call_to_action`, `topic_override`. Unknown keys are ignored and reported in the executor log (`content_intent` is an open dict by Phase 9 design; unknown keys never silently influence planning).

### 10.2 Validation rules (frozen)

- The adapter output must pass `ContentBrief` validation unchanged. **No silent clamping**: a unit requesting 25 scenes or a 400-second video fails loudly at plan-generation time for that unit (truthful failure, Section 9.4).
- The adapter is deterministic for a given (unit intent, production defaults) pair - re-deriving the brief for the same intent version yields identical brief fields.
- The adapter performs no I/O beyond reading the Production artifact; it never invokes providers and never mutates intent.

## 11. Production Bible Integration

### 11.1 Invariants (frozen)

- The Production Bible remains **optional** (Phase 9 decision A5) and **canonical** - a single copy inside the Production artifact.
- The Bible is never copied into units, never copied into per-unit plan artifacts, and never copied into bindings. Units reference it by `bible_refs` (element IDs).
- The `EXECUTION_STATE_KEYS` / `SECRET_STATE_KEYS` scans (`app/production/validation.py:33-101`) continue to apply to the Production artifact; Bible and planning-context data contain no execution state by construction.

### 11.2 Mechanism (frozen - decision A10)

The smallest truthful mechanism that lets the existing planner see Bible context without duplication:

1. `ContentBrief` gains ONE additive optional field: `production_context: Optional[Dict[str, Any]] = None` (`app/content/models.py`). Default `None` -> legacy behavior is unchanged at the software-contract level (plain Pydantic model; existing consumers unaffected; exact compatibility definition in Section 11.4).
2. `OrchestratorConfig` gains the same additive optional field and threads it through `to_content_brief_dict()` (`app/orchestrator/models.py:434-452`) so the brief reaches the engine through the existing path.
3. The adapter assembles `production_context` as a bounded, JSON-serializable planning payload derived from: production title, unit identity (unit_id, unit_type, ordinal), `unit.premise` / `unit.description`, `unit.episode` metadata when present, and the **resolved Bible slices** for `unit.bible_refs` - rendered as `{ "<section>": [element content, ...] }`. Only referenced elements are resolved; the full Bible is never embedded wholesale.
4. The existing prompt construction gains ONE additive conditional section: when `production_context` is present and non-empty, a deterministic "PRODUCTION CONTEXT" block is appended to the planner prompt; when absent, the prompt construction produces exactly the same prompt text as today (software-contract level; the stochastic LLM output itself is not guaranteed byte-identical and need not be - Section 11.4). This is an additive prompt extension, not a planner redesign - `ScenePlanner` and `ContentGenerator` remain fully responsible for planning (`app/content/planner.py:24-148`, `app/content/generator.py`).
5. `production_context` is transient planning input: it is not stored on the unit, not embedded in the `ProductionPlan` (beyond normal creative output), and not stored in bindings. (It may appear in the per-project `content_config` snapshot via the existing `to_content_brief_dict()` path - that snapshot is execution-side provenance and is explicitly permitted.)

### 11.3 Episodic continuity boundary

Episode metadata (`season_number`, `episode_number`, `episode_title`, `continuity_refs`) is passed to the planner as data inside `production_context`. Phase 10 does NOT implement continuity intelligence (cross-episode character/canon reasoning remains a future-phase concern, consistent with Phase 9 Section 12).

### 11.4 Planner "unchanged" clarification and legacy ContentBrief compatibility (frozen - decision A30)

Wherever this specification says the planner remains "unchanged", the precise meaning is:

**THE EXISTING PLANNING ARCHITECTURE AND THE `ProductionPlan` CONTRACT REMAIN INTACT.** Only the minimum additive capability required for production-derived context (Section 11.2) and exact supplied-plan execution (Section 12.4) may be introduced. "Unchanged" does NOT mean "no source line in the content planner may ever be touched"; it means: no second planner, no replacement planner, no new planning engine, no redesign of `ProductionPlan`, no provider-specific planning architecture, and no silent behavioral change for legacy `ContentBrief` users.

Exact backward compatibility (frozen):

- **If `production_context` is absent/`None`:** the additive field is inert. The planner prompt construction excludes the context block entirely, producing the same prompt text as the existing planning path; plan validation and the `ProductionPlan` schema behave exactly as today. **Legacy regression requirement:** a legacy `ContentBrief` with no `production_context` must produce behavior compatible with the existing planning path at the software-contract level (same inputs, same code paths, same validation, same prompt construction). Byte-for-byte equivalence of generated LLM output is NOT promised - stochastic providers may differ - and is not required.
- **If `production_context` is present:** the planner receives the additional production-derived context exclusively through the additive conditional mechanism of Section 11.2. No other behavior changes.

The Bible flow is exactly:

    Production Bible -> resolve only the unit's bible_refs -> create the unit's production context
                     -> pass through the additive production_context mechanism -> existing planner

The Bible MUST NOT become: a second source of truth, an execution-state store, a continuity engine, a typed story-management system, an AI director, a quality-control system, or a provider-routing engine. Phase 10 may transport and expose Bible context to planning; it does NOT interpret, enforce, score, or autonomously manage continuity.

## 12. Per-Unit Plan Generation

### 12.1 Rule (frozen - decisions A8, A22)

Per-unit `ProductionPlan` generation is **lazy**: a unit's plan is generated the first time the unit becomes actionable, not when the Production is created. Generation reuses the existing planner - planning architecture and `ProductionPlan` contract intact, with only the additive capabilities frozen in Sections 11.4 and 12.4:

    ProductionUnit intent -> Unit-to-ContentBrief adapter (Section 10)
                           -> existing ScenePlanner (app/content/planner.py)
                           -> existing ProductionPlan (app/content/models.py)
                           -> per-unit plan artifacts (below)

Provider selection for planning reuses the existing engine semantics (mock provider under `--mock`; the existing default LLM provider otherwise), so plan generation is fully hermetic under mock mode.

### 12.2 Plan artifacts (frozen locations)

    productions/<production_id>/units/<unit_id>/plan/
        production_plan.json   # the per-unit ProductionPlan (canonical intent artifact)
        content_brief.json     # the exact brief that generated the plan (provenance + validation input)
        plan_meta.json         # { production_id, unit_id, production_plan_version, content_hash, generated_at }

- The canonical plan file follows the existing serialization convention (`json.dumps(model_dump(), indent=2, ensure_ascii=False)` - same as `ScenePlanner.plan_and_save`, `app/content/planner.py:61-102`).
- `content_hash` is the SHA-256 of the canonical `production_plan.json` bytes, computed with the existing content-hash pattern (`app/production/storage.py`).
- All three files are **intent-natured planning artifacts** (the plan artifact itself is intent - Phase 9 Section 13). They contain no execution state.
- All writes are **atomic** (temporary file + `os.replace`, the proven Phase 9 pattern).

### 12.3 Generate / reuse / regenerate discipline (frozen)

For a unit becoming actionable:

- **No plan artifacts** -> generate (adapter -> ScenePlanner) and write the three artifacts atomically.
- **Artifacts exist for the CURRENT production plan version** -> reuse; never regenerate; never overwrite.
- **Artifacts exist for an OLDER production plan version** -> if the unit has NO execution binding (never executed): regenerate atomically (supersede) so the current intent is what executes; the superseded bytes are not preserved in Phase 10 (no execution was ever bound to them). If the unit HAS a binding: never regenerate - the bound project continues with the plan it already has (pinning, Section 20).
- **Binding exists but canonical plan artifacts are missing or hash-mismatched** -> loud data-integrity failure (Section 15.4). No silent regeneration.

### 12.4 Consumption by the engine (frozen - decision A23)

The engine must execute the **exact pre-generated plan** - the CONTENT stage must not generate a second plan. Additive mechanism:

- `OrchestratorConfig` gains an additive optional supplied-plan pointer (semantic field: the canonical per-unit plan artifact path). When set, the CONTENT stage (`app/orchestrator/pipeline.py:337-435`) loads that plan, validates it against the brief built from config (existing plan validation), persists it byte-identically into the project's content directory following the existing path convention (`content/<plan_project_id>/production_plan.json`), and proceeds exactly as today - including the existing manifest identity replacement quirk (Section 8.3), which is preserved unchanged.
- When the pointer is absent, the CONTENT stage behaves exactly as today (plan generated in-stage). Legacy single-video runs are unaffected.

**Exact supplied-plan execution invariant (frozen - decision A27):**

> **FOR A PRODUCTION-BACKED UNIT, ONCE A PLAN HAS BEEN RESOLVED AND PINNED, THE EXISTING EXECUTION ENGINE MUST EXECUTE THAT EXACT PLAN. THE NORMAL PLANNER MUST NOT BE INVOKED A SECOND TIME FOR THAT BOUND UNIT.**

It must be impossible for Phase 10 to: generate plan A for a unit, persist plan A, start `ProductionOrchestrator`, cause CONTENT to generate plan B, and execute plan B. That would invalidate exact-plan binding (A6) and is explicitly forbidden - "generate another plan and hope it matches" is not an acceptable mechanism under any circumstances.

The supplied-plan path is an **additive execution seam whose sole purpose is to prevent the CONTENT stage from regenerating a different plan**. For a production-backed unit it is the ONLY authoritative CONTENT-stage plan source. The two phases are strictly distinguished:

    PLAN GENERATION (once, executor-owned, before engine execution):
        Production Unit -> Unit-to-ContentBrief adapter -> existing ScenePlanner
                        -> exact ProductionPlan -> persist + hash plan -> pin plan identity/version/hash

    PLAN EXECUTION (existing engine, supplied plan):
        existing ProductionOrchestrator executes the supplied exact ProductionPlan
        -> existing downstream stages (voice, scenes, captions, assembly)

Full chain (frozen):

    Production Unit -> Unit-to-ContentBrief adapter -> existing ScenePlanner -> exact ProductionPlan
                    -> persist/hash plan -> pin plan identity/version/hash
                    -> existing ProductionOrchestrator executes supplied plan -> existing downstream stages

This designs no second planner, duplicates no `ProductionPlan` schema, bypasses no existing `ProductionOrchestrator` behavior, and changes nothing for legacy single-video runs (pointer absent -> in-stage planning exactly as today).

### 12.5 Plan generation failure

If `ScenePlanner` fails, no plan artifacts are written (atomic writes only on success), no binding exists, and the unit remains NOT_STARTED. The failure surfaces truthfully and the invocation stops (Section 9.4). A fresh operator invocation naturally re-attempts generation - this is new-invocation semantics, not an automatic retry policy.

## 13. Plan Reference / Binding Discipline

- `PlanReference` (`app/production/models.py:190-206`) remains **optional intent-side metadata**: it may point at the per-unit plan artifact (`relative_location`, `referenced_plan_identity`, `referenced_plan_version`, `content_hash`) and is verified by the existing Phase 9 hash check when resolved.
- `PlanReference` is **NOT an execution progress mechanism**. Execution progress is recorded exclusively by the execution-side binding (Section 15). Phase 10 does NOT create Production revisions to flip `PlanReference.status` as units execute.
- **No revision churn (frozen):** execution MUST NOT create Production revisions to mark units planned/executed/resolved. A 60-unit Production does not become 60 revisions. Production revisions occur only through the deliberate Phase 9 revision mechanism, driven by intent changes.

## 14. Intent vs Execution Separation (extended invariant)

**Forbidden in the Production artifact (enforced by existing Phase 9 scans, preserved):** execution status, stage state, scene checkpoints, remote job identities, artifact paths, timestamps of execution, provider credentials, Flow URLs, retry counters, progress percentages, "last error" fields.

**Where Phase 10 execution state lives (frozen):**

| Execution fact | Durable home |
|---|---|
| unit-to-project binding, plan pinning | additive `ProjectManifest` binding fields (Section 15) |
| stage/scene/caption/assembly progress | existing manifest stages + scene checkpoints (unchanged) |
| completion verdict | existing `reconcile_project` (unchanged) |
| "what remains" for a Production | derived on demand (Section 16) - never stored |

## 15. Execution Binding Model

### 15.1 Location and fields (frozen - decisions A7, A21)

The binding is implemented as **additive optional fields on `ProjectManifest`** (`app/orchestrator/models.py:154-204`), all defaulting to `None`, written from additive `OrchestratorConfig` fields at project initialization:

| Binding field | Meaning |
|---|---|
| `production_id` | the Production being executed |
| `production_plan_version` | the Production's `plan_version` executed against |
| `unit_id` | the unit this project executes |
| `unit_plan_identity` | the per-unit `ProductionPlan`'s own identity (`plan.project_id`, e.g. B in Section 8.3) |
| `unit_plan_version` | per-unit plan artifact revision; Phase 10 plans are single-revision, fixed at `1` (the field exists so future per-unit plan revisions stay representable) |
| `unit_plan_content_hash` | SHA-256 of the canonical per-unit plan artifact bytes |
| `project_id` | the project DIRECTORY identity (initial project_id A - Section 8.3); a **captured execution identity assertion** recorded at initialization, distinct from the mutable legacy `manifest.project_id` field (Section 8.4) |
| `bound_at` | binding timestamp (execution-side provenance; permitted here because it is not on the Production unit) |

Rationale for manifest placement: `ProjectManifest` is already the frozen authority for execution state (Phase 9 Section 21); the binding rides the manifest's first save (immediately after initialization, `app/orchestrator/pipeline.py:108`), so it is durable before any generation occurs; and bindings are discovered by scanning manifests, which is acceptable at personal scale and requires no new index.

### 15.2 Write timing and immutability (frozen)

- Written **once**, at project initialization (the manifest is constructed with the binding fields and saved immediately - before any stage runs).
- **Immutable thereafter.** No execution path rewrites binding fields. Resume validates supplied config binding fields against the manifest binding and fails loudly on mismatch (prevents cross-unit project reuse).
- Binding writes are crash-safe by construction: either the first manifest save completed (binding durable) or it did not (no binding; the exact handling of this window is frozen in Section 15.5 - the executor never silently rebinds and never guesses).

### 15.5 Binding persistence crash discipline (frozen - decision A28)

The binding MUST be durably established BEFORE any execution stage may begin. Deterministic sequence per unit:

1. Load and validate the Production.
2. Select the next actionable `ProductionUnit` (Section 17 ordering).
3. Resolve or generate its exact `ProductionPlan` (Section 12).
4. Validate the plan (existing plan validation).
5. Compute the plan content hash.
6. Determine/create the execution project directory identity (allocated by the engine at initialization).
7. Construct the initial `ProjectManifest` including the production binding (additive fields, Section 15.1).
8. Persist the binding-containing initial manifest (the engine's existing first save, `app/orchestrator/pipeline.py:108`) - using the existing persistence behavior, without changing legacy write semantics (decision A26).
9. ONLY AFTER the binding is durably established may execution stages begin.
10. Run the existing `ProductionOrchestrator` against the exact supplied plan (Section 12.4).

Crash windows (frozen handling; uses only the existing manifest/checkpoint/reconciliation architecture - no new recovery system is invented):

**A. Crash before project creation:** no binding exists; the unit remains NOT_STARTED; existing plan artifacts (if any) are reused per Section 12.3. The next invocation simply proceeds.

**B. Crash after project directory allocation but before binding persistence:** no loadable manifest exists, therefore no binding evidence can exist - and because binding persistence precedes the first stage (steps 8-9), the orphan directory is provably free of generation work. Handling (frozen): the executor MUST NOT adopt manifest-less directories (adoption without evidence is guessing - forbidden); the executor MUST NOT silently rebind. Binding discovery is manifest-based only: **a binding exists if and only if a loadable manifest asserts it** (Section 8.4). The next invocation creates a fresh binding for the unit and MUST report this explicitly and truthfully ("no durable binding found for unit X; initializing new execution project") - never silently. **Loud failure is required whenever durable evidence is ambiguous** - for example, a project directory contains a manifest file that exists but cannot be loaded/parsed (it could be hiding a binding), or a loaded manifest's binding fails integrity validation (Section 15.4). In those cases the executor fails loudly and requires operator resolution; it never guesses.

**C. Crash after binding persistence but before the first stage:** the binding exists; the unit derives INCOMPLETE (or IN_FLIGHT if a RUNNING stage marker or recoverable remote identity exists). The next invocation resumes/continues the bound project through the existing resume path. NEVER create another project.

**D. Crash during remote scene generation:** existing Phase 7/8 checkpoint semantics remain authoritative: OpenRouter jobs are never resubmitted (`resume_scene` continuation only, `app/orchestrator/openrouter_video.py:1049-1114`); Flow never re-clicks Create (wants-resume semantics, `app/orchestrator/visual_providers.py:234-253`). The executor adds no new recovery machinery.

**E. Crash after project completion:** `reconcile_project` proves COMPLETED; the unit is never re-executed (idempotent).

### 15.3 1:1 enforcement (frozen)

At binding time the executor scans existing manifests for this production: a unit already bound, or a project already bound to another unit, is a loud failure. Derived state also flags any violation detected during derivation as a loud data-integrity error.

### 15.4 Integrity verification (frozen)

During derivation, for every binding: the canonical per-unit plan artifact must exist and its SHA-256 must equal `unit_plan_content_hash`; the bound project directory must exist and contain a loadable manifest whose binding matches. Any violation (missing artifact, hash mismatch, missing project, cross-bound manifest) is a **loud failure** - no silent repair, no silent regeneration, no silent re-binding.

## 16. Production Progress / "What Remains"

### 16.1 Vocabulary (frozen - decision A14)

Derived, read-only unit execution states:

| State | Derivation (all from durable evidence) |
|---|---|
| `NOT_STARTED` | no execution binding exists for the unit |
| `BLOCKED` | no binding AND at least one declared dependency is not COMPLETED |
| `IN_FLIGHT` | binding exists; verdict != PROJECT_COMPLETE; manifest shows a RUNNING stage (crash marker) OR at least one scene checkpoint carries a recoverable remote identity (OpenRouter `job_id` + `polling_url` non-terminal - `app/orchestrator/scene_execution.py:56-67`; Flow submitted-state per `provider_wants_resume` - `app/orchestrator/visual_providers.py:234-253`) |
| `INCOMPLETE` | binding exists; verdict != PROJECT_COMPLETE; no IN_FLIGHT markers; partial progress but no truthful terminal failure |
| `FAILED` | binding exists; verdict != PROJECT_COMPLETE; a truthful terminal failure is recorded (failed stage / terminal provider failure with no recoverable remote identity) and no IN_FLIGHT markers |
| `COMPLETED` | binding exists; `reconcile_project` verdict = `PROJECT_COMPLETE` |

IN_FLIGHT and INCOMPLETE both continue through the existing project resume path; the distinction is protective/informational (IN_FLIGHT marks units where remote-job resume protections are active - never resubmit, never regenerate).

### 16.2 Derivation rules (frozen)

- Derivation is **read-only** over intent: `production.json` bytes are never touched.
- Reconciliation is invoked read-only (`reconcile_project`); repairs are never auto-persisted by the executor (persist remains the operator's explicit action, as today).
- Progress is computed on demand at every invocation. **No durable progress cache exists in Phase 10** (decision A25) - the binding + manifest + reconciliation evidence is the only truth.
- **Version-mismatch reporting:** a unit COMPLETED under an older `production_plan_version` than the current intent is reported as an informational mismatch; it does not change state (execution truth is what happened; fulfilling revised intent is a deliberate future decision).

### 16.3 Production-level report (frozen)

Every invocation prints: production_id, title, current plan_version, per-unit states (counts + detail), actionable units in execution order, and - at completion - a truthful production summary. The report contains no secrets and no absolute paths beyond the existing project layout conventions.

## 17. Dependency Ordering

- **DEPENDENCIES ARE AUTHORITATIVE PREREQUISITES. ORDINAL IS THE DETERMINISTIC ORDERING/TIE-BREAKING MECHANISM AMONG CURRENTLY ELIGIBLE UNITS.** Concretely: eligibility is computed first (a unit is eligible only when all declared dependencies are COMPLETED); among currently eligible units, selection follows topological order over `unit.dependencies` with `unit.ordinal` as the tie-breaker (ordinals are unique by Phase 9 model validation). Example: Unit 2 (ordinal=2) depends on Unit 5 (ordinal=5) - Unit 2 is BLOCKED until Unit 5 is COMPLETED, and the executor MUST NOT violate the dependency merely because Unit 2 has the lower ordinal; once Unit 5 completes, ordinal determines which eligible unit is selected first. Cycles fail loudly during validation/execution planning. No scheduler is introduced.
- A unit may be **STARTED** only when all declared dependencies are COMPLETED at that moment. Unsatisfied dependencies -> the unit derives BLOCKED and is skipped (never silently - the report names the blocking units).
- **Dependency gates apply to starting new units only.** An already-bound unit continues regardless of its dependencies' current derived states (its start gate was passed historically; continuation is governed by its own project evidence).
- Dependency cycles cannot be persisted (Phase 9 model rejection, `app/production/models.py:401-423`); the executor still validates defensively and fails loudly.

## 18. Production-Level Resume

- **Re-invocation is the resume mechanism.** `python produce.py --production <id>` re-derives everything and continues actionable units.
- A bound unit's continuation uses the engine's **existing resume path** (`--resume <directory identity>` semantics, `app/orchestrator/pipeline.py:236-328`) with binding-consistency validation (Section 15.2). No second project is ever created for a bound unit - this prevents duplicate generations and duplicate provider spend.
- Crash markers (RUNNING stages) and remote-job resume protections behave exactly as in Phases 8/9 - the executor adds no new recovery machinery, only production-level composition.
- A unit that is FAILED is never auto-resumed by the executor; resuming a failed project remains an explicit operator action with existing semantics.

## 19. Process Termination / Crash Recovery

A 60-unit production walkthrough (frozen behavior):

1. Invocation starts; 12 units COMPLETED, unit 13 IN_FLIGHT (crashed mid-scene with an OpenRouter job identity in its checkpoint), units 14-60 NOT_STARTED.
2. Re-invocation: derivation marks unit 13 IN_FLIGHT -> the executor resumes its bound project; the engine's existing resume protects the remote job (no resubmission), completes the scene, and finishes the unit -> COMPLETED.
3. Units 14+ proceed in order; each gets a lazily generated plan artifact (or reuses the existing one) and a fresh binding at its project's initialization.
4. A crash at any point leaves durable evidence: bindings in manifests, checkpoints in manifests, plan artifacts on disk. The next invocation re-derives and continues. No state is lost, duplicated, or resubmitted.

Crash windows (frozen analysis):

| Crash window | Durable outcome | Next invocation |
|---|---|---|
| before plan artifacts written | nothing written | regenerate plan |
| after plan artifacts, before project init | plan artifacts exist, no binding | reuse plan; bind + execute |
| after init, before first manifest save | manifest-less, provably work-free orphan directory; no binding evidence | unit NOT_STARTED; orphan never adopted, never guessed at; fresh binding created with explicit truthful reporting; loud failure if evidence is ambiguous (Section 15.5, window B) |
| after first save, mid-stage | binding + stage/checkpoint evidence durable | IN_FLIGHT/INCOMPLETE -> resume bound project |
| after PROJECT_COMPLETE | binding + complete evidence | COMPLETED (idempotent; never re-executed) |

All five crash windows (A-E) are specified exactly in Section 15.5 (decision A28).

## 20. Plan Version Pinning

- Every binding records the exact production `plan_version` and per-unit plan hash executed against (Section 15).
- **If Production version 3 is executed and later revised to version 4:** old executions remain provably bound to version 3 - their bindings are immutable proof. The current intent (v4) defines remaining units; unit identities are stable across revisions (Phase 9 storage never removes units - `app/production/storage.py:255-263`), so unexecuted units derive NOT_STARTED under v4 and their unbound plan artifacts regenerate for v4 (Section 12.3).
- Bound units are never re-planned and never re-bound; a bound unit whose intent was revised completes under the plan it was bound to, with the version mismatch reported informationally (Section 16.2).
- Execution never rewrites the intent version it executes (Phase 9 Section 20) - pinning is the enforcement mechanism.

## 21. Provider / Routing Intent (translation rules)

Unit `provider_intent` is **name-level** (Phase 9). The executor translates it into existing config inputs only; it never constructs providers, never imports provider modules, and never bypasses `resolve_scene_provider` / `build_hybrid_routing` / `create_visual_provider` (`app/orchestrator/visual_providers.py:918-946, 949-1000, 1195-1260`).

| `unit.provider_intent` | Translation (frozen) |
|---|---|
| `None` / `"default"` | no visual config influence from the unit; operator/runtime visual config applies unchanged |
| `image_motion`, `local_asset`, `external_video`, `gemini_omni`, `openrouter_video` | `visual_mode="hybrid"`, `visual_default_provider=<name>` (existing validator semantics; `google_flow` is impossible here by construction) |
| `"google_flow"` | `visual_mode="hybrid"`; default provider stays the safe existing default (`image_motion`); **after plan generation**, explicit per-scene routing is set for every scene of that unit's plan: `visual_scene_routing = { <scene_id>: "google_flow" for all scenes }` - exactly equivalent to today's `--route-scene <scene_id>=google_flow`, flowing through the existing `build_hybrid_routing` persistence path (`app/orchestrator/pipeline.py:1964-1975`) |

Additional frozen rules:

- **Precedence:** persisted scene config on an existing project remains authoritative on resume (existing behavior). Unit intent seeds initial config only.
- **Conflicts fail loudly:** operator-supplied explicit per-scene routing conflicting with unit-derived routing for the same scene is a loud failure (ambiguity refusal), not a silent override.
- Runtime operator config (Flow URL, OpenRouter model/paid flags, mock/dry-run, voice, captions, assembly) always fills whatever unit intent does not express. Flow URLs are never part of unit intent (Section 22).

## 22. Google Flow Preservation

- **`google_flow` is first-class but explicit-only.** It can never be a default provider (`app/orchestrator/models.py:469-482`); unit intent expressing Flow becomes explicit per-scene routing after plan generation (Section 21) - never a default, never implicit.
- The executor never constructs the Flow wrapper, never calls Flow APIs, and never stores Flow URLs in intent. The Flow URL remains **runtime operator config**, persisted per-project in the manifest exactly as today.
- The Flow wants-resume hook and resume behavior (`app/orchestrator/visual_providers.py:234-253`, `210-217`) are preserved unchanged.
- `app/flow/` is untouched by Phase 10.

## 23. OpenRouter Preservation

- **`app/orchestrator/openrouter_video.py` MUST NOT be modified** in Phase 10 (file-level non-modification constraint).
- OpenRouter resume semantics are preserved exactly: resume Scene A -> `resume_scene` continuation (`app/orchestrator/openrouter_video.py:1049-1114`); **never resubmit**; no re-prompting; no fallback provider; no retries beyond existing bounded behavior.
- Unit intent may name `openrouter_video` as a provider (name-level translation, Section 21); OpenRouter policy (free-first, paid disabled by default, atomic `.part` writes, provenance) is untouched.
- Scene checkpoints, remote job identity persistence, and completion checkpoint updates (`app/orchestrator/scene_execution.py:45-188`) are consumed as-is.

## 24. Reconciliation Composition

- The executor composes the **existing** per-project reconciliation (`reconcile_project`, `app/orchestrator/reconciliation.py:344-412`) into production-level derivation (Section 16). It does not duplicate, weaken, or bypass verdict logic (incomplete/foreign scene-artifact detection at `386-394`; artifact status checks at `32-37`).
- `reconcile_project` is invoked read-only by the executor. Persisting reconciliation remains an operator-only action with existing semantics.
- No new verdict levels are introduced in Phase 10; production-level states are derived from existing verdicts + binding + manifest evidence.

## 25. Publishing Boundary

- Publishing remains **per-project and post-gate**: `PROJECT_COMPLETE` remains the only publication gate (`app/orchestrator/pipeline.py:175-209`); publishing flows (`app/publishing/reconciliation.py:834+`) and their durable manifest state machines (`PublicationRecord` / TikTok publication record) are untouched.
- The executor does NOT orchestrate publication. A COMPLETED unit's project publishes exactly as a legacy completed project publishes today (operator-driven).
- No new publishing state, no batch publishing, no production-level publication tracking in Phase 10.

## 26. Single / Batch / Episodic Invariance

- **One executor. One binding mechanism. One derivation.** No branching on unit count or unit type.
- Single-unit Productions exercise the identical path as 60-unit episodic productions.
- `EpisodeMetadata` (season/episode/title/continuity_refs) is data flowing through the planning context (Section 11.3); it never introduces executor branching, special ordering, or dedicated state.
- `unit_type` is open vocabulary (Phase 9); the executor treats every video-producing unit identically.

## 27. Personal / Public AVM Compatibility

- The executor is AVM Core (Phase 9 Section 25): identity-based, secret-free, machine-independent.
- No secrets, credentials, absolute machine paths, or operator identity data enter bindings, plan artifacts, or progress reports.
- `productions/` and `output/projects/` remain portable directory trees; copying a workspace moves the full production state.
- The PUBLIC AVM boundary is preserved: no executor feature depends on publishing accounts, Flow credentials, or any operator-specific configuration.

## 28. Phase 10 Scope (INCLUDES)

1. Production executor module in the execution layer (Section 7) - orchestration/binding only.
2. Unit-to-ContentBrief adapter (pure function; Section 10).
3. Additive `production_context` planning-context mechanism (Section 11).
4. Lazy per-unit plan generation + plan artifacts under `units/<unit_id>/plan/` (Section 12).
5. Additive supplied-plan consumption path in the CONTENT stage (Section 12.4).
6. Additive execution binding on `ProjectManifest` + additive `OrchestratorConfig` binding/supplied-plan/planning-context fields (Sections 11, 12.4, 15).
7. Derived production progress + frozen vocabulary (Section 16).
8. Deterministic dependency-aware execution ordering (Section 17).
9. Production-level continuation/resume composition (Section 18).
10. `produce.py --production [--unit]` minimal CLI surface (Section 9.1).
11. Routing-intent translation table (Section 21).
12. Tests (Section 33).

## 29. Explicit Non-Goals / Future-Phase Boundaries

- **NO scheduling / automation / daemons / recurrence / cron** (Phase 12+ autonomy track).
- **NO retry policies, no fallback providers, no provider substitution** (Phase 12+).
- **NO SaaS infrastructure: no database, no queue, no worker, no message bus, no cloud services, no multi-user anything.**
- **NO cross-unit intelligence:** no "episode 37 knows what happened in episode 36" reasoning, no automatic continuity enforcement (Phase 13+ quality track).
- **NO production-level publishing orchestration** (publishing track).
- **NO batch cost optimization, provider arbitrage, or quota management.**
- **NO dashboard / analytics / progress persistence.**
- **NO reorganization of the project directory structure around productions; no new identity scheme.**
- **NO refactor of the project-id replacement quirk** (Section 8.3, decision A20).
- **NO changes to:** `app/production` model/identity/validation semantics, `app/flow/`, `app/orchestrator/openrouter_video.py`, publishing modules, provider factory, routing authority, audio policy, scene output contract.

## 30. Backward Compatibility

- All new manifest fields are `Optional` with `None` defaults: **legacy manifests load, reconcile, resume, and complete unchanged. No migration. No version bump.**
- Legacy projects are **never adopted** into Productions; no backfilling, no index of legacy projects.
- Default `produce.py` behavior (no `--production`) is bit-identical to Phase 9 behavior.
- `ContentBrief`/`OrchestratorConfig` additive fields default to `None`/absent -> existing prompt construction, planning behavior, and validation unchanged (software-contract level per Section 11.4; stochastic provider output is not guaranteed byte-identical and need not be).
- The supplied-plan CONTENT stage path activates only when the additive pointer is set.

## 31. Persistence / Atomicity

- **All NEW durable writes introduced by Phase 10 are atomic** (temporary file + `os.replace` - the proven Phase 9 pattern): per-unit plan artifacts, `plan_meta.json`, `content_brief.json`.
- **Decision A26 (explicit):** the existing non-atomic legacy manifest write (`_save_manifest`, plain `open("w")`, `app/orchestrator/pipeline.py:330-335`) is **left untouched** in Phase 10 - hardening it is a separate, explicitly authorized reliability change, not an opportunistic edit. The binding's crash exposure is bounded: the first manifest save occurs immediately after initialization, before any generation. This is documented as pre-existing technical debt.
- No durable progress cache (Section 16.2). No new artifact kinds beyond: per-unit plan artifacts and additive manifest fields.

## 32. Security / Secret Safety

- No secrets in any Phase 10 artifact: bindings carry names/hashes/timestamps only; plan artifacts and progress reports are secret-free. The existing `SECRET_STATE_KEYS` scan continues to guard the Production artifact.
- API keys remain environment-only (existing behavior, unchanged).
- The executor never logs or persists Flow URLs, tokens, account identifiers, or provider credentials.
- Binding data is identity-level only (IDs, versions, hashes) - safe for workspace portability and support bundles.

## 33. Test Strategy

Hermetic first: the full executor path must run under `--mock` (mock LLM + mock visual providers), including a 60-unit production.

Test categories (mirroring the acceptance criteria):

1. Adapter purity: mapping table, unknown-key reporting, no clamping (loud failure on out-of-range values), determinism.
2. Planning-context mechanism: absent field -> byte-identical legacy prompts; present field -> deterministic context block; no Bible duplication into units/plans/bindings.
3. Lazy plan generation: generate-on-first-action; reuse on current version; regenerate only when unbound + stale version; atomic writes; hash correctness.
4. Supplied-plan consumption: engine executes the exact pre-generated plan (no second generation); the planner is invoked exactly once per unit plan - at generation time, never during engine execution; byte-identical project copy; quirk preserved (identity replacement unchanged).
5. Binding: written at init, before stages; immutable; 1:1 enforcement (double-binding loud failure in both directions); integrity verification (missing artifact / hash mismatch / missing project -> loud failure).
6. Derived states: all six vocabulary states from constructed evidence; BLOCKED gating; no intent mutation during derivation (`production.json` bytes unchanged).
7. Dependency ordering: topological + ordinal determinism; cycle loud failure; start-gate enforcement.
8. Crash recovery: every window in the Section 19 table; 60-unit mock production interrupted and resumed to completion; IN_FLIGHT remote-identity protection (no resubmission).
9. Routing translation: each `provider_intent` row of the Section 21 table; google_flow -> explicit per-scene routing after plan generation; conflict loud failure; persisted-config authority on resume.
10. Preservation: OpenRouter file untouched; Flow wrapper untouched; publishing untouched; reconciliation verdicts unchanged; legacy single-video `produce.py` path unchanged.
11. Backward compatibility: legacy manifests with absent binding fields; no-migration load/resume.
12. Version pinning: execute under v3 -> revise to v4 -> old bindings prove v3; unbound artifacts regenerate; mismatch reporting.

## 34. Acceptance Criteria

Phase 10 is COMPLETE only when ALL of the following hold:

1. **`python produce.py --production <production_id>` executes a Phase 9 Production end-to-end**, with each unit producing a complete final video through the existing engine.
2. **Execution never modifies the Production intent artifact**: for every executed unit, `production.json` and `versions/` are byte-identical before and after execution (verified in tests).
3. **Per-unit plans are generated lazily**, stored at `productions/<pid>/units/<uid>/plan/production_plan.json` (+ `plan_meta.json`, `content_brief.json`), written atomically, and reused/regenerated exactly per Section 12.3.
4. **The engine executes the exact bound plan**: no second plan is generated for a bound unit; the project's content plan is byte-identical to the canonical artifact; the binding hash matches both.
5. **Bindings are durable, crash-safe, and re-loadable**: additive `ProjectManifest` fields (production_id, unit_id, production_plan_version, unit_plan_identity, unit_plan_version, unit_plan_content_hash, project_id, bound_at), written once at initialization, immutable thereafter, with 1:1 enforcement and loud integrity failures.
6. **"What remains" is derived, never stored**: all six states derive correctly; derivation is read-only over intent; no durable progress cache exists; no execution-state fields appear on ProductionUnit (existing Phase 9 scans still pass).
7. **No revision churn**: executing all units of a 60-unit Production creates zero Production revisions.
8. **Interruption at any point is recoverable**: killing the process mid-production and re-invoking resumes without duplicate projects, duplicate generations, or provider resubmission (60-unit mock test).
9. **Single, batch, and episodic productions execute through the identical executor path** (no unit-count/unit-type branching in code).
10. **`provider_intent` translations match Section 21 exactly**, including google_flow -> explicit per-scene routing after plan generation, never a default.
11. **`app/orchestrator/openrouter_video.py` is unmodified** (byte-identical to `ac0d648`); OpenRouter resume/never-resubmit semantics verified by test.
12. **`app/flow/` is unmodified**; Flow URL remains runtime config; the executor never constructs Flow.
13. **Reconciliation and publishing are unchanged**: `reconcile_project` verdicts and the `PROJECT_COMPLETE` publishing gate behave exactly as in Phase 9.
14. **Legacy compatibility**: a pre-Phase-10 project (manifest without binding fields) loads, reconciles, resumes, and completes with zero migration; default `produce.py` behavior unchanged.
15. **Dependency ordering is deterministic** and BLOCKED units are skipped with truthful reporting; cycles fail loudly.
16. **No secrets in any new artifact**; no absolute machine paths in bindings.
17. **Full test suite passes** (`python -m pytest -q tests`) with all new Phase 10 tests included; the complete executor path runs hermetically under mock mode, including the 60-unit production.
18. **No construction sentinels remain** (no unfinished-work marker text) in any Phase 10 artifact.
19. **Danger-zone checklist (Section 35) walked and clean** at implementation review.
20. **Scope containment verified by diff review**: implementation touches only the files Section 28 permits; Phase 1-9 behavior is preserved; no infrastructure, no scheduling, no retry policies.
21. **Supplied-plan exclusivity (decision A27)**: for every production-backed unit, the CONTENT stage never invokes the planner; the executed plan is byte-identical to the pinned canonical artifact (hash-verified); plan generation and plan execution are provably distinct phases; zero executions exist where the engine generated or executed a plan other than the pinned one.

## 35. Danger Zones (explicit refusals)

1. Creating a second production engine ("ProductionRunner", "BatchExecutor", "ProductionService") - FORBIDDEN.
2. Storing execution state, status, or progress on ProductionUnit / in production.json - FORBIDDEN (Phase 9 scans reject it; do not weaken them).
3. Conflating production_id with project_id, or reorganizing `output/projects/` around productions - FORBIDDEN.
4. Creating Production revisions as a side effect of execution (per-unit "resolved" revisions) - FORBIDDEN.
5. "Fixing" the project-id replacement quirk opportunistically - FORBIDDEN (documented debt; separate authorization required).
6. Making `google_flow` a default provider anywhere - FORBIDDEN (the existing validator is the guard).
7. Storing Flow URLs or credentials in intent, bindings, or plan artifacts - FORBIDDEN.
8. Touching `app/orchestrator/openrouter_video.py`, `app/flow/`, or publishing state machines - FORBIDDEN.
9. Adding retry/fallback/substitution policies - FORBIDDEN (Phase 12+).
10. Adding scheduling, daemons, recurrence, or background workers - FORBIDDEN.
11. Introducing a database, cache service, queue, or cloud dependency - FORBIDDEN.
12. Durable progress caches or a second source of truth for "what remains" - FORBIDDEN.
13. Silent clamping of unit intent (e.g., clamping 25 scenes to 20) - FORBIDDEN (loud failure).
14. Silent conflict resolution between operator routing and unit intent - FORBIDDEN (loud failure).
15. Regenerating or overwriting plan artifacts for bound units - FORBIDDEN.
16. Creating a second project for a bound unit - FORBIDDEN.
17. Auto-persisting reconciliation repairs from the executor - FORBIDDEN.
18. Copying the full Bible into units or plans - FORBIDDEN (resolve referenced slices only).
19. Non-atomic writes for any NEW Phase 10 artifact - FORBIDDEN.
20. Opportunistic hardening of the legacy manifest write - FORBIDDEN in Phase 10 (separate authorization).
21. Branching executor logic on unit count or unit type - FORBIDDEN.
22. Batch publishing or production-level publication orchestration - FORBIDDEN.
23. Breaking legacy manifests or requiring migration - FORBIDDEN.
24. Invoking the planner a second time for a bound unit during engine execution, or executing any plan other than the pinned supplied plan - FORBIDDEN (decision A27; the supplied-plan path is the only authoritative CONTENT-stage plan source for production-backed units).

## 36. Design Gate / Implementation Authorization

- This specification is a **design gate**. Freezing it authorizes nothing.
- Implementation requires: (1) formal acceptance of this document; (2) a separate, explicit implementation authorization; (3) an implementation task scoped exactly to Section 28.
- During implementation, any discovered conflict between this specification and repository reality must be resolved by amending this specification first - never by silent deviation.
- Acceptance review must walk Section 34 (all 21 criteria) and Section 35 (all 24 refusals) against the implementation diff.

## 37. References

- `AVM_MASTER_ROADMAP.md` (Section 4.7 Phase 10; Section 9 autonomy levels)
- `PHASE_9_DURABLE_PRODUCTION_PLAN.md` (intent layer contract; Sections 13, 14, 15, 20, 21, 22, 26, 31, 32, 35)
- `PHASE_8_PLANNING_EXECUTION_ARCHITECTURE.md` (scene execution boundary, routing, checkpoints)
- `PHASE_7D_AUDIO_CONTRACT.md`, `PHASE_7E_OPENROUTER_VIDEO.md`, `scene-output-contract.md`
- Source evidence: `produce.py`; `app/orchestrator/` (pipeline.py, models.py, scene_execution.py, visual_providers.py, openrouter_video.py, reconciliation.py); `app/content/` (models.py, planner.py, generator.py); `app/production/` (models.py, storage.py, validation.py, identity.py); `app/publishing/reconciliation.py`; `app/flow/scene_models.py`

## 38. Amendment / Decision Record

Decisions are frozen; amendments require a new dated revision of this document with an explicit rationale. Phase 10 implementation must honor decisions A1-A26, plus amendment decisions A27-A31 (2026-08-31 architectural hardening amendment; A1-A26 remain intact and unrenumbered):

- **A1 - Unit-to-project mapping.** One ProductionUnit -> one existing execution project; strictly 1:1; enforced by binding validation with loud failures.
- **A2 - Identity separation.** `production_id`, `unit_id`, `project_id` remain distinct forever; `project_id` is the execution identity; `production_id` never replaces it.
- **A3 - One engine.** `ProductionOrchestrator` remains the only production engine; Phase 10 adds an orchestration/binding layer only.
- **A4 - Shape invariance.** Single/batch/episodic use one executor; no unit-count or unit-type branching; episode metadata is data.
- **A5 - Intent immutability.** The Production intent is immutable during execution; zero revision churn from execution.
- **A6 - Exact pinning.** Execution binds to the exact production plan version and exact per-unit plan bytes (SHA-256); later revisions never rewrite executed history.
- **A7 - Execution-side binding.** The binding lives in additive `ProjectManifest` fields - never inside the Production artifact.
- **A8 - Lazy per-unit plans.** Plans are generated on first actionable need via the existing `ScenePlanner`; plan artifacts are write-once per intent version.
- **A9 - Bible canonical.** The Production Bible remains optional and a single canonical copy inside the Production.
- **A10 - Bible-to-planner mechanism.** ONE additive optional `ContentBrief.production_context` field, assembled by the adapter from `unit.bible_refs` (resolved slices only) plus unit narrative/episode context; additive conditional prompt section; transient; no duplication into units, plans, or bindings; absent field = bit-identical legacy behavior.
- **A11 - Name-level provider intent.** Translated to existing config inputs only; the executor never constructs providers or imports provider modules.
- **A12 - Flow explicit-only.** `google_flow` intent -> explicit per-scene routing after plan generation; never a default; Flow URL is runtime operator config.
- **A13 - OpenRouter untouched.** `openrouter_video.py` is file-level non-modifiable in Phase 10; resume/never-resubmit semantics preserved.
- **A14 - Derived progress.** The frozen six-state vocabulary (NOT_STARTED / BLOCKED / IN_FLIGHT / INCOMPLETE / COMPLETED / FAILED) is derived from bindings + manifests + `reconcile_project`; never stored on units; no durable progress cache.
- **A15 - Deterministic ordering.** Topological by dependencies, ties by ordinal; cycles fail loudly; start-gating on COMPLETED dependencies.
- **A16 - No scheduling/automation.** No daemons, cron, recurrence, or background processing.
- **A17 - No retry/fallback.** Stop-on-first-failure; re-invocation continues remaining actionable units; failed units are never auto-retried.
- **A18 - No SaaS infrastructure.** No database, queues, workers, or cloud services.
- **A19 - Legacy compatibility.** Optional binding fields; zero migration; legacy projects never adopted into Productions; default CLI behavior unchanged.
- **A20 - No opportunistic refactor.** The project-id replacement quirk is documented debt; the binding records the project DIRECTORY identity; remediation is separately authorized.
- **A21 - Binding write discipline.** Binding written once at project initialization (durable before any generation), immutable thereafter, with binding-consistency validation on resume.
- **A22 - Plan artifact discipline.** Canonical per-unit plan artifacts at `units/<unit_id>/plan/` with `plan_meta.json` + `content_brief.json`; atomic writes; reuse on current version; regeneration only for unbound units on stale versions; loud integrity failures on missing/mismatched artifacts.
- **A23 - Supplied-plan execution.** The CONTENT stage consumes the pre-generated plan via an additive config pointer (load, validate, persist byte-identically, proceed); the identity-replacement quirk is preserved unchanged.
- **A24 - Failure semantics.** Stop-on-first-truthful-failure; truthful reports; no automatic continuation past a failure within an invocation.
- **A25 - No progress cache.** Progress is derived on demand every invocation.
- **A26 - Manifest write hardening deferred.** The existing non-atomic manifest write remains untouched in Phase 10; all NEW Phase 10 durable writes are atomic; documented as pre-existing technical debt.
- **A27 - Exact supplied-plan execution is authoritative.** For a production-backed unit, once a plan is resolved and pinned, the engine executes that exact plan; the planner is never invoked a second time for the bound unit; the supplied-plan path is the only authoritative CONTENT-stage plan source and exists solely to prevent CONTENT-stage regeneration (Section 12.4).
- **A28 - Binding persistence crash discipline.** The deterministic 10-step sequence of Section 15.5; binding durable before any stage; the five crash windows A-E (before project creation; orphan before binding; bound before first stage; mid-remote-generation; post-completion) with manifest-only discovery, no adoption of manifest-less directories, no silent rebinding, explicit truthful reporting, and loud failure on ambiguous evidence.
- **A29 - Project directory identity is the execution binding target.** The binding's `project_id` is a captured execution identity assertion (the engine-allocated directory name at initialization); `manifest.project_id` is a legacy mutable field; `manifest.project_id == directory name` must never be assumed; resolution is production + unit -> binding -> directory identity, with no absolute paths and no guessing scans (Section 8.4).
- **A30 - Legacy ContentBrief compatibility / additive production_context.** The planning architecture and `ProductionPlan` contract remain intact; only minimum additive capability is permitted; absent `production_context` = software-contract-level legacy compatibility (same prompt construction, validation, and code paths; no byte-for-byte LLM output promise); the Bible is transport-only planning context and never a continuity/QC/routing engine (Section 11.4).
- **A31 - Dependency precedence over ordinal.** Dependencies are authoritative prerequisites; ordinal is only the deterministic ordering/tie-breaking mechanism among currently eligible units; a lower-ordinal unit is BLOCKED by a higher-ordinal dependency; cycles fail loudly; no scheduler (Section 17).

---

This document is a frozen strategic/architectural specification. Writing or freezing this document does not authorize Phase 10 implementation. Phase 10 implementation requires a separate explicit authorization after review and acceptance.
