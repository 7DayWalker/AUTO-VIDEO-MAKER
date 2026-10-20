"""V1 AUDIO POLICY / NARRATION EXECUTION FIX — focused regression tests.

These tests pin the corrected audio semantics end to end:

    production audio intent  ->  effective audio policy  ->  VOICE/TTS gating
                               ->  assembly track selection  ->  final artifact
                               ->  reconciliation / manifest evidence

They are deterministic and hermetic: no visual provider, no voice provider, no
network, and no billable call is ever made.  Media fixtures are generated
locally with FFmpeg (the same convention as ``tests/test_audio_contract.py``).
"""

import json
from pathlib import Path

import pytest

from app.assembly.audio_contract import (
    AudioMediaFacts,
    AudioPolicy,
    audio_policy_requires_narration,
    audio_selection_warnings,
    normalize_audio_policy,
    select_audio_inputs,
)
from app.assembly.models import AssemblyProject, Scene, VideoResolution
from app.assembly.pipeline import assemble_project
from app.content.models import ProductionPlan
from app.orchestrator.models import (
    OrchestratorConfig,
    PipelineStage,
    ProjectManifest,
    StageResult,
    StageStatus,
)
from app.orchestrator.pipeline import ProductionOrchestrator
from app.orchestrator.production_execution import (
    AUDIO_INTENT_TO_POLICY,
    AUDIO_SOURCE_DEFAULT,
    AUDIO_SOURCE_EXPLICIT,
    apply_production_route,
    compile_audio_policy,
)
from app.orchestrator.reconciliation import (
    ArtifactStatus,
    ProjectVerdict,
    _final_audio_required,
    _native_audio_observed,
    _narration_required,
    _voice_evidence,
    apply_reconciliation,
)
from app.production.models import (
    AUDIO_INTENTS,
    DEFAULT_AUDIO_INTENT,
    Production,
    ProductionCommand,
    ProductionUnit,
    normalize_audio_intent,
)
from app.production.routes import resolve_effective_route
from app.production.storage import load_production
from app.production.storage import load_production


PID = "prd_20260926_audio_intent_001"
CREATED = "2026-09-26T00:00:00"
WIDTH = 160
HEIGHT = 288


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def make_production(*, audio_intent=None, route=None, strategy="local") -> Production:
    """A minimal durable production intent, optionally declaring audio intent."""
    values = dict(
        production_id=PID,
        title="Audio intent production",
        command=ProductionCommand(
            text="Create one video.",
            workload_shape="single_video",
            intended_unit_count=1,
            issued_at=CREATED,
        ),
        units=[
            ProductionUnit(
                unit_id="unit-001",
                ordinal=1,
                unit_type="standalone_video",
                title="Unit 001",
                content_intent={"target_duration": 10, "number_of_scenes": 2},
            )
        ],
        created_at=CREATED,
        generation_strategy=strategy,
    )
    if route is not None:
        values["route"] = route
    if audio_intent is not None:
        values["audio_intent"] = audio_intent
    return Production(**values)


def make_plan(*, scenes: int = 2, narration: str = "Spoken narration text.") -> ProductionPlan:
    """A plan whose scenes ALL carry narration text.

    Narration text is creative plan content.  Under the corrected contract it
    must never be the trigger for narrator/TTS generation.
    """
    return ProductionPlan(
        project_id="20260926_audio_intent_plan_001",
        title="Audio intent plan",
        hook="Hook",
        script=" ".join([narration] * scenes),
        call_to_action=None,
        estimated_duration=scenes * 5,
        scenes=[
            {
                "id": f"scene_{index:02d}",
                "sequence": index,
                "narration": narration,
                "visual_prompt": f"Visual prompt {index}",
                "estimated_duration": 5,
                "purpose": "fact",
            }
            for index in range(1, scenes + 1)
        ],
    )


def make_manifest(**overrides) -> ProjectManifest:
    manifest = ProjectManifest(
        project_id=overrides.pop("project_id", "audio_intent_project"),
        topic="Audio intent policy",
        **overrides,
    )
    manifest.initialize_stages()
    return manifest


def _run_ffmpeg(args):
    import subprocess

    from app.assembly.ffmpeg import resolve_ffmpeg

    subprocess.run(
        [resolve_ffmpeg() or "ffmpeg", "-y", *args],
        capture_output=True,
        check=True,
        timeout=120,
    )


def make_video(path: Path, *, duration=2.0, with_audio=True, frequency=440) -> Path:
    """Small 9:16 MP4 with optional real (non-synthetic) native AAC audio."""
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["-f", "lavfi", "-i", f"color=c=blue:s={WIDTH}x{HEIGHT}:d={duration}:r=30"]
    if with_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    args += (
        ["-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest"] if with_audio else ["-an"]
    )
    args += [str(path)]
    _run_ffmpeg(args)
    return path


def make_audio(path: Path, *, duration=2.0, frequency=880) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg([
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", str(path),
    ])
    return path


def probe_streams(path: Path):
    import subprocess

    from app.assembly.ffmpeg import resolve_ffprobe

    result = subprocess.run(
        [
            resolve_ffprobe() or "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)


def audio_stream_count(path: Path) -> int:
    return len([s for s in probe_streams(path)["streams"] if s["codec_type"] == "audio"])


def video_stream_count(path: Path) -> int:
    return len([s for s in probe_streams(path)["streams"] if s["codec_type"] == "video"])


def make_assembly_project(tmp_path: Path, scenes, *, voiceover=None, music=None, policy):
    return AssemblyProject(
        project_id="audio_intent",
        title="Audio intent assembly",
        aspect_ratio="9:16",
        resolution=VideoResolution(width=WIDTH, height=HEIGHT),
        scenes=[Scene(id=path.stem, video=str(path)) for path in scenes],
        voiceover=str(voiceover) if voiceover else None,
        background_music=str(music) if music else None,
        audio_policy=policy,
        output={"filename": "final.mp4"},
    )



# ---------------------------------------------------------------------------
# 1. Explicit audio intent (distinct from Scene.narration)
# ---------------------------------------------------------------------------


def test_audio_intent_vocabulary_is_explicit():
    assert AUDIO_INTENTS == ("narration", "native", "native_plus_narration", "none")
    assert DEFAULT_AUDIO_INTENT == "narration"


def test_production_can_express_each_audio_intent():
    for intent in AUDIO_INTENTS:
        assert make_production(audio_intent=intent).audio_intent == intent


def test_undeclared_audio_intent_is_none_and_never_mutates_intent():
    production = make_production()
    assert production.audio_intent is None
    # The default is applied at the execution seam, never by rewriting intent.
    assert normalize_audio_intent(production.audio_intent) == DEFAULT_AUDIO_INTENT


def test_unknown_audio_intent_fails_loudly():
    with pytest.raises(Exception) as excinfo:
        make_production(audio_intent="narrator_please")
    assert "audio_intent" in str(excinfo.value)


def test_historical_production_record_without_the_field_still_loads():
    """Backward compatibility: a persisted pre-fix artifact stays readable."""
    legacy = {
        "schema": "avm.production",
        "schema_version": 1,
        "production_id": PID,
        "title": "Legacy production",
        "command": {
            "text": "Create one video.",
            "workload_shape": "single_video",
            "intended_unit_count": 1,
            "issued_at": CREATED,
        },
        "generation_strategy": "local",
        "units": [
            {
                "unit_id": "unit-001",
                "ordinal": 1,
                "unit_type": "standalone_video",
                "title": "Unit 001",
                "content_intent": {"target_duration": 10, "number_of_scenes": 2},
            }
        ],
        "created_at": CREATED,
    }
    production = Production.model_validate(legacy)
    assert production.audio_intent is None
    assert compile_audio_policy(production).policy == AudioPolicy.NARRATION_ONLY


# ---------------------------------------------------------------------------
# 2. Compile seam: intent -> effective policy (route-independent)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "expected_policy"),
    [
        ("narration", AudioPolicy.NARRATION_ONLY),
        ("native", AudioPolicy.NATIVE_ONLY),
        ("native_plus_narration", AudioPolicy.NATIVE_PLUS_NARRATION),
        ("none", AudioPolicy.NONE),
    ],
)
def test_requested_intent_reaches_effective_policy(intent, expected_policy):
    compilation = compile_audio_policy(make_production(audio_intent=intent))
    assert compilation.intent == intent
    assert compilation.policy == expected_policy
    assert compilation.source == AUDIO_SOURCE_EXPLICIT
    assert compilation.declared == intent
    assert AUDIO_INTENT_TO_POLICY[intent] == expected_policy


def test_intent_to_policy_mapping_is_total():
    assert set(AUDIO_INTENT_TO_POLICY) == set(AUDIO_INTENTS)


def test_undeclared_intent_compiles_to_documented_default():
    compilation = compile_audio_policy(make_production())
    assert compilation.intent == DEFAULT_AUDIO_INTENT
    assert compilation.policy == AudioPolicy.NARRATION_ONLY
    assert compilation.source == AUDIO_SOURCE_DEFAULT
    assert compilation.declared is None


def test_scene_narration_text_never_changes_the_compiled_policy():
    """``Scene.narration`` existing is NOT the audio intent."""
    plan = make_plan(scenes=3)
    assert all(scene.narration.strip() for scene in plan.scenes)
    for intent in AUDIO_INTENTS:
        compiled = compile_audio_policy(make_production(audio_intent=intent))
        assert compiled.policy == AUDIO_INTENT_TO_POLICY[intent]


@pytest.mark.parametrize(
    ("route", "strategy"),
    [
        ("local_ai_assisted", "local"),
        ("full_ai_video", "alibaba_video"),
    ],
)
def test_route_compilation_never_reads_or_writes_audio_policy(route, strategy):
    """ROUTE != AUDIO: there is no route-specific audio hack."""
    config = OrchestratorConfig(
        topic="Route independence",
        flow_project_url="",
        visual_mode="local",
        audio_policy=AudioPolicy.NONE,
        audio_intent="none",
    )
    effective = resolve_effective_route(route=route, generation_strategy=strategy)

    apply_production_route(config, effective_route=effective, plan=make_plan())

    assert config.audio_policy == AudioPolicy.NONE
    assert config.audio_intent == "none"


def test_narration_requirement_predicate_follows_the_policy():
    assert audio_policy_requires_narration(AudioPolicy.NARRATION_ONLY) is True
    assert audio_policy_requires_narration(AudioPolicy.NATIVE_PLUS_NARRATION) is True
    assert audio_policy_requires_narration(AudioPolicy.NATIVE_ONLY) is False
    assert audio_policy_requires_narration(AudioPolicy.NONE) is False
    # Legacy/absent policy keeps the historical narrated behavior.


# ---------------------------------------------------------------------------
# 3. Conditional VOICE stage (TTS is no longer unconditional)
# ---------------------------------------------------------------------------


def run_voice_gate(tmp_path, monkeypatch, *, audio_policy, audio_intent=None):
    """Execute the pipeline's VOICE gate with every provider stubbed out.

    ``voice_only`` makes the run terminal immediately after the VOICE decision,
    so no scene provider, browser, or network seam can be reached.  The stubbed
    voice stage only counts: reaching it proves the gate OPENED, and no real
    ``VoiceGenerator``/TTS provider is ever constructed.
    """
    monkeypatch.setattr("app.orchestrator.pipeline.ORCHESTRATOR_OUTPUT_DIR", tmp_path)
    config = OrchestratorConfig(
        topic="Audio intent policy",
        flow_project_url="",
        visual_mode="local",
        voice_only=True,
        skip_confirmation=True,
        audio_policy=audio_policy,
        audio_intent=audio_intent,
    )
    orchestrator = ProductionOrchestrator(config)
    calls = {"content": 0, "voice": 0}

    def content_stage():
        calls["content"] += 1
        plan_path = orchestrator.project_dir / "production_plan.json"
        plan_path.write_text(
            make_plan(scenes=2).model_dump_json(), encoding="utf-8"
        )
        orchestrator.manifest.content_plan_path = str(plan_path)
        orchestrator.manifest.set_stage_result(
            StageResult.completed(PipelineStage.CONTENT, output_path=str(plan_path))
        )

    def voice_stage():
        calls["voice"] += 1
        orchestrator.manifest.voice_audio_path = "voice.mp3"
        orchestrator.manifest.set_stage_result(
            StageResult.completed(PipelineStage.VOICE, output_path="voice.mp3")
        )

    monkeypatch.setattr(orchestrator, "_execute_content_stage", content_stage)
    monkeypatch.setattr(orchestrator, "_execute_voice_stage", voice_stage)
    manifest = orchestrator.run()
    return orchestrator, calls, manifest


def test_narration_intent_executes_tts(tmp_path, monkeypatch):
    _, calls, manifest = run_voice_gate(
        tmp_path, monkeypatch,
        audio_policy=AudioPolicy.NARRATION_ONLY, audio_intent="narration",
    )
    assert calls["voice"] == 1
    assert manifest.get_stage_result(PipelineStage.VOICE).status == StageStatus.COMPLETED
    assert manifest.voice_audio_path == "voice.mp3"


def test_native_plus_narration_intent_executes_tts(tmp_path, monkeypatch):
    _, calls, manifest = run_voice_gate(
        tmp_path, monkeypatch,
        audio_policy=AudioPolicy.NATIVE_PLUS_NARRATION,
        audio_intent="native_plus_narration",
    )
    assert calls["voice"] == 1
    assert manifest.get_stage_result(PipelineStage.VOICE).status == StageStatus.COMPLETED


@pytest.mark.parametrize(
    ("policy", "intent"),
    [
        (AudioPolicy.NATIVE_ONLY, "native"),
        (AudioPolicy.NONE, "none"),
    ],
)
def test_native_and_none_intents_never_execute_tts(tmp_path, monkeypatch, policy, intent):
    _, calls, manifest = run_voice_gate(
        tmp_path, monkeypatch, audio_policy=policy, audio_intent=intent
    )
    voice = manifest.get_stage_result(PipelineStage.VOICE)
    assert calls["voice"] == 0
    assert voice.status == StageStatus.SKIPPED
    # A skipped stage is intentional, never a failure, and claims no artifact.
    assert voice.status != StageStatus.FAILED
    assert voice.error is None
    assert manifest.voice_audio_path is None
    assert intent in voice.metadata["skip_reason"]
    assert policy.value in voice.metadata["skip_reason"]


def test_narration_text_in_the_plan_does_not_trigger_tts(tmp_path, monkeypatch):
    """A plan full of ``Scene.narration`` text still produces no TTS."""
    orchestrator, calls, manifest = run_voice_gate(
        tmp_path, monkeypatch, audio_policy=AudioPolicy.NATIVE_ONLY, audio_intent="native"
    )
    plan = ProductionPlan.model_validate_json(
        Path(manifest.content_plan_path).read_text(encoding="utf-8")
    )
    assert all(scene.narration.strip() for scene in plan.scenes)
    assert calls["voice"] == 0
    assert orchestrator.project_dir is not None


def test_audio_intent_and_policy_are_persisted_as_evidence(tmp_path, monkeypatch):
    orchestrator, _, manifest = run_voice_gate(
        tmp_path, monkeypatch, audio_policy=AudioPolicy.NONE, audio_intent="none"
    )
    assert manifest.assembly_config["audio_policy"] == "none"
    assert manifest.assembly_config["audio_intent"] == "none"
    persisted = json.loads(
        (orchestrator.project_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert persisted["assembly_config"]["audio_policy"] == "none"
    assert persisted["assembly_config"]["audio_intent"] == "none"
    assert persisted["stages"]["voice"]["status"] == "skipped"


def test_undeclared_intent_keeps_legacy_narrated_behavior(tmp_path, monkeypatch):
    _, calls, manifest = run_voice_gate(
        tmp_path, monkeypatch, audio_policy=AudioPolicy.NARRATION_ONLY
    )
    assert calls["voice"] == 1
    assert manifest.assembly_config["audio_intent"] is None
    assert manifest.assembly_config["audio_policy"] == "narration_only"

    assert audio_policy_requires_narration(None) is True
    assert normalize_audio_policy("none") == AudioPolicy.NONE



# ---------------------------------------------------------------------------
# 4. Resume: the persisted policy is authoritative; a skip never becomes TTS
# ---------------------------------------------------------------------------


def seed_project(tmp_path, project_id, *, assembly_config=None, voice_result=None):
    """Create a resumable project directory with a persisted manifest."""
    project_dir = tmp_path / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    manifest = ProjectManifest(
        project_id=project_id,
        topic="Audio intent policy",
        current_stage=PipelineStage.VOICE,
        content_plan_path=str(project_dir / "production_plan.json"),
        assembly_config=assembly_config or {},
    )
    manifest.initialize_stages()
    manifest.set_stage_result(
        StageResult.completed(PipelineStage.CONTENT, output_path=manifest.content_plan_path)
    )
    (project_dir / "production_plan.json").write_text(
        make_plan(scenes=2).model_dump_json(), encoding="utf-8"
    )
    if voice_result is not None:
        manifest.set_stage_result(voice_result)
    (project_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict()), encoding="utf-8"
    )
    return project_dir, manifest


def resume_orchestrator(tmp_path, monkeypatch, project_id, **config_overrides):
    monkeypatch.setattr("app.orchestrator.pipeline.ORCHESTRATOR_OUTPUT_DIR", tmp_path)
    config = OrchestratorConfig(
        topic="Audio intent policy",
        flow_project_url="",
        visual_mode="local",
        resume_project_id=project_id,
        voice_only=True,
        skip_confirmation=True,
        **config_overrides,
    )
    orchestrator = ProductionOrchestrator(config)
    calls = {"voice": 0}

    def voice_stage():
        calls["voice"] += 1

    monkeypatch.setattr(orchestrator, "_execute_voice_stage", voice_stage)
    monkeypatch.setattr(orchestrator, "_execute_content_stage", lambda: None)
    return orchestrator, calls


def test_resume_with_native_policy_never_re_enters_tts(tmp_path, monkeypatch):
    project_id = "resume_native"
    seed_project(
        tmp_path,
        project_id,
        assembly_config={
            "audio_policy": AudioPolicy.NATIVE_ONLY.value,
            "audio_intent": "native",
            "audio_intent_source": AUDIO_SOURCE_EXPLICIT,
        },
        voice_result=StageResult.skipped(
            PipelineStage.VOICE, reason="Narrator/TTS intentionally not required"
        ),
    )
    orchestrator, calls = resume_orchestrator(tmp_path, monkeypatch, project_id)

    manifest = orchestrator.run()

    # The persisted snapshot wins over any runtime default.
    assert orchestrator.config.audio_policy == AudioPolicy.NATIVE_ONLY
    assert orchestrator.config.audio_intent == "native"
    assert calls["voice"] == 0
    voice = manifest.get_stage_result(PipelineStage.VOICE)
    assert voice.status == StageStatus.SKIPPED
    assert voice.error is None


def test_resume_of_legacy_manifest_keeps_narrated_behavior(tmp_path, monkeypatch):
    """A pre-fix manifest (no audio snapshot) still requires narration."""
    project_id = "resume_legacy"
    seed_project(tmp_path, project_id, assembly_config={})
    orchestrator, calls = resume_orchestrator(tmp_path, monkeypatch, project_id)

    orchestrator.run()

    assert orchestrator.config.audio_policy == AudioPolicy.NARRATION_ONLY
    assert orchestrator.config.audio_intent is None
    assert calls["voice"] == 1


def test_skipped_voice_is_distinguishable_from_failed_and_completed():
    manifest = make_manifest()
    manifest.set_stage_result(
        StageResult.skipped(PipelineStage.VOICE, reason="not required by policy")
    )
    skipped = manifest.get_stage_result(PipelineStage.VOICE)
    assert skipped.status == StageStatus.SKIPPED
    assert skipped.error is None
    assert skipped.metadata["skip_reason"] == "not required by policy"
    assert manifest.is_stage_completed(PipelineStage.VOICE) is False
    assert manifest.is_stage_failed(PipelineStage.VOICE) is False

    manifest.set_stage_result(StageResult.failed(PipelineStage.VOICE, error="tts exploded"))
    failed = manifest.get_stage_result(PipelineStage.VOICE)
    assert failed.status == StageStatus.FAILED
    assert failed.error == "tts exploded"

    manifest.set_stage_result(StageResult.completed(PipelineStage.VOICE, output_path="v.mp3"))
    assert manifest.get_stage_result(PipelineStage.VOICE).status == StageStatus.COMPLETED
    assert manifest.is_stage_completed(PipelineStage.VOICE) is True



# ---------------------------------------------------------------------------
# 5. Captions never block a production that intentionally has no narration
# ---------------------------------------------------------------------------


def captions_orchestrator(tmp_path, monkeypatch, project_id, **overrides):
    monkeypatch.setattr("app.orchestrator.pipeline.ORCHESTRATOR_OUTPUT_DIR", tmp_path)
    orchestrator = ProductionOrchestrator(
        OrchestratorConfig(
            topic="Audio intent policy",
            flow_project_url="",
            visual_mode="local",
            resume_project_id=project_id,
            skip_confirmation=True,
            **overrides,
        )
    )
    orchestrator._resume_project(project_id)
    return orchestrator


@pytest.mark.parametrize(
    ("policy", "intent"),
    [
        (AudioPolicy.NATIVE_ONLY, "native"),
        (AudioPolicy.NONE, "none"),
    ],
)
def test_captions_do_not_fail_without_voice_timing(tmp_path, monkeypatch, policy, intent):
    project_id = f"captions_{intent}"
    seed_project(
        tmp_path,
        project_id,
        assembly_config={"audio_policy": policy.value, "audio_intent": intent},
    )
    orchestrator = captions_orchestrator(
        tmp_path, monkeypatch, project_id,
        audio_policy=policy, audio_intent=intent, enable_captions=True,
    )
    assert orchestrator.manifest.voice_timing_path is None

    orchestrator._execute_captions_stage()  # must NOT raise

    result = orchestrator.manifest.get_stage_result(PipelineStage.CAPTIONS)
    assert result.status == StageStatus.SKIPPED
    assert result.error is None
    assert "Narration intentionally not required" in result.metadata["skip_reason"]
    assert policy.value in result.metadata["skip_reason"]


def test_captions_for_narrated_production_keep_existing_behavior(tmp_path, monkeypatch):
    project_id = "captions_narration"
    seed_project(
        tmp_path,
        project_id,
        assembly_config={
            "audio_policy": AudioPolicy.NARRATION_ONLY.value,
            "audio_intent": "narration",
        },
    )
    orchestrator = captions_orchestrator(
        tmp_path, monkeypatch, project_id,
        audio_policy=AudioPolicy.NARRATION_ONLY, enable_captions=True,
    )

    orchestrator._execute_captions_stage()  # must NOT raise

    result = orchestrator.manifest.get_stage_result(PipelineStage.CAPTIONS)
    assert result.status == StageStatus.SKIPPED
    assert result.metadata["skip_reason"] == "Voice timing not available"



# ---------------------------------------------------------------------------
# 6. Assembly obeys the effective policy (real FFmpeg, no providers)
# ---------------------------------------------------------------------------


def test_native_policy_preserves_native_scene_audio(tmp_path):
    """No narrator track exists at all, so any output audio IS the native audio."""
    scene = make_video(tmp_path / "native.mp4", duration=2.0, with_audio=True)

    result = assemble_project(
        make_assembly_project(tmp_path, [scene], policy=AudioPolicy.NATIVE_ONLY),
        base_dir=tmp_path,
        output_dir=tmp_path / "final",
    )

    assert result.success is True, result.errors
    assert audio_stream_count(Path(result.output_path)) == 1
    assert video_stream_count(Path(result.output_path)) == 1
    assert not any("narrator/TTS fallback" in w for w in result.warnings)


def test_native_policy_without_native_audio_has_no_narrator_audio(tmp_path):
    scene = make_video(tmp_path / "silent.mp4", duration=2.0, with_audio=False)
    voice = make_audio(tmp_path / "voice.m4a", duration=2.0)

    result = assemble_project(
        make_assembly_project(
            tmp_path, [scene], voiceover=voice, policy=AudioPolicy.NATIVE_ONLY
        ),
        base_dir=tmp_path,
        output_dir=tmp_path / "final",
    )

    assert result.success is True, result.errors
    assert audio_stream_count(Path(result.output_path)) == 0
    assert any("forbids a narrator/TTS fallback" in w for w in result.warnings)


def test_none_policy_final_output_has_no_audio_stream(tmp_path):
    scene = make_video(tmp_path / "native.mp4", duration=2.0, with_audio=True)
    voice = make_audio(tmp_path / "voice.m4a", duration=2.0)
    music = make_audio(tmp_path / "music.m4a", duration=2.0, frequency=220)

    result = assemble_project(
        make_assembly_project(
            tmp_path, [scene], voiceover=voice, music=music, policy=AudioPolicy.NONE
        ),
        base_dir=tmp_path,
        output_dir=tmp_path / "final",
    )

    assert result.success is True, result.errors
    assert audio_stream_count(Path(result.output_path)) == 0
    assert video_stream_count(Path(result.output_path)) == 1
    assert any("no audio stream" in w for w in result.warnings)


def test_native_plus_narration_mixes_both_tracks(tmp_path):
    scene = make_video(tmp_path / "native.mp4", duration=2.0, with_audio=True)
    voice = make_audio(tmp_path / "voice.m4a", duration=2.0)

    result = assemble_project(
        make_assembly_project(
            tmp_path, [scene], voiceover=voice,
            policy=AudioPolicy.NATIVE_PLUS_NARRATION,
        ),
        base_dir=tmp_path,
        output_dir=tmp_path / "final",
    )

    assert result.success is True, result.errors
    assert audio_stream_count(Path(result.output_path)) == 1
    assert not any("unavailable" in w for w in result.warnings)


def test_native_plus_narration_without_native_uses_narration_alone(tmp_path):
    scene = make_video(tmp_path / "silent.mp4", duration=2.0, with_audio=False)
    voice = make_audio(tmp_path / "voice.m4a", duration=2.0)

    result = assemble_project(
        make_assembly_project(
            tmp_path, [scene], voiceover=voice,
            policy=AudioPolicy.NATIVE_PLUS_NARRATION,
        ),
        base_dir=tmp_path,
        output_dir=tmp_path / "final",
    )

    assert result.success is True, result.errors
    assert audio_stream_count(Path(result.output_path)) == 1
    assert any("narration is used alone" in w for w in result.warnings)


def test_narration_policy_still_suppresses_native_audio(tmp_path):
    """Existing narration-only behavior remains available when requested."""
    scene = make_video(tmp_path / "native.mp4", duration=2.0, with_audio=True)
    voice = make_audio(tmp_path / "voice.m4a", duration=2.0)

    result = assemble_project(
        make_assembly_project(
            tmp_path, [scene], voiceover=voice, policy=AudioPolicy.NARRATION_ONLY
        ),
        base_dir=tmp_path,
        output_dir=tmp_path / "final",
    )

    assert result.success is True, result.errors
    assert audio_stream_count(Path(result.output_path)) == 1


def test_audio_selection_warnings_are_single_sourced():
    silent_native = select_audio_inputs(
        AudioPolicy.NATIVE_ONLY, scene_native_audio=None,
        narration=Path("voice.m4a"), music=None,
    )
    assert audio_selection_warnings(silent_native) == [
        "Native audio was unavailable; audio intent=native forbids a "
        "narrator/TTS fallback, so the final video carries no audio stream."
    ]
    assert silent_native.fallback_used is False



# ---------------------------------------------------------------------------
# 7. Reconciliation, native-audio evidence, and synthetic silence
# ---------------------------------------------------------------------------


def test_voice_evidence_is_not_required_when_narration_is_not_required(tmp_path):
    for policy in (AudioPolicy.NATIVE_ONLY, AudioPolicy.NONE):
        manifest = make_manifest(assembly_config={"audio_policy": policy.value})
        evidence = _voice_evidence(manifest, tmp_path, scene_count=2)
        assert evidence["status"] == ArtifactStatus.NOT_REQUIRED.value, policy
        assert _narration_required(manifest) is False


def test_voice_evidence_still_required_for_narrated_and_legacy_projects(tmp_path):
    narrated = make_manifest(
        assembly_config={"audio_policy": AudioPolicy.NARRATION_ONLY.value}
    )
    legacy = make_manifest(assembly_config={})
    for manifest in (narrated, legacy):
        assert _narration_required(manifest) is True
        assert (
            _voice_evidence(manifest, tmp_path, scene_count=2)["status"]
            == ArtifactStatus.MISSING.value
        )


def test_leftover_narrator_artifact_is_never_promoted_into_required_evidence(tmp_path):
    """A stale voiceover file cannot make a native/none project 'need' TTS."""
    voice = make_audio(tmp_path / "voice.m4a", duration=1.0)
    manifest = make_manifest(
        assembly_config={"audio_policy": AudioPolicy.NONE.value},
        voice_audio_path=str(voice),
    )
    evidence = _voice_evidence(manifest, tmp_path, scene_count=2)
    assert evidence["status"] == ArtifactStatus.NOT_REQUIRED.value


@pytest.mark.parametrize(
    ("policy", "facts", "expected"),
    [
        (AudioPolicy.NONE, None, False),
        (AudioPolicy.NONE, {"has_audio": True, "is_synthetic": False}, False),
        (AudioPolicy.NATIVE_ONLY, None, False),
        (AudioPolicy.NATIVE_ONLY, {"has_audio": False}, False),
        (AudioPolicy.NATIVE_ONLY, {"has_audio": True, "is_synthetic": True}, False),
        (AudioPolicy.NATIVE_ONLY, {"has_audio": True, "is_synthetic": False}, True),
        (AudioPolicy.NARRATION_ONLY, None, True),
        (AudioPolicy.NATIVE_PLUS_NARRATION, None, True),
    ],
)
def test_final_audio_requirement_follows_policy_and_real_media_evidence(
    policy, facts, expected
):
    scene_checkpoints = (
        {"scene_01": {"audio_facts": facts}} if facts is not None else {}
    )
    manifest = make_manifest(
        assembly_config={"audio_policy": policy.value},
        scene_checkpoints=scene_checkpoints,
    )
    assert _final_audio_required(manifest) is expected


def test_synthetic_silence_is_not_valid_native_audio():
    facts = AudioMediaFacts(has_audio=True, audio_codec="aac", is_synthetic=True)
    assert facts.has_audio is True
    assert facts.is_synthetic is True
    assert facts.has_native_audio is False

    manifest = make_manifest(
        assembly_config={"audio_policy": AudioPolicy.NATIVE_ONLY.value},
        scene_checkpoints={"scene_01": {"audio_facts": facts.model_dump()}},
    )
    assert _native_audio_observed(manifest) is False
    assert _final_audio_required(manifest) is False

    # Synthetic silence is therefore never selected as native scene audio.
    selection = select_audio_inputs(
        AudioPolicy.NATIVE_ONLY, scene_native_audio=None, narration=None, music=None
    )
    assert selection.scene_native_audio is None
    assert selection.narration is None


def test_native_audio_evidence_is_read_from_validation_block_too():
    facts = {"has_audio": True, "is_synthetic": False}
    manifest = make_manifest(
        assembly_config={"audio_policy": AudioPolicy.NATIVE_ONLY.value},
        scene_checkpoints={"scene_01": {"validation": {"audio_facts": facts}}},
    )
    assert _native_audio_observed(manifest) is True
    assert _final_audio_required(manifest) is True


def test_apply_reconciliation_marks_unrequired_voice_skipped_not_failed():
    from app.orchestrator.reconciliation import ReconciliationReport

    manifest = make_manifest(assembly_config={"audio_policy": AudioPolicy.NONE.value})
    report = ReconciliationReport(
        project_id=manifest.project_id,
        content={"status": ArtifactStatus.VALID.value, "path": "plan.json"},
        voice={"status": ArtifactStatus.NOT_REQUIRED.value},
        scenes={"status": ArtifactStatus.VALID.value, "items": []},
        captions={"status": ArtifactStatus.NOT_REQUIRED.value},
        assembly={"status": ArtifactStatus.VALID.value, "path": "final.mp4"},
        verdict=ProjectVerdict.PROJECT_COMPLETE,
        reasons=[],
    )

    apply_reconciliation(manifest, report)

    voice = manifest.get_stage_result(PipelineStage.VOICE)
    assert voice.status == StageStatus.SKIPPED
    assert voice.error is None
    assert manifest.is_stage_failed(PipelineStage.VOICE) is False


def test_assembly_evidence_accepts_an_intentionally_silent_final_artifact(tmp_path):
    from app.orchestrator.reconciliation import _assembly_evidence

    scene = make_video(tmp_path / "silent.mp4", duration=1.0, with_audio=False)
    result = assemble_project(
        make_assembly_project(tmp_path, [scene], policy=AudioPolicy.NONE),
        base_dir=tmp_path,
        output_dir=tmp_path / "final",
    )
    assert result.success is True, result.errors

    manifest = make_manifest(
        project_id="silent_final",
        assembly_config={
            "audio_policy": AudioPolicy.NONE.value,
            "resolution_width": WIDTH,
            "resolution_height": HEIGHT,
        },
        final_video_path=result.output_path,
    )
    evidence = _assembly_evidence(manifest, tmp_path)

    assert evidence["status"] == ArtifactStatus.VALID.value, evidence
    assert evidence["validation"]["has_audio"] is False
    assert evidence["validation"]["audio_required"] is False
    assert evidence["validation"]["audio_policy"] == "none"



# ---------------------------------------------------------------------------
# 8. Creation contract: backward compatible, optional audio intent
# ---------------------------------------------------------------------------


def _creation_request(**overrides):
    request = {
        "mode": "single_video",
        "creation_method": "local",
        "title": "Audio intent creation test",
        "brief": "A deterministic production used to verify audio intent intake.",
        "output_intent": "youtube_shorts",
        "target_duration": 30,
        "scene_count": 6,
        "tone": "clear and cinematic",
    }
    request.update(overrides)
    return request


def test_existing_creation_request_without_audio_intent_still_works(tmp_path, monkeypatch):
    """A client that never sends the field keeps working (no frontend change)."""
    from app.production.storage import load_production
    from app.web import server as web

    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")

    status, payload = web.create_production_payload(_creation_request())

    assert status == 201
    # Undeclared intent is reported honestly, with the effective default beside it.
    assert payload["audio_intent"] is None
    assert payload["effective_audio_intent"] == DEFAULT_AUDIO_INTENT
    production = load_production(
        payload["production_id"], root=tmp_path / "productions"
    )
    assert production.audio_intent is None
    # And it still compiles to the historical narrated behavior.
    compiled = compile_audio_policy(production)
    assert compiled.policy == AudioPolicy.NARRATION_ONLY
    assert compiled.source == AUDIO_SOURCE_DEFAULT


@pytest.mark.parametrize("intent", list(AUDIO_INTENTS))
def test_creation_request_can_declare_each_audio_intent(tmp_path, monkeypatch, intent):
    from app.production.storage import load_production
    from app.web import server as web

    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")

    status, payload = web.create_production_payload(
        _creation_request(audio_intent=intent)
    )

    assert status == 201
    assert payload["audio_intent"] == intent
    assert payload["effective_audio_intent"] == intent
    production = load_production(
        payload["production_id"], root=tmp_path / "productions"
    )
    assert production.audio_intent == intent
    assert compile_audio_policy(production).policy == AUDIO_INTENT_TO_POLICY[intent]
    assert compile_audio_policy(production).source == AUDIO_SOURCE_EXPLICIT


def test_creation_request_rejects_unknown_audio_intent(tmp_path, monkeypatch):
    from app.web import server as web

    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")

    status, payload = web.create_production_payload(
        _creation_request(audio_intent="narrator_please")
    )

    assert status == 400
    assert payload["error"] == "INVALID_CREATION_REQUEST"
    assert "audio_intent" in payload["message"]
    # Nothing was persisted for a rejected request.
    assert not (tmp_path / "productions").exists() or not list(
        (tmp_path / "productions").iterdir()
    )


def test_declared_intent_survives_persistence_round_trip(tmp_path):
    """Intent -> persisted production.json -> reload -> same compiled policy."""
    from app.production.storage import load_production, save_new_production

    production = make_production(audio_intent="native_plus_narration")
    save_new_production(production, root=tmp_path)

    reloaded = load_production(production.production_id, root=tmp_path)
    assert reloaded.audio_intent == "native_plus_narration"
    assert (
        compile_audio_policy(reloaded).policy
        == AudioPolicy.NATIVE_PLUS_NARRATION
    )


# ---------------------------------------------------------------------------
# 9. End-to-end execution semantics (real executor, offline, no providers)
# ---------------------------------------------------------------------------
#
# These drive the ACTUAL seams: creation -> ProductionExecutor -> route/audio
# compilation -> ProductionOrchestrator -> real ImageMotion renders -> real
# FFmpeg assembly -> reconciliation, with a mock planner and fixture imagery.
# No visual provider, no TTS network call, and no billable request is made.


def _install_offline_intelligence(monkeypatch):
    """Explicit, labelled mock planner (mirrors the existing offline e2e proof)."""

    class _MockProvider:
        name = "mock"
        model = "mock-model"

    class _MockPlanner:
        provider = _MockProvider()

        def __init__(self):
            from app.content.planner import ScenePlanner as _SP

            self._sp = _SP(mock_mode=True)

        def plan(self, brief):
            return self._sp.plan(brief)

    monkeypatch.setattr(
        "app.content.planner.create_scene_planner",
        lambda *, mock_mode=False, output_dir=None: _MockPlanner(),
    )


def run_real_production(tmp_path, monkeypatch, *, audio_intent=None):
    from app.orchestrator import pipeline as pipeline_module
    from app.voice import config as voice_config
    from app.web import server as web

    projects = tmp_path / "projects"
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    monkeypatch.setattr(web, "PROJECTS_DIR", projects)
    monkeypatch.setattr(pipeline_module, "ORCHESTRATOR_OUTPUT_DIR", projects)
    monkeypatch.delenv("AVM_MOCK_MODE", raising=False)
    monkeypatch.setenv("AVM_IMAGE_SOURCE", "fixture")
    # Mock TTS keeps the narrated control offline; the silent-intent runs must
    # never reach it, which is exactly what those tests assert.
    monkeypatch.setattr(voice_config, "TTS_PROVIDER", "mock")
    _install_offline_intelligence(monkeypatch)

    body = {
        "mode": "single_video",
        "creation_method": "local",
        "title": "Audio intent end to end",
        "brief": "A deterministic offline production for audio intent verification.",
        "output_intent": "youtube_shorts",
        # The offline mock planner emits its own fixed 6-scene / ~31s plan, so
        # the brief must ask for the same shape the existing offline e2e proof
        # uses; otherwise plan validation rejects the duration range.
        "target_duration": 30,
        "scene_count": 6,
        "tone": "clear and cinematic",
    }
    if audio_intent is not None:
        body["audio_intent"] = audio_intent

    status, payload = web.create_production_payload(body)
    assert status == 201, payload
    production_id = payload["production_id"]

    executor = web._production_executor(production_id)
    report = executor.execute()

    production = load_production(production_id, root=web.PRODUCTIONS_DIR)
    bound = executor.discover_bindings(production)["unit-001"]
    finals = list(bound.project_dir.rglob("*_final.mp4"))
    assert finals, "no final MP4 artifact was produced"
    return report, production, bound.manifest, finals[0], payload


def test_native_intent_production_never_generates_narrator_audio(tmp_path, monkeypatch):
    report, production, manifest, final, payload = run_real_production(
        tmp_path, monkeypatch, audio_intent="native"
    )

    # The unit COMPLETES: a narration-less production is a valid production.
    assert report.complete is True
    assert production.audio_intent == "native"
    assert payload["audio_intent"] == "native"

    # VOICE was intentionally not required — skipped, never failed, no artifact.
    voice = manifest.get_stage_result(PipelineStage.VOICE)
    assert voice.status == StageStatus.SKIPPED
    assert voice.error is None
    assert manifest.voice_audio_path is None
    assert not list(final.parent.parent.rglob("voiceover.*"))
    assert not list(final.parent.parent.rglob("voice_timing.json"))

    # Captions did not block completion.
    captions = manifest.get_stage_result(PipelineStage.CAPTIONS)
    assert captions.status == StageStatus.SKIPPED
    assert captions.error is None

    # Evidence: requested intent + effective policy + what actually ran.
    assert manifest.assembly_config["audio_intent"] == "native"
    assert manifest.assembly_config["audio_intent_source"] == AUDIO_SOURCE_EXPLICIT
    assert manifest.assembly_config["audio_policy"] == "native_only"
    assembly = manifest.get_stage_result(PipelineStage.ASSEMBLY)
    assert assembly.status == StageStatus.COMPLETED
    audio_evidence = assembly.metadata["audio"]
    assert audio_evidence["voice_stage_status"] == "skipped"
    assert audio_evidence["narration_track_present"] is False
    assert audio_evidence["audio_policy"] == "native_only"

    # The delivered artifact obeys the policy.  Whatever audio it carries comes
    # from the scene renders (native), never from a narrator: on the local
    # route a user/local asset may legitimately contribute real audio, while
    # ImageMotion-only renders contribute synthetic silence and are excluded.
    assert video_stream_count(final) == 1
    if audio_evidence["native_audio_present"]:
        assert audio_evidence["scenes_with_native_audio"]
        assert audio_stream_count(final) == 1
    else:
        assert audio_stream_count(final) == 0


def test_none_intent_production_has_no_audio_at_all(tmp_path, monkeypatch):
    report, production, manifest, final, _ = run_real_production(
        tmp_path, monkeypatch, audio_intent="none"
    )

    assert report.complete is True
    assert production.audio_intent == "none"
    assert manifest.get_stage_result(PipelineStage.VOICE).status == StageStatus.SKIPPED
    assert manifest.assembly_config["audio_policy"] == "none"
    assert video_stream_count(final) == 1
    assert audio_stream_count(final) == 0


def test_undeclared_intent_keeps_the_narrated_v1_behavior(tmp_path, monkeypatch):
    """Backward compatibility, end to end: no field -> narrated production."""
    report, production, manifest, final, payload = run_real_production(
        tmp_path, monkeypatch
    )

    assert report.complete is True
    assert production.audio_intent is None
    assert payload["audio_intent"] is None
    assert payload["effective_audio_intent"] == DEFAULT_AUDIO_INTENT

    voice = manifest.get_stage_result(PipelineStage.VOICE)
    assert voice.status == StageStatus.COMPLETED
    assert manifest.voice_audio_path is not None
    assert manifest.assembly_config["audio_intent_source"] == AUDIO_SOURCE_DEFAULT
    assert manifest.assembly_config["audio_policy"] == "narration_only"

    assert video_stream_count(final) == 1
    assert audio_stream_count(final) == 1


