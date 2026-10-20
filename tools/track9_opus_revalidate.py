"""TRACK 9 STEP 1 -- live revalidation of the EXACT CodeCraft model.

Confirms, against the LIVE API (not a screenshot, not a cached file):

  1. ``claude-opus-5.5`` is still present in GET /v1/models.
  2. Its declared input modalities / capabilities (image? video?).
  3. The vision request is still reproducible through the EXISTING adapter
     using a real frame from the real source film.
  4. Native video remains UNESTABLISHED (no declared video modality).

Exit 0 only when the model is listed AND the adapter vision request succeeds
AND the answer is grounded in the real frame.

Run: python tools/track9_opus_revalidate.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "_real_smoke_source" / "The Last Dragon Queen _ Part I - AI Short Film by ReelixBox.mp4"
EVIDENCE = ROOT / "_real_smoke_wk" / "opus55_revalidation.json"
TARGET_MODEL = "claude-opus-5.5"

#: Tokens only obtainable by actually seeing the t=44s frame.
GROUNDING_TOKENS = ("captive", "prisoner", "soldier", "armor", "armour", "guard",
                    "spear", "mud", "line", "walk", "march", "girl", "woman", "ragged")


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


def main() -> int:
    _load_env()
    from app.content.providers.codecraft import CodeCraftProvider

    print("=" * 74)
    print("TRACK 9 STEP 1 -- LIVE REVALIDATION OF claude-opus-5.5")
    print("=" * 74)

    provider = CodeCraftProvider(model=TARGET_MODEL)
    evidence: dict = {"target_model": TARGET_MODEL, "base_url": provider._base_url}

    if not provider.is_available():
        print("CODECRAFT_API_KEY not configured -> cannot revalidate. STOP.")
        return 1

    print("-" * 74)
    print("CHECK 1  live GET /models contains the exact model id")
    try:
        catalogue = provider.list_models()
    except Exception as exc:  # noqa: BLE001
        print(f"  model list FAILED: {type(exc).__name__}: {exc}")
        evidence["catalogue_error"] = f"{type(exc).__name__}: {exc}"
        _write(evidence)
        return 1

    entry = next((m for m in catalogue if m.get("id") == TARGET_MODEL), None)
    evidence["catalogue_size"] = len(catalogue)
    evidence["model_listed"] = entry is not None
    print(f"  models in catalogue   : {len(catalogue)}")
    print(f"  '{TARGET_MODEL}' listed : {entry is not None}")
    if entry is None:
        print("  EXACT MODEL ABSENT from the live catalogue.")
        print("  => Do NOT register the binding. Reporting the discrepancy.")
        _write(evidence)
        return 1

    declares_image = CodeCraftProvider.model_declares_image_input(entry)
    declares_video = CodeCraftProvider.model_declares_native_video(entry)
    metadata_present = CodeCraftProvider.model_capability_metadata_present(entry)
    arch = entry.get("architecture") or {}
    print(f"  entry keys            : {sorted(entry.keys())}")
    print(f"  owned_by              : {entry.get('owned_by')}")
    print(f"  capability metadata   : {'PRESENT' if metadata_present else 'ABSENT (bare listing)'}")
    print(f"  modality              : {arch.get('modality')}")
    print(f"  input_modalities      : {arch.get('input_modalities')}")
    print(f"  capabilities          : {entry.get('capabilities')}")
    print(f"  context_length        : {entry.get('context_length')}")
    print(f"  declares IMAGE        : {declares_image}")
    print(f"  declares NATIVE VIDEO : {declares_video}")
    evidence["declared"] = {
        "entry_keys": sorted(entry.keys()),
        "owned_by": entry.get("owned_by"),
        "capability_metadata_present": metadata_present,
        "modality": arch.get("modality"),
        "input_modalities": arch.get("input_modalities"),
        "capabilities": entry.get("capabilities"),
        "context_length": entry.get("context_length"),
        "declares_image_input": declares_image,
        "declares_native_video": declares_video,
    }

    if not metadata_present:
        # TRUTHFUL tri-state: the catalogue now returns a BARE listing with no
        # capability fields at all. "No declared image input" here means
        # UNKNOWN, not text-only. The authoritative evidence in this case is a
        # real runtime request, which CHECK 2 performs.
        print("  NOTE: catalogue returned a BARE listing (no capability fields).")
        print("        Declared-capability check is INCONCLUSIVE, not negative.")
        print("        Falling through to RUNTIME capability verification.")
    elif not declares_image:
        print("  model explicitly declares NO image input -> do NOT register as vision.")
        _write(evidence)
        return 1
    return _check_vision(provider, evidence)



def _write(evidence: dict) -> None:
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2)[:200000], encoding="utf-8")


def _check_vision(provider, evidence: dict) -> int:
    from app.assembly.ffmpeg import resolve_ffmpeg

    print("-" * 74)
    print("CHECK 2  vision request reproducible through the EXISTING adapter")
    print(f"  adapter supports_multimodal   : {provider.supports_multimodal()}")
    print(f"  adapter vision source         : {provider.vision_capability_source()}")
    print(f"  adapter supports_native_video : {provider.supports_native_video()}")
    evidence["adapter"] = {
        "supports_multimodal": provider.supports_multimodal(),
        "vision_capability_source": provider.vision_capability_source(),
        "supports_native_video": provider.supports_native_video(),
    }

    ff = resolve_ffmpeg()
    if not ff or not SOURCE.is_file():
        print("  ffmpeg or source unavailable -> cannot reproduce. UNRESOLVED.")
        evidence["vision_reproducible"] = None
        evidence["blocker"] = "ffmpeg_or_source_unavailable"
        _write(evidence)
        return 1

    work = Path(tempfile.mkdtemp(prefix="opus55_"))
    frame = work / "f44.jpg"
    subprocess.run(
        [ff, "-y", "-ss", "44", "-i", str(SOURCE), "-frames:v", "1",
         "-vf", "scale=640:-2", "-q:v", "5", str(frame)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
    )
    if not frame.is_file():
        print("  frame extraction failed -> UNRESOLVED.")
        evidence["vision_reproducible"] = None
        _write(evidence)
        return 1
    print(f"  real frame t=44.0s  : {frame.stat().st_size} bytes")

    try:
        resp = provider.generate_multimodal(
            "Describe in one short sentence exactly what you see in this image.",
            images=[(44.0, str(frame))], max_tokens=400, temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  VISION REQUEST FAILED: {type(exc).__name__}: {str(exc)[:200]}")
        evidence["vision_reproducible"] = False
        evidence["vision_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        _write(evidence)
        return 1

    text = (resp.content or "").strip()
    meta = resp.metadata or {}
    hits = [t for t in GROUNDING_TOKENS if t in text.lower()]
    grounded = len(hits) >= 2
    print(f"  response model      : {resp.model}")
    print(f"  provider reported   : {meta.get('provider_reported_model')}")
    print(f"  input modality      : {meta.get('input_modality')}")
    print(f"  visual method       : {meta.get('visual_analysis_method')}")
    print(f"  native video ingest : {meta.get('native_video_ingestion')}")
    print(f"  usage               : {resp.usage}")
    print(f"  answer              : {text[:220]}")
    print(f"  grounding hits      : {hits}")
    print(f"  GROUNDED            : {grounded}")

    evidence["vision_reproducible"] = True
    evidence["vision_result"] = {
        "response_model": resp.model,
        "provider_reported_model": meta.get("provider_reported_model"),
        "input_modality": meta.get("input_modality"),
        "visual_analysis_method": meta.get("visual_analysis_method"),
        "native_video_ingestion": meta.get("native_video_ingestion"),
        "usage": resp.usage,
        "answer": text[:600],
        "grounding_hits": hits,
        "grounded": grounded,
    }
    _write(evidence)

    print("=" * 74)
    verdict = bool(evidence["model_listed"] and grounded)
    print(f"REVALIDATION: {'PASS' if verdict else 'FAIL'}")
    print(f"  model listed live     : {evidence['model_listed']}")
    print(f"  image input verified  : {grounded}")
    print("  native video          : NOT ESTABLISHED (no declared video modality)")
    print(f"evidence written: {EVIDENCE}")
    import shutil

    shutil.rmtree(work, ignore_errors=True)
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
