"""TRACK 9 -- SINGLE-WINDOW CodeCraft Summary Pro end-to-end proof.

Runs the REAL, non-mock Summary Pro planning path over EXACTLY ONE bounded
window of the real source film, using the verified CodeCraft frame-based route.

HOW ONE WINDOW IS ISOLATED WITHOUT CHANGING PRODUCTION BEHAVIOUR
----------------------------------------------------------------
``build_pro_multimodal_plan`` derives its windows purely from
``source_asset.duration``::

    n = max(1, min(ceil(dur/120.0), 64));  step = dur/n

For the full film (dur=1805.152608) that is n=16, step=112.822038, so

    window 0 = [0.000000, 112.822038]

Passing a SourceAsset whose ``duration`` is exactly that first boundary
(112.822038) yields n=1, step=112.822038 -> window 0 = [0.000000, 112.822038],
i.e. BIT-IDENTICAL boundaries to window 0 of the full run. The MP4 on disk is
the untouched full film; only the declared duration is window-scoped, and
ffmpeg is told to read [0, 112.822038]. No production function is modified and
no parallel implementation is introduced.

Instrumentation wraps ``requests.post`` to count real requests, 5xx retries,
latency and provider-returned usage. Nothing about routing is faked.

Run: python tools/track9_single_window_pro_proof.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "_real_smoke_source" / "The Last Dragon Queen _ Part I - AI Short Film by ReelixBox.mp4"
WORKROOT = ROOT / "_real_smoke_wk"
CACHED_PID = "prd_20261007_pro_real_001"
EVIDENCE = WORKROOT / "single_window_pro_proof.json"

CANONICAL_MODEL = "claude-opus-5.5"
#: First bounded window of the FULL source, from the existing windowing math.
FULL_SOURCE_DURATION = 1805.152608
WINDOW_END = 112.822038
#: Proportional target so validate_narration_artifact's plausibility check holds.
TARGET_DURATION = 20.0


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


def _write(evidence: dict) -> None:
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2, default=str)[:400000], encoding="utf-8")


class RequestMeter:
    """Wraps requests.post to measure REAL provider traffic. Alters nothing."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._orig = None

    def install(self):
        import app.content.providers.codecraft as cc

        self._orig = cc.requests.post

        def _instrumented(url, **kwargs):
            payload = kwargs.get("json") or {}
            messages = payload.get("messages") or []
            last = messages[-1] if messages else {}
            content = last.get("content")
            image_parts = 0
            if isinstance(content, list):
                image_parts = sum(1 for p in content if p.get("type") == "image_url")
            started = time.perf_counter()
            response = self._orig(url, **kwargs)
            elapsed = time.perf_counter() - started
            record = {
                "model_requested": payload.get("model"),
                "max_tokens": payload.get("max_tokens"),
                "image_parts": image_parts,
                "status": response.status_code,
                "elapsed_s": round(elapsed, 3),
            }
            try:
                body = response.json()
                record["model_echoed"] = body.get("model")
                record["usage"] = body.get("usage")
                choices = body.get("choices") or []
                if choices and isinstance(choices[0], dict):
                    record["finish_reason"] = choices[0].get("finish_reason")
                if body.get("error"):
                    record["error"] = json.dumps(body["error"])[:200]
            except Exception:
                pass
            self.calls.append(record)
            return response

        cc.requests.post = _instrumented
        return self

    def uninstall(self):
        if self._orig is not None:
            import app.content.providers.codecraft as cc

            cc.requests.post = self._orig

    # ---- derived metrics ------------------------------------------------
    @property
    def total(self) -> int:
        return len(self.calls)

    @property
    def ok(self) -> int:
        return sum(1 for c in self.calls if c["status"] == 200)

    @property
    def server_errors(self) -> int:
        return sum(1 for c in self.calls if 500 <= int(c["status"]) < 600)

    @property
    def vision_calls(self) -> int:
        return sum(1 for c in self.calls if c["image_parts"] > 0)

    def usage_totals(self) -> dict:
        totals: dict[str, int] = {}
        for c in self.calls:
            usage = c.get("usage")
            if not isinstance(usage, dict):
                continue

            def walk(node, prefix=""):
                for k, v in node.items():
                    name = f"{prefix}_{k}" if prefix else k
                    if isinstance(v, bool):
                        continue
                    if isinstance(v, int):
                        totals[name] = totals.get(name, 0) + v
                    elif isinstance(v, dict):
                        walk(v, name)

            walk(usage)
        return totals


class FrameSpy:
    """Captures the frames the EXISTING pipeline extracts, for grounding checks."""

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self._orig = None

    def install(self):
        import app.production.narration_pro as pro

        self._orig = pro._extract_window_frames

        def _spy(src, ws, we, out_dir, window_idx):
            result = self._orig(src, ws, we, out_dir, window_idx)
            for ts, path in result:
                p = Path(path)
                record = {
                    "window_idx": window_idx,
                    "window_start_s": round(float(ws), 3),
                    "window_end_s": round(float(we), 3),
                    "timestamp_s": ts,
                    "bytes": p.stat().st_size if p.is_file() else None,
                    "filename": p.name,
                }
                try:
                    # Use the project's EXISTING ffprobe helper -- Pillow is not
                    # a repository dependency and must not be introduced here.
                    from app.assembly.ffmpeg import probe_media

                    info = probe_media(p)
                    record["width"] = info.width
                    record["height"] = info.height
                except Exception:
                    record["width"] = record["height"] = None
                # Keep a copy so grounding can be re-inspected after cleanup.
                try:
                    keep = WORKROOT / "single_window_frames"
                    keep.mkdir(parents=True, exist_ok=True)
                    (keep / p.name).write_bytes(p.read_bytes())
                    record["kept_at"] = str(keep / p.name)
                except Exception:
                    pass
                self.frames.append(record)
            return result

        pro._extract_window_frames = _spy
        return self

    def uninstall(self):
        if self._orig is not None:
            import app.production.narration_pro as pro

            pro._extract_window_frames = self._orig


def preflight(evidence: dict) -> bool:
    """STEP 1 -- source, cached transcript, model eligibility, binding."""
    import hashlib

    from app.content.capability_router import assess_model_for_job
    from app.content.model_registry import (
        JOB_SOURCE_VIDEO_RECAP,
        IdentityVerification,
        InputModality,
        discover_models_for_job,
        get_model,
    )
    from app.content.providers.codecraft import CodeCraftProvider

    print("=" * 76)
    print("STEP 1  PRE-FLIGHT")
    print("=" * 76)

    if not SOURCE.is_file():
        print(f"  source MISSING: {SOURCE}")
        return False
    size = SOURCE.stat().st_size
    print(f"  source            : {SOURCE.name}")
    print(f"  size              : {size} bytes ({size/1e6:.1f} MB)  readable=True")

    cached = WORKROOT / CACHED_PID
    tpath = cached / "source" / "src-001.transcript.json"
    spath = cached / "source" / "src-001.mp4"
    if not tpath.is_file() or not spath.is_file():
        print("  cached transcript/source MISSING -> cannot reuse")
        return False

    def sha(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    real_hash, stored_hash = sha(SOURCE), sha(spath)
    print(f"  real sha256       : {real_hash[:32]}...")
    print(f"  cached sha256     : {stored_hash[:32]}...")
    print(f"  cache matches src : {real_hash == stored_hash}")
    if real_hash != stored_hash:
        print("  cached media differs from the real source -> refusing to reuse")
        return False

    traw = json.loads(tpath.read_text(encoding="utf-8"))
    segs = traw.get("segments") or []
    print(f"  transcript reused : {len(segs)} segments, provider="
          f"{traw.get('provider')} {traw.get('provider_version')}")
    print(f"  transcript covers : 0.0 -> {segs[-1]['end'] if segs else 0}s (NOT retranscribed)")
    evidence["preflight"] = {
        "source_file": str(SOURCE),
        "source_bytes": size,
        "source_sha256": real_hash,
        "cached_media_matches": True,
        "transcript_reused": True,
        "transcript_path": str(tpath),
        "transcript_segments": len(segs),
        "transcript_provider": f"{traw.get('provider')} {traw.get('provider_version')}",
        "retranscribed": False,
    }

    model = get_model(CANONICAL_MODEL)
    eligible_ids = [m.id for m in discover_models_for_job(JOB_SOURCE_VIDEO_RECAP)]
    binding = model.bindings[0]
    assessment = assess_model_for_job(CANONICAL_MODEL, JOB_SOURCE_VIDEO_RECAP)
    print(f"  canonical model   : {model.id} ({model.display_name})")
    print(f"  pro-eligible      : {CANONICAL_MODEL in eligible_ids}")
    print(f"  method            : {assessment.video_understanding_method}")
    print(f"  binding (internal): {binding.provider} / {binding.provider_model_id}")
    print(f"  identity evidence : {binding.identity_verification} (NOT independently attested)")

    provider = CodeCraftProvider(model=binding.provider_model_id)
    try:
        catalogue = provider.list_models()
        listed = any(m.get("id") == binding.provider_model_id for m in catalogue)
    except Exception as exc:  # noqa: BLE001
        print(f"  live catalogue    : FAILED {type(exc).__name__}")
        return False
    print(f"  live catalogue    : {len(catalogue)} models; wire id listed={listed}")
    print("  quota/balance     : UNKNOWN (no balance endpoint; credits not verifiable)")

    evidence["preflight"].update({
        "canonical_model": model.id,
        "display_name": model.display_name,
        "pro_eligible": CANONICAL_MODEL in eligible_ids,
        "video_understanding_method": assessment.video_understanding_method,
        "binding_provider": binding.provider,
        "binding_wire_model_id": binding.provider_model_id,
        "identity_verification": binding.identity_verification,
        "wire_id_in_live_catalogue": listed,
        "balance_state": "unknown_no_balance_endpoint",
    })
    ok = (
        assessment.video_understanding_method == InputModality.FRAME_SEQUENCE
        and binding.identity_verification == IdentityVerification.DECLARED
        and listed
        and CANONICAL_MODEL in eligible_ids
    )
    print(f"  PRE-FLIGHT        : {'OK' if ok else 'FAILED'}")
    return ok


def build_window_scoped_inputs():
    """Window-scoped SourceAsset + the REAL cached transcript.

    The asset's declared ``duration`` is the FIRST window boundary of the full
    source, which makes the existing windowing math emit exactly one window
    with bit-identical bounds. The media file itself is the untouched full film.
    """
    from app.production.models import SourceAsset, SourceTranscript

    cached = WORKROOT / CACHED_PID
    prod = json.loads((cached / "production.json").read_text(encoding="utf-8"))
    original = (prod.get("source_assets") or [])[0]

    asset = SourceAsset(
        asset_id=original["asset_id"],
        original_filename=original["original_filename"],
        stored_path=original["stored_path"],
        sha256=original["sha256"],
        # WINDOW-SCOPED: first bounded window only.
        duration=WINDOW_END,
        width=original.get("width"),
        height=original.get("height"),
        video_codec=original.get("video_codec"),
        audio_codec=original.get("audio_codec"),
        mime=original.get("mime"),
        file_size=original.get("file_size"),
        ingested_at=original["ingested_at"],
    )
    transcript = SourceTranscript.model_validate(
        json.loads((cached / "source" / "src-001.transcript.json").read_text(encoding="utf-8"))
    )
    return asset, transcript, original


def run_window(evidence: dict):
    """STEP 2+3 -- real, non-mock Summary Pro planning over ONE window."""
    from app.production.narration import build_grounded_narration_plan

    print()
    print("=" * 76)
    print("STEP 2/3  REAL SUMMARY PRO PLANNING -- ONE BOUNDED WINDOW")
    print("=" * 76)

    asset, transcript, original = build_window_scoped_inputs()
    print(f"  full source duration : {original['duration']:.6f}s (16 windows)")
    print(f"  window 0 boundary    : [0.000000, {WINDOW_END:.6f}]")
    print(f"  window-scoped asset  : duration={asset.duration:.6f}s -> exactly 1 window")
    print(f"  remaining source     : NOT processed")
    print(f"  selection mode       : explicit")
    print(f"  requested model      : {CANONICAL_MODEL}")
    print(f"  target duration      : {TARGET_DURATION}s (window-proportional)")

    in_window = [s for s in transcript.segments if s.start < WINDOW_END and s.end > 0]
    print(f"  transcript in window : {len(in_window)} segments")
    evidence["window"] = {
        "full_source_duration_s": original["duration"],
        "window_index": 0,
        "window_start_s": 0.0,
        "window_end_s": WINDOW_END,
        "windows_processed": 1,
        "windows_in_full_source": 16,
        "remaining_source_processed": False,
        "transcript_segments_in_window": len(in_window),
        "selection_mode": "explicit",
        "requested_canonical_model": CANONICAL_MODEL,
        "target_duration_seconds": TARGET_DURATION,
    }

    meter = RequestMeter().install()
    spy = FrameSpy().install()
    started = time.perf_counter()
    artifact = None
    error = None
    try:
        artifact = build_grounded_narration_plan(
            CACHED_PID,
            "unit-single-window",
            asset,
            transcript,
            target_duration_seconds=TARGET_DURATION,
            summary_understanding_model_id=CANONICAL_MODEL,
            summary_understanding_mode="explicit",
            summary_mode="pro",
            mock_mode=False,            # REAL path
            llm_provider_override=None,  # no injected provider
            root=WORKROOT,
        )
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {str(exc)[:400]}"
        print(f"  EXECUTION FAILED : {error}")
    finally:
        elapsed = time.perf_counter() - started
        meter.uninstall()
        spy.uninstall()

    print(f"  elapsed              : {elapsed:.1f}s")
    evidence["execution"] = {
        "elapsed_seconds": round(elapsed, 2),
        "succeeded": artifact is not None,
        "error": error,
        "mock_mode": False,
        "provider_override": None,
    }
    return artifact, meter, spy, elapsed, asset, transcript


def report_measurements(evidence: dict, meter: RequestMeter, spy: FrameSpy) -> None:
    """STEP 4 -- actual execution measurements (provider-returned vs derived)."""
    print()
    print("=" * 76)
    print("STEP 4  MEASURED EXECUTION")
    print("=" * 76)
    print(f"  provider requests (total) : {meter.total}")
    print(f"  ... HTTP 200              : {meter.ok}")
    print(f"  ... HTTP 5xx (transient)  : {meter.server_errors}")
    print(f"  ... carrying image parts  : {meter.vision_calls}")
    print(f"  frames extracted          : {len(spy.frames)}")
    for f in spy.frames:
        print(f"      t={f['timestamp_s']:>8.2f}s  {f['width']}x{f['height']}  "
              f"{f['bytes']} B  window=[{f['window_start_s']}, {f['window_end_s']}]")
    print("  --- per request ---")
    for i, c in enumerate(meter.calls, 1):
        print(f"   {i:>2}. status={c['status']} elapsed={c['elapsed_s']}s "
              f"images={c['image_parts']} max_tokens={c['max_tokens']} "
              f"finish={c.get('finish_reason')} requested={c['model_requested']} "
              f"echoed={c.get('model_echoed')}")
    totals = meter.usage_totals()
    print("  --- usage (AS RETURNED BY PROVIDER, summed) ---")
    for k, v in sorted(totals.items()):
        print(f"      {k:<48} {v}")
    print("  cost / remaining balance  : CANNOT BE VERIFIED")
    print("      (no pricing in the live catalogue; no balance endpoint)")

    evidence["measurements"] = {
        "provider_requests_total": meter.total,
        "provider_requests_http_200": meter.ok,
        "provider_requests_http_5xx": meter.server_errors,
        "retries_due_to_5xx": meter.server_errors,
        "requests_with_image_parts": meter.vision_calls,
        "frames_submitted": len(spy.frames),
        "frames": spy.frames,
        "per_request": meter.calls,
        "usage_returned_by_provider": totals,
        "usage_source": "provider_returned",
        "cost_verifiable": False,
        "balance_verifiable": False,
        "cost_note": (
            "CodeCraft's live /models no longer publishes pricing and exposes no "
            "balance endpoint; neither cost nor remaining credit can be verified."
        ),
    }


def validate_grounding(evidence: dict, artifact, spy: FrameSpy, transcript) -> bool:
    """STEP 5 -- is the analysis really grounded in the submitted frames?"""
    print()
    print("=" * 76)
    print("STEP 5  VISUAL GROUNDING VALIDATION")
    print("=" * 76)

    windows = list(getattr(artifact, "_pro_window_summaries", []) or [])
    method = getattr(artifact, "_pro_visual_method", None)
    print(f"  windows recorded      : {len(windows)}")
    print(f"  visual method         : {method}")
    if not windows:
        print("  no window evidence -> grounding NOT demonstrated")
        evidence["grounding"] = {"demonstrated": False, "reason": "no window evidence"}
        return False

    w = windows[0]
    analysis = str(w.get("visual_evidence") or "")
    print(f"  window                : [{w['start_s']}, {w['end_s']}]")
    print(f"  frames in record      : {w.get('frame_count')} @ {w.get('frame_timestamps')}")
    print("  --- AI VISUAL ANALYSIS (verbatim) ---")
    for line in analysis.splitlines()[:40]:
        print(f"    {line}")

    # Transcript for the same interval -- used to show the analysis is NOT
    # merely a restatement of dialogue.
    seg_texts = [s.text for s in transcript.segments
                 if s.start < float(w["end_s"]) and s.end > float(w["start_s"])]
    transcript_text = " ".join(seg_texts).lower()
    print("  --- TRANSCRIPT for the same interval ---")
    print(f"    {transcript_text[:400]}")

    lowered = analysis.lower()
    # Visual-only vocabulary: things you can ONLY know by seeing the frames.
    # None of these appear in the window's dialogue.
    visual_terms = [
        "mud", "muddy", "armor", "armour", "soldier", "guard", "spear", "captive",
        "prisoner", "ragged", "tattered", "sunset", "dusk", "mountain", "rocky",
        "ash", "tent", "dirt", "cloak", "helmet", "chain", "bound", "wagon",
        "crowd", "line", "walking", "marching", "fire", "smoke", "sky", "dragon",
    ]
    visual_hits = sorted({t for t in visual_terms if t in lowered})
    # Any visual hit that is NOT present in the dialogue is positive evidence.
    not_in_transcript = sorted({t for t in visual_hits if t not in transcript_text})
    prompt_echo = analysis.strip().lower().startswith("analyze window")

    print(f"  visual terms found    : {visual_hits}")
    print(f"  ... absent from dialogue: {not_in_transcript}")
    print(f"  prompt echo           : {prompt_echo}")

    demonstrated = (
        len(analysis.strip()) > 80
        and len(not_in_transcript) >= 2
        and not prompt_echo
        and method == "frame_based_visual_analysis"
    )
    print(f"  GROUNDING DEMONSTRATED: {demonstrated}")

    evidence["grounding"] = {
        "demonstrated": demonstrated,
        "visual_method": method,
        "window": {"start_s": w["start_s"], "end_s": w["end_s"]},
        "frame_count": w.get("frame_count"),
        "frame_timestamps": w.get("frame_timestamps"),
        "ai_analysis": analysis,
        "transcript_interval_text": transcript_text[:1200],
        "visual_terms_found": visual_hits,
        "visual_terms_absent_from_dialogue": not_in_transcript,
        "prompt_echo": prompt_echo,
    }
    return demonstrated


def validate_contract(evidence: dict, artifact, asset, transcript) -> bool:
    """STEP 6 -- existing Summary Pro / narration contracts preserved."""
    from app.production.models import NarrationArtifact
    from app.production.narration import validate_narration_artifact
    from app.production.storage import load_narration_artifact, save_narration_artifact

    print()
    print("=" * 76)
    print("STEP 6  OUTPUT CONTRACT VALIDATION")
    print("=" * 76)
    print(f"  type                  : {type(artifact).__name__}")
    print(f"  is NarrationArtifact  : {isinstance(artifact, NarrationArtifact)}")
    print(f"  narration_id          : {artifact.narration_id}")
    print(f"  status                : {artifact.status}")
    print(f"  beats                 : {len(artifact.beats)}")
    print(f"  source_refs           : {len(artifact.source_refs)}")
    print(f"  transcript_refs       : {len(artifact.transcript_refs)}")
    print(f"  summary               : {artifact.summary[:160]}")

    checks = {"is_narration_artifact": isinstance(artifact, NarrationArtifact)}

    try:
        validate_narration_artifact(artifact, source_asset=asset, source_transcript=transcript)
        checks["existing_validator"] = True
        print("  existing validator    : PASS")
    except Exception as exc:  # noqa: BLE001
        checks["existing_validator"] = False
        checks["validator_error"] = f"{type(exc).__name__}: {exc}"
        print(f"  existing validator    : FAIL {exc}")

    try:
        save_narration_artifact(
            artifact,
            production_id=artifact.production_id,
            unit_id=artifact.unit_id,
            root=WORKROOT,
            source_asset=asset,
            source_transcript=transcript,
        )
        reloaded = load_narration_artifact(artifact.production_id, artifact.unit_id, root=WORKROOT)
        checks["persistence_roundtrip"] = (
            reloaded is not None and reloaded.narration_id == artifact.narration_id
        )
        print(f"  persistence roundtrip : {'PASS' if checks['persistence_roundtrip'] else 'FAIL'}")
    except Exception as exc:  # noqa: BLE001
        checks["persistence_roundtrip"] = False
        checks["persistence_error"] = f"{type(exc).__name__}: {exc}"
        print(f"  persistence roundtrip : FAIL {exc}")

    refs_in_window = all(0 <= r.start_seconds and r.end_seconds <= WINDOW_END + 0.01
                         for r in artifact.source_refs)
    checks["source_refs_within_window"] = refs_in_window
    print(f"  refs within window    : {refs_in_window}")

    windows = list(getattr(artifact, "_pro_window_summaries", []) or [])
    stamps = windows[0].get("frame_timestamps") if windows else None
    checks["frame_timestamps_preserved"] = bool(stamps)
    print(f"  frame timestamps kept : {stamps}")

    checks["no_parallel_format"] = type(artifact).__name__ == "NarrationArtifact"
    print("  PARTIAL RESULT        : this is ONE window of 16; NOT the final recap")

    evidence["contract"] = {
        **checks,
        "narration_id": artifact.narration_id,
        "status": artifact.status,
        "beat_count": len(artifact.beats),
        "summary": artifact.summary,
        "narration_text": artifact.narration_text,
        "beats": [
            {
                "order": b.order, "title": b.title,
                "narration_text": b.narration_text,
                "source_refs": [
                    {"asset_id": r.asset_id, "start_s": r.start_seconds,
                     "end_s": r.end_seconds,
                     "transcript_segment_ids": r.transcript_segment_ids}
                    for r in b.source_refs
                ],
            }
            for b in artifact.beats
        ],
        "is_partial_single_window_result": True,
        "is_final_film_recap": False,
    }
    return all(v for k, v in checks.items() if isinstance(v, bool))


def main() -> int:
    _load_env()
    evidence: dict = {
        "proof": "single_window_codecraft_summary_pro",
        "note": "LIMITED live integration test. NOT final Track 9 acceptance.",
    }

    if not preflight(evidence):
        evidence["verdict"] = "SINGLE-WINDOW PROOF NOT PASSED"
        evidence["reason"] = "pre-flight failed"
        _write(evidence)
        print("\nVERDICT: SINGLE-WINDOW PROOF NOT PASSED (pre-flight)")
        return 1

    artifact, meter, spy, elapsed, asset, transcript = run_window(evidence)
    report_measurements(evidence, meter, spy)

    if artifact is None:
        evidence["verdict"] = "SINGLE-WINDOW PROOF NOT PASSED"
        evidence["reason"] = evidence["execution"].get("error")
        _write(evidence)
        print("\nVERDICT: SINGLE-WINDOW PROOF NOT PASSED (execution failed)")
        print(f"evidence written: {EVIDENCE}")
        return 1

    grounded = validate_grounding(evidence, artifact, spy, transcript)
    contract_ok = validate_contract(evidence, artifact, asset, transcript)

    # Routing evidence: canonical identity preserved end to end.
    wire_ids = {c.get("model_requested") for c in meter.calls}
    echoed = {c.get("model_echoed") for c in meter.calls if c.get("model_echoed")}
    identity_ok = wire_ids == {CANONICAL_MODEL} and echoed <= {CANONICAL_MODEL}
    evidence["routing"] = {
        "requested_canonical_model": CANONICAL_MODEL,
        "wire_model_ids_used": sorted(x for x in wire_ids if x),
        "wire_model_ids_echoed": sorted(echoed),
        "canonical_identity_preserved": identity_ok,
        "provider_used_internally": "codecraft",
    }

    print()
    print("=" * 76)
    print("VERDICT")
    print("=" * 76)
    print("  pre-flight            : OK")
    print(f"  one window executed   : {evidence['window']['windows_processed']} of 16")
    print(f"  real request success  : {meter.ok}/{meter.total}")
    print(f"  canonical identity    : {'preserved' if identity_ok else 'VIOLATED'}")
    print(f"  visual grounding      : {'demonstrated' if grounded else 'NOT demonstrated'}")
    print(f"  output contract       : {'preserved' if contract_ok else 'VIOLATED'}")
    passed = bool(grounded and contract_ok and identity_ok and meter.ok > 0)
    verdict = "SINGLE-WINDOW PROOF PASSED" if passed else "SINGLE-WINDOW PROOF NOT PASSED"
    evidence["verdict"] = verdict
    evidence["is_final_track9_acceptance"] = False
    _write(evidence)
    print(f"\n{verdict}")
    print("  (this verdict is NOT final Track 9 acceptance)")
    print(f"evidence written: {EVIDENCE}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
