#!/usr/bin/env python
"""
AUTO Video Maker - TikTok configuration diagnostic (READ-ONLY).

Reports safe, non-secret diagnostics about the TikTok OAuth configuration:
value lengths, masked head/tail, one-way fingerprints, formatting anomalies,
.env vs OS-environment precedence, and the effective redirect/scopes.

NEVER prints credential values. NEVER launches OAuth. NEVER calls any API.
"""

import hashlib
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"

NAMES = ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"]


def fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def mask(value: str) -> str:
    if len(value) <= 6:
        return f"**{len(value)} chars**"
    return f"{value[:2]}...{value[-3:]}"


def raw_env_lines():
    text = ENV_PATH.read_text(encoding="utf-8", errors="replace")
    return text, re.findall(rf"(?m)^({{'|'.join(NAMES)}})=(.*)$", text) if False else None


def main() -> int:
    print("=" * 62)
    print("TIKTOK CONFIG DIAGNOSTIC  (read-only, no secrets printed)")
    print("=" * 62)

    if not ENV_PATH.exists():
        print(".env NOT FOUND at", ENV_PATH)
        return 1

    text = ENV_PATH.read_text(encoding="utf-8", errors="replace")

    # --- 1. Raw .env line hygiene -----------------------------------------
    print("\n[1] RAW .env LINE HYGIENE")
    parsed_file = {}
    for name in NAMES:
        m = re.search(rf"(?m)^{name}=(.*)$", text)
        if not m:
            print(f"  {name}: NOT PRESENT in .env")
            continue
        raw = m.group(1)
        issues = []
        if raw != raw.rstrip():
            issues.append("trailing whitespace/CR")
        if raw.startswith(('"', "'")) or raw.endswith(('"', "'")):
            issues.append("wrapped in quotes")
        if re.search(rf"(?m)^{name}\s+= ", text):
            issues.append("space around '='")
        if any(ord(c) > 126 for c in raw):
            issues.append("non-ASCII characters present!")
        parsed_file[name] = raw.strip().strip('"').strip("'")
        status = "; ".join(issues) if issues else "clean"
        print(f"  {name}: len={len(raw)}  ->  {status}")

    # --- 2. Runtime effective values (after python-dotenv semantics) ------
    print("\n[2] RUNTIME EFFECTIVE VALUES (python-dotenv loaded like app/config.py)")
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH)  # same call as app/config.py; override=False default

    for name in NAMES:
        v = os.environ.get(name)
        if not v:
            print(f"  {name}: MISSING at runtime")
            continue
        file_v = parsed_file.get(name)
        print(f"  {name}: len={len(v)}  head={mask(v)}  {fingerprint(v)}")
        if file_v is not None and v != file_v:
            print(f"    !! DIFFERS from .env file value -> an OS/process environment "
                  f"variable is OVERRIDING .env (dotenv override=False)")
        if name == "TIKTOK_CLIENT_KEY" and not re.fullmatch(r"[A-Za-z0-9]+", v or ""):
            print("    !! FORMAT WARNING: contains characters outside [A-Za-z0-9] "
                  "(TikTok client keys/secret strings are plain alphanumerics)")
        if name == "TIKTOK_CLIENT_KEY" and len(v) < 16:
            print("    !! FORMAT WARNING: suspiciously short for a TikTok client key")

    k = os.environ.get("TIKTOK_CLIENT_KEY") or ""
    s = os.environ.get("TIKTOK_CLIENT_SECRET") or ""
    print(f"\n[3] KEY/SECRET SWAP CHECK:  key == secret ? {k == s and bool(k)}")
    if k and s:
        print(f"    key   fingerprint: {fingerprint(k)}")
        print(f"    secret fingerprint: {fingerprint(s)}")

    # --- 4. Effective OAuth surface ----------------------------------------
    sys.path.insert(0, str(PROJECT_ROOT))
    from app.publishing.tiktok import TIKTOK_SCOPES, TikTokPublisher

    pub = TikTokPublisher.from_env()
    print("\n[4] EFFECTIVE OAUTH SURFACE")
    print(f"  redirect_uri : {pub.redirect_uri}")
    print(f"  scopes       : {','.join(TIKTOK_SCOPES)}")
    print(f"  token file   : exists={pub.token_path.exists()}")

    print("\nDone. No values printed. No OAuth launched. No API calls made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
