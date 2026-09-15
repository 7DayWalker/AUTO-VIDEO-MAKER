"""Phase 11C Batch 1 — local-route end-to-end proofs.

Three proofs:

1. OFFLINE mechanical (always runs): explicit mock planner + mock narration +
   fixture imagery still exercise the REAL local route — real ImageMotion
   renders, real caption burn, real FFmpeg assembly — and the Command Center
   reports the authoritative result.

2. OFFLINE truthful failure (always runs): an unavailable intelligence
   provider fails the unit truthfully; nothing reports COMPLETED and no mock
   fallback is applied.

3. LIVE (``AVM_LIVE_E2E=1`` + ``OPENROUTER_API_KEY``): the exact user journey
   with a unique brief — real OpenRouter planning, real Wikimedia Commons
   acquisition, real Edge TTS narration, ImageMotion, captions, local FFmpeg
   assembly, a final MP4 verified with FFprobe AND audibility (volumedetect),
   and Command Center completion derived from the existing authorities.

All proofs run against isolated temp directories; nothing outside tmp is
touched and no fabricated artifacts are accepted.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from app.assembly.ffmpeg import probe_media, resolve_ffmpeg
from app.orchestrator import pipeline as pipeline_module
from app.production.storage import load_production
from app.voice import config as voice_config
from app.web import server as web

MOCK_PLAN_PROJECT_ID = "20260819_ocean_facts_mock"

BRIEF = (
    "Create a 30-second curious cinematic TikTok about why the ocean is "
    "blue, covering light absorption and scattering in water."
)


def _isolate(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    monkeypatch.setattr(web, "PROJECTS_DIR", projects)
    monkeypatch.setattr(pipeline_module, "ORCHESTRATOR_OUTPUT_DIR", projects)
    return projects


def _create_production():
    status, payload = web.create_production_payload({
        "mode": "single_video",
        "creation_method": "local",
        "title": "Why the ocean is blue",
        "brief": BRIEF,
        "output_intent": "tiktok",
        "target_duration": 30,
        "scene_count": 6,
        "tone": "curious and cinematic",
    })
    assert status == 201, payload
    return payload["production_id"]


def _plan_meta(production_id):
    return json.loads(
        (
            web.PRODUCTIONS_DIR / production_id / "units" / "unit-001"
            / "plan" / "plan_meta.json"
        ).read_text(encoding="utf-8")
    )


def _assert_final_media(project_dir: Path) -> Path:
    finals = list(project_dir.rglob("*_final.mp4"))
    assert finals, "no final MP4 artifact was produced"
    final = finals[0]
    info = probe_media(final)
    assert info.has_video, "final MP4 has no video stream"
    assert info.has_audio, "final MP4 has no audio stream"
    assert info.audio_codec == "aac"
    assert info.duration > 0
    assert info.file_size > 0
    return final


def _assert_audible(final: Path) -> float:
    ffmpeg = resolve_ffmpeg()
    assert ffmpeg, "FFmpeg must resolve for audibility verification"
    proc = subprocess.run(
        [ffmpeg, "-i", str(final), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300,
    )
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", proc.stderr)
    assert match, f"volumedetect produced no mean_volume: {proc.stderr[-400:]}"
    return float(match.group(1))


def _command_center_progress(production_id):
    status, payload = web.production_progress_payload(production_id)
    assert status == 200
    return payload
# ---------------------------------------------------------------------------
# Offline proof 1: explicit mock intelligence/narration through the REAL
# local route (real ImageMotion renders, captions, and FFmpeg assembly)
# ---------------------------------------------------------------------------

def test_batch1_local_route_full_pipeline_offline_mock_mode(tmp_path, monkeypatch):
    """Offline mechanical proof: mock planner + mock narration + fixture
    imagery (all explicit, all labelled) still exercise the REAL local route
    and the Command Center reports the authoritative result."""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.delenv("AVM_MOCK_MODE", raising=False)
    monkeypatch.setenv("AVM_IMAGE_SOURCE", "fixture")
    monkeypatch.setattr(voice_config, "TTS_PROVIDER", "mock")

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

    production_id = _create_production()
    report = web._production_executor(production_id).execute()
    assert report.complete

    production = load_production(production_id, root=web.PRODUCTIONS_DIR)
    executor = web._production_executor(production_id)
    bindings = executor.discover_bindings(production)
    assert set(bindings) == {"unit-001"}
    project_dir = bindings["unit-001"].project_dir

    # Explicit mock provenance is recorded and never passed off as AI output.
    meta = _plan_meta(production_id)
    assert meta["planner_provider"] == "mock"
    assert meta["plan_identity"] == MOCK_PLAN_PROJECT_ID

    # Real ImageMotion scene renders (not legacy placeholder files).
    scene_manifest = json.loads(
        (
            project_dir / "scenes" / MOCK_PLAN_PROJECT_ID / "scene_manifest.json"
        ).read_text(encoding="utf-8")
    )
    scene_entries = scene_manifest.get("scenes") or []
    assert scene_entries, "no scene manifest entries"
    assert all(entry.get("status") == "completed" for entry in scene_entries)
    assert not any(
        (entry.get("checkpoint_evidence") or {}).get("generation_kind")
        == "placeholder"
        for entry in scene_entries
    )

    final = _assert_final_media(project_dir)
    _assert_audible(final)  # mock narration is a tone — still a real stream
    progress = _command_center_progress(production_id)
    assert progress["counts"]["COMPLETED"] == 1
    assert progress["complete"] is True


# ---------------------------------------------------------------------------
# Offline proof 2: unavailable intelligence fails truthfully (no mock fallback)
# ---------------------------------------------------------------------------

def test_batch1_local_route_intelligence_failure_is_truthful(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.delenv("AVM_MOCK_MODE", raising=False)
    monkeypatch.setenv("AVM_IMAGE_SOURCE", "wikimedia")

    def _unavailable(*, mock_mode=False, output_dir=None):
        raise RuntimeError("content provider unavailable: openrouter")

    monkeypatch.setattr(
        "app.content.planner.create_scene_planner", _unavailable
    )

    production_id = _create_production()
    with pytest.raises(RuntimeError):
        web._production_executor(production_id).execute()

    progress = _command_center_progress(production_id)
    assert progress["counts"]["COMPLETED"] == 0
    assert progress["complete"] is False
# ---------------------------------------------------------------------------
# LIVE proof: the exact Batch 1 user journey with a unique brief
# ---------------------------------------------------------------------------

LIVE_E2E = (
    os.getenv("AVM_LIVE_E2E") == "1"
    and bool(os.getenv("OPENROUTER_API_KEY"))
)


@pytest.mark.skipif(
    not LIVE_E2E,
    reason=(
        "live Batch 1 E2E requires AVM_LIVE_E2E=1 and OPENROUTER_API_KEY "
        "(runs a real OpenRouter plan, Wikimedia acquisition and Edge TTS)"
    ),
)
def test_batch1_local_route_live_intelligence_end_to_end(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.delenv("AVM_MOCK_MODE", raising=False)
    monkeypatch.setenv("AVM_IMAGE_SOURCE", "wikimedia")

    # Route planning through the project's AI API router (OpenRouter) with the
    # configured free development model — configuration-driven, not hardcoded.
    monkeypatch.setattr(
        "app.content.providers.DEFAULT_LLM_PROVIDER", "openrouter"
    )
    model = os.getenv("OPENROUTER_MODEL") or "z-ai/glm-5.2:free"
    monkeypatch.setattr(
        "app.content.providers.openrouter.OPENROUTER_MODEL", model
    )

    production_id = _create_production()
    report = web._production_executor(production_id).execute()
    assert report.complete

    # The plan was produced by the configured provider and derives from THIS
    # unique brief — never the fixed mock artifact.
    meta = _plan_meta(production_id)
    assert meta["planner_provider"] == "openrouter"
    assert meta["planner_model"] == model
    plan = json.loads(
        (
            web.PRODUCTIONS_DIR / production_id / "units" / "unit-001"
            / "plan" / "production_plan.json"
        ).read_text(encoding="utf-8")
    )
    assert plan["project_id"] != MOCK_PLAN_PROJECT_ID
    assert "ocean" in json.dumps(plan).lower()
    # The plan is brief-derived; the free dev model may return a slightly
    # different scene count than requested — the validator enforces duration.
    assert len(plan["scenes"]) >= 2

    production = load_production(production_id, root=web.PRODUCTIONS_DIR)
    executor = web._production_executor(production_id)
    bindings = executor.discover_bindings(production)
    project_dir = bindings["unit-001"].project_dir

    # Scene imagery carries real Wikimedia Commons provenance (stock, not
    # fixture, not fabricated).
    asset_manifest = json.loads(
        (project_dir / "assets" / "asset_manifest.json").read_text()
    )
    assert "wikimedia" in json.dumps(asset_manifest).lower()
    images = list((project_dir / "assets" / "source").glob("*"))
    assert images, "no sourced scene imagery in project-owned storage"
    assert all(p.stat().st_size > 0 for p in images)

    final = _assert_final_media(project_dir)
    mean_db = _assert_audible(final)
    assert mean_db > -60.0, f"narration is not audible (mean {mean_db} dB)"

    progress = _command_center_progress(production_id)
    assert progress["counts"]["COMPLETED"] == 1
    assert progress["complete"] is True