"""Phase 2A targeted tests.

Covers exactly the three approved Core fixes and nothing else:

* P0-01 - ``GET /api/productions`` batch progress derivation (one shared
  projects scan instead of one scan per production).
* P0-02 - stale/orphaned ``IN_FLIGHT`` disclosure and the ``task_id`` vs
  ``job_id`` vocabulary drift.
* P2-03 - provenance/accounting capture for the Flow completion path, and
  honest labelling of legacy pre-Batch-1F checkpoint records.

Hermetic: no network, no providers, no media.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.orchestrator import production_execution as pe
from app.orchestrator.models import (
    PipelineStage,
    ProductionBinding,
    ProjectManifest,
    StageResult,
    StageStatus,
)
from app.orchestrator.production_execution import (
    ProductionExecutor,
    UnitExecutionState,
    UnitProgress,
)
from app.orchestrator.scene_execution import completion_checkpoint_update
from app.production.models import Production, ProductionCommand, ProductionUnit
from app.production.storage import save_new_production
from app.web.server import _scene_checkpoint_rows

CREATED = "2026-08-31T09:00:00+00:00"
NOW = datetime(2026, 9, 19, 12, 0, 0)
SIX_STATES = [
    "NOT_STARTED",
    "BLOCKED",
    "IN_FLIGHT",
    "INCOMPLETE",
    "COMPLETED",
    "FAILED",
]


def _binding() -> ProductionBinding:
    return ProductionBinding(
        production_id="prd_20260831_phase2a_test_001",
        unit_id="unit-001",
        production_plan_version=1,
        unit_plan_identity="identity-1",
        unit_plan_content_hash="0" * 64,
        project_id="proj_fixture",
        bound_at=CREATED,
    )


def _manifest(*, running=False, started_at=None, checkpoints=None, binding=False,
              updated_at=None):
    stages = {}
    if running:
        stages["content"] = StageResult(
            stage=PipelineStage.CONTENT,
            status=StageStatus.RUNNING,
            started_at=started_at,
        )
    return ProjectManifest(
        project_id="proj_fixture",
        topic="fixture topic",
        stages=stages,
        scene_checkpoints=checkpoints or {},
        production_binding=_binding() if binding else None,
        updated_at=updated_at or NOW.isoformat(),
    )


class _FakeReport:
    complete = False
    verdict = None


class _FakeUnit:
    unit_id = "unit-001"
    ordinal = 1
    dependencies = []


class _FakeBound:
    directory_id = "proj_fixture"
    project_dir = Path("proj_fixture")
    manifest_raw = None

    def __init__(self, manifest):
        self.manifest = manifest


# ---------------------------------------------------------------------------
# P0-02 - in-flight detection and staleness disclosure
# ---------------------------------------------------------------------------


def test_six_state_vocabulary_is_unchanged():
    """Phase 10 vocabulary is a published contract; P0-02 must not extend it."""
    assert [state.value for state in UnitExecutionState] == SIX_STATES


def test_unit_progress_stale_in_flight_defaults_false():
    """Additive field: existing positional construction keeps working."""
    progress = UnitProgress("unit-001", 1, UnitExecutionState.NOT_STARTED)
    assert progress.stale_in_flight is False
    assert progress.reason is None


def test_alibaba_task_id_alone_is_detected_as_in_flight():
    """P0-02 vocabulary drift: DashScope persists ``task_id`` with no
    ``polling_url``. The old check required ``job_id`` AND ``polling_url`` (both
    OpenRouter-only), so Alibaba in-flight jobs were invisible."""
    manifest = _manifest(
        checkpoints={"scene_01": {"status": "submitted", "task_id": "task-abc"}}
    )
    assert ProductionExecutor._has_remote_in_flight(manifest) is True


def test_openrouter_job_id_path_still_detected():
    """No regression for the OpenRouter vocabulary."""
    manifest = _manifest(
        checkpoints={
            "scene_01": {
                "status": "submitted",
                "job_id": "job-1",
                "polling_url": "https://example.invalid/poll",
            }
        }
    )
    assert ProductionExecutor._has_remote_in_flight(manifest) is True


def test_terminal_checkpoint_is_never_in_flight():
    """The real legacy Alibaba record is ``status=completed`` while still
    carrying ``task_id`` and ``remote_status=RUNNING``. A terminal checkpoint
    must not be reported as live activity."""
    manifest = _manifest(
        checkpoints={
            "scene_01": {
                "status": "completed",
                "task_id": "task-abc",
                "remote_status": "RUNNING",
            }
        }
    )
    assert ProductionExecutor._has_remote_in_flight(manifest) is False


def test_nested_submission_evidence_is_detected():
    """Batch 1F nests evidence under ``checkpoint_evidence``; both shapes count."""
    manifest = _manifest(
        checkpoints={
            "scene_01": {
                "status": "running",
                "checkpoint_evidence": {"submission": {"state": "SUBMITTED"}},
            }
        }
    )
    assert ProductionExecutor._has_remote_in_flight(manifest) is True


def _window() -> int:
    return int(ProductionExecutor._declared_remote_window_seconds())


def test_declared_window_is_derived_from_provider_constants():
    """The staleness horizon comes from the providers' own declared bounded
    polling windows, never an invented literal."""
    from app.orchestrator.alibaba_image import (
        DEFAULT_ALIBABA_IMAGE_POLL_INTERVAL,
        DEFAULT_ALIBABA_IMAGE_POLL_MAX_ATTEMPTS,
    )
    from app.orchestrator.alibaba_video_config import (
        DEFAULT_POLL_INTERVAL,
        DEFAULT_POLL_MAX_ATTEMPTS,
    )
    from app.orchestrator.openrouter_video import (
        OPENROUTER_DEFAULT_POLL_INTERVAL,
        OPENROUTER_DEFAULT_POLL_MAX_ATTEMPTS,
    )

    expected = max(
        float(DEFAULT_POLL_INTERVAL) * float(DEFAULT_POLL_MAX_ATTEMPTS),
        float(OPENROUTER_DEFAULT_POLL_INTERVAL)
        * float(OPENROUTER_DEFAULT_POLL_MAX_ATTEMPTS),
        float(DEFAULT_ALIBABA_IMAGE_POLL_INTERVAL)
        * float(DEFAULT_ALIBABA_IMAGE_POLL_MAX_ATTEMPTS),
    )
    assert expected > 0
    assert ProductionExecutor._declared_remote_window_seconds() == expected


def test_orphaned_running_stage_is_disclosed_as_stale():
    """The six real orphaned projects (stage stuck RUNNING since Aug/Sep) must
    be disclosed rather than presented as live activity."""
    started = (NOW - timedelta(days=16)).isoformat()
    manifest = _manifest(running=True, started_at=started)

    in_flight, stale, reason = ProductionExecutor._remote_in_flight_staleness(
        manifest, now=NOW
    )

    assert in_flight is True
    assert stale is True
    assert reason is not None
    assert f"{_window()}s" in reason
    assert started in reason


def test_fresh_remote_submission_is_not_stale():
    """Inside the declared window the state is genuinely in flight."""
    started = (NOW - timedelta(seconds=_window() // 2)).isoformat()
    manifest = _manifest(running=True, started_at=started)

    in_flight, stale, reason = ProductionExecutor._remote_in_flight_staleness(
        manifest, now=NOW
    )

    assert in_flight is True
    assert stale is False
    assert reason is None


def test_boundary_of_declared_window_is_inclusive_of_live_activity():
    """Only evidence strictly older than the provider-declared window may be
    called stale - no off-by-one over-claiming."""
    started = (NOW - timedelta(seconds=_window() - 5)).isoformat()
    manifest = _manifest(running=True, started_at=started)
    _, stale, _ = ProductionExecutor._remote_in_flight_staleness(manifest, now=NOW)
    assert stale is False


def test_unusable_timestamp_never_claims_staleness():
    """Absence of readable evidence is reported as unknown, not as stale."""
    manifest = _manifest(
        running=True, started_at="not-a-timestamp", updated_at=NOW.isoformat()
    )
    in_flight, stale, _ = ProductionExecutor._remote_in_flight_staleness(
        manifest, now=NOW
    )
    assert in_flight is True
    assert stale is False


def test_stale_disclosure_does_not_reclassify_the_state():
    """``execute()`` offers NOT_STARTED/IN_FLIGHT/INCOMPLETE as resume
    candidates. Disclosing staleness must not move a unit out of IN_FLIGHT, or
    resume eligibility would silently change."""
    started = (NOW - timedelta(days=16)).isoformat()
    manifest = _manifest(running=True, started_at=started, binding=True)
    executor = ProductionExecutor.__new__(ProductionExecutor)

    progress = executor._unit_progress_from(
        _FakeUnit(), _FakeBound(manifest), _FakeReport()
    )

    assert progress.state == UnitExecutionState.IN_FLIGHT
    assert progress.stale_in_flight is True
    assert progress.reason and "stale" in progress.reason


def test_fresh_in_flight_unit_carries_no_stale_flag():
    """``_unit_progress_from`` reads the real clock by design (a GET must report
    the age as of now), so this fixture is built relative to wall time."""
    manifest = _manifest(
        running=True, started_at=datetime.now().isoformat(), binding=True
    )
    executor = ProductionExecutor.__new__(ProductionExecutor)

    progress = executor._unit_progress_from(
        _FakeUnit(), _FakeBound(manifest), _FakeReport()
    )

    assert progress.state == UnitExecutionState.IN_FLIGHT
    assert progress.stale_in_flight is False
    assert progress.reason is None


def test_staleness_check_never_mutates_the_manifest():
    """A GET must not repair durable state - no reset_running_stages on read."""
    started = (NOW - timedelta(days=16)).isoformat()
    manifest = _manifest(running=True, started_at=started)
    before = manifest.model_dump_json()

    ProductionExecutor._remote_in_flight_staleness(manifest, now=NOW)

    assert manifest.model_dump_json() == before
    assert manifest.stages["content"].status == StageStatus.RUNNING


# ---------------------------------------------------------------------------
# P0-01 - one shared projects scan for the production collection
# ---------------------------------------------------------------------------


def _save_production(production_id: str, root: Path) -> Production:
    production = Production(
        production_id=production_id,
        title="Phase 2A fixture",
        command=ProductionCommand(
            text="Create a fixture production.",
            workload_shape="unit_count",
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
    )
    save_new_production(production, root=root)
    return production


@pytest.fixture()
def scan_fixture(tmp_path, monkeypatch):
    """A projects tree of unbound manifests plus several productions.

    Unbound manifests are parsed by every scan but bind nothing, so counting
    manifest reads directly measures the scan cost that caused P0-01.
    """
    productions_root = tmp_path / "productions"
    projects_root = tmp_path / "projects"
    projects_root.mkdir(parents=True)
    for index in range(5):
        directory = projects_root / f"proj_{index}"
        directory.mkdir()
        (directory / "manifest.json").write_text(
            json.dumps({"project_id": f"proj_{index}", "topic": "unbound"}), "utf-8"
        )
    ids = []
    for index in range(4):
        production = _save_production(
            f"prd_20260831_phase2a_fixture_{index:03d}", productions_root
        )
        ids.append(production.production_id)

    calls = {"count": 0}
    original = pe._load_json

    def counting(path):
        if Path(path).name == "manifest.json":
            calls["count"] += 1
        return original(path)

    monkeypatch.setattr(pe, "_load_json", counting)
    return {
        "ids": ids,
        "productions_root": productions_root,
        "projects_root": projects_root,
        "calls": calls,
    }


def _executor(fixture, production_id):
    return ProductionExecutor(
        production_id,
        productions_root=fixture["productions_root"],
        projects_root=fixture["projects_root"],
    )


def test_batch_derivation_scans_the_projects_tree_once(scan_fixture):
    """P0-01 root cause: the collection endpoint re-scanned the whole tree once
    per production - O(P*Q) manifest parses."""
    fixture = scan_fixture
    fixture["calls"]["count"] = 0
    for production_id in fixture["ids"]:
        _executor(fixture, production_id).derive_progress()
    sequential_reads = fixture["calls"]["count"]

    fixture["calls"]["count"] = 0
    ProductionExecutor.derive_progress_many(
        fixture["ids"],
        productions_root=fixture["productions_root"],
        projects_root=fixture["projects_root"],
    )
    batched_reads = fixture["calls"]["count"]

    assert batched_reads == 5, "expected exactly one shared scan"
    assert sequential_reads == 20, "per-production path scans once per production"
    assert batched_reads < sequential_reads


def test_batch_derivation_matches_single_production_derivation(scan_fixture):
    """The speed-up must not change a single reported value."""
    fixture = scan_fixture
    single = {
        production_id: _executor(fixture, production_id).derive_progress()
        for production_id in fixture["ids"]
    }
    batched = ProductionExecutor.derive_progress_many(
        fixture["ids"],
        productions_root=fixture["productions_root"],
        projects_root=fixture["projects_root"],
    )

    assert set(batched) == set(single)
    for production_id in fixture["ids"]:
        assert batched[production_id].counts() == single[production_id].counts()
        assert batched[production_id].complete == single[production_id].complete
        assert [u.state for u in batched[production_id].units] == [
            u.state for u in single[production_id].units
        ]
        assert [u.stale_in_flight for u in batched[production_id].units] == [
            u.stale_in_flight for u in single[production_id].units
        ]


def test_unbound_manifests_are_never_adopted_by_the_shared_scan(scan_fixture):
    """The shared scan must not create bindings the old path would have refused."""
    fixture = scan_fixture
    report = ProductionExecutor.derive_progress_many(
        [fixture["ids"][0]],
        productions_root=fixture["productions_root"],
        projects_root=fixture["projects_root"],
    )[fixture["ids"][0]]
    assert all(u.state == UnitExecutionState.NOT_STARTED for u in report.units)


def test_batch_derivation_of_empty_collection_is_free():
    assert ProductionExecutor.derive_progress_many([]) == {}


def test_reconcile_is_called_once_per_bound_project_with_reused_bytes(tmp_path, monkeypatch):
    """P0-01 must not become a counts-only shortcut: full per-project
    reconciliation still happens exactly once per bound project, and the raw
    manifest parsed by the shared scan is handed over so it is never re-read."""
    from app.orchestrator.production_execution import BoundProject

    productions_root = tmp_path / "productions"
    production = _save_production("prd_20260831_phase2a_test_001", productions_root)
    payload = json.loads(_manifest(binding=True).model_dump_json())
    bound = BoundProject(
        directory_id="proj_fixture",
        project_dir=tmp_path / "proj_fixture",
        manifest=ProjectManifest.from_dict(payload),
        manifest_raw=payload,
    )
    monkeypatch.setattr(
        ProductionExecutor,
        "discover_bindings_from_scan",
        lambda self, prod, scan: {unit.unit_id: bound for unit in prod.units},
    )
    seen = []

    def fake_reconcile(project_id, project_dir, persist=False, **kwargs):
        seen.append((project_id, str(project_dir), persist, kwargs))
        return _FakeReport()

    ProductionExecutor.derive_progress_many(
        [production.production_id],
        productions_root=productions_root,
        projects_root=tmp_path / "projects",
        reconcile=fake_reconcile,
    )

    assert len(seen) == 1, "exactly one reconcile per bound project"
    assert seen[0][0] == "proj_fixture"
    assert seen[0][2] is False, "a collection read must never persist"
    assert seen[0][3].get("manifest_raw") is payload, "raw bytes must be reused"


def test_batch_path_falls_back_when_raw_bytes_are_absent(tmp_path, monkeypatch):
    """Legacy single-production scans carry no ``manifest_raw``; the batch loop
    must still reconcile them rather than skipping the work."""
    from app.orchestrator.production_execution import BoundProject

    productions_root = tmp_path / "productions"
    production = _save_production("prd_20260831_phase2a_test_001", productions_root)
    payload = json.loads(_manifest(binding=True).model_dump_json())
    bound = BoundProject(
        directory_id="proj_fixture",
        project_dir=tmp_path / "proj_fixture",
        manifest=ProjectManifest.from_dict(payload),
        manifest_raw=None,
    )
    monkeypatch.setattr(
        ProductionExecutor,
        "discover_bindings_from_scan",
        lambda self, prod, scan: {unit.unit_id: bound for unit in prod.units},
    )
    seen = []

    def fake_reconcile(project_id, project_dir, persist=False, **kwargs):
        seen.append(kwargs)
        return _FakeReport()

    ProductionExecutor.derive_progress_many(
        [production.production_id],
        productions_root=productions_root,
        projects_root=tmp_path / "projects",
        reconcile=fake_reconcile,
    )

    assert len(seen) == 1
    assert "manifest_raw" not in seen[0]


# ---------------------------------------------------------------------------
# P2-03 - provenance/accounting capture, and honest legacy presentation
# ---------------------------------------------------------------------------

from app.flow.scene_models import SceneGenerationResult  # noqa: E402


def _flow_result(**verification):
    return SceneGenerationResult(
        success=True,
        scene_id="scene_01",
        sequence=1,
        status="completed",
        file_path=Path("scenes/scene_01.mp4"),
        file_size=1234,
        actual_duration=5.0,
        width=1080,
        height=1920,
        codec="h264",
        generation_cost=0,
        verification=dict(verification),
    )


# The genuine shape of the legacy Alibaba record found on disk: flat observed
# fields, no provenance block at all.
LEGACY_ALIBABA = {
    "status": "completed",
    "scene_id": "scene_01",
    "sequence": 1,
    "provider": "alibaba",
    "task_id": "8a0f0b6e-legacy",
    "model": "wanx2.1-t2v-turbo",
    "attempt": 2,
    "remote_status": "SUCCEEDED",
    "submitted_at": "2026-09-14T10:00:00",
    "usage": {"duration": 5, "video_count": 1},
    "generation_cost": 0,
    "file_path": None,
}


def test_flow_completion_path_still_records_provenance_and_accounting():
    """P2-03 root cause: the Flow completion path assembled its checkpoint by
    hand and so skipped the Batch 1F/1H seam. Routing it through the seam with
    ``include_provenance=False`` must still emit both blocks - the flag only
    suppresses the flat legacy remote keys."""
    update = completion_checkpoint_update(
        result=_flow_result(
            final_provider="google_flow",
            model="veo-3.0",
            request_id="op-777",
            cost=None,
        ),
        validation={"ok": True},
        provider_name="google_flow",
        sequence=1,
        include_provenance=False,
        extra_fields={"generation_cost": 0},
    )

    assert update["provenance"], "provenance must be captured on the Flow path"
    assert update["accounting"], "accounting must be captured on the Flow path"
    assert update["status"] == "completed"
    assert update["generation_cost"] == 0, "extra_fields must survive"
    assert "task_id" not in update, "flat remote keys stay suppressed"


def test_legacy_checkpoint_is_labelled_never_fabricated():
    rows = _scene_checkpoint_rows({"scene_01": dict(LEGACY_ALIBABA)})
    row = rows[0]

    assert row["provenance"] is None
    assert row["actual_provider"] is None
    assert row["actual_model"] is None
    assert row["cost"] is None, "a legacy generation_cost=0 is not a known zero"

    legacy = row["legacy_evidence"]
    assert legacy is not None
    assert "pre-Batch-1F" in legacy["source"]
    assert legacy["task_id"] == "8a0f0b6e-legacy"
    assert legacy["usage"] == {"duration": 5, "video_count": 1}
    assert legacy["attempt"] == 2
    assert legacy["remote_status"] == "SUCCEEDED"
    assert legacy["configured_model"] == "wanx2.1-t2v-turbo"
    assert "actual_model" not in legacy
    assert "cost" not in legacy


def test_record_with_real_provenance_has_no_legacy_block():
    checkpoints = {
        "scene_01": {
            "status": "completed",
            "sequence": 1,
            "provider": "alibaba",
            "task_id": "t-1",
            "provenance": {
                "provider": "alibaba",
                "model": "wan2.5-t2v-preview",
                "request_id": "r-9",
                "fallback_used": False,
            },
            "accounting": {
                "summary": {
                    "billing_status": "billed",
                    "cost": {"amount": 0.10, "currency": "USD"},
                    "cost_source": "provider",
                    "currency": "USD",
                    "attempt_count": 1,
                    "final_usage": {"duration": 5},
                }
            },
        }
    }
    row = _scene_checkpoint_rows(checkpoints)[0]

    assert row["legacy_evidence"] is None
    assert row["actual_model"] == "wan2.5-t2v-preview"
    assert row["actual_provider"] == "alibaba"
    assert row["cost"] == {"amount": 0.10, "currency": "USD"}


def test_checkpoint_without_observed_facts_has_no_legacy_block():
    row = _scene_checkpoint_rows({"scene_01": {"status": "pending"}})[0]
    assert row["legacy_evidence"] is None


def test_legacy_block_is_additive_and_leaves_existing_keys():
    """The Phase 1 read contract must not shift: ``job_id_present`` keeps its
    OpenRouter-only meaning while ``task_identity_present`` is kind-aware."""
    baseline = {"scene_01": {"status": "completed", "sequence": 1}}
    before = _scene_checkpoint_rows(baseline)[0]
    after = _scene_checkpoint_rows({"scene_01": dict(LEGACY_ALIBABA)})[0]

    for key in before:
        if key == "legacy_evidence":
            continue
        assert key in after, f"read key {key} was removed"
    assert before["job_id_present"] is False
    assert after["job_id_present"] is False
    assert after["task_identity_present"] is True






