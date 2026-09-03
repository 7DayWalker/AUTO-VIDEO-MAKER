# AVM MASTER ROADMAP

**AVM = Auto Video Maker.**

This document is the **authoritative strategic roadmap** for the AVM project. It records
where the project has been, where it is, and where it is going — from the original
foundation, through the personal production engine, into personal autonomy maturity,
and eventually toward a proven core, a public product, and a long-term content platform.

This is a **strategic and architectural** roadmap. It deliberately does **not** contain
implementation designs for future phases. Every future phase receives its own frozen
specification / design gate before implementation (see Cross-Cutting Principles).

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

### 4.9 PERSONAL AVM MATURITY — Phase 11B 🟡

**Name:** Command Center Experience.

**Purpose:** Transform the functional Phase 11 Personal Command Center into a
substantially more coherent, polished, understandable, and production-oriented
operator experience — **without changing the underlying AVM production architecture
or execution semantics**.

Phase 11B is **UX/UI refinement and experience architecture only**: a bounded
refinement of the existing Phase 11 Personal Command Center control surface.

**Scope direction for the future Phase 11B Discovery stage** (direction only —
these are **not** implementation requirements, and no Phase 11B specification exists
yet):

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

Phase 11B exists **only before Phase 12** — to polish the operator experience
before automation is introduced. Phase 11B must receive its own frozen
specification / design gate (the future Phase 11B Discovery stage) before any
implementation begins. This roadmap entry grants **no permission** to implement
Phase 11B early.

**Status:** 🟡 FUTURE.

### 4.10 PERSONAL AVM PRODUCT — Phase 11C 🟡

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
  its interrupted implementation must be separately reviewed and accepted before it is
  treated as complete.

Phase 11C shall preserve these boundaries. It shall not reopen completed phases merely
because V1 exposes their capabilities through a more integrated product workflow.

#### Current starting point — evidence-based baseline

The AVM Product Architecture V1 Baseline identified the following starting point:

| Area | Current evidence | 11C treatment |
|---|---|---|
| Production intent | `app/production/models.py` and `app/production/storage.py` provide `Production`, `ProductionUnit`, optional `ProductionBible`, dependencies, provider intent, versioning, and durable intent storage | Preserve and integrate |
| Plan-backed execution | `app/orchestrator/production_execution.py` provides `ProductionExecutor`, `derive_progress()`, binding discovery, per-unit plans, and resumable execution around the existing engine | Preserve; correct wiring where required; do not replace |
| Project binding | `ProjectManifest.production_binding` is the authoritative Production → Unit → Project relationship | Preserve as the only binding authority |
| Scene execution | `app/orchestrator/scene_execution.py` provides the provider-independent execution boundary | Preserve and extend additively |
| Local video | ImageMotion, local asset normalization, external video intake, FFmpeg, and FFprobe paths exist in `app/orchestrator/visual_providers.py` and `app/assembly/` | Consolidate, validate, and integrate |
| Google Flow | Browser/CDP Flow route and credit protections exist in `app/flow/` and the Flow provider wrapper | Preserve as a first-class route |
| AI video | Gemini Omni and OpenRouter video routes exist; OpenRouter includes catalog/access/cost gates and remote-job handling | Integrate and harden; no premature intelligence layer |
| Narration | `app/voice/` provides plan narration extraction, Edge TTS, mock TTS, validation, and timing | Reuse for existing workflows; add source-video narration only as a genuine workflow gap |
| Assembly/captions | `app/assembly/` and `app/orchestrator/captions.py` provide local assembly, audio policy, captions, and validation | Preserve and connect to new workflows |
| Publishing | YouTube and TikTok adapters, OAuth/connection paths, durable records, and reconciliation exist in `app/publishing/` | Consolidate through existing adapters; do not add a new state machine |
| Command Center | Phase 11 API/UI exists; Phase 11B implementation is interrupted and unaccepted | Complete and polish through controlled review |
| Long-video repurposing | No transcription, source-video analysis, segment planner, or derived-episode lineage was found | Genuine additive gap |
| SaaS/mobile | No identity, tenancy, billing, credit ledger, cloud storage, or mobile API exists | Keep as preparation/direction, not premature infrastructure |

The baseline also found that `productions/` is empty in the inspected working tree,
while `output/projects/` contains existing execution artifacts. Existing code and tests
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

The following is the initial product classification. It is a controlled baseline, not a
permanent freeze; changes are governed by the new-idea rule below.

| Capability | Evidence-based treatment | Initial product classification |
|---|---|---|
| Local video creation through ImageMotion | EXISTS; `ImageMotionVisualProvider` and local FFmpeg processing exist | **V1 — POLISH / HARDEN / INTEGRATE** |
| Local asset/video normalization and assembly | EXISTS; asset intake, FFmpeg, audio policy, captions, and validation exist | **V1 — INTEGRATE** |
| Google Flow production route | EXISTS; Flow browser/CDP provider and credit firewall exist | **V1 — INTEGRATE / HARDEN** |
| OpenRouter AI video creation | EXISTS; video gateway, catalog, async jobs, cost gates, and resume protections exist | **V1 — INTEGRATE / HARDEN** |
| Multi-model routing and suitable-model selection | Basic provider/model configuration exists; evidence-based intelligent ranking does not | **DECISION REQUIRED for V1; otherwise V1.x / Phase 14** |
| Cost-aware AI usage | Provider-specific controls exist, including Flow limits and OpenRouter paid gates; no unified ledger exists | **V1 — preserve existing controls; V1.x for unified accounting** |
| Single Video Production | Supported by one Production with one Unit and the existing single-project engine | **V1 — INTEGRATE / POLISH** |
| Multiple Independent Video Production | Production/Unit schema and serial executor support representation and composition; batch product workflow is incomplete | **V1 — INTEGRATE; parallel/batch expansion V1.x** |
| Series / Episodic Production | Optional Bible, episode metadata, dependencies, and shared context exist; continuity enforcement does not | **V1 — model/workflow integration; continuity intelligence V1.x / DECISION REQUIRED** |
| Approximately 45–60 second episodic output | Existing short-form plan and assembly constraints can produce short episodes | **V1 — INTEGRATE** |
| Production Bible-required series workflow | Bible is optional in the core by design; a series workflow may require it at creation/validation | **V1 — DECISION REQUIRED for exact product gate** |
| Long-video repurposing from approximately 45–60 minute source | External video intake exists, but transcription, analysis, segmentation, and lineage are absent | **V1 — GENUINE GAP, controlled additive track** |
| Approximately 60-second derived clips/episodes | Existing units and assembly can represent outputs; repurposing planner is missing | **V1 — GENUINE GAP for planner; output polish reuses existing core** |
| Video Upload → AI Narration | Voice/TTS exists for generated plans; upload ingestion and transcription do not | **V1 — GENUINE GAP for source workflow** |
| Manual publishing | YouTube/TikTok adapters and durable publication records exist | **V1 — INTEGRATE / HARDEN** |
| Automated publishing | Existing roadmap places automation after 11B; no automatic publishing in 11C | **POST-V1 / Phase 12** |
| Additional publishing platforms | Platform registry and adapter direction exist; Instagram/Facebook are not implemented | **V1.x / DECISION REQUIRED** |
| Personal AVM Command Center | Phase 11 exists; interrupted Phase 11B implementation needs review and completion | **V1 — POLISH / HARDEN / INTEGRATE** |
| Future public SaaS | Strategic one-core direction exists; identity, tenancy, billing, and service infrastructure do not | **V1 — architecture preparation only; SaaS product V1.x / POST-V1** |
| Subscriptions and billing | No implementation and no approved prices, tiers, provider, or revenue share | **DECISION REQUIRED; V1.x / POST-V1** |
| Credits and credit consumption | Provider-specific cost controls exist; no product credit ledger exists | **V1 — define boundaries only; V1.x implementation** |
| Future mobile application | Product direction is approved; no mobile application/API surface exists | **V1 — mobile-readiness constraints; POST-V1 application** |
| Advertising/revenue sharing | No authoritative commercial rules exist | **POST-V1 / DECISION REQUIRED** |

No prices, subscription tiers, billing provider, advertising model, or revenue-share
percentage is established by this amendment.

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

#### Controlled implementation sequence

The expected 11C sequence is:

| Order | Stage | Dependency rationale | Initial treatment |
|---:|---|---|---|
| 1 | V1 foundation and repository hardening | All later work depends on a trustworthy baseline, runtime prerequisites, evidence, and clean authority boundaries | DISCOVER; POLISH / HARDEN |
| 2 | Existing production-system consolidation | Production modes must use the existing intent, plan, binding, executor, manifest, checkpoint, and reconciliation chain | REUSE; INTEGRATE |
| 3 | Personal Command Center completion and polish | The operator needs a truthful control surface before broader production workflows are exposed | REUSE; POLISH / HARDEN; separately accept 11B |
| 4 | Local video-creation integration | The lowest-cost path and existing FFmpeg/ImageMotion primitives provide the baseline production route | REUSE; INTEGRATE |
| 5 | AI generation and OpenRouter integration | Existing provider/cost controls must be exposed only after the core local route is coherent | REUSE; INTEGRATE / HARDEN |
| 6 | Google Flow integration | Flow remains first-class but is externally/runtime sensitive and must retain credit protections | REUSE; INTEGRATE / HARDEN |
| 7 | Single, multiple-independent, and episodic mode consolidation | These modes share the Production/Unit architecture; product workflows depend on the consolidated execution path | REUSE; INTEGRATE; genuine UX gaps only |
| 8 | Long-video repurposing | Requires stable media ownership, units, assembly, captions, and publishing before adding source analysis | GENUINE GAP; additive workflow |
| 9 | AI narration for uploaded/source media | Depends on source ingestion, transcription, and segment context; extends existing voice and timing primitives | GENUINE GAP plus REUSE |
| 10 | Publishing consolidation | Requires stable final artifact evidence and unit/project relationships; automated publishing remains excluded | REUSE; INTEGRATE / HARDEN |
| 11 | SaaS foundation preparation | Must be informed by real personal production evidence; define seams without premature infrastructure | PROPOSED preparation only |
| 12 | Subscription and credit architecture preparation | Requires evidence of actual resource/cost behavior and approved commercial decisions | DECISION REQUIRED; design only unless separately authorized |
| 13 | Mobile-readiness preparation | Depends on stable control contracts and surface-neutral core operations | PROPOSED preparation only |
| 14 | V1 validation and stabilization | Final acceptance must cover integrated workflows and ensure later-phase boundaries remain intact | HARDEN; validate; accept |

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
- retry/fallback/substitution policy before its approved later phase;
- provider intelligence/ranking before its approved later phase;
- a new quality engine before its approved later phase;
- a new media-processing architecture where the approved local/FFmpeg primitives fit;
- a new publishing state machine;
- a frontend framework or unnecessary build-system migration.

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

#### Explicit non-goals

Phase 11C does **not**:

- restart AVM as a greenfield project;
- discard or rewrite working Phase 1–11B architecture;
- absorb Phase 12 personal automation, scheduling, unattended execution, or automatic
  continuation;
- absorb Phase 13 quality scoring, quality gates, or autonomous scale systems;
- absorb Phase 14 provider ranking, optimization, or intelligent model selection;
- absorb Phase 15 retry, fallback, substitution, or automated recovery policy;
- introduce a database, authentication, tenancy, billing, subscriptions, or payment
  provider without a separately approved SaaS phase;
- introduce a scheduler, queue, daemon, microservice fleet, or Kubernetes/cloud platform
  without evidence that the current stage requires it;
- create a second execution engine, state machine, binding authority, or publishing
  state machine;
- migrate the frontend to a framework or introduce an unnecessary build system;
- invent final commercial prices, tiers, credit exchange rates, or revenue-sharing terms;
- claim that external video intake is already long-video repurposing;
- claim that plan-driven TTS is already upload-to-AI-narration;
- implement every possible future platform, workflow, or idea merely because it is
  compatible with the long-term vision.

**Status:** 🟡 FUTURE / NOT STARTED.

### 4.11 PERSONAL AVM MATURITY — Phase 12 🟡

**Name:** Personal Automation.

**Purpose:** Move AVM from an operator-driven production tool toward an **autonomous
production system**.

The system should eventually support commands such as:

> "Create a 60-episode series and keep producing until the planned workload is complete."

> "Produce one episode every day for 30 days."

The key objective is **continuous execution of a durable production workload with
minimal human intervention**. This is where the meaning of "AUTO" becomes operationally
significant.

**Question this phase answers:** *"Continue production automatically."*

**Status:** 🟡 FUTURE.

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
| PERSONAL AVM MATURITY | 🟡 NOT YET COMPLETE |
| PROVEN AVM CORE | 🔵 FUTURE MILESTONE |
| PUBLIC AVM PRODUCT | 🔵 FUTURE |
| AVM CONTENT PLATFORM | 🔵 LONG TERM |

The current repository state (six visual providers, provider-agnostic execution
boundary, durable per-scene checkpoints/resume, hybrid routing, audio policy, captions,
assembly, publishing, plus the completed Phase 9 durable production plan, Phase 10
plan-backed execution, and Phase 11 Personal Command Center) corresponds to **LEVEL 3**
on the autonomy maturity model.

**The immediate next phase is:**

> **PHASE 11C — AVM V1 PRODUCT IMPLEMENTATION & EXPANSION** 🟡

Phase 11B remains the bounded predecessor experience phase and must be completed and
accepted before 11C is accepted as complete. Phase 11C is the approved roadmap bridge
from the existing Phase 1–11B architecture into the integrated AVM V1 product; it is
not a greenfield rebuild and this roadmap entry does **not** authorize implementation
without separately scoped implementation tasks and checkpoints.

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
- Pre-existing Gemini generation-cost reporting truthfulness issue (zero-cost figures
  recorded for a cost-bearing provider in the boundary pass)
- README factual drift (stale "Current Phase" and "Planned Pipeline" statements)
- Pydantic deprecation warning cleanup
- ⚠️ Phase 7E live acceptance remains **externally blocked** while no compatible free
  OpenRouter video model is available; it must never be forced via paid generation

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

---

*This roadmap is strategic documentation. It supersedes no frozen phase specification and
is superseded by nothing. Amendments require an explicit, documented update to this file.*






