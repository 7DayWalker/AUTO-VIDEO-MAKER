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

### 4.6 PERSONAL AVM MATURITY — Phase 9 🟡

**Name:** Durable Production Plan.

**Purpose:** Create a durable, inspectable, resumable **production-plan artifact**
representing what AVM intends to produce.

**Concept:** Move from ephemeral planning toward a persisted **production contract**.
The plan should eventually be capable of representing the intended production workload
sufficiently for later execution, inspection, resume, and automation.

**Question this phase answers:** *"What are we making?"*

No implementation details are defined here. Phase 9 must receive its own frozen
specification / design gate before implementation.

**Status:** 🟡 FUTURE.

### 4.7 PERSONAL AVM MATURITY — Phase 10 🟡

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

**Status:** 🟡 FUTURE.

### 4.8 PERSONAL AVM MATURITY — Phase 11 🟡

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

**Status:** 🟡 FUTURE.

### 4.9 PERSONAL AVM MATURITY — Phase 12 🟡

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

### 4.10 PERSONAL AVM MATURITY — Phase 13 🟡

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

### 4.11 PERSONAL AVM MATURITY — Phase 14 🟡

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

### 4.12 PERSONAL AVM MATURITY — Phase 15 🟡

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

### 4.13 PERSONAL AVM MATURITY — Phase 16+ 🟡

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
| **LEVEL 3** | AVM can execute a durable production plan | 🟡 Future — Phase 9 + Phase 10 |
| **LEVEL 4** | AVM can continue production autonomously | 🟡 Future — Phase 12 |
| **LEVEL 5** | AVM can autonomously maintain quality, recover from failures, and optimize provider usage under explicit policies | 🟡 Future — Phase 13 + Phase 14 + Phase 15 |
| **LEVEL 6** | AVM can operate ongoing content-production workloads | 🔵 Intended end state — Phase 16+ and beyond |

Existing capabilities are grounded in the completed engine phases (5A–5J, 6A–6F, 7A–7E,
8). Future capabilities require their own specifications. Nothing above LEVEL 2 is
claimed as achieved today.

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
assembly, publishing) corresponds to **LEVEL 2** on the autonomy maturity model.

**The immediate next phase is:**

> **PHASE 9 — DURABLE PRODUCTION PLAN** 🟡

Phase 9 must still receive its own frozen specification / design gate before any
implementation begins. This roadmap does **not** constitute a Phase 9 specification and
grants **no permission** to implement it early.

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

---

*This roadmap is strategic documentation. It supersedes no frozen phase specification and
is superseded by nothing. Amendments require an explicit, documented update to this file.*






