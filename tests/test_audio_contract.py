"""Phase 7D — explicit audio facts, policy, and deterministic assembly tests.

All media is generated locally with FFmpeg. These tests never call a visual or
voice provider API, launch Flow, or require an external service.
"""

import json
import subprocess
from pathlib import Path

import pytest

from app.assembly.audio_contract import (
    AudioMediaFacts,
    AudioPolicy,
    normalize_audio_policy,
    select_audio_inputs,
)
from app.assembly.media import inspect_audio
from app.assembly.models import AssemblyProject, Scene, VideoResolution
from app.assembly.pipeline import assemble_project
from app.flow.scene_models import FlowGenerationConfig, SceneGenerationRequest
from app.orchestrator.models import OrchestratorConfig, ProjectManifest
from app.orchestrator.pipeline import ProductionOrchestrator
from app.orchestrator.visual_providers import ImageMotionVisualProvider


WIDTH = 160
HEIGHT = 288


def _run_ffmpeg(args):
    from app.assembly.ffmpeg import resolve_ffmpeg

    subprocess.run([resolve_ffmpeg() or "ffmpeg", "-y", *args], capture_output=True, check=True, timeout=120)


def make_video(path: Path, *, duration=2.0, with_audio=True, frequency=440) -> Path:
    """Create a small 9:16 MP4 with optional native AAC audio."""
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-f", "lavfi", "-i", f"color=c=blue:s={WIDTH}x{HEIGHT}:d={duration}:r=30",
    ]
    if with_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if with_audio:
        args += ["-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest"]
    else:
        args += ["-an"]
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


def make_project(tmp_path: Path, scenes, *, voiceover=None, policy=None):
    return AssemblyProject(
        project_id="audio_contract",
        title="Audio contract",
        aspect_ratio="9:16",
        resolution=VideoResolution(width=WIDTH, height=HEIGHT),
        scenes=[Scene(id=path.stem, video=str(path)) for path in scenes],
        voiceover=str(voiceover) if voiceover else None,
        audio_policy=policy or AudioPolicy.NARRATION_ONLY,
        output={"filename": "final.mp4"},
    )


def probe_streams(path: Path):
    from app.assembly.ffmpeg import resolve_ffprobe

    result = subprocess.run(
        [resolve_ffprobe() or "ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)


def test_native_audio_facts_are_detected_with_available_metadata(tmp_path):
    path = make_video(tmp_path / "native.mp4", duration=2.0, with_audio=True)

    facts = inspect_audio(path)

    assert facts.has_audio is True
    assert facts.audio_codec == "aac"
    assert facts.audio_duration is not None
    assert facts.audio_duration > 0
    assert facts.sample_rate == 44100
    assert facts.channels == 2
    assert facts.has_native_audio is True


def test_video_without_audio_is_detected_without_invented_audio_facts(tmp_path):
    path = make_video(tmp_path / "silent.mp4", with_audio=False)

    facts = inspect_audio(path)

    assert facts.has_audio is False
    assert facts.audio_codec is None
    assert facts.audio_duration is None
    assert facts.sample_rate is None
    assert facts.channels is None
    assert facts.has_native_audio is False


def test_image_motion_required_silence_is_not_native_audio(tmp_path):
    image = tmp_path / "images" / "scene_001.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=red:s={WIDTH}x{HEIGHT}",
        "-frames:v", "1", str(image),
    ])
    provider = ImageMotionVisualProvider(image.parent, tmp_path / "scenes")
    result = provider.generate_scene(
        SceneGenerationRequest(
            project_id="audio_contract",
            scene_id="scene_001",
            sequence=1,
            visual_prompt="A deterministic still image motion fixture.",
            config=FlowGenerationConfig(aspect_ratio="9:16", duration=1),
        ),
        require_confirmation=False,
    )

    assert result.success is True
    assert result.audio_facts.has_audio is True
    assert result.audio_facts.is_synthetic is True
    assert result.audio_facts.has_native_audio is False


@pytest.mark.parametrize(
    ("policy", "native", "narration", "expected_native", "expected_narration"),
    [
        ("narration_only", Path("native.mp4"), Path("voice.m4a"), False, True),
        ("narration_only", Path("native.mp4"), None, True, False),
        ("native_only", Path("native.mp4"), Path("voice.m4a"), True, False),
        # V1 AUDIO POLICY FIX: ``native_only`` with no native audio selects
        # NOTHING.  The explicit intent wins, so narrator/TTS audio is never
        # silently substituted (the previous behavior fell back to narration).
        ("native_only", None, Path("voice.m4a"), False, False),
        ("native_plus_narration", Path("native.mp4"), Path("voice.m4a"), True, True),
        # ``native_plus_narration`` without native audio still uses narration
        # alone — that is the intended combination semantics, not a violation.
        ("native_plus_narration", None, Path("voice.m4a"), False, True),
        # Explicit silence: no native audio, no narration, no music.
        ("none", Path("native.mp4"), Path("voice.m4a"), False, False),
    ],
)
def test_audio_policy_selects_explicit_inputs(
    policy, native, narration, expected_native, expected_narration
):
    selection = select_audio_inputs(
        policy,
        scene_native_audio=native,
        narration=narration,
        music=Path("music.m4a"),
    )

    assert bool(selection.scene_native_audio) is expected_native
    assert bool(selection.narration) is expected_narration
    assert selection.policy.value == policy
    # ``none`` suppresses the additive music capability too, so the final
    # artifact cannot carry an audio stream.
    assert bool(selection.music) is (policy != "none")
    # Native unavailability is disclosed truthfully, and no policy silently
    # falls back from native to narrator audio any more.
    assert selection.native_unavailable is (
        policy in {"native_only", "native_plus_narration"} and native is None
    )
    assert selection.fallback_used is False


def test_audio_policy_defaults_legacy_projects_to_narration_only():
    assert normalize_audio_policy(None) == AudioPolicy.NARRATION_ONLY
    assert AssemblyProject(
        project_id="legacy", scenes=[Scene(id="s", video="scene.mp4")]
    ).audio_policy == AudioPolicy.NARRATION_ONLY


@pytest.mark.parametrize("policy", [
    AudioPolicy.NARRATION_ONLY,
    AudioPolicy.NATIVE_ONLY,
    AudioPolicy.NATIVE_PLUS_NARRATION,
])
def test_final_audio_has_one_stream_and_stays_within_video_duration(tmp_path, policy):
    scene = make_video(tmp_path / "scene.mp4", duration=2.0, with_audio=True)
    voice = make_audio(tmp_path / "voice.m4a", duration=2.0)
    project = make_project(
        tmp_path,
        [scene],
        voiceover=voice,
        policy=policy,
    )

    result = assemble_project(project, base_dir=tmp_path, output_dir=tmp_path / "final")

    assert result.success is True, result.errors
    streams = probe_streams(Path(result.output_path))["streams"]
    audio_streams = [stream for stream in streams if stream["codec_type"] == "audio"]
    video_streams = [stream for stream in streams if stream["codec_type"] == "video"]
    assert len(video_streams) == 1
    assert len(audio_streams) == 1
    assert float(probe_streams(Path(result.output_path))["format"]["duration"]) <= 2.2


def test_native_only_without_native_audio_is_silent_and_never_uses_narration(tmp_path):
    """V1 AUDIO POLICY FIX (was: native_only fell back to narration).

    A ``native`` intent with no native audio in the real scene media must
    produce a video with NO audio stream.  A narrator track that happens to
    exist on disk is never substituted, because that would silently violate the
    explicit audio intent.
    """
    scene = make_video(tmp_path / "silent.mp4", duration=2.0, with_audio=False)
    voice = make_audio(tmp_path / "voice.m4a", duration=2.0)
    result = assemble_project(
        make_project(
            tmp_path,
            [scene],
            voiceover=voice,
            policy=AudioPolicy.NATIVE_ONLY,
        ),
        base_dir=tmp_path,
        output_dir=tmp_path / "final",
    )

    assert result.success is True, result.errors
    assert any(
        "forbids a narrator/TTS fallback" in warning for warning in result.warnings
    ), result.warnings
    assert not any("fell back to narration" in warning for warning in result.warnings)
    streams = probe_streams(Path(result.output_path))["streams"]
    assert len([stream for stream in streams if stream["codec_type"] == "audio"]) == 0
    assert len([stream for stream in streams if stream["codec_type"] == "video"]) == 1


def test_mixed_audio_and_no_audio_scenes_have_deterministic_concat_topology(tmp_path):
    native = make_video(tmp_path / "native.mp4", duration=1.5, with_audio=True)
    silent = make_video(tmp_path / "silent.mp4", duration=1.5, with_audio=False)

    result = assemble_project(
        make_project(tmp_path, [native, silent], policy=AudioPolicy.NATIVE_ONLY),
        base_dir=tmp_path,
        output_dir=tmp_path / "final",
    )

    assert result.success is True, result.errors
    streams = probe_streams(Path(result.output_path))["streams"]
    assert len([stream for stream in streams if stream["codec_type"] == "audio"]) == 1
    assert float(probe_streams(Path(result.output_path))["format"]["duration"]) <= 3.2


def test_resume_restores_persisted_audio_policy(tmp_path, monkeypatch):
    manifest = ProjectManifest(
        project_id="resume_audio",
        topic="resume audio policy",
        assembly_config={"audio_policy": AudioPolicy.NATIVE_PLUS_NARRATION.value},
    )
    manifest.initialize_stages()
    project_dir = tmp_path / "resume_audio"
    project_dir.mkdir()
    (project_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict()), encoding="utf-8"
    )

    monkeypatch.setattr(
        "app.orchestrator.pipeline.ORCHESTRATOR_OUTPUT_DIR", tmp_path
    )
    orchestrator = ProductionOrchestrator(
        OrchestratorConfig(
            topic="resume audio policy",
            flow_project_url="",
            visual_mode="local",
            audio_policy=AudioPolicy.NARRATION_ONLY,
        )
    )
    orchestrator._resume_project("resume_audio")

    assert orchestrator.config.audio_policy == AudioPolicy.NATIVE_PLUS_NARRATION
    assert orchestrator.manifest.assembly_config["audio_policy"] == "native_plus_narration"