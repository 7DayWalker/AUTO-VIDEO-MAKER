# AUTO Video Maker

A local automation system that produces short-form videos and publishes them to YouTube Shorts and TikTok.

## Current Objective

Validate the concept of automated short-form video production using Google Flow's free credits for video generation, with a target of approximately 2 videos per day once fully operational.

## Current Phase

**Phase 1 — Google Flow Proof of Concept**

Prove that we can reliably automate a normal authenticated Google Flow browser session and download one generated video.

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

For Phase 1, no real API keys are needed. The defaults work fine.

### 6. Run the System Health Check

```cmd
python main.py
```

This verifies that the configuration loads and all required directories exist.

### 7. Run Unit Tests

```cmd
pytest
```

---

## Phase 1: Google Flow Proof of Concept

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

## Project Structure

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

## Planned Pipeline (Future Phases)

Content idea → content specification → video prompts → video generation → asset management → video assembly → audio → captions → quality control → YouTube publishing → TikTok publishing

**None of these production features are implemented yet.**

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
