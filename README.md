# AUTO Video Maker

A local automation system that produces short-form videos and publishes them to YouTube Shorts and TikTok.

## Current Objective

Validate the concept of automated short-form video production using Google Flow's free credits for video generation, with a target of approximately 2 videos per day once fully operational.

## Current Phase

**Phase 0 — Project Foundation**

The project is in its initial setup phase. No video generation, publishing, or external API connections are active yet.

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

### 4. Configure Environment Variables

Copy the example environment file and edit it with your values:

```cmd
copy .env.example .env
```

For Phase 0, no real API keys are needed. The defaults work fine.

### 5. Run the System (Health Check)

```cmd
python main.py
```

This will verify that the configuration loads and all required directories exist.

### 6. Run Tests

```cmd
pytest
```

---

## Project Structure

```
AUTO-VIDEO-MAKER/
│
├── app/                  # Application source code
│   ├── __init__.py
│   └── config.py         # Centralized configuration
│
├── tests/                # Test suite
│   ├── __init__.py
│   └── test_config.py    # Configuration tests
│
├── projects/             # Working files for video projects (gitignored)
├── output/               # Generated video output (gitignored)
├── assets/               # Static assets (images, fonts, templates)
├── logs/                 # Application logs (gitignored)
├── credentials/          # API credentials (gitignored)
│
├── .env.example          # Template for environment variables
├── .gitignore            # Git ignore rules
├── README.md             # This file
├── requirements.txt      # Python dependencies
└── main.py               # Entry point / health check
```

---

## Planned Pipeline (Future Phases)

Content idea → content specification → video prompts → video generation → asset management → video assembly → audio → captions → quality control → YouTube publishing → TikTok publishing

**None of these features are implemented yet.**

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
