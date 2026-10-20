"""NATIVE vs SYNTHETIC audio evidence hardening.

The single question this suite answers is:

    "Do we have EVIDENCE that this audio stream is synthetic or genuine
     native audio?"

It says nothing about loudness, quality, speech content, or whether a native
track is any good.

Covered contract:

1. Persistence (the actual defect): a local scene completion declares its audio
   identity on ``SceneGenerationResult.audio_facts``; that declaration must
   survive into the scene checkpoint instead of being replaced by a bare
   FFprobe description whose ``is_synthetic`` only ever carried the model
   default.
2. Classification: declared synthetic is NEVER native; declared non-synthetic
   may be; an undeclared identity stays UNKNOWN and is never resolved to
   ``False`` (which would fabricate native audio) nor to ``True``.
3. Historical evidence: probe-only audio facts stay uncertain.  Nothing is
   backfilled, guessed, or reinterpreted.
4. Accepted audio-policy semantics are unchanged: ``narration_only`` still
   suppresses native audio from delivery, ``native_only`` never promotes a
   narrator to native, ``native_plus_narration`` still combines, and ``none``
   stays intentionally audio-free.
5. The read-only ``/audio`` surface exposes the distinction and still performs
   no generation, execution, repair, extraction, transcoding, or write.

Hermetic: no network, no providers, nothing billable.  The real-media section
uses locally rendered FFmpeg artifacts (including a genuinely silent AAC stream)
and skips itself when FFmpeg/FFprobe are unavailable.
"""

from __future__ import annotations

import json
import subprocess

import pytest

import app.orchestrator.reconciliation as recon
from app.assembly.audio_contract import AudioMediaFacts
from app.assembly.ffmpeg import resolve_ffmpeg, resolve_ffprobe
from app.flow.scene_models import (
    FlowGenerationConfig,
    SceneGenerationRequest,
    SceneGenerationResult,
)
from app.orchestrator.models import (
    PipelineStage,
    ProductionBinding,
    ProjectManifest,
    StageResult,
)
from app.orchestrator.scene_execution import (
    completion_checkpoint_update,
    preserve_checkpoint_evidence,
)
from app.production.models import Production, ProductionCommand, ProductionUnit
from app.production.storage import (
    compute_content_hash,
    save_new_production,
    unit_plan_dir,
)
from app.web import server as web

PID = "prd_20260926_synthetic_evidence_001"
UID = "unit-001"
PROJECT_ID = "20260926_synthetic_evidence_project"
CREATED = "2026-09-26T00:00:00"
WIDTH = 160
HEIGHT = 288


def synthetic_facts(**overrides) -> dict:
    """DECLARED facts for a provider-generated silent topology track."""
    facts = {
        "has_audio": True,
        "audio_codec": "aac",
        "audio_duration": 10.0,
        "sample_rate": 44100,
        "channels": 2,
        "is_synthetic": True,
    }
    facts.update(overrides)
    return facts


def genuine_facts(**overrides) -> dict:
    """DECLARED facts for audio that really came from the source media."""
    return synthetic_facts(is_synthetic=False, **overrides)


def probe_only_facts(**overrides) -> dict:
    """The shape ``validate_video_file`` produces: a bare FFprobe description.

    ``AudioMediaFacts`` fills ``is_synthetic`` with its MODEL DEFAULT, so this is
    exactly what historical checkpoints hold and exactly why a default must never
    be read as an established identity.
    """
    facts = {
        "has_audio": True,
        "audio_codec": "aac",
        "audio_duration": 10.0,
        "sample_rate": 44100,
        "channels": 2,
    }
    facts.update(overrides)
    return facts


def make_production(audio_intent=None) -> Production:
    values = dict(
        production_id=PID,
        title="Native vs synthetic audio evidence",
        command=ProductionCommand(
            text="Create one video.",
            workload_shape="single_video",
            intended_unit_count=1,
            issued_at=CREATED,
        ),
        units=[
            ProductionUnit(
                unit_id=UID,
                ordinal=1,
                unit_type="standalone_video",
                title="Unit 001",
                content_intent={"target_duration": 10, "number_of_scenes": 2},
            )
        ],
        created_at=CREATED,
        generation_strategy="local",
    )
    if audio_intent is not None:
        values["audio_intent"] = audio_intent
    return Production(**values)


def make_plan():
    from app.content.models import ProductionPlan

    return ProductionPlan(
        project_id=PROJECT_ID,
        title="Synthetic evidence plan",
        hook="Hook",
        script="Narration text that is not an audio track by itself.",
        call_to_action=None,
        estimated_duration=10,
        scenes=[
            {
                "id": f"scene_{index:03d}",
                "sequence": index,
                "narration": "Spoken line.",
                "visual_prompt": f"Visual {index}",
                "estimated_duration": 5,
                "purpose": "fact",
            }
            for index in (1, 2)
        ],
    )


class Harness:
    """A bound production whose persisted scene evidence is fully controllable."""

    def __init__(self, tmp_path, monkeypatch):
        self.projects = tmp_path / "projects"
        self.productions = tmp_path / "productions"
        monkeypatch.setattr(web, "PROJECTS_DIR", self.projects)
        monkeypatch.setattr(web, "PRODUCTIONS_DIR", self.productions)
        monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(recon, "PROJECT_ROOT", tmp_path)
        self.directory = self.projects / PROJECT_ID
        self.directory.mkdir(parents=True)
        self.final = self.directory / f"{PROJECT_ID}_final.mp4"
        self.manifest = ProjectManifest(
            project_id=PROJECT_ID, topic="Native vs synthetic audio evidence"
        )
        self.manifest.initialize_stages()

    def policy(self, value, *, intent=None, intent_source="explicit_audio_intent"):
        config = {"audio_policy": value}
        if intent is not None:
            config["audio_intent"] = intent
            config["audio_intent_source"] = intent_source
        self.manifest.assembly_config = {
            **self.manifest.assembly_config,
            **config,
        }
        return self

    def with_final(self, size: int = 4096):
        self.final.write_bytes(b"\x00" * size)
        self.manifest.final_video_path = str(self.final)
        return self

    def checkpoints(self, **by_scene):
        """Persist scene checkpoints in the declared and/or probe-only shapes."""
        scene_checkpoints = {}
        for scene_id, payload in by_scene.items():
            checkpoint = {"status": "completed"}
            checkpoint.update(payload)
            scene_checkpoints[scene_id] = checkpoint
        self.manifest.scene_checkpoints = scene_checkpoints
        return self

    def stage(self, stage, result):
        self.manifest.set_stage_result(result)
        return self

    def voice_completed(self, *, provider="edge-tts", duration=9.8):
        self.manifest.voice_audio_path = str(self.directory / "voiceover.mp3")
        return self.stage(
            PipelineStage.VOICE,
            StageResult.completed(
                stage=PipelineStage.VOICE,
                output_path=self.manifest.voice_audio_path,
                metadata={"provider": provider, "duration": duration},
            ),
        )

    def voice_skipped(self, reason="Narrator/TTS intentionally not required"):
        return self.stage(
            PipelineStage.VOICE,
            StageResult.skipped(PipelineStage.VOICE, reason=reason),
        )

    def assembly_evidence(self, **overrides):
        block = {
            "audio_policy": (self.manifest.assembly_config or {}).get("audio_policy"),
            "voice_stage_status": "SKIPPED",
            "narration_track_present": False,
            "scenes_with_native_audio": [],
            "native_audio_present": False,
        }
        block.update(overrides)
        return self.stage(
            PipelineStage.ASSEMBLY,
            StageResult.completed(
                stage=PipelineStage.ASSEMBLY, metadata={"audio": block}
            ),
        )

    def acceptance(self, **metadata):
        facts = {
            "duration": 10.0,
            "width": WIDTH,
            "height": HEIGHT,
            "file_size": 4096,
            "video_codec": "h264",
            "audio_codec": None,
            "ffprobe": "passed",
        }
        facts.update(metadata)
        return self.stage(
            PipelineStage.COMPLETE,
            StageResult.completed(stage=PipelineStage.COMPLETE, metadata=facts),
        )

    def persist(self, audio_intent=None):
        plan_dir = unit_plan_dir(PID, UID, root=self.productions)
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_path = plan_dir / "production_plan.json"
        plan_path.write_text(
            json.dumps(make_plan().model_dump(mode="json")), encoding="utf-8"
        )
        content_hash = compute_content_hash(plan_path)
        (plan_dir / "plan_meta.json").write_text(
            json.dumps(
                {
                    "production_id": PID,
                    "unit_id": UID,
                    "production_plan_version": 1,
                    "unit_plan_version": 1,
                    "plan_identity": PROJECT_ID,
                    "content_hash": content_hash,
                }
            ),
            encoding="utf-8",
        )
        self.manifest.production_binding = ProductionBinding(
            production_id=PID,
            unit_id=UID,
            production_plan_version=1,
            unit_plan_identity=PROJECT_ID,
            unit_plan_version=1,
            unit_plan_content_hash=content_hash,
            project_id=PROJECT_ID,
            bound_at=CREATED,
        )
        (self.directory / "manifest.json").write_text(
            json.dumps(self.manifest.to_dict()), encoding="utf-8"
        )
        save_new_production(make_production(audio_intent), root=self.productions)
        return self

    def payload(self):
        return web.unit_audio_payload(PID, UID)


@pytest.fixture()
def h(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def stub_final_media_evidence(monkeypatch, *, has_audio, audio_required=None):
    """Deterministic stand-in for the existing final-media inspection seam."""

    def _fake(manifest, project_dir):
        return {
            "status": "VALID",
            "path": str(manifest.final_video_path or ""),
            "validation": {
                "valid": True,
                "exists": True,
                "has_audio": has_audio,
                "audio_stream_count": 1 if has_audio else 0,
                "audio_codec": "aac" if has_audio else None,
                "audio_sample_rate": 44100 if has_audio else None,
                "audio_channels": 2 if has_audio else None,
                "audio_stream_duration": 10.0 if has_audio else None,
                "audio_required": (
                    has_audio if audio_required is None else audio_required
                ),
            },
        }

    monkeypatch.setattr(recon, "final_media_evidence", _fake)
# ---------------------------------------------------------------------------
# 1. Persistence: the declared synthetic identity must survive the seam
# ---------------------------------------------------------------------------


def _image_motion_result(tmp_path, *, declared):
    """A local provider result shaped exactly like ImageMotion's completion."""
    artifact = tmp_path / "scene_001.mp4"
    artifact.write_bytes(b"\x00" * 2048)
    audio_facts = (
        AudioMediaFacts.model_validate(synthetic_facts()) if declared else None
    )
    return SceneGenerationResult.success_result(
        scene_id="scene_001",
        sequence=1,
        file_path=artifact,
        file_size=2048,
        actual_duration=10.0,
        width=WIDTH,
        height=HEIGHT,
        codec="h264",
        generation_cost=0,
        audio_facts=audio_facts,
    )


def test_completion_seam_persists_the_declared_synthetic_marker(tmp_path):
    """Phase 2's actual defect: the marker existed on the result and stopped."""
    result = _image_motion_result(tmp_path, declared=True)
    result_audio_facts = result.audio_facts.model_dump()
    # This is exactly what the local completion path now does before calling the
    # seam: the provider declaration supersedes the bare probe copy.
    validation = {
        "valid": True,
        "audio_facts": {**probe_only_facts(), **result_audio_facts},
    }

    update = completion_checkpoint_update(
        result=result,
        validation=validation,
        provider_name="image_motion",
        sequence=1,
        audio_facts=result_audio_facts,
        include_provenance=False,
    )

    persisted = update["audio_facts"]
    assert persisted["is_synthetic"] is True
    assert persisted["has_audio"] is True
    assert persisted["audio_codec"] == "aac"
    assert update["validation"]["audio_facts"]["is_synthetic"] is True


def test_completion_seam_never_invents_a_marker_the_provider_did_not_declare(
    tmp_path
):
    """Phase 2's guard: an absent declaration stays absent, never ``False``."""
    result = _image_motion_result(tmp_path, declared=False)
    validation = {"valid": True, "audio_facts": probe_only_facts()}

    update = completion_checkpoint_update(
        result=result,
        validation=validation,
        provider_name="image_motion",
        sequence=1,
        audio_facts=None,
        include_provenance=False,
    )

    assert "audio_facts" not in update
    # The unattributed probe copy is left exactly as it was: it establishes a
    # stream, not an identity.
    assert "is_synthetic" not in update["validation"]["audio_facts"]


def test_repeated_completion_and_inflight_rewrites_keep_the_marker(tmp_path):
    """Phase 7 item 11: evidence survives a checkpoint rewrite."""
    result = _image_motion_result(tmp_path, declared=True)
    validation = {"valid": True, "audio_facts": probe_only_facts()}
    completion = completion_checkpoint_update(
        result=result,
        validation=validation,
        provider_name="image_motion",
        sequence=1,
        audio_facts=result.audio_facts.model_dump(),
        include_provenance=False,
    )

    # A lifecycle rewrite merges over the checkpoint the way the pipeline's
    # ``persist_local_checkpoint`` does, and must not drop the declaration.
    checkpoint = {**completion, "status": "completed", "provider": "image_motion"}
    assert checkpoint["audio_facts"]["is_synthetic"] is True

    # A replacing scene-manifest write still preserves captured evidence.
    preserved = preserve_checkpoint_evidence(checkpoint, None)
    assert set(preserved) == {"provenance", "accounting"}
    assert checkpoint["audio_facts"]["is_synthetic"] is True


# ---------------------------------------------------------------------------
# 2. Classification: native / synthetic / unknown / absent are four answers
# ---------------------------------------------------------------------------


def manifest_with_checkpoints(**by_scene) -> ProjectManifest:
    manifest = ProjectManifest(project_id="provenance-project", topic="t")
    manifest.initialize_stages()
    manifest.scene_checkpoints = {
        scene_id: {"status": "completed", **payload}
        for scene_id, payload in by_scene.items()
    }
    return manifest


def test_declared_synthetic_audio_is_not_native_audio():
    manifest = manifest_with_checkpoints(scene_001={"audio_facts": synthetic_facts()})

    provenance = recon.scene_audio_provenance(manifest)
    summary = recon.summarize_audio_provenance(provenance)

    assert provenance == {"scene_001": recon.AUDIO_PROVENANCE_SYNTHETIC}
    assert summary["aggregate"] == recon.AUDIO_PROVENANCE_SYNTHETIC
    assert summary["established_native"] == []
    assert summary["established_synthetic"] == ["scene_001"]
    assert summary["unverified"] == []
    # The accepted observer agrees: synthetic silence is not native audio.
    assert recon.native_audio_observed(manifest) is False


def test_declared_genuine_audio_is_established_native():
    manifest = manifest_with_checkpoints(scene_001={"audio_facts": genuine_facts()})

    summary = recon.summarize_audio_provenance(recon.scene_audio_provenance(manifest))

    assert summary["aggregate"] == recon.AUDIO_PROVENANCE_NATIVE
    assert summary["established_native"] == ["scene_001"]
    assert recon.native_audio_observed(manifest) is True


def test_synthetic_aac_is_never_native_merely_because_aac_exists():
    """Phase 7 item 3: a perfectly valid stream that is still not native audio."""
    facts = synthetic_facts(audio_codec="aac", sample_rate=48000, channels=1)
    manifest = manifest_with_checkpoints(scene_001={"audio_facts": facts})

    summary = recon.summarize_audio_provenance(recon.scene_audio_provenance(manifest))

    assert summary["established_native"] == []
    assert summary["established_synthetic"] == ["scene_001"]
    assert AudioMediaFacts.model_validate(facts).has_audio is True
    assert AudioMediaFacts.model_validate(facts).has_native_audio is False


def test_probe_only_audio_facts_stay_unknown_and_are_never_backfilled():
    """Phase 4: a historical record must NOT become native audio by default.

    This is the exact harmful shape: ``validate_video_file`` builds
    ``AudioMediaFacts`` without an ``is_synthetic`` argument, so the persisted
    dict carries the model default and only LOOKS like an established
    non-synthetic answer.
    """
    historical = AudioMediaFacts.model_validate(probe_only_facts()).model_dump()
    assert historical["is_synthetic"] is False  # a DEFAULT, not evidence
    manifest = manifest_with_checkpoints(
        scene_001={"validation": {"valid": True, "audio_facts": historical}}
    )

    provenance = recon.scene_audio_provenance(manifest)
    summary = recon.summarize_audio_provenance(provenance)

    assert provenance == {"scene_001": recon.AUDIO_PROVENANCE_UNKNOWN}
    assert summary["aggregate"] == recon.AUDIO_PROVENANCE_UNKNOWN
    assert summary["established_native"] == []
    assert summary["unverified"] == ["scene_001"]


def test_declared_facts_without_a_marker_stay_unknown():
    """Phase 7 item 12: missing synthetic metadata must not become ``false``."""
    manifest = manifest_with_checkpoints(scene_001={"audio_facts": probe_only_facts()})

    assert (
        recon.scene_audio_provenance(manifest)["scene_001"]
        == recon.AUDIO_PROVENANCE_UNKNOWN
    )


def test_no_audio_evidence_is_absent_rather_than_unknown():
    manifest = manifest_with_checkpoints(
        scene_001={"audio_facts": synthetic_facts(has_audio=False)},
        scene_002={},
    )

    provenance = recon.scene_audio_provenance(manifest)

    assert provenance["scene_001"] == recon.AUDIO_PROVENANCE_ABSENT
    assert provenance["scene_002"] == recon.AUDIO_PROVENANCE_ABSENT
    assert (
        recon.summarize_audio_provenance(provenance)["aggregate"]
        == recon.AUDIO_PROVENANCE_ABSENT
    )


def test_mixed_scenes_keep_both_answers_separate():
    manifest = manifest_with_checkpoints(
        scene_001={"audio_facts": genuine_facts()},
        scene_002={"audio_facts": synthetic_facts()},
    )

    summary = recon.summarize_audio_provenance(recon.scene_audio_provenance(manifest))

    assert summary["aggregate"] == "mixed"
    assert summary["established_native"] == ["scene_001"]
    assert summary["established_synthetic"] == ["scene_002"]
    assert recon.native_audio_observed(manifest) is True


def test_provenance_reads_declared_facts_before_the_probe_copy():
    """Evidence hierarchy inside one checkpoint: declaration wins, always."""
    manifest = manifest_with_checkpoints(
        scene_001={
            "audio_facts": synthetic_facts(),
            "validation": {"valid": True, "audio_facts": genuine_facts()},
        }
    )

    assert (
        recon.scene_audio_provenance(manifest)["scene_001"]
        == recon.AUDIO_PROVENANCE_SYNTHETIC
    )


# ---------------------------------------------------------------------------
# 3. The read-only audio surface states the distinction honestly
# ---------------------------------------------------------------------------


def complete_run(
    h, monkeypatch, *, checkpoints, policy, intent, has_audio,
    narration=False, block_native=False, audio_required=None,
):
    """Persist one completed unit's evidence; stub the final-media probe."""
    h.with_final().policy(policy, intent=intent)
    if narration:
        h.voice_completed()
    else:
        h.voice_skipped(
            f"Narrator/TTS intentionally not required: audio intent={intent}, "
            f"effective audio policy={policy}"
        )
    h.assembly_evidence(
        voice_stage_status="COMPLETED" if narration else "SKIPPED",
        narration_track_present=narration,
        native_audio_present=block_native,
        scenes_with_native_audio=["scene_001"] if block_native else [],
    )
    if checkpoints:
        h.checkpoints(**checkpoints)
    h.acceptance(audio_codec="aac" if has_audio else None).persist(intent)
    stub_final_media_evidence(
        monkeypatch,
        has_audio=has_audio,
        audio_required=has_audio if audio_required is None else audio_required,
    )
    return h.payload()


def historical_probe_copy():
    """The exact persisted shape of a pre-hardening checkpoint."""
    return AudioMediaFacts.model_validate(probe_only_facts()).model_dump()


def test_declared_synthetic_silence_is_never_reported_as_native_audio(
    h, monkeypatch
):
    """A valid AAC stream, known synthetic, delivered under a native intent."""
    _status, body = complete_run(
        h, monkeypatch,
        checkpoints={"scene_001": {"audio_facts": synthetic_facts()}},
        policy="native_only", intent="native", has_audio=True, block_native=True,
        audio_required=True,
    )

    native = body["native_audio"]
    assert native["synthetic_identity"] == "synthetic"
    # An older assembly claim does not survive contradicting evidence.
    assert native["present"] is False
    assert native["established_synthetic_scenes"] == ["scene_001"]
    assert native["established_native_scenes"] == []
    # The stream really exists, so the artifact stays previewable; only the
    # PROVENANCE claim is withdrawn, and no narrator is invented to fill it.
    assert body["audio_stream"]["has_audio"] is True
    assert body["state"] == "AVAILABLE"
    assert body["playable"] is True
    assert body["audio_source"] == "unknown"
    assert body["narration"]["present"] is False


def test_declared_genuine_audio_is_still_reported_as_native(h, monkeypatch):
    _status, body = complete_run(
        h, monkeypatch,
        checkpoints={"scene_001": {"audio_facts": genuine_facts()}},
        policy="native_only", intent="native", has_audio=True, block_native=True,
    )

    native = body["native_audio"]
    assert native["synthetic_identity"] == "native"
    assert native["present"] is True
    assert native["established_native_scenes"] == ["scene_001"]
    assert body["audio_source"] == "native"


def test_historical_probe_only_evidence_stays_uncertain_instead_of_claiming_native(
    h, monkeypatch
):
    """No backfill and no false claim: the open question is reported as open."""
    _status, body = complete_run(
        h, monkeypatch,
        checkpoints={
            "scene_001": {
                "validation": {"valid": True, "audio_facts": historical_probe_copy()}
            }
        },
        policy="native_only", intent="native", has_audio=True, block_native=True,
        audio_required=True,
    )

    native = body["native_audio"]
    assert native["synthetic_identity"] == "unknown"
    assert native["unverified_scenes"] == ["scene_001"]
    assert native["established_native_scenes"] == []
    assert body["audio_source"] == "unknown"
    assert "synthetic identity was never declared" in body["reason"]
    # A valid, playable artifact is not demoted to a failure over provenance.
    assert body["state"] == "AVAILABLE"
    assert body["playable"] is True


def test_established_native_audio_outranks_a_weaker_unknown_scene(h, monkeypatch):
    """Known evidence beats inferred evidence, scene by scene."""
    _status, body = complete_run(
        h, monkeypatch,
        checkpoints={
            "scene_001": {"audio_facts": genuine_facts()},
            "scene_002": {
                "validation": {"valid": True, "audio_facts": historical_probe_copy()}
            },
        },
        policy="native_only", intent="native", has_audio=True,
    )

    native = body["native_audio"]
    assert native["established_native_scenes"] == ["scene_001"]
    assert native["unverified_scenes"] == ["scene_002"]
    assert native["present"] is True
    assert body["audio_source"] == "native"


def test_narration_only_still_suppresses_established_native_audio(h, monkeypatch):
    """Phase 5: accepted policy semantics are untouched by better evidence."""
    _status, body = complete_run(
        h, monkeypatch,
        checkpoints={"scene_001": {"audio_facts": genuine_facts()}},
        policy="narration_only", intent="narration", has_audio=True,
        narration=True, block_native=True,
    )

    assert body["audio_source"] == "narration"
    assert body["narration"]["present"] is True
    assert body["native_audio"]["synthetic_identity"] == "native"
    # Recorded, established, and still not delivered: suppression is policy.
    assert body["native_audio"]["scene_audio_recorded"] is True
    assert body["native_audio"]["present"] is False


def test_native_only_never_promotes_a_narrator_or_synthetic_silence(h, monkeypatch):
    _status, body = complete_run(
        h, monkeypatch,
        checkpoints={"scene_001": {"audio_facts": synthetic_facts()}},
        policy="native_only", intent="native", has_audio=True,
        narration=True, audio_required=True,
    )

    assert body["narration"]["present"] is False
    assert body["narration"]["track_recorded"] is True
    assert body["native_audio"]["present"] is False
    assert body["audio_source"] == "unknown"


def test_both_tracks_delivered_are_still_reported_together(h, monkeypatch):
    _status, body = complete_run(
        h, monkeypatch,
        checkpoints={"scene_001": {"audio_facts": genuine_facts()}},
        policy="native_plus_narration", intent="native_plus_narration",
        has_audio=True, narration=True, block_native=True,
    )

    assert body["audio_source"] == "native_plus_narration"
    assert body["narration"]["present"] is True
    assert body["native_audio"]["present"] is True


def test_none_policy_behavior_is_unchanged_by_this_task(h, monkeypatch):
    _status, body = complete_run(
        h, monkeypatch, checkpoints={}, policy="none", intent="none",
        has_audio=False, audio_required=False,
    )

    assert body["state"] == "NOT_REQUIRED"
    assert body["audio_source"] == "none"
    assert body["native_audio"]["synthetic_identity"] == "absent"
    assert body["playable"] is False


def test_final_artifact_evidence_outranks_scene_native_claims(h, monkeypatch):
    """Scene evidence never makes audio exist when the artifact carries none."""
    _status, body = complete_run(
        h, monkeypatch,
        checkpoints={"scene_001": {"audio_facts": genuine_facts()}},
        policy="native_only", intent="native", has_audio=False,
        block_native=True, audio_required=False,
    )

    assert body["state"] == "NO_AUDIO"
    assert body["audio_stream"]["has_audio"] is False
    assert body["native_audio"]["present"] is False
    assert body["audio_source"] == "none"
    assert body["playable"] is False


def test_scene_evidence_without_any_declared_copy_is_absent_not_false(h, monkeypatch):
    """No audio evidence at all is a different answer from a declared silence."""
    _status, body = complete_run(
        h, monkeypatch, checkpoints={}, policy="native_only", intent="native",
        has_audio=False, audio_required=False,
    )

    assert body["native_audio"]["synthetic_identity"] == "absent"
    assert body["native_audio"]["unverified_scenes"] == []
    assert body["native_audio"]["established_synthetic_scenes"] == []



# ---------------------------------------------------------------------------
# 4. REAL artifacts: silent AAC, genuine tone, and the actual local provider
# ---------------------------------------------------------------------------

_FFMPEG = resolve_ffmpeg()
_FFPROBE = resolve_ffprobe()
_REAL_MEDIA = pytest.mark.skipif(
    not (_FFMPEG and _FFPROBE),
    reason="FFmpeg/FFprobe unavailable; real-artifact validation skipped",
)


def render(path, *, extra_audio_args, size=None):
    from pathlib import Path as _P

    target = _P(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-f", "lavfi", "-i",
        f"color=c=blue:s={size or f'{WIDTH}x{HEIGHT}'}:d=1.0:r=30",
        *extra_audio_args,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
    ]
    args += (
        ["-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest"]
        if extra_audio_args
        else ["-an"]
    )
    args.append(str(target))
    result = subprocess.run(
        [_FFMPEG, "-y", *args], capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, result.stderr[-600:]
    return target


@_REAL_MEDIA
def test_real_silent_aac_stream_is_not_established_native_audio(tmp_path):
    """A real silent AAC track: audio exists, its identity does not."""
    from app.flow.scene_generator import validate_video_file

    silent = render(
        tmp_path / "silent.mp4",
        size="270x480",
        extra_audio_args=["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d=1.0"],
    )
    validation = validate_video_file(silent, "9:16")
    assert validation["valid"] is True, validation
    assert validation["has_audio"] is True           # a stream really exists

    persisted = validation["audio_facts"]            # the probe-only shape
    manifest = manifest_with_checkpoints(
        scene_001={"validation": {"valid": True, "audio_facts": persisted}}
    )
    provenance = recon.scene_audio_provenance(manifest)

    # The probe describes the stream; it cannot say whether AVM made it, and
    # nothing here promotes that silence into meaningful native audio.
    assert provenance["scene_001"] == recon.AUDIO_PROVENANCE_UNKNOWN
    assert (
        recon.summarize_audio_provenance(provenance)["established_native"] == []
    )
    assert AudioMediaFacts.model_validate(persisted).has_audio is True

    # The same bytes, once DECLARED synthetic by a provider, are settled facts.
    declared = {**persisted, "is_synthetic": True}
    declared_manifest = manifest_with_checkpoints(
        scene_001={"audio_facts": declared}
    )
    assert (
        recon.scene_audio_provenance(declared_manifest)["scene_001"]
        == recon.AUDIO_PROVENANCE_SYNTHETIC
    )
    assert recon.native_audio_observed(declared_manifest) is False


@_REAL_MEDIA
def test_real_image_motion_render_declares_silence_and_survives_the_seam(tmp_path):
    """Provider -> result -> completion seam -> checkpoint -> classifier.

    This is the end-to-end proof of the hardened path, using the REAL local
    provider and a REAL FFmpeg render.
    """
    from app.orchestrator.visual_providers import ImageMotionVisualProvider

    assets = tmp_path / "assets"
    assets.mkdir()
    image = assets / "scene_001.png"
    render_result = subprocess.run(
        [
            _FFMPEG, "-y", "-f", "lavfi", "-i", f"color=c=teal:s={WIDTH}x{HEIGHT}",
            "-frames:v", "1", str(image),
        ],
        capture_output=True, text=True, timeout=120,
    )
    assert render_result.returncode == 0, render_result.stderr[-400:]

    provider = ImageMotionVisualProvider(assets, tmp_path / "scenes")
    request = SceneGenerationRequest(
        project_id="real-motion-evidence",
        scene_id="scene_001",
        sequence=1,
        visual_prompt="A still image brought to life",
        config=FlowGenerationConfig(aspect_ratio="9:16", duration=1),
    )
    result = provider.generate_scene(request)
    assert result.success is True

    # The provider declares what it actually produced.
    assert result.audio_facts is not None
    assert result.audio_facts.has_audio is True
    assert result.audio_facts.is_synthetic is True
    assert result.audio_facts.has_native_audio is False

    from app.flow.scene_generator import validate_video_file

    validation = validate_video_file(result.file_path, "9:16")
    declared = result.audio_facts.model_dump()
    completion = completion_checkpoint_update(
        result=result,
        validation={**validation, "audio_facts": declared},
        provider_name="image_motion",
        sequence=1,
        audio_facts=declared,
        include_provenance=False,
    )

    manifest = manifest_with_checkpoints(
        scene_001={
            "audio_facts": completion["audio_facts"],
            "validation": completion["validation"],
        }
    )

    assert (
        recon.scene_audio_provenance(manifest)["scene_001"]
        == recon.AUDIO_PROVENANCE_SYNTHETIC
    )
    # The accepted native-audio observer now refuses to count it...
    assert recon.native_audio_observed(manifest) is False
    # ...which is exactly what it could not do before the marker survived: with
    # only the bare probe copy persisted (the historical shape), the very same
    # silent stream still reads as unattributed, and the accepted observer
    # counts it as native.  That contrast is the defect this task closes.
    historical_only = manifest_with_checkpoints(
        scene_001={"validation": {"valid": True, "audio_facts": validation}}
    )
    assert (
        recon.scene_audio_provenance(historical_only)["scene_001"]
        == recon.AUDIO_PROVENANCE_UNKNOWN
    )
    assert recon.native_audio_observed(historical_only) is True


@_REAL_MEDIA
def test_real_genuine_audio_declared_native_is_still_native(tmp_path):
    """The fix must not swallow genuine provider audio."""
    from app.flow.scene_generator import validate_video_file

    clip = render(
        tmp_path / "tone.mp4",
        size="270x480",
        extra_audio_args=[
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0"
        ],
    )
    validation = validate_video_file(clip, "9:16")
    assert validation["valid"] is True, validation

    declared = {**validation["audio_facts"], "is_synthetic": False}
    manifest = manifest_with_checkpoints(
        scene_001={"audio_facts": declared, "validation": validation}
    )
    summary = recon.summarize_audio_provenance(
        recon.scene_audio_provenance(manifest)
    )

    assert summary["aggregate"] == recon.AUDIO_PROVENANCE_NATIVE
    assert recon.native_audio_observed(manifest) is True
    assert AudioMediaFacts.model_validate(declared).has_native_audio is True


# ---------------------------------------------------------------------------
# 5. The real local pipeline persists the declaration end to end
# ---------------------------------------------------------------------------


@_REAL_MEDIA
def test_real_local_production_persists_synthetic_identity_end_to_end(
    tmp_path, monkeypatch
):
    """A real executed production, read back through the audio surface.

    Nothing is stubbed here except the intelligence layer: the scenes are real
    FFmpeg renders produced by the real local provider, the assembly is real,
    and the assertions read the durable checkpoint the pipeline actually wrote.
    """
    import app.content.planner as planner_module
    from app.orchestrator import pipeline as pipeline_module
    from app.orchestrator.models import ProjectManifest
    from app.voice import config as voice_config

    projects = tmp_path / "projects"
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    monkeypatch.setattr(web, "PROJECTS_DIR", projects)
    monkeypatch.setattr(pipeline_module, "ORCHESTRATOR_OUTPUT_DIR", projects)
    monkeypatch.delenv("AVM_MOCK_MODE", raising=False)
    monkeypatch.setenv("AVM_IMAGE_SOURCE", "fixture")
    monkeypatch.setattr(voice_config, "TTS_PROVIDER", "mock")

    class _Provider:
        name = "mock"
        model = "mock-model"

    class _Planner:
        provider = _Provider()

        def __init__(self):
            from app.content.planner import ScenePlanner

            self._sp = ScenePlanner(mock_mode=True)

        def plan(self, brief):
            return self._sp.plan(brief)

    monkeypatch.setattr(
        planner_module, "create_scene_planner",
        lambda *, mock_mode=False, output_dir=None: _Planner(),
    )

    status, created = web.create_production_payload({
        "mode": "single_video", "creation_method": "local",
        "title": "Synthetic identity end to end",
        "brief": "A deterministic offline production for synthetic audio evidence.",
        # The offline mock planner always emits its fixed 6-scene / ~31s plan,
        # so the brief must ask for that shape or plan validation rejects it
        # (same constraint as the accepted offline e2e proofs).
        "output_intent": "youtube_shorts", "target_duration": 30, "scene_count": 6,
        "tone": "clear and cinematic", "audio_intent": "native",
    })
    assert status == 201, created
    production_id = created["production_id"]

    report = web._production_executor(production_id).execute()
    assert report.complete is True, (
        "the offline real-render production did not complete; this test drives "
        f"real FFmpeg fixture renders and reports: {report}"
    )

    endpoint_status, body = web.unit_audio_payload(production_id, "unit-001")
    assert endpoint_status == 200, body
    assert body["project_id"], body

    # The declaration is now durable, not just in-memory on the result.
    # Resolved through the bound project id, never by scanning for a manifest.
    manifest_path = projects / body["project_id"] / "manifest.json"
    assert manifest_path.is_file(), manifest_path
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    scene_checkpoints = persisted["scene_checkpoints"]
    assert scene_checkpoints
    for checkpoint in scene_checkpoints.values():
        assert "audio_facts" in checkpoint, (
            "the local completion seam must persist the declared audio facts"
        )
        assert checkpoint["audio_facts"]["has_audio"] is True
        assert checkpoint["audio_facts"]["is_synthetic"] is True
        # The probe copy it replaced claimed the opposite by model default.
        assert checkpoint["validation"]["audio_facts"]["is_synthetic"] is True

    # Assembly evidence agrees: no scene contributed meaningful native audio.
    audio_block = persisted["stages"]["assembly"]["metadata"]["audio"]
    assert audio_block["native_audio_present"] is False
    assert audio_block["scenes_with_native_audio"] == []

    # And the read surface refuses the native claim it used to make.
    assert body["native_audio"]["synthetic_identity"] == "synthetic"
    assert body["native_audio"]["present"] is False
    assert body["native_audio"]["established_synthetic_scenes"]
    assert body["audio_intent"] == "native"
    assert body["effective_audio_policy"] == "native_only"
    assert body["narration"]["present"] is False
    # Native intent with only synthetic silence is delivered silence, never
    # narration: that policy outcome is the accepted one and stays untouched.
    assert body["audio_source"] in ("none", "unknown")
    assert body["state"] in ("NO_AUDIO", "NOT_REQUIRED", "AVAILABLE")
    assert isinstance(ProjectManifest.from_dict(persisted), ProjectManifest)





# ---------------------------------------------------------------------------
# 6. Side-effect safety: this is an evidence-only change
# ---------------------------------------------------------------------------


def snapshot(root):
    """Every file below ``root`` as ``{relative path: bytes}``."""
    from pathlib import Path as _P

    base = _P(root)
    return {
        str(path.relative_to(base)): (path.stat().st_size, path.read_bytes())
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }


def test_endpoint_read_writes_nothing_and_creates_no_new_media(h, monkeypatch):
    """Phase 8: no mutation, no new files, no extraction, no transcoding."""
    complete_run(
        h, monkeypatch,
        checkpoints={"scene_001": {"audio_facts": synthetic_facts()}},
        policy="native_only", intent="native", has_audio=True, audio_required=True,
    )
    before_projects = snapshot(h.projects)
    before_productions = snapshot(h.productions)

    _first, body_one = h.payload()
    _second, body_two = h.payload()

    assert snapshot(h.projects) == before_projects
    assert snapshot(h.productions) == before_productions
    assert body_one == body_two
    # No extracted or transcoded audio artifact appeared anywhere.
    for path in list(snapshot(h.projects)) + list(snapshot(h.productions)):
        assert not path.endswith((".mp3", ".wav", ".m4a", ".aac", ".ogg")), path


def test_endpoint_never_generates_tts_executes_or_repairs(h, monkeypatch):
    """Any generation, execution, or repair path is a hard failure here."""
    complete_run(
        h, monkeypatch,
        checkpoints={"scene_001": {"audio_facts": genuine_facts()}},
        policy="native_only", intent="native", has_audio=True, block_native=True,
    )

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("audio evidence must never generate or execute")

    from app.orchestrator.pipeline import ProductionOrchestrator
    from app.orchestrator.production_execution import ProductionExecutor
    from app.orchestrator.reconciliation import reconcile_project
    from app.voice.generator import VoiceGenerator

    monkeypatch.setattr(VoiceGenerator, "generate_from_plan", _forbidden)
    monkeypatch.setattr(ProductionExecutor, "execute", _forbidden)
    monkeypatch.setattr(ProductionOrchestrator, "run", _forbidden)
    monkeypatch.setattr(recon, "reconcile_project", _forbidden)
    monkeypatch.setattr(recon, "apply_reconciliation", _forbidden)
    monkeypatch.setattr(web, "production_command_payload", _forbidden)

    status, body = h.payload()

    assert status == 200
    assert body["native_audio"]["synthetic_identity"] == "native"


def test_endpoint_never_encodes_media(h, monkeypatch):
    """Reading audio evidence may inspect, but must never run FFmpeg encode."""
    from app.assembly import ffmpeg as ffmpeg_module

    complete_run(
        h, monkeypatch,
        checkpoints={"scene_001": {"audio_facts": synthetic_facts()}},
        policy="native_only", intent="native", has_audio=True, audio_required=True,
    )

    def _no_encoding(*_args, **_kwargs):
        raise AssertionError("the audio evidence surface must not encode media")

    monkeypatch.setattr(ffmpeg_module, "run_ffmpeg", _no_encoding)
    monkeypatch.setattr(ffmpeg_module.FFmpegRunner, "run", _no_encoding)

    status, body = h.payload()

    assert status == 200
    # Nothing encoded: the evidence claim still comes only from persisted facts.
    assert body["native_audio"]["synthetic_identity"] == "synthetic"
    assert body["native_audio"]["present"] is False


def test_provenance_reads_never_probe_or_mutate_the_manifest(h, monkeypatch):
    """The classifier is a pure read over persisted dicts."""
    from app.orchestrator.models import ProjectManifest

    complete_run(
        h, monkeypatch,
        checkpoints={"scene_001": {"audio_facts": synthetic_facts()}},
        policy="native_only", intent="native", has_audio=True, audio_required=True,
    )
    manifest = ProjectManifest.from_dict(json.loads(
        (h.directory / "manifest.json").read_text(encoding="utf-8")
    ))
    before = manifest.model_dump()

    recon.scene_audio_provenance(manifest)
    recon.summarize_audio_provenance(recon.scene_audio_provenance(manifest))
    recon.native_audio_observed(manifest)

    assert manifest.model_dump() == before
    assert manifest.scene_checkpoints == before["scene_checkpoints"]


def test_no_provider_or_network_call_is_needed_to_classify_audio(monkeypatch):
    """Classification uses persisted evidence only: imports stay local."""
    import inspect

    for name in (
        "scene_audio_provenance",
        "summarize_audio_provenance",
        "native_audio_observed",
    ):
        source = inspect.getsource(getattr(recon, name))
        for forbidden in ("requests", "urllib", "socket", "http", "subprocess"):
            assert forbidden not in source, f"{name} references {forbidden}"


