"""TRACK 9 TASK C1/C3 -- CodeCraft grounding control + native-video probe.

Proves:
  1. GROUNDING: three different images (two real frames at different
     timestamps + one synthetic solid-colour control) produce DISTINCT,
     image-appropriate answers -> genuine visual understanding, not prompt echo.
  2. NATIVE VIDEO: whether /chat/completions accepts a video content part.
     Never inferred from a Playground upload control.

Run: python tools/track9_codecraft_grounding_probe.py
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "_real_smoke_source" / "The Last Dragon Queen _ Part I - AI Short Film by ReelixBox.mp4"
EVIDENCE = ROOT / "_real_smoke_wk" / "codecraft_grounding_evidence.json"
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


def _post(base: str, key: str, payload: dict, timeout: int = 150) -> dict:
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

    work = Path(tempfile.mkdtemp(prefix="cc_ground_"))
    evidence: dict = {"model": MODEL, "base_url": base}

    print("=" * 72)
    print("TRACK 9 -- CODECRAFT GROUNDING CONTROL + NATIVE VIDEO PROBE")
    print("=" * 72)

    # ---- build 3 images: 2 real frames + 1 synthetic control --------------
    images: dict[str, Path] = {}
    for label, ts in (("real_t42s", 42.0), ("real_t420s", 420.0)):
        dest = work / f"{label}.jpg"
        subprocess.run(
            [ff, "-y", "-ss", f"{ts:.2f}", "-i", str(SOURCE), "-frames:v", "1",
             "-vf", "scale=640:-2", "-q:v", "4", str(dest)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
        )
        if dest.is_file() and dest.stat().st_size > 2048:
            images[label] = dest
    control = work / "control_solid_magenta.jpg"
    subprocess.run(
        [ff, "-y", "-f", "lavfi", "-i", "color=c=magenta:s=640x360:d=1",
         "-frames:v", "1", "-q:v", "4", str(control)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )
    if control.is_file():
        images["control_solid_magenta"] = control

    print("-" * 72)
    print("STAGE 1  GROUNDING CONTROL (distinct images -> distinct answers?)")
    results: dict[str, str] = {}
    for label, path in images.items():
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        res = _post(base, key, {
            "model": MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "In ONE short sentence describe only what is visually present in this image."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
            "max_tokens": 120, "temperature": 0,
        })
        txt = _content(res)
        results[label] = txt
        print(f"  [{label}] status={res.get('status') or res.get('error')}")
        print(f"      {txt[:240]}")
    evidence["grounding"] = results
    distinct = len({v.lower() for v in results.values() if v and not v.startswith("ERR")})
    print(f"  distinct answers: {distinct} / {len(results)}")
    evidence["grounding_distinct"] = distinct

    # ---- STAGE 2: native video content-part probe -------------------------
    print("-" * 72)
    print("STAGE 2  NATIVE VIDEO content-part probe (documented behaviour only)")
    clip = work / "clip.mp4"
    subprocess.run(
        [ff, "-y", "-ss", "42", "-t", "4", "-i", str(SOURCE), "-c:v", "libx264",
         "-preset", "ultrafast", "-crf", "30", "-vf", "scale=320:-2", "-an", str(clip)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
    )
    video_probe: dict = {}
    if clip.is_file() and clip.stat().st_size > 2048:
        vb64 = base64.b64encode(clip.read_bytes()).decode("ascii")
        print(f"  clip: {clip.stat().st_size} bytes -> b64 {len(vb64)} chars")
        variants = {
            "video_url_part": {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{vb64}"}},
            "input_video_part": {"type": "input_video", "video": {"data": vb64, "format": "mp4"}},
            "image_url_with_mp4": {"type": "image_url", "image_url": {"url": f"data:video/mp4;base64,{vb64}"}},
        }
        for vname, part in variants.items():
            res = _post(base, key, {
                "model": MODEL,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "Describe what happens in this video in one sentence."}, part,
                ]}],
                "max_tokens": 120, "temperature": 0,
            })
            txt = _content(res)
            video_probe[vname] = {"status": str(res.get("status") or res.get("error")), "content": txt[:300]}
            print(f"  {vname:22s} -> {res.get('status') or res.get('error')}  {txt[:180]}")
    else:
        video_probe["blocker"] = "clip_extraction_failed"
    evidence["native_video_probe"] = video_probe

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2)[:300000], encoding="utf-8")
    print("-" * 72)
    print(f"evidence written: {EVIDENCE}")
    import shutil

    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
