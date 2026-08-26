"""Step 5F-B: ONE controlled TikTok Direct Post INIT-only probe.

Executes exactly ONE live init request with TIKTOK_SINGLE_CHUNK_INIT=1
(chunk_size = exact file size, total_chunk_count = 1).

Hard guarantees enforced by this script:
- No upload, no PUT, no status polling, no reconciliation.
- No manifest modification.
- No secrets printed (tokens/secrets/full upload URL never emitted).
- Durable sanitized evidence written to diagnostics/step5f_single_chunk_init_probe.json.
"""

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT / ".env")

import os  # noqa: E402

os.environ["TIKTOK_SINGLE_CHUNK_INIT"] = "1"  # explicit opt-in for THIS process only

from app.publishing.tiktok import (  # noqa: E402
    TikTokPublisher,
    _resolve_chunk_params,
)

MANIFEST = PROJECT / "output/projects/20260821_why_some_plants_sleep_at_night_570/manifest.json"
ARTIFACT = PROJECT / "diagnostics/step5f_single_chunk_init_probe.json"


def mask(value: str, head: int = 10) -> str:
    return value[:head] + "…(masked)" if value else ""


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record = manifest["tiktok_publication"]

    # ---------------- Phase 1: pre-call safety checks ----------------
    video_path = Path(record["final_video_path"])
    size = video_path.stat().st_size if video_path.exists() else 0
    digest = hashlib.sha256(video_path.read_bytes()).hexdigest() if size else ""
    fingerprint_ok = bool(size) and digest == record.get("file_fingerprint")

    chunk_size, total_chunks = _resolve_chunk_params(size, 10_000_000)

    print("=== PHASE 1 PRE-CALL SAFETY CHECK ===")
    print("candidate file      :", video_path)
    print("file exists         :", "YES" if size else "NO")
    print("file size           :", size)
    print("sha256 match        :", "YES" if fingerprint_ok else "NO")
    print("manifest state      :", record["state"])
    print("publish_id present  :", "YES" if record.get("tiktok_publish_id") else "NO")
    print("effective chunk_size:", chunk_size)
    print("effective total     :", total_chunks)

    preconditions = (
        size == 22_670_365
        and fingerprint_ok
        and record.get("tiktok_publish_id") is None
        and (chunk_size, total_chunks) == (size, 1)
    )
    if not preconditions:
        print("PRECONDITION FAILURE - no live call made.")
        return 2

    # Credentials via existing mechanism; never printed.
    publisher = TikTokPublisher.from_env()
    try:
        user = publisher.authenticate()
        auth_ok = bool(user)
        display_name = user.get("display_name") if isinstance(user, dict) else None
    except Exception as exc:  # noqa: BLE001 - truthful failure reporting
        auth_ok = False
        display_name = f"AUTH ERROR: {exc}"
    print("auth successful     :", "YES" if auth_ok else "NO", "-", display_name)
    if not auth_ok:
        print("AUTH FAILURE - no live init call made.")
        return 3

    return _phase2(publisher, record, video_path, size, digest, chunk_size,
                   total_chunks, display_name)


def _phase2(publisher, record, video_path, size, digest, chunk_size,
            total_chunks, display_name):
    print("\n=== PHASE 2: SINGLE LIVE INIT REQUEST (init-only) ===")
    outcome = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_path": str(video_path),
        "file_size": size,
        "sha256_fingerprint": digest,
        "manifest_state_before": record["state"],
        "effective_chunk_size": chunk_size,
        "effective_total_chunk_count": total_chunks,
        "outcome": None,
        "http_status": None,
        "error_code": None,
        "error_message": None,
        "log_id": None,
        "publish_id": None,
        "publish_id_present": False,
        "upload_url_present": False,
        "auth_display_name": display_name,
    }
    try:
        result = publisher.init_direct_post(
            video_path=str(video_path),
            title=record["tiktok_metadata"]["title"],
            privacy_level=record["tiktok_metadata"]["privacy_level"],
            disable_comment=record["tiktok_metadata"]["disable_comment"],
            disable_duet=record["tiktok_metadata"]["disable_duet"],
            disable_stitch=record["tiktok_metadata"]["disable_stitch"],
            video_cover_timestamp_ms=record["tiktok_metadata"].get(
                "video_cover_timestamp_ms"
            ),
        )
        outcome["outcome"] = "SUCCESS"
        outcome["http_status"] = 200
        outcome["publish_id"] = result.get("publish_id")
        outcome["publish_id_present"] = bool(result.get("publish_id"))
        outcome["upload_url_present"] = bool(result.get("upload_url"))
        print("HTTP                : 200 OK (TikTok accepted the declaration)")
        print("publish_id present  :", "YES" if outcome["publish_id_present"] else "NO")
        if outcome["publish_id"]:
            print("publish_id (masked) :", mask(outcome["publish_id"]))
        print("upload_url present  :", "YES" if outcome["upload_url_present"] else "NO")
        print("(upload_url value NOT printed; NO upload performed)")
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        m = re.search(r"HTTP (\d+)", message)
        outcome["outcome"] = "FAILURE"
        outcome["http_status"] = int(m.group(1)) if m else None
        jm = re.search(r"\{.*\}", message)
        if jm:
            try:
                err = json.loads(jm.group(0)).get("error", {})
                outcome["error_code"] = err.get("code")
                outcome["error_message"] = err.get("message")
                outcome["log_id"] = err.get("log_id")
            except json.JSONDecodeError:
                outcome["error_message"] = message[:300]
        else:
            outcome["error_message"] = message[:300]
        print("INIT FAILED:")
        print("  http status       :", outcome["http_status"])
        print("  error code        :", outcome["error_code"])
        print("  error message     :", outcome["error_message"])
        print("  log_id            :", outcome["log_id"])

    # ---------------- Phase 3: durable diagnostic evidence ----------------
    ARTIFACT.write_text(json.dumps(outcome, indent=2), encoding="utf-8")
    print("\n=== PHASE 3: EVIDENCE WRITTEN ===")
    print("artifact            :", ARTIFACT)
    print("\nHARD STOP - no further TikTok calls. Manifest untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

