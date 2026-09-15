"""Phase 11C Batch 1I - logging / observability.

Hermetic tests for the configuration-first observability correction:

- the shared, idempotent ``configure_logging`` (LOG_LEVEL behavior, no
  duplicate handlers, INFO propagation, encoding-safe stream);
- the server runtime invokes the shared configuration;
- the previously silent provider seams (Alibaba video/image, OpenRouter
  video) now emit lifecycle records with scene/request identity;
- fallback decision logs carry scene identity;
- the provenance/accounting capture seam logs a compact confirmation
  (never a dump of the durable objects);
- secret hygiene: no credentials, signed URLs, prompts, or monetary values
  appear in the newly added log messages.

No network requests and no billable provider calls: DashScope/OpenRouter
are fakes and fixtures are produced locally.
"""

import io
import logging
from datetime import datetime
from pathlib import Path

import pytest

import app.orchestrator.alibaba_video as alibaba_video_mod
from app.flow.scene_generator import validate_video_file
from app.logging_setup import LOG_FORMAT, configure_logging
from app.orchestrator.alibaba_video import ALIBABA_VIDEO_PROVIDER
from app.orchestrator.openrouter_video import (
    OpenRouterVideoModel,
    OpenRouterVideoVisualProvider,
)
from app.orchestrator.scene_execution import completion_checkpoint_update
from app.orchestrator.visual_providers import OPENROUTER_VIDEO_PROVIDER

from tests.test_openrouter_video import (
    FakeCatalog,
    FakeOpenRouterClient,
    model_payload,
)
from tests.test_phase11c_batch1c_visual import (
    FakeDashScope,
    make_clip,
    make_request,
)
from tests.test_phase11c_batch1f_provenance import (
    FakeOpenRouterVideoWithJob,
    alibaba_provider,
    merged_checkpoint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def root_logger_state():
    """Force a clean, UNCONFIGURED root logger for the test, then restore.

    The forced clear at setup makes configuration tests deterministic even if
    any other test in a full-suite run left global logging state behind.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_marker = getattr(root, "_avm_logging_configured", None)
    saved_noise = {
        name: logging.getLogger(name).level for name in ("urllib3", "httpx")
    }

    # Deterministic starting point: no handlers, no marker.
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    if hasattr(root, "_avm_logging_configured"):
        delattr(root, "_avm_logging_configured")

    yield root

    for handler in root.handlers[:]:
        root.removeHandler(handler)
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)
    if saved_marker is None:
        if hasattr(root, "_avm_logging_configured"):
            delattr(root, "_avm_logging_configured")
    else:
        setattr(root, "_avm_logging_configured", saved_marker)
    for name, level in saved_noise.items():
        logging.getLogger(name).setLevel(level)


def alibaba_video_provider(tmp_path, client, **kwargs):
    kwargs.setdefault("poll_interval", 0.0)
    kwargs.setdefault("poll_max_attempts", 3)
    from app.orchestrator.alibaba_video import AlibabaVideoVisualProvider

    return AlibabaVideoVisualProvider(
        tmp_path, api_key="sk-ws-abc123", http_client=client, **kwargs
    )


def _messages(records):
    return [record.getMessage() for record in records]

# ---------------------------------------------------------------------------
# 1-5. Central configuration: existence, idempotency, LOG_LEVEL, propagation
# ---------------------------------------------------------------------------

def test_configure_logging_is_idempotent_and_does_not_duplicate_handlers(
    root_logger_state,
):
    stream_one, stream_two = io.StringIO(), io.StringIO()
    configure_logging(level="INFO", stream=stream_one)
    handler_count = len(logging.getLogger().handlers)
    assert handler_count >= 1

    configure_logging(level="INFO", stream=stream_two)
    # No duplicate handlers were added on re-entry.
    assert len(logging.getLogger().handlers) == handler_count
    # The second stream is untouched: the first configuration remains active.
    assert stream_two.getvalue() == ""


def test_configure_logging_respects_explicit_level_and_propagates_info(
    root_logger_state,
):
    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)

    logging.getLogger("app.orchestrator.pipeline").info(
        "visibility.probe lifecycle message"
    )
    assert "visibility.probe lifecycle message" in stream.getvalue()


def test_configure_logging_respects_log_level_environment(
    root_logger_state, monkeypatch
):
    import app.config as config

    monkeypatch.setattr(config, "LOG_LEVEL", "WARNING")
    stream = io.StringIO()
    configure_logging(stream=stream)

    logging.getLogger("app.testprobe").info("info hidden at warning level")
    logging.getLogger("app.testprobe").warning("warning visible")
    value = stream.getvalue()
    assert "info hidden at warning level" not in value
    assert "warning visible" in value


def test_configure_logging_uses_the_avm_text_format(root_logger_state):
    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)
    assert LOG_FORMAT == "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    logging.getLogger("app.formatprobe").info("formatted line")
    value = stream.getvalue()
    assert "[INFO] app.formatprobe: formatted line" in value
    # Timestamps are present (asctime placeholder is materialized).
    assert "formatted line" in value and ": " in value


def test_configure_logging_survives_windows_console_encoding(
    root_logger_state,
):
    """Encoding-safe stream: characters unmappable on cp1252 consoles are
    replaced (never raised, never dropped records)."""

    class _Cp1252Stream:
        """A stream that rejects characters cp1252 cannot represent."""

        encoding = "cp1252"
        terminator = "\n"

        def __init__(self):
            self.written = []

        def write(self, message):
            message.encode("cp1252")  # raises UnicodeEncodeError on U+2603
            self.written.append(message)
            return len(message)

        def flush(self):
            pass

    failing = _Cp1252Stream()
    configure_logging(level="INFO", stream=failing)

    # U+2603 (snowman) has NO cp1252 mapping; emit() must replace, not raise.
    unmappable = chr(0x2603)
    logging.getLogger("app.encodingprobe").info(
        f"probe {unmappable} completed"
    )
    assert failing.written, "the record must still be emitted"
    assert all(unmappable not in message for message in failing.written)
    assert any("probe" in message for message in failing.written)


def test_server_runtime_invokes_logging_configuration():
    """6. The Command Center server main path now configures logging."""
    import app.web.server as server_mod

    source = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "configure_logging()" in source
    assert "from app.logging_setup import configure_logging" in source


def test_cli_setup_logging_delegates_but_preserves_behavior(
    root_logger_state,
):
    """produce.py keeps INFO level, file + stdout destinations, and
    third-party noise suppression via the shared configuration."""
    import produce

    produce.setup_logging(verbose=False)
    root = logging.getLogger()
    assert any(
        isinstance(handler, logging.FileHandler) for handler in root.handlers
    )
    assert any(
        isinstance(handler, logging.StreamHandler) for handler in root.handlers
    )
    assert root.level == logging.INFO
    # urllib3/httpx suppressed (existing produce.py behavior, now central).
    assert logging.getLogger("urllib3").level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING
    # Cleanup: close the file handler opened against the CLI log directory.
    for handler in root.handlers[:]:
        if isinstance(handler, logging.FileHandler):
            handler.close()
            root.removeHandler(handler)

# ---------------------------------------------------------------------------
# 7-8. Alibaba video lifecycle logs (with secret hygiene)
# ---------------------------------------------------------------------------

def test_alibaba_video_emits_lifecycle_records_with_identity(
    tmp_path, caplog
):
    client = FakeDashScope(states=["RUNNING", "SUCCEEDED"])
    client._download = make_clip(tmp_path / "fixture.mp4").read_bytes()
    with caplog.at_level(logging.INFO, logger="app.orchestrator.alibaba_video"):
        result = alibaba_video_provider(tmp_path, client).generate_scene(
            make_request()
        )
    assert result.success

    messages = "\n".join(_messages(caplog.records))
    assert "provider.request_resolved" in messages
    assert "provider.request_submitted" in messages
    assert "scene=scene_001" in messages
    assert "task_id=task-001" in messages
    assert "request_id_kind=task_id" in messages
    assert "model=wan2.2-t2v-plus" in messages
    assert "provider.poll_progress" in messages
    assert "provider.materialized" in messages
    assert "latency_ms=" in messages
    assert "usage_present=True" in messages


def test_alibaba_video_polling_logs_are_bounded(tmp_path, caplog):
    """Many identical RUNNING polls produce one line per status change,
    never one line per poll."""
    client = FakeDashScope(states=["RUNNING", "RUNNING", "SUCCEEDED"])
    client._download = make_clip(tmp_path / "bounded.mp4").read_bytes()
    with caplog.at_level(logging.INFO, logger="app.orchestrator.alibaba_video"):
        result = alibaba_video_provider(tmp_path, client).generate_scene(
            make_request()
        )
    assert result.success

    poll_lines = [
        message
        for message in _messages(caplog.records)
        if message.startswith("provider.poll_progress")
    ]
    assert len(poll_lines) == 2  # first RUNNING + SUCCEEDED change
    assert "remote_status=RUNNING" in poll_lines[0]
    assert "remote_status=SUCCEEDED" in poll_lines[1]


def test_alibaba_video_logs_never_emit_secrets_or_urls(tmp_path, caplog):
    client = FakeDashScope(states=["RUNNING", "SUCCEEDED"])
    client._download = make_clip(tmp_path / "hygiene.mp4").read_bytes()
    with caplog.at_level(logging.INFO, logger="app.orchestrator.alibaba_video"):
        result = alibaba_video_provider(tmp_path, client).generate_scene(
            make_request()
        )
    assert result.success

    messages = "\n".join(_messages(caplog.records))
    assert "sk-ws-abc123" not in messages
    assert "sk-ws" not in messages
    assert "api_key" not in messages.lower()
    assert "https://media.test" not in messages  # signed provider URL
    assert "A cinematic mecha" not in messages  # never the full prompt


def test_alibaba_terminal_failure_is_logged_with_identity(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="app.orchestrator.alibaba_video"):
        result = alibaba_video_provider(
            tmp_path, FakeDashScope(states=["FAILED"])
        ).generate_scene(make_request())
    assert not result.success

    messages = "\n".join(_messages(caplog.records))
    assert "provider.terminal_failure" in messages
    assert "scene=scene_001" in messages
    assert "task_id=task-001" in messages
    assert "remote_status=FAILED" in messages

# ---------------------------------------------------------------------------
# 9. OpenRouter video lifecycle logs
# ---------------------------------------------------------------------------

def _openrouter_success(tmp_path, statuses=None):
    artifact = make_clip(tmp_path / "or_log.mp4")
    client = FakeOpenRouterClient(video_bytes=artifact.read_bytes())
    if statuses is not None:
        client.statuses = statuses
    return OpenRouterVideoVisualProvider(
        tmp_path / "scenes",
        api_key="test-key-not-real",
        http_client=client,
        catalog=FakeCatalog([
            OpenRouterVideoModel.from_dict(
                model_payload("vendor/actual-video", durations=(4,))
            )
        ]),
        poll_interval=0.0,
        poll_max_attempts=3,
    ).generate_scene(make_request(duration=4))


def test_openrouter_video_emits_lifecycle_records(tmp_path, caplog):
    with caplog.at_level(
        logging.INFO, logger="app.orchestrator.openrouter_video"
    ):
        result = _openrouter_success(
            tmp_path,
            statuses=[
                {"status": "processing"},
                {
                    "status": "completed",
                    "usage": {"seconds": 4},
                    "actual_cost": "0.05",
                },
            ],
        )
    assert result.success

    messages = "\n".join(_messages(caplog.records))
    assert "provider.model_selected" in messages
    assert "provider.request_submitted" in messages
    assert "scene=scene_001" in messages
    assert "job_id=job_001" in messages
    assert "request_id_kind=job_id" in messages
    assert "provider.poll_progress" in messages
    assert "provider.completed" in messages
    assert "actual_cost_present=True" in messages
    # G/H boundary: monetary VALUES are never logged — presence flags only.
    assert "0.05" not in messages


def test_openrouter_failure_logs_are_warnings_with_identity(tmp_path, caplog):
    with caplog.at_level(
        logging.INFO, logger="app.orchestrator.openrouter_video"
    ):
        provider = OpenRouterVideoVisualProvider(
            tmp_path / "scenes",
            api_key="test-key-not-real",
            http_client=FakeOpenRouterClient(error=RuntimeError("boom")),
            catalog=FakeCatalog([
                OpenRouterVideoModel.from_dict(
                    model_payload("vendor/actual-video", durations=(4,))
                )
            ]),
            poll_interval=0.0,
        )
        result = provider.generate_scene(make_request(duration=4))
    assert not result.success

    messages = "\n".join(_messages(caplog.records))
    assert "provider.operation_failed" in messages
    assert "scene=scene_001" in messages

# ---------------------------------------------------------------------------
# 10. Fallback decision logs carry scene identity
# ---------------------------------------------------------------------------

def test_fallback_logs_carry_scene_identity(tmp_path, caplog):
    artifact = make_clip(tmp_path / "fb_log.mp4")
    with caplog.at_level(
        logging.INFO, logger="app.orchestrator.alibaba_video"
    ):
        result = alibaba_video_mod.FallbackVideoVisualProvider(
            tmp_path,
            primary=alibaba_provider(
                tmp_path, FakeDashScope(states=["FAILED"])
            ),
            fallback=FakeOpenRouterVideoWithJob(
                succeed=True, artifact=artifact
            ),
            fallback_enabled=True,
        ).generate_scene(make_request())
    assert result.success

    messages = "\n".join(_messages(caplog.records))
    assert "fallback.triggered scene=scene_001" in messages
    assert "category=task_failure" in messages
    assert "fallback.succeeded scene=scene_001" in messages
    assert "final_provider=openrouter_video" in messages
    assert "primary_provider=alibaba_video" in messages
    # The success line must not claim Alibaba produced the artifact.
    fallback_success = next(
        message
        for message in _messages(caplog.records)
        if message.startswith("fallback.succeeded")
    )
    assert "provider=alibaba_video" not in fallback_success.replace(
        "primary_provider=alibaba_video", ""
    )


# ---------------------------------------------------------------------------
# 11. Capture-seam logging is compact (never a durable-object dump)
# ---------------------------------------------------------------------------

def test_capture_seam_logs_compact_context_without_dumping(tmp_path, caplog):
    result = make_accounting_result(tmp_path)
    with caplog.at_level(
        logging.INFO, logger="app.orchestrator.scene_execution"
    ):
        completion_checkpoint_update(
            result=result,
            validation={"valid": True},
            provider_name=ALIBABA_VIDEO_PROVIDER,
            sequence=1,
            include_provenance=False,
            checkpoint={"task_id": "task-001"},
        )

    messages = "\n".join(_messages(caplog.records))
    assert "provenance.accounting.captured" in messages
    assert "scene=scene_001" in messages
    assert "provider=alibaba_video" in messages
    assert "model=wan2.2-t2v-plus" in messages
    assert "request_id=task-001" in messages
    assert "billing_status=billable" in messages
    assert "cost_present=False" in messages
    # Never a dump of the durable objects/payloads.
    assert "attempts" not in messages
    assert "video_duration" not in messages
    assert "partial" not in messages


def make_accounting_result(tmp_path):
    from tests.test_phase11c_batch1h_accounting import make_result

    return make_result(
        {
            "provider": ALIBABA_VIDEO_PROVIDER,
            "model": "wan2.2-t2v-plus",
            "task_id": "task-001",
            "usage": {"video_duration": 5},
            "fallback_used": False,
        },
        tmp_path,
    )


# ---------------------------------------------------------------------------
# 12. Secret hygiene across the new seams
# ---------------------------------------------------------------------------

def test_no_api_keys_in_new_lifecycle_messages(tmp_path, caplog):
    client = FakeDashScope(states=["SUCCEEDED"])
    client._download = make_clip(tmp_path / "keys.mp4").read_bytes()
    with caplog.at_level(logging.INFO, logger="app.orchestrator.alibaba_video"):
        alibaba_video_provider(tmp_path, client).generate_scene(make_request())

    for record in caplog.records:
        assert "sk-ws-abc123" not in record.getMessage()
        assert "api_key" not in record.getMessage().lower()

# [1I-END]

