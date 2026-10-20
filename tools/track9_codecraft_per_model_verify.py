"""TRACK 9 TASK C2 -- per-model CodeCraft vision verification (retry-aware).

The CodeCraft gateway intermittently returns 500 ``api_error`` on valid
payloads (proven: identical payload succeeds on retry). Capability must
therefore be established PER MODEL with retry on transient 5xx only.

Verifies, for each candidate model:
  * text-only reachability
  * real-frame image understanding (content must match the real frame)
  * native video_url temporal ingestion

Run: python tools/track9_codecraft_per_model_verify.py
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

SOURCE = ROOT / "_real_smoke_source" / "The Last Dragon Queen _ Part I - AI Short Film by ReelixBox.mp4"
EVIDENCE = ROOT / "_real_smoke_wk" / "codecraft_per_model_verify.json"

CANDIDATES = ["claude-opus-5.5", "claude-opus-5", "gpt-5.6-sol", "gemini-3.7-flash", "qwen3.8-27b"]


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


def _post(base, key, payload, timeout=180):
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
        out["text"] = (r.text or "")[:400]
    return out


def _retry(base, key, payload, attempts=4, timeout=180):
    """Retry transient 5xx only; explicit 4xx is authoritative evidence."""
    last = {}
    for i in range(attempts):
        res = _post(base, key, payload, timeout=timeout)
        last = res
        st = res.get("status")
        if st == 200 or (isinstance(st, int) and 400 <= st < 500):
            res["_attempts"] = i + 1
            return res
        time.sleep(2 + 2 * i)
    last["_attempts"] = attempts
    return last


def _content(res):
    jb = res.get("json")
    if isinstance(jb, dict):
        ch = jb.get("choices") or []
        if ch and isinstance(ch[0], dict):
            c = str(((ch[0].get("message") or {}).get("content") or "")).strip()
            if c:
                return c.replace("\n", " ")
        if jb.get("error"):
            return "ERR " + json.dumps(jb.get("error"))[:160]
    return (res.get("text") or "")[:160]


# Words that can only come from actually seeing the t=42s frame of the film.
FRAME_EXPECT = ("captive", "prisoner", "soldier", "armor", "armour", "knight",
                "mud", "people", "line", "walk", "guard", "spear", "march", "girl", "woman")


def main() -> int:
    _load_env()
    key = (os.getenv("CODECRAFT_API_KEY") or "").strip()
    base = ((os.getenv("CODECRAFT_BASE_URL") or "").strip() or "https://codecraftapi.com/v1").rstrip("/")
    if not key:
        print("no CODECRAFT_API_KEY -> STOP")
        return 1
    from app.assembly.ffmpeg import resolve_ffmpeg

    ff = resolve_ffmpeg()
    if not ff or not SOURCE.is_file():
        print("ffmpeg or source unavailable -> STOP")
        return 1

    work = Path(tempfile.mkdtemp(prefix="cc_perm_"))
    frame = work / "f42.jpg"
    subprocess.run([ff, "-y", "-ss", "42", "-i", str(SOURCE), "-frames:v", "1",
                    "-vf", "scale=640:-2", "-q:v", "4", str(frame)],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    clip = work / "c42.mp4"
    subprocess.run([ff, "-y", "-ss", "42", "-t", "4", "-i", str(SOURCE), "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "30", "-vf", "scale=320:-2", "-an", str(clip)],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    if not frame.is_file():
        print("frame extraction failed -> STOP")
        return 1
    img_b64 = base64.b64encode(frame.read_bytes()).decode("ascii")
    vid_b64 = base64.b64encode(clip.read_bytes()).decode("ascii") if clip.is_file() else None

    print("=" * 72)
    print("TRACK 9 TASK C2 -- PER-MODEL CODECRAFT CAPABILITY VERIFICATION")
    print("=" * 72)
    print(f"frame: {frame.stat().st_size}B   clip: {clip.stat().st_size if clip.is_file() else 'n/a'}B")

    evidence: dict = {"base_url": base, "models": {}}
    for mid in CANDIDATES:
        print("-" * 72)
        print(f"MODEL {mid}")
        rec: dict = {}

        # text
        res = _retry(base, key, {"model": mid, "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                                 "max_tokens": 16, "temperature": 0}, timeout=90)
        rec["text"] = {"status": str(res.get("status") or res.get("error")), "attempts": res.get("_attempts"),
                       "answer": _content(res)[:80]}
        print(f"  text      -> {rec['text']['status']:5s} (att={rec['text']['attempts']}) {rec['text']['answer'][:60]}")

        # image
        res = _retry(base, key, {
            "model": mid,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Describe in one short sentence exactly what you see in this image."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            ]}],
            "max_tokens": 120, "temperature": 0,
        })
        txt = _content(res)
        hits = [w for w in FRAME_EXPECT if w in txt.lower()]
        rec["image"] = {"status": str(res.get("status") or res.get("error")), "attempts": res.get("_attempts"),
                        "answer": txt[:240], "grounding_hits": hits,
                        "verified": str(res.get("status")) == "200" and len(hits) >= 2}
        print(f"  image     -> {rec['image']['status']:5s} (att={rec['image']['attempts']}) verified={rec['image']['verified']} hits={hits[:5]}")
        print(f"              {txt[:150]}")

        # native video
        if vid_b64:
            res = _retry(base, key, {
                "model": mid,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "Describe what happens in this video in one short sentence."},
                    {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{vid_b64}"}},
                ]}],
                "max_tokens": 120, "temperature": 0,
            })
            vtxt = _content(res)
            vhits = [w for w in FRAME_EXPECT if w in vtxt.lower()]
            rec["video"] = {"status": str(res.get("status") or res.get("error")), "attempts": res.get("_attempts"),
                            "answer": vtxt[:240], "grounding_hits": vhits,
                            "verified": str(res.get("status")) == "200" and len(vhits) >= 2}
            print(f"  video     -> {rec['video']['status']:5s} (att={rec['video']['attempts']}) verified={rec['video']['verified']} hits={vhits[:5]}")
            print(f"              {vtxt[:150]}")
        evidence["models"][mid] = rec
        time.sleep(1)

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2)[:300000], encoding="utf-8")
    print("=" * 72)
    print("SUMMARY (verified = HTTP 200 AND answer grounded in real frame)")
    for mid, rec in evidence["models"].items():
        print(f"  {mid:20s} text={rec.get('text',{}).get('status'):5s} "
              f"image_verified={rec.get('image',{}).get('verified')} "
              f"video_verified={rec.get('video',{}).get('verified')}")
    print(f"evidence written: {EVIDENCE}")
    import shutil

    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
