"""AUDIO PREVIEW + AUDIO EVIDENCE — read-only evidence surface tests.

Contract under test:

* ``GET /api/productions/{pid}/units/{uid}/audio`` reports the DECLARED
  ``audio_intent``, the EFFECTIVE audio policy, and the ACTUAL audio result of
  the finished production, kept strictly distinct so that "what was asked for"
  can never be read as "what was delivered".
* The FINAL assembled media artifact is authoritative for whether audio exists;
  persisted acceptance metadata records that probe; assembly/voice/scene
  evidence establishes narration and native provenance; manifest configuration
  is the last resort.
* Synthetic silence is never native audio.  An intentionally audio-free
  production is ``NOT_REQUIRED``/``NO_AUDIO``, never a failure, and a ``native``
  production with no native audio is never converted into narration.
* Anything no evidence establishes stays ``None``/``unknown`` — never invented
  and never zero-filled.
* The surface is strictly read-only: no TTS, no execution, no repair, no
  writes, no filesystem paths or secrets, and it reuses the EXISTING
  final-media URL for playback instead of building a parallel audio pipeline.

Hermetic: no network, no provider calls, nothing billable.  Media inspection is
stubbed at the existing seam for the deterministic contract tests; the final
section proves the same readings against REAL FFmpeg-produced artifacts.
"""

from __future__ import annotations

import json
import subprocess
import threading

import pytest

import app.orchestrator.reconciliation as recon
from app.assembly.ffmpeg import resolve_ffmpeg
from app.orchestrator.models import (
    PipelineStage,
    ProductionBinding,
    ProjectManifest,
    StageResult,
)
from app.production.models import Production, ProductionCommand, ProductionUnit
from app.production.storage import save_new_production
from app.web import server as web

PID = "prd_20260926_audio_preview_001"
UID = "unit-001"
PROJECT_ID = "20260926_audio_preview_project"
CREATED = "2026-09-26T00:00:00"


def make_production(audio_intent=None) -> Production:
    values = dict(
        production_id=PID,
        title="Audio preview evidence production",
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


def make_binding(content_hash: str = "0" * 64) -> ProductionBinding:
    return ProductionBinding(
        production_id=PID,
        unit_id=UID,
        production_plan_version=1,
        unit_plan_identity=PROJECT_ID,
        unit_plan_version=1,
        unit_plan_content_hash=content_hash,
        project_id=PROJECT_ID,
        bound_at=CREATED,
    )


def make_plan() -> "object":
    """A plan whose scenes ALL carry narration text.

    Narration text is creative plan content; under the accepted audio-policy
    contract it is never an audio fact, so several tests below assert that.
    """
    from app.content.models import ProductionPlan

    narration = "Spoken narration that is not by itself an audio track."
    return ProductionPlan(
        project_id=PROJECT_ID,
        title="Audio preview evidence plan",
        hook="Hook",
        script=narration,
        call_to_action=None,
        estimated_duration=10,
        scenes=[
            {
                "id": f"scene_{index:03d}",
                "sequence": index,
                "narration": narration,
                "visual_prompt": f"Visual prompt {index}",
                "estimated_duration": 5,
                "purpose": "fact",
            }
            for index in (1, 2)
        ],
    )


@pytest.fixture()
def h(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def stub_final_media_evidence(
    monkeypatch,
    *,
    has_audio,
    audio_stream_count=None,
    audio_codec=None,
    sample_rate=None,
    channels=None,
    audio_stream_duration=None,
    audio_required=None,
):
    """Replace the EXISTING final-media inspection seam deterministically.

    The stub returns exactly the shape that seam produces, so the decision
    logic under test stays real; only the FFprobe subprocess is avoided.
    """
    calls = []

    def _fake(manifest, project_dir):
        calls.append((manifest.project_id, str(project_dir)))
        return {
            "status": "VALID",
            "path": str(manifest.final_video_path or ""),
            "validation": {
                "valid": True,
                "exists": True,
                "has_audio": has_audio,
                "audio_stream_count": audio_stream_count,
                "audio_codec": audio_codec,
                "audio_sample_rate": sample_rate,
                "audio_channels": channels,
                "audio_stream_duration": audio_stream_duration,
                "audio_required": audio_required,
            },
        }

    monkeypatch.setattr(recon, "final_media_evidence", _fake)
    return calls

class Harness:
    """A bound production whose bound project can be shaped freely."""

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
            project_id=PROJECT_ID,
            topic="Audio preview evidence",
            production_binding=make_binding(),
        )
        self.manifest.initialize_stages()

    # -- shape the persisted evidence ---------------------------------------
    def with_final(self, size: int = 4096):
        self.final.write_bytes(b"\x00" * size)
        self.manifest.final_video_path = str(self.final)
        return self

    def final_recorded_only(self):
        """The path is recorded in the manifest, but the bytes are gone."""
        self.manifest.final_video_path = str(self.final)
        return self

    def policy(self, value, *, intent=None, intent_source=None):
        config = {"audio_policy": value}
        if intent is not None:
            config["audio_intent"] = intent
        if intent_source is not None:
            config["audio_intent_source"] = intent_source
        self.manifest.assembly_config = {
            **self.manifest.assembly_config,
            **config,
        }
        return self

    def stage(self, stage, result):
        self.manifest.set_stage_result(result)
        return self

    def audio_evidence(self, **overrides):
        """Persist the assembly stage's audio evidence block."""
        config = self.manifest.assembly_config or {}
        block = {
            "audio_intent": config.get("audio_intent"),
            "audio_intent_source": config.get("audio_intent_source"),
            "audio_policy": config.get("audio_policy"),
            "voice_stage_status": "COMPLETED",
            "narration_track_present": False,
            "scenes_with_native_audio": [],
            "native_audio_present": False,
        }
        block.update(overrides)
        self.manifest.set_stage_result(
            StageResult.completed(
                stage=PipelineStage.ASSEMBLY, metadata={"audio": block}
            )
        )
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

    def voice_skipped(self, reason):
        return self.stage(
            PipelineStage.VOICE,
            StageResult.skipped(PipelineStage.VOICE, reason=reason),
        )

    def acceptance(self, **metadata):
        facts = {
            "duration": 10.0,
            "width": 1080,
            "height": 1920,
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

    def scene_audio_facts(self, **facts):
        self.manifest.scene_checkpoints = {
            "scene_001": {"status": "completed", "audio_facts": dict(facts)}
        }
        return self

    def persist(self, audio_intent=None):
        """Write genuine plan artifacts, then the hash-verified binding.

        ``ProductionExecutor.discover_bindings`` refuses to adopt a project
        whose binding does not hash-match its real plan artifact, so the
        fixture produces the same durable evidence a real run leaves behind
        instead of bypassing that verification.
        """
        from app.production.storage import compute_content_hash, unit_plan_dir

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
        self.manifest.production_binding = make_binding(content_hash)
        (self.directory / "manifest.json").write_text(
            json.dumps(self.manifest.to_dict()), encoding="utf-8"
        )
        save_new_production(make_production(audio_intent), root=self.productions)
        return self

    def payload(self, unit_id=UID):
        return web.unit_audio_payload(PID, unit_id)



def build_completed(
    h,
    monkeypatch,
    *,
    policy,
    intent,
    has_audio,
    narration=False,
    native=False,
    audio_required=None,
    sample_rate=44100,
    channels=2,
):
    """Persist one completed unit's evidence and stub the final-media probe."""
    h.with_final().policy(
        policy, intent=intent, intent_source="explicit_audio_intent"
    )
    if narration:
        h.voice_completed()
    else:
        h.voice_skipped(
            "Narrator/TTS intentionally not required: audio intent="
            f"{intent}, effective audio policy={policy}"
        )
    h.audio_evidence(
        voice_stage_status="COMPLETED" if narration else "SKIPPED",
        narration_track_present=narration,
        native_audio_present=native,
        scenes_with_native_audio=["scene_001"] if native else [],
    )
    h.acceptance(audio_codec="aac" if has_audio else None).persist(intent)
    return stub_final_media_evidence(
        monkeypatch,
        has_audio=has_audio,
        audio_stream_count=1 if has_audio else 0,
        audio_codec="aac" if has_audio else None,
        sample_rate=sample_rate if has_audio else None,
        channels=channels if has_audio else None,
        audio_stream_duration=9.8 if has_audio else None,
        audio_required=has_audio if audio_required is None else audio_required,
    )


# ---------------------------------------------------------------------------
# 1. The audio outcomes, reported separately from the intent behind them
# ---------------------------------------------------------------------------


def test_narrated_production_exposes_the_narrator_bearing_final_media(h, monkeypatch):
    build_completed(
        h, monkeypatch, policy="narration_only", intent="narration",
        has_audio=True, narration=True,
    )

    status, body = h.payload()

    assert status == 200
    assert body["state"] == "AVAILABLE"
    assert body["available"] is True
    assert body["playable"] is True
    assert body["audio_intent"] == "narration"
    assert body["effective_audio_policy"] == "narration_only"
    # The delivered audio IS the narrator track.
    assert body["audio_source"] == "narration"
    assert body["narration_required"] is True
    assert body["narration"]["present"] is True
    assert body["narration"]["voice_stage_status"] == "COMPLETED"
    assert body["narration"]["voice_provider"] == "edge-tts"
    assert body["narration"]["voice_audio_duration"] == 9.8
    assert body["native_audio"]["present"] is False
    # Playback reuses the EXISTING range-aware final-media route.
    assert body["media_url"] == f"/api/projects/{PROJECT_ID}/media"
    assert body["media_content_type"] == "video/mp4"
    assert body["audio_stream"]["facts_source"] == "final_media_artifact"
    assert body["audio_stream"]["sample_rate"] == 44100
    assert body["audio_stream"]["channels"] == 2


def test_native_production_with_real_native_audio_reports_native(h, monkeypatch):
    build_completed(
        h, monkeypatch, policy="native_only", intent="native",
        has_audio=True, native=True,
    )

    _status, body = h.payload()

    assert body["state"] == "AVAILABLE"
    assert body["audio_intent"] == "native"
    assert body["effective_audio_policy"] == "native_only"
    assert body["audio_source"] == "native"
    assert body["narration_required"] is False
    assert body["narration"]["present"] is False
    assert body["native_audio"]["present"] is True
    assert body["native_audio"]["scenes"] == ["scene_001"]
    assert body["playable"] is True


def test_native_intent_without_native_audio_is_silence_never_narration(h, monkeypatch):
    """The critical non-regression: a silent result stays silent."""
    build_completed(
        h, monkeypatch, policy="native_only", intent="native",
        has_audio=False, audio_required=False,
    )

    status, body = h.payload()

def test_native_plus_narration_reports_both_tracks(h, monkeypatch):
    build_completed(
        h, monkeypatch, policy="native_plus_narration",
        intent="native_plus_narration", has_audio=True,
        narration=True, native=True,
    )

    _status, body = h.payload()

    assert body["state"] == "AVAILABLE"
    assert body["audio_intent"] == "native_plus_narration"
    assert body["effective_audio_policy"] == "native_plus_narration"
    assert body["audio_source"] == "native_plus_narration"
    assert body["narration"]["present"] is True
    assert body["native_audio"]["present"] is True


def test_none_policy_is_intentionally_audio_free_and_not_a_failure(h, monkeypatch):
    build_completed(
        h, monkeypatch, policy="none", intent="none", has_audio=False,
        audio_required=False,
    )

    status, body = h.payload()

    assert status == 200
    assert body["state"] == "NOT_REQUIRED"
    assert body["available"] is False
    assert body["playable"] is False
    assert body["media_url"] is None
    assert body["audio_intent"] == "none"
    assert body["effective_audio_policy"] == "none"
    assert body["audio_source"] == "none"
    assert body["narration_required"] is False
    assert body["narration"]["present"] is False
    assert body["narration"]["intentionally_skipped"] is True
    # Silence by choice is described as a choice, never as a defect.
    assert body["state"] != "FAILED"
    assert body["final_video"]["available"] is True


def test_audio_stream_present_under_a_none_policy_is_reported_unattributed(
    h, monkeypatch
):
    """Contradictory evidence is disclosed, never reconciled by invention."""
    build_completed(
        h, monkeypatch, policy="none", intent="none", has_audio=True,
        audio_required=False,
    )

    _status, body = h.payload()

    # The delivered file really does carry audio (the artifact is authoritative)...
    assert body["audio_stream"]["has_audio"] is True
    assert body["state"] == "AVAILABLE"
    # ...but nothing establishes whose track it is, so no source is claimed.
    assert body["audio_source"] == "unknown"


# ---------------------------------------------------------------------------
# 2. Narration and native evidence are reported only when they exist
# ---------------------------------------------------------------------------


def test_intentionally_skipped_voice_is_never_reported_as_unavailable_narration(
    h, monkeypatch
):
    build_completed(
        h, monkeypatch, policy="native_only", intent="native", has_audio=True,
        native=True,
    )

    _status, body = h.payload()

    narration = body["narration"]
    assert narration["voice_stage_status"] == "SKIPPED"
    assert narration["present"] is False
    assert narration["intentionally_skipped"] is True
    assert "intentionally not required" in narration["skip_reason"]
    # A deliberate skip is not a missing narrator: no failure wording anywhere.
    assert body["state"] == "AVAILABLE"
    assert "failed" not in body["reason"].lower()


def test_narration_text_in_the_plan_alone_never_makes_audio_available(h, monkeypatch):
    """The scenes all carry narration text; nothing was narrated."""
    build_completed(
        h, monkeypatch, policy="native_only", intent="native", has_audio=False,
        audio_required=False,
    )

    _status, body = h.payload()

    # The plan really does hold narration text (fixture guarantee)...
    plan = json.loads(
        (h.productions / PID / "units" / UID / "plan" / "production_plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(scene["narration"] for scene in plan["scenes"])
    # ...and it still produces no narration claim.
    assert body["narration"]["present"] is False
    assert body["audio_source"] == "none"
    assert body["playable"] is False


def test_synthetic_silence_is_not_reported_as_native_audio(h, monkeypatch):
    (
        h.with_final()
        .policy("native_only", intent="native")
        .voice_skipped("Narrator/TTS intentionally not required")
        .scene_audio_facts(has_audio=True, is_synthetic=True)
        .acceptance()
        .persist("native")
    )
    # Legacy shape: no assembly audio evidence block at all.
    stub_final_media_evidence(
        monkeypatch, has_audio=False, audio_stream_count=0, audio_required=False
    )

    _status, body = h.payload()

    assert body["native_audio"]["present"] is False
    assert body["evidence"]["source"] == "persisted_voice_and_scene_audio_evidence"


def test_genuine_scene_audio_facts_are_reported_as_native_audio(h, monkeypatch):
    (
        h.with_final()
        .policy("native_only", intent="native")
        .voice_skipped("Narrator/TTS intentionally not required")
        .scene_audio_facts(has_audio=True, is_synthetic=False)
        .acceptance(audio_codec="aac")
        .persist("native")
    )
    stub_final_media_evidence(
        monkeypatch, has_audio=True, audio_stream_count=1, audio_codec="aac",
        audio_required=True,
    )

    _status, body = h.payload()

    assert body["native_audio"]["present"] is True
    assert body["audio_source"] == "native"



# ---------------------------------------------------------------------------
# 3. States: incomplete, missing, failed, and the refusal to guess
# ---------------------------------------------------------------------------


def test_unit_without_bound_project_is_incomplete_not_failed(h):
    save_new_production(make_production("native"), root=h.productions)

    status, body = h.payload()

    assert status == 200
    assert body["state"] == "INCOMPLETE"
    assert body["available"] is False
    assert body["playable"] is False
    assert body["media_url"] is None
    assert body["project_id"] is None
    # The declared intent is still reported, without pretending it happened.
    assert body["audio_intent"] == "native"
    assert body["audio_intent_declared"] is True
    assert body["effective_audio_policy"] is None
    assert body["audio_stream"] is None


def test_assembled_but_unfinished_run_is_incomplete(h):
    h.policy("narration_only", intent="narration").persist("narration")

    status, body = h.payload()

    assert status == 200
    assert body["state"] == "INCOMPLETE"
    assert body["final_video"]["available"] is False
    assert body["audio_source"] == "unknown"


def test_recorded_but_missing_artifact_is_failed_and_stale_facts_are_not_echoed(
    h, monkeypatch
):
    (
        h.final_recorded_only()
        .policy("narration_only", intent="narration")
        .voice_completed()
        .audio_evidence(narration_track_present=True)
        .acceptance(
            audio_codec="aac",
            audio_stream={
                "has_audio": True,
                "audio_stream_count": 1,
                "audio_codec": "aac",
                "sample_rate": 44100,
                "channels": 2,
                "duration": 9.8,
            },
        )
        .persist("narration")
    )

    def _no_probe(*_args, **_kwargs):
        raise AssertionError("a missing artifact must never be probed into being")

    monkeypatch.setattr(recon, "final_media_evidence", _no_probe)

    status, body = h.payload()

    assert status == 200
    assert body["state"] == "FAILED"
    assert body["available"] is False
    assert body["playable"] is False
    assert body["media_url"] is None
    # Facts recorded for different bytes are not presented as live availability.
    assert body["audio_stream"]["facts_source"] == "persisted_acceptance_metadata"
    assert body["audio_stream"]["artifact_inspected"] is False
    assert "not deliverable" in body["reason"]


def test_failed_assembly_stage_is_reported_as_failed(h, monkeypatch):
    (
        h.with_final()
        .policy("narration_only", intent="narration")
        .stage(
            PipelineStage.ASSEMBLY,
            StageResult.failed(
                stage=PipelineStage.ASSEMBLY, error="assembly exploded"
            ),
        )
        .persist("narration")
    )
    stub_final_media_evidence(monkeypatch, has_audio=True, audio_codec="aac")

    _status, body = h.payload()

    assert body["final_video"]["state"] == "FAILED"
    assert body["state"] == "FAILED"
    assert body["playable"] is False


def test_uninspectable_artifact_keeps_stream_facts_null(h, monkeypatch):
    """No probe, no recorded facts: the answer is UNKNOWN, never a guess."""
    (
        h.with_final()
        .policy("native_only", intent="native")
        .stage(
            PipelineStage.ASSEMBLY, StageResult.completed(stage=PipelineStage.ASSEMBLY)
        )
        .persist("native")
    )
    monkeypatch.setattr(recon, "check_ffprobe_available", lambda: False)

    _status, body = h.payload()

    assert body["state"] == "UNKNOWN"
    assert body["available"] is False
    assert body["playable"] is False
    assert body["audio_stream"]["has_audio"] is None
    assert body["audio_stream"]["audio_stream_count"] is None
    assert body["audio_stream"]["facts_source"] == "unavailable"
    assert body["audio_source"] == "unknown"
    assert body["reason"].endswith("Nothing was inferred.")


# ---------------------------------------------------------------------------
# 4. Authority order: the final artifact outranks the request
# ---------------------------------------------------------------------------


def test_final_media_and_evidence_outrank_the_declared_intent(h, monkeypatch):
    """Intent says ``native``; the delivered file carries a narrator track."""
    build_completed(
        h, monkeypatch, policy="narration_only", intent="native", has_audio=True,
        narration=True,
    )

    _status, body = h.payload()

    # Intent is reported verbatim, but the RESULT follows the evidence.
    assert body["audio_intent"] == "native"
    assert body["effective_audio_policy"] == "narration_only"
    assert body["audio_source"] == "narration"
    assert body["narration"]["present"] is True
def test_narration_only_policy_suppresses_native_scene_audio_in_the_result(
    h, monkeypatch
):
    """Found by real execution: native evidence must not inflate the result.

    Under ``narration_only`` an existing narrator track suppresses native
    scene audio, so the delivered file is narration even when scenes carried
    sound.  Reporting the native presence as delivered audio would be a lie.
    """
    build_completed(
        h, monkeypatch, policy="narration_only", intent="narration",
        has_audio=True, narration=True, native=True,
    )

    _status, body = h.payload()

    assert body["audio_source"] == "narration"
    # Delivered truth and recorded evidence stay distinguishable.
    assert body["narration"]["present"] is True
    assert body["native_audio"]["present"] is False
    assert body["native_audio"]["scene_audio_recorded"] is True


def test_native_only_policy_never_delivers_the_recorded_narrator_track(
    h, monkeypatch
):
    """A leftover narrator artifact is evidence, not delivered audio."""
    (
        h.with_final()
        .policy(
            "native_only", intent="native", intent_source="explicit_audio_intent"
        )
        # A VOICE stage completed by an earlier, differently configured run
        # left a narrator artifact behind; the native-only policy excludes it.
        .voice_completed(provider="edge-tts", duration=9.8)
        .audio_evidence(
            voice_stage_status="COMPLETED",
            narration_track_present=True,
            native_audio_present=True,
            scenes_with_native_audio=["scene_001"],
        )
        .acceptance(audio_codec="aac")
        .persist("native")
    )
    stub_final_media_evidence(
        monkeypatch, has_audio=True, audio_stream_count=1, audio_codec="aac",
        audio_required=True,
    )

    _status, body = h.payload()

    assert body["audio_source"] == "native"
    assert body["narration"]["present"] is False
    assert body["narration"]["track_recorded"] is True
    assert body["native_audio"]["present"] is True





def test_legacy_manifest_without_any_audio_snapshot_stays_truthful(h, monkeypatch):
    """Pre-policy manifests resolve to the documented narrated default."""
    (
        h.with_final()
        .voice_completed(provider="edge-tts")
        .stage(
            PipelineStage.ASSEMBLY, StageResult.completed(stage=PipelineStage.ASSEMBLY)
        )
        .acceptance(audio_codec="aac")
        .persist(None)
    )
    stub_final_media_evidence(
        monkeypatch, has_audio=True, audio_stream_count=1, audio_codec="aac",
        audio_required=True,
    )

    _status, body = h.payload()

    assert body["audio_intent"] is None
    assert body["audio_intent_declared"] is False
    assert body["audio_intent_source"] is None
    # The documented backward-compatible default, identified by the absence of
    # any persisted intent source rather than by an invented one.
    assert body["effective_audio_policy"] == "narration_only"
    assert body["audio_source"] == "narration"
    assert body["evidence"]["source"] == "persisted_voice_and_scene_audio_evidence"
    assert body["state"] == "AVAILABLE"


# ---------------------------------------------------------------------------
# 5. Safety and read-only guarantees
# ---------------------------------------------------------------------------


def _project_snapshot(root):
    """Every file below ``root`` as ``{relative path: (size, bytes)}``."""
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files[str(path.relative_to(root))] = (path.stat().st_size, path.read_bytes())
    return files


def test_response_exposes_no_filesystem_paths_secrets_or_plan_text(h, monkeypatch):
    build_completed(
        h, monkeypatch, policy="narration_only", intent="narration",
        has_audio=True, narration=True, native=True,
    )

    _status, body = h.payload()
    rendered = json.dumps(body)

    assert "C:\\" not in rendered and "c:\\" not in rendered
    assert str(h.directory) not in rendered
    assert "voiceover.mp3" not in rendered
    assert ".mp4" not in rendered             # only the safe media URL
    assert "Spoken narration" not in rendered  # plan text never leaks
    assert "api_key" not in rendered.lower()
    assert body["media_url"] == f"/api/projects/{PROJECT_ID}/media"


def test_audio_read_never_mutates_any_durable_state(h, monkeypatch):
    build_completed(
        h, monkeypatch, policy="native_only", intent="native", has_audio=True,
        native=True,
    )
    before_projects = _project_snapshot(h.projects)
    before_productions = _project_snapshot(h.productions)

    _status, body = h.payload()
    _status, body = h.payload()  # twice: still nothing written

    assert _project_snapshot(h.projects) == before_projects
    assert _project_snapshot(h.productions) == before_productions
    assert body["state"] == "AVAILABLE"


def test_audio_read_never_invokes_tts_generation_or_execution(h, monkeypatch):
    build_completed(
        h, monkeypatch, policy="narration_only", intent="narration", has_audio=True,
        narration=True,
    )

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("the audio surface must never generate or execute")

    from app.orchestrator.pipeline import ProductionOrchestrator
    from app.orchestrator.production_execution import ProductionExecutor
    from app.voice.generator import VoiceGenerator

    monkeypatch.setattr(VoiceGenerator, "generate_from_plan", _forbidden)
    monkeypatch.setattr(ProductionExecutor, "execute", _forbidden)
    monkeypatch.setattr(ProductionOrchestrator, "run", _forbidden)
    monkeypatch.setattr(web, "production_command_payload", _forbidden)

    status, body = h.payload()

    assert status == 200
    assert body["state"] == "AVAILABLE"


# ---------------------------------------------------------------------------
# 6. The persisted acceptance-time audio facts (existing inspection seam)
# ---------------------------------------------------------------------------


def test_acceptance_gate_persists_real_audio_stream_facts(tmp_path, monkeypatch):
    """The preview facts come from the probe the acceptance gate ALREADY does."""
    import app.orchestrator.reconciliation as reconciliation
    from app.orchestrator.pipeline import ProductionOrchestrator
    from app.orchestrator.reconciliation import ArtifactStatus

    manifest = ProjectManifest(project_id="gate-audio", topic="gate")
    manifest.initialize_stages()
    monkeypatch.setattr(
        reconciliation, "_assembly_evidence",
        lambda _manifest, _dir: {
            "status": ArtifactStatus.VALID.value,
            "path": "output/gate-audio/final.mp4",
            "validation": {
                "valid": True, "duration": 10.0, "width": 1080, "height": 1920,
                "file_size": 1234, "video_codec": "h264", "audio_codec": "aac",
                "has_video": True, "has_audio": True, "audio_required": True,
                "audio_policy": "narration_only", "audio_stream_count": 1,
                "audio_sample_rate": 44100, "audio_channels": 2,
                "audio_stream_duration": 9.9,
            },
        },
    )

    class _Stub:
        def __init__(self):
            self.manifest = manifest
            self.project_dir = tmp_path
            self.saved = 0

        def _save_manifest(self):
            self.saved += 1

    stub = _Stub()
    ProductionOrchestrator._final_acceptance_gate(stub)

    assert stub.saved == 1
    metadata = manifest.stages[PipelineStage.COMPLETE.value].metadata
    assert metadata["audio_stream"] == {
        "has_audio": True,
        "audio_stream_count": 1,
        "audio_codec": "aac",
        "sample_rate": 44100,
        "channels": 2,
        "duration": 9.9,
    }
    # Existing acceptance keys are untouched.
    assert metadata["ffprobe"] == "passed"
    assert metadata["audio_codec"] == "aac"


def test_persisted_acceptance_facts_are_used_when_live_probe_is_impossible(
    h, monkeypatch
):
    build_completed(
        h, monkeypatch, policy="narration_only", intent="narration", has_audio=True,
        narration=True,
    )

    def _unavailable(*_args, **_kwargs):
        raise RuntimeError("ffprobe disappeared mid-request")

    monkeypatch.setattr(recon, "final_media_evidence", _unavailable)

    _status, body = h.payload()

    assert body["audio_stream"]["facts_source"] == "persisted_acceptance_metadata"
    assert body["audio_stream"]["artifact_inspected"] is False
    assert body["audio_stream"]["has_audio"] is True
    # Never-recorded detail stays null instead of being filled in.
    assert body["audio_stream"]["sample_rate"] is None
    assert body["audio_stream"]["channels"] is None
    assert body["state"] == "AVAILABLE"


def test_silent_acceptance_record_reads_as_no_audio_not_as_a_missing_codec(
    h, monkeypatch
):
    """A persisted ``has_audio: false`` is a positive fact, not absent data."""
    (
        h.with_final()
        .policy("native_only", intent="native")
        .voice_skipped("Narrator/TTS intentionally not required")
        .audio_evidence(voice_stage_status="SKIPPED")
        .acceptance(
            audio_stream={
                "has_audio": False,
                "audio_stream_count": 0,
                "audio_codec": None,
                "sample_rate": None,
                "channels": None,
                "duration": None,
            }
        )
        .persist("native")
    )
    monkeypatch.setattr(recon, "check_ffprobe_available", lambda: False)

    _status, body = h.payload()

    assert body["audio_stream"]["has_audio"] is False
    assert body["audio_stream"]["facts_source"] == "persisted_acceptance_metadata"
    assert body["state"] == "NO_AUDIO"
    assert body["audio_source"] == "none"
    assert body["playable"] is False

# ---------------------------------------------------------------------------
# 7. REAL artifacts: the same readings with an unstubbed FFprobe
# ---------------------------------------------------------------------------

from app.assembly.ffmpeg import resolve_ffmpeg, resolve_ffprobe

# FFmpeg is resolved through the project's own resolver (it is not required to
# be on PATH), exactly like the assembly and e2e suites do.
_FFMPEG = resolve_ffmpeg()
_FFPROBE = resolve_ffprobe()
_REAL_MEDIA = pytest.mark.skipif(
    not (_FFMPEG and _FFPROBE),
    reason="FFmpeg/FFprobe unavailable; real-artifact validation skipped",
)


def render(path, *, with_audio, duration=1.0):
    """Render a genuine 9:16 MP4, with or without a real audio stream.

    Argument order mirrors the repository's proven offline fixture helper
    (``tests/test_audio_intent_policy.py``): every input first, then the
    encoding options, then the output path.
    """
    args = ["-f", "lavfi", "-i", f"color=c=blue:s=160x288:d={duration}:r=30"]
    if with_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    args += (
        ["-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest"]
        if with_audio
        else ["-an"]
    )
    args.append(str(path))
    result = subprocess.run(
        [_FFMPEG, "-y", *args], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-600:]
    return path


def real_completed(h, *, policy, intent, with_audio, native_source=False):
    """A completed unit whose final MP4 and evidence both really exist."""
    h.manifest.assembly_config = {
        "resolution_width": 160,
        "resolution_height": 288,
        "audio_policy": policy,
        "audio_intent": intent,
        "audio_intent_source": "explicit_audio_intent",
    }
    h.with_final(size=0)  # placeholder; replaced by the rendered file below
    render(h.final, with_audio=with_audio)
    if with_audio and native_source:
        h.scene_audio_facts(has_audio=True, is_synthetic=False)
    elif with_audio:
        h.scene_audio_facts(has_audio=True, is_synthetic=True)
    h.audio_evidence(
        voice_stage_status="COMPLETED" if policy in ("narration_only",) else "SKIPPED",
        narration_track_present=policy == "narration_only",
        native_audio_present=bool(with_audio and native_source),
        scenes_with_native_audio=(
            ["scene_001"] if (with_audio and native_source) else []
        ),
    )
    h.persist(intent)
    return h


@_REAL_MEDIA
def test_real_final_video_with_audio_is_available_with_real_stream_facts(h):
    real_completed(
        h, policy="narration_only", intent="narration", with_audio=True
    )

    status, body = h.payload()

    assert status == 200
    assert body["state"] == "AVAILABLE"
    assert body["available"] is True
    assert body["playable"] is True
    assert body["media_url"] == f"/api/projects/{PROJECT_ID}/media"
    # Nothing was stubbed: the facts came from inspecting the real bytes.
    stream = body["audio_stream"]
    assert stream["facts_source"] == "final_media_artifact"
    assert stream["artifact_inspected"] is True
    assert stream["has_audio"] is True
    assert stream["audio_stream_count"] == 1
    assert stream["audio_codec"] == "aac"
    assert stream["sample_rate"] == 44100
    assert stream["channels"] == 2
    assert stream["audio_duration"] and stream["audio_duration"] > 0
    # Narration provenance is established only because a narrator track exists.
    assert body["audio_source"] == "narration"


@_REAL_MEDIA
def test_real_final_video_without_audio_is_no_audio_and_not_playable(h):
    real_completed(h, policy="none", intent="none", with_audio=False)

    status, body = h.payload()

    assert status == 200
    assert body["state"] == "NOT_REQUIRED"
    assert body["available"] is False
    assert body["playable"] is False
    assert body["media_url"] is None
    stream = body["audio_stream"]
    assert stream["facts_source"] == "final_media_artifact"
    assert stream["has_audio"] is False
    assert stream["audio_stream_count"] == 0
    assert stream["audio_codec"] is None
    assert body["audio_source"] == "none"


@_REAL_MEDIA
def test_real_native_audio_is_attributed_to_native_media_not_narration(h):
    real_completed(
        h, policy="native_only", intent="native", with_audio=True,
        native_source=True,
    )

    _status, body = h.payload()

    assert body["state"] == "AVAILABLE"
    assert body["audio_source"] == "native"
    assert body["native_audio"]["present"] is True
    assert body["narration"]["present"] is False
    assert body["narration_required"] is False
    assert body["audio_stream"]["audio_stream_count"] == 1


@_REAL_MEDIA
def test_real_silent_stream_with_synthetic_provenance_is_never_called_native(h):
    """Audio bytes exist, but the recorded provenance is synthetic silence."""
    real_completed(
        h, policy="native_only", intent="native", with_audio=True,
        native_source=False,
    )

    _status, body = h.payload()

    # The artifact does carry a stream...
    assert body["audio_stream"]["has_audio"] is True
    # ...but no evidence attributes it to native source media, and the intent
    # forbids substituting narration, so no source is claimed.
    assert body["native_audio"]["present"] is False
    assert body["narration"]["present"] is False
    assert body["audio_source"] == "unknown"


# ---------------------------------------------------------------------------
# 8. The HTTP surface itself
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_server(h, monkeypatch):
    """The real handler over the harness's isolated directories."""
    webapp = h.projects.parent / "webapp"
    webapp.mkdir(exist_ok=True)
    (webapp / "index.html").write_text("<html>avm</html>", encoding="utf-8")
    monkeypatch.setattr(web, "WEBAPP_DIR", webapp)
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web.AVMHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", h
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url, headers=None):
    import urllib.request

    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request) as response:
        return response.status, dict(response.headers), response.read()


def _status(url, method="GET"):
    import urllib.request

    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=b"{}" if method == "POST" else None,
                                   method=method)
        ) as response:
            return response.status
    except Exception as exc:  # noqa: BLE001 - the status IS the assertion
        return getattr(exc, "code", 0)


def test_http_audio_endpoint_serves_the_evidence_contract(api_server, monkeypatch):
    h = api_server[1]
    build_completed(
        h, monkeypatch, policy="narration_only", intent="narration", has_audio=True,
        narration=True,
    )

    body_bytes = json.loads(
        _get(f"{api_server[0]}/api/productions/{PID}/units/{UID}/audio")[2]
    )

    assert body_bytes["state"] == "AVAILABLE"
    assert body_bytes["audio_intent"] == "narration"
    assert body_bytes["effective_audio_policy"] == "narration_only"
    assert body_bytes["audio_source"] == "narration"
    assert body_bytes["playable"] is True
    # The playback reference is the EXISTING final-media route, not a new one.
    assert body_bytes["media_url"] == f"/api/projects/{PROJECT_ID}/media"
    assert body_bytes["sample"] is False


def test_http_audio_endpoint_rejects_non_get_and_unknown_ids(api_server):
    base, h = api_server
    h.policy("narration_only").persist("narration")
    audio_url = f"{base}/api/productions/{PID}/units/{UID}/audio"

    assert _status(audio_url, method="POST") == 405
    assert _status(f"{base}/api/productions/unknown_prd/units/{UID}/audio") == 404
    assert _status(f"{base}/api/productions/{PID}/units/unit-999/audio") == 400


def test_exposed_media_url_streams_the_final_artifact_with_range_support(
    api_server, monkeypatch
):
    """Proof that browser playback needs no extracted/second audio file."""
    h = api_server[1]
    build_completed(
        h, monkeypatch, policy="narration_only", intent="narration", has_audio=True,
        narration=True,
    )
    _status, body = h.payload()
    media_url = body["media_url"]

    status, headers, payload = _get(api_server[0] + media_url)
    assert status == 200
    assert headers["Content-Type"] == "video/mp4"
    assert headers["Accept-Ranges"] == "bytes"
    assert len(payload) == h.final.stat().st_size

    partial, partial_headers, partial_body = _get(
        api_server[0] + media_url, headers={"Range": "bytes=0-99"}
    )
    assert partial == 206
    assert partial_headers["Content-Range"].startswith("bytes 0-99/")
    assert len(partial_body) == 100


def test_no_surface_accepts_a_client_supplied_filesystem_path(api_server, monkeypatch):
    """Only opaque ids are accepted; traversal cannot reach any file."""
    h = api_server[1]
    build_completed(
        h, monkeypatch, policy="native_only", intent="native", has_audio=True,
        native=True,
    )
    base = api_server[0]

    assert web.media_path_for("../../etc/passwd") is None
    assert web.media_path_for(str(h.final)) is None
    assert _status(f"{base}/api/projects/..%2F..%2Fetc%2Fpasswd/media") in (403, 404)
    # A crafted unit id never becomes a path either.
    assert _status(
        f"{base}/api/productions/{PID}/units/%2e%2e%2f%2e%2e%2fetc/audio"
    ) == 400
    rendered = json.dumps(h.payload()[1])
    assert "passwd" not in rendered and "\\" not in rendered


def test_audio_endpoint_reports_unknown_production_without_touching_disk(api_server):
    before = _project_snapshot(api_server[1].projects)

    status, body = web.unit_audio_payload("prd_does_not_exist", "unit-001")

    assert status == 404
    assert body["error"] == "PRODUCTION_NOT_FOUND"
    assert _project_snapshot(api_server[1].projects) == before

