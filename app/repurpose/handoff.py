"""Import a validated short through the ONE canonical acceptance oracle."""
from __future__ import annotations

import hashlib
import math
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.content.config import MIN_SCENE_DURATION, MAX_SCENE_DURATION
from app.content.models import ContentBrief, ProductionMetadata, ProductionPlan, Scene
from app.orchestrator.models import PipelineStage, ProductionBinding, ProjectManifest, SourceRepurposeHandoff, StageResult
from app.orchestrator.production_execution import BRIEF_FILE, META_FILE, PLAN_FILE, ProductionExecutor
from app.production.models import SourceAsset, SourceTranscript
from app.production.storage import compute_content_hash, production_dir, production_revision_lock, unit_plan_dir
from app.repurpose.cache import load_evidence
from app.repurpose.models import PlannerOutput, RepurposeError, RepurposePlan, RenderArtifact, timestamp, rendered_duration_bounds
from app.repurpose.planner import validate_output
from app.repurpose.storage import atomic_json, content_hash, load_render, load_unit_plan, read_json, unit_dir


def _inside(directory, path):
    path = Path(path).resolve()
    path.relative_to(Path(directory).resolve())
    return path


def _script(clip, transcript):
    if clip.narration_text:
        return clip.narration_text.strip()
    rows, seen = [], set()
    for cut in clip.cuts:
        for index, segment in enumerate(transcript.segments):
            if index not in seen and segment.end > cut.start_seconds and segment.start < cut.end_seconds:
                rows.append(segment.text)
                seen.add(index)
    return " ".join(rows).strip()


def _content_plan(project_id, clip, transcript, mode):
    script = _script(clip, transcript)
    scenes = []
    for cut in clip.cuts:
        span = cut.end_seconds-cut.start_seconds
        pieces = max(1, math.ceil(span/MAX_SCENE_DURATION))
        for part in range(pieces):
            start = cut.start_seconds + span*part/pieces
            end = cut.start_seconds + span*(part+1)/pieces
            scenes.append(Scene(id=f"source_edit_{len(scenes)+1:03d}", sequence=len(scenes)+1,
                narration=script, visual_prompt=f"Existing source [{start:.3f}, {end:.3f}); repurpose source edit, no scene generation.",
                estimated_duration=max(MIN_SCENE_DURATION, min(MAX_SCENE_DURATION, end-start)), purpose="repurpose_short"))
    return ProductionPlan(project_id=project_id, title=clip.title, hook=clip.hook, script=script,
        scenes=scenes, estimated_duration=sum(s.estimated_duration for s in scenes),
        metadata=ProductionMetadata(language="en" if mode == "narrated" else transcript.language or "en"))


def validate_repurpose_handoff(manifest, directory):
    """Read-only, immutable package verification; never consult mutable sidecars."""
    handoff, binding = manifest.source_repurpose_handoff, manifest.production_binding
    if (handoff is None or binding is None or binding.handoff_kind != "repurpose_short"
            or manifest.source_summary_handoff is not None or manifest.project_id != directory.name
            or binding.project_id != directory.name or binding.execution_dir != directory.name
            or (handoff.production_id, handoff.unit_id) != (binding.production_id, binding.unit_id)):
        raise ValueError("Short handoff binding/ownership mismatch")
    if (manifest.scene_checkpoints or manifest.scene_video_paths or manifest.voice_audio_path
            or manifest.voice_timing_path or manifest.scene_manifest_path
            or manifest.assembly_config.get("enable_captions") is not False):
        raise ValueError("Short handoff cannot claim engine-generated scene/voice/caption stages")
    package = _inside(directory, directory / handoff.snapshot_dir)
    for name, digest in handoff.artifact_hashes.items():
        path = _inside(directory, package/name)
        if not path.is_file() or compute_content_hash(path) != digest:
            raise ValueError(f"Short accepted evidence missing/changed: {name}")
    plan = RepurposePlan.model_validate(read_json(package/"repurpose_plan.json"))
    render = RenderArtifact.model_validate(read_json(package/"render.json"))
    source = SourceAsset.model_validate(read_json(package/"source_asset.json"))
    transcript = SourceTranscript.model_validate(read_json(package/"transcript.json"))
    evidence = read_json(package/"evidence.json")
    if (content_hash(plan) != handoff.plan_hash or render.plan_hash != handoff.plan_hash
            or plan.production_id != handoff.production_id or plan.batch_id != handoff.batch_id
            or (render.production_id, render.unit_id, render.batch_id) != (handoff.production_id, handoff.unit_id, handoff.batch_id)
            or source.asset_id != handoff.source_asset_id or transcript.asset_id != source.asset_id
            or source.sha256 != plan.source_sha256 or plan.transcript_sha256 != evidence["identity"]["transcript_sha256"]):
        raise ValueError("Short snapshot identity/fingerprint mismatch")
    from app.production.visual_cache import visual_source_identity
    identity = visual_source_identity(source, transcript)
    if identity != evidence["identity"] or content_hash({"identity": identity, "source_unit_id": evidence["source_unit_id"],
            "visuals": evidence["visuals"], "beats": evidence["beats"]}) != plan.request.evidence_revision:
        raise ValueError("Short cached evidence revision mismatch")
    output = PlannerOutput(clips=[c.model_copy(deep=True) for c in plan.clips])
    for clip in output.clips:
        clip.unit_id = None
        for cut in clip.cuts:
            cut.source_segment_id = None
    validate_output(output, SimpleNamespace(asset=source, transcript=transcript, visuals=evidence["visuals"]), plan.request)
    clip = next((c for c in plan.clips if c.unit_id == handoff.unit_id), None)
    if clip is None or len(render.cuts) != len(clip.cuts) or render.mode != plan.request.mode:
        raise ValueError("Short unit/edit mismatch")
    offset = 0.0
    for planned, actual in zip(clip.cuts, render.cuts):
        if any(actual.get(k) != v for k, v in planned.model_dump(mode="json").items()):
            raise ValueError("Rendered source cuts differ from accepted plan")
        if abs(actual["output_start"]-offset) > .02 or actual["output_duration"] <= 0:
            raise ValueError("Short output timing is discontinuous")
        offset += actual["output_duration"]
    if abs(offset-render.duration_seconds) > .15:
        raise ValueError("Short output timeline duration mismatch")
    minimum, maximum = rendered_duration_bounds(plan.request.target_seconds)
    if not minimum <= render.duration_seconds <= maximum+.1:
        raise ValueError("Short output duration is outside its editorial target bounds")
    levels = render.loudness
    if any(not isinstance(levels.get(key), (int, float)) or not math.isfinite(levels[key])
           for key in ("integrated_lufs", "true_peak_db")):
        raise ValueError("Short encoded loudness measurements must be finite")
    if (render.validation.get("valid") is not True or render.validation.get("decode_errors") != 0
            or not render.validation.get("captions_burned") or not render.validation.get("hook_banner_burned")
            or levels.get("integrated_lufs") is None or abs(levels["integrated_lufs"]+14) > .5
            or levels.get("true_peak_db") is None or levels["true_peak_db"] > -1):
        raise ValueError("Short media/audio acceptance evidence invalid")
    policy = "native_plus_narration" if plan.request.mode == "narrated" else "native_only"
    if manifest.assembly_config.get("audio_policy") != policy:
        raise ValueError("Short audio policy mismatch")
    content = _inside(directory, manifest.content_plan_path or "")
    if content != package/PLAN_FILE or compute_content_hash(content) != binding.unit_plan_content_hash:
        raise ValueError("Short pinned content plan changed")
    imported = ProductionPlan.model_validate(read_json(content))
    if imported.project_id != directory.name or imported.script != _script(clip, transcript) or imported.hook != clip.hook:
        raise ValueError("Short content plan does not match grounded edit")
    media = _inside(directory, manifest.final_video_path or "")
    if (media != package/"short.mp4" or not media.is_file() or media.stat().st_size != handoff.file_size
            or render.file_size != handoff.file_size or render.file_fingerprint != handoff.file_fingerprint
            or compute_content_hash(media) != handoff.file_fingerprint):
        raise ValueError("Short accepted media fingerprint changed")


def promote_short(production_id, unit_id, *, root=None, projects_root=None, expected_plan_hash=None, expected_file_fingerprint=None):
    from app.publishing.reconciliation import _PUBLICATION_MANIFEST_LOCK, save_manifest
    from app.orchestrator.reconciliation import apply_reconciliation, reconcile_project

    unit_dir(production_id, unit_id, root)  # Reject traversal before creating locks/directories.
    with production_revision_lock(production_id, root), production_revision_lock(unit_id, production_dir(production_id, root)/"units"), _PUBLICATION_MANIFEST_LOCK:
        production, unit, plan, clip = load_unit_plan(production_id, unit_id, root)
        render = load_render(production_id, unit_id, root)
        if render.plan_hash != content_hash(plan) or (expected_plan_hash and expected_plan_hash != render.plan_hash):
            raise RepurposeError("STALE_PLAN", "Promotion plan changed")
        if expected_file_fingerprint and expected_file_fingerprint != render.file_fingerprint:
            raise RepurposeError("STALE_MASTER", "Promotion media changed")
        executor = ProductionExecutor(production_id, productions_root=root, projects_root=projects_root)
        bound = executor.discover_bindings(production).get(unit_id)
        if bound:
            handoff = bound.manifest.source_repurpose_handoff
            if handoff is None:
                raise RepurposeError("BOUND_PROJECT_CONFLICT", "An existing non-short project owns this unit")
            if handoff.file_fingerprint != render.file_fingerprint or handoff.plan_hash != render.plan_hash:
                raise RepurposeError("ACCEPTED_MASTER_PROTECTED", "Changed media must use a new short unit")
            validate_repurpose_handoff(bound.manifest, bound.project_dir)
            return bound.project_dir, bound.manifest, False
        evidence = load_evidence(production_id, plan.request.source_asset_id, plan.request.source_unit_id, root=root)
        if evidence.revision != plan.request.evidence_revision:
            raise RepurposeError("STALE_EVIDENCE", "Cached evidence changed after rendering")
        unit_directory = unit_dir(production_id, unit_id, root)
        media = unit_directory/"short.mp4"
        if not media.is_file() or compute_content_hash(media) != render.file_fingerprint:
            raise RepurposeError("SHORT_MEDIA_CHANGED", "Rendered short bytes changed", 422)
        project_id = "short_" + hashlib.sha256(f"{production_id}\0{unit_id}".encode()).hexdigest()[:24]
        directory = executor.projects_root/project_id
        if directory.exists():
            raise RepurposeError("BOUND_PROJECT_CONFLICT", "Short project directory already exists without a valid binding")
        pinned = unit_plan_dir(production_id, unit_id, root)
        if any((pinned/name).exists() for name in (PLAN_FILE, BRIEF_FILE, META_FILE)):
            raise RepurposeError("BOUND_PROJECT_CONFLICT", "Existing unit content plan must not be overwritten")
        stamp, snapshot_dir = timestamp(), "repurpose_handoff/"+uuid4().hex[:16]
        content = _content_plan(project_id, clip, evidence.transcript, plan.request.mode)
        brief = ContentBrief(topic=clip.title if len(clip.title) >= 3 else f"Short: {clip.title}",
            target_duration=round(plan.request.target_seconds), number_of_scenes=max(2, min(20, len(content.scenes))),
            production_context={"unit_id": unit_id, "handoff_kind": "repurpose_short"})
        executor.projects_root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".short-", dir=executor.projects_root.parent) as temporary:
            staging = Path(temporary)/project_id
            package = staging/snapshot_dir
            package.mkdir(parents=True)
            snapshots = {"repurpose_plan.json": plan, "render.json": render, "source_asset.json": evidence.asset,
                "transcript.json": evidence.transcript, "evidence.json": {"identity": evidence.identity,
                    "source_unit_id": evidence.source_unit_id, "visuals": evidence.visuals, "beats": evidence.beats}}
            for name, value in snapshots.items():
                atomic_json(package/name, value)
            for name in ("captions.ass", "caption_timing.json"):
                shutil.copyfile(unit_directory/name, package/name)
            shutil.copyfile(media, package/"short.mp4")
            atomic_json(package/PLAN_FILE, content)
            plan_digest = compute_content_hash(package/PLAN_FILE)
            binding = ProductionBinding(production_id=production_id, unit_id=unit_id,
                production_plan_version=production.plan_version, unit_plan_identity=project_id, unit_plan_version=1,
                unit_plan_content_hash=plan_digest, project_id=project_id, execution_dir=project_id,
                handoff_kind="repurpose_short", bound_at=stamp)
            manifest = ProjectManifest(project_id=project_id, topic=clip.title, production_binding=binding,
                source_repurpose_handoff=SourceRepurposeHandoff(production_id=production_id, unit_id=unit_id,
                    batch_id=plan.batch_id, source_asset_id=evidence.asset.asset_id, plan_hash=render.plan_hash,
                    file_fingerprint=render.file_fingerprint, file_size=render.file_size, snapshot_dir=snapshot_dir,
                    artifact_hashes={name: compute_content_hash(package/name) for name in [*snapshots, "captions.ass", "caption_timing.json"]},
                    promoted_at=stamp), content_plan_path=str(package/PLAN_FILE), final_video_path=str(package/"short.mp4"),
                assembly_config={"resolution_width": 1080, "resolution_height": 1920, "enable_captions": False,
                    "audio_policy": "native_plus_narration" if plan.request.mode == "narrated" else "native_only", "handoff_kind": "repurpose_short"},
                stages={"voice": StageResult.skipped(PipelineStage.VOICE, "No engine voice stage; short audio is baked into accepted media."),
                    "scenes": StageResult.skipped(PipelineStage.SCENES, "No generated scenes; source cuts are baked into accepted media.")})
            atomic_json(staging/"manifest.json", manifest.to_dict())
            report = reconcile_project(project_id, staging, persist=False)
            if not report.complete:
                raise RepurposeError("SHORT_HANDOFF_INVALID", "Canonical acceptance refused: "+"; ".join(report.reasons), 422)
            apply_reconciliation(manifest, report)
            final_package = directory/snapshot_dir
            manifest.content_plan_path, manifest.final_video_path = str(final_package/PLAN_FILE), str(final_package/"short.mp4")
            for stage in (PipelineStage.CONTENT, PipelineStage.ASSEMBLY, PipelineStage.COMPLETE):
                result = manifest.stages[stage.value]
                result.output_path = manifest.content_plan_path if stage == PipelineStage.CONTENT else manifest.final_video_path
                result.metadata = {"source_repurpose_handoff": True}
                if stage != PipelineStage.CONTENT:
                    result.metadata.update(report.assembly["validation"])
                    result.metadata["path"] = manifest.final_video_path
            meta = {"production_id": production_id, "unit_id": unit_id,
                "production_plan_version": binding.production_plan_version, "unit_plan_version": 1,
                "plan_identity": project_id, "content_hash": plan_digest, "generated_at": stamp,
                "handoff_kind": "repurpose_short", "planner_provider": "agentrouter", "planner_model": "gpt-6.1-sol",
                "canonical_model_id": "gpt-6.1-sol", "planner_warnings": [],
                "source_repurpose_handoff": manifest.source_repurpose_handoff.model_dump(mode="json")}
            try:
                directory.mkdir(parents=True, exist_ok=False)
                final_package.parent.mkdir(parents=True, exist_ok=True)
                os.replace(package, final_package)
                atomic_json(pinned/PLAN_FILE, content)
                atomic_json(pinned/BRIEF_FILE, brief)
                atomic_json(pinned/META_FILE, meta)
                executor.discover_bindings_from_scan(production, {project_id: (directory, manifest, None)})
                save_manifest(directory/"manifest.json", manifest)
            except Exception:
                for name in (PLAN_FILE, BRIEF_FILE, META_FILE):
                    (pinned/name).unlink(missing_ok=True)
                if directory.is_dir() and not (directory/"manifest.json").exists():
                    shutil.rmtree(directory)
                raise
        atomic_json(unit_directory/"promotion.json", {"project_id": project_id, "file_fingerprint": render.file_fingerprint,
            "plan_hash": render.plan_hash, "promoted_at": stamp})
        return directory, manifest, True