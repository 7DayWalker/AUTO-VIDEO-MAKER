from concurrent.futures import ThreadPoolExecutor

import pytest

from app.production.storage import load_production
from app.repurpose.models import RepurposeError
from app.repurpose.service import batch_status, create_batch, render_batch
from app.repurpose.storage import load_job, load_plan, save_job, unit_dir
from tests.repurpose_fixtures import PID, FakePlanner, request_fixture, source_fixture


def test_batch_single_revision_and_request_idempotency(tmp_path):
    evidence = source_fixture(tmp_path)
    request = request_fixture(evidence).model_dump(mode="json")
    provider = FakePlanner()
    job = create_batch(PID, request, root=tmp_path, background=False, provider=provider)
    assert job["status"] == "planned", job
    assert [c["unit_id"] for c in job["clips"]] == ["unit-short-001", "unit-short-002", "unit-short-003"]
    production = load_production(PID, root=tmp_path)
    assert production.plan_version == 2 and production.units[0].unit_id == "unit-001"
    assert len(production.units[-1].source_lineage.source_segment_ids) == 9
    repeated = create_batch(PID, request, root=tmp_path, background=False, provider=provider)
    assert repeated["batch_id"] == job["batch_id"] and len(provider.calls) == 1
    assert all(job[k] == v for k, v in {"llm_calls": 1, "uploads": 0, "stt_calls": 0, "vision_calls": 0}.items())


def test_concurrent_same_request_dispatches_once(tmp_path):
    evidence = source_fixture(tmp_path)
    request = request_fixture(evidence).model_dump(mode="json")
    provider = FakePlanner()
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = list(pool.map(lambda _: create_batch(PID, request, root=tmp_path, background=False, provider=provider), range(3)))
    assert len(provider.calls) == 1
    assert len({j["batch_id"] for j in jobs}) == 1


def test_request_conflict_and_stale_evidence(tmp_path):
    evidence = source_fixture(tmp_path)
    request = request_fixture(evidence).model_dump(mode="json")
    create_batch(PID, request, root=tmp_path, background=False, provider=FakePlanner())
    with pytest.raises(RepurposeError, match="different settings"):
        create_batch(PID, {**request, "framing": "center_crop"}, root=tmp_path, provider=FakePlanner())
    with pytest.raises(RepurposeError, match="Reload"):
        create_batch(PID, {**request, "request_id": "second-stale-call", "evidence_revision": "b"*64}, root=tmp_path, provider=FakePlanner())


def test_render_reuses_plan_and_never_calls_provider(tmp_path):
    evidence = source_fixture(tmp_path)
    provider = FakePlanner()
    job = create_batch(PID, request_fixture(evidence).model_dump(mode="json"), root=tmp_path, background=False, provider=provider)
    calls = []
    body = {"request_id": "render-fixture-one", "confirm": True, "expected_plan_hash": job["plan_hash"], "unit_ids": ["unit-short-002"]}
    rendered = render_batch(PID, job["batch_id"], body, root=tmp_path, background=False,
        renderer=lambda p, u, **kw: calls.append(u))
    assert rendered["units"]["unit-short-002"]["status"] == "rendered"
    render_batch(PID, job["batch_id"], body, root=tmp_path, background=False, renderer=lambda *a, **k: pytest.fail("duplicate render"))
    assert calls == ["unit-short-002"] and len(provider.calls) == 1


def test_restart_status_never_dispatches_a_second_call(tmp_path):
    evidence = source_fixture(tmp_path)
    job = create_batch(PID, request_fixture(evidence).model_dump(mode="json"), root=tmp_path, background=False, provider=FakePlanner())
    saved = load_job(PID, job["batch_id"], tmp_path)
    saved["status"] = "planning"
    save_job(PID, job["batch_id"], saved, tmp_path)
    assert batch_status(PID, job["batch_id"], root=tmp_path)["status"] == "uncertain"


def test_second_batch_allocates_new_units(tmp_path):
    evidence = source_fixture(tmp_path)
    request = request_fixture(evidence).model_dump(mode="json")
    create_batch(PID, request, root=tmp_path, background=False, provider=FakePlanner())
    second = create_batch(PID, {**request, "request_id": "second-valid-batch"}, root=tmp_path, background=False, provider=FakePlanner())
    assert second["status"] == "planned"
    assert second["clips"][0]["unit_id"] == "unit-short-004"
    assert load_production(PID, root=tmp_path).plan_version == 3


def test_explicit_restart_recovery_never_invokes_a_provider(tmp_path, monkeypatch):
    from app.repurpose.service import recover_batch
    from app.repurpose.storage import batch_dir, read_json

    evidence = source_fixture(tmp_path)
    job = create_batch(PID, request_fixture(evidence).model_dump(mode="json"), root=tmp_path, background=False, provider=FakePlanner())
    saved = load_job(PID, job["batch_id"], tmp_path)
    saved["status"] = "planning"
    save_job(PID, job["batch_id"], saved, tmp_path)
    monkeypatch.setattr("app.repurpose.service.planner_provider", lambda: pytest.fail("no second LLM"))
    recovered = recover_batch(PID, job["batch_id"], root=tmp_path)
    assert recovered["status"] == "planned" and recovered["llm_calls"] == 1
    assert load_production(PID, root=tmp_path).plan_version == 2


def test_operator_segments_remain_readable_and_cannot_replace_pinned_cuts(tmp_path):
    from app.production.segments import load_segments, save_segments

    evidence = source_fixture(tmp_path)
    job = create_batch(PID, request_fixture(evidence).model_dump(mode="json"), root=tmp_path, background=False, provider=FakePlanner())
    assert job["status"] == "planned"
    status, data = load_segments(PID, "src-001", root=tmp_path)
    assert status == 200 and len(data["segments"]) == 27
    assert save_segments(PID, "src-001", {"segments": []}, root=tmp_path)[0] == 409


def test_standard_plan_resolution_rejects_an_unbound_short(tmp_path):
    from app.orchestrator.production_execution import ProductionExecutor

    evidence = source_fixture(tmp_path)
    create_batch(PID, request_fixture(evidence).model_dump(mode="json"), root=tmp_path, background=False, provider=FakePlanner())
    production = load_production(PID, root=tmp_path)
    with pytest.raises(ValueError, match="standard content generation"):
        ProductionExecutor(PID, productions_root=tmp_path, projects_root=tmp_path/"projects").resolve_plan(production, production.units[-1])


def test_interrupted_revision_snapshot_recovers_once(tmp_path, monkeypatch):
    from app.production import storage as intent
    from app.repurpose.service import recover_batch

    evidence = source_fixture(tmp_path)
    write = intent._atomic_write
    interrupted = False
    def stop_current(path, text):
        nonlocal interrupted
        if path == intent.current_path(PID, tmp_path) and not interrupted:
            interrupted = True
            raise OSError("synthetic interrupted current intent replacement")
        return write(path, text)
    monkeypatch.setattr(intent, "_atomic_write", stop_current)
    provider = FakePlanner()
    job = create_batch(PID, request_fixture(evidence).model_dump(mode="json"), root=tmp_path, background=False, provider=provider)
    assert job["status"] == "failed" and load_production(PID, root=tmp_path).plan_version == 1
    monkeypatch.setattr(intent, "_atomic_write", write)
    restored = recover_batch(PID, job["batch_id"], root=tmp_path)
    assert restored["status"] == "planned" and load_production(PID, root=tmp_path).plan_version == 2
    assert len(provider.calls) == 1


def test_rejected_planner_persists_sanitized_http_diagnostics_once(tmp_path):
    from app.content.exceptions import LLMInvalidRequestError, with_provider_diagnostics

    evidence = source_fixture(tmp_path)
    error = with_provider_diagnostics(LLMInvalidRequestError("Rejected parameters"), {
        "http_status": 400, "error_code": "unsupported_parameter", "message": "Unsupported field temperature",
        "request_id": "fixture-request", "api_style": "chat_completions", "transport": "local_proxy"})
    provider = FakePlanner(error=error)
    body = request_fixture(evidence).model_dump(mode="json")
    job = create_batch(PID, body, root=tmp_path, background=False, provider=provider)
    assert job["status"] == "failed" and job["llm_calls"] == 1
    assert job["error"]["diagnostics"]["http_status"] == 400
    assert "Unsupported field temperature" in job["error"]["message"]
    assert job["error"]["diagnostics"]["error_code"] == "unsupported_parameter"
    create_batch(PID, body, root=tmp_path, background=False, provider=provider)
    assert len(provider.calls) == 1


def test_timeout_retains_uncertain_status_and_no_successful_response(tmp_path):
    from app.content.exceptions import LLMConnectionError, with_provider_diagnostics

    evidence = source_fixture(tmp_path)
    error = LLMConnectionError("agentrouter", "Request timed out")
    error.category = "timeout"
    with_provider_diagnostics(error, {"http_status": None, "error_code": "REQUEST_TIMEOUT",
        "message": "No complete response before timeout", "timeout_seconds": 300,
        "api_style": "chat_completions", "transport": "local_proxy"})
    provider = FakePlanner(error=error)
    body = request_fixture(evidence).model_dump(mode="json")
    job = create_batch(PID, body, root=tmp_path, background=False, provider=provider)
    assert job["status"] == "uncertain" and job["llm_calls"] == 1 and job["llm_successful_calls"] == 0
    assert job["error"]["category"] == "timeout" and job["error"]["diagnostics"]["timeout_seconds"] == 300
    create_batch(PID, body, root=tmp_path, background=False, provider=provider)
    assert len(provider.calls) == 1