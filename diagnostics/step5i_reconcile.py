"""Step 5I: evidence-preserving reconciliation of the failed TikTok attempt.

Preserves the historical failure inside record.reconciliation_history (and an
external forensic JSON copy), then transitions the record to PREPARED so the
canonical pipeline can submit fresh. Touches NOTHING else.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

MANIFEST = PROJECT / "output/projects/20260821_why_some_plants_sleep_at_night_570/manifest.json"
FORENSIC = PROJECT / "diagnostics/step5i_reconciliation_evidence.json"

from app.publishing.models import PublicationState  # noqa: E402
from app.publishing import tiktok_reconciliation as tt  # noqa: E402


def main() -> int:
    manifest_path, manifest = tt.load_manifest(
        "20231024_why_plants_sleep_001", MANIFEST.parent
    )
    record = tt.get_tiktok_record(manifest)
    assert record is not None, "no tiktok_publication record"

    print("state before        :", record.state.value)
    print("publish_id          :", record.tiktok_publish_id)
    print("error               :", (record.error or "")[:120])

    if (
        record.state != PublicationState.UNKNOWN_RECONCILIATION_REQUIRED
        or record.tiktok_publish_id is not None
    ):
        print("REFUSING: state/id precondition not met.")
        return 2

    entry = {
        "superseded_at_utc": datetime.now(timezone.utc).isoformat(),
        "previous_state": record.state.value,
        "classification": "direct_post_init_rejected",
        "http_status": 400,
        "error_message": "The total chunk count is invalid",
        "tiktok_log_id": "2026082600323750988B79F7A172183C55",
        "publish_id_issued": False,
        "original_failure_at_local": record.updated_at,
        "authorized_by": "Paul - Step 5I",
        "note": (
            "INIT was rejected by TikTok before any identity was issued; no "
            "orphaned publish_id exists at TikTok for this attempt. Evidence "
            "preserved verbatim; fresh canonical submission authorized."
        ),
    }

    # Preserve BEFORE mutating anything else.
    record.reconciliation_history = list(record.reconciliation_history or []) + [entry]
    FORENSIC.write_text(json.dumps(entry, indent=2), encoding="utf-8")

    # Minimal transition: exactly as far as the canonical pipeline requires.
    record.state = PublicationState.PREPARED
    record.error = None

    tt.persist_tiktok_record(manifest_path, manifest, record)

    after = json.loads(MANIFEST.read_text(encoding="utf-8"))["tiktok_publication"]
    print("---")
    print("history entries     :", len(after["reconciliation_history"]))
    print("state after         :", after["state"])
    print("error after         :", after.get("error"))
    print("youtube untouched   :", m_yt := json.loads(MANIFEST.read_text(encoding="utf-8"))["publication"]["state"])
    print("forensic copy       :", FORENSIC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
