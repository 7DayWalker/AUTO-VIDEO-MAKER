"""Phase 2B targeted tests — safe parallel derivation.

Covers exactly the approved Core performance hardening and nothing else:

* B-01 - ``parallel_map``: bounded, order-preserving, read-only fan-out of
  the ffprobe-backed validations and per-unit reconciliations, with an
  operator serial kill switch.
* B-02 - ``_scene_evidence`` and the bound-unit reconcile fan-out produce
  identical results in parallel and serial mode; exceptions surface
  unchanged; ``persist=False`` remains universal on these paths.
* B-03 - ``derive_progress_with_bindings`` (ONE scan) matches the former
  two-call sequence, and the unit detail endpoint no longer scans the
  projects tree twice per request.
* B-04 - a failed worker-thread spawn cannot leave a phantom
  ``running=True`` registry entry (which would pin every later execute
  to a false 409).

Hermetic: no network, no providers, no media, no ffprobe binaries.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import app.orchestrator.reconciliation as reconciliation
from app.orchestrator.models import ProductionBinding, ProjectManifest
from app.orchestrator.production_execution import (
    BoundProject,
    ProductionExecutionReport,
    ProductionExecutor,
    UnitExecutionState,
    UnitProgress,
)
from app.orchestrator.reconciliation import parallel_map
from app.content.models import ProductionPlan, Scene
from app.production.models import Production, ProductionCommand, ProductionUnit
from app.production.storage import save_new_production

CREATED = "2026-09-20T09:00:00+00:00"
PID = "prd_20260920_phase2b_test_001"


def _production(unit_ids=("unit-001", "unit-002", "unit-003")) -> Production:
    units = [
        ProductionUnit(
            unit_id=unit_id,
            ordinal=index + 1,
            unit_type="standalone_video",
            title=f"Unit {index + 1}",
            content_intent={"target_duration": 10, "number_of_scenes": 2},
        )
        for index, unit_id in enumerate(unit_ids)
    ]
    return Production(
        production_id=PID,
        title="Phase 2B Parallel Derivation Test",
        command=ProductionCommand(
            text="Create units.",
            workload_shape="unit_count",
            intended_unit_count=len(units),
            issued_at=CREATED,
        ),
        units=units,
        created_at=CREATED,
    )


def _bound_manifest(unit_id: str, project_id: str) -> ProjectManifest:
    return ProjectManifest(
        project_id=project_id,
        topic="phase2b fixture",
        stages={},
        scene_checkpoints={},
        production_binding=ProductionBinding(
            production_id=PID,
            unit_id=unit_id,
            production_plan_version=1,
            unit_plan_identity=f"identity-{unit_id}",
            unit_plan_content_hash="0" * 64,
            project_id=project_id,
            bound_at=CREATED,
        ),
        updated_at=CREATED,
    )


class _FakeReport:
    def __init__(self, complete=False):
        self.complete = complete
        self.verdict = None


# ---------------------------------------------------------------------------
# B-01 - the shared parallel_map primitive
# ---------------------------------------------------------------------------


def test_parallel_map_preserves_exact_input_order_and_runs_every_item(monkeypatch):
    monkeypatch.setenv("AVM_RECONCILE_MAX_PARALLELISM", "8")
    items = list(range(12))
    seen = []
    lock = threading.Lock()

    def work(value):
        time.sleep(0.001 * ((value % 4)))  # stagger completion order
        with lock:
            seen.append(value)
        return value * 2

    results = parallel_map(work, items)
    assert results == [value * 2 for value in items], "output order must match input"
    assert sorted(seen) == items, "every item runs exactly once"


def test_parallel_map_actually_uses_multiple_workers_when_allowed(monkeypatch):
    monkeypatch.setenv("AVM_RECONCILE_MAX_PARALLELISM", "8")
    threads = set()
    lock = threading.Lock()

    def work(value):
        with lock:
            threads.add(threading.get_ident())
        time.sleep(0.05)
        return value

    parallel_map(work, list(range(10)))
    assert len(threads) > 1, "ten staggered tasks must not all run on one thread"


def test_parallel_map_serial_kill_switch_and_invalid_env(monkeypatch):
    monkeypatch.setenv("AVM_RECONCILE_MAX_PARALLELISM", "1")
    threads = set()
    lock = threading.Lock()

    def work(value):
        with lock:
            threads.add(threading.get_ident())
        time.sleep(0.01)
        return value

    assert parallel_map(work, list(range(6))) == list(range(6))
    assert len(threads) == 1, "cap=1 must run everything on the calling thread"

    monkeypatch.setenv("AVM_RECONCILE_MAX_PARALLELISM", "not-a-number")
    threads.clear()
    assert parallel_map(work, list(range(6))) == list(range(6))
    assert len(threads) == 1, "unreadable setting must fail conservatively (serial)"


def test_parallel_map_propagates_first_failure_in_item_order(monkeypatch):
    monkeypatch.setenv("AVM_RECONCILE_MAX_PARALLELISM", "8")

    def work(value):
        if value >= 2:
            time.sleep(0.02)
            raise RuntimeError(f"late-{value}")
        if value == 1:
            raise ValueError("second-item-fails")
        return value

    with pytest.raises(ValueError, match="second-item-fails"):
        parallel_map(work, list(range(6)))


def test_parallel_map_single_and_empty_items_run_in_place(monkeypatch):
    monkeypatch.setenv("AVM_RECONCILE_MAX_PARALLELISM", "8")
    assert parallel_map(lambda v: v, []) == []
    assert parallel_map(lambda v: v * 3, [7]) == [21]


# ---------------------------------------------------------------------------
# B-02 - scene evidence and bound-unit reconcile fan-out stay truthful
# ---------------------------------------------------------------------------


def _plan(scene_count: int) -> ProductionPlan:
    return ProductionPlan(
        project_id="phase2b-project",
        title="phase2b",
        hook="hook",
        script="script",
        estimated_duration=float(scene_count * 5),
        scenes=[
            Scene(
                id=f"scene_{index:02d}",
                sequence=index,
                narration=f"narration {index}",
                visual_prompt=f"prompt {index}",
                estimated_duration=5.0,
            )
            for index in range(1, scene_count + 1)
        ],
    )


def _scene_manifest() -> ProjectManifest:
    return ProjectManifest(
        project_id="phase2b-project",
        topic="phase2b scenes",
        stages={},
        scene_checkpoints={},
        updated_at=CREATED,
    )


def test_scene_evidence_parallel_results_identical_to_serial(tmp_path, monkeypatch):
    monkeypatch.setenv("AVM_RECONCILE_MAX_PARALLELISM", "8")
    plan = _plan(8)
    manifest = _scene_manifest()
    calls = []
    threads = set()
    lock = threading.Lock()

    def fake_validate(path, ratio):
        with lock:
            calls.append(str(path))
            threads.add(threading.get_ident())
        time.sleep(0.01)
        return {"valid": True, "echo": str(path), "ratio": ratio}

    monkeypatch.setattr(reconciliation, "validate_video_file", fake_validate)
    parallel = reconciliation._scene_evidence(manifest, tmp_path, plan)

    monkeypatch.setenv("AVM_RECONCILE_MAX_PARALLELISM", "1")
    serial = reconciliation._scene_evidence(manifest, tmp_path, plan)

    assert parallel == serial, "parallel derivation must change wall time only"
    assert [item["scene_id"] for item in parallel["items"]] == [
        f"scene_{index:02d}" for index in range(1, 9)
    ], "items keep exact plan sequence order"
    assert all(
        item["validation"]["echo"] == item["path"] for item in parallel["items"]
    ), "each scene's validation belongs to that scene's own path"
    assert len(calls) == 16, "8 parallel probes + 8 serial probes, each exactly once"
    assert len(threads) >= 3, (
        "the parallel pass spread across multiple workers and the serial "
        "pass ran on the calling thread"
    )
    assert parallel["status"] == "VALID" and parallel["required_count"] == 8


def test_bound_unit_reconcile_fanout_matches_serial_and_is_read_only(monkeypatch):
    monkeypatch.setenv("AVM_RECONCILE_MAX_PARALLELISM", "8")
    production = _production()
    raw_a, raw_c = {"raw": "a"}, {"raw": "c"}
    bindings = {
        "unit-001": BoundProject(
            directory_id="proj-a",
            project_dir=Path("proj-a"),
            manifest=_bound_manifest("unit-001", "proj-a"),
            manifest_raw=raw_a,
        ),
        "unit-002": BoundProject(
            directory_id="proj-b",
            project_dir=Path("proj-b"),
            manifest=_bound_manifest("unit-002", "proj-b"),
            manifest_raw=None,
        ),
        "unit-003": BoundProject(
            directory_id="proj-c",
            project_dir=Path("proj-c"),
            manifest=_bound_manifest("unit-003", "proj-c"),
            manifest_raw=raw_c,
        ),
    }
    executor = ProductionExecutor.__new__(ProductionExecutor)
    seen = []
    lock = threading.Lock()

    def fake_reconcile(project_id, project_dir, persist=False, **kwargs):
        with lock:
            seen.append((project_id, persist, kwargs))
        time.sleep(0.02)
        # unit-001 reports complete, the others do not.
        return _FakeReport(complete=project_id == "proj-a")

    executor._reconcile = fake_reconcile
    states = executor._derive_bound_progress(production, bindings)

    assert list(states) == [
        "unit-001", "unit-002", "unit-003",
    ], "states keep production.units order"
    assert states["unit-001"].state == UnitExecutionState.COMPLETED
    assert states["unit-002"].state == UnitExecutionState.INCOMPLETE
    assert states["unit-003"].state == UnitExecutionState.INCOMPLETE
    assert states["unit-001"].project_id == "proj-a"
    assert states["unit-001"].bound_plan_version == 1
    assert len(seen) == 3, "exactly one full reconcile per bound unit"
    assert all(persist is False for _, persist, _ in seen), "GET paths never persist"
    by_project = {project_id: kwargs for project_id, _, kwargs in seen}
    assert by_project["proj-a"].get("manifest_raw") is raw_a
    assert by_project["proj-c"].get("manifest_raw") is raw_c
    assert "manifest_raw" not in by_project["proj-b"], (
        "units without shared-scan bytes keep the original call shape"
    )

    # Serial kill switch returns the identical mapping.
    monkeypatch.setenv("AVM_RECONCILE_MAX_PARALLELISM", "1")
    seen.clear()
    serial_states = executor._derive_bound_progress(production, bindings)
    assert {
        unit_id: (progress.state, progress.project_id, progress.reason)
        for unit_id, progress in serial_states.items()
    } == {
        unit_id: (progress.state, progress.project_id, progress.reason)
        for unit_id, progress in states.items()
    }


def test_reconcile_exception_surfaces_unchanged_from_fanout(monkeypatch):
    monkeypatch.setenv("AVM_RECONCILE_MAX_PARALLELISM", "8")
    production = _production()
    bindings = {
        unit_id: BoundProject(
            directory_id=f"proj-{unit_id}",
            project_dir=Path(f"proj-{unit_id}"),
            manifest=_bound_manifest(unit_id, f"proj-{unit_id}"),
            manifest_raw=None,
        )
        for unit_id in ("unit-001", "unit-002", "unit-003")
    }
    executor = ProductionExecutor.__new__(ProductionExecutor)

    def fake_reconcile(project_id, project_dir, persist=False, **kwargs):
        if project_id == "proj-unit-002":
            raise ValueError("ambiguous project evidence; simulated")
        return _FakeReport()

    executor._reconcile = fake_reconcile
    with pytest.raises(ValueError, match="ambiguous project evidence"):
        executor._derive_bound_progress(production, bindings)


# ---------------------------------------------------------------------------
# B-03 - one scan serves unit detail; values match the two-call sequence
# ---------------------------------------------------------------------------


def _save(tmp_path):
    root = tmp_path / "productions"
    save_new_production(_production(("unit-001", "unit-002")), root=root)
    return root


def test_derive_progress_with_bindings_matches_two_call_sequence(tmp_path, monkeypatch):
    root = _save(tmp_path)
    projects_root = tmp_path / "projects"  # nonexistent → no bindings, no scans
    executor = ProductionExecutor(
        PID, productions_root=root, projects_root=projects_root
    )
    two_call_report = executor.derive_progress()
    calls = []
    original = ProductionExecutor.discover_bindings

    def counting(self, production=None):
        calls.append(True)
        return original(self, production)

    monkeypatch.setattr(ProductionExecutor, "discover_bindings", counting)
    report, bindings = executor.derive_progress_with_bindings()
    assert len(calls) == 1, "the merged path must scan exactly once"
    assert bindings == {}
    assert [
        (item.unit_id, item.state, item.project_id) for item in report.units
    ] == [
        (item.unit_id, item.state, item.project_id) for item in two_call_report.units
    ]
    assert report.counts() == two_call_report.counts()
    assert report.complete == two_call_report.complete


def test_unit_detail_payload_scans_project_tree_once(tmp_path, monkeypatch):
    import app.web.server as web

    root = _save(tmp_path)
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", root)
    monkeypatch.setattr(web, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(web, "_production_runs", {})

    calls = []
    original = ProductionExecutor.discover_bindings

    def counting(self, production=None):
        calls.append(True)
        return original(self, production)

    monkeypatch.setattr(ProductionExecutor, "discover_bindings", counting)

    status, payload = web.unit_detail_payload(PID, "unit-001")
    assert status == 200
    assert len(calls) == 1, (
        "unit detail previously scanned the whole projects tree twice per "
        "request (derive_progress + discover_bindings); Phase 2B serves both "
        "consumers from one scan"
    )
    assert payload["execution"]["state"] == "NOT_STARTED"
    assert payload["bound_project"] is None
    assert payload["unit_intent"]["unit_id"] == "unit-001"
    assert payload["sample"] is False


# ---------------------------------------------------------------------------
# B-04 - worker spawn failure cannot leave a phantom running entry
# ---------------------------------------------------------------------------


class _BoomThread:
    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        raise RuntimeError("can't start new thread")


def test_thread_start_failure_leaves_no_running_registry_entry(monkeypatch):
    import app.web.server as web

    production = _production(("unit-001", "unit-002"))
    monkeypatch.setattr(web, "_production_or_none", lambda production_id: production)
    report = ProductionExecutionReport(
        PID,
        1,
        tuple(
            UnitProgress(
                unit_id,
                index + 1,
                UnitExecutionState.NOT_STARTED,
            )
            for index, unit_id in enumerate(("unit-001", "unit-002"))
        ),
    )
    monkeypatch.setattr(web, "_derive_progress", lambda production_id: report)

    executed = []

    class FakeExec:
        def execute(self, *, unit_id=None):
            executed.append(unit_id)

    monkeypatch.setattr(web, "_production_executor", lambda production_id: FakeExec())
    monkeypatch.setattr(web, "_production_runs", {})
    monkeypatch.setattr(web.threading, "Thread", _BoomThread)

    status, payload = web.production_command_payload(
        PID, None, "execute", {"confirm": True}
    )
    assert status == 500
    assert payload["error"] == "EXECUTOR_ERROR"
    assert executed == [], "nothing may execute when the worker cannot start"
    assert web._production_runs == {}, (
        "the registry must not keep running=True after a failed spawn, or "
        "every later execute would be a false 409 EXECUTION_ALREADY_RUNNING"
    )
