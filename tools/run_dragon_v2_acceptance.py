"""Explicit, checkpointed one-plan Dragon Queen acceptance. Never retry planning."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PID = "prd_20261107_the_last_dragon_queen_part_i_ai_short_fi_057"
PREFIX = f"/productions/{PID}/repurpose/batches"
LEDGER = ROOT / "diagnostics/dragon-v2-live-ledger.json"


def api(path, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request("http://127.0.0.1:8787/api" + path, data=data,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def main():
    from app.repurpose.storage import atomic_json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["plan", "render", "promote", "status"])
    parser.add_argument("--render-request-id", default="dragon-v2-render-acceptance-one")
    args = parser.parse_args()
    payload = json.loads((ROOT / "diagnostics/dragon-v2-live-request.json").read_text(encoding="utf-8"))
    batch_id = "rpb-" + hashlib.sha256(f"{PID}\0{payload['request_id']}".encode()).hexdigest()[:24]
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"batch_id": batch_id, "production_id": PID}
    if args.step == "plan":
        proxy = Path.home() / "proxy.js"
        fingerprint_path = ROOT / "diagnostics/repurpose-proxy-approved-sha256.json"
        approved = json.loads(fingerprint_path.read_text(encoding="utf-8"))["sha256"]
        if hashlib.sha256(proxy.read_bytes()).hexdigest() != approved:
            raise RuntimeError("Proxy changed after offline verification; paid dispatch blocked")
        if ledger.get("plan_dispatched"):
            print("Existing dispatch checkpoint; GET only.")
        else:
            ledger["plan_dispatched"] = True; atomic_json(LEDGER, ledger)
            result = api(PREFIX, payload)
            print("planned_dispatch", result["batch_id"], result["status"], flush=True)
    if args.step in {"plan", "render"}:
        if args.step == "render":
            job = api(PREFIX + "/" + batch_id)
            expected = ["unit-short-004", "unit-short-005", "unit-short-006"]
            if [c["unit_id"] for c in job.get("clips", [])] != expected:
                raise RuntimeError("Unexpected units; existing v1 units must remain untouched")
            if job["status"] == "rendered":
                print("Already rendered; GET only.")
            else:
                request_id = args.render_request_id
                result = api(PREFIX + "/" + batch_id + "/render", {"request_id": request_id, "confirm": True,
                    "expected_plan_hash": job["plan_hash"], "unit_ids": expected})
                ledger["render_requested"] = True; atomic_json(LEDGER, ledger)
                print("render_dispatch", result["status"], flush=True)
        for _ in range(720):
            job = api(PREFIX + "/" + batch_id)
            print("status", job["status"], job.get("units", {}), flush=True)
            if job["status"] not in {"planning", "rendering"}:
                atomic_json(ROOT / "diagnostics/dragon-v2-live-status.json", job)
                if job["status"] not in {"planned", "rendered"}: raise RuntimeError(json.dumps(job.get("error") or job.get("units")))
                break
            time.sleep(10)
        else:
            raise RuntimeError("Acceptance polling expired; inspect saved batch, do not replay planning")
    if args.step == "promote":
        job = api(PREFIX + "/" + batch_id)
        if job["status"] != "rendered": raise RuntimeError("Complete validated renders required before promotion")
        results = []
        for clip in job["clips"]:
            if clip["unit_id"] not in {"unit-short-004", "unit-short-005", "unit-short-006"}: raise RuntimeError("Wrong unit")
            result = api(f"/productions/{PID}/units/{clip['unit_id']}/repurpose-video/promote", {"confirm": True,
                "expected_plan_hash": job["plan_hash"], "expected_file_fingerprint": clip["render"]["file_fingerprint"]}, timeout=180)
            results.append({"unit_id": clip["unit_id"], "project_id": result.get("project_id"), "promoted": result.get("promoted")})
            print("promotion", results[-1], flush=True)
        ledger["promotions"] = results; atomic_json(LEDGER, ledger)
    if args.step == "status":
        job = api(PREFIX + "/" + batch_id)
        print(json.dumps({k: job.get(k) for k in ("batch_id", "status", "units", "llm_calls", "llm_successful_calls", "error")}, indent=2))


if __name__ == "__main__":
    main()