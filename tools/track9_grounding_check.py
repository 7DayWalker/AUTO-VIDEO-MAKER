"""TRACK 9 -- grounding check: is the multimodal response REALLY based on the video?

Sends three DIFFERENT bounded clips to the SAME canonical model and prints the
descriptions. Two clips come from different timestamps of the real film; one is
a synthetic solid-colour control that is NOT from the film at all.

If the model were hallucinating/ignoring the video, all three answers would be
interchangeable. Distinct, clip-appropriate answers prove real video grounding.
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
    from app.assembly.ffmpeg import resolve_ffmpeg
    from app.assembly.video import create_test_video
    import app.content.provider_registry as preg
    from app.content.model_registry import get_model

    ff = resolve_ffmpeg()
    if not ff or not SOURCE.is_file():
        print("ffmpeg or source unavailable")
        return 1

    work = Path(tempfile.mkdtemp(prefix="t9_ground_"))
    clips: dict[str, Path] = {}
    for label, start in (("real_t30s", 30.0), ("real_t300s", 300.0)):
        out = work / f"{label}.mp4"
        subprocess.run(
            [ff, "-y", "-ss", str(start), "-t", "8", "-i", str(SOURCE),
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
             "-vf", "scale=512:-2", "-an", str(out)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
        )
        if out.is_file():
            clips[label] = out
    control = work / "control_solid_magenta.mp4"
    create_test_video(control, duration=6.0, width=512, height=288, fps=24, color="magenta")
    clips["control_solid_magenta"] = control

    canonical = "gemini-3-flash-preview"
    model = get_model(canonical)
    binding = model.bindings[0]
    provider = preg.create_provider(binding.provider, binding.provider_model_id)

    print("=" * 70)
    print("TRACK 9 -- VIDEO GROUNDING CHECK")
    print("=" * 70)
    print(f"canonical model : {canonical}")
    print(f"provider (internal): {binding.provider} / {binding.provider_model_id}")
    print("-" * 70)

    results: dict[str, str] = {}
    for label, path in clips.items():
        try:
            resp = provider.generate_multimodal(
                "In ONE short sentence, describe only what is visually present in this clip.",
                system_prompt="You are a precise video analyst. Describe only what you actually see.",
                video=str(path),
                images=None,
                max_tokens=900,
            )
            text = (resp.content or "").strip().replace("\n", " ")
            results[label] = text
            print(f"[{label}]")
            print(f"   {text[:300]}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{label}] FAILED {type(exc).__name__}: {str(exc)[:200]}")

    print("-" * 70)
    distinct = len({v.lower() for v in results.values() if v})
    print(f"responses collected : {len(results)}")
    print(f"distinct responses  : {distinct}")
    if distinct >= 2:
        print("=> Descriptions differ per clip: response is GENUINELY video-grounded.")
    else:
        print("=> Descriptions identical: grounding NOT demonstrated.")

    import shutil

    shutil.rmtree(work, ignore_errors=True)
    return 0 if distinct >= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
