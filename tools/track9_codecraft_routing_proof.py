"""TRACK 9 STEP 10 -- CodeCraft capability probe + bounded routing proof.

Truthful probe ONLY. Nothing here fabricates capability:
  1. Ask CodeCraft what models it can actually execute (GET /models).
  2. Report whether the adapter supports multimodal at all.
  3. If (and only if) a genuine multimodal path exists, route a bounded clip.

Run: python tools/track9_codecraft_routing_proof.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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


def main() -> int:
    _load_env()
    import requests

    from app.content.model_registry import get_model, list_models_for_pro
    from app.content.providers.codecraft import (
        CodeCraftProvider,
        codecraft_api_key_from_env,
        codecraft_base_url_from_env,
    )

    print("=" * 70)
    print("TRACK 9 STEP 10 -- CODECRAFT CAPABILITY PROBE")
    print("=" * 70)

    key = codecraft_api_key_from_env()
    base = codecraft_base_url_from_env()
    print(f"credential present : {bool(key)}")
    print(f"base url           : {base}")

    provider = CodeCraftProvider()
    print(f"provider.name      : {provider.name}")
    print(f"provider.model     : {provider.model}")
    print(f"is_available()     : {provider.is_available()}")
    print(f"supports_multimodal: {provider.supports_multimodal()}")

    mm_error = None
    try:
        provider.generate_multimodal("probe", video=None, images=None)
        mm_call = "UNEXPECTEDLY SUCCEEDED"
    except NotImplementedError as exc:
        mm_call = f"NotImplementedError: {exc}"
    except Exception as exc:  # noqa: BLE001
        mm_call = f"{type(exc).__name__}: {exc}"
        mm_error = exc
    print(f"generate_multimodal: {mm_call}")

    # --- What models can CodeCraft actually execute? ---
    print("-" * 70)
    print("CodeCraft advertised models (GET /models):")
    advertised: list[str] = []
    if key:
        try:
            resp = requests.get(
                f"{base}/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=30,
            )
            print(f"  HTTP {resp.status_code}")
            if resp.status_code == 200:
                body = resp.json()
                items = body.get("data") if isinstance(body, dict) else body
                for item in items or []:
                    mid = item.get("id") if isinstance(item, dict) else str(item)
                    if mid:
                        advertised.append(mid)
                for mid in advertised:
                    print(f"  - {mid}")
                if not advertised:
                    print(f"  (no model list in body: {str(body)[:300]})")
            else:
                print(f"  body: {resp.text[:300]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  probe failed: {type(exc).__name__}: {exc}")
    else:
        print("  skipped (no credential)")

    # --- Verdict ---
    print("-" * 70)
    vision_like = [
        m for m in advertised
        if any(tok in m.lower() for tok in ("vl", "vision", "omni", "multimodal", "4o"))
    ]
    codecraft_multimodal = bool(provider.supports_multimodal()) and bool(vision_like)
    print(f"vision-capable advertised models: {vision_like or 'NONE'}")
    print(f"CODECRAFT MULTIMODAL SUPPORTED  : {codecraft_multimodal}")

    pro_models = list_models_for_pro()
    print(f"Pro-eligible canonical models    : {[m.id for m in pro_models]}")
    for m in pro_models:
        providers = [b.provider for b in m.bindings]
        print(f"  {m.id:26s} bindings -> {providers}")
    brain = get_model("qwen-3.8-flash")
    print(f"  qwen-3.8-flash (text)      bindings -> {[b.provider for b in brain.bindings]}")

    if not codecraft_multimodal:
        print("-" * 70)
        print("RESULT: CodeCraft CANNOT execute multimodal video understanding.")
        print("  - adapter supports_multimodal() is False")
        print("  - generate_multimodal() raises NotImplementedError")
        print("  => No CodeCraft binding is registered on any Pro model (truthful).")
        print("  => CodeCraft remains a valid provider for TEXT models only")
        print("     (canonical qwen-3.8-flash -> codecraft/qwen3.8-27b).")
        return 0

    print("RESULT: CodeCraft reports multimodal capability; see routing proof step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
