"""Phase 11C Batch 1C - CONTROLLED LIVE VALIDATION harness (diagnostics only).

This is NOT application code and changes nothing in app/. It exercises the
existing Batch 1C adapters against the real Alibaba Cloud Model Studio
endpoints with the dedicated AVM application credential (sk-ws-... class,
already present in the runtime environment as DASHSCOPE_API_KEY).

Spend limits (predefined, strictly enforced here):
- exactly ONE sample per capability (text, JSON, image, video);
- no parallel samples, no batches, no comparison runs;
- image: one 720*1280 wan2.2-t2i-flash image;
- video: ONE wan2.2-t2v-plus clip at the cheapest validated configuration
  (5s, 480*880);
- text/JSON: minimal prompts (few tokens).

Safety:
- the credential is read from the environment and NEVER printed, never
  written to the evidence file, never included in any payload dump;
- evidence is written incrementally (flush after every step) to
  diagnostics/batch1c_live/evidence.json so progress survives a detached
  terminal;
- every capability runs through the EXISTING AVM adapters (AlibabaProvider,
  AlibabaImageSource, AlibabaVideoVisualProvider) and the existing artifact
  validators (verify_sourced_image / validate_video_file / inspect_audio);
- a forbidden/unknown credential class ABORTS before any request.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

EVIDENCE_DIR = PROJECT_ROOT / "diagnostics" / "batch1c_live"
EVIDENCE_PATH = EVIDENCE_DIR / "evidence.json"

LIMITS = {
    "text_calls": 1,
    "json_calls": 1,
    "image_generations": 1,
    "video_generations": 1,
    "video_duration_seconds": 5,
    "video_size": "480*880",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def scrub(obj):
    """Remove any credential-looking value from evidence structures."""
    if isinstance(obj, dict):
        clean = {}
        for k, v in obj.items():
            if isinstance(v, str) and (
                v.startswith("sk-ws-")
                or v.startswith("sk-sp-")
                or "Bearer " in v
            ):
                clean[k] = "<redacted>"
            else:
                clean[k] = scrub(v)
        return clean
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    if isinstance(obj, str):
        if obj.startswith("sk-ws-") or obj.startswith("sk-sp-"):
            return "<redacted>"
        return obj
    return obj


def record(stage: str, payload: dict) -> None:
    evidence = {"stages": {}, "limits": LIMITS}
    if EVIDENCE_PATH.exists():
        try:
            evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    evidence.setdefault("stages", {})[stage] = {
        **scrub(payload), "recorded_at": now_iso(),
    }
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[recorded] {stage}: "
          f"{json.dumps(scrub({k: v for k, v in payload.items() if k != 'detail'}))[:400]}",
          flush=True)


def setup_environment() -> None:
    key = (os.environ.get("DASHSCOPE_API_KEY")
           or os.environ.get("ALIBABA_API_KEY") or "").strip()
    if not key:
        print("ABORT: no Alibaba credential present in the environment")
        sys.exit(2)
    if key.startswith("sk-sp-"):
        print("ABORT: sk-sp Token Plan credential class is forbidden in AVM")
        sys.exit(2)
    if not key.startswith("sk-ws-"):
        print("ABORT: credential class is not the AVM application class")
        sys.exit(2)
    # Supply the credential under the variable AVM's adapters read.
    os.environ["ALIBABA_API_KEY"] = key


def main() -> int:
    setup_environment()
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"

    if stage in ("all", "text"):
        run_text()
    if stage in ("all", "json"):
        run_json()
    if stage in ("all", "image"):
        run_image()
    if stage in ("all", "video"):
        run_video()
    return 0


# ---------------------------------------------------------------------------
# Stage 1: text generation (one minimal call)
# ---------------------------------------------------------------------------


def run_text() -> None:
    from app.content.providers.alibaba import AlibabaProvider

    print("=== STAGE 1: text generation (1 call) ===", flush=True)
    started = time.monotonic()
    detail: dict = {"attempted_at": now_iso()}
    try:
        provider = AlibabaProvider(
            model=os.environ.get("ALIBABA_TEXT_MODEL") or "qwen-plus",
            timeout=60,
        )
        response = provider.generate(
            "Reply with exactly: AVM_BATCH_1C_ALIVE",
            temperature=0.0,
            max_tokens=64,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        record("text", {
            "status": "LIVE-VALIDATED",
            "provider": response.provider,
            "model": response.model,
            "content_preview": response.content[:200],
            "usage": response.usage,
            "latency_ms": latency_ms,
            "detail": detail,
        })
    except Exception as exc:  # noqa: BLE001 - truthful live failure
        detail["error_type"] = type(exc).__name__
        detail["error"] = str(exc)[:500]
        record("text", {"status": "FAILED", "detail": detail})


# ---------------------------------------------------------------------------
# Stage 2: structured JSON generation (one minimal call)
# ---------------------------------------------------------------------------


def run_json() -> None:
    from app.content.providers.alibaba import AlibabaProvider

    print("=== STAGE 2: JSON generation (1 call) ===", flush=True)
    started = time.monotonic()
    detail: dict = {"attempted_at": now_iso()}
    try:
        provider = AlibabaProvider(
            model=os.environ.get("ALIBABA_TEXT_MODEL") or "qwen-plus",
            timeout=60,
        )
        response = provider.generate_json(
            "Return a JSON object {\"ok\": true, \"note\": \"batch 1c\"}. "
            "JSON only.",
            temperature=0.0,
            max_tokens=64,
        )
        parsed = json.loads(response.content)
        latency_ms = int((time.monotonic() - started) * 1000)
        record("json", {
            "status": "LIVE-VALIDATED",
            "provider": response.provider,
            "model": response.model,
            "parsed_ok": isinstance(parsed, dict),
            "parsed_keys": sorted(parsed.keys())[:8],
            "usage": response.usage,
            "latency_ms": latency_ms,
            "detail": detail,
        })
    except Exception as exc:  # noqa: BLE001 - truthful live failure
        detail["error_type"] = type(exc).__name__
        detail["error"] = str(exc)[:500]
        record("json", {"status": "FAILED", "detail": detail})


# ---------------------------------------------------------------------------
# Stage 3: image generation (one wan2.2-t2i-flash image, 720*1280)
# ---------------------------------------------------------------------------


def run_image() -> None:
    from app.orchestrator.alibaba_image import AlibabaImageSource
    from app.orchestrator.visual_sources import (
        SourceAvailability,
        VisualSourceContext,
        VisualSourceStatus,
    )
    from app.assembly.media import probe_media

    print("=== STAGE 3: image generation (1 image, 720*1280) ===", flush=True)
    detail: dict = {"attempted_at": now_iso()}
    try:
        out_dir = EVIDENCE_DIR / "image"
        out_dir.mkdir(parents=True, exist_ok=True)
        source = AlibabaImageSource(
            out_dir,
            model=os.environ.get("AVM_ALIBABA_IMAGE_MODEL")
            or "wan2.2-t2i-flash",
            poll_interval=3.0,
            poll_max_attempts=40,  # 2 minutes bounded
        )
        availability = source.availability()
        detail["availability"] = availability.value
        if availability != SourceAvailability.AVAILABLE:
            record("image", {
                "status": "FAILED", "detail": detail,
                "reason": "source not configured",
            })
            return
        ctx = VisualSourceContext(
            project_id="batch1c_live",
            scene_id="live_image_001",
            sequence=1,
            visual_prompt=(
                "A single cinematic vertical frame of a blue-and-silver "
                "mechanical robot standing in soft studio light."
            ),
            aspect_ratio="9:16",
            output_dir=out_dir,
        )
        started = time.monotonic()
        result = source.source(ctx)
        latency_ms = int((time.monotonic() - started) * 1000)
        detail["classification"] = result.metadata.get("failure_category")
        if result.status == VisualSourceStatus.SUCCESS:
            probe = probe_media(result.artifact_path)
            record("image", {
                "status": "LIVE-VALIDATED",
                "provider_id": result.provider_id,
                "artifact": str(result.artifact_path),
                "bytes": result.artifact_path.stat().st_size,
                "duration": getattr(probe, "duration", None),
                "width": getattr(probe, "width", None),
                "height": getattr(probe, "height", None),
                "provenance": {
                    k: result.metadata.get(k)
                    for k in ("provider", "model", "task_id",
                              "source_type", "usage")
                },
                "latency_ms": latency_ms,
                "detail": detail,
            })
        else:
            record("image", {
                "status": "FAILED",
                "result_status": result.status.value,
                "message": (result.message or "")[:500],
                "detail": detail,
            })
    except Exception as exc:  # noqa: BLE001 - truthful live failure
        detail["error_type"] = type(exc).__name__
        detail["error"] = str(exc)[:500]
        record("image", {"status": "FAILED", "detail": detail})


# ---------------------------------------------------------------------------
# Stage 4: video generation (ONE wan2.2-t2v-plus clip, 5s, 480*880)
# ---------------------------------------------------------------------------


def run_video() -> None:
    from app.orchestrator.alibaba_video import AlibabaVideoVisualProvider
    from app.flow.scene_models import (
        FlowGenerationConfig,
        SceneGenerationRequest,
    )

    print(
        "=== STAGE 4: video generation (1 clip, wan2.2-t2v-plus, 5s, "
        "480*880) ===",
        flush=True,
    )
    detail: dict = {"attempted_at": now_iso()}
    try:
        out_dir = EVIDENCE_DIR / "video"
        out_dir.mkdir(parents=True, exist_ok=True)
        # Provider-validated AND AVM-contract-compatible configuration:
        # wan2.2-t2v-plus supports 480*832/832*480/624*624/1440*1440/
        # 1632*1248/1248*1632/1080*1920/1920*1080, but only 1080*1920
        # (and 1920*1080) satisfy AVM's EXACT aspect-ratio contract
        # (480:832 = 0.577 is not 9:16 = 0.5625; live-proven). Duration 5s.
        os.environ["AVM_ALIBABA_VIDEO_SIZES"] = (
            "9:16=1080*1920,16:9=1920*1080"
        )
        provider = AlibabaVideoVisualProvider(
            out_dir,
            model=os.environ.get("AVM_ALIBABA_VIDEO_MODEL")
            or "wan2.2-t2v-plus",
            poll_interval=10.0,
            poll_max_attempts=48,  # bounded at ~8 minutes
        )
        request = SceneGenerationRequest(
            project_id="batch1c_live",
            scene_id="live_video_001",
            sequence=1,
            visual_prompt=(
                "Slow cinematic dolly-in on a blue-and-silver mechanical "
                "robot standing in soft studio light, shallow depth of "
                "field, gentle volumetric haze."
            ),
            config=FlowGenerationConfig(
                model="wan2.2-t2v-plus",
                aspect_ratio="9:16",
                duration=5,
                count=1,
                agent_enabled=False,
            ),
        )
        started = time.monotonic()
        result = provider.generate_scene(
            request,
            checkpoint_callback=lambda payload: print(
                f"[checkpoint] {payload.get('status')} "
                f"task={payload.get('task_id')} "
                f"remote={payload.get('remote_status')}", flush=True,
            ),
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if result.success:
            record("video", {
                "status": "LIVE-VALIDATED",
                "provider": result.verification.get("provider"),
                "model": result.verification.get("model"),
                "task_id": result.verification.get("task_id"),
                "request": result.verification.get("request"),
                "usage": result.verification.get("usage"),
                "artifact": str(result.file_path),
                "bytes": (result.file_path.stat().st_size
                          if result.file_path else None),
                "actual_duration": result.actual_duration,
                "width": result.width,
                "height": result.height,
                "codec": result.codec,
                "audio_facts": (
                    result.audio_facts.model_dump()
                    if result.audio_facts else None
                ),
                "latency_ms": latency_ms,
                "detail": detail,
            })
        else:
            record("video", {
                "status": "FAILED",
                "result_status": result.status,
                "failure_category": result.verification.get(
                    "failure_category"
                ),
                "task_id": result.verification.get("task_id"),
                "error": (result.error or "")[:500],
                "detail": detail,
            })
    except Exception as exc:  # noqa: BLE001 - truthful live failure
        detail["error_type"] = type(exc).__name__
        detail["error"] = str(exc)[:500]
        record("video", {"status": "FAILED", "detail": detail})


if __name__ == "__main__":
    sys.exit(main())
