"""Beat clock, planning and source preparation proofs; all media/providers mocked."""
import json
import threading
import wave
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.request import Request, urlopen

import pytest

from app.repurpose.cache import load_evidence, sources_payload
from app.repurpose.models import MODEL_IDS, REASONING_BUDGETS, RepurposeError, StoryBeat, RepurposePlan, BatchRequest
from app.repurpose.planner import plan_once
from app.repurpose.preparation import save_anchors, prepare_source
from app.repurpose.service import create_batch, list_batches
from app.repurpose.storage import content_hash, atomic_json, unit_dir, load_job, save_job, read_json
from app.repurpose.timeline import resolve_timeline, validate_execution, SAMPLE_RATE, PIPELINE_VERSION
from tests.repurpose_fixtures import PID, FakePlanner, request_fixture, source_fixture


def beat_fixture(root):
    evidence = source_fixture(root)
    anchors = [{"action": f"Character makes visible decision {i}", "start_seconds": i * 20, "end_seconds": i * 20 + 15} for i in range(12)]
    save_anchors(PID, "src-001", {"confirm": True, "evidence_revision": evidence.revision, "anchors": anchors}, root=root)
    evidence = load_evidence(PID, "src-001", root=root, include_prepared=True)
    output = {"clips": [{"title": f"Visible story {i}", "hook": "A visible decision changes everything.", "rationale": "Grounded action arc.",
        "beats": [{"spoken_text": "The character makes this visible decision and changes the story.", "evidence_id": a["evidence_id"]}
                  for a in evidence.anchors[i * 4:i * 4 + 4]]} for i in range(3)]}
    request = request_fixture(evidence, story_version=2)
    return evidence, request, output


@pytest.mark.parametrize("model", MODEL_IDS)
@pytest.mark.parametrize("effort,budget", REASONING_BUDGETS.items())
def test_six_planner_settings_and_per_beat_anchor_contract(tmp_path, model, effort, budget):
    evidence, request, output = beat_fixture(tmp_path)
    request = request.model_copy(update={"model_id": model, "reasoning_effort": effort})
    provider = FakePlanner(output); provider.model = model
    plan = plan_once(evidence, request, "rpb-" + "a" * 24, provider=provider)
    assert plan.schema_version == 2 and len(provider.calls) == 1
    assert provider.calls[0][1]["max_tokens"] == budget
    assert provider.calls[0][1]["reasoning"] == {"effort": effort}
    for clip in plan.clips:
        assert len(clip.beats) == 4
        assert clip.narration_text == " ".join(b.spoken_text for b in clip.beats)
        assert [b.focal_source_seconds for b in clip.beats] == [c.start_seconds for c in clip.cuts]


def test_missing_verification_fails_before_dispatch(tmp_path):
    evidence = source_fixture(tmp_path)
    provider = FakePlanner()
    with pytest.raises(RepurposeError, match="Verify"):
        create_batch(PID, request_fixture(evidence, story_version=2).model_dump(), root=tmp_path, provider=provider, background=False)
    assert provider.calls == []


@pytest.mark.parametrize("samples", [1, 1600, 1601, 480001, 480000])
def test_measured_clock_pads_tail_only_without_dead_lead(samples):
    beats = [StoryBeat(order=i + 1, spoken_text="Visible action now.", evidence_id=f"a-{i:016x}",
        focal_source_seconds=i * 30, coverage_end_seconds=i * 30 + 20) for i in range(4)]
    cuts = resolve_timeline(beats, [samples] * 4)
    for i, cut in enumerate(cuts):
        assert cut["start_seconds"] == i * 30
        assert 0 <= cut["tail_padding_samples"] < 1600
        assert cut["padded_audio_samples"] == cut["frame_count"] * 1600
        assert cut["output_start_frame"] == sum(c["frame_count"] for c in cuts[:i])
    validate_execution(SimpleNamespace(beats=beats), cuts)
    cuts[0]["start_seconds"] += .01
    with pytest.raises(ValueError, match="changed"):
        validate_execution(SimpleNamespace(beats=beats), cuts)


def test_short_coverage_never_fills_neighboring_footage():
    beats = [StoryBeat(order=i + 1, spoken_text="Action", evidence_id=f"a-{i:016x}",
        focal_source_seconds=i * 10, coverage_end_seconds=i * 10 + 1) for i in range(4)]
    with pytest.raises(RepurposeError, match="verified footage"):
        resolve_timeline(beats, [96001] * 4)


def test_prepared_evidence_never_changes_legacy_revision(tmp_path):
    before = source_fixture(tmp_path)
    save_anchors(PID, "src-001", {"confirm": True, "evidence_revision": before.revision,
        "anchors": [{"action": "A visible action", "start_seconds": 10, "end_seconds": 30}]}, root=tmp_path)
    assert load_evidence(PID, "src-001", root=tmp_path).revision == before.revision
    assert load_evidence(PID, "src-001", root=tmp_path, include_prepared=True).revision != before.revision
    assert sources_payload(PID, root=tmp_path)["sources"][0]["verified_actions"] == 1


def test_verified_anchor_bounds_and_stale_revision(tmp_path):
    evidence = source_fixture(tmp_path)
    for anchors in [[{"action": "Action", "start_seconds": 10, "end_seconds": 9}],
                    [{"action": "Action", "start_seconds": 290, "end_seconds": 301}]]:
        with pytest.raises(ValueError):
            save_anchors(PID, "src-001", {"confirm": True, "evidence_revision": evidence.revision, "anchors": anchors}, root=tmp_path)
    with pytest.raises(RepurposeError, match="Reload"):
        save_anchors(PID, "src-001", {"confirm": True, "evidence_revision": "a" * 64,
            "anchors": [{"action": "Action", "start_seconds": 10, "end_seconds": 20}]}, root=tmp_path)


def test_validation_reports_safe_paths_not_input(tmp_path):
    evidence = source_fixture(tmp_path)
    output = {"clips": [{"title": "Valid", "hook": "Valid", "rationale": "x" * 127, "narration": "A grounded story " * 20,
        "evidence_ids": "not-an-array"}] * 3}
    job = create_batch(PID, request_fixture(evidence).model_dump(), root=tmp_path, background=False, provider=FakePlanner(output))
    assert "clips.0.evidence_ids: list_type" in job["error"]["message"]
    assert "x" * 127 not in job["error"]["message"]


def test_paid_editorial_metadata_variance_is_normalized():
    from app.repurpose.planner import _WireClip
    clip = _WireClip(title="Word " * 30, hook="Hook " * 40, rationale="Reason " * 19, narration="Story",
        evidence_ids=["w1", "w2", "w3"])
    assert len(clip.title) <= 60 and len(clip.hook) <= 90 and len(clip.rationale) <= 120
    assert clip.title.endswith("…")


def test_scene_bounded_slowdown_preserves_short_action():
    beats = [StoryBeat(order=i + 1, spoken_text="Visible action.", evidence_id=f"a-{i:016x}",
        focal_source_seconds=i * 20, coverage_end_seconds=i * 20 + 5) for i in range(4)]
    cuts = resolve_timeline(beats, [SAMPLE_RATE * 8] * 4)
    assert all(c["video_tempo_factor"] == 1.6 for c in cuts)
    assert cuts[0]["start_seconds"] == 0 and cuts[0]["end_seconds"] == 5


def test_list_batches_paginate_and_skip_corrupt_or_wrong_owner(tmp_path, monkeypatch):
    source_fixture(tmp_path)
    folder = tmp_path / PID / "repurpose/batches"
    ids = ["rpb-" + f"{i:024x}" for i in range(5)]
    for i, bid in enumerate(ids):
        atomic_json(folder / bid / "job.json", {"production_id": PID, "batch_id": bid, "status": "failed",
            "created_at": f"2026-11-10T00:00:0{i}", "request": {"source_asset_id": "src-001"}})
    atomic_json(folder / ("rpb-" + "f" * 24) / "job.json", {"production_id": "wrong", "batch_id": "rpb-" + "f" * 24})
    monkeypatch.setattr("app.repurpose.service.planner_provider", lambda *a: pytest.fail("GET cannot create provider"))
    page1 = list_batches(PID, limit=2, root=tmp_path)
    page2 = list_batches(PID, limit=2, cursor=page1["next_cursor"], root=tmp_path)
    page3 = list_batches(PID, limit=2, cursor=page2["next_cursor"], root=tmp_path)
    assert [b["batch_id"] for p in [page1, page2, page3] for b in p["batches"]] == ids[::-1]
    assert page3["next_cursor"] is None


def test_mocked_v2_renderer_preserves_exact_beat_audio_and_manifest(tmp_path, monkeypatch):
    from app.repurpose import renderer
    evidence, request, output = beat_fixture(tmp_path)
    job = create_batch(PID, request.model_dump(), root=tmp_path, background=False, provider=FakePlanner(output))
    assert job["status"] == "planned", job
    calls = []
    def fake_audio(clip, directory, **kwargs):
        narration = directory / "narration.wav"
        samples = [SAMPLE_RATE * 10] * 4
        cuts = resolve_timeline(clip.beats, samples)
        with wave.open(str(narration), "wb") as out:
            out.setnchannels(2); out.setsampwidth(2); out.setframerate(SAMPLE_RATE)
            out.writeframes(b"\0" * sum(samples) * 4)
        entries = [{"start": c["output_start"], "end": c["output_start"] + 1, "text": "Visible action", "words": None} for c in cuts]
        return narration, cuts, entries
    def ffmpeg(args, description, **kwargs):
        calls.append((args, description))
        if args[-1] == "-":
            return SimpleNamespace(stderr="")
        from pathlib import Path
        path = Path(args[-1]); path = Path(kwargs.get("cwd", ".")) / path if not path.is_absolute() else path
        if path.suffix == ".wav":
            with wave.open(str(path), "wb") as out:
                out.setnchannels(2); out.setsampwidth(2); out.setframerate(SAMPLE_RATE)
                out.writeframes(b"\0" * 40 * SAMPLE_RATE * 4)
        else:
            path.write_bytes(b"synthetic-render-no-decoding")
        return SimpleNamespace(stderr="")
    info = SimpleNamespace(has_video=True, has_audio=True, duration=40, width=1080, height=1920,
        video_codec="h264", audio_codec="aac")
    monkeypatch.setattr(renderer, "_beat_audio", fake_audio)
    monkeypatch.setattr(renderer, "_verify_beat_media", lambda *a, **kw: None)
    monkeypatch.setattr(renderer, "run_ffmpeg", ffmpeg)
    monkeypatch.setattr(renderer, "probe_media", lambda p: info)
    monkeypatch.setattr(renderer, "measure_audio_loudness", lambda p: {"integrated_lufs": -14, "true_peak_db": -1.5,
        "loudness_range_lu": 2, "threshold_lufs": -24, "target_offset_db": 0})
    result = renderer.render_short(PID, "unit-short-001", root=tmp_path)
    assert result.validation["sync_method"] == "measured_beat_audio"
    assert [c["start_seconds"] for c in result.cuts] == [0, 20, 40, 60]
    assert all(c["frame_count"] == 300 for c in result.cuts)
    execution = read_json(unit_dir(PID, "unit-short-001", tmp_path) / "execution.json")
    assert content_hash(execution) == result.validation["execution_hash"]
    assert sum("rendering a bounded" in desc for _, desc in calls) == 4
    count = len(calls)
    renderer.render_short(PID, "unit-short-001", root=tmp_path)
    assert len(calls) == count


def test_batch_list_and_source_range_routes_are_read_only(tmp_path, monkeypatch):
    from app.web import server
    source_fixture(tmp_path)
    monkeypatch.setattr(server, "PRODUCTIONS_DIR", tmp_path)
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.AVMHandler)
    worker = threading.Thread(target=http.serve_forever, daemon=True); worker.start()
    base = f"http://127.0.0.1:{http.server_port}/api/productions/{PID}/repurpose"
    try:
        with urlopen(base + "/batches") as response:
            assert json.load(response)["batches"] == []
        with urlopen(Request(base + "/sources/src-001/media", headers={"Range": "bytes=0-4"})) as response:
            assert response.status == 206 and response.read() == b"synth"
        with urlopen(Request(base + "/sources/src-001/media", method="HEAD")) as response:
            assert response.status == 200 and response.read() == b""
    finally:
        http.shutdown(); http.server_close(); worker.join()


def test_preparation_resumes_cached_frames_without_second_observer_call(tmp_path, monkeypatch):
    from pathlib import Path
    evidence = source_fixture(tmp_path)
    monkeypatch.setattr("app.content.capability_router.configured_visual_observer_routes",
        lambda: [SimpleNamespace(provider="gemini", provider_model_id="synthetic-observer")])
    monkeypatch.setattr("app.content.provider_registry.create_provider", lambda *a: object())
    monkeypatch.setattr("app.production.narration_pro._extract_window_frames",
        lambda media, start, end, directory, index, timestamps: [(ts, str(Path(directory) / "synthetic.jpg")) for ts in timestamps])
    calls = []
    def observe(provider, frames, rows, **kwargs):
        calls.append(frames); kwargs["on_request"]()
        return [{"frame_id": i, "timestamp_seconds": ts, "action": "A character performs a visible action.",
            "citable": True, "credits_only": False, "evidence_kind": "live_frame"} for i, (ts, _) in enumerate(frames)]
    monkeypatch.setattr("app.production.visual_observation.observe_chapter_frames", observe)
    body = {"request_id": "prepare-offline-one", "confirm": True, "evidence_revision": evidence.revision, "frame_count": 12}
    job = prepare_source(PID, "src-001", body, root=tmp_path, background=False)
    assert job["status"] == "ready_for_verification" and job["vision_calls"] == 1
    new = load_evidence(PID, "src-001", root=tmp_path, include_prepared=True)
    repeated = prepare_source(PID, "src-001", {**body, "request_id": "prepare-offline-two", "evidence_revision": new.revision}, root=tmp_path, background=False)
    assert repeated["status"] == "ready_for_verification" and repeated["vision_calls"] == 0
    assert len(calls) == 1 and new.anchors == []


@pytest.mark.parametrize("model", MODEL_IDS)
@pytest.mark.parametrize("effort,budget", REASONING_BUDGETS.items())
@pytest.mark.parametrize("style", ["responses", "chat"])
def test_adapter_forwards_all_selected_settings_once(monkeypatch, model, effort, budget, style):
    from app.content.providers.agentrouter import AgentRouterProvider
    envelope = ({"model": model, "status": "completed", "output": [{"type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": '{"ok":true}'}]}]} if style == "responses" else
        {"model": model, "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]})
    response = Mock(status_code=200); response.json.return_value = envelope
    post = Mock(return_value=response)
    monkeypatch.setattr("app.content.providers.agentrouter.requests.post", post)
    provider = AgentRouterProvider(api_key="synthetic-not-a-secret", model=model,
        base_url="https://agentrouter.org/v1" if style == "responses" else "http://127.0.0.1:8318/v1")
    provider.generate_json("Return grounded JSON.", max_tokens=budget, reasoning={"effort": effort})
    sent = post.call_args.kwargs["json"]
    assert post.call_count == 1 and sent["model"] == model
    assert sent["max_output_tokens" if style == "responses" else "max_tokens"] == budget
    assert (sent["reasoning"]["effort"] if style == "responses" else sent["reasoning_effort"]) == effort


def test_v2_canonical_handoff_pins_execution(tmp_path, monkeypatch):
    from app.orchestrator import reconciliation as oracle
    from app.repurpose.handoff import promote_short, validate_repurpose_handoff
    from app.repurpose.models import RenderArtifact
    from app.repurpose.storage import load_plan
    from app.production.storage import compute_content_hash
    root, projects = tmp_path / "productions", tmp_path / "projects"
    evidence, request, output = beat_fixture(root)
    job = create_batch(PID, request.model_dump(), root=root, background=False, provider=FakePlanner(output))
    plan = load_plan(PID, job["batch_id"], root)
    uid = "unit-short-001"; folder = unit_dir(PID, uid, root); folder.mkdir(parents=True)
    media = folder / "short.mp4"; media.write_bytes(b"synthetic-pinned-v2-master")
    clip = plan.clips[0]
    cuts = resolve_timeline(clip.beats, [SAMPLE_RATE * 10] * 4)
    for reserved, actual in zip(clip.cuts, cuts):
        actual.update(source_segment_id=reserved.source_segment_id, transcript_segment_ids=[], visual_frame_timestamps=[])
    execution = {"pipeline_version": PIPELINE_VERSION, "plan_hash": content_hash(plan), "cuts": cuts,
        "sample_rate": SAMPLE_RATE, "frame_rate": 30, "narration_sha256": "a" * 64}
    artifact = RenderArtifact(production_id=PID, unit_id=uid, batch_id=plan.batch_id, plan_hash=content_hash(plan),
        mode="narrated", framing="cinema_canvas", output_path=f"units/{uid}/repurpose/short.mp4",
        file_fingerprint=compute_content_hash(media), file_size=media.stat().st_size, duration_seconds=40, cuts=cuts,
        captions_precision="word", loudness={"integrated_lufs": -14, "true_peak_db": -1.5},
        validation={"valid": True, "decode_errors": 0, "captions_burned": True, "hook_banner_burned": True,
            "pipeline_version": PIPELINE_VERSION, "execution_hash": content_hash(execution)})
    atomic_json(folder / "execution.json", execution); atomic_json(folder / "render.json", artifact)
    atomic_json(folder / "caption_timing.json", {"entries": []})
    (folder / "captions.ass").write_text("[Script Info]\nPlayResX: 1080\nPlayResY: 1920", encoding="utf-8")
    info = SimpleNamespace(width=1080, height=1920, duration=40, has_video=True, has_audio=True,
        video_codec="h264", audio_codec="aac", file_size=media.stat().st_size, audio_sample_rate=48000,
        audio_channels=2, audio_duration=40, audio_stream_count=1, video_stream_count=1, sample_rate=48000, channels=2)
    monkeypatch.setattr(oracle, "check_ffprobe_available", lambda: True)
    monkeypatch.setattr(oracle, "probe_media", lambda p: info)
    directory, manifest, created = promote_short(PID, uid, root=root, projects_root=projects)
    assert created
    validate_repurpose_handoff(manifest, directory)
    assert "execution.json" in manifest.source_repurpose_handoff.artifact_hashes


def test_beat_tts_pcm_cache_and_caption_offsets_without_network(tmp_path, monkeypatch):
    from pathlib import Path
    from app.repurpose import renderer
    from app.voice.providers.edge_tts import EdgeTTSProvider
    beats = [StoryBeat(order=i + 1, spoken_text=f"Visible action {i}.", evidence_id=f"a-{i:016x}",
        focal_source_seconds=i * 20, coverage_end_seconds=i * 20 + 10) for i in range(4)]
    calls = []
    async def generate(self, text, output, **kwargs):
        calls.append(text); output.write_bytes(b"synthetic-audio")
        atomic_json(output.with_suffix(".mp3.timing.json"), {"boundaries": [
            {"kind": "WordBoundary", "text": text, "start": .1, "end": 1.0}]})
    monkeypatch.setattr(EdgeTTSProvider, "generate", generate)
    monkeypatch.setattr(renderer.time, "sleep", lambda _: None)
    monkeypatch.setattr(renderer, "probe_media", lambda p: SimpleNamespace(duration=1.2))
    def decode(args, *a, **kw):
        import struct
        with wave.open(str(Path(args[-1])), "wb") as out:
            out.setnchannels(2); out.setsampwidth(2); out.setframerate(SAMPLE_RATE)
            out.writeframes(b"\0" * 4800 * 4 + struct.pack("<hh", 1000, 1000) * 43201)
    monkeypatch.setattr(renderer, "run_ffmpeg", decode)
    clip = SimpleNamespace(beats=beats)
    narration, cuts, entries = renderer._beat_audio(clip, tmp_path)
    assert len(calls) == 4 and len(entries) == 4
    assert entries[0]["start"] == pytest.approx(.005)
    assert entries[1]["start"] == pytest.approx(cuts[1]["output_start"] + .005)
    with wave.open(str(narration), "rb") as stream:
        assert stream.getnframes() == sum(c["padded_audio_samples"] for c in cuts)
    renderer._beat_audio(clip, tmp_path)
    assert len(calls) == 4


def test_encoded_frame_verifier_rejects_count_or_mux_delay(monkeypatch):
    from app.repurpose import renderer
    monkeypatch.setattr(renderer, "get_ffmpeg_runner", lambda: SimpleNamespace(ensure_ffprobe=lambda: None, ffprobe_path="synthetic-probe"))
    streams = [{"codec_type": "video", "nb_read_frames": "300", "start_time": "0"},
        {"codec_type": "audio", "start_time": "0", "duration": "10"}]
    monkeypatch.setattr(renderer.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps({"streams": streams})))
    renderer._verify_beat_media("synthetic.mp4", 300, require_audio_duration=True)
    streams[0]["nb_read_frames"] = "299"
    with pytest.raises(RepurposeError, match="frame count"):
        renderer._verify_beat_media("synthetic.mp4", 300)
    streams[0]["nb_read_frames"] = "300"; streams[1]["start_time"] = "3"
    with pytest.raises(RepurposeError, match="origin"):
        renderer._verify_beat_media("synthetic.mp4", 300)