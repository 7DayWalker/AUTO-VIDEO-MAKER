"""Explicit Track 9 import into the existing canonical execution/publish model.

No providers, scene generation, voice timing, or publication transitions live
here. Imported source-edit intent is pinned as a normal ProductionPlan; the
single acceptance oracle validates the immutable import evidence and final MP4.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from app.content.config import MAX_SCENE_DURATION, MIN_SCENE_DURATION
from app.content.models import ContentBrief, ProductionMetadata, ProductionPlan, Scene
from app.orchestrator.models import (
    PipelineStage, ProductionBinding, ProjectManifest, SourceSummaryHandoff, StageResult,
)
from app.orchestrator.production_execution import (
    BRIEF_FILE, META_FILE, PLAN_FILE, ProductionExecutor,
)
from app.production.models import NarrationArtifact, SourceAsset, SourceTranscript
from app.production.narration import validate_narration_artifact
from app.production.storage import (
    compute_content_hash, load_narration_artifact, load_production,
    production_dir, transcript_path_for, unit_plan_dir,
)
from app.production.summary_video import (
    NarratedSummaryArtifact, _atomic_write_json, load_narrated_summary,
    narration_content_hash, summary_video_output_path, validate_grounded_visual_refs,
)
from app.publishing.models import PublicationState, YOUTUBE_MAX_TITLE_LENGTH


class SummaryHandoffError(ValueError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class SummaryHandoffResult:
    project_dir: Path
    manifest: ProjectManifest
    created: bool


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _inside(directory: Path, path: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(directory.resolve())
    return resolved


def _protected_records(manifest: ProjectManifest) -> list:
    # Include IDENTITY_PERSISTED and any future non-preparable state as well as
    # all explicitly requested active/verified/ambiguous/failed states.
    return [record for record in (manifest.publication, manifest.tiktok_publication)
            if record is not None and (
                record.state not in {PublicationState.NOT_STARTED, PublicationState.PREPARED}
                or getattr(record, "youtube_video_id", None)
                or getattr(record, "tiktok_publish_id", None)
                or getattr(record, "tiktok_post_id", None))]


def validate_source_summary_handoff(manifest: ProjectManifest, directory: Path) -> None:
    """Read-only evidence validation called by the ONE acceptance oracle.

    Current production sidecars may be rebuilt without altering this accepted
    project. Its owned snapshots, plan, final fingerprint and lineage must all
    still agree; a provenance label alone never confers acceptance.
    """
    handoff, binding = manifest.source_summary_handoff, manifest.production_binding
    if (handoff is None or binding is None or binding.handoff_kind != "narrated_summary"
            or binding.project_id != manifest.project_id
            or binding.project_id != directory.name
            or binding.execution_dir != binding.project_id
            or (handoff.production_id, handoff.unit_id) != (binding.production_id, binding.unit_id)):
        raise ValueError("summary handoff binding/lineage is invalid")
    if (manifest.scene_checkpoints or manifest.scene_video_paths or manifest.voice_timing_path
            or manifest.scene_manifest_path or manifest.voice_audio_path
            or manifest.assembly_config.get("audio_policy") != "narration_only"
            or manifest.assembly_config.get("enable_captions") is not False):
        raise ValueError("summary handoff must not claim engine-generated scene/voice evidence")
    package = _inside(directory, directory / handoff.snapshot_dir)
    for name, digest in handoff.artifact_hashes.items():
        path = _inside(directory, package / name)
        if not path.is_file() or compute_content_hash(path) != digest:
            raise ValueError(f"accepted summary snapshot missing/changed: {name}")
    narration = NarrationArtifact.model_validate(_read(package / "narration.json"))
    summary = NarratedSummaryArtifact.model_validate(_read(package / "narrated_summary.json"))
    source = SourceAsset.model_validate(_read(package / "source_asset.json"))
    transcript = SourceTranscript.model_validate(_read(package / "transcript.json"))
    identity = (handoff.production_id, handoff.unit_id, handoff.narration_id, handoff.source_asset_id)
    for artifact in (narration, summary):
        if (artifact.production_id, artifact.unit_id, artifact.narration_id, artifact.source_asset_id) != identity:
            raise ValueError("accepted summary snapshot identity mismatch")
    if (narration.status not in {"grounded", "approved"}
            or transcript.asset_id != source.asset_id
            or summary.output_path != handoff.source_summary_path
            or summary.validation.get("valid") is not True
            or narration_content_hash(narration) != handoff.narration_content_hash):
        raise ValueError("accepted summary grounding/provenance mismatch")
    validate_narration_artifact(narration, source_asset=source, source_transcript=transcript)
    validate_grounded_visual_refs(narration, source, source_transcript=transcript)
    plan_path = _inside(directory, Path(manifest.content_plan_path or ""))
    if (plan_path != package / PLAN_FILE or not plan_path.is_file()
            or compute_content_hash(plan_path) != binding.unit_plan_content_hash):
        raise ValueError("accepted summary content plan hash mismatch")
    plan = ProductionPlan.model_validate(_read(plan_path))
    if plan.script != narration.narration_text.strip():
        raise ValueError("accepted summary script does not match grounded narration")
    media = _inside(directory, Path(manifest.final_video_path or ""))
    if (media != package / "narrated_summary.mp4" or not media.is_file()
            or media.stat().st_size != handoff.file_size
            or compute_content_hash(media) != handoff.file_fingerprint
            or summary.file_size != handoff.file_size
            or (summary.file_fingerprint and summary.file_fingerprint != handoff.file_fingerprint)
            or (summary.narration_content_hash and summary.narration_content_hash != handoff.narration_content_hash)):
        raise ValueError("accepted summary media fingerprint/size mismatch")


def _import_plan(project_id, production, unit, narration, transcript) -> ProductionPlan:
    """Grounded source-edit INTENT, not evidence of scene generation.

    Scene duration fields remain estimates under the existing plan schema;
    exact source ranges are retained in the imported narration snapshot and
    visual descriptions. No observed scene/voice timing is manufactured.
    """
    edits = []
    for beat in narration.beats:
        for ref in beat.source_refs:
            span = ref.end_seconds - ref.start_seconds
            pieces = max(1, math.ceil(span / MAX_SCENE_DURATION))
            for part in range(pieces):
                start = ref.start_seconds + span * part / pieces
                end = ref.start_seconds + span * (part + 1) / pieces
                edits.append(Scene(
                    id=f"source_edit_{len(edits) + 1:03d}", sequence=len(edits) + 1,
                    narration=beat.narration_text,
                    visual_prompt=f"Existing {ref.asset_id} [{start:.3f}, {end:.3f}): {beat.title}. Imported source edit; no scene generation.",
                    estimated_duration=max(MIN_SCENE_DURATION, min(MAX_SCENE_DURATION, end - start)),
                    purpose="source_summary_handoff",
                ))
    title = " ".join(unit.title.split()) or " ".join(production.title.split())
    hook = unit.content_intent.get("hook") or narration.beats[0].narration_text
    cta = unit.content_intent.get("call_to_action")
    metadata = {key: value.strip() for key in ("niche", "content_goal", "target_audience", "style")
                if isinstance(value := unit.content_intent.get(key), str) and 0 < len(value.strip()) <= 100}
    return ProductionPlan(
        project_id=project_id, title=title[:YOUTUBE_MAX_TITLE_LENGTH],
        hook=hook.strip() if isinstance(hook, str) and hook.strip() else narration.beats[0].title,
        script=narration.narration_text.strip(), call_to_action=cta.strip() if isinstance(cta, str) and cta.strip() else None,
        estimated_duration=sum(edit.estimated_duration for edit in edits), scenes=edits,
        metadata=ProductionMetadata(language=transcript.language or "en", **metadata),
    )


def promote_narrated_summary_to_project(
    production_id: str, unit_id: str, *, root: Optional[Path] = None,
    projects_root: Optional[Path] = None,
) -> SummaryHandoffResult:
    # Share the existing fresh-manifest writer lock: promotion cannot erase a
    # record persisted concurrently by either platform. Immutable media packages
    # also ensure rebuilding a sidecar can never mutate uploaded bytes.
    from app.publishing.reconciliation import _PUBLICATION_MANIFEST_LOCK

    with _PUBLICATION_MANIFEST_LOCK:
        return _promote(production_id, unit_id, root=root, projects_root=projects_root)


def _promote(production_id, unit_id, *, root, projects_root):
    from app.assembly.ffmpeg import check_ffprobe_available, probe_media
    from app.orchestrator.reconciliation import apply_reconciliation, reconcile_project
    from app.publishing.reconciliation import save_manifest

    production = load_production(production_id, root=root)
    if production.production_id != production_id:
        raise SummaryHandoffError("BOUND_PROJECT_CONFLICT", "Production directory and intent identity do not match.")
    unit = next((item for item in production.units if item.unit_id == unit_id), None)
    if unit is None:
        raise SummaryHandoffError("UNIT_NOT_FOUND", "The requested unit is not part of this production.", 404)
    executor = ProductionExecutor(production_id, productions_root=root, projects_root=projects_root)
    try:
        bound = executor.discover_bindings(production).get(unit_id)
    except ValueError as exc:
        raise SummaryHandoffError("BOUND_PROJECT_CONFLICT", f"Existing binding evidence must be repaired first: {exc}") from exc
    if bound and bound.manifest.source_summary_handoff is None:
        raise SummaryHandoffError("BOUND_PROJECT_CONFLICT", "This unit already has a standard execution project. Summary promotion never replaces its media or publication records.")

    summary = load_narrated_summary(production_id, unit_id, root=root)
    narration = load_narration_artifact(production_id, unit_id, root=root)
    media = _inside(production_dir(production_id, root=root), summary_video_output_path(production_id, unit_id, root=root))
    if not media.is_file() or media.stat().st_size <= 0:
        raise SummaryHandoffError("SUMMARY_VIDEO_INVALID", "Assemble a non-empty narrated_summary.mp4 before promotion.", 422)
    digest, size = compute_content_hash(media), media.stat().st_size
    content_hash = narration_content_hash(narration)
    if bound:
        previous = bound.manifest.source_summary_handoff
        if (bound.manifest.scene_checkpoints or bound.manifest.scene_video_paths
                or bound.manifest.voice_audio_path or bound.manifest.voice_timing_path
                or bound.manifest.scene_manifest_path):
            raise SummaryHandoffError("BOUND_PROJECT_CONFLICT", "The bound project contains engine-generated scene/voice evidence. Summary promotion never replaces it.")
        protected = _protected_records(bound.manifest)
        if protected and (digest != previous.file_fingerprint or content_hash != previous.narration_content_hash
                          or any(record.file_fingerprint and record.file_fingerprint != previous.file_fingerprint for record in protected)):
            raise SummaryHandoffError("SUMMARY_PUBLICATION_PROTECTED", "Accepted summary media/narration has publication evidence. Changed media cannot replace it; inspect/reconcile the existing project and use a new unit for a new master.")

    expected_path = media.relative_to(production_dir(production_id, root=root).resolve()).as_posix()
    if ((summary.production_id, summary.unit_id, summary.narration_id, summary.source_asset_id)
            != (production_id, unit_id, narration.narration_id, narration.source_asset_id)
            or (narration.production_id, narration.unit_id) != (production_id, unit_id)
            or narration.status not in {"grounded", "approved"}
            or summary.output_path != expected_path
            or summary.file_size != size
            or summary.validation.get("valid") is not True
            or (summary.file_fingerprint and summary.file_fingerprint != digest)
            or (summary.narration_content_hash and summary.narration_content_hash != content_hash)):
        raise SummaryHandoffError("SUMMARY_VIDEO_INVALID", "Summary metadata, grounded narration and assembled bytes must agree. Reassemble the summary first.", 422)
    if not check_ffprobe_available():
        raise SummaryHandoffError("FFPROBE_UNAVAILABLE", "FFprobe is required to accept a summary master.", 503)
    try:
        info = probe_media(media)
    except Exception as exc:
        raise SummaryHandoffError("SUMMARY_VIDEO_INVALID", "The summary failed its fresh media probe; reassemble a valid narrated MP4.", 422) from exc
    if (not info.has_video or not info.has_audio or not info.duration
            or not math.isfinite(info.duration) or info.duration <= 0 or not info.width or not info.height
            or abs(info.duration - summary.output_duration_seconds) > 0.2
            or (summary.video_width and summary.video_width != info.width)
            or (summary.video_height and summary.video_height != info.height)):
        raise SummaryHandoffError("SUMMARY_VIDEO_INVALID", "The summary must pass a fresh video/audio/duration/dimension media check.", 422)
    if bound and digest == previous.file_fingerprint and content_hash == previous.narration_content_hash:
        if not reconcile_project(bound.directory_id, bound.project_dir, persist=False).complete:
            raise SummaryHandoffError("SUMMARY_HANDOFF_INVALID", "The accepted project evidence is missing or changed. Promotion will not overwrite it; inspect the bound project.", 422)
        return SummaryHandoffResult(bound.project_dir, bound.manifest, False)

    source = next((item for item in production.source_assets or [] if item.asset_id == narration.source_asset_id), None)
    if source is None:
        raise SummaryHandoffError("SOURCE_NOT_FOUND", "The summary's canonical source asset is missing.", 404)
    transcript = SourceTranscript.model_validate(_read(transcript_path_for(production_id, source.asset_id, root=root)))
    validate_narration_artifact(narration, source_asset=source, source_transcript=transcript)
    validate_grounded_visual_refs(narration, source, source_transcript=transcript)
    # Stable, compact directory identity also fits Windows staging paths. Full
    # production/unit lineage remains explicit on the immutable binding.
    identity_hash = hashlib.sha256(f"{production_id}\0{unit_id}".encode("utf-8")).hexdigest()[:24]
    project_id = bound.directory_id if bound else f"summary_{identity_hash}"
    directory = executor.projects_root / project_id
    _inside(executor.projects_root, directory)
    plan_dir = unit_plan_dir(production_id, unit_id, root=root)
    if not bound and (directory.exists() or any((plan_dir / name).exists() for name in (PLAN_FILE, BRIEF_FILE, META_FILE))):
        raise SummaryHandoffError("BOUND_PROJECT_CONFLICT", "An existing project directory or pinned unit plan occupies this handoff. Nothing was overwritten.")
    stamp = datetime.now().isoformat()
    snapshot_dir = f"summary_handoff/{uuid4().hex[:16]}"
    plan = _import_plan(project_id, production, unit, narration, transcript)
    brief = ContentBrief(
        topic=(plan.title if len(plan.title) >= 3 else f"Summary: {plan.title}"),
        target_duration=max(10, min(180, round(summary.target_duration_seconds))),
        number_of_scenes=max(2, min(20, len(plan.scenes))),
        production_context={"production_title": production.title, "unit_id": unit_id, "handoff_kind": "narrated_summary"},
    )
    executor.projects_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".summary-", dir=executor.projects_root.parent) as scratch:
        staging = Path(scratch) / project_id
        staging.mkdir()
        package = staging / snapshot_dir
        package.mkdir(parents=True)
        shutil.copyfile(media, package / "narrated_summary.mp4")
        if compute_content_hash(package / "narrated_summary.mp4") != digest:
            raise SummaryHandoffError("SUMMARY_VIDEO_CHANGED", "Summary bytes changed during promotion. No project was replaced.")
        snapshots = {"narration.json": narration, "narrated_summary.json": summary,
                     "source_asset.json": source, "transcript.json": transcript}
        for name, artifact in snapshots.items():
            _atomic_write_json(package / name, artifact.model_dump(mode="json"))
        _atomic_write_json(package / PLAN_FILE, plan.model_dump(mode="json"))
        plan_hash = compute_content_hash(package / PLAN_FILE)
        old_binding = bound.manifest.production_binding if bound else None
        binding = ProductionBinding(
            production_id=production_id, unit_id=unit_id, production_plan_version=production.plan_version,
            unit_plan_identity=plan.project_id, unit_plan_version=old_binding.unit_plan_version + 1 if old_binding else 1,
            unit_plan_content_hash=plan_hash, project_id=project_id,
            execution_dir=project_id, handoff_kind="narrated_summary", bound_at=old_binding.bound_at if old_binding else stamp,
        )
        manifest = bound.manifest.model_copy(deep=True) if bound else ProjectManifest(project_id=project_id, topic=plan.title)
        manifest.production_binding = binding
        manifest.source_summary_handoff = SourceSummaryHandoff(
            production_id=production_id, unit_id=unit_id, narration_id=narration.narration_id,
            source_asset_id=source.asset_id, source_summary_path=expected_path, snapshot_dir=snapshot_dir,
            artifact_hashes={name: compute_content_hash(package / name) for name in snapshots},
            narration_content_hash=content_hash, file_fingerprint=digest, file_size=size,
            promoted_at=stamp, summary_generated_at=summary.generated_at,
        )
        manifest.topic = plan.title
        manifest.content_plan_path = str(package / PLAN_FILE)
        manifest.final_video_path = str(package / "narrated_summary.mp4")
        manifest.assembly_config = {"resolution_width": info.width, "resolution_height": info.height,
                                    "enable_captions": False, "audio_policy": "narration_only", "handoff_kind": "narrated_summary"}
        manifest.stages = {
            "voice": StageResult.skipped(PipelineStage.VOICE, "Engine voice generation not run; Track 9 narration is baked into the accepted summary."),
            "scenes": StageResult.skipped(PipelineStage.SCENES, "Engine scene generation not run; Track 9 assembled grounded source excerpts."),
        }
        _atomic_write_json(staging / "manifest.json", manifest.to_dict())
        report = reconcile_project(project_id, staging, persist=False)
        if not report.complete:
            raise SummaryHandoffError("SUMMARY_HANDOFF_INVALID", f"Canonical acceptance refused the import: {'; '.join(report.reasons)}", 422)
        apply_reconciliation(manifest, report)
        final_package = directory / snapshot_dir
        manifest.content_plan_path = str(final_package / PLAN_FILE)
        manifest.final_video_path = str(final_package / "narrated_summary.mp4")
        for stage in (PipelineStage.CONTENT, PipelineStage.ASSEMBLY, PipelineStage.COMPLETE):
            result = manifest.stages[stage.value]
            result.output_path = manifest.content_plan_path if stage == PipelineStage.CONTENT else manifest.final_video_path
            result.metadata = {"source_summary_handoff": True}
            if stage != PipelineStage.CONTENT:
                result.metadata.update(report.assembly["validation"])
                result.metadata["path"] = manifest.final_video_path
        meta = {"production_id": production_id, "unit_id": unit_id,
                "production_plan_version": binding.production_plan_version, "unit_plan_version": binding.unit_plan_version,
                "plan_identity": plan.project_id, "content_hash": plan_hash, "generated_at": stamp,
                "handoff_kind": "narrated_summary", "source_unit_title": unit.title,
                "planner_provider": None, "planner_model": None, "planner_warnings": [],
                "source_summary_handoff": manifest.source_summary_handoff.model_dump(mode="json")}
        backups = {name: (plan_dir / name).read_bytes() if (plan_dir / name).exists() else None for name in (PLAN_FILE, BRIEF_FILE, META_FILE)}
        created = not bound
        directory_created = False
        try:
            if created:
                directory.mkdir(parents=True, exist_ok=False)
                directory_created = True
            final_package.parent.mkdir(parents=True, exist_ok=True)
            os.replace(package, final_package)
            _atomic_write_json(plan_dir / PLAN_FILE, plan.model_dump(mode="json"))
            _atomic_write_json(plan_dir / BRIEF_FILE, brief.model_dump(mode="json"))
            _atomic_write_json(plan_dir / META_FILE, meta)
            executor.discover_bindings_from_scan(production, {project_id: (directory, manifest, None)})
            save_manifest(directory / "manifest.json", manifest)
        except Exception:
            for name, data in backups.items():
                path = plan_dir / name
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    temporary = path.with_suffix(path.suffix + ".rollback")
                    temporary.write_bytes(data)
                    os.replace(temporary, path)
            shutil.rmtree(final_package, ignore_errors=True)
            if directory_created and directory.is_dir() and not (directory / "manifest.json").exists():
                shutil.rmtree(directory)
            raise
        return SummaryHandoffResult(directory, manifest, created)