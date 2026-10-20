"""TRACK 9 TASK A -- CodeCraft MODEL IDENTITY TRUST discriminator.

WHY THIS EXISTS

The echo probe showed CodeCraft returns ``model`` == whatever was requested,
AND that ``gemma-2-2b`` (a 2-billion-parameter, metadata-declared TEXT-ONLY
model) produced an accurate description of a real film frame. A 2B text-only
model cannot do that. Therefore either:

  (a) CodeCraft's /models metadata is wrong for many entries, AND/OR
  (b) CodeCraft routes requests to a different backing model while echoing the
      requested id (silent substitution).

Either way the requested-id echo is NOT proof of model identity.

This probe applies a CAPABILITY DISCRIMINATOR: a task where a small model and a
frontier model must differ sharply. If a declared 2B model answers at frontier
level, model identity on this gateway is UNVERIFIABLE and must be recorded as
such -- never asserted as a verified canonical binding.

Run: python tools/track9_codecraft_identity_trust.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EVIDENCE = ROOT / "_real_smoke_wk" / "codecraft_identity_trust.json"

# gemma-2-2b: declared text->text, 8192 ctx, 2B params (tiny).
# claude-opus-5.5: declared frontier, 1M ctx.
# A tiny model must fail the discriminator that the frontier model passes.
PROBE_MODELS = ["gemma-2-2b", "claude-opus-5.5", "qwen3.8-27b"]

# Deterministic, verifiable discriminator with a single correct answer.
DISCRIMINATOR = (
    "Solve precisely and show only the final answer on the last line as 'ANSWER: <n>'.\n"
    "A bag has 3 red, 4 blue and 5 green marbles. Two are drawn without replacement. "
    "What is the probability both are the same colour? Give the answer as a reduced fraction."
)
EXPECTED = "19/66"


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _retry(base, key, payload, attempts=4, timeout=180):
    last = {}
    for i in range(attempts):
        try:
            r = requests.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload, timeout=timeout,
            )
            res = {"status": r.status_code}
            try:
                res["json"] = r.json()
            except ValueError:
                res["text"] = (r.text or "")[:400]
        except Exception as exc:  # noqa: BLE001
            res = {"error": f"{type(exc).__name__}: {exc}"}
        last = res
        st = res.get("status")
        if st == 200 or (isinstance(st, int) and 400 <= st < 500):
            res["_attempts"] = i + 1
            return res
        time.sleep(2 + 2 * i)
    last["_attempts"] = attempts
    return last


def _answer(res):
    jb = res.get("json") if isinstance(res.get("json"), dict) else {}
    ch = jb.get("choices") or []
    if ch and isinstance(ch[0], dict):
        return str(((ch[0].get("message") or {}).get("content") or "")).strip()
    return ""


def main() -> int:
    _load_env()
    key = (os.getenv("CODECRAFT_API_KEY") or "").strip()
    base = ((os.getenv("CODECRAFT_BASE_URL") or "").strip() or "https://codecraftapi.com/v1").rstrip("/")
    if not key:
        print("no CODECRAFT_API_KEY -> STOP")
        return 1

    print("=" * 72)
    print("TRACK 9 -- CODECRAFT MODEL IDENTITY TRUST DISCRIMINATOR")
    print("=" * 72)
    print(f"discriminator expected answer: {EXPECTED}")
    print("a declared 2B text-only model should NOT reliably solve this")

    evidence: dict = {"base_url": base, "expected": EXPECTED, "models": {}}
    for mid in PROBE_MODELS:
        res = _retry(base, key, {
            "model": mid,
            "messages": [{"role": "user", "content": DISCRIMINATOR}],
            "max_tokens": 700, "temperature": 0,
        })
        ans = _answer(res)
        norm = ans.replace(" ", "").replace("\\", "")
        correct = EXPECTED.replace("/", "/") in norm or "19/66" in norm
        evidence["models"][mid] = {
            "status": str(res.get("status") or res.get("error")),
            "attempts": res.get("_attempts"),
            "correct": bool(correct),
            "answer_tail": ans[-220:],
        }
        print("-" * 72)
        print(f"  {mid:20s} status={evidence['models'][mid]['status']:5s} correct={correct}")
        print(f"     tail: {ans[-180:]!r}")
        time.sleep(1)

    tiny = evidence["models"].get("gemma-2-2b", {})
    frontier = evidence["models"].get("claude-opus-5.5", {})
    print("=" * 72)
    if tiny.get("correct") and frontier.get("correct"):
        verdict = "MODEL_IDENTITY_UNVERIFIABLE"
        print("  gemma-2-2b (declared 2B text-only) solved a frontier-level task")
        print("  AND previously described a real image accurately.")
        print("  => CodeCraft per-model identity is UNVERIFIABLE from the API alone.")
        print("  => Register CodeCraft bindings ONLY with identity_verification=declared,")
        print("     never as 'verified'. Capability (vision) IS proven at gateway level.")
    elif frontier.get("correct") and not tiny.get("correct"):
        verdict = "MODEL_IDENTITY_DISCRIMINATED"
        print("  Tiny model failed, frontier model passed => per-model routing")
        print("  behaves consistently with declared identities.")
    else:
        verdict = "INCONCLUSIVE"
        print("  Discriminator inconclusive (gateway errors or both failed).")
    evidence["verdict"] = verdict

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2)[:200000], encoding="utf-8")
    print(f"VERDICT: {verdict}")
    print(f"evidence written: {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
