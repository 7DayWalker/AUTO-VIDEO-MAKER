"""TRACK 9 TASK C1/C2 -- CodeCraft vision ROOT-CAUSE probe.

PROBE 3 of discovery returned 500/502 for image_url content parts. That is
ambiguous: it could be (a) the provider rejecting images, (b) the 2x2 PNG being
degenerate, or (c) a transient upstream error. This script disambiguates:

  A. text-only baseline on the SAME model  -> proves credential + credits work
  B. real JPEG frame from the real source  -> proves image acceptance
  C. retry/transience check                -> distinguishes 502 noise

Run: python tools/track9_codecraft_vision_probe.py
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
EVIDENCE = ROOT / "_real_smoke_wk" / "codecraft_vision_probe_evidence.json"


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _post(base: str, key: str, payload: dict, timeout: int = 120) -> dict:
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    out = {"status": r.status_code}
    try:
        out["json"] = r.json()
    except ValueError:
        out["text"] = (r.text or "")[:600]
    return out


def _summarize(res: dict) -> tuple[str, str]:
    status = str(res.get("status") or res.get("error"))
    jb = res.get("json")
    content = ""
    if isinstance(jb, dict):
        ch = jb.get("choices") or []
        if ch and isinstance(ch[0], dict):
            content = str(((ch[0].get("message") or {}).get("content") or "")).strip()
        if not content and jb.get("error"):
            content = "ERR " + json.dumps(jb.get("error"))[:200]
    if not content:
        content = (res.get("text") or "")[:200]
    return status, content.replace("\n", " ")[:300]


def _extract_frame(ff: str, at: float, dest: Path, width: int = 640) -> Path | None:
    cmd = [
        ff, "-y", "-ss", f"{at:.2f}", "-i", str(SOURCE), "-frames:v", "1",
        "-vf", f"scale={width}:-2", "-q:v", "4", str(dest),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    if dest.is_file() and dest.stat().st_size > 2048:
        return dest
    return None


MODELS = ["claude-opus-5.5", "claude-opus-5", "gemini-3.7-flash", "gpt-5.6-sol"]


def main() -> int:
    _load_env()
    key = (os.getenv("CODECRAFT_API_KEY") or "").strip()
    base = ((os.getenv("CODECRAFT_BASE_URL") or "").strip() or "https://codecraftapi.com/v1").rstrip("/")
    if not key:
        print("no CODECRAFT_API_KEY -> STOP")
        return 1

    evidence: dict = {"base_url": base, "stages": {}}
    print("=" * 72)
    print("TRACK 9 -- CODECRAFT VISION ROOT-CAUSE PROBE")
    print("=" * 72)

    # ---- STAGE A: text-only baseline (credential + credits evidence) -------
    print("-" * 72)
    print("STAGE A  text-only baseline (same models)")
    stage_a = {}
    for mid in MODELS:
        res = _post(base, key, {
            "model": mid,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 16, "temperature": 0,
        }, timeout=90)
        status, content = _summarize(res)
        stage_a[mid] = {"status": status, "content": content,
                        "usage": (res.get("json") or {}).get("usage") if isinstance(res.get("json"), dict) else None}
        print(f"  {mid:22s} -> {status:5s} {content[:90]}")
    evidence["stages"]["text_only"] = stage_a

    # ---- STAGE B: real JPEG frame from the real source --------------------
    print("-" * 72)
    print("STAGE B  real JPEG frame from real source film")
    from app.assembly.ffmpeg import resolve_ffmpeg

    ff = resolve_ffmpeg()
    if not ff or not SOURCE.is_file():
        print("  ffmpeg or source unavailable -> cannot run stage B")
        evidence["stages"]["image_real_frame"] = {"blocker": "ffmpeg_or_source_unavailable"}
    else:
        work = Path(tempfile.mkdtemp(prefix="cc_vis_"))
        frame = _extract_frame(ff, 42.0, work / "f42.jpg")
        if not frame:
            print("  frame extraction failed -> cannot run stage B")
            evidence["stages"]["image_real_frame"] = {"blocker": "frame_extraction_failed"}
        else:
            b64 = base64.b64encode(frame.read_bytes()).decode("ascii")
            print(f"  frame: {frame.name} {frame.stat().st_size} bytes -> b64 {len(b64)} chars")
            stage_b = {"frame_bytes": frame.stat().st_size, "b64_chars": len(b64), "results": {}}
            for mid in MODELS:
                payload = {
                    "model": mid,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe in one short sentence exactly what you see in this image."},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        ],
                    }],
                    "max_tokens": 120, "temperature": 0,
                }
                res = _post(base, key, payload, timeout=150)
                status, content = _summarize(res)
                stage_b["results"][mid] = {"status": status, "content": content}
                print(f"  {mid:22s} -> {status:5s} {content[:180]}")
            evidence["stages"]["image_real_frame"] = stage_b

            # ---- STAGE C: transience retry on best candidate --------------
            print("-" * 72)
            print("STAGE C  retry check (distinguish transient 502 from hard refusal)")
            stage_c = []
            for attempt in range(3):
                res = _post(base, key, {
                    "model": "claude-opus-5.5",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "One short sentence: what is in this image?"},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        ],
                    }],
                    "max_tokens": 120, "temperature": 0,
                }, timeout=150)
                status, content = _summarize(res)
                stage_c.append({"attempt": attempt + 1, "status": status, "content": content})
                print(f"  attempt {attempt+1} -> {status:5s} {content[:160]}")
                if str(status) == "200":
                    break
                time.sleep(4)
            evidence["stages"]["retry"] = stage_c

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2)[:300000], encoding="utf-8")
    print("-" * 72)
    print(f"evidence written: {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
