"""TRACK 9 -- final architecture verification (offline, deterministic)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.content.capability_router import (  # noqa: E402
    select_model_auto,
    select_provider_for_model,
)
from app.content.model_registry import (  # noqa: E402
    JOB_SOURCE_VIDEO_RECAP,
    discover_models_for_job,
    job_video_understanding_method,
    list_models,
    model_drawer_projection,
)


def main() -> int:
    print("=" * 74)
    print("CANONICAL MODELS + RETAINED PROVIDER BINDINGS")
    print("=" * 74)
    for m in list_models():
        mods = ",".join(m.capabilities.supported_input_modalities())
        print(f"{m.id:26s} [{m.family}] modalities={mods}")
        for b in m.bindings:
            print(f"    {b.provider:11s} {b.provider_model_id:28s} identity={b.identity_verification}")

    print()
    print("=" * 74)
    print("JOB DISCOVERY  (capability metadata only, no name prefixes)")
    print("=" * 74)
    for m in discover_models_for_job(JOB_SOURCE_VIDEO_RECAP):
        print(f"    {m.id:26s} method={job_video_understanding_method(m)}")

    print()
    print("=" * 74)
    print("AUTO  (model selected FIRST, provider routED afterwards)")
    print("=" * 74)
    sel = select_model_auto()
    print(f"    selected canonical model : {sel.canonical_model_id}")
    print(f"    ranking basis            : {sel.ranking_basis}")
    print(f"    cost optimized           : {sel.cost_optimized}")
    route = select_provider_for_model(sel.canonical_model_id)
    print(f"    provider chosen for THAT model : {route.provider} / {route.provider_model_id}")

    print()
    print("=" * 74)
    print("MODEL DRAWER CONTRACT  (provider identity hidden)")
    print("=" * 74)
    drawer = model_drawer_projection()
    print(f"    top-level keys : {sorted(drawer.keys())}")
    print(f"    entry keys     : {sorted(drawer['models'][0].keys())}")
    blob = repr(drawer).lower()
    leaks = [p for p in ("alibaba", "openrouter", "codecraft", "gemini_api") if p in blob]
    print(f"    provider leaks : {leaks or 'NONE'}")
    for entry in drawer["models"]:
        print(f"    - {entry['id']:26s} method={entry['video_understanding_method']:14s} cost={entry['cost_estimate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
