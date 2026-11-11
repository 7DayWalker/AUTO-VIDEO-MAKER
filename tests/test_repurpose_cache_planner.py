import json
from types import SimpleNamespace

import pytest

from app.repurpose.cache import load_evidence, sources_payload
from app.repurpose.models import PlannerOutput, RepurposeError
from app.repurpose.planner import compact_prompt, evidence_packet, plan_from_response, plan_once, validate_output
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
def test_planner_matches_summary_loopback_without_probe_or_retry(tmp_path, monkeypatch, base):
    from unittest.mock import Mock
    from app.repurpose.planner import planner_provider

    evidence = source_fixture(tmp_path)
    response = SimpleNamespace(status_code=200, json=lambda: {"model": "gpt-6.1-sol",
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output_fixture())}}]})
    post = Mock(return_value=response)
    monkeypatch.setenv("AGENTROUTER_API_KEY", "synthetic-only")
    monkeypatch.setenv("AGENTROUTER_BASE_URL", base)
    monkeypatch.setenv("AGENTROUTER_REASONING_EFFORT", "high")
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=planner_provider())
    assert post.call_count == 1
    assert post.call_args.args == (base.rstrip("/") + ("" if base.endswith("/v1") else "/v1") + "/chat/completions",)
    sent = post.call_args.kwargs
    assert sent["json"]["max_tokens"] == 16384
    assert sent["json"]["reasoning_effort"] == "low"
    assert sent["json"]["response_format"] == {"type": "json_object"}
    assert sent["json"]["temperature"] == .35
    assert sent["headers"]["User-Agent"] == "codex_cli_rs/0.1.0"
    assert sent["allow_redirects"] is False
    assert sent["timeout"] == 660
    assert sent["stream"] is True and sent["json"]["stream"] is True


def compact_output_fixture():
    return {"clips": [{"title": f"Compact hook {i+1}", "hook": f"Why does this moment change the story? {i+1}",
        "narration": " ".join(["The character faces a difficult choice and finds a grounded answer."]*8),
        "rationale": "A clear standalone turning point.", "evidence_ids": [f"w{i*2+1}", f"w{i*2+2}"],
        "cuts": [{"start": i*60+j*7.5, "end": i*60+(j+1)*7.5} for j in range(6)]} for i in range(3)]}


def test_compact_prompt_uses_flat_wire_shape_and_bounded_evidence(tmp_path):
    from app.production.token_budget import text_token_budget

    evidence = source_fixture(tmp_path)
    evidence.beats = [{"title": f"Grounded beat {i+1}", "text": "A detailed original beat "*200,
        "source_refs": [{"start_seconds": i*5, "end_seconds": i*5+5}]} for i in range(54)]
    prompt, system, metrics = compact_prompt(evidence, request_fixture(evidence))
    assert text_token_budget(prompt+system) < 3500
    assert metrics["recap_beats"] == 54 and metrics["evidence_lines"] <= 120
    packet = evidence_packet(evidence)
    assert all(f"[b{i+1} " in packet["notes"] for i in range(54))
    assert '"evidence_ids"' in prompt and '"narration"' in prompt
    assert '"cuts"' not in prompt and '"start"' not in prompt and '"end"' not in prompt
    assert '"transcript_segment_ids"' not in prompt and '"$defs"' not in prompt and '"source_refs"' not in prompt


def test_compact_wire_hydrates_internal_citations_without_llm_repair(tmp_path):
    evidence = source_fixture(tmp_path)
    provider = FakePlanner(compact_output_fixture())
    plan = plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=provider)
    assert len(provider.calls) == 1 and len(plan.clips) == 3
    assert all(len(c.cuts) == 6 for c in plan.clips)
    assert plan.clips[0].cuts[0].transcript_segment_ids == [0, 1]
    assert plan.clips[0].narration_text and not hasattr(plan.clips[0], "evidence_ids")


@pytest.mark.parametrize("case", ["unknown_evidence", "uncited_cut", "long_title", "narration_length", "overlap"])
def test_compact_wire_errors_fail_after_one_call(tmp_path, case):
    evidence = source_fixture(tmp_path)
    output = compact_output_fixture()
    if case == "unknown_evidence": output["clips"][0]["evidence_ids"] = ["no-such-id", "w1"]
    if case == "uncited_cut": output["clips"][0]["cuts"][0] = {"start": 250, "end": 257.5}
    if case == "long_title": output["clips"][0]["title"] = "x"*61
    if case == "narration_length": output["clips"][0]["narration"] = "One short phrase"
    if case == "overlap": output["clips"][1]["cuts"] = output["clips"][0]["cuts"]
    provider = FakePlanner(output)
    if case == "long_title":
        plan = plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=provider)
        assert len(plan.clips[0].title) <= 60
        assert len(provider.calls) == 1
        return
    with pytest.raises(RepurposeError):
        plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=provider)
    assert len(provider.calls) == 1


def test_legacy_response_remains_recoverable_without_a_provider(tmp_path):
    evidence = source_fixture(tmp_path)
    plan = plan_from_response(SimpleNamespace(model="gpt-6.1-sol", content=json.dumps(output_fixture())),
        evidence, request_fixture(evidence), "rpb-"+"a"*24)
    assert len(plan.clips) == 3


def test_planner_timeout_extended_without_changing_default_adapter(monkeypatch):
    from app.content.providers.agentrouter import AgentRouterProvider
    from app.repurpose.planner import planner_provider

    monkeypatch.setenv("AGENTROUTER_API_KEY", "synthetic-only")
    monkeypatch.delenv("AGENTROUTER_REQUEST_TIMEOUT", raising=False)
    assert planner_provider()._timeout == 660
    assert AgentRouterProvider(api_key="synthetic-only")._timeout == 300
    with pytest.raises(ValueError):
        AgentRouterProvider(api_key="synthetic-only", timeout=661)


def test_high_density_candidates_retain_beginning_middle_and_finale(tmp_path):
    from app.repurpose.planner import _candidate_windows, _source_catalog

    evidence = source_fixture(tmp_path)
    _, candidates = _source_catalog(evidence)
    selected = _candidate_windows(candidates, evidence.asset.duration, 8)
    assert selected[0]["start"] == candidates[0]["start"]
    assert selected[-1]["start"] == candidates[-1]["start"]
    assert {min(3, int(w["start"]*4/evidence.asset.duration)) for w in selected} == {0, 1, 2, 3}


def test_compact_unknown_or_unadvertised_evidence_cannot_be_hydrated(tmp_path):
    evidence = source_fixture(tmp_path)
    evidence.asset = evidence.asset.model_copy(update={"duration": 3600})
    # Many windows deliberately compete for a tiny prompt. IDs not shown to
    # the planner cannot be used merely because they exist in the full cache.
    evidence.beats = [{"title": f"B{i}", "source_refs": [{"start_seconds": i*5, "end_seconds": i*5+5}]} for i in range(54)]
    _, _, metrics = compact_prompt(evidence, request_fixture(evidence))
    from app.repurpose.planner import _source_catalog
    _, windows = _source_catalog(evidence)
    missing = next((w["id"] for w in windows if w["id"] not in metrics["evidence_ids"]), None)
    assert missing is not None
    output = compact_output_fixture()
    output["clips"][0]["evidence_ids"] = ["b1", missing]
    with pytest.raises(RepurposeError, match="Unknown"):
        plan_from_response(SimpleNamespace(model="gpt-6.1-sol", content=json.dumps(output)), evidence,
            request_fixture(evidence), "rpb-"+"a"*24)


def test_loopback_sse_is_one_request_with_validated_model_and_json(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from app.repurpose.planner import planner_provider

    evidence = source_fixture(tmp_path)
    content = json.dumps(compact_output_fixture())
    events = [{"model": "gpt-6.1-sol", "choices": [{"index": 0, "delta": {"content": content[i:i+80]}, "finish_reason": None}]}
              for i in range(0, len(content), 80)]
    events.append({"model": "gpt-6.1-sol", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    lines = []
    for event in events:
        lines.extend(["data: "+json.dumps(event), ""])
    lines.extend(["data: [DONE]", ""])
    response = Mock(status_code=200, headers={"Content-Type": "text/event-stream"})
    response.iter_lines.return_value = iter(lines)
    post = Mock(return_value=response)
    monkeypatch.setenv("AGENTROUTER_API_KEY", "synthetic-only")
    monkeypatch.setenv("AGENTROUTER_BASE_URL", "http://127.0.0.1:8318/v1")
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    plan = plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=planner_provider())
    assert len(plan.clips) == 3 and post.call_count == 1
    assert post.call_args.kwargs["timeout"] == 660 and post.call_args.kwargs["stream"] is True
    assert post.call_args.kwargs["json"]["max_tokens"] == 16384
    response.json.assert_not_called()
    response.close.assert_called_once()


@pytest.mark.parametrize("delta", [-2.0, -.2, .2, 2.0])
def test_minor_duration_variance_is_adjusted_without_an_llm_call(tmp_path, delta):
    evidence = source_fixture(tmp_path)
    output = compact_output_fixture()
    output["clips"][0]["cuts"][-1]["end"] += delta
    if delta > .5:
        output["clips"][0]["cuts"] = [{"start": j*8, "end": j*8+47/6} for j in range(6)]
    provider = FakePlanner(output)
    plan = plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=provider)
    assert len(provider.calls) == 1
    assert abs(sum(c.end_seconds-c.start_seconds for c in plan.clips[0].cuts)-45) < .01
    assert all(0 <= c.start_seconds < c.end_seconds <= evidence.asset.duration and c.end_seconds-c.start_seconds <= 8
               for c in plan.clips[0].cuts)


def test_major_duration_variance_is_not_hidden_as_local_adjustment(tmp_path):
    evidence = source_fixture(tmp_path)
    output = compact_output_fixture()
    output["clips"][0]["cuts"] = [{"start": j*5, "end": j*5+4} for j in range(6)]
    provider = FakePlanner(output)
    with pytest.raises(RepurposeError, match="adjustment budget"):
        plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=provider)
    assert len(provider.calls) == 1


def test_duration_adjustment_does_not_extend_into_another_clip():
    from app.repurpose.planner import _WireCut, _adjust_cut_windows

    cuts = [_WireCut(start=i*8, end=i*8+7) for i in range(6)]
    rows = _adjust_cut_windows(cuts, 45, 60, [(0, 60)], [(47.2, 52)])
    assert abs(sum(r["end"]-r["start"] for r in rows)-45) < .01
    assert all(r["end"] <= 47.2 and r["end"]-r["start"] <= 8 for r in rows)


def test_compact_prompt_defers_exact_arithmetic_to_python(tmp_path):
    evidence = source_fixture(tmp_path)
    prompt, _, metrics = compact_prompt(evidence, request_fixture(evidence))
    assert "NEVER count words or calculate timestamps in reasoning" in prompt
    assert "Python builds the video cuts" in prompt
    assert metrics["timeout_seconds"] == 660


def evidence_only_output():
    output = compact_output_fixture()
    for i, clip in enumerate(output["clips"]):
        clip.pop("cuts")
        clip["evidence_ids"] = [f"w{i*3+1}", f"w{i*3+2}", f"w{i*3+3}"]
    return output


def test_evidence_only_plan_derives_all_cuts_and_never_requests_coordinates(tmp_path):
    evidence = source_fixture(tmp_path)
    output = evidence_only_output()
    provider = FakePlanner(output)
    plan = plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=provider)
    assert len(provider.calls) == 1 and '"cuts"' not in provider.calls[0][0]
    footage = []
    for clip in plan.clips:
        assert abs(sum(c.end_seconds-c.start_seconds for c in clip.cuts)-45) < .01
        for cut in clip.cuts:
            assert 0 <= cut.start_seconds < cut.end_seconds <= evidence.asset.duration
            assert cut.transcript_segment_ids or cut.visual_frame_timestamps
            assert not any(min(cut.end_seconds,b)>max(cut.start_seconds,a) for a,b in footage)
            footage.append((cut.start_seconds,cut.end_seconds))


@pytest.mark.parametrize("target", [30,45,60,90,120,150,180])
def test_evidence_only_duration_presets_are_bounded_and_deterministic(tmp_path,target):
    from app.production.models import TranscriptSegment
    from app.repurpose.models import rendered_duration_bounds, maximum_shot_seconds

    evidence=source_fixture(tmp_path)
    evidence.asset=evidence.asset.model_copy(update={"duration":1800})
    evidence.transcript.segments=[TranscriptSegment(text=f"Source event {i}",start=i*5,end=i*5+5) for i in range(360)]
    evidence.beats=[{"title":f"Arc {i}","source_refs":[{"start_seconds":i*240,"end_seconds":i*240+210}]} for i in range(7)]
    output=evidence_only_output()
    for clip in output["clips"]: clip["evidence_ids"]=["b1","b3","b5"]
    provider=FakePlanner(output)
    request=request_fixture(evidence,target_seconds=target)
    plan=plan_once(evidence,request,"rpb-"+"a"*24,provider=provider)
    again=plan_from_response(SimpleNamespace(model="gpt-6.1-sol",content=json.dumps(output)),evidence,request,"rpb-"+"a"*24)
    for clip, second in zip(plan.clips,again.clips):
        assert clip.cuts==second.cuts and len(clip.cuts)<=16
        assert abs(sum(c.end_seconds-c.start_seconds for c in clip.cuts)-target)<.01
        assert max(c.end_seconds-c.start_seconds for c in clip.cuts)<=maximum_shot_seconds(target)+.001
    assert rendered_duration_bounds(target)[1]<=180
    assert len(provider.calls)==1


def test_evidence_only_raw_highlight_has_empty_narration(tmp_path):
    evidence=source_fixture(tmp_path)
    output=evidence_only_output()
    for clip in output["clips"]: clip["narration"]=""
    plan=plan_once(evidence,request_fixture(evidence,mode="raw_highlight"),"rpb-"+"a"*24,provider=FakePlanner(output))
    assert all(not c.narration_text for c in plan.clips)


@pytest.mark.parametrize("target", [29,181,float('nan'),float('inf')])
def test_duration_outside_30_180_is_rejected(tmp_path,target):
    evidence=source_fixture(tmp_path)
    with pytest.raises(ValueError):request_fixture(evidence,target_seconds=target)


def test_stream_incomplete_preserves_safe_reason_and_counts(monkeypatch):
    from unittest.mock import Mock
    from app.content.providers.agentrouter import AgentRouterProvider
    from app.content.exceptions import LLMOutputTruncatedError

    event = {"model": "gpt-6.1-sol", "choices": [{"delta": {}, "finish_reason": "length"}],
             "incomplete_details": {"reason": "max_output_tokens"}, "usage": {"completion_tokens": 16384}}
    response = Mock(status_code=200, headers={"Content-Type": "text/event-stream"})
    response.iter_lines.return_value = iter(["data: "+json.dumps(event), "", "data: [DONE]", ""])
    post = Mock(return_value=response)
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    provider = AgentRouterProvider(api_key="synthetic-only", base_url="http://127.0.0.1:8318/v1", timeout=660, stream=True)
    with pytest.raises(LLMOutputTruncatedError) as caught:
        provider.generate_json("Return JSON", max_tokens=16384, reasoning={"effort": "low"})
    assert caught.value.diagnostics["http_status"] == 200
    assert caught.value.diagnostics["error_code"] == "OUTPUT_INCOMPLETE"
    assert "reason=max_output_tokens" in caught.value.diagnostics["message"]
    assert "16384" in caught.value.diagnostics["message"] and post.call_count == 1
    response.close.assert_called_once()


@pytest.mark.parametrize("case", ["missing_finish", "wrong_model", "truncated", "error"])
def test_streaming_failure_never_returns_partial_plan_or_retries(tmp_path, monkeypatch, case):
    from unittest.mock import Mock
    from app.content.exceptions import LLMResponseError
    from app.repurpose.planner import planner_provider

    evidence = source_fixture(tmp_path)
    model = "other-model" if case == "wrong_model" else "gpt-6.1-sol"
    events = [{"model": model, "choices": [{"delta": {"content": json.dumps(compact_output_fixture())}, "finish_reason": None}]}]
    if case == "error": events = [{"error": {"message": "synthetic-never-echoed"}}]
    elif case != "missing_finish": events.append({"model": model, "choices": [{"delta": {}, "finish_reason": "length" if case == "truncated" else "stop"}]})
    lines = [line for event in events for line in ("data: "+json.dumps(event), "")]
    response = Mock(status_code=200, headers={"Content-Type": "text/event-stream"})
    response.iter_lines.return_value = iter(lines)
    post = Mock(return_value=response)
    monkeypatch.setenv("AGENTROUTER_API_KEY", "synthetic-only")
    monkeypatch.setenv("AGENTROUTER_BASE_URL", "http://127.0.0.1:8318/v1")
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    with pytest.raises(LLMResponseError) as caught:
        plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=planner_provider())
    assert "synthetic-never-echoed" not in str(caught.value)
    assert post.call_count == 1
    response.close.assert_called_once()


def test_native_responses_sse_completion_uses_same_acceptance(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from app.content.providers.agentrouter import AgentRouterProvider

    evidence = source_fixture(tmp_path)
    content = json.dumps(compact_output_fixture())
    complete = {"model": "gpt-6.1-sol", "status": "completed", "output": [{"type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": content}]}]}
    events = [{"type": "response.created", "response": {"model": "gpt-6.1-sol"}},
              {"type": "response.output_text.delta", "delta": content},
              {"type": "response.completed", "response": complete}]
    response = Mock(status_code=200, headers={"Content-Type": "text/event-stream"})
    response.iter_lines.return_value = iter([line for event in events for line in ("data: "+json.dumps(event), "")])
    post = Mock(return_value=response)
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    provider = AgentRouterProvider(api_key="synthetic-only", base_url="https://fixture.invalid/v1", timeout=600, stream=True)
    plan = plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=provider)
    assert len(plan.clips) == 3 and post.call_count == 1
    assert post.call_args.kwargs["json"]["max_output_tokens"] == 16384
    response.close.assert_called_once()


def test_interrupted_sse_is_safe_uncertain_and_never_retried(tmp_path, monkeypatch):
    from unittest.mock import Mock
    import requests
    from app.content.exceptions import LLMConnectionError
    from app.repurpose.planner import planner_provider

    response = Mock(status_code=200, headers={"Content-Type": "text/event-stream"})
    response.iter_lines.side_effect = requests.ConnectionError("synthetic-private-token https://private.invalid")
    post = Mock(return_value=response)
    monkeypatch.setenv("AGENTROUTER_API_KEY", "synthetic-only")
    monkeypatch.setenv("AGENTROUTER_BASE_URL", "http://127.0.0.1:8318/v1")
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    evidence = source_fixture(tmp_path)
    with pytest.raises(LLMConnectionError) as caught:
        plan_once(evidence, request_fixture(evidence), "rpb-"+"a"*24, provider=planner_provider())
    assert "synthetic-private-token" not in str(caught.value) and "private.invalid" not in str(caught.value)
    assert caught.value.diagnostics["error_code"] == "STREAM_INTERRUPTED"
    assert caught.value.category == "timeout" and post.call_count == 1
    response.close.assert_called_once()


def test_chat_done_marker_does_not_wait_for_an_idle_connection(monkeypatch):
    from unittest.mock import Mock
    from app.content.providers.agentrouter import AgentRouterProvider

    payload = {"model": "gpt-6.1-sol", "choices": [{"delta": {"content": '{"ok":true}'}, "finish_reason": "stop"}]}
    def events():
        yield "data: "+json.dumps(payload)
        yield ""
        yield "data: [DONE]"
        yield ""
        raise AssertionError("Completed SSE must not wait for another network read")
    response = Mock(status_code=200, headers={"Content-Type": "text/event-stream"})
    response.iter_lines.return_value = events()
    post = Mock(return_value=response)
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    provider = AgentRouterProvider(api_key="synthetic-only", base_url="http://127.0.0.1:8318/v1", stream=True, timeout=600)
    assert json.loads(provider.generate_json("Return JSON").content) == {"ok": True}
    assert post.call_count == 1
    response.close.assert_called_once()