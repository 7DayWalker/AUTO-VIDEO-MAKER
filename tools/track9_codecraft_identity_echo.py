"""TRACK 9 TASK A/C -- CodeCraft MODEL IDENTITY ECHO + substitution check.

CRITICAL INTEGRITY QUESTION.

Discovery showed ``qwen3.8-27b`` is declared ``text->text`` (input_modalities
``['text']``) by CodeCraft's own /models metadata, yet it RETURNED a grounded
description of a real image. Two explanations:

  (a) the declared metadata is stale/wrong, or
  (b) the gateway SILENTLY SUBSTITUTES a different (vision) model.

Explanation (b) would be a model-identity violation and must never be
registered as a verified binding. This probe inspects the ``model`` field the
API echoes back, plus a self-identification answer, to detect substitution.

Run: python tools/track9_codecraft_identity_echo.py
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
EVIDENCE = ROOT / "_real_smoke_wk" / "codecraft_identity_echo.json"

PROBE_MODELS = ["claude-opus-5.5", "gpt-5.6-sol", "qwen3.8-27b", "gemma-2-2b"]


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


def _parts(res):
    jb = res.get("json") if isinstance(res.get("json"), dict) else {}
    echoed = jb.get("model") if isinstance(jb.get("model"), str) else None
    content = ""
    ch = jb.get("choices") or []
    if ch and isinstance(ch[0], dict):
        content = str(((ch[0].get("message") or {}).get("content") or "")).strip().replace("\n", " ")
    if not content and jb.get("error"):
        content = "ERR " + json.dumps(jb.get("error"))[:160]
    return echoed, content


def main() -> int:
    _load_env()
    key = (os.getenv("CODECRAFT_API_KEY") or "").strip()
    base = ((os.getenv("CODECRAFT_BASE_URL") or "").strip() or "https://codecraftapi.com/v1").rstrip("/")
    if not key:
        print("no CODECRAFT_API_KEY -> STOP")
        return 1

    from app.assembly.ffmpeg import resolve_ffmpeg

    ff = resolve_ffmpeg()
    work = Path(tempfile.mkdtemp(prefix="cc_echo_"))
    img_b64 = None
    if ff and SOURCE.is_file():
        frame = work / "f42.jpg"
        subprocess.run([ff, "-y", "-ss", "42", "-i", str(SOURCE), "-frames:v", "1",
                        "-vf", "scale=640:-2", "-q:v", "4", str(frame)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        if frame.is_file():
            img_b64 = base64.b64encode(frame.read_bytes()).decode("ascii")

    print("=" * 72)
    print("TRACK 9 -- CODECRAFT MODEL IDENTITY ECHO / SUBSTITUTION CHECK")
    print("=" * 72)

    evidence: dict = {"base_url": base, "models": {}}
    for mid in PROBE_MODELS:
        print("-" * 72)
        print(f"REQUESTED MODEL: {mid}")
        rec: dict = {"requested": mid}

        # 1. text -> echoed model id
        res = _retry(base, key, {
            "model": mid,
            "messages": [{"role": "user", "content": "Reply with exactly: PING"}],
            "max_tokens": 16, "temperature": 0,
        }, timeout=90)
        echoed, content = _parts(res)
        rec["text"] = {"status": str(res.get("status") or res.get("error")),
                       "echoed_model": echoed, "answer": content[:80]}
        match = (echoed == mid) if echoed else None
        print(f"  text  status={rec['text']['status']:5s} echoed_model={echoed!r} matches_request={match}")

        # 2. image -> echoed model id (the decisive case for text-only models)
        if img_b64:
            res = _retry(base, key, {
                "model": mid,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "Describe in one short sentence what you see."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                ]}],
                "max_tokens": 100, "temperature": 0,
            })
            echoed_i, content_i = _parts(res)
            rec["image"] = {"status": str(res.get("status") or res.get("error")),
                            "echoed_model": echoed_i, "answer": content_i[:200]}
            match_i = (echoed_i == mid) if echoed_i else None
            print(f"  image status={rec['image']['status']:5s} echoed_model={echoed_i!r} matches_request={match_i}")
            print(f"        {content_i[:150]}")

        # 3. self-identification (weak signal, recorded but NOT authoritative)
        res = _retry(base, key, {
            "model": mid,
            "messages": [{"role": "user",
                          "content": "Which model family and version are you? Answer in under 12 words."}],
            "max_tokens": 40, "temperature": 0,
        }, timeout=90)
        echoed_s, content_s = _parts(res)
        rec["self_report"] = {"status": str(res.get("status") or res.get("error")),
                              "echoed_model": echoed_s, "answer": content_s[:160]}
        print(f"  self  status={rec['self_report']['status']:5s} says: {content_s[:110]}")

        evidence["models"][mid] = rec
        time.sleep(1)

    print("=" * 72)
    print("IDENTITY SUMMARY")
    for mid, rec in evidence["models"].items():
        t = rec.get("text", {}).get("echoed_model")
        i = rec.get("image", {}).get("echoed_model")
        print(f"  requested={mid:18s} echo(text)={str(t):18s} echo(image)={str(i):18s}")
    print("NOTE: a declared text-only model answering an image request while")
    print("      echoing its own id means the DECLARED METADATA is unreliable")
    print("      for that model. Such a model is NOT registered as vision-verified.")

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2)[:300000], encoding="utf-8")
    print(f"evidence written: {EVIDENCE}")
    import shutil

    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
