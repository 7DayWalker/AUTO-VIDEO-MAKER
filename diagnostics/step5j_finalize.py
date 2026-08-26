"""Step 5J: finalize the Step-5I Sandbox private publication record.

Applies the new VERIFIED_SANDBOX_PRIVATE terminal state to EXACTLY ONE record
using only durable evidence already in the manifest. Preconditions are hard:
wrong publish_id, missing PUBLISH_COMPLETE, non-null post_id, wrong privacy,
or wrong prior state aborts without any write. No network calls.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT / ".env")

import os  # noqa: E402

EXPECTED_PUBLISH_ID = "v_pub_file~v2-1.7678245367661578258"
MANIFEST = PROJECT / "output/projects/20260821_why_some_plants_sleep_at_night_570/manifest.json"

from app.publishing.models import PublicationState  # noqa: E402
from app.publishing import tiktok_reconciliation as tt  # noqa: E402


def main() -> int:
    if os.getenv("TIKTOK_PUBLISH_ENVIRONMENT") != "sandbox":
        print("REFUSING: TIKTOK_PUBLISH_ENVIRONMENT != sandbox")
        return 2

    manifest_path, manifest = tt.load_manifest(
        "20231024_why_plants_sleep_001", MANIFEST.parent
    )
    record = tt.get_tiktok_record(manifest)
    ev = record.verification_evidence or {}

    checks = {
        "publish_id matches Step-5I": record.tiktok_publish_id == EXPECTED_PUBLISH_ID,
        "evidence status PUBLISH_COMPLETE": ev.get("status") == "PUBLISH_COMPLETE",
        "post_id is null": record.tiktok_post_id is None and ev.get("post_id") is None,
        "privacy SELF_ONLY": bool(record.tiktok_metadata)
        and record.tiktok_metadata.privacy_level == "SELF_ONLY",
        "prior state PROCESSING": record.state == PublicationState.PROCESSING,
    }
    for name, ok in checks.items():
        print(f"{name:<36}: {'YES' if ok else 'NO'}")
    if not all(checks.values()):
        print("PRECONDITION FAILURE - no write performed.")
        return 3

    record.state = PublicationState.VERIFIED_SANDBOX_PRIVATE
    record.verified_at = datetime.now().isoformat()
    record.error = None
    ev["classification"] = "sandbox_private_terminal"
    ev["reason"] = (
        "PUBLISH_COMPLETE without post_id under explicit "
        "Sandbox/unaudited SELF_ONLY private context; human-confirmed "
        "private visibility in TikTok app (Step 5J)"
    )
    tt.persist_tiktok_record(manifest_path, manifest, record)

    after = json.loads(MANIFEST.read_text(encoding="utf-8"))["tiktok_publication"]
    print("---")
    print("state            :", after["state"])
    print("verified_at      :", after.get("verified_at"))
    print("history preserved:", len(after.get("reconciliation_history") or []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
