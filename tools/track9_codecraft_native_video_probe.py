"""TRACK 9 TASK C1 -- DECISIVE CodeCraft native-video temporal discrimination.

STAGE 2 of the grounding probe showed `video_url` content parts return HTTP 200
with a plausible description. That is NOT sufficient proof of native video
ingestion: a gateway could sample a single frame and still answer plausibly.

This probe is decisive. It builds two synthetic clips that are IDENTICAL except
for TEMPORAL ORDER:

    clip A : RED -> GREEN -> BLUE
    clip B : BLUE -> GREEN -> RED

Any single-frame reader cannot report the correct order for both. Only genuine
multi-frame/temporal ingestion can. Also re-tests the solid-colour control that
previously 500'd, to separate flakiness from refusal.

Run: python tools/track9_codecraft_native_video_probe.py
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EVIDENCE = ROOT / "_real_smoke_wk" / "codecraft_native_video_evidence.json"
MODEL = "gpt-5.6-sol"


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


def _post(base: str, key: str, payload: dict, timeout: int = 180) -> dict:
    try:
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload, timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    out = {"status": r.status_code}
    try:
        out["json"] = r.json()
    except ValueError:
        out["text"] = (r.text or "")[:600]
    return out


def _content(res: dict) -> str:
    jb = res.get("json")
    if isinstance(jb, dict):
        ch = jb.get("choices") or []
        if ch and isinstance(ch[0], dict):
            c = str(((ch[0].get("message") or {}).get("content") or "")).strip()
            if c:
                return c.replace("\n", " ")
        if jb.get("error"):
            return "ERR " + json.dumps(jb.get("error"))[:200]
    return (res.get("text") or "")[:200]


def _build_ordered_clip(ff: str, dest: Path, colors: list[str]) -> Path | None:
    """Concatenate 1s solid-colour segments in the given order."""
    work = dest.parent
    parts = []
    for i, c in enumerate(colors):
        seg = work / f"{dest.stem}_seg{i}_{c}.mp4"
        subprocess.run(
            [ff, "-y", "-f", "lavfi", "-i", f"color=c={c}:s=320x240:d=1:r=10",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(seg)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        if seg.is_file():
            parts.append(seg)
    if len(parts) != len(colors):
        return None
    listing = work / f"{dest.stem}_list.txt"
    listing.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts), encoding="utf-8")
    subprocess.run(
        [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(dest)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90,
    )
    return dest if dest.is_file() and dest.stat().st_size > 2048 else None


def _post_with_retry(base: str, key: str, payload: dict, *, attempts: int = 4, timeout: int = 180) -> dict:
    """Retry ONLY transient 5xx gateway errors.

    The CodeCraft gateway intermittently returns 500 ``api_error`` /502 for
    otherwise valid requests (proven: identical payloads succeed on retry).
    Explicit 4xx (invalid request, auth, quota) is NEVER retried -- an explicit
    refusal is real evidence and must be respected, not papered over.
    """
    last = {}
    for i in range(attempts):
        res = _post(base, key, payload, timeout=timeout)
        last = res
        status = res.get("status")
        if status == 200:
            res["_attempts_used"] = i + 1
            return res
        if isinstance(status, int) and 400 <= status < 500:
            res["_attempts_used"] = i + 1
            return res  # explicit refusal -> authoritative, do not retry
        time.sleep(3 + 2 * i)
    last["_attempts_used"] = attempts
    return last


ORDER_PROMPT = (
    "This video shows solid colour screens one after another. "
    "List the colours in the exact order they appear, comma separated, nothing else."
)


def main() -> int:
    _load_env()
    key = (os.getenv("CODECRAFT_API_KEY") or "").strip()
    base = ((os.getenv("CODECRAFT_BASE_URL") or "").strip() or "https://codecraftapi.com/v1").rstrip("/")
    if not key:
        print("no CODECRAFT_API_KEY -> STOP")
        return 1

    from app.assembly.ffmpeg import resolve_ffmpeg

    ff = resolve_ffmpeg()
    if not ff:
        print("ffmpeg unavailable -> STOP")
        return 1

    work = Path(tempfile.mkdtemp(prefix="cc_native_"))
    evidence: dict = {"model": MODEL, "base_url": base, "method": "temporal_order_discrimination"}

    print("=" * 72)
    print("TRACK 9 -- CODECRAFT NATIVE VIDEO TEMPORAL DISCRIMINATION")
    print("=" * 72)

    specs = {
        "clipA_red_green_blue": ["red", "green", "blue"],
        "clipB_blue_green_red": ["blue", "green", "red"],
    }
    clips: dict[str, Path] = {}
    for label, colors in specs.items():
        c = _build_ordered_clip(ff, work / f"{label}.mp4", colors)
        if c:
            clips[label] = c
            print(f"  built {label}: {c.stat().st_size} bytes  expected order={colors}")
    if len(clips) != 2:
        print("  could not build both ordered clips -> STOP (truthful)")
        return 1

    print("-" * 72)
    print("STAGE 1  temporal order discrimination via video_url content part")
    order_results: dict = {}
    for label, path in clips.items():
        vb64 = base64.b64encode(path.read_bytes()).decode("ascii")
        res = _post_with_retry(base, key, {
            "model": MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": ORDER_PROMPT},
                {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{vb64}"}},
            ]}],
            "max_tokens": 60, "temperature": 0,
        })
        txt = _content(res)
        expected = specs[label]
        lowered = txt.lower()
        positions = [lowered.find(c) for c in expected]
        correct = all(p >= 0 for p in positions) and positions == sorted(positions)
        order_results[label] = {
            "status": str(res.get("status") or res.get("error")),
            "attempts_used": res.get("_attempts_used"),
            "answer": txt[:200], "expected": expected, "order_correct": correct,
        }
        print(f"  [{label}] status={res.get('status') or res.get('error')} attempts={res.get('_attempts_used')}")
        print(f"      expected : {expected}")
        print(f"      answer   : {txt[:160]}")
        print(f"      correct  : {correct}")
        time.sleep(2)
    evidence["temporal_order"] = order_results

    both = all(v.get("order_correct") for v in order_results.values())
    statuses_ok = all(v.get("status") == "200" for v in order_results.values())
    print("-" * 72)
    if both and statuses_ok:
        verdict = "NATIVE_VIDEO_TEMPORAL_CONFIRMED"
        print("  => Both reversed orders reported correctly.")
        print("  => TEMPORAL/multi-frame ingestion CONFIRMED (not single-frame).")
    elif statuses_ok:
        verdict = "VIDEO_ACCEPTED_BUT_TEMPORAL_UNPROVEN"
        print("  => Requests accepted but order not reliably reported.")
        print("  => Native temporal understanding NOT proven -> treat as UNKNOWN.")
    else:
        verdict = "VIDEO_PART_UNRELIABLE"
        print("  => Video content part did not reliably succeed -> treat as UNSUPPORTED/UNKNOWN.")
    evidence["verdict"] = verdict

    # ---- STAGE 2: re-test solid-colour still image (flake vs refusal) -----
    print("-" * 72)
    print("STAGE 2  solid-colour STILL image re-test (flake vs refusal)")
    still = work / "solid_red.jpg"
    subprocess.run(
        [ff, "-y", "-f", "lavfi", "-i", "color=c=red:s=320x240:d=1", "-frames:v", "1", "-q:v", "4", str(still)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )
    still_results = []
    if still.is_file():
        b64 = base64.b64encode(still.read_bytes()).decode("ascii")
        for attempt in range(3):
            res = _post(base, key, {
                "model": MODEL,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "Name the single dominant colour in this image. One word."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]}],
                "max_tokens": 16, "temperature": 0,
            }, timeout=120)
            txt = _content(res)
            still_results.append({"attempt": attempt + 1, "status": str(res.get("status") or res.get("error")), "answer": txt[:120]})
            print(f"  attempt {attempt+1} -> {res.get('status') or res.get('error')}  {txt[:100]}")
            if str(res.get("status")) == "200":
                break
            time.sleep(3)
    evidence["solid_still_retest"] = still_results

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2)[:300000], encoding="utf-8")
    print("-" * 72)
    print(f"VERDICT: {verdict}")
    print(f"evidence written: {EVIDENCE}")
    import shutil

    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
