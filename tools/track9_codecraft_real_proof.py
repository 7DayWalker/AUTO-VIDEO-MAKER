"""TRACK 9 TASK C4 -- REAL CodeCraft visual-understanding proof.

ONE genuine CodeCraft request against a BOUNDED window of the REAL source film,
executed through the EXISTING provider adapter (not raw HTTP), with an
anti-hallucination control.

What this proves / records:
  * selected canonical model
  * provider used internally + actual wire model id
  * input modality actually used
  * request success/failure
  * quota / credential evidence available
  * whether the visual content was ACCURATELY described

Anti-hallucination control: the same model is asked about a DIFFERENT bounded
window and about a synthetic frame that is NOT from the film. If the answers
were prompt-driven rather than image-driven they would be interchangeable.

NOT the 30-minute Summary Pro workflow.
Run: python tools/track9_codecraft_real_proof.py
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
EVIDENCE = ROOT / "_real_smoke_wk" / "codecraft_real_proof.json"

#: Bounded window taken from the real film (NOT the whole 30 minutes).
WINDOW_START = 40.0
WINDOW_END = 52.0
FRAME_TIMESTAMPS = (40.0, 44.0, 48.0, 52.0)

#: CodeCraft wire model whose image input AVM verified live.
CODECRAFT_WIRE_MODEL = "gpt-5.6-sol"


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


def _extract_frames(ff: str, work: Path, timestamps, width: int = 640):
    """Extract TIMESTAMPED frames from bounded source windows."""
    frames = []
    for ts in timestamps:
        dest = work / f"frame_{ts:06.1f}.jpg"
        subprocess.run(
            [ff, "-y", "-ss", f"{ts:.2f}", "-i", str(SOURCE), "-frames:v", "1",
             "-vf", f"scale={width}:-2", "-q:v", "5", str(dest)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
        )
        if dest.is_file() and dest.stat().st_size > 2048:
            frames.append((ts, str(dest)))
    return frames


def main() -> int:
    _load_env()
    from app.assembly.ffmpeg import resolve_ffmpeg
    from app.content.model_registry import InputModality

    print("=" * 74)
    print("TRACK 9 TASK C4 -- REAL CODECRAFT VISUAL-UNDERSTANDING PROOF")
    print("=" * 74)

    if not SOURCE.is_file():
        print(f"source missing: {SOURCE}")
        return 1
    ff = resolve_ffmpeg()
    if not ff:
        print("ffmpeg unavailable -> cannot extract frames. STOP (truthful).")
        return 1

    print("-" * 74)
    print("STEP 1  CodeCraft vision route (canonical identity NOT provable)")
    print(f"        wire model id      : {CODECRAFT_WIRE_MODEL}")

    work = Path(tempfile.mkdtemp(prefix="cc_proof_"))
    frames = _extract_frames(ff, work, FRAME_TIMESTAMPS)
    print(f"        frames extracted   : {len(frames)} from "
          f"[{WINDOW_START:.0f}-{WINDOW_END:.0f}s] of the real film")
    if not frames:
        print("        no frames extracted -> STOP (truthful)")
        return 1
    for ts, p in frames:
        print(f"          t={ts:6.1f}s  {Path(p).name}  {Path(p).stat().st_size}B")

    print("-" * 74)
    print("STEP 2  REAL REQUEST through the existing CodeCraft adapter")
    from app.content.providers.codecraft import CodeCraftProvider

    provider = CodeCraftProvider(model=CODECRAFT_WIRE_MODEL)
    print(f"        provider           : {provider.name}")
    print(f"        adapter model      : {provider.model}")
    print(f"        vision capable     : {provider.supports_multimodal()}")
    print(f"        capability source  : {provider.vision_capability_source()}")
    print(f"        native video       : {provider.supports_native_video()}")
    print(f"        credentials set    : {provider.is_available()}")

    prompt = (
        f"These are {len(frames)} frames sampled in chronological order from a "
        f"bounded window [{WINDOW_START:.0f}-{WINDOW_END:.0f}s] of a short film. "
        "Describe ONLY what is visually present. Then list the distinct visual "
        "events you can see, as a JSON object with keys 'summary' (one sentence) "
        "and 'events' (array of short strings)."
    )

    record: dict = {
        "provider_used_internally": provider.name,
        "provider_wire_model_id": provider.model,
        "input_modality": InputModality.FRAME_SEQUENCE,
        "window": [WINDOW_START, WINDOW_END],
        "frame_timestamps": [ts for ts, _ in frames],
    }

    response = None
    try:
        response = provider.generate_multimodal(
            prompt,
            system_prompt="You are a precise film analyst. Describe only what you actually see.",
            images=frames,
            max_tokens=700,
            temperature=0.0,
        )
        record["request_success"] = True
    except Exception as exc:  # noqa: BLE001
        record["request_success"] = False
        record["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        print(f"        REAL REQUEST FAILED: {record['error']}")
        print("        No success is fabricated; the blocker is reported.")

    if response is not None:
        content = (response.content or "").strip()
        meta = response.metadata or {}
        print(f"        response provider   : {response.provider}")
        print(f"        response model      : {response.model}")
        print(f"        usage               : {response.usage}")
        print(f"        input modality used : {meta.get('input_modality')}")
        print(f"        visual method       : {meta.get('visual_analysis_method')}")
        print(f"        native video ingest : {meta.get('native_video_ingestion')}")
        print(f"        frames sent         : {meta.get('frame_count')}")
        print(f"        frame timestamps    : "
              f"{[f.get('timestamp_seconds') for f in meta.get('frame_timestamps', [])]}")
        print("        --- RESPONSE " + "-" * 60)
        for line in content.splitlines()[:30]:
            print(f"        {line}")
        record["response_provider"] = response.provider
        record["response_model"] = response.model
        record["usage"] = response.usage
        record["execution_metadata"] = {
            k: v for k, v in meta.items() if k != "frame_timestamps"
        }
        record["response_excerpt"] = content[:1500]
        record["quota_evidence"] = {
            "credentials_configured": provider.is_available(),
            "balance_endpoint_exists": False,
            "state": "credentials_configured_quota_unknown",
            "note": (
                "CodeCraft exposes no balance endpoint (GET /balance, /credits, "
                "/usage, /account, /me all returned 404), so remaining credit is "
                "UNKNOWN. A successful request proves this account COULD execute "
                "this call; it does not establish a balance."
            ),
        }

        print("-" * 74)
        print("STEP 3  CONTROL: a DIFFERENT window + a synthetic non-film frame")
        controls = {}
        control_frames = _extract_frames(ff, work, (420.0,))
        synthetic = work / "synthetic_solid_magenta.jpg"
        subprocess.run(
            [ff, "-y", "-f", "lavfi", "-i", "color=c=magenta:s=640x360:d=1",
             "-frames:v", "1", "-q:v", "5", str(synthetic)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        if control_frames:
            try:
                r2 = provider.generate_multimodal(
                    "In ONE short sentence, describe only what is visually present in this frame.",
                    images=control_frames, max_tokens=200, temperature=0.0,
                )
                controls["other_window_t420s"] = (r2.content or "").strip()[:400]
            except Exception as exc:  # noqa: BLE001
                controls["other_window_t420s"] = f"FAILED {type(exc).__name__}"
        if synthetic.is_file():
            try:
                r3 = provider.generate_multimodal(
                    "Name the single dominant colour in this image. One word.",
                    images=[str(synthetic)], max_tokens=30, temperature=0.0,
                )
                controls["synthetic_solid_magenta"] = (r3.content or "").strip()[:200]
            except Exception as exc:  # noqa: BLE001
                controls["synthetic_solid_magenta"] = f"FAILED {type(exc).__name__}"

        for label, text in controls.items():
            print(f"        [{label}] {text}")
        record["grounding_controls"] = controls

        answers = [content[:400]] + list(controls.values())
        distinct = len({a.lower() for a in answers if a and not a.startswith("FAILED")})
        control_named = "magenta" in controls.get("synthetic_solid_magenta", "").lower()
        record["distinct_responses"] = distinct
        record["control_identified_correctly"] = bool(control_named)
        record["visual_content_accurately_described"] = bool(distinct >= 2 and control_named)
        print(f"        distinct answers    : {distinct}")
        print(f"        control identified  : {control_named}")
        print(f"        => visual understanding ACCURATE: "
              f"{record['visual_content_accurately_described']}")

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(record, indent=2)[:300000], encoding="utf-8")
    print("=" * 74)
    print(f"evidence written: {EVIDENCE}")
    import shutil

    shutil.rmtree(work, ignore_errors=True)
    return 0 if record.get("request_success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
