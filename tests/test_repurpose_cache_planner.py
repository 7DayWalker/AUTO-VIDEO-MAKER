import json
from types import SimpleNamespace

import pytest

from app.repurpose.cache import load_evidence, sources_payload
from app.repurpose.models import PlannerOutput, RepurposeError
from app.repurpose.planner import plan_once, validate_output
from app.production.visual_cache import load_cached_visual_evidence, visual_source_identity
from app.repurpose.storage import atomic_json
from tests.repurpose_fixtures import PID, FakePlanner, output_fixture, request_fixture, source_fixture


def test_cache_selection_never_transcribes_or_observes(tmp_path, monkeypatch):
    evidence = source_fixture(tmp_path)
    monkeypatch.setattr("app.transcription.transcribe.transcribe_source", lambda *a, **k: pytest.fail("No STT"))
    monkeypatch.setattr("app.production.visual_observation.observe_chapter_frames", lambda *a, **k: pytest.fail("No vision"))
    loaded = load_evidence(PID, "src-001", root=tmp_path)
    assert loaded.revision == evidence.revision
    row = sources_payload(PID, root=tmp_path)["sources"][0]
    assert row["ready"] and row["captions_precision"] == "segment_estimated_phrases"


def test_one_call_exact_sol_budget_reasoning(tmp_path):
    evidence = source_fixture(tmp_path)
    provider = FakePlanner()
    plan = plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=provider)
    assert len(plan.clips) == 3 and len(provider.calls) == 1
    assert provider.calls[0][1]["max_tokens"] == 16384
    assert provider.calls[0][1]["reasoning"] == {"effort": "low"}


@pytest.mark.parametrize("mode", ["narrated", "raw_highlight"])
def test_both_modes_are_source_grounded(tmp_path, mode):
    evidence = source_fixture(tmp_path)
    validate_output(PlannerOutput.model_validate(output_fixture(mode)), evidence, request_fixture(evidence, mode=mode))


@pytest.mark.parametrize("case", ["unknown_id", "wrong_time", "overlap", "duration", "invented_visual", "raw_voice"])
def test_bad_citations_and_edits_fail_without_repair(tmp_path, case):
    evidence = source_fixture(tmp_path)
    raw = output_fixture()
    request = request_fixture(evidence)
    if case == "unknown_id": raw["clips"][0]["cuts"][0]["transcript_segment_ids"] = [9999]
    if case == "wrong_time": raw["clips"][0]["cuts"][0]["transcript_segment_ids"] = [55]
    if case == "overlap": raw["clips"][1]["cuts"] = raw["clips"][0]["cuts"]
    if case == "duration": raw["clips"][0]["cuts"].pop()
    if case == "invented_visual": raw["clips"][0]["cuts"][0]["visual_frame_timestamps"] = [2]
    if case == "raw_voice": request = request_fixture(evidence, mode="raw_highlight")
    provider = FakePlanner(raw)
    with pytest.raises(RepurposeError):
        plan_once(evidence, request, "rpb-"+"a"*24, provider=provider)
    assert len(provider.calls) == 1


def test_source_cache_missing_is_explicit(tmp_path):
    source_fixture(tmp_path)
    (tmp_path/PID/"source/src-001.transcript.json").unlink()
    with pytest.raises(RepurposeError, match="Transcribe explicitly"):
        load_evidence(PID, "src-001", root=tmp_path)


def test_visual_identity_change_and_unavailable_notes(tmp_path):
    evidence = source_fixture(tmp_path)
    path = tmp_path/"notes.json"
    note = {"frame_id": 0, "timestamp_seconds": 2, "action": "No retained visual description", "evidence_kind": "unavailable", "citable": False}
    atomic_json(path, {"version": 1, "source_identity": visual_source_identity(evidence.asset, evidence.transcript),
        "chapters": [{"frame_observations": [note]}]})
    assert load_cached_visual_evidence(path, evidence.asset, evidence.transcript) == []
    changed = evidence.asset.model_copy(update={"sha256": "b"*64})
    with pytest.raises(ValueError, match="identity changed"):
        load_cached_visual_evidence(path, changed, evidence.transcript)


def test_provider_exception_has_no_retry(tmp_path):
    evidence = source_fixture(tmp_path)
    provider = FakePlanner(error=TimeoutError("synthetic timeout"))
    with pytest.raises(TimeoutError):
        plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=provider)
    assert len(provider.calls) == 1


def test_actual_adapter_posts_one_responses_envelope(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from app.content.providers.agentrouter import AgentRouterProvider

    evidence = source_fixture(tmp_path)
    output = json.dumps(output_fixture())
    response = SimpleNamespace(status_code=200, json=lambda: {"model": "gpt-6.1-sol", "status": "completed",
        "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": output}]}]})
    post = Mock(return_value=response)
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    provider = AgentRouterProvider(api_key="synthetic-only", base_url="https://fixture.invalid/v1", model="gpt-6.1-sol")
    plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=provider)
    assert post.call_count == 1 and post.call_args.args[0].endswith("/responses")
    envelope = post.call_args.kwargs["json"]
    assert envelope["max_output_tokens"] == 16384 and envelope["reasoning"] == {"effort": "low"}
    assert envelope["model"] == "gpt-6.1-sol" and "temperature" not in envelope


@pytest.mark.parametrize("base", ["http://127.0.0.1:8318", "http://localhost:8318/v1"])
def test_planner_bypasses_chat_only_loopback_without_probe_or_retry(tmp_path, monkeypatch, base):
    from unittest.mock import Mock
    from app.repurpose.planner import planner_provider

    evidence = source_fixture(tmp_path)
    response = SimpleNamespace(status_code=200, json=lambda: {"model": "gpt-6.1-sol", "status": "completed",
        "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(output_fixture())}]}]})
    post = Mock(return_value=response)
    monkeypatch.setenv("AGENTROUTER_API_KEY", "synthetic-only")
    monkeypatch.setenv("AGENTROUTER_BASE_URL", base)
    monkeypatch.setenv("AGENTROUTER_REASONING_EFFORT", "high")
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=planner_provider())
    assert post.call_count == 1
    assert post.call_args.args == ("https://agentrouter.org/v1/responses",)
    sent = post.call_args.kwargs
    assert sent["json"]["max_output_tokens"] == 16384
    assert sent["json"]["reasoning"] == {"effort": "low"}
    assert sent["headers"]["User-Agent"] == "codex_cli_rs/0.1.0"
    assert sent["allow_redirects"] is False