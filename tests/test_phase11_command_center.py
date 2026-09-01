"""Hermetic Phase 11 Personal Command Center tests.

The Command Center must be THIN and SAFE:
- All state derivation flows through the Phase 10 ProductionExecutor; the six
  states are echoed verbatim, never re-implemented.
- Execution commands require explicit confirmation, return 202, run the
  executor in a background thread, and never stack (single-flight).
- Polling is read-only: it never spawns, continues, or retries execution.
- Publishing is a thin delegation to the existing publishing bridge.
- Secrets are never exposed; localhost-only binding; no database anywhere.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app.web import server as web

from app.orchestrator.models import (
    PipelineStage,
    ProductionBinding,
    ProjectManifest,
    StageResult,
)
from app.orchestrator.production_execution import (
    BoundProject,
    ProductionExecutionReport,
    UnitExecutionState,
    UnitProgress,
)
from app.production.models import (
    Production,
    ProductionCommand,
    ProductionUnit,
)
from app.production.storage import save_new_production

CREATED = "2026-09-01T09:00:00+00:00"
PID = "prd_20260901_cc_test_001"


@pytest.fixture()
def cc_server(monkeypatch, tmp_path):
    """Real localhost HTTP server with isolated intent/execution areas."""
    monkeypatch.setattr(web, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(web, "PRODUCTIONS_DIR", tmp_path / "productions")
    monkeypatch.setattr(web, "WEBAPP_DIR", tmp_path / "webapp")
    (tmp_path / "webapp").mkdir()
    (tmp_path / "webapp" / "index.html").write_text("<html>avm</html>", encoding="utf-8")
    monkeypatch.setattr(web, "_production_runs", {})

    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web.AVMHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path
    httpd.shutdown()
    httpd.server_close()


def make_production() -> Production:
    return Production(
        production_id=PID,
        title="Command Center Test Production",
        command=ProductionCommand(
            text="Create two units.",
            workload_shape="unit_count",
            intended_unit_count=2,
            issued_at=CREATED,
        ),
        units=[
            ProductionUnit(
                unit_id="unit-001",
                ordinal=1,
                unit_type="standalone_video",
                title="Unit 001",
                content_intent={"target_duration": 10, "number_of_scenes": 2},
            ),
            ProductionUnit(
                unit_id="unit-002",
                ordinal=2,
                unit_type="standalone_video",
                title="Unit 002",
                dependencies=["unit-001"],
                content_intent={"target_duration": 10, "number_of_scenes": 2},
            ),
        ],
        created_at=CREATED,
    )


@pytest.fixture()
def production_on_disk(cc_server):
    base, tmp = cc_server
    save_new_production(make_production(), root=tmp / "productions")
    return base, tmp


class FakeExecutor:
    """Executor stand-in: derives configured states, records execute() calls."""

    def __init__(self, states, projects=None, bindings=None):
        self.states = dict(states)
        self.projects = projects or {}
        self.bindings = bindings or {}
        self.execute_calls = []
        self.executed = threading.Event()

    def derive_progress(self):
        units = tuple(
            UnitProgress(
                unit_id,
                index + 1,
                UnitExecutionState(state),
                self.projects.get(unit_id),
                1 if unit_id in self.projects else None,
            )
            for index, (unit_id, state) in enumerate(self.states.items())
        )
        return ProductionExecutionReport(PID, 1, units)

    def discover_bindings(self, production=None):
        return self.bindings

    def execute(self, *, unit_id=None):
        self.execute_calls.append(unit_id)
        self.executed.set()
        return self.derive_progress()


def _install_executor(monkeypatch, executor):
    monkeypatch.setattr(web, "_production_executor", lambda production_id: executor)


def _get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post(url, payload=None):
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Production inspection
# ---------------------------------------------------------------------------


def test_production_list_loads_intent_and_counts(production_on_disk, monkeypatch):
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "NOT_STARTED", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    status, items = _get(base + "/api/productions")
    assert status == 200 and len(items) == 1
    item = items[0]
    assert item["production_id"] == PID
    assert item["title"] == "Command Center Test Production"
    assert item["unit_count"] == 2
    assert item["plan_version"] == 1
    assert item["command_text"] == "Create two units."
    assert item["counts"]["NOT_STARTED"] == 1
    assert item["counts"]["BLOCKED"] == 1


def test_production_detail_shows_units_states_and_dependencies(
    production_on_disk, monkeypatch
):
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "NOT_STARTED", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    status, payload = _get(base + "/api/productions/" + PID)
    assert status == 200
    assert payload["unit_count"] == 2
    assert payload["counts"]["BLOCKED"] == 1
    units = {u["intent"]["unit_id"]: u for u in payload["units"]}
    assert units["unit-002"]["intent"]["dependencies"] == ["unit-001"]
    assert units["unit-002"]["execution"]["state"] == "BLOCKED"
    assert units["unit-002"]["blocking_units"] == ["unit-001"]
    assert units["unit-001"]["execution"]["state"] == "NOT_STARTED"


def test_unknown_production_is_404_with_truthful_error(cc_server):
    base, _ = cc_server
    status, payload = _get(base + "/api/productions/prd_20260901_missing_999")
    assert status == 404
    assert payload["error"] == "PRODUCTION_NOT_FOUND"
    assert "message" in payload


# ---------------------------------------------------------------------------
# Progress derivation (six-state vocabulary, no cache, no persistence)
# ---------------------------------------------------------------------------


def test_progress_recomputed_on_every_call(production_on_disk, monkeypatch):
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "IN_FLIGHT", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    status, first = _get(base + "/api/productions/" + PID + "/progress")
    assert status == 200
    assert first["counts"]["IN_FLIGHT"] == 1

    # Executor state changes; the NEXT call must reflect it (no cache).
    fake.states["unit-001"] = "COMPLETED"
    status, second = _get(base + "/api/productions/" + PID + "/progress")
    assert status == 200
    assert second["counts"]["COMPLETED"] == 1
    assert second["counts"]["IN_FLIGHT"] == 0
    assert second["complete"] is False


def test_progress_vocabulary_is_exactly_the_phase10_six_states(
    production_on_disk, monkeypatch
):
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "COMPLETED", "unit-002": "INCOMPLETE"})
    _install_executor(monkeypatch, fake)

    _, payload = _get(base + "/api/productions/" + PID + "/progress")
    assert set(payload["counts"]) == {
        "NOT_STARTED", "BLOCKED", "IN_FLIGHT", "INCOMPLETE", "COMPLETED", "FAILED"
    }
    states = [u["state"] for u in payload["units"]]
    assert states == ["COMPLETED", "INCOMPLETE"]


def test_progress_gets_derived_but_never_persists(production_on_disk):
    base, tmp = production_on_disk
    # Real executor (no bindings): unbound units derive from intent alone.
    before = sorted(
        str(p.relative_to(tmp)) for p in (tmp / "productions").rglob("*") if p.is_file()
    )
    for _ in range(3):
        status, payload = _get(base + "/api/productions/" + PID + "/progress")
        assert status == 200
        assert payload["counts"]["NOT_STARTED"] == 1
        assert payload["counts"]["BLOCKED"] == 1
    after = sorted(
        str(p.relative_to(tmp)) for p in (tmp / "productions").rglob("*") if p.is_file()
    )
    assert before == after


def test_real_derivation_marks_unbound_units_not_started_or_blocked(
    production_on_disk
):
    base, _ = production_on_disk
    status, payload = _get(base + "/api/productions/" + PID + "/progress")
    assert status == 200
    states = {u["unit_id"]: u["state"] for u in payload["units"]}
    assert states["unit-001"] == "NOT_STARTED"
    assert states["unit-002"] == "BLOCKED"
    reason = next(u for u in payload["units"] if u["unit_id"] == "unit-002")["reason"]
    assert "unit-001" in reason


# ---------------------------------------------------------------------------
# Execution commands: confirmation, delegation, dependencies, single-flight
# ---------------------------------------------------------------------------


def test_execute_without_confirmation_is_400(production_on_disk, monkeypatch):
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "NOT_STARTED", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    status, payload = _post(base + "/api/productions/" + PID + "/execute", {})
    assert status == 400
    assert payload["error"] == "CONFIRMATION_REQUIRED"
    assert fake.execute_calls == []


def test_execute_with_confirmation_is_202_and_delegates_to_executor(
    production_on_disk, monkeypatch
):
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "NOT_STARTED", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    status, payload = _post(
        base + "/api/productions/" + PID + "/execute", {"confirm": True}
    )
    assert status == 202
    assert payload["status"] == "executing"
    assert payload["production_id"] == PID
    assert fake.executed.wait(timeout=5)
    assert fake.execute_calls == [None]


def test_execute_unknown_unit_is_400(production_on_disk, monkeypatch):
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "NOT_STARTED", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    status, payload = _post(
        base + "/api/productions/" + PID + "/execute?unit_id=unit-999",
        {"confirm": True},
    )
    assert status == 400
    assert payload["error"] == "UNIT_NOT_FOUND"


def test_execute_blocked_unit_is_400_with_blocking_list(production_on_disk, monkeypatch):
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "FAILED", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    status, payload = _post(
        base + "/api/productions/" + PID + "/execute?unit_id=unit-002",
        {"confirm": True},
    )
    assert status == 400
    assert payload["error"] == "BLOCKED_DEPENDENCY"
    assert payload["blocking_units"] == ["unit-001"]
    assert fake.execute_calls == []


def test_execute_completed_unit_is_a_noop_200(production_on_disk, monkeypatch):
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "COMPLETED", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    status, payload = _post(
        base + "/api/productions/" + PID + "/resume?unit_id=unit-001",
        {"confirm": True},
    )
    assert status == 200
    assert payload["status"] == "complete"
    assert fake.execute_calls == []


def test_resume_unit_delegates_with_unit_id(production_on_disk, monkeypatch):
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "INCOMPLETE", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    status, payload = _post(
        base + "/api/productions/" + PID + "/resume?unit_id=unit-001",
        {"confirm": True},
    )
    assert status == 202
    assert payload["unit_id"] == "unit-001"
    assert fake.executed.wait(timeout=5)
    assert fake.execute_calls == ["unit-001"]


def test_second_command_while_running_is_409_no_stacking(production_on_disk, monkeypatch):
    base, _ = production_on_disk
    web._production_runs[PID] = {"running": True, "action": "execute"}
    fake = FakeExecutor({"unit-001": "NOT_STARTED", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    status, payload = _post(
        base + "/api/productions/" + PID + "/execute", {"confirm": True}
    )
    assert status == 409
    assert payload["error"] == "EXECUTION_ALREADY_RUNNING"
    assert fake.execute_calls == []


def test_executor_exception_is_reported_truthfully_as_500(production_on_disk, monkeypatch):
    base, _ = production_on_disk

    class Exploding(FakeExecutor):
        def derive_progress(self):
            raise ValueError("ambiguous project evidence")

    _install_executor(monkeypatch, Exploding({}))
    status, payload = _get(base + "/api/productions/" + PID + "/progress")
    assert status == 500
    assert payload["error"] == "EXECUTOR_ERROR"
    assert "ValueError" in payload["message"]


def test_polling_never_triggers_execution(production_on_disk, monkeypatch):
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "IN_FLIGHT", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    for _ in range(4):
        status, payload = _get(base + "/api/productions/" + PID + "/progress")
        assert status == 200  # polling is always 200 OK, never 202
    assert fake.execute_calls == []


# ---------------------------------------------------------------------------
# Unit inspection, binding display, secret redaction, reconciliation
# ---------------------------------------------------------------------------


def _bound_project(tmp_path: Path, *, failed: bool = False) -> BoundProject:
    binding = ProductionBinding(
        production_id=PID,
        unit_id="unit-001",
        production_plan_version=1,
        unit_plan_identity="plan_unit_001",
        unit_plan_content_hash="a" * 64,
        project_id="proj_bound_001",
        bound_at=CREATED,
    )
    manifest = ProjectManifest(
        project_id="proj_bound_001",
        topic="Bound unit topic",
        production_binding=binding,
        mock_mode=True,
        scene_checkpoints={
            "scene_01": {
                "status": "COMPLETED",
                "provider": "openrouter_video",
                "job_id": "job-1",
                "api_key": "sk-supersecret-value",
            }
        },
    )
    manifest.initialize_stages()
    for stage in PipelineStage.get_order():
        if failed and stage == PipelineStage.VOICE:
            manifest.set_stage_result(
                StageResult.failed(stage, "voice provider exploded")
            )
        else:
            manifest.set_stage_result(StageResult.completed(stage))
    project_dir = tmp_path / "projects" / "proj_bound_001"
    project_dir.mkdir(parents=True, exist_ok=True)
    return BoundProject("proj_bound_001", project_dir, manifest)


def test_unit_detail_unbound_shows_intent_and_no_project(production_on_disk, monkeypatch):
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "NOT_STARTED", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    status, payload = _get(base + "/api/productions/" + PID + "/units/unit-001")
    assert status == 200
    intent = payload["unit_intent"]
    assert intent["unit_id"] == "unit-001"
    assert intent["unit_type"] == "standalone_video"
    assert intent["content_intent"]["target_duration"] == 10
    assert payload["execution"]["state"] == "NOT_STARTED"
    assert payload["execution"]["project_id"] is None
    assert payload["bound_project"] is None


def test_unit_detail_unknown_unit_is_400(production_on_disk, monkeypatch):
    base, _ = production_on_disk
    _install_executor(monkeypatch, FakeExecutor({"unit-001": "NOT_STARTED"}))
    status, payload = _get(base + "/api/productions/" + PID + "/units/unit-999")
    assert status == 400
    assert payload["error"] == "UNIT_NOT_FOUND"


def test_unit_detail_bound_project_snapshot_and_secret_redaction(
    production_on_disk, monkeypatch
):
    base, tmp = production_on_disk
    bound = _bound_project(tmp)
    fake = FakeExecutor(
        {"unit-001": "INCOMPLETE", "unit-002": "BLOCKED"},
        projects={"unit-001": "proj_bound_001"},
        bindings={"unit-001": bound},
    )
    _install_executor(monkeypatch, fake)

    status, payload = _get(base + "/api/productions/" + PID + "/units/unit-001")
    assert status == 200
    assert payload["execution"]["project_id"] == "proj_bound_001"
    project = payload["bound_project"]
    assert project["project_id"] == "proj_bound_001"
    assert project["production_binding"]["unit_id"] == "unit-001"
    assert project["stages"]["CONTENT"]["status"] == "COMPLETED"
    rows = {row["scene_id"]: row for row in payload["scene_checkpoint_rows"]}
    assert rows["scene_01"]["provider"] == "openrouter_video"
    # Secrets never leave the server (SEC-001).
    assert "sk-supersecret-value" not in json.dumps(payload)
    assert project["scene_checkpoints"]["scene_01"]["api_key"] == "<redacted>"
    # Config snapshots / Flow URLs are omitted entirely.
    assert "flow_project_url" not in project
    assert "voice_config" not in project


def test_unit_detail_failure_details_for_failed_unit(
    production_on_disk, monkeypatch
):
    base, tmp = production_on_disk
    bound = _bound_project(tmp, failed=True)
    fake = FakeExecutor(
        {"unit-001": "FAILED", "unit-002": "BLOCKED"},
        projects={"unit-001": "proj_bound_001"},
        bindings={"unit-001": bound},
    )
    _install_executor(monkeypatch, fake)

    status, payload = _get(base + "/api/productions/" + PID + "/units/unit-001")
    assert status == 200
    failures = {f["stage"]: f for f in payload["failure_details"]}
    assert failures["VOICE"]["error"] == "voice provider exploded"


def test_reconciliation_is_read_only_and_unbound_is_400(
    production_on_disk, monkeypatch
):
    base, tmp = production_on_disk
    bound = _bound_project(tmp)
    fake = FakeExecutor(
        {"unit-001": "INCOMPLETE", "unit-002": "BLOCKED"},
        projects={"unit-001": "proj_bound_001"},
        bindings={"unit-001": bound},
    )
    _install_executor(monkeypatch, fake)

    calls = []

    import app.orchestrator.reconciliation as reconciliation_module

    class FakeReport:
        def to_dict(self):
            return {
                "project_id": "proj_bound_001",
                "verdict": "PROJECT_INCOMPLETE",
                "reasons": ["voice audio missing"],
                "content": {"status": "VALID"},
                "voice": {"status": "MISSING"},
                "scenes": {"status": "MISSING", "items": []},
                "captions": {"status": "NOT_REQUIRED"},
                "assembly": {"status": "MISSING"},
            }

    def fake_reconcile(project_id, project_dir=None, *, persist=False):
        calls.append((project_id, persist))
        return FakeReport()

    monkeypatch.setattr(reconciliation_module, "reconcile_project", fake_reconcile)

    status, payload = _get(
        base + "/api/productions/" + PID + "/units/unit-001/reconciliation"
    )
    assert status == 200
    assert calls == [("proj_bound_001", False)]  # persist=False, read-only
    assert payload["reconciliation"]["verdict"] == "PROJECT_INCOMPLETE"
    assert payload["persisted"] is False

    # Unbound unit has nothing to reconcile.
    status, payload = _get(
        base + "/api/productions/" + PID + "/units/unit-002/reconciliation"
    )
    assert status == 400
    assert payload["error"] == "UNIT_NOT_BOUND"


# ---------------------------------------------------------------------------
# Publishing adapter (thin delegation to the existing bridge)
# ---------------------------------------------------------------------------


def test_publish_requires_confirmation_and_completed_unit(production_on_disk, monkeypatch):
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "INCOMPLETE", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    status, payload = _post(
        base + "/api/productions/" + PID + "/units/unit-001/publish", {}
    )
    assert status == 400
    assert payload["error"] == "CONFIRMATION_REQUIRED"

    status, payload = _post(
        base + "/api/productions/" + PID + "/units/unit-001/publish",
        {"confirm": True},
    )
    assert status == 400
    assert payload["error"] == "UNIT_NOT_COMPLETED"
    assert payload["state"] == "INCOMPLETE"


def test_publish_delegates_to_existing_bridge(production_on_disk, monkeypatch):
    base, _ = production_on_disk
    fake = FakeExecutor(
        {"unit-001": "COMPLETED", "unit-002": "BLOCKED"},
        projects={"unit-001": "proj_bound_001"},
    )
    _install_executor(monkeypatch, fake)

    delegated = []

    def fake_publish(project_id, privacy_level=None):
        delegated.append((project_id, privacy_level))
        return 200, {"state": "VERIFIED", "tiktok_post_id": "abc"}

    monkeypatch.setattr(web, "tiktok_publish_now", fake_publish)

    status, payload = _post(
        base + "/api/productions/" + PID + "/units/unit-001/publish",
        {"confirm": True},
    )
    assert status == 200
    assert payload["status"] == "published"
    assert payload["project_id"] == "proj_bound_001"
    assert delegated == [("proj_bound_001", None)]


def test_publish_rejects_platforms_without_existing_adapter(
    production_on_disk, monkeypatch
):
    base, _ = production_on_disk
    fake = FakeExecutor(
        {"unit-001": "COMPLETED", "unit-002": "BLOCKED"},
        projects={"unit-001": "proj_bound_001"},
    )
    _install_executor(monkeypatch, fake)

    status, payload = _post(
        base + "/api/productions/" + PID + "/units/unit-001/publish",
        {"confirm": True, "platforms": ["youtube"]},
    )
    assert status == 400
    assert payload["error"] == "PUBLISH_PLATFORM_NOT_SUPPORTED"


# ---------------------------------------------------------------------------
# Legacy compatibility, localhost binding, no database
# ---------------------------------------------------------------------------


def test_legacy_project_visible_but_never_adopted(production_on_disk):
    base, tmp = production_on_disk
    legacy = tmp / "projects" / "legacy_proj_001"
    legacy.mkdir(parents=True)
    (legacy / "manifest.json").write_text(json.dumps({
        "project_id": "legacy_proj_001",
        "topic": "Legacy Phase 1-8 project",
        "created_at": CREATED,
        "stages": {},
    }), encoding="utf-8")

    # Visible via the existing project inspection API (Phase 7A)...
    status, items = _get(base + "/api/projects")
    assert status == 200
    assert any(i["project_id"] == "legacy_proj_001" for i in items)

    # ...but never adopted into production units (no binding authority).
    status, payload = _get(base + "/api/productions/" + PID + "/progress")
    assert status == 200
    assert all(u["project_id"] is None for u in payload["units"])
    assert payload["counts"]["NOT_STARTED"] == 1
    assert payload["counts"]["BLOCKED"] == 1


def test_server_binds_localhost_only(cc_server):
    # The fixture itself is the proof: it only binds 127.0.0.1 successfully.
    base, _ = cc_server
    assert base.startswith("http://127.0.0.1:")
    status, payload = _get(base + "/api/health")
    assert status == 200 and payload["ok"] is True


def test_no_database_layer_anywhere_in_web_module():
    source = Path(web.__file__).read_text(encoding="utf-8")
    for forbidden in ("sqlite3", "sqlalchemy", "psycopg", "pymysql", "create_engine"):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# Post-audit remediation regressions (F-01/F-02/F-03/F-05/F-07/F-08/F-09/F-11)
# ---------------------------------------------------------------------------


def test_resume_failed_unit_is_truthful_409_without_execution(
    production_on_disk, monkeypatch
):
    """F-01/F-11: resuming a FAILED unit must be a truthful 409, never a 202 lie."""
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "FAILED", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    for action in ("execute", "resume"):
        status, payload = _post(
            base + f"/api/productions/{PID}/{action}?unit_id=unit-001",
            {"confirm": True},
        )
        assert status == 409
        assert payload["error"] == "FAILED_UNIT_REQUIRES_EXPLICIT_RECOVERY"
        assert payload["unit_id"] == "unit-001"
        assert payload["state"] == "FAILED"
        # The explanation points ONLY at existing recovery mechanisms.
        assert "does not automatically resume FAILED" in payload["message"]
        assert "produce.py --resume" in payload["message"]

    # Nothing was executed and no worker/run registry entry was created.
    assert fake.execute_calls == []
    assert web._production_runs == {}


def test_malformed_intent_returns_structured_error(production_on_disk):
    """F-03: unreadable production intent fails loudly and actionably."""
    base, tmp = production_on_disk
    (tmp / "productions" / PID / "production.json").write_text(
        "{ not valid json", encoding="utf-8"
    )

    for url in (
        f"/api/productions/{PID}",
        f"/api/productions/{PID}/progress",
        f"/api/productions/{PID}/units/unit-001",
        f"/api/productions/{PID}/units/unit-001/reconciliation",
    ):
        status, payload = _get(base + url)
        assert status == 500
        assert payload["error"] == "PRODUCTION_INTENT_MALFORMED"
        assert "guidance" in payload

    status, payload = _post(
        base + f"/api/productions/{PID}/execute", {"confirm": True}
    )
    assert status == 500
    assert payload["error"] == "PRODUCTION_INTENT_MALFORMED"

    # 404 semantics are preserved for genuinely unknown productions.
    status, _ = _get(base + "/api/productions/prd_does_not_exist")
    assert status == 404


def test_post_envelope_on_unexpected_error(production_on_disk, monkeypatch):
    """F-02: unexpected POST failures return a truthful JSON 500 envelope."""
    base, _ = production_on_disk

    def exploding(production_id, unit_id, action, body):
        raise RuntimeError("unexpected boom")

    monkeypatch.setattr(web, "production_command_payload", exploding)

    status, payload = _post(
        base + f"/api/productions/{PID}/execute", {"confirm": True}
    )
    assert status == 500
    assert payload["error"] == "INTERNAL_ERROR"
    assert "RuntimeError" in payload["message"]
    assert "unexpected boom" in payload["message"]


def test_secret_redaction_extends_to_unexpected_key_names(
    production_on_disk, monkeypatch
):
    """F-07: secret-like values under less common key names stay redacted."""
    base, tmp = production_on_disk
    bound = _bound_project(tmp)
    bound.manifest.scene_checkpoints["scene_01"]["config_snapshot"] = {
        "session_id": "SESS-secret-1",
        "cookie": "COOKIE-secret-1",
        "webhook_url": "https://hooks.example/x",
        "client_secret": "cs-secret-1",
        "signature": "sig-secret-1",
    }
    fake = FakeExecutor(
        {"unit-001": "INCOMPLETE", "unit-002": "BLOCKED"},
        projects={"unit-001": "proj_bound_001"},
        bindings={"unit-001": bound},
    )
    _install_executor(monkeypatch, fake)

    status, payload = _get(base + f"/api/productions/{PID}/units/unit-001")
    assert status == 200
    raw = json.dumps(payload)
    for secret in (
        "SESS-secret-1",
        "COOKIE-secret-1",
        "https://hooks.example/x",
        "cs-secret-1",
        "sig-secret-1",
    ):
        assert secret not in raw
    snapshot = payload["bound_project"]["scene_checkpoints"]["scene_01"][
        "config_snapshot"
    ]
    for key in ("session_id", "cookie", "webhook_url", "client_secret", "signature"):
        assert snapshot[key] == "<redacted>"


def test_scene_checkpoint_rows_expose_generation_cost_when_available(
    production_on_disk, monkeypatch
):
    """F-08: real cost data (Flow generation credits) is surfaced, never fabricated."""
    base, tmp = production_on_disk
    bound = _bound_project(tmp)
    bound.manifest.scene_checkpoints["scene_01"]["generation_cost"] = 12
    bound.manifest.scene_checkpoints["scene_02"] = {
        "status": "COMPLETED",
        "provider": "mock",
    }
    fake = FakeExecutor(
        {"unit-001": "INCOMPLETE", "unit-002": "BLOCKED"},
        projects={"unit-001": "proj_bound_001"},
        bindings={"unit-001": bound},
    )
    _install_executor(monkeypatch, fake)

    status, payload = _get(base + f"/api/productions/{PID}/units/unit-001")
    assert status == 200
    rows = {row["scene_id"]: row for row in payload["scene_checkpoint_rows"]}
    assert rows["scene_01"]["generation_cost"] == 12
    assert rows["scene_02"]["generation_cost"] is None  # absent, not fabricated


def test_successful_run_is_evicted_from_registry(production_on_disk, monkeypatch):
    """F-09: successful runs leave the ephemeral registry (no display value)."""
    base, _ = production_on_disk
    fake = FakeExecutor({"unit-001": "INCOMPLETE", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    status, _ = _post(
        base + f"/api/productions/{PID}/execute?unit_id=unit-001",
        {"confirm": True},
    )
    assert status == 202
    assert fake.executed.wait(timeout=5)
    for _ in range(200):
        if PID not in web._production_runs:
            break
        time.sleep(0.02)
    assert PID not in web._production_runs


def test_failed_run_is_retained_for_truthful_display(
    production_on_disk, monkeypatch
):
    """F-09: failed runs stay registered so 'last run failed' remains truthful."""

    class FailingExecutor(FakeExecutor):
        def execute(self, *, unit_id=None):
            self.execute_calls.append(unit_id)
            self.executed.set()
            raise RuntimeError("engine exploded")

    base, _ = production_on_disk
    fake = FailingExecutor({"unit-001": "INCOMPLETE", "unit-002": "BLOCKED"})
    _install_executor(monkeypatch, fake)

    status, _ = _post(
        base + f"/api/productions/{PID}/execute?unit_id=unit-001",
        {"confirm": True},
    )
    assert status == 202
    assert fake.executed.wait(timeout=5)
    for _ in range(200):
        run = web._production_runs.get(PID)
        if run and not run.get("running"):
            break
        time.sleep(0.02)
    run = web._production_runs[PID]
    assert run["running"] is False
    assert "engine exploded" in run["error"]


def test_phase11_ui_source_contract():
    """F-05: dependency-free static source verification of the UI contract.

    The repository has no JavaScript test infrastructure, and Phase 11
    explicitly forbids adding frontend framework/build tooling, so the UI
    contract is pinned via static source checks instead. This is a reasoned
    compromise: it verifies structure (confirmation gating, scope data,
    FAILED-unit handling, escaping of known dynamic interpolations), not
    runtime behavior.
    """
    root = Path(web.__file__).resolve().parents[2]
    views = root / "webapp" / "assets" / "js" / "views"
    src = {
        name: (views / name).read_text(encoding="utf-8")
        for name in ("productions.js", "production-detail.js", "unit-detail.js")
    }
    api_src = (root / "webapp" / "assets" / "js" / "api.js").read_text(
        encoding="utf-8"
    )

    # CONF-001: every state-changing surface is confirmation-gated.
    for name, text in src.items():
        assert "CC.confirmDialog" in text, name

    # F-04: confirmations communicate scope, unit count and first action.
    assert 'data-total="' in src["productions.js"]
    assert 'data-total="' in src["production-detail.js"]
    assert 'data-first="' in src["production-detail.js"]
    assert "First action" in src["production-detail.js"]
    assert "First action" in src["unit-detail.js"]
    assert "Scope: 1 unit" in src["unit-detail.js"]

    # F-01: the UI never offers Resume Unit for FAILED units.
    assert 'state === "INCOMPLETE" || state === "IN_FLIGHT"' in src["unit-detail.js"]
    assert "produce.py --resume" in src["unit-detail.js"]  # truthful recovery note
    assert 'exec.state === "FAILED"' in src["production-detail.js"]

    # F-10: known dynamic interpolations are escaped before HTML insertion.
    assert "COMPONENTS.esc(intent.dependencies.length" in src["unit-detail.js"]
    assert "COMPONENTS.esc(u.blocking_units.join" in src["production-detail.js"]
    assert "COMPONENTS.esc(" in src["productions.js"]

    # F-08: real generation cost is rendered when available.
    assert "generation_cost" in src["unit-detail.js"]

    # Structured API errors surface truthfully in the UI (F-01/F-02/F-03).
    assert "errBody.message || errBody.error || errBody.detail" in api_src




