"""TRACK 9 STEP 10 -- bounded REAL-VIDEO routing proof.

Proves the frozen contract with ONE real multimodal request:

    canonical model selected independently
            +
    provider selected internally
            +
    provider can change without changing the canonical model

Uses a small bounded clip cut from the real source film.
NOT the 30-minute acceptance smoke.

Run: python tools/track9_routing_proof_real.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "_real_smoke_source" / "The Last Dragon Queen _ Part I - AI Short Film by ReelixBox.mp4"
CLIP_START = 30.0
CLIP_DURATION = 8.0


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


def _cut_clip(dest: Path):
    from app.assembly.ffmpeg import resolve_ffmpeg

    ff = resolve_ffmpeg()
    if not ff:
        print("  ffmpeg unavailable; cannot cut bounded clip")
        return None
    cmd = [
        ff, "-y", "-ss", f"{CLIP_START:.2f}", "-t", f"{CLIP_DURATION:.2f}",
        "-i", str(SOURCE), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-vf", "scale=512:-2", "-an", "-movflags", "+faststart", str(dest),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    if dest.is_file() and dest.stat().st_size > 1024:
        return dest


def main() -> int:
    _load_env()
    from app.content.model_registry import get_model, list_pro_model_ids
    from app.content.model_resolver import resolve_model

    print("=" * 70)
    print("TRACK 9 STEP 10 -- BOUNDED REAL-VIDEO ROUTING PROOF")
    print("=" * 70)

    if not SOURCE.is_file():
        print(f"source missing: {SOURCE}")
        return 1
    print(f"source : {SOURCE.name} ({SOURCE.stat().st_size/1e6:.1f} MB)")
    print(f"clip   : start={CLIP_START}s duration={CLIP_DURATION}s (bounded)")

    # STEP 1 -- user selects a CANONICAL MODEL (no provider knowledge)
    print("-" * 70)
    print(f"Pro-eligible canonical models: {list(list_pro_model_ids())}")
    selected_model = os.environ.get("T9_PROOF_MODEL", "qwen-vl-max")
    print(f"STEP 1  USER SELECTS CANONICAL MODEL : {selected_model}")
    model = get_model(selected_model)
    print(f"        display_name  : {model.display_name}")
    print(f"        (internal) bindings: {[b.provider for b in model.bindings]}")

    # STEP 2 -- AVM selects the PROVIDER internally
    resolved = resolve_model(selected_model, mode="exact", try_next_on_failure=True)
    print(f"STEP 2  AVM SELECTS PROVIDER         : {resolved.provider}")
    print(f"        wire model    : {resolved.provider_model_id}")
    print(f"        canonical kept: {resolved.canonical_model_id}")
    assert resolved.canonical_model_id == selected_model, "canonical identity changed!"

    provider = resolved.provider_instance
    if not provider.supports_multimodal():
        print("        provider cannot do multimodal -> abort truthfully")
        return 1

    # STEP 3 -- ONE real multimodal request on the bounded clip
    print("-" * 70)
    tmpdir = Path(tempfile.mkdtemp(prefix="t9_routing_"))
    clip = _cut_clip(tmpdir / "bounded_clip.mp4")
    if clip is None:
        return 1
    print(f"STEP 3  bounded clip: {clip.name} ({clip.stat().st_size/1024:.0f} KB)")

    prompt = (
        "Describe ONLY what is visually present in this video clip. "
        "Reply as JSON: {\"visual_summary\": \"...\", \"dominant_colors\": [\"...\"], "
        "\"people_or_characters_visible\": true/false}"
    )
    system_prompt = "You are a precise video analyst. Describe only what you actually see."

    # Execution-level SAME-MODEL provider fallback. A runtime provider failure
    # (quota/rate-limit/unavailable) may change the PROVIDER, never the MODEL.
    import app.content.provider_registry as preg
    from app.providers.fallback_policy import classify_provider_failure, is_fallback_eligible

    response = None
    execution_provider = None
    attempts = []
    for binding in model.bindings:
        try:
            inst = preg.create_provider(binding.provider, binding.provider_model_id)
        except Exception as exc:  # noqa: BLE001
            attempts.append((binding.provider, f"create failed: {type(exc).__name__}"))
            continue
        if not inst.is_available() or not inst.supports_multimodal():
            attempts.append((binding.provider, "unavailable/not multimodal"))
            continue
        print(f"        trying provider: {binding.provider} (wire={binding.provider_model_id})")
        try:
            response = inst.generate_multimodal(
                prompt,
                system_prompt=system_prompt,
                video=str(clip),
                images=None,
                max_tokens=500,
            )
            execution_provider = binding.provider
            attempts.append((binding.provider, "SUCCESS"))
            break
        except Exception as exc:  # noqa: BLE001
            category = classify_provider_failure(exc)
            eligible = is_fallback_eligible(category)
            attempts.append((binding.provider, f"{type(exc).__name__} [{category}]"))
            print(f"          failed: {type(exc).__name__} category={category} fallback_eligible={eligible}")
            if not eligible:
                break
            # SAME MODEL, next provider binding
            continue

    print("        attempt ledger (all within ONE canonical model):")
    for prov_name, outcome in attempts:
        print(f"          {prov_name:12s} -> {outcome}")

    if response is None:
        print("        REAL REQUEST FAILED on every binding of this canonical model.")
        print(f"        canonical model UNCHANGED: {selected_model} (no cross-model substitution)")
        return 1

    print(f"        response provider : {response.provider}")
    print(f"        response model    : {response.model}")
    print(f"        usage             : {response.usage}")
    print("        content:")
    for line in (response.content or "").strip().splitlines():
        print(f"          {line}")
    print(f"        EXECUTED BY PROVIDER : {execution_provider}")
    print(f"        CANONICAL MODEL      : {selected_model} (unchanged)")

    # STEP 4 -- provider may change; canonical model must NOT
    print("-" * 70)
    print("STEP 4  SAME-MODEL PROVIDER FALLBACK (first binding forced unavailable)")
    import app.content.provider_registry as preg

    first = model.bindings[0].provider
    original_factory = preg._REGISTRY[first]

    def _dead(mid=None, _orig=original_factory):
        inst = _orig(mid)
        wire = getattr(inst, "model", mid)

        class _Dead:
            name = first
            model = wire

            def is_available(self):
                return False

            def supports_multimodal(self):
                return True

            def generate(self, *a, **kw):
                raise RuntimeError("forced unavailable")

        return _Dead()

    preg.register_provider(first, _dead, allow_override=True)
    try:
        fallback = resolve_model(selected_model, mode="exact", try_next_on_failure=True)
        print(f"        provider BEFORE : {resolved.provider}")
        print(f"        provider AFTER  : {fallback.provider}")
        print(f"        canonical BEFORE: {resolved.canonical_model_id}")
        print(f"        canonical AFTER : {fallback.canonical_model_id}")
        assert fallback.canonical_model_id == selected_model, "model changed on fallback!"
        assert fallback.provider != resolved.provider, "provider did not change"
        print("        => PROVIDER CHANGED, CANONICAL MODEL UNCHANGED")
    finally:
        preg.register_provider(first, original_factory, allow_override=True)

    print("=" * 70)
    print("ROUTING PROOF: PASS")
    print(f"  canonical model selected independently : {selected_model}")
    print(f"  provider selected internally           : {resolved.provider}")
    print("  provider changed without model change  : yes")
    print("=" * 70)

    import shutil

    shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

