"""
Evidence-based project reconciliation and acceptance validation.

This module is read-only by default: it validates durable artifacts and manifest
state, then optionally persists repaired stage state through ``reconcile_project``.
It never generates, downloads, or submits media.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, TypeVar

from app.content.models import ProductionPlan
from app.content.validator import validate_production_plan
from app.flow.scene_generator import validate_video_file
from app.orchestrator.captions import validate_srt_file
from app.orchestrator.models import (
    PipelineStage,
    ProjectManifest,
    StageResult,
    StageStatus,
)
from app.voice.validator import validate_audio_file
from app.assembly.ffmpeg import check_ffprobe_available, probe_media
from app.config import PROJECT_ROOT

_T = TypeVar("_T")
_R = TypeVar("_R")

# ---------------------------------------------------------------------------
# Phase 2B — bounded parallel VALIDATION (performance, never semantics)
#
# Root cause (measured): ``reconcile_project`` validates every scene video,
# the voice audio, and the final video through ffprobe SUBPROCESSES.  Run
# serially, ~150 sequential launches dominated ``GET /api/productions``
# (~7-9 s dev-warm, 20-25 s under live process-launch overhead) and single
# production progress time.  Each validation is independent, pure, and
# read-only, so running them concurrently changes ONLY wall time: the same
# validations run, on the same inputs, in preserved result order, on every
# single call.  Nothing is cached, skipped, reordered, or assumed.
#
# ``AVM_RECONCILE_MAX_PARALLELISM`` is an operator kill switch, not a state
# control: values < 1 are clamped, an unreadable value behaves
# conservatively (serial), and ``=1`` restores the original serial path
# byte-for-byte.  There is no default-on behavior that changes results.
# ---------------------------------------------------------------------------

DEFAULT_MAX_PARALLEL_VALIDATION = 8
# Measured on the 27-bound-project collection: 4 -> ~2.1-4.0s, 6 -> ~1.8-2.3s,
# 8 -> ~1.9-2.0s against 7.0-7.6s fully serial.  Six concurrent reconciliations
# (each fanning out at most eight leaf probes below it) captures the plateau
# while keeping worst-case transient subprocess fan-out bounded.
MAX_PARALLEL_RECONCILES = 6


def _validation_worker_cap() -> int:
    raw = str(os.environ.get("AVM_RECONCILE_MAX_PARALLELISM", "")).strip()
    if not raw:
        return DEFAULT_MAX_PARALLEL_VALIDATION
    try:
        value = int(raw)
    except ValueError:
        return 1  # unreadable setting: behave conservatively (serial).
    return max(1, value)


def parallel_map(function: Callable[[_T], _R], items: Iterable[_T], *,
                 cap: int = DEFAULT_MAX_PARALLEL_VALIDATION) -> List[_R]:
    """Map ``function`` over ``items``, preserving input order exactly.

    Runs serially when there is nothing to parallelize (0/1 items) or when
    the effective worker cap is below 2.  Exceptions propagate with the
    same type and FIRST-IN-ITEM-ORDER identity as the serial loop, so
    callers' error contracts (e.g. the HTTP 500 mapping in ``web.server``)
    are unchanged.  ``function`` must be independent per item and must not
    mutate shared state — validation/reconciliation probes qualify.
    """
    materialized = list(items)
    if len(materialized) < 2:
        return [function(item) for item in materialized]
    workers = min(_validation_worker_cap(), cap, len(materialized))
    if workers < 2:
        return [function(item) for item in materialized]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(function, materialized))


class ArtifactStatus(str, Enum):
    VALID = "VALID"
    MISSING = "MISSING"
    INVALID = "INVALID"
    STALE = "STALE"
    NOT_REQUIRED = "NOT_REQUIRED"


class ProjectVerdict(str, Enum):
    PROJECT_COMPLETE = "PROJECT_COMPLETE"
    PROJECT_INCOMPLETE = "PROJECT_INCOMPLETE"
    PROJECT_FAILED = "PROJECT_FAILED"
    PROJECT_REQUIRES_RECONCILIATION = "PROJECT_REQUIRES_RECONCILIATION"


@dataclass
class ReconciliationReport:
    project_id: str
    content: Dict[str, Any]
    voice: Dict[str, Any]
    scenes: Dict[str, Any]
    captions: Dict[str, Any]
    assembly: Dict[str, Any]
    verdict: ProjectVerdict
    reasons: List[str] = field(default_factory=list)
    persisted: bool = False

    @property
    def complete(self) -> bool:
        return self.verdict == ProjectVerdict.PROJECT_COMPLETE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "content": self.content,
            "voice": self.voice,
            "scenes": self.scenes,
            "captions": self.captions,
            "assembly": self.assembly,
            "verdict": self.verdict.value,
            "reasons": self.reasons,
            "persisted": self.persisted,
        }

    def summary(self) -> str:
        scene_items = self.scenes.get("items", [])
        valid = sum(item.get("status") == ArtifactStatus.VALID.value for item in scene_items)
        missing = sum(item.get("status") == ArtifactStatus.MISSING.value for item in scene_items)
        invalid = sum(item.get("status") == ArtifactStatus.INVALID.value for item in scene_items)
        final = self.assembly.get("validation", {})
        return "\n".join(
            [
                f"Project ID: {self.project_id}",
                f"Content: {self.content.get('status')}",
                f"Voice: {self.voice.get('status')} ({self.voice.get('path') or 'none'})",
                f"Scenes: {len(scene_items)} required; {valid} valid; {missing} missing; {invalid} failed",
                f"Captions: {self.captions.get('status')}",
                f"Assembly: {self.assembly.get('status')}",
                f"Final video: {final.get('path') or self.assembly.get('path') or 'none'}",
                f"Final validation: {final.get('valid', False)}",
                f"Project completion: {self.verdict.value}",
                f"Reason: {'; '.join(self.reasons) if self.reasons else 'all required evidence is valid'}",
            ]
        )


def _path(project_dir: Path, value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    # Relative manifest paths may be recorded against the project directory
    # or the workspace root; accept whichever resolves to an existing file.
    if (project_dir / path).exists():
        return project_dir / path
    return PROJECT_ROOT / path


def _content_evidence(manifest: ProjectManifest, project_dir: Path) -> Dict[str, Any]:
    path = _path(project_dir, manifest.content_plan_path)
    result: Dict[str, Any] = {"status": ArtifactStatus.MISSING.value, "path": str(path) if path else None}
    if not path or not path.exists():
        return result
    try:
        plan = ProductionPlan.model_validate(json.loads(path.read_text(encoding="utf-8")))
        validation = validate_production_plan(plan)
        if validation.valid and plan.project_id == manifest.project_id:
            result.update({"status": ArtifactStatus.VALID.value, "validation": validation.model_dump()})
        else:
            result.update(
                {
                    "status": ArtifactStatus.INVALID.value,
                    "validation": validation.model_dump(),
                    "error": "plan project_id mismatch" if plan.project_id != manifest.project_id else None,
                }
            )
    except Exception as exc:
        result.update({"status": ArtifactStatus.INVALID.value, "error": str(exc)})
    return result


def _voice_evidence(manifest: ProjectManifest, project_dir: Path, scene_count: int) -> Dict[str, Any]:
    audio_path = _path(project_dir, manifest.voice_audio_path)
    timing_path = _path(project_dir, manifest.voice_timing_path)
    result: Dict[str, Any] = {
        "status": ArtifactStatus.MISSING.value,
        "path": str(audio_path) if audio_path else None,
        "timing_path": str(timing_path) if timing_path else None,
    }
    if not audio_path or not audio_path.exists() or not timing_path or not timing_path.exists():
        return result
    audio = validate_audio_file(audio_path)
    result["validation"] = audio.model_dump()
    if not audio.valid:
        result.update({"status": ArtifactStatus.INVALID.value, "error": "; ".join(audio.errors)})
        return result
    try:
        timing = json.loads(timing_path.read_text(encoding="utf-8"))
        scenes = timing.get("scenes", [])
        duration = float(timing.get("voiceover_duration", 0))
        boundaries_valid = all(
            float(item.get("end", item.get("end_time", 0)))
            > float(item.get("start", item.get("start_time", 0)))
            for item in scenes
        )
        if (
            timing.get("project_id") != manifest.project_id
            or len(scenes) != scene_count
            or duration <= 0
            or not boundaries_valid
            or audio.duration is None
            or abs(duration - audio.duration) > 0.2
        ):
            raise ValueError("timing does not match project, scene count, or validated audio")
        result.update({"status": ArtifactStatus.VALID.value, "timing_validation": {"valid": True, "duration": duration}})
    except Exception as exc:
        result.update({"status": ArtifactStatus.INVALID.value, "error": str(exc)})
    return result


def _scene_evidence(manifest: ProjectManifest, project_dir: Path, plan: Optional[ProductionPlan]) -> Dict[str, Any]:
    expected = sorted(plan.scenes, key=lambda item: item.sequence) if plan else []
    configured_ratio = manifest.scene_config.get("aspect_ratio", "9:16")
    paths = [_path(project_dir, value) for value in manifest.scene_video_paths]
    by_name = {path.stem: path for path in paths if path}
    # Phase 6E: detect durable placeholder provenance from the scene manifest's
    # checkpoint_evidence. Legacy/real manifests carry no marker (or explicitly
    # say "real"), so absence remains classified as genuine generation.
    placeholders: Dict[str, bool] = {}
    if str((manifest.scene_config or {}).get("generation_kind")) == "placeholder":
        for scene in expected:
            placeholders[scene.id] = True
    try:
        sm_path = _path(project_dir, manifest.scene_manifest_path)
        if not placeholders and sm_path and Path(sm_path).exists():
            sm_raw = json.loads(Path(sm_path).read_text(encoding="utf-8"))
            raw_scenes = sm_raw.get("scenes") or []
            if isinstance(raw_scenes, dict):
                scene_pairs = list(raw_scenes.items())
            else:
                # Canonical SceneManifest serialization is a LIST of
                # SceneManifestEntry dicts keyed by "scene_id".
                scene_pairs = [
                    (str(entry.get("scene_id")), entry)
                    for entry in raw_scenes
                    if isinstance(entry, dict)
                ]
            for sid, entry in scene_pairs:
                evidence = entry.get("checkpoint_evidence") or {}
                if (
                    evidence.get("generation_kind") == "placeholder"
                    or evidence.get("provider") == "mock"
                ):
                    placeholders[str(sid)] = True
    except Exception:
        pass  # unreadable scene manifest cannot fabricate a real-generation claim
    # Phase 2B: resolve every expected scene's candidate path first (pure
    # path work, same values as the former per-iteration call), then run the
    # independent ffprobe validations concurrently through the bounded shared
    # helper.  Results keep exact ``expected`` order, so the produced items,
    # statuses, and verdict are identical to the serial path; with 0/1 scenes
    # (or the operator cap at 1) this IS the serial path.
    scene_dir = _path(project_dir, manifest.scene_manifest_path)
    resolved: List[tuple] = []
    for scene in expected:
        path = by_name.get(scene.id)
        if path is None:
            path = (scene_dir.parent if scene_dir else project_dir) / f"{scene.id}.mp4"
        resolved.append((scene, path))
    validations = parallel_map(
        lambda entry: validate_video_file(entry[1], configured_ratio), resolved
    )
    items = []
    for (scene, path), validation in zip(resolved, validations):
        status = ArtifactStatus.VALID.value if validation.get("valid") else (
            ArtifactStatus.MISSING.value if not path.exists() else ArtifactStatus.INVALID.value
        )
        item: Dict[str, Any] = {
            "scene_id": scene.id,
            "status": status,
            "path": str(path),
            "validation": validation,
        }
        if scene.id in placeholders:
            # Phase 6E: durably identifiable mock/placeholder output; this
            # item must never be mistaken for genuine provider generation.
            item["generation"] = "placeholder"
        items.append(item)
    return {
        "status": ArtifactStatus.VALID.value if items and all(i["status"] == ArtifactStatus.VALID.value for i in items) else ArtifactStatus.INVALID.value,
        "required_count": len(items),
        "items": items,
        "placeholder_count": sum(1 for i in items if i.get("generation") == "placeholder"),
    }


def _assembly_evidence(manifest: ProjectManifest, project_dir: Path) -> Dict[str, Any]:
    path = _path(project_dir, manifest.final_video_path)
    expected_width = int(manifest.assembly_config.get("resolution_width", 1080))
    expected_height = int(manifest.assembly_config.get("resolution_height", 1920))
    evidence: Dict[str, Any] = {"status": ArtifactStatus.MISSING.value, "path": str(path) if path else None}
    if not path or not path.exists():
        return evidence
    if path.stat().st_size <= 0 or not check_ffprobe_available():
        evidence.update({"status": ArtifactStatus.INVALID.value, "validation": {"valid": False, "path": str(path), "error": "empty file or FFprobe unavailable"}})
        return evidence
    try:
        info = probe_media(path)
        errors = []
        if not info.has_video:
            errors.append("no video stream")
        if not info.has_audio:
            errors.append("no audio stream")
        if info.duration <= 0:
            errors.append("duration is not positive")
        if info.width != expected_width or info.height != expected_height:
            errors.append(f"expected {expected_width}x{expected_height}, got {info.width}x{info.height}")
        validation = {
            "valid": not errors,
            "path": str(path),
            "exists": True,
            "file_size": info.file_size,
            "duration": info.duration,
            "width": info.width,
            "height": info.height,
            "video_codec": info.video_codec,
            "audio_codec": info.audio_codec,
            "has_video": info.has_video,
            "has_audio": info.has_audio,
            "errors": errors,
        }
        evidence.update({"status": ArtifactStatus.VALID.value if not errors else ArtifactStatus.INVALID.value, "validation": validation})
    except Exception as exc:
        evidence.update({"status": ArtifactStatus.INVALID.value, "validation": {"valid": False, "path": str(path), "error": str(exc)}})
    return evidence


def apply_reconciliation(manifest: ProjectManifest, report: "ReconciliationReport") -> None:
    """Apply evidence-based stage transitions to an in-memory manifest.

    Transitions are deliberately conservative:
    - Valid evidence upgrades a stage to completed (recovers interrupted runs).
    - Missing/invalid evidence downgrades only stages that claimed completion
      or were left running; never-attempted stages keep their pending state so
      the normal resume path executes them.
    """
    required_ok = report.verdict == ProjectVerdict.PROJECT_COMPLETE
    # Dependency-aware invalidation: a stage may only be upgraded to
    # completed when every upstream stage's evidence is also acceptable.
    # A changed/invalid upstream (e.g. regenerated captions) invalidates
    # downstream artifacts even if they physically validate in isolation.
    upstream_ok = True
    for stage, evidence in (
        (PipelineStage.CONTENT, report.content),
        (PipelineStage.VOICE, report.voice),
        (PipelineStage.SCENES, report.scenes),
        (PipelineStage.CAPTIONS, report.captions),
        (PipelineStage.ASSEMBLY, report.assembly),
    ):
        status = evidence.get("status")
        current = manifest.get_stage_result(stage)
        if status == ArtifactStatus.VALID.value and upstream_ok:
            manifest.set_stage_result(StageResult.completed(stage, output_path=evidence.get("path")))
        elif status == ArtifactStatus.NOT_REQUIRED.value:
            if current is None or current.status != StageStatus.SKIPPED:
                manifest.set_stage_result(StageResult.skipped(stage, "Not required"))
        elif status in {ArtifactStatus.MISSING.value, ArtifactStatus.INVALID.value}:
            if current is not None and current.status in {StageStatus.COMPLETED, StageStatus.RUNNING}:
                manifest.set_stage_result(StageResult.failed(
                    stage,
                    f"Reconciliation: {status}",
                    started_at=current.started_at,
                ))
        if status not in {ArtifactStatus.VALID.value, ArtifactStatus.NOT_REQUIRED.value}:
            upstream_ok = False
    if required_ok:
        manifest.set_stage_result(
            StageResult.completed(PipelineStage.COMPLETE, output_path=report.assembly.get("path"))
        )
    else:
        manifest.completed_at = None
        # Recompute the resume pointer from actual stage states; completing a
        # later stage must not advance past an earlier incomplete stage.
        for stage in PipelineStage.get_order()[:-1]:
            result = manifest.get_stage_result(stage)
            if result is None or result.status not in {StageStatus.COMPLETED, StageStatus.SKIPPED}:
                manifest.current_stage = stage
                break
        else:
            manifest.current_stage = PipelineStage.COMPLETE


def reconcile_project(
    project_id: str,
    project_dir: Optional[Path] = None,
    *,
    persist: bool = False,
    manifest_raw: Optional[dict] = None,
) -> ReconciliationReport:
    """Validate a project using physical evidence; optionally repair its manifest.

    ``manifest_raw`` is the already-parsed ``manifest.json`` dict. When supplied
    (by the P0-01 collection batch read) it is used verbatim so the manifest is
    not re-read from disk; the reconciliation itself is unchanged.
    """
    if project_dir is None:
        from app.orchestrator.pipeline import ORCHESTRATOR_OUTPUT_DIR
        project_dir = ORCHESTRATOR_OUTPUT_DIR / project_id
    project_dir = Path(project_dir)
    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        return ReconciliationReport(
            project_id=project_id,
            content={"status": ArtifactStatus.MISSING.value},
            voice={"status": ArtifactStatus.MISSING.value},
            scenes={"status": ArtifactStatus.MISSING.value, "items": []},
            captions={"status": ArtifactStatus.NOT_REQUIRED.value},
            assembly={"status": ArtifactStatus.MISSING.value},
            verdict=ProjectVerdict.PROJECT_FAILED,
            reasons=[f"manifest not found: {manifest_path}"],
        )

    manifest = ProjectManifest.from_dict(
        manifest_raw
        if manifest_raw is not None
        else json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    )
    content = _content_evidence(manifest, project_dir)
    plan = None
    if content.get("status") == ArtifactStatus.VALID.value:
        plan = ProductionPlan.model_validate(json.loads(Path(content["path"]).read_text(encoding="utf-8")))
    voice = _voice_evidence(manifest, project_dir, len(plan.scenes) if plan else 0)
    scenes = _scene_evidence(manifest, project_dir, plan)
    captions_required = bool(manifest.assembly_config.get("enable_captions", True))
    caption_path = _path(project_dir, manifest.captions_path)
    if not captions_required:
        captions = {"status": ArtifactStatus.NOT_REQUIRED.value, "path": str(caption_path) if caption_path else None}
    elif not caption_path or not caption_path.exists():
        captions = {"status": ArtifactStatus.MISSING.value, "path": str(caption_path) if caption_path else None}
    else:
        validation = validate_srt_file(caption_path)
        captions = {"status": ArtifactStatus.VALID.value if validation.get("valid") else ArtifactStatus.INVALID.value, "path": str(caption_path), "validation": validation}
    assembly = _assembly_evidence(manifest, project_dir)
    required_ok = (
        content.get("status") == ArtifactStatus.VALID.value
        and voice.get("status") == ArtifactStatus.VALID.value
        and scenes.get("status") == ArtifactStatus.VALID.value
        and captions.get("status") in {ArtifactStatus.VALID.value, ArtifactStatus.NOT_REQUIRED.value}
        and assembly.get("status") == ArtifactStatus.VALID.value
    )
    reasons = []
    for name, evidence in (("content", content), ("voice", voice), ("scenes", scenes), ("captions", captions), ("assembly", assembly)):
        if evidence.get("status") not in {ArtifactStatus.VALID.value, ArtifactStatus.NOT_REQUIRED.value}:
            reasons.append(f"{name}: {evidence.get('status')}")
    # Phase 6E: physical validity alone must not promote mock/placeholder
    # scene output into a genuine PROJECT_COMPLETE verdict.
    placeholder_count = int(scenes.get("placeholder_count") or 0)
    if required_ok and placeholder_count:
        reasons.append(
            f"scenes: {placeholder_count} placeholder/mock scene(s) "
            "(not genuine production output)"
        )
        required_ok = False
    verdict = ProjectVerdict.PROJECT_COMPLETE if required_ok else ProjectVerdict.PROJECT_REQUIRES_RECONCILIATION
    report = ReconciliationReport(
        project_id=manifest.project_id,
        content=content,
        voice=voice,
        scenes=scenes,
        captions=captions,
        assembly=assembly,
        verdict=verdict,
        reasons=reasons,
    )

    if persist:
        apply_reconciliation(manifest, report)
        manifest.updated_at = __import__("datetime").datetime.now().isoformat()
        manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        report.persisted = True
    return report
