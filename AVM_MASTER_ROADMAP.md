# AVM MASTER ROADMAP

**AVM = Auto Video Maker.**

This document is the **authoritative strategic roadmap** for the AVM project. It records
where the project has been, where it is, and where it is going — from the original
foundation, through the personal production engine, into personal autonomy maturity,
and eventually toward a proven core, a public product, and a long-term content platform.

This is a **strategic and architectural** roadmap. It deliberately does **not** contain
implementation designs for future phases. Every future phase receives its own frozen
specification / design gate before implementation (see Cross-Cutting Principles).

**Signed-off position — 2026-10-24:** **Phase 11C COMPLETE (Tracks 1–14 Verified &
Signed Off)**. Founder/CTO acceptance covers the bounded V1 scope below: **1,904
passed, 0 failed, 1 skipped** (opt-in live E2E) in Core and **86 passed, 0 failed**
in K3, with a successful production build. **Phase 12 — Automated Publishing,
Scheduling & V1.x Operator Polish** is next; this closure does not implement it.

---

## 1. Strategic Vision

AVM's objective is not merely to generate individual videos. AVM is intended to receive a
**production command** and autonomously plan, produce, validate, assemble, and publish
content — continuing through a defined production workload with minimal human intervention.

Example production command:

> "Create a 60-episode mecha series, 20–30 seconds per episode, and publish one episode
> every day."

The long-term evolution:

```
VIDEO GENERATION
        ↓
VIDEO PRODUCTION
        ↓
AUTONOMOUS PRODUCTION
        ↓
CONTENT OPERATIONS
        ↓
CONTENT PLATFORM
```

### 1.1 The central strategic principle

> **The long-term AVM architecture is ONE AVM CORE with multiple control/product
> surfaces — not separate personal and public production engines.**

The repository explicitly rejects this architecture:

> "Build a personal AVM and later rebuild a separate SaaS AVM."

The desired architecture is:

```
                         AVM CORE
                            │
             ┌──────────────┴──────────────┐
             │                             │
      PERSONAL CONTROL              PUBLIC CONTROL
          PLANE                         PLANE
             │                             │
       Personal UI                  SaaS/Web UI
       Operator tools               Customer UX
       Advanced controls            Simplified controls
             │                             │
             └──────────────┬──────────────┘
                            │
                    SAME PRODUCTION
                        ENGINE
                            │
        ┌────────────┬──────┴──────┬────────────┐
        ▼            ▼             ▼            ▼
      Flow       OpenRouter      Gemini       Local
        │            │             │            │
        └────────────┴─────────────┴────────────┘
                            │
                            ▼
                     Produce / QC
                            │
                            ▼
                         Publish
```

This is a strategic architecture principle, not an instruction to implement it now.

### 1.2 Personal vs. Public — what the maturity phases really mean

Phases 9 through 16+ are primarily the **PERSONAL AVM MATURITY / PROVING track**. This
does **not** mean everything before SaaS is disposable personal-only software:

- We are building the **real AVM Core**.
- We are initially operating that Core **ourselves**.
- Real personal production is the **proving ground**: production data, failures, quality
  results, provider behavior, automation behavior, and operational experience inform the
  architecture.
- Personal interfaces may expose **deeper operator controls** than public users will see.
- The underlying production engine remains **reusable** for the eventual public product.
- Public SaaS/Web/Mobile is a **productization layer** over the proven AVM Core.

---

## 2. Master Roadmap Diagram

```
                         AVM
                          │
                    FOUNDATION
                    Phases 1–4
                          │
                          ▼
              PERSONAL AVM ENGINE
                Phases 5–8
                          │
                          ▼
          ┌───────────────────────────┐
          │   PERSONAL AVM MATURITY   │
          │                           │
          │ Phase 9  Durable Plan     │
          │ Phase 10 Plan Execution   │
          │ Phase 11 Command Center   │
          │ Phase 11B Experience      │
          │ Phase 11C V1 Product      │
          │ Phase 12 Automation       │
          │ Phase 13 Quality + Scale  │
          │ Phase 14 Provider Intel   │
          │ Phase 15 Recovery/Fallback│
          │ Phase 16+ Full Personal   │
          │                           │
          └─────────────┬─────────────┘
                        │
                        ▼
               PROVEN AVM CORE
                        │
              ┌─────────┴─────────┐
              │                   │
              ▼                   ▼
       PERSONAL INTERFACE     PRODUCTIZATION
              │                   │
              │                   ▼
              │             PUBLIC AVM
              │             SaaS / Web
              │             Mobile App
              │                   │
              └─────────┬─────────┘
                        ▼
                AVM CONTENT PLATFORM
                     Long-term
```

---

## 3. Phase Status Legend

| Symbol | Meaning |
|---|---|
| ✅ | COMPLETE |
| 🟡 | FUTURE / NOT STARTED |
| 🔵 | LONG-TERM / STRATEGIC |
| ⚠️ | EXTERNALLY BLOCKED / CONDITIONAL |

Future phases are **not** marked partially complete merely because prerequisite
architecture already exists.

## 4. Detailed Phase Roadmap

### 4.1 FOUNDATION — Phases 1–4 ✅

**Purpose:** Establish the original AVM foundation and core production capabilities
(project scaffolding, configuration, content specification, voice, and the original
assembly engine — as established by repository history and the Phase 2E integration
audit).

**Status:** ✅ COMPLETE.

### 4.2 PERSONAL AVM ENGINE — Phases 5A–5J ✅

**Purpose:** Build the production engine and foundational production workflow:
platform connections, the web control surface, the Google Flow browser automation proof
of concept, the orchestrator, and the first successful TikTok Sandbox publication
(repository commit "Steps 1-5J: platform connection layer, premium multi-platform web
UI, TikTok publish bridge, and first successful Sandbox publication").

**Status:** ✅ COMPLETE.

### 4.3 PERSONAL AVM ENGINE — Phases 6A–6F ✅

**Purpose:** Expand production capabilities already established by the repository:

- 6A — pluggable scene visual providers with the Flow cost firewall
- 6B — ImageMotion provider + durable hybrid scene routing
- 6C — project-scoped asset workflow and preflight gate
- 6E — operational hardening: resume ergonomics, truthful CLI summary,
  mock/placeholder reconciliation guard
- 6F — automatic visual sources + reproducible caption presentation

**Status:** ✅ COMPLETE.

### 4.4 PERSONAL AVM ENGINE — Phases 7A–7E ✅

**Purpose:** Harden the visual production system:

- 7A — unified scene output contract (`scene-output-contract.md`)
- 7B — external video intake route
- 7C — Gemini Omni provider (plus 7C.1 SDK compatibility fix)
- 7D — explicit audio policy and mixing contract (`PHASE_7D_AUDIO_CONTRACT.md`)
- 7E — OpenRouter video gateway (`PHASE_7E_OPENROUTER_VIDEO.md`): live-service safety
  controls, dynamic model discovery, pricing-derived free/paid classification,
  free-first / paid-gated behavior, capability preflight, async job lifecycle,
  checkpoint-before-polling, resume/idempotency protections, terminal-job handling,
  atomic artifact handling, truthful provenance
- Plus: provider routing, provider isolation, and pre-live hardening honoring the
  visual-default-provider with explicit expired-job handling

**Status:** ✅ COMPLETE.

### 4.5 PERSONAL AVM ENGINE — Phase 8 ✅

**Name:** Provider-Agnostic Scene Execution Architecture.

**Purpose:** Separate orchestration/state management from provider-specific scene
generation/execution through a uniform scene execution boundary
(`app/orchestrator/scene_execution.py`).

**Important outcome:** Future ordinary visual providers can participate through the
provider execution architecture **without requiring a new provider-specific execution
branch in the central pipeline**. Dispatch is by name (a lookup), never a duplicated
per-provider execution path.

**Authoritative specification:** `PHASE_8_PLANNING_EXECUTION_ARCHITECTURE.md`
(frozen; Phase 8 closed by final acceptance with non-blocking concerns only).

**Provider identities established through Phase 8** (all first-class, all isolated):

| Provider | Class |
|---|---|
| `image_motion` | zero-cost local rendering (default) |
| `local_asset` | zero-cost asset normalization |
| `external_video` | zero-cost externally sourced clips |
| `gemini_omni` | cost-bearing text-to-video |
| `openrouter_video` | cost-gated remote gateway (free-first, paid disabled by default) |
| `google_flow` | first-class browser-automation provider with credit firewall |

**Do not reopen Phase 8** unless a future phase explicitly addresses one of its
documented non-blocking concerns (see Section 9).

**Status:** ✅ FORMALLY ACCEPTED.

### 4.6 PERSONAL AVM MATURITY — Phase 9 ✅

**Name:** Durable Production Plan.

**Purpose:** Create a durable, inspectable, resumable **production-plan artifact**
representing what AVM intends to produce.

**Concept:** Move from ephemeral planning toward a persisted **production contract**.
The plan should eventually be capable of representing the intended production workload
sufficiently for later execution, inspection, resume, and automation.

**Question this phase answers:** *"What are we making?"*

**Authoritative specification:** `PHASE_9_DURABLE_PRODUCTION_PLAN.md` (frozen;
closed by final acceptance).

**Status:** ✅ COMPLETE.

### 4.7 PERSONAL AVM MATURITY — Phase 10 ✅

**Name:** Plan-Backed Execution.

**Purpose:** Make the durable production plan the **authoritative input to execution**.

**Concept:** AVM should not merely generate a plan and immediately forget it. Execution
should be grounded in the durable plan so that AVM can:

- know what remains to be produced
- know what has already been completed
- resume production
- maintain production state
- distinguish planned work from completed work
- support multi-episode / multi-scene workloads

**Question this phase answers:** *"Execute the plan."*

**Authoritative specification:** `PHASE_10_PLAN_BACKED_EXECUTION.md` (frozen;
closed by final acceptance).

**Status:** ✅ COMPLETE.

### 4.8 PERSONAL AVM MATURITY — Phase 11 ✅

**Name:** Personal Command Center.

**Purpose:** Create the **operator-facing control surface** for the personal AVM.
This is **NOT** the final public SaaS UI.

The personal command center should eventually allow the operator to:

- issue production commands
- inspect plans
- inspect production progress
- inspect scenes / episodes
- see failures
- intervene when needed
- approve or reject where appropriate
- inspect provider usage
- inspect production state
- control ongoing jobs
- access deeper diagnostics than ordinary customers should see

The interface must remain **separable from the AVM Core** (UI is not the engine).

**Question this phase answers:** *"Give me a command center to inspect and control
production."*

**Authoritative specification:** `PHASE_11_PERSONAL_COMMAND_CENTER.md` (frozen;
closed by final acceptance; Git checkpoint `b50503c`).

**Status:** ✅ COMPLETE.

### 4.9 PERSONAL AVM MATURITY — Phase 11B ✅

**Name:** Command Center Experience.

**Purpose:** Transform the functional Phase 11 Personal Command Center into a
substantially more coherent, polished, understandable, and production-oriented
operator experience — **without changing the underlying AVM production architecture
or execution semantics**.

Phase 11B is **UX/UI refinement and experience architecture only**: a bounded
refinement of the existing Phase 11 Personal Command Center control surface.

**Accepted experience scope:** the original discovery direction below was frozen in
`PHASE_11B_COMMAND_CENTER_EXPERIENCE.md` (`dd21b26`), implemented, and accepted at
`cc551ca`. Specification-time discovery/no-implementation language is historical,
not the current implementation status. The original authority boundaries remain:

- information hierarchy
- navigation and screen organization
- production cards
- production/unit status visualization
- progress visualization
- empty states
- execution-control presentation
- unit/scene presentation
- project ↔ production relationship visualization
- media thumbnails/previews where existing data permits
- checkpoint/artifact presentation
- typography, spacing, layout and responsive behavior
- loading and error states
- confirmation-dialog presentation
- visual consistency across the web experience
- clearer distinction between production intent, execution state, project, and media
- overall Command Center usability and polish

**Question this phase answers:** *"Make the Command Center a coherent, polished,
production-oriented operator experience."*

**Hard scope boundaries — Phase 11B does NOT:**

- modify Phase 9 production-intent semantics
- modify Phase 10 ProductionExecutor semantics
- create a second execution engine
- introduce a scheduler
- introduce automation
- introduce a database
- introduce durable execution state
- introduce authentication/tenancy
- redesign provider routing
- introduce provider fallback/retry
- introduce new publishing orchestration
- add YouTube publishing orchestration
- change ProductionExecutor authority
- change ProjectManifest.production_binding authority
- make plan artifacts authoritative
- alter production intent
- create a parallel frontend architecture
- bypass the existing AVM Core

**Governing principle:** **ONE AVM CORE, MULTIPLE CONTROL SURFACES.** Phase 11B is
a refinement of the existing Personal Command Center surface — not a new engine and
not a separate product core.

Phase 11B was the bounded experience gate **before Phase 12**. Its specification,
implementation review, and acceptance are complete (`dd21b26`, `cc551ca`);
`tests/test_phase11_command_center.py` verifies 49 cases, including the accepted
11B projections, media confinement, diagnostics, and binding behavior. This status
reconciliation does not reopen its frozen specification or authorize new execution
semantics.

**Status:** ✅ COMPLETE — the Phase 11B specification was frozen (`dd21b26`) and its
recovered implementation was subsequently reviewed and accepted (`cc551ca`), as recorded
by the 2026-09-08 amendment (Section 13).

### 4.10 PERSONAL AVM PRODUCT — Phase 11C ✅

**Name:** AVM V1 Product Implementation & Expansion.

**Purpose:** Provide the controlled bridge from the existing AVM architecture into an
integrated AVM V1 product. Phase 11C is an additive integration and productization
phase, not a greenfield rebuild and not a replacement for Phases 1–11B.

The governing implementation philosophy is:

> **DISCOVER → REUSE → POLISH → INTEGRATE → HARDEN → FILL GENUINE GAPS**

not:

> **REWRITE → REBUILD EVERYTHING**

Phase 11C is the approved roadmap for controlled V1 implementation. It is not blanket
permission to implement every capability listed below immediately. Each implementation
increment still requires a separately authorized task, an appropriate design or
implementation checkpoint, validation, and acceptance evidence.

**Status:** ✅ **COMPLETE — Tracks 1–14 Verified & Signed Off, 2026-10-24**.
The Founder/CTO closure decision accepts the implemented V1 workflow and preparation
seams, not all future capabilities mentioned in this strategic roadmap. Core code
checkpoint: `a1bb5edb6b8574554548a1682b9427f84cc28ff3`; K3 real-mode Summary
checkpoint: `b6a7f3d9b3b37ff072456c1d4525e3b7f4cd73c7`. The earlier Track 9
integration/routing checkpoint is `114bad3`, Core HTTP projection checkpoint
`20713eb`, and manual publishing parity checkpoint `39ff7d3` (K3: `d59225b`).

**Strategic objective:** Make the existing AVM Core operate and feel like one coherent
creative AI video studio across the Personal AVM/local control surface, while preserving
the architectural path toward a future public SaaS control surface and future mobile
distribution surface.

**Question this phase answers:** *"Integrate the proven AVM capabilities into a coherent
V1 product, then build only the genuinely missing product workflows around them."*

#### Relationship to previous phases

Phase 11C starts from a substantial existing base:

- Phase 1–8 established the production engine, media pipeline, provider architecture,
  local processing, Google Flow, OpenRouter, narration, captions, assembly, and
  publishing foundations.
- Phase 9 established durable production intent, units, optional Production Bible,
  dependencies, versioning, and immutable intent snapshots.
- Phase 10 established plan-backed execution, `ProductionExecutor`, explicit project
  binding, derived progress, and resumable multi-unit composition.
- Phase 11 established the Personal Command Center control surface.
- Phase 11B froze the Command Center experience boundary and its no-new-authority rules;
  its interrupted implementation was subsequently recovered, reviewed, and accepted
  (recorded by the 2026-09-08 amendment, Section 13).

Phase 11C shall preserve these boundaries. It shall not reopen completed phases merely
because V1 exposes their capabilities through a more integrated product workflow.

#### Historical starting point and reconciled V1 inventory

The AVM Product Architecture V1 Baseline supplied the discovery-time starting point.
The inventory below is reconciled to the signed-off 2026-10-24 implementation;
the dated discovery/amendment records in Section 13 remain historical evidence.

| Area | Current evidence | Signed-off V1 treatment |
|---|---|---|
| Production intent | `app/production/models.py` and `app/production/storage.py` provide `Production`, `ProductionUnit`, optional `ProductionBible`, dependencies, provider intent, versioning, and durable intent storage | V1 COMPLETE; preserved and integrated |
| Plan-backed execution | `app/orchestrator/production_execution.py` provides `ProductionExecutor`, `derive_progress()`, binding discovery, per-unit plans, and resumable execution around the existing engine | V1 COMPLETE; existing executor authority preserved |
| Project binding | `ProjectManifest.production_binding` is the authoritative Production → Unit → Project relationship | V1 COMPLETE; only binding authority |
| Scene execution | `app/orchestrator/scene_execution.py` provides the provider-independent execution boundary | V1 COMPLETE; additive integration, no replacement engine |
| Local video | ImageMotion, local asset normalization, external video intake, FFmpeg, and FFprobe paths exist in `app/orchestrator/visual_providers.py` and `app/assembly/` | V1 COMPLETE; consolidated, validated and integrated |
| Google Flow | Browser/CDP Flow route and credit protections exist in `app/flow/` and the Flow provider wrapper | V1 COMPLETE within external/runtime limits; first-class route |
| AI video | Gemini Omni, OpenRouter and Batch 1C Alibaba routes are integrated; catalog/access/cost gates and remote-job handling remain explicit | V1 COMPLETE within configured-provider limits; no premature intelligence layer |
| Narration | Existing `app/voice/` TTS is reused by real Standard text/JSON and Pro windowed visual grounded planning in `app/production/narration*.py`; narration/audio artifacts and summary MP4s persist under unit plans | V1 COMPLETE; no silent mock or cross-model substitution |
| Assembly/captions | `app/assembly/` and `app/orchestrator/captions.py` provide local assembly, audio policy, captions, and validation | V1 COMPLETE; preserved and connected to new workflows |
| Publishing | Existing YouTube/TikTok adapters, non-interactive HTTP commands, durable verification/reconciliation, and Core/K3 unit publishing parity are implemented (`39ff7d3`, `d59225b`) | V1 COMPLETE for manual publishing of accepted completed projects; automation remains Phase 12 |
| Command Center | Phase 11B implementation accepted at `cc551ca`; creator/production surfaces and independent K3 control surface consume existing Core authorities | V1 COMPLETE; no frontend execution authority |
| Long-video repurposing | Source registration, timestamped local transcription, validated source segments, shared deterministic/AI grounded moment-selection primitives, and derived `ProductionUnit` lineage are present | V1 COMPLETE for the bounded source/lineage workflow; direct clip shortcuts remain V1.x |
| SaaS/mobile | Ownership/root/credential/media/accounting seams and client-neutral HTTP contracts are verified; no multi-tenant DB/auth, billing ledger, cloud platform, or native mobile app is implemented | V1 preparation COMPLETE; infrastructure/product rollout remains future |

At discovery time, the baseline found that `productions/` was empty in the inspected
working tree, while `output/projects/` contained existing execution artifacts. That
historical observation is not a current filesystem claim. Existing code and tests
must be inspected before any capability is described as missing: absence of a product
surface is not proof that the underlying engine does not exist.

#### Existing-capability reuse rule

Every 11C capability task shall begin with a short evidence record answering:

1. What already exists?
2. What actually works under current tests and runtime conditions?
3. What is incomplete?
4. What is merely rough UX?
5. What is disconnected from the rest of AVM?
6. What is fragile or externally blocked?
7. What is genuinely missing?
8. What can be reused without architectural changes?

The implementation record shall classify each finding as one of:

- **EXISTS** — capability is present in the repository.
- **POLISH / HARDEN** — capability exists but requires UX, reliability, safety, or
  operational improvement.
- **INTEGRATE** — capability exists but is not connected to the intended V1 workflow.
- **GENUINE GAP** — the required primitive or workflow does not exist.
- **DEFERRED** — intentionally outside the current increment or protected by a later
  phase boundary.

No rewrite is justified solely because an existing component is not yet exposed through
the preferred V1 experience.

#### V1 capability direction and classification

The following is the signed-off V1 classification (2026-10-24). Original discovery
classifications remain in the amendment history; future changes still follow the
new-idea rule below. COMPLETE always means the stated V1 boundary, not every possible
product extension.

| Capability | Verified implementation / remaining boundary | Signed-off product classification |
|---|---|---|
| Local video creation through ImageMotion | Local rendering, FFmpeg, captions, conditional audio, measured narration synchronization, and accepted final-media evidence are integrated | **V1 COMPLETE** |
| Local asset/video normalization and assembly | Project-owned intake, hashing, validation, audio policy, captions, and assembly preserve existing authorities | **V1 COMPLETE** |
| Google Flow production route | Browser/CDP route, checkpoint/resume, and credit firewall remain first-class; authenticated browser/provider availability remains an external prerequisite | **V1 COMPLETE within documented runtime limits** |
| OpenRouter AI video creation | Gateway/catalog/access/paid gates, async-job handling, and duplicate-submit protections are integrated; free-model live availability is external | **V1 COMPLETE within documented runtime limits** |
| Canonical model selection and routing | Standard text/JSON vs Pro visual eligibility, explicit canonical identity, metadata-based Auto, and same-model provider failover are verified; economics/quality optimization is not implemented | **V1 COMPLETE; intelligent optimization Phase 14/15** |
| Cost-aware AI usage | Operation/attempt/fallback accounting is durable, cost provenance is explicit, and unknown remains unknown; no account credit ledger exists | **V1 COMPLETE evidence seams; billing/ledger V1.x / POST-V1** |
| Single Video Production | One Production/Unit uses the canonical planner, executor, project binding, validation, and manual publishing path | **V1 COMPLETE** |
| Multiple Independent Video Production | `unit_count` creation and independent units use the shared serial executor; product expansion/unattended scheduling remains deferred | **V1 COMPLETE; expansion V1.x** |
| Series / Episodic Production | Episode metadata, dependency-aware units, and optional shared Bible/context use the same executor; continuity intelligence is not claimed | **V1 COMPLETE bounded workflow; continuity intelligence V1.x / DECISION REQUIRED** |
| Approximately 45–60 second episodic output | Explicit short-form duration intent and existing plan/assembly constraints are reusable; achieved media duration remains validated evidence, not a UI claim | **V1 COMPLETE canonical output path** |
| Production Bible-required series workflow | Core Bible remains optional; context/episode semantics are verified, while automatic Bible creation and mandatory continuity enforcement remain an explicit future product decision | **V1 COMPLETE optional-context seam; mandatory gate V1.x / DECISION REQUIRED** |
| Long-video repurposing from approximately 45–60 minute source | Owned source registration, timestamped transcription, validated moment ranges, shared deterministic/AI candidate selection, and derived-unit lineage are implemented | **Track 8 V1 COMPLETE; standalone clip-rendering shortcut V1.x** |
| Approximately 60-second derived clips/episodes | Derived units participate in canonical planning/execution and manual publishing once bound to an accepted completed project; source lineage alone is never completion | **V1 COMPLETE unit/lineage path; bypass rendering V1.x** |
| Video Upload → AI Narration / Summary Video | Real Standard and windowed Pro planners, TTS synthesis, `narrated_summary.mp4`, Core routes, and K3 SummaryStudio are integrated | **Track 9 V1 COMPLETE; automatic sidecar-to-project promotion V1.x** |
| Manual publishing | Core/K3 YouTube/TikTok commands require accepted completed project evidence and retain durable publication outcomes | **V1 COMPLETE** |
| Automated publishing | No automatic publishing/scheduler was added by 11C | **Phase 12 — next, not implemented** |
| Additional publishing platforms | Platform registry and adapter direction exist; Instagram/Facebook are not implemented | **V1.x / DECISION REQUIRED** |
| Personal AVM Command Center | Phase 11B accepted (`cc551ca`); Core creator/operational views and independent K3 consume the same HTTP authorities | **V1 COMPLETE** |
| Future public SaaS | Verified ownership, configurable storage, credentials, accounting, and media-delivery seams; no multi-tenant DB/auth or SaaS rollout | **Track 11 preparation COMPLETE; SaaS product V1.x / POST-V1** |
| Subscriptions and billing | No implementation and no approved prices, tiers, provider, or revenue share | **DECISION REQUIRED; V1.x / POST-V1** |
| Credits and credit consumption | Durable provider-operation evidence is separate from commercial credit consumption; K3 exposes no authoritative credit balance | **Track 12 seams COMPLETE; ledger/billing V1.x / POST-V1** |
| Future mobile application | Core HTTP and K3 are strictly decoupled; read-only vs reconciliation-mutating contracts are documented; no native mobile app exists | **Track 13 preparation COMPLETE; POST-V1 application** |
| Advertising/revenue sharing | No authoritative commercial rules exist | **POST-V1 / DECISION REQUIRED** |

No prices, subscription tiers, billing provider, advertising model, or revenue-share
percentage is established by this amendment.

**Founder/CTO V1 vs V1.x scope decision — 2026-10-24:**

- **Track 8 — Long-Video Repurposing: V1 COMPLETE.** Acceptance covers production-owned
  source registration, timestamped transcription, deterministic/AI candidate moment
  selection through reused grounded-planning primitives, validated source segment
  ranges, and derived `ProductionUnit` lineage creation. Deterministic synthetic
  planning remains explicitly mock-labelled; real AI selection uses the resolved
  adapter. There is no claim of a separate unattended clip-discovery/rendering engine
  or dedicated candidate-selection HTTP endpoint. Derived units participate in
  canonical execution and publishing **once bound to an accepted completed project**.
  Standalone direct clip-rendering shortcuts bypassing `ProductionUnit` project
  execution remain **V1.x**.
- **Track 9 — Upload Video → AI Narration / Summary Video: V1 COMPLETE.** Both Summary
  Standard (real text+JSON grounded planner) and Summary Pro (bounded/windowed visual
  understanding plus grounded narration), TTS audio synthesis, `narrated_summary.mp4`
  assembly, Core `/api` routes, and K3 `SummaryStudio` are accepted. K3 defaults to
  real execution; the explicit sandbox toggle is optional and starts off.
  Standalone summary sidecars are inspectable/downloadable through
  `/api/productions/{id}/units/{unitId}/summary-video` and `/summary-video/media`.
  They do **not** create a `ProjectManifest.production_binding`, mark a project
  complete, or fabricate a publishing record. Automatic promotion into a canonical
  publishing record without a bound completed project remains **V1.x**; V1 refuses
  that handoff with **`422 SUMMARY_HANDOFF_REQUIRED`**.

#### Major capability tracks

11C is organized into dependency-aware tracks rather than implementing the product list
in presentation order:

1. **Core and repository hardening** — make the current baseline reproducible and
   trustworthy; preserve existing artifacts and authorities.
2. **Existing production consolidation** — bring the Production/Unit model, plan-backed
   execution, local pipeline, providers, manifests, checkpoints, and reconciliation into
   one proven operational path.
3. **Personal Command Center completion** — complete the Phase 11B review and polish
   without changing Phase 11 authority boundaries.
4. **Local creation integration** — make ImageMotion, local assets, FFmpeg, captions,
   audio policy, and final outputs coherent in the V1 creation experience.
5. **AI creation integration** — expose existing OpenRouter/Gemini capability safely,
   preserving cost gates and explicit provider evidence.
6. **Google Flow integration** — preserve Flow as a first-class route and verify its
   operator controls, credit protections, and failure boundaries.
7. **Production-mode consolidation** — provide one coherent workflow over Single,
   Multiple Independent, and Series/Episodic productions; do not create separate
   engines for each mode.
8. **Long-video repurposing** — add only the missing source ingestion, transcription,
   analysis, segmentation, derived-unit, and lineage primitives; reuse the existing
   media, narration, caption, assembly, and publishing core.
9. **AI narration** — extend the existing voice layer for source-video/transcript-driven
   narration without weakening the current plan-driven narration path.
10. **Publishing consolidation** — integrate manual YouTube/TikTok publishing and
    publication evidence; keep automatic publishing and scheduling in Phase 12.
11. **SaaS foundation preparation** — define seams for identity, tenant scope, storage,
    jobs, secrets, usage, and media delivery without implementing the whole SaaS stack.
12. **Subscription/credit architecture preparation** — document metering boundaries and
    cost semantics; do not invent commercial terms or prematurely add billing.
13. **Mobile readiness** — keep core commands and payloads surface-neutral and avoid
    making the personal web UI the only expression of AVM behavior.
14. **V1 validation and stabilization** — validate the integrated product against actual
    workflows, safety boundaries, runtime prerequisites, and acceptance criteria.

**Tracks 1–14 — final verification and sign-off (2026-10-24):** all tracks below are
**COMPLETE for their explicitly bounded V1 scope**. Evidence counts refer to the
recorded hermetic Core run and K3 Node suite; shared tests support more than one
track and must not be added together as independent totals. Provider-safe contracts
prove wiring and guards, not current external quota/uptime or unattended operation.

| Track | Signed-off status | Verified evidence / accepted boundary |
|---:|---|---|
| 1 — Core/repository hardening | ✅ COMPLETE | `test_phase11c_track1_truthfulness.py`: 27 passed; `test_phase2a_core_fixes.py`: 25; `test_phase2b_parallel_derivation.py`: 11; `test_phase2c_capability_readiness.py`: 15; `test_phase2d_output_metadata.py`: 12. The 10 credential/profile-dependent tests now pass without runner-supplied synthetic configuration. |
| 2 — Production consolidation | ✅ COMPLETE | `test_production_plan.py`: 87 passed; `test_phase10_plan_backed_execution.py`: 20; scene/checkpoint/route suites protect the existing Production → Unit → bound ProjectManifest → execution/validation chain. No competing authority. |
| 3 — Command Center | ✅ COMPLETE | Accepted 11B checkpoint `cc551ca`; `test_phase11_command_center.py`: 49 passed; `test_web_server.py`: 9. K3's 86-case suite protects shell/views, truthful mappings, and selected-unit commands. |
| 4 — Local creation | ✅ COMPLETE | `test_assembly.py`: 47 passed; `test_voice.py`: 44; `test_caption_presets.py`: 7; Batch 1A timeline suite: 8; local E2E: 2 passed (live E2E excluded). Local rendering/audio/captions/accepted MP4 evidence reuse existing primitives. |
| 5 — AI creation | ✅ COMPLETE | Batch 1C Alibaba/follow-up/visual suites: 88 passed; `test_openrouter_video.py`: 33; `test_gemini_omni.py`: 12. Credential-class, request, paid-operation, native-audio, fallback, and provenance guards remain intact. |
| 6 — Google Flow | ✅ COMPLETE within runtime limits | `test_flow.py`: 29 passed; `test_flow_scene.py`: 78; `test_browser_lifecycle.py`: 4. First-class private operator route, dedicated browser/CDP and credit firewall retained; no live generation required by the closure run. |
| 7 — Production modes | ✅ COMPLETE | Multiple Independent: 19 passed; Series/Episodic: 21. Shared unit-count/episode/context/dependency-aware workflow and canonical executor; optional Bible retained, continuity intelligence not claimed. |
| 8 — Long-video repurposing | ✅ COMPLETE — V1 decision above | Source ingestion/transcription/segments suites: 68 passed; Track 8 derivation/lineage/truthfulness suites: 15. Deterministic/AI candidate selection reuses Track 9 grounded-planning evidence; Track 10 verifies publishing once the derived unit has an accepted completed project. Direct clip shortcut stays V1.x. |
| 9 — Upload → AI narration/summary | ✅ COMPLETE — V1 decision above | All Track 9 suites: 188 passed, including 31 real Standard contracts and 17 HTTP cases. K3 Summary/tier suite: 14 passed; real/sandbox handler requests and baseline JSX verified. Standalone publishing promotion stays V1.x. |
| 10 — Manual publishing | ✅ COMPLETE | `test_track10_publishing_parity.py`: 105 passed; existing YouTube/TikTok metadata/reconciliation suites remain green. K3 publishing/evidence suite: 32 passed. Canonical project completion, target identity, privacy, duplicate-submit, and durable verification boundaries preserved. |
| 11 — SaaS foundation seams | ✅ COMPLETE — preparation only | Ownership/relocation/secret exclusions in the 87-case production-plan suite; range-aware media in Command Center/Track 9/web suites; non-interactive HTTP credentials in Track 10. Core/K3 remain one engine with separate control surfaces; no tenant DB/auth. |
| 12 — Subscription/credit seams | ✅ COMPLETE — evidence only | `test_phase11c_batch1h_accounting.py`: 20 passed; `test_model_provenance_accounting.py`: 23; `test_local_route_provenance.py`: 20; provenance/logging suites: 14/16. K3 planner evidence mapping: 9 passed. Unknown monetary cost is not zero or a product credit balance. |
| 13 — Mobile-readiness seams | ✅ COMPLETE — preparation only | Core client-neutral HTTP contracts, configurable roots and explicit GET/status side-effect documentation; K3's 86 tests and Vite production build verify separate client integration. No native app, mobile engine, database, queue, or auth service. |
| 14 — V1 validation/stabilization | ✅ COMPLETE — SIGNED OFF | Core: **1,904 passed, 0 failed, 1 skipped** via `tools/run_hermetic_pytest.py`, **0 external network attempts**. K3: **86 passed, 0 failed**, production build exit 0 / 0 errors. Known warnings and V1.x scope remain explicit. |

#### Controlled implementation sequence

The authorized sequence is preserved below as the completed implementation audit
trail. All 14 stages are signed off for the bounded V1 scope; historical initial
treatments describe why work was authorized, not pending implementation status.

| Order | Stage | Dependency rationale | Final V1 status / original treatment |
|---:|---|---|---|
| 1 | V1 foundation and repository hardening | All later work depends on a trustworthy baseline, runtime prerequisites, evidence, and clean authority boundaries | ✅ COMPLETE — DISCOVER; POLISH / HARDEN |
| 2 | Existing production-system consolidation | Production modes must use the existing intent, plan, binding, executor, manifest, checkpoint, and reconciliation chain | ✅ COMPLETE — REUSE; INTEGRATE |
| 3 | Personal Command Center completion and polish | The operator needs a truthful control surface before broader production workflows are exposed | ✅ COMPLETE — REUSE; POLISH / HARDEN; 11B accepted |
| 4 | Local video-creation integration | The lowest-cost path and existing FFmpeg/ImageMotion primitives provide the baseline production route | ✅ COMPLETE — REUSE; INTEGRATE |
| 5 | AI generation and OpenRouter integration | Existing provider/cost controls must be exposed only after the core local route is coherent | ✅ COMPLETE — REUSE; INTEGRATE / HARDEN |
| 6 | Google Flow integration | Flow remains first-class but is externally/runtime sensitive and must retain credit protections | ✅ COMPLETE within runtime limits — REUSE; INTEGRATE / HARDEN |
| 7 | Single, multiple-independent, and episodic mode consolidation | These modes share the Production/Unit architecture; product workflows depend on the consolidated execution path | ✅ COMPLETE — REUSE; INTEGRATE; no separate engines |
| 8 | Long-video repurposing | Requires stable media ownership, units, assembly, captions, and publishing before adding source analysis | ✅ COMPLETE bounded source/lineage workflow — GENUINE GAP; additive reuse |
| 9 | AI narration for uploaded/source media | Depends on source ingestion, transcription, and segment context; extends existing voice and timing primitives | ✅ COMPLETE Standard + Pro + Core/K3 — GENUINE GAP plus REUSE |
| 10 | Publishing consolidation | Requires stable final artifact evidence and unit/project relationships; automated publishing remains excluded | ✅ COMPLETE manual parity — REUSE; INTEGRATE / HARDEN |
| 11 | SaaS foundation preparation | Must be informed by real personal production evidence; define seams without premature infrastructure | ✅ COMPLETE ownership/storage/credential/media seams only |
| 12 | Subscription and credit architecture preparation | Requires evidence of actual resource/cost behavior and approved commercial decisions | ✅ COMPLETE accounting evidence; commercial policy remains DECISION REQUIRED |
| 13 | Mobile-readiness preparation | Depends on stable control contracts and surface-neutral core operations | ✅ COMPLETE two-repo HTTP seam and side-effect documentation only |
| 14 | V1 validation and stabilization | Final acceptance must cover integrated workflows and ensure later-phase boundaries remain intact | ✅ COMPLETE — final hermetic validation and Founder/CTO sign-off |

The sequence may be amended only through the new-idea/change rule below. A later stage
must not be pulled forward merely because its UI can be sketched before its authority,
cost, security, or persistence implications are understood.

#### Architecture constraints

Phase 11C preserves the established AVM authority model:

- **Intent authority:** `productions/<production_id>/production.json`.
- **Execution authority:** `ProductionExecutor` plus the existing execution pipeline and
  project manifests/checkpoints.
- **State authority:** `ProductionExecutor.derive_progress()` for production-unit state.
- **Binding authority:** `ProjectManifest.production_binding`.
- **Artifact authority:** existing manifest, checkpoint, filesystem-validation, and
  reconciliation evidence.
- **Publishing authority:** existing publishing modules and durable publication records.

11C shall not introduce:

- a second execution engine;
- a second state machine for production progress;
- a fourth authority or competing state authority;
- an independent frontend execution or production-state authority;
- a competing project-binding mechanism;
- a new durable execution-history authority;
- an unnecessary database;
- a scheduler, unattended automation, or automatic continuation;
- generalized retry/fallback/substitution policy or provider ranking before its
  approved later phase (the single fixed **Alibaba-primary → OpenRouter-fallback**
  resilience policy is explicitly authorized for Batch 1C by the 2026-09-12 amendment —
  see Section 4.10 "Batch 1C" and Section 8A.14; it is capability-aware,
  failure-classified, with no parallel generation and no provider ranking; Track 9's
  separately verified same-canonical-model provider failover is also allowed and
  never substitutes a different model);
- generalized provider intelligence or cost/quality/latency optimization before its
  approved later phase; Track 9's deterministic capability-metadata eligibility/Auto
  choice is the accepted V1 exception, not a commercial intelligence engine;
- a new quality engine before its approved later phase;
- a new media-processing architecture where the approved local/FFmpeg primitives fit;
- a new publishing state machine;
- an unnecessary migration of the Core frontend/framework/build system. The approved
  independent K3 React/Vite repository consumes Core HTTP; it does not replace the
  Core engine or create a second execution/state authority.

New workflow artifacts such as transcripts, segment plans, source-media records, and
derived-unit lineage may become durable evidence when separately designed. They must not
silently replace Production intent, execution evidence, or binding authority.

#### New-idea and change-management rule

The Founder has approved continued introduction of new ideas during V1 development. V1
is therefore not architecturally frozen forever. It is controlled rather than closed.

For each proposed idea, the implementation owner should record a lightweight decision
note answering:

- Is it necessary for V1, or only attractive?
- Is it compatible with the existing architecture?
- What can be reused?
- What is its dependency impact?
- What is its scope impact?
- What is its security/data-boundary impact?
- What is its SaaS impact?
- What is its mobile impact?
- Does it leak into the Phase 12–15 boundaries?
- What is its implementation cost and complexity?

The idea is then classified as exactly one of:

1. **ADOPT INTO CURRENT 11C** — necessary or strongly enabling for V1, compatible with
   the authority model, and bounded by an authorized task.
2. **DEFER TO V1.x** — valuable but not required for the first integrated V1 baseline.
3. **DEFER POST-V1** — belongs after the proven core/product milestone.
4. **REQUIRES ARCHITECTURAL DECISION** — changes authority, persistence, cost, security,
   tenancy, phase boundaries, or core assumptions and requires Founder/CTO decision
   before implementation.

This is intentionally a lightweight classification, not a new bureaucracy. The rule
exists to preserve room for useful ideas without allowing uncontrolled scope expansion,
architecture drift, or silent leakage into Phases 12–15.

#### SaaS-readiness direction

The strategic target remains **ONE AVM CORE WITH MULTIPLE CONTROL/PRODUCT SURFACES**.
Personal AVM is the immediate proving environment; future public SaaS should use the
same core where practical rather than requiring a second production engine.

During 11C, implementation should keep future seams visible for:

- identity and tenant context;
- tenant-scoped storage and media delivery;
- durable job ownership and execution lifecycle;
- secret/credential providers;
- usage and cost metering;
- subscription/credit policy;
- API versioning and client-neutral command contracts.

11C shall not implement authentication, tenancy, subscriptions, billing, payment
providers, a credit ledger, cloud infrastructure, or a database merely to claim SaaS
readiness. Personal/local operation remains the immediate constraint. SaaS design work
belongs only where it prevents an avoidable architectural dead end in the shared core.

**Track 11 — verified V1 SaaS foundation seams (COMPLETE, preparation only):**

- `Production` / `ProductionUnit` own declared source/creative intent;
  `ProjectManifest.production_binding` owns the relationship to an execution project.
  Manifest/checkpoint/reconciliation evidence owns execution and final-media facts.
  These are ownership boundaries, **not** implemented users, tenants, permissions,
  or database tables.
- Production storage, `ProductionExecutor(productions_root=..., projects_root=...)`,
  and existing media/credential roots are configurable/isolatable. The hermetic
  runner verifies relocation without importing the operator's OAuth/profile data.
- HTTP publishing/status uses non-interactive credential handling
  (`allow_interactive=False` on request-path publisher factories): it may validate
  or refresh stored credentials but cannot launch OAuth consent. Interactive
  consent is confined to the explicit platform-connect command.
- Core `/api` project, scene, asset, and summary media delivery is root-confined,
  MIME-aware and range-aware; a sidecar URL is not project ownership or completion.
  K3 consumes these contracts through its same-origin proxy and service seam.
- Evidence: production ownership/relocation tests, 49 Command Center tests,
  17 Track 9 HTTP tests, and 105 Track 10 parity tests passed. **No multi-tenant
  database/auth, billing service, job queue or cloud platform is present in V1.**

**Track 12 — verified V1 subscription/credit seams (COMPLETE, evidence only):**

- `app/orchestrator/scene_execution.py::build_operation_accounting` is the existing
  operation-record builder. `BILLING_STATUS_BILLABLE`, non-billable and unknown status,
  `usage`, `cost`, `cost_source`, `currency`, request identity and source are recorded
  separately. `ZERO_COST_PROVIDERS` establishes local/non-billable facts, not a
  promise that every route or AVM service is free.
- Generated/reused operations and primary/final fallback attempts retain durable
  provenance through planner evidence, execution checkpoints and manifests. A
  submitted billable operation with unknown cost stays `cost=None`; missing usage,
  unknown totals or multiple costs without a verified aggregation do not become
  zero. Provider-reported vs AVM-calculated vs absent cost sources remain explicit.
- K3 maps existing planner/provider evidence (`planner-evidence.mapper.test.mjs`,
  9 passed) and configuration readiness, but exposes no authoritative account credit
  balance or consumption economy. Design-preview numbers are not billing evidence.
  No fabricated credit balances, billing tables, subscription tiers, prices, or
  exchange rates were introduced. Accounting seams do not implement collection of
  payment or a product credit ledger.

Commercial direction is recorded without invented terms:

- Local creation: free initially / potentially monetized later.
- AI creation: higher-cost paid capability.
- Long-video repurposing: paid capability.
- AI narration: paid capability.
- Publishing: subscription-gated capability with applicable future usage/feature
  charges.
- SaaS: subscription plus credit-based direction.

Prices, tiers, billing rules, payment providers, and revenue-sharing terms remain
**DECISION REQUIRED** at a later stage.

#### Mobile-readiness direction

11C shall prepare for a future mobile surface without creating a mobile application.
The core should remain expressible through stable, surface-neutral operations such as:

- inspect production;
- inspect unit;
- inspect project/media evidence;
- initiate an explicitly authorized command;
- inspect derived progress;
- inspect publication state.

The personal web UI may remain richer and more diagnostic than a future mobile UI. Mobile
readiness does not require a mobile framework, a separate execution engine, or a new
mobile-specific authority.

**Track 13 — verified V1 mobile-readiness seams (COMPLETE, preparation only):**

- Strict two-repo separation: Core `C:\Projects\AUTO-VIDEO-MAKER` exposes HTTP
  `/api/*` at default `127.0.0.1:8787`; K3
  `C:\Users\Paul Ella\AppData\Local\Cline\avm-studio-pro` serves its independent
  client at `:5173`. K3's `src/services/avm/core-api.js` and `service.js` call the
  same-origin `/api` proxy; `AVM_CORE_URL` configures its target. K3 does not import
  Core Python, persist production state, or own execution. Both repositories have
  their own verified tests/build contracts.
- **Read-only/local inspection:** production list/detail/unit/progress reads,
  `GET /api/capabilities`, summary/transcript reads and media GETs do not start
  execution, generation or publication. Unit reconciliation uses the existing
  oracle with `persist=False`; it is inspection, not a publishing reconciliation.
- **GET does not universally mean passive:** `/api/connections` performs a
  non-interactive stored-token check that may refresh credentials/contact the
  platform. `GET /api/projects/{id}/publish/{youtube|tiktok}/status` can reconcile an
  existing remote identity and persist updated verification evidence, serialized
  with publishing. It never submits/uploads; disconnected or active-publish cases
  stay passive. K3 exposes this as explicit `reconcileProjectPublication` inspection,
  not a background upload retry or generic read-only health check.
- Execute/resume/publish/connect and source/transcript/narration/synthesis/assembly
  remain explicit operator commands with existing validation and safety gates.
  The 86-case K3 suite verifies selected-unit targeting and transport uncertainty;
  the Core HTTP suites verify refusal/no-submission and read-vs-mutation boundaries.
  No native mobile application or premature infrastructure is claimed.

#### Validation and checkpoint strategy

Each 11C increment shall use the smallest appropriate acceptance checkpoint:

1. **Evidence checkpoint** — repository paths, actual behavior, dependencies, and
   reuse/gap classification are recorded.
2. **Architecture checkpoint** — authority, persistence, security, cost, SaaS, mobile,
   and Phase 12–15 boundary impact are reviewed.
3. **Implementation checkpoint** — separately authorized code is limited to the agreed
   scope; unrelated worktree changes are not overwritten.
4. **Behavior checkpoint** — focused tests, static/source checks, local media validation,
   and provider-safe tests are run as appropriate.
5. **Integration checkpoint** — the workflow is traced end-to-end through intent,
   execution, binding, artifacts, reconciliation, and publication where applicable.
6. **Acceptance checkpoint** — status is explicitly recorded as EXISTING, WORKING,
   PARTIALLY IMPLEMENTED, BROKEN, MISSING, or COMPLETE; assumptions and external blocks
   remain visible.

Validation must distinguish local processing from paid/external operations. Expensive or
irreversible operations require explicit authorization and must not be used merely to
produce acceptance evidence. Existing tests remain regression protection; they are not
permission to widen scope.

**Final validation checkpoint — 2026-10-24 (accepted):**

```text
Core: python tools/run_hermetic_pytest.py tests -q --tb=short
1904 passed, 1 skipped, 2 warnings in 456.80s (0:07:36)
Hermetic network guard: 0 external attempts.

Core Track 9 suites: 188 passed, 2 warnings in 82.96s (0:01:22)
Core Track 9 HTTP:   17 passed, 2 warnings in 21.36s
Original 10 configuration/profile blockers: 10 passed, 1 warning in 1.44s
All four affected files directly: 85 passed, 1 warning in 73.53s (0:01:13)

K3: node --test tests/*.mjs
86 passed, 0 failed, 0 skipped; exit 0
K3: npm run build
1976 modules transformed; production build exit 0; 0 build errors
```

The Core runner disables dotenv/inherited provider configuration, isolates runtime
and credential/profile roots, and blocks external networking while allowing
loopback HTTP. Synthetic configuration and fake adapters are scoped to tests;
there are no external runner overrides for the 10 fixed blockers. The only skip is
`test_batch1_local_route_live_intelligence_end_to_end`, an expressly opt-in live
E2E; no AI-provider, YouTube or TikTok call was made for final closure. Existing
Pydantic deprecations and K3's non-fatal >500 kB bundle warning remain visible.
An earlier full run encountered a Windows loopback `WinError 10053` in one Track 10
HTTP test; that file passed unchanged on recheck (105 passed) and the sequential
full run above passed. It was not masked by retries or weakened guards.

Recorded transcripts/JUnit: `C:\Users\Paul Ella\AppData\Local\Temp\phase11c-core-sequential.txt`,
`phase11c-core-sequential.xml`, `phase11c-core-track9.txt`,
`phase11c-core-api-routes-final.txt`, `phase11c-core-ten-tests.txt`,
`phase11c-k3-tests-final.txt`, and `phase11c-k3-build-final.txt` in that same directory.
These are local audit evidence, not committed production artifacts. The inspected
pre-fix checkout collected 1,865 tests, not the earlier quoted 1,866; 40 new contract/
HTTP cases yield 1,905 collected = 1,904 passed + one opt-in skip. Counts do not claim
a paid/live smoke or native mobile/SaaS implementation.

#### Exit criteria

Phase 11C may be accepted only when evidence demonstrates, at minimum:

- the V1 product boundary and accepted V1/V1.x/Post-V1 classifications are recorded;
- existing Phase 1–11B capabilities have been inventoried and reused where sound;
- the Production/Unit/Project/Manifest/Checkpoint authority model remains intact;
- `ProductionExecutor`, `derive_progress()`, and `ProjectManifest.production_binding`
  remain authoritative;
- the Personal Command Center is accepted after its separate Phase 11B implementation
  review and truthful execution/media verification;
- local creation, existing AI routes, Google Flow, and manual publishing are integrated
  or have explicit documented external/runtime limitations;
- Single, Multiple Independent, and Series/Episodic workflows use the shared
  Production/Unit architecture rather than parallel engines;
- the long-video repurposing workflow has either met its separately defined V1
  acceptance criteria or has an explicit Founder/CTO decision to classify it V1.x;
- Upload → AI Narration has either met its separately defined V1 acceptance criteria or
  has an explicit Founder/CTO decision to classify it V1.x;
- cost controls remain explicit and no paid operation is silently introduced;
- publishing state remains owned by existing publishing modules and records;
- SaaS and mobile seams are documented without premature platform infrastructure;
- no Phase 12, 13, 14, or 15 responsibility has been silently absorbed;
- focused and integration validation evidence is available, with external/runtime
  limitations clearly reported;
- the resulting product is coherent enough to serve as the proven V1 baseline rather
  than a collection of disconnected demonstrations.

**Exit-criteria disposition:** ✅ **MET AND SIGNED OFF — Phase 11C COMPLETE**.
The explicit Founder/CTO 2026-10-24 decision accepts Tracks 1–14 against the
verified inventory, safe execution/ownership chain, hermetic results, documented
Track 8/9 V1 vs V1.x boundaries, and preparation-only Tracks 11–13. Phase 11B and
Batch 1A/1B/1C are implemented and verified, not pending discovery/application code.
Phase 12 (automation/scheduling/V1.x operator polish), Phase 13 (quality/scale),
Phase 14 (intelligent optimization), Phase 15 (generalized recovery) and future
public SaaS/native mobile work retain their separate authorization gates.

#### Explicit non-goals

Phase 11C does **not**:

- restart AVM as a greenfield project;
- discard or rewrite working Phase 1–11B architecture;
- absorb Phase 12 personal automation, scheduling, unattended execution, or automatic
  continuation;
- absorb Phase 13 quality scoring, quality gates, or autonomous scale systems;
- absorb Phase 14 provider ranking, optimization, or intelligent model selection
  beyond the accepted Track 9 capability-metadata selection/same-model failover;
- absorb Phase 15 generalized retry, substitution, or automated recovery policy, or any
  provider ranking/intelligent routing (the single fixed **Alibaba-primary →
  OpenRouter-fallback** resilience policy is explicitly authorized for Batch 1C by the
  2026-09-12 amendment; it is not a general multi-provider orchestration system);
- introduce a database, authentication, tenancy, billing, subscriptions, or payment
  provider without a separately approved SaaS phase;
- introduce a scheduler, queue, daemon, microservice fleet, or Kubernetes/cloud platform
  without evidence that the current stage requires it;
- create a second execution engine, state machine, binding authority, or publishing
  state machine;
- unnecessarily migrate the Core frontend/build system; the approved independent
  K3 React/Vite control surface is not a second Core or an execution authority;
- invent final commercial prices, tiers, credit exchange rates, or revenue-sharing terms;
- claim that external video intake is already long-video repurposing;
- claim that plan-driven TTS is already upload-to-AI-narration;
- implement every possible future platform, workflow, or idea merely because it is
  compatible with the long-term vision.

#### Batch 1 scope structure — 1A / 1B / 1C (added by the 2026-09-08 scope amendment)

Phase 11C implementation is organized into controlled batches. Batch 1's core
production and local creation work is implemented and committed (`8f3940a`). This
amendment formally structures the remaining immediate Batch 1 scope into three
workstreams. Batch assignment is a roadmap scope/sequence decision: each workstream
still receives its own separately authorized implementation task(s), design or
implementation checkpoint, validation, and acceptance evidence under the existing
Phase 11C discipline.

**Batch 1A — Timeline Synchronization Fix.** The immediate correction of the
confirmed narration/visual duration divergence observed in real production
(approximately 36.19s measured narration against a 60s planned visual timeline,
producing a long silent tail — recorded in Section 11). The fix is applied within
the existing local scene-rendering seam; it must NOT assume narration-driven timing
for media that owns its own duration/audio (Phase 7D; Section 8A.12): remote/billable
generated video keeps plan-backed duration, and native generated audio remains a
first-class media property. The broader Production Timeline abstraction remains
**Batch 2** and is not authorized by Batch 1A.

**Batch 1B — Stock Visual Relevance Hardening.** Improve stock search-query
construction (Pexels/Wikimedia) so richer scene semantics survive acquisition, and
improve metadata-based candidate relevance scoring, addressing the recorded current
limitations (Sections 8A.17/8A.18). The existing VisualSource abstraction, the visual
routing layer, truthful failure/fall-through behavior, and Phase 6C intake remain
intact. Multimodal/pixel-level Semantic Visual Evaluation remains **Batch 2** and is
not authorized by Batch 1B.

**Batch 1C — Alibaba Cloud Provider Integration + OpenRouter Fallback.** *(Reassigned
from ComfyUI by the 2026-09-12 amendment; ComfyUI is deferred — see Section 8A.9 and
Section 13.)* Integrate **Alibaba Cloud Model Studio as AVM's primary AI provider** and
retain **OpenRouter as the automatic fallback**, both through **existing provider
seams** — no second execution architecture. Scope:

1. Alibaba LLM/text provider;
2. Alibaba reasoning/planning provider (the frozen `ProductionPlan`/`Scene` contract is
   preserved — Alibaba adapts to AVM's contract, not the reverse);
3. Alibaba image-generation provider (visual-source seam);
4. Alibaba video-generation provider (visual-provider seam);
5. Alibaba primary-provider configuration;
6. **capability-aware Alibaba → OpenRouter fallback**;
7. **fallback error classification** (classified, never blind);
8. provider provenance/observability;
9. **video/image safety guards** (explicit model/size/duration; never rely on provider
   defaults; spend/quota limits — per the live-validation finding that invalid
   parameters can silently default to a billable generation);
10. **credential separation and `sk-sp-` rejection** (AVM uses only its dedicated
    standard application key, e.g. `sk-ws-…`; the Token Plan `sk-sp-…` key is Cline-only
    and must never be used by AVM);
11. preservation of existing AVM provider seams and the Mode / Route / Provider-Model /
    Engine / Endpoint distinctions of Section 8A.1/8A.2;
12. preservation of the existing OpenRouter implementation as the fallback.

**Economic rule:** normal production uses **Alibaba only**; OpenRouter is invoked
**only after** a fallback-eligible Alibaba failure — **no parallel or duplicate
generation** of the same artifact, and no requirement for continuous paid subscriptions
to both providers. **Fallback is capability-aware and failure-classified, never
blind:** it applies only where both providers can legitimately satisfy the requested
capability, and only for classified provider failures (outage/unavailability, rate
limiting, quota exhaustion, provider-side 5xx, transport failure/timeout,
model/capability temporarily unavailable) — never for AVM's own malformed request,
invalid duration/size, contract error, self-caused validation failure, or credential
misuse. If Alibaba fails on a capability OpenRouter cannot legitimately satisfy, AVM
returns a truthful failure.

**Audio:** the Phase 7D contract (Sections 8A.12/8A.13) is preserved. Alibaba video and
any OpenRouter video fallback each carry their **native generated audio facts**; the
fallback never converts provider-owned media into narration-owned media. **Edge TTS
remains the current narration provider** — Alibaba TTS is **not** authorized by this
scope.

**ComfyUI: DEFERRED / NOT IMPLEMENTED in Batch 1C.**

**Batch 1 acceptance.** Batch 1A, 1B, and 1C each close under the existing Phase 11C
validation and checkpoint discipline before Batch 2 work begins.

**Batch 2 boundary (unchanged by this amendment).**

```
BATCH 1
├── Batch 1A — Timeline Synchronization Fix
├── Batch 1B — Stock Visual Relevance Hardening
└── Batch 1C — Alibaba Cloud Provider Integration + OpenRouter Fallback
        ↓
BATCH 1 ACCEPTANCE
        ↓
BATCH 2
├── Semantic Visual Evaluation            (Section 8A.17 future target)
└── Production Timeline Abstraction       (Section 11 hardening requirement)
```

Nothing in Batch 1A, 1B, or 1C implements either Batch 2 capability.

**Status:** ✅ **Batch 1A / 1B / 1C IMPLEMENTED AND VERIFIED — accepted at Phase 11C
closure, 2026-10-24.** Core production/local creation checkpoints `8f3940a` and
`72113d8` are retained. Batch 1A's `reconcile_local_render_durations` has 8 passing
timeline tests; Batch 1B's metadata/query relevance hardening is covered by the
42-case Batch 1 hardening and 28-case visual-source suites. Batch 1C's Alibaba text/
reasoning/image/video adapters and classified OpenRouter fallback have 88 passing
Alibaba/follow-up/visual tests, plus durable provenance/accounting/logging evidence.
The 2026-09-12 **scope-only** amendment remains historical; its pending/no-code
status no longer describes the current checkout. ComfyUI stays deferred. Batch 2's
semantic pixel evaluation and general Production Timeline abstraction were not
implemented by 1A/1B/1C and remain separately scoped V1.x/future work.

### 4.11 PERSONAL AVM MATURITY — Phase 12 🟡

**Name:** Personal Automation.

**Next authorized roadmap phase:** **Phase 12 — Automated Publishing, Scheduling &
V1.x Operator Polish**. The original Personal Automation name and continuous-workload
objective below are preserved; operator-polish/Track 8–9 handoff items are follow-up
scope, not permission to bypass canonical execution or absorb Phase 13–15.

**Purpose:** Move AVM from an operator-driven production tool toward an **autonomous
production system**.

The system should eventually support commands such as:

> "Create a 60-episode series and keep producing until the planned workload is complete."

> "Produce one episode every day for 30 days."

The key objective is **continuous execution of a durable production workload with
minimal human intervention**. This is where the meaning of "AUTO" becomes operationally
significant.

**Question this phase answers:** *"Continue production automatically."*

**Status:** 🟡 NEXT — Phase 11C is signed off; automated publishing, scheduling and
V1.x operator-polish implementation require separately scoped Phase 12 tasks.

### 4.12 PERSONAL AVM MATURITY — Phase 13 🟡

**Name:** Autonomous Production Quality + Scale.

**Purpose:** Make autonomous production **reliable enough to operate at meaningful
workload sizes**.

Scope should eventually address areas such as:

- production quality control
- automated acceptance
- consistency checks
- failure detection
- production metrics
- throughput
- resource / cost awareness
- workload-scale behavior
- operational observability
- quality gates before publication

The conceptual distinction:

- Phase 12: *"AVM can keep producing."*
- Phase 13: *"AVM can keep producing reliably."*

**Question this phase answers:** *"Continue production reliably at meaningful scale."*

**Status:** 🟡 FUTURE.

### 4.13 PERSONAL AVM MATURITY — Phase 14 🟡

**Name:** Multi-Provider Intelligence.

**Purpose:** Move from merely **supporting** multiple visual providers to making
**evidence-based provider decisions**. Providers remain first-class and isolated.
**Google Flow remains a first-class provider.**

Potential future intelligence may consider:

- visual requirements
- scene complexity
- quality
- cost
- duration
- aspect ratio
- audio capabilities
- provider reliability
- observed production performance
- historical results

**Important:** automatic provider ranking and model selection are **not** implemented
now; they are future capabilities. Phase 14 deliberately comes **after** substantial
personal production because AVM needs real production evidence before making
sophisticated provider decisions.

**Question this phase answers:** *"Make intelligent provider decisions using production
evidence."*

**Status:** 🟡 FUTURE.

### 4.14 PERSONAL AVM MATURITY — Phase 15 🟡

**Name:** Recovery, Retry & Fallback.

**Purpose:** Introduce deliberate policies for what AVM does when production fails.
These are three distinct concepts:

| Concept | Meaning |
|---|---|
| **RECOVERY** | Resume interrupted or partially completed work using existing durable state |
| **RETRY** | Attempt the same operation again under defined conditions |
| **FALLBACK** | Switch to another provider or execution strategy under an **explicitly authorized policy** |

This phase is especially important because Phase 8 explicitly deferred cross-provider
retry/fallback policy to a future phase (`PHASE_8_PLANNING_EXECUTION_ARCHITECTURE.md`,
Deferred Items).

**Boundary reconciliation (2026-09-12 amendment).** The single fixed
**Alibaba-primary → OpenRouter-fallback** resilience policy — capability-aware,
failure-classified, with no parallel generation and no provider ranking — is explicitly
authorized for **Phase 11C Batch 1C** and is therefore no longer deferred. Phase 15
remains the home for everything **beyond** that fixed two-provider policy:
generalized/intelligent cross-provider fallback and routing, provider ranking, dynamic
routing policies, broader multi-provider orchestration, generalized recovery/retry/
substitution, and other future resilience capabilities. The safety principles below
bind the Batch 1C policy as well as Phase 15.

**Safety principles that survive into this phase:**

- Fallback must **never** silently become uncontrolled spending or provider substitution.
- OpenRouter paid access must remain **explicitly authorized** per its established
  safety model (free-first, paid disabled by default, no automatic free→paid fallback).
- Google Flow credit protections must remain intact.

**Question this phase answers:** *"Recover, retry, and use explicit fallback policies
when things go wrong."*

**Status:** 🟡 FUTURE.

### 4.15 PERSONAL AVM MATURITY — Phase 16+ 🟡

**Name:** Full Personal AVM.

**Purpose:** Integrate the preceding capabilities into a mature personal autonomous
production system.

At this stage the intended experience is approximately:

**USER:** "Create and publish this production."

**AVM:**

- interprets the command
- creates/updates the durable production plan
- executes the plan
- selects appropriate production mechanisms
- generates content
- generates visuals
- uses Google Flow where appropriate
- uses other providers where appropriate
- validates outputs
- assembles episodes
- performs QC
- publishes
- records durable state
- resumes interrupted work
- handles approved retry/fallback policies
- continues until the commanded workload is complete

This is the point at which AVM should genuinely behave like an **autonomous production
machine** rather than a collection of production tools.

**Question this phase answers:** *"Operate the complete personal AVM system."*

**Status:** 🟡 FUTURE / EVOLVING.

---

## 5. PROVEN AVM CORE — Maturity Milestone 🔵

**PROVEN AVM CORE** is **not necessarily a single implementation phase**. It is a
**maturity milestone**: the point at which the underlying production engine has been
sufficiently proven through real personal production to serve as the foundation for
productization.

The Core should be understood as including the reusable production capabilities
developed across the roadmap:

- planning
- durable production state
- execution
- visual generation
- Google Flow integration
- OpenRouter integration
- other provider integrations
- asset handling
- voice / audio
- captions
- assembly
- quality control
- publishing
- checkpoint / resume
- automation
- recovery
- provider intelligence

The exact boundaries of the Core will be defined by future architecture work — they are
**not** fixed by this roadmap.

**Status:** 🔵 FUTURE MILESTONE (reached after sufficient personal maturity).

---

## 6. PRODUCTIZATION — PUBLIC AVM 🔵

After the AVM Core is sufficiently proven, a separate **PRODUCTIZATION track** begins.

This track is **NOT a second production engine**. Its job is to expose **controlled,
safe, scalable access to the same underlying AVM Core**. The Public AVM must consume the
proven AVM Core rather than reimplementing production.

### 6.1 PUBLIC AVM PRODUCT — Discovery & Product Architecture 🟡

Define (at future product-architecture time, not now):

- public user model
- workspace / project model
- authentication
- permissions
- usage model
- subscription / monetization strategy
- product boundaries
- API / service boundaries
- customer-facing capabilities
- operator / admin capabilities
- isolation between customers
- cost and quota boundaries
- production ownership
- data retention
- security model

**Status:** 🟡 FUTURE.

### 6.2 PUBLIC AVM WEB / SAAS 🔵

Build the public web experience around the proven Core. The public user should not need
to understand provider-specific complexity.

A customer should be able to express a production command such as:

> "Create a 30-day horror short-form series and publish one episode per day."

The product translates that command into the **same underlying production system**.

The public UI is **simpler** than the personal operator interface:

| Personal interface may expose | Public interface exposes |
|---|---|
| advanced routing | only appropriate product-level controls |
| provider controls | simplified production commands |
| diagnostics | status, outputs, approvals |
| experimental capabilities | usage / account management |
| production internals | |

**Status:** 🔵 LONG-TERM / STRATEGIC.

### 6.3 PUBLIC AVM MOBILE APP 🔵

Eventually expose appropriate AVM functionality through mobile. The mobile application
**consumes the same underlying AVM production services / Core** rather than implementing
its own video-generation pipeline. The mobile app is a **control surface, not a second
production engine**.

Potential capabilities:

- issue production commands
- inspect production status
- review outputs
- approve / reject
- receive notifications
- monitor jobs
- manage publishing
- manage account / workspace
- review usage

Exact scope belongs to future product design.

**Status:** 🔵 LONG-TERM / STRATEGIC.

### 6.4 PUBLIC AVM API / DEVELOPER ACCESS 🔵

Longer-term, expose appropriate programmatic access to AVM capabilities.

Potential users:

- developers
- agencies
- businesses
- content operations
- integrations

Again, this sits **on top of the same AVM Core**.

**Status:** 🔵 LONG-TERM / STRATEGIC.

---

## 7. AVM CONTENT PLATFORM — Long Term 🔵

After the production engine and public product are mature, AVM can evolve beyond being
simply a video-production SaaS. Long-term possibilities include:

- persistent content operations
- multi-channel content management
- recurring content programs
- content calendars
- series management
- large-scale episode production
- publishing networks
- analytics
- optimization based on real performance
- reusable characters / worlds / brands
- content libraries
- templates
- organization / team workflows
- agency workflows
- ecosystem / API integrations
- intelligent content operations

The strategic idea:

```
VIDEO GENERATION
        ↓
VIDEO PRODUCTION
        ↓
AUTONOMOUS PRODUCTION
        ↓
CONTENT OPERATIONS
        ↓
CONTENT PLATFORM
```

These possibilities are **long-term strategic direction only** — they are not committed
implementation requirements.

**Status:** 🔵 LONG-TERM / STRATEGIC.

---

## 8. Cross-Cutting Architectural Principles

1. **ONE AVM CORE.** Personal and public products share the underlying production engine
   wherever practical.
2. **PERSONAL FIRST AS PROVING GROUND.** Personal production is where AVM proves
   reliability, quality, economics, automation, provider behavior, and operational
   workflows.
3. **PRODUCTIZE THE PROVEN.** Do not build a public SaaS shell around an unproven
   autonomous production system.
4. **UI IS NOT THE ENGINE.** Personal UI, SaaS Web UI, Mobile UI, and future API surfaces
   are control layers over the AVM Core.
5. **PROVIDER AGNOSTICISM.** The Core must not become structurally dependent on one
   visual provider.
6. **GOOGLE FLOW REMAINS FIRST-CLASS.** Google Flow is not being removed, deprecated, or
   replaced by this roadmap. It remains a first-class visual production provider
   throughout.
7. **SAFETY BEFORE AUTONOMY.** Autonomy must never mean uncontrolled retries, uncontrolled
   spending, or silent provider substitution.
8. **DURABLE STATE.** Production commands, plans, execution state, checkpoints, and
   results become progressively more durable and inspectable.
9. **EVIDENCE-DRIVEN INTELLIGENCE.** Provider intelligence and production optimization
   are based on real production evidence rather than assumptions.
10. **PHASE DISCIPLINE.** One phase at a time. Each phase gets: specification / design
    gate → implementation → audit → acceptance, before the next phase begins.
11. **NO SPECULATIVE IMPLEMENTATION.** Future phases are roadmap items, not permission to
    implement them early.
12. **PERSONAL ADVANTAGE.** The personal/operator interface may remain more powerful than
    the public customer interface.
13. **MODE ≠ ROUTE ≠ PROVIDER/MODEL.** Production modes, production routes, and
    providers/models are separate architectural dimensions and must never be treated as
    interchangeable (Section 8A.1/8A.2).
14. **CONDITIONAL AUDIO.** Generated media may carry native audio; audio generation is
    conditional, never automatically mandatory (Section 8A.12; frozen Phase 7D contract).
15. **ONE COHERENT TIMELINE (TARGET).** Measured narration, native generated audio, scene
    timing, captions, and final assembly duration must eventually reconcile into one
    coherent production timeline; today's divergence is a recorded future hardening
    requirement (Section 11), not a completed capability.
16. **LOCAL ≠ THIS PC FOREVER.** "Local/owned compute" is an economic/execution strategy,
    not a permanently fixed physical machine; the same route may later run on owned
    servers or cloud GPU infrastructure with unchanged production semantics (Section
    8A.10).
17. **PROVIDER COST ≠ AVM SERVICE VALUE.** Provider economics and AVM credit/service
    economics are distinct; a zero-cost provider does not imply zero-cost AVM service
    value (Section 8A.16).

---

## 8A. CANONICAL PRODUCTION ARCHITECTURE — Formalized Conceptual Model

> Added by the **2026-09-08 architectural formalization amendment** (Section 13). This
> section is **roadmap documentation only**. It formalizes established and discovered
> architecture as explicit roadmap authority. It does **not** authorize implementation,
> does **not** create a new phase, does **not** move work between phases, and does
> **not** modify any frozen phase specification or contract.
>
> Status vocabulary used here: **IMPLEMENTED AND VERIFIED** (evidence exists),
> **ARCHITECTURALLY DEFINED** (decided target; not built), **PLANNED** (assigned to an
> existing future phase or controlled track), **FUTURE** (intended; unassigned),
> **CURRENT LIMITATION** (known present-day gap), **EXPERIMENTAL / UNDER EVALUATION**.

### 8A.1 Canonical production conceptual hierarchy (ARCHITECTURALLY DEFINED)

AVM production is understood through the following conceptual hierarchy. Each layer
answers a different question and must never be collapsed into another:

```
Production Intent          — what does the user actually want to produce?
        ↓
Production Mode            — what shape of production is this?
        ↓
Production Bible           — (when continuity requires it) what persistent
        ↓                    creative/continuity context governs it?
Production Route           — how is it produced?
        ↓
Provider / Model           — who/what service, and which model, execute a capability?
        ↓
Media Strategy             — what media types are used, per scene?
        ↓
Audio Strategy             — what audio exists and who/what owns it?
        ↓
Motion / Video Engine      — how is still/generated media animated or rendered?
        ↓
Assembly                   — how are the resulting assets combined?
        ↓
Quality / Validation       — how is correctness proven?
        ↓
Distribution               — where/how is the finished asset delivered/published?
```

**CRITICAL — these concepts are never interchangeable:**

> **Production Mode ≠ Production Route ≠ Provider/Model.**

- The **mode** describes *what is being produced* (one video, many independent videos,
  or a connected series).
- The **route** describes *how it is produced* (the production pathway).
- The **provider/model** describes *who executes a capability and with which model*.

Any mode may combine with (almost) any route, any route may serve multiple modes, and
each route may draw on multiple providers/models (see 8A.7 for the composition rule).

### 8A.2 Terminology control — critical architectural rule (ARCHITECTURALLY DEFINED)

To prevent future confusion, the following terms are separate architectural dimensions:

| Term | Question it answers |
|---|---|
| **MODE** | What is being produced? |
| **ROUTE** | How is it produced? |
| **PROVIDER** | Who/what service executes a capability? |
| **MODEL** | Which AI model is used? |
| **PRODUCTION BIBLE** | What persistent creative/continuity context governs the production? |
| **MEDIA STRATEGY** | What media types are used? |
| **AUDIO STRATEGY** | What audio exists and who/what owns it? |
| **MOTION ENGINE** | How is still/generated media animated or rendered? |
| **ASSEMBLY** | How are the resulting assets combined? |
| **DISTRIBUTION** | Where/how is the finished asset delivered/published? |

**Disambiguation of existing "routing" language.** The roadmap and codebase have used
"route"/"routing" for two different things. A **Production Route** (8A.6) is the
strategic production pathway. **Scene-level visual routing** (`visual_scene_routing`,
Phases 6/7 hybrid routing) is a lower-level per-scene mechanism for choosing between
visual sources/providers. Neither term implies the other, and neither replaces the
Phase 8 provider execution boundary. Earlier informal labels in this roadmap (for
example the Section 1.1 diagram's "Flow / OpenRouter / Gemini / Local" row) are
provider/route labels, not mode definitions; where any conflict arises, this section is
the controlling vocabulary.

**Disambiguation of "production intent".** Phase 9's durable **production intent**
(`productions/<production_id>/production.json`) is the *storage* of what AVM intends to
produce. **Production Intent** as defined in 8A.3 is the *upstream interpretation of the
user's brief* that determines mode, continuity, route, provider, and strategy
requirements. The former is a durable artifact; the latter is an interpretation
capability. They are related but not identical.

### 8A.3 Production Intent (ARCHITECTURALLY DEFINED — future capability)

**Production Intent** represents what the user actually wants to produce. It is
interpreted from the user's brief/request **before** AVM determines:

- production mode;
- continuity requirements;
- route;
- provider/model;
- media strategy;
- audio strategy;
- output requirements;
- distribution requirements.

Example interpretation (conceptual only — not an implemented behavior):

> Brief: "Make 60 episodes about a Nigerian family with recurring characters."

| Layer | Resolved toward |
|---|---|
| Production Intent | connected episodic story |
| Production Mode | Series / Episodic (Mode C) |
| Production Bible | required |
| Continuity | required |
| Route | selected according to production requirements |
| Provider/Model | selected according to route/capability/policy |

**Current state (truthful):** Phase 9's durable production plan and the Phase 11C
product workflow already *store and represent* intent artifacts (Production, Command,
Units, optional Bible). The *intelligent interpretation of free-form briefs into
mode/continuity/route/provider/strategy decisions* is a **future capability**. It is
not part of Phase 11C unless separately authorized, and nothing here authorizes
implementation. Its eventual home is the production-intent/plan side of the existing
architecture (Phase 9 semantics); provider-related selection belongs to the Phase 14
boundary; distribution interpretation belongs to the publishing boundary.

### 8A.4 Formal production modes (ARCHITECTURALLY DEFINED)

All three modes share the **same underlying AVM production architecture** — Phase 9
Production/Units, Phase 10 plan-backed execution, the Phase 8 provider boundary, and
the existing assembly/QC path. There are **no separate mode engines**. Mode is a
property of the production request, not a different codebase.

**MODE A — SINGLE VIDEO.** One standalone video. Continuity between separate
productions is not inherently required. (Represented today by one Production with one
Unit over the existing single-project engine; Phase 11C integration is COMPLETE.)

**MODE B — MULTIPLE INDEPENDENT VIDEOS.** Multiple videos created as one production
request, but each video is independent. Examples: 20 motivational videos; 30 unrelated
social videos; bulk content generation. Each unit may have a different story,
characters, visual identity, assets, and even a different provider/model. A Production
Bible is **not** inherently required. (Production/Unit schema, unit-count creation
and serial execution are V1 COMPLETE; 19 Multiple Independent and 21 Series/Episodic
contract tests pass. Additional parallel/batch product expansion remains V1.x.)

**MODE C — SERIES / EPISODIC PRODUCTION.** Multiple connected videos/episodes where
continuity matters. Examples: Episode 1 → Episode 2 → Episode 3…; a 60-episode
narrative; recurring characters/world. A Production Bible and continuity become
**required when the production intent demands persistent continuity** (8A.5).
(V1 episode metadata, dependency-aware units, and optional shared Bible/context are
implemented through the same executor. Automatic continuity enforcement/intelligence
remains V1.x / DECISION REQUIRED and must not be inferred from the V1 sign-off.)

### 8A.5 Production Bible — architectural role (ARCHITECTURALLY DEFINED)

The Production Bible is a **persistent creative/continuity/context authority for
productions where continuity matters.** It is **NOT**:

- a production route;
- a provider;
- a model;
- an execution engine;
- a media renderer;
- a router;
- a state machine;
- a replacement for manifests;
- a second source of execution truth.

This matches the frozen Phase 9 decision that the Bible is optional shared creative
context — not execution state, provider configuration, a checkpoint, or a manifest.

**Potential Bible domains** (extensible per the Phase 9 canonical-sections decision):
characters; character identity; character appearance; names; personalities;
relationships; locations; world rules; recurring objects; wardrobe; visual style;
cinematography; tone; narrative rules; voice characteristics; continuity constraints.

**Per-mode guidance:**

| Mode | Bible |
|---|---|
| Single Video | normally unnecessary unless continuity/context is explicitly requested |
| Multiple Independent Videos | normally unnecessary |
| Series / Episodic | normally required where continuity is intrinsic to the production |

**Story Intelligence ownership (FUTURE):** Story Intelligence should eventually
create/maintain the Bible rather than forcing normal users to manually construct it.
Automatic Bible creation and mandatory Bible/continuity gates remain **V1.x /
DECISION REQUIRED**. Founder/CTO V1 acceptance preserves the Core's optional Bible
contract; storing episode/shared context is not automatic continuity enforcement.

### 8A.6 Formal production routes (ARCHITECTURALLY DEFINED)

Routes are **distinct from modes** (8A.1/8A.7). The recognized routes:

**ROUTE 1 — LOCAL AI-ASSISTED PRODUCTION.** A low-cost/free-oriented route combining
local and free/low-cost services where practical. Potential components: AI Story
Intelligence; OpenRouter; local/open AI models; free AI image generation where
available; free stock imagery; ComfyUI (8A.9); ImageMotion; local TTS/audio; FFmpeg;
captions; assembly; quality validation. The route should be capable of extremely low
operating cost when free/local capabilities are available. **Truthfulness rule:** AVM
must NOT claim every provider/model in this route is permanently free — free provider
availability can change at any time (already experienced in Phases 7D/7E). *Status:
core primitives and V1 route-level consolidation implemented and verified
(ImageMotion, stock sources, configured TTS, FFmpeg, captions, measured local
narration synchronization and accepted completed project evidence).* No claim is
made that externally hosted TTS or all AI generation is offline/free.

**ROUTE 2 — FULL AI VIDEO PRODUCTION.** Production primarily using AI-generated video
rather than static-image-plus-motion workflows. Potential models/providers: Veo;
Seedance; Wan; Kling; other future AI video models; OpenRouter-accessible models where
applicable. AVM should eventually select an appropriate model based on: visual
quality; scene requirements; motion requirements; duration; modality; provider
availability; model capability; cost; latency; user budget; user settings; and
local/cloud availability. *Status: Gemini Omni (7C) and the OpenRouter video gateway
(7E) and Batch 1C's Alibaba video adapters/fallback are integrated first-class
providers; V1 canonical capability routing is verified, while quality/economics
optimization remains Phase 14. The additional models above are FUTURE candidates,
not commitments; external configured-model access is a runtime prerequisite.*

**ROUTE 3 — GOOGLE FLOW PRODUCTION.** Google Flow remains a **specialized production
route** and a first-class provider (Section 8, principle 6). It is **not** collapsed
into generic provider/model semantics. Conceptually: Story/Scene Intelligence → Google
Flow → resulting media → AVM post-processing → applicable audio handling → captions →
assembly → validation → output. *Status: implemented and verified (Phases 5A/8);
credit firewall intact.*

**ROUTE 4 — LONG-FORM REPURPOSING.** Existing long-form content: 45–60 minute video →
transcription → content understanding → identify high-value segments →
generate/restructure short-form content → captions → platform-specific outputs →
final shorts. This is **fundamentally different from generating video from scratch**.
*Status: Track 8 V1 COMPLETE under the Founder/CTO 2026-10-24 decision.* Source
registration, timestamped transcription, validated source ranges, deterministic/AI
candidate moment selection through shared grounded planners, and derived
`ProductionUnit` lineage are implemented. Operator-selected segments remain
explicit; no dedicated candidate-selection endpoint or unattended clip engine is
claimed. Canonical execution/publishing requires an accepted completed project
binding; direct clip-rendering shortcuts bypassing that path remain **V1.x**.
External video intake alone (7B) is still explicitly not repurposing.

**ROUTE 5 — VIDEO UPLOAD → AI NARRATION / TRANSFORMATION.** User supplies an existing
video. Conceptually: upload → analyze → understand → generate narration/script where
appropriate → generate/obtain voice where required → synchronize → captions → assembly
→ output. Potential uses: AI narration; replacement narration; educational commentary;
documentary-style transformation; social-media transformation. *Status: Track 9 V1
COMPLETE.* Real **Summary Standard** text+JSON grounded planning and **Summary Pro**
windowed visual understanding/grounded narration feed the existing TTS authority
and `narrated_summary.mp4` assembly. Core `/api` and K3 `SummaryStudio` are integrated
with real execution by default and explicit opt-in sandbox execution. Model users
select canonical identities; providers/wire IDs remain internal. Summary sidecars
are inspectable/downloadable through `/summary-video` and `/summary-video/media`;
automatic promotion to a `ProjectManifest` publication record without an accepted
completed project binding remains **V1.x** (`422 SUMMARY_HANDOFF_REQUIRED` in V1).
Plan-driven TTS by itself remains distinct from this complete source workflow.

**ROUTE 6 — PUBLISHING / DISTRIBUTION.** Publishing remains **downstream of
production**. Conceptually: Finished Asset → Review → Manual / Assisted / Automated
Publishing. Potential destinations: YouTube; TikTok; Instagram/Reels; future
platforms; a future AVM mobile app. Publishing automation remains subject to existing
phase boundaries and is not pulled forward. *Status: manual YouTube/TikTok publishing
is V1 COMPLETE in Core and K3 for accepted completed projects; automated publishing
and scheduling remain Phase 12.*

### 8A.7 Mode + route composition (ARCHITECTURALLY DEFINED — mandatory distinction)

Modes and routes are **independent dimensions** and can be combined. Valid examples:

- **Mode:** Series / Episodic + **Route:** Local AI-Assisted → possible stack:
  Production Bible → Story Intelligence → OpenRouter → ComfyUI → stock sources where
  appropriate → ImageMotion → local audio where required → FFmpeg → QC.
- **Mode:** Series / Episodic + **Route:** Full AI Video → possible provider: Veo.
- **Mode:** Multiple Independent Videos + **Route:** Local AI-Assisted.

The **same mode can use different routes**, and the **same route can serve multiple
modes**. This distinction is architecturally mandatory.

### 8A.8 Provider / Model layer (ARCHITECTURALLY DEFINED)

Provider/Model is a layer **below** Production Route. Examples: Alibaba Cloud Model
Studio (Qwen text/reasoning/vision, Qwen-Image, Wan/HappyHorse video — Batch 1C
primary); OpenRouter (Batch 1C fallback); ComfyUI/local models (deferred); Veo;
Seedance; Wan; Kling; future providers/models.

- ComfyUI itself is **not** a production mode (8A.9).
- Route + provider + model must never be collapsed into one concept.

**Verified V1 canonical model-selection semantics (Tracks 5/9):**

- Users select **canonical models**, never provider names or provider wire IDs.
  Summary Standard requires text + structured JSON; Summary Pro requires supported
  visual understanding, with native-video vs frame-sequence routes recorded
  distinctly. Eligible model metadata is projected from the one Core registry.
- Explicit selection resolves only the requested canonical identity; provider
  failover stays within its declared eligible bindings. An unavailable route cannot
  silently change the selected model or return synthetic narration.
- Auto is deterministic capability/eligibility metadata selection, not a claim of
  cheapest/fastest/best output. Core/K3 readiness is truthful local configuration:
  `ready`, `not_configured`, or `unknown`; configured credentials are not proof of
  quota, remaining credit, provider uptime, or paid access.

**Additional SaaS selection/credit semantics — PLANNED, not implemented:**

- AVM may automatically choose an eligible provider/model;
- users may explicitly select a canonical model under approved product policy;
  provider choice remains internal, not a public provider/wire-ID picker;
- explicit model selection consumes the corresponding AVM credit cost;
- model/provider capability and cost must remain distinct from route semantics.

Personal/operator UI may eventually expose actual underlying provider economics
(8A.15). Automatic model-selection intelligence remains Phase 14; billing/credit
consumption remains future SaaS work (DECISION REQUIRED). Nothing here implements
either.

### 8A.9 ComfyUI — optional local/owned generation engine (ARCHITECTURALLY DEFINED — integration DEFERRED; superseded as the Batch 1C target by the 2026-09-12 Alibaba provider amendment)

ComfyUI is added to the roadmap as an **optional local/owned media-generation
engine**. It can potentially provide image generation, image-to-video, video
generation, and other compatible workflows/models in the future.

ComfyUI must integrate through **existing AVM architectural seams** — the Phase 8
provider execution boundary and the existing visual-source/intake architecture —
rather than creating a second production engine:

```
AVM Local AI-Assisted Route
        ↓
ComfyUI
        ↓
local/owned model/workflow
        ↓
media
        ↓
existing AVM intake/validation/assembly
```

ComfyUI must remain **optional** and must **not** become a hard dependency of AVM.
The architecture must permit all of the following states without architectural damage:

- ComfyUI absent;
- installed but unconfigured;
- available locally;
- running on owned hardware;
- running on cloud GPU infrastructure (8A.10);
- temporarily unavailable;
- a workflow/model unavailable;
- generation failure;
- insufficient compute/VRAM.

AVM should eventually capability-gate the route and preserve truthful failure/
fall-through behavior consistent with the established provider/source architecture (a
failed source reports failure truthfully; fall-through follows the existing routing
ladder — it is **not** automatic cross-provider substitution).

**Batch 1C assignment — SUPERSEDED (2026-09-12 amendment).** The 2026-09-08 scope
amendment assigned ComfyUI integration to Phase 11C Batch 1C. The **2026-09-12
amendment supersedes that assignment**: following the final provider-architecture
investigation and the Alibaba Cloud live-validation gate, **Batch 1C now targets
Alibaba Cloud Model Studio as AVM's primary provider with OpenRouter fallback**, and
**ComfyUI integration is DEFERRED / NOT IMPLEMENTED in Batch 1C**. ComfyUI remains an
optional local/owned generation engine under this subsection; every constraint here
(optional, capability-gated, fail-open, truthful failure/fall-through, no hard
dependency, all availability states non-breaking) continues to apply and will bind any
future ComfyUI implementation. The historical 2026-09-08 assignment record is preserved
in Section 13, and Section 8A.19's factual description of the current desktop
installation is unchanged.

### 8A.10 Local compute vs cloud compute (ARCHITECTURALLY DEFINED)

"Local" or "owned compute" describes an **economic/execution strategy, not a
permanently fixed physical machine**. The same local/owned route may eventually
execute on:

```
Operator PC
    ↓
Owned workstation
    ↓
Dedicated GPU server
    ↓
Cloud GPU
    ↓
same AVM Core / same production semantics
```

Moving ComfyUI (or any local capability) from a desktop to cloud GPU infrastructure
must **not** require redesigning AVM's production architecture. This supports the
future strategy of hosting AVM and ComfyUI on monthly GPU infrastructure rather than
paying per-generation cloud video credits, where economically advantageous.

**Do not implement cloud infrastructure now.**

### 8A.11 Media strategy (ARCHITECTURALLY DEFINED)

AVM may construct media using different combinations of: generated images; stock
images; user-provided images; generated video; uploaded video; image-to-video; local
generation; cloud generation. Media strategy is **separate from production mode and
route**, and a single production may use multiple media types — even within one
production:

- Scene 1 → generated image + motion
- Scene 2 → stock image + motion
- Scene 3 → generated AI video
- Scene 4 → user-provided video

No scene is required to use the same media type as any other. (Existing basis: Phase
6A pluggable visual providers, Phase 6F automatic visual sources, Phase 7B external
video intake, Phase 8 provider boundary. Richer per-scene strategy intelligence is
future.)

### 8A.12 Generated video may contain native audio — conditional audio generation (ARCHITECTURALLY DEFINED; Phase 7D preserved)

A generated video may contain **VIDEO + NATIVE AUDIO**. Therefore AVM must **not**
assume "video generated → always generate external TTS". AVM must eventually
inspect/apply an explicit audio policy. Possible policies include:

1. retain native generated audio;
2. suppress native audio;
3. replace native audio;
4. add external narration;
5. mix native audio + narration;
6. use external sound design/music;
7. intentionally produce silent video.

The existence of native audio is a **first-class media property** (FFprobe-observable
audio facts, per Phase 7D). **Audio generation is conditional, not automatically
mandatory.**

**Phase 7D preservation (frozen contract unchanged):** the Phase 7D audio contract
already establishes that a media provider may return optional audio, that visual
providers do not decide whether that audio competes with narration, and that final
audio handling belongs to the assembly/audio-policy layer (`narration_only`,
`native_only`, `native_plus_narration`; background music as the existing additive
capability). Policies 4–7 above are **future policy directions** requiring their own
design; they neither modify nor weaken the frozen Phase 7D contract. No audio-policy
changes are implemented by this amendment.

### 8A.13 Audio / visual ownership rule (ARCHITECTURALLY DEFINED)

AVM must distinguish between: media generation; audio generation; audio ownership;
audio mixing; and timeline authority.

- A generated-video provider may own the initial audio.
- An external TTS provider may own narration.
- A music/sound-design provider may contribute additional audio.
- Assembly ultimately produces the final media output according to the applicable
  audio policy.

**No layer may silently assume ownership of all audio.** (Consistent with the Phase 7D
boundary: providers produce media with optional audio; assembly inspects, decides, and
mixes.)

### 8A.14 Routing and fallback — preserved boundaries (ARCHITECTURALLY DEFINED)

Existing routing/fallback safety principles are preserved, with one explicitly
authorized Batch 1C addition (2026-09-12 amendment) and the bounded Track 9
canonical-model routing accepted at V1 closure (2026-10-24):

- Nothing in this roadmap implies "if provider A fails, automatically substitute
  provider B" except where an explicitly authorized policy permits it. **The single
  fixed Alibaba-primary → OpenRouter-fallback policy is now such an explicitly
  authorized policy (Phase 11C Batch 1C, 2026-09-12).** Track 9 also permits provider
  failover **only within one selected canonical model's eligible bindings**.
  Model-first summary requests do not use capability similarity as permission to
  substitute another model via the legacy provider-default fallback.
- The Phase 8 no-fallback contract continues to prohibit **blind/generalized**
  automatic substitution and any automatic free→paid fallback. The authorized Batch 1C
  policy is **capability-aware and failure-classified**: OpenRouter is invoked **only
  after** Alibaba fails in a fallback-eligible way (provider outage/unavailability,
  rate limiting, quota exhaustion, provider-side 5xx, transport failure/timeout,
  model/capability temporarily unavailable) — never on AVM's own malformed request,
  invalid duration/size, contract error, local validation failure caused by AVM's own
  output, or credential misuse (e.g. an `sk-sp-` Token Plan key supplied to AVM). There
  is **no parallel generation** of the same artifact, and OpenRouter's existing
  free-first / paid-explicitly-authorized safety model is preserved. **Generalized or
  intelligent cross-provider fallback, provider ranking, and dynamic routing remain
  Phase 14/15.**
- Provider **availability**, **capability**, **fallback**, **retry**, and
  **substitution** remain separate concepts.
- Track 9's real Standard planner invokes the resolved text/JSON adapter rather than
  a missing bridge or forced mock. Explicit models retain their identity, metadata
  Auto remains deterministic, and local `ready` / `not_configured` / `unknown`
  projections never expose provider/wire IDs or invent remaining quota/cost.
  Invalid model JSON/grounding fails without a mock downgrade or automatic resend.
- The architecture should eventually permit broader **capability-aware routing**
  (Phase 14/15 territory) without weakening existing truthfulness/scope guarantees —
  including the visual-source fall-through ladder, which reports truthful failure
  states rather than masking them.

### 8A.15 Personal/Operator UI vs SaaS exposure (ARCHITECTURALLY DEFINED)

The underlying AVM production architecture is **shared** (ONE AVM CORE — Section 8,
principle 1).

**Personal / Operator UI** may expose: all available production modes; available
production routes; local/owned infrastructure status; ComfyUI; providers/models;
actual provider economics; operational diagnostics; experimental capabilities.

**SaaS** may expose a controlled subset according to: plan; credits; provider
availability; capability; account settings; monetization policy. SaaS may eventually
allow automatic model selection, explicit model selection, model-specific AVM credit
consumption, series production, Production Bible usage, and publishing capabilities.

No SaaS billing, account policy, tenancy, or infrastructure is implemented by this
amendment (Phase 11C non-goals and Section 6 boundaries remain intact).

### 8A.16 Provider cost vs AVM service value (ARCHITECTURALLY DEFINED — no billing)

Strategic distinction: **provider cost ≠ AVM service value.** A provider may be:
free; open-source/local; low-cost; pay-per-use; subscription-based; temporarily free;
rate-limited. AVM SaaS may still charge AVM credits for orchestration/service value
even where the underlying provider has zero direct API cost.

This complements the Phase 11C commercial direction without inventing prices, tiers,
exchange rates, or billing rules — those remain **DECISION REQUIRED**. No billing is
implemented by this amendment.

### 8A.17 Visual relevance — current vs future (CURRENT LIMITATION / FUTURE)

**CURRENT (implemented and verified — Phase 11C Batch 1):** stock/auto visual sourcing
applies a lightweight textual relevance gate:

```
candidate retrieval → search terms → provider metadata → textual token overlap
                    → relevance gate → candidate selection
```

- zero-overlap candidates are rejected;
- candidates are ranked by relevance (technical width only as tie-break/final filter);
- relaxation/fall-through behavior is preserved;
- regression coverage exists (`tests/test_phase11c_batch1_hardening.py`,
  `tests/test_visual_sources.py`); the suite passed.

This is useful but **limited**. The forensic audit established that the system does
**not** inspect image pixels for semantic relevance. The textual mechanism can still
accept an image whose metadata overlaps the search terms while the actual image is
semantically wrong for the scene. This is a recorded **current limitation** — it is
textual relevance, **not** full semantic image understanding.

**FUTURE TARGET (planned/future — no technology mandated here, no implementation
now):**

```
Full Scene Intent → richer search query → candidate retrieval → textual relevance
                  → actual visual/semantic evaluation → quality evaluation → best asset
```

Potential future semantic evaluation includes: image embeddings; CLIP-like
similarity; vision-model evaluation; subject detection; action detection; environment
matching; character/identity matching; composition matching; aesthetic/quality
evaluation. Specific technologies belong to future design gates.

Phase 11C Batch 1B (2026-09-08 scope amendment) hardens the metadata/search layer —
query construction and metadata-based candidate relevance — within the CURRENT
textual-relevance domain described above; the visual/semantic evaluation future
target remains **Batch 2**.

### 8A.18 Rich scene intent preservation (CURRENT LIMITATION / FUTURE IMPROVEMENT)

Forensic finding: scene planning can contain subject; identity; action; environment;
attributes; emotion; time period; camera; composition; lighting; visual style;
purpose; and motion intent — but the current stock-search path can flatten this into
a small number of search tokens, losing intent before retrieval/evaluation.

Future visual intelligence should **preserve richer scene intent** when constructing
retrieval/evaluation inputs. The existing search-term abstraction is **not removed**
by this amendment; changing it requires an explicitly approved implementation task.
This is a future intelligence improvement.

### 8A.19 ComfyUI desktop installation — experimental status; hardware capability gating (EXPERIMENTAL)

The current ComfyUI desktop installation is recorded as an **experimental/local
capability environment and proof-of-concept execution environment**. It is **NOT**:

- a mandatory AVM dependency;
- proof that all local models are hardware-feasible;
- a finalized production route implementation;
- a replacement for AVM's provider abstraction.

Hardware constraints, VRAM constraints, model requirements, workflow requirements, and
performance must remain **capability-gated**. No assumption about the current RTX 2050
hardware is to be hard-coded as universal.

**Capability states (ARCHITECTURALLY DEFINED):** a machine may be installed;
configured; available; insufficient; temporarily unavailable. Local generation must be
capability-aware and must **not** become a mandatory dependency of the entire AVM
system. Future cloud GPU deployment (8A.10) may provide substantially larger compute
capacity while preserving the same AVM architecture. No hardware detection or cloud
deployment is implemented by this amendment.

Phase 11C Batch 1C was originally assigned the ComfyUI integration (2026-09-08 scope
amendment); the **2026-09-12 amendment supersedes that assignment** — Batch 1C now
targets Alibaba Cloud provider integration + OpenRouter fallback, and **ComfyUI
integration is deferred** (see 8A.9). The capability-gating requirements of this
subsection continue to bind any future ComfyUI implementation, and the current desktop
installation remains an experimental capability environment — not a dependency and not
proof of universal feasibility.

### 8A.20 Canonical architecture summary (ARCHITECTURALLY DEFINED)

```
AUTO VIDEO MAKER
│
├── PRODUCTION INTENT                      (8A.3 — future interpretation capability)
├── PRODUCTION MODE                        (8A.4 — A/B/C)
│   ├── Single Video
│   ├── Multiple Independent Videos
│   └── Series / Episodic
├── PRODUCTION BIBLE                       (8A.5 — required when continuity demands it)
├── PRODUCTION ROUTE                       (8A.6)
│   ├── Local AI-Assisted
│   ├── Full AI Video
│   ├── Google Flow
│   ├── Long-Form Repurposing
│   ├── Video Upload → AI Narration/Transformation
│   └── Publishing / Distribution
├── PROVIDER / MODEL                       (8A.8)
│   ├── Alibaba Cloud Model Studio (Batch 1C primary)
│   ├── OpenRouter (Batch 1C fallback)
│   ├── ComfyUI / local models (deferred)
│   ├── Veo
│   ├── Seedance
│   ├── Wan
│   ├── Kling
│   └── future providers/models
├── MEDIA STRATEGY                         (8A.11)
│   ├── AI images
│   ├── stock images
│   ├── user uploads
│   ├── AI video
│   ├── image-to-video
│   └── uploaded/generated media
├── AUDIO STRATEGY                         (8A.12/8A.13)
│   ├── native generated audio
│   ├── narration
│   ├── TTS
│   ├── music
│   ├── sound design
│   ├── mixed audio
│   └── intentional silence
├── MOTION / VIDEO ENGINE
│   ├── ImageMotion
│   ├── ComfyUI workflows
│   └── AI video models
├── ASSEMBLY
│   └── FFmpeg / future engines
├── QUALITY / VALIDATION
└── DISTRIBUTION                           (Route 6)
    ├── Manual
    ├── Assisted
    ├── Automated (Phase 12)
    ├── YouTube
    ├── TikTok
    ├── Instagram/Reels
    └── future platforms/mobile
```

Where a layer's future implementation belongs to an existing future phase, that phase
remains its home (Phase 12 automation/publishing automation; Phase 13 quality/scale;
Phase 14 provider intelligence; Phase 15 generalized/intelligent fallback policy — the
single fixed Alibaba-primary → OpenRouter-fallback policy is authorized in Phase 11C
Batch 1C by the 2026-09-12 amendment). This section adds no new phase and authorizes no
implementation.

---

## 9. Autonomy Maturity Model

A concise conceptual ladder. Levels are **not** claimed as achieved unless stated.

| Level | Meaning | Status |
|---|---|---|
| **LEVEL 0** | Manual production | Historical starting point |
| **LEVEL 1** | AVM can execute individual production steps | **Existing capability** (individual stages/steps execute under operator control) |
| **LEVEL 2** | AVM can execute a defined production workflow | **Existing capability** (single-project pipeline: content → voice → scenes → captions → assembly) |
| **LEVEL 3** | AVM can execute a durable production plan | ✅ Achieved — Phase 9 + Phase 10 (completed) |
| **LEVEL 4** | AVM can continue production autonomously | 🟡 Future — Phase 12 |
| **LEVEL 5** | AVM can autonomously maintain quality, recover from failures, and optimize provider usage under explicit policies | 🟡 Future — Phase 13 + Phase 14 + Phase 15 |
| **LEVEL 6** | AVM can operate ongoing content-production workloads | 🔵 Intended end state — Phase 16+ and beyond |

Existing capabilities are grounded in the completed phases (5A–5J, 6A–6F, 7A–7E, 8,
9, 10, and 11). Future capabilities require their own specifications. Nothing above
LEVEL 3 is claimed as achieved today.

---

## 10. Current Position

| Track | Position |
|---|---|
| FOUNDATION | ✅ COMPLETE |
| PERSONAL PRODUCTION ENGINE | ✅ COMPLETE THROUGH PHASE 8 |
| PERSONAL AVM V1 PRODUCT — PHASE 11C | ✅ COMPLETE — TRACKS 1–14 VERIFIED & SIGNED OFF (2026-10-24) |
| PERSONAL AVM MATURITY | 🟡 NOT YET COMPLETE |
| NEXT ROADMAP PHASE | 🟡 PHASE 12 — AUTOMATED PUBLISHING, SCHEDULING & V1.x OPERATOR POLISH |
| PROVEN AVM CORE | 🔵 FUTURE MILESTONE |
| PUBLIC AVM PRODUCT | 🔵 FUTURE |
| AVM CONTENT PLATFORM | 🔵 LONG TERM |

The current Core/K3 product integrates provider-agnostic scene execution, durable
checkpoints/resume, hybrid routing, explicit audio policy/captions/assembly,
canonical model selection, source/transcript/segment/derived-unit lineage,
real Standard and Pro narrated summaries, and verified manual YouTube/TikTok
publishing. Phase 9 intent, Phase 10 executor/binding authority, Phase 11/accepted
11B Command Center, and Phase 11C Tracks 1–14 are complete for the signed-off V1
scope. This still corresponds to **LEVEL 3**, not autonomous LEVEL 4–6: no scheduler,
unattended publishing, generalized recovery, SaaS billing/auth or native mobile
product has been silently introduced.

**Current phase disposition:**

> **PHASE 11C COMPLETE (Tracks 1–14 Verified & Signed Off)** ✅ — **2026-10-24**.
> Core: **1,904 passed, 0 failed, 1 skipped** (opt-in live E2E), zero external
> network attempts via `tools/run_hermetic_pytest.py`; K3: **86 passed, 0 failed**,
> production build exit 0 / zero errors. Code checkpoints: Core `a1bb5ed`, K3
> `b6a7f3d`; formal V1/V1.x decision and amendment record below.

**The immediate next roadmap phase is:**

> **PHASE 12 — AUTOMATED PUBLISHING, SCHEDULING & V1.x OPERATOR POLISH** 🟡.

Phase 11B's bounded specification/recovered implementation are accepted (`dd21b26`,
`cc551ca`); Batch 1A/1B/1C implementation and validation are reconciled in Section
4.10. Phase 11C sign-off is the controlled V1 baseline, not a greenfield rebuild or
permission for future work. Phase 12 receives separately scoped implementation and
validation gates. Track 8 bypass clip rendering and Track 9 automatic standalone
summary-to-project publishing promotion remain V1.x, while accepted bound projects
continue through the canonical execution/manual publishing path.

---

## 11. Known Deferred Items / Future Hardening

These are recorded so they are not lost. They are **not** reasons to reopen completed
phases, and they do **not** automatically become new phases unless explicitly decided
later:

- Phase 8 non-blocking concern: Flow result-tail duplication (inline validation +
  completion checkpoint instead of the shared boundary helpers)
- Phase 8 non-blocking concern: `build_scene_request` provider-name conditional
- Phase 8 non-blocking concern: OpenRouter `audio_facts` null/omission cosmetic
  checkpoint difference
- Historical Gemini generation-cost reporting truthfulness issue (zero-cost figures
  recorded for a cost-bearing provider in the boundary pass): reconciled by V1
  operation accounting/native-vs-synthetic provenance. Cost-bearing/unknown evidence
  is never fabricated as zero; future economics aggregation remains separate.
- README factual drift (stale "Current Phase" and "Planned Pipeline" statements):
  corrected by the 2026-10-24 closure; historical Phase 1 instructions remain labelled.
- Pydantic deprecation warning cleanup
- ⚠️ Phase 7E live acceptance remains **externally blocked** while no compatible free
  OpenRouter video model is available; it must never be forced via paid generation
- **Production timeline coherence (FUTURE HARDENING REQUIREMENT — recorded 2026-09-08).**
  Forensic finding from Batch 1 testing: video duration can be approximately 60 seconds
  while measured narration is substantially shorter; captions follow narration timing;
  visual scene durations follow independent plan estimates; and assembly then pads audio
  to the video duration. Result: **visual timeline ≠ narration timeline**, producing long
  silent sections. Target architecture (future engineering hardening — this is **not**
  implemented, no solution is fixed here, and it must not be moved into a completed
  phase): reconcile measured narration, native generated audio, scene durations,
  captions, motion/video duration, applicable transitions, and final assembly duration
  toward **one coherent production timeline**:
  ```
  Production Timeline
      ├── scene timing
      ├── narration timing
      ├── native audio timing
      ├── captions
      ├── visual duration
      └── final assembly duration
  ```
  Phase 11C Batch 1A's immediate local-render synchronization is **implemented and
  verified** (`reconcile_local_render_durations`, 8 tests). It does not transfer
  ownership of native generated audio/duration. The broader Production Timeline
  abstraction remains **Batch 2 / V1.x**, separately authorized future work.
- **Visual relevance is textual-only today (CURRENT LIMITATION).** The Batch 1 gate is
  textual token overlap on provider metadata; the system does not inspect image pixels
  for semantic relevance (see Section 8A.17).
- **Scene-intent flattening (CURRENT LIMITATION).** Rich scene intent can be flattened
  into a small number of search tokens on the stock-search path (see Section 8A.18).
- **ComfyUI local/hosted generation integration — DEFERRED (2026-09-12).** Superseded
  as the Batch 1C target by the Alibaba Cloud provider-integration decision; remains an
  optional future local/owned generation engine under Sections 8A.9/8A.19 with all
  capability-gating constraints intact. Not implemented in Batch 1C.
- **Track 8 standalone direct clip rendering — V1.x (Founder/CTO, 2026-10-24).**
  V1's owned source/transcript/segments/candidate-selection/derived-unit lineage
  workflow is complete. Rendering shortcuts that bypass canonical `ProductionUnit`
  project execution are not part of the V1 sign-off.
- **Track 9 automatic summary publishing promotion — V1.x (Founder/CTO, 2026-10-24).**
  Standalone `narrated_summary.mp4` is inspectable/downloadable, not a canonical
  project completion/binding/publication record. Without an accepted completed
  project binding, V1 returns `422 SUMMARY_HANDOFF_REQUIRED`; automatic sidecar
  promotion needs a separately designed handoff, never fabricated binding.

---

## 12. Numbering Note

The repository previously contained a phase-numbering collision: the visual/production
track used Phases 6A–6F, 7A–7E, and 8, while the publishing track independently used
"Phase 8A" for TikTok publishing (already implemented:
`app/publishing/tiktok.py`). The visual/production Phase 8 and the publishing "Phase
8A" are **different, unrelated milestones**. Future roadmap naming (Phases 9+) belongs
to the visual/production / personal-maturity track and deliberately avoids reusing the
"8A"-style collision space.

Phase 11B — COMMAND CENTER EXPERIENCE is a deliberately lettered, bounded refinement
phase inserted between Phase 11 and Phase 11C on the personal-maturity track. Phase 11C
is the controlled V1 product implementation and expansion bridge inserted between Phase
11B and Phase 12. Neither phase reuses the historical publishing-track "8A" collision
space.

---

## 13. Roadmap Amendment Log

This roadmap carries no version number. Amendments are recorded here, per the footer
convention: amendments require an explicit, documented update to this file.

- **2026-09-01 — Phase 11B insertion (roadmap-only amendment).**
  - Inserted **PHASE 11B — COMMAND CENTER EXPERIENCE** between the completed
    Phase 11 and Phase 12, as a bounded UX/UI refinement phase of the Phase 11
    Personal Command Center.
  - Reason: the newly implemented Phase 11 Command Center (accepted and checkpointed
    at Git commit `b50503c`) is functional but needs experience polish before
    automation work begins.
  - Phase 12 — Personal Automation (automation / scheduling) remains unchanged in
    name, position, and meaning, and remains the next substantive phase after
    Phase 11B.
  - Recorded Phases 9, 10, and 11 as COMPLETE (each closed by its own frozen
    specification and final acceptance). No change to Phase 9/10/11 semantics.
  - No architectural authority changes and no implementation are included in this
    amendment.

- **2026-09-03 — Phase 11C insertion (roadmap-only amendment).**
  - Inserted **PHASE 11C — AVM V1 PRODUCT IMPLEMENTATION & EXPANSION** between Phase
    11B and Phase 12.
  - Established 11C as an additive, reuse-first bridge from the existing AVM Core into
    the integrated V1 product; it is not a greenfield rebuild and does not invalidate
    previous phases.
  - Added evidence-based capability treatment, V1/V1.x/Post-V1/Decision Required
    classification, a dependency-aware implementation sequence, SaaS/mobile readiness
    direction, lightweight new-idea governance, validation checkpoints, exit criteria,
    and explicit non-goals.
  - Preserved the Phase 11B authority boundaries and retained Phase 12–15 as separate
    future responsibilities. No application implementation, dependency, database,
    billing, authentication, or execution changes are included in this amendment.

- **2026-09-08 — Architectural formalization (roadmap-only amendment).**
  - Formalized the canonical production conceptual hierarchy — Production Intent →
    Production Mode → Production Bible (when continuity requires it) → Production Route →
    Provider/Model → Media Strategy → Audio Strategy → Motion/Video Engine → Assembly →
    Quality/Validation → Distribution — as new **Section 8A**, including the mandatory
    distinction **Production Mode ≠ Production Route ≠ Provider/Model** and the
    terminology-control table (8A.1/8A.2).
  - Defined formal **Production Modes A/B/C** (Single Video; Multiple Independent
    Videos; Series/Episodic) over the shared Production/Unit architecture — no separate
    mode engines (8A.4); defined formal **Production Routes 1–6** (Local AI-Assisted;
    Full AI Video; Google Flow; Long-Form Repurposing; Video Upload → AI
    Narration/Transformation; Publishing/Distribution) with truthful per-route status
    (8A.6); documented mode+route composition as architecturally mandatory (8A.7).
  - Clarified the **Production Bible** role: persistent creative/continuity/context
    authority — not a route, provider, model, execution engine, media renderer, router,
    state machine, manifest replacement, or second execution truth — with its potential
    domains, per-mode necessity guidance, and future Story Intelligence ownership
    (8A.5).
  - Separated the **Provider/Model layer** from route semantics (OpenRouter; ComfyUI/
    local models; Veo; Seedance; Wan; Kling; future providers/models), including future
    SaaS automatic/explicit model selection with model-specific AVM credit consumption
    as PLANNED-not-implemented, and capability/cost kept distinct from route semantics
    (8A.8).
  - Added **ComfyUI** as an optional local/owned generation engine — experimental,
    capability-gated, integrated through existing AVM seams (Phase 8 provider boundary
    and visual-source architecture), never a hard dependency, with all availability
    states supported (8A.9); recorded the current ComfyUI desktop installation as an
    experimental proof-of-concept environment — not a mandatory dependency, not proof of
    hardware feasibility, not a finalized route, not a provider-abstraction replacement
    — with hardware/VRAM/workflow capability gating and no universal hardware
    assumptions (8A.19).
  - Recorded the **local-vs-cloud compute** principle: "local/owned" is an
    economic/execution strategy, not a fixed machine; the same route may later run on an
    operator PC, owned workstation, dedicated GPU server, private GPU infrastructure, or
    cloud GPU with the same AVM Core — supporting future monthly-GPU hosting instead of
    per-generation cloud video credits where economically advantageous. No cloud
    infrastructure is implemented (8A.10).
  - Added **Media Strategy** (mixed media types per scene, separate from mode and
    route) (8A.11); **native generated audio and conditional audio generation** with the
    possible audio-policy space (retain/suppress/replace native audio; external
    narration; mix; sound design/music; intentional silence), explicitly preserving the
    frozen Phase 7D audio contract — policies beyond the Phase 7D three are future
    directions requiring their own design (8A.12); and the **audio/visual ownership
    rule** — no layer silently assumes ownership of all audio (8A.13).
  - Preserved **routing/fallback** boundaries: nothing implies "provider A fails →
    automatically substitute provider B"; the Phase 8 no-fallback contract, Phase 7E
    free-first/paid-gated safety model, and Phase 15 explicit-policy home are unchanged;
    availability/capability/fallback/retry/substitution remain separate concepts, with
    future capability-aware routing (8A.14).
  - Clarified **Personal/Operator UI vs SaaS** exposure of modes, routes, infrastructure,
    ComfyUI, providers/models, actual provider economics, diagnostics, and experimental
    capabilities vs a SaaS-controlled subset (8A.15); recorded the **provider cost vs AVM
    service value** distinction — AVM SaaS may charge credits for orchestration/service
    value even where the underlying provider has zero direct cost. No billing, prices,
    tiers, or account policy are implemented (8A.16).
  - Recorded **visual relevance current-vs-future**: the Phase 11C Batch 1 textual
    relevance gate (candidate retrieval → search terms → provider metadata → textual
    token overlap → relevance gate → selection; zero-overlap rejection; relevance
    ranking; preserved relaxation/fall-through; regression coverage; passing suite) is
    **implemented and verified** but is explicitly **not** semantic image understanding —
    no pixel inspection exists; visual/semantic evaluation (embeddings, CLIP-like
    similarity, vision-model evaluation, subject/action/environment/identity/composition
    matching, aesthetic/quality evaluation) is future with no technology mandated
    (8A.17); recorded rich **scene-intent preservation** (subject, identity, action,
    environment, attributes, emotion, time period, camera, composition, lighting, visual
    style, purpose, motion intent) as a future intelligence improvement, with the current
    search-term abstraction retained until an approved task changes it (8A.18).
  - Added **production-timeline coherence** to Known Deferred Items / Future Hardening
    (Section 11) from the Batch 1 forensic finding (~60 s video vs substantially shorter
    measured narration; captions on narration timing; scene durations on plan estimates;
    audio padded by assembly → visual timeline ≠ narration timeline → long silent
    sections), as a future reconciliation requirement with no implementation solution
    fixed here.
  - Recorded truthful post-roadmap status: Phase 11B specification frozen and recovered
    implementation accepted (commits `dd21b26`, `cc551ca`); Phase 11C Batch 1 core
    production and local creation implemented and committed (`8f3940a`), with the Batch 1
    textual visual-relevance hardening and regression coverage present and passing in the
    current working tree (`app/orchestrator/visual_sources.py`,
    `tests/test_phase11c_batch1_hardening.py`, `tests/test_visual_sources.py`), and the
    story-LLM OpenRouter configuration
    correction (the obsolete free-model configuration was replaced via
    `OPENROUTER_MODEL`, initially `openrouter/free`, after which a specific free model
    was configured and manually tested successfully). OpenRouter free-model availability
    remains external/provider-dependent and creates no architectural dependency. Phase
    11B/11C status lines and Current Position were updated to this evidence.
  - Added cross-cutting principles 13–17 (Section 8).
  - No new phase was created; no work was moved between phases; no future capability is
    labelled implemented; and no application code, test, schema, prompt, contract,
    dependency, database, billing, publishing, ComfyUI, timeline, provider-intelligence,
    or infrastructure changes are included in this amendment.

- **2026-09-08 — Phase 11C Batch 1 scope structuring (roadmap-only amendment).**
  - Formally structured the remaining immediate Phase 11C Batch 1 scope into three
    workstreams, recorded as a new subsection of Phase 11C (Section 4.10):
    **Batch 1A — Timeline Synchronization Fix** (immediate correction of the confirmed
    ~36.19s narration / 60s visual divergence recorded in Section 11), **Batch 1B —
    Stock Visual Relevance Hardening** (stock search-query construction and
    metadata-based candidate relevance, preserving the existing VisualSource
    abstraction, routing layer, and truthful failure/fall-through behavior), and
    **Batch 1C — ComfyUI Local/Hosted Generation Integration** (ComfyUI as an
    optional, capability-gated, fail-open generation engine/source within the existing
    visual-source architecture; local desktop ComfyUI supported, with the adapter
    designed so the same integration can later target AVM's own cloud-GPU/hosted
    ComfyUI endpoint without a second architectural implementation).
  - Preserved the Batch 2 boundary unchanged: **Semantic Visual Evaluation** and the
    **Production Timeline Abstraction** remain Batch 2 capabilities; Batch 1A/1B/1C do
    not implement either. Added the explicit Batch 1 → Batch 1 Acceptance → Batch 2
    progression to Section 4.10.
  - Updated the affected ComfyUI status language so the roadmap no longer contradicts
    the Batch 1C assignment: Section 8A.9's heading and staging note now record the
    Batch 1C assignment (previously "experimental / under evaluation — do not
    implement now"), Section 8A.19 records that its capability-gating constraints
    bind Batch 1C, and Section 8A.17 records that Batch 1B hardens the
    metadata/search layer while semantic evaluation remains Batch 2. The historical
    2026-09-08 architectural-formalization record (which correctly described ComfyUI
    as experimental/deferred at that time) is preserved unchanged in this log, and all
    Section 8A.9 capability-gating constraints continue to bind the Batch 1C
    implementation. Section 11's timeline entry now cross-references Batch 1A as the
    immediate fix and Batch 2 as the abstraction home. The Current Position Phase 11C
    note was updated so it no longer implies Batch 1 is already complete.
  - Reaffirmed the architecture distinctions: ComfyUI is an ENGINE/generation
    capability — not a Production Mode, not a Production Route, not a replacement for
    existing routes or providers; Production Intent → Mode → Route → Provider/Model →
    Engine → Endpoint (Sections 8A.1/8A.2) remain distinct, and local-desktop vs
    hosted cloud-GPU ComfyUI are endpoints of one integration (Section 8A.10).
  - Roadmap scope/sequence decision only. No application code, tests, schemas,
    prompts, .env/configuration, dependencies, ComfyUI workflows or configuration, or
    any other implementation are included in this amendment.

- **2026-09-12 — Phase 11C Batch 1C Alibaba-primary / OpenRouter-fallback
  authorization (roadmap-only amendment).**
  - **Reassigned Batch 1C** from "ComfyUI Local/Hosted Generation Integration" to
    **"Alibaba Cloud Provider Integration + OpenRouter Fallback"** (Section 4.10).
    **Alibaba replaces ComfyUI as the Batch 1C generation-provider target** following
    the final provider-architecture investigation and the Alibaba Cloud Model Studio
    live-validation gate. **ComfyUI is DEFERRED / NOT IMPLEMENTED in Batch 1C** and
    remains an optional future local/owned generation engine under Sections
    8A.9/8A.19 with all capability-gating constraints intact; the 2026-09-08 ComfyUI
    assignment is superseded (historical record preserved).
  - **Alibaba Cloud Model Studio becomes AVM's normal/primary AI provider**;
    **OpenRouter becomes the automatic fallback/resilience provider** and remains
    fully implemented (text, image, video). AVM does not depend operationally on
    Alibaba alone, and does not run both providers in parallel for ordinary
    production.
  - **Authorized the automatic Alibaba-primary → OpenRouter-fallback policy** for
    Batch 1C: capability-aware (text/LLM, structured JSON/planning, reasoning, image
    generation, video generation; vision/multimodal only where an actual AVM consumer
    exists and both providers support the required contract) and
    **failure-classified** — fallback only on fallback-eligible provider failures
    (outage/unavailability, rate limiting, quota exhaustion, provider-side 5xx,
    transport failure/timeout, model/capability temporarily unavailable), never on
    AVM's own malformed request, invalid duration/size, contract error, self-caused
    local validation failure, or credential misuse. **Fallback is classified, not
    blind.** If Alibaba fails on a capability OpenRouter cannot legitimately satisfy,
    AVM returns a truthful failure.
  - **No parallel generation:** the same production artifact is never generated
    through both providers merely for redundancy; OpenRouter is invoked only after a
    fallback-eligible Alibaba failure. Normal production uses Alibaba only — no
    requirement for continuous paid subscriptions to both providers. No new
    billing/credit-ledger architecture is introduced; provider usage/cost accounting
    remains subject to the existing roadmap.
  - **Not authorized by this amendment:** Phase 14 intelligent provider/model
    ranking, dynamic multi-provider optimization, cheapest/latency/quality-based
    ranking, automatic model marketplace selection, arbitrary provider rotation,
    parallel provider execution, speculative provider selection, user-facing provider
    marketplace, or generalized multi-provider orchestration. **Phase 15 remains the
    home for generalized/intelligent provider fallback and routing architecture
    beyond this fixed two-provider policy** (Phase 15 is preserved, not deleted or
    collapsed; its boundary is reconciled in Section 4.14 and Section 8A.14).
  - **Preserved unchanged:** the Phase 8 provider execution boundary and no-fallback
    contract for blind/generalized substitution; `ProductionPlan`/`Scene` and existing
    scene contracts; Phase 6C intake/preflight; artifact lifecycle; video/image
    validation; assembly; the frozen **Phase 7D audio contract** (Alibaba video and
    any OpenRouter video fallback each carry their **native generated audio facts**;
    fallback never converts provider-owned media into narration-owned media; **Edge
    TTS remains the current narration provider** — Alibaba TTS integration is NOT
    authorized); Phase 7E; production-execution authority model; publishing
    architecture; billing architecture; authentication/tenancy architecture; and the
    existing OpenRouter implementations. Fallback occurs through existing provider
    seams/abstractions — production orchestration must not become hard-coded around
    `if Alibaba fails: call OpenRouter` inside arbitrary business logic; the exact
    implementation location is determined during Batch 1C implementation.
  - **Credential separation preserved:** the Alibaba Token Plan `sk-sp-…` key is for
    Cline/interactive coding tools only; AVM uses only its dedicated standard
    application/API key (e.g. `sk-ws-…`); AVM must never use the Token Plan Personal
    Edition credential; no actual credential is exposed, printed, hardcoded, or
    inserted by this amendment.
  - **Internal-consistency updates:** Section 4.10 architecture constraints and
    explicit non-goals (carve-out for the authorized fixed fallback), the Batch 1C
    scope paragraph and Batch 1 diagram, the Batch 1C status line, Section 4.14 Phase
    15 boundary reconciliation, Sections 8A.8/8A.9/8A.14/8A.19/8A.20, and a
    ComfyUI-deferred entry in Section 11. **Batch 1A and Batch 1B are unchanged.**
  - **Roadmap-only amendment.** No application code, tests, schemas, prompts,
    .env/configuration, dependencies, packages, or any other implementation are
    included. **Batch 1C remains NOT IMPLEMENTED / PENDING IMPLEMENTATION**; this
    amendment authorizes scope only and does not mark any implementation complete.

- **2026-10-24 — Phase 11C Tracks 1–14 completion, exit-criteria sign-off and V1/V1.x
  scope reconciliation (Founder/CTO authorized closure).**
  - **Formal disposition:** **Phase 11C COMPLETE (Tracks 1–14 Verified & Signed Off)**.
    Phase 11B specification/recovered implementation acceptance (`dd21b26`, `cc551ca`)
    and Batch 1A/1B/1C implemented/verified status are reconciled in the current
    sections. Earlier discovery, scope-only and pending/no-application-code entries
    above remain unchanged as dated historical audit records.
  - **Code checkpoints:** Core Standard grounded narration/hermetic-test fixes
    [`a1bb5edb6b8574554548a1682b9427f84cc28ff3`](https://github.com/7DayWalker/AUTO-VIDEO-MAKER/commit/a1bb5edb6b8574554548a1682b9427f84cc28ff3);
    K3 real-execution SummaryStudio/explicit sandbox toggle
    [`b6a7f3d9b3b37ff072456c1d4525e3b7f4cd73c7`](https://github.com/7DayWalker/avm-studio-pro/commit/b6a7f3d9b3b37ff072456c1d4525e3b7f4cd73c7).
    Earlier Track 9 canonical routing/Pro/assembly (`114bad3`), Core HTTP (`20713eb`),
    and Core/K3 manual publishing parity (`39ff7d3` / `d59225b`) remain part of the
    implementation chain. This documentation sign-off is a separate follow-up
    commit over the Core code checkpoint; its hash is recorded by Git history.
  - **Validation:** **1,904 Core passed / 0 failed / 1 skipped** via
    `tools/run_hermetic_pytest.py` (one opt-in live E2E; **0 external network
    attempts**); **86 K3 passed / 0 failed**, production build exit 0 / zero errors.
    Track 9: 188 passed; HTTP routes: 17; original 10 configuration/profile blockers:
    10 passed, all four affected files directly: 85 passed. The actual pre-fix
    checkout collected 1,865 tests; 40 new cases yield 1,905 collected. No live AI,
    YouTube or TikTok call was used to obtain closure evidence. Existing Pydantic/
    bundle-size warnings and the passing sequential recheck after an earlier
    loopback-only `WinError 10053` remain documented, not hidden.
  - **Track 8 decision:** V1 complete for source registration, timestamped
    transcription, validated segments, deterministic/AI candidate moment selection
    through shared grounded planners, and derived `ProductionUnit` lineage.
    Publishing uses the canonical accepted-completed-project path once bound.
    Direct standalone clip-rendering shortcuts bypassing that execution remain
    **V1.x**; no new clipping engine or autonomous selection endpoint is claimed.
  - **Track 9 decision:** V1 complete for real Summary Standard text+JSON grounding,
    Summary Pro windowed visual understanding/narration, TTS synthesis,
    `narrated_summary.mp4`, Core `/api` and K3 SummaryStudio. Standalone sidecars are
    inspectable/downloadable via `/summary-video` and `/summary-video/media`.
    Automatic promotion into a canonical `ProjectManifest` publishing record without
    a bound completed project remains **V1.x** (`422 SUMMARY_HANDOFF_REQUIRED` in V1).
    Users select canonical models; providers/wire IDs are internal, same-model
    failover preserves identity, and readiness/cost unknowns remain truthful.
  - **Tracks 11–13 decision:** V1 ownership/configurable-storage/non-interactive
    credential/range-media, durable operation/attempt accounting, and two-repo HTTP
    client/mobile-readiness seams are complete. Status GET reconciliation side
    effects are explicit. No tenant DB/auth, fabricated credit balance, billing
    tables, subscription economy, native mobile app or premature infrastructure.
  - **Next phase:** **Phase 12 — Automated Publishing, Scheduling & V1.x Operator
    Polish**. Phase 13 quality/scale, Phase 14 intelligent optimization and Phase 15
    generalized recovery retain their original distinct gates. This closure does
    not implement automation, Batch 2 semantic/timeline infrastructure, mandatory
    continuity intelligence, ComfyUI or a second AVM Core.

---

*This roadmap is strategic documentation. It supersedes no frozen phase specification and
is superseded by nothing. Amendments require an explicit, documented update to this file.*






