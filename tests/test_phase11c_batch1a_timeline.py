"""Phase 11C Batch 1A — narration/visual timeline synchronization regressions.

The confirmed defect: local narration-driven scenes were rendered from the
plan's independent ``estimated_duration`` values while the measured narration
ran substantially shorter, so assembly padded ~24s of silence into a real
production (36.19s narration inside a ~60s video). The fix reconciles the LOCAL
render durations proportionally to the measured narration at the post-voice /
pre-render seam — without touching the plan, the voice timing artifact,
remote/billable providers, or the Phase 7D audio contract.

Unit tests pin the reconciliation arithmetic; the end-to-end test drives the
REAL local route (real renders, real caption burn, real FFmpeg assembly)
offline through the production executor and asserts that narration, visuals,
captions, and the final file converge on one timeline.
"""

import json
import math

from app.assembly.ffmpeg import probe_media
from app.content.models import ProductionPlan
from app.orchestrator import pipeline as pipeline_module
from app.orchestrator.captions import _parse_srt_timestamp
from app.orchestrator.pipeline import reconcile_local_render_durations
from app.voice import config as voice_config
from app.web import server as web

# The observed failing production's shape: 60s planned visual timeline,
# 36.192s measured Edge TTS narration.
PLANNED_DURATIONS = [7.0, 10.0, 6.0, 6.0, 6.0, 6.0, 10.0, 9.0]
MEASURED_NARRATION = 36.192


def test_short_narration_shrinks_visual_timeline_proportionally():
    reconciled = reconcile_local_render_durations(PLANNED_DURATIONS, MEASURED_NARRATION)
    assert reconciled is not None
    assert all(duration >= 1 for duration in reconciled)  # renderer minimum
    assert all(duration <= 60 for duration in reconciled)  # renderer maximum
    # The visual total converges on the narration timeline: the full narration
    # is covered (assembly must never trim narration) and the overshoot stays
    # below one second (floor + largest-remainder, no cumulative ceil drift).
    assert sum(reconciled) >= math.ceil(MEASURED_NARRATION)
    assert sum(reconciled) - MEASURED_NARRATION < 1.0
    # Proportions survive: every rendered duration stays within one second of
    # its proportional share of the narration timeline.
    scale = MEASURED_NARRATION / sum(PLANNED_DURATIONS)
    for rendered, planned in zip(reconciled, PLANNED_DURATIONS):
        assert abs(rendered - planned * scale) < 1.0


def test_expected_distribution_for_the_observed_defect_is_pinned():
    # Freezing the exact deterministic distribution for the observed 36.192s /
    # 60s defect so any future rounding change is a conscious decision.
    assert reconcile_local_render_durations(
        PLANNED_DURATIONS, MEASURED_NARRATION
    ) == [4, 6, 4, 4, 4, 4, 6, 5]


def test_small_shortfall_within_deadband_is_left_to_assembly_tolerance():
    # A ~1.2s shortfall is container/codec-sized: the existing assembly
    # tolerance/padding behavior handles it and no reconciliation churns.
    assert reconcile_local_render_durations(PLANNED_DURATIONS, 58.8) is None


def test_longer_narration_still_extends_the_visual_timeline():
    # Extension keeps the previous always-apply behavior (no deadband) and
    # still guarantees the narration is fully covered.
    reconciled = reconcile_local_render_durations([6.0] * 10, 61.5)
    assert reconciled is not None
    assert sum(reconciled) >= math.ceil(61.5)
    assert all(duration >= 1 for duration in reconciled)


def test_renderer_minimum_respected_for_very_short_narration():
    # 20 scenes planned at 3s each (60s) against 6s of narration: proportional
    # scaling would create 0.3s scenes, below the renderer's 1s minimum. The
    # smallest safe adjustment clamps to the minimum; the overshoot is the
    # unavoidable consequence of the immutable plan structure.
    reconciled = reconcile_local_render_durations([3.0] * 20, 6.0)
    assert reconciled is not None
    assert reconciled == [1] * 20


def test_no_narration_or_no_scenes_returns_none():
    assert reconcile_local_render_durations(PLANNED_DURATIONS, 0) is None
    assert reconcile_local_render_durations(PLANNED_DURATIONS, None) is None
    assert reconcile_local_render_durations([], MEASURED_NARRATION) is None


def test_reconciliation_is_deterministic():
    first = reconcile_local_render_durations(PLANNED_DURATIONS, MEASURED_NARRATION)
    second = reconcile_local_render_durations(PLANNED_DURATIONS, MEASURED_NARRATION)
    assert first == second


# ---------------------------------------------------------------------------
# End-to-end: the real local route converges on the narration timeline
# ---------------------------------------------------------------------------

PLAN_PROJECT_ID = "20260908_batch1a_timeline_sync"
# 90 narration words at the mock TTS rate (2.5 words/second) -> 36.0s measured
# narration against the 60s planned visual timeline (>2s divergence).
WORD_COUNTS = [11, 16, 10, 10, 10, 10, 12, 11]


def _isolate(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    monkeypatch.setattr(web, "PROJECTS_DIR", projects)
    monkeypatch.setattr(pipeline_module, "ORCHESTRATOR_OUTPUT_DIR", projects)
    return projects


def _verification_plan() -> dict:
    scenes = []
    for sequence, (estimated, words) in enumerate(
        zip(PLANNED_DURATIONS, WORD_COUNTS), start=1
    ):
        scenes.append(
            {
                "id": f"scene_{sequence:02d}",
                "sequence": sequence,
                "narration": " ".join(["narration"] * words),
                "visual_prompt": (
                    f"Cinematic vertical shot for verification scene {sequence}, "
                    "dramatic lighting, shallow depth of field"
                ),
                "estimated_duration": estimated,
                "purpose": "fact",
            }
        )
    return {
        "project_id": PLAN_PROJECT_ID,
        "title": "Batch 1A Timeline Synchronization Verification",
        "hook": "A synchronized timeline proof.",
        "script": " ".join(scene["narration"] for scene in scenes),
        "call_to_action": "Follow for more",
        "estimated_duration": 60.0,
        "scenes": scenes,
        "metadata": {"platform": "tiktok", "language": "en"},
    }


class _FixedPlanPlanner:
    """Deterministic planner returning the 60s/8-scene verification plan."""

    def plan(self, brief):
        from app.content.models import ContentGenerationResult

        plan = ProductionPlan(**_verification_plan())
        return ContentGenerationResult.from_plan(
            plan, provider="fixed-plan", model=None
        )


def test_local_render_uses_reconciled_narration_timeline_end_to_end(
    tmp_path, monkeypatch
):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.delenv("AVM_MOCK_MODE", raising=False)
    monkeypatch.setenv("AVM_IMAGE_SOURCE", "fixture")
    monkeypatch.setattr(voice_config, "TTS_PROVIDER", "mock")
    monkeypatch.setattr(
        "app.content.planner.create_scene_planner",
        lambda *, mock_mode=False, output_dir=None: _FixedPlanPlanner(),
    )

    status, payload = web.create_production_payload(
        {
            "mode": "single_video",
            "creation_method": "local",
            "title": "Batch 1A timeline synchronization verification",
            "brief": (
                "Create a 60-second eight-scene vertical story to verify that "
                "visual scene durations reconcile to the measured narration."
            ),
            "output_intent": "tiktok",
            "target_duration": 60,
            "scene_count": 8,
            "tone": "curious and cinematic",
        }
    )
    assert status == 201, payload
    production_id = payload["production_id"]

    report = web._production_executor(production_id).execute()
    assert report.complete

    from app.production.storage import load_production

    production = load_production(production_id, root=web.PRODUCTIONS_DIR)
    bindings = web._production_executor(production_id).discover_bindings(production)
    project_dir = bindings["unit-001"].project_dir

    # Measured narration: 90 words at the mock provider's 2.5 words/second.
    narration = probe_media(
        project_dir / "voice" / PLAN_PROJECT_ID / "voiceover.mp3"
    ).duration
    assert 35.5 <= narration <= 36.6

    # Every rendered scene carries a reconciled INTEGER duration (the defect
    # previously rendered the plan's original 7/10/6/6/6/6/10/9 = 60s total).
    scene_dir = project_dir / "scenes" / PLAN_PROJECT_ID
    scene_entries = json.loads((scene_dir / "scene_manifest.json").read_text())[
        "scenes"
    ]
    assert [entry["status"] for entry in scene_entries] == ["completed"] * 8
    rendered = [
        probe_media(scene_dir / entry["file"]).duration for entry in scene_entries
    ]
    assert all(abs(duration - round(duration)) < 0.01 for duration in rendered)
    assert all(duration >= 1 for duration in rendered)
    total_visual = sum(rendered)
    # Exact reconciliation contract: the visual total covers the full measured
    # narration with sub-second rounding overshoot — versus 60s before the fix.
    assert total_visual == math.ceil(narration)
    assert total_visual - narration < 1.0
    assert total_visual < 45

    # The applied reconciliation is recorded as stage evidence.
    manifest = json.loads((project_dir / "manifest.json").read_text())
    scenes_metadata = manifest["stages"]["scenes"]["metadata"]
    assert scenes_metadata["local_timeline_reconciled"] is True
    assert list(scenes_metadata["local_render_durations"].values()) == [
        round(duration) for duration in rendered
    ]

    # Assembly converges: the final file is the reconciled visual total, the
    # narration is never trimmed, and any padding is sub-second.
    finals = list(project_dir.rglob("*_final.mp4"))
    assert finals, "no final MP4 artifact was produced"
    final = probe_media(finals[0])
    assert abs(final.duration - total_visual) <= 0.6
    assert final.duration >= narration - 0.1
    assert final.duration - narration < 2.0

    # Captions ride the same reconciled narration timeline and end with it.
    srt_text = (project_dir / "captions" / "captions.srt").read_text()
    end_times = [
        _parse_srt_timestamp(line.split(" --> ")[1])
        for line in srt_text.splitlines()
        if " --> " in line
    ]
    assert end_times, "captions file has no entries"
    assert abs(max(end_times) - narration) <= 0.5

