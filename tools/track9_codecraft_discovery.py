"""TRACK 9 TASK C1 -- LIVE CodeCraft capability discovery.

Answers, from the REAL API only (no inference from screenshots):

1. Which model IDs CodeCraft actually offers (GET /models).
2. Which advertise image input (capability metadata, if any).
3. Whether /chat/completions accepts OpenAI-style image_url content parts.
4. Whether a balance/quota endpoint exists.

Writes a JSON evidence file; prints a human summary.
Run: python tools/track9_codecraft_discovery.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EVIDENCE = ROOT / "_real_smoke_wk" / "codecraft_capability_evidence.json"


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


def _probe(method: str, url: str, key: str, payload=None, timeout: int = 60):
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=timeout)
        else:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "method": method, "error": f"{type(exc).__name__}: {exc}"}
    out = {"url": url, "method": method, "status": r.status_code}
    try:
        out["json"] = r.json()
    except ValueError:
        out["text"] = (r.text or "")[:800]
    return out


def _extract_models(body) -> tuple[list[str], list[dict]]:
    model_ids: list[str] = []
    raw_models: list[dict] = []
    items = None
    if isinstance(body, dict):
        for key in ("data", "models", "result"):
            if isinstance(body.get(key), list):
                items = body[key]
                break
    elif isinstance(body, list):
        items = body
    for item in items or []:
        if isinstance(item, dict):
            raw_models.append(item)
            mid = item.get("id") or item.get("model") or item.get("name")
            if isinstance(mid, str):
                model_ids.append(mid)
        elif isinstance(item, str):
            model_ids.append(item)
            raw_models.append({"id": item})
    return model_ids, raw_models


# 2x2 red PNG (tiny, valid) -- deterministic local pixel data, no network.
TINY_RED_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAACZgbYnAAAAD0lEQVR4nGP8z8DAwMDAAA"
    "AMAAFvXkFzAAAAAElFTkSuQmCC"
)


def main() -> int:
    _load_env()
    key = (os.getenv("CODECRAFT_API_KEY") or "").strip()
    base = ((os.getenv("CODECRAFT_BASE_URL") or "").strip() or "https://codecraftapi.com/v1").rstrip("/")
    print("=" * 72)
    print("TRACK 9 TASK C1 -- LIVE CODECRAFT CAPABILITY DISCOVERY")
    print("=" * 72)
    print(f"base_url      : {base}")
    print(f"api_key       : {'CONFIGURED len=' + str(len(key)) if key else 'MISSING'}")
    if not key:
        print("no credential -> cannot discover. STOP (truthful).")
        return 1

    evidence: dict = {"base_url": base, "probes": []}

    # ---- 1. model list -----------------------------------------------------
    print("-" * 72)
    print("PROBE 1  GET /models")
    models_probe = _probe("GET", f"{base}/models", key)
    evidence["probes"].append({"name": "models", **models_probe})
    model_ids, raw_models = _extract_models(models_probe.get("json"))
    print(f"  status      : {models_probe.get('status') or models_probe.get('error')}")
    print(f"  models found: {len(model_ids)}")
    for m in raw_models:
        mid = m.get("id") or m.get("model") or m.get("name")
        extra = {k: v for k, v in m.items() if k not in ("id", "model", "name")}
        print(f"    - {mid}")
        if extra:
            print(f"        metadata: {json.dumps(extra)[:400]}")
    if not model_ids:
        snippet = json.dumps(models_probe.get("json"))[:600] if models_probe.get("json") is not None else (models_probe.get("text") or "")[:600]
        print(f"  raw body    : {snippet}")
    evidence["model_ids"] = model_ids
    evidence["raw_models"] = raw_models

    # ---- 2. balance / quota endpoints --------------------------------------
    print("-" * 72)
    print("PROBE 2  balance / quota endpoints")
    for path in ("/balance", "/credits", "/usage", "/account", "/me"):
        p = _probe("GET", f"{base}{path}", key, timeout=30)
        evidence["probes"].append({"name": f"balance{path}", **p})
        status = p.get("status") or p.get("error")
        snippet = json.dumps(p.get("json"))[:200] if p.get("json") is not None else (p.get("text") or "")[:200]
        print(f"  {path:10s} -> {status}  {snippet}")

    # ---- 3. vision probe (OpenAI-style image_url content parts) ------------
    print("-" * 72)
    print("PROBE 3  multimodal image_url content-part acceptance")
    vision_candidates = [m for m in model_ids if any(
        t in m.lower() for t in ("opus", "sonnet", "claude", "gpt", "gemini", "vl", "vision")
    )] or model_ids[:3]
    evidence["vision_probe_candidates"] = vision_candidates
    vision_results = {}
    for mid in vision_candidates:
        payload = {
            "model": mid,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Reply with the single dominant colour word you see."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{TINY_RED_PNG_B64}"}},
                ],
            }],
            "max_tokens": 24,
            "temperature": 0,
        }
        p = _probe("POST", f"{base}/chat/completions", key, payload=payload, timeout=90)
        evidence["probes"].append({"name": f"vision:{mid}", **p})
        status = p.get("status") or p.get("error")
        content = ""
        jb = p.get("json")
        if isinstance(jb, dict):
            ch = jb.get("choices") or []
            if ch and isinstance(ch[0], dict):
                content = str(((ch[0].get("message") or {}).get("content") or ""))[:160]
            if not content and jb.get("error"):
                content = json.dumps(jb.get("error"))[:240]
        vision_results[mid] = {"status": status, "content": content}
        print(f"  {mid:38s} -> {status}  {content}")
    evidence["vision_results"] = vision_results

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2)[:400000], encoding="utf-8")
    print("-" * 72)
    print(f"evidence written: {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
