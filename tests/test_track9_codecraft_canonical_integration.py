"""
TRACK 9 -- CodeCraft canonical model integration (claude-opus-5.5).

Focused, deterministic proofs for the 9 required properties of STEP 4.
Test doubles are used for all routing; the live evidence lives in
``_real_smoke_wk/opus55_revalidation.json`` and is NOT re-fetched here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.content.capability_router import (
    JOB_SOURCE_VIDEO_RECAP,
    assess_model_for_job,
    assess_provider_route,
    clear_quota_blockers,
    select_provider_for_model,
)
from app.content.model_registry import (
    MODEL_REGISTRY,
    IdentityVerification,
    InputModality,
    QuotaEvidence,
    discover_models_for_job,
    get_model,
    has_model,
    job_video_understanding_method,
    list_models,
    model_drawer_projection,
)
from app.content.model_resolver import resolve_model
from app.content.providers.codecraft import CodeCraftProvider

OPUS = "claude-opus-5.5"
PROVIDER_NAMES = {"alibaba", "openrouter", "gemini", "codecraft", "mock"}
REVALIDATION = (
    Path(__file__).resolve().parents[1] / "_real_smoke_wk" / "opus55_revalidation.json"
)


@pytest.fixture(autouse=True)
def _clean_blockers():
    clear_quota_blockers()
    yield
    clear_quota_blockers()


# =========================================================================
# 1. claude-opus-5.5 is a CANONICAL MODEL, not a provider identity
# =========================================================================

def test_1_opus_is_a_canonical_model_not_a_provider_identity():
    assert has_model(OPUS) is True
    model = get_model(OPUS)
    assert model.id == OPUS
    assert model.display_name == "Claude Opus 5.5"
    assert model.family == "anthropic"
    # The canonical id is a MODEL name, never a provider name.
    assert model.id not in PROVIDER_NAMES
    assert "codecraft" not in model.id.lower()
    # And NOT a provider-prefixed id.
    assert has_model("codecraft-claude-opus-5.5") is False
    assert OPUS in [m.id for m in list_models()]


def test_1b_no_provider_prefixed_or_generic_placeholder_identity_exists():
    ids = [m.id for m in list_models()]
    for banned in ("codecraft-claude-opus-5.5", "codecraft-vision-gateway",
                   "codecraft-vision", "generic-vision-model"):
        assert banned not in ids, f"'{banned}' must not be a canonical identity"

    # No canonical id may be a BARE provider name.
    for mid in ids:
        assert mid.lower() not in PROVIDER_NAMES, f"'{mid}' is a provider name"

    # Provider-ROUTE names must never prefix a canonical id. 'gemini-3-flash-preview'
    # is legitimate -- 'gemini' is the model's own vendor/product name, which is
    # genuinely part of the model identity. 'codecraft'/'openrouter' are AGGREGATOR
    # route names that are never part of any model's real identity, so a canonical
    # id must never be built from them.
    aggregator_routes = {"codecraft", "openrouter", "mock"}
    for mid in ids:
        for route in aggregator_routes:
            assert route not in mid.lower(), (
                f"canonical id '{mid}' leaks aggregator route '{route}'"
            )


# =========================================================================
# 2. CodeCraft is registered ONLY as an internal provider binding
# =========================================================================

def test_2_codecraft_is_only_an_internal_binding():
    model = get_model(OPUS)
    assert len(model.bindings) == 1
    binding = model.bindings[0]
    assert binding.provider == "codecraft"
    # The exact live wire id.
    assert binding.provider_model_id == OPUS
    # Credentials are internal execution metadata.
    assert binding.config_keys == ("CODECRAFT_API_KEY", "CODECRAFT_BASE_URL")
    # Provider is registered in the PROVIDER catalogue, not the model catalogue.
    import app.content.provider_registry as preg

    assert preg.has_provider("codecraft") is True
    assert "codecraft" not in [m.id for m in list_models()]


# =========================================================================
# 3. Model drawer projection does not expose provider names as models
# =========================================================================

def test_3_drawer_hides_codecraft_while_offering_opus():
    payload = model_drawer_projection(JOB_SOURCE_VIDEO_RECAP)
    offered = [e["id"] for e in payload["models"]]
    assert OPUS in offered, "the registered model must reach the drawer"
    blob = repr(payload).lower()
    assert "codecraft" not in blob, "CodeCraft leaked into the drawer contract"
    for leak in ("api_key", "config_keys", "provider_model_id",
                 "identity_verification", "bindings"):
        assert leak not in blob, f"internal detail '{leak}' leaked into drawer"
    entry = next(e for e in payload["models"] if e["id"] == OPUS)
    assert entry["display_name"] == "Claude Opus 5.5"
    assert "provider" not in entry
    # Truthful capability split in the user-facing contract.
    assert entry["capabilities"]["image_input"] is True
    assert entry["capabilities"]["native_video_input"] is False
    assert entry["capabilities"]["frame_based_video_understanding"] is True
    assert entry["video_understanding_method"] == InputModality.FRAME_SEQUENCE
    # No fabricated cost.
    assert entry["cost_estimate"] is None


# =========================================================================
# 4. Summary Pro discovers the model from GENUINE capability metadata
# =========================================================================

def test_4_summary_pro_discovers_opus_by_capability():
    discovered = [m.id for m in discover_models_for_job(JOB_SOURCE_VIDEO_RECAP)]
    assert OPUS in discovered
    model = get_model(OPUS)
    # It qualifies through a capability AVM actually supports.
    capable, method = model.capabilities.can_understand_source_video()
    assert capable is True
    assert method == InputModality.FRAME_SEQUENCE
    # Qualification is NOT name-driven: no product/provider token required.
    assert "summary" not in OPUS and "pro" not in OPUS
    # It is also reachable through the existing Pro listing.
    from app.content.model_registry import list_pro_model_ids

    assert OPUS in list_pro_model_ids()


def test_4b_opus_qualifies_only_through_supported_capabilities():
    model = get_model(OPUS)
    caps = model.capabilities
    # Image input: verified -> True.
    assert caps.image_input is True
    # Native video: NOT established -> must be False.
    assert caps.native_video_input is False
    assert caps.video is False
    # Frame-based understanding: supported through the adapter.
    assert caps.frame_sequence_capable is True
    assert InputModality.NATIVE_VIDEO not in caps.supported_input_modalities()
    assert InputModality.FRAME_SEQUENCE in caps.supported_input_modalities()
    assert InputModality.IMAGE in caps.supported_input_modalities()


def test_4c_route_eligibility_uses_the_frame_modality(monkeypatch):
    monkeypatch.setenv("CODECRAFT_API_KEY", "cc_fake_for_test")
    monkeypatch.setenv("CODECRAFT_BASE_URL", "https://example.invalid/v1")
    assessment = assess_model_for_job(OPUS, JOB_SOURCE_VIDEO_RECAP)
    assert assessment.capability_ok is True
    assert assessment.video_understanding_method == InputModality.FRAME_SEQUENCE
    assert assessment.eligible is True
    route = assessment.eligible_routes()[0]
    assert route.provider == "codecraft"
    # The route is eligible FOR THE FRAME MODALITY, not native video.
    assert route.input_modality == InputModality.FRAME_SEQUENCE


# =========================================================================
# 5. Explicit selection resolves this exact canonical model
# =========================================================================

def test_5_explicit_selection_resolves_opus_exactly():
    from app.production.narration import resolve_summary_model

    chosen = resolve_summary_model("pro", preferred_model_id=OPUS, selection_mode="explicit")
    assert chosen == OPUS
    chosen2 = resolve_summary_model("pro", summary_understanding_model_id=OPUS,
                                    selection_mode="manual")
    assert chosen2 == OPUS


def test_5b_explicit_opus_is_not_overridden_by_auto_preference():
    from app.content.capability_router import select_model_auto
    from app.production.narration import resolve_summary_model

    auto_choice = select_model_auto(JOB_SOURCE_VIDEO_RECAP).canonical_model_id
    # Auto prefers a native-video model; explicit Opus must still win.
    assert auto_choice != OPUS
    assert resolve_summary_model("pro", preferred_model_id=OPUS,
                                 selection_mode="explicit") == OPUS


# =========================================================================
# 6. Provider execution uses the EXACT CodeCraft wire id
# =========================================================================

def test_6_execution_uses_the_exact_codecraft_wire_id(monkeypatch):
    monkeypatch.setenv("CODECRAFT_API_KEY", "cc_fake_for_test")
    resolved = resolve_model(OPUS)
    assert resolved.canonical_model_id == OPUS
    assert resolved.provider == "codecraft"
    assert resolved.provider_model_id == OPUS
    # The instantiated provider really carries that wire id.
    assert resolved.provider_instance.name == "codecraft"
    assert resolved.provider_instance.model == OPUS
    # Routing agrees.
    route = select_provider_for_model(OPUS, JOB_SOURCE_VIDEO_RECAP)
    assert route.provider_model_id == OPUS


def test_6b_adapter_advertises_opus_vision_but_not_native_video():
    p = CodeCraftProvider(api_key="k", model=OPUS)
    assert p.supports_multimodal() is True
    assert p.vision_capability_source() == "verified_real_request"
    assert p.supports_native_video() is False


# =========================================================================
# 7. Frame-based analysis is distinguished from native video ingestion
# =========================================================================

def test_7_opus_execution_is_labelled_frame_based(tmp_path):
    from unittest.mock import Mock, patch

    frame_a = tmp_path / "f_10.jpg"
    frame_b = tmp_path / "f_20.jpg"
    frame_a.write_bytes(b"\xff\xd8\xff" + b"a" * 4096)
    frame_b.write_bytes(b"\xff\xd8\xff" + b"b" * 4096)

    fake = Mock()
    fake.status_code = 200
    fake.json.return_value = {
        "id": "cc-opus", "model": OPUS,
        "choices": [{"finish_reason": "stop", "message": {"content": "frames described"}}],
        "usage": {"completion_tokens": 120, "total_tokens": 500,
                  "completion_tokens_details": {"reasoning_tokens": 80}},
    }
    p = CodeCraftProvider(api_key="k", model=OPUS)
    with patch("app.content.providers.codecraft.requests.post", return_value=fake) as post:
        resp = p.generate_multimodal(
            "Analyze this bounded window.",
            images=[(10.0, str(frame_a)), (20.0, str(frame_b))],
        )
    meta = resp.metadata or {}
    assert meta["input_modality"] == InputModality.FRAME_SEQUENCE
    assert meta["visual_analysis_method"] == "frame_based_visual_analysis"
    assert meta["native_video_ingestion"] is False
    assert meta["frame_count"] == 2
    assert [f["timestamp_seconds"] for f in meta["frame_timestamps"]] == [10.0, 20.0]
    # The request genuinely carried image parts.
    parts = post.call_args[1]["json"]["messages"][-1]["content"]
    assert sum(1 for x in parts if x.get("type") == "image_url") == 2


def test_7b_mp4_is_never_sent_through_the_image_interface():
    """A video= request is REFUSED, never silently treated as native video."""
    p = CodeCraftProvider(api_key="k", model=OPUS)
    with pytest.raises(Exception) as exc:
        p.generate_multimodal("analyze", video="C:/window.mp4", images=None)
    assert "native video" in str(exc.value).lower()


def test_7c_pro_pipeline_selects_the_frame_route_for_frame_only_providers():
    """The EXISTING bounded-window loop chooses frames, not an MP4."""
    import app.production.narration_pro as pro

    class _FrameOnly:
        name = "codecraft"
        model = OPUS

        def supports_multimodal(self):
            return True

        def supports_native_video(self):
            return False

    class _NativeVideo:
        name = "gemini"
        model = "gemini-3-flash-preview"

        def supports_multimodal(self):
            return True

        def supports_native_video(self):
            return True

    class _Legacy:
        """No supports_native_video at all -> historical behaviour preserved."""
        name = "legacy"
        model = "legacy-wire"

        def supports_multimodal(self):
            return True

    assert pro._provider_uses_native_video(_FrameOnly()) is False
    assert pro._provider_uses_native_video(_NativeVideo()) is True
    assert pro._provider_uses_native_video(_Legacy()) is True


def test_7d_frame_extraction_preserves_bounded_window_and_timestamps(tmp_path):
    """Frames come from INSIDE the window and keep real source offsets."""
    import app.production.narration_pro as pro

    calls = []

    class _Runner:
        def run(self, args, label):
            # Record the -ss timestamp and synthesize an output file.
            ts = float(args[args.index("-ss") + 1])
            out = Path(args[-1])
            out.write_bytes(b"\xff\xd8\xff" + b"x" * 4096)
            calls.append(ts)

    monkey = pro.__dict__
    src = tmp_path / "src.mp4"
    src.write_bytes(b"0" * 8192)
    import app.assembly.ffmpeg as ffmod

    original = ffmod.get_ffmpeg_runner
    ffmod.get_ffmpeg_runner = lambda: _Runner()
    try:
        frames = pro._extract_window_frames(src, 100.0, 120.0, tmp_path, 3)
    finally:
        ffmod.get_ffmpeg_runner = original

    assert len(frames) == pro.PRO_FRAMES_PER_WINDOW
    stamps = [ts for ts, _ in frames]
    # Strictly inside the bounded window, in chronological order.
    assert all(100.0 < ts < 120.0 for ts in stamps), stamps
    assert stamps == sorted(stamps)
    assert calls == stamps


# =========================================================================
# 8. No different model is substituted during execution or fallback
# =========================================================================

def test_8_no_model_substitution_on_execution(monkeypatch):
    monkeypatch.setenv("CODECRAFT_API_KEY", "cc_fake_for_test")
    resolved = resolve_model(OPUS, mode="exact", try_next_on_failure=True)
    assert resolved.canonical_model_id == OPUS
    assert resolved.provider_model_id == OPUS
    # Only this model's own binding may ever be used.
    declared = {(b.provider, b.provider_model_id) for b in get_model(OPUS).bindings}
    assert (resolved.provider, resolved.provider_model_id) in declared


def test_8b_codecraft_failure_does_not_fall_back_to_another_model(monkeypatch):
    """Opus has ONE route; its failure must NOT yield a different model."""
    import app.content.provider_registry as preg
    from app.content.model_resolver import AllExactModelProvidersFailedError

    class _Dead:
        name = "codecraft"
        model = OPUS

        def is_available(self):
            return False

        def supports_multimodal(self):
            return True

        def supports_native_video(self):
            return False

    original = preg._REGISTRY["codecraft"]
    preg.register_provider("codecraft", lambda mid=None: _Dead(), allow_override=True)
    try:
        with pytest.raises(AllExactModelProvidersFailedError) as exc:
            resolve_model(OPUS, mode="exact", try_next_on_failure=True)
        # The error names OPUS and never silently returns another model.
        assert exc.value.canonical_model_id == OPUS
        for other in ("gemini-3-flash-preview", "qwen-vl-max", "gpt-4o"):
            assert other not in str(exc.value)
        for attempt in exc.value.attempted:
            assert attempt.get("provider_model_id") == OPUS
    finally:
        preg.register_provider("codecraft", original, allow_override=True)


def test_8c_adapter_reports_the_model_it_requested():
    from unittest.mock import Mock, patch

    fake = Mock()
    fake.status_code = 200
    fake.json.return_value = {
        "model": OPUS,
        "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
    }
    p = CodeCraftProvider(api_key="k", model=OPUS)
    with patch("app.content.providers.codecraft.requests.post", return_value=fake) as post:
        resp = p.generate("hi")
    assert post.call_args[1]["json"]["model"] == OPUS
    assert resp.model == OPUS
    assert (resp.metadata or {}).get("provider_reported_model") == OPUS
    assert "provider_model_mismatch" not in (resp.metadata or {})


# =========================================================================
# 9. Unknown CodeCraft balance stays UNKNOWN after a successful request
# =========================================================================

def test_9_successful_request_is_not_a_verified_balance(monkeypatch):
    monkeypatch.setenv("CODECRAFT_API_KEY", "cc_fake_for_test")
    monkeypatch.setenv("CODECRAFT_BASE_URL", "https://example.invalid/v1")
    binding = get_model(OPUS).bindings[0]
    ev = assess_provider_route(OPUS, binding,
                               required_modality=InputModality.FRAME_SEQUENCE).evidence
    # Credentials configured...
    assert ev.credentials_configured is True
    # ...and capability demonstrated, but BALANCE remains unknown.
    assert ev.quota_state == QuotaEvidence.CREDENTIALS_CONFIGURED
    assert ev.quota_known is False
    assert ev.quota_state != QuotaEvidence.BALANCE_VERIFIED
    assert "unknown" in (ev.quota_detail or "").lower()


def test_9b_no_balance_endpoint_is_claimed_for_codecraft():
    from app.content.capability_router import PROVIDERS_WITH_BALANCE_ENDPOINT

    assert "codecraft" not in PROVIDERS_WITH_BALANCE_ENDPOINT


def test_9c_identity_is_declared_not_independently_attested():
    binding = get_model(OPUS).bindings[0]
    # Declared + runtime-verified capability -- NOT 'verified' identity.
    assert binding.identity_verification == IdentityVerification.DECLARED
    assert binding.identity_verification != IdentityVerification.VERIFIED
    evidence = (binding.identity_evidence or "").lower()
    assert evidence, "a declared binding must cite its evidence"
    # The evidence must state both the capability proof and the identity limit.
    assert "verified at runtime" in evidence
    assert "not independently" in evidence or "provider-declared" in evidence
    assert "native video" in evidence


def test_9d_live_revalidation_evidence_is_recorded_on_disk():
    """The registration is backed by a persisted live-evidence artifact."""
    assert REVALIDATION.is_file(), "live revalidation evidence must exist"
    data = json.loads(REVALIDATION.read_text(encoding="utf-8"))
    assert data["target_model"] == OPUS
    assert data["model_listed"] is True
    assert data["vision_reproducible"] is True
    result = data["vision_result"]
    assert result["grounded"] is True
    assert result["response_model"] == OPUS
    assert result["native_video_ingestion"] is False
    assert result["input_modality"] == InputModality.FRAME_SEQUENCE
    # The catalogue shape change is recorded honestly.
    assert data["declared"]["declares_native_video"] is False


# =========================================================================
# Pro pipeline integration: the frame route runs through the EXISTING loop
# =========================================================================

def _fixture_asset_and_transcript(tmp_path, pid: str):
    from app.production.models import SourceAsset, SourceTranscript, TranscriptSegment
    from app.production.storage import production_dir

    base = production_dir(pid, root=tmp_path)
    (base / "source").mkdir(parents=True, exist_ok=True)
    src_rel = "source/src-001.mp4"
    (base / src_rel).write_bytes(b"0" * 65536)
    asset = SourceAsset(
        asset_id="src-001", original_filename="src-001.mp4", stored_path=src_rel,
        sha256="c" * 64, duration=240.0, width=640, height=360,
        video_codec="h264", audio_codec="aac", mime="video/mp4",
        file_size=65536, ingested_at="2026-10-15T00:00:00+00:00",
    )
    transcript = SourceTranscript(
        asset_id="src-001", language="en",
        segments=[TranscriptSegment(text=f"seg {i}", start=i * 40.0, end=(i + 1) * 40.0)
                  for i in range(6)],
        provider="mock", provider_version="1.0",
        created_at="2026-10-15T00:00:00+00:00",
        transcript_path="source/src-001.transcript.json",
    )
    return asset, transcript


class _StubRunner:
    """Writes a plausible output file for whatever ffmpeg invocation is asked."""

    def run(self, args, label):
        out = Path(args[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() in (".jpg", ".jpeg", ".png"):
            out.write_bytes(b"\xff\xd8\xff" + b"x" * 4096)
        else:
            out.write_bytes(b"\x00" * 65536)


def _plan_json(provider_name: str, model: str) -> str:
    return json.dumps({
        "summary": "s",
        "beats": [{"beat_id": "b1", "title": "t", "narration_text": "narr",
                   "source_refs": [{"asset_id": "src-001", "start_s": 0, "end_s": 5,
                                    "transcript_segment_ids": [0]}]}],
    })


def test_10_pro_pipeline_sends_frames_not_mp4_for_opus(tmp_path):
    """End-to-end through build_pro_multimodal_plan with a frame-only provider.

    Proves the EXISTING bounded-window loop:
      * supplies timestamped frames (never an MP4) to a frame-only provider,
      * keeps window boundaries contiguous and source-grounded,
      * labels every window record 'frame_based_visual_analysis'.
    """
    import app.assembly.ffmpeg as ffmod
    import app.production.narration_pro as pro
    from app.content.providers.base import LLMResponse

    received = []

    class _FrameOnlyProvider:
        name = "codecraft"
        model = OPUS

        def is_available(self):
            return True

        def supports_multimodal(self):
            return True

        def supports_native_video(self):
            return False

        def generate_multimodal(self, prompt, system_prompt=None, *, video=None,
                                images=None, video_bytes=None, **kw):
            # A frame-only provider must NEVER be handed a clip.
            assert video is None, "MP4 must not be sent to a frame-only provider"
            assert video_bytes is None
            assert images, "frames must be supplied"
            # Verify the frame files exist NOW -- the pipeline cleans up its temp
            # directory before returning, so existence must be checked in-call.
            for ts, path in images:
                assert isinstance(ts, float)
                assert Path(path).is_file(), f"frame {path} must exist at call time"
                assert Path(path).stat().st_size > 1024
            received.append({"prompt": prompt, "images": list(images)})
            return LLMResponse(content=json.dumps({"visual_summary": "frames seen"}),
                               provider="codecraft", model=OPUS)

        def generate(self, prompt, system_prompt=None, **kw):
            return LLMResponse(content=_plan_json("codecraft", OPUS),
                               provider="codecraft", model=OPUS)

    pid, uid = "prod-opus", "unit-opus"
    asset, transcript = _fixture_asset_and_transcript(tmp_path, pid)

    original = ffmod.get_ffmpeg_runner
    ffmod.get_ffmpeg_runner = lambda: _StubRunner()
    try:
        artifact = pro.build_pro_multimodal_plan(
            pid, uid, asset, transcript, target_duration_seconds=30.0,
            summary_model_id=OPUS, prov=_FrameOnlyProvider(), root=tmp_path,
        )
    finally:
        ffmod.get_ffmpeg_runner = original

    # Frames really were supplied for every window (existence verified in-call).
    assert received, "the frame route must have been exercised"
    for call in received:
        assert len(call["images"]) == pro.PRO_FRAMES_PER_WINDOW
        # The prompt discloses frame-based evidence.
        assert "frames" in call["prompt"].lower()

    # Window records are truthfully labelled and bounded.
    windows = getattr(artifact, "_pro_window_summaries", [])
    assert windows
    for w in windows:
        assert w["visual_method"] == "frame_based_visual_analysis"
        assert w["frame_count"] == pro.PRO_FRAMES_PER_WINDOW
        for ts in w["frame_timestamps"]:
            assert w["start_s"] <= ts <= w["end_s"]
    # Bounded-window contract preserved (contiguous, covers the source).
    assert windows[0]["start_s"] == 0
    assert abs(windows[-1]["end_s"] - float(asset.duration)) < 1.0
    for a, b in zip(windows, windows[1:]):
        assert abs(a["end_s"] - b["start_s"]) < 0.5
    assert getattr(artifact, "_pro_visual_method") == "frame_based_visual_analysis"


def test_10b_native_video_provider_still_receives_the_clip(tmp_path):
    """Existing native-video behaviour is UNCHANGED (no regression)."""
    import app.assembly.ffmpeg as ffmod
    import app.production.narration_pro as pro
    from app.content.providers.base import LLMResponse

    seen = []

    class _NativeProvider:
        name = "gemini"
        model = "gemini-3-flash-preview"

        def is_available(self):
            return True

        def supports_multimodal(self):
            return True

        def supports_native_video(self):
            return True

        def generate_multimodal(self, prompt, system_prompt=None, *, video=None,
                                images=None, video_bytes=None, **kw):
            seen.append({"video": video, "images": images})
            return LLMResponse(content=json.dumps({"visual_summary": "clip seen"}),
                               provider="gemini", model=self.model)

        def generate(self, prompt, system_prompt=None, **kw):
            return LLMResponse(content=_plan_json("gemini", self.model),
                               provider="gemini", model=self.model)

    pid, uid = "prod-native", "unit-native"
    asset, transcript = _fixture_asset_and_transcript(tmp_path, pid)

    original = ffmod.get_ffmpeg_runner
    ffmod.get_ffmpeg_runner = lambda: _StubRunner()
    try:
        artifact = pro.build_pro_multimodal_plan(
            pid, uid, asset, transcript, target_duration_seconds=30.0,
            summary_model_id="gemini-3-flash-preview",
            prov=_NativeProvider(), root=tmp_path,
        )
    finally:
        ffmod.get_ffmpeg_runner = original

    assert seen, "native provider must have been called"
    # It received a CLIP, and no frame images.
    assert any(c["video"] for c in seen)
    for c in seen:
        assert c["images"] is None
    for w in getattr(artifact, "_pro_window_summaries", []):
        assert w["visual_method"] == "native_video_ingestion"
        assert "frame_timestamps" not in w




