# AUTO Video Maker

A local automation system that produces short-form videos and publishes them to YouTube Shorts and TikTok.

## Current Objective

Operate the signed-off AVM V1 product through one AVM Core: durable productions and
units, canonical model selection, local/AI video creation, source-video repurposing
and grounded narration, validated media, and explicit manual YouTube/TikTok publishing.
K3 is an independent control surface, not a second production engine.

## Current Phase

**Phase 11C COMPLETE — Tracks 1–14 Verified & Signed Off (2026-10-24).**

Authoritative scope and historical audit trail: [AVM_MASTER_ROADMAP.md](AVM_MASTER_ROADMAP.md).
The next phase is **Phase 12 — Automated Publishing, Scheduling & V1.x Operator Polish**;
automation/scheduling is not implemented by the V1 closure.

Verified closure: **Core 1,904 passed, 0 failed, 1 skipped** (opt-in live E2E),
**K3 86 passed, 0 failed**, and a successful K3 production build with zero errors.
Core code checkpoint: `a1bb5ed`; K3 real-mode Summary checkpoint: `b6a7f3d`.

**Phase 11C-PLUS V1.x increment — implemented and verified (2026-10-25):** standalone
AI Summary → canonical publishable project handoff, with **170/170 focused Core
Track 9/10 tests**, **96/96 additional Core regressions**, **97/97 K3 tests**, zero
live external network calls, and a successful K3 build. K3 handoff checkpoint:
`fe2719d`. The dated V1 closure above remains the historical baseline; Phase 12
automation/scheduling is still not implemented.

---

## Setup Instructions (Windows)

### 1. Create a Virtual Environment

Open a terminal in the project root directory and run:

```cmd
python -m venv venv
```

### 2. Activate the Virtual Environment

```cmd
venv\Scripts\activate
```

### 3. Install Dependencies

```cmd
pip install -r requirements.txt
```

### 4. Install Playwright Chromium Browser

After installing the Python dependencies, install the Chromium browser for Playwright:

```cmd
playwright install chromium
```

This downloads the Chromium binary that Playwright will use. It is separate from your system Chrome/Edge browser.

### 5. Configure Environment Variables

Copy the example environment file and edit it with your values:

```cmd
copy .env.example .env
```

Hermetic tests require no real API credentials or `.env`. Real AI generation,
narration and platform publishing require the applicable configured provider or
stored platform authorization; missing configuration fails truthfully. Transcription
uses local Whisper by default. Google Flow remains a private operator route with
manual authentication and credit protections. Never use an Alibaba Token Plan
`sk-sp-` credential inside AVM; mock execution is an explicit offline option only.

### 6. Run the System Health Check

```cmd
python main.py
```

This verifies that the configuration loads and all required directories exist.

### 7. Run Unit Tests

```cmd
python tools\run_hermetic_pytest.py tests -q --tb=short
```

The strict runner disables dotenv loading, removes inherited provider configuration,
isolates runtime/credential/profile roots, and blocks external networking while
allowing loopback HTTP route tests. Synthetic configuration belongs to individual
tests; no AI-provider, YouTube or TikTok calls are permitted in this run. Local
FFmpeg/FFprobe fixtures remain real; installed media tools are required.

K3 lives in the separate repository
`C:\Users\Paul Ella\AppData\Local\Cline\avm-studio-pro`. From that repository,
run `node --test tests/*.mjs` (Git Bash, or expanded file arguments in PowerShell)
and `npm run build`. The closure build has a non-fatal bundle-size warning, not a
build error; Core's existing Pydantic deprecation warnings remain documented.

### 8. Core / K3 Control Surface Boundary

Run `python -m app.web.server` for Core HTTP `/api/*` (default
`http://127.0.0.1:8787`). K3 runs on `:5173` and proxies same-origin `/api` requests
to Core; `AVM_CORE_URL` configures the target. Production intent, execution and
binding remain Core-owned. Core production/media roots are configurable/isolatable;
there is no multi-tenant database/auth, billing ledger or native mobile app in V1.

Production/progress/capability/media reads do not start production or upload.
Publication-status GETs may reconcile an **existing** remote identity and persist
fresh verification evidence; connection checks may refresh stored credentials.
They are non-interactive, not universally read-only. OAuth consent is an explicit
connect command; automated retries/publishing/scheduling remain Phase 12.

---

## Historical Phase 1: Google Flow Proof of Concept

The following records the original Flow proof-of-concept workflow, not current V1
phase status. The dedicated-profile/manual-authentication and credit safety rules
remain in force; the authoritative current architecture is in the roadmap above.

### What It Does

The Flow test script (`flow_test.py`) automates a video generation workflow through Google Flow using your normal authenticated browser session.

### First-Run Authentication

On the **first run**, Chromium will open and navigate to Google Flow. If you are not already logged in, the script will:

1. Display a message: `AUTHENTICATION REQUIRED`
2. Wait for you to manually log in to your Google account in the browser window
3. Continue automatically once authentication is detected

**Your Google password is NEVER stored by this project.**

### Running the Flow Test

```cmd
python flow_test.py
```

The script will:
1. Launch Chromium with a persistent browser profile
2. Open Google Flow
3. Wait for authentication if needed (manual login)
4. Navigate to video creation
5. Configure settings (9:16 vertical, cheapest/fastest option)
6. Enter the test prompt
7. Submit generation
8. Wait for completion (up to 10 minutes)
9. Download the video
10. Save it to `output/flow_test/flow_test_YYYYMMDD_HHMMSS.mp4`
11. Validate the file
12. Report success/failure

### Where Things Are Stored

| Item | Location | Git Status |
|------|----------|------------|
| Browser profile (login session) | `browser_profile/` | Gitignored |
| Generated test videos | `output/flow_test/` | Gitignored |
| Screenshots (errors/debug) | `logs/flow/` | Gitignored |
| Flow configuration | `app/flow/config.py` | Tracked |

### Timeouts (Configurable via .env)

| Setting | Default | Env Variable |
|---------|---------|--------------|
| Manual auth wait | 300s (5 min) | `FLOW_AUTH_TIMEOUT` |
| Page navigation | 30s | `FLOW_NAV_TIMEOUT` |
| Video generation | 600s (10 min) | `FLOW_GEN_TIMEOUT` |
| Download | 120s (2 min) | `FLOW_DOWNLOAD_TIMEOUT` |
| Polling interval | 5s | `FLOW_POLL_INTERVAL` |

### Troubleshooting

**Browser doesn't open:**
- Make sure Playwright Chromium is installed: `playwright install chromium`

**Authentication keeps being required:**
- The `browser_profile/` directory stores your session. Don't delete it between runs.
- If Google requires re-authentication, just log in manually when prompted.

**Flow UI has changed:**
- The automation adapts to common UI patterns but may fail if Google significantly changes Flow.
- Check screenshots in `logs/flow/` for what the automation saw.
- Report the issue so selectors can be updated.

**Generation times out:**
- Increase `FLOW_GEN_TIMEOUT` in your `.env` file.
- Some generations take longer than others.

**Download fails:**
- Check `logs/flow/` for screenshots showing the download button state.
- Flow may have changed its download mechanism.

---

## Project Structure (Historical Phase 1 Snapshot)

This original snapshot is retained as history, not a complete V1 module inventory.
Current production, content, assembly, voice, transcription, publishing and HTTP
seams are documented in `AVM_MASTER_ROADMAP.md`; K3 remains a separate repository.

```
AUTO-VIDEO-MAKER/
│
├── app/                  # Application source code
│   ├── __init__.py
│   ├── config.py         # Centralized configuration
│   └── flow/             # Google Flow automation module
│       ├── __init__.py
│       ├── config.py     # Flow-specific settings & timeouts
│       ├── browser.py    # Persistent browser management
│       └── automation.py # Core Flow automation workflow
│
├── tests/                # Test suite
│   ├── __init__.py
│   ├── test_config.py    # Configuration tests
│   └── test_flow.py      # Flow module tests (non-browser)
│
├── browser_profile/      # Persistent Chromium profile (gitignored)
├── projects/             # Working files for video projects (gitignored)
├── output/               # Generated video output (gitignored)
│   └── flow_test/        # Flow test videos
├── assets/               # Static assets (images, fonts, templates)
├── logs/                 # Application logs (gitignored)
│   └── flow/             # Flow automation screenshots
├── credentials/          # API credentials (gitignored)
│
├── .env.example          # Template for environment variables
├── .gitignore            # Git ignore rules
├── README.md             # This file
├── requirements.txt      # Python dependencies
├── main.py               # Entry point / health check
└── flow_test.py          # Phase 1: Flow proof-of-concept test
```

---

## Implemented V1 Production Pipeline and V1.x Boundaries

Production command → durable `Production` / `ProductionUnit` intent → canonical
planning → provider-independent execution → project-owned assets and conditional
audio → captions / FFmpeg assembly → validation / accepted completed project →
explicit manual YouTube/TikTok publishing and durable reconciliation evidence.

- **Track 8 — V1 complete:** source video registration, timestamped transcription,
  validated source segments, deterministic/AI candidate moment selection through the
  shared grounded-planning primitives, and derived `ProductionUnit` lineage.
  Deterministic synthetic planning is mock-labelled; source lineage alone is not
  execution completion. Derived units publish through the canonical path once bound
  to an accepted completed project. Direct clip-rendering shortcuts bypassing that
  execution path remain **V1.x**; no separate unattended clipping engine is claimed.
- **Track 9 — V1 complete:** real Summary Standard text+JSON grounded planning,
  windowed Summary Pro visual understanding/narration, TTS synthesis, and
  `narrated_summary.mp4` assembly with Core HTTP and K3 SummaryStudio integration.
  Users select canonical models; AVM resolves providers internally with same-model
  failover and truthful `ready` / `not_configured` / `unknown` readiness.
  SummaryStudio defaults to real execution; its sandbox checkbox is opt-in.
- Standalone summary sidecars are inspectable/downloadable through
  `/api/productions/{id}/units/{unitId}/summary-video` and `/summary-video/media`.
  **Phase 11C-PLUS — V1.x handoff implemented and verified:** explicitly call
  `POST /api/productions/{pid}/units/{uid}/summary-video/promote`, or request
  `promote_to_publishable: true` during assembly (default `false`).
  `app/production/summary_handoff.py` binds a project-owned accepted master and
  records `source_summary_handoff` provenance on its canonical `ProjectManifest`.
  The existing `ProductionExecutor` binding checks and reconciliation validate
  `PROJECT_COMPLETE` / `COMPLETED`; engine voice/scene generation is truthfully
  skipped, not fabricated. Standard bound projects and existing publication
  records are protected. **Unpromoted summary-only units still return
  `422 SUMMARY_HANDOFF_REQUIRED` until explicitly promoted.**
  K3 offers **Promote to Publishable Master & Open Export** in SummaryStudio and
  **Promote Summary Video for Publishing** in Export. Promotion never uploads;
  YouTube/TikTok use the existing manual publishing engine with separate platform
  inspection/confirmation. Automated publishing/scheduling remains Phase 12.
- Tracks 11–13 are complete as ownership/storage/credential/media, operation-accounting
  and two-repo HTTP/mobile-readiness seams, not implemented SaaS billing/auth or a
  native mobile app. Costs and attempt/fallback provenance are durable evidence;
  unknown cost is never fabricated as zero or an account credit balance.
- Automated publishing, scheduling and V1.x operator polish are next in Phase 12;
  autonomous quality/scale, intelligent optimization and generalized recovery retain
  their later-phase gates. Provider quota/uptime and compatible free video-model
  availability remain external prerequisites, not guarantees from passing tests.

---

## Development Rules

1. Build one phase at a time.
2. Do not implement future phases unless explicitly instructed.
3. Keep dependencies minimal.
4. Prefer simple Python modules over complex agent frameworks.
5. Do NOT use CrewAI or LangGraph unless explicitly authorized.
6. Use official APIs wherever practical.
7. Do not bypass authentication, CAPTCHAs, rate limits, or anti-abuse systems.
8. Keep video-generation providers modular (replaceable).
9. Keep social-media publishing providers modular.
10. Store secrets only in `.env` — never commit them to Git.
11. Every phase must have a clear test and pass/fail condition.
12. Do not make speculative features.
13. Do not over-engineer.

---

## Security

- **Browser profile** (`browser_profile/`) is gitignored — session cookies never committed.
- **Credentials directory** is gitignored.
- **`.env`** is gitignored.
- **No Google password** is ever stored by this project.
- **No authentication cookies** are committed to Git.
- **No session tokens** are printed to logs.
